"""MOS 6569/8565/6567 VIC-II: a statement-level Python port of VICE's cycle-based
VIC-II (x64sc, src/viciisc: vicii-cycle.c, vicii-fetch.c, vicii-draw-cycle.c,
vicii-mem.c, vicii-irq.c, vicii-lightpen.c, vicii-chip-model.c).

    Written by Hannu Nuotio, Daniel Kahlin and the VICE team, based on code by
    Ettore Perazzoli and Andreas Boose.

This file is free software; you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation; either version 2 of the License, or (at your option) any later
version.  It is distributed WITHOUT ANY WARRANTY.

Everything that is visible to the CPU is ported: the cycle tables, raster and
bad-line logic, sprite DMA/expansion/crunch, BA and the three-cycle prefetch,
the phi1/phi2 fetches (sprite data, open bus via ``last_read_phi1``), the
graphics and sprite pixel pipelines as far as they decide collisions, the
collision registers and interrupts, and the light pen.  Colour resolution and
the frame buffer are not ported (sid2midi draws no picture), nor is VICE's
random VSP-bug memory corruption (disabled by default in VICE).

Clock: ``cycle(c)`` is VICE's ``vicii_cycle()`` after ``maincpu_clk`` became
``c``; it runs before the CPU's bus access in cycle ``c``.  ``t`` is the next
cycle to run.
"""
import copy

# A $D01E/$D01F read clears the register for the rest of the next cycle's
# drawing and also for the first 4 pixels of the cycle after it (real-hardware
# reference data of VICE testprogs VICII/spritevssprite, which VICE fails).
COLLISION_CLEAR_TAIL = 4
# A bad line triggered while the VIC-II is idle (DMA delay, the "VSP" case)
# fetches that cycle's idle byte from $38FF on the 6569 and $3807 on the
# 8565/8566 instead of $3FFF (VICE testprogs VICII/vsp-tester readme: real
# machines; VICE fails the test). The NTSC 6567s are given the 6569 address
# by analogy (not measured).
VSP_IDLE_ADDR_NMOS, VSP_IDLE_ADDR_HMOS = 0x38FF, 0x3807

NEVER = 1 << 62

PHI1_IDLE, PHI1_REFRESH, PHI1_FETCH_G, PHI1_SPR_PTR, PHI1_SPR_DMA1 = range(5)
SPR_NONE, SPR_DMA, SPR_DISP, SPR_MCBASE, SPR_CRUNCH = range(5)

# name: (cycles per line, raster lines, colour latency, old light pen irq mode)
MODELS = {
    "6569R1": (63, 312, 1, 1),
    "6569": (63, 312, 1, 0),
    "8565": (63, 312, 0, 0),
    "6567": (65, 263, 1, 0),
    "8562": (65, 263, 0, 0),
    "6567R56A": (64, 262, 1, 1),
}

VICII_25ROW_START_LINE, VICII_25ROW_STOP_LINE = 0x33, 0xFB
VICII_24ROW_START_LINE, VICII_24ROW_STOP_LINE = 0x37, 0xF7
VICII_FIRST_DMA_LINE, VICII_LAST_DMA_LINE = 0x30, 0xF7


class Cycle:
    """One decoded entry of VICE's ``cycle_table`` (both half cycles merged)."""
    __slots__ = ("phi1", "spr", "spr_ba", "fetch_ba", "fetch_c", "xpos", "update_vc",
                 "update_rc", "spr_check", "chk_exp", "brd_l", "brd_r")

    def __init__(self):
        self.phi1, self.spr, self.spr_ba, self.fetch_ba, self.fetch_c = PHI1_IDLE, 0, 0, False, False
        self.xpos, self.update_vc, self.update_rc, self.spr_check, self.chk_exp = 0, False, False, SPR_NONE, False
        self.brd_l = self.brd_r = None                 # None, or the CSEL value the check applies to


def build_cycle_table(cpl):
    """cycle_tab_pal / cycle_tab_ntsc / cycle_tab_ntsc_old of vicii-chip-model.c."""
    tab = [Cycle() for _ in range(cpl)]

    def at(n):                                         # 1-based cycle number, wrapping
        return tab[(n - 1) % cpl]

    ptr0 = 58 if cpl == 63 else 59
    for s in range(8):
        p = (ptr0 - 1 + 2 * s) % cpl + 1
        at(p).phi1, at(p).spr = PHI1_SPR_PTR, s
        at(p + 1).phi1, at(p + 1).spr = PHI1_SPR_DMA1, s
        for k in range(-3, 2):
            at(p + k).spr_ba |= 1 << s
    for n in range(11, 16):
        at(n).phi1 = PHI1_REFRESH
    for n in range(16, 56):
        at(n).phi1 = PHI1_FETCH_G
    for n in range(12, 55):
        at(n).fetch_ba = True
    for n in range(15, 55):
        at(n).fetch_c = True                           # FetchC in phi2 also marks the cycle visible
    base = 0x194 if cpl == 63 else 0x19C
    wrap = 0x1F8 if cpl == 63 else 0x200
    for n in range(1, cpl + 1):
        x = (base + 8 * (n - 1)) % wrap
        if cpl == 65 and n >= 63:
            x -= 8                                     # 6567R8: xpos $184 repeats in cycles 62/63
        at(n).xpos = (x >> 3) << 3                     # the table keeps xpos / 8
    at(14).update_vc = True
    at(15).spr_check = SPR_CRUNCH
    at(16).spr_check = SPR_MCBASE
    at(17).brd_l = 1
    at(18).brd_l = 0
    at(56).brd_r = 0
    at(57).brd_r = 1
    at(56).chk_exp = True
    at(58).update_rc = True
    if cpl == 63:
        at(55).spr_check = at(56).spr_check = SPR_DMA
        at(58).spr_check = SPR_DISP
    else:
        at(56).spr_check = at(57).spr_check = SPR_DMA
        at(59 if cpl == 65 else 58).spr_check = SPR_DISP
    return tab


class VicIISC:
    def __init__(self, machine, model="6569"):
        self.m = machine
        self.model = model
        self.cpl, self.lines, self.color_latency, self.lightpen_old_irq_mode = MODELS[model]
        self.frame = self.cpl * self.lines
        self.table = build_cycle_table(self.cpl)
        self.t = 0
        self.ba_log = bytearray(256)
        self.irq = False
        self.ram01 = bytearray(2)                      # RAM under the 6510 port, seen by the VIC-II
        self.regs = bytearray(0x40)
        self.raster_cycle = self.cpl - 1
        self.raster_line = self.lines - 1
        self.cycle_flags = self.table[self.raster_cycle]
        self.start_of_frame = 0
        self.irq_status = 0
        self.raster_irq_line = 0
        self.raster_irq_triggered = 0
        self.vbuf = bytearray(41)
        self.cbuf = bytearray(41)
        self.gbuf = 0
        self.ysmooth = 0
        self.allow_bad_lines = 0
        self.sprite_sprite_collisions = 0
        self.sprite_background_collisions = 0
        self.clear_collisions = 0
        self.idle_state = 0
        self.vcbase = self.vc = self.rc = self.vmli = 0
        self.bad_line = 0
        self.lp_state = 0
        self.lp_triggered = 0
        self.lp_x = self.lp_y = self.lp_x_extra_bits = 0
        self.lp_trigger_cycle = NEVER
        self.reg11_delay = 0
        self.prefetch_cycles = 0
        self.sprite_display_bits = 0
        self.sprite_dma = 0
        self.spr_data = [0] * 8
        self.spr_mc = [0] * 8
        self.spr_mcbase = [0] * 8
        self.spr_pointer = [0] * 8
        self.spr_exp_flop = [1] * 8
        self.spr_x = [0] * 8
        self.last_read_phi1 = 0xFF
        self.last_bus_phi2 = 0xFF
        self.vborder = self.set_vborder = self.main_border = 1
        self.refresh_counter = 0xFF
        # vicii-draw-cycle.c state (colour/priority only as far as collisions need it)
        self.gbuf_pipe0_reg = self.cbuf_pipe0_reg = 0
        self.gbuf_pipe1_reg = self.cbuf_pipe1_reg = 0
        self.xscroll_pipe = self.vmode11_pipe = self.vmode16_pipe = self.vmode16_pipe2 = 0
        self.gbuf_reg = self.gbuf_mc_flop = self.gbuf_pixel_reg = self.cbuf_reg = 0
        self.dmli = 0
        self.sprite_x_pipe = [0] * 8
        self.sprite_pri_bits = self.sprite_mc_bits = self.sprite_expx_bits = 0
        self.sprite_pending_bits = self.sprite_active_bits = self.sprite_halt_bits = 0
        self.sbuf_reg = [0] * 8
        self.sbuf_pixel_reg = [0] * 8
        self.sbuf_expx_flops = self.sbuf_mc_flops = 0
        self.pri_buffer = [0] * 8
        self.cycle_flags_pipe = self.table[0]
        self.gfx_deferred = None                       # inputs of the last graphics loop not yet run
        self.clear_tail = 0                            # register whose clear also drops the next cycle's first pixels

    # ---- machine interface ------------------------------------------------------
    @property
    def reg(self):
        return self.regs

    def save(self):
        return {k: copy.deepcopy(v) for k, v in self.__dict__.items() if k not in ("m", "table")}

    def load(self, state):
        for k, v in state.items():
            setattr(self, k, copy.deepcopy(v))

    def sync(self, t):
        """Run the cycles < t."""
        cycle = self.cycle
        c = self.t
        while c < t:
            self.ba_log[c & 0xFF] = cycle(c)
            c += 1
        if c > self.t:
            self.t = c

    def next_irq_cycle(self):
        if self.irq:
            return None
        if self.regs[0x1A] & 0x0E:
            return self.t                              # collisions and the light pen: cycle by cycle
        if not self.regs[0x1A] & 0x01:
            return None
        return self.t + self._cycles_to_raster(self.raster_irq_line)

    def _cycles_to_raster(self, line):
        """Cycles from the next one to run until the raster reaches ``line`` (upper bound 1 frame)."""
        if line >= self.lines:
            return NEVER
        cur = self.raster_line * self.cpl + self.raster_cycle
        want = line * self.cpl + (1 if line == 0 else 0)
        d = (want - cur) % self.frame
        return max(0, d - 1)

    def rdy(self, t):
        """CPU hook: first cycle >= t (CPU clock) whose ``vicii_cycle()`` left BA high."""
        m = self.m
        base = m.time_base
        c = base + t
        if c < self.t - 200:
            raise RuntimeError("VIC-II ran ahead of the CPU")
        while True:
            if self.t <= c:
                self.sync(c + 1)
            if not self.ba_log[c & 0xFF]:
                break
            c += 1
        m.cpu.rdy_until = 0
        return c - base

    def next_ba_cycle(self, c):
        return c

    def read_phi1(self):
        self.sync(self.m.now() + 1)
        return self.last_read_phi1

    def note_port_write(self, a):
        """The 6510 port write also stores the phi1 bus value into RAM $00/$01."""
        self.sync(self.m.now() + 1)
        self.ram01[a] = self.last_read_phi1

    def set_light_pen(self, mclk, state):
        """vicii_set_light_pen(): CIA #1 PB4 / control port 1 fire line."""
        self.sync(mclk + 1)
        if state:
            self.lp_x_extra_bits = 2 if self.color_latency else 1
            self.lp_trigger_cycle = mclk + 1
        self.lp_state = state

    def _set_line(self, clk):
        """vicii_irq_set_line() with the CPU clock of the change."""
        if self.irq_status & self.regs[0x1A]:
            self.irq_status |= 0x80
            low = True
        else:
            self.irq_status &= 0x7F
            low = False
        if low != self.irq:
            self.irq = low
            self.m.predict_dirty = True
            self.m.irq_changed("vic", low, clk if low else clk + 1)

    # ---- memory as seen by the VIC-II (vicii-fetch.c) -------------------------------
    def _fetch(self, addr):
        m = self.m
        c2 = m.cia2.c_cia
        addr = (addr + ((3 - ((c2[0] | (~c2[2] & 0xFF)) & 3)) << 14)) & 0xFFFF
        if (addr & 0x7000) == 0x1000 and m.chargen:
            return m.chargen[addr & 0xFFF]
        if addr < 2:
            return self.ram01[addr]
        return m.ram[addr]

    def _is_char_rom(self, addr):
        c2 = self.m.cia2.c_cia
        addr = (addr + ((3 - ((c2[0] | (~c2[2] & 0xFF)) & 3)) << 14)) & 0xFFFF
        return (addr & 0x7000) == 0x1000

    def _g_fetch_addr(self, mode):
        if mode & 0x20:
            a = (self.vc << 3) | self.rc
            a |= (self.regs[0x18] & 0x8) << 10
        else:
            a = (self.vbuf[self.vmli] << 3) | self.rc
            a |= (self.regs[0x18] & 0xE) << 10
        if mode & 0x40:
            a &= 0x39FF
        return a & 0xFFFF

    def _fetch_matrix(self):
        if self.prefetch_cycles:
            self.vbuf[self.vmli] = 0xFF
            self.cbuf[self.vmli] = self.m.ram[self.m.cpu.pc & 0xFFFF] & 0xF
        else:
            self.vbuf[self.vmli] = self._fetch(((self.regs[0x18] & 0xF0) << 6) + self.vc)
            self.cbuf[self.vmli] = self.m.ram[0xD800 + self.vc] & 0xF

    def _fetch_graphics(self):
        if self.color_latency:
            r11, d = self.regs[0x11], self.reg11_delay
            addr = self._g_fetch_addr(r11 | (d & 0x20))
            if (r11 ^ d) & 0x20:
                addr_from = self._g_fetch_addr(d)
                addr_to = self._g_fetch_addr(r11)
                if not self._is_char_rom(addr_from) and self._is_char_rom(addr_to):
                    addr = (addr_from & 0xFF) | (addr_to & 0x3F00)
        else:
            addr = self._g_fetch_addr(self.reg11_delay)
        data = self._fetch(addr)
        self.gbuf = data
        self.vmli += 1
        self.vc = (self.vc + 1) & 0x3FF
        return data

    def _fetch_idle_gfx(self):
        reg11 = self.regs[0x11] if self.color_latency else self.reg11_delay
        data = self._fetch(0x39FF if reg11 & 0x40 else 0x3FFF)
        self.gbuf = data
        return data

    def _phi1_fetch(self, fl):
        p = fl.phi1
        if p == PHI1_FETCH_G:
            return self._fetch_graphics() if not self.idle_state else self._fetch_idle_gfx()
        if p == PHI1_SPR_PTR:
            s = fl.spr
            self.spr_pointer[s] = v = self._fetch(((self.regs[0x18] & 0xF0) << 6) + 0x3F8 + s)
            return v
        if p == PHI1_SPR_DMA1:
            s = fl.spr
            if self.sprite_dma & (1 << s):
                v = self._fetch((self.spr_pointer[s] << 6) + self.spr_mc[s])
                self.spr_mc[s] = (self.spr_mc[s] + 1) & 0x3F
            else:
                v = self._fetch(0x3FFF)
            self.spr_data[s] = (self.spr_data[s] & 0xFF00FF) | (v << 8)
            return v
        if p == PHI1_REFRESH:
            v = self._fetch(0x3F00 + self.refresh_counter)
            self.refresh_counter = (self.refresh_counter - 1) & 0xFF
            return v
        return self._fetch(0x3FFF)

    def _fetch_sprites(self, fl):
        """vicii_fetch_sprites(): the phi2 sprite fetches of the previous cycle."""
        p = fl.phi1
        if p != PHI1_SPR_PTR and p != PHI1_SPR_DMA1:
            return
        s = fl.spr
        sprdata = self.last_bus_phi2
        if self.sprite_dma & (1 << s):
            if not self.prefetch_cycles:
                sprdata = self._fetch((self.spr_pointer[s] << 6) + self.spr_mc[s])
            self.spr_mc[s] = (self.spr_mc[s] + 1) & 0x3F
        if p == PHI1_SPR_PTR:
            self.spr_data[s] = (self.spr_data[s] & 0x00FFFF) | (sprdata << 16)
        else:
            self.spr_data[s] = (self.spr_data[s] & 0xFFFF00) | sprdata

    # ---- vicii-cycle.c ----------------------------------------------------------------
    def cycle(self, clk):
        regs = self.regs
        self._fetch_sprites(self.cycle_flags)

        rcyc = self.raster_cycle + 1
        if rcyc == self.cpl:
            rcyc = 0
        self.raster_cycle = rcyc
        fl = self.cycle_flags = self.table[rcyc]

        if (fl.phi1 == PHI1_FETCH_G and self.idle_state and not self.bad_line
                and self.allow_bad_lines and (self.raster_line & 7) == self.ysmooth):
            # bad line triggered during idle state: altered idle byte address
            data = self._fetch(VSP_IDLE_ADDR_NMOS if self.color_latency else VSP_IDLE_ADDR_HMOS)
            self.gbuf = data
            self.last_read_phi1 = data
        else:
            self.last_read_phi1 = self._phi1_fetch(fl)

        csel = 1 if regs[0x16] & 0x08 else 0
        if fl.brd_l == csel:
            self._check_vborder_bottom(self.raster_line)
            self.vborder = self.set_vborder
            if self.vborder == 0:
                self.main_border = 0
        if fl.brd_r == csel:
            self.main_border = 1

        can_sprite_sprite = self.sprite_sprite_collisions == 0
        can_sprite_background = self.sprite_background_collisions == 0

        self._draw_cycle()

        self.clear_tail = 0
        if self.clear_collisions == 0x1E:
            self.sprite_sprite_collisions = 0
            self.clear_collisions = 0
            self.clear_tail = 0x1E
        elif self.clear_collisions == 0x1F:
            self.sprite_background_collisions = 0
            self.clear_collisions = 0
            self.clear_tail = 0x1F

        if can_sprite_sprite and self.sprite_sprite_collisions:
            self.irq_status |= 0x4
            self._set_line(clk)
        if can_sprite_background and self.sprite_background_collisions:
            self.irq_status |= 0x2
            self._set_line(clk)

        if rcyc == 0:
            if self.raster_line == self.lines - 1:     # vicii_cycle_end_of_line
                self.start_of_frame = 1
            if self.raster_line == VICII_FIRST_DMA_LINE and not self.allow_bad_lines and regs[0x11] & 0x10:
                self.allow_bad_lines = 1
            if self.raster_line == VICII_LAST_DMA_LINE:
                self.allow_bad_lines = 0
            self.bad_line = 0

        if self.start_of_frame:
            if rcyc == 1:
                self.start_of_frame = 0
                self.raster_line = 0
                self.refresh_counter = 0xFF
                self.allow_bad_lines = 0
                self.vcbase = 0
                self.vc = 0
                self.lp_triggered = 0
                if self.lp_state:
                    self.lp_x_extra_bits = 2 if self.color_latency else 1
                    self._trigger_light_pen(1, clk)
        elif rcyc == 0:
            self.raster_line += 1

        line = self.raster_line
        if line == self.raster_irq_line:
            if not self.raster_irq_triggered:
                if not self.irq_status & 0x1:
                    self.irq_status |= 0x1
                    self._set_line(clk)
                self.raster_irq_triggered = 1
        else:
            self.raster_irq_triggered = 0

        rsel = regs[0x11] & 0x08
        if line == (VICII_25ROW_START_LINE if rsel else VICII_24ROW_START_LINE) and regs[0x11] & 0x10:
            self.vborder = 0
            self.set_vborder = 0
        self._check_vborder_bottom(line)
        if rcyc == 0:
            self.vborder = self.set_vborder

        chk = fl.spr_check
        if chk == SPR_MCBASE:
            for i in range(8):
                if self.spr_exp_flop[i]:
                    self.spr_mcbase[i] = self.spr_mc[i]
                    if self.spr_mcbase[i] == 63:
                        self.sprite_dma &= ~(1 << i)
        elif chk == SPR_DMA:
            enable = regs[0x15]
            if enable:
                low = line & 0xFF
                for i in range(8):
                    b = 1 << i
                    if enable & b and regs[i * 2 + 1] == low and not self.sprite_dma & b:
                        self.sprite_dma |= b
                        self.spr_mcbase[i] = 0
                        self.spr_exp_flop[i] = 1
        if fl.chk_exp:
            dma_exp = self.sprite_dma & regs[0x17]
            if dma_exp:
                for i in range(8):
                    if dma_exp & (1 << i):
                        self.spr_exp_flop[i] ^= 1
        if chk == SPR_DISP:
            enable = regs[0x15]
            low = line & 0xFF
            for i in range(8):
                b = 1 << i
                self.spr_mc[i] = self.spr_mcbase[i]
                if self.sprite_dma & b:
                    if enable & b and regs[i * 2 + 1] == low:
                        self.sprite_display_bits |= b
                else:
                    self.sprite_display_bits &= ~b

        if line == VICII_FIRST_DMA_LINE and not self.allow_bad_lines:
            self.allow_bad_lines = 1 if regs[0x11] & 0x10 else 0
        if self.allow_bad_lines:
            if (line & 7) == self.ysmooth:
                self.bad_line = 1
                self.idle_state = 0
            else:
                self.bad_line = 0

        if fl.update_vc:
            self.vc = self.vcbase
            self.vmli = 0
            if self.bad_line:
                self.rc = 0
        if fl.update_rc:
            if self.rc == 7:
                self.idle_state = 1
                self.vcbase = self.vc
            if not self.idle_state or self.bad_line:
                self.rc = (self.rc + 1) & 7
                self.idle_state = 0

        ba_low = 1 if (self.bad_line and fl.fetch_ba) or (self.sprite_dma & fl.spr_ba) else 0
        if ba_low:
            if self.prefetch_cycles:
                self.prefetch_cycles -= 1
        else:
            self.prefetch_cycles = 4

        if self.bad_line and fl.fetch_c:
            self._fetch_matrix()

        self.last_bus_phi2 = 0xFF
        self.reg11_delay = regs[0x11]

        if self.lp_trigger_cycle == clk:
            self._trigger_light_pen(0, clk)
        return ba_low

    def _check_vborder_bottom(self, line):
        if line == (VICII_25ROW_STOP_LINE if self.regs[0x11] & 0x08 else VICII_24ROW_STOP_LINE):
            self.set_vborder = 1

    # ---- vicii-draw-cycle.c -----------------------------------------------------------
    def _draw_graphics8(self, xs, cbuf1, gbuf1, vm11, vm16_2, r11, r16, pri):
        """draw_graphics8() pixel loop; returns the pipe values after the cycle."""
        cl = self.color_latency
        gbuf_reg, flop, px = self.gbuf_reg, self.gbuf_mc_flop, self.gbuf_pixel_reg
        cbuf_reg = self.cbuf_reg
        vm16 = self.vmode16_pipe
        for i in range(8):
            if i == 4:
                vm16 = (r16 & 0x10) >> 2
                if cl:
                    vm11 |= (r11 & 0x60) >> 2
            elif i == 6:
                if cl:
                    vm11 &= (r11 & 0x60) >> 2
            elif i == 7:
                if vm16 and not vm16_2:
                    flop = 0
                vm16_2 = vm16
            if i == xs:
                cbuf_reg = cbuf1
                gbuf_reg = gbuf1
                flop = 1
            if vm16_2:
                if (vm11 & 0x08) or (cbuf_reg & 0x08):
                    if flop:
                        px = gbuf_reg >> 6
                else:
                    px = 3 if gbuf_reg & 0x80 else 0
            elif gbuf_reg & 0x80:
                px = 2 if ((vm11 & 0x08) or (cbuf_reg & 0x08)) else 3
            else:
                px = 0
            gbuf_reg = (gbuf_reg << 1) & 0xFF
            flop ^= 1
            if pri is not None:
                pri[i] = px & 0x2
        self.gbuf_reg, self.gbuf_mc_flop, self.gbuf_pixel_reg = gbuf_reg, flop, px
        self.cbuf_reg = cbuf_reg
        self.vmode16_pipe = vm16

    def _draw_cycle(self):
        fl = self.cycle_flags_pipe
        regs = self.regs
        cl = self.color_latency
        r11, r16 = regs[0x11], regs[0x16]

        # draw_sprites8() needs the pixel priorities of this cycle only when a
        # sprite is (or may become) active; otherwise the graphics loop is
        # deferred: its end state depends only on this cycle's inputs.
        xpos = fl.xpos
        spr_disp = fl.spr_check == SPR_DISP
        pending_any = self.sprite_pending_bits | (self.sprite_display_bits if spr_disp else 0)
        candidates = 0
        if pending_any:
            xp = self.sprite_x_pipe
            base = xpos & 0x1F8
            for s in range(8):
                if base == (xp[s] & 0x1F8):
                    candidates |= 1 << s
        need = self.sprite_active_bits or (candidates & pending_any)

        vm11, vm16_2 = self.vmode11_pipe, self.vmode16_pipe2
        if need:
            deferred = self.gfx_deferred
            if deferred is not None:
                self.gfx_deferred = None
                self._draw_graphics8(*deferred, None)
            self._draw_graphics8(self.xscroll_pipe, self.cbuf_pipe1_reg, self.gbuf_pipe1_reg,
                                 vm11, vm16_2, r11, r16, self.pri_buffer)
        else:
            self.gfx_deferred = (self.xscroll_pipe, self.cbuf_pipe1_reg, self.gbuf_pipe1_reg,
                                 vm11, vm16_2, r11, r16)
            self.vmode16_pipe = (r16 & 0x10) >> 2
        self.vmode11_pipe = (r11 & 0x60) >> 2           # both chip models end the cycle here
        self.vmode16_pipe2 = self.vmode16_pipe

        self.cbuf_pipe1_reg = self.cbuf_pipe0_reg
        self.gbuf_pipe1_reg = self.gbuf_pipe0_reg
        if fl.fetch_c and self.vborder == 0:
            self.gbuf_pipe0_reg = self.gbuf
            self.xscroll_pipe = r16 & 0x07
            if not self.idle_state:
                self.cbuf_pipe0_reg = self.cbuf[self.dmli]
                self.dmli = (self.dmli + 1) & 0xFF
            else:
                self.cbuf_pipe0_reg = 0
        else:
            self.gbuf_pipe0_reg = 0
            self.dmli = 0

        dma_cycle_0 = (1 << fl.spr) if fl.phi1 == PHI1_SPR_PTR else 0
        dma_cycle_2 = (1 << fl.spr) if fl.phi1 == PHI1_SPR_DMA1 else 0
        if need:
            for i in range(8):
                if i == 2:
                    self.sprite_active_bits &= ~dma_cycle_2
                elif i == 3:
                    self.sprite_halt_bits |= dma_cycle_0
                elif i == 4:
                    if spr_disp:
                        self.sprite_pending_bits = self.sprite_display_bits
                    if dma_cycle_2:
                        self.sbuf_reg[fl.spr] = self.spr_data[fl.spr]
                elif i == 6:
                    if not cl:
                        self._update_sprite_mc_bits_8565()
                    self.sprite_pri_bits = regs[0x1B]
                    self.sprite_expx_bits = regs[0x1D]
                elif i == 7:
                    if cl:
                        self._update_sprite_mc_bits_6569()
                    self.sprite_halt_bits &= ~dma_cycle_2
                if candidates and self.sprite_pending_bits:
                    self._trigger_sprites(xpos + i, candidates)
                if self.sprite_active_bits:
                    self._draw_sprites(i)
        else:
            if dma_cycle_0:
                self.sprite_halt_bits |= dma_cycle_0
            if spr_disp:
                self.sprite_pending_bits = self.sprite_display_bits
            if dma_cycle_2:
                self.sbuf_reg[fl.spr] = self.spr_data[fl.spr]
                self.sprite_halt_bits &= ~dma_cycle_2
            if regs[0x1C] != self.sprite_mc_bits:
                if cl:
                    self._update_sprite_mc_bits_6569()
                else:
                    self._update_sprite_mc_bits_8565()
            self.sprite_pri_bits = regs[0x1B]
            self.sprite_expx_bits = regs[0x1D]
        spr_x = self.spr_x
        if self.sprite_x_pipe != spr_x:
            self.sprite_x_pipe = spr_x[:]
        self.cycle_flags_pipe = self.cycle_flags

    def _update_sprite_mc_bits_6569(self):
        nxt = self.regs[0x1C]
        toggled = nxt ^ self.sprite_mc_bits
        self.sbuf_mc_flops &= ~toggled & 0xFF
        self.sprite_mc_bits = nxt

    def _update_sprite_mc_bits_8565(self):
        nxt = self.regs[0x1C]
        toggled = nxt ^ self.sprite_mc_bits
        self.sbuf_mc_flops ^= toggled & ~self.sbuf_expx_flops & 0xFF
        self.sbuf_mc_flops |= toggled & ~self.sbuf_expx_flops & ~nxt & 0xFF
        self.sprite_mc_bits = nxt

    def _trigger_sprites(self, xpos, candidate_bits):
        xp = self.sprite_x_pipe
        for s in range(8):
            m = 1 << s
            if (candidate_bits & m and self.sprite_pending_bits & m and not self.sprite_active_bits & m
                    and not self.sprite_halt_bits & m and xpos == xp[s]):
                self.sbuf_expx_flops |= m
                self.sbuf_mc_flops |= m
                self.sprite_active_bits |= m

    def _draw_sprites(self, i):
        collision_mask = 0
        sbuf, spix = self.sbuf_reg, self.sbuf_pixel_reg
        for s in range(7, -1, -1):
            m = 1 << s
            if self.sprite_active_bits & m:
                if sbuf[s] or spix[s]:
                    if not self.sprite_halt_bits & m:
                        if self.sbuf_expx_flops & m:
                            if self.sprite_mc_bits & m:
                                if self.sbuf_mc_flops & m:
                                    spix[s] = (sbuf[s] >> 22) & 0x03
                                self.sbuf_mc_flops ^= m
                            elif (self.sbuf_mc_flops & m) or self.color_latency:
                                spix[s] = ((sbuf[s] >> 23) & 0x01) << 1
                            else:
                                self.sbuf_mc_flops |= m
                        if self.sbuf_expx_flops & m:
                            sbuf[s] = (sbuf[s] << 1) & 0xFFFFFFFF
                        if self.sprite_expx_bits & m:
                            self.sbuf_expx_flops ^= m
                        else:
                            self.sbuf_expx_flops |= m
                    if spix[s]:
                        collision_mask |= m
                else:
                    self.sprite_active_bits &= ~m
        tail = self.clear_tail if i < COLLISION_CLEAR_TAIL else 0
        if collision_mask:
            if self.pri_buffer[i] and tail != 0x1F:
                self.sprite_background_collisions |= collision_mask
        if collision_mask & (collision_mask - 1) and tail != 0x1E:
            self.sprite_sprite_collisions |= collision_mask

    # ---- vicii-lightpen.c -----------------------------------------------------------
    def _trigger_light_pen(self, retrigger, clk):
        self.lp_trigger_cycle = NEVER
        if self.lp_triggered:
            return
        self.lp_triggered = 1
        y = self.raster_line
        if y == self.lines - 1 and self.raster_cycle > 0:
            return
        x = (self.table[self.raster_cycle].xpos // 2) + self.lp_x_extra_bits
        if retrigger:
            x = 0xD5 if self.cpl == 65 else 0xD1
            if self.lightpen_old_irq_mode:
                self.irq_status |= 0x8
                self._set_line(clk)
        self.lp_x = x & 0xFF
        self.lp_y = y & 0xFF
        self.lp_x_extra_bits = 0
        if not self.lightpen_old_irq_mode:
            self.irq_status |= 0x8
            self._set_line(clk)

    # ---- vicii-mem.c ------------------------------------------------------------------
    def write(self, addr, value):
        now = self.m.now()
        self.sync(now + 1)
        regs = self.regs
        addr &= 0x3F
        value &= 0xFF
        self.last_bus_phi2 = value
        if addr < 0x10:
            if addr & 1:
                regs[addr] = value
            else:
                regs[addr] = value
                n = addr >> 1
                self.spr_x[n] = value | (0x100 if regs[0x10] & (1 << n) else 0)
        elif addr == 0x10:
            regs[0x10] = value
            for i in range(8):
                self.spr_x[i] = regs[2 * i] | (0x100 if value & (1 << i) else 0)
        elif addr == 0x11:
            self.ysmooth = value & 7
            regs[0x11] = value
            self.raster_irq_line = regs[0x12] | ((value & 0x80) << 1)
        elif addr == 0x12:
            regs[0x12] = value
            self.raster_irq_line = value | ((regs[0x11] & 0x80) << 1)
        elif addr in (0x13, 0x14, 0x1E, 0x1F) or addr >= 0x2F:
            pass
        elif addr == 0x17:
            if value != regs[0x17]:
                for i in range(8):
                    b = 1 << i
                    if not value & b and not self.spr_exp_flop[i]:
                        if self.cycle_flags.spr_check == SPR_CRUNCH:
                            mc, mcbase = self.spr_mc[i], self.spr_mcbase[i]
                            self.spr_mc[i] = (0x2A & (mcbase & mc)) | (0x15 & (mcbase | mc))
                        self.spr_exp_flop[i] = 1
                regs[0x17] = value
        elif addr == 0x19:
            self.irq_status &= ~((value & 0xF) | 0x80) & 0xFF
            self._set_line(now)
        elif addr == 0x1A:
            regs[0x1A] = value & 0xF
            self._set_line(now)
        elif addr >= 0x20:
            regs[addr] = value & 0x0F
        else:
            regs[addr] = value

    def read(self, addr):
        now = self.m.now()
        self.sync(now + 1)
        regs = self.regs
        addr &= 0x3F
        if addr == 0x11:
            value = (regs[0x11] & 0x7F) | ((self.raster_line & 0x100) >> 1)
        elif addr == 0x12:
            value = self.raster_line & 0xFF
        elif addr == 0x13:
            value = self.lp_x
        elif addr == 0x14:
            value = self.lp_y
        elif addr == 0x16:
            value = regs[0x16] | 0xC0
        elif addr == 0x18:
            value = regs[0x18] | 0x01
        elif addr == 0x19:
            value = self.irq_status | 0x70
        elif addr == 0x1A:
            value = regs[0x1A] | 0xF0
        elif addr == 0x1E:
            regs[0x1E] = self.sprite_sprite_collisions
            self.clear_collisions = 0x1E
            value = regs[0x1E]
        elif addr == 0x1F:
            regs[0x1F] = self.sprite_background_collisions
            self.clear_collisions = 0x1F
            value = regs[0x1F]
        elif 0x20 <= addr <= 0x2E:
            value = regs[addr] | 0xF0
        elif addr >= 0x2F:
            value = 0xFF
        else:
            value = regs[addr]
        self.last_bus_phi2 = value
        return value

    # ---- start from the fast model ----------------------------------------------------
    @classmethod
    def from_fast(cls, machine, fast, model):
        """Take over from ``Vic6569`` at the current time.

        Registers, raster position, interrupt state and sprite DMA state come
        from the fast model; the line state (VC, RC, idle state, bad lines,
        border flags) is replayed from the start of the frame with the current
        registers.  The pixel pipelines start empty: a sprite that is already
        being drawn in the current line is picked up in the next line."""
        v = cls(machine, model)
        t = machine.now()
        fast.sync(t)
        fast._spr_sync(t)
        regs = v.regs
        regs[:0x2F] = fast.reg[:0x2F]
        regs[0x12] = fast.compare & 0xFF
        regs[0x11] = (regs[0x11] & 0x7F) | ((fast.compare >> 1) & 0x80)
        regs[0x1A] = fast.mask & 0x0F
        regs[0x19] = regs[0x1E] = regs[0x1F] = 0
        for a in range(0x20, 0x2F):
            regs[a] &= 0x0F
        v.raster_irq_line = fast.compare
        v.ysmooth = regs[0x11] & 7
        for i in range(8):
            v.spr_x[i] = regs[2 * i] | (0x100 if regs[0x10] & (1 << i) else 0)
        v.sprite_x_pipe = v.spr_x[:]
        v.irq_status = fast.flags | (0x80 if fast.irq else 0)
        v.irq = fast.irq
        for i in range(8):
            if fast.spr_disp[i]:
                v.sprite_dma |= 1 << i
                v.sprite_display_bits |= 1 << i
            v.spr_mcbase[i] = v.spr_mc[i] = fast.spr_mcbase[i]
            v.spr_exp_flop[i] = 1 if fast.spr_ff[i] else 0
        v.sprite_pending_bits = v.sprite_display_bits
        v.sprite_pri_bits, v.sprite_expx_bits, v.sprite_mc_bits = regs[0x1B], regs[0x1D], regs[0x1C]
        # replay the line logic of this frame up to cycle t - 1
        start = (t - 1) - ((t - 1) % v.frame)
        v.raster_line, v.raster_cycle = 0, 0
        v.rc, v.idle_state, v.vcbase, v.vc = 7, 1, 0, 0
        v.allow_bad_lines = 0
        v.vborder = v.set_vborder = v.main_border = 1
        for c in range(start + 1, t):
            v._replay_line_logic(c - start)
        v.t = t
        v.raster_cycle = (t - 1) % v.cpl
        off = (t - 1) % v.frame
        if off < 1:
            v.raster_line, v.start_of_frame = v.lines - 1, 1
        else:
            v.raster_line = off // v.cpl
        v.cycle_flags = v.cycle_flags_pipe = v.table[v.raster_cycle]
        v.raster_irq_triggered = 1 if v.raster_line == v.raster_irq_line else 0
        v.reg11_delay = regs[0x11]
        v.prefetch_cycles = 4
        pb = machine.cia1.c_cia
        v.lp_state = 0 if (pb[1] | (~pb[3] & 0xFF)) & 0x10 else 1
        v.lp_triggered = 1 if v.lp_state else 0
        v.ram01[0], v.ram01[1] = 0xFF, 0xFF
        return v

    def _replay_line_logic(self, off):
        """Bad-line, VC/RC and border state for frame offset ``off`` (no fetches)."""
        rcyc = off % self.cpl
        line = off // self.cpl
        fl = self.table[rcyc]
        regs = self.regs
        csel = 1 if regs[0x16] & 0x08 else 0
        if fl.brd_l == csel:
            self._check_vborder_bottom(line)
            self.vborder = self.set_vborder
            if self.vborder == 0:
                self.main_border = 0
        if fl.brd_r == csel:
            self.main_border = 1
        if rcyc == 0:
            if line - 1 == VICII_LAST_DMA_LINE:
                self.allow_bad_lines = 0
            self.bad_line = 0
        rsel = regs[0x11] & 0x08
        if line == (VICII_25ROW_START_LINE if rsel else VICII_24ROW_START_LINE) and regs[0x11] & 0x10:
            self.vborder = 0
            self.set_vborder = 0
        self._check_vborder_bottom(line)
        if rcyc == 0:
            self.vborder = self.set_vborder
        if line == VICII_FIRST_DMA_LINE and not self.allow_bad_lines:
            self.allow_bad_lines = 1 if regs[0x11] & 0x10 else 0
        if self.allow_bad_lines:
            if (line & 7) == self.ysmooth:
                self.bad_line = 1
                self.idle_state = 0
            else:
                self.bad_line = 0
        if fl.phi1 == PHI1_FETCH_G and not self.idle_state:
            self.vc = (self.vc + 1) & 0x3FF
        if fl.update_vc:
            self.vc = self.vcbase
            if self.bad_line:
                self.rc = 0
        if fl.update_rc:
            if self.rc == 7:
                self.idle_state = 1
                self.vcbase = self.vc
            if not self.idle_state or self.bad_line:
                self.rc = (self.rc + 1) & 7
                self.idle_state = 0
