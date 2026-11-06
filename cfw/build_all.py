#!/usr/bin/env python3
"""Build, verify and stage every CFW target into cfw/release/.

usage: python3 cfw/build_all.py        (needs clang; ld.lld optional for the cross-check)"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, 'tools'))
import build  # noqa: E402

TARGETS = ['playplus-1.3.0', 'play-1.6.0', 'play-1.6.0-k66']

if __name__ == '__main__':
    rel = os.path.join(HERE, 'release')
    os.makedirs(rel, exist_ok=True)
    for t in TARGETS:
        out = build.build(os.path.join(HERE, 'targets', t, 'target.json'))
        for p in (out, out[:-4] + '.md', out[:-4] + '.patch.json'):
            shutil.copy2(p, rel)
    print(f'release files in {rel}')
