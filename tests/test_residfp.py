"""residfp.py against libresidfp's own unit tests (tests/TestEnvelopeGenerator.cpp,
tests/TestWaveformGenerator.cpp at commit b8bdc00), transliterated one to one.

This file is part of the reSIDfp port and is distributed under the GNU General
Public License, version 2 or later.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import residfp as R  # noqa: E402


class EnvelopeGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.generator = R.EnvelopeGenerator()
        self.generator.reset()
        self.generator.envelope_counter = 0

    def test_adsr_delay_bug(self):
        g = self.generator
        g.writeATTACK_DECAY(0x70)
        g.writeCONTROL_REG(0x01)
        for _ in range(200):
            g.clock()
        self.assertEqual(g.readENV(), 1)
        g.writeATTACK_DECAY(0x20)
        for _ in range(200):
            g.clock()
        self.assertEqual(g.readENV(), 1)

    def test_flip_ff_to_00(self):
        g = self.generator
        g.writeATTACK_DECAY(0x77)
        g.writeSUSTAIN_RELEASE(0x77)
        g.writeCONTROL_REG(0x01)
        while True:
            g.clock()
            if g.readENV() == 0xFF:
                break
        g.writeCONTROL_REG(0x00)
        g.clock(); g.clock(); g.clock()
        g.writeCONTROL_REG(0x01)
        for _ in range(315):
            g.clock()
        self.assertEqual(g.readENV(), 0)

    def test_flip_00_to_ff(self):
        g = self.generator
        g.counter_enabled = False
        g.writeATTACK_DECAY(0x77)
        g.writeSUSTAIN_RELEASE(0x77)
        g.clock()
        self.assertEqual(g.readENV(), 0)
        g.writeCONTROL_REG(0x01)
        g.clock(); g.clock(); g.clock()
        g.writeCONTROL_REG(0x00)
        for _ in range(315):
            g.clock()
        self.assertEqual(g.readENV(), 0xFF)


class WaveformGeneratorTests(unittest.TestCase):
    def test_shift_register_init_value(self):
        g = R.WaveformGenerator()
        g.reset()
        self.assertEqual(g.shift_register, 0x3FFFFF)

    def test_clock_shift_register(self):
        g = R.WaveformGenerator()
        g.reset()
        g.shift_register = 0x35555E
        g.test_or_reset = False
        g.shift_latch = g.shift_register
        g.shift_phase2(0, 0)
        self.assertEqual(g.noise_output, 0x9E0)

    def test_noise_output(self):
        g = R.WaveformGenerator()
        g.reset()
        g.shift_register = 0x35555F
        g.set_noise_output()
        self.assertEqual(g.noise_output, 0xE20)

    def test_write_shift_register(self):
        g = R.WaveformGenerator()
        g.reset()
        g.waveform = 0xF
        g.write_shift_register()
        self.assertEqual(g.shift_register, 0x2DD6EB)

    def test_set_test_bit(self):
        g = R.WaveformGenerator()
        g.setModel(True)
        g.reset()
        g.shift_register = 0x35555E
        g.model_pulldown = R.pulldown_tables(R.MOS6581)
        g.writeCONTROL_REG(0x08)
        g.clock()
        g.writeCONTROL_REG(0x00)
        g.clock()
        self.assertEqual(g.noise_output, 0x9F0)

    def test_noise_write_back_1(self):
        modulator = R.WaveformGenerator()
        g = R.WaveformGenerator()
        g.setModel(True)
        g.model_pulldown = R.pulldown_tables(R.MOS6581)
        g.prevVoice = g.nextVoice = modulator
        g.reset()

        def step(control):
            g.writeCONTROL_REG(control)
            g.clock()
            g.output()

        step(0x88)
        step(0x90)
        expected = [0xFC, 0x6C, 0xD8, 0xB1, 0xD8, 0x6A, 0xB1, 0xF0]
        got = []
        for _ in expected:
            step(0x88)
            step(0x80)
            got.append(g.readOSC())
        self.assertEqual(got, expected)


class SidBusTests(unittest.TestCase):
    def test_bus_value_ttl_by_model(self):
        for model, ttl in ((R.MOS6581, 0x1D00), (R.MOS8580, 0xA2000)):
            sid = R.SID(model)
            sid.write(0x00, 0x5A)
            self.assertEqual(sid.read(0x00), 0x5A)
            sid.write(0x00, 0x5A)
            sid.clockDigital(ttl - 1)
            self.assertEqual(sid.busValue, 0x5A)
            sid.clockDigital(1)
            self.assertEqual(sid.busValue, 0)
            self.assertEqual(sid.read(0x19), 0xFF)


if __name__ == "__main__":
    unittest.main()
