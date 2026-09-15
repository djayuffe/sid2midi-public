# sid2midi 3.1.1 — release notes

3.1.1 closes most of the VICE test programs that 3.1.0 still failed. Each fix
is based on real-hardware reference data (the test programs' own tables or
the measurements published with them), including cases where the VICE
emulator itself fails. The MIDI mapping is unchanged.

## VIC-II (`vicii_sc.py`)

- **Collision register clear:** a `$D01E`/`$D01F` read also clears the
  collisions of the first 4 pixels of the cycle after the one VICE clears
  (`VICII/spritevssprite`, real-hardware table; VICE fails it).
- **DMA delay in idle state (the "VSP" case):** when a bad line is triggered
  while the VIC-II is idle, that cycle's idle byte is fetched from `$38FF`
  (6569) or `$3807` (8565/8566) instead of `$3FFF` (`VICII/vsp-tester` readme,
  measured on real machines; VICE fails both variants). The NTSC 6567 uses the
  6569 address by analogy; the NTSC variant of the test passes with it.

## Fast VIC-II path (`sid2midi.py`)

- Colour RAM reads (the upper nibble is the open bus) and unmapped I/O reads
  (`$DE00-$DFFF`) now switch to the full VIC-II, so the value is exact. Colour
  RAM copies made by the KERNAL screen editor, which never uses the upper
  nibble, stay on the fast model so BASIC programs keep their speed. The corpus
  conversions are unchanged.

## SID (`residfp.py`)

- **8580 noise+pulse read-back:** OSC3 pulls one more bit low than the value
  written back into the shift register (`SID/wf12nsr-8580`; VICE fails it).
- **6581 writeback into noise+pulse:** switching from `$D`/`$E`/`$F` to `$C`
  writes back only the three lowest noise output bits (register taps 22, 20
  and 17), taken from the register. `$F -> $C` is identical on both 6581 chips
  measured for `SID/wb_testsuite`; `$D`/`$E -> $C` matches chip 2783, whose
  data the tests use.
- **6581 `$F -> $8`:** no writeback, as measured on both 6581 chips
  (`wb_testsuite` `F_to_8_old`). `SID/noiselfsrinit` expects a writeback here
  but its reference data was measured on 8580 chips only; its two programs now
  fail on the 6581 (they still pass on the 8580).

## Tests

- New `tests/test_fast_vic_open_bus.py` and `tests/test_sid_writeback_rules.py`;
  VSP idle fetch tests in `tests/test_mus_vic_cpu.py`.

## Validation

VICE test programs: 852 pass, 3 fail (3.1.0: 846 / 9); VIC-II and interrupts
76 of 76, SID 164 of 167. Corpus of 33 files: status and note count identical
to 3.1.0. Conversion speed unchanged (`Arkanoid.sid`, 30 s: 16.1 s CPU).
127 unit tests pass (13 skipped without the ROM images).

## Still failing

See `FINAL_CLOSURE_REPORT.md`. Known: `SID/wb_testsuite`
`noise_writeback_check_D_to_E_old` (the two measured 6581 chips disagree and
chip 2783 marks its result unstable) and `SID/noiselfsrinit` on the 6581 (see
above).
