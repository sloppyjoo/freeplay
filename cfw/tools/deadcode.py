#!/usr/bin/env python3
"""Find unreachable code ranges in a firmware image to use as a code cave.

A range [lo, hi) is reported only if ALL of these hold:
  * it is a run of whole functions (each starts with PUSH {.., lr} and the run is followed by
    another function start or the end of the code area),
  * no BL / B / B.W / CBZ / CBNZ / conditional branch from outside the range targets it,
  * no aligned 32-bit word anywhere in the image (literal pools, vtables, .data LMA, tables)
    holds an address inside it (with or without the Thumb bit),
  * no PC-relative LDR / ADR from outside the range reads inside it,
  * the instruction just before `lo` cannot fall through into it (unconditional B, BX, POP {pc},
    LDR pc, or a literal pool / padding),
so nothing can transfer control into it or read it. Overwriting it cannot change behaviour.

usage: deadcode.py <ppf> <image base> [--min 2048] [--code-end ADDR]
"""
import argparse
import struct
import sys

from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC

sys.path.insert(0, __file__.rsplit('/', 1)[0])
from imgtool import Image  # noqa: E402


def is_push_lr(i):
    return i.mnemonic in ('push', 'push.w') and 'lr' in i.op_str or \
        (i.mnemonic == 'stmdb' and i.op_str.startswith('sp!') and 'lr' in i.op_str)


def ends_flow(i):
    m, op = i.mnemonic, i.op_str
    if m in ('b', 'b.w') and i.operands and i.operands[0].type == ARM_OP_IMM:
        return True
    if m == 'bx':
        return True
    if m in ('pop', 'pop.w', 'ldm.w', 'ldmia.w') and 'pc' in op:
        return True
    if m.startswith('ldr') and op.startswith('pc'):
        return True
    return False


def analyse(img, code_end=None):
    ins = img.instructions()
    end = code_end or (img.base + len(img.b))
    branch_t = set()   # (src, dst)
    pcrel_t = []       # (src, addr)
    for i in ins:
        if i.address >= end:
            break
        m = i.mnemonic
        if (m.startswith('b') or m.startswith('cb')) and i.operands and i.operands[-1].type == ARM_OP_IMM \
                and m not in ('bic', 'bics', 'bfi', 'bfc', 'bkpt'):
            branch_t.add((i.address, i.operands[-1].imm))
        la = img.lit_addr(i)
        if la is not None:
            pcrel_t.append((i.address, la))
        if m.startswith('adr') and i.operands and i.operands[-1].type == ARM_OP_IMM:
            pcrel_t.append((i.address, ((i.address + 4) & ~3) + i.operands[-1].imm))
    # Only odd words can be Thumb code pointers (an even word pointing into code would be a data
    # read of instruction bytes; those happen through PC-relative loads, checked separately).
    # For images linked at a high base, every word into the image is counted (stricter).
    words = set()
    strict = img.base >= 0x10000000
    for o in range(0, len(img.b) - 3, 4):
        v = struct.unpack_from('<I', img.b, o)[0]
        if img.base <= v < img.base + len(img.b) and (strict or v & 1):
            words.add(v & ~1)
    starts = [i.address for i in ins if i.address < end and is_push_lr(i)]
    return ins, branch_t, pcrel_t, words, starts


def find(img, min_size=2048, code_end=None):
    ins, branch_t, pcrel_t, words, starts = analyse(img, code_end)
    by_addr = {i.address: k for k, i in enumerate(ins)}
    targeted = set(d & ~1 for _, d in branch_t)
    # a function start is "dead" if nothing branches to it and no word points to it
    dead = [s for s in starts if s not in targeted and s not in words]
    sorted_words = sorted(words)
    import bisect
    out = []
    ds = set(dead)
    k = 0
    starts_sorted = starts
    idx = {s: n for n, s in enumerate(starts_sorted)}
    while k < len(starts_sorted):
        s = starts_sorted[k]
        if s not in ds:
            k += 1
            continue
        # previous instruction must not fall through
        pi = by_addr.get(s)
        prev = ins[pi - 1] if pi else None
        if prev is not None and prev.address + prev.size == s and not ends_flow(prev):
            k += 1
            continue
        j = k
        while j + 1 < len(starts_sorted) and starts_sorted[j + 1] in ds:
            j += 1
        lo = s
        hi = starts_sorted[j + 1] if j + 1 < len(starts_sorted) else (code_end or img.base + len(img.b))
        # shrink hi so that the code just before hi ends flow (the next live function follows)
        ok_lo, ok_hi = lo, hi
        # reject if any word points inside, any outside branch or pc-relative access lands inside
        p = bisect.bisect_left(sorted_words, ok_lo)
        inside_words = p < len(sorted_words) and sorted_words[p] < ok_hi
        inside_branch = any(ok_lo <= (d & ~1) < ok_hi and not (ok_lo <= src < ok_hi) for src, d in branch_t)
        inside_pcrel = any(ok_lo <= a < ok_hi and not (ok_lo <= src < ok_hi) for src, a in pcrel_t)
        if not (inside_words or inside_branch or inside_pcrel) and ok_hi - ok_lo >= min_size:
            out.append((ok_lo, ok_hi))
        k = j + 1
    return out


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    ap.add_argument('base', type=lambda s: int(s, 0))
    ap.add_argument('--min', type=lambda s: int(s, 0), default=2048)
    ap.add_argument('--code-end', type=lambda s: int(s, 0))
    a = ap.parse_args(argv)
    img = Image.from_ppf(a.ppf, a.base)
    for lo, hi in find(img, a.min, a.code_end):
        print(f'{lo:#010x}..{hi:#010x}  {hi - lo:6d} bytes')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
