# Project Manifest — sid2midi 3.1.3

```text
README.md                               overview, options, mapping, accuracy boundary
QUICKSTART.md                           first commands
REQUIREMENTS.md                         runtime requirements
LICENSE.md                              licence (GPL-2.0-or-later) and third-party material
COPYING                                 GNU General Public License, version 2
RELEASE_NOTES_v3.1.3.md                 changes in this release
RELEASE_NOTES_v3.1.2.md                 changes in 3.1.2
RELEASE_NOTES_v3.1.1.md                 changes in 3.1.1
RELEASE_NOTES_v3.1.0.md                 changes in 3.1.0
RELEASE_NOTES_v3.0.0.md                 changes in 3.0.0
RELEASE_NOTES_v2.1.0.md                 changes in 2.1.0
RELEASE_NOTES_v2.0.0.md                 changes in 2.0.0
RELEASE_NOTES_NMOS_CPU.md               1.x release notes (history)
FINAL_CLOSURE_REPORT.md                 3.1.3 validation report
PROJECT_MANIFEST.md                     this file
SHA256SUMS.txt                          integrity manifest
ruff.toml                               lint configuration (development only)

sid2midi.py                             converter, C64 machine, fast VIC-II model
cpu6502.py                              cycle-exact NMOS 6502/6510 core
cia_vice.py                             MOS 6526/6526A: port of VICE's CIA core (GPL-2.0-or-later)
vicii_sc.py                             VIC-II: port of VICE's cycle-based VIC-II (GPL-2.0-or-later)
residfp.py                              reSIDfp digital part (GPL-2.0-or-later)
mus.py                                  Compute!'s Sidplayer (MUS) loader and xa assembler
roms/README.md                          which C64 ROM images are needed and why
roms/basic.bin                          C64 BASIC ROM (release archive only)
roms/chargen.bin                        C64 character ROM (release archive only)
roms/kernal.bin                         C64 KERNAL ROM (release archive only)
examples/simple_pulse.sid               smoke-test tune

tests/test_cia6526_lorenz.py            CIA timers vs Lorenz cia1ta/cia1tb (41,664 cases)
tests/test_residfp.py                   reSIDfp port vs libresidfp's unit tests
tests/test_mus_vic_cpu.py               MUS loader, VIC-II tables/read-back/VSP fetch, CIA port B, RDY rules
tests/test_fast_vic_open_bus.py         fast VIC-II path: colour RAM / unmapped I/O open bus
tests/test_sid_writeback_rules.py       SID noise writeback and 8580 noise+pulse OSC3 rules
tests/test_sid2midi_hardware.py         CIA/NMI/VIC/SID machine behaviour, cold start, BASIC
tests/test_sid2midi_release.py          converter + CPU regression tests
tests/test_cpu6502_nmos_upgrade.py      CPU tests
tests/test_cpu6502_final_100_closure.py CPU tests
tests/test_singlesteptests_tool.py      SingleStepTests harness

tools/testbench.py                      VICE test programs on the emulated C64
tools/midicheck.py                      structural MIDI validator
tools/validate_corpus.py                batch conversion + validation
tools/cpu_opcode_coverage_report.py     opcode coverage report
tools/singlesteptests.py                CPU vs SingleStepTests 6502 vectors

validate_release.sh                     full validation (tests, smoke, SHA256, layout)
validate_nmos_cpu_release.sh            tests, coverage and smoke conversion
run_example.sh                          smoke conversion helper

docs/README.md                          documentation index
docs/ARCHITECTURE.md                    modules, machine, timing, capture, drivers, Python API
docs/ACCURACY.md                        evidence per chip, deviations from the references, limits
docs/MIDI_MAPPING.md                    the MIDI output in detail
docs/TESTING.md                         unit tests, VICE test programs, SingleStepTests, corpus
docs/DEVELOPMENT.md                     conventions, accuracy changes, release checklist
docs/audits/SID2MIDI_3_1_0_AUDIT.md     current audit (3.1.x reference ports and fixes)
docs/audits/SID2MIDI_3_0_0_AUDIT.md     3.0.0 audit
docs/audits/SID2MIDI_2_0_0_AUDIT.md     2.0.0 audit
docs/audits/CPU_OPCODE_COVERAGE_REPORT.txt
docs/audits/NMOS_6502_CPU_UPGRADE_AUDIT.md          1.x (historical)
docs/audits/NMOS_6502_CPU_FINAL_CLOSURE_AUDIT.md    1.x (historical)
docs/audits/NMOS_6502_CPU_100_PERCENT_CLOSURE_AUDIT.md  1.x (historical)
docs/audits/FINAL_PERFECT_RELEASE_AUDIT.md          1.x (historical)
```
