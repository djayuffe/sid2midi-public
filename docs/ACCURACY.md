# Accuracy

What each part of the emulated C64 is checked against, every place where
sid2midi deliberately differs from its reference implementation and why, and
the known limits. Current pass/fail numbers:
[FINAL_CLOSURE_REPORT.md](../FINAL_CLOSURE_REPORT.md). How to run the checks:
[TESTING.md](TESTING.md).

## What "accurate" means here

sid2midi does not produce sound. Accuracy matters because a tune's player
code can depend on anything a real C64 does: interrupt timing, timer quirks,
the VIC-II stopping the CPU, or values read back from the SID. If the machine
differs, the player can take a different path and write different notes.

The claim is limited to what was checked:

- **References:** the CIA and VIC-II are ports of VICE (x64sc) and the SID's
  digital part is a port of libresidfp (reSIDfp), the most thoroughly tested
  open implementations.
- **Hardware data:** the VICE test programs, many of which carry values
  measured on real chips, and Wolfgang Lorenz's test suite.
- **Vectors:** SingleStepTests for the CPU.
- **Rules:** a deviation from a reference is only made with hardware evidence
  that covers all the data of the test concerned, and never breaks another
  test unless recorded as a trade-off.

Levels of evidence used below:

| Level | Meaning |
|-------|---------|
| port | follows the reference statement by statement |
| measured | deviation backed by real-chip data (a test's reference table, readme or published measurements) |
| analogy | applied to a chip variant by analogy with a measured one, not measured itself |

## CPU — `cpu6502.py`

Own implementation, checked against:

- SingleStepTests 6502 v1: 2,560,000 vectors, every bus access compared. All
  match except 1,278 ANE vectors (below).
- VICE `CPU/` and `interrupts/` test programs and Lorenz 2.15.
- Decimal mode against Bruce Clark's NMOS equations.

| Behaviour | Level | Evidence |
|-----------|-------|----------|
| ANE (`$8B`) magic constant `$EF` | measured | value measured on the C64; SingleStepTests assume `$EE` (the 1,278 vectors) |
| ANE `$EE` when RDY holds the operand read; SHx store the un-ANDed value when the indexed read is stalled; a stalled CLI loses its delay; a stalled SEI gets no credit for held cycles | port | VICE `maincpu_steal_cycles` (`mainc64cpu.c`, `6510dtvcore.c`); `interrupts/irqdma` |
| LXA magic constant `$EE` | port | the value VICE uses |
| JAM: bus accesses as the SingleStepTests vectors, then halt with the bus at `$FFFF/$FFFE` | measured | SingleStepTests |
| Interrupt poll two cycles before the opcode fetch; taken branch without page crossing skips one poll; NMI takes over BRK/IRQ vector fetch | port / measured | VICE interrupt handling; Lorenz and VICE interrupt tests |

## CIA — `cia_vice.py`

Port of VICE `ciacore.c` / `ciatimer.c` (6526 and 6526A): timers with the
timer B bug, interrupt delay line, serial port, TOD clock and alarm, PB6/PB7.

| Behaviour | Level | Evidence |
|-----------|-------|----------|
| Clock mapping of reads, writes and the interrupt line to machine cycles | measured | fixed by the 41,664 Lorenz `cia1ta`/`cia1tb` register-access cases |
| Released interrupt line reported one cycle later | port | VICE keeps `IK_IRQPEND` to `clk + 3` against a two-cycle sampling delay |
| TOD power-line ticks use VICE's non-random correction branch | port (deterministic choice) | runs are reproducible |
| No devices on the ports, CNT, SP or FLAG | limit | a bare C64: keyboard and joysticks released, lines pulled up |

## VIC-II — `vicii_sc.py` and the fast model

Port of VICE's cycle-based VIC-II (`src/viciisc`) for the 6569, 8565, 6567,
8562 and 6567R56A: cycle tables, bad lines, sprite DMA/expansion/crunch, BA,
phi1/phi2 fetches and open bus, collision registers and interrupts, light pen.

| Behaviour | Level | Evidence |
|-----------|-------|----------|
| A `$D01E`/`$D01F` read also clears collisions in the first 4 pixels of the cycle after the one VICE clears | measured | `VICII/spritevssprite` reference table (collision band 4 pixels early otherwise); VICE fails the test |
| A bad line triggered in idle state fetches that cycle's idle byte from `$38FF` (6569) / `$3807` (8565/8566) | measured | `VICII/vsp-tester` readme, real machines; both variants pass; VICE fails them |
| The same for the NTSC 6567 (`$38FF`) | analogy | not measured; `vsp-tester-ntsc` passes with it |
| VICE's random VSP-bug memory corruption | not emulated | random and chip-dependent; disabled by default in VICE |
| Colour output, frame buffer | not emulated | nothing visible to the CPU |

**Fast model.** Conversions start on `Vic6569` in `sid2midi.py`, which
computes raster interrupts, BA and register read-back directly. It switches to
the full port on the first sprite-collision or light-pen register read or
interrupt enable, the light-pen line going low (CIA #1 PB4), or an open-bus
read (colour RAM upper nibble, `$DE00-$DFFF`) from the tune's own code. One
exception keeps BASIC programs fast: colour RAM copies by the KERNAL screen
editor return the stored nibble on the fast model; the editor never uses the
upper nibble. The fast model was compared with the port cycle by cycle (BA and
interrupt edges, random register sequences, PAL and NTSC) and its register
read-back is unit-tested against the port.

## SID — `residfp.py` and the capture model

Port of libresidfp's digital part (commit `b8bdc00a`): oscillators, noise
LFSR and its writeback, combined waveforms, envelope generator and bus value.
It answers `$D41B` (OSC3) and `$D41C` (ENV3) reads. The analog filter, DACs
and audio output are not ported.

Noise writeback — the combined waveforms writing back into the noise shift
register — is where libresidfp and the VICE `SID/wb_testsuite` measurements
disagree. The test suite includes sampling logs of two 6581 chips (2586 and
2783) and two 8580 chips; its test tables use chip 2783.

| Behaviour | Chip | Level | Evidence |
|-----------|------|-------|----------|
| Noise+pulse writes into the shift register only while accumulator bit 19 is high | 6581 | measured | `wb_testsuite *_to_C_old` (stopped oscillator), `SID/wf12nsr` (running) |
| `$D`/`$E`/`$F → $C` and `$D → $E` write back only noise output bits 11–7 (register taps 22, 20 and 17 cleared) | 6581 | measured | `$F → $C` identical on both chips; `$D`/`$E → $C` and `$D → $E` from chip 2783 (unstable on that chip, different on chip 2586). For `$D → $E` a search over all tap masks matches the table's 10 reads only with this mask |
| `$F → $8` at test-bit fall: no writeback | 6581 | measured | both chips; `wb_testsuite F_to_8_old` |
| Test bit rising in the same write that selects a combined noise waveform latches that waveform's output (accumulator 0) into the noise taps | 6581 | measured | `SID/noiselfsrinit` (`$F8`, `$80` repeated); `F_to_8_old` sets the test bit one write earlier (`$88`, `$F8`, `$80`) and shows no writeback, which is what separates the two |
| `$C → $9`/`$E`/`$F` write back; `$C → $F` writes back the new waveform's output | 8580 | measured | `wb_testsuite C_to_x_new` |
| Noise+pulse output from reSID's `noise_pulse8580()` instead of the pulldown table | 8580 | port (reSID) | VICE `src/resid/wave.h`; `wb_testsuite C_to_9_new`/`C_to_E_new`, `wf12nsr-8580` |
| OSC3 read of noise+pulse pulls one more bit low than the written-back value | 8580 | measured | `SID/wf12nsr-8580` (two reads) |

The 6581 rules for `$D → $C`, `$E → $C` and `$D → $E` follow chip 2783, the
chip the tests were built from; chip 2586 behaves differently, so real 6581s
vary here.

**Capture model.** For the MIDI conversion itself, `SidChip` tracks
register-level envelope and oscillator state (reSID 0.16 style) for gate
triggers and velocity; the exact reSIDfp chip is only built when a tune reads
OSC3 or ENV3, by replaying every write since power-on, so it is cycle-exact
whenever the tune can observe it.

## Machine and drivers

| Behaviour | Level | Notes |
|-----------|-------|-------|
| Time skipping at the idle address and in self-jumping loops | exact | skips only up to the first cycle at which an interrupt or BA edge could change anything |
| PSID `$01` banking per call | PSID specification | v2NG load-range aware |
| RSID/BASIC cold start | exact with ROMs | real KERNAL/BASIC cold start; without the ROM images a minimal environment is used and a warning is printed |
| Keyboard, joysticks, paddles, IEC bus, cartridges, REU | not emulated | inputs read as released; paddles `$FF` |
| Power-on RAM contents | not emulated | RAM starts as `$00`; real machines show a pattern that three `C64/raminitpattern` programs check |

## Known limits

- Programs that depend on disk drives, cartridges, memory expansions or input
  devices are outside the model.
- Real 6581s differ in their noise writeback for some waveform transitions;
  sid2midi follows the chip the VICE tests were built from.
- The NTSC 6567 VSP idle-fetch address is not measured.
- 185 VICE test programs are not run (unemulated hardware, or tests that mount
  disk or cartridge images), and 7 of the programs that do run fail: three
  depend on the power-on RAM pattern, one on disk autostart, and three
  (`C64/bankio`, two `general/fuxxortest` programs) are not yet diagnosed. See
  the validation report.
- SID output is register intent, not sound: filter and waveform timbre are
  mapped to controllers, not reproduced.
