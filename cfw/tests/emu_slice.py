#!/usr/bin/env python3
"""Emulated end-to-end tests of the slice CFW against the patched firmware image.

Runs, in Unicorn, the REAL code of the patched image for:
  * the root menu static constructor (shows the patched builder literal is picked up),
  * menu population through the stock pointer-to-member dispatcher FUN_60104fbc,
  * stock + CFW builders, stock submenu/action constructors and their vector growth,
  * the stock std::function manager (clone) and the action invoker, like FUN_601053a8,
  * engine track getStep and step isOn/setParam/getParam/trackType methods.
Stubs (recorded): operator new/delete, atexit, undo snapshot, toast, menu close, the two NVIC
helpers, and the fake controller objects that only exist at runtime on the device.

usage: emu_slice.py <patched.ppf> <target.json> <image-name>   (exit status 0 = all pass)
"""
import json
import os
import struct
import sys

from unicorn import arm_const as UA

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
from emu import Emu, EmuError  # noqa: E402
from link import load_symbols  # noqa: E402
from ppf import PPF  # noqa: E402

PROFILES = {
    'playplus-1.3.0:rt1062': dict(
        ui_cur=0x300, ui_toast=0x148,
        image_base=0x60030000,
        mirrors=[(0x8, 0x6017625C, 0x144F0), (0x20000000, 0x6018A74C, 0x22E8)],
        ram=[(0x0, 0x20000), (0x20000000, 0x60000)],
        root_ctor=0x600c97c5, root_item=0x2001dc04,
        populate=0x60104fbd,
        stub_new=[0x600aab65, 0x600ad665], stub_delete=[0x601546d9, 0x601509ed],
        stub_atexit=0x600aab75, stub_throw=0x6016041f,
        track_vtable=0x20002104, step_vt_sample=0x20001ddc, step_vt_synth=0x20001d74,
        track_size=0xBB4, step_base=0x134, step_size=0x2A,
        param_offsets=0x20035ca0, param_desc=0x20001bb8, n_params=22,
        n_tracks=16, n_steps=64, sample_tracks=range(0, 8),
        root_labels=['File', 'Samples', 'Synths', 'Scales', 'MIDI', 'Settings'],
        samples_labels=['View Sample Pool', 'Load Sample Pack', 'Load Files or Folders',
                        'Delete Unused Samples', 'Slice'],
        version=(0x600c83ed, '1.3.0-cfw1'),
    ),
    'play-1.6.0:rt1062': dict(
        ui_cur=0x2fc, ui_toast=0x144,
        image_base=0x60020000,
        mirrors=[(0x20000000, 0x600D3BE4, 0x1478)],
        ram=[(0x0, 0x20000), (0x20000000, 0x60000)],
        root_ctor=0x6005c479, root_item=0x2000e428,
        populate=0x60069b55,
        stub_new=[0x60021df5, 0x60021df9, 0x60024895], stub_delete=[0x60021dfd, 0x60021e01, 0x60077889],
        stub_atexit=0x60021e05, stub_throw=0x6007ac77,
        track_vtable=0x600cf47c, step_vt_sample=0x600cfa24, step_vt_synth=0x600cf9bc,
        track_size=0xBB4, step_base=0x128, step_size=0x2A,
        param_offsets=0x20026330, param_desc=0x600cf800, n_params=22,
        n_tracks=16, n_steps=64, sample_tracks=range(0, 8),
        root_labels=['File', 'Samples', 'Scales', 'MIDI', 'Settings'],
        samples_labels=['View Sample Pool', 'Load Sample Pack', 'Load Files or Folders',
                        'Delete Unused Samples', 'Slice'],
        version=(0x6003fa41, '1.6.0-cfw1'),
    ),
    'play-1.6.0:k66': dict(
        cpu=UA.UC_CPU_ARM_CORTEX_M4, stack_top=0x2002F000, flash_end=0x00100000,
        ui_cur=0x2fc, ui_toast=0x144,
        image_base=0x0,
        mirrors=[(0x1fff1ca0, 0xaec30, 0x1a6c)],
        ram=[(0x1FFF0000, 0x40000)],
        root_ctor=0x367c9, root_item=0x20001ebc, root_hook='cfw_tramp_root',
        populate=0x43e91,
        stub_new=[0x30c1, 0x30c5, 0x56f5d], stub_delete=[0x30c9, 0x30cd, 0x30d1, 0x56f6d],
        stub_atexit=0x4b781, stub_throw=0x4b935,
        track_vtable=0xa9d90, step_vt_sample=0xaa338, step_vt_synth=0xaa2d0,
        track_size=0xBB4, step_base=0x128, step_size=0x2A,
        param_offsets=0x20019dc4, param_desc=0xaa114, n_params=22,
        n_tracks=16, n_steps=64, sample_tracks=range(0, 8),
        root_labels=['File', 'Samples', 'Scales', 'MIDI', 'Settings'],
        samples_labels=['View Sample Pool', 'Load Sample Pack', 'Load Files or Folders',
                        'Delete Unused Samples', 'Slice'],
        version=None,
    ),
}

SLICE_LABELS = ['x4', 'x8', 'x16', 'x32', 'x64', '= Steps']
POINTS_LABELS = ['Points']  # Play+ / Play RT only; + 'To Folder' where the export code is present
SLICE_N = [4, 8, 16, 32, 64, 0]
PARAM_START, PARAM_END, POS_MAX = 10, 11, 32767
# cfw_ram: layout + alive_ms header (8 bytes), then the selection snapshot (magic, count, bits)


class World:
    """Fake runtime objects around the real code."""

    def __init__(self, ppf_path, target_json, image):
        t = json.load(open(target_json))
        tdir = os.path.dirname(os.path.abspath(target_json))
        im = next(i for i in t['images'] if i['name'] == image)
        self.P = P = PROFILES[f'{t.get("profile", t["name"])}:{image}']
        self.fw = {}
        for sp in im['symbols']:
            self.fw.update(load_symbols(os.path.join(tdir, sp)))
        # expected version string: CFW_VERSION_STRING from the image's target.h
        self.version_text = None
        for inc in im.get('include', ['.']):
            th = os.path.join(tdir, inc, 'target.h')
            if os.path.exists(th):
                import re
                m = re.search(r'#define\s+CFW_VERSION_STRING\s+"([^"]+)"', open(th).read())
                if m:
                    self.version_text = m.group(1)
        ppf = PPF(ppf_path)
        img = ppf.image(P['image_base'])
        if 'flash_end' in P:  # appended records: one flash buffer with 0xFF in the gaps
            buf = bytearray(b'\xff' * (P['flash_end'] - P['image_base']))
            for a, b in ppf.segments():
                if P['image_base'] <= a < P['flash_end']:
                    buf[a - P['image_base']:a - P['image_base'] + len(b)] = b
            img = bytes(buf)
        self.e = e = Emu(img, P['image_base'], P['mirrors'], P['ram'], P.get('cpu', UA.UC_CPU_ARM_CORTEX_M7),
                         P.get('stack_top', 0x2005F000))
        self.cfw = {}
        mp = None
        for build in (os.path.dirname(os.path.abspath(ppf_path)), os.path.join(HERE, '..', 'build', t['name'])):
            for name in (f'link_{image}.map', 'link.map'):
                if mp is None and os.path.exists(os.path.join(build, name)):
                    mp = os.path.join(build, name)
        mp = mp or ''
        self.sections = []
        if os.path.exists(mp):
            for ln in open(mp):
                p = ln.split()
                if len(p) == 2 and p[0].startswith('0x'):
                    self.cfw[p[1]] = int(p[0], 16)
                elif p and p[0] == 'section':
                    self.sections.append((int(p[1], 16), int(p[2], 16), p[3]))
        self.calls = []
        for a in P['stub_new']:
            e.stub(a, lambda e, n, *_: e.alloc(n, 8, fill=0xCD), 'operator new')
        for a in P['stub_delete']:
            e.stub(a, lambda e, *_: 0, 'operator delete')
        e.stub(P['stub_atexit'], lambda e, *_: 0, 'atexit')
        e.stub(P['stub_throw'], self._throw, 'throw_bad_function_call')
        e.stub(self.fw['fw_undo_push'], self._undo, 'undo')
        e.stub(self.fw['fw_toast_show'], self._toast, 'toast')
        e.stub(self.fw['fw_menu_close'], self._close, 'menu_close')
        e.stub(self.fw['fw_audio_irq_off'], self._irq_off, 'irq_off')
        e.stub(self.fw['fw_audio_irq_on'], self._irq_on, 'irq_on')
        self.irq_masked = False
        self.lock_violations = []
        self._param_offsets()
        self._objects()

    # ---- stubs ----------------------------------------------------------------------------
    def _throw(self, e, *a):
        raise EmuError('std::bad_function_call')

    def _undo(self, e, undo, label, *_):
        self.calls.append(('undo', undo, e.cstr(label)))

    def _toast(self, e, toast, msg, *_):
        self.calls.append(('toast', toast, e.cstr(msg)))

    def _close(self, e, ctx, *_):
        self.calls.append(('menu_close', ctx))

    def _irq_off(self, e, *_):
        if self.irq_masked:
            self.lock_violations.append('irq_off while masked')
        self.irq_masked = True
        self.calls.append(('irq_off',))

    def _irq_on(self, e, *_):
        if not self.irq_masked:
            self.lock_violations.append('irq_on while unmasked')
        self.irq_masked = False
        self.calls.append(('irq_on',))

    # ---- runtime state that the device computes at boot -------------------------------------
    def _param_offsets(self):
        """BSS table: param idx -> byte offset in the step's 34-byte param area. Any injective
        packing by the descriptor types is valid for get/setParam (they share the table)."""
        e, P = self.e, self.P
        off = 0
        for i in range(P['n_params']):
            typ = e.r8(P['param_desc'] + 16 * i)
            e.w8(P['param_offsets'] + i, off)
            off += 2 if typ == 3 else 1
        assert off <= 0x22, off

    def _objects(self):
        e, P = self.e, self.P
        st = lambda name, ret=0: (lambda e, *a: ret, name)  # noqa: E731
        # UI manager with distinct menu and pattern screens
        self.ui = e.alloc(0x400)
        self.menu_screen = e.alloc(0x100)
        self.pattern_screen = e.alloc(0x100)
        e.w32(self.ui + 0x10, self.menu_screen)
        e.w32(self.ui + P['ui_cur'], self.pattern_screen)
        # file system object for the root builder's "/.debug" check
        fsvt = e.alloc(0x40)
        self.fs = e.alloc(0x10)
        e.w32(self.fs, fsvt)
        e.w32(fsvt + 0x10, e.fake_fn(lambda e, *a: self.fs, 'fs.get'))
        e.w32(fsvt + 0x04, e.fake_fn(lambda e, *a: 0, 'fs.exists'))
        # controller, selection, mode
        self.ctrl = c = e.alloc(0x2600)
        e.w32(c + 0x0c, self.ui)
        self.dirty_a = e.alloc(0x200)
        self.dirty_b = e.alloc(0x40)
        e.w32(c + 0x1c, self.dirty_a)
        e.w32(c + 0x24, self.dirty_b)
        self.refresh = []
        modevt = e.fake_vtable({0x2c: (lambda e, m, r, col, *_: self.refresh.append(('pad', r, col)), 'pad_refresh'),
                                0x30: (lambda e, m, r, col, z, *_: self.refresh.append(('side', r, col, z)), 'side_refresh')})
        self.mode = e.alloc(0x10)
        e.w32(self.mode, modevt)
        e.w32(c + 0x24b4, self.mode)
        self.sel = c + 0x2c
        self.pairs = e.alloc(0x804)
        self.clip = e.alloc(0x804)
        e.w32(self.sel + 4, self.pairs)
        e.w32(self.sel + 8, self.clip)
        # engine tracks with real track and step vtables
        self.tracks = []
        for t in range(P['n_tracks']):
            tr = e.alloc(P['track_size'], 4)
            e.w32(tr, P['track_vtable'])
            vt = P['step_vt_sample'] if t in P['sample_tracks'] else P['step_vt_synth']
            for s in range(P['n_steps']):
                e.w32(tr + P['step_base'] + s * P['step_size'], vt)
            self.tracks.append(tr)
        pvvt = e.fake_vtable({0: (lambda e, pv, t, *_: self.tracks[t], 'pv.track')})
        self.pv = e.alloc(0x10)
        e.w32(self.pv, pvvt)
        e.w32(self.sel + 0x5c, self.pv)
        # menu controller (ctx)
        self.ctx = x = e.alloc(0x100)
        e.w32(x + 0x00, self.ui)
        e.w32(x + 0x14, self.fs)
        e.w32(x + 0x1c, self.ctrl)
        e.w32(self.menu_screen + 0x34, x)

    # ---- helpers --------------------------------------------------------------------------
    def step(self, t, s):
        return self.tracks[t] + self.P['step_base'] + s * self.P['step_size']

    def vcall(self, obj, off, *args):
        return self.e.call(self.e.r32(self.e.r32(obj) + off), obj, *args)

    def set_trig(self, t, s, on=True):
        self.vcall(self.step(t, s), 0x08, 1 if on else 0)

    def get_param(self, t, s, idx):
        out = self.e.alloc(4)
        ok = self.vcall(self.step(t, s), 0x18, idx, out)
        return (self.e.r16s(out) if ok else None)

    def set_pairs(self, buf, pairs):
        for i, (t, s) in enumerate(pairs):
            self.e.write(buf + 2 * i, struct.pack('bb', t, s))
        self.e.w32(buf + 0x800, len(pairs))

    def menu_items(self, menu):
        e = self.e
        b, en = e.r32(menu + 0x28), e.r32(menu + 0x2c)
        lb = e.r32(menu + 0x34)
        items = [e.r32(a) for a in range(b, en, 4)]
        labels = [e.cstr(e.r32(lb + 4 * i)) for i in range(len(items))]
        return items, labels

    def populate(self, menu):
        """What opening/entering a menu does: FUN_60104fbc(screen, menu)."""
        self.e.call(self.P['populate'], self.menu_screen, menu)

    def activate(self, item):
        """FUN_601053a8 for an action item: clone the std::function, call invoker(&copy)."""
        e = self.e
        mgr, inv = e.r32(item + 0x1c), e.r32(item + 0x20)
        local = e.alloc(16)
        e.call(mgr, local, item + 0x14, 2)
        e.call(inv, local)
        e.call(mgr, local, local, 3)

    def reset_calls(self):
        self.calls.clear()
        self.refresh.clear()


def expected(k, n):
    j = k % n
    return (2 * j * POS_MAX + n) // (2 * n), (2 * (j + 1) * POS_MAX + n) // (2 * n)


def run(ppf_path, target_json, image):
    results = []

    def check(name, cond, detail=''):
        results.append((name, bool(cond), detail))
        return cond

    w = World(ppf_path, target_json, image)
    CUR, TOAST = w.P['ui_cur'], w.P['ui_toast']
    e, P = w.e, w.P
    if not w.cfw:
        raise SystemExit('missing build/<target>/link.map; run build.py first')

    # 1. static constructor of the root item, from the patched flash
    e.call(P['root_ctor'])
    root = P['root_item']
    check('root ctor stores CFW root builder', e.r32(root + 0x44) == w.cfw[P.get('root_hook', 'cfw_menu_build_root')],
          f'{e.r32(root + 0x44):#x}')
    check('root builder adj == 0 (non-virtual)', e.r32(root + 0x48) == 0)

    # 2. version string getter returns the CFW string
    if P['version']:
        vaddr, vtext = P['version'][0], w.version_text or P['version'][1]
        check('version getter returns CFW string', e.cstr(e.call(vaddr)) == vtext, e.cstr(e.call(vaddr)))

    # 3. open the menu with steps held: track 2, steps 0,4,..,60 (16 steps), pressed out of order
    held = [(2, s) for s in range(60, -1, -4)]
    for t, s in held:
        w.set_trig(t, s)
    w.set_pairs(w.pairs, held)
    w.populate(root)
    items, labels = w.menu_items(root)
    check('root menu items', labels == P['root_labels'], labels)
    st = e.r32(w.fw['cfw_ram'] + 8)
    check('snapshot taken on open', st == 0x43465731 and e.r32(w.fw['cfw_ram'] + 12) == 16, f'{st:#x}')
    samples = items[labels.index('Samples')]
    check('Samples builder is CFW', e.r32(samples + 0x44) == w.cfw['cfw_menu_build_samples'])

    # pads released after the menu opened: live selection becomes empty
    w.set_pairs(w.pairs, [])

    # 4. enter Samples (menu screen is current now)
    e.w32(w.ui + CUR, w.menu_screen)
    w.populate(samples)
    sitems, slabels = w.menu_items(samples)
    check('Samples items: stock + Slice', slabels == P['samples_labels'], slabels)
    slice_menu = sitems[-1]
    check('Slice is a submenu', e.r8(slice_menu + 8) == 0)
    check('Slice builder is CFW', e.r32(slice_menu + 0x44) == w.cfw['cfw_menu_build_slice'])
    check('Slice parent is Samples', e.r32(slice_menu + 4) == samples)
    stock_types = [e.r8(i + 8) for i in sitems[:-1]]
    check('stock Samples items untouched (actions)', stock_types == [2, 2, 2, 2], stock_types)

    # 5. back to root (menu screen current) must keep the snapshot
    w.populate(root)
    check('back-to-root keeps snapshot', e.r32(w.fw['cfw_ram'] + 12) == 16)
    _, labels = w.menu_items(root)
    w.populate(samples)  # re-enter (items rebuilt by builder; stock clears first on device)

    # 6. enter Slice
    w.populate(slice_menu)
    aitems, alabels = w.menu_items(slice_menu)
    check('Slice items', alabels[:6] == SLICE_LABELS and alabels[6:] in ([], POINTS_LABELS, POINTS_LABELS + ['To Folder']),
          alabels)
    invs = [w.cfw[f'inv_{n}'] for n in ('x4', 'x8', 'x16', 'x32', 'x64', 'sel')]
    ok = True
    for it, inv in zip(aitems, invs):
        ok &= e.r8(it + 8) == 2 and e.r32(it + 0x14) == w.ctx and e.r32(it + 0x1c) == w.fw['fw_fn_manager_word'] \
            and e.r32(it + 0x20) == inv and e.r8(it + 0x24) == 0
    check('action items: type 2, capture=ctx, stock manager, CFW invoker, flag 0', ok)

    # 7. x16 on the snapshot
    w.reset_calls()
    w.activate(aitems[SLICE_LABELS.index('x16')])
    good = all(w.get_param(2, s, PARAM_START) == expected(k, 16)[0] and
               w.get_param(2, s, PARAM_END) == expected(k, 16)[1] for k, s in enumerate(range(0, 64, 4)))
    check('x16: Start/End per step', good, [(w.get_param(2, s, 10), w.get_param(2, s, 11)) for s in range(0, 16, 4)])
    check('x16: untouched steps have no lock', w.get_param(2, 1, PARAM_START) is None)
    names = [c[0] for c in w.calls]
    check('x16: order close, undo, irq..., toast', names[0] == 'menu_close' and names[1] == 'undo' and names[-1] == 'toast',
          names[:3] + names[-2:])
    check('x16: undo label', ('undo', w.ctrl + 0x23a8, 'Slice') in w.calls)
    check('x16: toast text', w.calls[-1] == ('toast', w.ui + TOAST, 'Slice x16: 16 steps'), w.calls[-1])
    check('x16: dirty flags', e.r8(w.dirty_a + 0x1c4) == 1 and e.r8(w.dirty_b + 0x24) == 1)
    check('x16: irq lock balanced', not w.irq_masked and not w.lock_violations and
          e.r32(w.fw['fw_edit_lock_depth']) == 0 and names.count('irq_off') == 16, names.count('irq_off'))
    check('x16: pad refresh 8x16 + 8x4', len([r for r in w.refresh if r[0] == 'pad']) == 128 and
          len([r for r in w.refresh if r[0] == 'side']) == 32 and all(r[3] == 0 for r in w.refresh if r[0] == 'side'))
    check('x16: menu closed with ctx', ('menu_close', w.ctx) in w.calls)

    # 8. x4 wraps (k mod 4)
    w.reset_calls()
    w.activate(aitems[0])
    check('x4: wraps every 4 steps', all(w.get_param(2, s, 10) == expected(k, 4)[0] and
                                         w.get_param(2, s, 11) == expected(k, 4)[1] for k, s in enumerate(range(0, 64, 4))))
    check('x4: toast', w.calls[-1][2] == 'Slice x4: 16 steps', w.calls[-1])

    # 9. x64 and "= Steps"
    w.activate(aitems[4])
    check('x64: first 16 of 64', all(w.get_param(2, s, 10) == expected(k, 64)[0] for k, s in enumerate(range(0, 64, 4))))
    w.reset_calls()
    w.activate(aitems[5])
    check('= Steps: N = 16', all(w.get_param(2, s, 11) == expected(k, 16)[1] for k, s in enumerate(range(0, 64, 4))))
    check('= Steps toast', w.calls[-1][2] == 'Slice: 16 steps', w.calls[-1])
    check('last slice ends at 32767', w.get_param(2, 60, 11) == 32767)

    # 10. selection with an empty step and a synth-track step: both skipped
    for s in (1, 2, 3):
        w.set_trig(5, s)
    w.set_trig(9, 0)
    e.w32(w.ui + CUR, w.pattern_screen)
    w.set_pairs(w.pairs, [(5, 3), (5, 1), (5, 2), (5, 10), (9, 0)])  # (5,10) has no trig
    w.populate(root)
    w.reset_calls()
    w.activate(aitems[1])  # x8
    check('skip: 3 trigged sample steps sliced in step order',
          [w.get_param(5, s, 10) for s in (1, 2, 3)] == [expected(k, 8)[0] for k in range(3)])
    check('skip: empty step untouched', w.get_param(5, 10, 10) is None)
    check('skip: synth step untouched (no lock)', not e.r8(w.step(9, 0) + 0x26) and not e.r8(w.step(9, 0) + 0x27))
    check('skip: toast', w.calls[-1][2] == 'Slice x8: 3 steps', w.calls[-1])

    # 11. multi-track
    for s in (0, 1):
        w.set_trig(6, s)
    w.set_pairs(w.pairs, [(5, 1), (6, 0), (5, 2), (6, 1)])
    w.populate(root)
    w.reset_calls()
    w.activate(aitems[5])
    check('multi-track: per-track = Steps', [w.get_param(5, s, 10) for s in (1, 2)] == [0, 16384] and
          [w.get_param(6, s, 11) for s in (0, 1)] == [16384, 32767])
    check('multi-track toast', w.calls[-1][2] == 'Slice: 4 on 2 trks', w.calls[-1])

    # 12. nothing held -> clipboard fallback
    for s in range(0, 12):
        w.set_trig(1, s)
    w.set_pairs(w.pairs, [])
    w.set_pairs(w.clip, [(1, s) for s in range(12)])
    w.populate(root)
    check('empty snapshot on open', e.r32(w.fw['cfw_ram'] + 12) == 0)
    w.reset_calls()
    w.activate(aitems[2])  # x16
    check('clipboard: sliced', [w.get_param(1, s, 10) for s in range(12)] == [expected(k, 16)[0] for k in range(12)])
    check('clipboard: toast', w.calls[-1][2] == 'Slice x16: 12 copied', w.calls[-1])

    # 13. nothing held, empty clipboard -> toast only, no edit
    w.set_pairs(w.clip, [])
    w.populate(root)
    w.reset_calls()
    w.activate(aitems[2])
    names = [c[0] for c in w.calls]
    check('nothing: no undo, no lock', 'undo' not in names and 'irq_off' not in names, names)
    check('nothing: toast', w.calls[-1][2] == 'Hold or copy steps first', w.calls[-1])
    check('nothing: menu closed', names[0] == 'menu_close')

    # 14. only synth steps
    w.set_pairs(w.pairs, [(9, 0)])
    w.populate(root)
    w.reset_calls()
    w.activate(aitems[0])
    check('synth only: toast', w.calls[-1][2] == 'Slice: no sample-track trigs', w.calls[-1])

    # 15. corrupt count is ignored
    e.w32(w.pairs + 0x800, 0x7fffffff)
    w.populate(root)
    w.reset_calls()
    w.activate(aitems[0])
    check('corrupt pair count ignored', e.r32(w.fw['cfw_ram'] + 16) == 0 and w.calls[-1][2] == 'Hold or copy steps first',
          w.calls[-1])

    # 15b. Menu > Steps (Randomize / Fill), images that have it
    if 'cfw_menu_build_steps' in w.cfw:
        items, labels = w.menu_items(root)
        steps = items[labels.index('Steps')]
        check('Steps builder is CFW', e.r32(steps + 0x44) == w.cfw['cfw_menu_build_steps'])
        w.populate(steps)
        st_items, st_labels = w.menu_items(steps)
        check('Steps items', st_labels == ['Randomize', 'Fill', 'Arp'], st_labels)
        w.populate(st_items[1])
        f_items, f_labels = w.menu_items(st_items[1])
        check('Fill targets', f_labels[:4] == ['Volume / Velocity', 'Pan', 'Delay', 'Reverb'] and
              f_labels[4:] == [f'MIDI CC {i}' for i in range(1, 9)], f_labels)
        SH = ['Ramp Up', 'Ramp Down', 'Exp Up', 'Exp Down', 'Triangle', 'Sine', 'Random']
        # sample track 3: 8 trigs at 0..7, synth/MIDI track 9: 4 trigs at 0..3
        for k in range(8):
            w.set_trig(3, k)
        for k in range(4):
            w.set_trig(9, k)
        sel = [(3, k) for k in range(8)] + [(9, k) for k in range(4)]
        def fill(target, shape):
            e.w32(w.ui + CUR, w.pattern_screen)
            w.set_pairs(w.pairs, sel)
            w.populate(root)
            e.w32(w.ui + CUR, w.menu_screen)
            w.populate(f_items[target])
            sh_items, sh_labels = w.menu_items(f_items[target])
            w.reset_calls()
            w.activate(sh_items[sh_labels.index(shape)])
            return sh_labels
        sh_labels = fill(0, 'Ramp Up')
        check('Fill shapes', sh_labels == SH, sh_labels)
        vols = [w.get_param(3, k, 0) for k in range(8)]
        vels = [w.get_param(9, k, 0) for k in range(4)]
        check('Volume Ramp Up: 0..10000 over 8 steps', vols == [round(10000 * k / 7 / 1024 * 1024) for k in range(8)] or
              (vols[0] == 0 and vols[-1] == 10000 and vols == sorted(vols)), vols)
        check('Velocity Ramp Up: 1..127 over 4 steps', vels[0] == 1 and vels[-1] == 127 and vels == sorted(vels), vels)
        names = [c[0] for c in w.calls]
        check('Fill: close, undo "Fill", toast', names[0] == 'menu_close' and ('undo', w.ctrl + 0x23a8, 'Fill') in w.calls
              and w.calls[-1][0] == 'toast', names[:3])
        check('Fill toast', w.calls[-1][2] == 'Vol Ramp Up: 12 steps', w.calls[-1])
        check('Fill: irq lock balanced', not w.irq_masked and not w.lock_violations and
              e.r32(w.fw['fw_edit_lock_depth']) == 0)
        fill(4, 'Ramp Down')  # MIDI CC 1 = param 2, MIDI steps only
        cc = [w.get_param(9, k, 2) for k in range(4)]
        check('CC1 Ramp Down: 127..0 on param 2', cc[0] == 127 and cc[-1] == 0 and cc == sorted(cc, reverse=True), cc)
        check('CC1: sample steps untouched', w.get_param(3, 0, 2) is None)
        check('CC1 toast counts MIDI steps', w.calls[-1][2] == 'CC1 Ramp Down: 4 steps', w.calls[-1])
        fill(1, 'Sine')
        pan = [w.get_param(3, k, 1) for k in range(8)]
        check('Pan Sine: starts centred, swings both ways', abs(pan[0]) <= 100 and max(pan) > 5000 and min(pan) < -5000, pan)
        fill(1, 'Triangle')
        pan = [w.get_param(3, k, 1) for k in range(8)]
        check('Pan Triangle: low-high-low', pan[0] == -10000 and pan[-1] == -10000 and max(pan) > 5000, pan)
        # Randomize
        r_items = st_items
        def randomize(which):
            e.w32(w.ui + CUR, w.pattern_screen)
            w.set_pairs(w.pairs, sel)
            w.populate(root)
            e.w32(w.ui + CUR, w.menu_screen)
            w.populate(r_items[0])
            ri, rl = w.menu_items(r_items[0])
            w.reset_calls()
            w.activate(ri[rl.index(which)])
            return rl
        for k in range(8):  # known starting point: volume 8000, pan 0
            w.vcall(w.step(3, k), 0x10, 0, 8000)
            w.vcall(w.step(3, k), 0x10, 1, 0)
        rl = randomize('Subtle')
        check('Randomize items', rl == ['Subtle', 'Medium', 'Wild'], rl)
        vols = [w.get_param(3, k, 0) for k in range(8)]
        pans = [w.get_param(3, k, 1) for k in range(8)]
        check('Subtle: volume within 8000-2000..8000', all(6000 <= v <= 8000 for v in vols), vols)
        check('Subtle: pan within +-4000', all(-4000 <= p <= 4000 for p in pans), pans)
        check('Subtle: values vary', len(set(vols + pans)) > 4, vols + pans)
        check('Randomize undo label', ('undo', w.ctrl + 0x23a8, 'Randomize') in w.calls)
        check('Randomize toast', w.calls[-1][2] == 'Rnd Subtle: 12 steps', w.calls[-1])
        randomize('Wild')
        vels = [w.get_param(9, k, 0) for k in range(4)]
        dl = [w.get_param(3, k, 7) for k in range(8)]
        check('Wild: velocity 1..127', all(1 <= v <= 127 for v in vels), vels)
        check('Wild: delay send 0..10000 and varied', all(0 <= d <= 10000 for d in dl) and len(set(dl)) > 3, dl)
        check('Randomize: steps outside the selection untouched', w.get_param(3, 8, 0) is None)
        check('Randomize: irq lock balanced', not w.irq_masked and not w.lock_violations and
              e.r32(w.fw['fw_edit_lock_depth']) == 0)
        # Arp: Major Up on 8 sample steps (track 3) and 4 MIDI steps (track 9)
        e.w32(w.ui + CUR, w.pattern_screen)
        w.set_pairs(w.pairs, sel)
        w.populate(root)
        e.w32(w.ui + CUR, w.menu_screen)
        w.populate(st_items[2])
        a_items, a_labels = w.menu_items(st_items[2])
        check('Arp items', a_labels[:4] == ['Major Up', 'Major Down', 'Major Up-Down', 'Major Random'] and
              a_labels[-1] == 'Octaves', a_labels)
        for k in range(8):
            w.vcall(w.step(3, k), 0x10, 6, 48)
        w.reset_calls()
        w.activate(a_items[0])
        notes = [w.get_param(3, k, 6) for k in range(8)]
        check('Arp Major Up from C: 48 52 55 60 64 67 48 52', notes == [48, 52, 55, 60, 64, 67, 48, 52], notes)
        mnotes = [w.get_param(9, k, 6) for k in range(4)]
        check('Arp on MIDI steps: 4 chord tones', len(set(mnotes)) == 4 and all(n is not None for n in mnotes), mnotes)
        check('Arp toast', w.calls[-1][2] == 'Arp Maj Up: 12 steps', w.calls[-1])
        e.w32(w.ui + CUR, w.pattern_screen)
        w.set_pairs(w.pairs, [])
        w.set_pairs(w.clip, [])
        w.populate(root)
        e.w32(w.ui + CUR, w.menu_screen)
        w.populate(r_items[0])
        ri, rl = w.menu_items(r_items[0])
        w.reset_calls()
        w.activate(ri[0])
        names = [c[0] for c in w.calls]
        check('Randomize, nothing held: toast only', 'undo' not in names and w.calls[-1][2] == 'Hold or copy steps first',
              w.calls[-1])

    # 16. guarded entry: if the appended records were never flashed, the menu must be stock
    if P.get('root_hook') == 'cfw_tramp_root':
        tramp = w.cfw['cfw_tramp_root'] & ~1
        appended = [(a, n) for a, n, name in w.sections if a != tramp]
        for a, n in appended:
            e.write(a, b'\xff' * n)
        e.w32(w.fw['cfw_ram'] + 8, 0)
        e.w32(w.ui + CUR, w.pattern_screen)
        before = len(w.menu_items(root)[0])
        try:
            w.populate(root)
            ok = True
        except EmuError as ex:
            ok = False
        items, labels = w.menu_items(root)
        new = items[before:]
        sam = new[labels[before:].index('Samples')] if ok and 'Samples' in labels[before:] else None
        check('guard: menu builds with appended code erased', ok and labels[before:] == P['root_labels'],
              labels[before:])
        check('guard: Samples keeps the stock builder', sam and e.r32(sam + 0x44) == w.fw['fw_menu_build_samples'])
        check('guard: no CFW code ran (no snapshot written)', e.r32(w.fw['cfw_ram'] + 8) == 0)
    return results


def main(argv):
    res = run(argv[0], argv[1], argv[2])
    bad = [r for r in res if not r[1]]
    for name, ok, detail in res:
        print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'   [{detail}]' if not ok and detail != '' else ''))
    print(f'{len(res) - len(bad)}/{len(res)} passed')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
