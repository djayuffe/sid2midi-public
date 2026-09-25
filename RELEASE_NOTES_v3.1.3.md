# sid2midi 3.1.3 — release notes

3.1.3 fixes three machine-level bugs found by VICE test programs that earlier
releases had never run, and closes the last test failures that were in scope.
All three are cases where sid2midi differed from a real C64.

## Colour RAM is its own memory (`sid2midi.py`)

Colour RAM shared storage with the RAM under the I/O area, so a write to
`$D800-$DBFF` while I/O was banked out changed what the colour RAM read back
(and the VIC-II saw). On a C64 they are separate: the 1 KB of 4-bit colour RAM
is only reachable while I/O is mapped, and the RAM underneath keeps its own
contents. Found by `C64/bankio`, whose real-hardware table checks every page of
the I/O area for several `$01` settings.

This changes conversions of tunes that copy the I/O area into their own data:
`quake.sid` now produces 647 notes instead of 656. The new value is the correct
one.

## SID bus value (`residfp.py`)

Reading a write-only SID register returns the last value on the SID data bus.
libresidfp halves the remaining lifetime on every such read, so the value
decayed after a few reads; reSID (and the C64) leave it alone. `C64/bankio`
reads the SID pages repeatedly and its reference data still expects the last
written value. VICE, which uses reSID, passes.

## Power-on RAM pattern (`sid2midi.py`)

RAM started as all zeros. A real C64 powers up with a pattern of `$00` and
`$FF`, and a surprising number of programs depend on it. sid2midi now fills RAM
with VICE's default C64 pattern (two bytes `$00`, then groups of four
alternating `$FF`/`$00`, inverted every 16 KB). VICE also flips single bits with
a 0.1% chance; that is left out so runs stay reproducible. Fixes
`C64/raminitpattern/cyberloadtest`, `darkstarbbstest` and `platoontest`.

## Tests

New unit tests for colour-RAM separation and the power-on pattern; the SID
bus-value tests now follow the reSID rule.

## Still out of scope

Three of the programs added in 3.1.2 cannot pass without hardware sid2midi does
not emulate, as their own readmes state: `general/fuxxortest/ef2-inst1` and
`test-fuxxored` need true 1541 drive emulation, and `C64/autostart/defaults/test`
must be autostarted from a disk image.
