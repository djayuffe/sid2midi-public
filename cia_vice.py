"""MOS 6526 / 6526A CIA: a statement-level Python port of VICE's CIA core.

Source: VICE (https://vice-emu.sourceforge.io/), src/core/ciacore.c and
src/core/ciatimer.c/h (trunk).  Written by Andre Fachat, Andreas Boose and
many others; the interrupt delay line and serial port by the VICE team.

This file is free software; you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation; either version 2 of the License, or (at your option) any later
version.  It is distributed WITHOUT ANY WARRANTY.

VICE drives the chip with alarms; here the same alarms are kept in the object
and dispatched lazily before every register access and on ``sync(t)``.  The
port keeps VICE's names and statement order.  Differences from VICE:

* the TOD power-line ticks use VICE's non-random correction branch, so runs are
  reproducible;
* no external devices on the ports, CNT, SP or FLAG lines (a bare C64);
* the RMW double store is not simulated here because the CPU core performs
  both writes itself.

Clock mapping to the machine (``READ_OFFSET``, ``WRITE_OFFSET``,
``IRQ_OFFSET``) is fixed by the Lorenz register-access tests.
"""
import copy

NEVER = 1 << 62

# ---- ciatimer.h ------------------------------------------------------------
CIAT_TABLEN = 2 << 13
CIAT_CR_MASK = 0x039
CIAT_CR_START = 0x001
CIAT_CR_ONESHOT = 0x008
CIAT_CR_FLOAD = 0x010
CIAT_PHI2IN = 0x020
CIAT_STEP = 0x004
CIAT_COUNT2 = 0x002
CIAT_COUNT3 = 0x040
CIAT_COUNT = 0x800
CIAT_LOAD1 = 0x080
CIAT_ONESHOT0 = 0x100
CIAT_ONESHOT = 0x1000
CIAT_LOAD = 0x200
CIAT_OUT = 0x400


def _init_table():
    table = [0] * CIAT_TABLEN
    for i in range(CIAT_TABLEN):
        tmp = i & (CIAT_CR_START | CIAT_CR_ONESHOT | CIAT_PHI2IN)
        if (i & CIAT_CR_START) and (i & CIAT_PHI2IN):
            tmp |= CIAT_COUNT2
        if (i & CIAT_COUNT2) or ((i & CIAT_STEP) and (i & CIAT_CR_START)):
            tmp |= CIAT_COUNT3
        if i & CIAT_COUNT3:
            tmp |= CIAT_COUNT
        if i & CIAT_CR_FLOAD:
            tmp |= CIAT_LOAD1
        if i & CIAT_LOAD1:
            tmp |= CIAT_LOAD
        if i & CIAT_CR_ONESHOT:
            tmp |= CIAT_ONESHOT0
        if i & CIAT_ONESHOT0:
            tmp |= CIAT_ONESHOT
        table[i] = tmp
    return table


CIAT_TABLE = _init_table()

_WARP_COUNT_MASK = (CIAT_CR_START | CIAT_CR_FLOAD | CIAT_LOAD1 | CIAT_PHI2IN
                    | CIAT_COUNT2 | CIAT_COUNT3 | CIAT_COUNT | CIAT_LOAD)
_WARP_COUNT_VAL = CIAT_CR_START | CIAT_PHI2IN | CIAT_COUNT2 | CIAT_COUNT3 | CIAT_COUNT


def _oneshot_steady(t):
    return (((t & CIAT_CR_ONESHOT) and (t & CIAT_ONESHOT0) and (t & CIAT_ONESHOT))
            or (not (t & CIAT_CR_ONESHOT) and not (t & CIAT_ONESHOT0) and not (t & CIAT_ONESHOT)))


class CiaTimer:
    __slots__ = ("state", "latch", "cnt", "alarmclk", "clk")

    def __init__(self, cclk=0):
        self.reset(cclk)

    def reset(self, cclk):
        self.clk = cclk
        self.alarmclk = NEVER
        self.cnt = 0xFFFF
        self.latch = 0xFFFF
        self.state = 0

    def set_alarm(self, cclk):
        tmp = 0
        aclk = self.clk
        cnt = self.cnt
        t = self.state
        while True:
            if (t & _WARP_COUNT_MASK) == _WARP_COUNT_VAL and _oneshot_steady(t):
                tmp = aclk + cnt                       # warp counting
                break
            elif (not (t & (CIAT_COUNT2 | CIAT_COUNT3 | CIAT_COUNT))
                  and (not (t & CIAT_CR_START) or not (t & (CIAT_PHI2IN | CIAT_STEP)))
                  and _oneshot_steady(t)):
                tmp = NEVER                            # warp stopped
                break
            else:
                if cnt and (t & CIAT_COUNT3):
                    cnt -= 1
                t = CIAT_TABLE[t]
                aclk += 1
            if cnt == 0 and (t & CIAT_COUNT3):
                t |= CIAT_LOAD | CIAT_OUT
                tmp = aclk
                break
            if t & CIAT_LOAD:
                cnt = self.latch
                t &= ~CIAT_COUNT3
            if (t & CIAT_OUT) and (t & (CIAT_ONESHOT | CIAT_ONESHOT0)):
                t &= ~(CIAT_CR_START | CIAT_COUNT2)
        self.alarmclk = tmp

    def update(self, cclk):
        n = 0
        t = self.state
        while self.clk < cclk:
            if (t & _WARP_COUNT_MASK) == _WARP_COUNT_VAL and _oneshot_steady(t):
                if self.clk + self.cnt > cclk:
                    self.cnt = (self.cnt - ((cclk - self.clk) & 0xFFFF)) & 0xFFFF
                    self.clk = cclk
                else:
                    if t & (CIAT_CR_ONESHOT | CIAT_ONESHOT0):
                        self.clk = self.clk + self.cnt
                        self.cnt = 0
                    else:
                        self.clk = self.clk + self.cnt
                        self.cnt = 0
                        if ((cclk - self.clk) & 0xFFFF) >= self.latch + 1:
                            m = (cclk - self.clk) // (self.latch + 1)
                            n += m
                            self.clk += m * (self.latch + 1)
            elif (not (t & (CIAT_COUNT2 | CIAT_COUNT3 | CIAT_COUNT))
                  and (not (t & CIAT_CR_START) or not (t & (CIAT_PHI2IN | CIAT_STEP)))
                  and not (t & (CIAT_CR_FLOAD | CIAT_LOAD1 | CIAT_LOAD))
                  and _oneshot_steady(t)):
                self.clk = cclk                        # warp stopped
            elif (t == (CIAT_COUNT | CIAT_OUT | CIAT_LOAD | CIAT_PHI2IN | CIAT_COUNT2 | CIAT_CR_START)
                  and self.cnt == 1 and self.latch == 1):
                m = (cclk - self.clk) & ~1
                if m:
                    self.clk += m
                    n += m >> 1
                else:
                    t = CIAT_TABLE[t]
                    self.clk += 1
            else:
                if self.cnt and (t & CIAT_COUNT3):
                    self.cnt -= 1
                t = CIAT_TABLE[t]
                self.clk += 1
            if self.cnt == 0 and (t & CIAT_COUNT3):
                t |= CIAT_LOAD | CIAT_OUT
                n += 1
            if t & CIAT_LOAD:
                self.cnt = self.latch
                t &= ~CIAT_COUNT3
            if (t & CIAT_OUT) and (t & (CIAT_ONESHOT | CIAT_ONESHOT0)):
                t &= ~(CIAT_CR_START | CIAT_COUNT2)
        self.state = t
        return n

    def is_underflow_clk(self):
        return 1 if self.state & CIAT_OUT else 0

    def is_running(self):
        return 1 if self.state & CIAT_CR_START else 0

    def single_step(self, cclk):
        if self.state & CIAT_CR_START:
            self.state |= CIAT_STEP
            self.set_alarm(cclk)
        return 0

    def set_latchhi(self, cclk, byte):
        self.latch = (self.latch & 0xFF) | (byte << 8)
        if (self.state & CIAT_LOAD) or not (self.state & CIAT_CR_START):
            self.cnt = self.latch
        self.set_alarm(cclk)

    def set_latchlo(self, cclk, byte):
        self.latch = (self.latch & 0xFF00) | byte
        if self.state & CIAT_LOAD:
            self.cnt = (self.cnt & 0xFF00) | byte
        self.set_alarm(cclk)

    def set_ctrl(self, cclk, byte):
        self.state &= ~CIAT_CR_MASK
        self.state |= (byte & CIAT_CR_MASK) ^ CIAT_PHI2IN
        self.set_alarm(cclk)

    def ack_alarm(self, cclk):
        self.alarmclk = NEVER


# ---- cia.h -----------------------------------------------------------------
CIA_PRA, CIA_PRB, CIA_DDRA, CIA_DDRB = 0, 1, 2, 3
CIA_TAL, CIA_TAH, CIA_TBL, CIA_TBH = 4, 5, 6, 7
CIA_TOD_TEN, CIA_TOD_SEC, CIA_TOD_MIN, CIA_TOD_HR = 8, 9, 10, 11
CIA_SDR, CIA_ICR, CIA_CRA, CIA_CRB = 12, 13, 14, 15

CIA_IM_TA, CIA_IM_TB, CIA_IM_TOD, CIA_IM_SDR, CIA_IM_FLG = 0x01, 0x02, 0x04, 0x08, 0x10
CIA_IM_TBB = 0x40                 # internal: timer B bug pending (never read back)
CIA_IM_SET = 0x80

CIA_CR_START = 0x01
CIA_CR_PBON = 0x02
CIA_CR_OUTMODE_TOGGLE = 0x04
CIA_CR_RUNMODE = 0x08
CIA_CR_RUNMODE_CONTINUOUS = 0x00
CIA_CRA_INMODE = 0x20
CIA_CRA_INMODE_PHI2 = 0x00
CIA_CRA_SPMODE = 0x40
CIA_CRA_SPMODE_IN = 0x00
CIA_CRA_SPMODE_OUT = 0x40
CIA_CRA_TODIN_50HZ = 0x80
CIA_CRB_INMODE = 0x60
CIA_CRB_INMODE_PHI2 = 0x00
CIA_CRB_INMODE_TA = 0x40
CIA_CRB_ALARM = 0x80
CIA_CRB_ALARM_TOD = 0x00
CIA_CRB_ALARM_ALARM = 0x80

CIA_MODEL_6526 = "6526"
CIA_MODEL_6526A = "6526A"

# ---- ciacore.c ---------------------------------------------------------------
CIA_SDR_TOGGLE_CNT2 = 0x0001
CIA_SDR_TOGGLE_CNT1 = 0x0002
CIA_SDR_TOGGLE_CNT0 = 0x0004
CIA_SDR_TOGGLE_CNT_1 = 0x0008
CIA_SDR_NOGGLE_CNT2 = 0x0010
CIA_SDR_NOGGLE_CNT1 = 0x0020
CIA_SDR_NOGGLE_CNT0 = 0x0040
CIA_SDR_NOGGLE_CNT_1 = 0x0080
CIA_SDR_SET_SDR_IRQ3 = 0x0100
CIA_SDR_SET_SDR_IRQ2 = 0x0200
CIA_SDR_SET_SDR_IRQ1 = 0x0400
CIA_SDR_SET_SDR_IRQ0 = 0x0800
CIA_SDR_CNT0 = 0x1000
CIA_SDR_CNT1 = 0x2000
CIA_SDR_CNT2 = 0x4000
CIA_SDR_CNT3 = 0x8000
CIA_SDR_SET3 = 0x00010000
CIA_SDR_SET2 = 0x00020000
CIA_SDR_SET1 = 0x00040000
CIA_SDR_SET0 = 0x00080000
CIA_SDR_LEFTMOST = 0x00100000
CIA_SDR_CLEAR = CIA_SDR_NOGGLE_CNT2 | CIA_SDR_SET_SDR_IRQ3 | CIA_SDR_CNT0 | CIA_SDR_SET3 | CIA_SDR_LEFTMOST
CIA_SDR_ACTIVE = (CIA_SDR_TOGGLE_CNT2 | CIA_SDR_TOGGLE_CNT1 | CIA_SDR_TOGGLE_CNT0 | CIA_SDR_TOGGLE_CNT_1
                  | CIA_SDR_NOGGLE_CNT2 | CIA_SDR_NOGGLE_CNT1 | CIA_SDR_NOGGLE_CNT0 | CIA_SDR_NOGGLE_CNT_1
                  | CIA_SDR_SET_SDR_IRQ3 | CIA_SDR_SET_SDR_IRQ2 | CIA_SDR_SET_SDR_IRQ1 | CIA_SDR_SET_SDR_IRQ0
                  | CIA_SDR_SET3 | CIA_SDR_SET2 | CIA_SDR_SET1 | CIA_SDR_SET0)
ALL_SDR_CNT = CIA_SDR_CNT0 | CIA_SDR_CNT1 | CIA_SDR_CNT2 | CIA_SDR_CNT3
ALL_SDR_TOGGLE_CNT = CIA_SDR_TOGGLE_CNT2 | CIA_SDR_TOGGLE_CNT1 | CIA_SDR_TOGGLE_CNT0 | CIA_SDR_TOGGLE_CNT_1
ALL_SDR_NOGGLE_CNT = CIA_SDR_NOGGLE_CNT2 | CIA_SDR_NOGGLE_CNT1 | CIA_SDR_NOGGLE_CNT0 | CIA_SDR_NOGGLE_CNT_1

CIA_IRQ_ACK1 = 0x0001
CIA_IRQ_ACK0 = 0x0002
CIA_IRQ_ACK_1 = 0x0004
CIA_IRQ_ACK_2 = 0x0008
CIA_IRQ_D7SET1 = 0x0010
CIA_IRQ_D7SET0 = 0x0020
CIA_IRQ_D7SET_1 = 0x0040
CIA_IRQ_RAISE1 = 0x0100
CIA_IRQ_RAISE0 = 0x0200
CIA_IRQ_RAISE_1 = 0x0400
CIA_IRQ_READ0 = 0x1000
CIA_IRQ_READ1 = 0x2000
CIA_IRQ_READ2 = 0x4000
CIA_IRQ_CLEAR = CIA_IRQ_ACK_2 | CIA_IRQ_D7SET_1 | CIA_IRQ_RAISE_1 | CIA_IRQ_READ2

CIA_MAX_IDLE_CYCLES = 5000

CIA_IFR_CURRENT, CIA_IFR_NEXT, CIA_IFR_CUR_NXT = 0x01, 0x02, 0x03

_ALARMS = ("ta", "tb", "sdr", "tod", "idle")


class Cia6526:
    """VICE CIA core.  ``on_irq(level_low, cycle)`` reports /IRQ changes."""

    READ_OFFSET = 0          # machine cycle t -> VICE clock for reads
    WRITE_OFFSET = 0         # ... for writes
    IRQ_OFFSET = 0           # VICE interrupt clock -> first machine cycle with the new level

    def __init__(self, on_irq=None, model=CIA_MODEL_6526):
        self.on_irq = on_irq
        self.on_pb = None                              # store_ciapb(rclk, byte): port B output lines
        self.model = model
        self.ta = CiaTimer()
        self.tb = CiaTimer()
        self.c_cia = bytearray(16)
        self.ticks_per_sec = 985248
        self.power_freq = 50
        self.power_ticks = 0
        self.power_tickcounter = 0
        self.todticks = self.ticks_per_sec // self.power_freq
        self.alarm = {name: NEVER for name in _ALARMS}
        self.t = 0                                     # clocks < t have been synchronised
        self.reset(0)

    # ---- machine interface --------------------------------------------------
    def set_clock(self, cycles_per_second, power_hz):
        self.ticks_per_sec = int(cycles_per_second)
        self.power_freq = power_hz
        self.todticks = self.ticks_per_sec // self.power_freq
        self.todclk = self.t + self.todticks
        self.alarm["tod"] = self.todclk

    def save(self):
        callbacks = self.on_irq, self.on_pb
        self.on_irq = self.on_pb = None
        try:
            return copy.deepcopy(self.__dict__)
        finally:
            self.on_irq, self.on_pb = callbacks

    def load(self, state):
        callbacks = self.on_irq, self.on_pb
        self.__dict__.update(copy.deepcopy(state))
        self.on_irq, self.on_pb = callbacks

    def sync(self, t):
        """Process everything VICE would have done for clocks < t."""
        self._run_pending_alarms(t)
        if t > self.t:
            self.t = t

    def next_irq_cycle(self):
        """Earliest machine cycle at which /IRQ may change on its own."""
        if self.irq_enabled:
            return None
        self._sync_timer_alarms()                      # register access may have moved them
        nxt = min(self.alarm.values())
        return None if nxt >= NEVER else nxt + self.IRQ_OFFSET

    def read(self, r, t):
        return self._read(r & 0x0F, t + self.READ_OFFSET)

    def write(self, r, v, t):
        self._store(r & 0x0F, v & 0xFF, t + self.WRITE_OFFSET)

    # ---- alarms -------------------------------------------------------------
    def _alarm_set(self, name, clk):
        self.alarm[name] = clk

    def _alarm_unset(self, name):
        self.alarm[name] = NEVER

    def _sync_timer_alarms(self):
        self.alarm["ta"] = self.ta.alarmclk
        self.alarm["tb"] = self.tb.alarmclk

    def _run_pending_alarms(self, clk):
        """Dispatch alarms due before ``clk`` in clock order (VICE run_pending_alarms)."""
        while True:
            self._sync_timer_alarms()
            name = min(_ALARMS, key=lambda k: self.alarm[k])
            aclk = self.alarm[name]
            if aclk >= clk:
                return
            if name == "ta":
                self._intta_entry(aclk)
            elif name == "tb":
                self._inttb_entry(aclk)
            elif name == "sdr":
                self._intsdr_entry(aclk)
            elif name == "tod":
                self._inttod_entry(aclk)
            else:
                self._idle(aclk)

    def _my_set_int(self, value, rclk):
        changed = bool(value) != bool(self.irq_enabled)
        self.irq_enabled = value
        # interrupt_set_irq() ignores a source that is already in the requested state
        if changed and self.on_irq is not None:
            # A released line stays pending to the CPU until clk + 3
            # (IK_IRQPEND), one cycle past the two-cycle sampling delay.
            self.on_irq(bool(value), rclk + self.IRQ_OFFSET + (0 if value else 1))

    # ---- reset ----------------------------------------------------------------
    def reset(self, clk):
        for i in range(16):
            self.c_cia[i] = 0
        self.rdi = 0
        self.sr_bits = 0
        self.shifter = 0
        self.ta.reset(clk)
        self.tb.reset(clk)
        self.tat = self.tbt = 0
        self.sdr_valid = False
        self.sdr_force_finish = False
        self.sdr_delay = CIA_SDR_CNT0 | CIA_SDR_CNT1 | CIA_SDR_CNT2 | CIA_SDR_CNT3
        self.sp_in_state = True
        self.cnt_in_state = True
        self.cnt_out_state = True
        self.todalarm = bytearray(4)
        self.todlatched = 0
        self.todstopped = 1
        self.c_cia[CIA_TOD_HR] = 1
        self.todlatch = bytearray(self.c_cia[CIA_TOD_TEN:CIA_TOD_HR + 1])
        self.todclk = clk + self.todticks
        self.alarm = {name: NEVER for name in _ALARMS}
        self._alarm_set("tod", self.todclk)
        self.todtickcounter = 0
        self.irqflags = 0
        self.ack_irqflags = 0
        self.new_irqflags = 0
        self.irq_enabled = 0
        self.ifr_clock = 0
        self.ifr_delay = 0
        self.irq_enabled = False
        self.old_pa = 0xFF
        self.old_pb = 0xFF
        self.last_read = 0
        self._alarm_set("idle", clk + CIA_MAX_IDLE_CYCLES)

    # ---- timer updates --------------------------------------------------------
    def _check_ciatodalarm(self, rclk):
        if self.todalarm == self.c_cia[CIA_TOD_TEN:CIA_TOD_HR + 1]:
            self._cia_set_irq_flag(rclk, CIA_IM_TOD)

    def _cia_do_update_ta(self, rclk):
        n = self.ta.update(rclk)
        if n:
            self._cia_set_irq_flag(rclk, CIA_IM_TA)
            self.tat = (self.tat + n) & 1

    def _cia_do_update_tb(self, rclk):
        n = self.tb.update(rclk)
        if n:
            self._cia_set_irq_flag(rclk, CIA_IM_TB)
            if self.model == CIA_MODEL_6526 and self.rdi == rclk - 1:
                self.irqflags |= CIA_IM_TBB          # flag the timer B bug
            else:
                self.irqflags &= ~CIA_IM_TBB
            self.tbt = (self.tbt + n) & 1

    def _cia_do_step_tb(self, rclk):
        n = self.tb.single_step(rclk)
        if n:
            self._cia_set_irq_flag(rclk, CIA_IM_TB)
            self.tbt = (self.tbt + n) & 1

    def _cia_update_ta(self, rclk):
        last_tmp = 0
        tmp = self.ta.alarmclk
        while tmp <= rclk:
            self._intta(tmp)
            last_tmp = tmp
            tmp = self.ta.alarmclk
        if last_tmp != rclk:
            self._cia_do_update_ta(rclk)

    def _cia_update_tb(self, rclk):
        if (self.c_cia[CIA_CRB] & (CIA_CRB_INMODE_TA | CIA_CR_START)) == (CIA_CRB_INMODE_TA | CIA_CR_START):
            self._cia_update_ta(rclk)
        last_tmp = 0
        tmp = self.tb.alarmclk
        while tmp <= rclk:
            self._inttb(tmp)
            last_tmp = tmp
            tmp = self.tb.alarmclk
        if last_tmp != rclk:
            self._cia_do_update_tb(rclk)

    # ---- interrupt flag register delay line -------------------------------------
    def _cia_run_ifr_cycle(self):
        delay = self.ifr_delay
        rclk = self.ifr_clock
        if self.model != CIA_MODEL_6526:
            if delay & CIA_IRQ_ACK0:
                self.irqflags &= ~self.ack_irqflags
                self.ack_irqflags = 0
        else:
            if delay & CIA_IRQ_ACK0:
                self.irqflags &= ~self.ack_irqflags
                self.irqflags &= ~CIA_IM_SET
                self.ack_irqflags = 0
        if self.new_irqflags & self.c_cia[CIA_ICR] & 0x1F:
            if self.model != CIA_MODEL_6526:
                if self.rdi + 1 == rclk:
                    delay |= CIA_IRQ_RAISE1
                    delay |= CIA_IRQ_D7SET1
                else:
                    delay |= CIA_IRQ_RAISE0
                    delay |= CIA_IRQ_D7SET0
            else:
                delay |= CIA_IRQ_RAISE1
                delay |= CIA_IRQ_D7SET1
        if delay & CIA_IRQ_D7SET0:
            self.irqflags |= CIA_IM_SET
        if delay & CIA_IRQ_RAISE0:
            self._my_set_int(True, rclk)
        self.new_irqflags = 0
        delay <<= 1
        delay &= ~CIA_IRQ_CLEAR
        self.ifr_delay = delay & 0xFFFF
        self.ifr_clock += 1

    def _cia_ifr_current(self, rclk, what):
        if self.ta.alarmclk != rclk and self.tb.alarmclk != rclk:
            if what & CIA_IFR_CURRENT:
                self._cia_run_ifr_cycle()
            if what & CIA_IFR_NEXT:
                delay = self.ifr_delay
                if delay & CIA_IRQ_RAISE0:
                    self._my_set_int(True, rclk + 1)          # USE_IRQ_RAISE0_SHORTCUT
                elif delay & CIA_IRQ_RAISE1:
                    self._alarm_set("idle", rclk + 1)

    def _cia_ifr_catchup(self, rclk):
        if self.ifr_clock < rclk:
            while (self.ifr_delay or self.new_irqflags or self.ack_irqflags) and self.ifr_clock < rclk:
                self._cia_run_ifr_cycle()
            self.ifr_clock = rclk

    def _cia_set_irq_flag(self, rclk, bits):
        self._cia_ifr_catchup(rclk)
        self.irqflags |= bits
        self.new_irqflags |= bits
        self.ack_irqflags &= ~bits

    # ---- serial port helpers ----------------------------------------------------
    def _strange_extra_sdr_flags(self, rclk, byte):
        if (1 < self.sr_bits < 15) or (self.sr_bits == 15 and not (self.sdr_delay & CIA_SDR_CNT2)):
            self._schedule_sdr_alarm(rclk, CIA_SDR_SET_SDR_IRQ2)
        if (byte & CIA_CRA_SPMODE_OUT) == CIA_CRA_SPMODE_IN:
            sdr_delay = self.sdr_delay
            cnt_wanted = CIA_SDR_CNT1 | CIA_SDR_CNT2
            force_finish = (sdr_delay & cnt_wanted) != cnt_wanted
            if not force_finish:
                if (self.sr_bits != 2 and not (sdr_delay & CIA_SDR_TOGGLE_CNT2)
                        and not (sdr_delay & CIA_SDR_TOGGLE_CNT1) and (sdr_delay & CIA_SDR_TOGGLE_CNT0)):
                    force_finish = True
            self.sdr_force_finish = force_finish
        else:
            if not self.cnt_out_state and self.sr_bits != 0:
                self.shifter = (self.shifter << 1) & 0x1FFFF
            if self.sdr_force_finish:
                self._schedule_sdr_alarm(rclk, CIA_SDR_SET_SDR_IRQ2)
                self.sdr_force_finish = False

    def _pb67_byte(self, rclk, byte):
        if self.c_cia[CIA_CRA] & CIA_CR_PBON:
            self._cia_update_ta(rclk)
            byte &= 0xBF
            if (self.tat if self.c_cia[CIA_CRA] & CIA_CR_OUTMODE_TOGGLE else self.ta.is_underflow_clk()):
                byte |= 0x40
        if self.c_cia[CIA_CRB] & CIA_CR_PBON:
            self._cia_update_tb(rclk)
            byte &= 0x7F
            if (self.tbt if self.c_cia[CIA_CRB] & CIA_CR_OUTMODE_TOGGLE else self.tb.is_underflow_clk()):
                byte |= 0x80
        return byte

    def _ciacore_update_pb67(self, rclk):
        byte = (self.c_cia[CIA_PRB] | ~self.c_cia[CIA_DDRB]) & 0xFF
        current_called = False
        if (self.c_cia[CIA_CRA] | self.c_cia[CIA_CRB]) & CIA_CR_PBON:
            byte = self._pb67_byte(rclk, byte)
            self._cia_ifr_catchup(rclk)
            self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)
            current_called = True
        self.old_pb = byte
        return current_called

    # ---- register writes ----------------------------------------------------------
    def _store(self, addr, byte, rclk):
        self._run_pending_alarms(rclk)
        c = self.c_cia
        if addr in (CIA_PRA, CIA_DDRA):
            c[addr] = byte
            self.old_pa = (c[CIA_PRA] | ~c[CIA_DDRA]) & 0xFF
        elif addr in (CIA_PRB, CIA_DDRB):
            c[addr] = byte
            self._ciacore_update_pb67(rclk)
            if self.on_pb is not None:
                self.on_pb(rclk, (c[CIA_PRB] | ~c[CIA_DDRB]) & 0xFF)
        elif addr in (CIA_TAL, CIA_TBL, CIA_TAH, CIA_TBH):
            if addr in (CIA_TAL, CIA_TAH):
                self._cia_update_ta(rclk)
                tm = self.ta
            else:
                self._cia_update_tb(rclk)
                tm = self.tb
            self._cia_ifr_catchup(rclk)
            self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)
            if addr in (CIA_TAL, CIA_TBL):
                tm.set_latchlo(rclk, byte)
            else:
                tm.set_latchhi(rclk, byte)
        elif CIA_TOD_TEN <= addr <= CIA_TOD_HR:
            if addr == CIA_TOD_HR:
                byte &= 0x9F
                if (byte & 0x1F) == 0x12 and (c[CIA_CRB] & CIA_CRB_ALARM) == CIA_CRB_ALARM_TOD:
                    byte ^= 0x80
            elif addr in (CIA_TOD_MIN, CIA_TOD_SEC):
                byte &= 0x7F
            else:
                byte &= 0x0F
            if c[CIA_CRB] & CIA_CRB_ALARM_ALARM:
                changed = self.todalarm[addr - CIA_TOD_TEN] != byte
                self.todalarm[addr - CIA_TOD_TEN] = byte
            else:
                if addr == CIA_TOD_TEN and self.todstopped:
                    self.todtickcounter = 0
                    self.todstopped = 0
                if addr == CIA_TOD_HR:
                    self.todstopped = 1
                changed = c[addr] != byte
                if changed:
                    c[addr] = byte
            if changed:
                self._check_ciatodalarm(rclk)
                self._cia_ifr_catchup(rclk)
                self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)
        elif addr == CIA_SDR:
            if (c[CIA_CRA] & CIA_CRA_SPMODE) == CIA_CRA_SPMODE_OUT:
                self._schedule_sdr_alarm(rclk, CIA_SDR_SET1)
            c[addr] = byte
        elif addr == CIA_ICR:
            self._cia_update_ta(rclk)
            self._cia_update_tb(rclk)
            self._cia_ifr_catchup(rclk)
            self._cia_ifr_current(rclk, CIA_IFR_CURRENT)
            if byte & CIA_IM_SET:
                c[CIA_ICR] |= byte & 0x7F
            else:
                c[CIA_ICR] &= ~(byte & 0x7F) & 0xFF
            if self.irqflags & c[CIA_ICR] & 0x7F:
                if not self.irq_enabled:
                    if self.model != CIA_MODEL_6526:
                        if not (self.ifr_delay & CIA_IRQ_READ1):
                            self.ifr_delay |= CIA_IRQ_RAISE0 | CIA_IRQ_D7SET0
                    else:
                        self.ifr_delay |= CIA_IRQ_RAISE1 | CIA_IRQ_D7SET1
            else:
                if self.model == CIA_MODEL_6526 and (self.ifr_delay & CIA_IRQ_ACK_1):
                    self.ifr_delay &= ~(CIA_IRQ_RAISE0 | CIA_IRQ_D7SET0)
            if c[CIA_ICR] & CIA_IM_TA:
                self.ta.set_alarm(rclk)
            if c[CIA_ICR] & CIA_IM_TB:
                self.tb.set_alarm(rclk)
            self._cia_ifr_current(rclk, CIA_IFR_NEXT)
        elif addr == CIA_CRA:
            self._cia_update_ta(rclk)
            if (byte & CIA_CR_START) and not (c[CIA_CRA] & CIA_CR_START):
                self.tat = 1
            if (byte ^ c[CIA_CRA]) & CIA_CRA_SPMODE:
                self._strange_extra_sdr_flags(rclk, byte)
                self.sr_bits = 0
                self.sdr_valid = False
                self.sdr_delay &= ~(ALL_SDR_TOGGLE_CNT | ALL_SDR_NOGGLE_CNT)
                if not self.cnt_out_state:
                    self.cnt_out_state = True
            self.ta.set_ctrl(rclk, byte)
            c[addr] = byte & 0xEF
            if not self._ciacore_update_pb67(rclk):
                self._cia_ifr_catchup(rclk)
                self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)
        elif addr == CIA_CRB:
            if (byte & 1) and not (c[CIA_CRB] & CIA_CR_START):
                self.tbt = 1
            self._cia_update_ta(rclk)
            self._cia_update_tb(rclk)
            if byte & CIA_CRB_INMODE_TA:
                self.ta.set_alarm(rclk)
                self.tb.set_ctrl(rclk, byte | 0x20)
            else:
                self.tb.set_ctrl(rclk, byte)
            c[addr] = byte & 0xEF
            if not self._ciacore_update_pb67(rclk):
                self._cia_ifr_catchup(rclk)
                self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)
        else:
            c[addr] = byte

    # ---- register reads -------------------------------------------------------------
    def _read(self, addr, rclk):
        self._run_pending_alarms(rclk)
        c = self.c_cia
        if addr == CIA_PRA:
            self.last_read = (c[CIA_PRA] | ~c[CIA_DDRA]) & 0xFF
            return self.last_read
        if addr == CIA_PRB:
            byte = (c[CIA_PRB] | ~c[CIA_DDRB]) & 0xFF
            if (c[CIA_CRA] | c[CIA_CRB]) & CIA_CR_PBON:
                byte = self._pb67_byte(rclk, byte)
                self._cia_ifr_catchup(rclk)
                self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)
            self.last_read = byte
            return byte
        if addr in (CIA_TAL, CIA_TAH):
            self._cia_update_ta(rclk)
            self._cia_ifr_catchup(rclk)
            self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)
            self.last_read = (self.ta.cnt >> (8 if addr == CIA_TAH else 0)) & 0xFF
            return self.last_read
        if addr in (CIA_TBL, CIA_TBH):
            self._cia_update_tb(rclk)
            self._cia_ifr_catchup(rclk)
            self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)
            self.last_read = (self.tb.cnt >> (8 if addr == CIA_TBH else 0)) & 0xFF
            return self.last_read
        if CIA_TOD_TEN <= addr <= CIA_TOD_HR:
            if not self.todlatched:
                self.todlatch[:] = c[CIA_TOD_TEN:CIA_TOD_HR + 1]
            if addr == CIA_TOD_TEN:
                self.todlatched = 0
            if addr == CIA_TOD_HR:
                self.todlatched = 1
            self.last_read = self.todlatch[addr - CIA_TOD_TEN]
            return self.last_read
        if addr == CIA_SDR:
            self.last_read = c[CIA_SDR]
            return self.last_read
        if addr == CIA_ICR:
            self._cia_update_ta(rclk)
            self._cia_update_tb(rclk)
            self._cia_ifr_catchup(rclk)
            self._cia_ifr_current(rclk, CIA_IFR_CURRENT)
            self.rdi = rclk
            self.ta.set_alarm(rclk)
            self.tb.set_alarm(rclk)
            if self.irqflags & CIA_IM_TBB:
                self.irqflags &= ~(CIA_IM_TBB | CIA_IM_TB)
            if self.model != CIA_MODEL_6526:
                if self.ifr_delay & CIA_IRQ_RAISE0:
                    if self.irqflags & 0x1F:
                        self.irqflags |= CIA_IM_SET
                if self.irqflags & 0x9F:
                    self.ack_irqflags |= (self.irqflags & 0x9F) | 0x80
                self.ifr_delay |= CIA_IRQ_ACK1
                self.ifr_delay &= ~(CIA_IRQ_RAISE0 | CIA_IRQ_D7SET0)
                result = self.irqflags
            else:
                self.ifr_delay |= CIA_IRQ_ACK1
                self.ifr_delay &= ~CIA_IRQ_RAISE0
                result = self.irqflags
                self.irqflags &= CIA_IM_SET
                self.new_irqflags = 0
            self.ifr_delay |= CIA_IRQ_READ0
            self._my_set_int(False, rclk)
            self._cia_ifr_current(rclk, CIA_IFR_NEXT)
            self.last_read = result & 0xFF
            return self.last_read
        if addr == CIA_CRA:
            self._cia_update_ta(rclk)
            self._cia_ifr_catchup(rclk)
            self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)
            self.last_read = (c[CIA_CRA] & ~CIA_CR_START & 0xFF) | self.ta.is_running()
            return self.last_read
        if addr == CIA_CRB:
            self._cia_update_tb(rclk)
            self._cia_ifr_catchup(rclk)
            self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)
            self.last_read = (c[CIA_CRB] & ~CIA_CR_START & 0xFF) | self.tb.is_running()
            return self.last_read
        self.last_read = c[addr]
        return self.last_read

    # ---- alarm callbacks ----------------------------------------------------------------
    def _intta(self, rclk):
        c = self.c_cia
        self._cia_do_update_ta(rclk)
        self.ta.ack_alarm(rclk)
        if (c[CIA_CRA] & (CIA_CRA_INMODE | CIA_CR_RUNMODE | CIA_CR_START)) == (
                CIA_CRA_INMODE_PHI2 | CIA_CR_RUNMODE_CONTINUOUS | CIA_CR_START):
            if (((c[CIA_ICR] & CIA_IM_TA) and not (self.irqflags & CIA_IM_SET))
                    or (c[CIA_CRA] & (CIA_CRA_SPMODE | CIA_CRA_INMODE))
                    or (c[CIA_CRB] & CIA_CRB_INMODE_TA)):
                self.ta.set_alarm(rclk)
        if c[CIA_CRA] & CIA_CRA_SPMODE_OUT:
            if self.sr_bits != 0 or self.sdr_valid:
                event = CIA_SDR_TOGGLE_CNT1
                if self.sdr_delay & (CIA_SDR_TOGGLE_CNT0 | CIA_SDR_NOGGLE_CNT0):
                    event = CIA_SDR_NOGGLE_CNT1
                self._schedule_sdr_alarm(rclk, event)
        if (c[CIA_CRB] & (CIA_CRB_INMODE_TA | CIA_CR_START)) == (CIA_CRB_INMODE_TA | CIA_CR_START):
            self._cia_update_tb(rclk)
            self._cia_do_step_tb(rclk)

    def _intta_entry(self, rclk):
        self._intta(rclk)
        self._cia_ifr_catchup(rclk)
        self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)

    def _inttb(self, rclk):
        c = self.c_cia
        self._cia_do_update_tb(rclk)
        self.tb.ack_alarm(rclk)
        if (c[CIA_CRB] & (CIA_CRB_INMODE | CIA_CR_RUNMODE | CIA_CR_START)) == (
                CIA_CRB_INMODE_PHI2 | CIA_CR_RUNMODE_CONTINUOUS | CIA_CR_START):
            if c[CIA_ICR] & CIA_IM_TB:
                self.tb.set_alarm(rclk)

    def _inttb_entry(self, rclk):
        self._inttb(rclk)
        self._cia_ifr_catchup(rclk)
        self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)

    def _schedule_sdr_alarm(self, rclk, feed):
        self.sdr_delay |= feed
        self._alarm_set("sdr", rclk)

    def _intsdr(self, rclk):
        c = self.c_cia
        feed = 0
        self._sync_timer_alarms()
        if self.alarm["ta"] == rclk:
            self._intta(rclk)
        if self.sdr_delay & CIA_SDR_SET0:
            if self.sr_bits == 0:
                self.sr_bits = 16
                self.shifter = (c[CIA_SDR] << 1) & 0x1FFFF
            elif self.sr_bits == 1:
                self.shifter |= c[CIA_SDR]
                self.sr_bits = 17
            else:
                self.sdr_valid = True
        if self.sdr_delay & CIA_SDR_TOGGLE_CNT0:
            if self.sr_bits:
                self.sr_bits -= 1
                odd = self.sr_bits & 1
            else:
                odd = 0
            if odd:
                self.cnt_out_state = False
                if self.sr_bits == 1:
                    feed |= CIA_SDR_SET_SDR_IRQ2
                    if self.sdr_valid:
                        self.shifter |= c[CIA_SDR]
                        self.sdr_valid = False
                        self.sr_bits = 17
            else:
                self.shifter = (self.shifter << 1) & 0x1FFFF
                self.cnt_out_state = True
        if self.sdr_delay & CIA_SDR_SET_SDR_IRQ0:
            self._cia_set_irq_flag(rclk, CIA_IM_SDR)
        self.sdr_delay |= feed
        self.sdr_delay = (self.sdr_delay << 1) & 0xFFFFFFFF
        self.sdr_delay &= ~CIA_SDR_CLEAR
        if self.cnt_out_state:
            self.sdr_delay |= CIA_SDR_CNT0
        active = (self.sdr_delay & CIA_SDR_ACTIVE) != 0
        if not active:
            all_cnt = self.sdr_delay & ALL_SDR_CNT
            if all_cnt != 0 and all_cnt != ALL_SDR_CNT:
                active = True
        if active:
            self._alarm_set("sdr", rclk + 1)
        else:
            self._alarm_unset("sdr")

    def _intsdr_entry(self, rclk):
        self._intsdr(rclk)
        self._cia_ifr_catchup(rclk)
        if self.ifr_clock == rclk:
            self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)

    def _inttod(self, rclk):
        c = self.c_cia
        update = False
        self.todticks = self.ticks_per_sec // self.power_freq
        tclk = (self.power_tickcounter * self.ticks_per_sec) // self.power_freq
        if self.power_ticks < tclk:
            self.todticks += 1
        elif self.power_ticks > tclk:
            self.todticks -= 1
        self.power_tickcounter += 1
        if self.power_tickcounter >= self.power_freq:
            self.todticks = self.ticks_per_sec - self.power_ticks
            self.power_tickcounter = 0
            self.power_ticks = 0
        else:
            self.power_ticks += self.todticks
        self.todclk = rclk + self.todticks
        self._alarm_set("tod", self.todclk)
        if not self.todstopped:
            update = self.todtickcounter == (4 if c[CIA_CRA] & CIA_CRA_TODIN_50HZ else 5)
            if update:
                self.todtickcounter = 0
            else:
                self.todtickcounter += 1
                if self.todtickcounter > 5:
                    self.todtickcounter = 0
        if update:
            ts = c[CIA_TOD_TEN] & 0x0F
            sl = c[CIA_TOD_SEC] & 0x0F
            sh = (c[CIA_TOD_SEC] >> 4) & 0x07
            ml = c[CIA_TOD_MIN] & 0x0F
            mh = (c[CIA_TOD_MIN] >> 4) & 0x07
            hl = c[CIA_TOD_HR] & 0x0F
            hh = (c[CIA_TOD_HR] >> 4) & 0x01
            pm = c[CIA_TOD_HR] & 0x80
            ts = (ts + 1) & 0x0F
            if ts == 10:
                ts = 0
                sl = (sl + 1) & 0x0F
                if sl == 10:
                    sl = 0
                    sh = (sh + 1) & 0x07
                    if sh == 6:
                        sh = 0
                        ml = (ml + 1) & 0x0F
                        if ml == 10:
                            ml = 0
                            mh = (mh + 1) & 0x07
                            if mh == 6:
                                mh = 0
                                if (hh == 1 and hl == 2) or (hh == 0 and hl == 9):
                                    hl = hh
                                    hh ^= 1
                                else:
                                    hl = (hl + 1) & 0x0F
                                    if hh == 1 and hl == 2:
                                        pm ^= 0x80
            c[CIA_TOD_TEN] = ts
            c[CIA_TOD_SEC] = sl | (sh << 4)
            c[CIA_TOD_MIN] = ml | (mh << 4)
            c[CIA_TOD_HR] = hl | (hh << 4) | pm
            self._check_ciatodalarm(rclk)

    def _inttod_entry(self, rclk):
        self._inttod(rclk)
        self._cia_ifr_catchup(rclk)
        if self.ifr_clock == rclk:
            self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)

    def _idle(self, rclk):
        self._cia_update_ta(rclk)
        self._cia_update_tb(rclk)
        self._alarm_set("idle", rclk + CIA_MAX_IDLE_CYCLES)
        self._cia_ifr_catchup(rclk)
        if self.ifr_clock == rclk:
            self._cia_ifr_current(rclk, CIA_IFR_CUR_NXT)
