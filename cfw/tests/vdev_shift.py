#!/usr/bin/env python3
"""Knob-page Shift layer and pickers (pages.c) on the virtual Play+ / Play RT.

  * Reverb / Delay (knob 3): holding Shift shows tiles Reverb / Delay; Shift + top key opens the
    reverb list, encoder +1 = preset 1 (the stock page title then reads "Reverb (Small Room)"),
    click keeps it; Shift + bottom key, encoder +2, Back: the delay preset is back to 0,
  * Overdrive (knob 8): Shift + top = Saturation list, +1 -> type 2,
  * Master / Limiter (knob 0): Shift + bottom = Limiter list, +1 -> preset 1,
  * Note (knob 1): Shift + top = Scale list, +2 -> scale 2 (Major) and the scale filter on,
  * releasing Shift gives the stock Reverb page back (no panel, its widgets visible),
  * Tempo (knob 5) with Shift: tiles Tap / -, Sample Attack / Decay (knob 12): the envelope panel,
  * encoder steps: Attack 100 per click (stock 10); Note + Shift = octaves, the page shows
    for a second, then the Shift layer again; Tempo faster with Shift; Menu rows x5.

usage: vdev_shift.py <patched.ppf>   (exit status 0 = all pass)
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))
sys.path.insert(0, HERE)
from vdev_slice import Device, boot_snapshot  # noqa: E402

APP_PATMGR = {'playplus-1.3.0': 0x4430, 'play-1.6.0-rt': 0x2e60}
VIEW = 0x90


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    a = ap.parse_args()
    work = os.environ.setdefault('VPLAY_DIR', os.path.join('/tmp', 'vplay-' + str(os.getuid())))
    os.makedirs(work, exist_ok=True)
    snap = os.path.join(work, os.path.basename(a.ppf) + '.boot.snap')
    if not os.path.exists(snap):
        boot_snapshot(a.ppf, snap)
    results = []

    def check(name, ok, detail=''):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f': {detail}' if detail and not ok else ''))

    d = Device(a.ppf, snap)
    s, vp = d.s, d.vp
    ram = vp.profile['cfw_ram']
    s.run_ms(200)
    proj = vp.r32(vp.profile['app'] + APP_PATMGR[vp.profile['name']])
    ui = vp.r32(d.listener + 8)

    def wmode():
        return vp.mu.mem_read(ram + VIEW + 0x29, 1)[0]

    def shift(down):
        vp.inputs.button(10, 0 if down else 1)
        s.run_ms(60)
        if down:
            vp.inputs.button(10, 2)
            s.run_ms(200)

    def title():
        cur = vp.r32(ui + vp.profile['ui_cur'])
        tab = vp.r32(vp.r32(vp.r32(ui + 0x10) + 4) + 8)
        w = vp.r32(tab + 4 * vp.r32(cur + 0xdc))
        return bytes(vp.mu.mem_read(vp.r32(w + 0x10), 40)).split(b'\0')[0].decode('latin1')

    def stock_visible():
        cur = vp.r32(ui + vp.profile['ui_cur'])
        tab = vp.r32(vp.r32(vp.r32(ui + 0x10) + 4) + 8)
        return [vp.r32(vp.r32(tab + 4 * vp.r32(cur + 0xdc + 4 * k)) + 0xc) & 1 for k in range(6)]

    def page(knob):
        s.touch(knob)
        s.run_ms(400)

    # Reverb / Delay
    page(3)
    shift(True)
    check('Reverb page, Shift held: tiles Reverb / Delay', wmode() == 3 and d.tiles() == ['Reverb', 'Delay'], f'{wmode()} {d.tiles()}')
    s.tap(0, after_ms=300)
    s.turn(1, after_ms=300)
    s.tap(6, after_ms=300)
    check('reverb preset 1 chosen (project +0x50 = 1)', vp.r32(proj + 0x50) == 1, str(vp.r32(proj + 0x50)))
    s.tap(3, after_ms=300)
    s.turn(2, after_ms=300)
    check('delay list: +2 applied live', vp.r32(proj + 0x4c) == 2, str(vp.r32(proj + 0x4c)))
    s.tap(7, after_ms=300)
    check('Back: the delay preset is back to 0', vp.r32(proj + 0x4c) == 0, str(vp.r32(proj + 0x4c)))
    shift(False)
    s.run_ms(400)
    check('Shift released: the stock Reverb page is back', wmode() != 3 and stock_visible() == [1] * 6, f'{wmode()} {stock_visible()}')
    check('the stock title shows the new reverb: "Reverb (Small Room)"', title() == 'Reverb (Small Room)', title())

    # Overdrive: saturation
    page(8)
    shift(True)
    check('Overdrive page, Shift: tile Saturation', d.tiles()[:1] == ['Saturation'], str(d.tiles()))
    sat = vp.mu.mem_read(proj + 0x1fc, 1)[0]
    s.tap(0, after_ms=300)
    s.turn(1, after_ms=300)
    s.tap(6, after_ms=300)
    check(f'saturation type {sat} -> {sat + 1}', vp.mu.mem_read(proj + 0x1fc, 1)[0] == sat + 1, str(vp.mu.mem_read(proj + 0x1fc, 1)[0]))
    shift(False)

    # Master / Limiter
    page(0)
    shift(True)
    check('Master page, Shift: tile Limiter (bottom)', d.tiles() == ['Limiter'], str(d.tiles()))
    s.tap(3, after_ms=300)
    s.turn(1, after_ms=300)
    s.tap(6, after_ms=300)
    check('limiter preset 1', vp.r32(proj + 0xd8) == 1, str(vp.r32(proj + 0xd8)))
    shift(False)

    # Note: scale
    page(1)
    shift(True)
    check('Note page, Shift: tiles Scale / Root', d.tiles() == ['Scale', 'Root'], str(d.tiles()))
    s.tap(0, after_ms=300)
    s.turn(2, after_ms=300)
    s.tap(6, after_ms=300)
    check('scale 2 (Major), scale filter on', vp.r32(proj + 0x1e4) == 2 and vp.mu.mem_read(proj + 0x1f8, 1)[0] == 1,
          f'{vp.r32(proj + 0x1e4)} {vp.mu.mem_read(proj + 0x1f8, 1)[0]}')
    shift(False)

    # Tempo and Attack / Decay
    page(5)
    shift(True)
    check('Tempo page, Shift: tile Tap', d.tiles()[:1] == ['Tap'], str(d.tiles()))
    shift(False)
    page(12)
    check('Sample Attack / Decay: the envelope panel', wmode() == 3, str(wmode()))

    # Encoder steps: Attack / Decay 100 per click (stock 10)
    def bar(k=0):
        cur = vp.r32(ui + vp.profile['ui_cur'])
        tab = vp.r32(vp.r32(vp.r32(ui + 0x10) + 4) + 8)
        return vp.r32(vp.r32(tab + 4 * vp.r32(cur + 0xdc + 4 * (k + 4))) + 0x18)

    def value(k=0):
        cur = vp.r32(ui + vp.profile['ui_cur'])
        tab = vp.r32(vp.r32(vp.r32(ui + 0x10) + 4) + 8)
        w = vp.r32(tab + 4 * vp.r32(cur + 0xdc + 4 * (k + 2)))
        return bytes(vp.mu.mem_read(vp.r32(w + 0x10), 12)).split(b'\0')[0].decode('latin1')

    a0 = bar()
    s.turn(1, after_ms=300)
    a1 = bar()
    check('Attack: one encoder click = 100', a1 - a0 == 100, f'{a0} -> {a1}')
    shift(True)
    s.turn(1, after_ms=300)
    a2 = bar()
    check('Attack page: Shift + encoder is Sustain now, Attack stays (vdev_mod checks Sustain)', a2 == a1, f'{a1} -> {a2}')
    shift(False)

    # Note: Shift + encoder = full octaves; the keyboard page stays up for a moment, not the Shift layer
    page(1)
    n0 = value()
    shift(True)
    check('Note page, Shift: the Shift layer', d.tiles() == ['Scale', 'Root'], str(d.tiles()))
    s.turn(1, after_ms=200)
    n1 = value()
    check(f'Note: Shift + one click = one octave up ({n0} -> {n1})',
          n0[:-1] == n1[:-1] and int(n1[-1]) == int(n0[-1]) + 1, f'{n0} -> {n1}')
    check('after the turn the page itself shows (no Shift tiles)', d.tiles() != ['Scale', 'Root'], str(d.tiles()))
    s.run_ms(1100)
    check('a second later the Shift layer is back', d.tiles() == ['Scale', 'Root'], str(d.tiles()))
    shift(False)

    # Tempo: Shift moves further than a plain click
    page(5)
    t0 = value()
    s.turn(1, after_ms=300)
    t1 = value()
    shift(True)
    s.turn(1, after_ms=300)
    t2 = value()
    shift(False)
    check(f'Tempo: Shift + click moves more ({t0}, {t1}, {t2})',
          abs(float(t2.split()[0]) - float(t1.split()[0])) > abs(float(t1.split()[0]) - float(t0.split()[0])), f'{t0} {t1} {t2}')

    # Menus and lists: Shift + click moves 5 rows
    s.tap(6, after_ms=500)
    s.turn(-64, after_ms=200)
    shift(True)
    s.turn(1, after_ms=300)
    shift(False)
    d.s.shot(os.path.join(work, 'shift_menu.png'))
    # row 5 = Scales on the Play+ (File Samples LFO Matrix Synths Scales MIDI Settings), MIDI on the Play: Enter opens
    # CC Mapping's menu on one and not on the other (one click alone = Samples)
    vp.eve.cmd_log.clear()
    s.tap(6, after_ms=500)
    in_midi = 'CC Mapping' in d.texts()
    check('Menu: Shift + one click lands on row 5', in_midi == (vp.profile['name'] != 'playplus-1.3.0') and 'View Sample Pool' not in d.texts(), str(d.texts()[-6:]))
    print(f'{sum(results)}/{len(results)} passed')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
