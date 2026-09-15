# sid2midi 2.1.0 — release notes

2.1.0 replaces the scheduled-IRQ approximation of 2.0.0 with a C64 that runs
continuously, adds the second CIA with NMIs, timer B, SID envelope/oscillator
readback and C64 BASIC RSIDs.

## Added

- **CIA #2 and NMI playback.** Both CIAs are 6526 models; CIA #2's interrupt
  output drives the edge-triggered NMI (a new NMI needs the ICR to be read,
  as on hardware). NMIs enter through `$FFFA` (KERNAL `$FE43` → `($0318)` when
  the KERNAL is banked in, the RAM vector otherwise). NMI-driven digis play in
  full: *Great Giana Sisters* executes ~5,600 NMIs per second of music.
- **CIA timer B** on both CIAs: phi2 counting, counting timer-A underflows
  (CRB modes `%10`/`%11`), CNT mode (no pulses on a C64 without hardware),
  one-shot and continuous modes, force load, latch loads of stopped timers,
  ICR bit 1. Timer A gained one-shot mode.
- **`$D41C` ENV3 readback** from a reSID 0.16-equivalent envelope generator
  (rate counter with the ADSR delay bug, exponential decay table, sustain
  compare, attack wrap). Verified cycle-for-cycle against an independent
  per-cycle reference.
- **`$D41B` OSC3 readback** from reSID-equivalent oscillators (24-bit
  accumulators, noise LFSR, test bit, hard sync between all three voices,
  ring modulation). Also verified against a per-cycle reference.
- **Write-only SID registers** read back the last value written to the chip
  for `$2000` cycles, then 0; `$D419/$D41A` (paddles) read `$FF`.
- **C64 BASIC RSIDs** (header flag bit 1): the program is loaded, BASIC's
  pointers are set and the lines relinked (`$A533`), the song number is placed
  in `$030C` and `RUN` is typed into the keyboard buffer, exactly as a user
  would start it.
- **Real cold start.** The bundled KERNAL/BASIC ROMs are reset once per
  process (0.6 s, then cached): RAM test, I/O init, PAL/NTSC detection via the
  raster, 60 Hz CIA IRQ, vectors, BASIC `READY.` RSIDs and PSIDs with
  play = 0 start from that state.
- **Continuous execution** for RSID, PSID play = 0 and BASIC tunes: after init
  the CPU keeps running; IRQs interrupt whatever is executing (including an
  init that never returns and runs a main loop), NMIs interrupt IRQ handlers.
  Idle time (init returned, or a jump-to-itself busy loop) is skipped to the
  next device event, so interrupt-only tunes convert quickly.
- **Velocities from the emulated envelope** at the end of each frame, for
  every capture made by `sid2midi.py`.
- `tests/test_sid2midi_hardware.py`: 25 tests (83 in total).

## Changed

- CIA counter semantics follow the 6526: the counter stays at 0 for one cycle
  and underflows on the next count (first underflow `value + 1` cycles after
  loading; period `latch + 1`).
- VIC raster-compare flags latch whenever the raster reaches the compare line,
  even with the interrupt disabled (the KERNAL's PAL/NTSC detection needs it).
- Interrupt-driven captures start a frame at each IRQ entry (at most 16 per
  video frame) and every video frame when no IRQs arrive, so NMI-only and
  BASIC tunes are sampled too.
- `--init-budget`, `--play-budget` and `--max-stalls` apply only to called
  PSID players; continuous tunes are bounded by `--seconds`.
- `TuneRun` gained `irq_count`, `nmi_count`, `init_returned` and `t0`; frame
  snapshots carry per-voice envelope levels as a fourth element.
- MUS files are still rejected (exit code 2), with a clearer message.

## Not implemented: MUS (Compute!'s Sidplayer)

MUS files are data for Craig Chamberlain's Sidplayer. They contain no player
code, so a converter must supply the Sidplayer player routine. That routine is
not part of this package and cannot be reconstructed from memory with
confidence. A guessed player would produce wrong music without any error. See
README "MUS files" for what would be needed.

## Accuracy boundary

See README "Accuracy boundary".
