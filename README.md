# sid2midi 3.1.4 — PSID/RSID/MUS → MIDI on a cycle-exact emulated C64

A self-contained, dependency-free **SID → MIDI extraction toolkit**. `sid2midi.py`
loads a PSID/RSID tune (or a Sidplayer MUS file), runs it on a cycle-exact C64
model (NMOS 6510, two 6526/6526A CIAs ported from VICE, a VIC-II with bus
stealing, collisions and light pen ported from VICE, up to three SIDs with the
digital part of reSIDfp, the real KERNAL and BASIC ROMs),
captures every SID register write with its CPU cycle, and turns the register
stream into a Standard MIDI File with notes, pitch bend and automation.

```text
sid2midi.py                          converter (C64 machine, fast VIC-II, player driver, MIDI writer)
cpu6502.py                           cycle-exact NMOS 6502/6510 core
cia_vice.py                          MOS 6526/6526A, port of VICE's CIA core
vicii_sc.py                          VIC-II, port of VICE's cycle-based VIC-II
residfp.py                           digital part of reSIDfp (GPL-2.0-or-later)
mus.py                               Compute!'s Sidplayer (MUS) loader
roms/                                C64 ROMs (901226-01, 901225-01, 901227-03); see roms/README.md
examples/simple_pulse.sid            smoke-test tune
tests/                               regression tests (CPU, CIA vs Lorenz, reSIDfp, converter, machine)
tools/testbench.py                   run the VICE test programs on the emulated C64
tools/midicheck.py                   structural MIDI validator
tools/validate_corpus.py             batch-convert and validate a SID collection
tools/singlesteptests.py             check the CPU against the SingleStepTests 6502 vectors
tools/cpu_opcode_coverage_report.py  opcode coverage report
docs/                                architecture, accuracy, MIDI mapping, testing, development
docs/audits/                         audit reports (3.1 current, 3.0/2.0, 1.x historical)
ruff.toml                            lint configuration (optional, for development)
validate_release.sh                  full validation incl. SHA256 manifest
```

## Quick start

```bash
./validate_release.sh
python3 sid2midi.py tune.sid --report                     # writes tune.mid
python3 sid2midi.py tune.sid --auto --seconds 600         # trim to intro + first loop
python3 sid2midi.py tune.sid --all-songs -o out/tune.mid  # out/tune_song01.mid ...
python3 sid2midi.py tune.sid --info                       # header only
```

## Options

```text
-o, --out PATH      output file (default: next to the .sid)
--song N            subtune, 1-based (default: header start song)
--all-songs         convert every subtune to NAME_songNN.mid
--seconds S         capture length in C64 time (default 180)
--auto              detect the loop; keep intro + one loop, add loop markers
--bpm B             DAW grid tempo; event times still follow the C64 clock
--drumvoice 1-3     route this SID A voice to channel 10 (default: noise voices)
--no-bend / --no-cc disable pitch bend / CC automation
--report            print header, timing, IRQ/NMI counts, loop and stall summary
--info              print the header summary and exit
--no-fixups         do not repair malformed files (see Robustness)
--sid-model M       auto (header, else 6581; default), 6581 or 8580
--sidplayer FILE    Compute!'s Sidplayer player routine for MUS files (not included)
--init-budget N     called PSIDs: instruction limit for init (default 2,000,000)
--play-budget N     called PSIDs: instruction limit per play call (default 50,000)
--max-stalls N      called PSIDs: stop after N consecutive stalled calls (default 25)
--version
```

Exit status: `0` success, `1` a song produced no output, `2` unreadable,
malformed or unsupported file / bad arguments. Warnings go to stderr.

## How tunes are run

**The machine** — a breadbin C64, emulated one CPU cycle at a time:

- **CPU:** NMOS 6510 with one bus access per cycle (dummy reads, the
  read-modify-write double write); interrupts polled from the line state two
  cycles before an opcode fetch (three after a taken branch without page
  crossing), the one-instruction delay after `CLI`/`SEI`/`PLP`, NMI takeover of
  BRK/IRQ vector fetches, undocumented opcodes including the SHA/SHX/SHY/TAS
  page-crossing corruption, RDY stalls by the VIC-II with VICE's rules for
  stalled SHx/ANE/CLI/SEI. `$00/$01` banking port
  with fading unused bits; PLA memory map; colour RAM.
- **CIAs:** a port of VICE's CIA core (`cia_vice.py`), original 6526 by
  default, 6526A available: timer state machine, interrupt delay line and
  timer B bug, serial port, TOD clock with alarm, PB6/PB7 timer outputs.
  CIA #1 drives IRQ, CIA #2 NMI.
- **VIC-II:** a fast model (raster interrupt, BA bus stealing by bad lines and
  sprite fetches) that switches to a port of VICE's cycle-based VIC-II
  (`vicii_sc.py`) as soon as a program uses sprite collisions or the light pen.
  The full chip adds collisions, light pen, phi1/phi2 fetches and the open bus
  (colour RAM upper nibble, unmapped I/O).
- **SID:** SID #1 at `$D400` (mirrored to `$D7FF`), SIDs #2/#3 from header
  v3/v4. CPU reads are answered by `residfp.py`, a statement-by-statement port
  of reSIDfp's digital part: `$D41B` OSC3 (with the 6581/8580 combined-waveform
  tables), `$D41C` ENV3, `$FF` for the paddles and the decaying bus value of
  write-only registers, for the chip model in the header or `--sid-model`. The
  cycle-exact chip is only built once a tune reads OSC3/ENV3.

**PSID with a play address.** `init`, then `play` once per frame at v-sync
(50/60 Hz) or, when the speed bit selects CIA, at the CIA #1 timer-A rate the
tune programs. `$01` follows the PSID spec per call: `$37`, `$36` (BASIC area),
`$35` (KERNAL area or data loaded over it), `$34` (`$D000-$DFFF`). BRK returns
to the driver.

**RSID, and PSID with play = 0.** The C64 ROMs in `roms/` are cold-started (RAM
test, I/O init, PAL/NTSC detection, BASIC `READY.`; 0.6 s once per process, then
cached). The ROMs are in the release archive but not in the public source
repository; without them a minimal environment is used and a warning is printed
(see `roms/README.md`). The tune is loaded and init is called with the song number in A.
From then on the C64 runs continuously: IRQ handlers (via `$0314` or `$FFFE`)
and NMI handlers (via `$0318` or `$FFFA`) run when CIA #1, the VIC or CIA #2
raise them, interrupting whatever code is executing. That includes an init
that never returns. Once init returns, the CPU idles with interrupts enabled.

**C64 BASIC RSIDs.** After the cold start the program is loaded at `$0801`,
BASIC's pointers are set, the lines are relinked, the song number (0-based) is
stored in `$030C` and `RUN` is typed into the keyboard buffer.

**Capture.** Every SID write is stamped with its cycle; note-ONs land on the
gate-trigger cycle. Interrupt-driven captures start a frame at each IRQ entry
(at most 16 per video frame), or every video frame when no IRQ arrives. Idle
time is skipped to the next CIA/VIC event, so emulation cost follows the code
the tune actually runs.

## Robustness

- **Called PSIDs:** each call has an instruction budget; after `--max-stalls`
  consecutive runaway calls the capture stops with a warning and the MIDI up
  to that point is written. BRK returns to the driver.
- **Continuous tunes** are bounded by `--seconds` of C64 time; a CPU jam stops
  the capture with a warning.
- **Header validation:** bad magic/version/offsets and empty files are
  rejected with exit 2 (MUS files without `--sidplayer` too); data past `$FFFF` is truncated; `init=0` (non-BASIC),
  0 songs, out-of-range start song, RSID play address and invalid SID addresses
  are repaired with a warning; a duplicated load address in front of the code is
  skipped and an embedded load address stored big-endian is byte-swapped
  (`--no-fixups` disables both); an init address pointing at empty data is
  reported.

## MIDI mapping

```text
Channels   SID A voices 1-3 -> ch 1-3    Filter/Master A -> ch 4
           SID B voices 1-3 -> ch 5-7    Filter/Master B -> ch 8
           SID C voices 1-3 -> ch 11-13  Filter/Master C -> ch 14
           drums (noise voices / --drumvoice) -> ch 10
Voice      notes (exact 16-bit frequency, sync -> source pitch), pitch bend ±2,
           program change on waveform (ring mod -> 13), velocity from the
           emulated envelope level
           CC70 pulse width  CC71 resonance  CC73 attack  CC75 decay
           CC79 sustain  CC72 release  CC20 waveform  CC21 sync  CC22 ring  CC23 test
Filter     CC74 cutoff (11-bit -> 0-127)  CC71 resonance  CC24 mode  CC25 routing
           CC7 volume  CC26 $D418 writes per frame (digi activity, SID A)
Meta       track name, copyright, header text, "song start", "loop start"/"loop end"
```

Tracks: `meta, A V1-V3, [B V1-V3], [C V1-V3], Drums, Filter/Master A [B] [C]`.
How notes, velocity, bends, drums and every CC are derived:
[docs/MIDI_MAPPING.md](docs/MIDI_MAPPING.md).

## Validation

```bash
./validate_release.sh                                   # tests, coverage, smoke, SHA256, layout
python3 tools/validate_corpus.py ~/HVSC --seconds 60    # your own collection
python3 tools/midicheck.py out/*.mid
python3 tools/testbench.py testbench/x64-testlist.txt testprogs --json results.json
python3 tools/singlesteptests.py 65x02/6502/v1         # CPU against SingleStepTests
```

`tools/testbench.py` runs the exit-code programs of the VICE test repository
(`testprogs`, not included) on the emulated machine; a program passes when it
writes `$00` to the debug cartridge register `$D7FF`; it always uses the full
VIC-II and picks CIA/VIC-II/SID models from the test options. The unit tests
reproduce all 41,664 register-access cases of Lorenz `cia1ta`/`cia1tb` and
libresidfp's own tests; CPU decimal mode is checked against Bruce Clark's NMOS
equations, and the CPU core was checked against the SingleStepTests 6502 v1
vectors. The fast VIC-II model was checked cycle by cycle against the full chip
(BA and interrupt edges).
See `FINAL_CLOSURE_REPORT.md` for the results and what was not tested,
and [docs/TESTING.md](docs/TESTING.md) for obtaining the reference data and
running each check.

## Accuracy boundary

This extracts musical intent from the SID register stream; it does not produce
audio. Known limits of the hardware model:

- **Evidence:** exactness means agreement with Lorenz's test suite and the VICE
  test programs that were available (`FINAL_CLOSURE_REPORT.md` lists pass/fail
  and what was not tested). Rules fitted to those tests rather than taken from
  chip documentation are marked as such in the source.
- **References:** the CIA and VIC-II are ports of VICE (x64sc); where VICE
  itself is known to differ from real hardware, sid2midi does too.
- **VIC-II:** no display output; VICE's random VSP-bug memory corruption is not
  emulated. Colour RAM and unmapped I/O reads switch to the full VIC-II for the
  exact open-bus value, except colour RAM copies made by the KERNAL screen
  editor, which return the stored nibble on the fast model.
- **CIA:** no keyboard, joystick or IEC devices (inputs read as released/pulled
  up).
- **SID:** digital part only; no audio. Where the reference implementation and
  real-chip data disagree, the data wins; real 6581s also differ from each
  other in some noise writeback transitions (see `docs/ACCURACY.md`).
- **Speed:** cycle exactness costs time; tunes that read OSC3/ENV3 clock the
  SID model every cycle, and programs that use sprite collisions or the light
  pen run the full VIC-II; both convert several times slower than real time.

## MUS files

MUS files are data for *Compute!'s Sidplayer* by Craig Chamberlain and contain
no player code. sid2midi plays them with the original player routine, which is
**not included**; supply it with `--sidplayer`:

```bash
python3 sid2midi.py song.mus --sidplayer sidplayer1.a65 --report
```

The player can be an xa-style 6502 source (for example `sidplayer1.a65` from
libsidplayfp), an o65 object built from it, or a binary whose first two bytes
are its load address. It is installed as libsidplayfp does: the MUS file at
`$0900`, the player at `$E000` with its unused SID reads removed, init `$EC60`,
play `$EC80`, timing by CIA #1. Stereo STR companions are not supported.

## Documentation

| Document | Contents |
|----------|----------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | modules, emulated machine, timing, capture, drivers, Python API |
| [docs/ACCURACY.md](docs/ACCURACY.md) | evidence per chip, every deviation from the references, known limits |
| [docs/MIDI_MAPPING.md](docs/MIDI_MAPPING.md) | the MIDI output in detail |
| [docs/TESTING.md](docs/TESTING.md) | unit tests, VICE test programs, SingleStepTests, corpus, debugging |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | conventions, making an accuracy change, release checklist |

## Audit history

`docs/audits/SID2MIDI_3_1_0_AUDIT.md` covers the 3.1.0 reference ports and
the 3.1.1-3.1.4 hardware-evidence fixes,
`docs/audits/SID2MIDI_3_0_0_AUDIT.md` the 3.0.0 accuracy work and
`docs/audits/SID2MIDI_2_0_0_AUDIT.md` the 2.0.0 fixes; see
`RELEASE_NOTES_v3.1.4.md`, `RELEASE_NOTES_v3.1.3.md`, `RELEASE_NOTES_v3.1.2.md`, `RELEASE_NOTES_v3.1.1.md`, `RELEASE_NOTES_v3.1.0.md`, `RELEASE_NOTES_v3.0.0.md` and
`RELEASE_NOTES_v2.1.0.md`. The 1.x documents
in `docs/audits/` are kept for history; several of their claims were found
wrong in the 2.0.0 audit.

## Licence

`residfp.py` and `tests/test_residfp.py` port libresidfp (reSIDfp),
`cia_vice.py` and `vicii_sc.py` port VICE; all are GPL-2.0-or-later, and the
package as a whole is distributed under GPL-2.0-or-later. See `LICENSE.md`.

## File integrity

```bash
shasum -a 256 -c SHA256SUMS.txt
```
