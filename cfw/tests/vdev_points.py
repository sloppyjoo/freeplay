#!/usr/bin/env python3
"""Samples > Slice > Points (hand-placed slice points) on the virtual Play+ / Play RT.

Boots with WAVs on the card, loads break120.wav (2.00 s) into the pool, places 16 trigs on
track 1, holds them, opens Menu > Samples > Slice, presses the top key (Points) and checks:
  * the top key left of the screen opens the editor in the Slice panel (full width), cursor at 1.000 s,
  * the bottom bar stays stock (Back | Play | Perform); white key tiles label the keys left of the
    screen ("Delete" top, "Apply" bottom; with Shift held "Clear" / "To Folder"),
  * encoder click adds a point at the cursor; the encoder moves the cursor; Shift + encoder zooms;
    click on a point removes it; top-left key ("Delete") removes the nearest point; knobs are swallowed,
  * Back returns to the count view (menu still in Slice); the points are kept,
  * bottom-left key ("Apply") slices the 16 steps at the points (step k gets slice k mod 3),
  * Shift + top-left clears all points; Shift held with its "held" event still zooms.
Screenshots go to --out.

usage: vdev_points.py <patched.ppf> [--out DIR]   (needs link.map next to the .ppf)
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))
sys.path.insert(0, HERE)
from vdev_slice import Device, BTN_ENTER  # noqa: E402
from vdev_sliceview import BOOT_MS, WAVES, View, link_symbol, make_wavs, W_W  # noqa: E402

BTN_LTOP, BTN_LBOT, BTN_CLICK, BTN_BACK, BTN_SHIFT = 0, 3, 6, 7, 10   # left of screen: top / bottom; encoder click
SPAN = 32768
CFW_RAM = {'playplus-1.3.0': 0x20002700, 'play-1.6.0-rt': 0x20001700}


def bar(t):
    """the soft-key labels of the newest frame (the log may hold the frame before too)"""
    return [x for x in t if x in ('Back', 'Play', 'Perform')][-3:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    ap.add_argument('--out')
    a = ap.parse_args()
    work = os.path.join(os.environ.setdefault('VPLAY_DIR', os.path.join('/tmp', 'vplay-' + str(os.getuid()))),
                        'points')
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
    vt = link_symbol(a.ppf, 'cfw_wave_vt')
    results = []

    def check(name, ok, detail=''):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f': {detail}' if detail and not ok else ''))

    import flows
    d = Device(a.ppf, snap)
    s, vp = d.s, d.vp
    v = View(d, vt)
    s.run_ms(200)
    # Stale scratch RAM, as left by an older CFW or the previous boot: cfw_ram survives warm
    # resets and firmware updates. CFW4 as first built crashed on hardware here (a CFW3 word was
    # read as the points pointer). Keep the layout word, claim a later uptime (= a reboot since),
    # fill the rest with CFW3's "RND1" word: the CFW must zero it all before use.
    ram = CFW_RAM[vp.profile['name']]
    layout = vp.r32(ram)
    check('cfw_ram has its layout word after boot', layout >> 24 == 0xCF, hex(layout))
    vp.mu.mem_write(ram, struct.pack('<II', layout, 0xFFFFFF00) + struct.pack('<I', 0x524E4431) * 62)
    s.run_ms(100)
    check('stale cfw_ram zeroed after a "reboot"', vp.r32(ram + 8) == 0 and vp.r32(ram + 0xf0) == 0,
          hex(vp.r32(ram + 8)))
    vp.mu.mem_write(ram, struct.pack('<I', 0x524E4431) * 64)   # and with a foreign layout word
    s.run_ms(100)
    check('foreign cfw_ram zeroed', vp.r32(ram) == layout and vp.r32(ram + 0xf0) == 0, hex(vp.r32(ram)))
    # A fresh device for the rest: the garbage above lands mid main-loop pass, so that pass's ticks
    # allocate heap states the next pass's check then forgets (on hardware the garbage is only there
    # at power-up, before any CFW code runs). Two such "reboots" leak ~10 KB, which the Play RT's
    # small on-chip heap can't spare (the browser's allocation then fails).
    d = Device(a.ppf, snap)
    s, vp = d.s, d.vp
    v = View(d, vt)
    s.run_ms(200)
    flows.load_samples(s, [0])                       # break120.wav -> pool slot 0
    for c in range(16):
        s.pad(0, c, hold_ms=40, after_ms=60)
    d.hold(0, range(16))
    d.open_slice_menu()
    s.turn(2, after_ms=150)                          # x16 highlighted in the count strip
    vp.eve.cmd_log.clear()
    s.tap(BTN_LTOP, after_ms=500)                    # top key left of the screen: Points
    t = d.texts()
    mine = v.ours()
    check('top key opens the editor in the Slice panel (full width 320)',
          len(mine) == 1 and vp.r32(mine[0][1] + W_W) == 320 and d.menu_is_open(),
          str([(i, vp.r32(w + W_W)) for i, w in mine]))
    check('starts at 1.000 s, no points, hint', '1.000 s' in t and 'Click: add a point' in t and '1 slice' in t,
          str(t[-8:]))
    check('bottom bar stays stock (unchanged: cached, or re-sent as Back | Play | Perform)', bar(t) in ([], ['Back', 'Play', 'Perform']), str(bar(t)))
    check('key tiles: "Delete" (top), "Apply" (bottom)', d.tiles() == ['Delete', 'Apply'], str(d.tiles()))
    s.shot(os.path.join(out, 'points_1_open.png'))

    def tap_enter():
        vp.eve.cmd_log.clear()
        s.tap(BTN_CLICK, after_ms=300)               # encoder click
        return d.texts()
    t = tap_enter()                                  # point at 16384 (1.000 s)
    check('click adds a point', '2 slices' in t and '1 point' in t, str(t[-6:]))
    vp.eve.cmd_log.clear()
    s.turn(32, after_ms=300)                         # +32 detents x 257 = 8224 -> 24608 (1.501 s)
    check('encoder moves the cursor (1.501 s)', '1.501 s' in d.texts(), str(d.texts()[-6:]))
    t = tap_enter()
    check('second point', '3 slices' in t, str(t[-6:]))
    vp.inputs.button(BTN_SHIFT, 0)
    s.run_ms(60)
    vp.inputs.button(BTN_SHIFT, 2)                   # the firmware's "held" event: still held
    s.run_ms(60)
    t = d.texts()
    check('Shift held: tiles show "Clear" / "Apply"', d.tiles() == ['Clear', 'Apply'], str(d.tiles()))
    vp.eve.cmd_log.clear()
    s.turn(1, after_ms=300)
    vp.inputs.button(BTN_SHIFT, 1)
    s.run_ms(60)
    check('Shift + encoder zooms (x2)', 'zoom x2' in ' '.join(d.texts()), str(d.texts()[-6:]))
    s.shot(os.path.join(out, 'points_2_zoom.png'))
    vp.eve.cmd_log.clear()
    s.turn(10, after_ms=300)                         # zoomed: 129 per detent -> 25898
    t = tap_enter()                                  # third point at 25898
    check('third point while zoomed', '4 slices' in t, str(t[-6:]))
    t = tap_enter()                                  # same spot: removed again
    check('click on a point removes it', '3 slices' in t, str(t[-6:]))
    t = tap_enter()                                  # third point again (25898)
    vp.eve.cmd_log.clear()
    s.tap(BTN_LTOP, after_ms=300)                    # "Delete": the nearest point (25898) goes
    check('top key Delete removes the nearest point', '3 slices' in d.texts(), str(d.texts()[-6:]))
    before = d.param(0, 0, 0)
    s.knob(0, 5, after_ms=200)
    check('knobs are ignored in the editor', d.param(0, 0, 0) == before, str(d.param(0, 0, 0)))
    # Back: the count view again, menu still in Slice
    vp.eve.cmd_log.clear()
    s.tap(BTN_BACK, after_ms=500)
    t = d.texts()
    check('Back: the count view (tiles Points / Apply, strip), menu still open',
          d.menu_is_open() and d.tiles() == ['Points', 'Apply'] and 'Steps' in t and len(v.ours()) == 1,
          f'{d.tiles()} {t[-10:]}')
    check('count view: bottom bar unchanged (cached or Back | Play | Perform)', bar(t) in ([], ['Back', 'Play', 'Perform']), str(bar(t)))
    s.shot(os.path.join(out, 'points_3_counts.png'))
    vp.eve.cmd_log.clear()
    s.tap(BTN_LTOP, after_ms=400)                    # back into the editor: points kept
    check('editor again: the 2 points are kept', '2 points' in d.texts(), str(d.texts()[-6:]))
    s.tap(BTN_LBOT, after_ms=400)                    # "Apply": the choice
    check('Apply opens the choice: tiles Folder / Start/End', d.tiles() == ['Folder', 'Start/End'], str(d.tiles()))
    s.shot(os.path.join(out, 'points_4_choice.png'))
    s.tap(BTN_LBOT, after_ms=200)                    # Start/End: classic slice
    s.shot(os.path.join(out, 'points_4_applied.png'))
    check('toast "Slice pts: 16 steps"', 'Slice pts: 16 steps' in d.texts(), str(d.texts()[:6]))
    d.release(0, range(16))
    s.run_ms(200)
    cuts = [0, 16384, 24608, 32767]
    exp = [(cuts[k % 3], cuts[k % 3 + 1]) for k in range(16)]
    got = [(d.param(0, i, 10), d.param(0, i, 11)) for i in range(16)]
    check('steps get the hand-placed slices in order (wrapping)', got == exp, str(got[:4]))
    check('menu closed after Apply', not d.menu_is_open())
    # Shift + Delete clears all points
    d.hold(0, range(16))
    d.open_slice_menu()
    s.tap(BTN_LTOP, after_ms=400)                    # Points
    vp.inputs.button(BTN_SHIFT, 0)
    s.run_ms(60)
    vp.eve.cmd_log.clear()
    s.tap(BTN_LTOP, after_ms=300)
    vp.inputs.button(BTN_SHIFT, 1)
    s.run_ms(300)
    check('Shift + Delete clears all points', 'Click: add a point' in d.texts() and '1 slice' in d.texts(),
          str(d.texts()[-6:]))
    s.shot(os.path.join(out, 'points_5_cleared.png'))
    print(f'{sum(results)}/{len(results)} passed; screenshots in {out}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
