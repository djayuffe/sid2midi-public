# C64 ROM images

sid2midi runs RSID tunes, PSIDs with play address 0 and C64 BASIC tunes on a
real KERNAL/BASIC cold start, and PSID players that exit through the KERNAL
interrupt routines (`JMP $EA31`, `$EA81`). For that it needs the original
Commodore 64 ROM images in this directory:

| File          | Size       | ROM                          | SHA-256 of the images sid2midi is tested with |
|---------------|------------|------------------------------|-----------------------------------------------|
| `basic.bin`   | 8192 bytes | BASIC V2, 901226-01          | `89878cea0a268734696de11c4bae593eaaa506465d2029d619c0e0cbccdfa62d` |
| `chargen.bin` | 4096 bytes | character generator, 901225-01 | `fd0d53b8480e86163ac98998976c72cc58d5dd8eb824ed7b829774e74213b420` |
| `kernal.bin`  | 8192 bytes | KERNAL, 901227-03            | `83c60d47047d7beab8e5b7bf6f67f80daa088b7a6a27de0d7e016f6484042721` |

The ROMs are copyrighted and are **not** covered by sid2midi's GPL licence. The
public source repository does not include them. Use images you are entitled to:
dump them from your own C64, or copy them from an emulator installation you
obtained legitimately (VICE ships them in its C64 data directory as
`basic-901226-01.bin`, `chardgen-901225-01.bin` and `kernal-901227-03.bin`;
rename them to the file names above).

Check your images:

```bash
shasum -a 256 roms/basic.bin roms/chargen.bin roms/kernal.bin
```

## Without ROMs

- PSID tunes with a play address convert normally, unless their player jumps
  into the KERNAL.
- RSID and PSID play-0 tunes run in a minimal environment instead of a real cold
  start, with a warning; their output may be incomplete.
- C64 BASIC tunes cannot run.
- Tests that need the ROMs are skipped; `validate_release.sh` still passes.
