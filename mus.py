"""Compute!'s Sidplayer (MUS/STR) support.

MUS files hold only music data; playing them needs the Sidplayer player
routine by Craig Chamberlain (Compute! 1986). That routine is not bundled with
sid2midi. Point sid2midi at a copy with ``--sidplayer FILE``: an xa-style 6502
source such as libsidplayfp's ``src/sidtune/sidplayer1.a65``, or a raw binary
whose first two bytes are its load address.

Installation follows libsidplayfp's MUS loader (src/sidtune/MUS.cpp):
  * the MUS file, including its 2-byte header, is placed at $0900;
  * player #1 at its load address ($E000), with its 12 bytes of useless
    OSC3/ENV3 reads replaced by NOPs;
  * the player's data pointer (offsets $C6E/$C70) is set to $0902;
  * init $EC60, play $EC80, driven by CIA #1 timer A (PSID speed bit set).
Only single-SID MUS files are supported (no STR stereo part).
"""
import re

MUS_DATA_ADDR = 0x0900
HLT_CMD = 0x014F
PLAYER1_INIT, PLAYER1_PLAY = 0xEC60, 0xEC80
# LDA $D41B / STA abs / LDA $D41C / STA abs: SID reads whose results the player
# never uses; libsidplayfp replaces these 12 bytes with NOPs.
_USELESS_READS = re.compile(rb"\xAD\x1B\xD4\x8D..\xAD\x1C\xD4\x8D..", re.DOTALL)
_DATA_PTR_LO, _DATA_PTR_HI = 0xC6E, 0xC70


class MusError(ValueError):
    pass


# ------------------------------------------------------------------ detection --
def voice3_index(buf):
    """Offset of the credits text, or None if ``buf`` is not a MUS file."""
    if len(buf) < 8:
        return None
    v1 = 2 + 3 * 2 + (buf[2] | buf[3] << 8)
    v2 = v1 + (buf[4] | buf[5] << 8)
    v3 = v2 + (buf[6] | buf[7] << 8)
    if v3 > len(buf) or v1 < 2 or v2 < 2 or v3 < 2:
        return None
    for end in (v1, v2, v3):
        if (buf[end - 2] << 8 | buf[end - 1]) != HLT_CMD:
            return None
    return v3


def _petscii(b):
    if 0x41 <= b <= 0x5A or 0x20 <= b <= 0x3F:
        return chr(b)
    if 0xC1 <= b <= 0xDA:
        return chr(b - 0x80)
    if 0x61 <= b <= 0x7A:
        return chr(b - 0x20)
    return " " if b == 0x0D else ""


def credit_lines(buf):
    i = voice3_index(buf)
    lines = []
    if i is None:
        return lines
    line = ""
    while i < len(buf) and buf[i]:
        if buf[i] == 0x0D:
            lines.append(line.strip())
            line = ""
        else:
            line += _petscii(buf[i])
        i += 1
    if line.strip():
        lines.append(line.strip())
    return [s for s in lines if s]


# ------------------------------------------------------------------ assembler --
_MODES = {
    # mnemonic: {mode: opcode}; modes imp acc imm zp zpx zpy abs abx aby ind izx izy rel
    "adc": dict(imm=0x69, zp=0x65, zpx=0x75, abs=0x6D, abx=0x7D, aby=0x79, izx=0x61, izy=0x71),
    "and": dict(imm=0x29, zp=0x25, zpx=0x35, abs=0x2D, abx=0x3D, aby=0x39, izx=0x21, izy=0x31),
    "asl": dict(acc=0x0A, zp=0x06, zpx=0x16, abs=0x0E, abx=0x1E),
    "bcc": dict(rel=0x90), "bcs": dict(rel=0xB0), "beq": dict(rel=0xF0), "bmi": dict(rel=0x30),
    "bne": dict(rel=0xD0), "bpl": dict(rel=0x10), "bvc": dict(rel=0x50), "bvs": dict(rel=0x70),
    "bit": dict(zp=0x24, abs=0x2C),
    "brk": dict(imp=0x00), "clc": dict(imp=0x18), "cld": dict(imp=0xD8), "cli": dict(imp=0x58),
    "clv": dict(imp=0xB8),
    "cmp": dict(imm=0xC9, zp=0xC5, zpx=0xD5, abs=0xCD, abx=0xDD, aby=0xD9, izx=0xC1, izy=0xD1),
    "cpx": dict(imm=0xE0, zp=0xE4, abs=0xEC), "cpy": dict(imm=0xC0, zp=0xC4, abs=0xCC),
    "dec": dict(zp=0xC6, zpx=0xD6, abs=0xCE, abx=0xDE), "dex": dict(imp=0xCA), "dey": dict(imp=0x88),
    "eor": dict(imm=0x49, zp=0x45, zpx=0x55, abs=0x4D, abx=0x5D, aby=0x59, izx=0x41, izy=0x51),
    "inc": dict(zp=0xE6, zpx=0xF6, abs=0xEE, abx=0xFE), "inx": dict(imp=0xE8), "iny": dict(imp=0xC8),
    "jmp": dict(abs=0x4C, ind=0x6C), "jsr": dict(abs=0x20),
    "lda": dict(imm=0xA9, zp=0xA5, zpx=0xB5, abs=0xAD, abx=0xBD, aby=0xB9, izx=0xA1, izy=0xB1),
    "ldx": dict(imm=0xA2, zp=0xA6, zpy=0xB6, abs=0xAE, aby=0xBE),
    "ldy": dict(imm=0xA0, zp=0xA4, zpx=0xB4, abs=0xAC, abx=0xBC),
    "lsr": dict(acc=0x4A, zp=0x46, zpx=0x56, abs=0x4E, abx=0x5E), "nop": dict(imp=0xEA),
    "ora": dict(imm=0x09, zp=0x05, zpx=0x15, abs=0x0D, abx=0x1D, aby=0x19, izx=0x01, izy=0x11),
    "pha": dict(imp=0x48), "php": dict(imp=0x08), "pla": dict(imp=0x68), "plp": dict(imp=0x28),
    "rol": dict(acc=0x2A, zp=0x26, zpx=0x36, abs=0x2E, abx=0x3E),
    "ror": dict(acc=0x6A, zp=0x66, zpx=0x76, abs=0x6E, abx=0x7E),
    "rti": dict(imp=0x40), "rts": dict(imp=0x60),
    "sbc": dict(imm=0xE9, zp=0xE5, zpx=0xF5, abs=0xED, abx=0xFD, aby=0xF9, izx=0xE1, izy=0xF1),
    "sec": dict(imp=0x38), "sed": dict(imp=0xF8), "sei": dict(imp=0x78),
    "sta": dict(zp=0x85, zpx=0x95, abs=0x8D, abx=0x9D, aby=0x99, izx=0x81, izy=0x91),
    "stx": dict(zp=0x86, zpy=0x96, abs=0x8E), "sty": dict(zp=0x84, zpx=0x94, abs=0x8C),
    "tax": dict(imp=0xAA), "tay": dict(imp=0xA8), "tsx": dict(imp=0xBA), "txa": dict(imp=0x8A),
    "txs": dict(imp=0x9A), "tya": dict(imp=0x98),
}
_SIZE = dict(imp=1, acc=1, imm=2, zp=2, zpx=2, zpy=2, rel=2, izx=2, izy=2, abs=3, abx=3, aby=3, ind=3)
_TOKEN = re.compile(r"\s*(\$[0-9A-Fa-f]+|%[01]+|\d+|[A-Za-z_][A-Za-z0-9_]*|\*|[+\-<>()])")


class AsmError(ValueError):
    pass


def _eval(expr, symbols, pc, line_no, strict):
    toks = _TOKEN.findall(expr)
    if "".join(t.strip() for t in toks) != re.sub(r"\s+", "", expr):
        raise AsmError("line %d: cannot parse expression %r" % (line_no, expr))
    value, op, unary = 0, "+", None
    for t in toks:
        t = t.strip()
        if t in "+-" and t and op is None:
            op = t
            continue
        if t in ("<", ">"):
            unary = t
            continue
        if t.startswith("$"):
            v = int(t[1:], 16)
        elif t.startswith("%"):
            v = int(t[1:], 2)
        elif t.isdigit():
            v = int(t)
        elif t == "*":
            v = pc
        elif t in symbols:
            v = symbols[t]
        elif strict:
            raise AsmError("line %d: undefined symbol %s" % (line_no, t))
        else:
            v = 0
        if unary == "<":
            v &= 0xFF
        elif unary == ">":
            v = (v >> 8) & 0xFF
        unary = None
        value = value + v if op == "+" else value - v
        op = None
    return value & 0xFFFF


def _short_literal(expr):
    """A plain $hh literal: the source asks for a zero-page operand."""
    return re.fullmatch(r"\s*\$[0-9A-Fa-f]{1,2}\s*", expr) is not None


def _parse_operand(mnemonic, operand):
    modes = _MODES[mnemonic]
    s = operand.strip()
    if not s or s.lower() == "a":
        return ("acc" if "acc" in modes else "imp"), None
    if "rel" in modes:
        return "rel", s
    if s.startswith("#"):
        return "imm", s[1:]
    m = re.fullmatch(r"\((.+)\)\s*,\s*[yY]", s)
    if m:
        return "izy", m.group(1)
    m = re.fullmatch(r"\((.+)\s*,\s*[xX]\s*\)", s)
    if m:
        return "izx", m.group(1)
    m = re.fullmatch(r"\((.+)\)", s)
    if m and "ind" in modes:
        return "ind", m.group(1)
    m = re.fullmatch(r"(.+?)\s*,\s*([xXyY])", s)
    if m:
        expr, reg = m.group(1), m.group(2).lower()
        zp = "zp" + reg
        if _short_literal(expr) and zp in modes:
            return zp, expr
        return "ab" + reg, expr
    if _short_literal(s) and "zp" in modes:
        return "zp", s
    return "abs", s


def assemble_xa(text):
    """Assemble the xa-style subset used by dxa disassemblies.

    Returns (load_address, bytes): the address from a leading ``.word`` header
    (or the first ``* =``) and the code from that address on."""
    lines = text.splitlines()
    symbols = {}

    def run(strict):
        pc = None
        out = {}
        header = []
        for no, raw in enumerate(lines, 1):
            line = raw.split(";", 1)[0].rstrip()
            if not line.strip():
                continue
            m = None if line[0].isspace() else re.match(r"([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                label = m.group(1)
                rest = line[m.end():]
                if not re.match(r"\s*=", rest):
                    if pc is None:
                        raise AsmError("line %d: label before origin" % no)
                    if not strict:
                        symbols[label] = pc
                    line = " " + rest
                # else: "name = expr" at column 0 is handled below
            stmt = line.strip()
            if not stmt:
                continue
            m = re.fullmatch(r"\*\s*=\s*(.+)", stmt)
            if m:
                pc = _eval(m.group(1), symbols, pc or 0, no, True)
                continue
            m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)", stmt)
            if m:
                if pc is None and "*" in m.group(2):
                    raise AsmError("line %d: '*' before origin" % no)
                symbols[m.group(1)] = _eval(m.group(2), symbols, pc or 0, no, strict)
                continue
            # a label and an assignment on one line: "lec80 lec81 = * + 1"
            m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*\s*=.+)", stmt)
            if m and m.group(1).lower() not in _MODES:
                raise AsmError("line %d: unexpected %r" % (no, stmt))
            parts = stmt.split(None, 1)
            word, operand = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
            if word in (".byt", ".byte", ".word"):
                values = [_eval(v, symbols, pc or 0, no, strict) for v in operand.split(",")]
                data = []
                for v in values:
                    data += [v & 0xFF] if word != ".word" else [v & 0xFF, v >> 8]
                if pc is None:
                    header += data
                else:
                    for b in data:
                        out[pc] = b
                        pc += 1
                continue
            if word not in _MODES:
                raise AsmError("line %d: unknown statement %r" % (no, stmt))
            if pc is None:
                raise AsmError("line %d: code before origin" % no)
            mode, expr = _parse_operand(word, operand)
            if mode not in _MODES[word]:
                raise AsmError("line %d: %s has no %s mode" % (no, word, mode))
            code = [_MODES[word][mode]]
            if mode == "rel":
                target = _eval(expr, symbols, pc, no, strict)
                off = target - (pc + 2)
                if strict and not -128 <= off <= 127:
                    raise AsmError("line %d: branch out of range" % no)
                code.append(off & 0xFF)
            elif _SIZE[mode] == 2:
                v = _eval(expr, symbols, pc, no, strict)
                if strict and mode not in ("imm",) and v > 0xFF:
                    raise AsmError("line %d: operand $%X does not fit %s" % (no, v, mode))
                code.append(v & 0xFF)
            elif _SIZE[mode] == 3:
                v = _eval(expr, symbols, pc, no, strict)
                code += [v & 0xFF, v >> 8]
            for b in code:
                out[pc] = b
                pc += 1
        return out, header

    run(False)                      # pass 1: labels (operand sizes never depend on them)
    run(False)                      # pass 2: forward "= * + n" symbols settle
    out, header = run(True)
    if not out:
        raise AsmError("no code")
    lo, hi = min(out), max(out)
    load = (header[0] | header[1] << 8) if len(header) >= 2 else lo
    if load != lo:
        raise AsmError("header load address $%04X differs from origin $%04X" % (load, lo))
    return load, bytes(out.get(a, 0) for a in range(lo, hi + 1))


def load_player(path):
    """(load address, code) from an xa source or a load-address-prefixed binary."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:1] in (b";", b"\t", b" ", b"\n") or b".byt" in raw[:4096]:
        return assemble_xa(raw.decode("latin-1"))
    if raw[:4] == b"\x01\x00o6":            # o65 object as built by libsidplayfp
        raw = raw[27:]
    if len(raw) < 3:
        raise MusError("%s: player file too short" % path)
    return raw[0] | raw[1] << 8, raw[2:]


def install(ram, mus_bytes, player):
    """Place MUS data and the player in C64 RAM; returns (init, play)."""
    load, code = player
    if load + len(code) > 0x10000:
        raise MusError("player does not fit in memory")
    if MUS_DATA_ADDR + len(mus_bytes) > load:
        raise MusError("MUS data ($%X bytes) overlaps the player at $%04X" % (len(mus_bytes), load))
    ram[MUS_DATA_ADDR:MUS_DATA_ADDR + len(mus_bytes)] = mus_bytes
    ram[load:load + len(code)] = code
    m = _USELESS_READS.search(bytes(code))
    if m:
        ram[load + m.start():load + m.end()] = b"\xEA" * 12
    ram[load + _DATA_PTR_LO] = (MUS_DATA_ADDR + 2) & 0xFF
    ram[load + _DATA_PTR_HI] = (MUS_DATA_ADDR + 2) >> 8
    return PLAYER1_INIT, PLAYER1_PLAY
