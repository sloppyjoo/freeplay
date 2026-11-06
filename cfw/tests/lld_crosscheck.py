#!/usr/bin/env python3
"""Cross-check cfw/tools/link.py against ld.lld (when available) on the same objects.

usage: lld_crosscheck.py --base 0x.. --syms a.ld [--syms b.ld] x.o [y.o ...]
Exit 0 if the flat images are byte-identical, 1 if they differ, 77 if ld.lld is missing."""
import argparse, os, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
from link import Linker, load_symbols

def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', type=lambda s: int(s, 0), required=True)
    ap.add_argument('--syms', action='append', default=[])
    ap.add_argument('objs', nargs='+')
    a = ap.parse_args(argv)
    lld = shutil.which('ld.lld'); objcopy = shutil.which('llvm-objcopy')
    if not lld or not objcopy:
        print('ld.lld/llvm-objcopy not found; skipping'); return 77
    fw = {}
    for p in a.syms: fw.update(load_symbols(p))
    lk = Linker(a.base, fw)
    for o in a.objs: lk.add(o)
    mine = lk.link()
    with tempfile.TemporaryDirectory() as d:
        ld = os.path.join(d, 'x.ld')
        with open(ld, 'w') as f:
            for n, v in fw.items(): f.write(f'{n} = {v:#x};\n')
            f.write(f'SECTIONS {{ . = {a.base:#x}; .text : {{ *(.text .text.*) }} '
                    f'.rodata : {{ *(.rodata .rodata.*) }} /DISCARD/ : {{ *(.ARM.exidx*) *(.comment) }} }}\n')
        elf = os.path.join(d, 'x.elf'); binp = os.path.join(d, 'x.bin')
        subprocess.run([lld, '-O0', '-T', ld, '-e', '0', '-o', elf] + a.objs, check=True)
        subprocess.run([objcopy, '-O', 'binary', '--only-section=.text', '--only-section=.rodata', elf, binp], check=True)
        theirs = open(binp, 'rb').read()
    if mine == theirs:
        print(f'OK: link.py == ld.lld ({len(mine)} bytes, {len(lk.log)} relocations)'); return 0
    print(f'MISMATCH: link.py {len(mine)} bytes vs ld.lld {len(theirs)} bytes')
    for i in range(min(len(mine), len(theirs))):
        if mine[i] != theirs[i]:
            print(f'  first difference at {a.base + i:#x}: {mine[i:i+8].hex()} vs {theirs[i:i+8].hex()}'); break
    return 1

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
