#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
TMPBASE="${TMPDIR:-/tmp}"
OUT="${1:-$(mktemp -d "${TMPBASE%/}/sid2midi-example.XXXXXX")/simple_pulse.mid}"
python3 sid2midi.py examples/simple_pulse.sid --seconds 1 --report -o "$OUT"
python3 tools/midicheck.py "$OUT"
echo "Wrote: $OUT"
