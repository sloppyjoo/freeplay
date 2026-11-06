#!/usr/bin/env python3
"""Run the virtual Play+ in bounded stages with snapshots.

usage: stage.py [--from SNAP] [--to SNAP] [--insns N] [--seconds S] [--ppf PPF]"""
import argparse
import collections
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import boot  # noqa: E402
from vplay import save_snapshot, load_snapshot  # noqa: E402

DEFAULT_PPF = os.path.join(HERE, '..', '..', 'PlayPlus_v1.3.0.ppf')


def stage(ppf=DEFAULT_PPF, src=None, dst=None, insns=10**10, seconds=480, quiet=False, setup=None,
          sd_files=None, keep_sd=False):
    """keep_sd: boot from reset but keep the existing card image (a power cycle)."""
    vp = boot.make(ppf, fresh=src is None and not keep_sd, sd_files=sd_files)
    if src:
        load_snapshot(vp, src)
    if setup:
        setup(vp)
    names = boot.fnnames()
    pcs = []
    t0 = time.time()

    def chunk(v):
        pcs.append(v.pc())
        if len(pcs) % 500 == 0 and not quiet:
            c = collections.Counter(names(p) for p in pcs[-500:])
            spi = {b: len(s.log) for b, s in v.spi.items() if s.log}
            print(f'  {v.icount / 1e6:7.0f}M {v.ms:6}ms {time.time() - t0:4.0f}s ' +
                  ', '.join(f'{n}:{k}' for n, k in c.most_common(3)) + f' spi={spi} sd={v.card.reads}/{v.card.writes}',
                  flush=True)
        if boot.detect_hang(pcs):
            v.stopped = v.stopped or f'hang {v.pc():#x}'
        if time.time() - t0 > seconds:
            v.stopped = v.stopped or 'time budget'

    vp.run(insns, on_chunk=chunk)
    print(f'stopped: {vp.stopped} at {vp.icount / 1e6:.0f}M insns, {vp.ms} ms; sd r/w {vp.card.reads}/{vp.card.writes}')
    reason = vp.stopped
    if reason and reason.startswith('hang'):
        vp.stopped = None
        boot.report(vp)
    if dst:
        vp.stopped = None
        save_snapshot(vp, dst)
        print('saved', dst)
    return vp, reason


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='src')
    ap.add_argument('--to', dest='dst')
    ap.add_argument('--insns', type=int, default=10**10)
    ap.add_argument('--seconds', type=int, default=480)
    ap.add_argument('--ppf', default=DEFAULT_PPF)
    a = ap.parse_args()
    stage(a.ppf, a.src, a.dst, a.insns, a.seconds)
