#!/usr/bin/env python3
"""Stock-parity check: walk the whole menu tree on the virtual device and compare stock vs CFW.

For every submenu reachable from Menu (depth-first, entering submenus only: actions and value
editors are never pressed, so nothing is changed), record what the list shows while the cursor
scrolls from the first to the last row (labels and right-hand values, as drawn by the firmware),
plus each row's item type. Run it on the stock image and on the CFW image of the same device and
compare: the only allowed differences are the CFW's own additions (whitelisted below); every stock
row must still be there with the same text, in the same order, with the same type.

usage: vdev_menutree.py walk <image.ppf> --snap BOOT.snap --out tree.json
       vdev_menutree.py diff <stock_tree.json> <cfw_tree.json>     (exit 0 = only allowed changes)
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'vdev'))
sys.path.insert(0, HERE)

SUB_ITEMS, SUB_LABELS, SUB_COUNT, ITEM_TYPE = 0x28, 0x34, 0x0c, 0x08   # children, their labels (char*)
SCR_SUBMENU = 0x2c
TYPE_SUBMENU = 0
MAX_DEPTH = 5

# CFW additions (path of the menu they appear in -> row labels the CFW adds there). Anything
# under an added submenu is the CFW's own and is not compared.
ALLOWED = {
    'Menu': ['LFO', 'Matrix'],
    'Menu/MIDI/CC Mapping/*': ['Load Preset', 'Save Preset'],
    'Menu/File': ['Delete Project'],
    'Menu/Samples': ['Slice'],
    'Menu/Settings': ['Screen Brightness', 'Screen Dim', 'Knob Touch'],
    'Menu/Synths/*': ['Randomize'],
}


def cstr(vp, a, n=64):
    if not a:
        return None
    try:
        b = bytes(vp.mu.mem_read(a, n))
    except Exception:
        return None
    s = b.split(b'\0')[0]
    return s.decode('latin1') if s and all(32 <= c < 127 for c in s) else None


class Walker:
    def __init__(self, ppf, snap):
        from vdev_slice import Device
        import flows
        self.flows = flows
        self.d = Device(ppf, snap)
        self.s, self.vp = self.d.s, self.d.vp

    def screen(self):
        return self.vp.r32(self.vp.r32(self.d.listener + 8) + 0x10)

    def current(self):
        return self.vp.r32(self.screen() + SCR_SUBMENU)

    def items(self, sub):
        """[(item, type, label)]: children vector at +0x28, parallel label vector at +0x34."""
        vp = self.vp
        b, e = vp.r32(sub + SUB_ITEMS), vp.r32(sub + SUB_ITEMS + 4)
        lb = vp.r32(sub + SUB_LABELS)
        out = []
        for k, a in enumerate(range(b, e, 4)):
            it = vp.r32(a)
            out.append((it, vp.mu.mem_read(it + ITEM_TYPE, 1)[0], cstr(vp, vp.r32(lb + 4 * k)) if lb else None))
        return out

    def rows_shown(self, n):
        """Scroll top -> bottom; collect the list's TEXT draws (font 8 rows) in order."""
        s, vp = self.s, self.vp
        seen = []
        vp.eve.cmd_log.clear()
        s.turn(-64, after_ms=150)
        for k in range(max(n, 1)):
            for name, a in vp.eve.cmd_log:
                if name == 'TEXT' and len(a) > 2 and (a[1] & 0xffff) == 8 and a[2] and a[2] not in seen:
                    seen.append(a[2])
            vp.eve.cmd_log.clear()
            if k < n - 1:
                s.turn(1, after_ms=120)
        return seen

    def walk(self, path='Menu', depth=0):
        sub = self.current()
        its = self.items(sub)
        node = dict(path=path, types=[t for _, t, _ in its], labels=[lb for _, _, lb in its],
                    shown=self.rows_shown(len(its)), children={})
        if depth >= MAX_DEPTH:
            node['truncated'] = True
            return node
        for idx in range(len(its)):
            its = self.items(sub)          # going back rebuilds the children: re-read every time
            it, typ, label = its[idx]
            if typ != TYPE_SUBMENU:
                continue
            self.flows.enter_item(self.s, idx, after_ms=500)
            if self.current() != it:
                node['children'][str(idx)] = dict(path=f'{path}/{label}', error='did not enter')
                self.back_to(sub)
                continue
            name = label or f'#{idx}'
            node['children'][str(idx)] = self.walk(f'{path}/{name}', depth + 1)
            self.back_to(sub)
        return node

    def back_to(self, sub):
        for _ in range(4):
            if self.current() == sub:
                return
            self.s.tap(self.flows.BTN_BACK, after_ms=450)
        if self.current() != sub:
            raise RuntimeError('lost the menu position')


def cmd_walk(a):
    global MAX_DEPTH
    MAX_DEPTH = a.depth
    w = Walker(a.ppf, a.snap)
    w.s.run_ms(300)
    w.flows.open_menu(w.s)
    tree = w.walk()
    json.dump(tree, open(a.out, 'w'), indent=1)
    def count(n):
        return 1 + sum(count(c) for c in n.get('children', {}).values())
    print(f'{count(tree)} menus walked -> {a.out}')
    return 0


def same_row(a, b):
    """Stock row text vs CFW row text: equal, or the firmware version gaining its -cfwN suffix."""
    if a == b:
        return True
    import re
    return bool(re.fullmatch(re.escape(a) + r'-(cfw\d+(\.\d+)?|FreePlay[\d.]+b?)', b or '')) and (a or '').startswith('Version: ')


def allowed_here(path):
    for pat, rows in ALLOWED.items():
        pp, qq = pat.split('/'), path.split('/')
        if len(pp) == len(qq) and all(x == '*' or x == y for x, y in zip(pp, qq)):
            return rows
    return []


def cmd_diff(a):
    stock, cfw = json.load(open(a.stock)), json.load(open(a.cfw))
    problems, added = [], []

    def cmp(s, c):
        path = s['path']
        extra = allowed_here(path)
        if 'error' in s or 'error' in c:
            if s.get('error') != c.get('error'):
                problems.append(f'{path}: {s.get("error")} vs {c.get("error")}')
            return
        # stock rows (labels/types) must appear in the CFW menu in the same order
        sl = list(zip(s['labels'], s['types']))
        cl = list(zip(c['labels'], c['types']))
        cl_stock = [r for r in cl if r[0] not in extra]
        if len(sl) != len(cl_stock) or not all(same_row(a[0], b[0]) and a[1] == b[1] for a, b in zip(sl, cl_stock)):
            problems.append(f'{path}: rows differ\n    stock {sl}\n    cfw   {cl_stock}')
        for a, b in zip(sl, cl_stock):
            if a[0] != b[0] and same_row(a[0], b[0]):
                added.append(f'{path}: {a[0]} -> {b[0]} (version string)')
        for r in cl:
            if r[0] in extra:
                added.append(f'{path}: + {r[0]}')
        ss = [t for t in s['shown']]
        cs = [t for t in c['shown'] if t not in extra]
        if extra and len(cs) > len(ss):
            cs = cs[:len(ss)]  # the values our option rows show (after the stock rows)
        if len(ss) != len(cs) or not all(same_row(a, b) for a, b in zip(ss, cs)):
            problems.append(f'{path}: shown text differs\n    stock {ss}\n    cfw   {cs}')
        # children: match by stock index -> CFW index of the same label
        for idx, child in s['children'].items():
            lab = s['labels'][int(idx)]
            cidx = next((k for k, (l2, _) in enumerate(cl) if l2 == lab), None)
            cc = c['children'].get(str(cidx)) if cidx is not None else None
            if cc is None:
                problems.append(f'{path}/{lab}: missing in CFW')
                continue
            cmp(child, cc)

    cmp(stock, cfw)
    for x in added:
        print('ADDED  ', x)
    for p in problems:
        print('PROBLEM', p)
    print(f'{len(problems)} problems, {len(added)} CFW additions')
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest='cmd', required=True)
    w = sp.add_parser('walk')
    w.add_argument('ppf')
    w.add_argument('--snap', required=True)
    w.add_argument('--out', required=True)
    w.add_argument('--depth', type=int, default=MAX_DEPTH)
    d = sp.add_parser('diff')
    d.add_argument('stock')
    d.add_argument('cfw')
    a = ap.parse_args()
    return cmd_walk(a) if a.cmd == 'walk' else cmd_diff(a)


if __name__ == '__main__':
    sys.exit(main())
