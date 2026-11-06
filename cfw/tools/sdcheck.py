#!/usr/bin/env python3
"""Check files on the Play's SD card the way the Play's bootloader reads them: straight from the
card's FAT32 file system (boot sector, FAT, directory entries, cluster chain), not through macOS.

Why: macOS's FAT driver (FSKit msdos) was seen to return wrong bytes for parts of freshly written,
fragmented files when it reads more than one cluster at a time, so `shasum` on the mounted card is
no proof either way (docs/FIRMWAfirmware notes.md).

READ-ONLY: the device or image is opened O_RDONLY and only read, in whole sectors. Reading a raw
disk needs root:

    sudo /usr/bin/python3 cfw/tools/sdcheck.py                     # card mounted at /Volumes/POLYEND
    sudo /usr/bin/python3 cfw/tools/sdcheck.py --device /dev/rdisk10s1 /Firmware/X.ppf=local/X.ppf
    python3 cfw/tools/sdcheck.py --device card.img ...             # an image file needs no root

Without file arguments it checks /Firmware/PlayPlus_v1.3.0_CFW4.ppf against the release image and the
stock recovery file /Firmware/PlayPlus_v1.3.0.ppf against PlayPlus_v1.3.0.ppf (paths relative to the
repo). Exit 0 = every file matches byte for byte.
"""
import argparse
import hashlib
import os
import plistlib
import struct
import subprocess
import sys
from array import array

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
DEFAULT_FILES = ['/Firmware/PlayPlus_v1.3.0_CFW4.ppf=cfw/release/PlayPlus_v1.3.0_CFW4.ppf',
                 '/Firmware/PlayPlus_v1.3.0.ppf=PlayPlus_v1.3.0.ppf']
EOC_MIN, BAD = 0x0FFFFFF8, 0x0FFFFFF7


class Fat32Reader:
    def __init__(self, path):
        self.fd = os.open(path, os.O_RDONLY)
        bs = os.pread(self.fd, 512, 0)
        if len(bs) < 512 or bs[510:512] != b'\x55\xaa':
            raise SystemExit(f'{path}: no boot sector signature (not a FAT volume?)')
        self.bps = struct.unpack_from('<H', bs, 11)[0]
        self.spc = bs[13]
        self.reserved = struct.unpack_from('<H', bs, 14)[0]
        self.nfats = bs[16]
        self.fatsz = struct.unpack_from('<I', bs, 36)[0]
        total = struct.unpack_from('<I', bs, 32)[0]
        flags = struct.unpack_from('<H', bs, 40)[0]
        self.root = struct.unpack_from('<I', bs, 44)[0]
        if struct.unpack_from('<H', bs, 22)[0] != 0 or self.fatsz == 0 or self.bps not in (512, 1024, 2048, 4096):
            raise SystemExit(f'{path}: not FAT32')
        self.csize = self.bps * self.spc
        self.data_off = (self.reserved + self.nfats * self.fatsz) * self.bps
        self.maxclus = (total - self.reserved - self.nfats * self.fatsz) // self.spc + 1
        self.active = (flags & 0xF) if flags & 0x80 else 0          # mirroring off: one active FAT
        self.fats = [self._fat(k) for k in range(self.nfats)]
        self.fat = self.fats[self.active]

    def read(self, off, n):
        assert off % self.bps == 0 and n % self.bps == 0, (off, n)
        out = bytearray()
        while len(out) < n:                                         # whole sectors, 4 MB at a time
            chunk = os.pread(self.fd, min(n - len(out), 4 << 20), off + len(out))
            if not chunk:
                raise SystemExit(f'short read at {off + len(out):#x}')
            out += chunk
        return bytes(out)

    def _fat(self, k):
        a = array('I')
        a.frombytes(self.read((self.reserved + k * self.fatsz) * self.bps, self.fatsz * self.bps))
        if sys.byteorder != 'little':
            a.byteswap()
        return a

    def chain(self, first):
        """the cluster chain from `first`; raises on a free / bad / out-of-range link or a loop"""
        out, c, seen = [], first, set()
        while True:
            if c < 2 or c > self.maxclus or c in seen:
                raise ValueError(f'broken chain at cluster {c:#x} after {len(out)} clusters')
            seen.add(c)
            out.append(c)
            nxt = self.fat[c] & 0x0FFFFFFF
            if nxt >= EOC_MIN:
                return out
            if nxt in (0, BAD):
                raise ValueError(f'cluster {c:#x} links to {nxt:#x}')
            c = nxt

    def cluster(self, c):
        return self.read(self.data_off + (c - 2) * self.csize, self.csize)

    def entries(self, dclus):
        """(name, attr, first_cluster, size) of a directory, long names assembled from LFN entries"""
        lfn = {}
        for c in self.chain(dclus):
            d = self.cluster(c)
            for o in range(0, len(d), 32):
                e = d[o:o + 32]
                if e[0] == 0:
                    return
                if e[0] == 0xE5:
                    lfn = {}
                    continue
                attr = e[11]
                if attr & 0x3F == 0x0F:
                    part = e[1:11] + e[14:26] + e[28:32]
                    lfn[e[0] & 0x1F] = (e[13], part)
                    continue
                short = e[0:11]
                ck = 0
                for b in short:
                    ck = (((ck & 1) << 7) | (ck >> 1)) + b & 0xFF
                name = None
                if lfn and all(v[0] == ck for v in lfn.values()):
                    raw = b''.join(lfn[k][1] for k in sorted(lfn))
                    name = raw.decode('utf-16-le', 'replace').split('\x00')[0].replace('￿', '')
                lfn = {}
                if attr & 0x08:                                     # volume label
                    continue
                if name is None:
                    base, ext = short[:8].decode('latin-1').rstrip(), short[8:].decode('latin-1').rstrip()
                    if e[12] & 0x08:
                        base = base.lower()
                    if e[12] & 0x10:
                        ext = ext.lower()
                    name = base + ('.' + ext if ext else '')
                first = struct.unpack_from('<H', e, 20)[0] << 16 | struct.unpack_from('<H', e, 26)[0]
                yield name, attr, first, struct.unpack_from('<I', e, 28)[0]

    def lookup(self, path):
        clus, ent = self.root, None
        for part in [p for p in path.split('/') if p]:
            ent = next((x for x in self.entries(clus) if x[0].lower() == part.lower()), None)
            if ent is None:
                return None
            clus = ent[2]
        return ent


def device_for(volume):
    out = subprocess.run(['diskutil', 'info', '-plist', volume], capture_output=True, check=True).stdout
    node = plistlib.loads(out)['DeviceNode']                        # /dev/disk10s1
    return node.replace('/dev/disk', '/dev/rdisk')


def runs(clusters):
    out = []
    for c in clusters:
        if out and out[-1][1] == c - 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return out


def check(fs, card_path, ref_path):
    ref = open(ref_path if os.path.isabs(ref_path) else os.path.join(REPO, ref_path), 'rb').read()
    print(f'{card_path}')
    ent = fs.lookup(card_path)
    if ent is None:
        print('   FAIL not found on the card')
        return False
    name, attr, first, size = ent
    try:
        ch = fs.chain(first)
    except ValueError as e:
        print(f'   FAIL {e}')
        return False
    need = (size + fs.csize - 1) // fs.csize
    fr = runs(ch)
    print(f'   {size} bytes (reference {len(ref)}), {len(ch)} clusters (needs {need}), {len(fr)} fragment(s): ' +
          ', '.join(f'{a:#x}..{b:#x}' if a != b else f'{a:#x}' for a, b in fr[:8]) + (' ...' if len(fr) > 8 else ''))
    differ = [k for k in range(len(fs.fats)) if any(fs.fats[k][c] != fs.fat[c] for c in ch)]
    if differ:
        print(f'   note: FAT copy {differ} differs from the active FAT for this chain')
    data = b''.join(fs.cluster(c) for c in ch)[:size]
    bad = [k * fs.csize for k in range((max(len(ref), len(data)) + fs.csize - 1) // fs.csize)
           if data[k * fs.csize:(k + 1) * fs.csize] != ref[k * fs.csize:(k + 1) * fs.csize]]
    ok = size == len(ref) and len(ch) == need and data == ref
    print(f"   {'OK  ' if ok else 'FAIL'} sha256 on the card {hashlib.sha256(data).hexdigest()}" +
          (f'\n        reference     {hashlib.sha256(ref).hexdigest()}\n        differs at file offsets ' +
           ', '.join(hex(x) for x in bad[:12]) if not ok else ''))
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('files', nargs='*', help='/path/on/card=local/reference (default: CFW4 + stock)')
    ap.add_argument('--device', help='raw device or image file (default: the device of --volume)')
    ap.add_argument('--volume', default='/Volumes/POLYEND')
    a = ap.parse_args()
    dev = a.device or device_for(a.volume)
    try:
        fs = Fat32Reader(dev)
    except PermissionError:
        raise SystemExit(f'{dev}: permission denied - run with sudo (the check only reads)')
    print(f'{dev}: FAT32, {fs.bps} B sectors, {fs.csize // 1024} KB clusters, {fs.nfats} FATs '
          f'(active {fs.active}), {fs.maxclus - 1} clusters')
    ok = all([check(fs, *f.split('=', 1)) for f in (a.files or DEFAULT_FILES)])
    print('ALL MATCH' if ok else 'MISMATCH')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
