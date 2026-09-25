# sid2midi 3.1.4 — release notes

A clean-up release: no behaviour changes to the emulation or the MIDI output,
but the code, the lint rules, the documentation and the repository metadata are
brought in line with 3.1.3's results.

## Code

- `power_on_ram()` builds the C64 power-on pattern once and copies it, instead
  of running a 64 K loop for every machine: creating a `C64` went from 7.2 ms to
  0.3 ms, which matters for the test harness and batch conversions.
- `mus.credits()` is now `mus.credit_lines()` (the old name shadowed a Python
  builtin), `re.S` spelled `re.DOTALL`, an unnecessary dict comprehension
  removed, a chained comparison simplified, and `subprocess.run` calls in the
  tools and tests state `check=` explicitly.
- Scripts and tools that carry a `#!` line are executable.

## Lint

`ruff.toml` now selects a much wider rule set (pycodestyle, pyflakes, bugbear,
simplify, builtins, comprehensions, flake8-executable, refurb, ruff, blind
except, pylint W/E/C) and the tree passes it. Every remaining exception is
listed in the file with its reason: the ports keep their reference control flow
and `__slots__` order, the opcode table in `mus.py` keeps its `dict(...)` form,
and the code carries no type annotations.

## Documentation

- The validation report, the accuracy document and the audit describe 3.1.3's
  results: 878 of 881 test programs pass, and the three failures need hardware
  sid2midi does not emulate.
- Both repository descriptions state the current result instead of the old
  855-program figure, and the repositories carry topics.

## Validation

See `FINAL_CLOSURE_REPORT.md`: the test programs, the corpus and the unit tests
were rerun on the final 3.1.4 code.
