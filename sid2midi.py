#!/usr/bin/env python3
"""
sid2midi.py — PSID/RSID -> Standard MIDI File converter (any .sid)
===================================================================

Runs the tune on an NMOS 6510 + C64 model and converts the SID register
stream to a richly annotated Standard MIDI File.  Pure stdlib.

MACHINE
  CPU      NMOS 6510, instruction level; IRQ/NMI sampled between instructions.
  CIA 1/2  6526 timers A and B (phi2, timer-A-underflow and one-shot modes,
           force load), interrupt control register, ports.  CIA 1 drives IRQ,
           CIA 2 drives the edge-triggered NMI.
  VIC-II   raster counter and raster-compare interrupt (PAL 6569 / NTSC 6567R8).
  SID      register capture for up to three chips plus reSID-equivalent
           envelope generators and oscillators: $D41B (OSC3) and $D41C (ENV3)
           read back, and note velocities come from the real envelope level.
  ROMs     the bundled KERNAL and BASIC ROMs are cold-started (once per
           process, then cached) so RSIDs find a genuine post-reset machine.

TIMING
  PSID with a play address : play is called at v-sync or at the CIA 1 timer-A
                             rate the tune programs (re-read every call).
  RSID, PSID play = 0      : the machine runs continuously after init: IRQ and
                             NMI handlers and any main loop execute as on a C64.
  RSID "C64 BASIC"         : the program is loaded at $0801 and started with RUN;
                             the song number is placed in $030C.
  Every SID write is cycle-stamped; note-ONs land on the gate-trigger cycle.

MIDI
  per voice : notes, pitch bend (exact 16-bit frequency), program change on
              waveform, CC70 pwm, CC71 res, CC72-79 ADSR, CC20-23 ctrl bits.
  per SID   : Filter/Master track, CC74 cutoff, CC71 res, CC24 mode,
              CC25 routing, CC7 volume, CC26 $D418 write density (digi).
  channels  : SID A voices 1-3, SID B 5-7, SID C 11-13, drums 10,
              filter tracks 4 / 8 / 14.

Usage:
  python3 sid2midi.py tune.sid [-o out.mid] [--song N | --all-songs]
        [--seconds S] [--auto] [--bpm B] [--drumvoice 1-3] [--no-bend]
        [--no-cc] [--report] [--info]
"""
import argparse
import copy
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cpu6502 import CPU6502
from cia_vice import Cia6526
from vicii_sc import VicIISC
import residfp
import mus

VERSION = "3.1.1"

PAL_CLOCK, NTSC_CLOCK = 985248.0, 1022727.0
PAL_CPL, PAL_LINES = 63, 312
NTSC_CPL, NTSC_LINES = 65, 263
PAL_FRAME, NTSC_FRAME = PAL_CPL * PAL_LINES, NTSC_CPL * NTSC_LINES
PAL_CIA_LATCH, NTSC_CIA_LATCH = 0x4025, 0x4295   # KERNAL default 60 Hz IRQ
ACC = 16777216.0
SIDMODEL = {0: "unknown", 1: "MOS6581", 2: "MOS8580", 3: "6581+8580"}
IDLE_PC = 0x0001          # init's return address: the CPU idles here between interrupts
PORT_FADE_CYCLES = 0x28000  # unused 6510 port bits 6/7 hold their charge this long as inputs
READY_LOOP = 0xE5CD       # KERNAL keyboard wait loop entered once BASIC prints READY.
NEVER = float("inf")

_ROMDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roms")


def _rom(name, size):
    try:
        with open(os.path.join(_ROMDIR, name), "rb") as fh:
            d = fh.read()
    except OSError:
        return None
    return d[:size] if len(d) >= size else None


KERNAL = _rom("kernal.bin", 8192)
BASIC = _rom("basic.bin", 8192)
CHARGEN = _rom("chargen.bin", 4096)


# --------------------------------------------------------------- SID file -----
class SidError(ValueError):
    """The file cannot be interpreted as a playable PSID/RSID tune."""


class DebugCartExit(Exception):
    """A test program wrote its exit code to the VICE debug cartridge ($D7FF)."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def sid_address(byte):
    """Decode a v3/v4 header SID address byte ($Dxx0); 0 if absent or invalid."""
    if not byte or byte & 1:
        return 0
    addr = 0xD000 | (byte << 4)
    return addr if (0xD420 <= addr <= 0xD7E0 or 0xDE00 <= addr <= 0xDFE0) else 0


def _text(raw):
    return raw.split(b"\0")[0].decode("latin1")


class SidFile:
    def __init__(self, path, fixups=True):
        with open(path, "rb") as fh:
            d = fh.read()
        self.path = path
        self.warnings = []
        warn = self.warnings.append
        if len(d) < 0x76:
            raise SidError("file too short for a PSID/RSID header (%d bytes)" % len(d))
        self.magic = d[:4]
        if self.magic not in (b"PSID", b"RSID"):
            raise SidError("not a PSID/RSID file")
        (self.version, self.data_off, self.load, self.init, self.play,
         self.songs, self.start, self.speed) = struct.unpack(">HHHHHHHI", d[4:0x16])
        self.rsid = self.magic == b"RSID"
        if self.version not in (1, 2, 3, 4):
            raise SidError("unsupported header version %d" % self.version)
        if self.rsid and self.version < 2:
            raise SidError("RSID files require header version 2 or later")
        if not 0x76 <= self.data_off <= len(d):
            raise SidError("data offset $%04X lies outside the file" % self.data_off)
        expected = 0x76 if self.version == 1 else 0x7C
        if self.data_off != expected:
            warn("unusual data offset $%04X for header v%d (expected $%04X)"
                 % (self.data_off, self.version, expected))
        self.name = _text(d[0x16:0x36])
        self.author = _text(d[0x36:0x56])
        self.release = _text(d[0x56:0x76])
        extended = self.version >= 2 and self.data_off >= 0x7C
        self.flags = struct.unpack(">H", d[0x76:0x78])[0] if extended else 0
        sid2b = d[0x7A] if extended and self.version >= 3 else 0
        sid3b = d[0x7B] if extended and self.version >= 4 else 0
        if self.flags & 1 and not self.rsid:
            raise SidError("MUS (Compute!'s Sidplayer) data needs the Sidplayer player routine, "
                           "which is not bundled; not supported")
        self.basic = self.rsid and bool(self.flags & 2)

        data = d[self.data_off:]
        if self.load == 0:
            if len(data) < 2:
                raise SidError("missing embedded load address")
            self.load = data[0] | data[1] << 8
            data = data[2:]
            swapped = data and ((self.load >> 8) | (self.load << 8)) & 0xFFFF
            if (fixups and self.load < 0x0200 and swapped >= 0x0200
                    and all(swapped <= a < swapped + len(data) for a in (self.init, self.play) if a)):
                # Some converters store the embedded address big-endian: the
                # code would land in zero page while init/play point elsewhere.
                warn("embedded load address $%04X looks byte-swapped; using $%04X (--no-fixups disables)"
                     % (self.load, swapped))
                self.load = swapped
        elif (fixups and len(data) >= 3 and (data[0] | data[1] << 8) == self.load
              and data[2] == 0x4C and data[0] != 0x4C):
            # Converters sometimes keep the PRG load address although the
            # header already names it; the code then sits 2 bytes too high.
            data = data[2:]
            warn("data repeats the load address $%04X; skipped 2 bytes (--no-fixups disables)" % self.load)
        if not data:
            raise SidError("no C64 data after the header")
        if self.load + len(data) > 0x10000:
            warn("data runs past $FFFF (%d bytes at $%04X); truncated to fit 64K"
                 % (len(data), self.load))
            data = data[:0x10000 - self.load]
        self.data = bytes(data)
        self.end = self.load + len(self.data)
        if self.songs == 0:
            warn("header declares 0 songs; assuming 1")
            self.songs = 1
        if not 1 <= self.start <= self.songs:
            warn("start song %d out of range 1-%d; using 1" % (self.start, self.songs))
            self.start = 1
        if self.rsid and self.play:
            warn("RSID header has play address $%04X; ignored (RSID is interrupt driven)" % self.play)
            self.play = 0
        if self.basic:
            if self.init:
                warn("C64 BASIC tune declares init address $%04X; ignored (started with RUN)" % self.init)
                self.init = 0
            if self.load != 0x0801:
                warn("C64 BASIC program loads at $%04X instead of $0801" % self.load)
        else:
            if self.init == 0:
                self.init = self.load
            if not self.load <= self.init < self.end:
                warn("init address $%04X lies outside the loaded data $%04X-$%04X"
                     % (self.init, self.load, self.end - 1))
            elif not any(self.data[self.init - self.load:self.init - self.load + 16]):
                warn("init address $%04X points at empty ($00) data; the file is probably damaged" % self.init)
        if self.play and not self.load <= self.play < self.end:
            warn("play address $%04X lies outside the loaded data $%04X-$%04X"
                 % (self.play, self.load, self.end - 1))
        if len(self.data) < 16:
            warn("only %d bytes of C64 data; the file is probably a stub" % len(self.data))

        self.pal = ((self.flags >> 2) & 3) != 2
        self.clock = PAL_CLOCK if self.pal else NTSC_CLOCK
        self.frame = PAL_FRAME if self.pal else NTSC_FRAME
        self.rate = 50 if self.pal else 60
        self.model = SIDMODEL[(self.flags >> 4) & 3]
        codes = ((self.flags >> 4) & 3, (self.flags >> 6) & 3, (self.flags >> 8) & 3)
        first = "8580" if codes[0] == 2 else "6581"
        # Per-chip model for the emulation: unknown/both -> 6581; SIDs #2/#3 default to #1.
        self.chip_models = [first] + ["8580" if m == 2 else "6581" if m == 1 else first for m in codes[1:]]
        self.extra_sids = []
        for n, b in ((2, sid2b), (3, sid3b)):
            if not b:
                continue
            addr = sid_address(b)
            if addr and addr not in self.extra_sids:
                self.extra_sids.append(addr)
            else:
                warn("SID #%d address byte $%02X is invalid; ignored" % (n, b))

    @property
    def sid2(self):
        return self.extra_sids[0] if self.extra_sids else 0

    def vsync(self, song):
        return (self.speed >> min(song, 31)) & 1 == 0

    def bank_for(self, addr):
        """PSID $01 value for a call to ``addr`` (PSID v2NG spec, load-range aware)."""
        def touches(lo, hi):
            return self.load < hi and self.end > lo
        if 0xD000 <= addr < 0xE000:
            return 0x34
        if addr >= 0xE000 or touches(0xE000, 0x10000):
            return 0x35
        if addr >= 0xA000 or touches(0xA000, 0xC000):
            return 0x36
        return 0x37

    def describe(self):
        entry = "BASIC RUN" if self.basic else "init $%04X" % self.init
        lines = ["%s v%d  '%s' / %s / %s" % (self.magic.decode(), self.version,
                                             self.name, self.author, self.release),
                 "chip %s  %s %dHz  songs %d (start %d)" % (self.model, "PAL" if self.pal else "NTSC",
                                                           self.rate, self.songs, self.start),
                 "load $%04X-$%04X  %s  play $%04X%s" % (self.load, self.end - 1, entry, self.play,
                                                         "  (interrupt driven)" if not self.play else ""),
                 "flags $%04X  speed $%08X" % (self.flags, self.speed)]
        if self.extra_sids:
            lines.append("extra SIDs: " + " ".join("$%04X" % a for a in self.extra_sids))
        return lines


class MusTune(SidFile):
    """A Compute!'s Sidplayer MUS file played by a user-supplied player (see mus.py).

    It behaves like a single-song PSID: init/play of the player are called
    at the rate CIA #1 timer A is programmed to."""

    def __init__(self, path, player_path):
        with open(path, "rb") as fh:
            data = fh.read()
        if mus.voice3_index(data) is None:
            raise SidError("not a MUS file")
        try:
            self.player = mus.load_player(player_path)
        except (OSError, mus.MusError, mus.AsmError) as e:
            raise SidError("Sidplayer player %s: %s" % (player_path, e))
        self.path = path
        self.warnings = []
        self.magic, self.version, self.rsid, self.basic = b"MUS ", 0, False, False
        self.mus = data
        lines = mus.credits(data)
        self.name = lines[0] if lines else os.path.basename(path)
        self.author = lines[1] if len(lines) > 1 else ""
        self.release = lines[2] if len(lines) > 2 else ""
        self.load, self.data = mus.MUS_DATA_ADDR, data
        self.end = self.load + len(data)
        self.init, self.play = mus.PLAYER1_INIT, mus.PLAYER1_PLAY
        self.songs = self.start = 1
        self.speed = 1                                   # CIA #1 timer A
        self.flags, self.data_off = 0x14, 0
        self.pal = True
        self.clock, self.frame, self.rate = PAL_CLOCK, PAL_FRAME, 50
        self.model = SIDMODEL[1]
        self.chip_models = ["6581"]
        self.extra_sids = []
        if len(data) + mus.MUS_DATA_ADDR > self.player[0]:
            raise SidError("MUS data too large for the player at $%04X" % self.player[0])

    def place(self, ram):
        mus.install(ram, self.mus, self.player)

    def bank_for(self, addr):
        return 0x35 if addr >= 0xE000 else 0x37            # player under the KERNAL ROM

    def describe(self):
        return ["MUS  '%s' / %s / %s" % (self.name, self.author, self.release),
                "Compute!'s Sidplayer data at $%04X-$%04X, player $%04X (init $%04X, play $%04X)"
                % (self.load, self.end - 1, self.player[0], self.init, self.play)]


# ------------------------------------------------------------- SID model ------
class SidEnvelope:
    """reSID 0.16 envelope generator: 15-bit rate counter (with the ADSR delay
    bug), exponential decay/release periods, sustain comparison, attack wrap."""
    RATE = (9, 32, 63, 95, 149, 220, 267, 313, 392, 977, 1954, 3126, 3907, 11720, 19532, 31251)
    __slots__ = ("counter", "attack", "decay", "sustain", "release", "gate", "rate_counter",
                 "rate_period", "exp_counter", "exp_period", "state", "hold_zero")

    def __init__(self):
        self.counter = 0
        self.attack = self.decay = self.sustain = self.release = 0
        self.gate = 0
        self.rate_counter = 0
        self.rate_period = self.RATE[0]
        self.exp_counter = 0
        self.exp_period = 1
        self.state = 2                       # 0 attack, 1 decay/sustain, 2 release
        self.hold_zero = True

    def write_control(self, v):
        g = v & 1
        if g and not self.gate:
            self.state = 0
            self.rate_period = self.RATE[self.attack]
            self.hold_zero = False
        elif self.gate and not g:
            self.state = 2
            self.rate_period = self.RATE[self.release]
        self.gate = g

    def write_ad(self, v):
        self.attack, self.decay = v >> 4, v & 15
        if self.state == 0:
            self.rate_period = self.RATE[self.attack]
        elif self.state == 1:
            self.rate_period = self.RATE[self.decay]

    def write_sr(self, v):
        self.sustain, self.release = v >> 4, v & 15
        if self.state == 2:
            self.rate_period = self.RATE[self.release]

    def clock(self, dt):
        rate_step = self.rate_period - self.rate_counter
        if rate_step <= 0:
            rate_step += 0x7FFF
        while dt > 0:
            if dt < rate_step:
                rc = self.rate_counter + dt
                if rc & 0x8000:
                    rc = (rc + 1) & 0x7FFF
                self.rate_counter = rc
                return
            self.rate_counter = 0
            dt -= rate_step
            state = self.state
            period = self.rate_period
            if self.hold_zero or (state == 1 and self.counter == self.sustain * 0x11):
                # The level cannot change: take this step and every further
                # whole step at once; only the exponential counter moves.
                k = dt // period
                dt -= k * period
                if state == 0:
                    self.exp_counter = 0
                else:
                    self.exp_counter = (self.exp_counter + 1 + k) % self.exp_period
                rate_step = period
                continue
            if state != 0:
                self.exp_counter += 1
                if self.exp_counter != self.exp_period:
                    rate_step = period
                    continue
            self.exp_counter = 0
            c = self.counter
            if state == 0:
                c = (c + 1) & 0xFF
                if c == 0xFF:
                    self.state = 1
                    self.rate_period = self.RATE[self.decay]
            elif state == 1:
                c -= 1
            else:
                c = (c - 1) & 0xFF
            self.counter = c
            if c == 0xFF:
                self.exp_period = 1
            elif c == 0x5D:
                self.exp_period = 2
            elif c == 0x36:
                self.exp_period = 4
            elif c == 0x1A:
                self.exp_period = 8
            elif c == 0x0E:
                self.exp_period = 16
            elif c == 0x06:
                self.exp_period = 30
            elif c == 0x00:
                self.exp_period = 1
                self.hold_zero = True
            rate_step = self.rate_period


class SidWave:
    """reSID 0.16 oscillator: 24-bit phase accumulator, 23-bit noise LFSR,
    test/sync/ring bits.  Single waveforms are exact; combined waveforms are
    the bitwise AND of their components (the analog 6581/8580 mixing tables
    are not reproduced)."""
    __slots__ = ("freq", "pw", "acc", "lfsr", "test", "sync", "ring", "waveform",
                 "msb_rising", "src", "dest", "track_noise")

    def __init__(self, track_noise=False):
        self.freq = self.pw = self.acc = 0
        self.lfsr = 0x7FFFF8
        self.test = self.sync = self.ring = self.waveform = 0
        self.msb_rising = False
        self.src = self.dest = None
        self.track_noise = track_noise

    def write_control(self, v):
        self.ring = v & 0x04
        self.sync = v & 0x02
        test = v & 0x08
        if test:
            self.acc = 0
            self.lfsr = 0
        elif self.test:
            self.lfsr = 0x7FFFF8
        self.test = test
        self.waveform = v >> 4

    def clock(self, dt):
        if self.test:
            self.msb_rising = False
            return
        prev = self.acc
        delta = dt * self.freq
        acc = (prev + delta) & 0xFFFFFF
        self.acc = acc
        self.msb_rising = (not (prev & 0x800000)) and (acc & 0x800000) != 0
        if delta and self.track_noise:
            lfsr = self.lfsr
            period = 0x100000
            while delta:
                if delta < period:
                    period = delta
                    if period <= 0x080000:
                        if ((acc - period) & 0x080000) or not (acc & 0x080000):
                            break
                    elif ((acc - period) & 0x080000) and not (acc & 0x080000):
                        break
                lfsr = ((lfsr << 1) & 0x7FFFFF) | (((lfsr >> 22) ^ (lfsr >> 17)) & 1)
                delta -= period
            self.lfsr = lfsr

    def output(self):
        w = self.waveform
        if not w:
            return 0
        if w & 8:
            if w != 8:
                return 0
            s = self.lfsr
            return (((s & 0x400000) >> 11) | ((s & 0x100000) >> 10) | ((s & 0x010000) >> 7)
                    | ((s & 0x002000) >> 5) | ((s & 0x000800) >> 4) | ((s & 0x000080) >> 1)
                    | ((s & 0x000010) << 1) | ((s & 0x000004) << 2))
        acc = self.acc
        out = 0xFFF
        if w & 1:
            msb = (acc ^ self.src.acc if self.ring else acc) & 0x800000
            out &= ((~acc if msb else acc) >> 11) & 0xFFF
        if w & 2:
            out &= acc >> 12
        if w & 4:
            out &= 0xFFF if (self.test or (acc >> 12) >= self.pw) else 0
        return out


def clock_oscillators(waves, dt):
    """Clock three coupled oscillators by dt cycles, splitting at every MSB
    rise that hard-syncs another voice (as reSID's delta clock does)."""
    while dt > 0:
        step = dt
        for w in waves:
            if w.freq and w.dest.sync and not w.test:
                acc = w.acc
                need = (0x1000000 if acc & 0x800000 else 0x800000) - acc
                n = -(-need // w.freq)
                if n < step:
                    step = n
        for w in waves:
            w.clock(step)
        for w in waves:
            if w.msb_rising and w.dest.sync and not (w.sync and w.src.msb_rising):
                w.dest.acc = 0
        dt -= step


_IDLE_SID = {}     # (model, power-on cycle) -> (cycle, untouched reSIDfp chip clocked to it)


class SidChip:
    """One SID: register-level tracking for the MIDI conversion (reSID 0.16
    style envelope/oscillator estimates), plus an exact reSIDfp digital chip
    that answers OSC3/ENV3 reads; paddle and write-only register reads use the
    same bus-value rules without it."""

    def __init__(self, base, machine=None, model="6581"):
        self.base = base
        self.m = machine
        self.reg = bytearray(0x20)
        self.gate = [0, 0, 0]
        self.trig = [0, 0, 0]
        self.trigcyc = [-1, -1, -1]
        self.vol_writes = 0
        self.env = [SidEnvelope() for _ in range(3)]
        self.wave = [SidWave(track_noise=(i == 2)) for i in range(3)]
        for i, w in enumerate(self.wave):
            w.src = self.wave[(i + 2) % 3]
            w.dest = self.wave[(i + 1) % 3]
        self.t = 0
        self.bus = 0            # SID data bus value and its remaining lifetime (reSIDfp rules)
        self.bus_ttl = 0
        self.bus_t = 0
        self.model = model
        self.fp = None          # residfp.SID, built on the first read
        self.fp_t0 = 0          # power-on cycle
        self.fp_t = 0           # cycles < fp_t are clocked into fp
        self.fp_log = []        # (cycle, register, value) while fp is None

    def _now(self):
        return self.m.now() if self.m is not None else self.t

    def advance(self, now):
        dt = now - self.t
        if dt <= 0:
            return
        self.t = now
        for e in self.env:
            e.clock(dt)
        clock_oscillators(self.wave, dt)

    def reset_frame(self):
        self.trig = [0, 0, 0]
        self.trigcyc = [-1, -1, -1]
        self.vol_writes = 0

    def write(self, r, v):
        now = self._now()
        if r < 0x15:
            vi, k = divmod(r, 7)
            if k != 2 and k != 3:
                self.advance(now)
            w = self.wave[vi]
            if k == 0:
                w.freq = (w.freq & 0xFF00) | v
            elif k == 1:
                w.freq = (w.freq & 0x00FF) | (v << 8)
            elif k == 2:
                w.pw = (w.pw & 0xF00) | v
            elif k == 3:
                w.pw = (w.pw & 0x0FF) | ((v & 0x0F) << 8)
            elif k == 4:
                w.write_control(v)
                self.env[vi].write_control(v)
                g = v & 1
                if g and not self.gate[vi]:
                    self.trig[vi] = 1
                    if self.trigcyc[vi] < 0:
                        self.trigcyc[vi] = now - self.m.frame_start if self.m is not None else 0
                self.gate[vi] = g
            elif k == 5:
                self.env[vi].write_ad(v)
            else:
                self.env[vi].write_sr(v)
        elif r == 0x18:
            self.vol_writes += 1
        self.reg[r] = v
        self.bus, self.bus_ttl, self.bus_t = v, self._model_ttl(), now
        if self.fp is not None:
            self._fp(now).write(r, v)
        else:
            self.fp_log.append((now, r, v))

    def read(self, r):
        now = self._now()
        if self.fp is not None or r == 0x1B or r == 0x1C:
            return self._fp(now).read(r)
        # Paddles and write-only registers only involve the bus value, which
        # needs no oscillator/envelope state: same rules as reSIDfp.
        self._age_bus(now)
        if r == 0x19 or r == 0x1A:
            self.bus, self.bus_ttl = 0xFF, self._model_ttl()    # no paddles connected
        else:
            self.bus_ttl //= 2
        return self.bus

    def _model_ttl(self):
        return residfp.BUS_TTL_8580 if self.model == "8580" else residfp.BUS_TTL_6581

    def _age_bus(self, now):
        if self.bus_ttl:
            self.bus_ttl -= now - self.bus_t
            if self.bus_ttl <= 0:
                self.bus, self.bus_ttl = 0, 0
        self.bus_t = now

    # ---- exact chip ----
    def set_model(self, model):
        self.model = model
        if self.fp is not None:
            self.fp.setChipModel(residfp.MOS8580 if model == "8580" else residfp.MOS6581)

    def _fp(self, now):
        """The reSIDfp chip clocked up to ``now``.  It is built on the first
        $D41B/$D41C read by replaying every write since power-on, so a tune
        that never reads OSC3/ENV3 never pays for cycle-exact clocking."""
        fp = self.fp
        if fp is None:
            t, fp = self._idle_chip(self.fp_log[0][0] if self.fp_log else now)
            self.fp = fp
            for wt, r, v in self.fp_log:
                if wt > t:
                    fp.clockDigital(wt - t)
                    t = wt
                fp.write(r, v)
            self.fp_log = []
            self.fp_t = t
            if now > self.fp_t:
                fp.clockDigital(now - self.fp_t)
                self.fp_t = now
            self._age_bus(now)
            fp.busValue, fp.busValueTtl = self.bus, self.bus_ttl    # includes bus-only reads so far
        if now > self.fp_t:
            fp.clockDigital(now - self.fp_t)
            self.fp_t = now
        return fp

    def _idle_chip(self, until):
        """(cycle, chip) for a SID powered on at fp_t0 and untouched until ``until``;
        the earliest such chip is kept per model and cloned."""
        key = (self.model, self.fp_t0)
        cached = _IDLE_SID.get(key)
        memo = self._table_memo()
        if cached is not None and cached[0] <= until:
            t, fp = cached[0], copy.deepcopy(cached[1], memo)
        else:
            t, fp = self.fp_t0, residfp.SID(residfp.MOS8580 if self.model == "8580" else residfp.MOS6581)
        if until > t:
            fp.clockDigital(until - t)
            t = until
        if cached is None or t < cached[0]:
            _IDLE_SID[key] = (t, copy.deepcopy(fp, memo))
        return t, fp

    @staticmethod
    def _table_memo():
        memo = {}
        for tables in residfp._PULLDOWN_CACHE.values():
            memo[id(tables)] = tables
            for table in tables:
                memo[id(table)] = table                  # shared, never copied
        return memo

    def save(self):
        return {"reg": bytes(self.reg), "model": self.model, "fp_t0": self.fp_t0, "fp_t": self.fp_t,
                "bus": (self.bus, self.bus_ttl, self.bus_t),
                "fp_log": list(self.fp_log), "fp": copy.deepcopy(self.fp, self._table_memo())}

    def load(self, st):
        self.reg[:] = st["reg"]
        self.bus, self.bus_ttl, self.bus_t = st["bus"]
        self.model, self.fp_t0, self.fp_t = st["model"], st["fp_t0"], st["fp_t"]
        self.fp_log = list(st["fp_log"])
        self.fp = copy.deepcopy(st["fp"], self._table_memo())

    def snapshot(self, now):
        self.advance(now)
        return (bytes(self.reg[:0x19]), tuple(self.trig), tuple(self.trigcyc),
                tuple(e.counter for e in self.env))


# ------------------------------------------------------------- VIC-II ---------
# First sprite-fetch cycle of sprites 0-7 as an offset in the line (VIC cycle
# number - 1); sprites 3-7 are fetched at the start of the following line.
SPRITE_FETCH_PAL = (57, 59, 61, 0, 2, 4, 6, 8)
SPRITE_FETCH_NTSC = (58, 60, 62, 64, 1, 3, 5, 7)    # 6567R8 (VICE cycle_tab_ntsc): pointer fetch cycles - 1


class Vic6569:
    """VIC-II raster interrupt and BA (bus request) for the CPU.

    Cycle n of the VIC-II documentation (1-based) is offset n-1 in the line.
    The raster counter changes at the first cycle of every line; the compare
    match for a line happens in that cycle (line 0: one cycle later), setting
    $D019 bit 0 whether or not the interrupt is enabled.  The interrupt output
    is $D019 & $D01A: it goes low in the match cycle, and changes one cycle
    after an acknowledge ($D019) or enable ($D01A) write.

    BA is low -- the CPU halts at its next read -- in cycles 12-54 of a bad
    line (raster $30-$F7, low three bits = YSCROLL, DEN set during some cycle
    of line $30 of the frame) and, for every sprite whose display is on, from
    three cycles before its two data-fetch cycles until the end of them.  The
    sprite display turns on in cycle 55 or 56 of a line whose low 8 bits match
    the enabled sprite's Y (MCBASE = 0, expansion flip-flop set); the flip-flop
    toggles in cycle 55 when Y-expanded; MCBASE grows by 2 in cycle 15 and by 1
    in cycle 16 while the flip-flop is set, and the display ends in cycle 16
    once MCBASE is 63.
    """

    def __init__(self, machine):
        self.m = machine
        self.cpl, self.lines = machine.cpl, machine.lines
        self.frame = self.cpl * self.lines
        self.reg = bytearray(0x40)
        self.compare = 0
        self.flags = 0
        self.mask = 0
        self.irq = False
        self.t = 0                                  # cycles < t processed
        self.fetch = SPRITE_FETCH_PAL if self.cpl == PAL_CPL else SPRITE_FETCH_NTSC
        self.spr_disp = [False] * 8
        self.spr_mcbase = [0] * 8
        self.spr_ff = [False] * 8
        self.spr_t = 0                              # sprite events at cycles < spr_t processed
        self.den_t = 0                              # DEN history accounted for cycles < den_t
        self.den_frame = -1                         # latest frame whose line $30 saw DEN
        self.ba_free_until = 0                      # BA is high at every cycle below this

    def save(self):
        return {k: copy.deepcopy(v) for k, v in self.__dict__.items() if k != "m"}

    def load(self, state):
        for k, v in state.items():
            setattr(self, k, copy.deepcopy(v))

    def line(self, now):
        return (now // self.cpl) % self.lines

    def next_match(self, t):
        """First cycle >= t in which the raster reaches the compare line, or None."""
        if self.compare >= self.lines:
            return None
        u = (t // self.frame) * self.frame + self.compare * self.cpl + (1 if self.compare == 0 else 0)
        return u if u >= t else u + self.frame

    def sync(self, t):
        """Process cycles < t."""
        if t <= self.t:
            return
        u = self.next_match(self.t)
        self.t = t
        if u is not None and u < t:
            self.flags |= 0x01
            self._update_irq(u)

    def _update_irq(self, cycle):
        low = bool(self.flags & self.mask & 0x0F)
        if low != self.irq:
            self.irq = low
            self.m.irq_changed("vic", low, cycle)

    def next_irq_cycle(self):
        if self.irq or not (self.mask & 0x01):
            return None
        return self.next_match(self.t)

    # ---- BA ----
    def _note_den(self, t):
        """Account DEN for cycles < t with the current $D011."""
        if t <= self.den_t:
            return
        if self.reg[0x11] & 0x10:
            k = (t - 1 - 0x30 * self.cpl) // self.frame      # latest frame whose line $30 began before t
            if k >= 0 and k * self.frame + 0x31 * self.cpl > self.den_t and k > self.den_frame:
                self.den_frame = k
        self.den_t = t

    def _spr_sync(self, t):
        """Run the sprite display events of cycles < t."""
        st = self.spr_t
        if st >= t:
            return
        reg, cpl, disp = self.reg, self.cpl, self.spr_disp
        if not (reg[0x15] or any(disp)):
            self.spr_t = t
            return
        dma_a, dma_b = (54, 55) if cpl == PAL_CPL else (55, 56)   # DMA checks: cycles 55/56 (PAL), 56/57 (NTSC)
        ls = st - st % cpl
        while ls < t:
            for off in (14, 15, dma_a, dma_b):
                e = ls + off
                if e < st:
                    continue
                if e >= t:
                    break
                mc, ff = self.spr_mcbase, self.spr_ff
                if off == 14:
                    for n in range(8):
                        if disp[n] and ff[n]:
                            mc[n] += 2
                elif off == 15:
                    for n in range(8):
                        if disp[n]:
                            if ff[n]:
                                mc[n] += 1
                            if mc[n] == 63:
                                disp[n] = False
                else:
                    raster = ((e % self.frame) // cpl) & 0xFF
                    enabled = reg[0x15]
                    for n in range(8):
                        if enabled >> n & 1 and not disp[n] and reg[1 + 2 * n] == raster:
                            disp[n], mc[n], ff[n] = True, 0, True
                    if off == 55:                      # cycle 56: Y expansion, after the DMA check
                        ye = reg[0x17]
                        for n in range(8):
                            if ye >> n & 1 and disp[n]:
                                ff[n] = not ff[n]
            if not (reg[0x15] or any(disp)):
                break
            ls += cpl
        self.spr_t = t

    def _ba_low(self, c):
        """BA level in cycle c (sprite events up to c must be processed)."""
        cpl = self.cpl
        off = c % cpl
        if 11 <= off <= 53:
            line = (c % self.frame) // cpl
            if 0x30 <= line <= 0xF7 and (line & 7) == (self.reg[0x11] & 7):
                self._note_den(c + 1)
                if self.den_frame == c // self.frame:
                    return True
        disp = self.spr_disp
        for n in range(8):
            if disp[n] and ((off - self.fetch[n]) % cpl <= 1 or (off - self.fetch[n]) % cpl >= cpl - 3):
                return True
        return False

    def _ba_free_from(self, c):
        """A cycle > c such that BA stays high from c up to it (conservative)."""
        cpl = self.cpl
        off = c % cpl
        ls = c - off
        until = NEVER
        if self.reg[0x15] or any(self.spr_disp):
            if off >= 54 or off <= 9:
                return c + 1
            until = ls + 54
        self._note_den(c + 1)
        if self.reg[0x11] & 0x10 or self.den_frame == c // self.frame:
            ys = self.reg[0x11] & 7
            if off > 53:
                ls += cpl
            for _ in range(8):
                line = (ls % self.frame) // cpl
                if 0x30 <= line <= 0xF7 and (line & 7) == ys:
                    until = min(until, max(c + 1, ls + 11))
                    break
                ls += cpl
            else:
                until = min(until, ls)
        return until

    def rdy(self, t):
        """CPU hook: first cycle >= t (CPU clock) at which a read can happen."""
        base = self.m.time_base
        c = base + t
        if c < self.ba_free_until:
            self.m.cpu.rdy_until = self.ba_free_until - base
            return t
        self._spr_sync(c + 1)
        while self._ba_low(c):
            c += 1
            self._spr_sync(c + 1)
        self.ba_free_until = self._ba_free_from(c)
        self.m.cpu.rdy_until = self.ba_free_until - base
        return c - base

    def next_ba_cycle(self, c):
        """Absolute cycle before which BA certainly stays high (for time skipping)."""
        if c < self.ba_free_until:
            return self.ba_free_until
        self._spr_sync(c + 1)
        return c if self._ba_low(c) else self._ba_free_from(c)

    def read(self, r):
        now = self.m.now()
        self.sync(now + 1)
        self.m.predict_dirty = True
        if r == 0x11:
            return (self.reg[0x11] & 0x7F) | (0x80 if self.line(now) & 0x100 else 0)
        if r == 0x12:
            return self.line(now) & 0xFF
        if r == 0x19:
            return self.flags | 0x70 | (0x80 if self.irq else 0)
        if r == 0x1A:
            return self.mask | 0xF0
        if r == 0x1E or r == 0x1F:
            return 0
        if r >= 0x2F:
            return 0xFF
        if r == 0x16:
            return self.reg[r] | 0xC0                  # unused bits read as 1 (VICE vicii-mem.c)
        if r == 0x18:
            return self.reg[r] | 0x01
        if r >= 0x20:
            return self.reg[r] | 0xF0
        return self.reg[r]

    def write(self, r, v):
        now = self.m.now()
        self.sync(now + 1)
        self.m.predict_dirty = True
        if r == 0x11 or r == 0x15 or r == 0x17 or (r < 0x10 and r & 1):
            self._spr_sync(now + 1)                  # this cycle's events see the old value
            self._note_den(now)
            if r == 0x11 and v & 0x10:
                line = (now % self.frame) // self.cpl
                if line == 0x30:
                    self.den_frame = max(self.den_frame, now // self.frame)
            self.ba_free_until = 0
            self.m.cpu.rdy_until = 0
            if r == 0x17:
                # Clearing Y expansion sets the flip-flop again; in cycle 15 the
                # sprite crunch mixes MC and MCBASE (VICE vicii-mem.c d017_store).
                off = now % self.cpl
                for n in range(8):
                    if not v >> n & 1 and not self.spr_ff[n]:
                        if off == 14 and self.spr_disp[n]:
                            base = self.spr_mcbase[n]
                            mc = (base + 3) & 0x3F
                            self.spr_mcbase[n] = ((0x2A & (base & mc)) | (0x15 & (base | mc))) - 1
                        self.spr_ff[n] = True
        if r == 0x11 or r == 0x12:
            if r == 0x11:
                self.reg[0x11] = v
                new = (self.compare & 0xFF) | ((v & 0x80) << 1)
            else:
                new = (self.compare & 0x100) | v
            if new != self.compare:
                self.compare = new
                if self.line(now) == new:
                    self.flags |= 0x01
                    self._update_irq(now + 1)
        elif r == 0x19:
            self.flags &= ~v & 0x0F
            self._update_irq(now + 1)
        elif r == 0x1A:
            self.mask = v & 0x0F
            # enabling a pending source pulls the line in the write cycle, a
            # release shows one cycle later (VICE vicii_irq_set_line + IRQPEND)
            self._update_irq(now if self.flags & self.mask & 0x0F else now + 1)
        else:
            self.reg[r] = v


# ---------------------------------------------------------------- C64 env -----
class C64:
    _CPU_FIELDS = ("a", "x", "y", "sp", "pc", "C", "Z", "I", "D", "B", "V", "N", "cycles", "I_poll",
                   "delay_int", "last_op")

    def __init__(self, sid_bases=(), pal=True):
        self.ram = bytearray(0x10000)
        self.ram[0x00], self.ram[0x01] = 0x2F, 0x37      # 6510 port power-on state
        self.kernal, self.basic, self.chargen = KERNAL, BASIC, CHARGEN
        self.pal = pal
        self.cpl, self.lines = (PAL_CPL, PAL_LINES) if pal else (NTSC_CPL, NTSC_LINES)
        self.frame = self.cpl * self.lines
        self.time_base = 0
        self.frame_start = 0
        self.irq_count = self.nmi_count = 0
        self.debug_cart = False                          # testbench: $D7FF write ends the run
        self.idle_trap = False                           # IDLE_PC means "driver idle" (SID player only)
        # 6510 port: bits 3, 6 and 7 keep their last output level as inputs
        # (6 and 7 are unconnected and fade after PORT_FADE_CYCLES).
        self.port_last = 0x37
        self.port_fade = {0x40: None, 0x80: None}
        # Interrupt lines: cycle each source last went low / high.
        self.low_since = {"cia1": None, "vic": None}
        self.high_since = {"cia1": -1, "vic": -1}
        self.nmi_edge = None
        self.next_line_event = 0
        self.predict_dirty = True
        self._frame_irq = None
        self.cpu = CPU6502(self.read, self.write)
        self.cia1 = Cia6526(lambda low, cycle: self.irq_changed("cia1", low, cycle))
        self.cia2 = Cia6526(lambda low, cycle: self.irq_changed("cia2", low, cycle))
        for cia in (self.cia1, self.cia2):
            cia.set_clock(*((985248, 50) if pal else (1022730, 60)))
        self.cia1.on_pb = self._cia1_port_b
        self.vic = Vic6569(self)
        self.cpu.rdy = self.vic.rdy
        self.chips = [SidChip(0xD400, self)] + [SidChip(b, self) for b in sid_bases]
        self.sidmap = {}
        for chip in self.chips[1:]:
            for r in range(0x20):
                self.sidmap[chip.base + r] = (chip, r)
        for a in range(0xD400, 0xD800):                  # SID #1 mirrors
            self.sidmap.setdefault(a, (self.chips[0], (a - 0xD400) & 0x1F))

    # ---- time ----
    def now(self):
        return self.time_base + self.cpu.cycles

    @property
    def cia_latch(self):
        return self.cia1.ta.latch

    # ---- interrupt lines ----
    def irq_changed(self, source, low, cycle):
        self.predict_dirty = True
        if source == "cia2":
            if low:
                self.nmi_edge = cycle
            return
        if low:
            self.low_since[source] = cycle
        else:
            self.high_since[source] = cycle

    def irq_low_at(self, cycle):
        for source in ("cia1", "vic"):
            lo = self.low_since[source]
            if lo is not None and lo <= cycle:
                hi = self.high_since[source]
                if hi <= lo or cycle < hi:
                    return True
        return False

    def _sync_lines(self, t):
        self.cia1.sync(t)
        self.cia2.sync(t)
        self.vic.sync(t)
        nxt = NEVER
        for u in (self.cia1.next_irq_cycle(), self.cia2.next_irq_cycle(), self.vic.next_irq_cycle()):
            if u is not None and u < nxt:
                nxt = u
        for source in ("cia1", "vic"):                  # a low edge already scheduled ahead of t
            lo = self.low_since[source]
            if lo is not None and lo >= t and self.high_since[source] <= lo and lo < nxt:
                nxt = lo
        self.next_line_event = nxt
        self.predict_dirty = False

    def _pending(self, clk, i_poll, delayed, irq_clk=None):
        """CPU hook: interrupt to take before the opcode fetch at cycle ``clk``.

        Lines are sampled two cycles before the fetch (three after a taken
        branch that did not cross a page, four right after an interrupt
        sequence: ``delayed`` is False, True or 2)."""
        lim = clk - 2 - int(delayed)
        if self.predict_dirty or self.next_line_event <= lim:
            self._sync_lines(lim + 1)
        if self.nmi_edge is not None and self.nmi_edge <= lim:
            self.nmi_edge = None
            self.nmi_count += 1
            return "nmi"
        if not i_poll and self.irq_low_at(lim if irq_clk is None else irq_clk - 2 - int(delayed)):
            if self._frame_irq is not None:
                self._frame_irq(clk)
            self.irq_count += 1
            return "irq"
        return None

    def _nmi_hijack(self, t):
        lim = t - 2
        if self.predict_dirty or self.next_line_event <= lim:
            self._sync_lines(lim + 1)
        if self.nmi_edge is not None and self.nmi_edge <= lim:
            self.nmi_edge = None
            self.nmi_count += 1
            return True
        return False

    def _next_interrupt_fetch(self, now, i_poll, delayed):
        """Earliest opcode-fetch cycle >= now at which an interrupt could be taken."""
        extra = 2 + int(delayed)
        if self.predict_dirty:
            self._sync_lines(max(0, now - extra + 1))
        if self.nmi_edge is not None:
            return max(now, self.nmi_edge + extra)
        if not i_poll and self.irq_low_at(now - extra):
            return now
        if self.next_line_event >= NEVER:
            return NEVER
        return max(now, self.next_line_event + extra)

    # ---- memory map ----
    def read(self, a):
        if 1 < a < 0xA000:
            return self.ram[a]
        ram = self.ram
        if a < 2:
            return ram[0] if a == 0 else self._port_read()
        p = (ram[1] | ~ram[0]) & 7
        if a < 0xC000:                                  # BASIC ROM / RAM
            return self.basic[a - 0xA000] if (p & 3) == 3 and self.basic else ram[a]
        if a < 0xD000:                                  # $C000-$CFFF: always RAM
            return ram[a]
        if a < 0xE000:                                  # I/O / CHARGEN / RAM
            if (p & 3) == 0:
                return ram[a]
            if p & 4:
                return self._io_read(a)
            return self.chargen[a - 0xD000] if self.chargen else ram[a]
        return self.kernal[a - 0xE000] if (p & 2) and self.kernal else ram[a]

    def write(self, a, v):
        v &= 0xFF
        if a < 2:
            self._port_write(a, v)
            return
        if 0xD000 <= a < 0xE000:
            p = (self.ram[1] | ~self.ram[0]) & 7
            if (p & 3) and (p & 4):
                self._io_write(a, v)
                return
        self.ram[a] = v

    # ---- 6510 I/O port ----
    def _port_fade_now(self, now):
        for bit, at in self.port_fade.items():
            if at is not None and now >= at:
                self.port_last &= ~bit
                self.port_fade[bit] = None

    def _port_read(self):
        now = self.now()
        self._port_fade_now(now)
        ddr, data = self.ram[0], self.ram[1]
        v = (self.port_last & 0xC8) | ((~ddr | data) & 0x37)
        if not ddr & 0x20:
            v &= 0xDF                                   # cassette motor line pulls bit 5 low
        return v

    def _port_write(self, a, v):
        now = self.now()
        self._port_fade_now(now)
        old_ddr = self.ram[0]
        self.ram[a] = v
        if isinstance(self.vic, VicIISC):
            self.vic.note_port_write(a)
        ddr, data = self.ram[0], self.ram[1]
        self.port_last = (~ddr & self.port_last) | (ddr & data)
        for bit in (0x40, 0x80):
            if ddr & bit:
                self.port_fade[bit] = None
            elif old_ddr & bit and self.port_last & bit:
                self.port_fade[bit] = now + PORT_FADE_CYCLES

    def _io_read(self, a):
        if a < 0xD400:
            r = (a - 0xD000) & 0x3F
            if r in (0x13, 0x14, 0x1E, 0x1F) and not isinstance(self.vic, VicIISC):
                self.use_vicii_sc()                     # light pen / collisions need the full chip
            return self.vic.read(r)
        if a < 0xD800:
            chip, r = self.sidmap[a]
            return chip.read(r)
        if a < 0xDC00:
            # Colour RAM has 4 bits; the upper nibble is the open bus, i.e. the
            # VIC-II's phi1 fetch, which only the full chip knows.  The KERNAL
            # screen editor copies colour RAM without using the upper nibble, so
            # its reads do not need the full chip (BASIC programs stay fast).
            if not isinstance(self.vic, VicIISC) and not self._pc_in_kernal_rom():
                self.use_vicii_sc()
            if isinstance(self.vic, VicIISC):
                return (self.ram[a] & 0x0F) | (self.vic.read_phi1() & 0xF0)
            return self.ram[a]
        if a < 0xDD00:
            self.predict_dirty = True
            return self.cia1.read(a & 0x0F, self.now())
        if a < 0xDE00:
            self.predict_dirty = True
            return self.cia2.read(a & 0x0F, self.now())
        hit = self.sidmap.get(a)
        if hit:
            return hit[0].read(hit[1])
        if not isinstance(self.vic, VicIISC):
            self.use_vicii_sc()                         # unmapped I/O reads the open bus
        return self.vic.read_phi1()

    def _io_write(self, a, v):
        if a < 0xD400:
            r = (a - 0xD000) & 0x3F
            if r == 0x1A and v & 0x0E and not isinstance(self.vic, VicIISC):
                self.use_vicii_sc()                     # collision / light pen interrupts enabled
            self.vic.write(r, v)
        elif a < 0xD800:
            if a == 0xD7FF and self.debug_cart:
                raise DebugCartExit(v)
            chip, r = self.sidmap[a]
            chip.write(r, v)
        elif a < 0xDC00:
            self.ram[a] = v
        elif a < 0xDD00:
            self.predict_dirty = True
            self.cia1.write(a & 0x0F, v, self.now())
        elif a < 0xDE00:
            self.predict_dirty = True
            self.cia2.write(a & 0x0F, v, self.now())
        else:
            hit = self.sidmap.get(a)
            if hit:
                hit[0].write(hit[1], v)

    # ---- environments ----
    def setup_psid_environment(self):
        """Minimal KERNAL-like state for called PSID players (no ROM boot)."""
        ram = self.ram
        ram[0x00], ram[0x01] = 0x2F, 0x37
        ram[0x02A6] = 1 if self.pal else 0
        ram[0x0314:0x031A] = bytes([0x31, 0xEA, 0x66, 0xFE, 0x47, 0xFE])  # CINV/CBINV/NMINV
        latch = PAL_CIA_LATCH if self.pal else NTSC_CIA_LATCH
        t = self.now()
        self.cia1.write(0x04, latch & 0xFF, t)
        self.cia1.write(0x05, latch >> 8, t)
        self.cia1.write(0x0D, 0x81, t)
        self.cia1.write(0x0E, 0x11, t)
        self.vic.reg[0x11] = 0x1B
        self.predict_dirty = True

    def set_sid_model(self, *models):
        """Chip model per SID ("6581" / "8580"); the last one given applies to the rest."""
        for i, chip in enumerate(self.chips):
            chip.set_model(models[min(i, len(models) - 1)])

    def use_vicii_sc(self, model=None):
        """Switch to the cycle-exact VIC-II (VICE viciisc port) from now on.

        ``model``: "6569" (default PAL), "8565", "6567" (default NTSC), "8562"
        or "6567R56A"."""
        if isinstance(self.vic, VicIISC):
            return
        self.vic = VicIISC.from_fast(self, self.vic, model or ("6569" if self.pal else "6567"))
        self.cpu.rdy = self.vic.rdy
        self.cpu.rdy_until = 0
        self.predict_dirty = True

    def _pc_in_kernal_rom(self):
        """The CPU is executing the KERNAL ROM (not RAM under it)."""
        return (self.cpu.pc >= 0xE000 and self.kernal is not None
                and ((self.ram[1] | ~self.ram[0]) & 3) >= 2)

    def _cia1_port_b(self, clk, byte):
        if not byte & 0x10 and not isinstance(self.vic, VicIISC):
            self.use_vicii_sc()                         # light pen line pulled low
        if isinstance(self.vic, VicIISC):
            self.vic.set_light_pen(clk, 0 if byte & 0x10 else 1)   # control port 1 fire = light pen

    def set_cia_model(self, model):
        """"6526" (old CIA, breadbin) or "6526A" (new CIA, C64C)."""
        for cia in (self.cia1, self.cia2):
            cia.model = model

    def load_sid(self, sid):
        self.setup_psid_environment()
        if hasattr(sid, "place"):
            sid.place(self.ram)
        else:
            self.ram[sid.load:sid.end] = sid.data

    def cold_reset(self):
        """Run the real KERNAL/BASIC cold start until BASIC waits for a key."""
        cpu = self.cpu
        self.ram[0x00], self.ram[0x01] = 0x2F, 0x37
        cpu.sp, cpu.I, cpu.D, cpu.cycles, cpu.I_poll = 0xFD, 1, 0, 0, 1
        cpu.pc = self.read(0xFFFC) | self.read(0xFFFD) << 8
        self.predict_dirty = True
        return self.run(10 * 1000000, stop_pc=READY_LOOP)

    def save_state(self):
        return {"ram": bytes(self.ram),
                "cpu": {k: getattr(self.cpu, k) for k in self._CPU_FIELDS},
                "cia1": self.cia1.save(), "cia2": self.cia2.save(), "vic": self.vic.save(),
                "sid": [chip.save() for chip in self.chips],
                "lines": (dict(self.low_since), dict(self.high_since), self.nmi_edge)}

    def restore_state(self, st):
        self.ram[:] = st["ram"]
        for k, v in st["cpu"].items():
            setattr(self.cpu, k, v)
        self.cia1.load(st["cia1"])
        self.cia2.load(st["cia2"])
        if ("regs" in st["vic"]) != isinstance(self.vic, VicIISC):
            self.vic = VicIISC(self, st["vic"]["model"]) if "regs" in st["vic"] else Vic6569(self)
            self.cpu.rdy = self.vic.rdy
        self.vic.load(st["vic"])
        for chip, chip_state in zip(self.chips, st["sid"]):
            chip.load(chip_state)
        low, high, edge = st["lines"]
        self.low_since, self.high_since, self.nmi_edge = dict(low), dict(high), edge
        for chip in self.chips:
            chip.t = self.cpu.cycles
        self.time_base = 0
        self.cpu.rdy_until = 0
        self.predict_dirty = True

    def begin_frame(self, t):
        """Called-player mode: a new call starts at absolute cycle t."""
        self.cpu.rdy_until = 0
        self.time_base = t
        self.frame_start = t
        for chip in self.chips:
            chip.reset_frame()

    def start_frame(self, now):
        """Continuous mode: a new capture frame starts at cycle ``now``."""
        self.frame_start = now
        for chip in self.chips:
            chip.reset_frame()

    def snapshot(self):
        now = self.now()
        return tuple(chip.snapshot(now) for chip in self.chips), self.chips[0].vol_writes

    # ---- continuous execution ----
    def run(self, end, stop_pc=None, on_irq=None):
        """Execute until cycle ``end``, ``stop_pc`` or a CPU jam -> 'time' | 'stop' | 'jam'.

        While the CPU sits at IDLE_PC, or in an instruction that jumps to
        itself, only an interrupt can change anything; time then skips to the
        first opcode fetch (on the loop's instruction boundaries) at which a
        line change could be recognised."""
        cpu = self.cpu
        self._frame_irq = on_irq
        cpu.pending = self._pending
        cpu.nmi_hijack = self._nmi_hijack
        step = cpu.step
        idle_pc, loop_len = -1, 0
        try:
            while True:
                now = cpu.cycles
                if now >= end:
                    return "time"
                pc = cpu.pc
                if pc == stop_pc:
                    return "stop"
                if pc == IDLE_PC and self.idle_trap:
                    kind = self._pending(now, cpu.I_poll, False)
                    if kind is not None:
                        cpu._interrupt(kind == "nmi")
                        idle_pc = -1
                        continue
                    target = self._next_interrupt_fetch(now, cpu.I_poll, False)
                    cpu.cycles = min(end, target) if target > now else now + 1
                    continue
                if pc == idle_pc:
                    target = self._next_interrupt_fetch(now, cpu.I_poll, cpu.delay_int)
                    target = min(target, self.vic.next_ba_cycle(self.time_base + now) - self.time_base)
                    if target > now + loop_len:
                        k = (min(end, target) - now) // loop_len
                        if k > 0:
                            cpu.cycles = now + k * loop_len
                            continue
                before = cpu.cycles
                if not step():
                    return "jam"
                if cpu.pc == pc:
                    idle_pc, loop_len = pc, cpu.cycles - before
                else:
                    idle_pc = -1
        finally:
            cpu.pending = None
            cpu.nmi_hijack = None

    def call_subroutine(self, addr, limit=10 * 1000000):
        """JSR to ``addr`` from the current context in continuous mode."""
        cpu = self.cpu
        saved = cpu.pc, cpu.sp, self.idle_trap
        cpu._push(0x00)
        cpu._push(0x00)
        cpu.pc = addr
        self.idle_trap = True
        reason = self.run(cpu.cycles + limit, stop_pc=IDLE_PC)
        cpu.pc, cpu.sp, self.idle_trap = saved
        return reason


_RESET_STATES = {}


def cold_reset_state(pal):
    """Machine state after a real KERNAL/BASIC cold start (cached per video standard)."""
    st = _RESET_STATES.get(pal)
    if st is None:
        c = C64((), pal)
        if not (c.kernal and c.basic):
            raise RuntimeError("roms/kernal.bin and roms/basic.bin are required")
        if c.cold_reset() != "stop":
            raise RuntimeError("KERNAL cold start did not reach READY")
        st = _RESET_STATES[pal] = c.save_state()
    return st


# ---------------------------------------------------------- MIDI writer --------
def vlq(n):
    o = bytearray([n & 0x7F])
    n >>= 7
    while n:
        o.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(o)


class Trk:
    def __init__(self, name=""):
        self.ev = []
        if name:
            self.meta(0, 0x03, name)

    def _a(self, t, b, p=1):
        self.ev.append((max(0, t), p, bytes(b)))

    def meta(self, t, k, d):
        if isinstance(d, str):
            d = d.encode("latin1", "replace")
        self._a(t, [0xFF, k] + list(vlq(len(d))) + list(d), 0)

    def tempo(self, t, us): self.meta(t, 0x51, bytes([(us >> 16) & 255, (us >> 8) & 255, us & 255]))
    def marker(self, t, s): self.meta(t, 0x06, s)
    def cc(self, t, ch, n, v): self._a(t, [0xB0 | ch, n & 0x7F, max(0, min(127, int(v)))], 0)
    def prog(self, t, ch, p): self._a(t, [0xC0 | ch, p & 0x7F], 0)

    def bend(self, t, ch, val):
        val = max(0, min(16383, val))
        self._a(t, [0xE0 | ch, val & 0x7F, (val >> 7) & 0x7F], 0)

    def on(self, t, ch, n, v): self._a(t, [0x90 | ch, n & 0x7F, max(1, min(127, v))], 2)
    def off(self, t, ch, n): self._a(t, [0x80 | ch, n & 0x7F, 0], 1)

    def render(self):
        ev = sorted(self.ev, key=lambda e: (e[0], e[1]))
        d = bytearray()
        last = 0
        for t, _, b in ev:
            d += vlq(t - last) + b
            last = t
        d += vlq(0) + bytes([0xFF, 0x2F, 0x00])
        return b"MTrk" + struct.pack(">I", len(d)) + bytes(d)


def write_smf(path, ppq, trks):
    with open(path, "wb") as fh:
        fh.write(b"MThd" + struct.pack(">IHHH", 6, 1, len(trks), ppq) + b"".join(t.render() for t in trks))


# ----------------------------------------------------- helpers / detection ----
def hz_exact(hz): return 69 + 12 * math.log2(hz / 440.0) if hz > 0 else None
def wave_code(w): return (1 if w & 0x10 else 0) | (2 if w & 0x20 else 0) | (4 if w & 0x40 else 0) | (8 if w & 0x80 else 0)


def wave_prog(w):
    if w & 0x80 and not w & 0x70: return None      # noise
    if w & 0x40 and w & 0x20: return 81            # pulse+saw
    if w & 0x40: return 80                         # pulse  -> square lead
    if w & 0x20: return 81                         # saw    -> saw lead
    if w & 0x10: return 89                         # tri    -> warm pad
    return 80


class Env:
    """Frame-stepped ADSR estimate, used only when a capture carries no
    emulated envelope levels (hand-built frame lists)."""
    RATE = [9, 32, 63, 95, 149, 220, 267, 313, 392, 977, 1954, 3126, 3907, 11720, 19532, 31251]
    EXP = {0xFF: 1, 0x5D: 2, 0x36: 4, 0x1A: 8, 0x0E: 16, 0x06: 30, 0x00: 1}

    def __init__(self):
        self.env = 0; self.state = 2; self.rc = 0; self.ec = 0; self.ep = 1
        self.hold = True; self.gate = 0; self.ad = 0; self.sr = 0

    def trigger(self):
        self.state = 0; self.hold = False; self.gate = 1

    def set_gate(self, g):
        if g and not self.gate: self.trigger()
        elif not g and self.gate: self.state = 2
        self.gate = g

    def _rate(self):
        return self.RATE[self.ad >> 4] if self.state == 0 else \
               self.RATE[self.ad & 15] if self.state == 1 else self.RATE[self.sr & 15]

    def _step(self):
        if self.state == 0:
            self.env = (self.env + 1) & 0xFF
            if self.env == 0xFF: self.state = 1
        else:
            self.ec += 1
            if self.ec >= self.ep:
                self.ec = 0
                if not self.hold and self.env > 0: self.env -= 1
        if self.env in self.EXP: self.ep = self.EXP[self.env]
        if self.env == 0: self.hold = True

    def _frozen(self):
        sus = (self.sr >> 4) * 0x11
        return (self.state == 1 and self.env <= sus) or (self.env == 0 and self.state == 2)

    def advance(self, cycles):
        if self._frozen():
            return
        rp = self._rate(); self.rc += cycles
        guard = self.rc // self.RATE[0] + 2
        g = 0
        while self.rc >= rp and g < guard:
            self.rc -= rp; self._step(); rp = self._rate(); g += 1
            if self._frozen(): break


def compute_env(frames, getregs, gettrig, fcyc):
    out = [[], [], []]; es = [Env(), Env(), Env()]
    for f in range(len(frames)):
        regs = getregs(f); trig = gettrig(f); pf = fcyc[f + 1] - fcyc[f]
        for v in range(3):
            o = 7 * v; e = es[v]; e.ad = regs[o + 5]; e.sr = regs[o + 6]
            gate = regs[o + 4] & 1
            if trig[v]:
                e.trigger()
                if not gate:
                    e.advance(pf); e.set_gate(0); out[v].append(e.env)
                    continue
            e.set_gate(gate); e.advance(pf); out[v].append(e.env)
    return out


def _loop_sig(f):
    snap = f[0][0]
    regs, trig = snap[0], snap[1]
    return (regs[0], regs[1], regs[4] & 1, regs[11] & 1, regs[18] & 1, trig, f[1] > 4)


def find_loop(frames, min_period):
    """Detect the song loop -> (start_frame, period_frames) or None.

    Hashes a phase-robust musical signature (bass frequency, gate bits,
    trigger pattern, digi activity): free-running filter/PWM phases never
    repeat byte-for-byte, the row-locked note data does.  The period is the
    smallest one whose tail repeats; the start is found by extending the
    match backwards, so intros are kept.
    """
    N = len(frames)
    win = min(512, N // 3)
    if win < 8 or N - win < min_period:
        return None
    H = [hash(_loop_sig(f)) for f in frames]
    tail = H[N - win:]
    if len(set(tail)) < 2:
        return None
    for P in range(min_period, N - win + 1):
        if H[N - win - P:N - P] == tail:
            s = N - win - P
            while s > 0 and H[s - 1] == H[s - 1 + P]:
                s -= 1
            return s, P
    return None


# ------------------------------------------------------------ conversion ------
VOICE_CH = ((0, 1, 2), (4, 5, 6), (10, 11, 12))
FILTER_CH = (3, 7, 13)
LABELS = "ABC"


def chip_active(frames, k):
    first = frames[0][0][k][0]
    return any(first) or any(f[0][k][0] != first for f in frames)


def convert(sid, frames, fcyc, bpm, drum_voice=None, bend=True, do_cc=True, loop=None):
    if bpm <= 0:
        raise ValueError("bpm must be positive")
    if isinstance(loop, int):
        loop = (0, loop)
    PPQ = 960; k_tick = PPQ * bpm / (60.0 * sid.clock)
    tick = lambda c: int(round(c * k_tick))
    pf = lambda f: fcyc[f + 1] - fcyc[f]
    N = len(frames)
    meta = Trk(sid.name or "SID tune")
    meta.tempo(0, int(round(60_000_000 / bpm))); meta.meta(0, 0x58, bytes([4, 2, 24, 8]))
    if sid.release: meta.meta(0, 0x02, sid.release)
    for line in ("name: %s" % sid.name, "author: %s" % sid.author, "released: %s" % sid.release,
                 "chip: %s  %s %dHz" % (sid.model, "PAL" if sid.pal else "NTSC", sid.rate),
                 "format: %s v%d  songs %d" % (sid.magic.decode(), sid.version, sid.songs),
                 "init $%04X  play $%04X  load $%04X" % (sid.init, sid.play, sid.load),
                 "converted by sid2midi.py %s" % VERSION):
        meta.meta(0, 0x01, line)
    if sid.extra_sids:
        meta.meta(0, 0x01, "extra SIDs: " + " ".join("$%04X" % a for a in sid.extra_sids))
    meta.marker(0, "song start")
    if loop:
        s, P = loop
        meta.marker(tick(fcyc[s]), "loop start")
        meta.marker(tick(fcyc[min(s + P, N)]), "loop end")
    dtr = Trk("Drums"); dtr.cc(0, 9, 7, 110)
    nchips = len(frames[0][0]) if frames else 1
    active = [k for k in range(nchips) if k == 0 or chip_active(frames, k)]

    def render_sid(tracks, k, envs):
        ch0 = VOICE_CH[k]
        cur = [None] * 3; basev = [0] * 3; lastcc = [{} for _ in range(3)]
        lastbend = [8192] * 3; lastprog = [None] * 3

        def velocity(v, f, sr, master):
            peak = max(envs[v][f:min(f + 4, N)] or [envs[v][f]])
            load = 0.55 * peak + 0.45 * ((sr >> 4) * 0x11)
            return max(8, min(127, int(18 + (load / 255.0) * ((master + 1) / 16.0) * 108)))

        for f in range(N):
            snap = frames[f][0][k]
            regs, trig, tcyc = snap[0], snap[1], snap[2]
            cyc0 = fcyc[f]; mvol = regs[24] & 15
            for v in range(3):
                tr = tracks[v]; ch = ch0[v]; o = 7 * v
                freq = regs[o] | regs[o + 1] << 8; ctrl = regs[o + 4]; gate = ctrl & 1
                wave = ctrl & 0xF0; test = ctrl & 8; sync = ctrl & 2; ring = ctrl & 4
                pw = regs[o + 2] | (regs[o + 3] & 0x0F) << 8; ad = regs[o + 5]; sr = regs[o + 6]
                noise = (wave & 0x80) and not (wave & 0x70)
                src = (v + 2) % 3; sfreq = regs[7 * src] | regs[7 * src + 1] << 8
                pfreq = sfreq if (sync and sfreq) else freq
                hz = pfreq * sid.clock / ACC
                tt = tick(cyc0 + (tcyc[v] if tcyc[v] >= 0 else 0))
                if do_cc:
                    for num, val in ((71, (regs[23] >> 4) * 8), (70, pw >> 5), (73, (ad >> 4) * 8), (75, (ad & 15) * 8),
                                     (79, (sr >> 4) * 8), (72, (sr & 15) * 8), (20, wave_code(wave) * 8),
                                     (21, 127 if sync else 0), (22, 127 if ring else 0), (23, 127 if test else 0)):
                        if lastcc[v].get(num) != val: tr.cc(tick(cyc0), ch, num, val); lastcc[v][num] = val
                    pg = 13 if ring else wave_prog(wave)
                    if pg is not None and pg != lastprog[v]: tr.prog(tick(cyc0), ch, pg); lastprog[v] = pg
                is_drum = ((k == 0 and drum_voice is not None and v == drum_voice)
                           or ((drum_voice is None or k > 0) and noise))
                if is_drum:
                    if cur[v] is not None:
                        tr.off(tt, ch, cur[v]); cur[v] = None
                    if trig[v]:
                        dn = (42 if hz > 1800 else 39 if hz > 400 else 38) if noise else (36 if hz < 400 else 38)
                        dtr.on(tt, 9, dn, 108); dtr.off(tt + max(1, tick(pf(f)) // 2), 9, dn)
                    continue
                ex = hz_exact(hz) if (gate and not test and wave and not noise) else None
                if ex is not None and not -0.5 <= ex < 127.5:
                    ex = None
                note = int(round(ex)) if ex is not None else None

                def setbend(tk, b):
                    b = max(0, min(16383, b))
                    if b != lastbend[v]: tr.bend(tk, ch, b); lastbend[v] = b
                if note is not None and (trig[v] or note != cur[v]):
                    if cur[v] is not None: tr.off(tt, ch, cur[v])
                    tr.on(tt, ch, note, velocity(v, f, sr, mvol)); cur[v] = note; basev[v] = note
                    if bend: setbend(tt, 8192 + int(round((ex - note) * 8192 / 2.0)))
                elif note is None and cur[v] is not None:
                    tr.off(tt, ch, cur[v]); cur[v] = None
                elif note is not None and bend:
                    setbend(tick(cyc0), 8192 + int(round((ex - basev[v]) * 8192 / 2.0)))
        end = tick(fcyc[N])
        for v in range(3):
            if cur[v] is not None: tracks[v].off(end, ch0[v], cur[v])

    def render_filter(trk, k):
        ch = FILTER_CH[k]; last = {}
        for f in range(N):
            regs = frames[f][0][k][0]; t = tick(fcyc[f])
            vals = [(74, ((regs[22] << 3) | (regs[21] & 7)) >> 4), (71, (regs[23] >> 4) * 8),
                    (24, ((regs[24] >> 4) & 7) * 16), (25, (regs[23] & 7) * 16), (7, (regs[24] & 15) * 8)]
            if k == 0:
                vals.append((26, min(127, frames[f][1])))
            for num, val in vals:
                if last.get(num) != val: trk.cc(t, ch, num, val); last[num] = val

    voice_tracks, filter_tracks = [], []
    for k in active:
        label = LABELS[k]
        ts = [Trk("%s V%d" % (label, i + 1)) for i in range(3)]
        for i, tr in enumerate(ts):
            tr.prog(0, VOICE_CH[k][i], (38, 81, 80)[i]); tr.cc(0, VOICE_CH[k][i], 7, 100)
            tr.meta(0, 0x04, "SID %s voice %d" % (label, i + 1))
        if N and len(frames[0][0][k]) > 3:               # emulated envelope levels
            envs = [[frames[f][0][k][3][v] for f in range(N)] for v in range(3)]
        else:
            envs = compute_env(frames, lambda f, k=k: frames[f][0][k][0], lambda f, k=k: frames[f][0][k][1], fcyc)
        render_sid(ts, k, envs)
        voice_tracks += ts
        flt = Trk("Filter/Master" if k == 0 else "Filter/Master " + label)
        flt.cc(0, FILTER_CH[k], 7, 110)
        if do_cc:
            render_filter(flt, k)
        filter_tracks.append(flt)
    return PPQ, [meta] + voice_tracks + [dtr] + filter_tracks


# --------------------------------------------------------------- driver --------
class TuneRun:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def run_tune(sid, song, seconds, auto=False, init_budget=2_000_000, play_budget=50_000,
             max_stalls=25, max_frames=400_000):
    """Run the tune for ``seconds`` of C64 time.  ``song`` is 0-based.

    PSIDs with a play address are called per frame; RSIDs, PSIDs with
    play = 0 and C64 BASIC tunes run continuously in a cold-started C64.
    """
    if sid.rsid or sid.play == 0:
        run = _run_continuous(sid, song, seconds, max_frames)
    else:
        run = _run_called(sid, song, seconds, init_budget, play_budget, max_stalls, max_frames)
    if auto and len(run.frames) > 1:
        per_second = len(run.frames) / max((run.fcyc[-1] - run.fcyc[0]) / sid.clock, 1e-9)
        run.loop = find_loop(run.frames, max(1, int(per_second * 6)))
        if run.loop:
            s, P = run.loop
            run.frames = run.frames[:s + P]
            run.fcyc = run.fcyc[:s + P + 1]
    return run


def _run_called(sid, song, seconds, init_budget, play_budget, max_stalls, max_frames):
    c = C64(sid.extra_sids, sid.pal)
    c.set_sid_model(*sid.chip_models)
    c.load_sid(sid)
    cpu = c.cpu
    warnings = []
    cpu.brk_stop = True                            # PSID: BRK returns to the player
    c.ram[0x01] = sid.bank_for(sid.init)
    c.begin_frame(0)
    cpu.call(sid.init, a=song, max_ins=init_budget)
    init_returned = not (cpu.jammed or cpu.timed_out)
    if cpu.jammed:
        warnings.append("init jammed the CPU at $%04X" % cpu.pc)
    elif cpu.timed_out:
        warnings.append("init did not return within %d instructions (pc $%04X); continuing"
                        % (init_budget, cpu.pc))
    elif cpu.brk_hit:
        warnings.append("init ended on a BRK instruction")

    total = int(seconds * sid.clock)
    frames, fcyc = [], []
    stalls = stalled_calls = brk_calls = 0
    stop = None
    vs = sid.vsync(song)
    t = 0
    while t < total and len(frames) < max_frames:
        c.ram[0x01] = sid.bank_for(sid.play)
        c.begin_frame(t)
        cpu.call(sid.play, max_ins=play_budget)
        fcyc.append(t)
        frames.append(c.snapshot())
        brk_calls += cpu.brk_hit
        if cpu.timed_out or cpu.jammed:
            stalls += 1
            stalled_calls += 1
            if stalls >= max_stalls:
                stop = ("playback stalled: %d consecutive calls %s (pc $%04X); capture stopped at %.1fs"
                        % (stalls, "jammed the CPU" if cpu.jammed else "exceeded %d instructions" % play_budget,
                           cpu.pc, t / sid.clock))
                t += sid.frame
                break
        else:
            stalls = 0
        latch = c.cia1.ta.latch
        t += sid.frame if (vs or latch <= 16) else latch + 1
    fcyc.append(t)
    if stalled_calls and not stop:
        warnings.append("%d player calls exceeded the instruction budget" % stalled_calls)
    if brk_calls:
        warnings.append("%d player calls ended on BRK" % brk_calls)
    return TuneRun(frames=frames, fcyc=fcyc, irq=False, loop=None, warnings=warnings, stop_reason=stop,
                   timing="v-sync" if vs else "CIA", stalled_calls=stalled_calls, irq_count=0,
                   nmi_count=0, init_returned=init_returned, t0=0)


def _start_basic_program(c, sid, song, warnings):
    """Prepare BASIC's pointers for the loaded program, put the song in $030C, type RUN."""
    ram = c.ram
    ram[0x2B], ram[0x2C] = sid.load & 0xFF, sid.load >> 8          # TXTTAB
    end = sid.end
    for zp in (0x2D, 0x2F, 0x31, 0xAE):                            # VARTAB ARYTAB STREND EAL
        ram[zp], ram[zp + 1] = end & 0xFF, end >> 8
    ram[0x030C] = song & 0xFF
    if c.call_subroutine(0xA533) != "stop":                        # LINKPRG: rechain lines
        warnings.append("BASIC LINKPRG did not return")
    keys = b"RUN\r"
    ram[0x0277:0x0277 + len(keys)] = keys
    ram[0xC6] = len(keys)


def _run_continuous(sid, song, seconds, max_frames):
    c = C64(sid.extra_sids, sid.pal)
    c.set_sid_model(*sid.chip_models)
    cpu = c.cpu
    warnings = []
    timing = "BASIC" if sid.basic else "IRQ"
    try:
        c.restore_state(cold_reset_state(sid.pal))
    except RuntimeError as e:
        if sid.basic:
            return TuneRun(frames=[], fcyc=[0], irq=True, loop=None, warnings=warnings,
                           stop_reason="C64 BASIC tunes need a working KERNAL/BASIC cold start: %s" % e,
                           timing=timing, stalled_calls=0, irq_count=0, nmi_count=0,
                           init_returned=False, t0=0)
        warnings.append("KERNAL cold start unavailable (%s); using a minimal environment, "
                        "output may be incomplete (see roms/README.md)" % e)
        c.setup_psid_environment()
        cpu.sp = 0xFF
    c.ram[sid.load:sid.end] = sid.data
    if sid.basic:
        _start_basic_program(c, sid, song, warnings)
        init_pending = False
    else:
        c.idle_trap = True
        c.ram[0x01] = 0x37 if sid.rsid else sid.bank_for(sid.init)
        cpu.brk_stop = not sid.rsid
        cpu.a, cpu.x, cpu.y = song & 0xFF, 0, 0
        cpu.sp = 0xFF
        cpu._push(0x00)                                # RTS from init lands on IDLE_PC
        cpu._push(0x00)
        cpu.pc = sid.init
        init_pending = True
    c.dirty = True

    t0 = cpu.cycles
    end = t0 + int(seconds * sid.clock)
    frames, fcyc = [], []
    min_gap = sid.frame // 16
    last_irq = [-NEVER]
    c.start_frame(t0)

    def close(now):
        fcyc.append(c.frame_start - t0)
        frames.append(c.snapshot())
        c.start_frame(now)

    def on_irq(now):
        last_irq[0] = now
        if now - c.frame_start >= min_gap and len(frames) < max_frames:
            close(now)

    init_returned = False
    stop = None
    while len(frames) < max_frames:
        now = cpu.cycles
        if now >= end:
            break
        period = 3 * sid.frame if now - last_irq[0] < 3 * sid.frame else sid.frame
        deadline = min(end, c.frame_start + period)
        if deadline <= now:
            close(now)
            continue
        reason = c.run(deadline, IDLE_PC if init_pending else None, on_irq)
        if reason == "stop":
            init_pending = False
            init_returned = True
            cpu.I = cpu.I_poll = 0                     # driver main loop runs with IRQs enabled
        elif reason == "jam":
            stop = "CPU jammed at $%04X after %.1fs" % (cpu.pc, (cpu.cycles - t0) / sid.clock)
            break
    now = cpu.cycles
    if now > c.frame_start or not frames:
        close(max(now, c.frame_start + 1))
    fcyc.append(c.frame_start - t0)
    if cpu.brk_count:
        warnings.append("%d BRK instructions returned to the idle loop" % cpu.brk_count)
    return TuneRun(frames=frames, fcyc=fcyc, irq=True, loop=None, warnings=warnings, stop_reason=stop,
                   timing=timing, stalled_calls=0, irq_count=c.irq_count, nmi_count=c.nmi_count,
                   init_returned=init_returned, t0=t0)


# ---------------------------------------------------------------- main ---------
def build_parser():
    ap = argparse.ArgumentParser(description="Cycle-stamped PSID/RSID -> MIDI converter")
    ap.add_argument("sid")
    ap.add_argument("-o", "--out", default=None, help="output .mid (default: next to the .sid)")
    songs = ap.add_mutually_exclusive_group()
    songs.add_argument("--song", type=int, default=None, help="subtune, 1-based (default: header start song)")
    songs.add_argument("--all-songs", action="store_true", help="convert every subtune to NAME_songNN.mid")
    ap.add_argument("--seconds", type=float, default=180.0, help="maximum capture length")
    ap.add_argument("--auto", action="store_true", help="trim to intro + first loop via loop detection")
    ap.add_argument("--bpm", type=float, default=125.0, help="DAW grid tempo (timing still follows the C64)")
    ap.add_argument("--drumvoice", type=int, choices=(1, 2, 3), default=None,
                    help="route this SID A voice to drums (default: noise voices)")
    ap.add_argument("--no-bend", action="store_true")
    ap.add_argument("--no-cc", action="store_true")
    ap.add_argument("--report", action="store_true", help="print a conversion summary")
    ap.add_argument("--info", action="store_true", help="print the header summary and exit")
    ap.add_argument("--no-fixups", action="store_true", help="do not repair malformed headers/data")
    ap.add_argument("--sidplayer", metavar="FILE", default=None,
                    help="Compute!'s Sidplayer player for MUS files (xa source, e.g. libsidplayfp "
                         "sidplayer1.a65, or a binary with load address); not bundled")
    ap.add_argument("--sid-model", choices=("auto", "6581", "8580"), default="auto",
                    help="SID chip model to emulate (default: from the header, else 6581)")
    ap.add_argument("--init-budget", type=int, default=2_000_000,
                    help="instruction limit for a called PSID init")
    ap.add_argument("--play-budget", type=int, default=50_000,
                    help="instruction limit per called PSID play")
    ap.add_argument("--max-stalls", type=int, default=25,
                    help="stop a called PSID after this many consecutive stalled calls")
    ap.add_argument("--version", action="version", version="sid2midi %s" % VERSION)
    return ap


def _report(sid, song, run, digi):
    frames, fcyc = run.frames, run.fcyc
    n = len(frames)
    dur = fcyc[n] / sid.clock
    period = fcyc[n] // max(1, n)
    print(sid.describe()[0])
    print("chip %s  %s %dHz  songs %d  song %d  load $%04X %s play $%04X%s" % (
        sid.model, "PAL" if sid.pal else "NTSC", sid.rate, sid.songs, song, sid.load,
        "BASIC RUN" if sid.basic else "init $%04X" % sid.init, sid.play,
        "".join("  SID $%04X" % a for a in sid.extra_sids)))
    loop = run.loop
    detail = ""
    if run.irq:
        detail = "  IRQs %d  NMIs %d%s" % (run.irq_count, run.nmi_count,
                                           "" if run.init_returned or sid.basic else "  init runs a main loop")
    print("timing %s period~%d (%.2f frames/video frame)  %d frames (%.1fs)%s%s%s%s" % (
        run.timing, period, sid.frame / max(1, period), n, dur,
        "  loop %.1fs-%.1fs" % (fcyc[loop[0]] / sid.clock, fcyc[loop[0] + loop[1]] / sid.clock) if loop else "",
        "  DIGI($D418)" if digi > n // 4 else "",
        "  stalled calls %d" % run.stalled_calls if run.stalled_calls else "", detail))


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.seconds <= 0:
        ap.error("--seconds must be positive")
    if a.bpm <= 0:
        ap.error("--bpm must be positive")
    try:
        sid = SidFile(a.sid, fixups=not a.no_fixups)
    except (OSError, SidError) as e:
        sid = None
        try:
            is_mus = mus.voice3_index(open(a.sid, "rb").read()) is not None
        except OSError:
            is_mus = False
        if is_mus and a.sidplayer:
            try:
                sid = MusTune(a.sid, a.sidplayer)
            except (OSError, SidError) as e2:
                e = e2
                sid = None
        elif is_mus:
            e = SidError("MUS (Compute!'s Sidplayer) file: the player routine is not bundled; "
                         "give it with --sidplayer FILE")
        if sid is None:
            print("error: %s: %s" % (a.sid, e), file=sys.stderr)
            return 2
    for w in sid.warnings:
        print("warning: %s" % w, file=sys.stderr)
    if a.sid_model != "auto":
        sid.chip_models = [a.sid_model] * len(sid.chip_models)
    if a.info:
        print("\n".join(sid.describe()))
        return 0
    if a.all_songs:
        songs = list(range(1, sid.songs + 1))
    else:
        song = a.song if a.song is not None else sid.start
        if not 1 <= song <= sid.songs:
            print("error: --song %d out of range 1-%d" % (song, sid.songs), file=sys.stderr)
            return 2
        songs = [song]
    base = a.out or os.path.splitext(a.sid)[0] + ".mid"
    rc = 0
    for song in songs:
        out = base
        if a.all_songs:
            root, ext = os.path.splitext(base)
            out = "%s_song%02d%s" % (root, song, ext or ".mid")
        run = run_tune(sid, song - 1, a.seconds, a.auto, a.init_budget, a.play_budget, a.max_stalls)
        prefix = "song %d: " % song if a.all_songs else ""
        for w in run.warnings:
            print("warning: %s%s" % (prefix, w), file=sys.stderr)
        if run.stop_reason:
            print("warning: %s%s" % (prefix, run.stop_reason), file=sys.stderr)
        if not run.frames:
            print("error: %sno player activity was captured" % prefix, file=sys.stderr)
            rc = 1
            continue
        digi = sum(1 for f in run.frames if f[1] > 4)
        if a.report:
            _report(sid, song, run, digi)
        dv = (a.drumvoice - 1) if a.drumvoice else None
        ppq, trks = convert(sid, run.frames, run.fcyc, a.bpm, drum_voice=dv, bend=not a.no_bend,
                            do_cc=not a.no_cc, loop=run.loop)
        try:
            write_smf(out, ppq, trks)
        except OSError as e:
            print("error: cannot write %s: %s" % (out, e), file=sys.stderr)
            rc = 1
            continue
        notes = sum(1 for t in trks for _, _, b in t.ev if b[0] & 0xF0 == 0x90 and b[2] > 0)
        cc = sum(1 for t in trks for _, _, b in t.ev if b[0] & 0xF0 == 0xB0)
        dur = run.fcyc[len(run.frames)] / sid.clock
        print("%s : %d tracks, %d notes, %d CC, PPQ %d, %.1fs @ grid %g BPM" % (
            out, len(trks), notes, cc, ppq, dur, a.bpm))
    return rc


if __name__ == "__main__":
    sys.exit(main())
