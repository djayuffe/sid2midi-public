# sid2midi 3.1.2 — release notes

3.1.2 fixes the last VICE test programs that 3.1.1 failed, cleans up the code
and adds developer and user documentation. Conversions of the 33-tune corpus
are byte-identical to 3.1.1 (apart from the version string).

## SID (`residfp.py`)

- **6581 LFSR initialisation by the test bit** (`SID/noiselfsrinit`): when the
  test bit rises in the same write that selects a combined noise waveform, that
  waveform's output is latched into the noise taps. This is what separates
  `noiselfsrinit` (`$F8` straight after `$80`) from `wb_testsuite F_to_8_old`
  (test bit already set by `$88`, no writeback), which 3.1.1 treated as a
  conflict. Both now pass on the 6581; `noiselfsrinit` still passes on the
  8580.
- **6581 `$D → $E` writeback** (`wb_testsuite D_to_E_old`): only noise output
  bits 11–7 are written back, as for `$D`/`$E`/`$F → $C`. An exhaustive search
  over the writeback and pulldown masks matches the test's 10 reference reads
  only with this rule.

## Code clean-up (no behaviour change)

- One statement per line throughout (checked by comparing the AST before and
  after), unused variables and an unused property removed, lambdas and a loop
  closure in the MIDI conversion turned into functions, a file handle in `main`
  now closed, exception chaining for Sidplayer loader errors.
- `ruff.toml`: lint configuration; the tree passes `ruff check .`.
- The opcode coverage tool no longer describes undocumented opcodes as NOPs
  (all 256 are implemented).

## Tools and tests

- New `tools/singlesteptests.py`: runs the SingleStepTests 6502 vectors against
  the CPU, comparing every bus access, and reports the known ANE `$EE`
  difference separately.
- New tests: `tests/test_singlesteptests_tool.py`; the new SID rules in
  `tests/test_sid_writeback_rules.py`.

## Documentation

New in `docs/`: `ARCHITECTURE.md` (modules, machine, timing, capture, drivers,
Python API), `ACCURACY.md` (evidence per chip and every deviation from the
reference implementations), `MIDI_MAPPING.md` (the MIDI output in detail),
`TESTING.md` (unit tests, VICE test programs, SingleStepTests, corpus,
debugging), `DEVELOPMENT.md` (conventions, accuracy changes, release checklist)
and an index; the README links them.

## Validation

See `FINAL_CLOSURE_REPORT.md`. VICE test programs: of the 855 available to
earlier releases, 855 pass and 0 fail (3.1.1: 852 / 3), all rerun on the final
code in one sweep; SID alone 167 of 167. The 26
exit-code programs never downloaded before were added for this release: 19 more
pass, 7 fail (power-on RAM pattern, disk autostart, three undiagnosed) and are
listed in the report as open gaps. SingleStepTests: 2,558,722 vectors match, 1,278
known ANE differences. Corpus: all 33 MIDI files byte-identical to 3.1.1 apart
from the version string. Speed unchanged (`Arkanoid.sid`, 30 s: 16.4 s CPU for
both). 134 unit tests pass on CPython 3.14 and 3.9 (13 skipped without the ROM
images).
