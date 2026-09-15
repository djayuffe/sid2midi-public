# sid2midi 3.1.0 — release notes

3.1.0 replaces the rules that 3.0.0 had fitted to test data with ports of
reference implementations, closes the remaining gaps in CIA, VIC-II, CPU and
SID behaviour found by the VICE test programs, and adds MUS support. The MIDI
mapping is unchanged; a conversion's output changes only where the emulated
hardware now reads back differently (for example `quake.sid`, whose player
copies the I/O area into its data, from about 39 s on).

## CIA (`cia_vice.py`, replaces `cia6526.py`)

- Statement-level port of VICE's CIA core and timer (`ciacore.c`,
  `ciatimer.c`): timer state machine, interrupt flag delay line, serial port,
  TOD with the 50/60 Hz divider and alarm, PB6/PB7, and both chip models:
  the original **6526** and the **6526A** ("new CIA").
- The fitted rules of 3.0.0 (ICR back-to-back reads, TB bug, interrupt
  pipeline) are gone; the port reproduces the same test data by construction.
- The shift register, TOD and 6526A tests of the VICE test repository, which
  3.0.0 did not pass or did not run, now pass.

## VIC-II (`vicii_sc.py`, new; fast model in `sid2midi.py` corrected)

- Port of VICE's cycle-based VIC-II (x64sc `viciisc`) for 6569, 8565, 6567R8,
  8562 and 6567R56A: cycle tables, bad lines, sprite DMA/expansion/crunch, BA
  with the three-cycle prefetch, phi1/phi2 fetches (open bus), sprite and
  graphics pipelines as far as they decide **sprite-sprite and
  sprite-background collisions** (registers and interrupts), and the **light
  pen** (CIA #1 PB4 / control port 1).
- Colour RAM reads return the phi1 bus value in the upper nibble; unmapped I/O
  (`$DE00-$DFFF`) returns the phi1 bus value; the 6510 port write stores the
  bus value into RAM `$00/$01` as seen by the VIC-II.
- Conversions keep the fast VIC-II model and switch to the full chip the first
  time a program reads the collision or light pen registers, enables their
  interrupts, or pulls the light pen line low. The fast model was checked
  against the port cycle by cycle (random sprite/DEN/YSCROLL scenarios); its
  NTSC sprite fetch cycles, the order of the Y-expansion flip-flop and the
  `$D017` crunch rule were corrected.
- The fast model now reads back unused register bits as 1, like the chip:
  `$D016 | $C0`, `$D018 | $01`, `$D020-$D02E | $F0` (a unit test compares
  every register against the full chip).
- `tools/testbench.py` always uses the full VIC-II and selects the chip model
  from the test options.

## CPU (`cpu6502.py`)

- RDY rules from VICE's `maincpu_steal_cycles`: SHA/SHX/SHY/TAS store the
  register without the `& (H+1)` when BA stalls the indexed read; ANE uses
  magic `$EE` instead of `$EF` when the operand read is stalled; a stalled CLI
  loses its one-instruction delay; a stalled SEI gets no credit for the held
  cycles. The 3.0.0 PLP rule, which VICE does not have, was removed.
- JAM leaves the address bus at `$FFFF/$FFFE` for its 11 cycles.
- Verified against SingleStepTests 6502 v1 (2.56 million vectors, every
  opcode, cycle by cycle); the only mismatches are ANE (`$8B`), where the test
  vectors assume magic `$EE` and sid2midi follows the C64 measurement `$EF`.

## SID (`residfp.py`)

- Noise write-back rules corrected against the VICE testbench `wb_testsuite`
  and `wf12nsr` reference data (6581 R3, 8580 R5): on the 6581, noise+pulse
  writes into the shift register only while accumulator bit 19 is high;
  `$D/$E/$F -> $C` and `$D -> $E` write back (6581);
  `$C -> $9/$E/$F` write back (8580), `$C -> $F` with the new waveform's output.
- 8580 noise+pulse output uses reSID's `noise_pulse8580()` combination instead
  of the pulldown table. Pass/fail counts and the failing programs, with their
  status in VICE, are in `FINAL_CLOSURE_REPORT.md`.

## MUS files (`mus.py`, new)

- Compute!'s Sidplayer MUS files are converted when the Sidplayer player
  routine is supplied with `--sidplayer FILE` (xa source such as libsidplayfp's
  `sidplayer1.a65`, or a binary with a load address). Installation follows
  libsidplayfp: data at `$0900`, player at `$E000` with its useless SID reads
  removed, init `$EC60`, play `$EC80`, CIA timing. The player is not bundled.

## Tools and tests

- `tools/testbench.py`: runs the `cia-new` variants with 6526A CIAs and the
  VIC-II model from the options (`vicii-ntsc`, `vicii-ntscold`, `vicii-new`).
- New `tests/test_mus_vic_cpu.py`; CIA tests run against `cia_vice.py`.

## Packaging

- The 3.0.0 archive accidentally contained Python bytecode
  (`__pycache__/*.pyc`); 3.1.0 ships sources only.

## Known limitations

See README "Accuracy boundary" and `FINAL_CLOSURE_REPORT.md`.
