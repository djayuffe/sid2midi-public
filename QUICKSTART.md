# Quickstart

```bash
unzip sid2midi_superhuman_cpu_nmos_v3.1.1.zip
cd sid2midi_superhuman_cpu_nmos
./validate_release.sh
```

Smoke conversion:

```bash
./run_example.sh
```

Your own SID:

```bash
python3 sid2midi.py /path/to/tune.sid --report                   # all defaults, writes tune.mid
python3 sid2midi.py /path/to/tune.sid --auto --seconds 600       # intro + one loop
python3 sid2midi.py /path/to/tune.sid --all-songs -o out/tune.mid
python3 sid2midi.py /path/to/tune.sid --song 2 --bpm 125 --drumvoice 3 -o tune.mid
```

A whole collection:

```bash
python3 tools/validate_corpus.py /path/to/sids --seconds 60 --out /path/to/midi
```

MUS (Compute!'s Sidplayer) files need the Sidplayer player routine, which is not
included; pass your copy with `--sidplayer FILE` (see README).

RSID/BASIC tunes and players that exit through the KERNAL need the C64 ROM
images in `roms/` (included in the release archive, not in the public source
repository; see `roms/README.md`).
