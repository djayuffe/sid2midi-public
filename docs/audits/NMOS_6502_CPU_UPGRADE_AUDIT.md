# NMOS 6502 CPU Upgrade Audit

> **Historical (1.x).** Superseded by `SID2MIDI_2_0_0_AUDIT.md`. The page-cross
> set described below ("abs,X RMW opcodes") was incorrect and is fixed in 2.0.0.

## Purpose

This release integrates the improved callback-based MOS 6502/NMOS core supplied for the SID2MIDI project.  The target is not an audio-emulator CPU benchmark harness; it is a C64 SID-player extraction CPU that must run PSID/RSID init/play routines safely while preserving useful timing, flags and interrupt behavior.

## Integrated changes

- Replaced the earlier minimal cycle table with a full 256-entry base cycle table.
- Added `PAGE_CROSS`/`PAGE` page-cross penalty set, including indexed reads and abs,X RMW opcodes.
- Integrated improved NMOS decimal-mode ADC/SBC behavior.
- Kept JMP indirect page-wrap bug.
- Kept callback-only memory access for C64 RAM/ROM/I/O mapping.
- Kept `call(addr, a, x, y, max_ins)` sentinel contract used by `sid2midi.py`.
- Kept `irq(vec, kernal, max_ins)` with KERNAL-style A/X/Y push and `$EA31/$EA7E/$EA81` exit emulation for RSID IRQ handlers.
- Integrated common undocumented opcodes: LAX, SAX, DCP, ISC, SLO, RLA, SRE, RRA, ANC, ALR, ARR, SBX, USBC and NOP variants.
- Filled unstable/KIL/JAM opcodes as 2-cycle NOPs for SID extraction safety, matching the converter's batch-recovery policy.
- Added `jammed` state placeholder for future stricter emulation modes without breaking current behavior.

## Tests added

`tests/test_cpu6502_nmos_upgrade.py` verifies:

- decimal ADC/SBC behavior;
- JMP indirect NMOS page-wrap bug;
- page-cross cycle penalty for indexed reads;
- illegal opcode sanity for LAX/SAX/DCP;
- KERNAL IRQ `$EA31` exit path preserving A/X/Y and returning PC;
- all 256 opcodes have handlers.

## Smoke validation

A minimal PSID file was generated at:

```text
examples/simple_pulse.sid
```

It validates end-to-end import of `CPU6502` by `sid2midi.py` and writes a MIDI with one note and CC data.

## Honest limits

This CPU is semantically complete enough for SID-player extraction and much closer in timing behavior, but it is still not a transistor/cycle-exact 6510 bus emulator.  In particular, unstable illegal opcodes are deliberately treated as harmless NOPs so batch SID conversion does not hang.  That is correct for this converter's robustness goal, but not a replacement for a hardware-verification emulator.
