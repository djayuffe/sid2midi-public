#!/usr/bin/env python3
"""Run VICE testbench programs (exit-code tests) on the sid2midi C64 model.

Usage:
    python3 tools/testbench.py TESTLIST TESTPROGS_ROOT [--filter REGEX] [--jobs N]
                               [--json results.json] [--verbose]

TESTLIST is VICE's testbench/x64-testlist.txt (lines: path,prg,method,cycles,options).
Paths in it are relative to testprogs/testbench/; TESTPROGS_ROOT is the testprogs
directory.  Only "exitcode" tests are run on a C64 model with old 6526 CIAs;
cia-new -> 6526A CIAs.  Options: sid-old -> 6581, sid-new -> 8580,
vicii-ntsc/vicii-ntscold -> NTSC (6567R8 / 6567R56A), vicii-new -> 8565/8562;
the cycle-exact VIC-II (vicii_sc.py) is used unless TESTBENCH_VIC=fast.  Tests that mount disks or cartridges are skipped.

A test passes when it writes $00 to the debug cartridge register $D7FF, fails on
any other value, and times out when the cycle limit from the list is reached.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sid2midi as S  # noqa: E402

SKIP_OPTIONS = ("mountd64", "mountcrt", "mountg64", "mountp64", "mounttap")


def parse_testlist(path, root):
    tests = []
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) < 4 or parts[2] != "exitcode" or not parts[1]:
            continue
        rel, prg, cycles, options = parts[0], parts[1], int(parts[3]), parts[4:]
        rel = rel[3:] if rel.startswith("../") else "testbench/" + rel[2:] if rel.startswith("./") else rel
        tests.append(SimpleNamespace(
            name=rel + prg, path=str(Path(root) / rel / prg), cycles=cycles, options=options,
            skip=next((o for o in options if o.startswith(SKIP_OPTIONS)), None),
            expect_error=any(o == "expect:error" for o in options),
            expect_timeout=any(o == "expect:timeout" for o in options)))
    return tests


def run_program(path, cycles, pal=True, sid_model="6581", cia_model="6526", vic_model=None):
    """Cold-start a C64, load and RUN a PRG, stop at the debug-cart write or limit."""
    data = Path(path).read_bytes()
    load = data[0] | data[1] << 8
    body = data[2:]
    c = S.C64((), pal)
    c.restore_state(S.cold_reset_state(pal))
    if hasattr(c, "set_sid_model"):
        c.set_sid_model(sid_model)
    c.set_cia_model(cia_model)
    if vic_model:
        c.use_vicii_sc(vic_model)
    c.ram[load:load + len(body)] = body
    prog = SimpleNamespace(load=load, end=load + len(body))
    warnings = []
    S._start_basic_program(c, prog, 0, warnings)
    c.debug_cart = True
    start = time.time()
    try:
        reason = c.run(cycles)
        code = None
    except S.DebugCartExit as e:
        reason, code = "exit", e.code
    return SimpleNamespace(reason=reason, code=code, cycles=c.cpu.cycles, seconds=time.time() - start,
                           pc=c.cpu.pc)


def execute(test):
    opts = test.options
    pal = not any(o in ("vicii-ntsc", "vicii-ntscold") for o in opts)
    sid_model = "8580" if "sid-new" in opts else "6581"
    cia_model = "6526A" if "cia-new" in opts else "6526"
    new_vic = "vicii-new" in opts
    if "vicii-ntscold" in opts:
        vic_model = "6567R56A"
    elif "vicii-ntsc" in opts:
        vic_model = "8562" if new_vic else "6567"
    else:
        vic_model = "8565" if new_vic else "6569"
    if os.environ.get("TESTBENCH_VIC", "sc") != "sc":
        vic_model = None
    try:
        r = run_program(test.path, test.cycles, pal, sid_model, cia_model, vic_model)
    except Exception as e:  # noqa: BLE001 - report emulator crashes as results
        return test.name, "CRASH", "%s: %s" % (type(e).__name__, e), 0.0
    if test.expect_timeout:
        # A jammed CPU stays halted until the limit: that is the expected outcome.
        status = "PASS" if r.reason in ("time", "jam") else "FAIL"
        detail = "exit $%02X at cycle %d" % (r.code, r.cycles) if r.reason == "exit" else \
            "%s at cycle %d (pc $%04X)" % (r.reason, r.cycles, r.pc)
    elif r.reason == "exit":
        ok = (r.code == 0) != test.expect_error
        status = "PASS" if ok else "FAIL"
        detail = "exit $%02X at cycle %d" % (r.code, r.cycles)
    elif r.reason == "jam":
        status, detail = "FAIL", "CPU jam at $%04X" % r.pc
    else:
        status, detail = ("PASS" if test.expect_error else "TIMEOUT"), "no exit after %d cycles (pc $%04X)" % (r.cycles, r.pc)
    return test.name, status, detail, r.seconds


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("testlist")
    ap.add_argument("root")
    ap.add_argument("--filter", default=None, help="regular expression on test path")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--verbose", action="store_true", help="print passing tests too")
    a = ap.parse_args(argv)
    tests = parse_testlist(a.testlist, a.root)
    if a.filter:
        rx = re.compile(a.filter)
        tests = [t for t in tests if rx.search(t.name)]
    runnable = [t for t in tests if not t.skip and Path(t.path).is_file()]
    skipped = len(tests) - len(runnable)
    counts, results = {}, [None] * len(runnable)
    with cf.ProcessPoolExecutor(max_workers=a.jobs) as pool:
        futures = {pool.submit(execute, t): i for i, t in enumerate(runnable)}
        for done, fut in enumerate(cf.as_completed(futures), 1):
            name, status, detail, secs = fut.result()
            counts[status] = counts.get(status, 0) + 1
            results[futures[fut]] = {"test": name, "status": status, "detail": detail, "seconds": round(secs, 2)}
            if status != "PASS" or a.verbose:
                print("%-7s %6.1fs  %s  %s  [%d/%d]" % (status, secs, name, detail, done, len(runnable)), flush=True)
    print("\nsummary: %s  (skipped %d)" % (", ".join("%s %d" % kv for kv in sorted(counts.items())), skipped))
    if a.json:
        a.json.write_text(json.dumps(results, indent=1) + "\n")
    return 0 if set(counts) <= {"PASS"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
