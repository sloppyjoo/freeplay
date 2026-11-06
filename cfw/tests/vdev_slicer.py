#!/usr/bin/env python3
"""Menu > Samples > Slice on the virtual Play+ / Play RT: pick a track, set the slices, choose where they land, apply.

  1. a track with no sample says so and offers one thing to do; the top key opens the sample list, click picks,
  2. the encoder sets the number of slices; Shift + encoder zooms and leaves it alone; zoomed in the encoder scrolls,
  3. In time: slices land where they sit in the sample, trigs are made and removed; Every step; My trigs,
  4. the screen stays up after applying; the step pads preview the plan; the select pad blinks,
  5. select pads change the track, and the other track stays as it was; a count above the track's length is capped,
  6. Points: cuts by hand, In time places them by their position; playing it starts the voice at the cuts,
  7. Shift + click: every slice as its own sample in a new folder; Back leaves and the pads go back to stock.
Screenshots go to --out.

usage: vdev_slicer.py <patched.ppf> [--out DIR]
"""
import argparse
import os
import struct
import sys

import unicorn.arm_const as A

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))
sys.path.insert(0, HERE)
from vdev_slice import Device  # noqa: E402
from vdev_sliceview import BOOT_MS, WAVES, make_wavs  # noqa: E402

BTN_TOP, BTN_BOT, BTN_CLICK, BTN_BACK, BTN_PLAY, BTN_SHIFT = 0, 3, 6, 7, 8, 10
SEL_COL, MAX = 19, 32767
LED_SET = {'playplus-1.3.0': 0x600ac580, 'play-1.6.0-rt': 0x60023810}
STRIDE = {'playplus-1.3.0': 0xbb4, 'play-1.6.0-rt': 0xba8}
RD_START = {'playplus-1.3.0': 0x2c, 'play-1.6.0-rt': 0x28}
BLUE = b'\x00\x28\xc0'


def cut(j, n):
    return (2 * j * MAX + n) // (2 * n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    ap.add_argument('--out')
    a = ap.parse_args()
    work = os.path.join(os.environ.setdefault('VPLAY_DIR', os.path.join('/tmp', 'vplay-' + str(os.getuid()))), 'slicer')
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
    import sdcard
    d = Device(a.ppf, snap)
    s, vp = d.s, d.vp
    s.run_ms(200)
    flows.load_samples(s, [0])                       # break120.wav, 2.00 s, in the pool; no trigs yet

    def shift(on):
        vp.inputs.button(BTN_SHIFT, 0 if on else 1)
        s.run_ms(60)

    def texts(ms=150):
        """the text of the panel's newest drawing: it only draws again when something changes, so the log stays whole
        and this looks back for the last drawing that was the panel (its top row, or the sample list)"""
        s.run_ms(ms)
        seg, found = [], None
        for n, a in vp.eve.cmd_log:
            if 'DLSTART' in n:
                if any(x.startswith('Track ') or x == 'break120' for x in seg):
                    found = seg
                seg = []
            elif n == 'TEXT' and len(a) > 2:
                seg.append(a[2])
        if any(x.startswith('Track ') or x == 'break120' for x in seg):
            found = seg
        return found or []

    def on_steps(t, n=16):
        return [i for i in range(n) if d.is_on(t, i)]

    def cuts_of(t, steps):
        return [(d.param(t, i, 10), d.param(t, i, 11)) for i in steps]

    # --- 1. no sample yet --------------------------------------------------------------------------
    d.open_slice_menu()
    s.run_ms(300)
    t = texts()
    check('a track without a sample says so', 'No sample yet' in t and 'Press Sample to choose one' in t and 'Track 1' in t, str(t[-6:]))
    check('tiles: Sample (lit) and Points', d.tiles() == ['Sample', 'Points'], str(d.tiles()))
    s.shot(os.path.join(out, 'slicer_1_empty.png'))
    s.tap(BTN_CLICK, after_ms=200)
    check('click does nothing without a sample', on_steps(0) == [] and d.menu_is_open(), str(on_steps(0)))
    s.tap(BTN_TOP, after_ms=300)
    t = texts()
    check('top key: the sample list', 'break120' in t, str(t[-6:]))
    s.shot(os.path.join(out, 'slicer_1_list.png'))
    s.tap(BTN_CLICK, after_ms=300)
    t = texts()
    check('click picks it: 16 slices on track 1, In time', '16 slices' in t and 'In time' in t and 'break120' in t and 'Track 1' in t, str(t[-8:]))
    check('tiles: Layout and Points', d.tiles() == ['Layout', 'Points'], str(d.tiles()))
    s.shot(os.path.join(out, 'slicer_2_main.png'))

    # the pads: the plan on the track's row, the select pad blinking
    leds = []

    def led_hit(vp):
        r = vp.mu.reg_read
        p = r(A.UC_ARM_REG_R2)
        leds.append((r(A.UC_ARM_REG_R1), bytes(vp.mu.mem_read(p, 3)) if p else b''))
    vp.hook_addr(LED_SET[vp.profile['name']], led_hit)
    s.run_ms(1100)
    check('the select pad of the track blinks blue', any(x == (0 * 20 + SEL_COL, BLUE) for x in leds), str(leds[:6]))

    # --- 2. the encoder and the zoom ----------------------------------------------------------------
    s.turn(-2, after_ms=150)                         # 16 -> 12 -> 8
    t = texts()
    check('the encoder sets the slices (8)', '8 slices' in t, str(t[-6:]))
    s.turn(1, after_ms=200)
    leds.clear()
    s.turn(-1, after_ms=300)
    lit = sorted({x[0] for x in leds if x[1] == BLUE and x[0] // 20 == 0 and x[0] % 20 < 16})
    check('the step pads show where the 8 slices will land', lit == [0, 2, 4, 6, 8, 10, 12, 14], str(lit))
    shift(True)
    t = texts()
    check('Shift: the hint and the tile change', 'Sample' in d.tiles() and any('zoom' in x for x in t), f'{d.tiles()} {t[-4:]}')
    s.turn(1, after_ms=200)
    shift(False)
    t = texts()
    check('Shift + encoder zooms and leaves the count', 'x2' in t and '8 slices' in t, str(t[-6:]))
    s.shot(os.path.join(out, 'slicer_2_zoom.png'))
    s.turn(3, after_ms=200)
    t = texts()
    check('zoomed in: the encoder scrolls, the count stays', '8 slices' in t and any('scroll' in x for x in t), str(t[-6:]))
    shift(True)
    s.turn(-1, after_ms=200)
    shift(False)
    t = texts()
    check('zoomed out: back', 'x2' not in t, str(t[-6:]))

    # --- 3. the layouts -----------------------------------------------------------------------------
    s.tap(BTN_CLICK, after_ms=300)
    steps = on_steps(0)
    check('In time, x8 on 16 steps: every second step gets a trig', steps == [0, 2, 4, 6, 8, 10, 12, 14], str(steps))
    check('each its slice', cuts_of(0, steps) == [(cut(k, 8), cut(k + 1, 8)) for k in range(8)], str(cuts_of(0, steps)[:3]))
    t = texts()
    check('it stays up and says what happened', d.menu_is_open() and d.tiles() == ['Layout', 'Points'] and any('Applied' in x for x in t), str(t[-5:]))
    s.shot(os.path.join(out, 'slicer_3_applied.png'))
    s.tap(BTN_TOP, after_ms=200)
    t = texts()
    check('top key: the next layout (Every step)', 'Every step' in t, str(t[-5:]))
    s.tap(BTN_CLICK, after_ms=300)
    steps = on_steps(0)
    check('Every step: steps 1..8, the others off', steps == list(range(8)), str(steps))
    check('in order', cuts_of(0, steps) == [(cut(k, 8), cut(k + 1, 8)) for k in range(8)], str(cuts_of(0, steps)[:3]))
    s.tap(BTN_TOP, after_ms=200)
    s.turn(-2, after_ms=200)                         # x8 -> x6 -> x4
    t = texts()
    check('My trigs', 'My trigs' in t and '4 slices' in t, str(t[-5:]))
    s.shot(os.path.join(out, 'slicer_3_mytrigs.png'))
    s.tap(BTN_CLICK, after_ms=300)
    check('My trigs: the same trigs, the slices in order and around', on_steps(0) == list(range(8)) and
          cuts_of(0, range(8)) == [(cut(k % 4, 4), cut(k % 4 + 1, 4)) for k in range(8)], str(cuts_of(0, range(8))[:5]))
    s.tap(BTN_TOP, after_ms=200)                     # In time again

    # --- 4. the step pads don't change anything here ------------------------------------------------
    before = on_steps(0)
    s.pad(0, 3, hold_ms=40, after_ms=200)
    check('a step pad changes nothing in this screen', on_steps(0) == before, str(on_steps(0)))

    # --- 5. another track ---------------------------------------------------------------------------
    s.pad(2, SEL_COL, hold_ms=40, after_ms=300)
    t = texts()
    check('select pad: track 3, no sample yet', 'Track 3' in t and 'No sample yet' in t, str(t[-6:]))
    s.tap(BTN_TOP, after_ms=300)
    s.tap(BTN_CLICK, after_ms=300)
    s.turn(64, after_ms=300)                         # as many as it can have
    t = texts()
    check('the count stops at the track\'s 16 steps', '16 slices' in t, str(t[-6:]))
    s.tap(BTN_CLICK, after_ms=300)
    check('track 3 gets all 16 steps', on_steps(2) == list(range(16)), str(on_steps(2)))
    check('track 1 stays as it was', on_steps(0) == before, str(on_steps(0)))

    # --- 6. Points ----------------------------------------------------------------------------------
    s.pad(0, SEL_COL, hold_ms=40, after_ms=300)
    s.tap(BTN_BOT, after_ms=300)
    check('bottom key: Points, tiles Delete / Done', d.tiles() == ['Delete', 'Done'], str(d.tiles()))
    s.tap(BTN_CLICK, after_ms=200)                   # at 1.000 s: 16384
    s.turn(32, after_ms=200)                         # 16384 + 32 x 257 = 24608
    s.tap(BTN_CLICK, after_ms=200)
    t = texts()
    check('two cuts: 3 slices', '3 slices' in t, str(t[-6:]))
    s.shot(os.path.join(out, 'slicer_6_points.png'))
    s.tap(BTN_BOT, after_ms=300)
    t = texts()
    check('Done: 3 slices by hand', '3 slices by hand' in t, str(t[-6:]))
    s.tap(BTN_CLICK, after_ms=300)
    steps = on_steps(0)
    check('In time with points: the trigs sit where the cuts are (0, 1 s, 1.5 s of 2 s)', steps == [0, 8, 12], str(steps))
    check('each its slice', cuts_of(0, steps) == [(0, 16384), (16384, 24608), (24608, MAX)], str(cuts_of(0, steps)))
    # it plays: the voice starts at the cuts
    hle.enable_audio(vp)
    s.tap(BTN_PLAY, hold_ms=40, after_ms=100)
    seq = vp.r32(vp.profile['app'] + 0x10)
    buf = vp.call_fn(vp.r32(vp.r32(seq) + 8), seq)
    inst = vp.r32(buf + 0x98)
    seen = set()
    for _ in range(40):
        s.run_ms(60)
        seen.add(vp.r32(inst + 0xd8 + RD_START[vp.profile['name']]))
    check('playing: the voice starts at each slice', len(seen) >= 3, str(seen))
    s.tap(BTN_PLAY, hold_ms=40, after_ms=200)
    s.turn(1, after_ms=200)                          # the encoder goes back to a count
    t = texts()
    check('the encoder after points: back to equal slices', not any('by hand' in x for x in t), str(t[-5:]))

    # --- 7. to a folder ------------------------------------------------------------------------------
    s.turn(-64, after_ms=200)                        # 2
    s.turn(2, after_ms=200)                          # 4
    shift(True)
    s.tap(BTN_CLICK, after_ms=200)
    shift(False)
    s.run_ms(4000)
    steps = on_steps(0)
    check('Shift + click: x4 as samples in a folder', sdcard.read_file(vp.card.path, '/Samples/break120 x4/04.wav') is not None, '')
    check('In time: steps 1, 5, 9, 13', steps == [0, 4, 8, 12], str(steps))
    samples = [d.param(0, i, 9) for i in steps]
    check('each plays its own sample, whole', len(set(samples)) == 4 and cuts_of(0, steps) == [(0, MAX)] * 4, f'{samples} {cuts_of(0, steps)}')
    s.shot(os.path.join(out, 'slicer_7_folder.png'))
    # leave
    s.tap(BTN_BACK, after_ms=500)
    s.shot(os.path.join(out, 'slicer_8_back.png'))
    check('Back leaves: no tiles over the list', d.tiles() == [] and d.menu_is_open(), str(d.tiles()))
    leds.clear()
    s.run_ms(800)
    check('the pads go back to stock', not any(x[1] == BLUE for x in leds), str(leds[:6]))
    print(f'{sum(results)}/{len(results)} passed; screenshots in {out}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
