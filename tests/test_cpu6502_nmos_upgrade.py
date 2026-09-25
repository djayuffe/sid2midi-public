import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cpu6502 import CPU6502


class Mem:
    def __init__(self):
        self.m = bytearray(65536)

    def read(self, a):
        return self.m[a & 0xFFFF]

    def write(self, a, v):
        self.m[a & 0xFFFF] = v & 0xFF

    def cpu(self):
        return CPU6502(self.read, self.write)


class CPU6502NMOSUpgradeTests(unittest.TestCase):
    def test_decimal_adc_sbc(self):
        mem = Mem()
        c = mem.cpu()
        # SED; LDA #$45; ADC #$55; STA $2000; SBC #$01; STA $2001; RTS
        mem.m[0x1000:0x100E] = bytes([0xF8,0xA9,0x45,0x69,0x55,0x8D,0x00,0x20,0xE9,0x01,0x8D,0x01,0x20,0x60])
        c.C = 0
        c.call(0x1000)
        self.assertEqual(mem.m[0x2000], 0x00)
        self.assertEqual(c.C, 0)  # after 00 - 01 decimal, borrow
        self.assertEqual(mem.m[0x2001], 0x99)

    def test_jmp_indirect_page_wrap_bug(self):
        mem = Mem()
        c = mem.cpu()
        mem.m[0x1000:0x1003] = bytes([0x6C,0xFF,0x12])  # JMP ($12FF)
        mem.m[0x12FF] = 0x34
        mem.m[0x1200] = 0x12  # NMOS wrap, not $1300
        c.pc = 0x1000
        self.assertTrue(c.step())
        self.assertEqual(c.pc, 0x1234)

    def test_page_cross_cycle_penalty(self):
        mem = Mem()
        c = mem.cpu()
        mem.m[0x1000:0x1004] = bytes([0xBD,0xFF,0x20,0x60])  # LDA $20FF,X
        mem.m[0x2100] = 0x7E
        c.x = 1
        c.pc = 0x1000
        self.assertTrue(c.step())
        self.assertEqual(c.a, 0x7E)
        self.assertEqual(c.cycles, CPU6502.CYC[0xBD] + 1)

    def test_illegal_lax_sax_dcp(self):
        mem = Mem()
        c = mem.cpu()
        # LDA #$AA; LDX #$0F; SAX $20; LAX $20; DCP $20; RTS
        mem.m[0x1000:0x100B] = bytes([0xA9,0xAA,0xA2,0x0F,0x87,0x20,0xA7,0x20,0xC7,0x20,0x60])
        c.call(0x1000)
        self.assertEqual(mem.m[0x20], 0x09)
        self.assertEqual(c.a, 0x0A)
        self.assertEqual(c.x, 0x0A)
        self.assertEqual(c.C, 1)

    def test_kernal_irq_ea31_exit(self):
        mem = Mem()
        c = mem.cpu()
        mem.m[0x0314] = 0x00
        mem.m[0x0315] = 0x20
        # INC $4000; JMP $EA31
        mem.m[0x2000:0x2006] = bytes([0xEE,0x00,0x40,0x4C,0x31,0xEA])
        c.pc = 0x3000
        c.I = 0
        c.a = 0x11
        c.x = 0x22
        c.y = 0x33
        c.irq(0x0314, kernal=True)
        self.assertEqual(mem.m[0x4000], 1)
        self.assertEqual((c.a,c.x,c.y), (0x11,0x22,0x33))
        self.assertEqual(c.pc, 0x3000)

    def test_all_opcodes_have_handlers(self):
        mem = Mem()
        c = mem.cpu()
        self.assertEqual(sum(1 for op in c.ops if op is not None), 256)


if __name__ == '__main__':
    unittest.main()


class CPU6502NMOSClosureTests(unittest.TestCase):
    def test_kil_jam_stops_execution_safely(self):
        mem = Mem()
        c = mem.cpu()
        # KIL/JAM at $1000 followed by LDA #$55.  CPU must stop, not continue.
        mem.m[0x1000:0x1004] = bytes([0x02, 0xA9, 0x55, 0x60])
        c.pc = 0x1000
        self.assertFalse(c.step())
        self.assertTrue(c.jammed)
        self.assertEqual(c.a, 0)
        self.assertEqual(c.cycles, 11)          # opcode, next byte, then 9 reads of $FFFF/$FFFE

    def test_las_abs_y_sets_a_x_sp(self):
        mem = Mem()
        c = mem.cpu()
        # LAS $20FF,Y with Y=1 reads $2100 and ANDs with SP.
        mem.m[0x1000:0x1004] = bytes([0xBB, 0xFF, 0x20, 0x60])
        mem.m[0x2100] = 0xF0
        c.sp = 0x7C
        c.y = 1
        c.pc = 0x1000
        self.assertTrue(c.step())
        self.assertEqual((c.a, c.x, c.sp), (0x70, 0x70, 0x70))
        self.assertEqual(c.N, 0)
        self.assertEqual(c.Z, 0)

    def test_unstable_store_family_practical_write_values(self):
        mem = Mem()
        c = mem.cpu()
        # A=$FF X=$0F Y=$F0; SHX $20FF,Y crosses a page: the value is
        # X & (base high + 1) = $0F & $21 = $01, and on the page crossing that
        # value also becomes the target's high byte -> $01EF (Lorenz shxay).
        mem.m[0x1000:0x100A] = bytes([0xA9,0xFF,0xA2,0x0F,0xA0,0xF0,0x9E,0xFF,0x20,0x60])
        c.call(0x1000)
        self.assertEqual(mem.m[0x01EF], 0x01)
        self.assertEqual(mem.m[0x21EF], 0x00)

    def test_all_kil_opcodes_stop(self):
        for op in (0x02,0x12,0x22,0x32,0x42,0x52,0x62,0x72,0x92,0xB2,0xD2,0xF2):
            mem = Mem()
            c = mem.cpu()
            mem.m[0x1000] = op
            c.pc = 0x1000
            self.assertFalse(c.step(), hex(op))
            self.assertTrue(c.jammed, hex(op))
