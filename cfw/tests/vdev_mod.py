#!/usr/bin/env python3
"""The voice layer on the virtual Play+ / Play RT: moving playheads, Sustain / Release, Menu > LFO.

  1. Sample Start / End page while playing: an amber playhead moves through the waveform, and is gone
     once the voice has ended,
  2. Attack / Decay page: a playhead on the envelope. Holding Shift changes only the two text rows to
     Sustain / Release (the curve's vertices stay the same); keys pick, encoder and knob 12 set them,
  3. Sustain and Release change the audio as drawn, compared with a control pass,
  4. the settings reach /Projects/cfwMod.<project> a moment after the last change,
  5. Menu > LFO: tiles, scope, the cells; taking a cell and turning changes it; Shift + turn too,
  6. pick a track with its select pad, then Map held + touch or turn a knob maps it, the knob sets the amount, a touch
     frees it; the Matrix: one knob per cell, nothing else moves; LFO 1 on track 1's Filter and track 3's Pan: those
     live targets swing and the others stay; Start mapped: each note starts elsewhere,
  7. the LFO panel's Back leaves the menu; Slice panel and Points editor show the playhead too.
Screenshots go to --out.

usage: vdev_mod.py <patched.ppf> [--out DIR]   (needs link.map next to the .ppf)
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
from vdev_sliceview import BOOT_MS, WAVES, link_symbol, make_wavs  # noqa: E402

BTN_TOP, BTN_BOT, BTN_CLICK, BTN_BACK, BTN_PLAY, BTN_SHIFT = 0, 3, 6, 7, 8, 10
C_PLAY = 0xffb020
AUDIO = {'playplus-1.3.0': 0x2000281c, 'play-1.6.0-rt': 0x2000181c}
STRIDE = {'playplus-1.3.0': 0xbb4, 'play-1.6.0-rt': 0xba8}
RD_POS = {'playplus-1.3.0': 0x48, 'play-1.6.0-rt': 0x44}
RD_START = {'playplus-1.3.0': 0x2c, 'play-1.6.0-rt': 0x28}
PATMGR = {'playplus-1.3.0': 0x4430, 'play-1.6.0-rt': 0x2e60}
MIDI_ROW = {'playplus-1.3.0': 6, 'play-1.6.0-rt': 5}
LED_SET = {'playplus-1.3.0': 0x600ac580, 'play-1.6.0-rt': 0x60023811 - 1}
# struct mod_save
O_SUS, O_REL, O_LFO, O_AMT = 4, 12, 20, 36
SAVE_SIZE = 36 + 2 * 8 * 14


def amber_x(dl):
    """x of the amber vertical playhead lines in a display list"""
    out, on, line = [], False, False
    for i in range(len(dl) // 4):
        w = struct.unpack_from('<I', dl, 4 * i)[0]
        op = w >> 24
        if op == 0x04:
            on = (w & 0xffffff) == C_PLAY
        elif op == 0x1f:
            line = (w & 15) == 3
        elif w >> 30 == 1 and on and line:
            out.append(((w >> 15) & 0x7fff) / 16)
    return sorted(set(round(x) for x in out))


def strip_vertices(dl):
    """vertices of line strips (the envelope curve)"""
    out, strip = [], False
    for i in range(len(dl) // 4):
        w = struct.unpack_from('<I', dl, 4 * i)[0]
        if w >> 24 == 0x1f:
            strip = (w & 15) == 4
        elif w >> 30 == 1 and strip:
            out.append(w)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ppf')
    ap.add_argument('--out')
    a = ap.parse_args()
    work = os.path.join(os.environ.setdefault('VPLAY_DIR', os.path.join('/tmp', 'vplay-' + str(os.getuid()))), 'mod')
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
    sym_save = link_symbol(a.ppf, 'cfw_mod_save_data')
    sym_out = link_symbol(a.ppf, 'cfw_lfo_out')

    def boot():
        d = Device(a.ppf, snap)
        d.s.run_ms(200)
        flows.load_samples(d.s, [0])                 # break120.wav, 2.00 s
        d.s.pad(0, 0, hold_ms=40, after_ms=100)      # one trig on track 1, step 1
        hle.enable_audio(d.vp)
        return d

    def save_bytes(d):
        p = d.vp.call_fn(sym_save)
        return bytes(d.vp.mu.mem_read(p, SAVE_SIZE)), p

    def ctx(d, t=0):
        vp = d.vp
        seq = vp.r32(vp.profile['app'] + 0x10)
        buf = vp.call_fn(vp.r32(vp.r32(seq) + 8), seq)
        return buf + 0x98 + t * STRIDE[vp.profile['name']]

    def target(d, ident, t=0):
        vp = d.vp
        inst = vp.r32(ctx(d, t))
        return vp.call_fn(vp.r32(vp.r32(inst) + 0x18), inst, ident)

    def level(d, ms=200, every=20):
        vp, peak = d.vp, 0
        base = AUDIO[vp.profile['name']]
        for _ in range(ms // every):
            d.s.run_ms(every)
            b = struct.unpack('<512h', bytes(vp.mu.mem_read(base, 1024)))
            peak = max(peak, max(abs(x) for x in b))
        return peak

    # --- 1. Start / End: the playhead moves -------------------------------------------------------
    d = boot()
    s, vp = d.s, d.vp
    s.knob(7, 1, after_ms=300)
    s.tap(BTN_PLAY, hold_ms=40, after_ms=250)
    xs = []
    for k in range(4):
        s.run_ms(300)
        xs.append(amber_x(vp.eve.last_dl or b''))
        if k == 1:
            s.shot(os.path.join(out, 'mod_1_startend_playhead.png'))
    flat = [x[0] for x in xs if x]
    check('Start / End: one amber playhead in each frame', len(flat) == 4, str(xs))
    check('Start / End: it moves to the right', all(b > a for a, b in zip(flat, flat[1:])), str(flat))
    s.tap(BTN_PLAY, hold_ms=40, after_ms=2300)
    check('Start / End: gone when the voice has ended', amber_x(vp.eve.last_dl or b'') == [],
          str(amber_x(vp.eve.last_dl or b'')))

    # --- 2. Attack / Decay page ---------------------------------------------------------------------
    d = boot()
    s, vp = d.s, d.vp
    s.knob(12, 40, after_ms=300)                     # Attack up
    s.tap(BTN_BOT, after_ms=200)
    s.knob(12, 30, after_ms=300)                     # Decay up
    s.tap(BTN_TOP, after_ms=200)
    curve0 = strip_vertices(vp.eve.last_dl or b'')
    check('envelope: a curve is drawn', len(curve0) > 40, str(len(curve0)))
    s.shot(os.path.join(out, 'mod_2_env.png'))
    vp.inputs.button(BTN_SHIFT, 0)
    s.run_ms(60)
    vp.inputs.button(BTN_SHIFT, 2)
    vp.eve.cmd_log.clear()
    s.run_ms(200)
    t = d.texts()
    check('Shift: the rows read Sustain / Release', 'Sustain, track 1' in t and 'Release, track 1' in t and 'Off' in t, str(t[-8:]))
    check('Shift: the envelope itself does not change', strip_vertices(vp.eve.last_dl or b'') == curve0)
    s.shot(os.path.join(out, 'mod_2_env_shift.png'))
    s.turn(40, after_ms=200)                         # Sustain 40 %
    sv, _ = save_bytes(d)
    check('Shift + encoder sets Sustain (40 %)', sv[O_SUS] == 40, str(sv[O_SUS]))
    s.tap(BTN_BOT, after_ms=150)
    s.knob(12, 25, after_ms=200)                     # Release 25 %, with the knob
    sv, _ = save_bytes(d)
    check('Shift + bottom key, knob 12 sets Release (25 %)', sv[O_REL] == 25 and sv[O_SUS] == 40, f'{sv[O_REL]} {sv[O_SUS]}')
    vp.eve.cmd_log.clear()
    s.run_ms(150)
    check('the rows show the values', '40 %' in d.texts() and '25 %' in d.texts(), str(d.texts()[-6:]))
    curve1 = strip_vertices(vp.eve.last_dl or b'')
    check('the curve follows the values', curve1 != curve0)
    s.shot(os.path.join(out, 'mod_2_env_sr.png'))
    vp.inputs.button(BTN_SHIFT, 1)
    s.run_ms(200)
    s.tap(BTN_PLAY, hold_ms=40, after_ms=400)
    s.touch(12)
    s.run_ms(200)
    ax = amber_x(vp.eve.last_dl or b'')
    check('envelope: the playhead shows while it plays', len(ax) == 1, str(ax))
    s.shot(os.path.join(out, 'mod_2_env_playhead.png'))
    s.run_ms(300)
    ax2 = amber_x(vp.eve.last_dl or b'')
    check('envelope: the playhead moves', ax2 and ax and ax2[0] > ax[0], f'{ax} {ax2}')
    s.tap(BTN_PLAY, hold_ms=40, after_ms=2300)

    # --- 3. what it does to the sound -------------------------------------------------------------
    def peaks(sus, rel):
        d = boot()
        p = d.vp.call_fn(sym_save)
        d.vp.mu.mem_write(p + O_SUS, bytes([sus]))
        d.vp.mu.mem_write(p + O_REL, bytes([rel]))
        d.s.tap(BTN_PLAY, hold_ms=40, after_ms=10)
        lv = [level(d, 200) for _ in range(9)]       # 0 .. 1.8 s of the 2 s sample
        d.s.tap(BTN_PLAY, hold_ms=40, after_ms=100)
        return lv
    ctl, sus, rel = peaks(0, 0), peaks(30, 0), peaks(0, 50)
    print('levels', ctl, sus, rel, flush=True)
    check('control: the sample sounds to its end', min(ctl) > 200, str(ctl))
    # the gain goes through the firmware's volume curve, so 30 % sounds like the Volume knob at 30 %: about -26 dB
    check('Sustain 30 % holds a steady lower level', all(0.02 * c < x < 0.12 * c for c, x in zip(ctl[2:], sus[2:])), f'{ctl} {sus}')
    check('Release 50 %: first half as stock, last part fading', rel[1] > 0.8 * ctl[1] and rel[8] < 0.35 * ctl[8], f'{ctl} {rel}')

    # --- 4. the settings file --------------------------------------------------------------------
    d = boot()
    s, vp = d.s, d.vp
    p = vp.call_fn(sym_save)
    s.knob(12, 1, after_ms=200)
    vp.inputs.button(BTN_SHIFT, 0)
    s.run_ms(60)
    s.turn(7, after_ms=100)
    vp.inputs.button(BTN_SHIFT, 1)
    s.run_ms(2500)
    proj = vp.r32(vp.profile['app'] + PATMGR[vp.profile['name']])
    name = bytes(vp.mu.mem_read(proj + 0x2c, 32)).split(b'\0')[0].decode()
    names = f'project {name!r}'
    data = sdcard.read_file(vp.card.path, '/Projects/cfwMod.' + name)
    check('settings file written', data is not None and len(data) == SAVE_SIZE and data[O_SUS] == 7, f'{names}')

    # --- 5. Menu > LFO ----------------------------------------------------------------------------
    SEL_COL = 19
    F, P = 4, 1                                      # Filter, Pan in the table

    def open_lfo(d):
        d.s.tap(BTN_CLICK, after_ms=500)
        d.s.turn(-64, after_ms=300)
        d.s.turn(2, after_ms=200)                    # File, Samples, LFO
        d.s.tap(BTN_CLICK, after_ms=600)

    def amt(sv, l, t, i):
        return struct.unpack('b', sv[O_AMT + l * 112 + t * 14 + i:O_AMT + l * 112 + t * 14 + i + 1])[0]

    def sel_pad(row):
        s.pad(row, SEL_COL, hold_ms=40, after_ms=150)

    def map_hold(after):                             # hold Map, run after(), let go
        vp.inputs.button(BTN_BOT, 0)
        s.run_ms(80)
        vp.inputs.button(BTN_BOT, 2)
        s.run_ms(80)
        after()
        vp.inputs.button(BTN_BOT, 1)
        s.run_ms(200)

    d = boot()
    s, vp = d.s, d.vp
    sel_pad(2)                                       # track 3 selected with its select pad, before the panel
    open_lfo(d)
    check('Menu > LFO: panel with the tiles LFO 1 / Map', d.tiles() == ['LFO 1', 'Map'] and d.menu_is_open(), str(d.tiles()))
    s.shot(os.path.join(out, 'mod_5_lfo1.png'))
    sv, p = save_bytes(d)
    d0 = sv[O_LFO + 4]
    s.turn(2, after_ms=120)                          # Rate -> Shape -> Depth
    s.tap(BTN_CLICK, after_ms=120)                   # take it
    s.turn(-30, after_ms=120)
    s.tap(BTN_CLICK, after_ms=120)                   # let go
    s.turn(-1, after_ms=120)                         # back to Shape
    sv, _ = save_bytes(d)
    check('taken: the encoder changes Depth', sv[O_LFO + 4] == d0 - 30, f'{sv[O_LFO + 4]} {d0}')
    vp.inputs.button(BTN_SHIFT, 0)
    s.run_ms(60)
    s.turn(2, after_ms=120)                          # Shape +2 directly
    vp.inputs.button(BTN_SHIFT, 1)
    s.run_ms(60)
    sv, _ = save_bytes(d)
    check('Shift + encoder changes the highlighted cell', sv[O_LFO] == 2, str(sv[O_LFO]))
    s.tap(BTN_TOP, after_ms=200)
    check('top key: LFO 2', d.tiles() == ['LFO 2', 'Map'], str(d.tiles()))
    s.shot(os.path.join(out, 'mod_5_lfo2.png'))
    s.tap(BTN_TOP, after_ms=200)
    a0 = vp.call_fn(sym_out, 0)
    s.run_ms(400)
    a1 = vp.call_fn(sym_out, 0)
    check('the LFO moves with nothing playing (for the scope)', a0 != a1, f'{a0} {a1}')

    # the select pad of the track being edited blinks blue; the others don't
    leds = []

    def led_hit(vp):
        r = vp.mu.reg_read
        if r(A.UC_ARM_REG_R1) % 20 == SEL_COL:
            leds.append((r(A.UC_ARM_REG_R1) // 20, bytes(vp.mu.mem_read(r(A.UC_ARM_REG_R2), 3)) if r(A.UC_ARM_REG_R2) else b''))
    vp.hook_addr(LED_SET[vp.profile['name']], led_hit)
    s.run_ms(1100)
    blue = b'\x00\x28\xc0'
    on = [x for x in leds if x[1] == blue]
    check('the select pad of track 3 blinks blue', len(on) >= 2 and {x[0] for x in on} == {2} and any(x[0] == 2 and x[1] != blue for x in leds), str(leds[:10]))
    leds.clear()
    s.pad(5, SEL_COL, hold_ms=40, after_ms=700)      # track 6
    s.pad(2, SEL_COL, hold_ms=40, after_ms=100)      # and back to track 3
    check('another track: its pad blinks instead', any(x == (5, blue) for x in leds), str(leds[:10]))

    # --- 6. pick a track, then map: hold Map, touch or turn a knob ------------------------------------
    map_hold(lambda: s.touch(11, hold_ms=120, after_ms=150))
    sv, _ = save_bytes(d)
    check('a track selected before the panel is the one edited (track 3)', amt(sv, 0, 2, F) == 50 and amt(sv, 0, 0, F) == 0,
          f'{amt(sv, 0, 2, F)} {amt(sv, 0, 0, F)}')
    map_hold(lambda: s.touch(11, hold_ms=120, after_ms=150))
    sv, _ = save_bytes(d)
    check('Map held, touch a mapped knob and let go: freed', amt(sv, 0, 2, F) == 0, str(amt(sv, 0, 2, F)))
    sel_pad(0)                                       # track 1
    vp.inputs.button(BTN_BOT, 0)
    s.run_ms(80)
    vp.inputs.button(BTN_BOT, 2)
    s.run_ms(80)
    s.shot(os.path.join(out, 'mod_6_held.png'))
    vp.eve.cmd_log.clear()
    s.touch(11, hold_ms=120, after_ms=200)
    sv, p = save_bytes(d)
    check('Map held + touch the Filter knob: track 1 Filter, amount 50', amt(sv, 0, 0, F) == 50, str(amt(sv, 0, 0, F)))
    check('it says so', 'Filter: LFO 1' in d.texts(), str(d.texts()[:6]))
    s.knob(11, 20, after_ms=200)
    sv, _ = save_bytes(d)
    check('the knob goes on setting the amount', amt(sv, 0, 0, F) == 70, str(amt(sv, 0, 0, F)))
    s.shot(os.path.join(out, 'mod_6_held_filter.png'))
    vp.inputs.button(BTN_SHIFT, 0)
    s.run_ms(60)
    s.knob(11, 3, after_ms=200)                      # a slight turn, with Shift: its second parameter
    vp.inputs.button(BTN_SHIFT, 1)
    s.run_ms(60)
    sv, _ = save_bytes(d)
    check('Map + Shift + turn: Resonance, the knob\'s second parameter', amt(sv, 0, 0, 5) == 50 and amt(sv, 0, 0, F) == 70,
          f'{amt(sv, 0, 0, 5)} {amt(sv, 0, 0, F)}')
    s.knob(5, 1, after_ms=150)                       # Tempo: not a target
    sv2, _ = save_bytes(d)
    check('a knob with no target maps nothing', sv2[O_AMT:] == sv[O_AMT:])
    vp.inputs.button(BTN_BOT, 1)
    s.run_ms(200)
    check('Map used as a shortcut: the view stays', d.tiles() == ['LFO 1', 'Map'], str(d.tiles()))
    vp.mu.mem_write(p + O_AMT + 5, bytes([0]))       # Resonance off again

    # a tap on Map: the Matrix. The knobs are its own, the encoder does nothing
    s.tap(BTN_BOT, after_ms=250)
    check('a tap on Map opens the Matrix: tiles LFO 1 / Scope', d.tiles() == ['LFO 1', 'Scope'], str(d.tiles()))
    before = save_bytes(d)[0]
    s.turn(5, after_ms=150)
    check('Matrix: turning only moves between the cells', save_bytes(d)[0] == before)
    s.tap(BTN_CLICK, after_ms=120)                   # cell 5 (Pan) taken
    s.turn(7, after_ms=120)
    s.tap(BTN_CLICK, after_ms=120)                   # let go
    s.turn(1, after_ms=120)                          # moves on: Volume, unchanged
    sv, _ = save_bytes(d)
    check('Matrix: click, turn, click sets the cell with the encoder', amt(sv, 0, 0, P) == 7 and amt(sv, 0, 0, 0) == 0, f'{amt(sv, 0, 0, P)} {amt(sv, 0, 0, 0)}')
    s.turn(-1, after_ms=120)
    vp.inputs.button(BTN_SHIFT, 0)
    s.run_ms(60)
    s.tap(BTN_CLICK, after_ms=120)                   # Shift + click: back to 0
    vp.inputs.button(BTN_SHIFT, 1)
    s.run_ms(60)
    sv, _ = save_bytes(d)
    check('Matrix: Shift + click clears the cell', amt(sv, 0, 0, P) == 0, str(amt(sv, 0, 0, P)))
    s.shot(os.path.join(out, 'mod_6_matrix.png'))
    # the request: LFO 1 on track 1's Filter and track 3's Pan
    sel_pad(2)                                       # track 3
    s.knob(5, -30, after_ms=150)                     # knob 5: Pan
    sv, _ = save_bytes(d)
    check('LFO 1: track 1 Filter and track 3 Pan at once, each only there',
          amt(sv, 0, 0, F) == 70 and amt(sv, 0, 2, P) == -30 and amt(sv, 0, 2, F) == 0 and amt(sv, 0, 0, P) == 0,
          f'{amt(sv, 0, 0, F)} {amt(sv, 0, 2, P)} {amt(sv, 0, 2, F)} {amt(sv, 0, 0, P)}')
    s.shot(os.path.join(out, 'mod_6_matrix_t3.png'))
    # LFO 2 on the same parameter of another track
    s.tap(BTN_TOP, after_ms=200)
    s.knob(11, 40, after_ms=150)                     # LFO 2, track 3, Filter
    s.tap(BTN_TOP, after_ms=200)
    sv, _ = save_bytes(d)
    check('LFO 2 can have track 3 Filter as well', amt(sv, 1, 2, F) == 40 and amt(sv, 0, 0, F) == 70, f'{amt(sv, 1, 2, F)} {amt(sv, 0, 0, F)}')
    # several tracks: hold one select pad, tap another
    vp.inputs.pad(3, SEL_COL, 0)
    s.run_ms(80)
    s.pad(4, SEL_COL, hold_ms=40, after_ms=100)
    s.knob(1, 12, after_ms=150)                      # knob 1: Note
    vp.inputs.pad(3, SEL_COL, 1)
    s.run_ms(80)
    sv, _ = save_bytes(d)
    check('hold a select pad, tap another: the knob sets both', amt(sv, 0, 3, 2) == 12 and amt(sv, 0, 4, 2) == 12 and amt(sv, 0, 2, 2) == 0,
          f'{amt(sv, 0, 3, 2)} {amt(sv, 0, 4, 2)} {amt(sv, 0, 2, 2)}')
    vp.inputs.button(BTN_SHIFT, 0)
    s.run_ms(60)
    sel_pad(0)
    vp.inputs.button(BTN_SHIFT, 1)
    s.run_ms(60)
    s.knob(6, 9, after_ms=150)                       # knob 6: Volume
    sv, _ = save_bytes(d)
    check('Shift + select pad: all tracks', all(amt(sv, 0, t, 0) == 9 for t in range(8)), str([amt(sv, 0, t, 0) for t in range(8)]))
    vp.inputs.button(BTN_SHIFT, 0)
    s.run_ms(60)
    s.tap(BTN_CLICK, after_ms=150)                   # Shift + click: the cell touched last (Volume) back to 0, all tracks
    vp.inputs.button(BTN_SHIFT, 1)
    s.run_ms(60)
    sv, _ = save_bytes(d)
    check('Matrix: click clears the cell touched last', all(amt(sv, 0, t, 0) == 0 for t in range(8)) and amt(sv, 0, 3, 2) == 12,
          str([amt(sv, 0, t, 0) for t in range(8)]))
    sel_pad(0)
    tempo0 = bytes(vp.mu.mem_read(vp.r32(vp.profile['app'] + PATMGR[vp.profile['name']]) + 0x1f0, 4))
    s.knob(0, 10, after_ms=150)                      # knob 0: Microtune, not Master
    r0 = save_bytes(d)[0][O_LFO + 2]
    s.knob(14, 4, after_ms=150)                      # knob 14: the LFO's Rate
    sv, _ = save_bytes(d)
    check('every knob sets its own cell', amt(sv, 0, 0, 3) == 10 and sv[O_LFO + 2] == r0 + 4, f'{amt(sv, 0, 0, 3)} {sv[O_LFO + 2]} {r0}')
    tempo1 = bytes(vp.mu.mem_read(vp.r32(vp.profile['app'] + PATMGR[vp.profile['name']]) + 0x1f0, 4))
    check('the knobs\' regular functions stay out of it (Tempo unchanged)', tempo0 == tempo1)
    check('the panel stays up', d.tiles() == ['LFO 1', 'Scope'] and d.menu_is_open(), str(d.tiles()))
    s.shot(os.path.join(out, 'mod_6_matrix_set.png'))
    vp.mu.mem_write(p + O_AMT, bytes(224))           # clean up for the sound checks
    s.tap(BTN_BOT, after_ms=200)                     # the LFO view again
    check('bottom key: back to the LFO view', d.tiles() == ['LFO 1', 'Map'], str(d.tiles()))
    s.shot(os.path.join(out, 'mod_6_lfo_after.png'))
    # what it does: a sine, track 1's Filter and track 3's Pan
    vp.mu.mem_write(p + O_LFO, bytes([0, 0, 40, 5, 100, 0, 0, 0]))  # Sine, free, 1.6 Hz
    vp.mu.mem_write(p + O_AMT + 0 * 14 + F, bytes([100]))
    vp.mu.mem_write(p + O_AMT + 2 * 14 + P, bytes([100]))
    s.pad(1, 2, hold_ms=40, after_ms=100)            # trigs on tracks 2 and 3 as well
    s.pad(2, 2, hold_ms=40, after_ms=100)
    s.tap(BTN_PLAY, hold_ms=40, after_ms=300)

    def s16(a):
        return struct.unpack('<h', bytes(vp.mu.mem_read(a + 2, 2)))[0]
    c3 = ctx(d, 2)
    mix, slot = vp.r32(c3 + 4), vp.r32(c3 + 8)
    tg, tg3 = target(d, 1), target(d, 1, 2)
    pan3 = vp.call_fn(vp.r32(vp.r32(mix) + 0x10), mix, slot, 1)
    pan1 = vp.call_fn(vp.r32(vp.r32(mix) + 0x10), mix, vp.r32(ctx(d, 0) + 8), 1)
    vals, f3, p3, p1 = [], [], [], []
    for _ in range(40):
        s.run_ms(30)
        vals.append(s16(tg))
        f3.append(s16(tg3))
        p3.append(s16(pan3))
        p1.append(s16(pan1))
    print('filter values', vals[:12], flush=True)
    check('track 1 Filter swings both ways', max(vals) > 2500 and min(vals) < -2500, f'{min(vals)} {max(vals)}')
    check('track 3 Pan swings both ways', max(p3) > 2500 and min(p3) < -2500, f'{min(p3)} {max(p3)}')
    check('track 3 Filter and track 1 Pan stay', set(f3) == {0} and set(p1) == {0}, f'{set(f3)} {set(p1)}')
    s.shot(os.path.join(out, 'mod_6_live.png'))
    vp.mu.mem_write(p + O_AMT + F, bytes([0]))       # unmapped
    s.run_ms(100)
    check('unmapped: the base value is back', s16(tg) == 0, str(s16(tg)))
    # Sample Start mapped: read at note on
    vp.mu.mem_write(p + O_AMT + 10, bytes([100]))
    vp.mu.mem_write(p + O_LFO, bytes([0, 0, 22, 5, 100, 0, 0, 0]))
    inst = vp.r32(ctx(d))
    rd = inst + 0xd8
    starts = set()
    for _ in range(5):
        s.run_ms(2000)
        starts.add(vp.r32(rd + RD_START[vp.profile['name']]))
    check('Sample Start mapped: notes start at different places', len(starts) >= 3, str(starts))
    vp.mu.mem_write(p + O_AMT + 10, bytes([0]))
    s.tap(BTN_PLAY, hold_ms=40, after_ms=100)
    # --- 7. leave ------------------------------------------------------------------------------------
    s.tap(BTN_BACK, after_ms=500)
    s.shot(os.path.join(out, 'mod_7_back.png'))
    check('Back leaves the LFO panel: no key tiles over the menu list', d.tiles() == [] and d.menu_is_open(), str(d.tiles()))
    # Menu > Matrix, right under LFO; Back leaves it for the menu
    s.turn(1, after_ms=200)
    s.tap(BTN_CLICK, after_ms=600)
    check('Menu > Matrix: opens the Matrix', d.tiles() == ['LFO 1', 'Scope'], str(d.tiles()))
    s.tap(BTN_BACK, after_ms=500)
    check('Back leaves the Matrix for the menu', d.tiles() == [] and d.menu_is_open(), str(d.tiles()))
    leds.clear()
    s.run_ms(800)
    check('out of the panel the select pads stop blinking', not any(x[1] == blue for x in leds), str(leds[:8]))

    # --- 7b. a parameter an LFO moves is seen moving on its knob page ------------------------------------
    d = boot()
    s, vp = d.s, d.vp
    _, p = save_bytes(d)
    vp.mu.mem_write(p + O_LFO, bytes([0, 0, 40, 5, 100, 0, 0, 0]))   # Sine, 1.6 Hz
    s.tap(BTN_PLAY, hold_ms=40, after_ms=200)
    s.knob(11, 1, after_ms=300)                      # the Filter page; nothing mapped: still
    c0 = strip_vertices(vp.eve.last_dl or b'')
    s.run_ms(300)
    check('Filter page, no LFO: the curve is still', strip_vertices(vp.eve.last_dl or b'') == c0)
    vp.mu.mem_write(p + O_AMT + F, bytes([80]))      # LFO 1, track 1, Filter
    frames = []
    for _ in range(6):
        s.run_ms(120)
        frames.append(strip_vertices(vp.eve.last_dl or b''))
    check('with an LFO on track 1 Filter the curve moves', len({tuple(f) for f in frames}) >= 4, str(len({tuple(f) for f in frames})))
    vp.eve.cmd_log.clear()
    s.run_ms(100)
    check('the row says LFO', 'LFO' in d.texts(), str(d.texts()[-6:]))
    s.shot(os.path.join(out, 'mod_7b_filter.png'))
    vp.mu.mem_write(p + O_AMT + F, bytes([0]))
    vp.mu.mem_write(p + O_AMT + 9, bytes([80]))      # Reverb
    s.knob(3, 1, after_ms=300)
    bars = []
    for _ in range(6):
        s.run_ms(120)
        bars.append(bytes(vp.eve.last_dl or b'')[:4096])
    check('Reverb page with an LFO: its bar moves', len(set(bars)) >= 3, str(len(set(bars))))
    s.shot(os.path.join(out, 'mod_7b_reverb.png'))
    vp.mu.mem_write(p + O_AMT + 9, bytes([0]))
    s.tap(BTN_PLAY, hold_ms=40, after_ms=200)

    # --- 8. CC presets: MIDI > CC Mapping > Jack Channel 1 ---------------------------------------------
    d = boot()
    s, vp = d.s, d.vp
    proj = vp.r32(vp.profile['app'] + PATMGR[vp.profile['name']])

    def cc(ch=0):
        return list(vp.mu.mem_read(proj + 0x201 + ch * 8, 8))

    def open_channel(ch=0):
        s.tap(BTN_CLICK, after_ms=500)                 # Menu
        s.turn(-64, after_ms=200)
        s.turn(MIDI_ROW[vp.profile['name']], after_ms=200)
        s.tap(BTN_CLICK, after_ms=400)                 # MIDI
        s.turn(-64, after_ms=200)
        s.tap(BTN_CLICK, after_ms=400)                 # CC Mapping
        s.turn(-64, after_ms=200)
        s.turn(ch, after_ms=200)
        s.tap(BTN_CLICK, after_ms=400)                 # the channel

    before = cc()
    open_channel()
    s.shot(os.path.join(out, 'mod_8_cc_rows.png'))
    s.turn(-64, after_ms=150)
    s.tap(BTN_CLICK, after_ms=150)                     # the stock row: its value
    s.turn(3, after_ms=150)
    s.tap(BTN_BACK, after_ms=200)
    changed = cc()
    check('the stock row edits project + 0x201: first of the 8', changed[0] != before[0] and changed[1:] == before[1:], f'{before} {changed}')
    # 8 CC rows, stock's "Set values for Polyend Tracker", then Load Preset, Save Preset
    s.turn(-64, after_ms=150)
    s.turn(9, after_ms=150)
    vp.eve.cmd_log.clear()
    s.tap(BTN_CLICK, after_ms=500)
    check('Load Preset lists the synths', 'Korg minilogue xd' in d.texts(), str(d.texts()[-8:]))
    s.shot(os.path.join(out, 'mod_8_cc_list.png'))
    s.turn(15, after_ms=200)                           # Sequential Prophet-6, the 16th
    vp.eve.cmd_log.clear()
    s.tap(BTN_CLICK, after_ms=300)
    check('Prophet-6 loaded', cc() == [102, 103, 43, 44, 51, 47, 9, 106], str(cc()))
    check('toast', 'Loaded Sequential Prophet-6' in d.texts(), str(d.texts()[:6]))
    # Save Preset, User 3
    s.turn(-64, after_ms=150)
    s.turn(10, after_ms=150)
    s.tap(BTN_CLICK, after_ms=400)
    s.turn(2, after_ms=150)
    s.tap(BTN_CLICK, after_ms=500)
    data = sdcard.read_file(vp.card.path, '/Projects/cfwCC')
    check('Save Preset: User 3 in /Projects/cfwCC', data is not None and data[4] == 4 and list(data[8 + 16:8 + 24]) == [102, 103, 43, 44, 51, 47, 9, 106],
          str(data[:32] if data else None))
    # a synth with slots it leaves alone: TB-03 keeps slots 3 and 5. The list now starts with User 3
    s.turn(-64, after_ms=150)
    s.turn(9, after_ms=150)
    s.tap(BTN_CLICK, after_ms=400)
    s.turn(-64, after_ms=200)
    s.turn(31, after_ms=200)                           # the 31st synth, after User 3
    vp.eve.cmd_log.clear()
    s.tap(BTN_CLICK, after_ms=300)
    check('TB-03 leaves the slots it has no control for', cc() == [74, 71, 43, 75, 51, 19, 17, 18], str(cc()))
    s.turn(-64, after_ms=150)
    s.turn(9, after_ms=150)
    s.tap(BTN_CLICK, after_ms=400)
    s.turn(-64, after_ms=200)
    s.tap(BTN_CLICK, after_ms=500)                     # first row: User 3
    check('Load Preset: the saved one comes first and loads', cc() == [102, 103, 43, 44, 51, 47, 9, 106], str(cc()))

    print(f'{sum(results)}/{len(results)} passed; screenshots in {out}')
    return 0 if all(results) else 1


if __name__ == '__main__':
    sys.exit(main())
