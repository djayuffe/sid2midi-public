# sid2midi 3.1.x Audit — reference ports and hardware-evidence fixes

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
data was measured on 8580 chips; 3.1.0 removed it (VICE passes `noiselfsrinit`
and fails `F_to_8` too). 3.1.1 restores it on the evidence of the real 6581
sampling logs (see "Changes in 3.1.1").
3.1.0 result over all 167 SID test programs: 161 pass, 6 fail (3.1.0 column of
`FINAL_CLOSURE_REPORT.md`).

### MUS — `mus.py`

libsidplayfp's installation of Compute!'s Sidplayer data and player #1, with a
two-pass xa-subset assembler so the player can be supplied as source. The
player routine is not bundled (`--sidplayer FILE`). Checked with real MUS files
(Star Wars, Rendez-vous) and a unit test for the assembler, loader and patching.
Found and fixed: column-0 origin lines crashed the assembler.

## Changes in 3.1.1

3.1.1 closes most of the programs 3.1.0 still failed, each with real-hardware
evidence: the programs' own reference tables, the readme measurements or the
sampling logs published with them. VICE itself fails `spritevssprite`, both
`vsp-tester` variants, `wf12nsr-8580` and the 6581 `D`/`E`/`F → C` and `F → 8`
writeback programs.

- **Collision clear tail (VIC-II):** `VICII/spritevssprite` reads `$D01E` twice
  per line while two identical sprites move one pixel per frame. A trace showed
  the collision band starting exactly 4 pixels before the reference table and
  ending on the same pixel, so the read's clear extends 4 pixels further than
  VICE models. With the clear also dropping pixels 0–3 of the following cycle,
  the program and all 17 other collision, collision-IRQ and sprite-X programs
  pass. (Shifting the drawing window by 4 pixels instead broke 12 programs.)
- **VSP idle fetch (VIC-II):** `VICII/vsp-tester` opens the borders, triggers a
  bad line in idle state with a `$D011` write near cycle 53, and reads the idle
  byte back through the sprite-background collision register. Its readme gives
  the addresses measured on real machines: `$38FF` (6569) and `$3807`
  (8565/8566). Fetching from these in the trigger cycle makes both variants pass
  and shows `38FF` on screen; the NTSC 6567 uses the 6569 address by analogy.
- **8580 noise+pulse OSC3 (SID):** `SID/wf12nsr-8580` differed only in the two
  reads taken while noise+pulse was selected (`$FC` vs `$F8`); the register
  contents already matched. One extra `noise & (noise << 1)` on the OSC3 read,
  with the writeback value unchanged, passes it and keeps all 8580 `C_to_x_new`
  writeback programs passing.
- **6581 writeback into noise+pulse (SID):** the four 6581 sampling logs
  published with `wb_testsuite` (chips 2586 and 2783) show `$F -> $C` identical on
  both chips, while `$D -> $C`, `$E -> $C` and `$D -> $E` differ between the chips
  and are marked unstable on chip 2783, whose data the tests use. Reconstructing
  the register from the `$F -> $C` reads showed that only register taps 22, 20
  and 17 are cleared, i.e. a writeback of `noise & $F80`. Applied to
  `$D`/`$E`/`$F -> $C`, it passes those three programs.
- **6581 `$F -> $8` (SID):** both 6581 chips show a plain shift (no writeback),
  so that rule was restored; `SID/noiselfsrinit`, whose reference data comes
  from 8580 chips only, now fails on the 6581.
- **Fast VIC-II path:** colour RAM and unmapped I/O reads switch to the full chip
  (except KERNAL colour RAM copies); the corpus results did not change.

## Changes in 3.1.2

3.1.2 closes the last SID programs that 3.1.1 failed, again from the test
programs' own data.

- **`SID/noiselfsrinit` and `wb_testsuite F_to_8_old` are not in conflict.**
  Disassembly shows both switch from waveform `$F` with the test bit to
  waveform `$8` without it. `noiselfsrinit` writes `$F8` straight after `$80`,
  so the test bit rises in the same write that selects the combined waveform;
  `F_to_8_old` writes `$88` first, so the test bit is already set when `$F8`
  arrives. Rule: on the 6581, a test-bit rise together with a combined noise
  waveform latches that waveform's output (accumulator 0) into the noise taps;
  the test-bit fall `$F → $8` itself still writes nothing back. Both
  `noiselfsrinit` programs pass on the 6581 and the 8580, and `F_to_8_old`
  still passes.
- **`wb_testsuite D_to_E_old`.** A trace showed libresidfp clearing every noise
  tap (the `$D` output at accumulator 0 is zero), while the table's first read
  `$FC` shows only tap 22 cleared before the first shift. All 65,536
  combinations of a writeback mask at the test-bit fall and a pulldown mask
  during the following `$E` cycles were simulated with the exact register
  shift: the 10 expected reads match only when the writeback keeps noise output
  bits 11–7, the same `noise & $F80` rule as `$D`/`$E`/`$F → $C`. The rule was
  extended to `$D → $E`; the neighbouring 6581 programs still pass.

Code clean-up (no behaviour change): one statement per line (verified by
comparing the AST before and after), unused variables and an unused property
removed, lambdas and a loop closure turned into functions, a `ruff.toml` lint
configuration; the SingleStepTests harness is now shipped as
`tools/singlesteptests.py`.

## Known gaps

See `FINAL_CLOSURE_REPORT.md` for the results.

Of the programs available to the earlier runs, none fails in 3.1.2: 855 of 855,
all rerun on the final code in one sweep of the whole test list. The 26
programs that had never been downloaded were then fetched: 19 pass, 7 fail
(power-on RAM pattern, disk autostart, and three undiagnosed); the validation
report lists them. Remaining limits are listed in `docs/ACCURACY.md`: real
6581 chips differ from each other in some noise writeback transitions (sid2midi
follows chip 2783, which the tests were built from), and the NTSC 6567 VSP
idle-fetch address is not measured.

Other limits: no display output; VICE's random VSP-bug memory corruption is not
emulated; on the fast path, colour RAM copies made by the KERNAL screen editor
return the stored nibble.
