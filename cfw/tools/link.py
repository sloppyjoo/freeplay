#!/usr/bin/env python3
"""Minimal static linker for CFW patch objects (ARM Thumb-2, ELF32 REL relocations).

Places the allocatable read-only sections of one or more relocatable objects at a fixed
flash address, resolves undefined symbols against a firmware symbol map, applies the
relocations and returns a flat binary. No writable sections are allowed: patch code may
only use RAM it is given explicitly through the symbol map (see cfw/targets/*/symbols.ld).

Supported relocations (AAELF32 names):
  R_ARM_ABS32, R_ARM_TARGET1, R_ARM_REL32, R_ARM_THM_CALL, R_ARM_THM_JUMP24,
  R_ARM_THM_MOVW_ABS_NC, R_ARM_THM_MOVT_ABS, R_ARM_THM_JUMP11, R_ARM_THM_JUMP8,
  R_ARM_NONE, R_ARM_V4BX.  .ARM.exidx* sections (and their PREL31 relocs) are dropped.

Symbol files use the GNU ld assignment syntax that the target folders use:
    name = 0x600e7995;      /* comments allowed */
Function addresses must carry the Thumb bit (odd); data addresses must not.

usage: link.py --base 0x6018cc00 --syms a.ld [--syms b.ld] -o out.bin x.o [y.o ...]
"""
import argparse
import re
import struct
import sys

from elftools.elf.elffile import ELFFile
from elftools.elf.constants import SH_FLAGS

R = {
    'NONE': 0, 'ABS32': 2, 'REL32': 3, 'THM_CALL': 10, 'THM_JUMP24': 30, 'TARGET1': 38,
    'V4BX': 40, 'PREL31': 42, 'THM_MOVW_ABS_NC': 47, 'THM_MOVT_ABS': 48, 'THM_JUMP11': 102,
    'THM_JUMP8': 103,
}
RNAME = {v: k for k, v in R.items()}


class LinkError(Exception):
    pass


def load_symbols(path):
    syms = {}
    rx = re.compile(r'^\s*([A-Za-z_.$][\w.$]*)\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*;')
    with open(path) as f:
        for ln in f:
            ln = re.sub(r'/\*.*?\*/', '', ln)
            m = rx.match(ln)
            if m:
                syms[m.group(1)] = int(m.group(2), 0)
    return syms


# ---- Thumb-2 instruction field helpers ---------------------------------------------------
def _hw(buf, off):
    return struct.unpack_from('<HH', buf, off)


def decode_bl(buf, off):
    h, l = _hw(buf, off)
    s = (h >> 10) & 1
    j1, j2 = (l >> 13) & 1, (l >> 11) & 1
    i1, i2 = 1 - (j1 ^ s), 1 - (j2 ^ s)
    imm = (s << 24) | (i1 << 23) | (i2 << 22) | ((h & 0x3FF) << 12) | ((l & 0x7FF) << 1)
    return imm - (1 << 25) if s else imm


def encode_bl(buf, off, value, is_call):
    if value & 1:
        raise LinkError(f'branch offset {value:#x} is odd')
    if not -(1 << 24) <= value < (1 << 24):
        raise LinkError(f'branch offset {value:#x} out of +-16MiB range')
    v = value & 0x1FFFFFF
    s = (v >> 24) & 1
    i1, i2 = (v >> 23) & 1, (v >> 22) & 1
    j1, j2 = (1 - i1) ^ s, (1 - i2) ^ s
    h = 0xF000 | (s << 10) | ((v >> 12) & 0x3FF)
    # BL: 11x1 with bit12=1, bit14=1; B.W (T4): bit12=1, bit14=0
    l = (0xD000 if is_call else 0x9000) | (j1 << 13) | (j2 << 11) | ((v >> 1) & 0x7FF)
    struct.pack_into('<HH', buf, off, h, l)


def decode_movw(buf, off):
    h, l = _hw(buf, off)
    imm = ((h & 0xF) << 12) | (((h >> 10) & 1) << 11) | (((l >> 12) & 7) << 8) | (l & 0xFF)
    return imm - 0x10000 if imm & 0x8000 else imm  # REL addend is signed 16-bit


def encode_movw(buf, off, imm16):
    h, l = _hw(buf, off)
    imm16 &= 0xFFFF
    h = (h & 0xFBF0) | ((imm16 >> 12) & 0xF) | (((imm16 >> 11) & 1) << 10)
    l = (l & 0x8F00) | (((imm16 >> 8) & 7) << 12) | (imm16 & 0xFF)
    struct.pack_into('<HH', buf, off, h, l)


# ---- linker -------------------------------------------------------------------------------
class Obj:
    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        self.elf = ELFFile(self.f)
        if self.elf['e_machine'] != 'EM_ARM' or self.elf.elfclass != 32:
            raise LinkError(f'{path}: not an ELF32 ARM object')
        if self.elf['e_type'] != 'ET_REL':
            raise LinkError(f'{path}: not a relocatable object')
        self.symtab = self.elf.get_section_by_name('.symtab')
        self.place = {}  # section index -> address


class Linker:
    def __init__(self, base, fw_syms, limit=None, fixed=None, ext=None):
        self.base, self.fw, self.limit = base, dict(fw_syms), limit
        # second region for whole objects (code past the image end): (addr, limit, {obj paths});
        # its section ".ext_hdr" (if any) goes first
        self.ext = ext
        self.ext_out = None  # (addr, bytearray) after link()
        # sections placed at their own fixed address: {section name: (addr, limit)}
        self.fixed = dict(fixed or {})
        self.fixed_out = {}  # section name -> (addr, bytes) after link()
        self.objs = []
        self.log = []      # applied relocations, for the verification report
        self.symbols = {}  # name -> address (globals and named locals of placed sections)
        self.sections = []  # (addr, size, obj path, section name)

    def add(self, path):
        self.objs.append(Obj(path))

    @staticmethod
    def _wanted(sec):
        if not sec['sh_flags'] & SH_FLAGS.SHF_ALLOC:
            return False
        if sec['sh_type'] == 'SHT_ARM_EXIDX' or sec.name.startswith('.ARM.exidx'):
            return False
        return True

    def _layout(self):
        groups = {'text': [], 'rodata': []}
        for o in self.objs:
            for i, sec in enumerate(o.elf.iter_sections()):
                if not self._wanted(sec):
                    continue
                if sec['sh_size'] and (sec['sh_flags'] & SH_FLAGS.SHF_WRITE or sec['sh_type'] == 'SHT_NOBITS'):
                    raise LinkError(f'{o.path}: writable/bss section {sec.name} not allowed; '
                                    'use a RAM address from the target symbol map')
                if sec.name in self.fixed:
                    fa, flim = self.fixed[sec.name]
                    if fa % max(sec['sh_addralign'], 1):
                        raise LinkError(f'{sec.name}: fixed address {fa:#x} breaks its alignment')
                    if fa + sec['sh_size'] > flim:
                        raise LinkError(f'{sec.name}: {sec["sh_size"]} bytes do not fit {fa:#x}..{flim:#x}')
                    o.place[i] = fa
                    self._fixed_bufs[sec.name] = (fa, bytearray(sec.data()))
                    self.sections.append((fa, sec['sh_size'], o.path, sec.name))
                    continue
                kind = 'text' if sec['sh_flags'] & SH_FLAGS.SHF_EXECINSTR else 'rodata'
                if self.ext and o.path in self.ext[2]:
                    kind = 'ext_hdr' if sec.name == '.ext_hdr' else 'ext_' + kind
                groups.setdefault(kind, []).append((o, i, sec))
        addr = self.base
        image = bytearray()
        for kind in ('text', 'rodata'):
            # like ld.lld: an output section is aligned to its most-aligned input section; the gap
            # before it is zero (objcopy -O binary fills gaps between output sections with 0)
            gal = max([max(sec['sh_addralign'], 1) for _, _, sec in groups[kind]] or [1])
            gpad = (-addr) % gal
            image += b'\0' * gpad
            addr += gpad
            for o, i, sec in groups[kind]:
                al = max(sec['sh_addralign'], 1)
                pad = (-addr) % al
                # same fill as ld.lld so link.py output can be cross-checked byte for byte
                image += (b'\xd4' if kind == 'text' else b'\0') * pad
                addr += pad
                o.place[i] = addr
                if sec['sh_size']:
                    self.sections.append((addr, sec['sh_size'], o.path, sec.name))
                image += sec.data()
                addr += sec['sh_size']
        if self.limit is not None and addr > self.limit:
            raise LinkError(f'image {self.base:#x}..{addr:#x} exceeds limit {self.limit:#x}')
        if self.ext:
            ea, elim, _ = self.ext
            addr, buf = ea, bytearray()
            for kind in ('ext_hdr', 'ext_text', 'ext_rodata'):
                for o, i, sec in groups.get(kind, []):
                    al = max(sec['sh_addralign'], 1)
                    pad = (-addr) % al
                    buf += (b'\xd4' if kind == 'ext_text' else b'\0') * pad
                    addr += pad
                    o.place[i] = addr
                    if sec['sh_size']:
                        self.sections.append((addr, sec['sh_size'], o.path, sec.name))
                    buf += sec.data()
                    addr += sec['sh_size']
            buf += b'\0' * ((-len(buf)) % 4)
            if ea + len(buf) > elim:
                raise LinkError(f'ext region {ea:#x}..{ea + len(buf):#x} exceeds limit {elim:#x}')
            self._fixed_bufs['__ext__'] = (ea, buf)
        return image

    def _collect_globals(self):
        defined = {}
        for o in self.objs:
            for s in o.symtab.iter_symbols():
                if not s.name or s['st_info']['bind'] == 'STB_LOCAL':
                    continue
                shn = s['st_shndx']
                if shn == 'SHN_UNDEF':
                    continue
                if shn == 'SHN_ABS':
                    v = s['st_value']
                elif shn in o.place:
                    v = o.place[shn] + s['st_value']
                else:
                    continue
                if s.name in defined:
                    raise LinkError(f'duplicate symbol {s.name}')
                if s.name in self.fw:
                    raise LinkError(f'{s.name} defined in patch and in firmware map')
                defined[s.name] = v
        return defined

    def _symval(self, o, idx, globs):
        s = o.symtab.get_symbol(idx)
        shn = s['st_shndx']
        name = s.name
        if s['st_info']['type'] == 'STT_SECTION':
            if shn not in o.place:
                raise LinkError(f'{o.path}: reloc against unplaced section {shn}')
            return o.place[shn], f'[{o.elf.get_section(shn).name}]', False
        if shn == 'SHN_UNDEF':
            if name in globs:
                v = globs[name]
            elif name in self.fw:
                v = self.fw[name]
            else:
                raise LinkError(f'undefined symbol {name}')
            return v, name, bool(v & 1)
        if shn == 'SHN_ABS':
            return s['st_value'], name, False
        if shn not in o.place:
            raise LinkError(f'{o.path}: symbol {name} in unplaced section')
        v = o.place[shn] + s['st_value']
        is_thumb_func = s['st_info']['type'] == 'STT_FUNC' and bool(v & 1)
        return v, name, is_thumb_func

    def _buf(self, image, P, n):
        if self.base <= P and P + n <= self.base + len(image):
            return image, P - self.base
        for fa, buf in self._fixed_bufs.values():
            if fa <= P and P + n <= fa + len(buf):
                return buf, P - fa
        raise LinkError(f'relocation place {P:#x} outside every placed section')

    def link(self):
        self._fixed_bufs = {}
        image = self._layout()
        globs = self._collect_globals()
        for o in self.objs:
            for rs in o.elf.iter_sections():
                if rs['sh_type'] == 'SHT_RELA':
                    raise LinkError(f'{o.path}: RELA relocations not supported')
                if rs['sh_type'] != 'SHT_REL':
                    continue
                tgt = rs['sh_info']
                if tgt not in o.place:
                    continue  # relocations for dropped sections (exidx, debug)
                for r in rs.iter_relocations():
                    self._apply(o, image, o.place[tgt], r, globs)
        self.fixed_out = {n: (a, bytes(b)) for n, (a, b) in self._fixed_bufs.items()}
        ext = self.fixed_out.pop('__ext__', None)
        self.ext_out = (ext[0], bytearray(ext[1])) if ext else None
        self.symbols = dict(globs)
        for o in self.objs:
            for s in o.symtab.iter_symbols():
                if s.name and not s.name.startswith('$') and s['st_info']['type'] in ('STT_FUNC', 'STT_OBJECT') \
                        and s['st_shndx'] in o.place:
                    self.symbols.setdefault(s.name, o.place[s['st_shndx']] + s['st_value'])
        return bytes(image)

    def _apply(self, o, image, sec_addr, r, globs):
        typ = r['r_info_type']
        P = sec_addr + r['r_offset']
        if typ in (R['NONE'], R['V4BX']):
            return
        image, off = self._buf(image, P, 2 if typ in (R['THM_JUMP11'], R['THM_JUMP8']) else 4)
        Sv, name, thumb = self._symval(o, r['r_info_sym'], globs)
        T = 1 if thumb else 0
        S = Sv & ~1 if thumb else Sv
        if typ in (R['ABS32'], R['TARGET1']):
            A = struct.unpack_from('<i', image, off)[0]
            val = ((S + A) | T) & 0xFFFFFFFF
            struct.pack_into('<I', image, off, val)
        elif typ == R['REL32']:
            A = struct.unpack_from('<i', image, off)[0]
            val = (((S + A) | T) - P) & 0xFFFFFFFF
            struct.pack_into('<I', image, off, val)
        elif typ in (R['THM_CALL'], R['THM_JUMP24']):
            if not T:
                raise LinkError(f'{name}: Thumb branch to non-Thumb target {Sv:#x} at {P:#x} '
                                '(firmware function symbols must be odd)')
            A = decode_bl(image, off)
            val = (S + A) - P
            encode_bl(image, off, val, typ == R['THM_CALL'])
        elif typ == R['THM_MOVW_ABS_NC']:
            A = decode_movw(image, off)
            val = ((S + A) | T) & 0xFFFF
            encode_movw(image, off, val)
        elif typ == R['THM_MOVT_ABS']:
            A = decode_movw(image, off)
            val = ((S + A) >> 16) & 0xFFFF
            encode_movw(image, off, val)
        elif typ == R['THM_JUMP11']:
            h = struct.unpack_from('<H', image, off)[0]
            A = ((h & 0x7FF) << 1)
            A = A - 0x1000 if A & 0x800 else A
            val = (S + A) - P
            if not -2048 <= val < 2048:
                raise LinkError(f'JUMP11 out of range at {P:#x}')
            struct.pack_into('<H', image, off, (h & 0xF800) | ((val >> 1) & 0x7FF))
        elif typ == R['THM_JUMP8']:
            h = struct.unpack_from('<H', image, off)[0]
            A = ((h & 0xFF) << 1)
            A = A - 0x200 if A & 0x100 else A
            val = (S + A) - P
            if not -256 <= val < 256:
                raise LinkError(f'JUMP8 out of range at {P:#x}')
            struct.pack_into('<H', image, off, (h & 0xFF00) | ((val >> 1) & 0xFF))
        else:
            raise LinkError(f'{o.path}: unsupported relocation {RNAME.get(typ, typ)} at {P:#x} ({name})')
        self.log.append(dict(P=P, type=RNAME.get(typ, str(typ)), sym=name, S=Sv, value=val))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', type=lambda s: int(s, 0), required=True)
    ap.add_argument('--limit', type=lambda s: int(s, 0))
    ap.add_argument('--syms', action='append', default=[])
    ap.add_argument('-o', required=True)
    ap.add_argument('--map')
    ap.add_argument('objs', nargs='+')
    a = ap.parse_args(argv)
    fw = {}
    for p in a.syms:
        fw.update(load_symbols(p))
    lk = Linker(a.base, fw, a.limit)
    for p in a.objs:
        lk.add(p)
    img = lk.link()
    with open(a.o, 'wb') as f:
        f.write(img)
    if a.map:
        with open(a.map, 'w') as f:
            for addr, size, path, name in lk.sections:
                f.write(f'{addr:#010x} {size:#7x} {name} ({path})\n')
            for n, v in sorted(lk.symbols.items(), key=lambda kv: kv[1]):
                f.write(f'{v:#010x} {n}\n')
            for e in lk.log:
                f.write(f'reloc {e["P"]:#010x} {e["type"]:<16} {e["sym"]} = {e["S"]:#x} -> {e["value"]:#x}\n')
    print(f'linked {len(img)} bytes at {a.base:#x}, {len(lk.log)} relocations')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
