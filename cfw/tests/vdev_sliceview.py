#!/usr/bin/env python3
"""Slice preview (waveform + slice markers) on the virtual Play+, real firmware end to end.

Boots the patched image with WAV files on the card (or restores the cached boot snapshot), loads
break120.wav into the sample pool with the stock "Load Files or Folders" browser, places 16 trigs
on track 1, holds them, opens Menu > Samples > Slice and walks x4 .. x64 / = Steps. Checks:
  * the slice panel exists only while the Slice submenu is open (our vtable in the display
    manager's table); the stock item list is hidden meanwhile and shown again afterwards,
  * the white key tiles (Points, To Folder) and the count strip (4 8 16 32 64 Steps),
  * the frame contains the waveform columns and N+1 slice markers for the highlighted count,
  * the texts (sample name, length, track/steps, slices),
  * the top key opens the Points editor in the same panel (tiles Delete / Apply, with Shift
    Clear / To Folder) and Back returns to the counts; neither key ever runs a Slice,
  * Back destroys it; the encoder click on x16 slices the steps and also destroys it.
Screenshots of every state go to --out (these are the images used in cfw/README.md).

usage: vdev_sliceview.py <patched.ppf> [--snap BOOT.snap] [--wavdir DIR] [--out DIR]
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))
sys.path.insert(0, HERE)
from vdev_slice import Device, BTN_ENTER  # noqa: E402

BOOT_MS = 23000
DISP_TABLE, DISP_SLOTS = 0x08, 50
SCR_DISP, SCR_LIST_SLOT = 0x04, 0x3c
W_W = 0x24
WAVES = ('break120.wav', 'break_st.wav', 'longpad.wav')
ITEMS = ['x4', 'x8', 'x16', 'x32', 'x64', '= Steps']


def make_wavs(d):
    """Deterministic test loops (one bar at 120 BPM: kicks, snares, hats), mono + stereo + a pad."""
    import numpy as np
    import wave
    os.makedirs(d, exist_ok=True)
    sr, beat = 44100, 0.5
    n = int(sr * beat * 4)
    out = np.zeros(n)
    rng = np.random.default_rng(1)

    def add(at, sig):
        i0 = int(at * sr)
        seg = out[i0:i0 + len(sig)]
        seg += sig[:len(seg)]

    def kick():
        tt = np.arange(int(0.35 * sr)) / sr
        return np.sin(2 * np.pi * np.cumsum(50 + 120 * np.exp(-tt * 30)) / sr) * np.exp(-tt * 9)

    def snare():
        tt = np.arange(int(0.25 * sr)) / sr
        return (rng.standard_normal(len(tt)) * 0.6 + np.sin(2 * np.pi * 190 * tt) * 0.5) * np.exp(-tt * 18)

    def hat(a):
        tt = np.arange(int(0.06 * sr)) / sr
        return rng.standard_normal(len(tt)) * a * np.exp(-tt * 70)
    e = beat / 2
    for k in range(8):
        add(k * e, hat(0.2 if k % 2 else 0.3))
    for at in (0, 3 * e, 4.5 * e, 5 * e):
        add(at, kick())
    for at in (2 * e, 6 * e):
        add(at, snare())
    out /= np.max(np.abs(out)) * 1.05
    mono = (out * 32767).astype('<i2')

    def write(name, data, ch):
        with wave.open(os.path.join(d, name), 'wb') as w:
            w.setnchannels(ch)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(data.tobytes())
    write(WAVES[0], mono, 1)
    write(WAVES[1], np.stack([mono, np.roll(mono, 2000) // 2], 1).astype('<i2'), 2)
    t = np.arange(20 * sr) / sr
    write(WAVES[2], (np.sin(2 * np.pi * 110 * t) * np.minimum(1, t / 2) * np.exp(-t / 10) * 20000).astype('<i2'), 1)


def link_symbol(ppf, name):
    mp = os.path.join(os.path.dirname(os.path.abspath(ppf)), 'link.map')
    for ln in open(mp):
        p = ln.split()
        if len(p) == 2 and p[1] == name:
            return int(p[0], 16)
    raise KeyError(name)


class View:
    def __init__(self, d, vt):
        self.d, self.vp, self.vt = d, d.vp, vt

    def screen(self):
        """the menu screen object (UI manager + 0x10)"""
        ui = self.vp.r32(self.d.listener + 8)
        return self.vp.r32(ui + 0x10)

    def widgets(self):
        vp = self.vp
        disp = vp.r32(self.screen() + SCR_DISP)
        tbl = vp.r32(disp + DISP_TABLE)
        return [(i, vp.r32(tbl + 4 * i)) for i in range(DISP_SLOTS) if vp.r32(tbl + 4 * i)]

    def ours(self):
        return [(i, w) for i, w in self.widgets() if self.vp.r32(w) == self.vt]

    def list_visible(self):
        """the menu's item list widget: visible flag (bit 0 of +0x0c)"""
        vp = self.vp
        scr = self.screen()
        slot = vp.r32(scr + SCR_LIST_SLOT)
        disp = vp.r32(scr + SCR_DISP)
        w = vp.r32(vp.r32(disp + DISP_TABLE) + 4 * slot)
        return bool(vp.r32(w + 0x0c) & 1) if w else None

    def frame_lines(self):
        """(x0, y0, x1, y1, rgb) for every LINES pair in the last swapped frame."""
        dl = self.vp.eve.last_dl
        out, prim, color, verts, fmt = [], None, 0xffffff, [], 4
        for i in range(len(dl) // 4):
            w = struct.unpack_from('<I', dl, 4 * i)[0]
            if w >> 30 == 1 and prim == 3:
                f = 1 << fmt
                x = ((w >> 15) & 0x7fff) - (0x8000 if w & (1 << 29) else 0)
                y = (w & 0x7fff) - (0x8000 if w & (1 << 14) else 0)
                verts.append((x / f, y / f))
                if len(verts) == 2:
                    out.append(verts[0] + verts[1] + (color,))
                    verts = []
                continue
            op = w >> 24
            if op == 0x1f:
                prim, verts = w & 15, []
            elif op == 0x04:
                color = w & 0xffffff
            elif op == 0x27:
                fmt = w & 7
            elif op == 0x00 and w == 0:
                break
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    ap.add_argument('--snap', help='boot snapshot of this ppf with the WAV card (created if missing)')
    ap.add_argument('--wavdir')
    ap.add_argument('--out')
    a = ap.parse_args()
    work = os.environ.setdefault('VPLAY_DIR', os.path.join('/tmp', 'vplay-' + str(os.getuid()), 'wav'))
    os.makedirs(work, exist_ok=True)
    wavdir = a.wavdir or os.path.join(work, 'wavs')
    if not all(os.path.exists(os.path.join(wavdir, n)) for n in WAVES):
        make_wavs(wavdir)
    snap = a.snap or os.path.join(work, os.path.basename(a.ppf) + '.wav.boot.snap')
    if not os.path.exists(snap):
        import stage
        print(f'booting {os.path.basename(a.ppf)} with WAVs on the card (one-off, a few minutes) ...', flush=True)
        files = {f'/Samples/{n}': open(os.path.join(wavdir, n), 'rb').read() for n in WAVES}
        stage.stage(a.ppf, None, snap, insns=BOOT_MS * 100_000, seconds=3600, quiet=True, sd_files=files)
    out = a.out or os.path.join(work, 'shots')
    os.makedirs(out, exist_ok=True)
    vt = link_symbol(a.ppf, 'cfw_wave_vt')
    results = []

    def check(name, ok, detail=''):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f': {detail}' if detail and not ok else ''))

    import flows
    d = Device(a.ppf, snap)
    s = d.s
    v = View(d, vt)
    s.run_ms(200)
    flows.load_samples(s, [0])                     # break120.wav -> pool slot 0
    check('sample loaded, back on the pattern screen', flows.current_screen(s) == 'other')
    for c in range(16):
        s.pad(0, c, hold_ms=40, after_ms=60)
    check('16 trigs with sample 0', all(d.is_on(0, i) and d.param(0, i, 9) == 0 for i in range(16)))
    d.hold(0, range(16))
    d.open_slice_menu()
    check('Samples > Slice open', 'Samples > Slice' in d.texts())
    s.run_ms(300)
    mine = v.ours()
    check('preview widget created (our vtable in the display table)', len(mine) == 1, str(mine))
    check('item list hidden behind the panel', v.list_visible() is False, str(v.list_visible()))
    t = d.texts()
    check('key tiles Points / Apply and the count strip',
          d.tiles() == ['Points', 'Apply'] and all(x in t for x in ('4', '8', '16', '32', '64', 'Steps')),
          f'{d.tiles()} {t[-16:]}')
    for k, item in enumerate(ITEMS):
        if k:
            d.vp.eve.cmd_log.clear()
            s.turn(1, after_ms=300)
        n = 4 << k if k < 5 else 16
        lines = v.frame_lines()
        marks = [ln for ln in lines if ln[4] in (0x3a3a3a, 0x1c1c1c)]   # grid separators
        bright = [ln for ln in lines if ln[4] == 0x3a3a3a]
        ticks = [ln for ln in lines if ln[4] == 0xffffff and ln[0] == ln[2] and abs(ln[3] - ln[1]) <= 6]
        check(f'{item}: bright separators and white ticks only around the {min(n, 16)} used slices',
              len(bright) == min(n, 16) + 1 and len(ticks) == 2 * (min(n, 16) + 1), f'{len(bright)} {len(ticks)}')
        check(f'{item}: no red on the waveform', not [ln for ln in lines if ln[4] in (0xff5540, 0x5c2219)])
        wave = [ln for ln in lines if ln[4] in (0xe6e6e6, 0x4a4a4a) and ln[0] == ln[2]]
        check(f'{item}: {n + 1} slice separators', len(marks) == n + 1, f'{len(marks)}')
        check(f'{item}: waveform columns', len(wave) >= 200, f'{len(wave)}')
        used = 0xe6e6e6 if n <= 16 else None
        if n > 16:
            dim = sum(1 for ln in wave if ln[4] == 0x4a4a4a)
            check(f'{item}: slices past step 16 dimmed', dim > 0, f'{dim}')
        if k == 2:
            t = d.texts()
            check('texts: name, track + length, steps (no slice numbers)',
                  'break120' in t and 'T1  2.00s' in t and '16 steps' in t and '15' not in t, str(t[-12:]))
        if k == 0:
            check('x4 with 16 steps says it loops', '16 steps, loop' in d.texts(), str(d.texts()[-4:]))
        s.shot(os.path.join(out, f'preview_{k}_{item.replace("= ", "eq").replace(" ", "")}.png'))
    # the Points editor in the same panel (top key), Shift shows its Shift functions, Back returns
    starts = [d.param(0, i, 10) for i in range(16)]
    d.vp.eve.cmd_log.clear()
    s.tap(0, after_ms=300)
    t = d.texts()
    check('top key: Points editor (tiles Delete / Apply), menu still in Slice',
          d.tiles() == ['Delete', 'Apply'] and d.menu_is_open() and len(v.ours()) == 1, f'{d.tiles()} {t[-12:]}')
    s.shot(os.path.join(out, 'preview_points.png'))
    d.vp.inputs.button(10, 0)
    s.run_ms(200)
    t = d.texts()
    check('Shift held: tiles Clear / Apply', d.tiles() == ['Clear', 'Apply'], str(d.tiles()))
    d.vp.inputs.button(10, 1)
    s.run_ms(200)
    d.vp.eve.cmd_log.clear()
    s.tap(7, after_ms=300)
    t = d.texts()
    check('Back in the editor: the counts again (menu still open, panel up)',
          len(v.ours()) == 1 and d.tiles() == ['Points', 'Apply'] and 'Steps' in t, f'{d.tiles()} {t[-12:]}')
    check('neither key ran a Slice', [d.param(0, i, 10) for i in range(16)] == starts)
    # back out: widget gone, list shown again
    s.tap(7, after_ms=400)
    check('Back: preview destroyed', not v.ours(), str(v.ours()))
    check('Back: item list visible again', v.list_visible() is True, str(v.list_visible()))
    s.shot(os.path.join(out, 'preview_back.png'))
    # re-enter, run x16 with the encoder click (the keys left of the screen are Points / To Folder)
    s.turn(4, after_ms=120)
    s.tap(BTN_ENTER, after_ms=400)
    check('re-entered: preview again', len(v.ours()) == 1)
    s.turn(-8, after_ms=200)
    s.turn(2, after_ms=200)
    d.vp.eve.cmd_log.clear()
    s.tap(6, after_ms=200)
    check('x16 toast', 'Slice x16: 16 steps' in d.texts(), str(d.texts()[:6]))
    d.release(0, range(16))
    s.run_ms(200)
    got = [(d.param(0, i, 10), d.param(0, i, 11)) for i in range(16)]
    exp = [(round(j * 32767 / 16), round((j + 1) * 32767 / 16)) for j in range(16)]
    check('x16 applied', got == exp, str(got[:3]))
    check('menu closed: no preview widget left', not [w for i, w in v.widgets() if d.vp.r32(w) == vt])
    s.shot(os.path.join(out, 'preview_done.png'))

    # nothing held, nothing copied: the panel explains what to do
    d2 = Device(a.ppf, snap)
    v2 = View(d2, vt)
    d2.s.run_ms(200)
    d2.open_slice_menu()
    d2.s.run_ms(300)
    t = d2.texts()
    check('empty: panel shown with instructions', len(v2.ours()) == 1 and 'No steps chosen' in t and
          'or Copy steps first' in t, str(t[-6:]))
    d2.s.shot(os.path.join(out, 'preview_empty.png'))

    print(f'{sum(results)}/{len(results)} passed; screenshots in {out}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
