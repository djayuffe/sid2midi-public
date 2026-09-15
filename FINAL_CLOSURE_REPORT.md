# sid2midi 3.1.0 — Validation Report

Date: 2026-09-15. Platform: macOS arm64, CPython 3.14.2.

## 1. Release validation

```text
./validate_release.sh
  Python syntax check, unit tests, opcode coverage, smoke conversion,
  SHA256 manifest, release file layout
```

Unit tests: 115 pass. They include all 41,664 register-access cases of Lorenz
`cia1ta`/`cia1tb` (`tests/test_cia6526_lorenz.py`, now against `cia_vice.py`),
libresidfp's own unit tests (`tests/test_residfp.py`), and the MUS loader,
VIC-II cycle tables, fast-vs-full VIC-II register read-back, CIA port B and
RDY-dependent CPU rules (`tests/test_mus_vic_cpu.py`). Without the C64 ROM
images (public source tree) 115 tests pass with 12 skipped.

## 2. CPU against SingleStepTests

SingleStepTests 6502 v1 on the final CPU code: all 256 opcodes, 2,560,000
vectors, compared cycle by cycle (address, value, read/write) and by final
registers and memory. Every vector matches except 1,278 ANE (`$8B`) vectors,
which assume the magic constant `$EE`; sid2midi uses `$EF` as measured on the
C64 (and `$EE` when BA holds the operand read, as VICE does).

## 3. Test programs on the emulated machine

`tools/testbench.py` with VICE `testbench/x64-testlist.txt`: every exit-code
test whose program was available. Machine: PAL breadbin C64 unless the test
options select NTSC (6567R8), old NTSC (6567R56A), new VIC-II (8565/8562), new
CIA (6526A) or the 8580 SID; always the full VIC-II (`vicii_sc.py`).

The full run used the final CPU, CIA and VIC-II code. The SID group was run
again on the final SID code after two SID fixes made during the full run; its
results replace the SID rows of the full run.

| Group | Pass | Fail |
|-------|------|------|
| Lorenz 2.15 | 314 | 0 |
| CPU | 114 | 0 |
| interrupts | 39 | 0 |
| CIA | 184 | 0 |
| VIC-II | 34 | 3 |
| SID | 161 | 6 |
| **Total** | **846** | **9** |

No timeouts, no crashes. For comparison, 3.0.0: 486 pass, 4 fail on 490
programs (a smaller selection; no 6526A, shift register, TOD, sprite
collision, light pen or `wb_testsuite` programs).

Failing, with their status in VICE x64sc r45942 (2026-01-13):

| Test | VICE x64sc | Cause |
|------|------------|-------|
| `VICII/vsp-tester/vsp-tester.prg` | fails | probes the VSP-bug memory corruption, random and chip-dependent; not emulated |
| `VICII/vsp-tester/vsp-tester-ntsc.prg` | fails | as above |
| `VICII/spritevssprite/spritevssprite.prg` | fails | collision onset 4 pixels early for identical overlapping sprites |
| `SID/wb_testsuite/noise_writeback_check_D_to_C_old.prg` | fails | 6581 transition writeback into noise+pulse |
| `SID/wb_testsuite/noise_writeback_check_D_to_E_old.prg` | fails | as above |
| `SID/wb_testsuite/noise_writeback_check_E_to_C_old.prg` | fails | as above |
| `SID/wb_testsuite/noise_writeback_check_F_to_C_old.prg` | fails | as above |
| `SID/wb_testsuite/noise_writeback_check_F_to_8_old.prg` | fails | its 6581 data contradicts `SID/noiselfsrinit` (8580 reference data), which passes |
| `SID/wf12nsr/wf12nsr-8580.prg` | fails | 8580 noise+pulse output while the test bit toggles (2 reads differ) |

Fixed since 3.0.0 among the programs 3.0.0 ran: `interrupts/irqdma/test6`,
`test6b`, `interrupts/irq-ackn-bug/irq-ack-vicii`.

Not run (211): programs for hardware sid2midi does not emulate (REU 87, disk
drive 24, memory expansions 6, GEO-RAM 3, +60K/+256K 3); tests that mount disk
or cartridge images (62); programs not downloaded for this run (26).

## 4. Real-world corpus

The 33 PSID/RSID files of the earlier reports, `tools/validate_corpus.py
--seconds 180 --timeout 3600 --jobs 2`, final 3.1.0 code; every MIDI checked by
`tools/midicheck.py`.

| Result  | 2.1.0 (180 s) | 3.0.0 (180 s) | 3.1.0 (180 s) |
|---------|---------------|---------------|---------------|
| OK      | 25            | 25            | 25            |
| SILENT  | 8             | 8             | 8             |
| INVALID | 0             | 0             | 0             |
| CRASH   | 0             | 0             | 0             |
| TIMEOUT | 0             | 0             | 0             |

Compared file by file with the 3.0.0 report's corpus run (same settings),
status and note count are identical for 32 of the 33 files. The exception is
`quake.sid` (3.0.0: 660, 3.1.0: 656; a fresh 180 s conversion with the released
3.0.0 code also gives 660). Its player copies the whole I/O area into its data;
an I/O read log of both versions first differs at cycle 38,644,133, where
`$D016` reads `$00` in 3.0.0 and `$C0` in 3.1.0 (then `$D018` `$00`/`$01`). The
real VIC-II reads unused register bits as 1, so the 3.1.0 output is the correct
one. The tune's `$D013` read switches the machine to the full VIC-II, which
makes it the slowest conversion (1,020 s; 3.0.0: 409 s). The 8 silent files are
the defective inputs identified in earlier reports.

## 5. Conversion speed

`Arkanoid.sid`, 30 s of C64 time, idle machine, CPU time of two runs each:
3.0.0 15.1 s, 3.1.0 16.1 s. The MIDI files are identical apart from the version
string. Tunes that use sprite collisions or the light pen run the full VIC-II
(about 3.8 µs per emulated cycle for the VIC-II alone) and convert several
times slower.

## 6. Scope boundary

sid2midi extracts SID register intent into MIDI; it does not produce audio.
See README "Accuracy boundary" and `docs/audits/SID2MIDI_3_1_0_AUDIT.md`.
MUS files need the Compute!'s Sidplayer player routine, which is not included
(`--sidplayer FILE`). The C64 ROM images are in the release archive but not in
the public source repository (`roms/README.md`).
