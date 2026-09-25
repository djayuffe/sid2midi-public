#!/usr/bin/env python3
"""Structural Standard MIDI File validator.

Checks chunk framing, variable-length quantities, running status, data-byte
ranges, end-of-track placement and note-on/note-off balance per channel/key.
Used by the test-suite and by tools/validate_corpus.py.
"""
from __future__ import annotations

import struct
import sys


class MidiError(ValueError):
    pass


def _vlq(d, p, end):
    n = 0
    for _ in range(4):
        if p >= end:
            raise MidiError("truncated variable-length quantity")
        b = d[p]
        p += 1
        n = (n << 7) | (b & 0x7F)
        if b < 0x80:
            return n, p
    raise MidiError("variable-length quantity longer than 4 bytes")


def check_smf(data: bytes) -> dict:
    if data[:4] != b"MThd" or len(data) < 14:
        raise MidiError("missing MThd header")
    hlen, fmt, ntracks, division = struct.unpack(">IHHH", data[4:14])
    if hlen != 6:
        raise MidiError("MThd length %d != 6" % hlen)
    if fmt not in (0, 1, 2):
        raise MidiError("unknown SMF format %d" % fmt)
    p = 14
    stats = {"format": fmt, "tracks": ntracks, "division": division, "notes": 0, "cc": 0, "events": 0}
    for t in range(ntracks):
        if data[p:p + 4] != b"MTrk":
            raise MidiError("track %d: missing MTrk" % t)
        length = struct.unpack(">I", data[p + 4:p + 8])[0]
        q, end = p + 8, p + 8 + length
        if end > len(data):
            raise MidiError("track %d: chunk runs past end of file" % t)
        running, eot, held = None, False, {}
        while q < end:
            if eot:
                raise MidiError("track %d: events after End Of Track" % t)
            _, q = _vlq(data, q, end)
            if q >= end:
                raise MidiError("track %d: delta time without event" % t)
            status = data[q]
            if status == 0xFF:
                if q + 2 > end:
                    raise MidiError("track %d: truncated meta event" % t)
                kind = data[q + 1]
                n, q = _vlq(data, q + 2, end)
                q += n
                eot = kind == 0x2F
                stats["events"] += 1
                continue
            if status in (0xF0, 0xF7):
                n, q = _vlq(data, q + 1, end)
                q += n
                stats["events"] += 1
                continue
            if status & 0x80:
                running = status
                q += 1
            elif running is None:
                raise MidiError("track %d: data byte without running status" % t)
            kind = running & 0xF0
            need = 1 if kind in (0xC0, 0xD0) else 2
            body = data[q:q + need]
            if len(body) != need or any(b & 0x80 for b in body):
                raise MidiError("track %d: bad data bytes for status $%02X" % (t, running))
            q += need
            stats["events"] += 1
            key = (running & 0x0F, body[0])
            if kind == 0x90 and body[1] > 0:
                held[key] = held.get(key, 0) + 1
                stats["notes"] += 1
            elif kind == 0x80 or (kind == 0x90 and body[1] == 0):
                if held.get(key, 0) <= 0:
                    raise MidiError("track %d: note-off without note-on ch%d key %d" % (t, key[0] + 1, key[1]))
                held[key] -= 1
            elif kind == 0xB0:
                stats["cc"] += 1
        if q != end:
            raise MidiError("track %d: last event overruns the chunk" % t)
        if not eot:
            raise MidiError("track %d: missing End Of Track" % t)
        hanging = {k: v for k, v in held.items() if v}
        if hanging:
            raise MidiError("track %d: %d notes never released" % (t, sum(hanging.values())))
        p = end
    if p != len(data):
        raise MidiError("%d trailing bytes after the last track" % (len(data) - p))
    return stats


def main(argv=None) -> int:
    rc = 0
    for path in (argv if argv is not None else sys.argv[1:]):
        try:
            with open(path, "rb") as fh:
                s = check_smf(fh.read())
            print("OK   %s  format %d, %d tracks, %d notes, %d CC"
                  % (path, s["format"], s["tracks"], s["notes"], s["cc"]))
        except (OSError, MidiError) as e:
            print("FAIL %s  %s" % (path, e))
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
