"""Cycle-exact NMOS 6502 / 6510 core for the sid2midi C64 model.

Every CPU cycle is exactly one bus access -- a read or a write -- performed
at its real cycle number, following the per-cycle tables of the NMOS 6502
("64doc"):

* dummy reads of the next byte by implied instructions, of the unfixed
  address by indexed instructions (always for stores and read-modify-write,
  on page crossing for reads), of the base address by zero-page indexed and
  (zp,X) modes, of the stack by pull/JSR/RTS/RTI, and of the program counter
  by taken branches;
* read-modify-write instructions write the unmodified value before the result;
* interrupts are polled between instructions from the line state two cycles
  before the opcode fetch (the second-to-last cycle; RDY stalls inside the
  instruction count, a stall of the opcode fetch itself comes after the
  poll -- irqdma); CLI, SEI and PLP change
  the I flag after that point, so their effect is seen one instruction late
  (a RDY stall of the last cycle repeats the decision: see step()); a
  taken branch that does not cross a page skips the poll of its last cycle;
  an NMI that arrives while BRK or an IRQ pushes its frame takes over the
  vector;
* SHA/SHX/SHY/TAS store (register & (base high byte + 1)); when indexing
  crosses a page the high byte of the target address becomes that value;
  LXA uses the constant $EE, ANE $EF ($EE when RDY held its operand read,
  as measured on a C64); JAM halts the CPU (leaving the bus at $FFFF/$FFFE).

The machine connects three optional hooks:

``rdy(t)``           first cycle >= t at which a read may happen (VIC-II BA)
``pending(clk, I, delayed, irq_clk)``  interrupt to take before the opcode at cycle clk
                     (irq_clk, when given, replaces clk for the IRQ line sample)
``nmi_hijack(t)``    True if an NMI takes over a BRK/IRQ vector fetch at t
"""


class CPU6502:
    # Base cycle counts per opcode (JAM listed as 2).  The core does not use
    # this table -- cycles follow from the bus accesses -- but it documents
    # the timing and is checked against the executed sequences by the tests.
    CYC = bytearray([
        7,6,2,8,3,3,5,5,3,2,2,2,4,4,6,6,
        2,5,2,8,4,4,6,6,2,4,2,7,4,4,7,7,
        6,6,2,8,3,3,5,5,4,2,2,2,4,4,6,6,
        2,5,2,8,4,4,6,6,2,4,2,7,4,4,7,7,
        6,6,2,8,3,3,5,5,3,2,2,2,3,4,6,6,
        2,5,2,8,4,4,6,6,2,4,2,7,4,4,7,7,
        6,6,2,8,3,3,5,5,4,2,2,2,5,4,6,6,
        2,5,2,8,4,4,6,6,2,4,2,7,4,4,7,7,
        2,6,2,6,3,3,3,3,2,2,2,2,4,4,4,4,
        2,6,2,6,4,4,4,4,2,5,2,5,5,5,5,5,
        2,6,2,6,3,3,3,3,2,2,2,2,4,4,4,4,
        2,5,2,5,4,4,4,4,2,4,2,4,4,4,4,4,
        2,6,2,8,3,3,5,5,2,2,2,2,4,4,6,6,
        2,5,2,8,4,4,6,6,2,4,2,7,4,4,7,7,
        2,6,2,8,3,3,5,5,2,2,2,2,4,4,6,6,
        2,5,2,8,4,4,6,6,2,4,2,7,4,4,7,7
    ])

    # Indexed reads that take one more cycle when the index crosses a page.
    PAGE_CROSS = frozenset([
        0x1D,0x19,0x11,0x3D,0x39,0x31,0x5D,0x59,0x51,0x7D,0x79,0x71,
        0xDD,0xD9,0xD1,0xFD,0xF9,0xF1,0xBD,0xB9,0xB1,0xBC,0xBE,
        0xBF,0xB3,0xBB,
        0x1C,0x3C,0x5C,0x7C,0xDC,0xFC,
    ])
    PAGE = PAGE_CROSS

    JAM_OPCODES = (0x02, 0x12, 0x22, 0x32, 0x42, 0x52, 0x62, 0x72, 0x92, 0xB2, 0xD2, 0xF2)
    MAGIC = 0xEE                      # LXA constant
    ANE_MAGIC = 0xEF                  # ANE constant (C64 measurement, VICE CPU/ane)
    ANE_RDY_MAGIC = 0xEE              # ANE when RDY held its operand read

    def __init__(self, read, write):
        self.read = read
        self.write = write
        self.a = self.x = self.y = 0
        self.sp = 0xFD
        self.pc = 0
        self.C = self.Z = self.I = self.D = self.B = self.V = self.N = 0
        self.cycles = 0
        self.jammed = False
        self.rdy = None
        self.rdy_until = 0              # rdy() need not be asked before this cycle (set by the hook's owner)
        self.last_op = 0                # opcode of the last instruction (0 after an interrupt)
        self.resumed = -1               # cycle in which the last RDY-stalled read executed
        self.stall_start = -1           # first cycle of that stall
        self.pending = None
        self.nmi_hijack = None
        self.I_poll = 1
        self.delay_int = False
        # When set, BRK ends the current call() like an RTS to the sentinel
        # (PSID player semantics) instead of vectoring through $FFFE.
        self.brk_stop = False
        self.brk_hit = False
        self.brk_count = 0
        self.timed_out = False
        self.CYC = bytearray(CPU6502.CYC)
        self._build()

    # ---- flags ----
    def _setp(self, p):
        self.C = p & 1
        self.Z = (p >> 1) & 1
        self.I = (p >> 2) & 1
        self.D = (p >> 3) & 1
        self.B = (p >> 4) & 1
        self.V = (p >> 6) & 1
        self.N = (p >> 7) & 1

    def _getp(self, b=0):
        return (self.C | (self.Z << 1) | (self.I << 2) | (self.D << 3) |
                (b << 4) | 0x20 | (self.V << 6) | (self.N << 7))

    def _nz(self, v):
        v &= 0xFF
        self.Z = 1 if v == 0 else 0
        self.N = v >> 7
        return v

    # ---- bus: one access per cycle ----
    def rd(self, addr):
        if self.rdy is not None and self.cycles >= self.rdy_until:
            t = self.rdy(self.cycles)
            if t != self.cycles:
                self.stall_start = self.cycles
                self.cycles = t
                self.resumed = t
        v = self.read(addr)
        self.cycles += 1
        return v

    def wr(self, addr, v):
        self.write(addr, v & 0xFF)
        self.cycles += 1

    # Driver helpers: direct memory access that is not CPU bus activity.
    def _push(self, v):
        self.write(0x100 | self.sp, v & 0xFF)
        self.sp = (self.sp - 1) & 0xFF

    def _pop(self):
        self.sp = (self.sp + 1) & 0xFF
        return self.read(0x100 | self.sp)

    def _r16(self, a):
        return self.read(a) | (self.read((a + 1) & 0xFFFF) << 8)

    # ---- arithmetic ----
    def _adc(self, m):
        m &= 0xFF
        a = self.a
        c = self.C
        if self.D:
            lo = (a & 0x0F) + (m & 0x0F) + c
            if lo > 0x09:
                lo += 0x06
            t = (lo & 0x0F) + (a & 0xF0) + (m & 0xF0) + (0x10 if lo > 0x0F else 0)
            self.Z = 1 if ((a + m + c) & 0xFF) == 0 else 0
            self.N = (t >> 7) & 1
            self.V = 1 if ((a ^ t) & 0x80) and not ((a ^ m) & 0x80) else 0
            if (t & 0x1F0) > 0x90:
                t += 0x60
            self.C = 1 if (t & 0xFF0) > 0xF0 else 0
            self.a = t & 0xFF
        else:
            s = a + m + c
            self.C = 1 if s > 0xFF else 0
            self.V = (~(a ^ m) & (a ^ s) & 0x80) >> 7
            self.a = self._nz(s)

    def _sbc(self, m):
        m &= 0xFF
        a = self.a
        borrow = 1 - self.C
        s = a - m - borrow
        if self.D:
            lo = (a & 0x0F) - (m & 0x0F) - borrow
            if lo & 0x10:
                t = ((lo - 0x06) & 0x0F) | ((a & 0xF0) - (m & 0xF0) - 0x10)
            else:
                t = (lo & 0x0F) | ((a & 0xF0) - (m & 0xF0))
            if t & 0x100:
                t -= 0x60
            self.a = t & 0xFF
        else:
            self.a = s & 0xFF
        self.C = 1 if s >= 0 else 0
        self.V = 1 if ((a ^ s) & 0x80) and ((a ^ m) & 0x80) else 0
        self._nz(s)

    def _cmp(self, r, m):
        self.C = 1 if r >= m else 0
        self._nz(r - m)

    # ---- execution ----
    def step(self):
        """Execute one instruction, or take one interrupt; False when jammed."""
        if self.jammed:
            return False
        if self.pending is not None:
            clk, delayed, i_poll, irq_clk = self.cycles, self.delay_int, self.I_poll, None
            if self.resumed == clk - 1:
                # RDY held the instruction's last cycle (VICE x64sc
                # maincpu_steal_cycles): a taken branch decided before that
                # cycle; a CLI loses its one-instruction delay; a SEI gets no
                # credit for the held cycles (line state before the stall).
                if delayed is True:
                    clk = self.stall_start + 1
                elif self.last_op == 0x58 and i_poll != self.I:
                    i_poll = 0
                elif self.last_op == 0x78 and i_poll != self.I:
                    irq_clk = self.stall_start + 1
            kind = self.pending(clk, i_poll, delayed, irq_clk)
            self.delay_int = False
            if kind is not None:
                self._interrupt(kind == "nmi")
                return True
        self.delay_int = False
        self.I_poll = None
        op = self.last_op = self.rd(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        self.ops[op]()
        if self.I_poll is None:
            self.I_poll = self.I
        return not self.jammed

    def _interrupt(self, nmi, brk=False):
        pc = self.pc
        if not brk:
            self.rd(pc)                              # opcode fetch, discarded
            self.rd(pc)
        self.wr(0x100 | self.sp, pc >> 8)
        self.sp = (self.sp - 1) & 0xFF
        self.wr(0x100 | self.sp, pc & 0xFF)
        self.sp = (self.sp - 1) & 0xFF
        self.wr(0x100 | self.sp, self._getp(1 if brk else 0))
        self.sp = (self.sp - 1) & 0xFF
        self.I = 1
        if not nmi and self.nmi_hijack is not None and self.nmi_hijack(self.cycles):
            nmi = True
        vec = 0xFFFA if nmi else 0xFFFE
        lo = self.rd(vec)
        hi = self.rd(vec + 1)
        self.pc = lo | (hi << 8)
        self.I_poll = 1
        self.last_op = 0
        # An edge too late to take over the vector fetch is not seen before
        # the handler's first instruction (Lorenz nmi, BRK clock 4..7).
        self.delay_int = 2

    def interrupt(self, vector):
        """Take an interrupt through ``vector`` (7 cycles, B clear)."""
        pc = self.pc
        self.rd(pc)
        self.rd(pc)
        self.wr(0x100 | self.sp, pc >> 8)
        self.sp = (self.sp - 1) & 0xFF
        self.wr(0x100 | self.sp, pc & 0xFF)
        self.sp = (self.sp - 1) & 0xFF
        self.wr(0x100 | self.sp, self._getp(0))
        self.sp = (self.sp - 1) & 0xFF
        self.I = 1
        lo = self.rd(vector)
        hi = self.rd((vector + 1) & 0xFFFF)
        self.pc = lo | (hi << 8)
        self.I_poll = 1

    def call(self, addr, a=0, x=0, y=0, max_ins=4_000_000):
        """Run a subroutine until it returns to the $0001 sentinel (player driver)."""
        self.a = a & 0xFF
        self.x = x & 0xFF
        self.y = y & 0xFF
        self.sp = 0xFD
        self.pc = addr & 0xFFFF
        self.jammed = False
        self.brk_hit = False
        self._push(0x00)
        self._push(0x00)
        self.cycles = 0
        n = 0
        while n < max_ins:
            if self.pc == 0x0001:
                break
            if not self.step():
                break
            n += 1
        self.timed_out = n >= max_ins and self.pc != 0x0001
        return self.cycles

    def irq(self, vec=0xFFFE, kernal=True, max_ins=4_000_000):
        """Driver-level IRQ call: the KERNAL entry pushes A/X/Y and jumps via
        ``vec``; $EA31/$EA7E/$EA81 are treated as the KERNAL exit."""
        self.timed_out = False
        if self.I:
            return 0
        self.jammed = False
        self.brk_hit = False
        self.cycles = 0
        isp = self.sp
        self.interrupt(vec if not kernal else 0xFFFE)
        if kernal:
            self._push(self.a)
            self._push(self.x)
            self._push(self.y)
            self.pc = self._r16(vec)
        n = 0
        while n < max_ins:
            if self.sp == isp or self.pc == 0x0001:
                break
            if kernal and self.pc in (0xEA31, 0xEA7E, 0xEA81):
                self.y = self._pop()
                self.x = self._pop()
                self.a = self._pop()
                self._setp(self._pop())
                lo = self._pop()
                hi = self._pop()
                self.pc = (hi << 8) | lo
                continue
            if not self.step():
                break
            n += 1
        self.timed_out = n >= max_ins
        return self.cycles

    # ---- opcode table ----
    def _build(self):
        rd, wr = self.rd, self.wr
        cpu = self

        # Effective-address sequences.  Each returns the address and has
        # performed every bus access up to (not including) the data access.
        def imm():
            a = cpu.pc
            cpu.pc = (a + 1) & 0xFFFF
            return a

        def zp():
            a = rd(cpu.pc)
            cpu.pc = (cpu.pc + 1) & 0xFFFF
            return a

        def zpx():
            a = rd(cpu.pc)
            cpu.pc = (cpu.pc + 1) & 0xFFFF
            rd(a)
            return (a + cpu.x) & 0xFF

        def zpy():
            a = rd(cpu.pc)
            cpu.pc = (cpu.pc + 1) & 0xFFFF
            rd(a)
            return (a + cpu.y) & 0xFF

        def ab():
            lo = rd(cpu.pc)
            hi = rd((cpu.pc + 1) & 0xFFFF)
            cpu.pc = (cpu.pc + 2) & 0xFFFF
            return lo | (hi << 8)

        def indexed(idx, always):
            lo = rd(cpu.pc)
            hi = rd((cpu.pc + 1) & 0xFFFF)
            cpu.pc = (cpu.pc + 2) & 0xFFFF
            t = lo + idx
            if always or t > 0xFF:
                rd((hi << 8) | (t & 0xFF))
            return ((hi << 8) + t) & 0xFFFF

        def abx_r():
            return indexed(cpu.x, False)

        def aby_r():
            return indexed(cpu.y, False)

        def abx_w():
            return indexed(cpu.x, True)

        def aby_w():
            return indexed(cpu.y, True)

        def izx():
            p = rd(cpu.pc)
            cpu.pc = (cpu.pc + 1) & 0xFFFF
            rd(p)
            p = (p + cpu.x) & 0xFF
            lo = rd(p)
            hi = rd((p + 1) & 0xFF)
            return lo | (hi << 8)

        def izy(always):
            p = rd(cpu.pc)
            cpu.pc = (cpu.pc + 1) & 0xFFFF
            lo = rd(p)
            hi = rd((p + 1) & 0xFF)
            t = lo + cpu.y
            if always or t > 0xFF:
                rd((hi << 8) | (t & 0xFF))
            return ((hi << 8) + t) & 0xFFFF

        def izy_r():
            return izy(False)

        def izy_w():
            return izy(True)

        # Instruction shapes.
        def R(mode, f):
            def h():
                f(rd(mode()))
            return h

        def W(mode, f):
            def h():
                a = mode()
                wr(a, f())
            return h

        def M(mode, f):
            def h():
                a = mode()
                v = rd(a)
                wr(a, v)
                wr(a, f(v))
            return h

        def IMP(f):
            def h():
                rd(cpu.pc)
                f()
            return h

        nz = cpu._nz

        # Read operations.
        def LDA(v):
            cpu.a = nz(v)

        def LDX(v):
            cpu.x = nz(v)

        def LDY(v):
            cpu.y = nz(v)

        def ORA(v):
            cpu.a = nz(cpu.a | v)

        def AND(v):
            cpu.a = nz(cpu.a & v)

        def EOR(v):
            cpu.a = nz(cpu.a ^ v)

        def ADC(v):
            cpu._adc(v)

        def SBC(v):
            cpu._sbc(v)

        def CMP(v):
            cpu._cmp(cpu.a, v)

        def CPX(v):
            cpu._cmp(cpu.x, v)

        def CPY(v):
            cpu._cmp(cpu.y, v)

        def BIT(v):
            cpu.Z = 1 if (cpu.a & v) == 0 else 0
            cpu.N = (v >> 7) & 1
            cpu.V = (v >> 6) & 1

        def LAX(v):
            cpu.a = cpu.x = nz(v)

        def NOPR(v):
            pass

        def ANC(v):
            cpu.a = nz(cpu.a & v)
            cpu.C = cpu.N

        def ALR(v):
            t = cpu.a & v
            cpu.C = t & 1
            cpu.a = nz(t >> 1)

        def ARR(v):
            t = cpu.a & v
            if cpu.D:
                r = (t >> 1) | (cpu.C << 7)
                cpu.N = cpu.C
                cpu.Z = 1 if r == 0 else 0
                cpu.V = 1 if (t ^ r) & 0x40 else 0
                if (t & 0x0F) + (t & 0x01) > 0x05:
                    r = (r & 0xF0) | ((r + 0x06) & 0x0F)
                if (t & 0xF0) + (t & 0x10) > 0x50:
                    r = (r & 0x0F) | ((r + 0x60) & 0xF0)
                    cpu.C = 1
                else:
                    cpu.C = 0
                cpu.a = r & 0xFF
                return
            cpu.a = nz(((cpu.C << 7) | (t >> 1)) & 0xFF)
            cpu.C = (cpu.a >> 6) & 1
            cpu.V = ((cpu.a >> 6) ^ (cpu.a >> 5)) & 1

        def SBX(v):
            t = (cpu.a & cpu.x) - v
            cpu.C = 1 if t >= 0 else 0
            cpu.x = nz(t)

        def LXA(v):
            cpu.a = cpu.x = nz((cpu.a | cpu.MAGIC) & v)

        def ANE(v):
            magic = cpu.ANE_RDY_MAGIC if cpu.resumed == cpu.cycles - 1 else cpu.ANE_MAGIC
            cpu.a = nz((cpu.a | magic) & cpu.x & v)

        def LAS(v):
            cpu.a = cpu.x = cpu.sp = nz(v & cpu.sp)

        # Read-modify-write operations: return the new value.
        def ASL(v):
            cpu.C = v >> 7
            return nz(v << 1)

        def LSR(v):
            cpu.C = v & 1
            return nz(v >> 1)

        def ROL(v):
            c = cpu.C
            cpu.C = v >> 7
            return nz((v << 1) | c)

        def ROR(v):
            c = cpu.C
            cpu.C = v & 1
            return nz((v >> 1) | (c << 7))

        def INC(v):
            return nz(v + 1)

        def DEC(v):
            return nz(v - 1)

        def SLO(v):
            cpu.C = v >> 7
            n = (v << 1) & 0xFF
            cpu.a = nz(cpu.a | n)
            return n

        def RLA(v):
            n = ((v << 1) | cpu.C) & 0xFF
            cpu.C = v >> 7
            cpu.a = nz(cpu.a & n)
            return n

        def SRE(v):
            cpu.C = v & 1
            n = v >> 1
            cpu.a = nz(cpu.a ^ n)
            return n

        def RRA(v):
            n = (v >> 1) | (cpu.C << 7)
            cpu.C = v & 1
            cpu._adc(n)
            return n

        def DCP(v):
            n = (v - 1) & 0xFF
            cpu._cmp(cpu.a, n)
            return n

        def ISC(v):
            n = (v + 1) & 0xFF
            cpu._sbc(n)
            return n

        o = [None] * 256
        for code, mode, fn in (
            (0xA9, imm, LDA), (0xA5, zp, LDA), (0xB5, zpx, LDA), (0xAD, ab, LDA), (0xBD, abx_r, LDA),
            (0xB9, aby_r, LDA), (0xA1, izx, LDA), (0xB1, izy_r, LDA),
            (0xA2, imm, LDX), (0xA6, zp, LDX), (0xB6, zpy, LDX), (0xAE, ab, LDX), (0xBE, aby_r, LDX),
            (0xA0, imm, LDY), (0xA4, zp, LDY), (0xB4, zpx, LDY), (0xAC, ab, LDY), (0xBC, abx_r, LDY),
            (0x09, imm, ORA), (0x05, zp, ORA), (0x15, zpx, ORA), (0x0D, ab, ORA), (0x1D, abx_r, ORA),
            (0x19, aby_r, ORA), (0x01, izx, ORA), (0x11, izy_r, ORA),
            (0x29, imm, AND), (0x25, zp, AND), (0x35, zpx, AND), (0x2D, ab, AND), (0x3D, abx_r, AND),
            (0x39, aby_r, AND), (0x21, izx, AND), (0x31, izy_r, AND),
            (0x49, imm, EOR), (0x45, zp, EOR), (0x55, zpx, EOR), (0x4D, ab, EOR), (0x5D, abx_r, EOR),
            (0x59, aby_r, EOR), (0x41, izx, EOR), (0x51, izy_r, EOR),
            (0x69, imm, ADC), (0x65, zp, ADC), (0x75, zpx, ADC), (0x6D, ab, ADC), (0x7D, abx_r, ADC),
            (0x79, aby_r, ADC), (0x61, izx, ADC), (0x71, izy_r, ADC),
            (0xE9, imm, SBC), (0xE5, zp, SBC), (0xF5, zpx, SBC), (0xED, ab, SBC), (0xFD, abx_r, SBC),
            (0xF9, aby_r, SBC), (0xE1, izx, SBC), (0xF1, izy_r, SBC), (0xEB, imm, SBC),
            (0xC9, imm, CMP), (0xC5, zp, CMP), (0xD5, zpx, CMP), (0xCD, ab, CMP), (0xDD, abx_r, CMP),
            (0xD9, aby_r, CMP), (0xC1, izx, CMP), (0xD1, izy_r, CMP),
            (0xE0, imm, CPX), (0xE4, zp, CPX), (0xEC, ab, CPX),
            (0xC0, imm, CPY), (0xC4, zp, CPY), (0xCC, ab, CPY),
            (0x24, zp, BIT), (0x2C, ab, BIT),
            (0xA7, zp, LAX), (0xB7, zpy, LAX), (0xAF, ab, LAX), (0xBF, aby_r, LAX), (0xA3, izx, LAX),
            (0xB3, izy_r, LAX),
            (0x80, imm, NOPR), (0x82, imm, NOPR), (0x89, imm, NOPR), (0xC2, imm, NOPR), (0xE2, imm, NOPR),
            (0x04, zp, NOPR), (0x44, zp, NOPR), (0x64, zp, NOPR),
            (0x14, zpx, NOPR), (0x34, zpx, NOPR), (0x54, zpx, NOPR), (0x74, zpx, NOPR), (0xD4, zpx, NOPR),
            (0xF4, zpx, NOPR), (0x0C, ab, NOPR),
            (0x1C, abx_r, NOPR), (0x3C, abx_r, NOPR), (0x5C, abx_r, NOPR), (0x7C, abx_r, NOPR),
            (0xDC, abx_r, NOPR), (0xFC, abx_r, NOPR),
            (0x0B, imm, ANC), (0x2B, imm, ANC), (0x4B, imm, ALR), (0x6B, imm, ARR), (0xCB, imm, SBX),
            (0xAB, imm, LXA), (0x8B, imm, ANE), (0xBB, aby_r, LAS),
        ):
            o[code] = R(mode, fn)

        for code, mode, fn in (
            (0x85, zp, lambda: cpu.a), (0x95, zpx, lambda: cpu.a), (0x8D, ab, lambda: cpu.a),
            (0x9D, abx_w, lambda: cpu.a), (0x99, aby_w, lambda: cpu.a), (0x81, izx, lambda: cpu.a),
            (0x91, izy_w, lambda: cpu.a),
            (0x86, zp, lambda: cpu.x), (0x96, zpy, lambda: cpu.x), (0x8E, ab, lambda: cpu.x),
            (0x84, zp, lambda: cpu.y), (0x94, zpx, lambda: cpu.y), (0x8C, ab, lambda: cpu.y),
            (0x87, zp, lambda: cpu.a & cpu.x), (0x97, zpy, lambda: cpu.a & cpu.x),
            (0x8F, ab, lambda: cpu.a & cpu.x), (0x83, izx, lambda: cpu.a & cpu.x),
        ):
            o[code] = W(mode, fn)

        for fn, codes in (
            (ASL, ((0x06, zp), (0x16, zpx), (0x0E, ab), (0x1E, abx_w))),
            (LSR, ((0x46, zp), (0x56, zpx), (0x4E, ab), (0x5E, abx_w))),
            (ROL, ((0x26, zp), (0x36, zpx), (0x2E, ab), (0x3E, abx_w))),
            (ROR, ((0x66, zp), (0x76, zpx), (0x6E, ab), (0x7E, abx_w))),
            (INC, ((0xE6, zp), (0xF6, zpx), (0xEE, ab), (0xFE, abx_w))),
            (DEC, ((0xC6, zp), (0xD6, zpx), (0xCE, ab), (0xDE, abx_w))),
            (SLO, ((0x07, zp), (0x17, zpx), (0x0F, ab), (0x1F, abx_w), (0x1B, aby_w), (0x03, izx), (0x13, izy_w))),
            (RLA, ((0x27, zp), (0x37, zpx), (0x2F, ab), (0x3F, abx_w), (0x3B, aby_w), (0x23, izx), (0x33, izy_w))),
            (SRE, ((0x47, zp), (0x57, zpx), (0x4F, ab), (0x5F, abx_w), (0x5B, aby_w), (0x43, izx), (0x53, izy_w))),
            (RRA, ((0x67, zp), (0x77, zpx), (0x6F, ab), (0x7F, abx_w), (0x7B, aby_w), (0x63, izx), (0x73, izy_w))),
            (DCP, ((0xC7, zp), (0xD7, zpx), (0xCF, ab), (0xDF, abx_w), (0xDB, aby_w), (0xC3, izx), (0xD3, izy_w))),
            (ISC, ((0xE7, zp), (0xF7, zpx), (0xEF, ab), (0xFF, abx_w), (0xFB, aby_w), (0xE3, izx), (0xF3, izy_w))),
        ):
            for code, mode in codes:
                o[code] = M(mode, fn)

        # Implied and accumulator instructions.
        def setter(name, value):
            def f():
                setattr(cpu, name, value)
            return f

        def CLI():
            cpu.I_poll = cpu.I
            cpu.I = 0

        def SEI():
            cpu.I_poll = cpu.I
            cpu.I = 1

        def ACC(fn):
            def f():
                cpu.a = fn(cpu.a)
            return f

        for code, f in (
            (0x18, setter("C", 0)), (0x38, setter("C", 1)), (0xB8, setter("V", 0)),
            (0xD8, setter("D", 0)), (0xF8, setter("D", 1)), (0x58, CLI), (0x78, SEI),
            (0xAA, lambda: setattr(cpu, "x", nz(cpu.a))), (0xA8, lambda: setattr(cpu, "y", nz(cpu.a))),
            (0x8A, lambda: setattr(cpu, "a", nz(cpu.x))), (0x98, lambda: setattr(cpu, "a", nz(cpu.y))),
            (0xBA, lambda: setattr(cpu, "x", nz(cpu.sp))), (0x9A, lambda: setattr(cpu, "sp", cpu.x)),
            (0xE8, lambda: setattr(cpu, "x", nz(cpu.x + 1))), (0xCA, lambda: setattr(cpu, "x", nz(cpu.x - 1))),
            (0xC8, lambda: setattr(cpu, "y", nz(cpu.y + 1))), (0x88, lambda: setattr(cpu, "y", nz(cpu.y - 1))),
            (0x0A, ACC(ASL)), (0x4A, ACC(LSR)), (0x2A, ACC(ROL)), (0x6A, ACC(ROR)),
            (0xEA, lambda: None), (0x1A, lambda: None), (0x3A, lambda: None), (0x5A, lambda: None),
            (0x7A, lambda: None), (0xDA, lambda: None), (0xFA, lambda: None),
        ):
            o[code] = IMP(f)

        # Stack.
        def PHA():
            rd(cpu.pc)
            wr(0x100 | cpu.sp, cpu.a)
            cpu.sp = (cpu.sp - 1) & 0xFF

        def PHP():
            rd(cpu.pc)
            wr(0x100 | cpu.sp, cpu._getp(1))
            cpu.sp = (cpu.sp - 1) & 0xFF

        def PLA():
            rd(cpu.pc)
            rd(0x100 | cpu.sp)
            cpu.sp = (cpu.sp + 1) & 0xFF
            cpu.a = nz(rd(0x100 | cpu.sp))

        def PLP():
            rd(cpu.pc)
            rd(0x100 | cpu.sp)
            cpu.sp = (cpu.sp + 1) & 0xFF
            p = rd(0x100 | cpu.sp)
            cpu.I_poll = cpu.I
            cpu._setp(p)

        # Jumps and subroutines.
        def JSR():
            lo = rd(cpu.pc)
            cpu.pc = (cpu.pc + 1) & 0xFFFF
            rd(0x100 | cpu.sp)
            wr(0x100 | cpu.sp, cpu.pc >> 8)
            cpu.sp = (cpu.sp - 1) & 0xFF
            wr(0x100 | cpu.sp, cpu.pc & 0xFF)
            cpu.sp = (cpu.sp - 1) & 0xFF
            hi = rd(cpu.pc)
            cpu.pc = lo | (hi << 8)

        def RTS():
            rd(cpu.pc)
            rd(0x100 | cpu.sp)
            cpu.sp = (cpu.sp + 1) & 0xFF
            lo = rd(0x100 | cpu.sp)
            cpu.sp = (cpu.sp + 1) & 0xFF
            hi = rd(0x100 | cpu.sp)
            cpu.pc = lo | (hi << 8)
            rd(cpu.pc)
            cpu.pc = (cpu.pc + 1) & 0xFFFF

        def RTI():
            rd(cpu.pc)
            rd(0x100 | cpu.sp)
            cpu.sp = (cpu.sp + 1) & 0xFF
            cpu._setp(rd(0x100 | cpu.sp))
            cpu.sp = (cpu.sp + 1) & 0xFF
            lo = rd(0x100 | cpu.sp)
            cpu.sp = (cpu.sp + 1) & 0xFF
            hi = rd(0x100 | cpu.sp)
            cpu.pc = lo | (hi << 8)

        def JMP():
            lo = rd(cpu.pc)
            hi = rd((cpu.pc + 1) & 0xFFFF)
            cpu.pc = lo | (hi << 8)

        def JMPI():
            lo = rd(cpu.pc)
            hi = rd((cpu.pc + 1) & 0xFFFF)
            p = lo | (hi << 8)
            lo = rd(p)
            hi = rd((p & 0xFF00) | ((p + 1) & 0xFF))
            cpu.pc = lo | (hi << 8)

        def BRK():
            if cpu.brk_stop:
                cpu.brk_hit = True
                cpu.brk_count += 1
                cpu.pc = 0x0001
                return
            rd(cpu.pc)
            cpu.pc = (cpu.pc + 1) & 0xFFFF
            cpu._interrupt(False, brk=True)

        def branch(flag, value):
            def h():
                off = rd(cpu.pc)
                cpu.pc = (cpu.pc + 1) & 0xFFFF
                if getattr(cpu, flag) == value:
                    rd(cpu.pc)
                    new = (cpu.pc + (off - 256 if off & 0x80 else off)) & 0xFFFF
                    if (new ^ cpu.pc) & 0xFF00:
                        rd((cpu.pc & 0xFF00) | (new & 0xFF))
                    else:
                        cpu.delay_int = True
                    cpu.pc = new
            return h

        o[0x48], o[0x08], o[0x68], o[0x28] = PHA, PHP, PLA, PLP
        o[0x20], o[0x60], o[0x40], o[0x4C], o[0x6C], o[0x00] = JSR, RTS, RTI, JMP, JMPI, BRK
        for code, flag, value in ((0x10, "N", 0), (0x30, "N", 1), (0x50, "V", 0), (0x70, "V", 1),
                                  (0x90, "C", 0), (0xB0, "C", 1), (0xD0, "Z", 0), (0xF0, "Z", 1)):
            o[code] = branch(flag, value)

        # SHA/SHX/SHY/TAS: value = register & (H + 1); a page crossing puts
        # that value into the high byte of the target address.  When RDY
        # (sprite DMA) holds the indexed dummy read, the stored value is the
        # register without the AND; the address is unchanged (VICE x64sc,
        # measured on a C64).
        def sh_store(reg_of, via_zp=False, idx_is_x=False, tas=False):
            def h():
                if via_zp:
                    p = rd(cpu.pc)
                    cpu.pc = (cpu.pc + 1) & 0xFFFF
                    lo = rd(p)
                    hi = rd((p + 1) & 0xFF)
                else:
                    lo = rd(cpu.pc)
                    hi = rd((cpu.pc + 1) & 0xFFFF)
                    cpu.pc = (cpu.pc + 2) & 0xFFFF
                t = lo + (cpu.x if idx_is_x else cpu.y)
                rd((hi << 8) | (t & 0xFF))
                reg = reg_of()
                anded = reg & ((hi + 1) & 0xFF)
                val = reg if cpu.resumed == cpu.cycles - 1 else anded
                ea = (anded << 8) | (t & 0xFF) if t > 0xFF else (hi << 8) | t
                if tas:
                    cpu.sp = reg
                wr(ea, val)
            return h

        o[0x93] = sh_store(lambda: cpu.a & cpu.x, via_zp=True)
        o[0x9F] = sh_store(lambda: cpu.a & cpu.x)
        o[0x9E] = sh_store(lambda: cpu.x)
        o[0x9C] = sh_store(lambda: cpu.y, idx_is_x=True)
        o[0x9B] = sh_store(lambda: cpu.a & cpu.x, tas=True)

        def JAM():
            # The halted NMOS 6502 leaves the address bus at $FFFF/$FFFE:
            # the reads that follow the jam (SingleStepTests 6502/v1).
            rd(cpu.pc)
            for addr in (0xFFFF, 0xFFFE, 0xFFFE, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF):
                rd(addr)
            cpu.jammed = True

        for code in self.JAM_OPCODES:
            o[code] = JAM

        missing = [hex(i) for i, h in enumerate(o) if h is None]
        if missing:
            raise AssertionError("opcodes without handlers: %s" % missing)
        self.ops = o
