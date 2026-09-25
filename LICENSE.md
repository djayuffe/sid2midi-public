# Licence

## Package

`residfp.py` and `tests/test_residfp.py` are a port of libresidfp (reSIDfp,
<https://github.com/libsidplayfp/libresidfp>), copyright 2011-2026 Leandro Nini,
2018 VICE Project, 2007-2010 Antti Lankila, 2004,2010 Dag Lem. They are free
software: you can redistribute and/or modify them under the terms of the GNU
General Public License as published by the Free Software Foundation, either
version 2 of the License or (at your option) any later version.

`sid2midi.py` uses `residfp.py`, so sid2midi 3.1.4 as a whole is distributed
under the **GNU General Public License, version 2 or later**
(SPDX: `GPL-2.0-or-later`). It is distributed WITHOUT ANY WARRANTY; without
even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE.

The full licence text is in `COPYING` (GNU GPL version 2, as distributed with
libresidfp).

## Other material

- `tests/test_cia6526_lorenz.py` transliterates expectation tables of Wolfgang
  Lorenz's C64 Emulator Test Suite, which is public domain.
- `roms/*.bin` (release archive only, not in the public source repository) are
  the Commodore 64 BASIC, KERNAL and character ROM images; they are not
  covered by the GPL and remain the property of their copyright holders. See
  `roms/README.md`.
- `cia_vice.py` and `vicii_sc.py` are ports of the VICE emulator's CIA core
  (`src/core/ciacore.c`, `ciatimer.c`) and cycle-based VIC-II
  (`src/viciisc`), copyright the VICE team, GPL-2.0-or-later.
- `mus.py` implements libsidplayfp's MUS loading rules; the Compute!'s
  Sidplayer player routine itself is not included.
- The VICE test programs used for validation (`tools/testbench.py`) are not
  included.
