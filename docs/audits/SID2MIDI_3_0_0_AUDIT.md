# sid2midi 3.0.0 Audit — cycle, bit and timing accuracy

Scope: the emulated C64 of sid2midi (CPU, CIAs, VIC-II bus behaviour, SID
read-back) measured against external references, and every change made to
agree with them. Release results (pass/fail counts, failing tests, corpus and
timing) are in `FINAL_CLOSURE_REPORT.md`.

## References used

| Reference | Licence | Use |
|-----------|---------|-----|
| Wolfgang Lorenz C64 Emulator Test Suite 2.15 (sources + programs) | public domain | CPU, CIA, interrupt behaviour; `cia1ta`/`cia1tb` expectations transliterated into `tests/test_cia6526_lorenz.py` |
| VICE test programs (`testprogs`, testbench list `x64-testlist.txt`) | various | run unchanged by `tools/testbench.py`; not redistributed |
| libresidfp at commit b8bdc00 (sources and unit tests) | GPL-2.0-or-later | ported to `residfp.py`; unit tests transliterated into `tests/test_residfp.py` |

Not used: the SingleStepTests 6502 per-instruction vectors, VICE's emulator
source code, disk/cartridge-based and screenshot-compared tests. Only the
VICE test programs that were downloaded (CPU, CIA, interrupts, VIC-II raster
IRQ, SID) could be run; see the report for skipped groups.

## Method

1. Baseline: the 2.1.0 emulator run through the testbench (357 pass, 124 fail,
   9 timeout of the runnable subset at that time).
2. Each failing program was traced at bus-access level (cycle, address,
   value, raster position, stalls, interrupt line changes) and its source
   read, until the difference from the expected result was located.
3. A model change was accepted only if it fixed the failing case without
   breaking any previously passing test; the full test set was rerun after
   each group of changes.
4. Where the expected behaviour could not be derived from chip documentation
   but only from test data, the rule is marked "fitted" below and in the code.

## Changes by component

### CPU — `cpu6502.py` rewritten

| Behaviour | Evidence |
|-----------|----------|
| one bus access per cycle incl. dummy reads, RMW double write | all 256 opcodes: accesses = base cycles + page-cross penalties; Lorenz CPU suite |
| interrupt poll two cycles before fetch; +1 after taken non-page-crossing branch; CLI/SEI/PLP one instruction late | Lorenz `irq`, `nmi`, `branchwrap`, `cli`, `sei`, `plp`; VICE `irqnmi`, `cia-int` |
| NMI takes over BRK/IRQ vector fetch; NMI edge in the last two cycles of an interrupt sequence waits one instruction | Lorenz `nmi` (BRK cases, clock 0-9) |
| SHA/SHX/SHY/TAS page-crossing corruption | Lorenz `shaay`, `shaiy`, `shxay`, `shsay` |
| RDY: reads halt while BA is low; the interrupt decision of an instruction whose last cycle is held repeats (fitted) | VICE irqdma test1-5/7 (and b variants), nmitest6/6b |

### CIA — `cia6526.py` new (original 6526)

| Behaviour | Evidence |
|-----------|----------|
| COUNT2/COUNT3 pipeline, two-stage loads, one-shot sampling, force load | Lorenz `cia1ta`, `cia1tb` (41,664 cases), `cia2ta`, `cia2tb`, `oneshot`, `cnto2`, `flipos`, `loadth` |
| timer B counting TA underflows through the same pipeline | Lorenz `cia1tab`, VICE `cmp-b-counts-a` |
| IRQ one tick after the flag, cancelled by an ICR read; mask enable of a pending flag | Lorenz `icr01`, `imr` |
| timer B bug: ICR read in the underflow cycle loses the TB flag, not the IRQ | VICE `cia-timer-oldcias`, `ciavarious` cia3/3a/4/8 |
| a timer B interrupt survives a mask clear one cycle later (timer A does not) — fitted | VICE `cia-int-nmi` vs `dd0dtest` test 11 |
| back-to-back ICR reads of a running timer's flag act as one read — fitted | VICE `dd0dtest` tests 0c-0e/12/13 vs 17-19 |
| PB6/PB7 outputs | Lorenz `cia1pb6`, `cia1pb7`, `cia2pb6`, `cia2pb7` |
| TOD clock, hour latch, stop on hour write, alarm, AM/PM flip on writing 12 | VICE `ciavarious` cia15 |

### Machine and VIC-II — `sid2midi.py`

| Behaviour | Evidence |
|-----------|----------|
| `$01` port bits 6/7 retain and fade, bit 5 low as input | Lorenz `cpuport`, VICE `CPU/cpuport` |
| idle trap only for the SID player driver | Lorenz `trap16`, `trap17` |
| BA for bad lines (cycles 12-54) and sprite fetches (3 cycles before to end of fetch) | VICE irqdma; alternatives of the sprite window were tested and rejected |

### SID — `residfp.py` new

| Behaviour | Evidence |
|-----------|----------|
| reSIDfp digital part: envelope, waveforms, combined-waveform tables, noise write-back, fades, bus value | libresidfp unit tests; VICE SID tests (busvalue, detect, env_test, envelope, noiselfsrinit, noisewriteback, osc3-wave0, osc_topbit, oscinit, resid-test, ringmod, waveforms, wf12nsr) |

## Known gaps

- VICE irqdma test6/test6b: a PLP whose last cycle is held by sprite DMA.
  Two entries of test6 with identical CPU/VIC sequences differ only by the
  cycle in which the IRQ line falls, and real hardware takes the interrupt
  earlier for the later fall; no rule consistent with the other irqdma data
  was found without further references.
- VICE `irq-ack-vicii`: sprite-sprite collision interrupts are not modelled.
- VICE `wf12nsr-8580`: 8580 noise+pulse read-back differs from the test's
  reference data.
- The fitted CIA rules reproduce the test data but their hardware mechanism is
  not established.
- NTSC (6567R8) sprite fetch cycles are an estimate; no NTSC DMA test ran.
- Only the original 6526 is modelled; the 6526A ("new CIA") tests are skipped.
