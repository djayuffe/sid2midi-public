"""tools/singlesteptests.py: vector execution, failure detection and the known ANE difference."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import singlesteptests as SST  # noqa: E402
from cpu6502 import CPU6502  # noqa: E402


def vector(program, a, x, final_a, final_p, cycles):
    ram = [[0x1000 + i, b] for i, b in enumerate(program)]
    return {"name": " ".join("%02x" % b for b in program),
            "initial": {"pc": 0x1000, "s": 0xFD, "a": a, "x": x, "y": 0, "p": 0x26, "ram": ram},
            "final": {"pc": 0x1000 + len(program), "s": 0xFD, "a": final_a, "x": x, "y": 0, "p": final_p,
                      "ram": [list(cell) for cell in ram]},
            "cycles": cycles}


def lda_immediate():
    return vector([0xA9, 0x42], 0x00, 0x00, 0x42, 0x24, [[0x1000, 0xA9, "read"], [0x1001, 0x42, "read"]])


class SingleStepToolTests(unittest.TestCase):
    def setUp(self):
        self.bus = SST.Bus()
        self.cpu = CPU6502(self.bus.read, self.bus.write)

    def fields(self, test):
        return {f for f, _ in SST.run_test(self.cpu, self.bus, test)}

    def test_matching_vector_passes_and_memory_is_cleared(self):
        self.assertEqual(self.fields(lda_immediate()), set())
        self.assertEqual(self.bus.mem, bytearray(0x10000))

    def test_register_and_bus_differences_are_reported(self):
        wrong_a = lda_immediate()
        wrong_a["final"]["a"] = 0x43
        self.assertEqual(self.fields(wrong_a), {"a"})
        wrong_bus = lda_immediate()
        wrong_bus["cycles"][1][2] = "write"
        self.assertEqual(self.fields(wrong_bus), {"bus"})
        wrong_ram = lda_immediate()
        wrong_ram["final"]["ram"][1][1] = 0x00
        self.assertEqual(self.fields(wrong_ram), {"ram"})

    def test_ane_vectors_assuming_magic_ee_are_recognised(self):
        # ANE #$FF with A=0, X=$FF: $EE on the vector, $EF on the C64 (cpu6502.py)
        ane = vector([0x8B, 0xFF], 0x00, 0xFF, 0xEE, 0xA4, [[0x1000, 0x8B, "read"], [0x1001, 0xFF, "read"]])
        self.assertEqual(self.fields(ane), {"a"})
        self.assertTrue(SST.ane_needs_magic_ee(ane))
        self.assertFalse(SST.ane_needs_magic_ee(lda_immediate()))


if __name__ == "__main__":
    unittest.main()
