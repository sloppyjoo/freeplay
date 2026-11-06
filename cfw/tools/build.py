#!/usr/bin/env python3
"""Build a CFW .ppf: compile patch C, link into the code cave, patch hook words, write report.

usage: build.py cfw/targets/<target>/target.json [--out DIR] [--no-verify]

Steps per image in target.json:
  1. check the input .ppf SHA-256 (refuse unknown firmware)
  2. clang -c each source (flags: the usual ones + -ffunction-sections), link with link.py at
     the cave start, cross-check against ld.lld when it is installed
  3. check the cave holds only the expected fill byte and every hook word holds `expect`
  4. patch the cave bytes and hook words in place (ppf.PPF.patched) and save the output
  5. write <output>.md: SHA-256s, every changed byte run, hooks, symbols, relocations
  6. run verify.py on the result (disassembly + relocation re-check + Unicorn emulation)
"""
import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CFW = os.path.dirname(HERE)
TOP = os.path.dirname(CFW)
sys.path.insert(0, HERE)
from link import Linker, load_symbols, decode_bl, encode_bl  # noqa: E402
from ppf import PPF, diff, runs  # noqa: E402

CFLAGS = ['-mthumb', '-Oz', '-ffreestanding', '-fno-builtin', '-fno-pic', '-fno-exceptions',
          '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-ffunction-sections',
          '-fdata-sections', '-fno-common', '-fshort-enums', '-std=c11', '-Wall', '-Wextra',
          '-Werror']


def h(x):
    return int(x, 0) if isinstance(x, str) else x


def compile_image(tdir, img, bdir, key='sources'):
    cc = os.environ.get('CC', 'clang')
    objs = []
    inc = [f'-I{os.path.join(CFW, "src")}'] + [f'-I{os.path.normpath(os.path.join(tdir, d))}'
                                               for d in img.get('include', ['.'])]
    for src in img.get(key, []):
        sp = os.path.normpath(os.path.join(tdir, src))
        op = os.path.join(bdir, f'{img["name"]}_{os.path.splitext(os.path.basename(sp))[0]}.o')
        cmd = [cc] + img['cpu'] + CFLAGS + inc + ['-c', sp, '-o', op]
        subprocess.run(cmd, check=True)
        objs.append(op)
    return objs


def lld_crosscheck(base, syms, objs, image, fixed=None):
    """Link the same objects with ld.lld -O0 and compare the main blob and every fixed section."""
    lld, objcopy = shutil.which('ld.lld'), shutil.which('llvm-objcopy')
    if not lld or not objcopy:
        return 'skipped (ld.lld not installed)'
    fixed = fixed or {}
    ld = os.path.join(os.path.dirname(objs[0]), 'crosscheck.ld')
    with open(ld, 'w') as f:
        for n, v in syms.items():
            f.write(f'{n} = {v:#x};\n')
        f.write('SECTIONS {\n')
        for k, (name, (addr, _)) in enumerate(sorted(fixed.items())):
            f.write(f'  .fixed{k} {addr:#x} : {{ *({name}) }}\n')
        f.write(f'  . = {base:#x}; .text : {{ *(.text .text.*) }} .rodata : {{ *(.rodata .rodata.*) }}\n'
                f'  /DISCARD/ : {{ *(.ARM.exidx*) *(.comment) }} }}\n')
    elf, binp = ld[:-3] + '.elf', ld[:-3] + '.bin'
    subprocess.run([lld, '-O0', '-T', ld, '-e', '0', '-o', elf] + objs, check=True)
    subprocess.run([objcopy, '-O', 'binary', '--only-section=.text', '--only-section=.rodata', elf, binp],
                   check=True)
    theirs = open(binp, 'rb').read()
    if theirs != image:
        raise SystemExit(f'link.py output differs from ld.lld ({len(image)} vs {len(theirs)} bytes)')
    return f'identical to ld.lld ({len(image)} bytes)'


def lld_crosscheck_fixed(objs, lk):
    lld, objcopy = shutil.which('ld.lld'), shutil.which('llvm-objcopy')
    if not lld or not lk.fixed_out:
        return ''
    ld = os.path.join(os.path.dirname(objs[0]), 'crosscheck.ld')
    elf = ld[:-3] + '.elf'
    out = []
    for k, (name, (addr, blob)) in enumerate(sorted(lk.fixed_out.items())):
        binp = ld[:-3] + f'.fixed{k}.bin'
        subprocess.run([objcopy, '-O', 'binary', f'--only-section=.fixed{k}', elf, binp], check=True)
        if open(binp, 'rb').read() != blob:
            raise SystemExit(f'fixed section {name} differs from ld.lld')
        out.append(f'{name} identical')
    return '; ' + ', '.join(out)


def build(target_json, out_dir=None, verify=True):
    tdir = os.path.dirname(os.path.abspath(target_json))
    t = json.load(open(target_json))
    out_dir = out_dir or os.path.join(CFW, 'build', t['name'])
    os.makedirs(out_dir, exist_ok=True)
    name = t['output']
    if len(name) > 24 or not name.startswith(t.get('update_prefix', '')) or not name.endswith('.ppf'):
        raise SystemExit(f'output name {name!r}: must start with {t.get("update_prefix")!r}, end in .ppf and be '
                         '<= 24 characters (the longest name proven to flash)')
    inp = os.path.join(TOP, t['input'])
    stock = PPF(inp)
    if stock.sha256() != t['input_sha256']:
        raise SystemExit(f'{inp}: SHA-256 {stock.sha256()} is not the expected stock file')

    patches = {}
    report_imgs = []
    appends = []   # (after, {addr: bytes})
    for img in t['images']:
        mode = 'append' if 'append' in img else 'cave'
        area = img['append'] if mode == 'append' else {'at': img['cave'][0], 'limit': img['cave'][1]}
        base, limit = h(area['at']), h(area['limit'])
        syms = {}
        for s in img['symbols']:
            syms.update(load_symbols(os.path.join(tdir, s)))
        objs = compile_image(tdir, img, out_dir)
        fixed = {fx['section']: (h(fx['addr']), h(fx['limit'])) for fx in img.get('fixed', [])}
        # "ext": whole sources linked past the image end as new HEX records (with a header the
        # cave code checks before using any of it: magic, length, sum of the words after it)
        ext = img.get('ext')
        ext_objs = compile_image(tdir, dict(img, sources=ext['sources'], name=img['name'] + '_ext'), out_dir) if ext else []
        lk = Linker(base, syms, limit, fixed,
                    (h(ext['at']), h(ext['limit']), set(ext_objs)) if ext else None)
        for o in objs + ext_objs:
            lk.add(o)
        code = lk.link()
        if ext:
            ea, eb = lk.ext_out
            if struct.unpack_from('<I', eb, 0)[0] != h(ext['magic']):
                raise SystemExit(f'ext region {ea:#x} does not start with the header (section .ext_hdr)')
            struct.pack_into('<I', eb, 4, len(eb))
            struct.pack_into('<I', eb, 8, sum(struct.unpack_from(f'<{len(eb) // 4 - 3}I', eb, 12)) & 0xffffffff)
            appends.append((h(ext['after']), {ea: bytes(eb)}))
            objs = objs + ext_objs
        missing = set(fixed) - set(lk.fixed_out)
        if missing:
            raise SystemExit(f'fixed sections not produced by the sources: {missing}')
        xcheck = lld_crosscheck(base, syms, objs, code, fixed) + lld_crosscheck_fixed(objs, lk)
        if mode == 'cave':
            # cave must hold only the fill byte in the stock image
            fill = h(img.get('cave_fill', '0'))
            cur = stock.read(base, len(code))
            if any(b != fill for b in cur):
                raise SystemExit(f'cave {base:#x}+{len(code)} is not all {fill:#04x} in the stock image')
            patches[base] = code
        else:
            appends.append((h(area['after']), {base: code}))
        for fx in img.get('fixed', []):
            fa, blob = lk.fixed_out[fx['section']]
            fill = h(fx.get('fill', '0xff'))
            if any(b != fill for b in stock.read(fa, len(blob))):
                raise SystemExit(f'fixed area {fa:#x}+{len(blob)} is not all {fill:#04x} in the stock image')
            patches[fa] = blob
        hooks = []
        for hk in img['hooks']:
            a = h(hk['addr'])
            if hk.get('kind') == 'bl':
                # code hook: retarget one stock `bl` (expect = its stock callee) to a CFW function
                buf = bytearray(stock.read(a, 4))
                if (buf[1] & 0xF8) != 0xF0 or (buf[3] & 0xD0) != 0xD0:
                    raise SystemExit(f'hook {hk["what"]} at {a:#x}: not a BL instruction')
                old = a + 4 + decode_bl(buf, 0)
                if old != h(hk['expect']) & ~1:
                    raise SystemExit(f'hook {hk["what"]} at {a:#x}: calls {old:#x}, expected {hk["expect"]}')
                new = lk.symbols[hk['symbol']]
                encode_bl(buf, 0, (new & ~1) - (a + 4), True)
                patches[a] = bytes(buf)
                hooks.append(dict(hk, old=old, new=new))
                continue
            old = struct.unpack('<I', stock.read(a, 4))[0]
            if old != h(hk['expect']):
                raise SystemExit(f'hook {hk["what"]} at {a:#x}: found {old:#x}, expected {hk["expect"]}')
            new = lk.symbols[hk['symbol']]
            patches[a] = struct.pack('<I', new)
            hooks.append(dict(hk, old=old, new=new))
        for ep in img.get('entry_points', []):
            if not lk.symbols[ep] & 1:
                raise SystemExit(f'entry point {ep} is not a Thumb function')
        with open(os.path.join(out_dir, f'{img["name"]}.bin'), 'wb') as f:
            f.write(code)
        mapname = 'link.map' if len(t['images']) == 1 else f'link_{img["name"]}.map'
        with open(os.path.join(out_dir, mapname), 'w') as f:
            for n, v in sorted(lk.symbols.items(), key=lambda kv: kv[1]):
                f.write(f'{v:#010x} {n}\n')
            for addr, size, path, name in lk.sections:
                f.write(f'section {addr:#010x} {size:#x} {name}\n')
            for e in lk.log:
                f.write(f'reloc {e["P"]:#010x} {e["type"]} {e["sym"]} {e["S"]:#x} {e["value"]:#x}\n')
        report_imgs.append(dict(img=img, base=base, limit=limit, code=code, lk=lk, hooks=hooks,
                                xcheck=xcheck, objs=objs, mode=mode))

    out = stock.patched(patches)
    for after, blobs in appends:
        out = out.appended(after, blobs)
    out_path = os.path.join(out_dir, t['output'])
    out.save(out_path)
    # the patch package for the public patcher: only CFW bytes (hook words, our code, new records),
    # no Polyend data; the patcher replays patched() / appended() on the user's own stock file
    pkg = dict(target=t['name'], device=t.get('device', ''), input=t['input'], input_sha256=stock.sha256(),
               output=t['output'], output_sha256=out.sha256(),
               patches={f'{a:#x}': bytes(b).hex() for a, b in sorted(patches.items())},
               appends=[dict(after=f'{after:#x}', blobs={f'{a:#x}': bytes(b).hex() for a, b in sorted(blobs.items())})
                        for after, blobs in appends])
    with open(out_path[:-4] + '.patch.json', 'w') as f:
        json.dump(pkg, f, indent=1)
    changed, lines, inserted = diff(stock, out)
    md = write_report(t, stock, out, out_path, changed, lines, inserted, report_imgs)
    print(f'wrote {out_path} ({len(changed)} bytes changed in {len(lines)} records, '
          f'{len(inserted)} records inserted)')
    print(f'wrote {md}')
    if verify:
        import verify as V
        ok = V.verify_build(target_json, out_path, report_imgs)
        if not ok:
            raise SystemExit('verification FAILED')
    return out_path


def write_report(t, stock, out, out_path, changed, lines, inserted, imgs):
    md = out_path[:-4] + '.md'
    allowed = []
    for r in imgs:
        allowed.append((r['base'], r['base'] + len(r['code'])))
        if r['lk'].ext_out:
            allowed.append((r['lk'].ext_out[0], r['lk'].ext_out[0] + len(r['lk'].ext_out[1])))
        for a, blob in r['lk'].fixed_out.values():
            allowed.append((a, a + len(blob)))
        for hk in r['hooks']:
            a = h(hk['addr'])
            allowed.append((a, a + 4))
    unexpected = [a for a, o, n in changed if not any(lo <= a < hi for lo, hi in allowed)]
    with open(md, 'w') as f:
        f.write(f'# {t["output"]}: byte-level diff summary\n\n')
        f.write(f'| | |\n|---|---|\n')
        f.write(f'| Device | {t["device"]} |\n')
        f.write(f'| Input | `{t["input"]}` SHA-256 `{stock.sha256()}` |\n')
        f.write(f'| Output | `{t["output"]}` SHA-256 `{out.sha256()}` |\n')
        f.write(f'| File size | {len(stock.text)} -> {len(out.text)} bytes |\n')
        f.write(f'| Changed | {len(changed)} data bytes in {len(lines)} existing HEX records (data and checksum '
                f'only) |\n')
        f.write(f'| Inserted | {len(inserted)} new data records' +
                (f' at `{inserted[0][1].addr:#x}..{inserted[-1][1].addr + inserted[-1][1].count:#x}`, file lines '
                 f'{inserted[0][0] + 1}..{inserted[-1][0] + 1}' if inserted else '') + '; all other lines identical |\n')
        f.write(f'| Bytes outside cave+hooks | {len(unexpected)} |\n\n')
        for r in imgs:
            img = r['img']
            f.write(f'## Image {img["name"]}\n\n')
            if r['mode'] == 'cave':
                f.write(f'- Code cave `{r["base"]:#x}..{r["base"] + len(r["code"]):#x}` ({len(r["code"])} bytes '
                        f'of {r["limit"] - r["base"]} available); stock content was all '
                        f'`{img.get("cave_fill", "0x00")}`\n')
            else:
                f.write(f'- Code in NEW records `{r["base"]:#x}..{r["base"] + len(r["code"]):#x}` ({len(r["code"])} '
                        f'bytes), inserted after the image end `{img["append"]["after"]}`\n')
            for name, (a, blob) in r['lk'].fixed_out.items():
                f.write(f'- In-place `{name}` at `{a:#x}..{a + len(blob):#x}` ({len(blob)} bytes), stock bytes '
                        f'were all padding\n')
            f.write(f'- Link: {len(r["lk"].log)} relocations, {r["xcheck"]}\n')
            if img.get('ram'):
                f.write(f'- Scratch RAM `{img["ram"][0]}..{img["ram"][1]}` (unreferenced DTCM gap)\n')
            f.write('\n### Hooks (one 32-bit literal each; `bl` hooks: one retargeted call)\n\n| Address | What | Stock | CFW |\n'
                    '|---|---|---|---|\n')
            for hk in r['hooks']:
                f.write(f'| `{h(hk["addr"]):#x}` | {hk["what"]} | `{hk["old"]:#010x}` | '
                        f'`{hk["new"]:#010x}` = `{hk["symbol"]}` |\n')
            f.write('\n### Symbols\n\n| Address | Name |\n|---|---|\n')
            for n, v in sorted(r['lk'].symbols.items(), key=lambda kv: kv[1]):
                f.write(f'| `{v:#010x}` | `{n}` |\n')
            f.write('\n### Relocations\n\n| Place | Type | Symbol | Value |\n|---|---|---|---|\n')
            for e in r['lk'].log:
                f.write(f'| `{e["P"]:#x}` | {e["type"]} | `{e["sym"]}` = `{e["S"]:#x}` | `{e["value"]:#x}` |\n')
        f.write('\n## Changed byte runs\n\n| Start | Length | Stock (first 16) | CFW (first 16) |\n'
                '|---|---|---|---|\n')
        for s, o, n in runs(changed):
            f.write(f'| `{s:#x}` | {len(o)} | `{o[:16].hex()}` | `{n[:16].hex()}` |\n')
        f.write('\n## Recovery\n\n')
        f.write(RECOVERY.format(stock=os.path.basename(t['input'])))
        f.write(f'- {t["recovery_note"]}\n' if t.get('recovery_note') else '')
    if unexpected:
        raise SystemExit(f'{len(unexpected)} changed bytes outside the cave and hooks')
    return md


RECOVERY = """\
- Keep the stock `{stock}` in the SD card's `/Firmware` folder next to the CFW file.
- To go back: System Settings > Firmware > Update Firmware and pick the stock file.
- If the device does not boot to the UI: power off, press the hidden reset button on the back; the
  bootloader re-flashes from `/Firmware` (keep only the stock `.ppf` there for this).
"""


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('target')
    ap.add_argument('--out')
    ap.add_argument('--no-verify', action='store_true')
    a = ap.parse_args()
    build(a.target, a.out, not a.no_verify)
