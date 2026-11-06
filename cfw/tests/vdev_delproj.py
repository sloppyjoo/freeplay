#!/usr/bin/env python3
"""Menu > File > Delete Project on the virtual Play+ / Play RT, on a card with two saved projects.

The card is made with /Projects/Alpha and /Projects/Beta (files and a patterns/ subfolder each).
  1. File > Delete Project lists Alpha and Beta (not globalSettings, not cfw files)
  2. Alpha once: "Again to delete Alpha", nothing deleted; again within 3 s: "Deleted Alpha",
     /Projects/Alpha gone (with its subfolder), Beta and /workspace untouched, menu back in File
  3. Beta once, wait 3.5 s, Beta again: nothing deleted (it re-arms); once more: Beta deleted
  4. the card passes fsck_msdos -n (macOS) / fsck.fat -n (Linux) when available

usage: vdev_delproj.py <patched.ppf> [--snap BOOT.snap] [--out DIR]   (exit status 0 = all pass)
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))

from vdev_slice import BTN_MENU, BOOT_MS, Device  # noqa: E402

FILES = {
    '/Projects/Alpha/settings': b'A' * 300,
    '/Projects/Alpha/patterns/1.pattern': b'P' * 5000,
    '/Projects/Alpha/patterns/2.pattern': b'Q' * 700,
    '/Projects/Beta/settings': b'B' * 300,
    '/Projects/Beta/patterns/1.pattern': b'R' * 4000,
}


def fsck(img):
    for tool, args in (('fsck_msdos', ['-n']), ('fsck.fat', ['-n'])):
        if shutil.which(tool):
            r = subprocess.run([tool] + args + [img], capture_output=True, text=True)
            return r.returncode == 0, (r.stdout + r.stderr)[-400:]
    return None, 'no fsck tool'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    ap.add_argument('--snap')
    ap.add_argument('--out')
    a = ap.parse_args()
    work = os.path.join(os.environ.setdefault('VPLAY_DIR', os.path.join('/tmp', 'vplay-' + str(os.getuid()))),
                        'delproj')
    os.makedirs(work, exist_ok=True)
    os.environ['VPLAY_DIR'] = work          # own card image
    snap = a.snap or os.path.join(work, os.path.basename(a.ppf) + '.proj.boot.snap')
    if not os.path.exists(snap):
        import stage
        print(f'booting {os.path.basename(a.ppf)} with two saved projects (one-off, a few minutes) ...', flush=True)
        stage.stage(a.ppf, None, snap, insns=BOOT_MS * 100_000, seconds=3600, quiet=True, sd_files=FILES)
    out = a.out or os.path.join(work, 'shots')
    os.makedirs(out, exist_ok=True)
    results = []

    def check(name, ok, detail=''):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f': {detail}' if detail and not ok else ''))

    import flows
    d = Device(a.ppf, snap)
    s, vp = d.s, d.vp
    card = vp.card.path

    def ls(p):
        fs = __import__('pyfat').Fat32(card)
        try:
            return [n for n, _, _ in fs.listdir(p)]
        finally:
            fs.close()

    s.run_ms(200)
    check('card has Alpha and Beta', {'Alpha', 'Beta'} <= set(ls('/Projects')), str(ls('/Projects')))

    def open_list():
        flows.close_menu(s)                          # Menu inside a menu is an enter key
        vp.eve.cmd_log.clear()
        s.tap(BTN_MENU, after_ms=500)
        flows.enter_item(s, 0)                       # File
        scr = vp.r32(vp.r32(d.listener + 8) + 0x10)
        n = vp.r32(vp.r32(scr + 0x2c) + 0x0c)
        vp.eve.cmd_log.clear()
        flows.enter_item(s, n - 1)                   # Delete Project (last)
        return d.texts()

    t = open_list()
    s.shot(os.path.join(out, 'delproj_1_list.png'))
    check('list shows Alpha and Beta only', 'Alpha' in t and 'Beta' in t and 'globalSettings' not in t and
          'cfwSettings' not in t, str(t[-8:]))
    rows = [x for x in ('Alpha', 'Beta')]
    idx = {n: i for i, n in enumerate(sorted(rows, key=lambda n: t.index(n)))}
    # 2. Alpha: once, then again
    vp.eve.cmd_log.clear()
    flows.enter_item(s, idx['Alpha'], after_ms=200)
    check('first press: "Again to delete Alpha"', 'Again to delete Alpha' in d.texts(), str(d.texts()[:6]))
    check('first press deletes nothing', 'Alpha' in ls('/Projects'))
    vp.eve.cmd_log.clear()
    s.tap(flows.BTN_ENTER, after_ms=800)
    s.shot(os.path.join(out, 'delproj_2_deleted.png'))
    check('second press: "Deleted Alpha"', 'Deleted Alpha' in d.texts(), str(d.texts()[:6]))
    left = ls('/Projects')
    check('/Projects/Alpha gone', 'Alpha' not in left, str(left))
    check('Beta untouched', 'Beta' in left and ls('/Projects/Beta/patterns') == ['1.pattern'], str(left))
    check('workspace untouched', 'workspace' in ls('/') and len(ls('/workspace')) > 0, str(ls('/workspace')))
    # 3. Beta: once, wait past the window, once more -> armed again only
    t = open_list()
    check('list now shows Beta only', 'Beta' in t and 'Alpha' not in t, str(t[-8:]))
    vp.eve.cmd_log.clear()
    flows.enter_item(s, 0, after_ms=3500)
    s.tap(flows.BTN_ENTER, after_ms=300)
    check('press after 3.5 s does not delete', 'Beta' in ls('/Projects'), str(ls('/Projects')))
    vp.eve.cmd_log.clear()
    s.tap(flows.BTN_ENTER, after_ms=800)           # the late press re-armed: this one deletes
    check('the late press re-armed: next press deletes Beta', 'Beta' not in ls('/Projects') and
          'Deleted Beta' in d.texts(), str(ls('/Projects')) + str(d.texts()[:4]))
    flows.close_menu(s)
    # 4. filesystem check
    ok, log = fsck(card)
    check('card passes fsck', ok is not False, log)
    print(f'{sum(results)}/{len(results)} passed; screenshots in {out}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
