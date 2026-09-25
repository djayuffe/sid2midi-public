#!/usr/bin/env bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
./validate_nmos_cpu_release.sh

echo "== SHA256 manifest =="
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum -c SHA256SUMS.txt
else
  shasum -a 256 -c SHA256SUMS.txt
fi

python3 - <<'PY'
from pathlib import Path
required = [
    'cpu6502.py', 'cia_vice.py', 'vicii_sc.py', 'mus.py', 'residfp.py', 'sid2midi.py', 'README.md', 'QUICKSTART.md',
    'REQUIREMENTS.md', 'LICENSE.md', 'PROJECT_MANIFEST.md', 'RELEASE_NOTES_v3.1.4.md', 'RELEASE_NOTES_v3.1.3.md', 'RELEASE_NOTES_v3.1.2.md', 'RELEASE_NOTES_v3.1.1.md', 'RELEASE_NOTES_v3.1.0.md', 'RELEASE_NOTES_v3.0.0.md',
    'RELEASE_NOTES_v2.1.0.md', 'RELEASE_NOTES_v2.0.0.md',
    'RELEASE_NOTES_NMOS_CPU.md', 'FINAL_CLOSURE_REPORT.md', 'SHA256SUMS.txt',
    'examples/simple_pulse.sid',
    'tests/test_cpu6502_nmos_upgrade.py', 'tests/test_cpu6502_final_100_closure.py',
    'tests/test_sid2midi_release.py', 'tests/test_sid2midi_hardware.py',
    'tests/test_cia6526_lorenz.py', 'tests/test_residfp.py', 'tests/test_mus_vic_cpu.py',
    'tests/test_fast_vic_open_bus.py', 'tests/test_sid_writeback_rules.py', 'tests/test_singlesteptests_tool.py',
    'tools/testbench.py', 'tools/singlesteptests.py',
    'tools/cpu_opcode_coverage_report.py', 'tools/midicheck.py', 'tools/validate_corpus.py',
    'docs/audits/SID2MIDI_2_0_0_AUDIT.md', 'docs/audits/SID2MIDI_3_0_0_AUDIT.md',
    'docs/audits/SID2MIDI_3_1_0_AUDIT.md', 'COPYING', 'ruff.toml',
    'docs/README.md', 'docs/ARCHITECTURE.md', 'docs/ACCURACY.md', 'docs/MIDI_MAPPING.md',
    'docs/TESTING.md', 'docs/DEVELOPMENT.md',
]
# The C64 ROM images are optional (the public source tree does not include them);
# when any is present, all three must be.
roms = ['roms/basic.bin', 'roms/chargen.bin', 'roms/kernal.bin']
if any(Path(p).exists() for p in roms):
    required += roms
else:
    print('C64 ROMs not present: RSID/BASIC cold start and ROM-dependent tests are skipped')
missing = [p for p in required if not Path(p).exists()]
if missing:
    raise SystemExit('missing required files: ' + ', '.join(missing))
print('Release file layout OK')
PY
echo "sid2midi full release validation OK"
