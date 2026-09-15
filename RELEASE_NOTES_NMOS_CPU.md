# Release notes — NMOS CPU 100% closure

This release finalizes the CPU6502 core for SID2MIDI extraction.

## Added

- `tests/test_cpu6502_final_100_closure.py`
- `tools/cpu_opcode_coverage_report.py`
- `docs/audits/CPU_OPCODE_COVERAGE_REPORT.txt`
- `docs/audits/NMOS_6502_CPU_100_PERCENT_CLOSURE_AUDIT.md`

## Closed

- Verified call sentinel return contract.
- Verified JSR/RTS, BRK, RTI, zero-page wrap, branch cycle penalties and KERNAL IRQ exit.
- Added more decimal ADC/SBC reference cases.
- Verified all 256 opcodes are bounded for one-step execution.
- Confirmed official opcode coverage: 151/151.
- Confirmed handler coverage: 256/256.
- Confirmed KIL/JAM stops safely instead of continuing as NOP.

## Policy

The CPU is complete for PSID/RSID SID-register extraction. It remains intentionally non-transistor-level: unstable illegal opcodes that are not useful to SID extraction are bounded/recoverable so large HVSC batches do not hang.

## Final-final closure pass

- Added `FINAL_CLOSURE_REPORT.md`.
- Added `REQUIREMENTS.md`.
- Added `validate_release.sh` as a top-level full validator.
- Added `run_example.sh` for quick smoke conversion.
- Reworked validation syntax check to avoid generating `__pycache__` in release validation.
- Regenerated `SHA256SUMS.txt` after final cleanup.
