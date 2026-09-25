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


class CPU6502Final100ClosureTests(unittest.TestCase):
    def test_call_sentinel_stops_after_rts_to_0001(self):
        mem = Mem()
        c = mem.cpu()
        mem.m[0x2000:0x2003] = bytes([0xA9, 0x42, 0x60])
        cyc = c.call(0x2000)
        self.assertEqual(c.a, 0x42)
        self.assertEqual(c.pc, 0x0001)
        self.assertGreater(cyc, 0)
        self.assertFalse(c.jammed)

    def test_jsr_rts_stack_return_is_correct(self):
        mem = Mem()
        c = mem.cpu()
        # JSR $1010; LDA #$77; RTS; sub: LDX #$55; RTS
        mem.m[0x1000:0x1007] = bytes([0x20,0x10,0x10,0xA9,0x77,0x60,0xEA])
        mem.m[0x1010:0x1013] = bytes([0xA2,0x55,0x60])
        c.call(0x1000)
        self.assertEqual((c.x, c.a), (0x55, 0x77))
        self.assertEqual(c.pc, 0x0001)

    def test_brk_pushes_b_flag_and_vectors(self):
        mem = Mem()
        c = mem.cpu()
        mem.m[0xFFFE] = 0x34
        mem.m[0xFFFF] = 0x12
        c.pc = 0x2000
        c.C = 1
        c.Z = 0
        c.I = 0
        mem.m[0x2000] = 0x00
        self.assertTrue(c.step())
        self.assertEqual(c.pc, 0x1234)
        self.assertEqual(c.I, 1)
        # BRK incremented PC by opcode fetch + padding byte => pushed $2002.
        pushed_p = mem.m[0x01FB]
        pushed_lo = mem.m[0x01FC]
        pushed_hi = mem.m[0x01FD]
        self.assertEqual((pushed_hi, pushed_lo), (0x20, 0x02))
        self.assertTrue(pushed_p & 0x10)
        self.assertTrue(pushed_p & 0x20)

    def test_rti_restores_status_and_pc(self):
        mem = Mem()
        c = mem.cpu()
        # Stack as if interrupt had pushed P=$C3 PC=$4567, then RTI.
        c.sp = 0xFA
        mem.m[0x01FB] = 0xC3
        mem.m[0x01FC] = 0x67
        mem.m[0x01FD] = 0x45
        mem.m[0x3000] = 0x40
        c.pc = 0x3000
        self.assertTrue(c.step())
        self.assertEqual(c.pc, 0x4567)
        self.assertEqual((c.C, c.Z, c.N, c.V), (1,1,1,1))

    def test_zero_page_index_and_indirect_wrap(self):
        mem = Mem()
        c = mem.cpu()
        # LDX #$02; LDA $FF,X -> $01; LDA ($FE,X) -> pointer at $00/$01.
        mem.m[0x1000:0x1008] = bytes([0xA2,0x02,0xB5,0xFF,0xA1,0xFE,0x60,0xEA])
        mem.m[0x0001] = 0x44
        mem.m[0x0000] = 0x00
        mem.m[0x0001] = 0x20
        mem.m[0x2000] = 0x99
        c.call(0x1000)
        self.assertEqual(c.a, 0x99)

    def test_branch_cycle_counts(self):
        # Not taken: base 2.
        mem = Mem()
        c = mem.cpu()
        mem.m[0x1000:0x1002] = bytes([0xD0,0x10])
        c.Z=1
        c.pc=0x1000
        self.assertTrue(c.step())
        self.assertEqual(c.cycles, 2)
        self.assertEqual(c.pc, 0x1002)
        # Taken same page: base 2 + 1.
        mem = Mem()
        c = mem.cpu()
        mem.m[0x1000:0x1002] = bytes([0xD0,0x10])
        c.Z=0
        c.pc=0x1000
        self.assertTrue(c.step())
        self.assertEqual(c.cycles, 3)
        self.assertEqual(c.pc, 0x1012)
        # Taken page cross: base 2 + 1 + 1.
        mem = Mem()
        c = mem.cpu()
        mem.m[0x10FD:0x10FF] = bytes([0xD0,0x01])
        c.Z=0
        c.pc=0x10FD
        self.assertTrue(c.step())
        self.assertEqual(c.cycles, 4)
        self.assertEqual(c.pc, 0x1100)

    def test_decimal_adc_reference_cases(self):
        cases = [
            (0x00,0x00,0,0x00,0), (0x09,0x01,0,0x10,0), (0x45,0x55,0,0x00,1),
            (0x99,0x01,0,0x00,1), (0x50,0x49,1,0x00,1),
        ]
        for a,m,cin,res,cout in cases:
            mem=Mem()
            c=mem.cpu()
            c.D=1
            c.a=a
            c.C=cin
            c._adc(m)
            self.assertEqual((c.a,c.C), (res,cout), (a,m,cin))

    def test_decimal_sbc_reference_cases(self):
        cases = [
            (0x00,0x01,1,0x99,0), (0x10,0x01,1,0x09,1), (0x50,0x49,1,0x01,1),
            (0x00,0x00,1,0x00,1), (0x00,0x00,0,0x99,0),
        ]
        for a,m,cin,res,cout in cases:
            mem=Mem()
            c=mem.cpu()
            c.D=1
            c.a=a
            c.C=cin
            c._sbc(m)
            self.assertEqual((c.a,c.C), (res,cout), (a,m,cin))

    def test_kernal_irq_exit_restores_original_context_once(self):
        mem = Mem()
        c = mem.cpu()
        mem.m[0x0314] = 0x00
        mem.m[0x0315] = 0x20
        # IRQ handler mutates registers, then jumps to KERNAL restore/exit marker.
        mem.m[0x2000:0x2009] = bytes([0xA9,0xAA,0xA2,0xBB,0xA0,0xCC,0x4C,0x31,0xEA])
        c.pc = 0x3456
        c.a=0x11
        c.x=0x22
        c.y=0x33
        c.C=1
        c.I=0
        c.D=1
        sp0 = c.sp
        c.irq(0x0314, kernal=True)
        self.assertEqual((c.a,c.x,c.y,c.pc,c.sp), (0x11,0x22,0x33,0x3456,sp0))
        self.assertEqual((c.C,c.D), (1,1))

    def test_all_256_opcodes_are_bounded(self):
        for op in range(256):
            mem = Mem()
            c = mem.cpu()
            mem.m[0x1000] = op
            c.pc = 0x1000
            # Seed operands and vectors to valid memory so every opcode can execute one step.
            mem.m[0x1001] = 0x00
            mem.m[0x1002] = 0x20
            mem.m[0xFFFE] = 0x00
            mem.m[0xFFFF] = 0x30
            c.step()
            # JAM: opcode, next byte and nine reads of $FFFF/$FFFE (SingleStepTests)
            self.assertLessEqual(c.cycles, 11 if op in c.JAM_OPCODES else 9, hex(op))


if __name__ == '__main__':
    unittest.main()
