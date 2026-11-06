#!/usr/bin/env python3
"""Structural read of a Polyend .ppf (Intel HEX) firmware file.

usage: ppf_inspect.py <file.ppf> [--dump-dir DIR]

Reports: record/line statistics, checksum validity, address segments, start address,
per-image vector table + startup copy/zero tables, peripheral fingerprints (MCU family),
toolchain/build strings, version strings, USB identity strings, and subsystem markers.
With --dump-dir, writes each contiguous image as <dir>/image_<base>.bin.
"""
import sys, struct, re, collections, os

def parse_hex(path):
    raw = open(path, 'rb').read()
    crlf = raw.count(b'\r\n'); lf = raw.count(b'\n')
    recs = []; bad = 0; types = collections.Counter(); lens = collections.Counter()
    upper = 0; seg = 0; start = None; eof = 0
    mem = {}
    for n, line in enumerate(raw.splitlines()):
        if not line: continue
        if line[:1] != b':': raise ValueError(f'line {n+1}: no colon')
        b = bytes.fromhex(line[1:].decode())
        cnt, addr, typ = b[0], (b[1] << 8) | b[2], b[3]
        data = b[4:4 + cnt]
        if (sum(b) & 0xFF) != 0: bad += 1
        types[typ] += 1
        if typ == 0:
            lens[cnt] += 1
            a = upper + seg + addr
            mem[a] = data
        elif typ == 1: eof += 1
        elif typ == 2: seg = ((data[0] << 8) | data[1]) << 4; upper = 0
        elif typ == 4: upper = ((data[0] << 8) | data[1]) << 16; seg = 0
        elif typ == 5: start = struct.unpack('>I', data)[0]
        elif typ == 3: start = ('CS:IP', data.hex())
        recs.append(typ)
    return dict(size=len(raw), crlf=crlf, lf=lf, lines=len(recs), bad=bad, types=types,
                lens=lens, start=start, eof=eof, mem=mem)

def segments(mem):
    segs = []
    for a in sorted(mem):
        d = mem[a]
        if segs and segs[-1][0] + len(segs[-1][1]) == a:
            segs[-1][1].extend(d)
        else:
            segs.append([a, bytearray(d)])
    return [(a, bytes(b)) for a, b in segs]

PERIPH = {  # base -> name, used to fingerprint MCU family
    # i.MX RT10xx
    0x400FC000: 'RT:CCM', 0x401F8000: 'RT:IOMUXC', 0x400AC000: 'RT:IOMUXC_GPR', 0x402F0000: 'RT:SEMC',
    0x402A8000: 'RT:FlexSPI', 0x402C0000: 'RT:USDHC1', 0x402C4000: 'RT:USDHC2', 0x40384000: 'RT:SAI1',
    0x40388000: 'RT:SAI2', 0x4038C000: 'RT:SAI3', 0x40394000: 'RT:LPSPI1', 0x40398000: 'RT:LPSPI2',
    0x4039C000: 'RT:LPSPI3', 0x403A0000: 'RT:LPSPI4', 0x403F0000: 'RT:LPI2C1', 0x403F4000: 'RT:LPI2C2',
    0x403F8000: 'RT:LPI2C3', 0x403FC000: 'RT:LPI2C4', 0x402E0000: 'RT:USB1(OTG)', 0x402E0200: 'RT:USB2',
    0x401BC000: 'RT:GPIO2', 0x401B8000: 'RT:GPIO1', 0x401C0000: 'RT:GPIO3', 0x401C4000: 'RT:GPIO4',
    0x42000000: 'RT:GPIO6(fast)', 0x400E8000: 'RT:DMA0', 0x400EC000: 'RT:DMAMUX', 0x402B8000: 'RT:eLCDIF',
    0x40184000: 'RT:LPUART1', 0x400D8000: 'RT:CCM_ANALOG', 0x400B0000: 'RT:SRC?', 0x40080000: 'RT:ADC?',
    # Kinetis K66
    0x40048000: 'K:SIM', 0x40049000: 'K:PORTA', 0x4004A000: 'K:PORTB', 0x40064000: 'K:MCG',
    0x400B1000: 'K:SDHC', 0x4002F000: 'K:I2S0', 0x4002C000: 'K:SPI0', 0x4002D000: 'K:SPI1',
    0x40020000: 'K:FTFE', 0x40072000: 'K:USB0', 0x400FF000: 'K:GPIO',
}

def u32(b, o): return struct.unpack_from('<I', b, o)[0]

def strings(b, base, minlen=6):
    for m in re.finditer(rb'[\x20-\x7e]{%d,}' % minlen, b):
        yield base + m.start(), m.group().decode()

def vector(base, img):
    sp, rst = u32(img, 0), u32(img, 4)
    return sp, rst

def rt_copy_table(base, img):
    """Teensy4-style startup: find (src,dst,len) triples near the reset handler: look for the
    literal-pool pair (table_start, table_end) in the first 0x400 bytes after vectors."""
    out = []
    for off in range(0x200, min(len(img), 0x600), 4):
        a, b = u32(img, off), u32(img, off + 4)
        if base <= a < b < base + 0x400 and (b - a) % 12 == 0 and b - a <= 96:
            for t in range(a, b, 12):
                o = t - base
                out.append(struct.unpack_from('<III', img, o))
            return (a, b, out)
    return None

def fingerprint(base, img):
    c = collections.Counter()
    for off in range(0, len(img) - 3, 4):
        v = u32(img, off)
        pb = v & ~0x3FFF
        if pb in PERIPH: c[PERIPH[pb]] += 1
        elif (v & ~0x1FF) in PERIPH: c[PERIPH[v & ~0x1FF]] += 1
    return c

MARKERS = [
    ('toolchain', rb'GCC: \([^)]*\) [0-9.]+[^\x00]*|GNU [A-Za-z ]+Toolchain[^\x00]*'),
    ('copyright', rb'[Cc]opyright[^\x00]{0,60}'),
    ('teensy', rb'Teensy[^\x00]{0,40}|Teensyduino[^\x00]{0,40}|PJRC[^\x00]{0,40}'),
    ('fatfs/sdfat', rb'SdFat[^\x00]{0,30}|FatFs[^\x00]{0,30}|exFAT[^\x00]{0,20}'),
    ('nanopb', rb'invalid wire_type|invalid field descriptor|end-of-stream'),
    ('libstdc++', rb'basic_string::[^\x00]{0,30}|vector::[^\x00]{0,30}'),
    ('fw-update', rb'/Firmware[^\x00]{0,30}|new firmware[^\x00]{0,20}|Update Firmware'),
    ('usb', rb'USB [A-Za-z ]{3,30}|PLAY STORAGE|POLYEND[^\x00]{0,20}'),
    ('debug-flags', rb'/\.debug|/\.develop|/Test/[^\x00]{0,30}'),
    ('version', rb'(?<![0-9.])[0-9]\.[0-9]{1,2}\.[0-9A-Z]{1,2}(?![0-9.])'),
]

def report(path, dump_dir=None):
    h = parse_hex(path)
    print(f'# {os.path.basename(path)}')
    print(f'size {h["size"]} B, {h["lines"]} records, line endings: CRLF={h["crlf"]} LF={h["lf"]}')
    print(f'record types: {dict(h["types"])}  (0=data 1=EOF 2=ext-seg 4=ext-linear 5=start-linear)')
    print(f'data record lengths: {dict(h["lens"].most_common(6))}')
    print(f'bad checksums: {h["bad"]}   EOF records: {h["eof"]}   start address: '
          f'{h["start"]:#010x}' if isinstance(h['start'], int) else f'start: {h["start"]}')
    segs = segments(h['mem'])
    print(f'contiguous segments: {len(segs)}')
    for base, img in segs:
        print(f'\n## image @ {base:#010x} .. {base+len(img):#010x}  ({len(img)} B = {len(img)/1024:.1f} KiB)')
        if dump_dir:
            os.makedirs(dump_dir, exist_ok=True)
            open(os.path.join(dump_dir, f'image_{base:08x}.bin'), 'wb').write(img)
        sp, rst = vector(base, img)
        print(f'vector: initial SP {sp:#010x}, reset {rst:#010x} ({"thumb" if rst & 1 else "ARM?"})')
        nirq = 0
        for off in range(8, 0x400, 4):
            v = u32(img, off)
            if v and base <= (v & ~1) < base + len(img) and v & 1: nirq += 1
        print(f'vector entries pointing into image: {nirq}')
        fp = fingerprint(base, img)
        fam = 'i.MX RT10xx (Cortex-M7)' if sum(v for k, v in fp.items() if k.startswith('RT:')) > \
              sum(v for k, v in fp.items() if k.startswith('K:')) else 'Kinetis K6x (Cortex-M4F)'
        print(f'MCU fingerprint: {fam}; top literal refs: ' +
              ', '.join(f'{k}×{v}' for k, v in fp.most_common(14)))
        ct = rt_copy_table(base, img)
        if ct:
            a, b, rows = ct
            print(f'startup table {a:#x}..{b:#x}:')
            for s, d, n in rows:
                print(f'   src {s:#010x} -> dst {d:#010x} len {n:#x}' if s else f'   (zero/none) dst {d:#010x} len {n:#x}')
        found = collections.defaultdict(list)
        for name, rx in MARKERS:
            for m in re.finditer(rx, img):
                s = m.group().decode('latin1').strip()
                if s not in found[name]: found[name].append(s)
        for name, vals in found.items():
            vals = vals[:12] if name != 'version' else sorted(set(vals))[:20]
            print(f'{name}: {vals}')
        allstr = list(strings(img, base))
        print(f'printable strings (>=6): {len(allstr)}')
    return h, segs

if __name__ == '__main__':
    dd = None
    args = sys.argv[1:]
    if '--dump-dir' in args:
        i = args.index('--dump-dir'); dd = args[i + 1]; del args[i:i + 2]
    for p in args:
        report(p, dd); print()
