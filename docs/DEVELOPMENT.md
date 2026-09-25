# Development guide

How the code is organised, the conventions it follows, how to make an accuracy
change and how a release is put together. Background:
[ARCHITECTURE.md](ARCHITECTURE.md); tests: [TESTING.md](TESTING.md).

## Requirements

- Python 3.9 or newer; no third-party packages at run time. Keep the code
  3.9-compatible (no `match`, no `X | Y` type unions, no 3.10+ library calls).
- Optional: [ruff](https://docs.astral.sh/ruff/) for linting
  (`ruff check .`, configured in `ruff.toml`). The selected rule set is wide
  (pycodestyle, pyflakes, bugbear, simplify, builtins, comprehensions,
  flake8-executable, refurb, ruff, blind-except, pylint W/E/C); every exception
  is listed in `ruff.toml` with its reason. The tree passes it with no
  findings.

## Conventions

- **Ports stay ports.** `cia_vice.py`, `vicii_sc.py` and `residfp.py` keep the
  names, statement order and control flow of VICE and libresidfp, so they can
  be compared with the reference side by side. Do not restructure them for
  style (`ruff.toml` exempts them from the nested-if rules).
- **Every deviation has evidence.** A rule that differs from the reference
  carries a comment naming the test program and the reference data behind it
  (real-chip tables, readmes, sampling logs), and is listed in
  [ACCURACY.md](ACCURACY.md). Fitting a rule to make a test pass without such
  data is not accepted.
- **No claims without runs.** Documentation states measured results only: test
  counts, corpus results and speed come from runs on the released code.
- **Style.** One statement per line; `%` formatting as used throughout; lines
  up to 120 columns; comments explain *why*, not *what*; no type annotations.
- **Speed matters in three places.** The CPU step (`cpu6502.py` builds one
  closure per opcode), `VicIISC.cycle` and the reSIDfp clock run millions of
  times per conversion. Keep attribute lookups and allocations out of them, and
  prefer lazy catch-up (clock a chip only when it is accessed) to per-cycle work.
- **The fast VIC-II must agree with the port.** A change to BA, raster
  interrupt or register read-back in `Vic6569` needs the matching behaviour in
  `vicii_sc.py`, and the differential test in `tests/test_mus_vic_cpu.py`.

## Making an accuracy change

1. **Reproduce.** Run the failing program alone:
   `python3 tools/testbench.py x64-testlist.txt testprogs --filter NAME --verbose`.
2. **Find the first diverging value.** Most exit-code tests compare a table;
   run the program in-process with `tools/testbench.run_program` and log reads
   or chip state by wrapping methods, for example:

   ```python
   import sys; sys.path[:0] = [".", "tools"]
   import residfp, testbench
   orig = residfp.WaveformGenerator.readOSC
   def logged(self):
       v = orig(self)
       print("OSC3 %02X  shift register %06X" % (v, self.shift_register))
       return v
   residfp.WaveformGenerator.readOSC = logged
   testbench.run_program("noise_writeback_check_D_to_E_old.prg", 30_000_000, True, "6581")
   ```

3. **Compare with the reference** (VICE, libresidfp) statement by statement.
   If the reference passes the test, sid2midi's port differs: fix the port.
4. **If the reference fails too**, look for hardware evidence: the test's own
   expected values, its readme, or measurements published with it. When the
   data allows several explanations, test them all (for example by searching
   every candidate bit mask against the full table) and keep a rule only if it
   explains all of the data.
5. **Implement** with a comment citing the evidence, and add a unit test that
   pins the rule (see `tests/test_sid_writeback_rules.py`).
6. **Check for regressions:** unit tests, the test-program group of the
   changed chip, then the full testbench and the corpus comparison
   (`tools/validate_corpus.py` on a fixed set of tunes, compared file by file
   with the previous release). A rule that fixes one program and breaks
   another is a trade-off: record it as one, with the evidence for each side.
7. **Record** the change in `docs/ACCURACY.md`, the release notes and
   `FINAL_CLOSURE_REPORT.md`.

## Release checklist

1. Set `VERSION` in `sid2midi.py`; update the version in `README.md`,
   `QUICKSTART.md`, `REQUIREMENTS.md`, `LICENSE.md` and `PROJECT_MANIFEST.md`.
2. Write `RELEASE_NOTES_vX.Y.Z.md` and add it to `PROJECT_MANIFEST.md` and to
   the required-file list in `validate_release.sh`, together with any new
   module, test or document.
3. Run the unit tests (also on the oldest supported Python), the full
   testbench, the corpus and a speed comparison with the previous release on an
   idle machine; write the results into `FINAL_CLOSURE_REPORT.md`.
4. Remove bytecode and regenerate the manifest:

   ```bash
   find . -name __pycache__ -type d -prune -exec rm -rf {} +
   LC_ALL=C find . -path ./.git -prune -o -type f ! -name SHA256SUMS.txt ! -name .DS_Store -print \
     | LC_ALL=C sort | while IFS= read -r f; do shasum -a 256 "$f"; done > SHA256SUMS.txt
   ```

5. `./validate_release.sh` must pass, both with and without the ROM images.
6. Build the archive without `.git` and `__pycache__`, unpack it elsewhere and
   run `./validate_release.sh` there.

**ROM images.** The Commodore KERNAL, BASIC and character ROMs may be shipped in
the release archive, but they must never be committed to a public source
repository, in the tree or in its history. Everything works without them
except RSID/BASIC cold starts (see `roms/README.md`); the tests that need them
are skipped.
