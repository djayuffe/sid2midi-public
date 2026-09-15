# sid2midi 3.0.0 — release notes

3.0.0 makes the emulated C64 cycle exact and checks it against Wolfgang
Lorenz's test suite and the VICE test programs instead of against its own
assumptions. The MIDI conversion itself is unchanged; tunes that depend on
exact timing, CIA quirks or SID read-back now run as on a breadbin C64.

**Licence change:** the SID read-back uses a port of reSIDfp, which is
GPL-2.0-or-later, so the package is now GPL-2.0-or-later (see `LICENSE.md`).

## CPU (`cpu6502.py`, rewritten)

- One bus access per cycle at its real cycle number: dummy reads of implied
  and indexed instructions, the unmodified-value write of read-modify-write
  instructions, stack dummy reads, the branch dummy read.
- Interrupts are polled from the line state two cycles before the opcode
  fetch; three after a taken branch that does not cross a page; `CLI`, `SEI`
  and `PLP` take effect one instruction late; an NMI arriving while BRK or an
  IRQ pushes its frame takes over the vector; an NMI edge in the last two
  cycles of an interrupt sequence waits for the handler's first instruction.
- SHA/SHX/SHY/TAS store `value & (base high byte + 1)`, and on a page crossing
  that value also replaces the target's high byte; JAM halts.
- RDY: reads halt while the VIC-II holds BA low. A stall of the last cycle
  repeats the interrupt decision (branches decided earlier; CLI/SEI/PLP see
  both I values) — fitted to the VICE irqdma tests.

## CIA (`cia6526.py`, new)

- Old 6526 timers with the COUNT2/COUNT3 pipeline, two-stage loads, one-shot
  sampling, force load, latch loads of stopped timers, CNT mode, timer B
  counting timer-A underflows through the same pipeline. All 41,664 cases of
  Lorenz `cia1ta`/`cia1tb` are reproduced.
- Interrupt output one cycle after the flag, cancelled by an ICR read; the
  timer B bug (an ICR read in the underflow cycle loses the TB flag but not the
  interrupt); a timer B interrupt survives a mask clear in the next cycle.
- Back-to-back ICR reads of a running timer's flag act as one read
  (`INC $DD0D,X`) — fitted to VICE `dd0dtest`.
- PB6/PB7 timer outputs (pulse and toggle).
- TOD clock: 50/60 Hz mains ticks divided by 5 or 6, hour latch on read,
  stop on hour write, alarm, AM/PM flip when 12 is written.

## Machine (`sid2midi.py`)

- VIC-II BA bus stealing: bad lines (cycles 12-54) and sprite data fetches,
  with the sprite display state machine (MCBASE, Y expansion).
- 6510 `$01` port: bits 6/7 keep their last output level and fade; bit 5
  reads low as input.
- The idle trap at `$0001` is used only by the SID player driver, so programs
  that execute code there run normally.
- The KERNAL/BASIC cold start and SID power-on state are cached per process.

## SID (`residfp.py`, new)

- Statement-by-statement port of the digital part of reSIDfp (envelope
  generator, waveform generator with noise write-back, floating output and
  shift-register fades, 6581/8580 combined-waveform pulldown tables, bus value
  lifetimes). libresidfp's own unit tests are transliterated in
  `tests/test_residfp.py`.
- `$D41B` OSC3 and `$D41C` ENV3 come from that model, clocked every cycle from
  power-on. It is built only when a tune first reads OSC3/ENV3; paddle and
  write-only register reads use the same bus-value rules without it.
- Chip model per SID from the header (bits for SIDs #1-#3); new option
  `--sid-model {auto,6581,8580}`.

## Files and robustness

- An embedded load address stored big-endian (code in zero page while init
  and play point elsewhere) is byte-swapped with a warning (`--no-fixups`
  disables).
- A warning when the init address points at empty data.

## Tools and tests

- `tools/testbench.py` runs the exit-code programs of the VICE test repository
  on the emulated machine (parallel, JSON results).
- New tests: `tests/test_cia6526_lorenz.py`, `tests/test_residfp.py`; the
  hardware and CPU tests were updated to the cycle-exact semantics.

## Costs

Cycle-exact emulation is slower than 2.1.0; see `FINAL_CLOSURE_REPORT.md` for
measured conversion times.

## Known limitations

See README "Accuracy boundary" and the failing tests in `FINAL_CLOSURE_REPORT.md`.
MUS files are still not supported.
