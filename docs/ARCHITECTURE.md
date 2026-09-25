# Architecture

How sid2midi turns a SID file into MIDI: the modules, the emulated machine,
how time advances, how SID writes are captured and how the capture becomes a
Standard MIDI File. For command-line use see the [README](../README.md); for
the MIDI output in detail see [MIDI_MAPPING.md](MIDI_MAPPING.md).

## Pipeline

```text
 .sid / .mus file
      │  SidFile / MusTune           header parsing, validation, repairs
      ▼
 run_tune()                          choose a driver
      │  _run_called()               PSID with a play address: init, then play per frame
      │  _run_continuous()           RSID, PSID with play = 0, BASIC: a running C64
      ▼
 C64 machine                         CPU + CIAs + VIC-II + SIDs, one CPU cycle at a time
      │  every SID write is stamped with its cycle
      ▼
 frames + frame start cycles         one register snapshot per player call / IRQ frame
      │  find_loop()                 optional (--auto): trim to intro + one loop
      ▼
 convert()                           notes, pitch bend, CCs, drums, meta events
      │
 write_smf()                         Standard MIDI File, format 1
```

## Modules

| Module | Role | Origin |
|--------|------|--------|
| `sid2midi.py` | file parsing, the C64 machine, the fast VIC-II model, SID capture, drivers, loop detection, MIDI writer, command line | sid2midi |
| `cpu6502.py` | cycle-exact NMOS 6502/6510 | sid2midi, checked against SingleStepTests and the VICE CPU tests |
| `cia_vice.py` | MOS 6526 / 6526A CIA | statement-level port of VICE `ciacore.c` / `ciatimer.c` |
| `vicii_sc.py` | cycle-based VIC-II (6569, 8565, 6567, 8562, 6567R56A) | statement-level port of VICE `src/viciisc` |
| `residfp.py` | digital part of the SID (oscillators, noise LFSR, combined waveforms, envelopes, bus) | statement-level port of libresidfp (reSIDfp) |
| `mus.py` | Compute!'s Sidplayer MUS loader and a small xa assembler | follows libsidplayfp's MUS loader |

The ports keep the names and statement order of their references, so a
difference from the reference can be found by reading both side by side.
Deliberate deviations are commented in the source with the evidence behind
them and listed in [ACCURACY.md](ACCURACY.md).

## The machine (`C64` in `sid2midi.py`)

A breadbin C64 (PAL or NTSC) with:

- **Memory:** 64 KB RAM, the `$00/$01` processor port with fading unused bits,
  the PLA banking of BASIC, KERNAL, character ROM and I/O, colour RAM
  (4 bits), SID mirrors in `$D400-$D7FF`, extra SIDs from PSID v3/v4 headers.
  `C64.read` / `C64.write` dispatch by address; I/O goes through `_io_read` /
  `_io_write`.
- **CPU:** `cpu6502.CPU6502`. Each cycle performs exactly one bus access
  (dummy reads and the read-modify-write double write included). Three hooks
  connect it to the machine: `rdy(t)` (the VIC-II's BA line), `pending(...)`
  (which interrupt to take before an opcode fetch) and `nmi_hijack(t)` (an NMI
  taking over a BRK/IRQ vector fetch).
- **CIAs:** two `cia_vice.Cia6526` objects. CIA #1 drives IRQ, CIA #2 NMI. The
  VICE alarm scheduler is replaced by lazy dispatch: a CIA brings itself up to
  date before every register access and when the machine asks for its next
  interrupt edge.
- **VIC-II:** starts as the fast model `Vic6569` (raster interrupt, BA from
  bad lines and sprite data fetches, register read-back). The machine switches
  to the full `vicii_sc.VicIISC` (`use_vicii_sc`) the first time a program
  needs something only the full chip has: a sprite-collision or light-pen
  register read or interrupt enable, the light-pen line going low, or an
  open-bus read (colour RAM upper nibble, unmapped I/O) from the tune's own code.
  The full chip is built from the fast model's state (`VicIISC.from_fast`), so
  the switch is invisible to the program.
- **SIDs:** one `SidChip` per SID (see below).
- **ROMs:** `roms/kernal.bin`, `basic.bin`, `chargen.bin` when present. The
  KERNAL/BASIC cold start is run once per video standard and cached
  (`cold_reset_state`).

### Interrupt lines

Each source (CIA #1, VIC-II, CIA #2) reports line changes with their cycle
(`irq_changed`). The machine keeps, per IRQ source, when the line last went
low and high, and the cycle of the last NMI edge from CIA #2. Before each
opcode fetch the CPU asks `_pending` which interrupt, if any, it takes; the
CPU core itself applies the sampling point two cycles earlier and the
one-instruction delay after CLI, SEI and PLP.

### Time

`cpu.cycles` counts CPU cycles; `C64.now()` adds `time_base` (the start of the
current player call in called mode). Every peripheral is lazily clocked: it
remembers the last cycle it processed and catches up when it is accessed or
when its next event is needed.

`C64.run(end, stop_pc, on_irq)` executes instructions until a cycle limit, a
stop address or a CPU jam. Two shortcuts keep idle tunes cheap without changing
any result:

- at the driver's idle address `IDLE_PC` (after init returned, or between
  called-mode calls) nothing can happen until an interrupt, so time jumps to
  the first opcode-fetch cycle at which an interrupt could be recognised
  (`_next_interrupt_fetch`);
- an instruction that jumps to itself (`JMP *`, `BNE *`) is repeated in whole
  multiples of its length up to the next possible interrupt or BA edge.

### Two VIC-II models

The full VIC-II port costs about 3.8 µs per emulated cycle in CPython, which
would make every conversion several times slower. Most tunes only need the
raster interrupt and BA, so `Vic6569` computes those from the registers in
closed form (next compare match, bad-line and sprite-fetch BA windows) and
`vicii_sc.py` is only used when a program can observe more. The fast model was
checked against the port cycle by cycle (BA and interrupt edges over random
register sequences, PAL and NTSC), and its register read-back matches the full
chip (`tests/test_mus_vic_cpu.py`).

## SID capture (`SidChip`)

A SID write updates three things:

1. **The register image** (`reg`), snapshotted per frame.
2. **Register-level estimates** of the envelope and oscillators (`SidEnvelope`,
   `SidWave`, reSID 0.16 style). They are cheap, stepped only when needed, and
   provide gate-trigger detection with the trigger cycle (`trig`, `trigcyc`)
   and envelope counters for note velocity.
3. **An exact reSIDfp chip** (`residfp.SID`), but only for tunes that read
   `$D41B` (OSC3) or `$D41C` (ENV3). It is created on the first such read by
   replaying every write since power-on, then clocked every cycle. Tunes that
   never read those registers never pay for it. Paddle reads return `$FF`;
   write-only registers return the decaying bus value.

`C64.snapshot()` returns, per SID, `(registers $00-$18, trigger flags, trigger
cycles, envelope counters)` plus the number of `$D418` writes in the frame
(digi activity).

## Drivers

### Called PSIDs (`_run_called`)

`init` is called once with the song number in A, then `play` once per frame:
at v-sync (PAL 19,656 / NTSC 17,095 cycles) or, when the header's speed bit
selects CIA timing, at the rate CIA #1 timer A is programmed to. `$01` is set
per call as the PSID specification describes. Every call has an instruction
budget; BRK returns to the driver; repeated stalled calls stop the capture.

### Continuous tunes (`_run_continuous`)

RSIDs, PSIDs with play address 0 and C64 BASIC tunes run on a cold-started C64.
The tune is loaded, init is entered with an RTS frame that returns to
`IDLE_PC`, and from then on the machine runs freely: IRQ and NMI handlers run
when the CIAs or the VIC-II raise them, including during an init that never
returns. BASIC tunes are started by setting BASIC's pointers and typing `RUN`
into the keyboard buffer (`_start_basic_program`).

Capture frames follow the interrupts: a frame closes at each IRQ entry that is
at least 1/16 of a video frame after the previous frame start, and at the
latest after one video frame (three while IRQs are arriving), so tunes with
several IRQs per frame keep their timing.

## Loop detection (`find_loop`, `--auto`)

Each frame is reduced to a musical signature: voice 1 frequency, the three gate
bits, the trigger pattern and digi activity. Filter sweeps and pulse-width
modulation are left out because they rarely repeat byte for byte. The period is
the smallest one (at least 6 seconds) for which the last window of up to 512
frames repeats; the loop start is then moved back as far as the repetition
holds, so intros are kept. The capture is trimmed to intro + one loop and loop
markers are written.

## MIDI conversion (`convert`, `write_smf`)

Frames are converted voice by voice: an exact note number from the oscillator
frequency (the sync source's frequency for hard-synced voices), note-on at the
gate-trigger cycle, pitch bend for the fractional part and for slides, CC
automation for the SID parameters, noise voices on the drum channel, and one
filter/master track per SID. Tick times come from the cycle stamps, so the MIDI
timing follows the C64 clock whatever grid tempo `--bpm` sets. Details:
[MIDI_MAPPING.md](MIDI_MAPPING.md).

## Using the modules from Python

```python
import sid2midi as S

sid = S.SidFile("tune.sid")                          # or S.MusTune("song.mus", "sidplayer1.a65")
run = S.run_tune(sid, song=0, seconds=60, auto=True) # song is 0-based
ppq, tracks = S.convert(sid, run.frames, run.fcyc, bpm=125, loop=run.loop)
S.write_smf("tune.mid", ppq, tracks)
print(run.timing, run.irq_count, run.warnings)
```

For hardware experiments, `tools/testbench.py` exposes
`run_program(path, cycles, pal, sid_model, cia_model, vic_model)`, which
cold-starts a machine, loads a PRG and runs it until it writes to the debug
cartridge register; see [TESTING.md](TESTING.md).
