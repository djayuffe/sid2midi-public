# sid2midi 3.1.3 — Validation Report

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

Lint: `ruff check .` reports no findings (configuration in `ruff.toml`).

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
on the final 3.1.3 code in one sweep. Machine: PAL breadbin C64 unless the test
options select NTSC (6567R8), old NTSC (6567R56A), new VIC-II (8565/8562), new
CIA (6526A) or the 8580 SID; always the full VIC-II (`vicii_sc.py`).

| Group | 3.1.2 | 3.1.3 |
|-------|-------|-------|
| general (incl. Lorenz 2.15) | 322 / 2 | 322 / 2 |
| CIA | 188 / 0 | 188 / 0 |
| SID | 167 / 0 | 167 / 0 |
| CPU | 114 / 0 | 114 / 0 |
| interrupts | 39 / 0 | 39 / 0 |
| VIC-II | 37 / 0 | 37 / 0 |
| C64 | 5 / 5 | 9 / 1 |
| testbench self-test | 2 / 0 | 2 / 0 |
| **Total** | **874 / 7** | **878 / 3** |

Fixed in 3.1.3: `C64/bankio/bankio` (colour RAM and the SID bus value) and
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
--seconds 180 --timeout 3600`, final 3.1.3 code against a fresh 3.1.2 run; every
MIDI checked by `tools/midicheck.py`.

| Result  | 3.0.0 | 3.1.0 | 3.1.1 | 3.1.2 | 3.1.3 |
|---------|-------|-------|-------|-------|-------|
| OK      | 25    | 25    | 25    | 25    | 25    |
| SILENT  | 8     | 8     | 8     | 8     | 8     |
| INVALID | 0     | 0     | 0     | 0     | 0     |
| CRASH   | 0     | 0     | 0     | 0     | 0     |
| TIMEOUT | 0     | 0     | 0     | 0     | 0     |

32 of the 33 files keep their status and note count; 29 MIDI files are
byte-identical to 3.1.2. The four that differ are caused by the colour-RAM fix,
confirmed by rerunning `quake.sid` with each 3.1.3 change reverted in turn
(reverting the RAM pattern or the bus-value rule still gives the new result):

- `quake.sid`: 656 → 647 notes. The player copies the whole I/O area into its
  data; colour RAM is now its own memory, so the copied bytes are the ones a
  real C64 returns (`C64/bankio`).
- three `midi_import*.sid`: defective files (data past `$FFFF`, every player
  call ends on BRK) that produce no notes; their meta/controller bytes differ.

## 5. Conversion speed

`Arkanoid.sid`, 30 s of C64 time, CPU time of two alternating runs each:
3.1.2 17.4 s and 17.2 s, 3.1.3 17.3 s and 17.2 s (the machine was not fully
idle, so only the comparison is meaningful). Tunes that read colour RAM or
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
