# sid2midi 3.1.4 — Validation Report

Date: 2026-09-25. Platform: macOS arm64, CPython 3.14.2 (unit tests also on
CPython 3.9.6).

## 1. Release validation

```text
./validate_release.sh
  Python syntax check, unit tests, opcode coverage, smoke conversion,
  SHA256 manifest, release file layout
```

Unit tests: 136 pass on CPython 3.14.2 and on 3.9.6. They include all 41,664
register-access cases of Lorenz `cia1ta`/`cia1tb` (`tests/test_cia6526_lorenz.py`,
against `cia_vice.py`), libresidfp's own unit tests (`tests/test_residfp.py`),
the MUS loader, VIC-II cycle tables, fast-vs-full VIC-II register read-back,
CIA port B, RDY-dependent CPU rules and the VSP idle fetch
(`tests/test_mus_vic_cpu.py`), fast-path open-bus reads
(`tests/test_fast_vic_open_bus.py`), the SID writeback and bus-value rules
(`tests/test_sid_writeback_rules.py`, `tests/test_sid2midi_hardware.py`),
colour-RAM separation and the power-on RAM pattern
(`tests/test_sid2midi_release.py`) and the SingleStepTests harness
(`tests/test_singlesteptests_tool.py`). Without the C64 ROM images (public
source tree) 136 tests run, 13 of them skipped.

Lint: `ruff check .` reports no findings. 3.1.4 widened the rule set
(pycodestyle, pyflakes, bugbear, simplify, builtins, comprehensions,
flake8-executable, refurb, ruff, blind-except, pylint W/E/C); every exception
is listed in `ruff.toml` with its reason.

## 2. CPU against SingleStepTests

`cpu6502.py` is unchanged since 3.1.2, whose run of `tools/singlesteptests.py`
covered all 256 opcodes and 2,560,000 vectors cycle by cycle:

```text
summary: 256 opcodes, 2560000 vectors, 2558722 pass, 1278 known (ANE magic $EE), 0 fail
```

The 1,278 ANE (`$8B`) vectors assume the magic constant `$EE`; sid2midi uses
`$EF` as measured on the C64 (and `$EE` when BA holds the operand read, as VICE
does).

## 3. Test programs on the emulated machine

`tools/testbench.py` with VICE `testbench/x64-testlist.txt`: every exit-code
test whose program is available (881 of the 1,066 listed). All of them were run
on the final 3.1.4 code in one sweep (70,709 s of emulation). Machine: PAL breadbin C64 unless the test
options select NTSC (6567R8), old NTSC (6567R56A), new VIC-II (8565/8562), new
CIA (6526A) or the 8580 SID; always the full VIC-II (`vicii_sc.py`).

| Group | 3.1.2 | 3.1.3 | 3.1.4 |
|-------|-------|-------|-------|
| general (incl. Lorenz 2.15) | 322 / 2 | 322 / 2 | 322 / 2 |
| CIA | 188 / 0 | 188 / 0 | 188 / 0 |
| SID | 167 / 0 | 167 / 0 | 167 / 0 |
| CPU | 114 / 0 | 114 / 0 | 114 / 0 |
| interrupts | 39 / 0 | 39 / 0 | 39 / 0 |
| VIC-II | 37 / 0 | 37 / 0 | 37 / 0 |
| C64 | 5 / 5 | 9 / 1 | 9 / 1 |
| testbench self-test | 2 / 0 | 2 / 0 | 2 / 0 |
| **Total** | **874 / 7** | **878 / 3** | **878 / 3** |

3.1.4 is a clean-up release: its results are identical to 3.1.3, which fixed
`C64/bankio/bankio` (colour RAM and the SID bus value) and
`C64/raminitpattern/cyberloadtest`, `darkstarbbstest`, `platoontest` (power-on
RAM pattern). Evidence: `RELEASE_NOTES_v3.1.3.md` and `docs/ACCURACY.md`.

The three remaining failures need hardware sid2midi does not emulate, as their
own readmes state:

| Test | Reason |
|------|--------|
| `general/fuxxortest/ef2-inst1` | "fails without true drive emulation" (1541) |
| `general/fuxxortest/test-fuxxored` | bundles that drive test; times out when it fails |
| `C64/autostart/defaults/test` | "must be loaded from disk using LOAD"TEST",8 - else it will fail" |

Not run (185): programs for hardware sid2midi does not emulate (REU 87, disk
drive 24, memory expansions 6, GEO-RAM 3, +60K/+256K 3) and tests that mount
disk, cartridge, G64 or P64 images (62).

## 4. Real-world corpus

The 33 PSID/RSID files of the earlier reports, `tools/validate_corpus.py
--seconds 180 --timeout 3600`, final 3.1.4 code against the 3.1.3 run; every
MIDI checked by `tools/midicheck.py`.

| Result  | 3.0.0 | 3.1.0 | 3.1.1 | 3.1.2 | 3.1.3 | 3.1.4 |
|---------|-------|-------|-------|-------|-------|-------|
| OK      | 25    | 25    | 25    | 25    | 25    | 25    |
| SILENT  | 8     | 8     | 8     | 8     | 8     | 8     |
| INVALID | 0     | 0     | 0     | 0     | 0     | 0     |
| CRASH   | 0     | 0     | 0     | 0     | 0     | 0     |
| TIMEOUT | 0     | 0     | 0     | 0     | 0     | 0     |

All 33 files keep their status and note count, and all 33 MIDI files are
byte-identical to the 3.1.3 run apart from the version string: the clean-up
changed no output. (3.1.3 itself changed `quake.sid` from 656 to 647 notes
through the colour-RAM fix; see that release's notes.)

## 5. Conversion speed

`Arkanoid.sid`, 30 s of C64 time, CPU time of two alternating runs each:
3.1.3 16.8 s and 16.7 s, 3.1.4 16.7 s and 16.7 s (the machine was not fully
idle, so only the comparison is meaningful). Creating a `C64` object is 24x
cheaper in 3.1.4 (7.2 ms to 0.3 ms) because the power-on RAM pattern is built
once and copied; that shows up in the test harness rather than in conversions. Tunes that read colour RAM or
unmapped I/O from their own code, or use sprite collisions or the light pen, run
the full VIC-II (about 3.8 µs per emulated cycle for the VIC-II alone) and
convert several times slower.

## 6. Scope boundary

sid2midi extracts SID register intent into MIDI; it does not produce audio.
What each chip model is checked against, and every deviation from the reference
implementations with its evidence, is in `docs/ACCURACY.md`. MUS files need the
Compute!'s Sidplayer player routine, which is not included (`--sidplayer FILE`).
The C64 ROM images are in the release archive but not in the public source
repository (`roms/README.md`).
