# sid2midi 2.0.0 — release notes

2.0.0 fixes every defect found in the full audit of 1.x and in batch
conversion of 33 real-world SID files, and adds the C64 features those files
needed. Output MIDI differs from 1.x (see "Behaviour changes").

## Fixed — converter (`sid2midi.py`)

- **Crash:** reads from `$C000-$CFFF` were served from the 8K BASIC ROM and
  raised `IndexError` (e.g. *Great Giana Sisters*). That block is now RAM.
- **Hangs:** a runaway player ran up to 4 million instructions per call, for
  every call (5 of 33 real files never finished). Calls now have an instruction
  budget and a consecutive-stall limit.
- **PSID banking:** `$01` was always `$37`, so tunes loaded over the KERNAL ran
  ROM code (*quake.sid*). `$01` now follows the PSID spec per call.
- **BRK** in a PSID player looped through the KERNAL instead of returning.
- **Second SID address** decoded as `$D420|b<<4` instead of `$D000|b<<4`
  (`$50` gave `$D520`, should be `$D500`); no validation of the address.
- **Oversized data** (> 64K) grew the emulated RAM array instead of being rejected/truncated.
- **Envelope:** a gate pulsed on and off inside one player call never entered
  release, inflating velocities; the reSID rate table had wrong entries
  (`11719, 15625, 31250` → `11720, 19532, 31251`); long calls were capped at 6000 steps.
- **`--auto`** kept only the first *period* frames, cutting songs with an intro;
  it now detects loop start and period, keeps intro + one loop and writes
  `loop start` / `loop end` markers.
- **CC74 cutoff** sent `$D416` (0-255) and was clamped to 127 for the top half;
  now the 11-bit cutoff scaled to 0-127.
- **CC26 digi density** wrapped (`& 127`) instead of saturating.
- **Hanging tones:** a voice switching to noise/drums kept its previous note on.
- **Out-of-range notes** wrapped with `& 0x7F` (very low frequencies became mid notes); now dropped.
- **Meta events** longer than 127 bytes wrote an invalid length byte; lengths are now VLQ.
- **Track-name encoding** mixed UTF-8 and Latin-1.
- **IRQ timing:** CIA and VIC IRQ flags were both raised on every call at one
  fixed rate; `$D012` advanced by one per read.
- `--song 0` silently meant the default song; out-of-range songs are now an error.
- I/O writes also landed in the RAM under `$D000-$DFFF`.
- Errors surfaced as Python tracebacks; files are now closed deterministically.

## Fixed — CPU (`cpu6502.py`)

- **Base cycle table:** `$0C $1C $2C $3C $4C $5C $6C $7C $DC $FC` were 6 cycles
  (JMP abs is 3, JMP ind 5, BIT abs and NOP abs/abs,X 4).
- **Page-cross set:** RMW abs,X opcodes (`$1E $3E $5E $7E $DE $FE`) wrongly got
  +1; indexed illegal reads (`$BF $B3 $BB`, NOP abs,X) were missing it.
- **Decimal mode:** ADC took N from the adjusted result; SBC took N/Z from the
  BCD result. Both now match NMOS behaviour for all 131,072 inputs
  (checked against Bruce Clark's equations). ARR now honours decimal mode.
- **Read-modify-write dummy write** (old value written before the new one) added;
  `ASL $D019` acknowledges and `INC $D418` digis depend on it.
- **LXA `$AB` / XAA `$8B`** were NOPs; now implemented with the `$EE` constant.
- `_build()` mutated the class-level cycle table shared by all instances.

## Added

- Event-scheduled IRQ playback from CIA #1 timer A and VIC raster compare, with
  a counting CIA timer and cycle-derived `$D011/$D012`.
- KERNAL power-on environment (vectors `$0314-$0319`, 60 Hz CIA IRQ, `$02A6`).
- Up to three SIDs (header v4), `$D400-$D7FF` SID mirrors, per-SID filter tracks,
  noise drums for every SID.
- CLI: `--all-songs`, `--info`, `--version`, `--no-fixups`, `--init-budget`,
  `--play-budget`, `--max-stalls`; header warnings on stderr; exit codes.
- Header validation and repairs (see README "Robustness").
- `tools/midicheck.py` (structural SMF validator) and `tools/validate_corpus.py`
  (parallel batch conversion with timeouts and a summary).
- `tests/test_sid2midi_release.py`: 38 regression tests, one per defect/feature,
  with synthesised tunes (58 tests in total).

## Behaviour changes

- MIDI output differs: CC74 values, loop markers, envelope-derived velocities,
  cycle stamps (cycle table), IRQ tune timing, drum routing on SID B/C.
- Track order is `meta, voices (A[,B][,C]), Drums, filter tracks`; single-SID
  files keep the 1.x layout.
- `run_tune()` returns a `TuneRun` object (`frames, fcyc, irq, loop, warnings,
  stop_reason, timing, stalled_calls`) instead of a tuple; frames are
  `(per-SID snapshots, digi_count)`.
- `validate_nmos_cpu_release.sh` runs every test module and writes to a private
  temporary directory; `validate_release.sh` falls back to `shasum`.

## Known limitations

See README "Accuracy boundary": no NMI/CIA #2 playback, no CIA timer B, no ENV3
readback, no BASIC-program RSIDs or MUS files, SHX-family page-cross address
corruption not modelled.
