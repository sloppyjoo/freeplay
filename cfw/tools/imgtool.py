#!/usr/bin/env python3
"""Static helpers for porting patches to another firmware image (no Ghidra needed).

    img = Image.from_ppf('stock_firmware/PolyendPlay_v1.6.0.ppf', 0x60020000,
                         mirrors=[(0x20000000, 0x600D3BE4, 0x1478)])
    s = img.find_str('Samples')            # address of the NUL-terminated string
    img.lit_refs(s)                        # literal-pool words holding that address
    img.loads_of(lit)                      # PC-relative LDRs of a literal word
    img.func_start(addr)                   # nearest PUSH {.., lr} at or before addr
    img.dis(start, end)                    # annotated disassembly (strings, names)

CLI: imgtool.py <ppf> <base> dis <start> [+len|end]
     imgtool.py <ppf> <base> str <text>      (strings -> literals -> loading code -> function)
"""
import re
import struct
import sys

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC


class Image:
    def __init__(self, data, base, mirrors=(), names=None):
        self.b, self.base, self.mirrors = bytes(data), base, list(mirrors)
        self.names = dict(names or {})
        self.md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        self.md.detail = True
        self._ins = None

    @classmethod
    def from_ppf(cls, path, base, mirrors=(), names=None):
        sys.path.insert(0, __file__.rsplit('/', 1)[0])
        from ppf import PPF
        return cls(PPF(path).image(base), base, mirrors, names)

    # ---- memory ---------------------------------------------------------------------------
    def off(self, a):
        if self.base <= a < self.base + len(self.b):
            return a - self.base
        for r, f, n in self.mirrors:
            if r <= a < r + n:
                return f + (a - r) - self.base
        return None

    def r32(self, a):
        o = self.off(a)
        return None if o is None or o + 4 > len(self.b) else struct.unpack_from('<I', self.b, o)[0]

    def r16(self, a):
        o = self.off(a)
        return None if o is None else struct.unpack_from('<H', self.b, o)[0]

    def r8(self, a):
        o = self.off(a)
        return None if o is None else self.b[o]

    def cstr(self, a, maxlen=80):
        o = self.off(a)
        if o is None:
            return None
        s = self.b[o:o + maxlen]
        z = s.find(b'\0')
        if z < 1:
            return None
        s = s[:z]
        return s.decode() if all(32 <= c < 127 for c in s) else None

    # ---- search ---------------------------------------------------------------------------
    def find_str(self, text, all_=False):
        pat = text.encode() + b'\0'
        hits = []
        for m in re.finditer(re.escape(pat), self.b):
            if m.start() == 0 or self.b[m.start() - 1] in (0, 0xFF) or True:
                hits.append(self.base + m.start())
        # prefer a string that starts after a NUL (not a suffix of a longer string)
        full = [h for h in hits if self.b[h - self.base - 1] == 0 or (h - self.base) % 4 == 0]
        res = full or hits
        return res if all_ else (res[0] if res else None)

    def lit_refs(self, value, aligned=True):
        pat = struct.pack('<I', value)
        out, i = [], self.b.find(pat)
        while i >= 0:
            if not aligned or i % 4 == 0:
                out.append(self.base + i)
            i = self.b.find(pat, i + 1)
        return out

    def instructions(self):
        if self._ins is None:
            ins, a, end = [], self.base, self.base + len(self.b)
            while a < end:
                got = False
                for i in self.md.disasm(self.b[a - self.base:], a):
                    ins.append(i)
                    a = i.address + i.size
                    got = True
                if a < end:
                    a += 2
                if not got and a >= end:
                    break
            self._ins = ins
            self._by_addr = {i.address: k for k, i in enumerate(ins)}
        return self._ins

    def lit_addr(self, i):
        if i.mnemonic.startswith(('ldr', 'vldr')) and len(i.operands) == 2 and \
                i.operands[1].type == ARM_OP_MEM and i.operands[1].mem.base == ARM_REG_PC:
            return ((i.address + 4) & ~3) + i.operands[1].mem.disp
        return None

    def loads_of(self, lit):
        """PC-relative loads of the literal at `lit` (linear-sweep; robust for literal pools)."""
        out = []
        a = max(self.base, lit - 0x1000)
        while a < lit:
            for i in self.md.disasm(self.b[a - self.base:lit - self.base], a):
                if self.lit_addr(i) == lit:
                    out.append(i)
                a = i.address + i.size
            a += 2
        return out

    def calls_to(self, target):
        target |= 1
        return [i for i in self.instructions() if i.mnemonic in ('bl', 'b.w') and i.operands
                and i.operands[0].type == ARM_OP_IMM and (i.operands[0].imm | 1) == target]

    def func_start(self, addr, limit=0x2000):
        """Nearest PUSH {..., lr} (16- or 32-bit) at or before addr."""
        a = addr & ~1
        lo = max(self.base, a - limit)
        best = None
        for i in self.md.disasm(self.b[lo - self.base:a + 4 - self.base], lo):
            if i.address > a:
                break
            if (i.mnemonic == 'push' and 'lr' in i.op_str) or (i.mnemonic == 'push.w' and 'lr' in i.op_str) or \
                    (i.mnemonic == 'stmdb' and 'sp!' in i.op_str and 'lr' in i.op_str):
                best = i.address
        return best

    def str_users(self, text):
        """[(string addr, literal addr, loading insn addr, function start)]"""
        out = []
        for s in self.find_str(text, all_=True):
            for lit in self.lit_refs(s):
                for i in self.loads_of(lit):
                    out.append((s, lit, i.address, self.func_start(i.address)))
        return out

    # ---- disassembly ----------------------------------------------------------------------
    def ann(self, v):
        if v is None:
            return ''
        out = []
        if (v | 1) in self.names:
            out.append(self.names[v | 1])
        elif v in self.names:
            out.append(self.names[v])
        s = self.cstr(v)
        if s:
            out.append(repr(s))
        return ' '.join(out)

    def dis(self, start, end, show=True):
        lines = []
        a = start
        while a < end:
            got = False
            o = self.off(a)
            for i in self.md.disasm(self.b[o:o + (end - a)], a):
                got = True
                note = ''
                la = self.lit_addr(i)
                if la is not None:
                    v = self.r32(la)
                    note = f'=[{la:#x}] {v:#x} {self.ann(v)}' if v is not None else ''
                elif i.mnemonic in ('bl', 'b.w', 'b', 'blx') or (i.mnemonic.startswith('b') and i.operands and
                                                                  i.operands[-1].type == ARM_OP_IMM):
                    if i.operands and i.operands[-1].type == ARM_OP_IMM:
                        note = self.ann(i.operands[-1].imm)
                lines.append(f'{i.address:08x}: {i.bytes.hex():<10} {i.mnemonic:<8} {i.op_str:<30} {note}')
                a = i.address + i.size
            if not got or a < end:
                lines.append(f'{a:08x}: {self.b[self.off(a):self.off(a) + 2].hex():<10} .short')
                a += 2
        if show:
            print('\n'.join(lines))
        return lines


def _main(argv):
    path, base = argv[0], int(argv[1], 16)
    img = Image.from_ppf(path, base)
    if argv[2] == 'dis':
        s = int(argv[3], 16)
        e = argv[4] if len(argv) > 4 else '+0x80'
        e = s + int(e[1:], 16) if e.startswith('+') else int(e, 16)
        img.dis(s, e)
    elif argv[2] == 'str':
        for s, lit, ld, fn in img.str_users(' '.join(argv[3:])):
            print(f'string {s:#x}  literal {lit:#x}  loaded at {ld:#x}  in function {fn and hex(fn)}')
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))


def refs_into(img, lo, hi):
    """Evidence that [lo,hi) is referenced: aligned literal words, PC-relative loads of such words,
    and movw/movt pairs. Returns dict of lists."""
    words = []
    for o in range(0, len(img.b) - 3, 4):
        v = struct.unpack_from('<I', img.b, o)[0]
        if lo <= (v & ~1) < hi:
            words.append((img.base + o, v))
    loads = []
    for a, v in words:
        for i in img.loads_of(a):
            loads.append((i.address, a, v))
    movs = []
    last = {}
    for i in img.instructions():
        if i.mnemonic == 'movw' and '#' in i.op_str:
            last[i.op_str.split(',')[0]] = (int(i.op_str.split('#')[1], 0), i.address)
        elif i.mnemonic == 'movt' and '#' in i.op_str:
            r = i.op_str.split(',')[0]
            if r in last and i.address - last[r][1] < 24:
                v = (int(i.op_str.split('#')[1], 0) << 16) | last[r][0]
                if lo <= v < hi:
                    movs.append((i.address, v))
    return dict(words=words, loads=loads, movw_movt=movs)
