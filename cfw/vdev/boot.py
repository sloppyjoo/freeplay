#!/usr/bin/env python3
"""Boot driver for the virtual Play+: runs until the firmware hangs, then shows the hang.

usage: boot.py [ppf] [--insns N]"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
from vplay import VPlay  # noqa: E402
import periph  # noqa: E402
import hle  # noqa: E402
import sdcard  # noqa: E402
import display  # noqa: E402
import eve  # noqa: E402

SCRATCH = os.environ.get('VPLAY_DIR', os.path.join('/tmp', 'vplay-' + str(os.getuid())))  # same default as cfw/tests


def make(ppf, verbose=False, sd_image=None, fresh=True, sd_files=None, profile=None):
    """sd_files: {'/Samples/x.wav': bytes} copied into a fresh card image. profile: hle.PROFILES
    key (default: $VPLAY_PROFILE, else detected from the image)."""
    prof = hle.PROFILES[profile or os.environ.get('VPLAY_PROFILE') or hle.detect_profile(ppf)]
    vp = VPlay(ppf, base=prof['base'], verbose=verbose)
    vp.profile = prof
    periph.install(vp)
    scratch = os.environ.get('VPLAY_DIR', SCRATCH)
    os.makedirs(scratch, exist_ok=True)
    sd_image = sd_image or os.path.join(scratch, 'sd.img')
    if fresh or not os.path.exists(sd_image):
        sdcard.make_image(sd_image, files=sd_files)
    vp.card = sdcard.SDCard(sd_image)
    hle.install_sd(vp, vp.card, prof)
    hle.install_stubs(vp, prof)
    display.attach(vp)
    eve.attach(vp)
    hle.install_inputs(vp, prof)
    return vp


def fnnames():
    import bisect
    F = [l.split('\t') for l in open(os.path.join(HERE, '..', '..', 'rebuild', 'export', 'functions.tsv')).read()
         .splitlines()[1:]]
    st = sorted((int(p[0], 16), p[5]) for p in F)
    keys = [a for a, _ in st]
    return lambda a: st[bisect.bisect_right(keys, a) - 1][1] if a >= keys[0] else hex(a)


def detect_hang(pcs, window=3000, span=0x80):
    if len(pcs) < window:
        return False
    last = pcs[-window:]
    return max(last) - min(last) < span


def report(vp):
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
    vp.verbose = True
    vp.mmio_log.clear()
    vp.run(4000, chunk=4000) if False else None
    vp.mu.emu_start(vp.pc() | 1, 0xFFFFFFFF, count=4000)
    pcs = sorted(set(e[3] for e in vp.mmio_log))
    pc = vp.pc()
    lo = (min(pcs + [pc]) - 0x20) & ~1
    code = bytes(vp.mu.mem_read(lo, 0x60))
    for i in md.disasm(code, lo):
        if abs(i.address - pc) <= 0x18:
            print(f'   {i.address:08x}: {i.mnemonic:8} {i.op_str}')
    from unicorn import arm_const as A
    regs = ' '.join(f'r{k}={vp.mu.reg_read(getattr(A, f"UC_ARM_REG_R{k}")):#x}' for k in range(8))
    print('   ' + regs)
    seen = {}
    for kind, a, v, p in vp.mmio_log:
        seen.setdefault((kind, a), set()).add(v)
    for (kind, a), vals in sorted(seen.items()):
        print(f'   {kind} {a:#010x} values {[hex(x) for x in list(vals)[:4]]}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf', nargs='?', default=os.path.join(HERE, '..', '..', 'PlayPlus_v1.3.0.ppf'))
    ap.add_argument('--insns', type=int, default=200_000_000)
    a = ap.parse_args()
    vp = make(a.ppf)
    pcs = []
    t = time.time()
    hung = False

    names = fnnames()

    def chunk(v):
        global hung
        pcs.append(v.pc())
        if len(pcs) % 500 == 0:
            import collections
            c = collections.Counter(names(p) for p in pcs[-500:])
            print(f'  {v.icount/1e6:7.0f}M insns, {getattr(v, "ms", 0)} ms: ' +
                  ', '.join(f'{n}:{k}' for n, k in c.most_common(4)), flush=True)
        if detect_hang(pcs):
            v.stopped = v.stopped or f'hang around {v.pc():#x}'

    vp.run(a.insns, on_chunk=chunk)
    print(f'{vp.icount:,} insns in {time.time() - t:.1f}s; stopped: {vp.stopped}')
    if vp.stopped and vp.stopped.startswith('hang'):
        vp.stopped = None
        report(vp)
