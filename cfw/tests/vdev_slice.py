#!/usr/bin/env python3
"""Slice CFW on the virtual Play+ (cfw/vdev): the whole firmware, driven through its own input path.

Boots the patched image (or restores a cached boot snapshot), then plays the user workflow with
pad/button/encoder events delivered to the firmware's input listener:
  1. tap 16 step pads on track 1 (trigs), hold them all, press Menu,
  2. Samples > Slice > x16, release the pads,
  3. check the 16 steps' Sample Start/End, the toast text and that Menu opened with pads held;
plus "= Steps" on 4 held steps and the empty-selection toast. Screenshots go to --out.

usage: vdev_slice.py <patched.ppf> [--snap BOOT.snap] [--out DIR]   (exit status 0 = all pass)
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))

# Addresses come from the virtual device's profile (cfw/vdev/hle.py): app object, UI offsets, the
# CFW scratch RAM. HW controller = app + 0x38, selection = controller + 0x2c, input listener = app +
# 0x1c (+8 = UI manager); cfw_state: layout, alive_ms, magic, count, bits[16][2]; OUT_WORD: getParam out word.
BTN_MENU, BTN_ENTER = 6, 0
BTN_CLICK = 6   # encoder click: runs the highlighted Slice count (keys 0 / 3 are Points / To Folder there)
BOOT_MS = 23000


class Device:
    def __init__(self, ppf, snap):
        from session import Session
        self.s = Session(snap, ppf=ppf)
        self.vp = self.s.vp
        prof = self.vp.profile
        self.ctrl = prof['app'] + 0x38
        self.sel = self.ctrl + 0x2c
        self.listener = prof['app'] + 0x1c
        self.cfw_ram = prof['cfw_ram']
        self.out_word = prof['cfw_ram'] - 0x10   # unused DTCM gap just below cfw_ram (the struct fills it)
        self.ui_cur = prof['ui_cur']

    def vfn(self, obj, off):
        return self.vp.r32(self.vp.r32(obj) + off)

    def step(self, t, i):
        vp = self.vp
        pv = vp.r32(self.sel + 0x5c)
        trk = vp.call_fn(self.vfn(pv, 0), pv, t)
        return vp.call_fn(self.vfn(trk, 0), trk, i)

    def param(self, t, i, idx):
        vp, st = self.vp, self.step(t, i)
        vp.w32(self.out_word, 0)
        ok = vp.call_fn(self.vfn(st, 0x18), st, idx, self.out_word) & 0xFF
        return struct.unpack('<h', vp.mu.mem_read(self.out_word, 2))[0] if ok else None

    def is_on(self, t, i):
        st = self.step(t, i)
        return self.vp.call_fn(self.vfn(st, 0x0c), st) & 0xFF

    def menu_is_open(self):
        ui = self.vp.r32(self.listener + 8)
        return self.vp.r32(ui + self.ui_cur) == self.vp.r32(ui + 0x10)

    def texts(self):
        return [a[2] for n, a in self.vp.eve.cmd_log if n == 'TEXT' and len(a) > 2]

    def tiles(self):
        """labels of the CFW key tiles in the newest frame: font 12 glyphs turned on their side
        (handle 12's draw box set to 22 x 16, then one CELL per letter; not CMD_TEXT)"""
        import struct
        dl = getattr(self.vp.eve, 'last_dl', b'') or b''
        out, cur, on = [], [], False
        for i in range(len(dl) // 4):
            w = struct.unpack_from('<I', dl, 4 * i)[0]
            if w == 0:   # DISPLAY: the rest of the buffer is an older, longer frame
                break
            if w >> 24 == 0x08:
                if cur:
                    out.append(''.join(cur))
                    cur = []
                on = ((w >> 9) & 511, w & 511) == (22, 16)
            elif w >> 24 == 0x06 and on:
                cur.append(chr(w & 127))  # CELL = character code on the chip
        if cur:
            out.append(''.join(cur))
        return out

    def hold(self, row, cols):
        for c in cols:
            self.vp.inputs.pad(row, c, 0)
            self.s.run_ms(30)

    def release(self, row, cols):
        for c in cols:
            self.vp.inputs.pad(row, c, 1)
            self.s.run_ms(20)

    def open_slice_menu(self, menu_open=False):
        """Menu > Samples > Slice. Menus remember their last row, so count from the top."""
        import flows
        s = self.s
        if not menu_open:
            s.tap(BTN_MENU, after_ms=500)
        flows.enter_item(s, flows.MENU_SAMPLES)
        s.run_ms(600)                         # the submenu settles (slower with WAVs on the card)
        s.turn(-64, after_ms=300)
        for _ in range(4):                    # Slice is the 5th Samples item
            s.turn(1, after_ms=250)
        s.tap(BTN_ENTER, after_ms=400)


def boot_snapshot(ppf, path):
    import stage
    print(f'booting {os.path.basename(ppf)} to {BOOT_MS} ms (one-off, a few minutes) ...', flush=True)
    stage.stage(ppf, None, path, insns=BOOT_MS * 100_000, seconds=3600, quiet=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    ap.add_argument('--snap', help='boot snapshot of this ppf (created if missing)')
    ap.add_argument('--out', default=None, help='screenshot directory')
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

    # --- scenario 1: 16 held steps, x16 ---------------------------------------------------
    d = Device(a.ppf, snap)
    d.s.run_ms(200)
    for c in range(16):
        d.s.pad(0, c, hold_ms=40, after_ms=60)
    check('16 trigs placed by pad taps', all(d.is_on(0, i) for i in range(16)))
    d.hold(0, range(16))
    d.s.tap(BTN_MENU, after_ms=500)
    check('Menu opens while 16 step pads are held', d.menu_is_open())
    magic, count, b0 = (d.vp.r32(d.cfw_ram + 8 + 4 * k) for k in range(3))   # after layout, alive_ms
    check('selection snapshot at menu open', (magic, count, b0) == (0x43465731, 16, 0xFFFF),
          f'{magic:#x} {count} {b0:#x}')
    d.s.shot(os.path.join(out, 'slice_1_menu.png'))
    d.open_slice_menu(menu_open=True)
    d.s.run_ms(200)
    check('Samples > Slice panel shown (tiles Points / Apply, count strip)',
          d.tiles() == ['Points', 'Apply'] and 'Steps' in d.texts(), f'{d.tiles()} {d.texts()[-10:]}')
    d.s.turn(1, after_ms=120)
    d.s.turn(1, after_ms=120)
    d.s.shot(os.path.join(out, 'slice_2_x16.png'))
    d.vp.eve.cmd_log.clear()
    d.s.tap(BTN_CLICK, after_ms=150)
    d.s.shot(os.path.join(out, 'slice_3_toast.png'))
    check('toast "Slice x16: 16 steps"', 'Slice x16: 16 steps' in d.texts(), str(d.texts()[:8]))
    d.release(0, range(16))
    d.s.run_ms(200)
    got = [(d.param(0, i, 10), d.param(0, i, 11)) for i in range(16)]
    exp = [(round(j * 32767 / 16), round((j + 1) * 32767 / 16)) for j in range(16)]
    check('x16: Start/End of the 16 steps', got == exp, f'{got}')
    check('menu closed after the action', not d.menu_is_open())

    # --- scenario 2: 4 held steps, "= Steps" --------------------------------------------
    d = Device(a.ppf, snap)
    d.s.run_ms(200)
    cols = [0, 4, 8, 12]
    for c in cols:
        d.s.pad(2, c, hold_ms=40, after_ms=60)
    d.hold(2, cols)
    d.open_slice_menu()
    for _ in range(5):
        d.s.turn(1, after_ms=120)            # = Steps (6th item)
    d.vp.eve.cmd_log.clear()
    d.s.tap(BTN_CLICK, after_ms=150)
    check('toast "Slice: 4 steps"', 'Slice: 4 steps' in d.texts(), str(d.texts()[:8]))
    d.release(2, cols)
    got = [(d.param(2, c, 10), d.param(2, c, 11)) for c in cols]
    exp = [(round(j * 32767 / 4), round((j + 1) * 32767 / 4)) for j in range(4)]
    check('= Steps: 4 slices over 4 steps', got == exp, f'{got}')
    untouched = [d.param(2, c, 10) for c in (1, 2, 3)]
    check('steps between the selection untouched', untouched == [None] * 3, f'{untouched}')

    # --- scenario 3: nothing held, no clipboard ---------------------------------------------
    d = Device(a.ppf, snap)
    d.s.run_ms(200)
    d.open_slice_menu()
    d.vp.eve.cmd_log.clear()
    d.s.tap(BTN_CLICK, after_ms=150)
    check('empty selection toast', 'Hold or copy steps first' in d.texts(), str(d.texts()[:8]))
    d.s.shot(os.path.join(out, 'slice_4_empty.png'))

    print(f'{sum(results)}/{len(results)} passed; screenshots in {out}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
