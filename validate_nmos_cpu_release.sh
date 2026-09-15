#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
TMPBASE="${TMPDIR:-/tmp}"
WORK="$(mktemp -d "${TMPBASE%/}/sid2midi-validate.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

echo "== $(python3 sid2midi.py --version) release validation =="
python3 - <<'PY'
from pathlib import Path
files = ['cpu6502.py', 'cia_vice.py', 'vicii_sc.py', 'mus.py', 'residfp.py', 'sid2midi.py']
files += [str(p) for p in sorted(Path('tests').glob('test_*.py'))]
files += [str(p) for p in sorted(Path('tools').glob('*.py'))]
for fn in files:
    compile(Path(fn).read_text(encoding='utf-8'), fn, 'exec')
print('Python syntax check OK (%d files)' % len(files))
PY

echo "== Regression tests (CPU + converter) =="
python3 -m unittest discover -s tests

echo "== Opcode coverage =="
python3 tools/cpu_opcode_coverage_report.py

echo "== SID smoke conversion =="
python3 sid2midi.py examples/simple_pulse.sid --seconds 1 --report -o "$WORK/simple_pulse.mid"
python3 tools/midicheck.py "$WORK/simple_pulse.mid"

echo "sid2midi release validation OK"
