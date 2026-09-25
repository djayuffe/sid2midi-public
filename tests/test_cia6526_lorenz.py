"""6526 timer model against the Wolfgang Lorenz test suite (cia1ta / cia1tb, old CIA).

The expectation routines x000..x919 of cia1ta.s / cia1tb.s (public domain) are
transliterated below.  Each case replays the programme's register accesses at
their exact cycle offsets: every access in the measured sequence is the last
cycle of a 4-cycle absolute-address instruction, apart from one 2-cycle LDX #.

    A0      STX $DC04/06   i4      (latch low)
    A0+6    STX $DC0E/0F   $10     (force load, stopped)
    A0+10   BIT $DC0D              (acknowledge)
    A0+14   STA $DC0E/0F   ie
    A0+22   STA $DC04/06   b4
    A0+26   STY $DC0E/0F   be
    A0+30   LDA $DC04/06   -> a4
    A0+34   LDX $DC0D      -> ad
    A0+38   LDY $DC0E/0F   -> ae
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cia_vice import Cia6526

SUB100 = [5, 5, 5, 3, 1, 5, 4, 3, 2, 1]
SPEC100 = [0x71, 0x62, 0x53, 0x52, 0x51, 0x31, 0x23, 0x22, 0x21, 0x13, 0x12, 0x11, 0x03, 0x02, 0x01, 0x00]
CORR100 = [0x00, 0x01, 0x02, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x02, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00]
SUB101 = [7, 7, 7, 5, 3, 7, 6, 5, 4, 3, 2, 1]
SPEC101 = [0x82, 0x73, 0x64, 0x63, 0x55, 0x54, 0x52, 0x33, 0x25, 0x24, 0x22, 0x15, 0x14, 0x12, 0x05, 0x04, 0x02]
CORR101 = [0x01, 0x02, 0x03, 0x01, 0x04, 0x02, 0x01, 0x02, 0x04, 0x02, 0x01, 0x04, 0x02, 0x01, 0x04, 0x02, 0x01]
SUB109 = [7, 7, 7, 5, 3, 7, 6, 5, 4, 3, 0, 0]
B4COMP = [0x10, 0x10, 0x10, 0x0E, 0x0C, 0x10, 0x0F, 0x0E, 0x0D, 0x0C]
NODEC119 = [0x82, 0x73, 0x72, 0x64, 0x63, 0x55, 0x54, 0x52, 0x33, 0x32, 0x25, 0x24, 0x22, 0x15, 0x14,
            0x12, 0x05, 0x04, 0x02]
SUB901 = [1, 0, 0, 0, 0, 2, 2, 2, 2, 2, 0, 1, 0, 0]
SUB909 = [0, 0, 0, 0, 0, 2, 2, 2, 2, 2, 0, 0, 0, 0]


def expected(i4, ie, b4, be, bit):
    """(counter low, ICR, control) the old-CIA C64 produces -- per cia1ta.s/cia1tb.s."""
    irq, low = 0x80 | bit, bit

    def flags(v):
        return (low if v < 6 else 0) | (0x80 if v < 5 else 0)

    def spec(i, b, table, corr):
        key = ((i << 4) | b) & 0xFF
        return corr[table.index(key)] if key in table else b

    def x000():
        return i4, 0, be

    def x001():
        r4 = (i4 - 2) & 0xFF if i4 >= 3 else (b4 - 1 if i4 == 0 and b4 >= 2 else b4)
        return r4, (low if i4 < 7 else 0) | (0x80 if i4 < 6 else 0), 0x01

    def x009():
        r4 = i4 - 2 if i4 >= 3 else b4
        return r4, (low if i4 < 7 else 0) | (0x80 if i4 < 6 else 0), 0x08 if i4 < 0x0B else 0x09

    def x010():
        return b4, 0, be & 0x09

    def x011():
        return (b4 - 1 if b4 >= 2 else b4), flags(b4) | (irq if i4 == 0 else 0), be & 0x09

    def x019():
        r4 = b4 - 1 if b4 >= 2 and i4 != 0 else b4
        re = 0x08 if (i4 == 0 or b4 < 0x0A) else 0x09
        return r4, flags(b4) | (irq if i4 == 0 else 0), re

    def x100():
        if i4 >= 0x0B:
            return i4 - 0x0B, 0, be
        if i4 >= 0x0A:
            return b4, irq, be
        s = SUB100[i4]
        return (b4 - s if b4 >= s else spec(i4, b4, SPEC100, CORR100)), irq, be

    def x101():
        if i4 > 0x0D:
            d = i4 - 0x0D
            return (d, irq, 1) if d < 4 else (4, low, 1) if d == 4 else (d, 0, 1)
        if i4 >= 0x0C:
            return b4, irq, 1
        s = SUB101[i4]
        return (b4 - s if b4 > s else spec(i4, b4, SPEC101, CORR101)), irq, 1

    def x109():
        if i4 > 0x0D:
            d = i4 - 0x0D
            r4, rd = (d, irq) if d < 4 else (4, low) if d == 4 else (d, 0)
        else:
            s = 0 if i4 >= 0x0C else SUB109[i4]
            r4, rd = (b4 - s if b4 > s else b4), irq
        if i4 >= 0x16:
            re = 0x09
        elif i4 >= 0x0A:
            re = 0x08
        else:
            re = 0x09 if b4 >= B4COMP[i4] else 0x08
        return r4, rd, re

    def x110():
        return b4, (irq if i4 < 0x0B else 0), be & 0x09

    def x111():
        rd = irq if i4 < 0x0C else flags(b4) | (irq if i4 == 0 else 0)
        return (b4 - 1 if b4 >= 2 else b4), rd, be & 0x09

    def x119():
        r4 = b4
        if b4 >= 2 and (i4 >= 0x0C or (i4 < 0x0A and (b4 >= 0x0F or ((i4 << 4) | b4) & 0xFF not in NODEC119))):
            r4 -= 1
        rd = flags(b4) | (irq if i4 < 0x0C else 0)
        re = 0x08 if (0x0A <= i4 < 0x0C or b4 < 0x0A) else 0x09
        return r4, rd, re

    def x900():
        if i4 < 5:
            return i4, irq, be
        if i4 >= 0x0B:
            return i4 - 0x0B, 0, be
        return b4, irq, be

    def x901():
        if i4 >= 0x0E:
            r4 = i4 - 0x0D
        elif i4 in (3, 4):
            r4 = i4 - 2
        else:
            s = SUB901[i4]
            r4 = b4 - s if b4 > s else b4
        rd = ((low if i4 == 0x11 else 0) if i4 >= 0x11 else irq)
        return r4, rd, 0 if i4 == 0x0A else 1

    def x909():
        if i4 in (3, 4):
            r4 = i4 - 2
        elif i4 > 0x0D:
            r4 = i4 - 0x0D
        else:
            s = SUB909[i4]
            r4 = b4 - s if b4 > s else b4
        rd = ((low if i4 == 0x11 else 0) if i4 >= 0x11 else irq)
        if i4 >= 0x16:
            re = 0x09
        elif i4 >= 0x0A or i4 < 5 or b4 < 0x0B:
            re = 0x08
        else:
            re = 0x09
        return r4, rd, re

    def x910():
        return b4, (irq if i4 < 0x0B else 0), be & 0x09

    def x911():
        r4 = b4 if i4 == 0x0A else (b4 - 1 if b4 >= 2 else b4)
        rd = irq if i4 < 0x0C else ((low if b4 == 5 else 0) if b4 >= 5 else irq)
        return r4, rd, 0 if i4 == 0x0A else be & 0x09

    def x919():
        r4 = b4 if i4 in (0, 0x0A, 0x0B) else (b4 - 1 if b4 >= 2 else b4)
        rd = irq if i4 < 0x0C else ((low if b4 == 5 else 0) if b4 >= 5 else irq)
        if i4 == 0 or 0x0A <= i4 < 0x0C:
            re = 0x08
        else:
            re = 0x08 if b4 < 0x0A else 0x09
        return r4, rd, re

    table = {
        0x10: {0x00: x000, 0x01: x001, 0x08: x000, 0x09: x009, 0x10: x010, 0x11: x011, 0x18: x010, 0x19: x019},
        0x11: {0x00: x100, 0x01: x101, 0x08: x100, 0x09: x109, 0x10: x110, 0x11: x111, 0x18: x110, 0x19: x119},
        0x18: {0x00: x000, 0x01: x001, 0x08: x000, 0x09: x009, 0x10: x010, 0x11: x011, 0x18: x010, 0x19: x019},
        0x19: {0x00: x900, 0x01: x901, 0x08: x900, 0x09: x909, 0x10: x910, 0x11: x911, 0x18: x910, 0x19: x919},
    }
    r4, rd, re = table[ie][be]()
    return r4 & 0xFF, rd, re


def measure(i4, ie, b4, be, timer_b):
    cia = Cia6526()
    lo, cr, bit = (0x06, 0x0F, 0x02) if timer_b else (0x04, 0x0E, 0x01)
    t = 10
    if not timer_b:                               # IOINIT leaves timer A running at 60 Hz
        cia.write(0x04, 0x25, t)
        cia.write(0x05, 0x40, t + 4)
        cia.write(0x0E, 0x11, t + 8)
    cia.write(0x0D, 0x7F, t + 100)
    cia.write(0x0D, 0x80 | bit, t + 106)
    b = t + 5000
    cia.write(cr, 0x00, b)
    cia.write(lo + 1, 0x00, b + 4)
    a0 = b + 36
    cia.write(lo, i4, a0)
    cia.write(cr, 0x10, a0 + 6)
    cia.read(0x0D, a0 + 10)
    cia.write(cr, ie, a0 + 14)
    cia.write(lo, b4, a0 + 22)
    cia.write(cr, be, a0 + 26)
    return cia.read(lo, a0 + 30), cia.read(0x0D, a0 + 34), cia.read(cr, a0 + 38)


class LorenzTimerTests(unittest.TestCase):
    def check_timer(self, timer_b):
        failures = []
        for i4 in range(31):
            for b4 in range(21):
                for ie in (0x10, 0x11, 0x18, 0x19):
                    for be in (0x00, 0x01, 0x08, 0x09, 0x10, 0x11, 0x18, 0x19):
                        got = measure(i4, ie, b4, be, timer_b)
                        want = expected(i4, ie, b4, be, 0x02 if timer_b else 0x01)
                        if got != want:
                            failures.append((i4, ie, b4, be, got, want))
        if failures:
            lines = ["i4=%02X ie=%02X b4=%02X be=%02X got %02X %02X %02X want %02X %02X %02X"
                     % (f[0], f[1], f[2], f[3], *f[4], *f[5]) for f in failures[:25]]
            self.fail("%d of %d cases differ:\n%s" % (len(failures), 31 * 21 * 32, "\n".join(lines)))

    def test_cia1ta(self):
        self.check_timer(False)

    def test_cia1tb(self):
        self.check_timer(True)


if __name__ == "__main__":
    unittest.main()
