"""SID noise writeback / OSC3 rules that deviate from libresidfp, fixed by real-chip reference data
(VICE testprogs SID/wb_testsuite and its 6581/8580 samplings, SID/wf12nsr)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import residfp as R  # noqa: E402


class WritebackTableTests(unittest.TestCase):
    def test_6581_rules(self):
        self.assertFalse(R.do_writeback(0xF, 0x8, True))          # both measured 6581 chips: plain shift
        for old in (0xD, 0xE, 0xF):
            self.assertTrue(R.do_writeback(old, 0xC, True))       # partial writeback into noise+pulse
        self.assertTrue(R.do_writeback(0xD, 0xE, True))

    def test_8580_rules(self):
        for new in (0x9, 0xE, 0xF):
            self.assertTrue(R.do_writeback(0xC, new, False))
        self.assertFalse(R.do_writeback(0xC, 0x8, False))

    def test_unchanged_libresidfp_cases(self):
        self.assertFalse(R.do_writeback(0x8, 0xC, True))          # no combined waveform before
        self.assertFalse(R.do_writeback(0x9, 0x4, True))          # new waveform without noise


def generator(is6581, waveform):
    g = R.WaveformGenerator()
    g.is6581 = is6581
    g.prevVoice = g.nextVoice = g
    g.waveform = waveform
    g.setWave()
    g.pulldown = None
    g.no_noise = 0x000 if waveform & 0x8 else 0xFFF
    g.no_pulse = 0x000 if waveform & 0x4 else 0xFFF
    g.pulse_output = 0xFFF
    g.accumulator = 0
    g.ring_msb_mask = 0
    g.shift_register = 0x7FFFFF
    g.set_noise_output()                                          # all 8 noise bits set: $FF0
    return g


class NoisePulseOutputTests(unittest.TestCase):
    def test_8580_osc3_read_pulls_one_more_bit_than_the_writeback_value(self):
        g = generator(False, 0xC)
        g.output()
        self.assertEqual(g.waveform_output, 0xFC0)                # reSID noise_pulse8580 (register writeback)
        self.assertEqual(g.osc3, 0xF80)                           # OSC3 read (SID/wf12nsr-8580)

    def test_6581_writeback_into_noise_pulse_clears_three_low_noise_bits(self):
        g = generator(True, 0xF)
        g.shift_latch = g.shift_register
        g.waveform_output = 0x000                                 # held combined waveform output
        g.waveform = 0xC
        g.shift_phase2(0xF, 0xC)
        latch_taps_cleared = [t for t in (22, 20, 17) if not (g.shift_latch >> t) & 1]
        self.assertEqual(latch_taps_cleared, [22, 20, 17])
        self.assertTrue(all((g.shift_latch >> t) & 1 for t in (13, 11, 8, 4, 2)))


if __name__ == "__main__":
    unittest.main()
