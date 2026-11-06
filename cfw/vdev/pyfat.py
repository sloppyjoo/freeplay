"""Minimal pure-Python FAT32 image tool for the virtual SD card (no mkfs.fat / mtools needed).

Covers what the vdev needs: format a fresh FAT32 image the way mkfs.fat lays one out (1 sector
per cluster below 260 MB, 32 reserved sectors, 2 FATs, FSINFO at 1, backup boot at 6), create
directories, write files (long names via VFAT LFN entries), list a directory and read a file.
Not a general FAT driver: no deletes, no fragmentation handling beyond following cluster chains.
"""
import struct
import time

SECTOR = 512
EOC = 0x0FFFFFFF
ATTR_DIR = 0x10
ATTR_ARCHIVE = 0x20
ATTR_LFN = 0x0F


class Fat32:
    def __init__(self, path):
        self.f = open(path, 'r+b')
        bs = self._read(0, 1)
        self.spc = bs[13]
        self.reserved = struct.unpack_from('<H', bs, 14)[0]
        self.nfats = bs[16]
        self.total = struct.unpack_from('<I', bs, 32)[0]
        self.fatsz = struct.unpack_from('<I', bs, 36)[0]
        self.root = struct.unpack_from('<I', bs, 44)[0]
        self.data = self.reserved + self.nfats * self.fatsz
        self.nclus = (self.total - self.data) // self.spc
        self.csize = self.spc * SECTOR
        fat = self._read(self.reserved, self.fatsz)
        self.fat = list(struct.unpack('<%dI' % (len(fat) // 4), fat))
        self.dirty = False

    # ---- low level ------------------------------------------------------------------------
    def _read(self, lba, n):
        self.f.seek(lba * SECTOR)
        return self.f.read(n * SECTOR)

    def _write(self, lba, data):
        self.f.seek(lba * SECTOR)
        self.f.write(data)

    def _clus_lba(self, c):
        return self.data + (c - 2) * self.spc

    def _chain(self, c):
        out = []
        while 2 <= c < 0x0FFFFFF8:
            out.append(c)
            c = self.fat[c] & 0x0FFFFFFF
        return out

    def _alloc(self, n, zero=True):
        got = []
        for c in range(2, self.nclus + 2):
            if self.fat[c] == 0:
                got.append(c)
                if len(got) == n:
                    break
        if len(got) < n:
            raise IOError('card full')
        for a, b in zip(got, got[1:] + [None]):
            self.fat[a] = b if b else EOC
        if zero:
            for c in got:
                self._write(self._clus_lba(c), bytes(self.csize))
        self.dirty = True
        return got

    def _read_chain(self, c):
        return b''.join(self._read(self._clus_lba(x), self.spc) for x in self._chain(c))

    def close(self):
        if self.dirty:
            raw = struct.pack('<%dI' % len(self.fat), *self.fat)
            for i in range(self.nfats):
                self._write(self.reserved + i * self.fatsz, raw)
            free = self.fat[2:self.nclus + 2].count(0)
            fsi = bytearray(self._read(1, 1))
            struct.pack_into('<II', fsi, 488, free, 0xFFFFFFFF)
            self._write(1, bytes(fsi))
            self._write(7, bytes(fsi))
        self.f.close()

    # ---- directories ----------------------------------------------------------------------
    def _entries(self, dclus):
        raw = self._read_chain(dclus)
        lfn = []
        for off in range(0, len(raw), 32):
            e = raw[off:off + 32]
            if e[0] == 0:
                break
            if e[0] == 0xE5:
                lfn = []
                continue
            if e[11] == ATTR_LFN:
                part = e[1:11] + e[14:26] + e[28:32]
                lfn.insert(0, part.decode('utf-16le', 'ignore').split('\x00')[0].rstrip('￿'))
                continue
            short = e[0:8].decode('ascii', 'replace').rstrip() + \
                ('.' + e[8:11].decode('ascii', 'replace').rstrip() if e[8:11].strip() else '')
            name = ''.join(lfn) or short
            lfn = []
            if e[11] & 0x08:
                continue  # volume label
            clus = (struct.unpack_from('<H', e, 20)[0] << 16) | struct.unpack_from('<H', e, 26)[0]
            yield name, short, e[11], clus, struct.unpack_from('<I', e, 28)[0]

    def _lookup(self, path):
        c = self.root
        for part in [p for p in path.strip('/').split('/') if p]:
            for name, short, attr, clus, size in self._entries(c):
                if part.lower() in (name.lower(), short.lower()):
                    c = clus if attr & ATTR_DIR else (c, clus, size)
                    break
            else:
                return None
        return c

    def _short_name(self, dclus, name):
        base, _, ext = name.rpartition('.') if '.' in name.strip('.') else (name, '', '')
        ok = lambda s: ''.join(ch for ch in s.upper() if ch.isalnum() or ch in '_-~!#$%&()')
        b, x = ok(base), ok(ext)[:3]
        if name.upper() == (b + ('.' + x if x else '')) and len(b) <= 8 and b:
            return b.ljust(8).encode() + x.ljust(3).encode(), False
        used = {s for _, s, _, _, _ in self._entries(dclus)}
        for i in range(1, 1000):
            tail = '~%d' % i
            s = (b[:8 - len(tail)] or 'FILE') + tail
            if (s + ('.' + x if x else '')) not in used:
                return s.ljust(8).encode() + x.ljust(3).encode(), True
        raise IOError('no short name')

    def _add_entry(self, dclus, name, attr, clus, size):
        short, need_lfn = self._short_name(dclus, name)
        ents = []
        if need_lfn or name != name.upper():
            csum = 0
            for ch in short:
                csum = (((csum & 1) << 7) + (csum >> 1) + ch) & 0xFF
            u = name.encode('utf-16le') + b'\x00\x00'
            u += b'\xff' * ((-len(u)) % 26)
            parts = [u[i:i + 26] for i in range(0, len(u), 26)]
            for i in range(len(parts), 0, -1):
                p = parts[i - 1]
                seq = i | (0x40 if i == len(parts) else 0)
                ents.append(bytes([seq]) + p[0:10] + bytes([ATTR_LFN, 0, csum]) + p[10:22] + b'\0\0' + p[22:26])
        t = time.localtime()
        dt = ((t.tm_year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday
        tm = (t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2)
        ents.append(short + struct.pack('<BBBHHHHHHHI', attr, 0, 0, tm, dt, dt, clus >> 16, tm, dt,
                                         clus & 0xFFFF, size))
        need = b''.join(ents)
        chain = self._chain(dclus)
        raw = bytearray(self._read_chain(dclus))
        # first run of free slots (0x00 or 0xE5) long enough
        run, start = 0, None
        for off in range(0, len(raw), 32):
            if raw[off] in (0, 0xE5):
                if run == 0:
                    start = off
                run += 1
                if run * 32 >= len(need):
                    break
            else:
                run = 0
        else:
            extra = self._alloc((len(need) + self.csize - 1) // self.csize + 1)
            self.fat[chain[-1]] = extra[0]
            chain += extra
            start = start if run else len(raw)
            raw += bytes(len(extra) * self.csize)
        raw[start:start + len(need)] = need
        for i, c in enumerate(chain):
            self._write(self._clus_lba(c), bytes(raw[i * self.csize:(i + 1) * self.csize]))

    def mkdir(self, path):
        parent, _, name = path.strip('/').rpartition('/')
        pc = self._lookup(parent) if parent else self.root
        if not isinstance(pc, int):
            raise IOError('no parent ' + parent)
        if self._lookup(path) is not None:
            return
        c = self._alloc(1)[0]
        dot = lambda n, cl: n.ljust(11).encode() + struct.pack('<BBBHHHHHHHI', ATTR_DIR, 0, 0, 0, 0x21, 0x21,
                                                                cl >> 16, 0, 0x21, cl & 0xFFFF, 0)
        self._write(self._clus_lba(c), dot('.', c) + dot('..', 0 if pc == self.root else pc))
        self._add_entry(pc, name, ATTR_DIR, c, 0)

    def write_file(self, path, data):
        parent, _, name = path.strip('/').rpartition('/')
        if parent:
            acc = ''
            for p in parent.split('/'):
                acc += '/' + p
                self.mkdir(acc)
        pc = self._lookup(parent) if parent else self.root
        if self._lookup(path) is not None:
            raise IOError('exists: ' + path)
        n = (len(data) + self.csize - 1) // self.csize
        chain = self._alloc(n, zero=False) if n else []
        for i, c in enumerate(chain):
            blk = data[i * self.csize:(i + 1) * self.csize]
            self._write(self._clus_lba(c), blk + bytes(self.csize - len(blk)))
        self._add_entry(pc, name, ATTR_ARCHIVE, chain[0] if chain else 0, len(data))

    def listdir(self, path='/'):
        c = self._lookup(path) if path.strip('/') else self.root
        if not isinstance(c, int):
            return []
        return [(n, a, s) for n, _, a, _, s in self._entries(c) if n not in ('.', '..')]

    def read_file(self, path):
        r = self._lookup(path)
        if not isinstance(r, tuple):
            return None
        _, clus, size = r
        return self._read_chain(clus)[:size] if clus else b''


def mkfs(path, label='PLAYSD'):
    """Format the (already sized) image file as FAT32, laid out like mkfs.fat -F 32."""
    with open(path, 'r+b') as f:
        f.seek(0, 2)
        total = f.tell() // SECTOR
        spc = 1 if total * SECTOR < 260 * 1024 * 1024 else 8
        reserved, nfats = 32, 2
        fatsz = 1
        while True:
            clusters = (total - reserved - nfats * fatsz) // spc
            need = ((clusters + 2) * 4 + SECTOR - 1) // SECTOR
            if need <= fatsz:
                break
            fatsz = need
        bs = bytearray(SECTOR)
        bs[0:3] = b'\xeb\x58\x90'
        bs[3:11] = b'mkfs.fat'
        struct.pack_into('<HBHBHHBHHHII', bs, 11, SECTOR, spc, reserved, nfats, 0, 0, 0xF8, 0, 32, 64, 0, total)
        struct.pack_into('<IHHIHH', bs, 36, fatsz, 0, 0, 2, 1, 6)
        struct.pack_into('<BBBI', bs, 64, 0x80, 0, 0x29, int(time.time()) & 0xFFFFFFFF)
        bs[71:82] = label.upper().ljust(11)[:11].encode()
        bs[82:90] = b'FAT32   '
        bs[510:512] = b'\x55\xaa'
        fsi = bytearray(SECTOR)
        struct.pack_into('<I', fsi, 0, 0x41615252)
        struct.pack_into('<III', fsi, 484, 0x61417272, clusters - 1, 3)
        struct.pack_into('<I', fsi, 508, 0xAA550000)
        for base in (0, 6):
            f.seek(base * SECTOR)
            f.write(bs)
            f.write(fsi)
        fat = bytearray(fatsz * SECTOR)
        struct.pack_into('<III', fat, 0, 0x0FFFFFF8, 0x0FFFFFFF, EOC)
        for i in range(nfats):
            f.seek((reserved + i * fatsz) * SECTOR)
            f.write(fat)
        root_lba = reserved + nfats * fatsz
        f.seek(root_lba * SECTOR)
        f.write(label.upper().ljust(11)[:11].encode() + bytes([0x08]) + bytes(20) + bytes(spc * SECTOR - 32))
