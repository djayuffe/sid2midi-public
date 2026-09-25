"""Machine-level tests for sid2midi 3.0.

CIA 6526 bus behaviour and NMI, continuous interrupt-driven playback, the
cached KERNAL cold start, C64 BASIC RSIDs, SID read-back and bus value through
the reSIDfp port, header repairs.  The reSID 0.16 envelope/oscillator estimates
used for MIDI velocities are still checked against independent per-cycle
transcriptions of reSID's single-cycle clock().
"""
import random
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sid2midi as S  # noqa: E402

HAVE_ROMS = bool(S.KERNAL and S.BASIC)


# ---------------------------------------------------------------- assembler --
def w16(a):
    return bytes([a & 0xFF, a >> 8])


def POKE(a, v):
    return bytes([0xA9, v]) + b"\x8D" + w16(a)


def STA(a):
    return b"\x8D" + w16(a)


def LDA(a):
    return b"\xAD" + w16(a)


def INC(a):
    return b"\xEE" + w16(a)


def JMP(a):
    return b"\x4C" + w16(a)


SEI, CLI, RTS, RTI, PHA, PLA, NOP = b"\x78", b"\x58", b"\x60", b"\x40", b"\x48", b"\x68", b"\xEA"


def build_sid(data, load=0x1000, init=None, play=0, songs=1, flags=0x14, magic=b"RSID"):
    hdr = bytearray(0x7C)
    hdr[:4] = magic
    struct.pack_into(">HHHHHHHI", hdr, 4, 2, 0x7C, load, load if init is None else init, play, songs, 1, 0)
    hdr[0x16:0x1A] = b"Test"
    struct.pack_into(">H", hdr, 0x76, flags)
    return bytes(hdr) + bytes(data)


def basic_program(lines, start=0x0801):
    out = bytearray()
    addr = start
    for number, body in lines:
        line = struct.pack("<H", number) + body + b"\0"
        addr += 2 + len(line)
        out += struct.pack("<H", addr) + line
    return bytes(out + b"\0\0")


class TempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def sidfile(self, *args, **kw):
        p = self.dir / "t.sid"
        p.write_bytes(build_sid(*args, **kw))
        return S.SidFile(str(p))


# ------------------------------------------------------------------ CIA 6526 --
class CiaTimerTests(unittest.TestCase):
    """Bus-level 6526 behaviour (old CIA, cia_vice.py).  The full timer matrix is
    covered by test_cia6526_lorenz; these pin the behaviour on the machine's
    register map."""

    def setUp(self):
        self.c = S.C64()

    def w(self, t, r, v):
        self.c.begin_frame(t)
        self.c.write(0xDC00 + r, v)

    def rd(self, t, r):
        self.c.begin_frame(t)
        return self.c.read(0xDC00 + r)

    def test_start_delay_and_period(self):
        self.w(0, 0x04, 9)
        self.w(0, 0x05, 0)            # stopped: a latch high write loads the counter
        self.w(10, 0x0E, 0x11)                             # force load + start
        values = [self.rd(t, 0x04) for t in range(10, 25)]
        self.assertEqual(values, [9, 9, 9, 9, 8, 7, 6, 5, 4, 3, 2, 1, 9, 9, 8])

    def test_old_cia_irq_follows_flag_one_tick_later(self):
        self.w(0, 0x04, 9)
        self.w(0, 0x05, 0)
        self.w(0, 0x0D, 0x81)
        self.w(10, 0x0E, 0x11)                             # underflow on tick 21
        self.assertEqual(self.rd(23, 0x0D), 0x81)
        self.assertFalse(self.c.irq_low_at(22))
        self.assertTrue(self.c.irq_low_at(23))
        self.assertFalse(self.c.irq_low_at(24))           # acknowledged by the read at 23

    def test_icr_read_on_flag_cancels_the_interrupt(self):
        self.w(0, 0x04, 9)
        self.w(0, 0x05, 0)
        self.w(0, 0x0D, 0x81)
        self.w(10, 0x0E, 0x11)
        self.assertEqual(self.rd(22, 0x0D), 0x01)
        self.assertEqual(self.rd(26, 0x0D), 0x00)          # next instruction's read
        self.c.cia1.sync(40)
        self.assertFalse(self.c.irq_low_at(23))

    def test_back_to_back_icr_reads_keep_bit_7(self):
        # INC $DC0D,X: the dummy read clears the timer flag, but the old 6526
        # still reports the pending bit 7 on the next cycle's read (dd0dtest).
        self.w(0, 0x04, 9)
        self.w(0, 0x05, 0)
        self.w(0, 0x0D, 0x81)
        self.w(10, 0x0E, 0x11)
        self.assertEqual(self.rd(22, 0x0D), 0x01)
        self.assertEqual(self.rd(23, 0x0D), 0x80)
        self.c.cia1.sync(40)
        self.assertFalse(self.c.irq_low_at(23))

    def test_one_shot_stops_at_underflow(self):
        self.w(0, 0x04, 2)
        self.w(0, 0x05, 0)
        self.w(10, 0x0E, 0x09)                             # start, one-shot, no force load
        self.assertEqual(self.rd(13, 0x0E), 0x09)
        self.assertEqual(self.rd(14, 0x0E), 0x08)
        self.assertEqual(self.rd(100, 0x04), 2)

    def test_cnt_to_phi2_switch_takes_two_ticks(self):
        self.w(0, 0x04, 0xFF)
        self.w(0, 0x05, 0xFF)
        self.w(10, 0x0E, 0x21)                             # started, counting CNT (never pulsed)
        self.w(100, 0x0E, 0x01)
        self.assertEqual(self.rd(104, 0x04), 253)
        self.w(108, 0x0E, 0x21)
        self.assertEqual(self.rd(112, 0x04), 247)

    def test_timer_b_counts_timer_a_underflows(self):
        self.w(0, 0x04, 9)
        self.w(0, 0x05, 0)
        self.w(0, 0x06, 5)
        self.w(0, 0x07, 0)
        self.w(10, 0x0F, 0x51)                             # timer B: force load, start, TA underflows
        self.w(10, 0x0E, 0x11)                             # timer A underflows on ticks 21, 31, ...
        # The underflow takes phi2's place in the two-stage count pipeline:
        # timer B decrements two ticks later (Lorenz cia1tab, cmp-b-counts-a).
        self.assertEqual(self.rd(23, 0x06), 5)
        self.assertEqual(self.rd(24, 0x06), 4)
        self.assertEqual(self.rd(34, 0x06), 3)

    def test_timer_b_cnt_mode_never_counts(self):
        self.w(0, 0x06, 3)
        self.w(0, 0x07, 0)
        self.w(10, 0x0F, 0x31)
        self.assertEqual((self.rd(1000, 0x06), self.rd(1000, 0x0D)), (3, 0))

    def test_pb6_toggle_output(self):
        self.w(0, 0x04, 9)
        self.w(0, 0x05, 0)
        self.w(10, 0x0E, 0x17)                             # start, PB6 on, toggle, force load
        self.assertTrue(self.rd(11, 0x01) & 0x40)          # starting sets the toggle high
        self.assertFalse(self.rd(22, 0x01) & 0x40)         # underflow on tick 21 toggles
        self.assertTrue(self.rd(32, 0x01) & 0x40)

    def test_ports_read_outputs_and_pulled_up_inputs(self):
        self.w(0, 0x02, 0xF0)
        self.w(0, 0x00, 0x5A)
        self.assertEqual(self.rd(0, 0x00), 0x5F)

    def test_vic_raster_flag_latches_without_mask(self):
        c = self.c
        c.write(0xD012, 0x40)
        c.begin_frame(0x41 * 63)
        self.assertEqual(c.read(0xD019) & 0x81, 0x01)
        c.write(0xD019, 0x01)
        self.assertEqual(c.read(0xD019) & 0x01, 0)


# --------------------------------------------------- reSID envelope reference --
class RefEnvelope:
    RATE = (9, 32, 63, 95, 149, 220, 267, 313, 392, 977, 1954, 3126, 3907, 11720, 19532, 31251)

    def __init__(self):
        self.counter = 0
        self.a = self.d = self.s = self.r = 0
        self.gate = 0
        self.rc = 0
        self.period = self.RATE[0]
        self.exp = 0
        self.exp_period = 1
        self.state = 2
        self.hold = True

    def control(self, v):
        g = v & 1
        if g and not self.gate:
            self.state = 0
            self.period = self.RATE[self.a]
            self.hold = False
        elif self.gate and not g:
            self.state = 2
            self.period = self.RATE[self.r]
        self.gate = g

    def ad(self, v):
        self.a, self.d = v >> 4, v & 15
        if self.state == 0:
            self.period = self.RATE[self.a]
        elif self.state == 1:
            self.period = self.RATE[self.d]

    def sr(self, v):
        self.s, self.r = v >> 4, v & 15
        if self.state == 2:
            self.period = self.RATE[self.r]

    def cycle(self):
        self.rc += 1
        if self.rc & 0x8000:
            self.rc = (self.rc + 1) & 0x7FFF
        if self.rc != self.period:
            return
        self.rc = 0
        if self.state != 0:
            self.exp += 1
            if self.exp != self.exp_period:
                return
        self.exp = 0
        if self.hold:
            return
        if self.state == 0:
            self.counter = (self.counter + 1) & 0xFF
            if self.counter == 0xFF:
                self.state = 1
                self.period = self.RATE[self.d]
        elif self.state == 1:
            if self.counter != self.s * 0x11:
                self.counter -= 1
        else:
            self.counter = (self.counter - 1) & 0xFF
        self.exp_period = {0xFF: 1, 0x5D: 2, 0x36: 4, 0x1A: 8, 0x0E: 16, 0x06: 30, 0x00: 1}.get(
            self.counter, self.exp_period)
        if self.counter == 0:
            self.hold = True


class RefWave:
    def __init__(self):
        self.freq = self.acc = 0
        self.lfsr = 0x7FFFF8
        self.test = self.sync = self.ring = 0
        self.msb_rising = False

    def control(self, v):
        self.ring, self.sync = v & 4, v & 2
        if v & 8:
            self.acc = 0
            self.lfsr = 0
        elif self.test:
            self.lfsr = 0x7FFFF8
        self.test = v & 8


def ref_wave_cycle(ws):
    for w in ws:
        if w.test:
            w.msb_rising = False
            continue
        prev = w.acc
        w.acc = (prev + w.freq) & 0xFFFFFF
        w.msb_rising = (not (prev & 0x800000)) and bool(w.acc & 0x800000)
        if (not (prev & 0x080000)) and (w.acc & 0x080000):
            w.lfsr = ((w.lfsr << 1) & 0x7FFFFF) | (((w.lfsr >> 22) ^ (w.lfsr >> 17)) & 1)
    for i, w in enumerate(ws):
        dest, src = ws[(i + 1) % 3], ws[(i + 2) % 3]
        if w.msb_rising and dest.sync and not (w.sync and src.msb_rising):
            dest.acc = 0


class SidModelTests(unittest.TestCase):
    def assertEnvEqual(self, fast, ref, note):
        self.assertEqual((fast.counter, fast.state, fast.rate_counter, fast.exp_counter, fast.hold_zero),
                         (ref.counter, ref.state, ref.rc, ref.exp, ref.hold), note)

    def test_envelope_matches_per_cycle_reference(self):
        rng = random.Random(6581)
        for trial in range(3):
            fast, ref = S.SidEnvelope(), RefEnvelope()
            for i in range(160):
                op, v = rng.random(), rng.randrange(256)
                if op < 0.4:
                    fast.write_control(v & 1)
                    ref.control(v & 1)
                elif op < 0.7:
                    fast.write_ad(v)
                    ref.ad(v)
                else:
                    fast.write_sr(v)
                    ref.sr(v)
                dt = rng.choice((rng.randrange(40), rng.randrange(1500), rng.randrange(9000)))
                fast.clock(dt)
                for _ in range(dt):
                    ref.cycle()
                self.assertEnvEqual(fast, ref, (trial, i, dt))

    def test_full_release_through_exponential_steps(self):
        fast, ref = S.SidEnvelope(), RefEnvelope()
        for e in (fast, ref):
            (e.write_ad if e is fast else e.ad)(0x00)
            (e.write_sr if e is fast else e.sr)(0xF0)
            (e.write_control if e is fast else e.control)(1)
        fast.clock(3000)
        for _ in range(3000):
            ref.cycle()
        self.assertEqual(fast.counter, 0xFF)
        fast.write_control(0)
        ref.control(0)
        for chunk in (7, 1000, 25000, 40000):
            fast.clock(chunk)
            for _ in range(chunk):
                ref.cycle()
            self.assertEnvEqual(fast, ref, chunk)
        self.assertEqual(fast.counter, 0)

    def test_adsr_delay_bug(self):
        fast, ref = S.SidEnvelope(), RefEnvelope()
        fast.write_sr(0x0F)
        ref.sr(0x0F)                  # release rate 31251
        fast.clock(20000)
        for _ in range(20000):
            ref.cycle()
        fast.write_sr(0x00)
        ref.sr(0x00)                  # period 9 below the counter: wraps via $7FFF
        fast.clock(15000)
        for _ in range(15000):
            ref.cycle()
        self.assertEnvEqual(fast, ref, "delay bug")
        self.assertEqual(fast.counter, 0)                  # still waiting for the counter to wrap

    def test_oscillators_match_per_cycle_reference(self):
        rng = random.Random(8580)
        fast = [S.SidWave(track_noise=True) for _ in range(3)]
        for i, w in enumerate(fast):
            w.src, w.dest = fast[(i + 2) % 3], fast[(i + 1) % 3]
        ref = [RefWave() for _ in range(3)]
        for step in range(150):
            v = rng.randrange(3)
            if rng.random() < 0.5:
                f = rng.choice((rng.randrange(0x10000), rng.randrange(0x400), rng.randrange(0xC000, 0x10000)))
                fast[v].freq = ref[v].freq = f
            else:
                ctl = rng.randrange(256)
                if rng.random() < 0.85:
                    ctl &= 0xF7
                fast[v].write_control(ctl)
                ref[v].control(ctl)
            dt = rng.choice((rng.randrange(20), rng.randrange(3000)))
            S.clock_oscillators(fast, dt)
            for _ in range(dt):
                ref_wave_cycle(ref)
            self.assertEqual([(w.acc, w.lfsr) for w in fast], [(w.acc, w.lfsr) for w in ref], (step, dt))

    def test_waveform_outputs(self):
        w = S.SidWave()
        w.src = w.dest = S.SidWave()
        w.acc = 0xABCDEF
        w.waveform = 2
        self.assertEqual(w.output(), 0xABC)
        w.waveform = 1
        self.assertEqual(w.output(), (~0xABCDEF >> 11) & 0xFFF)
        w.waveform, w.pw = 4, 0xABC
        self.assertEqual(w.output(), 0xFFF)
        w.pw = 0xABD
        self.assertEqual(w.output(), 0)
        w.waveform, w.lfsr = 8, 0x7FFFFF
        self.assertEqual(w.output(), 0xFF0)
        w.waveform = 9
        self.assertEqual(w.output(), 0)


# ------------------------------------------------------- machine end-to-end --
class ReadbackTests(TempDir):
    def test_env3_and_osc3_read_back_in_a_called_player(self):
        init = (POKE(0xD40E, 0x00) + POKE(0xD40F, 0x10) + POKE(0xD413, 0x00) + POKE(0xD414, 0xF0)
                + POKE(0xD412, 0x21) + RTS)
        play = LDA(0xD41C) + STA(0xD400) + LDA(0xD41B) + STA(0xD401) + RTS
        s = self.sidfile(init + play, magic=b"PSID", play=0x1000 + len(init))
        run = S.run_tune(s, 0, 0.2)
        regs = [f[0][0][0] for f in run.frames]
        self.assertTrue(all(r[0] == 0xFF for r in regs[1:]))        # ENV3 attack done, sustain F
        self.assertEqual(regs[1][0x12 + 0] & 0xF0, 0x20)
        self.assertIn((regs[3][1] - regs[2][1]) & 0xFF, (204, 205))  # OSC3 saw: $1000 * 19656 cycles >> 16
        self.assertEqual(run.frames[5][0][0][3][2], 0xFF)            # captured envelope level

    def test_bus_value_decays(self):
        c = S.C64()
        c.write(0xD400, 0x5A)
        self.assertEqual(c.read(0xD400), 0x5A)              # a write-only read also halves the lifetime
        c.begin_frame(S.residfp.BUS_TTL_6581 // 2)
        self.assertEqual(c.read(0xD400), 0)
        self.assertEqual(c.read(0xD419), 0xFF)

    def test_bus_value_reads_match_residfp_without_building_it(self):
        rng = random.Random(3)
        for model, sid_model in (("6581", S.residfp.MOS6581), ("8580", S.residfp.MOS8580)):
            c = S.C64()
            c.set_sid_model(model)
            ref = S.residfp.SID(sid_model)
            t = 0
            for _ in range(400):
                t += rng.choice((1, 7, 300, 0x0E00, 0x1D00, 0x40000))
                c.begin_frame(t)
                r = rng.choice((0x00, 0x04, 0x12, 0x18, 0x19, 0x1A, 0x1F))
                if rng.random() < 0.4:
                    v = rng.randrange(256)
                    ref.ageBusValue(t - getattr(ref, "_t", 0))
                    ref._t = t
                    ref.busValue, ref.busValueTtl = v, ref.modelTTL
                    c.write(0xD400 + r, v)
                else:
                    ref.ageBusValue(t - getattr(ref, "_t", 0))
                    ref._t = t
                    if r in (0x19, 0x1A):
                        ref.busValue, ref.busValueTtl = 0xFF, ref.modelTTL
                    else:
                        ref.busValueTtl = int(ref.busValueTtl / 2)
                    self.assertEqual(c.read(0xD400 + r), ref.busValue, (model, t, hex(r)))
            self.assertIsNone(c.chips[0].fp)                    # no OSC3/ENV3 read: no full chip

    def test_bus_state_carries_into_the_full_chip(self):
        c = S.C64()
        c.write(0xD400, 0x5A)
        c.begin_frame(100)
        self.assertEqual(c.read(0xD404), 0x5A)                  # halves the lifetime
        c.begin_frame(200)
        c.read(0xD41B)                                           # builds the reSIDfp chip
        fp = c.chips[0].fp
        self.assertEqual((fp.busValue, fp.busValueTtl),
                         (fp.wave[2].readOSC(), S.residfp.BUS_TTL_6581))   # an OSC3 read drives the bus
        c.begin_frame(300)
        self.assertEqual(c.read(0xD404), fp.busValue)


class HeaderRepairTests(TempDir):
    def sid(self, blob):
        p = self.dir / "t.sid"
        p.write_bytes(blob)
        return p

    def test_byte_swapped_embedded_load_address(self):
        code = POKE(0xD418, 0x0F) + RTS
        blob = build_sid(b"\x10\x00" + code, load=0, init=0x1000, play=0, magic=b"PSID")
        s = S.SidFile(str(self.sid(blob)))
        self.assertEqual(s.load, 0x1000)
        self.assertTrue(any("byte-swapped" in w for w in s.warnings))
        s = S.SidFile(str(self.sid(blob)), fixups=False)
        self.assertEqual(s.load, 0x0010)

    def test_init_on_empty_data_is_reported(self):
        blob = build_sid(bytes(64) + RTS, load=0x1000, init=0x1000, magic=b"PSID")
        s = S.SidFile(str(self.sid(blob)))
        self.assertTrue(any("empty ($00) data" in w for w in s.warnings))

    def test_chip_models_from_header(self):
        blob = build_sid(RTS, magic=b"PSID", flags=0x04 | 0x20)             # SID #1: 8580
        s = S.SidFile(str(self.sid(blob)))
        self.assertEqual(s.chip_models, ["8580", "8580", "8580"])
        blob = build_sid(RTS, magic=b"PSID", flags=0x14)
        self.assertEqual(S.SidFile(str(self.sid(blob))).chip_models, ["6581", "6581", "6581"])


@unittest.skipUnless(HAVE_ROMS, "needs roms/kernal.bin and roms/basic.bin")
class ContinuousMachineTests(TempDir):
    def test_cold_start_reaches_ready(self):
        for pal, flag, latch in ((True, 1, 0x4025), (False, 0, 0x4295)):
            c = S.C64(pal=pal)
            c.restore_state(S.cold_reset_state(pal))
            self.assertEqual(c.cpu.pc, S.READY_LOOP)
            self.assertEqual(c.ram[0x02A6], flag)
            self.assertEqual(c.cia1.ta.latch, latch)
            self.assertEqual(bytes(c.ram[0x0314:0x0316]), b"\x31\xEA")
            screen = bytes(c.ram[0x0400:0x07E8])
            self.assertIn(b"\x2A\x2A\x2A\x2A\x20\x03\x0F\x0D\x0D\x0F\x04\x0F\x12\x05", screen)
            self.assertIn(b"\x12\x05\x01\x04\x19\x2E", screen)

    def nmi_tune(self, ack=True):
        nmi = 0x1040
        init = (SEI + POKE(0x0318, nmi & 0xFF) + POKE(0x0319, nmi >> 8) + POKE(0xDD0D, 0x7F)
                + POKE(0xDD04, 122) + POKE(0xDD05, 0) + POKE(0xDD0D, 0x81) + POKE(0xDD0E, 0x11) + CLI + RTS)
        handler = PHA + LDA(0x0002) + STA(0xD418) + INC(0x0002) + (LDA(0xDD0D) if ack else b"") + PLA + RTI
        return init.ljust(0x40, NOP) + handler

    def test_nmi_digi_through_kernal_vector(self):
        run = S.run_tune(self.sidfile(self.nmi_tune()), 0, 1.0)
        expected = int(S.PAL_CLOCK) // 123
        self.assertLessEqual(abs(run.nmi_count - expected), 2)
        self.assertLessEqual(abs(sum(f[1] for f in run.frames) - run.nmi_count), 1)
        self.assertTrue(run.init_returned)
        self.assertIsNone(run.stop_reason)

    def test_nmi_is_edge_triggered_until_icr_is_read(self):
        run = S.run_tune(self.sidfile(self.nmi_tune(ack=False)), 0, 0.5)
        self.assertEqual(run.nmi_count, 1)

    def test_nmi_through_hardware_vector_with_kernal_banked_out(self):
        nmi, irq = 0x1070, 0x1080
        init = (SEI + POKE(0xDC0D, 0x7F) + LDA(0xDC0D) + POKE(0x0001, 0x35)
                + POKE(0xFFFA, nmi & 0xFF) + POKE(0xFFFB, nmi >> 8)
                + POKE(0xFFFE, irq & 0xFF) + POKE(0xFFFF, irq >> 8)
                + POKE(0xDD0D, 0x7F) + POKE(0xDD04, 0xFF) + POKE(0xDD05, 0x03)
                + POKE(0xDD0D, 0x81) + POKE(0xDD0E, 0x11) + CLI + RTS)
        code = init.ljust(0x70, NOP) + (PHA + INC(0xD401) + LDA(0xDD0D) + PLA + RTI).ljust(0x10, NOP) + RTI
        run = S.run_tune(self.sidfile(code), 0, 1.0)
        expected = int(S.PAL_CLOCK) // 1024
        self.assertLessEqual(abs(run.nmi_count - expected), 2)
        self.assertEqual(run.irq_count, 0)
        self.assertIn((run.frames[-1][0][0][0][1] - run.nmi_count) & 0xFF, (0, 0xFF))

    def test_timer_b_nmi(self):
        nmi = 0x1040
        init = (SEI + POKE(0x0318, nmi & 0xFF) + POKE(0x0319, nmi >> 8) + POKE(0xDD0D, 0x7F)
                + POKE(0xDD06, 0xE7) + POKE(0xDD07, 0x03) + POKE(0xDD0D, 0x82) + POKE(0xDD0F, 0x11) + CLI + RTS)
        handler = PHA + INC(0xD402) + LDA(0xDD0D) + PLA + RTI
        run = S.run_tune(self.sidfile(init.ljust(0x40, NOP) + handler), 0, 1.0)
        self.assertLessEqual(abs(run.nmi_count - int(S.PAL_CLOCK) // 1000), 2)

    def test_irq_interrupts_a_running_main_loop(self):
        irq = 0x1040
        init = SEI + POKE(0x0314, irq & 0xFF) + POKE(0x0315, irq >> 8) + CLI
        main = INC(0xD401) + JMP(0x1000 + len(init))
        code = (init + main).ljust(0x40, NOP) + INC(0x0002) + LDA(0x0002) + STA(0xD400) + JMP(0xEA31)
        run = S.run_tune(self.sidfile(code), 0, 1.0)
        self.assertFalse(run.init_returned)
        self.assertLessEqual(abs(run.irq_count - 60), 2)
        self.assertIn((run.frames[-1][0][0][0][0] - run.irq_count) & 0xFF, (0, 0xFF))
        self.assertGreater(len({f[0][0][0][1] for f in run.frames}), 20)   # main loop kept running

    def test_busy_wait_loop_skips_time(self):
        init = SEI + CLI
        code = init + JMP(0x1000 + len(init))
        t = time.time()
        run = S.run_tune(self.sidfile(code), 0, 30.0)
        self.assertLess(time.time() - t, 15)
        self.assertLessEqual(abs(run.irq_count - 30 * 60), 3)

    def test_rsid_cia_timer_irq_rate(self):
        latch, handler = 9827, 0x1030
        init = (SEI + POKE(0x0314, handler & 0xFF) + POKE(0x0315, handler >> 8)
                + POKE(0xDC0D, 0x7F) + POKE(0xDC0D, 0x81) + POKE(0xDC04, latch & 0xFF)
                + POKE(0xDC05, latch >> 8) + POKE(0xDC0E, 0x11) + CLI + RTS)
        code = init.ljust(0x30, NOP) + INC(0xD400) + LDA(0xDC0D) + JMP(0xEA31)
        run = S.run_tune(self.sidfile(code), 0, 2.0)
        self.assertEqual(run.timing, "IRQ")
        self.assertLessEqual(abs(run.irq_count - int(2.0 * S.PAL_CLOCK) // (latch + 1)), 2)
        gaps = {b - a for a, b in zip(run.fcyc[2:-2], run.fcyc[3:-1])}
        self.assertEqual(gaps, {latch + 1})

    def test_basic_rsid_runs_program_with_song_number(self):
        prog = basic_program([
            (10, b"\x97" + b"54296,15:" + b"\x97" + b"54273,\xC2(780):" + b"\x97" + b"54276,17"),
            (20, b"\x89" + b"20"),
        ])
        s = self.sidfile(prog, load=0x0801, init=0, flags=0x16, songs=3)
        self.assertTrue(s.basic)
        run = S.run_tune(s, 2, 2.0)
        self.assertEqual(run.timing, "BASIC")
        last = run.frames[-1][0][0][0]
        self.assertEqual((last[0x18], last[0x01], last[0x04]), (15, 2, 17))

    def test_psid_with_play_zero_runs_continuously(self):
        handler = 0x1030
        init = SEI + POKE(0x0314, handler & 0xFF) + POKE(0x0315, handler >> 8) + CLI + RTS
        code = init.ljust(0x30, NOP) + INC(0xD400) + JMP(0xEA31)
        run = S.run_tune(self.sidfile(code, magic=b"PSID"), 0, 1.0)
        self.assertTrue(run.irq)
        self.assertLessEqual(abs(run.irq_count - 60), 2)


if __name__ == "__main__":
    unittest.main()
