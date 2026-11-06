"""Convenience driver: restore a booted snapshot, inject inputs, take screenshots."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import boot  # noqa: E402
from vplay import load_snapshot, save_snapshot  # noqa: E402

PPF = os.path.join(HERE, '..', '..', 'PlayPlus_v1.3.0.ppf')


class Session:
    def __init__(self, snap, ppf=PPF):
        self.vp = vp = boot.make(ppf, fresh=False)
        load_snapshot(vp, snap)
        vp.eve.on_swap = []

    def run_ms(self, ms):
        vp = self.vp
        t = vp.ms + ms
        vp.run(10 ** 11, on_chunk=lambda v: setattr(v, 'stopped', '_ok') if v.ms >= t else None)
        if vp.stopped == '_ok':
            vp.stopped = None
        if vp.stopped:
            raise RuntimeError(vp.stopped)

    def tap(self, btn, hold_ms=60, after_ms=400):
        self.vp.inputs.button(btn, 0)
        self.run_ms(hold_ms)
        self.vp.inputs.button(btn, 1)
        self.run_ms(after_ms)

    def pad(self, row, col, hold_ms=60, after_ms=300):
        self.vp.inputs.pad(row, col, 0)
        self.run_ms(hold_ms)
        self.vp.inputs.pad(row, col, 1)
        self.run_ms(after_ms)

    def turn(self, delta, after_ms=300):
        self.vp.inputs.encoder(delta)
        self.run_ms(after_ms)

    def knob(self, k, delta, after_ms=300):
        self.vp.inputs.knob(k, delta)
        self.run_ms(after_ms)

    def touch(self, k, hold_ms=100, after_ms=300):
        self.vp.inputs.touch(k, 0)
        self.run_ms(hold_ms)
        self.vp.inputs.touch(k, 1)
        self.run_ms(after_ms)

    def shot(self, path, scale=2):
        self.vp.eve.screenshot(path, scale)
        return path

    def save(self, path):
        save_snapshot(self.vp, path)
