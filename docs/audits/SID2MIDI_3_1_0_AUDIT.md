# sid2midi 3.1.0 Audit — reference ports and remaining gaps

## Goal

3.0.0 reached its test results partly with rules fitted to test data (CIA
interrupt pipeline, ICR reads, RDY interrupt decisions) and left CIA shift
register/TOD/6526A, sprite collisions, light pen and MUS unsupported. 3.1.0
replaces fitted rules with ports of reference implementations and checks every
change against the VICE test programs, real-hardware reference data in those
programs, and SingleStepTests.

## References used

- VICE trunk sources (read-only, not shipped): `src/core/ciacore.c`,
  `ciatimer.c/h`; `src/viciisc/*` (cycle tables, cycle, fetch, draw-cycle,
  mem, irq, lightpen); `src/mainc64cpu.c`, `src/6510dtvcore.c`,
  `src/interrupt.c/h` (interrupt delay and cycle-steal handling);
  `src/resid/wave.cc/h` (noise writeback comparison).
- VICE test programs (`testprogs`), test list `x64-testlist.txt`, and the
  published x64sc result file r45942 (2026-01-13) to tell emulator limits from
  sid2midi bugs.
- The test program sources `irqdma.asm`, `irq-ack-vicii.asm`,
  `spritevssprite.asm` and the reference tables embedded in the SID tests.
- SingleStepTests 6502 v1 vectors; libsidplayfp `sidplayer1.a65` (local only)
  and its MUS loader.

## Method

1. Port the reference statement by statement, keeping names and order.
2. Map the reference clock to the machine cycle and fix the mapping with test
   programs (CIA: Lorenz register-access replay and the interrupt tests).
3. Run every exit-code test program; for each failure read the test source or
   its reference data, find the first diverging value, and compare with the
   reference implementation before changing anything.
4. Where the reference itself fails a test (per the VICE result file), try
   evidence-driven rules from the test's hardware data; keep a rule only when
   it fixes tests without breaking any other.
5. Check the fast VIC-II model used for conversions against the port with a
   cycle-by-cycle differential test (BA and raster interrupt edges, random
   sprite/DEN/YSCROLL register sequences, PAL and NTSC).

## Changes by component

### CIA — `cia_vice.py` (replaces the fitted `cia6526.py`)

Statement-level port of VICE's CIA core: timer state table and warp counting,
alarms (TA, TB, SDR, TOD, idle), interrupt flag delay line with the 6526 /
6526A differences, timer B bug, serial port pipeline, TOD divider and alarm,
PB6/PB7. Clock mapping: reads, writes and the interrupt line at the machine
cycle; a released line is reported one cycle later (VICE's `IK_IRQPEND` keeps
it pending to `clk + 3` against a two-cycle sampling delay).

Integration bugs found and fixed while porting:

- the port reported "IRQ low" again while already low, which moved the
  machine's line timestamp; VICE's `interrupt_set_irq` ignores repeats;
- the next-event prediction used stale timer alarms after register access, and
  missed an interrupt edge already scheduled one cycle ahead, so idle-time
  skipping overshot by up to 5000 cycles.

### VIC-II — `vicii_sc.py` (new) and the fast model in `sid2midi.py`

Port of VICE's cycle-based VIC-II. Cycle tables are generated from the rules of
`vicii-chip-model.c` for 63/64/65-cycle chips and unit-tested against VICE's
tables; the x position is kept as `xpos / 8 * 8` exactly as VICE stores it
(keeping the raw value made sprite triggering impossible for X ≡ 0..3 mod 8).
Collision and light pen interrupts, the phi1 bus value (colour RAM upper nibble,
unmapped I/O, the RAM under `$00/$01`) and the light pen line on CIA #1 PB4 are
wired into the machine.

Performance: the graphics pixel loop only decides sprite-background priority,
so it is deferred while no sprite can draw; its end state depends only on the
inputs of its own cycle, so the last deferred cycle is replayed before a sprite
starts. 6.0 → 3.8 µs per emulated cycle.

Fast model corrections found by the differential test: NTSC sprite fetch cycles
(VICE `cycle_tab_ntsc`), DMA check cycles per standard, Y-expansion toggle after
the DMA check, `$D017` flip-flop reset and sprite crunch. After the fixes: no
difference in 60 PAL and 40 NTSC random scenarios (write cycles excluded, where
BA is decided before the write).

Conversions switch to the full chip on the first `$D013/$D014/$D01E/$D01F` read,
collision/light pen interrupt enable, or light pen line low.

Found by the corpus run: `quake.sid` converted differently from 3.0.0 after about
39 s. An I/O read log of both versions diverged at cycle 38,644,133, where
`$D016` read `$00` in 3.0.0 and `$C0` in 3.1.0, then `$D018` `$00` vs `$01`. The
player copies the whole I/O area into its data, and its `$D013` read switches
to the full chip, which reads unused bits as 1 like the real VIC-II. The fast
model still returned the stored bits; it now applies the same masks (unit test
over every register).

### CPU — `cpu6502.py`

- VICE `maincpu_steal_cycles` rules: SHx stores the un-ANDed register when the
  indexed read is stalled (the page-crossing address still uses the ANDed
  value); ANE magic `$EE` after a stalled operand read; stalled CLI loses its
  delay; stalled SEI gets no credit for held cycles. The fitted 3.0.0 PLP rule
  was removed; without it, and with the exact VIC-II BA, `irqdma` test6,
  test6b and test7b pass.
- JAM bus behaviour per SingleStepTests.
- SingleStepTests 6502 v1: all opcodes match except the 1,278 ANE vectors that
  assume magic `$EE`.

### SID — `residfp.py`

libresidfp's noise writeback differs from the VICE `wb_testsuite`/`wf12nsr`
hardware data in a few waveform transitions. Rules adopted (each fixes tests,
none breaks another): 6581 noise+pulse writes into the shift register only
while accumulator bit 19 is high; 6581 `$D/$E/$F → $C` and `$D → $E` write back;
8580 `$C → $9/$E/$F` write back, `$C → $F` with the new
waveform's output; 8580 noise+pulse output from reSID's `noise_pulse8580()`
(VICE `src/resid/wave.h`) instead of libresidfp's pulldown table. Tried and
rejected (made other tests fail): skipping the writeback for noise+pulse
entirely, gating on the shift pipeline or on the reset latch, writing back the
new waveform's output for every transition, and reSID's test-bit hold rule.
A 6581 rule "no writeback on `$F → $8`" fixed `wb_testsuite` `F_to_8_old`
but broke `SID/noiselfsrinit` `simple`/`scan` on the 6581, whose reference
data was measured on 8580 chips; it was removed (VICE passes `noiselfsrinit`
and fails `F_to_8` too).
Result: 107 of 112 SID test programs pass; the five failures also fail in VICE.

### MUS — `mus.py`

libsidplayfp's installation of Compute!'s Sidplayer data and player #1, with a
two-pass xa-subset assembler so the player can be supplied as source. The
player routine is not bundled (`--sidplayer FILE`). Checked with real MUS files
(Star Wars, Rendez-vous) and a unit test for the assembler, loader and patching.
Found and fixed: column-0 origin lines crashed the assembler.

## Known gaps

See `FINAL_CLOSURE_REPORT.md` for the failing test programs. Their status in
VICE x64sc r45942:

| Test | VICE x64sc | Cause in sid2midi |
|------|------------|-------------------|
| `VICII/spritevssprite` | fails | collision onset 4 pixels early for identical overlapping sprites; not modelled by the reference either |
| `VICII/vsp-tester`, `vsp-tester-ntsc` | fail | probe the VSP-bug memory corruption, which is random and chip-dependent; not emulated (VICE disables it by default) |
| SID `noise_writeback_check_{D,E,F}_to_C_old`, `D_to_E_old` | fail | 6581 transition writeback into noise+pulse |
| SID `noise_writeback_check_F_to_8_old` | fails | contradicts `noiselfsrinit` (8580 reference data) on the 6581 |
| SID `wf12nsr-8580` | fails | 8580 noise+pulse output while the test bit toggles (2 mismatching reads) |

Other limits: no display output; VICE's random VSP-bug memory corruption is not
emulated; on the fast path (no collisions/light pen used yet) colour RAM reads
return the stored byte.
