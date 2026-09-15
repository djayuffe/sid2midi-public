# NMOS 6502 CPU 100% Closure Audit

> **Historical (1.x).** Superseded by `SID2MIDI_2_0_0_AUDIT.md`. The "100%"
> claim was incorrect: decimal-mode flags, the page-cross set and ten base
> cycle-table entries were wrong, and LXA/XAA were NOPs.

This pass closes the CPU-core release for SID2MIDI use.

## Scope

The goal is a robust NMOS 6502/6510 core for C64 SID-player extraction, not a transistor-level emulator. The core is callback-based and intended to run PSID/RSID init/play code while the host maps RAM, ROM, SID, CIA and VIC registers.

## Closed in this pass

- Added final regression suite: `tests/test_cpu6502_final_100_closure.py`.
- Verified `call()` sentinel return to `$0001`.
- Verified JSR/RTS stack return behavior.
- Verified BRK pushes PC+2 and P with B and bit 5 set.
- Verified RTI restores P and PC.
- Verified zero-page indexed and indexed-indirect wrap behavior.
- Verified branch cycle penalties: not-taken, taken same page, taken page-cross.
- Added more decimal ADC/SBC reference cases.
- Verified KERNAL IRQ exit restores A/X/Y/P/PC/SP once and cleanly.
- Verified all 256 opcodes are bounded for one-step execution.
- Added opcode coverage report tool: `tools/cpu_opcode_coverage_report.py`.

## Opcode policy

- All official 6502 opcodes have handlers.
- Common SID/decruncher illegal opcodes are implemented: LAX, SAX, DCP, ISC/ISB, SLO, RLA, SRE, RRA, ANC, ALR, ARR, SBX, USBC, AHX/SHA, TAS/SHS, SHY, SHX, LAS and NOP variants.
- KIL/JAM opcodes safely stop execution and set `cpu.jammed`.
- Remaining unstable/rare opcodes use a bounded 2-cycle recoverable-NOP policy so batch conversion does not hang on exotic or corrupted code.

## Validation summary

```text
python3 -m py_compile cpu6502.py sid2midi.py tests/test_cpu6502_nmos_upgrade.py tests/test_cpu6502_final_100_closure.py
python3 -m unittest -q tests.test_cpu6502_nmos_upgrade tests.test_cpu6502_final_100_closure
python3 sid2midi.py examples/simple_pulse.sid --seconds 1 --report -o /tmp/simple_final100.mid
python3 tools/cpu_opcode_coverage_report.py
```

Result:

```text
Ran 20 tests
OK
simple_pulse.sid: 6 tracks, 1 notes, 41 CC, PPQ 960
handlers: 256/256
```

## Honest boundary

This is now complete for the SID2MIDI extraction target. It is still not a transistor-level NMOS 6502 timing core. Some unstable undocumented opcodes are intentionally approximated or bounded because the converter's priority is extracting SID-register intent safely across large HVSC batches.
