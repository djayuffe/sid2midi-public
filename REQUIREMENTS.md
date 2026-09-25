# Requirements

Runtime:

```text
Python 3.9 or newer (3.1.3 test suite verified on CPython 3.9.6 and 3.14.2, macOS arm64)
No third-party Python packages
bash, plus sha256sum or shasum, for validate_release.sh
```

Included assets:

```text
examples/simple_pulse.sid
```

C64 ROM images (`roms/basic.bin`, `roms/chargen.bin`, `roms/kernal.bin`):
included in the release archive, not in the public source repository; needed
for RSID/BASIC tunes, see `roms/README.md`.

Validate:

```bash
python3 --version
./validate_release.sh
```
