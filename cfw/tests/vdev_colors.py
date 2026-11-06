#!/usr/bin/env python3
"""Folder colours (colors.c) on the virtual Play+ / Play RT.

Boots with WAVs on the card, loads break120.wav into a new pool folder, places trigs on track 1,
then checks:
  * plain trig pads get the stock trig colour (55 77 55) while the folder is White (default),
  * View Sample Pool shows our panel (stripes left of the folder rows),
  * on the folder row the top key opens the palette, encoder +1 = Red (pads follow live), the
    encoder click sets it: /Projects/cfwColors on the card names the folder,
  * back on the grid the trig pads light red (a0 08 04); empty steps stay stock,
  * the palette again, encoder +2 then Back: the colour stays Red (cancel).

usage: vdev_colors.py <patched.ppf> [--out DIR]   (exit status 0 = all pass)
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

LED_SET = {'playplus-1.3.0': 0x600ac580, 'play-1.6.0-rt': 0x60023810}
VIEW = 0x90


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    ap.add_argument('--out')
    a = ap.parse_args()
    work = os.path.join(os.environ.setdefault('VPLAY_DIR', os.path.join('/tmp', 'vplay-' + str(os.getuid()))), 'colors')
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
    import sdcard
    import unicorn
    from unicorn.arm_const import UC_ARM_REG_R1, UC_ARM_REG_R2
    d = Device(a.ppf, snap)
    s, vp = d.s, d.vp
    ram = vp.profile['cfw_ram']
    s.run_ms(200)
    leds = {}

    def hook(mu, addr, size, ud):
        idx = mu.reg_read(UC_ARM_REG_R1)
        p = mu.reg_read(UC_ARM_REG_R2)
        leds[idx] = bytes(mu.mem_read(p, 3)).hex() if p else None
    fn = LED_SET[vp.profile['name']]
    vp.mu.hook_add(unicorn.UC_HOOK_CODE, hook, begin=fn, end=fn)

    flows.load_samples(s, [0])                       # break120.wav -> a new pool folder
    for c in (0, 2, 4):
        s.pad(0, c, hold_ms=40, after_ms=150)
    s.run_ms(300)
    check('White folder (default): trig pads in the stock trig colour', leds.get(0) == '557755' and leds.get(2) == '557755',
          f'{leds.get(0)} {leds.get(2)}')

    flows.open_menu(s)
    flows.enter_item(s, flows.MENU_SAMPLES)
    flows.enter_item(s, 0, after_ms=800)             # View Sample Pool
    s.turn(-8, after_ms=200)                          # the first folder row
    s.run_ms(300)
    check('pool view: our panel is up', vp.mu.mem_read(ram + VIEW + 0x29, 1)[0] == 4)
    s.shot(os.path.join(out, 'colors_1_pool.png'))
    s.tap(0, after_ms=300)                            # top key: palette
    s.turn(1, after_ms=300)                           # Red
    s.shot(os.path.join(out, 'colors_2_palette.png'))
    s.tap(6, after_ms=500)                            # set
    f = sdcard.read_file(vp.card.path, '/Projects/cfwColors')
    check('click sets the colour: /Projects/cfwColors names the folder with Red (1)',
          f is not None and len(f) >= 8 + 20 and f[:4] == b'1LOC' and f[8 + 17] == 1, repr(f[:32]) if f else 'no file')
    s.tap(0, after_ms=300)                            # palette again
    s.turn(2, after_ms=300)                           # Yellow (live)
    s.tap(7, after_ms=300)                            # Back: cancel
    f2 = sdcard.read_file(vp.card.path, '/Projects/cfwColors')
    check('Back cancels: still Red in the file', f2 is not None and f2[8 + 17] == 1)
    flows.close_menu(s)
    leds.clear()
    for c in (0, 2):                                  # refresh the pads: trigs off and on again
        s.pad(0, c, hold_ms=40, after_ms=150)
        s.pad(0, c, hold_ms=40, after_ms=150)
    s.pad(0, 4, hold_ms=40, after_ms=150)             # step 5 off -> empty
    s.run_ms(300)
    check('trig pads now red (a0 08 04)', leds.get(0) == 'a00804' and leds.get(2) == 'a00804', f'{leds.get(0)} {leds.get(2)}')
    check('an empty step stays stock (000000)', leds.get(4) == '000000', str(leds.get(4)))
    print(f'{sum(results)}/{len(results)} passed; screenshots in {out}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
