# sid2midi 3.1.1 — Validation Report

Date: 2026-09-15. Platform: macOS arm64, CPython 3.14.2.

## 1. Release validation

```text
./validate_release.sh
  Python syntax check, unit tests, opcode coverage, smoke conversion,
  SHA256 manifest, release file layout
```

Unit tests: 127 pass. They include all 41,664 register-access cases of Lorenz
`cia1ta`/`cia1tb` (`tests/test_cia6526_lorenz.py`, against `cia_vice.py`),
libresidfp's own unit tests (`tests/test_residfp.py`), the MUS loader, VIC-II
cycle tables, fast-vs-full VIC-II register read-back, CIA port B, RDY-dependent
CPU rules and the VSP idle fetch (`tests/test_mus_vic_cpu.py`), fast-path
open-bus reads (`tests/test_fast_vic_open_bus.py`) and the SID writeback rules
added in 3.1.1 (`tests/test_sid_writeback_rules.py`). Without the C64 ROM
images (public source tree) 127 tests run, 13 of them skipped.

## 2. CPU against SingleStepTests

SingleStepTests 6502 v1: all 256 opcodes, 2,560,000 vectors, compared cycle by
cycle (address, value, read/write) and by final registers and memory. Every
vector matches except 1,278 ANE (`$8B`) vectors, which assume the magic constant
`$EE`; sid2midi uses `$EF` as measured on the C64 (and `$EE` when BA holds the
operand read, as VICE does). The CPU core is unchanged since 3.1.0, which was
checked on these vectors.

## 3. Test programs on the emulated machine

`tools/testbench.py` with VICE `testbench/x64-testlist.txt`: every exit-code
test whose program was available. Machine: PAL breadbin C64 unless the test
options select NTSC (6567R8), old NTSC (6567R56A), new VIC-II (8565/8562), new
CIA (6526A) or the 8580 SID; always the full VIC-II (`vicii_sc.py`).

3.1.1 changed only the VIC-II and SID code, so the VIC-II, interrupts and SID
groups were run again on the final 3.1.1 code. The Lorenz, CPU and CIA rows
come from the 3.1.0 full run; the CPU and CIA code has not changed since.

| Group | 3.1.0 pass / fail | 3.1.1 pass / fail |
|-------|-------------------|-------------------|
| Lorenz 2.15 | 314 / 0 | 314 / 0 |
| CPU | 114 / 0 | 114 / 0 |
| interrupts | 39 / 0 | 39 / 0 |
| CIA | 184 / 0 | 184 / 0 |
| VIC-II | 34 / 3 | 37 / 0 |
| SID | 161 / 6 | 164 / 3 |
| **Total** | **846 / 9** | **852 / 3** |

No timeouts, no crashes.

Fixed in 3.1.1 (all fail in VICE x64sc r45942, 2026-01-13):
`VICII/spritevssprite`, `VICII/vsp-tester/vsp-tester`, `vsp-tester-ntsc`,
`SID/wf12nsr/wf12nsr-8580`, `SID/wb_testsuite/noise_writeback_check_D_to_C_old`,
`E_to_C_old`, `F_to_C_old` and `F_to_8_old`. Details and evidence:
`RELEASE_NOTES_v3.1.1.md` and the audit's "Changes in 3.1.1".

Failing:

| Test | VICE x64sc | Cause |
|------|------------|-------|
| `SID/wb_testsuite/noise_writeback_check_D_to_E_old.prg` | fails | 6581 `$D -> $E` writeback; the two measured 6581 chips disagree and chip 2783, whose data the test uses, marks the result unstable |
| `SID/noiselfsrinit/simple.prg` (6581) | passes | newly failing: 3.1.1 applies "no writeback on `$F -> $8`" on the 6581 as measured on both 6581 chips of `wb_testsuite`; this program's reference data was measured on 8580 chips only |
| `SID/noiselfsrinit/scan.prg` (6581) | passes | as above |

The `noiselfsrinit` programs are a deliberate trade-off: real 6581 measurements
(`F_to_8_old` and the published sampling logs) were preferred over 8580 data
applied to the 6581. Both programs still pass with the 8580.

Not run (211): programs for hardware sid2midi does not emulate (REU 87, disk
drive 24, memory expansions 6, GEO-RAM 3, +60K/+256K 3); tests that mount disk
or cartridge images (62); programs not downloaded for this run (26).

## 4. Real-world corpus

The 33 PSID/RSID files of the earlier reports, `tools/validate_corpus.py
--seconds 180 --timeout 3600 --jobs 3`, final 3.1.1 code; every MIDI checked by
`tools/midicheck.py`.

| Result  | 2.1.0 | 3.0.0 | 3.1.0 | 3.1.1 |
|---------|-------|-------|-------|-------|
| OK      | 25    | 25    | 25    | 25    |
| SILENT  | 8     | 8     | 8     | 8     |
| INVALID | 0     | 0     | 0     | 0     |
| CRASH   | 0     | 0     | 0     | 0     |
| TIMEOUT | 0     | 0     | 0     | 0     |

Compared file by file with the 3.1.0 run: status and note count are identical
for all 33 files (`quake.sid` 656 notes, see the 3.1.0 report for why it
differs from 3.0.0). The 8 silent files are the defective inputs identified in
earlier reports. The run shared the machine with the test program runs, so its
times are not a speed measurement.

## 5. Conversion speed

`Arkanoid.sid`, 30 s of C64 time, idle machine, CPU time of two alternating runs
each: 3.1.0 16.1 s and 16.2 s, 3.1.1 16.1 s and 16.1 s. The MIDI files are
identical apart from the version string. Tunes that read colour RAM or unmapped
I/O from their own code, or use sprite collisions or the light pen, run the full
VIC-II (about 3.8 µs per emulated cycle for the VIC-II alone) and convert
several times slower.

## 6. Scope boundary

sid2midi extracts SID register intent into MIDI; it does not produce audio.
See README "Accuracy boundary" and `docs/audits/SID2MIDI_3_1_0_AUDIT.md`.
MUS files need the Compute!'s Sidplayer player routine, which is not included
(`--sidplayer FILE`). The C64 ROM images are in the release archive but not in
the public source repository (`roms/README.md`).
