#!/usr/bin/env python3
"""Slice > To Folder (slices as samples in a new pool folder) on the virtual Play+ / Play RT.

Boots with WAVs on the card, loads break120.wav (mono) and break_st.wav (stereo) into the pool,
places 16 trigs on track 1, holds them, and checks:
  * the Slice rows are just the six counts (Points / To Folder are on the keys left of the screen),
  * x4 highlighted, bottom key (To Folder): toast "break120 x4"; /Samples/break120 x4/01.wav .. 04.wav
    on the card (valid 16-bit WAVs); a pool folder "break120 x4" with 4 new samples whose PCM is
    exactly the 4 parts of the source (Start/End rule of Slice: position k * 32767 / 4),
  * stereo: the steps switched to the stereo sample, two points placed in the Points editor (top
    key), Shift + bottom key (To Folder): pool folder "break_st pts", 3 samples, both channels exact,
  * with the ext region's header damaged (as if the updater had not written the appended
    records), the bottom key does nothing (menu stays open, no folder) and nothing crashes.

usage: vdev_export.py <patched.ppf> [--out DIR]   (needs link.map next to the .ppf)
"""
import argparse
import os
import struct
import sys
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))
sys.path.insert(0, HERE)
from vdev_slice import Device, BTN_ENTER  # noqa: E402
from vdev_sliceview import BOOT_MS, WAVES, make_wavs  # noqa: E402

BTN_BACK = 7
BTN_CLICK = 6                   # encoder click (adds a point in the Points editor)
BTN_LTOP, BTN_LBOT, BTN_SHIFT = 0, 3, 10   # keys left of the screen: Points / To Folder
COUNTS = ['x4', 'x8', 'x16', 'x32', 'x64', '= Steps']
POS_MAX = 32767
EXT_BASE = {'playplus-1.3.0': 0x60190B00, 'play-1.6.0-rt': 0x600D9100}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    ap.add_argument('--out')
    a = ap.parse_args()
    work = os.path.join(os.environ.setdefault('VPLAY_DIR', os.path.join('/tmp', 'vplay-' + str(os.getuid()))),
                        'export')
    os.makedirs(work, exist_ok=True)
    os.environ['VPLAY_DIR'] = work
    wavdir = os.path.join(work, 'wavs')
    if not all(os.path.exists(os.path.join(wavdir, n)) for n in WAVES):
        make_wavs(wavdir)
    snap = os.path.join(work, os.path.basename(a.ppf) + '.wav.boot.snap')
    import sdcard
    card = os.path.join(work, 'sd.img')
    if os.path.exists(snap) and os.path.exists(card) and \
            sdcard.read_file(card, '/Samples/break120 x4/01.wav') is not None:
        os.remove(snap)  # a card with an earlier run's exports: the browser rows differ, start fresh
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
    d = Device(a.ppf, snap)
    s, vp = d.s, d.vp
    s.run_ms(200)
    flows.load_samples(s, [0, 1])                    # break120.wav -> slot 0, break_st.wav -> slot 1
    for c in range(16):
        s.pad(0, c, hold_ms=40, after_ms=60)

    def menu_ctx():
        scr = vp.r32(vp.r32(d.listener + 8) + 0x10)
        return vp.r32(scr + 0x34)

    def pool_of(ctx):
        return vp.r32(vp.r32(ctx + 4) + 0x418)

    def entry(pool, i):
        e = vp.r32(pool + 4) + i * 0x38
        b, en = vp.r32(e), vp.r32(e + 4)
        chans = [vp.r32(b + 4 * k) for k in range((en - b) // 4)]
        name = bytes(vp.mu.mem_read(e + 0x14, 36)).split(b'\0')[0].decode('latin1')
        return chans, vp.r32(e + 0xc), name

    def pcm(ptr, n):
        return bytes(vp.mu.mem_read(ptr, 2 * n))

    def folders(pool):
        meta = vp.r32(pool + 8)
        n = vp.r32(meta + 0x400)
        names = [bytes(vp.mu.mem_read(meta + 0x404 + 0x11 * k, 17)).split(b'\0')[0].decode('latin1') for k in range(n)]
        slot_folder = [vp.r32(meta + 4 * i) for i in range(256)]
        return names, slot_folder

    def slice_items():
        scr = vp.r32(vp.r32(d.listener + 8) + 0x10)
        sub = vp.r32(scr + 0x2c)
        cnt = vp.r32(sub + 0x0c)
        labels = vp.r32(sub + 0x34)
        out_ = []
        for k in range(cnt):
            p = vp.r32(labels + 4 * k)
            out_.append(bytes(vp.mu.mem_read(p, 24)).split(b'\0')[0].decode('latin1'))
        return out_

    def export(row, settle_ms=4000):
        """highlight count `row` in the Slice panel, then the bottom key left of the screen (To Folder)"""
        d.hold(0, range(16))
        d.open_slice_menu()
        s.turn(-8, after_ms=150)
        for _ in range(row):
            s.turn(1, after_ms=150)
        s.tap(BTN_LBOT, after_ms=400)                # Apply: the choice
        vp.eve.cmd_log.clear()
        s.tap(BTN_LTOP, after_ms=settle_ms)          # Folder
        d.release(0, range(16))
        s.run_ms(200)

    # --- 1. mono, x4 ------------------------------------------------------------------------------
    d.hold(0, range(16))
    d.open_slice_menu()
    ctx = menu_ctx()
    pool = pool_of(ctx)
    labels = slice_items()
    check('Slice rows: just the six counts', labels == COUNTS, str(labels))
    check('panel: key tiles Points / Apply', d.tiles() == ['Points', 'Apply'], str(d.tiles()))
    flows.close_menu(s)
    d.release(0, range(16))
    (src_ch, src_n, src_name) = entry(pool, 0)
    used_before = sum(1 for i in range(255) if vp.r32(vp.r32(pool + 4) + i * 0x38 + 4) != vp.r32(vp.r32(pool + 4) + i * 0x38))
    export(0)                                        # x4
    t = d.texts()
    s.shot(os.path.join(out, 'export_1_toast.png'))
    check('toast "break120 x4"', 'break120 x4' in t, str(t[:6]))
    path = vp.card.path
    files = [sdcard.read_file(path, f'/Samples/break120 x4/{k:02d}.wav') for k in range(1, 5)]
    check('card: /Samples/break120 x4/01..04.wav', all(f is not None for f in files), str([f is not None for f in files]))
    bounds = [0] + [k * POS_MAX // 4 for k in range(1, 4)] + [POS_MAX + 1]
    frames = [(bounds[k] * src_n) >> 15 for k in range(5)]
    ok_wav = True
    for k, f in enumerate(files):
        if f is None:
            ok_wav = False
            continue
        tmp = os.path.join(work, 'chk.wav')
        open(tmp, 'wb').write(f)
        with wave.open(tmp) as w:
            ok_wav &= w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == 44100
            ok_wav &= w.readframes(w.getnframes()) == pcm(src_ch[0] + 2 * frames[k], frames[k + 1] - frames[k])
    check('card WAVs: 16-bit mono 44.1 kHz, PCM = the 4 parts of the source', ok_wav)
    names, slot_folder = folders(pool)
    check('pool folder "break120 x4" exists', 'break120 x4' in names, str(names))
    fi = names.index('break120 x4') if 'break120 x4' in names else -1
    new = [i for i in range(255) if slot_folder[i] == fi and i not in (0, 1) and entry(pool, i)[1]]
    check('4 new pool samples in that folder', len(new) == 4, str(new))
    ok_pcm = len(new) == 4
    for k, i in enumerate(new[:4]):
        ch, n, nm = entry(pool, i)
        ok_pcm &= n == frames[k + 1] - frames[k] and pcm(ch[0], n) == pcm(src_ch[0] + 2 * frames[k], n)
    check('pool samples: lengths and PCM = the source parts, in order', ok_pcm,
          str([(entry(pool, i)[1], entry(pool, i)[2]) for i in new]))
    check('source sample untouched', entry(pool, 0)[1] == src_n and pcm(entry(pool, 0)[0][0], 64) == pcm(src_ch[0], 64))
    check('menu closed after export', not d.menu_is_open())
    got = [(d.param(0, c, 9), d.param(0, c, 10), d.param(0, c, 11)) for c in range(16)]
    check('steps now play the folder\'s slices in order (k mod 4), Start / End over the whole slice',
          len(new) == 4 and got == [(new[c % 4], 0, POS_MAX) for c in range(16)], str(got[:5]))
    for c in range(16):                              # back to the source sample for the next part
        st = d.step(0, c)
        vp.call_fn(d.vfn(st, 0x10), st, 9, 0)

    # --- 2. stereo, Points -------------------------------------------------------------------------
    for c in range(16):                              # the trigs now play the stereo sample (slot 1)
        st = d.step(0, c)
        vp.call_fn(d.vfn(st, 0x10), st, 9, 1)
    (sch, sn, sname) = entry(pool, 1)
    d.hold(0, range(16))
    d.open_slice_menu()
    s.tap(BTN_LTOP, after_ms=400)                    # Points editor
    s.tap(BTN_CLICK, after_ms=200)                   # encoder click: point at 16384
    s.turn(32, after_ms=200)                         # -> 24608
    s.tap(BTN_CLICK, after_ms=200)
    s.tap(BTN_LBOT, after_ms=400)                    # Apply: the choice
    vp.eve.cmd_log.clear()
    s.tap(BTN_LTOP, after_ms=4000)                   # Folder (the points)
    d.release(0, range(16))
    s.run_ms(200)
    t = d.texts()
    check('toast "break_st pts"', 'break_st pts' in t, str(t[:6]))
    names, slot_folder = folders(pool)
    fi = names.index('break_st pts') if 'break_st pts' in names else -1
    new = [i for i in range(255) if slot_folder[i] == fi and fi >= 0 and entry(pool, i)[1]]
    cuts = [0, 16384, 24608, POS_MAX + 1]
    fr = [(c * sn) >> 15 for c in cuts]
    # the Play+ keeps stereo files stereo; the original Play loads them as mono: same channels as the source
    ok = len(new) == 3
    for k, i in enumerate(new[:3]):
        ch, n, nm = entry(pool, i)
        ok = ok and len(ch) == len(sch) and n == fr[k + 1] - fr[k]
        ok = ok and all(pcm(ch[c], n) == pcm(sch[c] + 2 * fr[k], n) for c in range(len(sch)))
    check(f'Points export of break_st ({len(sch)} ch): 3 pool samples, every channel exact', ok,
          f'source {sname} {len(sch)} ch {sn} frames; new ' +
          str([(entry(pool, i)[2], entry(pool, i)[1], len(entry(pool, i)[0])) for i in new]))
    s.shot(os.path.join(out, 'export_2_stereo.png'))

    # --- 3. damaged ext region -> no "To Folder", no crash -------------------------------------
    eb = EXT_BASE.get(vp.profile['name'])
    if eb:
        vp.mu.mem_write(eb, b'\xff' * 4)
        vp.w32(vp.profile['cfw_ram'] + 4, 0xFFFFFF00)   # "reboot": cfw_ram (and its ext check cache) reset
        d.hold(0, range(16))
        d.open_slice_menu()
        n_before = len(folders(pool)[0])
        s.tap(BTN_LBOT, after_ms=400)                # Apply: the choice
        s.tap(BTN_LTOP, after_ms=1500)               # Folder: not available
        check('ext header damaged: Folder does nothing (menu open, no new folder)',
              d.menu_is_open() and len(folders(pool)[0]) == n_before, str(folders(pool)[0]))
        s.tap(BTN_BACK, after_ms=300)
        s.tap(BTN_LTOP, after_ms=300)
        check('ext header damaged: Points still opens', d.tiles() == ['Delete', 'Apply'], str(d.tiles()))
        s.turn(8, after_ms=300)
        flows.close_menu(s)
        d.release(0, range(16))
        s.run_ms(300)
        check('ext header damaged: still running', not vp.stopped if hasattr(vp, 'stopped') else True)
    print(f'{sum(results)}/{len(results)} passed; screenshots in {out}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
