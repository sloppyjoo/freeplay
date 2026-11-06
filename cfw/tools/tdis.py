#!/usr/bin/env python3
"""Thumb-2 disassembler for the Play+ 1.3.0 image with literal/string/function annotation.
usage: tdis.py <start_hex> [<end_hex>|+<len>]
Needs the Play+ 1.3.0 image as raw bytes in playplus_1.3.0.bin at the repo root (make it with ppf.py; git ignores it)."""
import sys, struct, os
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
from capstone.arm import ARM_OP_MEM, ARM_REG_PC, ARM_OP_IMM

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = 0x60030000
IMG = open(os.path.join(TOP, 'playplus_1.3.0.bin'), 'rb').read()
# runtime mirrors: ITCM 0x8 <- 0x6017625C (0x144F0), DTCM .data 0x20000000 <- 0x6018A74C (0x22E8)
MIRRORS = [(0x8, 0x6017625C, 0x144F0), (0x20000000, 0x6018A74C, 0x22E8)]

def flash_off(a):
    if BASE <= a < BASE + len(IMG): return a - BASE
    for r, f, n in MIRRORS:
        if r <= a < r + n: return f + (a - r) - BASE
    return None

def rd32(a):
    o = flash_off(a)
    return None if o is None or o + 4 > len(IMG) else struct.unpack_from('<I', IMG, o)[0]

def cstr(a, maxlen=60):
    o = flash_off(a)
    if o is None: return None
    s = IMG[o:o + maxlen]
    z = s.find(b'\0')
    if z < 2: return None
    s = s[:z]
    if all(32 <= c < 127 for c in s): return s.decode()
    return None

NAMES = {}
_f = os.path.join(TOP, 'rebuild/export/functions.tsv')   # your own Ghidra export, optional
if os.path.exists(_f):
    for ln in open(_f).read().splitlines()[1:]:
        p = ln.split('\t'); NAMES[int(p[0], 16)] = p[5]
_u = os.path.join(TOP, 'cfw/notes/labels.tsv')
if os.path.exists(_u):
    for ln in open(_u).read().splitlines():
        if ln and not ln.startswith('#'):
            a, n = ln.split('\t')[:2]; NAMES[int(a, 16)] = n

def ann(v):
    if v is None: return ''
    out = []
    if (v & ~1) in NAMES: out.append(NAMES[v & ~1])
    s = cstr(v)
    if s: out.append(repr(s))
    return ' '.join(out)

def code_bytes(a, n):
    o = flash_off(a); return IMG[o:o + n]

def disasm(start, end):
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS); md.detail = True
    a = start
    while a < end:
        got = False
        for i in md.disasm(code_bytes(a, end - a), a):
            got = True
            note = ''
            if i.mnemonic.startswith('ldr') and len(i.operands) == 2 and i.operands[1].type == ARM_OP_MEM and i.operands[1].mem.base == ARM_REG_PC:
                la = ((i.address + 4) & ~3) + i.operands[1].mem.disp
                v = rd32(la); note = f'=[{la:#x}] {v:#x} {ann(v)}' if v is not None else ''
            elif i.mnemonic in ('bl', 'b', 'blx', 'b.w') or i.mnemonic.startswith('b') and i.operands and i.operands[-1].type == ARM_OP_IMM and i.mnemonic not in ('bic', 'bics', 'bfi', 'bfc'):
                if i.operands and i.operands[-1].type == ARM_OP_IMM:
                    note = ann(i.operands[-1].imm)
            elif i.mnemonic in ('adr', 'adr.w', 'add') and 'pc' in i.op_str:
                pass
            pre = f'\n{NAMES[i.address]}:\n' if i.address in NAMES and i.address != start else ''
            print(f'{pre}{i.address:08x}: {i.bytes.hex():<10} {i.mnemonic:<8} {i.op_str:<28} {note}')
            a = i.address + i.size
        if not got or a < end:
            print(f'{a:08x}: {code_bytes(a, 2).hex():<10} .short'); a += 2

if __name__ == '__main__':
    s = int(sys.argv[1], 16)
    e = sys.argv[2] if len(sys.argv) > 2 else '+0x80'
    e = s + int(e[1:], 16) if e.startswith('+') else int(e, 16)
    disasm(s, e)
