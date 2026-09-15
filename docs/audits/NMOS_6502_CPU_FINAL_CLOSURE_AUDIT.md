# NMOS 6502 CPU Final Closure Audit

> **Historical (1.x).** Superseded by `SID2MIDI_2_0_0_AUDIT.md`.

This pass completes the CPU upgrade by fixing the remaining semantic mismatch
between documentation and runtime behavior, and by adding practical support for
more unstable NMOS illegal opcodes that can appear in packers/decrunchers.

## Final fixes

### 1. KIL/JAM semantics fixed

Previous closure documented KIL/JAM as a safe stop, but the fallback table still
made unknown opcodes behave as 2-cycle NOPs.  Closure-final now maps the real
NMOS KIL/JAM opcode set to a `JAM()` handler:

```text
02 12 22 32 42 52 62 72 92 B2 D2 F2
```

Behavior:

- `step()` adds the opcode cycles.
- `cpu.jammed` becomes `True`.
- `step()` returns `False`.
- `call()` / `irq()` stop cleanly instead of hanging or running through bogus
  code.

This is the correct policy for SID extraction: it is safer than infinite JAM and
more honest than pretending KIL is NOP.

### 2. Added practical unstable illegal opcodes

Added approximate handlers for the less common NMOS store/load family:

```text
AHX/SHA  $93 ($zp),Y and $9F abs,Y
TAS/SHS  $9B abs,Y
SHY      $9C abs,X
SHX      $9E abs,Y
LAS      $BB abs,Y
```

These are analog/unstable on real NMOS silicon, especially with page-cross high
byte behavior, but the implementation is intentionally practical for C64 SID
player extraction: preserve memory side effects and register behavior well enough
for decrunchers and init code.

### 3. Opcode policy clarified

- Official opcodes: implemented.
- Common stable illegals: implemented.
- KIL/JAM: safe stop.
- Remaining rare/unstable opcodes not explicitly modeled: recoverable 2-cycle
  NOP policy to keep batch conversion robust.

## Regression tests added

New tests verify:

- KIL/JAM stops execution and does not execute following bytes.
- All KIL opcodes stop safely.
- LAS abs,Y sets A/X/SP from memory & SP.
- SHX/TAS/AHX-family practical writes have expected address/value behavior.

## Validation

```text
python3 -m py_compile cpu6502.py sid2midi.py tests/test_cpu6502_nmos_upgrade.py
python3 -m unittest -q tests.test_cpu6502_nmos_upgrade
python3 sid2midi.py examples/simple_pulse.sid --seconds 1 --report -o /tmp/simple_cpu_upgrade.mid
unzip -t sid2midi_nmos6502_cpu_final_closure_release.zip
```

Result:

```text
Ran 10 tests
OK
```
