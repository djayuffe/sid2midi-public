"""Open-bus reads on the fast VIC-II path switch to the full chip (except KERNAL colour RAM copies)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sid2midi as S  # noqa: E402
from vicii_sc import VicIISC  # noqa: E402


def machine():
    c = S.C64((), True)
    c.time_base, c.cpu.cycles = 0, 1000
    c.ram[0x00], c.ram[0x01] = 0x2F, 0x37          # KERNAL, BASIC and I/O mapped in
    return c


class FastPathOpenBusTests(unittest.TestCase):
    def test_colour_ram_read_from_program_code_uses_the_full_chip(self):
        c = machine()
        c.write(0xD800, 0xFE)                        # the chip stores only the low nibble
        c.cpu.pc = 0x1000
        v = c.read(0xD800)
        self.assertIsInstance(c.vic, VicIISC)
        self.assertEqual(v & 0x0F, 0x0E)
        self.assertEqual(v & 0xF0, c.vic.read_phi1() & 0xF0)

    @unittest.skipUnless(S.KERNAL, "needs roms/kernal.bin")
    def test_colour_ram_copy_inside_the_kernal_stays_on_the_fast_model(self):
        c = machine()
        c.write(0xD800, 0x05)
        c.cpu.pc = 0xE9E0                            # KERNAL screen editor line copy
        self.assertEqual(c.read(0xD800) & 0x0F, 0x05)
        self.assertNotIsInstance(c.vic, VicIISC)

    def test_colour_ram_read_with_kernal_banked_out_uses_the_full_chip(self):
        c = machine()
        c.ram[0x01] = 0x35                           # RAM at $E000, I/O still mapped
        c.cpu.pc = 0xE100
        c.read(0xD800)
        self.assertIsInstance(c.vic, VicIISC)

    def test_unmapped_io_read_uses_the_full_chip(self):
        c = machine()
        c.cpu.pc = 0x1000
        v = c.read(0xDE00)
        self.assertIsInstance(c.vic, VicIISC)
        self.assertEqual(v, c.vic.read_phi1())


if __name__ == "__main__":
    unittest.main()
