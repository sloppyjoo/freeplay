#!/usr/bin/env python3
"""FreePlay patcher: turns YOUR official Polyend .ppf into the FreePlay build of the same version.

    python3 freeplay_patch.py PlayPlus_v1.3.0.ppf
    python3 freeplay_patch.py PolyendPlay_v1.6.0.ppf            (Play, RT units)

Download the official firmware from polyend.com. The patcher checks its SHA-256 against the
versions in ../patches, applies only FreePlay's own bytes (hook words, patch code, new records)
in place, and checks that the result is byte-identical to the build the FreePlay authors tested.
It never contains or downloads Polyend firmware. Python 3.8+, no other packages.

Recovery: keep the official .ppf in /Firmware on the SD card. Flash it from Settings > Firmware >
Update Firmware. If the device won't start, leave only the official file in /Firmware and press
the reset button on the back.
"""
import argparse
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ppf import PPF  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description='Apply FreePlay to an official Polyend Play / Play+ .ppf')
    ap.add_argument('ppf', help='the official firmware file from polyend.com')
    ap.add_argument('--patches', default=os.path.join(HERE, '..', 'patches'))
    ap.add_argument('--out', help='output folder (default: next to the input)')
    a = ap.parse_args()

    data = open(a.ppf, 'rb').read()
    sha = hashlib.sha256(data).hexdigest()
    pkgs = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(a.patches, '*.patch.json')))]
    match = [p for p in pkgs if p['input_sha256'] == sha]
    if not match:
        print(f'Unknown input (SHA-256 {sha}).')
        print('Supported official files:')
        for p in pkgs:
            print(f"  {p['input']}  ({p['device']})  SHA-256 {p['input_sha256']}")
        return 1
    pkg = match[0]
    stock = PPF(text=data)
    out = stock.patched({int(k, 16): bytes.fromhex(v) for k, v in pkg['patches'].items()})
    for ap_ in pkg['appends']:
        out = out.appended(int(ap_['after'], 16), {int(k, 16): bytes.fromhex(v) for k, v in ap_['blobs'].items()})
    if out.sha256() != pkg['output_sha256']:
        print('The result does not match the tested build. Nothing written.')
        return 1
    path = os.path.join(a.out or os.path.dirname(os.path.abspath(a.ppf)), pkg['output'])
    out.save(path)
    print(f"Wrote {path}")
    print(f"  {pkg['device']}, SHA-256 {pkg['output_sha256']} (identical to the tested build)")
    print('Copy it to /Firmware on the SD card, next to the official file, and flash it from')
    print('Settings > Firmware > Update Firmware. To go back, flash the official file the same way.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
