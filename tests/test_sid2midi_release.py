"""Release regression tests for sid2midi.py 2.x and the CPU fixes it relies on.

Every test encodes a defect found in the 1.x audit or in real-tune batch
validation, or a feature added for 2.0.  Tunes are synthesised in-test, so
the suite needs no copyrighted SID files.
"""
import io
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import sid2midi as S  # noqa: E402
from cpu6502 import CPU6502  # noqa: E402
from midicheck import MidiError, check_smf  # noqa: E402


# ------------------------------------------------------------------ helpers --
def build_sid(data, load=0x1000, init=None, play=None, songs=1, start=1, speed=0,
              flags=0x14, version=2, magic=b"PSID", sid2=0, sid3=0, name=b"Test tune"):
    hdr = bytearray(0x7C if version >= 2 else 0x76)
    hdr[:4] = magic
    struct.pack_into(">HHHHHHHI", hdr, 4, version, len(hdr), load,
                     load if init is None else init, 0 if play is None else play,
                     songs, start, speed)
    hdr[0x16:0x16 + len(name)] = name
    if version >= 2:
        struct.pack_into(">H", hdr, 0x76, flags)
        hdr[0x7A] = sid2
        hdr[0x7B] = sid3
    return bytes(hdr) + bytes(data)


class TempSids(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def sid(self, *args, filename="t.sid", **kw):
        p = self.dir / filename
        p.write_bytes(build_sid(*args, **kw))
        return str(p)


def tone_psid_code(freq=0x1D45, ctrl=0x11, base=0xD400):
    """init: volume, ADSR, frequency; play: gate the voice on (A4 = 440 Hz)."""
    lo, hi = base & 0xFF, base >> 8
    init = bytes([
        0xA9, 0x0F, 0x8D, 0x18 + lo, hi,       # LDA #$0F STA base+$18
        0xA9, 0x00, 0x8D, 0x05 + lo, hi,       # AD = $00
        0xA9, 0xF0, 0x8D, 0x06 + lo, hi,       # SR = $F0
        0xA9, freq & 0xFF, 0x8D, 0x00 + lo, hi,
        0xA9, freq >> 8, 0x8D, 0x01 + lo, hi,
        0x60,
    ])
    play = bytes([0xA9, ctrl, 0x8D, 0x04 + lo, hi, 0x60])
    return init, play


def track_events(trk):
    return sorted(trk.ev, key=lambda e: (e[0], e[1]))


# ---------------------------------------------------------------- C64 model --
class MemoryMapTests(unittest.TestCase):
    def test_c000_block_is_ram_with_basic_banked_in(self):
        c = S.C64()
        c.ram[0x01] = 0x37
        c.ram[0xC123] = 0x5A
        self.assertEqual(c.read(0xC123), 0x5A)          # 1.x: IndexError
        self.assertEqual(c.read(0xCFFF), c.ram[0xCFFF])  # RAM, with its power-on pattern

    def test_pla_banking(self):
        c = S.C64()
        c.ram[0xA000] = c.ram[0xE000] = c.ram[0xD000] = 0x11
        c.ram[0x01] = 0x37
        if S.BASIC:
            self.assertEqual(c.read(0xA000), S.BASIC[0])
        if S.KERNAL:
            self.assertEqual(c.read(0xE000), S.KERNAL[0])
        c.ram[0x01] = 0x35                               # BASIC+KERNAL out, I/O in
        self.assertEqual((c.read(0xA000), c.read(0xE000)), (0x11, 0x11))
        c.ram[0x01] = 0x34                               # all RAM
        self.assertEqual(c.read(0xD000), 0x11)

    def test_io_writes_do_not_leak_into_ram_under_io(self):
        c = S.C64()
        c.ram[0x01] = 0x37
        c.write(0xD418, 0x0F)
        self.assertEqual(c.chips[0].reg[0x18], 0x0F)
        c.ram[0x01] = 0x34
        self.assertEqual(c.read(0xD418), c.ram[0xD418])  # RAM under I/O, untouched by the SID write
        self.assertNotEqual(c.ram[0xD418], 0x0F)

    def test_colour_ram_is_not_the_ram_under_the_io_area(self):
        c = S.C64()                                      # VICE testprogs C64/bankio
        c.ram[0x01] = 0x37
        c.write(0xD800, 0x0A)                            # colour RAM (4 bits)
        c.ram[0x01] = 0x34                               # I/O banked out
        c.write(0xD800, 0x55)                            # goes to RAM under the I/O area
        self.assertEqual(c.ram[0xD800], 0x55)
        c.ram[0x01] = 0x37
        self.assertEqual(c.read(0xD800) & 0x0F, 0x0A)    # colour RAM kept its own value

    def test_power_on_ram_pattern(self):
        ram = S.power_on_ram()                           # VICE testprogs C64/raminitpattern
        self.assertEqual(list(ram[:14]), [0, 0, 255, 255, 255, 255, 0, 0, 0, 0, 255, 255, 255, 255])
        self.assertEqual(ram[0x4000], 0xFF ^ ram[0x0000])   # pattern inverts every 16 KB
        self.assertEqual(len(ram), 0x10000)

    def test_sid_mirrors_and_extra_sid_mapping(self):
        c = S.C64()
        c.write(0xD504, 0x11)                            # mirror of $D404
        self.assertEqual(c.chips[0].reg[0x04], 0x11)
        self.assertEqual(c.chips[0].trig, [1, 0, 0])
        c2 = S.C64(sid_bases=(0xD500,))
        c2.write(0xD504, 0x11)
        self.assertEqual((c2.chips[0].reg[4], c2.chips[1].reg[4]), (0, 0x11))

    def test_raster_register_follows_cycle_time(self):
        c = S.C64()
        c.begin_frame(100 * 63 + 5)
        self.assertEqual(c.read(0xD012), 100)
        c.begin_frame(300 * 63)
        self.assertEqual(c.read(0xD012), 300 & 0xFF)
        self.assertTrue(c.read(0xD011) & 0x80)

    def test_cia_timer_counts_down_and_schedules_underflows(self):
        c = S.C64()
        c.write(0xDC04, 0x10)
        c.write(0xDC05, 0x00)     # stopped: the counter loads two ticks later
        c.begin_frame(10)
        c.write(0xDC0E, 0x11)
        self.assertEqual(c.read(0xDC04), 0x10)
        c.begin_frame(14)
        self.assertEqual(c.read(0xDC04), 0x0F)             # counting starts two ticks after the write
        c.begin_frame(28)
        self.assertEqual(c.read(0xDC04), 0x01)
        c.begin_frame(29)
        self.assertEqual(c.read(0xDC04), 0x10)             # underflow reloads (period latch + 1)
        c.begin_frame(31)
        self.assertEqual(c.read(0xDC04), 0x0F)

    def test_bank_selection_follows_psid_spec(self):
        def sidfile(load, size, init):
            with tempfile.NamedTemporaryFile(suffix=".sid", delete=False) as fh:
                fh.write(build_sid(b"\x60" * size, load=load, init=init))
            try:
                return S.SidFile(fh.name)
            finally:
                os.unlink(fh.name)
        small = sidfile(0x1000, 16, 0x1000)
        self.assertEqual(small.bank_for(0x1000), 0x37)
        self.assertEqual(small.bank_for(0xB000), 0x36)
        self.assertEqual(small.bank_for(0xD400), 0x34)
        self.assertEqual(small.bank_for(0xE000), 0x35)
        big = sidfile(0x0E00, 0xF86C - 0x0E00, 0x0E00)     # quake.sid layout
        self.assertEqual(big.bank_for(0x0E00), 0x35)
        basic = sidfile(0x9000, 0x2000, 0x9000)
        self.assertEqual(basic.bank_for(0x9000), 0x36)


# -------------------------------------------------------------- SID header --
class HeaderTests(TempSids):
    def test_second_and_third_sid_addresses(self):
        s = S.SidFile(self.sid(b"\x60", version=3, sid2=0x50))
        self.assertEqual(s.extra_sids, [0xD500])          # 1.x: $D520
        s = S.SidFile(self.sid(b"\x60", version=3, sid2=0x42))
        self.assertEqual(s.sid2, 0xD420)
        s = S.SidFile(self.sid(b"\x60", version=4, sid2=0x50, sid3=0xE0))
        self.assertEqual(s.extra_sids, [0xD500, 0xDE00])
        s = S.SidFile(self.sid(b"\x60", version=4, sid2=0x50, sid3=0xDE))   # $DDE0: not a SID slot
        self.assertEqual(s.extra_sids, [0xD500])
        s = S.SidFile(self.sid(b"\x60", version=3, sid2=0x43))
        self.assertEqual(s.extra_sids, [])
        self.assertTrue(any("invalid" in w for w in s.warnings))

    def test_rejects_garbage(self):
        p = self.dir / "short.sid"
        p.write_bytes(b"PSID\x00\x02")
        with self.assertRaises(S.SidError):
            S.SidFile(str(p))
        p.write_bytes(b"XXXX" + bytes(0x80))
        with self.assertRaises(S.SidError):
            S.SidFile(str(p))
        with self.assertRaises(S.SidError):
            S.SidFile(self.sid(b""))
        with self.assertRaises(S.SidError):
            S.SidFile(self.sid(b"\x60", flags=0x01))       # MUS

    def test_oversized_data_is_truncated_not_grown(self):
        s = S.SidFile(self.sid(bytes(0x20000), load=0x0E00))
        self.assertEqual(s.end, 0x10000)
        self.assertTrue(any("truncated" in w for w in s.warnings))
        c = S.C64()
        c.load_sid(s)
        self.assertEqual(len(c.ram), 0x10000)             # 1.x: RAM grew to 275921 bytes

    def test_header_repairs(self):
        s = S.SidFile(self.sid(b"\x60", songs=0, start=7))
        self.assertEqual((s.songs, s.start), (1, 1))
        s = S.SidFile(self.sid(b"\x60", init=0))
        self.assertEqual(s.init, s.load)
        embedded = S.SidFile(self.sid(b"\x00\x20\x60", load=0))
        self.assertEqual((embedded.load, embedded.data), (0x2000, b"\x60"))

    def test_duplicate_load_address_fixup(self):
        data = b"\x00\x10" + b"\x4C\x06\x10" + b"\x60\x60"
        s = S.SidFile(self.sid(data, load=0x1000, init=0x1003, play=0x1000))
        self.assertEqual(s.data[:1], b"\x4C")
        raw = S.SidFile(self.sid(data, load=0x1000, init=0x1003, play=0x1000), fixups=False)
        self.assertEqual(raw.data[:2], b"\x00\x10")


# ----------------------------------------------------------------- playback --
class PlaybackTests(TempSids):
    def test_psid_tone_end_to_end(self):
        init, play = tone_psid_code()
        s = S.SidFile(self.sid(init + play, init=0x1000, play=0x1000 + len(init)))
        run = S.run_tune(s, 0, 2.0)
        self.assertIsNone(run.stop_reason)
        self.assertEqual(run.timing, "v-sync")
        self.assertEqual(len(run.frames), 101)             # calls at 0, 1 .. 100 frames < 2.0 s
        ppq, trks = S.convert(s, run.frames, run.fcyc, 125)
        out = self.dir / "tone.mid"
        S.write_smf(str(out), ppq, trks)
        stats = check_smf(out.read_bytes())
        self.assertEqual(stats["notes"], 1)
        notes = [b[1] for _, _, b in trks[1].ev if b[0] & 0xF0 == 0x90]
        self.assertEqual(notes, [69])

    def test_runaway_player_is_stopped_quickly(self):
        init = b"\x60"
        play = b"\x4C\x01\x10"                               # JMP * (never returns)
        s = S.SidFile(self.sid(init + play, init=0x1000, play=0x1001))
        t = time.time()
        run = S.run_tune(s, 0, 60.0, play_budget=2000, max_stalls=5)
        self.assertLess(time.time() - t, 5)
        self.assertEqual(len(run.frames), 5)
        self.assertIn("stalled", run.stop_reason)

    def test_brk_stub_returns_instead_of_hanging(self):
        # WastedYears.sid / Darren_Porter...sid: 7-byte stub, play hits BRK.
        data = bytes([0xA9, 0x00, 0x8D, 0x00, 0xD4, 0x60, 0x60])
        s = S.SidFile(self.sid(data, load=0x0E00, init=0x0E00, play=0x0E03))
        t = time.time()
        run = S.run_tune(s, 0, 10.0)
        self.assertLess(time.time() - t, 5)
        self.assertIsNone(run.stop_reason)
        self.assertEqual(run.stalled_calls, 0)
        self.assertTrue(any("BRK" in w for w in run.warnings))

    @unittest.skipUnless(S.KERNAL and S.BASIC,
                         "needs roms/kernal.bin and roms/basic.bin (IRQ handler exits through the KERNAL)")
    def test_rsid_cia_irq_rate_is_scheduled_from_timer(self):
        latch = 9827                                       # 2x PAL frame rate
        handler = 0x1030
        init = bytes([0x78,
                      0xA9, handler & 0xFF, 0x8D, 0x14, 0x03,
                      0xA9, handler >> 8, 0x8D, 0x15, 0x03,
                      0xA9, 0x7F, 0x8D, 0x0D, 0xDC,
                      0xA9, 0x81, 0x8D, 0x0D, 0xDC,
                      0xA9, latch & 0xFF, 0x8D, 0x04, 0xDC,
                      0xA9, latch >> 8, 0x8D, 0x05, 0xDC,
                      0xA9, 0x11, 0x8D, 0x0E, 0xDC,
                      0x58, 0x60])
        # INC $02 / LDA $02 / STA $D400 (SID registers are write-only) / LDA $DC0D / JMP $EA31
        code = init.ljust(0x30, b"\xEA") + bytes([0xE6, 0x02, 0xA5, 0x02, 0x8D, 0x00, 0xD4,
                                                  0xAD, 0x0D, 0xDC, 0x4C, 0x31, 0xEA])
        s = S.SidFile(self.sid(code, magic=b"RSID", play=0))
        run = S.run_tune(s, 0, 2.0)
        self.assertEqual(run.timing, "IRQ")
        expected = int(2.0 * S.PAL_CLOCK) // (latch + 1)
        self.assertLessEqual(abs(run.irq_count - expected), 2)
        self.assertIn((run.frames[-1][0][0][0][0] - run.irq_count) & 0xFF, (0, 0xFF))  # one INC per IRQ
        gaps = {b - a for a, b in zip(run.fcyc[2:-2], run.fcyc[3:-1])}
        self.assertEqual(gaps, {latch + 1})

    @unittest.skipUnless(S.KERNAL and S.BASIC,
                         "needs roms/kernal.bin and roms/basic.bin (IRQ handler exits through the KERNAL)")
    def test_rsid_raster_irq_with_cia_disabled(self):
        handler = 0x1030
        init = bytes([0x78,
                      0xA9, handler & 0xFF, 0x8D, 0x14, 0x03,
                      0xA9, handler >> 8, 0x8D, 0x15, 0x03,
                      0xA9, 0x7F, 0x8D, 0x0D, 0xDC,          # CIA IRQs off
                      0xA9, 0x1B, 0x8D, 0x11, 0xD0,
                      0xA9, 0x80, 0x8D, 0x12, 0xD0,          # raster line $80
                      0xA9, 0x01, 0x8D, 0x1A, 0xD0,          # raster IRQ on
                      0x58, 0x60])
        code = init.ljust(0x30, b"\xEA") + bytes([0xEE, 0x00, 0xD4, 0x0E, 0x19, 0xD0, 0x4C, 0x81, 0xEA])
        s = S.SidFile(self.sid(code, magic=b"RSID", play=0))
        run = S.run_tune(s, 0, 2.0)
        self.assertLessEqual(abs(run.irq_count - 100), 2)
        gaps = {b - a for a, b in zip(run.fcyc[2:-2], run.fcyc[3:-1])}
        self.assertEqual(gaps, {S.PAL_FRAME})
        # The raster match is at cycle 0 of line $80; the idle CPU recognises the
        # line two cycles later, at its next opcode fetch.
        self.assertEqual((run.t0 + run.fcyc[2]) % S.PAL_FRAME, 0x80 * 63 + 2)
        self.assertFalse(run.warnings)

    def test_multi_sid_voices_are_rendered(self):
        init, play = tone_psid_code(base=0xD420)
        s = S.SidFile(self.sid(init + play, version=3, sid2=0x42, play=0x1000 + len(init)))
        run = S.run_tune(s, 0, 1.0)
        ppq, trks = S.convert(s, run.frames, run.fcyc, 125)
        names = [t.ev[0][2][3:].decode() for t in trks[1:] if t.ev and t.ev[0][2][:2] == b"\xFF\x03"]
        self.assertIn("B V1", names)
        b_notes = [b for t in trks for _, _, b in t.ev if b[0] == 0x94]
        self.assertEqual(len(b_notes), 1)

    def test_all_songs_and_info_cli(self):
        init, play = tone_psid_code()
        path = self.sid(init + play, play=0x1000 + len(init), songs=2)
        out = self.dir / "x.mid"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(S.main([path, "--all-songs", "--seconds", "1", "-o", str(out)]), 0)
            self.assertEqual(S.main([path, "--song", "3", "--seconds", "1"]), 2)
            self.assertEqual(S.main([path, "--info"]), 0)
            self.assertEqual(S.main([str(self.dir / "missing.sid")]), 2)
        for n in (1, 2):
            check_smf((self.dir / ("x_song%02d.mid" % n)).read_bytes())


# ------------------------------------------------------------- conversion --
def frame(regs=None, trig=(0, 0, 0), digi=0):
    r = bytearray(0x19)
    for k, v in (regs or {}).items():
        r[k] = v
    return ((bytes(r), tuple(trig), (-1, -1, -1)),), digi


class FakeSid:
    name = author = release = "t"
    model = "MOS6581"
    pal = True
    rate = 50
    magic = b"PSID"
    version = 2
    songs = 1
    init = play = load = 0x1000
    clock = S.PAL_CLOCK
    frame = S.PAL_FRAME
    extra_sids = []


class ConversionTests(unittest.TestCase):
    def convert(self, frames, **kw):
        fcyc = [i * S.PAL_FRAME for i in range(len(frames) + 1)]
        ppq, trks = S.convert(FakeSid, frames, fcyc, 125, **kw)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "o.mid"
            S.write_smf(str(p), ppq, trks)
            check_smf(p.read_bytes())
        return trks

    def test_cutoff_cc_uses_full_range(self):
        trks = self.convert([frame({0x16: 0xFF, 0x15: 7}), frame({0x16: 0x80}), frame({0x16: 0x00})])
        flt = trks[-1]
        cc74 = [b[2] for _, _, b in track_events(flt) if b[0] == 0xB3 and b[1] == 74]
        self.assertEqual(cc74, [127, 64, 0])              # 1.x: 127, 127, 0

    def test_voice_switching_to_noise_releases_its_note(self):
        tone = {0x00: 0x45, 0x01: 0x1D, 0x04: 0x11, 0x06: 0xF0, 0x18: 0x0F}
        noise = dict(tone)
        noise[0x04] = 0x81
        frames = ([frame(tone, trig=(1, 0, 0))] + [frame(tone)] * 4
                  + [frame(noise, trig=(1, 0, 0))] + [frame(noise)] * 10)
        trks = self.convert(frames)
        v1, drums = trks[1], trks[4]
        off = [t for t, _, b in v1.ev if b[0] == 0x80]
        drum_on = [t for t, _, b in drums.ev if b[0] == 0x99]
        self.assertEqual(len(off), 1)
        self.assertEqual(len(drum_on), 1)
        self.assertLessEqual(off[0], drum_on[0])           # 1.x: tone held until song end

    def test_notes_outside_midi_range_are_dropped(self):
        low = {0x00: 0x01, 0x01: 0x00, 0x04: 0x11, 0x18: 0x0F}
        trks = self.convert([frame(low, trig=(1, 0, 0))] * 3)
        self.assertFalse([b for t in trks for _, _, b in t.ev if b[0] & 0xF0 == 0x90])

    def test_long_meta_text_uses_variable_length(self):
        t = S.Trk("x" * 300)
        blob = b"MThd" + struct.pack(">IHHH", 6, 1, 1, 960) + t.render()
        self.assertEqual(check_smf(blob)["tracks"], 1)

    def test_digi_cc_saturates(self):
        trks = self.convert([frame({0x18: 0x0F}, digi=300)])
        cc26 = [b[2] for _, _, b in trks[-1].ev if b[0] == 0xB3 and b[1] == 26]
        self.assertEqual(cc26, [127])                     # 1.x: 300 & 127 = 44


class LoopAndEnvelopeTests(unittest.TestCase):
    def test_loop_detection_keeps_intro(self):
        intro = [frame({0x00: i & 0xFF, 0x01: 0x80}) for i in range(100)]
        body = [frame({0x00: i & 0xFF, 0x01: i >> 8}) for i in range(300)]
        s_p = S.find_loop(intro + body * 3, 50)
        self.assertEqual(s_p, (100, 300))                 # 1.x: trimmed to 300 frames incl. intro

    def test_silence_is_not_a_loop(self):
        self.assertIsNone(S.find_loop([frame()] * 2000, 50))

    def test_resid_rate_table(self):
        self.assertEqual(S.Env.RATE, [9, 32, 63, 95, 149, 220, 267, 313, 392,
                                      977, 1954, 3126, 3907, 11720, 19532, 31251])

    def test_gate_pulse_inside_one_call_releases(self):
        regs = bytearray(0x19)
        regs[6] = 0xF0            # sustain F, release 0, gate now 0
        fcyc = [i * S.PAL_FRAME for i in range(11)]
        env = S.compute_env([0] * 10, lambda f: bytes(regs), lambda f: (1 if f == 0 else 0, 0, 0), fcyc)
        self.assertEqual(env[0][0], 255)
        self.assertLess(env[0][-1], 255)                  # 1.x: stuck at 255 forever

    def test_sustained_gate_holds_level(self):
        regs = bytearray(0x19)
        regs[4] = 0x11
        regs[6] = 0xA0
        fcyc = [i * S.PAL_FRAME for i in range(21)]
        env = S.compute_env([0] * 20, lambda f: bytes(regs), lambda f: (1 if f == 0 else 0, 0, 0), fcyc)
        self.assertEqual(env[0][-1], 0xAA)


# ---------------------------------------------------------------------- CPU --
class Mem:
    def __init__(self):
        self.m = bytearray(65536)
        self.writes = []

    def read(self, a):
        return self.m[a & 0xFFFF]

    def write(self, a, v):
        self.writes.append((a, v))
        self.m[a & 0xFFFF] = v & 0xFF

    def cpu(self):
        return CPU6502(self.read, self.write)


def _s8(x):
    return x - 256 if x & 0x80 else x


def clark_adc(a, b, c):
    """Bruce Clark, 'Decimal Mode' appendix: NMOS 6502 ADC, all inputs."""
    al = (a & 15) + (b & 15) + c
    if al >= 0x0A:
        al = ((al + 6) & 15) + 0x10
    r = (a & 0xF0) + (b & 0xF0) + al
    r2 = _s8(a & 0xF0) + _s8(b & 0xF0) + al
    if r >= 0xA0:
        r += 0x60
    return r & 0xFF, int(r >= 0x100), int(bool(r2 & 0x80)), int(not -128 <= r2 <= 127), int((a + b + c) & 0xFF == 0)


def clark_sbc(a, b, c):
    al = (a & 15) - (b & 15) + c - 1
    if al < 0:
        al = ((al - 6) & 15) - 0x10
    r = (a & 0xF0) - (b & 0xF0) + al
    if r < 0:
        r -= 0x60
    binary = a - b + c - 1
    signed = _s8(a) - _s8(b) + c - 1
    return r & 0xFF, int(binary >= 0), int(bool(binary & 0x80)), int(not -128 <= signed <= 127), int(binary & 0xFF == 0)


class CpuReleaseTests(unittest.TestCase):
    def step(self, prog, **reg):
        mem = Mem()
        c = mem.cpu()
        mem.m[0x1000:0x1000 + len(prog)] = bytes(prog)
        c.pc = 0x1000
        for k, v in reg.items():
            setattr(c, k, v)
        c.step()
        return c, mem

    def test_decimal_mode_matches_clark_for_every_input(self):
        c = Mem().cpu()
        c.D = 1
        for a in range(256):
            for b in range(256):
                for cin in (0, 1):
                    c.a, c.C = a, cin
                    c._adc(b)
                    self.assertEqual((c.a, c.C, c.N, c.V, c.Z), clark_adc(a, b, cin), ("ADC", a, b, cin))
                    c.a, c.C = a, cin
                    c._sbc(b)
                    self.assertEqual((c.a, c.C, c.N, c.V, c.Z), clark_sbc(a, b, cin), ("SBC", a, b, cin))

    def test_page_cross_penalties(self):
        c, _ = self.step([0x1E, 0xFF, 0x20], x=1)          # ASL abs,X: always 7
        self.assertEqual(c.cycles, 7)
        c, _ = self.step([0xBF, 0xFF, 0x20], y=1)          # LAX abs,Y: 4 + 1
        self.assertEqual(c.cycles, 5)
        c, _ = self.step([0x1C, 0xFF, 0x20], x=1)          # NOP abs,X: 4 + 1
        self.assertEqual(c.cycles, 5)
        c, _ = self.step([0x9D, 0xFF, 0x20], x=1)          # STA abs,X: always 5
        self.assertEqual(c.cycles, 5)

    def test_lxa_xaa_arr_decimal(self):
        c, _ = self.step([0xAB, 0x5F], a=0x01)             # ($01 | $EE) & $5F
        self.assertEqual((c.a, c.x), (0x4F, 0x4F))
        c, _ = self.step([0x8B, 0xFF], a=0x00, x=0x3C)
        self.assertEqual(c.a, 0x2C)
        c, _ = self.step([0x6B, 0xFF], a=0xFF, C=0, D=1)
        self.assertEqual((c.a, c.C, c.N, c.V), (0xD5, 1, 0, 0))

    def test_rmw_dummy_write(self):
        c, mem = self.step([0xEE, 0x00, 0x20])
        self.assertEqual(mem.writes, [(0x2000, 0x00), (0x2000, 0x01)])

    def test_brk_stop_and_budget_flags(self):
        mem = Mem()
        c = mem.cpu()
        c.brk_stop = True
        mem.m[0x1000] = 0x00
        c.call(0x1000)
        self.assertEqual((c.pc, c.brk_hit, c.timed_out), (0x0001, True, False))
        mem.m[0x1000:0x1003] = b"\x4C\x00\x10"
        c.call(0x1000, max_ins=100)
        self.assertTrue(c.timed_out)

    def test_base_cycle_table_matches_nmos_reference(self):
        ref = [  # NMOS 6502 base cycles; 0 = JAM (stored as 2 by the core)
            7,6,0,8,3,3,5,5,3,2,2,2,4,4,6,6, 2,5,0,8,4,4,6,6,2,4,2,7,4,4,7,7,
            6,6,0,8,3,3,5,5,4,2,2,2,4,4,6,6, 2,5,0,8,4,4,6,6,2,4,2,7,4,4,7,7,
            6,6,0,8,3,3,5,5,3,2,2,2,3,4,6,6, 2,5,0,8,4,4,6,6,2,4,2,7,4,4,7,7,
            6,6,0,8,3,3,5,5,4,2,2,2,5,4,6,6, 2,5,0,8,4,4,6,6,2,4,2,7,4,4,7,7,
            2,6,2,6,3,3,3,3,2,2,2,2,4,4,4,4, 2,6,0,6,4,4,4,4,2,5,2,5,5,5,5,5,
            2,6,2,6,3,3,3,3,2,2,2,2,4,4,4,4, 2,5,0,5,4,4,4,4,2,4,2,4,4,4,4,4,
            2,6,2,8,3,3,5,5,2,2,2,2,4,4,6,6, 2,5,0,8,4,4,6,6,2,4,2,7,4,4,7,7,
            2,6,2,8,3,3,5,5,2,2,2,2,4,4,6,6, 2,5,0,8,4,4,6,6,2,4,2,7,4,4,7,7]
        c = Mem().cpu()
        for op in range(256):
            self.assertEqual(c.CYC[op], ref[op] or 2, "$%02X" % op)
        mem = Mem()
        c = mem.cpu()
        mem.m[0x1000:0x1003] = b"\x4C\x00\x20"             # JMP abs = 3 (1.x: 6)
        c.pc = 0x1000
        c.step()
        self.assertEqual(c.cycles, 3)

    def test_cycle_table_is_per_instance(self):
        c = Mem().cpu()
        c.CYC[0xEA] = 99
        self.assertEqual(CPU6502.CYC[0xEA], 2)


class ExampleTests(unittest.TestCase):
    def test_bundled_example_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "example.mid")
            r = subprocess.run([sys.executable, str(ROOT / "sid2midi.py"), str(ROOT / "examples" / "simple_pulse.sid"),
                                "--seconds", "1", "--report", "-o", out], capture_output=True, check=False, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertGreaterEqual(check_smf(Path(out).read_bytes())["notes"], 1)

    def test_midicheck_rejects_broken_files(self):
        with self.assertRaises(MidiError):
            check_smf(b"MThd" + struct.pack(">IHHH", 6, 1, 1, 960) + b"MTrk\x00\x00\x00\x04\x00\x90\x3C\x40")


if __name__ == "__main__":
    unittest.main()
