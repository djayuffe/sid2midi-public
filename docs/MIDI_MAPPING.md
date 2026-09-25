# MIDI mapping

What `convert()` in `sid2midi.py` writes, and how each value is derived from the
captured SID registers. The capture itself is described in
[ARCHITECTURE.md](ARCHITECTURE.md).

## File layout

Standard MIDI File, format 1, 960 ticks per quarter note.

| Track | Channel (1-based) | Contents |
|-------|-------------------|----------|
| meta | – | track name, tempo, 4/4 time signature, copyright (the header's release field), text events with the header data and converter version, markers |
| `A V1`, `A V2`, `A V3` | 1, 2, 3 | SID #1 voices |
| `B V1` … `B V3` | 5, 6, 7 | SID #2 voices (only if SID #2 is used) |
| `C V1` … `C V3` | 11, 12, 13 | SID #3 voices (only if SID #3 is used) |
| `Drums` | 10 | percussion from noise voices |
| `Filter/Master` [`B`] [`C`] | 4 [8] [14] | filter and volume per SID |

A second or third SID gets tracks only if its registers are non-zero at some
point during the capture. Voice tracks start with program 38 (V1), 81 (V2)
or 80 (V3), volume CC7 = 100 and an instrument-name event (`SID A voice 1`).
The drum and filter tracks start with CC7 = 110.

## Time

Every event time comes from the C64 cycle at which it happened:

```text
tick = round(cycle × 960 × bpm / (60 × clock))
clock = 985,248 Hz (PAL) or 1,022,727 Hz (NTSC)
```

`--bpm` (default 125) only sets the tempo event and therefore the bar grid a
DAW shows; the music keeps its real timing. Choose the tune's actual tempo to
make bars line up.

A **frame** is one player call (called PSIDs) or one interrupt frame
(continuous tunes). Parameter changes are written at the frame's start;
note-ons are written at the cycle of the gate trigger inside the frame.

## Notes

For each voice and frame:

1. **Pitch source.** The voice's 16-bit frequency register; for a hard-synced
   voice (control bit 1) the frequency of its sync source (the previous voice),
   which is what the ear hears.
2. **Frequency:** `hz = freq × clock / 2^24`.
3. **Exact note:** `69 + 12 × log2(hz / 440)`, only when the gate is on, the
   test bit is off, a waveform is selected and the waveform is not pure noise.
   Values outside MIDI range give no note.
4. **Note number:** the rounded exact note.
5. **Note-on** when the gate was triggered in this frame or the rounded note
   changed; the previous note is ended at the same tick.
6. **Note-off** when the note disappears (gate off, test bit on, waveform off
   or switched to noise), or at the end of the capture.

**Velocity** combines the envelope and the master volume:

```text
peak     = highest emulated envelope level in this and the next 3 frames (0-255)
sustain  = sustain nibble × 17
load     = 0.55 × peak + 0.45 × sustain
velocity = clamp(8, 127, 18 + load / 255 × (volume + 1) / 16 × 108)
```

The envelope levels are those of the emulated envelope generator at the end of
each frame.

**Pitch bend** (range ±2 semitones, disable with `--no-bend`): at note-on, the
fractional part of the exact note (`8192 + (exact − note) × 4096`); in later
frames of the same note, the distance from the note-on pitch, so vibrato,
slides and portamento come out as bends. Bend events are only written when the
value changes.

## Programs

A program change is written when the waveform changes (disable together with
CC output with `--no-cc`):

| Waveform | Program (0-based GM) |
|----------|----------------------|
| ring modulation on | 13 |
| pulse + sawtooth | 81 |
| pulse | 80 |
| sawtooth | 81 |
| triangle | 89 |
| noise | no change |
| none | 80 |

## Voice CCs

Written at frame start when the value changes (`--no-cc` disables them):

| CC | Parameter | Value |
|----|-----------|-------|
| 70 | pulse width | 12-bit width >> 5 |
| 71 | filter resonance | resonance nibble × 8 |
| 73 | attack | attack nibble × 8 |
| 75 | decay | decay nibble × 8 |
| 79 | sustain | sustain nibble × 8 |
| 72 | release | release nibble × 8 |
| 20 | waveform | (tri 1 + saw 2 + pulse 4 + noise 8) × 8 |
| 21 | sync | 0 / 127 |
| 22 | ring modulation | 0 / 127 |
| 23 | test bit | 0 / 127 |

## Drums

A voice is treated as percussion when it plays pure noise (all SIDs), or when
it is the SID #1 voice chosen with `--drumvoice` (then only that voice of
SID #1 is percussion; noise on the other SID #1 voices produces no notes). A
percussion voice writes no notes on its own track. Each gate trigger becomes a
hit on channel 10, velocity 108, half a frame long:

| Voice | Frequency | Note |
|-------|-----------|------|
| noise | above 1,800 Hz | 42 closed hi-hat |
| noise | 400–1,800 Hz | 39 hand clap |
| noise | up to 400 Hz | 38 snare |
| `--drumvoice`, not noise | below 400 Hz | 36 bass drum |
| `--drumvoice`, not noise | 400 Hz and above | 38 snare |

## Filter and master tracks

One per SID, written at frame start when the value changes:

| CC | Parameter | Value |
|----|-----------|-------|
| 74 | cutoff | 11-bit cutoff >> 4 |
| 71 | resonance | resonance nibble × 8 |
| 24 | filter mode | (low-pass 1 + band-pass 2 + high-pass 4) × 16 |
| 25 | filter routing | (voice 1 + voice 2 + voice 3) × 16 |
| 7 | master volume | volume nibble × 8 |
| 26 | `$D418` writes in the frame (SID #1 only) | count, at most 127 |

CC26 shows digi playback: tunes that play samples through the volume register
write it hundreds of times per frame.

## Meta events

- Track name: the tune name (or `SID tune`).
- Text events: `name`, `author`, `released`, chip model and video standard,
  file format and song count, init/play/load addresses, extra SID addresses,
  `converted by sid2midi.py <version>`.
- Markers: `song start`; with `--auto`, `loop start` and `loop end` around the
  detected loop (the capture is cut after one loop).

Events at the same tick are ordered meta/controller/bend first, then note-off,
then note-on, so a retriggered note is never cut by its own note-off.
