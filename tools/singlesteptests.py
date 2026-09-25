#!/usr/bin/env python3
"""Check cpu6502.py against the SingleStepTests 6502 vectors.

The vectors come from the SingleStepTests project (65x02 repository, directory
6502/v1: one JSON file per opcode, 10,000 tests each) and are not included.
Each test sets the registers and memory, executes one instruction and lists
every bus access (address, value, read/write) and the final registers and
memory; all of them are compared.

Usage:
    python3 tools/singlesteptests.py DIR_OR_FILE... [--opcodes 8b,ea] [--limit N]
                                     [--jobs N] [--show N]

Known difference: ANE ($8B) uses the magic constant $EF measured on the C64
($EE only when RDY holds its operand read); vectors that assume $EE and whose
result depends on it are counted as "known" and do not fail the run.
Exit status 1 when any other vector fails.
"""
import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cpu6502 import CPU6502


class Bus:
    """64 KB of memory that logs every access."""

    def __init__(self):
        self.mem = bytearray(0x10000)
        self.log = []

    def read(self, a):
        v = self.mem[a]
        self.log.append((a, v, "read"))
        return v

    def write(self, a, v):
        self.mem[a] = v
        self.log.append((a, v, "write"))


def ane_needs_magic_ee(test):
    """The vector's expected A is ANE with magic $EE, and $EF would give another value."""
    ini = test["initial"]
    ram = dict(ini["ram"])
    imm = ram.get((ini["pc"] + 1) & 0xFFFF, 0)
    ee = (ini["a"] | 0xEE) & ini["x"] & imm
    ef = (ini["a"] | 0xEF) & ini["x"] & imm
    return ee != ef and test["final"]["a"] == ee


def run_test(cpu, bus, test):
    """Execute one vector; return a list of (field, description) differences."""
    ini, fin = test["initial"], test["final"]
    mem = bus.mem
    for a, v in ini["ram"]:
        mem[a] = v
    bus.log = []
    cpu.pc, cpu.sp, cpu.a, cpu.x, cpu.y = ini["pc"], ini["s"], ini["a"], ini["x"], ini["y"]
    cpu._setp(ini["p"])
    cpu.I_poll = cpu.I
    cpu.delay_int = False
    cpu.jammed = False
    cpu.cycles = 0
    cpu.step()
    diffs = []
    expected_bus = [tuple(c) for c in test["cycles"]]
    if bus.log != expected_bus:
        diffs.append(("bus", "bus accesses %s, expected %s" % (bus.log, expected_bus)))
    for field, got in (("pc", cpu.pc), ("s", cpu.sp), ("a", cpu.a), ("x", cpu.x), ("y", cpu.y)):
        if got != fin[field]:
            diffs.append((field, "%s $%02X, expected $%02X" % (field, got, fin[field])))
    if (cpu._getp() | 0x30) != (fin["p"] | 0x30):              # B and bit 5 are not flags
        diffs.append(("p", "p $%02X, expected $%02X" % (cpu._getp() | 0x30, fin["p"] | 0x30)))
    for a, v in fin["ram"]:
        if mem[a] != v:
            diffs.append(("ram", "$%04X = $%02X, expected $%02X" % (a, mem[a], v)))
    for a, _ in ini["ram"]:
        mem[a] = 0
    for a, _, _ in bus.log:
        mem[a] = 0
    return diffs


def check_file(job):
    path, limit, show = job
    tests = json.loads(Path(path).read_text())
    if limit:
        tests = tests[:limit]
    bus = Bus()
    cpu = CPU6502(bus.read, bus.write)
    passed = known = failed = 0
    examples = []
    opcode = Path(path).stem.lower()
    for test in tests:
        diffs = run_test(cpu, bus, test)
        if not diffs:
            passed += 1
        elif opcode == "8b" and {f for f, _ in diffs} <= {"a", "p"} and ane_needs_magic_ee(test):
            known += 1
        else:
            failed += 1
            if len(examples) < show:
                examples.append((test["name"], [d for _, d in diffs]))
    return opcode, len(tests), passed, known, failed, examples


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run SingleStepTests 6502 vectors against cpu6502.py")
    ap.add_argument("paths", nargs="+", help="directory with 00.json ... ff.json, or vector files")
    ap.add_argument("--opcodes", default=None, help="comma-separated hex opcodes (default: all files)")
    ap.add_argument("--limit", type=int, default=0, help="vectors per opcode (default: all)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--show", type=int, default=3, help="failing vectors to print per opcode")
    a = ap.parse_args(argv)
    files = []
    for p in map(Path, a.paths):
        files += sorted(p.glob("*.json")) if p.is_dir() else [p]
    if a.opcodes:
        wanted = {"%02x" % int(o, 16) for o in a.opcodes.split(",")}
        files = [f for f in files if f.stem.lower() in wanted]
    if not files:
        ap.error("no vector files found")
    total = passed = known = failed = 0
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        for opcode, n, ok, kn, bad, examples in ex.map(check_file, [(str(f), a.limit, a.show) for f in files]):
            total, passed, known, failed = total + n, passed + ok, known + kn, failed + bad
            if bad or kn:
                print("$%s: %d vectors, %d pass, %d known, %d FAIL" % (opcode.upper(), n, ok, kn, bad))
            for name, diffs in examples:
                print("    %s: %s" % (name, "; ".join(diffs)))
    print("summary: %d opcodes, %d vectors, %d pass, %d known (ANE magic $EE), %d fail"
          % (len(files), total, passed, known, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
