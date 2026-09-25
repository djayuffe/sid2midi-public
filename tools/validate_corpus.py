#!/usr/bin/env python3
"""Batch-convert a SID collection and validate every MIDI file produced.

Usage:
    python3 tools/validate_corpus.py DIR_OR_FILE... [--seconds 30] [--timeout 180]
                                     [--jobs 4] [--out DIR] [--songs all]

Each tune runs in its own process with a wall-clock timeout.  Results:
    OK       converted, MIDI structurally valid, at least one note
    SILENT   converted and valid, but no notes (check the warnings column)
    INVALID  a MIDI file was written but fails validation
    REJECTED sid2midi refused the file (exit code 2: malformed header)
    CRASH    sid2midi exited with a traceback
    TIMEOUT  exceeded --timeout
Exit status is 1 when any INVALID, CRASH or TIMEOUT occurred.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from midicheck import MidiError, check_smf  # noqa: E402

BAD = {"INVALID", "CRASH", "TIMEOUT"}


def collect(paths):
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(x for x in p.rglob("*") if x.suffix.lower() == ".sid" and x.is_file())
        elif p.is_file():
            out.append(p)
    return out


def convert_one(sid: Path, outdir: Path, seconds: float, timeout: float, all_songs: bool):
    out = outdir / (sid.stem + ".mid")
    cmd = [sys.executable, str(ROOT / "sid2midi.py"), str(sid), "--seconds", str(seconds), "-o", str(out)]
    if all_songs:
        cmd.append("--all-songs")
    start = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return sid, "TIMEOUT", time.time() - start, "", ""
    elapsed = time.time() - start
    warnings = "; ".join(ln[len("warning: "):] for ln in r.stderr.splitlines() if ln.startswith("warning: "))
    if r.returncode == 2 and "Traceback" not in r.stderr:
        return sid, "REJECTED", elapsed, r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "", ""
    if r.returncode and "Traceback" in r.stderr:
        return sid, "CRASH", elapsed, r.stderr.strip().splitlines()[-1], ""
    mids = sorted(outdir.glob(sid.stem + "_song*.mid")) if all_songs else [out]
    if not mids or not all(m.exists() for m in mids):
        return sid, "CRASH" if r.returncode else "INVALID", elapsed, r.stderr.strip()[-200:], ""
    notes = 0
    for m in mids:
        try:
            notes += check_smf(m.read_bytes())["notes"]
        except MidiError as e:
            return sid, "INVALID", elapsed, "%s: %s" % (m.name, e), warnings
    return sid, "OK" if notes else "SILENT", elapsed, "notes=%d" % notes, warnings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--out", type=Path, default=None, help="keep MIDI files here (default: temporary)")
    ap.add_argument("--songs", choices=("start", "all"), default="start")
    a = ap.parse_args(argv)
    files = collect(a.paths)
    if not files:
        print("no .sid files found", file=sys.stderr)
        return 2
    tmp = None
    if a.out is None:
        tmp = tempfile.TemporaryDirectory(prefix="sid2midi-corpus-")
        outdir = Path(tmp.name)
    else:
        outdir = a.out
        outdir.mkdir(parents=True, exist_ok=True)
    counts = {}
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as pool:
        futures = [pool.submit(convert_one, f, outdir, a.seconds, a.timeout, a.songs == "all") for f in files]
        for fut in cf.as_completed(futures):
            sid, status, elapsed, detail, warnings = fut.result()
            counts[status] = counts.get(status, 0) + 1
            line = "%-8s %6.1fs  %s  %s" % (status, elapsed, sid.name, detail)
            if warnings:
                line += "  [%s]" % warnings
            print(line, flush=True)
    print("\nsummary: " + ", ".join("%s %d" % kv for kv in sorted(counts.items())) + " (of %d)" % len(files))
    if tmp:
        tmp.cleanup()
    return 1 if BAD & counts.keys() else 0


if __name__ == "__main__":
    raise SystemExit(main())
