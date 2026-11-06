#!/usr/bin/env python3
"""Intel HEX (.ppf) reader and in-place patcher for Polyend firmware files.

Rule: never re-emit a .ppf. A patch changes only the data bytes of existing
data records and recomputes those records' checksums. Every other byte of the file,
including line endings, record lengths and address records, stays identical.

Handles both record styles seen in Polyend files:
  * type 04 (extended linear address) only           -- Play+ 1.3.0
  * mixed type 02 (segment) and 04, two EOF records   -- Play 1.6.0 (K66 + RT images)
A type 02 record resets the linear base and vice versa, matching how the original
files are laid out (see docs/FIRMWARE_ANALYSIS.md).

usage:
  ppf.py info  <file.ppf>
  ppf.py diff  <stock.ppf> <patched.ppf>     # byte-level diff summary (markdown)
"""
import hashlib
import struct
import sys


class HexError(Exception):
    pass


class Record:
    __slots__ = ('lineno', 'raw', 'count', 'offset', 'type', 'data', 'addr')

    def __init__(self, lineno, raw, count, offset, typ, data, addr):
        self.lineno, self.raw, self.count, self.offset = lineno, raw, count, offset
        self.type, self.data, self.addr = typ, data, addr


class PPF:
    """A parsed .ppf that remembers its exact text, so it can be patched in place."""

    def __init__(self, path=None, text=None):
        if text is None:
            with open(path, 'rb') as f:
                text = f.read()
        self.path = path
        self.text = text
        self.eol = b'\r\n' if b'\r\n' in text[:4096] else b'\n'
        self.lines = text.split(self.eol)
        self.records = []
        self.start = None
        self._parse()

    def _parse(self):
        upper = seg = 0
        for n, line in enumerate(self.lines):
            if not line:
                continue
            if line[:1] != b':':
                raise HexError(f'line {n + 1}: no colon')
            try:
                b = bytes.fromhex(line[1:].decode('ascii'))
            except ValueError as e:
                raise HexError(f'line {n + 1}: bad hex') from e
            if len(b) < 5 or len(b) != b[0] + 5:
                raise HexError(f'line {n + 1}: bad length')
            if sum(b) & 0xFF:
                raise HexError(f'line {n + 1}: bad checksum')
            cnt, off, typ, data = b[0], (b[1] << 8) | b[2], b[3], b[4:4 + b[0]]
            addr = None
            if typ == 0:
                addr = upper + seg + off
            elif typ == 2:
                seg, upper = ((data[0] << 8) | data[1]) << 4, 0
            elif typ == 4:
                upper, seg = ((data[0] << 8) | data[1]) << 16, 0
            elif typ == 5:
                self.start = struct.unpack('>I', data)[0]
            elif typ not in (1, 3):
                raise HexError(f'line {n + 1}: record type {typ}')
            self.records.append(Record(n, line, cnt, off, typ, bytes(data), addr))

    # ---- reading -------------------------------------------------------------------------
    def data_records(self):
        return [r for r in self.records if r.type == 0]

    def segments(self):
        """Contiguous images as [(base, bytes)] in file order of their first record."""
        recs = sorted(self.data_records(), key=lambda r: r.addr)
        segs = []
        for r in recs:
            if segs and segs[-1][0] + len(segs[-1][1]) == r.addr:
                segs[-1][1].extend(r.data)
            elif segs and r.addr < segs[-1][0] + len(segs[-1][1]):
                raise HexError(f'overlapping data at {r.addr:#x}')
            else:
                segs.append([r.addr, bytearray(r.data)])
        return [(a, bytes(b)) for a, b in segs]

    def image(self, base):
        for a, b in self.segments():
            if a == base:
                return b
        raise HexError(f'no image starting at {base:#x}')

    def read(self, addr, n):
        out = bytearray()
        segs = self.segments()
        for a, b in segs:
            if a <= addr and addr + n <= a + len(b):
                return bytes(b[addr - a:addr - a + n])
        raise HexError(f'{addr:#x}+{n} not covered by one segment')

    def sha256(self):
        return hashlib.sha256(self.text).hexdigest()

    # ---- patching ------------------------------------------------------------------------
    def patched(self, patches):
        """Return a new PPF with `patches` ({addr: bytes}) applied in place.

        Every patched byte must already exist in a data record. Only those records'
        data bytes and checksum characters change. Raises if anything is uncovered."""
        want = {}
        for a, data in patches.items():
            for i, v in enumerate(data):
                if a + i in want and want[a + i] != v:
                    raise HexError(f'conflicting patch bytes at {a + i:#x}')
                want[a + i] = v
        lines = list(self.lines)
        done = set()
        for r in self.data_records():
            hit = [i for i in range(r.count) if r.addr + i in want]
            if not hit:
                continue
            b = bytearray(bytes.fromhex(r.raw[1:].decode('ascii')))
            for i in hit:
                b[4 + i] = want[r.addr + i]
                done.add(r.addr + i)
            b[4 + r.count] = (-sum(b[:4 + r.count])) & 0xFF
            # keep the original letter case (Polyend files use upper case)
            s = b.hex()
            if r.raw[1:].decode('ascii').isupper() or not any(c.isalpha() for c in r.raw[1:].decode()):
                s = s.upper()
            lines[r.lineno] = b':' + s.encode('ascii')
        missing = sorted(set(want) - done)
        if missing:
            raise HexError('patch bytes not covered by any data record: ' +
                           ', '.join(f'{a:#x}' for a in missing[:8]) +
                           (' ...' if len(missing) > 8 else ''))
        return PPF(text=self.eol.join(lines))

    def appended(self, after, blobs, reclen=16):
        """Return a new PPF with new data records for `blobs` ({addr: bytes}) inserted right after
        the data record that ends at `after` (the last record of an image, before its EOF).

        Every original line stays byte-identical; only new lines are inserted. A block that crosses a
        64 KB boundary gets a type 04 address record there (as the stock files do), and the base in force
        at the anchor is restored after the new records, so the stock lines that follow keep their
        addresses."""
        recs = [r for r in self.data_records() if r.addr + r.count == after]
        if len(recs) != 1:
            raise HexError(f'no unique data record ending at {after:#x}')
        anchor = recs[0]
        base = anchor.addr - anchor.offset
        covered = set()
        for r in self.data_records():
            covered.update(range(r.addr, r.addr + r.count))
        new = []
        upper_hex = anchor.raw[1:].decode().upper() == anchor.raw[1:].decode()

        def rec(body):
            body += bytes([(-sum(body)) & 0xFF])
            h = body.hex().upper() if upper_hex else body.hex()
            return b':' + h.encode()

        def ela(b16):
            return rec(bytes([2, 0, 0, 4, b16 >> 8, b16 & 0xFF]))
        cur = base
        for a in sorted(blobs):
            data = blobs[a]
            if any(x in covered for x in (a, a + len(data) - 1)):
                raise HexError(f'appended block {a:#x} overlaps existing records')
            k = 0
            while k < len(data):
                ra = a + k
                n = min(reclen, 0x10000 - (ra & 0xFFFF), len(data) - k)
                if not cur <= ra < cur + 0x10000:
                    cur = ra & 0xFFFF0000
                    new.append(ela(cur >> 16))
                off = ra - cur
                new.append(rec(bytes([n, off >> 8, off & 0xFF, 0]) + data[k:k + n]))
                k += n
        if cur != base:
            if base & 0xFFFF:
                raise HexError(f'cannot restore base {base:#x} with a type 04 record')
            new.append(ela(base >> 16))
        lines = list(self.lines)
        lines[anchor.lineno + 1:anchor.lineno + 1] = new
        return PPF(text=self.eol.join(lines))

    def save(self, path):
        with open(path, 'wb') as f:
            f.write(self.text)


def diff(stock, patched):
    """Compare two PPFs. Returns (changed_bytes [(addr, old, new)], changed_lines [stock lineno],
    inserted [(patched lineno, Record)]). Every stock line must still be present, in order; a
    changed line may only differ in data bytes and checksum. Inserted lines must be data records."""
    rb = {r.lineno: r for r in patched.records}
    ra = {r.lineno: r for r in stock.records}
    changed_lines, inserted, bytes_ = [], [], []
    i = j = 0
    A, B = stock.lines, patched.lines
    while i < len(A):
        if j >= len(B):
            raise HexError('patched file is missing stock lines')
        if A[i] == B[j]:
            i += 1
            j += 1
            continue
        a, b = A[i], B[j]
        if len(a) == len(b) and a[:9] == b[:9] and i in ra and ra[i].type == 0:
            x, y = ra[i], rb[j]
            for k in range(x.count):
                if x.data[k] != y.data[k]:
                    bytes_.append((x.addr + k, x.data[k], y.data[k]))
            changed_lines.append(i)
            i += 1
            j += 1
            continue
        r = rb.get(j)
        if r is None or r.type not in (0, 4):
            raise HexError(f'patched line {j + 1}: not an in-place change or an inserted data / address record')
        if r.type == 0:
            inserted.append((j, r))
        j += 1
    for k in range(j, len(B)):
        r = rb.get(k)
        if B[k] and (r is None or r.type != 0):
            raise HexError(f'patched line {k + 1}: unexpected trailing line')
        if B[k]:
            inserted.append((k, r))
    return bytes_, changed_lines, inserted


def runs(changed):
    """Group (addr, old, new) into contiguous runs [(start, old_bytes, new_bytes)]."""
    out = []
    for a, o, n in sorted(changed):
        if out and out[-1][0] + len(out[-1][1]) == a:
            out[-1][1].append(o)
            out[-1][2].append(n)
        else:
            out.append([a, bytearray([o]), bytearray([n])])
    return [(a, bytes(o), bytes(n)) for a, o, n in out]


def _main(argv):
    if len(argv) >= 2 and argv[0] == 'info':
        p = PPF(argv[1])
        print(f'{argv[1]}: {len(p.records)} records, sha256 {p.sha256()}, start {p.start:#x}')
        for a, b in p.segments():
            print(f'  image {a:#010x}..{a + len(b):#010x} ({len(b)} bytes)')
        return 0
    if len(argv) == 3 and argv[0] == 'diff':
        a, b = PPF(argv[1]), PPF(argv[2])
        ch, lines, ins = diff(a, b)
        print(f'{len(ch)} bytes changed in {len(lines)} records, {len(ins)} records inserted')
        for s, o, n in runs(ch):
            print(f'  {s:#010x} +{len(o):<5} {o[:16].hex()}{"..." if len(o) > 16 else ""} -> '
                  f'{n[:16].hex()}{"..." if len(n) > 16 else ""}')
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
