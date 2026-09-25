# Testing

sid2midi is checked on four levels: unit tests (shipped), the VICE test
programs, the SingleStepTests CPU vectors and a corpus of real tunes (reference
data not shipped). The results of the current release are in
[FINAL_CLOSURE_REPORT.md](../FINAL_CLOSURE_REPORT.md); what each check means for
accuracy is in [ACCURACY.md](ACCURACY.md).

## Release validation

```bash
./validate_release.sh
```

Runs, in order: a Python syntax check of every module, test and tool; the unit
tests; the opcode coverage report; a smoke conversion of
`examples/simple_pulse.sid` checked with `tools/midicheck.py`; the SHA-256
manifest (`SHA256SUMS.txt`); and the release file layout. The C64 ROM images
are optional: without them the ROM-dependent tests are skipped.

## Unit tests

```bash
python3 -m unittest discover -s tests                       # all
python3 -m unittest discover -s tests -p test_residfp.py    # one file
```

| File | Covers |
|------|--------|
| `test_cpu6502_nmos_upgrade.py`, `test_cpu6502_final_100_closure.py` | CPU: per-cycle bus accesses, undocumented opcodes, decimal mode (Bruce Clark's NMOS equations), interrupt timing |
| `test_mus_vic_cpu.py` | RDY-dependent CPU rules; VIC-II cycle tables against VICE; fast vs full VIC-II register read-back; VSP idle fetch; CIA port B; MUS detection, assembler and installation |
| `test_cia6526_lorenz.py` | all 41,664 register-access cases of Lorenz `cia1ta` / `cia1tb` against `cia_vice.py` |
| `test_residfp.py` | libresidfp's own unit tests, ported |
| `test_sid_writeback_rules.py` | the SID noise writeback and 8580 noise+pulse rules that deviate from libresidfp |
| `test_fast_vic_open_bus.py` | open-bus reads switching the fast VIC-II path to the full chip |
| `test_sid2midi_hardware.py` | CIA timers in the machine, SID models, register read-back, header repairs, continuous machine |
| `test_sid2midi_release.py` | memory map, headers, called and continuous playback, conversion, loops and envelopes, examples; RSID tests need the ROMs |
| `test_singlesteptests_tool.py` | the SingleStepTests harness itself |

Without `roms/*.bin`, 13 tests are skipped.

## VICE test programs

The VICE project maintains hundreds of C64 test programs, many with reference
data measured on real chips. `tools/testbench.py` runs the ones that report a
result through the debug cartridge register (`$D7FF`).

**Getting them.** Check out the `testprogs` directory of the VICE Subversion
repository (`https://svn.code.sf.net/p/vice-emu/code/`). The test list is
`testprogs/testbench/x64-testlist.txt`.

**Running.**

```bash
python3 tools/testbench.py testprogs/testbench/x64-testlist.txt testprogs \
    --jobs 4 --json results.json                             # everything
python3 tools/testbench.py testprogs/testbench/x64-testlist.txt testprogs \
    --filter '^SID/' --verbose                               # one group, show passes
```

- A program **passes** when it writes `$00` to `$D7FF`, **fails** on any other
  value and **times out** at the cycle limit from the list.
- Machine per test: PAL breadbin with 6526 CIAs and a 6581, changed by the test
  options: `cia-new` → 6526A, `sid-new` → 8580, `vicii-ntsc` → 6567R8,
  `vicii-ntscold` → 6567R56A, `vicii-new` → 8565 (PAL) / 8562 (NTSC).
- The full VIC-II port is always used; `TESTBENCH_VIC=fast` runs the fast
  model instead (useful to check the fast model against the same programs).
- Skipped: tests that mount disk or cartridge images, and hardware sid2midi
  does not emulate (REU, disk drives, memory expansions).
- A full run takes many CPU hours; run groups (`--filter '^VICII/'`,
  `'^CIA/'`, `'^SID/'`, `'^interrupts/'`, `'^CPU/'`) while working on a chip.

**Comparing with VICE.** VICE publishes x64sc result files for the same list.
A program that also fails in VICE usually probes behaviour beyond the
reference; see "Making an accuracy change" in [DEVELOPMENT.md](DEVELOPMENT.md).

**Debugging one program.** Run it in-process and log what it reads:

```python
import sys; sys.path[:0] = [".", "tools"]
import testbench
result = testbench.run_program("testprogs/SID/wb_testsuite/noise_writeback_check_F_to_8_old.prg",
                               30_000_000, True, "6581")
print(result)
```

`run_program(path, cycles, pal=True, sid_model="6581", cia_model="6526",
vic_model=None)` cold-starts the machine, loads and runs the PRG, and stops at
the debug-cartridge write or the cycle limit. Wrap chip methods to trace them
(example in [DEVELOPMENT.md](DEVELOPMENT.md)). Many SID programs keep their
expected values in the program itself; in `wb_testsuite` the table sits at
`$08D5`: previous control value, new control value, count, then the expected
OSC3 reads.

## SingleStepTests (CPU)

The SingleStepTests project publishes 10,000 random vectors per 6502 opcode,
each with every bus access of the instruction.

**Getting them.** The `6502/v1` directory of the SingleStepTests `65x02`
repository on GitHub (256 JSON files, about 1 GB).

```bash
python3 tools/singlesteptests.py 65x02/6502/v1 --jobs 4
python3 tools/singlesteptests.py 65x02/6502/v1 --opcodes 8b,93,9e --show 5
```

Every bus access (address, value, read or write), the final registers and the
final memory are compared; the B flag and bit 5 are ignored. Expected result:

```text
$8B: 10000 vectors, 8722 pass, 1278 known, 0 FAIL
summary: 256 opcodes, 2560000 vectors, 2558722 pass, 1278 known (ANE magic $EE), 0 fail
```

The 1,278 ANE vectors assume the magic constant `$EE`; sid2midi uses `$EF`, as
measured on the C64 (see [ACCURACY.md](ACCURACY.md)). Any other difference
makes the tool exit with status 1.

## Corpus

```bash
python3 tools/validate_corpus.py ~/HVSC/MUSICIANS/H/Hubbard_Rob --seconds 60 --jobs 4
python3 tools/validate_corpus.py tunes/*.sid --seconds 180 --timeout 3600 --out out/
python3 tools/midicheck.py out/*.mid
```

Each tune is converted in its own process with a wall-clock timeout, and each
MIDI file is validated structurally (chunk framing, running status, data
ranges, end of track, balanced notes). Results per tune: `OK`, `SILENT` (valid
but no notes), `INVALID`, `REJECTED` (malformed file), `CRASH`, `TIMEOUT`; the
exit status is 1 on any `INVALID`, `CRASH` or `TIMEOUT`.

To check a change, convert a fixed set of tunes with the same settings before
and after and compare status and note count file by file; any difference must
be explained (the release reports do this for 33 tunes).

## Speed

Compare CPU time, not wall time, on an idle machine, alternating the versions:

```bash
/usr/bin/time -p python3 -B sid2midi.py tune.sid -o /tmp/a.mid --seconds 30
```

The MIDI files of two versions should be identical apart from the version
string unless the change was meant to alter the output.
