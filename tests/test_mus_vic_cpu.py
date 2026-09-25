"""MUS support, the VICE VIC-II/CIA ports and the RDY-dependent CPU rules."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mus
import vicii_sc
from cia_vice import Cia6526
from cpu6502 import CPU6502


def mus_file(voices=(b"\x01\x4F", b"\x01\x4F", b"\x01\x4F"), text=b"HELLO\rWORLD\0"):
    head = b"\x00\x00" + b"".join(len(v).to_bytes(2, "little") for v in voices)
    return head + b"".join(voices) + text


class MusDetectionTests(unittest.TestCase):
    def test_voice3_index_and_credits(self):
        data = mus_file((b"\x12\x34\x01\x4F", b"\x01\x4F", b"\x00\x00\x01\x4F"))
        self.assertEqual(mus.voice3_index(data), 8 + 4 + 2 + 4)
        self.assertEqual(mus.credit_lines(data), ["HELLO", "WORLD"])

    def test_rejects_non_mus(self):
        self.assertIsNone(mus.voice3_index(b"PSID" + bytes(120)))
        bad = bytearray(mus_file())
        bad[9] = 0x50                               # voice 1 does not end in HLT
        self.assertIsNone(mus.voice3_index(bytes(bad)))
        self.assertIsNone(mus.voice3_index(b"\0\0\x10"))


class XaAssemblerTests(unittest.TestCase):
    SOURCE = """
        .word $c000
        * = $c000
start   ldx #$03
loop    lda table,x
        sta $02
        sta $d400,x
        dex
        bpl loop
        jmp (vector)
        rts
ptr     = * + 1
        lda $1234
vector  .word start
table   .byt 1, 2, <start, >start
"""

    def test_assembles_addressing_modes_and_symbols(self):
        load, code = mus.assemble_xa(self.SOURCE)
        self.assertEqual(load, 0xC000)
        expected = bytes([
            0xA2, 0x03,                 # ldx #$03
            0xBD, 0x16, 0xC0,           # lda table,x (absolute: symbol)
            0x85, 0x02,                 # sta $02     (zero page literal)
            0x9D, 0x00, 0xD4,           # sta $d400,x
            0xCA,                       # dex
            0x10, 0xF5,                 # bpl loop
            0x6C, 0x14, 0xC0,           # jmp (vector)
            0x60,                       # rts
            0xAD, 0x34, 0x12,           # lda $1234 (ptr = $C012)
            0x00, 0xC0,                 # vector: .word start
            0x01, 0x02, 0x00, 0xC0,     # table
        ])
        self.assertEqual(code, expected)

    def test_errors(self):
        with self.assertRaises(mus.AsmError):
            mus.assemble_xa("* = $1000\n bogus #1\n")
        with self.assertRaises(mus.AsmError):
            mus.assemble_xa("* = $1000\n lda undefined_symbol\n")
        with self.assertRaises(mus.AsmError):
            mus.assemble_xa("* = $1000\n beq far\n* = $1100\nfar rts\n")


class MusInstallTests(unittest.TestCase):
    def player(self):
        code = bytearray(0xC90)
        code[0x407:0x413] = b"\xAD\x1B\xD4\x8D\x00\x10\xAD\x1C\xD4\x8D\x01\x10"
        return 0xE000, bytes(code)

    def test_install_patches_reads_and_data_pointer(self):
        ram = bytearray(0x10000)
        data = mus_file()
        init, play = mus.install(ram, data, self.player())
        self.assertEqual((init, play), (0xEC60, 0xEC80))
        self.assertEqual(bytes(ram[0x900:0x900 + len(data)]), data)
        self.assertEqual(bytes(ram[0xE407:0xE413]), b"\xEA" * 12)
        self.assertEqual((ram[0xE000 + 0xC6E], ram[0xE000 + 0xC70]), (0x02, 0x09))

    def test_overlap_is_rejected(self):
        with self.assertRaises(mus.MusError):
            mus.install(bytearray(0x10000), bytes(0x9000), (0x1000, b"\x60"))

    def test_load_player_binary_and_source(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "player.bin"
            p.write_bytes(b"\x00\xE0\x60")
            self.assertEqual(mus.load_player(str(p)), (0xE000, b"\x60"))
            s = Path(d) / "player.a65"
            s.write_text(";test\n* = $e000\n rts\n")
            self.assertEqual(mus.load_player(str(s)), (0xE000, b"\x60"))


class Mem:
    def __init__(self):
        self.m = bytearray(0x10000)

    def cpu(self):
        return CPU6502(lambda a: self.m[a & 0xFFFF], lambda a, v: self.m.__setitem__(a & 0xFFFF, v & 0xFF))


class RdyCpuRuleTests(unittest.TestCase):
    def stall_at(self, c, cycle, until):
        c.rdy = lambda t: until if t == cycle else t

    def test_ane_magic_depends_on_rdy(self):
        for stall, magic in ((False, 0xEF), (True, 0xEE)):
            mem = Mem()
            c = mem.cpu()
            mem.m[0x1000:0x1002] = b"\x8B\xFF"               # ANE #$FF
            c.pc, c.a, c.x = 0x1000, 0x00, 0xFF
            if stall:
                self.stall_at(c, 1, 5)                       # BA holds the operand read
            c.step()
            self.assertEqual(c.a, magic, "stall=%s" % stall)

    def test_shx_value_is_not_anded_after_a_steal(self):
        for stall, value in ((False, 0x21), (True, 0xFF)):
            mem = Mem()
            c = mem.cpu()
            mem.m[0x1000:0x1003] = b"\x9E\x00\x20"           # SHX $2000,Y
            c.pc, c.x, c.y = 0x1000, 0xFF, 0x10
            if stall:
                self.stall_at(c, 3, 9)                       # steal during the indexed dummy read
            c.step()
            self.assertEqual(mem.m[0x2010], value, "stall=%s" % stall)


class VicTableTests(unittest.TestCase):
    def test_pal_ntsc_and_old_ntsc_tables_match_vice(self):
        pal = vicii_sc.build_cycle_table(63)
        self.assertEqual((pal[57].phi1, pal[57].spr), (vicii_sc.PHI1_SPR_PTR, 0))      # cycle 58
        self.assertEqual(pal[54].spr_ba, 0x01)                                         # cycle 55
        self.assertEqual(pal[62].spr_ba, 0x1C)                                         # cycle 63
        self.assertEqual([n + 1 for n, e in enumerate(pal) if e.spr_check == vicii_sc.SPR_DMA], [55, 56])
        ntsc = vicii_sc.build_cycle_table(65)
        self.assertEqual((ntsc[0].phi1, ntsc[0].spr, ntsc[0].spr_ba), (vicii_sc.PHI1_SPR_DMA1, 3, 0x38))
        self.assertEqual(ntsc[9].phi1, vicii_sc.PHI1_IDLE)
        self.assertEqual([n + 1 for n, e in enumerate(ntsc) if e.spr_check == vicii_sc.SPR_DISP], [59])
        self.assertEqual(ntsc[62].xpos, 0x180)                                         # $184 / 8 * 8
        old = vicii_sc.build_cycle_table(64)
        self.assertEqual((old[0].phi1, old[0].spr), (vicii_sc.PHI1_SPR_PTR, 3))
        self.assertEqual([n + 1 for n, e in enumerate(old) if e.spr_check == vicii_sc.SPR_DISP], [58])
        for tab in (pal, ntsc, old):
            self.assertEqual(sum(e.fetch_c for e in tab), 40)
            self.assertTrue(all(e.xpos % 8 == 0 for e in tab))


class VicReadBackTests(unittest.TestCase):
    def test_fast_model_reads_back_like_the_full_chip(self):
        import random
        import sid2midi as S
        rnd = random.Random(7)
        fast = S.C64((), True)
        full = S.C64((), True)
        full.use_vicii_sc()
        skip = (0x11, 0x12, 0x13, 0x14, 0x19, 0x1A, 0x1E, 0x1F)   # raster, light pen, IRQ, collisions
        for m in (fast, full):
            m.time_base, m.cpu.cycles = 0, 100
        for r in range(0x40):
            if r in skip:
                continue
            v = rnd.randrange(256)
            for m in (fast, full):
                m.vic.write(r, v)
            self.assertEqual(fast.vic.read(r), full.vic.read(r), "register $D0%02X" % r)


class CiaPortBTests(unittest.TestCase):
    def test_port_b_output_callback_and_state_save(self):
        seen = []
        cia = Cia6526()
        cia.on_pb = lambda clk, byte: seen.append((clk, byte))
        cia.write(0x03, 0x10, 100)                  # DDRB: PB4 output (PRB = 0 -> low)
        cia.write(0x01, 0x10, 104)                  # PB4 high
        self.assertEqual(seen, [(100, 0xEF), (104, 0xFF)])
        state = cia.save()
        self.assertNotIn("on_pb_callable", state)
        cia.load(state)
        self.assertIsNotNone(cia.on_pb)


class VspIdleFetchTests(unittest.TestCase):
    """A bad line triggered in idle state fetches the idle byte from $38FF / $3807 (VICII/vsp-tester)."""

    def fetch_in_trigger_cycle(self, model):
        import sid2midi as S
        c = S.C64((), True)
        c.time_base, c.cpu.cycles = 0, 100
        c.use_vicii_sc(model)
        v = c.vic
        c.ram[0x3FFF], c.ram[0x38FF], c.ram[0x3807] = 0x11, 0x22, 0x33
        cycle_16 = 15                                  # first FETCH_G cycle (index)
        v.raster_cycle, v.raster_line = cycle_16 - 1, 0x32
        v.idle_state, v.bad_line, v.allow_bad_lines, v.ysmooth = 1, 0, 1, 0x32 & 7
        v.cycle(v.t)
        return v.last_read_phi1

    def test_nmos_6569_uses_38ff(self):
        self.assertEqual(self.fetch_in_trigger_cycle("6569"), 0x22)

    def test_hmos_8565_uses_3807(self):
        self.assertEqual(self.fetch_in_trigger_cycle("8565"), 0x33)

    def test_no_trigger_keeps_the_normal_idle_address(self):
        import sid2midi as S
        c = S.C64((), True)
        c.time_base, c.cpu.cycles = 0, 100
        c.use_vicii_sc("6569")
        v = c.vic
        c.ram[0x3FFF], c.ram[0x38FF] = 0x11, 0x22
        v.raster_cycle, v.raster_line = 14, 0x33
        v.idle_state, v.bad_line, v.allow_bad_lines, v.ysmooth = 1, 0, 1, 0x32 & 7
        v.cycle(v.t)
        self.assertEqual(v.last_read_phi1, 0x11)


if __name__ == "__main__":
    unittest.main()
