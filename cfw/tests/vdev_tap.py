#!/usr/bin/env python3
"""Menu > Tap Tempo on the virtual Play+ / Play RT.

  * right after boot (the Tempo page on the second knob-page screen, ui+4): Shift + the top key
    left of the screen taps (750 ms apart -> 80 BPM),
  * no Tap Tempo row in the menu (since CFW5.2): the Tempo page's Shift + key taps,
  * first press: toast "Tap again in time"; 4 taps 500 ms apart: tempo 120 BPM, toast "Tempo 120.0 BPM",
  * the menu stays open while tapping,
  * a pause of 2.5 s starts a new series; taps 400 ms apart move the tempo to ~150 BPM,
  * on the Tempo / Track Swing knob page: Shift + the top key left of the screen taps too
    (taps 600 ms apart -> 100 BPM); without Shift that key keeps its stock job.

usage: vdev_tap.py <patched.ppf>   (exit status 0 = all pass)
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))
from vdev_slice import BTN_ENTER, Device, boot_snapshot  # noqa: E402

APP_PATMGR = {'playplus-1.3.0': 0x4430, 'play-1.6.0-rt': 0x2e60}


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

    import flows
    d = Device(a.ppf, snap)
    s, vp = d.s, d.vp
    proj = vp.r32(vp.profile['app'] + APP_PATMGR[vp.profile['name']])
    tempo = lambda: struct.unpack('<f', vp.mu.mem_read(proj + 0x1f0, 4))[0]  # noqa: E731
    def shift_taps(n, ms):
        vp.inputs.button(10, 0)                       # Shift down
        s.run_ms(60)
        vp.inputs.button(10, 2)                       # ... held
        for _ in range(n):
            s.tap(0, hold_ms=60, after_ms=ms - 60)
        vp.inputs.button(10, 1)
        s.run_ms(100)

    s.run_ms(200)
    # right after boot the Tempo page is up on the second knob-page screen (ui+4), no knob touched
    ui = vp.r32(d.listener + 8)
    cur = vp.r32(ui + vp.profile['ui_cur'])
    shift_taps(4, 750)
    check('boot page (Tempo, ui+4): Shift + top-left taps (750 ms -> 80 BPM)',
          cur == vp.r32(ui + 4) and abs(tempo() - 80.0) < 1.0, f'cur {cur:#x} ui+4 {vp.r32(ui + 4):#x} {tempo()}')
    s.run_ms(2500)                                    # pause: a new series
    vp.eve.cmd_log.clear()
    flows.open_menu(s)
    s.turn(64, after_ms=300)
    t = d.texts()
    check('no Tap Tempo row in the menu', 'Tap Tempo' not in t and 'Settings' in t, str(t[-8:]))
    flows.close_menu(s)
    s.run_ms(300)
    vp.eve.cmd_log.clear()
    shift_taps(1, 100)
    check('first tap: toast "Tap again in time"', 'Tap again in time' in d.texts(), str(d.texts()[:6]))
    s.run_ms(2500)
    s.knob(5, 1, after_ms=300)                        # Tempo / Track Swing page (tempo +1)
    s.knob(5, -1, after_ms=300)
    before = tempo()
    shift_taps(5, 600)
    check(f'Tempo page: Shift + top-left taps (600 ms -> 100 BPM; was {before:.1f})', abs(tempo() - 100.0) < 1.5,
          str(tempo()))
    s.tap(0, hold_ms=60, after_ms=300)                # without Shift: stock (selects the top parameter)
    check('without Shift the key is stock (tempo unchanged)', abs(tempo() - 100.0) < 1.5, str(tempo()))
    # while playing: the new tempo reaches the running sequencer at once (no Stop / Play needed)
    import hle
    for c in (0, 4, 8, 12):
        s.pad(0, c, hold_ms=40, after_ms=60)
    hle.enable_audio(vp)
    s.tap(8, hold_ms=40, after_ms=400)                # Play
    s.run_ms(2500)                                    # pause: new series
    seq = vp.r32(vp.profile['app'] + 0x10)
    buf = vp.call_fn(vp.r32(vp.r32(seq) + 8), seq)
    shift_taps(4, 500)                                # 120 BPM
    live = struct.unpack('<f', vp.mu.mem_read(buf + 4, 4))[0]
    check(f'while playing: the playing tempo follows at once (120, got {live:.1f})', abs(live - 120.0) < 1.5, str(live))
    s.tap(8, hold_ms=40, after_ms=200)                # Stop
    print(f'{sum(results)}/{len(results)} passed')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
