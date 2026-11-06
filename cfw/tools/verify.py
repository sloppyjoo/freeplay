#!/usr/bin/env python3
"""Offline verification of a built CFW .ppf (no hardware).

Checks, from the output file alone (not trusting the linker's log):
  1. the .ppf parses with every record checksum valid, same line count/headers as stock,
     and the changed bytes are confined to the code cave(s) and hook words;
  2. every instruction in the injected code decodes; every BL/B.W target and every
     MOVW/MOVT-built address resolves to a CFW function, a firmware symbol from the target's
     symbols.ld, a CFW string/constant, or the scratch RAM window;
  3. each hook word holds a CFW symbol of the right kind, and the stock instruction that loads
     it is a PC-relative LDR (shown disassembled with its use);
  4. the emulated end-to-end tests (cfw/tests/emu_slice.py) pass on the output image.
Results are appended to the build's .md report.

usage: verify.py cfw/targets/<t>/target.json [patched.ppf]
"""
import json
import os
import struct
import subprocess
import sys

from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC

HERE = os.path.dirname(os.path.abspath(__file__))
CFW = os.path.dirname(HERE)
TOP = os.path.dirname(CFW)
sys.path.insert(0, HERE)
from link import load_symbols, decode_bl  # noqa: E402

# Well-known magic numbers that CFW code builds with MOVW/MOVT and that happen to fall inside a
# memory window. Each is a value, never an address; keep this list short and named.
KNOWN_CONSTANTS = {
    0x41615252: 'FAT32 FSINFO lead signature "RRaA"',
    0x61417272: 'FAT32 FSINFO struct signature "rrAa"',
    0x811C9DC5: 'FNV-1a 32-bit offset basis',
    0x46464952: 'WAV "RIFF"',
    0x45564157: 'WAV "WAVE"',
    0x20746d66: 'WAV "fmt "',
    0x61746164: 'WAV "data"',
    0x58574643: 'CFW ext region header magic "CFWX"',
    0x60000001: 'flash range bound (startend.c obj_ok: a Thumb code pointer is >= 0x60000001)',
    0x000927C0: 'Tap Tempo: 600000 (tenths of a BPM x ms per beat)',
    0x00021480: 'pages.c VU meter scale: 284 px x 480 (meter width x the -48.0 dB floor in tenths)',
}
from ppf import PPF, diff  # noqa: E402


def h(x):
    return int(x, 0) if isinstance(x, str) else x


def read_map(path):
    syms, sections = {}, []
    for ln in open(path):
        p = ln.split()
        if len(p) == 2 and p[0].startswith('0x'):
            syms[p[1]] = int(p[0], 16)
        elif p and p[0] == 'section':
            sections.append((int(p[1], 16), int(p[2], 16), p[3]))
    return syms, sections


def disasm(code, base):
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    md.detail = True
    return list(md.disasm(code, base))


def _pc_rel(i):
    """(address, size) of the constant a PC-relative load reads, else None."""
    m = i.mnemonic.split('.')[0]
    if not (m.startswith('ldr') or m.startswith('vldr')) or len(i.operands) < 2:
        return None
    op = i.operands[-1]
    if op.type != ARM_OP_MEM or op.mem.base != ARM_REG_PC:
        return None
    a = ((i.address + 4) & ~3) + op.mem.disp
    if m == 'ldrd' or (m == 'vldr' and i.op_str.startswith('d')):
        return a, 8
    if m in ('ldrh', 'ldrsh'):
        return a, 2
    if m in ('ldrb', 'ldrsb'):
        return a, 1
    return a, 4


def sweep(img, lo, hi):
    """Decode [lo, hi) instruction by instruction, skipping the constants that PC-relative loads
    read and TBB/TBH tables (compilers place literal pools and jump tables between functions'
    code, -Oz also inside them). Repeats until the set of constants is stable (backward loads).
    Returns (instructions, {literal addr: size}, errors)."""
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    md.detail = True
    lits, skips = {}, {}
    for _ in range(8):
        ins, errs, a, last_cmp = [], [], lo, {}
        before = (dict(lits), dict(skips))
        while a < hi:
            if a in lits:
                a += max(lits[a], 2) if lits[a] >= 2 else 2
                continue
            if a in skips:
                a += skips[a]
                continue
            got = list(md.disasm(img(a, min(4, hi - a)), a, count=1))
            if not got:
                if not any(la <= a < la + n for la, n in lits.items()):
                    errs.append(f'undecodable code at {a:#x} in {lo:#x}..{hi:#x}')
                a += 2
                continue
            i = got[0]
            ins.append(i)
            r = _pc_rel(i)
            if r and lo <= r[0] < hi:
                lits.setdefault(r[0], r[1])
            if i.mnemonic == 'cmp' and len(i.operands) == 2 and i.operands[1].type == ARM_OP_IMM:
                last_cmp[i.op_str.split(',')[0]] = i.operands[1].imm
            if i.mnemonic in ('tbb', 'tbh'):
                idx = i.op_str.split(',')[1].strip(' ]').split()[0]
                n = last_cmp.get(idx)
                w = 1 if i.mnemonic == 'tbb' else 2
                t0 = i.address + 4
                if n is None:  # range known to the compiler: the table ends at its nearest target
                    end, k = hi, 0
                    while t0 + k * w < min(end, hi):
                        e = img(t0 + k * w, w)
                        end = min(end, t0 + 2 * int.from_bytes(e, 'little'))
                        k += 1
                    size = k * w
                    if end <= t0:
                        errs.append(f'{i.address:#x}: {i.mnemonic} table could not be sized')
                else:
                    size = (n + 1) * w
                skips[t0] = (size + 1) & ~1
            a += i.size
        if (dict(lits), dict(skips)) == before:
            break
    return ins, lits, errs


def check_code(img, code_ranges, cfw_syms, fw_syms, ram, out):
    """Decode text ranges; resolve every branch and movw/movt address. Returns error list."""
    errs = []
    fw_by_addr = {v: n for n, v in fw_syms.items()}
    cfw_by_addr = {v: n for n, v in cfw_syms.items()}
    data_ranges = [(lo, hi) for lo, hi, kind in code_ranges if kind == 'rodata']
    # Thumb pointers to the start of one of our own text sections (a static helper whose name
    # is also used in another object, so the symbol map keeps only one of them)
    text_starts = {lo | 1 for lo, hi, kind in code_ranges if kind == 'text'}
    ext = {}
    for lo, hi, kind in code_ranges:
        if kind != 'text':
            continue
        ins, lits, sweep_errs = sweep(img, lo, hi)
        errs += sweep_errs
        for a, n in sorted(lits.items()):
            if n != 4:
                continue  # 8-byte (ldrd / vldr.64) constants are data, not addresses
            v = struct.unpack('<I', img(a, 4))[0]
            if v in cfw_by_addr or v in fw_by_addr:
                ext.setdefault(v, fw_by_addr.get(v) or cfw_by_addr.get(v))
            elif any(a2 <= v < b2 for a2, b2 in data_ranges) or (ram[0] <= v < ram[1]) or v in KNOWN_CONSTANTS \
                    or v in text_starts \
                    or v < 0x10000:
                pass  # string/const in our rodata (merged strings have no symbol), scratch RAM, magic,
                #       small constants (0, 0.0f, bit masks) as the movw/movt rule below
            elif 0x60000000 <= v < 0x70000000 or 0x1FFF0000 <= v < 0x20080000 or v < 0x00100000:
                errs.append(f'{a:#x}: literal {v:#x} is not a known symbol')
        pending = {}
        for i in ins:
            if i.mnemonic in ('bl', 'b.w', 'b') and i.operands and i.operands[0].type == ARM_OP_IMM:
                t = i.operands[0].imm
                inside = any(a <= t < b for a, b, k in code_ranges if k == 'text')
                if (t | 1) in cfw_by_addr:
                    pass
                elif (t | 1) in fw_by_addr:
                    ext[t | 1] = fw_by_addr[t | 1]
                elif inside and i.mnemonic == 'b':
                    pass  # local branch
                elif inside:
                    pass
                else:
                    errs.append(f'{i.address:#x}: {i.mnemonic} to unknown {t:#x}')
            if i.mnemonic in ('movw', 'movt'):
                rd = i.op_str.split(',')[0]
                imm = i.operands[1].imm & 0xFFFF
                if i.mnemonic == 'movw':
                    pending[rd] = (imm, i.address)
                elif rd in pending:
                    v = (imm << 16) | pending.pop(rd)[0]
                    if v in cfw_by_addr or v in fw_by_addr:
                        ext.setdefault(v, fw_by_addr.get(v) or cfw_by_addr.get(v))
                    elif any(a <= v < b for a, b in data_ranges):
                        pass  # string/const in our rodata
                    elif ram[0] <= v < ram[1]:
                        pass
                    elif v < 0x10000:
                        pass  # small constant (e.g. magic halves are not addresses)
                    elif v in KNOWN_CONSTANTS:
                        pass  # named magic number, see KNOWN_CONSTANTS
                    else:
                        # constants like magic numbers: accept only if not in any memory window
                        if 0x60000000 <= v < 0x70000000 or 0x20000000 <= v < 0x20080000 or v < 0x00100000 \
                                or 0x80000000 <= v < 0x82000000:
                            errs.append(f'{i.address:#x}: movw/movt address {v:#x} is not a known symbol')
            if i.mnemonic.startswith('ldr') and len(i.operands) == 2 and i.operands[1].type == ARM_OP_MEM \
                    and i.operands[1].mem.base == ARM_REG_PC:
                la = ((i.address + 4) & ~3) + i.operands[1].mem.disp
                if not lo <= la < hi:
                    errs.append(f'{i.address:#x}: literal load from {la:#x} outside its own section')
    out.append('External references from CFW code: ' +
               ', '.join(f'`{n}` {a:#x}' for a, n in sorted(ext.items())))
    return errs


def loader_of(img_bytes, base, lit):
    """Find PC-relative LDRs that load the literal at `lit` (search 4 KiB back)."""
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    md.detail = True
    hits = []
    a = max(base, lit - 0x1000)
    while a < lit:
        for i in md.disasm(img_bytes[a - base:lit - base], a):
            if i.mnemonic.startswith('ldr') and len(i.operands) == 2 and i.operands[1].type == ARM_OP_MEM \
                    and i.operands[1].mem.base == ARM_REG_PC:
                if ((i.address + 4) & ~3) + i.operands[1].mem.disp == lit:
                    hits.append(i)
            a = i.address + i.size
        a += 2
    return hits


def verify_build(target_json, out_path=None, report_imgs=None):
    tdir = os.path.dirname(os.path.abspath(target_json))
    t = json.load(open(target_json))
    # the link map sits next to the built .ppf (build.py --out DIR writes both there)
    bdir = os.path.dirname(os.path.abspath(out_path)) if out_path else os.path.join(CFW, 'build', t['name'])
    out_path = out_path or os.path.join(bdir, t['output'])
    stock = PPF(os.path.join(TOP, t['input']))
    out = PPF(out_path)  # parsing validates every checksum
    lines_md = ['', '## Verification (offline)', '']
    logs = []
    errs = []
    changed, lines, inserted = diff(stock, out)
    lines_md.append(f'- `.ppf` parses, all {len(out.records)} record checksums valid; {len(lines)} stock records '
                    f'changed (data+checksum only), {len(inserted)} data records inserted, every other stock '
                    f'line byte-identical and in order')
    allowed = []
    for img in t['images']:
        mp = os.path.join(bdir, 'link.map' if len(t['images']) == 1 else f'link_{img["name"]}.map')
        cfw_syms, sections = read_map(mp)
        fw = {}
        for s in img['symbols']:
            fw.update(load_symbols(os.path.join(tdir, s)))
        base = h(img['base'])
        image = out.image(base)
        rd = lambda a, n: out.read(a, n)  # noqa: E731
        for a, n, _ in sections:
            allowed.append((a, a + n))
        if sections:  # alignment padding between linked sections is part of the patch too
            allowed.append((min(a for a, _, _ in sections), max(a + n for a, n, _ in sections)))
        ranges = [(a, a + n, 'text' if name.startswith('.text') else 'rodata') for a, n, name in sections]
        ram = (h(img['ram'][0]), h(img['ram'][1])) if img.get('ram') else (0, 0)
        errs += check_code(rd, ranges, cfw_syms, fw, ram, lines_md)
        lines_md.append(f'- Image {img["name"]}: {sum(1 for r in ranges if r[2] == "text")} functions '
                        f'decoded, every branch / address resolved')
        for hk in img['hooks']:
            a = h(hk['addr'])
            allowed.append((a, a + 4))
            want = cfw_syms[hk['symbol']]
            if hk.get('kind') == 'bl':
                # code hook: the stock BL at `a` now calls the CFW function (decoded, not trusted)
                ins = bytearray(rd(a, 4))
                ok = (ins[1] & 0xF8) == 0xF0 and (ins[3] & 0xD0) == 0xD0
                tgt = a + 4 + decode_bl(ins, 0) if ok else None
                if tgt != want & ~1:
                    errs.append(f'bl hook {a:#x}: calls {tgt}, want {hk["symbol"]} {want & ~1:#x}')
                ins_stock = [i for i in Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS).disasm(bytes(ins), a)][:1]
                lines_md.append(f'- Hook `{a:#x}`: `bl` retargeted from `{hk["expect"]}` to `{want & ~1:#x}` '
                                f'({hk["symbol"]}): ' + (f'`{ins_stock[0].mnemonic} {ins_stock[0].op_str}`' if ins_stock else '?'))
                continue
            v = struct.unpack('<I', rd(a, 4))[0]
            if v != want:
                errs.append(f'hook {a:#x} holds {v:#x}, want {hk["symbol"]} {want:#x}')
            if 'vtable' in hk:
                # a virtual method slot: reached through the object's vtable pointer, so check the
                # slot lies in that vtable and the vtable address itself is loaded by stock code
                vt = h(hk['vtable'])
                if not (vt <= a < vt + 0x100) or (a - vt) % 4:
                    errs.append(f'hook {a:#x}: not a slot of vtable {vt:#x}')
                refs = [r for r in range(base, base + len(image) - 3, 4)
                        if struct.unpack_from('<I', image, r - base)[0] == vt]
                loads = [i for r in refs for i in loader_of(image, base, r)]
                if not loads:
                    errs.append(f'hook {a:#x}: vtable {vt:#x} is not loaded by any stock code')
                for i in loads:
                    lines_md.append(f'- Hook `{a:#x}` = `{v:#010x}` ({hk["symbol"]}): slot +{a - vt:#x} of vtable '
                                    f'`{vt:#x}`, which is stored by `{i.address:#x}: {i.mnemonic} {i.op_str}`')
                continue
            loads = loader_of(image, base, a)
            if not loads:
                errs.append(f'hook {a:#x}: no PC-relative load of this literal found')
            for i in loads:
                lines_md.append(f'- Hook `{a:#x}` = `{v:#010x}` ({hk["symbol"]}) loaded by '
                                f'`{i.address:#x}: {i.mnemonic} {i.op_str}`')
    stray = [x for x, _, _ in changed if not any(a <= x < b for a, b in allowed)]
    if stray:
        errs.append(f'{len(stray)} changed bytes outside cave+hooks, first {stray[0]:#x}')
    stray_ins = [r.addr for _, r in inserted if not any(a <= r.addr < b for a, b in allowed)]
    if stray_ins:
        errs.append(f'{len(stray_ins)} inserted records outside the linked code, first {stray_ins[0]:#x}')
    # emulation, per image
    for img in t['images']:
        emu = subprocess.run([sys.executable, os.path.join(CFW, 'tests', 'emu_slice.py'), out_path,
                              os.path.abspath(target_json), img['name']], capture_output=True, text=True)
        summary = emu.stdout.strip().splitlines()[-1] if emu.stdout.strip() else emu.stderr[-500:]
        lines_md.append(f'- Emulated end-to-end tests, image {img["name"]} (Unicorn, real stock + CFW code): '
                        f'{summary}')
        if emu.returncode != 0:
            errs.append(f'emulation tests failed ({img["name"]}):\n' + emu.stdout + emu.stderr[-2000:])
        logs.append((img['name'], emu.stdout))
    lines_md.append(f'- Result: **{"PASS" if not errs else "FAIL"}**')
    for e in errs:
        lines_md.append(f'  - ERROR: {e}')
    md = out_path[:-4] + '.md'
    if os.path.exists(md):
        with open(md, 'a') as f:
            f.write('\n'.join(lines_md) + '\n')
            for name, log in logs:
                f.write(f'\n<details><summary>Emulation test log ({name})</summary>\n\n```\n' + log + '```\n</details>\n')
    print('\n'.join(lines_md))
    return not errs


if __name__ == '__main__':
    ok = verify_build(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    sys.exit(0 if ok else 1)
