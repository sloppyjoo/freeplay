#!/usr/bin/env python3
"""Randomize synth patch on the virtual Play+, real firmware end to end.

Menu > Synths > (synth 1) > Randomize > Subtle / Medium / Wild / Undo, driven through the firmware's
input listener. The patch's edit-mapped parameter values are read before and after each action
through the patch's own vtable. Checks: the new submenu sits after the stock items, Subtle and
Medium only nudge continuous parameters (within +-8 % / +-25 %), Wild also changes enums but keeps
cutoff / attack / release in range, volume / tune / glide / oversampling never change, the slot
is marked modified, the toasts, and Undo restores the exact values from before the last randomize
(then "Nothing to undo").

usage: vdev_synthrand.py <patched.ppf> [--snap BOOT.snap] [--out DIR]   (exit status 0 = all pass)
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))
sys.path.insert(0, HERE)
from vdev_slice import Device, boot_snapshot  # noqa: E402

SYNTHMGR = 0x20018c18 + 0x270c   # Play+ only (the original Play has no synth engines)
SLOT_SIZE, SLOT_PATCH, SLOT_MODIFIED = 0x438, 0x0c, 0x435
PARAM_SIZE = 0x38
NONE = 0x7fedcba9
OUT = 0x200027F0
SKIP = ('Volume', 'Gain', 'Tune', 'Glide', 'Oversampling')
STOCK_ITEMS = 10          # Model .. Init patch
ITEMS = ['Subtle', 'Medium', 'Wild', 'Undo']


class Synth:
    def __init__(self, d, slot=0):
        self.vp, self.slot = d.vp, slot

    def patch(self):
        return self.vp.r32(SYNTHMGR + self.slot * SLOT_SIZE + SLOT_PATCH)

    def modified(self):
        return self.vp.mu.mem_read(SYNTHMGR + self.slot * SLOT_SIZE + SLOT_MODIFIED, 1)[0]

    def clear_modified(self):
        self.vp.mu.mem_write(SYNTHMGR + self.slot * SLOT_SIZE + SLOT_MODIFIED, b'\0')

    def params(self):
        """[(name, is_enum, min == max, raw value bits)] through Patch::getParamValueEditMapped."""
        vp = self.vp
        p = self.patch()
        vt = vp.r32(p)
        get = vp.r32(vt + 0x04)
        meta = p + 0x54
        pb, pe = vp.r32(meta), vp.r32(meta + 4)
        out = []
        for i in range((pe - pb) // PARAM_SIZE):
            e = pb + i * PARAM_SIZE
            sp, sl = vp.r32(e), vp.r32(e + 4)
            name = bytes(vp.mu.mem_read(sp, sl)).decode('latin1') if 0 < sl < 64 else ''
            vp.w32(OUT, NONE)
            vp.call_fn(get, OUT, p, i)
            raw = vp.r32(OUT)
            same = vp.mu.mem_read(e + 0x1c, 2) == vp.mu.mem_read(e + 0x1e, 2)
            out.append((name, vp.mu.mem_read(e + 0x22, 1)[0] != 0, same, raw))
        return out


def f(raw):
    return struct.unpack('<f', struct.pack('<I', raw))[0]


def skipped(name, same):
    return not name or same or any(k in name for k in SKIP)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    ap.add_argument('--snap')
    ap.add_argument('--out')
    a = ap.parse_args()
    work = os.environ.setdefault('VPLAY_DIR', os.path.join('/tmp', 'vplay-' + str(os.getuid())))
    os.makedirs(work, exist_ok=True)
    snap = a.snap or os.path.join(work, os.path.basename(a.ppf) + '.boot.snap')
    if not os.path.exists(snap):
        boot_snapshot(a.ppf, snap)
    out = a.out or os.path.join(work, 'shots')
    os.makedirs(out, exist_ok=True)
    results = []

    def check(name, ok, detail=''):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f': {detail}' if detail and not ok else ''))

    import flows
    d = Device(a.ppf, snap)
    s = d.s
    syn = Synth(d, 0)
    s.run_ms(200)
    flows.open_menu(s)
    flows.enter_item(s, 4)                  # Synths (under File, Samples, LFO, Matrix)
    flows.enter_item(s, 0)                  # synth 1
    t = d.texts()
    check('synth submenu has Randomize after the stock items', 'Init patch' in t or 'Model' in t, str(t[-14:]))
    flows.enter_item(s, STOCK_ITEMS)        # Randomize
    t = d.texts()
    check('Randomize submenu: Subtle / Medium / Wild / Undo', all(k in t for k in ITEMS), str(t[-10:]))
    s.shot(os.path.join(out, 'rand_menu.png'))

    p0 = syn.params()
    check('patch has parameters', len(p0) > 10, str(len(p0)))
    syn.clear_modified()

    def press(item):
        d.vp.eve.cmd_log.clear()
        flows.enter_item(s, ITEMS.index(item), after_ms=300)
        return d.texts()

    def compare(before, after, label, amount):
        bad, moved = [], 0
        for (n, en, same, r0), (_, _, _, r1) in zip(before, after):
            if skipped(n, same) or r0 == NONE:
                if r1 != r0:
                    bad.append(f'{n}: skipped param changed')
                continue
            if r1 != r0:
                moved += 1
            if amount is not None:
                if en and r1 != r0:
                    bad.append(f'{n}: enum changed')
                elif abs(f(r1) - f(r0)) > amount + 1e-4:
                    bad.append(f'{n}: {f(r0):.3f} -> {f(r1):.3f}')
            if not 0.0 <= f(r1) <= 1.0:
                bad.append(f'{n}: out of 0..1 ({f(r1)})')
        check(f'{label}: only allowed changes', not bad, '; '.join(bad[:5]))
        check(f'{label}: something changed', moved > 0, str(moved))
        return moved

    t = press('Subtle')
    p1 = syn.params()
    compare(p0, p1, 'Subtle', 0.08)
    check('Subtle: toast', any(x.startswith('Subtle: ') and x.endswith(' params changed') for x in t), str(t[-4:]))
    check('Subtle: slot marked modified', syn.modified() == 1)

    t = press('Medium')
    p2 = syn.params()
    compare(p1, p2, 'Medium', 0.25)

    t = press('Wild')
    p3 = syn.params()
    moved = compare(p2, p3, 'Wild', None)
    enums = sum(1 for (n, en, same, r0), (_, _, _, r1) in zip(p2, p3) if en and not skipped(n, same) and r0 != r1)
    check('Wild: enums change too', enums > 0, str(enums))
    rng = []
    for (n, en, same, r1) in p3:
        if skipped(n, same):
            continue
        v = f(r1)
        if 'Cutoff' in n and v < 0.3 - 1e-4 or 'Attack' in n and v > 0.45 + 1e-4 or 'Release' in n and v > 0.6 + 1e-4:
            rng.append(f'{n}={v:.3f}')
    check('Wild: cutoff / attack / release kept playable', not rng, ', '.join(rng[:5]))
    s.shot(os.path.join(out, 'rand_wild.png'))

    t = press('Undo')
    p4 = syn.params()
    diff = [n for (n, _, _, r2), (_, _, _, r4) in zip(p2, p4) if r2 != r4]
    check('Undo: exact values from before Wild', not diff, ', '.join(diff[:6]))
    check('Undo: toast', 'Randomize undone' in t, str(t[-4:]))
    t = press('Undo')
    check('Undo twice: "Nothing to undo"', 'Nothing to undo' in t, str(t[-4:]))
    check('menu still open after the actions', flows.current_screen(s) == 'menu')

    # the other two synths run other engines: Wild + exact Undo on each
    for slot in (1, 2):
        syn = Synth(d, slot)
        s.tap(7, after_ms=400)                  # Randomize -> synth
        s.tap(7, after_ms=400)                  # synth -> Synths
        flows.enter_item(s, slot)
        flows.enter_item(s, STOCK_ITEMS)
        before = syn.params()
        press('Wild')
        after = syn.params()
        bad = [n for (n, en, same, r0), (_, _, _, r1) in zip(before, after) if skipped(n, same) and r0 != r1]
        moved = sum(1 for a0, a1 in zip(before, after) if a0[3] != a1[3])
        check(f'synth {slot + 1}: Wild changes {moved} params, none of the protected ones', moved > 0 and not bad,
              ', '.join(bad[:5]))
        press('Undo')
        back = syn.params()
        diff = [n for (n, _, _, r0), (_, _, _, r2) in zip(before, back) if r0 != r2]
        check(f'synth {slot + 1}: Undo exact', not diff, ', '.join(diff[:6]))

    print(f'{sum(results)}/{len(results)} passed; screenshots in {out}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
