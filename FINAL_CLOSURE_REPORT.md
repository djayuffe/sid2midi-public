# sid2midi 3.1.2 — Validation Report

Date: 2026-09-16. Platform: macOS arm64, CPython 3.14.2 (unit tests also on
CPython 3.9.6).

## 1. Release validation

```text
./validate_release.sh
  Python syntax check, unit tests, opcode coverage, smoke conversion,
  SHA256 manifest, release file layout
```

Unit tests: 134 pass on CPython 3.14.2 and on 3.9.6. They include all 41,664
register-access cases of Lorenz `cia1ta`/`cia1tb` (`tests/test_cia6526_lorenz.py`,
against `cia_vice.py`), libresidfp's own unit tests (`tests/test_residfp.py`),
the MUS loader, VIC-II cycle tables, fast-vs-full VIC-II register read-back,
CIA port B, RDY-dependent CPU rules and the VSP idle fetch
(`tests/test_mus_vic_cpu.py`), fast-path open-bus reads
(`tests/test_fast_vic_open_bus.py`), the SID writeback rules
(`tests/test_sid_writeback_rules.py`) and the SingleStepTests harness
(`tests/test_singlesteptests_tool.py`). Without the C64 ROM images (public
source tree) 134 tests run, 13 of them skipped.

Lint: `ruff check .` reports no findings (configuration in `ruff.toml`).

## 2. CPU against SingleStepTests

`tools/singlesteptests.py` (new in 3.1.2) on the final CPU code: all 256
opcodes, 2,560,000 vectors, compared cycle by cycle (address, value,
read/write) and by final registers and memory.

```text
$8B: 10000 vectors, 8722 pass, 1278 known, 0 FAIL
summary: 256 opcodes, 2560000 vectors, 2558722 pass, 1278 known (ANE magic $EE), 0 fail
```

The 1,278 ANE (`$8B`) vectors assume the magic constant `$EE`; sid2midi uses
`$EF` as measured on the C64 (and `$EE` when BA holds the operand read, as VICE
does).

## 3. Test programs on the emulated machine

`tools/testbench.py` with VICE `testbench/x64-testlist.txt`: every exit-code
test whose program was available. Machine: PAL breadbin C64 unless the test
options select NTSC (6567R8), old NTSC (6567R56A), new VIC-II (8565/8562), new
CIA (6526A) or the 8580 SID; always the full VIC-II (`vicii_sc.py`).

Every one of these programs was run on the final 3.1.2 code, in one sweep of
the whole test list (`summary: PASS 855`), not only the groups whose code
changed.

| Group | 3.1.0 | 3.1.1 | 3.1.2 |
|-------|-------|-------|-------|
| Lorenz 2.15 | 314 / 0 | 314 / 0 | 314 / 0 |
| CIA | 184 / 0 | 184 / 0 | 184 / 0 |
| CPU | 114 / 0 | 114 / 0 | 114 / 0 |
| interrupts | 39 / 0 | 39 / 0 | 39 / 0 |
| VIC-II | 34 / 3 | 37 / 0 | 37 / 0 |
| SID | 161 / 6 | 164 / 3 | 167 / 0 |
| **Total** | **846 / 9** | **852 / 3** | **855 / 0** |

No failures, no timeouts, no crashes among these 855 programs. The three
programs 3.1.1 still failed —
`SID/wb_testsuite/noise_writeback_check_D_to_E_old` and `SID/noiselfsrinit`
`simple` and `scan` on the 6581 — pass in 3.1.2; see `RELEASE_NOTES_v3.1.2.md`
and the audit for the evidence behind the two rules.

### Programs obtained for this release

The 26 exit-code programs that earlier runs had not downloaded were fetched and
run, raising the runnable set from 855 to 881. 19 pass; 7 fail and are new,
honestly open gaps rather than regressions:

| Program(s) | Result | Cause |
|------------|--------|-------|
| `C64/raminitpattern/cyberloadtest`, `darkstarbbstest`, `platoontest` | fail | sid2midi fills RAM with `$00` at power-on and emulates no RAM initialisation pattern; `typicaltest` of the same group passes |
| `C64/autostart/defaults/test` | fail | expects to be autostarted from a disk image under the name `TEST`; the testbench loads and runs the PRG directly |
| `C64/bankio/bankio` | fail | not diagnosed |
| `general/fuxxortest/test-fuxxored` | timeout | not diagnosed |
| `general/fuxxortest/ef2-inst1` | fail | not diagnosed; the other seven programs of the group pass |

The four 6526A shift-register programs (`CIA/shiftregister/*-new`), both
testbench self-tests, the BASIC autostart programs, `C64/openio/gauntlet`,
`general/banking00` and `general/ram0001/test1` are among the 19 that pass.

Not run (185): programs for hardware sid2midi does not emulate (REU 87, disk
drive 24, memory expansions 6, GEO-RAM 3, +60K/+256K 3) and tests that mount
disk, cartridge, G64 or P64 images (62).

## 4. Real-world corpus

The 33 PSID/RSID files of the earlier reports, `tools/validate_corpus.py
--seconds 180 --timeout 3600`, final 3.1.2 code; every MIDI checked by
`tools/midicheck.py`.

| Result  | 2.1.0 | 3.0.0 | 3.1.0 | 3.1.1 | 3.1.2 |
|---------|-------|-------|-------|-------|-------|
| OK      | 25    | 25    | 25    | 25    | 25    |
| SILENT  | 8     | 8     | 8     | 8     | 8     |
| INVALID | 0     | 0     | 0     | 0     | 0     |
| CRASH   | 0     | 0     | 0     | 0     | 0     |
| TIMEOUT | 0     | 0     | 0     | 0     | 0     |

All 33 MIDI files are byte-identical to the 3.1.1 run apart from the version
string in the meta track, which is what the code clean-up had to achieve. The 8
silent files are the defective inputs identified in earlier reports.

## 5. Conversion speed

`Arkanoid.sid`, 30 s of C64 time, idle machine, CPU time of two alternating runs
each: 3.1.1 16.4 s and 16.4 s, 3.1.2 16.4 s and 16.5 s. The MIDI files are
identical apart from the version string. Tunes that read colour RAM or unmapped
I/O from their own code, or use sprite collisions or the light pen, run the full
VIC-II (about 3.8 µs per emulated cycle for the VIC-II alone) and convert
several times slower.

## 6. Scope boundary

sid2midi extracts SID register intent into MIDI; it does not produce audio.
What each chip model is checked against, and every deviation from the reference
implementations with its evidence, is in `docs/ACCURACY.md`. MUS files need the
Compute!'s Sidplayer player routine, which is not included (`--sidplayer FILE`).
The C64 ROM images are in the release archive but not in the public source
repository (`roms/README.md`).
