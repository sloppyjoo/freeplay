#!/usr/bin/env python3
"""Find a function of one firmware image in another by its masked byte signature.

The Play+ and Play builds come from the same sources, so most functions match instruction for
instruction except for branch targets, PC-relative literal offsets and MOVW/MOVT addresses. A
signature keeps every instruction byte except those fields and is searched as a regex over the
other image (halfword aligned).

    from sigmatch import signature, find
    sig = signature(src_image_bytes, src_base, func_addr, length)
    hits = find(sig, dst_image_bytes, dst_base)

CLI: sigmatch.py <src.ppf> <src_base> <dst.ppf> <dst_base> <func_hex> <len_hex> [<func_hex> <len_hex> ...]
"""
import re
import sys

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS

_md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)


def _mask(ins):
    """Regex for one instruction: literal bytes, '.' where the bytes depend on the link layout."""
    b, m, ops = ins.bytes, ins.mnemonic, ins.op_str
    esc = lambda x: re.escape(bytes([x]))  # noqa: E731
    if ins.size == 4 and (m in ('bl', 'blx', 'b.w') or (m.startswith('b') and m.endswith('.w'))):
        return b'.' * 4
    if m in ('movw', 'movt'):
        return b'.' * 4
    if 'pc' in ops and (m.startswith('ldr') or m.startswith('adr') or m.startswith('vldr') or m == 'add'):
        if ins.size == 2:
            return b'.' + esc(b[1])              # imm8 in the low byte
        return esc(b[0]) + esc(b[1]) + b'..'     # imm12 in the second halfword
    if ins.size == 2 and (m.startswith('b') and len(m) <= 3 and m != 'bx' and m != 'bl'):
        return b'.' + esc(b[1])                  # B<cond> / B imm8/imm11 (low byte)
    if m in ('cbz', 'cbnz'):
        return b'.' + b'.'
    return b''.join(esc(x) for x in b)


def signature(img, base, addr, length):
    code = img[addr - base:addr - base + length]
    parts, n = [], 0
    for ins in _md.disasm(code, addr):
        parts.append(_mask(ins))
        n += ins.size
    if n < length - 2:
        parts.append(b'.' * (length - n))        # undecodable tail (data): wildcard
    return b''.join(parts)


def find(sig, img, base, limit=20):
    out = []
    for mt in re.finditer(sig, img, re.DOTALL):
        if mt.start() % 2 == 0:
            out.append(base + mt.start())
            if len(out) >= limit:
                break
    return out


def _main(argv):
    sys.path.insert(0, __file__.rsplit('/', 1)[0])
    from ppf import PPF
    src, sb, dst, db = PPF(argv[0]).image(int(argv[1], 16)), int(argv[1], 16), None, int(argv[3], 16)
    dst = PPF(argv[2]).image(db)
    pairs = argv[4:]
    for k in range(0, len(pairs), 2):
        a, n = int(pairs[k], 16), int(pairs[k + 1], 16)
        hits = find(signature(src, sb, a, n), dst, db)
        print(f'{a:#x}+{n:#x}: ' + (' '.join(hex(h) for h in hits) if hits else 'no match'))


if __name__ == '__main__':
    _main(sys.argv[1:])
