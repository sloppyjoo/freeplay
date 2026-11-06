#!/usr/bin/env python3
"""Sample Start / End editor on the knob-7 page, on the virtual Play+ / Play RT.

  0. fresh boot, no menu opened yet (the menu context is 0 then: the first CFW4 build crashed the
     real Play+ here): the boot page is on ui+4; knob 7 -> the panel shows (on ui+0), a trig and
     knob 7 again -> still up, the device keeps running,
  1. break120.wav (2.00 s) loaded, trig on step 1: knob 7 -> panel with the sample name, white key
     tiles "Start" (top, value "0 ms" next to it) and "End" (bottom, "2000 ms"); bar stays stock,
  2. the playhead starts at Start; encoder +32 detents moves it by 8224 (32 x 257); soft key "Start"
     (top-left key) -> the track's Start = playhead, panel "Start <ms>",
  3. Shift + encoder: zoom x2 shown; encoder +64 (129 per detent zoomed) -> soft key "End" -> End =
     playhead (bottom-left key); Shift kept through its "held" event; Play still plays and stops,
  4. Menu over it: panel gone, menu normal; closing it: panel back,
  5. another knob page without a CFW visual (Chance): panel gone, stock page and its widgets back,
  6. ext region missing (header damaged, cfw_ram reset as after a reboot): no panel, stock page.

usage: vdev_startend.py <patched.ppf> [--out DIR]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))
sys.path.insert(0, HERE)
from vdev_slice import Device, boot_snapshot  # noqa: E402
from vdev_sliceview import BOOT_MS, WAVES, make_wavs  # noqa: E402

SE_KNOB = 7
BTN_START, BTN_END, BTN_PLAY, BTN_SHIFT = 0, 3, 8, 10   # keys left of the screen: top / bottom
EXT_BASE = {'playplus-1.3.0': 0x60190B00, 'play-1.6.0-rt': 0x600D9100}
VIEW = 0x90                     # struct cfw_view inside cfw_ram (after header, snapshot)
SE_OBJ = VIEW + 0x1c
PTS = VIEW + 0x18               # struct cfw_pts * (heap): magic, u16 cursor, u16 view0, u8 zoom
WMODE = VIEW + 0x29
FRAMES, RATE = 88200, 44100


def bar(t):
    """the soft-key labels of the newest frame (the log may hold the frame before too)"""
    return [x for x in t if x in ('Live Rec', 'Undo', 'Play', 'Stop', 'Perform', 'Redo')][-3:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    ap.add_argument('--out')
    a = ap.parse_args()
    work = os.path.join(os.environ.setdefault('VPLAY_DIR', os.path.join('/tmp', 'vplay-' + str(os.getuid()))),
                        'startend')
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
    d = Device(a.ppf, snap)
    s, vp = d.s, d.vp
    ram = vp.profile['cfw_ram']
    panel = lambda: vp.mu.mem_read(ram + WMODE, 1)[0] == 2  # noqa: E731

    def page(knob):
        """show a knob's page by touching it (the value stays as it is)"""
        vp.eve.cmd_log.clear()
        s.touch(knob)
        s.run_ms(400)
        return d.texts()

    def param(idx):
        obj = vp.r32(ram + SE_OBJ)
        vp.w32(d.out_word, 0)
        ok = vp.call_fn(d.vfn(obj, 0x18), obj, idx, d.out_word) & 0xFF
        return vp.r32(d.out_word) & 0xffff if ok else None

    def panel_up():
        """our widget is up and visible and the page's own six widgets are hidden (the text log is
        no evidence: a widget whose content did not change is composed without re-sending its text)"""
        slot = vp.r32(ram + VIEW + 4)
        if not panel() or slot >= 50:
            return False
        tab = vp.r32(vp.r32(vp.r32(ui + 0x10) + 4) + 8)
        cur = vp.r32(ui + vp.profile['ui_cur'])
        stock = [vp.r32(vp.r32(tab + 4 * vp.r32(cur + 0xdc + 4 * k)) + 0xc) & 1 for k in range(6)]
        return vp.r32(vp.r32(tab + 4 * slot) + 0xc) & 1 == 1 and stock == [0] * 6

    # 0. fresh boot, no menu opened yet
    s.run_ms(200)
    ui = vp.r32(d.listener + 8)
    check('fresh boot: menu context still 0', vp.r32(vp.r32(ui + 0x10) + 0x34) == 0)
    cur = vp.r32(ui + vp.profile['ui_cur'])
    check('fresh boot: the boot page is on the second knob-page screen (ui+4), no panel',
          cur == vp.r32(ui + 4) and not panel(), f'cur {cur:#x} ui+4 {vp.r32(ui + 4):#x}')
    t = page(SE_KNOB)
    check('fresh boot, knob 7: panel shown ("No Sample")', panel_up() and 'Sample Start / End' in t, str(t[-6:]))
    s.pad(0, 0, hold_ms=40, after_ms=300)
    page(SE_KNOB)
    check('a trig, knob 7 again: panel still up, still running', panel_up())
    s.shot(os.path.join(out, 'startend_0_fresh.png'))
    s.pad(0, 0, hold_ms=40, after_ms=300)          # trig off again

    # 1. with a sample
    flows.load_samples(s, [0])                       # break120.wav -> pool slot 0
    s.pad(0, 0, hold_ms=40, after_ms=300)
    t = page(SE_KNOB)
    check('knob 7: panel with name, tiles Start / End, 0 ms / 2000 ms',
          panel() and d.tiles() == ['Start', 'End'] and all(x in t for x in ('break120', '0 ms', '2000 ms')),
          f'{d.tiles()} {t[-12:]}')
    check('bottom bar stays stock: Live Rec | Play | Perform', bar(t) == ['Live Rec', 'Play', 'Perform'], str(bar(t)))
    s.shot(os.path.join(out, 'startend_1_track.png'))

    def cursor():
        return int.from_bytes(vp.mu.mem_read(vp.r32(ram + PTS) + 4, 2), 'little')

    def ms(pos):
        return round(((pos * FRAMES) >> 15) * 1000 / RATE)

    # 2. playhead, Start
    c0 = cursor()
    check('the playhead starts at Start', c0 == (param(10) or 0), f'{c0} vs {param(10)}')
    vp.eve.cmd_log.clear()
    s.turn(32, after_ms=300)                         # 32 x 257 = 8224
    c1 = cursor()
    check(f'encoder moves the playhead by 8224 ({ms(c1)} ms shown)', c1 == c0 + 8224 and f'{ms(c1)} ms' in d.texts(),
          str(d.texts()[-6:]))
    s.tap(BTN_START, after_ms=400)
    st = param(10)
    t = d.texts()
    check(f'top key (Start tile): Start = playhead ({c1}, got {st}), "{ms(c1)} ms"', st == c1 and
          f'{ms(c1)} ms' in t, str(t[-6:]))
    s.shot(os.path.join(out, 'startend_2_start.png'))

    # 3. zoom, End
    vp.inputs.button(BTN_SHIFT, 0)
    s.run_ms(60)
    vp.inputs.button(BTN_SHIFT, 2)                   # the firmware's "held" event: Shift is still down
    s.run_ms(60)
    vp.eve.cmd_log.clear()
    s.turn(1, after_ms=300)
    vp.inputs.button(BTN_SHIFT, 1)
    s.run_ms(60)
    check('Shift (held) + encoder zooms (x2)', any('x2' in x for x in d.texts()), str(d.texts()[-6:]))
    s.turn(64, after_ms=300)                         # zoomed: 129 per detent -> + 8256
    c2 = cursor()
    s.tap(BTN_END, after_ms=400)
    en = param(11)
    check(f'bottom key (End tile): End = playhead ({c2} = {c1} + 8256, got {en})', c2 == c1 + 8256 and en == c2,
          str(d.texts()[-6:]))
    s.shot(os.path.join(out, 'startend_3_end.png'))
    seq = vp.r32(vp.profile['app'] + 0x10)
    playing = lambda: vp.call_fn(vp.r32(vp.r32(seq) + 0x28), seq) & 0xff  # noqa: E731
    check('Start / End keys did not start playback', not playing())
    import hle
    hle.enable_audio(vp)                             # playback runs on the audio clock
    s.tap(BTN_PLAY, hold_ms=40, after_ms=300)
    p1 = playing()
    s.tap(BTN_PLAY, hold_ms=40, after_ms=300)
    check('Play (middle key) still plays and stops on this page', p1 and not playing(), f'{p1} {playing()}')


    # 4. menu over it
    flows.open_menu(s)
    s.run_ms(300)
    check('Menu: panel gone', not panel())
    flows.close_menu(s)
    s.run_ms(400)
    check('menu closed: panel back', panel())

    # 5. another page (Chance: no CFW visual there, so the stock page itself must be back)
    t = page(4)
    check('Chance page: panel gone, stock page, bar Play / Perform', not panel() and 'Chance' in t and
          bar(t)[-2:] == ['Play', 'Perform'], str(t[-8:]))
    pscr = vp.r32(ui)
    disp = vp.r32(vp.r32(ui + 0x10) + 4)
    tab = vp.r32(disp + 8)
    vis = [vp.r32(vp.r32(tab + 4 * vp.r32(pscr + 0xdc + 4 * k)) + 0xc) & 1 for k in range(6)]
    check('stock page widgets visible again (not left hidden)', vis == [1] * 6, str(vis))
    s.shot(os.path.join(out, 'startend_5_chance.png'))

    # 6. ext region missing
    eb = EXT_BASE.get(vp.profile['name'])
    vp.mu.mem_write(eb, b'\xff' * 4)
    vp.w32(ram + 4, 0xFFFFFF00)                      # as after a reboot: cfw_ram reset
    s.run_ms(200)
    t = page(SE_KNOB)
    check('ext missing: no panel, stock Sample Start page', not panel() and 'Sample Start' in t, str(t[-8:]))
    print(f'{sum(results)}/{len(results)} passed; screenshots in {out}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
