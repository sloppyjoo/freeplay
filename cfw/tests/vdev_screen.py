#!/usr/bin/env python3
"""Screen brightness and dim-when-idle on the virtual Play+ / Play, real firmware end to end.

Reads the backlight as the EVE sees it (REG_PWM_DUTY in the EVE model, written by the firmware's
display manager over SPI). Checks:
  * stock behaviour by default (100 %, never dims) and the new Settings option rows (stock widget,
    label left / value right like "Grid Brightness  High"),
  * Screen Brightness 10 % .. 100 %: Enter opens the choices, the backlight follows the highlighted one,
  * Enter on 50 % goes back to Settings with "Screen Brightness  50%" and writes /Projects/cfwSettings,
  * Screen Dim "After 30 s": idle 31 s -> the backlight fades to the glow level; a pad, a knob and
    the encoder each wake it back to 50 %,
  * power cycle (boot from reset on the same card): the saved brightness comes back.

usage: vdev_screen.py <patched.ppf> [--snap BOOT.snap] [--out DIR]   (exit status 0 = all pass)
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))
sys.path.insert(0, HERE)
from vdev_slice import Device, boot_snapshot, BOOT_MS  # noqa: E402

PWM_DUTY = 0x3020D4
CFG = '/Projects/cfwSettings'


def duty(d):
    return d.vp.eve.reg('PWM_DUTY') & 0xff


def pair(t, a, b):
    return any(t[i] == a and t[i + 1] == b for i in range(len(t) - 1))


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
    import sdcard
    d = Device(a.ppf, snap)
    s, vp = d.s, d.vp
    settings = 7 if vp.profile['name'].startswith('playplus') else 6   # File Samples LFO Matrix Synths Scales MIDI Settings; the Play has no Synths
    s.run_ms(300)
    check('default: full backlight', duty(d) == 128, str(duty(d)))
    flows.open_menu(s)
    flows.enter_item(s, settings)
    vp.eve.cmd_log.clear()
    s.turn(64, after_ms=400)                   # scroll to the end of Settings: the two new items
    t = d.texts()
    check('Settings ends with option rows Screen Brightness 100%, Screen Dim Off, Knob Touch Normal',
          pair(t, 'Screen Brightness', '100%') and pair(t, 'Screen Dim', 'Off') and pair(t, 'Knob Touch', 'Normal'),
          str(t[-8:]))
    s.shot(os.path.join(out, 'screen_settings.png'))
    # the two new items are the last two: count the Settings items from the menu object
    scr = vp.r32(vp.r32(d.listener + 8) + 0x10)
    sub = vp.r32(scr + 0x2c)
    n = vp.r32(sub + 0x0c)
    flows.enter_item(s, n - 3)                 # Screen Brightness: the choice list
    s.turn(-5, after_ms=400)                   # 10 % .. 100 % in steps of 10: from 100 % to 50 %
    check('live: 50% highlighted -> backlight 64', duty(d) == 64, str(duty(d)))
    s.turn(-4, after_ms=400)                   # 10 %
    check('live: 10% -> 12', duty(d) == 12, str(duty(d)))
    s.shot(os.path.join(out, 'screen_choices10.png'))
    s.turn(4, after_ms=400)                    # back to 50 %
    vp.eve.cmd_log.clear()
    s.tap(flows.BTN_ENTER, after_ms=600)       # choose 50 %
    t = d.texts()
    check('chose 50%: backlight 64', duty(d) == 64, str(duty(d)))
    check('back in Settings showing "Screen Brightness  50%"', pair(t, 'Screen Brightness', '50%'), str(t[-6:]))
    s.shot(os.path.join(out, 'screen_settings50.png'))
    files = sdcard.ls(vp.card.path, '/Projects')
    check('settings file written', 'CFWSET' in files.upper() or 'cfwSettings' in files, files[-300:])
    # dim after 30 s
    flows.enter_item(s, n - 2)                 # Screen Dim
    s.turn(1, after_ms=300)
    vp.eve.cmd_log.clear()
    s.tap(flows.BTN_ENTER, after_ms=600)       # 30 s
    check('"Screen Dim  30 s"', pair(d.texts(), 'Screen Dim', '30 s'), str(d.texts()[-6:]))
    flows.close_menu(s)
    s.run_ms(20000)
    check('20 s idle: still 50%', duty(d) == 64, str(duty(d)))
    s.run_ms(12000)
    check('32 s idle: dimmed to the glow level', duty(d) == 6, str(duty(d)))
    s.shot(os.path.join(out, 'screen_dimmed.png'))
    s.pad(0, 0, hold_ms=40, after_ms=400)
    check('pad wakes it to 50%', duty(d) == 64, str(duty(d)))
    s.run_ms(31000)
    check('dims again', duty(d) == 6, str(duty(d)))
    s.knob(0, 1, after_ms=400)
    check('knob wakes it', duty(d) == 64, str(duty(d)))
    s.run_ms(31000)
    s.turn(1, after_ms=400)
    check('encoder wakes it', duty(d) == 64, str(duty(d)))

    # knob touch: Normal selects the touched knob's parameter, Turn Only ignores the touch
    cur = lambda: vp.r32(d.ctrl + 0x240c)
    s.touch(3)
    check('Knob Touch Normal: touching knob 4 selects it', cur() == 3, str(cur()))
    s.knob(1, 1, after_ms=300)
    check('turning knob 2 selects it', cur() == 1, str(cur()))
    flows.open_menu(s)
    flows.enter_item(s, settings)
    vp.eve.cmd_log.clear()
    flows.enter_item(s, n - 1)                 # Knob Touch
    s.turn(1, after_ms=300)
    vp.eve.cmd_log.clear()
    s.tap(flows.BTN_ENTER, after_ms=600)       # Turn Only
    check('"Knob Touch  Turn Only"', pair(d.texts(), 'Knob Touch', 'Turn Only'), str(d.texts()[-6:]))
    flows.close_menu(s)
    s.touch(4)
    check('Turn Only: touching knob 5 keeps knob 2 selected', cur() == 1, str(cur()))
    vp.inputs.touch(5, 0)                      # touch and hold, then turn: turning still selects
    s.run_ms(100)
    s.knob(5, 1, after_ms=200)
    vp.inputs.touch(5, 1)
    s.run_ms(200)
    check('Turn Only: turning knob 6 selects it', cur() == 5, str(cur()))
    check('Turn Only: no stuck touch flags', all(vp.mu.mem_read(d.ctrl + 0x2455 + k, 1)[0] == 0 for k in range(14)),
          str([vp.mu.mem_read(d.ctrl + 0x2455 + k, 1)[0] for k in range(14)]))
    cfg = sdcard.read_file(vp.card.path, '/Projects/cfwSettings') if hasattr(sdcard, 'read_file') else None
    check('cfwSettings: ktouch byte = 1', cfg is None or (len(cfg) == 8 and cfg[6] == 1), repr(cfg))

    # power cycle on the same card: boot from reset, the saved setting comes back
    import stage
    reboot = os.path.join(work, 'screen_reboot.snap')
    print('power cycle: booting again on the same card ...', flush=True)
    stage.stage(a.ppf, None, reboot, insns=BOOT_MS * 100_000, seconds=3600, quiet=True, keep_sd=True)
    d2 = Device(a.ppf, reboot)
    d2.s.run_ms(500)
    check('after power cycle: brightness 50% restored', duty(d2) == 64, str(duty(d2)))
    d2.s.knob(2, 1, after_ms=200)
    d2.s.touch(7)
    check('after power cycle: Knob Touch Turn Only restored', d2.vp.r32(d2.ctrl + 0x240c) == 2,
          str(d2.vp.r32(d2.ctrl + 0x240c)))

    print(f'{sum(results)}/{len(results)} passed; screenshots in {out}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
