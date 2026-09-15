"""Digital part of the MOS 6581 / CSG 8580 SID: a literal Python port of reSIDfp.

Source: libresidfp (https://github.com/libsidplayfp/libresidfp), commit
b8bdc00a980bab7ef855050a0293700e08c43784 -- EnvelopeGenerator, WaveformGenerator,
WaveformCalculator and the register/bus handling of SID.

    Copyright 2011-2026 Leandro Nini, 2018 VICE Project,
    2007-2010 Antti Lankila, 2004,2010 Dag Lem

This file is free software; you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation; either version 2 of the License, or (at your option) any later
version.  It is distributed WITHOUT ANY WARRANTY; see the GNU General Public
License for details.

Only the digital parts that determine register read-back ($D41B OSC3, $D41C
ENV3, the bus value) and envelope levels are ported; the analog filter, DACs
and resampling are not.  Every function mirrors its C++ counterpart statement
by statement, including C integer widths and short-circuit evaluation.  The
combined-waveform pulldown tables are computed with IEEE single-precision
arithmetic, as the C++ code does with ``float``.
"""
from array import array
import math

MOS6581, MOS8580 = "6581", "8580"


def _f32(x):
    return array("f", (x,))[0]


# ------------------------------------------------------------ envelope -------
ATTACK, DECAY_SUSTAIN, RELEASE = 0, 1, 2

ADSRTABLE = (0x007F, 0x3000, 0x1E00, 0x0660, 0x0182, 0x5573, 0x000E, 0x3805,
             0x2424, 0x2220, 0x090C, 0x0ECD, 0x010E, 0x23F7, 0x5237, 0x64A8)


class EnvelopeGenerator:
    __slots__ = ("lfsr", "rate", "exponential_counter", "exponential_counter_period",
                 "new_exponential_counter_period", "state_pipeline", "envelope_pipeline",
                 "exponential_pipeline", "state", "next_state", "counter_enabled", "gate",
                 "resetLfsr", "envelope_counter", "attack", "decay", "sustain", "release", "env3")

    def __init__(self):
        self.lfsr = 0x7FFF
        self.rate = 0
        self.exponential_counter = 0
        self.exponential_counter_period = 1
        self.new_exponential_counter_period = 0
        self.state_pipeline = 0
        self.envelope_pipeline = 0
        self.exponential_pipeline = 0
        self.state = RELEASE
        self.next_state = RELEASE
        self.counter_enabled = True
        self.gate = False
        self.resetLfsr = False
        self.envelope_counter = 0xAA
        self.attack = self.decay = self.sustain = self.release = 0
        self.env3 = 0

    def clock(self):
        self.env3 = self.envelope_counter
        if self.new_exponential_counter_period > 0:
            self.exponential_counter_period = self.new_exponential_counter_period
            self.new_exponential_counter_period = 0
        if self.state_pipeline:
            self.state_change()
        env_hit = False
        if self.envelope_pipeline != 0:
            self.envelope_pipeline -= 1
            env_hit = self.envelope_pipeline == 0
        if env_hit:
            if self.counter_enabled:
                if self.state == ATTACK:
                    self.envelope_counter = (self.envelope_counter + 1) & 0xFF
                    if self.envelope_counter == 0xFF:
                        self.next_state = DECAY_SUSTAIN
                        self.state_pipeline = 3
                elif self.state == DECAY_SUSTAIN or self.state == RELEASE:
                    self.envelope_counter = (self.envelope_counter - 1) & 0xFF
                    if self.envelope_counter == 0x00:
                        self.counter_enabled = False
                self.set_exponential_counter()
        else:
            exp_hit = False
            if self.exponential_pipeline != 0:
                self.exponential_pipeline -= 1
                exp_hit = self.exponential_pipeline == 0
            if exp_hit:
                self.exponential_counter = 0
                if (self.state == DECAY_SUSTAIN and self.envelope_counter != self.sustain) or self.state == RELEASE:
                    self.envelope_pipeline = 1
            elif self.resetLfsr:
                self.lfsr = 0x7FFF
                self.resetLfsr = False
                if self.state == ATTACK:
                    self.exponential_counter = 0
                    self.envelope_pipeline = 2
                elif self.counter_enabled:
                    self.exponential_counter += 1
                    if self.exponential_counter == self.exponential_counter_period:
                        self.exponential_pipeline = 2 if self.exponential_counter_period != 1 else 1
        if self.lfsr != self.rate:
            feedback = ((self.lfsr << 14) ^ (self.lfsr << 13)) & 0x4000
            self.lfsr = (self.lfsr >> 1) | feedback
        else:
            self.resetLfsr = True

    def state_change(self):
        self.state_pipeline -= 1
        ns = self.next_state
        if ns == ATTACK:
            if self.state_pipeline == 1:
                self.rate = ADSRTABLE[self.decay]
            elif self.state_pipeline == 0:
                self.state = ATTACK
                self.rate = ADSRTABLE[self.attack]
                self.counter_enabled = True
        elif ns == DECAY_SUSTAIN:
            if self.state_pipeline == 0:
                self.state = DECAY_SUSTAIN
                self.rate = ADSRTABLE[self.decay]
        else:
            if (self.state == ATTACK and self.state_pipeline == 0) or \
                    (self.state == DECAY_SUSTAIN and self.state_pipeline == 1):
                self.state = RELEASE
                self.rate = ADSRTABLE[self.release]

    def set_exponential_counter(self):
        c = self.envelope_counter
        if c == 0xFF or c == 0x00:
            self.new_exponential_counter_period = 1
        elif c == 0x5D:
            self.new_exponential_counter_period = 2
        elif c == 0x36:
            self.new_exponential_counter_period = 4
        elif c == 0x1A:
            self.new_exponential_counter_period = 8
        elif c == 0x0E:
            self.new_exponential_counter_period = 16
        elif c == 0x06:
            self.new_exponential_counter_period = 30

    def reset(self):
        self.envelope_pipeline = 0
        self.state_pipeline = 0
        self.attack = self.decay = self.sustain = self.release = 0
        self.gate = False
        self.resetLfsr = True
        self.exponential_counter = 0
        self.exponential_counter_period = 1
        self.new_exponential_counter_period = 0
        self.state = RELEASE
        self.counter_enabled = True
        self.rate = ADSRTABLE[self.release]

    def writeCONTROL_REG(self, control):
        gate_next = (control & 0x01) != 0
        if gate_next != self.gate:
            self.gate = gate_next
            if gate_next:
                self.next_state = ATTACK
                self.state_pipeline = 2
                if self.resetLfsr or self.exponential_pipeline == 2:
                    self.envelope_pipeline = 2 if (self.exponential_counter_period == 1 or
                                                   self.exponential_pipeline == 2) else 4
                elif self.exponential_pipeline == 1:
                    self.state_pipeline = 3
            else:
                self.next_state = RELEASE
                self.state_pipeline = 3 if self.envelope_pipeline > 0 else 2

    def writeATTACK_DECAY(self, attack_decay):
        self.attack = (attack_decay >> 4) & 0x0F
        self.decay = attack_decay & 0x0F
        if self.state == ATTACK:
            self.rate = ADSRTABLE[self.attack]
        elif self.state == DECAY_SUSTAIN:
            self.rate = ADSRTABLE[self.decay]

    def writeSUSTAIN_RELEASE(self, sustain_release):
        self.sustain = (sustain_release & 0xF0) | ((sustain_release >> 4) & 0x0F)
        self.release = sustain_release & 0x0F
        if self.state == RELEASE:
            self.rate = ADSRTABLE[self.release]

    def output(self):
        return self.envelope_counter

    def readENV(self):
        return self.env3


# ------------------------------------------------------ waveform tables -------
def _exponential_distance(distance, i):
    return _f32(math.pow(distance, -i))


def _linear_distance(distance, i):
    return _f32(1.0 / _f32(1.0 + _f32(i * distance)))


def _quadratic_distance(distance, i):
    return _f32(1.0 / _f32(1.0 + _f32((i * i) * distance)))


def _cfg(func, threshold, topbit, pulsestrength, distance1, distance2):
    return (func, _f32(threshold), _f32(topbit), _f32(pulsestrength), _f32(distance1), _f32(distance2))


_E, _L, _Q = _exponential_distance, _linear_distance, _quadratic_distance

CONFIG_AVERAGE = {
    MOS6581: (  # 6581 R3 0486S sampled by Trurl
        _cfg(_E, 0.877322257, 1.11349654, 0.0, 2.14537621, 9.08618164),      # TS
        _cfg(_L, 0.941692829, 1.0, 1.80072665, 0.033124879, 0.232303441),    # PT
        _cfg(_L, 1.66494179, 1.03760982, 5.62705326, 0.291590303, 0.283631504),  # PS
        _cfg(_L, 1.09762526, 0.975265801, 1.52196741, 0.151528224, 0.841949463),  # PTS
        _cfg(_E, 0.96, 1.0, 2.5, 1.1, 1.2),                                  # NP (guessed)
    ),
    MOS8580: (  # 8580 R5 1088 sampled by reFX-Mike
        _cfg(_E, 0.853578329, 1.09615636, 0.0, 1.8819375, 6.80794907),       # TS
        _cfg(_E, 0.929835618, 1.0, 1.12836814, 1.10453653, 1.48065746),      # PT
        _cfg(_Q, 0.911938608, 0.996440411, 1.2278074, 0.000117214302, 0.18948476),  # PS
        _cfg(_E, 0.938004673, 1.04827631, 1.21178246, 0.915959001, 1.42698038),  # PTS
        _cfg(_E, 0.95, 1.0, 1.15, 1.0, 1.45),                                # NP (guessed)
    ),
}


def _tri_xor(val):
    return ((val if (val & 0x800) == 0 else val ^ 0xFFF) << 1)


WAVE_TABLE = (
    array("H", [0xFFF] * 4096),
    array("H", [_tri_xor(i) & 0xFFFF for i in range(4096)]),
    array("H", range(4096)),
    array("H", [i & ((i << 1) & 0xFFFF) for i in range(4096)]),
)


def _calculate_pulldown(distancetable, topbit, pulsestrength, threshold, accumulator):
    bit = [1.0 if accumulator & (1 << i) else 0.0 for i in range(12)]
    bit[11] = _f32(bit[11] * topbit)
    pulldown = [0.0] * 12
    for sb in range(12):
        avg = 0.0
        n = 0.0
        for cb in range(12):
            if cb == sb:
                continue
            weight = distancetable[sb - cb + 12]
            avg = _f32(avg + _f32(_f32(1.0 - bit[cb]) * weight))
            n = _f32(n + weight)
        avg = _f32(avg - pulsestrength)
        pulldown[sb] = _f32(avg / n)
    value = 0
    for i in range(12):
        bit_value = _f32(1.0 - pulldown[i]) if bit[i] > 0.0 else 0.0
        if bit_value > threshold:
            value |= 1 << i
    return value


_PULLDOWN_CACHE = {}


def pulldown_tables(model):
    """The five 4096-entry pulldown tables (TS, PT, PS, PTS, NP) for a chip model."""
    tables = _PULLDOWN_CACHE.get(model)
    if tables is None:
        tables = []
        for func, threshold, topbit, pulsestrength, distance1, distance2 in CONFIG_AVERAGE[model]:
            distancetable = [0.0] * 25
            distancetable[12] = 1.0
            for i in range(12, 0, -1):
                distancetable[12 - i] = func(distance1, i)
                distancetable[12 + i] = func(distance2, i)
            tables.append(array("H", [_calculate_pulldown(distancetable, topbit, pulsestrength, threshold, idx)
                                      for idx in range(4096)]))
        tables = _PULLDOWN_CACHE[model] = tuple(tables)
    return tables


# ------------------------------------------------------------ waveform -------
FLOATING_OUTPUT_TTL_6581R3 = 54000
FLOATING_OUTPUT_FADE_6581R3 = 1400
FLOATING_OUTPUT_TTL_8580R5 = 800000
FLOATING_OUTPUT_FADE_8580R5 = 50000
SHIFT_REGISTER_RESET_6581R3 = 50000
SHIFT_REGISTER_FADE_6581R3 = 15000
SHIFT_REGISTER_RESET_8580R5 = 986000
SHIFT_REGISTER_FADE_8580R5 = 314300

SHIFT_MASK = ~((1 << 2) | (1 << 4) | (1 << 8) | (1 << 11) | (1 << 13) | (1 << 17) | (1 << 20) | (1 << 22)) & 0xFFFFFFFF


def do_writeback(waveform_old, waveform_new, is6581):
    # Deviations from libresidfp, fixed by the VICE testbench wb_testsuite
    # (reference data measured on a 6581 R3 and an 8580 R5):
    #   6581: noise+pulse+tri/saw -> noise+pulse and $D -> $E write back;
    #   8580: $C -> $9/$E/$F write back.
    if is6581:
        if (waveform_new == 0xC and waveform_old in (0xD, 0xE, 0xF)) or (waveform_old == 0xD and waveform_new == 0xE):
            return True
    elif waveform_old == 0xC and waveform_new in (0x9, 0xE, 0xF):
        return True
    if waveform_old <= 8:
        return False
    if waveform_new < 8:
        return False
    if waveform_new == 8 and waveform_old != 0xF:
        return False
    if is6581 and ((((waveform_old & 0x3) == 0x1) and ((waveform_new & 0x3) == 0x2)) or
                   (((waveform_old & 0x3) == 0x2) and ((waveform_new & 0x3) == 0x1))):
        return False
    if waveform_old == 0xC:
        return False
    if waveform_new == 0xC:
        return False
    return True


def get_noise_writeback(wo):
    return (((wo & (1 << 11)) >> 9) | ((wo & (1 << 10)) >> 6) | ((wo & (1 << 9)) >> 1) |
            ((wo & (1 << 8)) << 3) | ((wo & (1 << 7)) << 6) | ((wo & (1 << 6)) << 11) |
            ((wo & (1 << 5)) << 15) | ((wo & (1 << 4)) << 18))


class WaveformGenerator:
    __slots__ = ("model_pulldown", "wave", "pulldown", "pw", "shift_register", "shift_latch",
                 "shift_pipeline", "ring_msb_mask", "no_noise", "noise_output", "no_noise_or_noise_output",
                 "no_pulse", "pulse_output", "waveform_output", "accumulator", "freq", "tri_saw_pipeline",
                 "osc3", "shift_register_reset", "floating_output_ttl", "waveform", "test", "sync",
                 "test_or_reset", "msb_rising", "is6581", "prevVoice", "nextVoice")

    def __init__(self):
        self.model_pulldown = None
        self.wave = WAVE_TABLE[0]
        self.pulldown = None
        self.pw = 0
        self.shift_register = 0
        self.shift_latch = 0
        self.shift_pipeline = 0
        self.ring_msb_mask = 0
        self.no_noise = 0
        self.noise_output = 0
        self.no_noise_or_noise_output = 0
        self.no_pulse = 0
        self.pulse_output = 0
        self.waveform_output = 0
        self.accumulator = 0x555555
        self.freq = 0
        self.tri_saw_pipeline = 0x555
        self.osc3 = 0
        self.shift_register_reset = 0
        self.floating_output_ttl = 0
        self.waveform = 0
        self.test = False
        self.sync = False
        self.test_or_reset = False
        self.msb_rising = False
        self.is6581 = True
        self.prevVoice = self.nextVoice = None

    def setModel(self, is6581):
        self.is6581 = is6581

    def setWave(self):
        self.wave = WAVE_TABLE[self.waveform & 0x3]

    def setPulldown(self):
        w = self.waveform & 0x7
        p = self.model_pulldown
        if w == 3:
            self.pulldown = p[0]
        elif w == 4:
            self.pulldown = p[4] if (self.waveform & 0x8) else None
        elif w == 5:
            self.pulldown = p[1]
        elif w == 6:
            self.pulldown = p[2]
        elif w == 7:
            self.pulldown = p[3]
        else:
            self.pulldown = None

    def clock(self):
        if self.test:
            if self.shift_register_reset != 0:
                self.shift_register_reset -= 1
                if self.shift_register_reset == 0:
                    self.shiftregBitfade()
                    self.shift_latch = self.shift_register
                    self.set_noise_output()
            self.test_or_reset = True
            self.pulse_output = 0xFFF
        else:
            accumulator_old = self.accumulator
            self.accumulator = (self.accumulator + self.freq) & 0xFFFFFF
            accumulator_bits_set = ~accumulator_old & self.accumulator
            self.msb_rising = (accumulator_bits_set & 0x800000) != 0
            if accumulator_bits_set & 0x080000:
                self.shift_pipeline = 2
            elif self.shift_pipeline != 0:
                self.shift_pipeline -= 1
                if self.shift_pipeline == 0:
                    self.shift_phase2(self.waveform, self.waveform)
                elif self.shift_pipeline == 1:
                    self.test_or_reset = False
                    self.shift_latch = self.shift_register

    def output(self):
        if self.waveform != 0:
            ix = (self.accumulator ^ (~self.prevVoice.accumulator & self.ring_msb_mask)) >> 12
            wo = self.wave[ix] & (self.no_pulse | self.pulse_output) & self.no_noise_or_noise_output
            if self.waveform == 0xC and not self.is6581:
                # 8580 noise+pulse: reSID's noise_pulse8580() instead of the
                # pulldown table (VICE wb_testsuite C_to_9/C_to_E_new, wf12nsr-8580)
                wo = wo & (wo << 1) & 0xFFF if wo < 0xFC0 else 0xFC0
            elif self.pulldown is not None:
                wo = self.pulldown[wo]
            self.waveform_output = wo
            if (self.waveform & 3) and not self.is6581:
                osc3 = self.tri_saw_pipeline & (self.no_pulse | self.pulse_output) & self.no_noise_or_noise_output
                if self.pulldown is not None:
                    osc3 = self.pulldown[osc3]
                self.osc3 = osc3
                self.tri_saw_pipeline = self.wave[ix]
            else:
                self.osc3 = wo
            if self.is6581 and (self.waveform & 0x2) and (wo & 0x800) == 0:
                self.msb_rising = False
                self.accumulator &= 0x7FFFFF
            self.write_shift_register()
        else:
            if self.floating_output_ttl != 0:
                self.floating_output_ttl -= 1
                if self.floating_output_ttl == 0:
                    self.waveBitfade()
        self.pulse_output = 0xFFF if (self.accumulator >> 12) >= self.pw else 0x000
        return self.waveform_output

    def shift_phase2(self, waveform_old, waveform_new):
        if do_writeback(waveform_old, waveform_new, self.is6581):
            wb = self.waveform_output
            if not self.is6581 and waveform_old == 0xC and waveform_new == 0xF:
                # the 8580 writes back the new waveform's output here (wb_testsuite C_to_F_new)
                ix = (self.accumulator ^ (~self.prevVoice.accumulator & self.ring_msb_mask)) >> 12
                wb = self.wave[ix] & (self.no_pulse | self.pulse_output) & self.no_noise_or_noise_output
                if self.pulldown is not None:
                    wb = self.pulldown[wb]
            self.shift_latch = (self.shift_register & SHIFT_MASK) | get_noise_writeback(wb)
        bit22 = ((1 if self.test_or_reset else 0) | self.shift_latch) << 22
        bit0 = (bit22 ^ (self.shift_latch << 17)) & (1 << 22)
        self.shift_register = ((self.shift_latch >> 1) | bit0) & 0xFFFFFFFF
        self.set_noise_output()

    def write_shift_register(self):
        if self.waveform > 0x8:
            if self.shift_pipeline != 1 and not self.test:
                # 6581 noise+pulse writes into the register only while
                # accumulator bit 19 is high (VICE wb_testsuite *_to_C_old with a
                # stopped oscillator, SID/wf12nsr with a running one).
                if not (self.is6581 and self.waveform == 0xC and not self.accumulator & 0x80000):
                    self.shift_register = self.shift_register & (SHIFT_MASK | get_noise_writeback(self.waveform_output))
                self.noise_output &= self.waveform_output
            else:
                self.noise_output = self.waveform_output
            self.set_no_noise_or_noise_output()

    def set_noise_output(self):
        s = self.shift_register
        self.noise_output = (((s & (1 << 2)) << 9) | ((s & (1 << 4)) << 6) | ((s & (1 << 8)) << 1) |
                             ((s & (1 << 11)) >> 3) | ((s & (1 << 13)) >> 6) | ((s & (1 << 17)) >> 11) |
                             ((s & (1 << 20)) >> 15) | ((s & (1 << 22)) >> 18))
        self.set_no_noise_or_noise_output()

    def set_no_noise_or_noise_output(self):
        self.no_noise_or_noise_output = self.no_noise | self.noise_output

    def synchronize(self):
        if self.msb_rising and self.nextVoice.sync and not (self.sync and self.prevVoice.msb_rising):
            self.nextVoice.accumulator = 0

    def writeFREQ_LO(self, v):
        self.freq = (self.freq & 0xFF00) | (v & 0xFF)

    def writeFREQ_HI(self, v):
        self.freq = ((v << 8) & 0xFF00) | (self.freq & 0xFF)

    def writePW_LO(self, v):
        self.pw = (self.pw & 0xF00) | (v & 0x0FF)

    def writePW_HI(self, v):
        self.pw = ((v << 8) & 0xF00) | (self.pw & 0x0FF)

    def writeCONTROL_REG(self, control):
        waveform_prev = self.waveform
        test_prev = self.test
        self.waveform = (control >> 4) & 0x0F
        self.test = (control & 0x08) != 0
        self.sync = (control & 0x02) != 0
        self.ring_msb_mask = ((~control >> 5) & (control >> 2) & 0x1) << 23
        if self.waveform != waveform_prev:
            self.setWave()
            self.setPulldown()
            self.no_noise = 0x000 if (self.waveform & 0x8) else 0xFFF
            self.set_no_noise_or_noise_output()
            self.no_pulse = 0x000 if (self.waveform & 0x4) else 0xFFF
            if self.waveform == 0:
                self.floating_output_ttl = FLOATING_OUTPUT_TTL_6581R3 if self.is6581 else FLOATING_OUTPUT_TTL_8580R5
        if self.test != test_prev:
            if self.test:
                self.accumulator = 0
                self.shift_pipeline = 0
                self.shift_latch = self.shift_register
                self.shift_register_reset = SHIFT_REGISTER_RESET_6581R3 if self.is6581 else SHIFT_REGISTER_RESET_8580R5
            else:
                self.shift_phase2(waveform_prev, self.waveform)

    def waveBitfade(self):
        self.waveform_output &= self.waveform_output >> 1
        self.osc3 = self.waveform_output
        if self.waveform_output != 0:
            self.floating_output_ttl = FLOATING_OUTPUT_FADE_6581R3 if self.is6581 else FLOATING_OUTPUT_FADE_8580R5

    def shiftregBitfade(self):
        self.shift_register |= self.shift_register >> 1
        self.shift_register |= 0x400000
        if self.shift_register != 0x7FFFFF:
            self.shift_register_reset = SHIFT_REGISTER_FADE_6581R3 if self.is6581 else SHIFT_REGISTER_FADE_8580R5

    def reset(self):
        self.freq = 0
        self.pw = 0
        self.msb_rising = False
        self.waveform = 0
        self.osc3 = 0
        self.test = False
        self.sync = False
        self.wave = WAVE_TABLE[0]
        self.pulldown = None
        self.ring_msb_mask = 0
        self.no_noise = 0xFFF
        self.no_pulse = 0xFFF
        self.pulse_output = 0xFFF
        self.shift_register_reset = 0
        self.shift_register = 0x7FFFFF
        self.test_or_reset = True
        self.shift_latch = self.shift_register
        self.shift_phase2(0, 0)
        self.shift_pipeline = 0
        self.waveform_output = 0
        self.floating_output_ttl = 0

    def readOSC(self):
        return (self.osc3 >> 4) & 0xFF


# ------------------------------------------------------------------ SID -------
BUS_TTL_6581 = 0x01D00
BUS_TTL_8580 = 0xA2000
INT_MAX = 2 ** 31 - 1


class SID:
    """reSIDfp SID, digital part only (clockDigital)."""

    def __init__(self, model=MOS8580):
        self.wave = [WaveformGenerator() for _ in range(3)]
        self.env = [EnvelopeGenerator() for _ in range(3)]
        for i in range(3):
            self.wave[i].prevVoice = self.wave[(i + 2) % 3]
            self.wave[i].nextVoice = self.wave[(i + 1) % 3]
        self.busValue = 0
        self.busValueTtl = 0
        self.modelTTL = 0
        self.nextVoiceSync = INT_MAX
        self.paddleX = self.paddleY = 0xFF
        self.model = None
        self.setChipModel(model)
        self.reset()

    def setChipModel(self, model):
        self.model = model
        self.modelTTL = BUS_TTL_6581 if model == MOS6581 else BUS_TTL_8580
        tables = pulldown_tables(model)
        for w in self.wave:
            w.setModel(model == MOS6581)
            w.model_pulldown = tables

    def reset(self):
        for i in range(3):
            self.wave[i].reset()
            self.env[i].reset()
        self.busValue = 0
        self.busValueTtl = 0
        self.voiceSync(False)

    def voiceSync(self, sync):
        if sync:
            for w in self.wave:
                w.synchronize()
        self.nextVoiceSync = INT_MAX
        for w in self.wave:
            freq = w.freq
            if w.test or freq == 0 or not w.nextVoice.sync:
                continue
            this_sync = ((0x7FFFFF - w.accumulator) & 0xFFFFFF) // freq + 1
            if this_sync < self.nextVoiceSync:
                self.nextVoiceSync = this_sync

    def ageBusValue(self, n):
        if self.busValueTtl != 0:
            self.busValueTtl -= n
            if self.busValueTtl <= 0:
                self.busValue = 0
                self.busValueTtl = 0

    def clockDigital(self, cycles):
        self.ageBusValue(cycles)
        wave, env = self.wave, self.env
        while cycles != 0:
            delta_t = min(self.nextVoiceSync, cycles)
            if delta_t > 0:
                for _ in range(delta_t):
                    wave[0].clock(); wave[1].clock(); wave[2].clock()
                    env[0].clock(); env[1].clock(); env[2].clock()
                    wave[0].output(); wave[1].output(); wave[2].output()
                cycles -= delta_t
                self.nextVoiceSync -= delta_t
            if self.nextVoiceSync == 0:
                self.voiceSync(True)

    def read(self, offset):
        if offset == 0x19:
            self.busValue = self.paddleX
            self.busValueTtl = self.modelTTL
        elif offset == 0x1A:
            self.busValue = self.paddleY
            self.busValueTtl = self.modelTTL
        elif offset == 0x1B:
            self.busValue = self.wave[2].readOSC()
            self.busValueTtl = self.modelTTL
        elif offset == 0x1C:
            self.busValue = self.env[2].readENV()
            self.busValueTtl = self.modelTTL
        else:
            self.busValueTtl = int(self.busValueTtl / 2)
        return self.busValue

    def write(self, offset, value):
        self.busValue = value
        self.busValueTtl = self.modelTTL
        if offset < 0x15:
            v, k = divmod(offset, 7)
            w, e = self.wave[v], self.env[v]
            if k == 0:
                w.writeFREQ_LO(value)
            elif k == 1:
                w.writeFREQ_HI(value)
            elif k == 2:
                w.writePW_LO(value)
            elif k == 3:
                w.writePW_HI(value)
            elif k == 4:
                w.writeCONTROL_REG(value)
                e.writeCONTROL_REG(value)
            elif k == 5:
                e.writeATTACK_DECAY(value)
            else:
                e.writeSUSTAIN_RELEASE(value)
        self.voiceSync(False)
