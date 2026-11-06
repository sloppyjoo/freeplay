#!/usr/bin/env python3
"""Knob-page visuals (pages.c) on the virtual Play+ / Play RT.

Boots with WAVs on the card, loads break120.wav, places 16 trigs on track 1 and plays them (audio
clock on), then checks per page:
  * Master Volume / Limiter (knob 0): our panel replaces the six stock widgets; titles, values, the
    dB scale and L / R; with the pattern playing the meter rises from the -48 dB floor and the level
    history fills,
  * Note / Microtune (knob 1): "Note", "C 4", octave labels; knob 1 +5 -> "F 4" and the panel's note
    is 53; the bottom key makes Microtune the active parameter (as stock, panel follows),
  * Volume / Panning (knob 6): titles, values, the dB scale, L / R,
  * Filter Cutoff / Resonance (knob 11): "No Filter"; knob 11 +20 -> "High-Pass 20" and the curve
    (a 151-point line strip) is in the frame,
  * a page without a visual (Chance, knob 4): no panel, the six stock widgets visible again,
  * Menu over a page: panel gone; menu closed: back.
Screenshots of every page go to --out.

usage: vdev_pages.py <patched.ppf> [--out DIR]   (exit status 0 = all pass)
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))
sys.path.insert(0, HERE)
from vdev_slice import Device  # noqa: E402
from vdev_sliceview import BOOT_MS, WAVES, make_wavs  # noqa: E402

VIEW = 0x90           # struct cfw_view in cfw_ram
PAGES_PTR = 0xfc      # cfw_state.pages (last word of the 256-byte block)
WMODE_PAGE = 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    ap.add_argument('--out')
    a = ap.parse_args()
    work = os.path.join(os.environ.setdefault('VPLAY_DIR', os.path.join('/tmp', 'vplay-' + str(os.getuid()))), 'pages')
    os.makedirs(work, exist_ok=True)
    os.environ['VPLAY_DIR'] = work
    wavdir = os.path.join(work, 'wavs')
    if not all(os.path.exists(os.path.join(wavdir, n)) for n in WAVES):
        make_wavs(wavdir)
    snap = os.path.join(work, os.path.basename(a.ppf) + '.wav.boot.snap')
    if not os.path.exists(snap):
        import stage
        print('booting with WAVs on the card (one-off) ...', flush=True)
        files = {f'/Samples/{n}': open(os.path.join(wavdir, n), 'rb').read() for n in WAVES}
        stage.stage(a.ppf, None, snap, insns=BOOT_MS * 100_000, seconds=3600, quiet=True, sd_files=files)
    out = a.out or os.path.join(work, 'shots')
    os.makedirs(out, exist_ok=True)
    results = []

    def check(name, ok, detail=''):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f': {detail}' if detail and not ok else ''))

    import flows
    import hle
    d = Device(a.ppf, snap)
    s, vp = d.s, d.vp
    ram = vp.profile['cfw_ram']
    s.run_ms(200)
    ui = vp.r32(d.listener + 8)

    def wmode():
        return vp.mu.mem_read(ram + VIEW + 0x29, 1)[0]

    def state():
        g = vp.r32(ram + PAGES_PTR)
        if not g:
            return None
        b = bytes(vp.mu.mem_read(g, 48))
        page, active, hpos = b[4], b[5], b[6]
        lvl = struct.unpack_from('<2h', b, 8)
        raw = struct.unpack_from('<2i', b, 40)
        return dict(page=page, active=active, hpos=hpos, lvl=lvl, raw=raw)

    def stock_visible():
        cur = vp.r32(ui + vp.profile['ui_cur'])
        tab = vp.r32(vp.r32(vp.r32(ui + 0x10) + 4) + 8)
        return [vp.r32(vp.r32(tab + 4 * vp.r32(cur + 0xdc + 4 * k)) + 0xc) & 1 for k in range(6)]

    def page(knob, ms=500):
        vp.eve.cmd_log.clear()
        s.touch(knob)
        s.run_ms(ms)
        return d.texts()

    def panel_up():
        return wmode() == WMODE_PAGE and stock_visible() == [0] * 6

    flows.load_samples(s, [0])                       # break120.wav -> pool slot 0
    for c in range(0, 16, 2):
        s.pad(0, c, hold_ms=40, after_ms=60)
    hle.enable_audio(vp)
    s.tap(8, hold_ms=40, after_ms=300)               # Play

    # Master Volume / Limiter
    t = page(0, 1500)
    st = state()
    check('Master: panel replaces the stock widgets', panel_up(), f'{wmode()} {stock_visible()}')
    check('Master: titles, values, scale, L / R',
          all(x in t for x in ('Master Volume', 'Limiter (Squishy)', '0.0 dB', '0 dB', '-48', '-12', 'L', 'R')), str(t[-24:]))
    check('Master: the meter follows the playing pattern (above -48 dB)', st and max(st['lvl']) > -480, str(st))
    s.run_ms(1500)
    st2 = state()
    check('Master: the level history moves', st and st2 and st2['hpos'] != st['hpos'], f'{st} {st2}')
    s.shot(os.path.join(out, 'pages_1_master.png'))

    # Note / Microtune
    t = page(1)
    check('Note: panel, "Note" "C 4" "Microtune", octave labels',
          panel_up() and all(x in t for x in ('Note', 'C 4', 'Microtune', 'C4', 'C5')), str(t[-14:]))
    s.knob(1, 5, after_ms=400)
    st = state()
    check('Note: knob 1 +5 -> F 4 on the panel (note 53)', st and st['raw'][0] == 53 and 'F 4' in d.texts(), str(st))
    s.shot(os.path.join(out, 'pages_2_note.png'))
    s.tap(3, after_ms=300)                           # bottom key: Microtune active (stock)
    check('Note: bottom key -> Microtune active (panel follows)', state()['active'] == 1, str(state()))
    s.tap(0, after_ms=300)
    check('Note: top key -> Note active again', state()['active'] == 0, str(state()))

    # Volume / Panning
    t = page(6)
    check('Volume: panel, titles, values, dB scale, L / R',
          panel_up() and all(x in t for x in ('Volume', 'Panning', '0 dB', 'Center', '-30', '+12', 'L', 'R')), str(t[-16:]))
    s.knob(6, -10, after_ms=400)
    check('Volume: knob 6 -10 -> raw 5600 on the panel', state()['raw'][0] == 5600, str(state()))
    s.shot(os.path.join(out, 'pages_3_volume.png'))

    # Filter Cutoff / Resonance
    t = page(11)
    check('Filter: panel, "No Filter", grid labels', panel_up() and all(x in t for x in ('Filter Cutoff', 'No Filter', '1k', '10k')),
          str(t[-12:]))
    vp.eve.cmd_log.clear()
    s.knob(11, 20, after_ms=500)
    dlw = vp.eve.last_dl
    strip = 0
    prim, n = None, 0
    for i in range(len(dlw) // 4):
        w = struct.unpack_from('<I', dlw, 4 * i)[0]
        if w >> 24 == 0x1f:
            prim, n = w & 15, 0
        elif w >> 30 == 1 and prim == 4:
            n += 1
        elif w == 0x21000000 and prim == 4:
            strip = max(strip, n)
    check('Filter: knob 11 +20 -> "High-Pass 20", the curve is drawn (151-point strip)',
          'High-Pass 20' in d.texts() and strip == 151 and state()['raw'][0] == 2000, f'{strip} {state()} {d.texts()[-6:]}')
    s.knob(11, 4, after_ms=200)                      # resonance: Shift-free second knob is the same knob; use the key
    s.tap(3, after_ms=200)
    s.knob(11, 30, after_ms=400)
    s.tap(0, after_ms=200)
    s.shot(os.path.join(out, 'pages_4_filter.png'))

    # a page without a visual
    t = page(4)
    check('Chance: no panel, stock widgets visible', wmode() != WMODE_PAGE and stock_visible() == [1] * 6,
          f'{wmode()} {stock_visible()}')

    # menu over a page
    page(6)
    flows.open_menu(s)
    s.run_ms(300)
    check('Menu over Volume: panel gone', wmode() != WMODE_PAGE)
    flows.close_menu(s)
    s.run_ms(500)
    check('menu closed: panel back', wmode() == WMODE_PAGE and panel_up(), f'{wmode()} {stock_visible()}')
    s.tap(8, hold_ms=40, after_ms=200)               # stop
    print(f'{sum(results)}/{len(results)} passed; screenshots in {out}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
