#!/usr/bin/env python3
"""Virtual Play+: boots the real Play+ firmware (stock or CFW .ppf) in Unicorn.

The CPU is Unicorn's ARMv8 AArch32 core in Thumb state (Unicorn's Cortex-M mode cannot map the
system control space). M-profile-only behaviour is modelled around it:
  * MSR/MRS of PRIMASK map onto CPSR.I (cpsid/cpsie i work natively); the few MSP/PSP/IPSR/BASEPRI
    sites are emulated by address hooks.
  * Interrupts are injected by the host between execution chunks (exception entry/exit in Python).
  * Peripherals are register files with per-register behaviour added as the firmware needs it
    (periph.py). Nothing here is a cycle-accurate model; it exists to run UI and editing code.
"""
import struct
import sys
import os

from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, \
    UC_HOOK_MEM_UNMAPPED, UC_HOOK_CODE, UC_HOOK_INTR, UC_HOOK_INSN_INVALID, UC_PROT_ALL
from unicorn import arm_const as A
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
from ppf import PPF  # noqa: E402

KB, MB = 1024, 1024 * 1024
REGS = [A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3, A.UC_ARM_REG_R4,
        A.UC_ARM_REG_R5, A.UC_ARM_REG_R6, A.UC_ARM_REG_R7, A.UC_ARM_REG_R8, A.UC_ARM_REG_R9,
        A.UC_ARM_REG_R10, A.UC_ARM_REG_R11, A.UC_ARM_REG_R12, A.UC_ARM_REG_SP, A.UC_ARM_REG_LR,
        A.UC_ARM_REG_PC]
ISR_RET = 0x0FFE0000
ASYNC_RET = ISR_RET + 0x100   # return address of calls started with async_call()


class Stop(Exception):
    pass


class VPlay:
    def __init__(self, ppf_path, base=0x60030000, verbose=False):
        self.verbose = verbose
        self.mu = mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
        mu.ctl_set_cpu_model(A.UC_CPU_ARM_MAX)
        ppf = PPF(ppf_path)
        img = ppf.image(base)
        self.base, self.img = base, img
        # records past the image end (CFW "ext" code) are flashed too, like a full update; set
        # VPLAY_DROP_APPENDED=1 to model an updater that only writes the image itself
        self.extra = [] if os.environ.get('VPLAY_DROP_APPENDED') else \
            [(a, b) for a, b in ppf.segments() if a != base and 0x60000000 <= a < 0x61000000]
        # memory map (i.MX RT1062 as configured by this firmware)
        mu.mem_map(0x00000000, 512 * KB)            # ITCM (FlexRAM)
        mu.mem_map(0x20000000, 512 * KB)            # DTCM
        mu.mem_map(0x20200000, 1 * MB)              # OCRAM
        mu.mem_map(0x60000000, 16 * MB)             # FlexSPI flash (XIP)
        mu.mem_write(0x60000000, b'\xff' * (base - 0x60000000))
        mu.mem_write(base, img)
        end = base + len(img)
        mu.mem_write(end, b'\xff' * (0x60000000 + 16 * MB - end))   # erased flash
        for a, b in self.extra:
            mu.mem_write(a, b)
        mu.mem_map(0x80000000, 32 * MB)             # SEMC SDRAM
        mu.mem_map(0x40000000, 4 * MB)              # AIPS peripherals
        mu.mem_map(0x42000000, 64 * KB)             # GPIO6-9 (fast)
        mu.mem_map(0xE0000000, 1 * MB)              # PPB / SCS
        mu.mem_map(ISR_RET & ~0xFFF, 4 * KB)
        mu.mem_write(ISR_RET & ~0xFFF, b'\x00\xbf' * 2048)
        mu.hook_add(UC_HOOK_CODE, self._async_ret, begin=ASYNC_RET, end=ASYNC_RET)
        self.async_ctx = None
        self.mmio_w = {}
        self.mmio_log = []
        self.read_rules = {}    # addr -> fn(vp, addr, size, stored) -> value
        self.write_rules = {}   # addr -> fn(vp, addr, size, value)
        self.range_rules = []   # (lo, hi, read_fn, write_fn)
        mu.hook_add(UC_HOOK_MEM_READ, self._mmio_read, begin=0x40000000, end=0x4FFFFFFF)
        mu.hook_add(UC_HOOK_MEM_WRITE, self._mmio_write, begin=0x40000000, end=0x4FFFFFFF)
        mu.hook_add(UC_HOOK_MEM_READ, self._mmio_read, begin=0xE0000000, end=0xE00FFFFF)
        mu.hook_add(UC_HOOK_MEM_WRITE, self._mmio_write, begin=0xE0000000, end=0xE00FFFFF)
        mu.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)
        mu.hook_add(UC_HOOK_INTR, self._intr)
        mu.hook_add(UC_HOOK_INSN_INVALID, self._invalid)
        self.addr_hooks = {}
        self.icount = 0           # instructions executed (chunk granularity)
        self.cycles = 0
        # reset state
        sp, pc = struct.unpack_from('<II', img, 0)
        mu.reg_write(A.UC_ARM_REG_SP, sp)
        mu.reg_write(A.UC_ARM_REG_PC, pc)
        # privileged, IRQs masked until the firmware enables them; VFP on
        mu.reg_write(A.UC_ARM_REG_C1_C0_2, 0xF << 20)
        mu.reg_write(A.UC_ARM_REG_FPEXC, 0x40000000)
        self.pending = set()
        self.enabled_irqs = set()
        self.stopped = None
        self.md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        self.sysregs = dict(msp=sp, psp=0, basepri=0, faultmask=0, control=0)
        self.ipsr = 0
        self.invalid_seen = {}

    # ---- helpers -----------------------------------------------------------------------------
    def r32(self, a): return struct.unpack('<I', self.mu.mem_read(a, 4))[0]
    def w32(self, a, v): self.mu.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))
    def pc(self): return self.mu.reg_read(A.UC_ARM_REG_PC)

    def hook_addr(self, addr, fn):
        a = addr & ~1
        self.addr_hooks[a] = fn
        self.mu.hook_add(UC_HOOK_CODE, lambda mu, ad, sz, _: self.addr_hooks[ad](self), begin=a, end=a)

    # ---- MMIO ----------------------------------------------------------------------------------
    def _mmio_read(self, mu, access, addr, size, value, _):
        a = addr & ~3
        stored = self.mmio_w.get(a, 0)
        fn = self.read_rules.get(a)
        if fn is None:
            for lo, hi, rf, wf in self.range_rules:
                if lo <= a < hi and rf:
                    fn = rf
                    break
        v = fn(self, a, size, stored) if fn else stored
        if v is not None:
            mu.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))
        if self.verbose:
            self.mmio_log.append(('R', a, v, self.pc()))

    def _mmio_write(self, mu, access, addr, size, value, _):
        a = addr & ~3
        sh = (addr & 3) * 8
        mask = ((1 << (size * 8)) - 1) << sh
        cur = self.mmio_w.get(a, 0)
        nv = (cur & ~mask) | ((value << sh) & mask)
        self.mmio_w[a] = nv
        fn = self.write_rules.get(a)
        if fn is None:
            for lo, hi, rf, wf in self.range_rules:
                if lo <= a < hi and wf:
                    fn = wf
                    break
        if fn:
            fn(self, a, size, nv)
        if self.verbose:
            self.mmio_log.append(('W', a, nv, self.pc()))

    def _unmapped(self, mu, access, addr, size, value, _):
        self.stopped = f'unmapped access {addr:#x} size {size} at pc {self.pc():#x}'
        return False

    def _intr(self, mu, intno, _):
        pc = self.pc()
        if intno == 2:  # SVC
            self.stopped = f'svc at {pc:#x}'
        elif intno == 7:  # BKPT
            self.stopped = f'bkpt at {pc:#x}'
        else:
            self.stopped = f'exception {intno} at {pc:#x}'
        mu.emu_stop()

    REGNAME = {'r0': A.UC_ARM_REG_R0, 'r1': A.UC_ARM_REG_R1, 'r2': A.UC_ARM_REG_R2, 'r3': A.UC_ARM_REG_R3,
               'r4': A.UC_ARM_REG_R4, 'r5': A.UC_ARM_REG_R5, 'r6': A.UC_ARM_REG_R6, 'r7': A.UC_ARM_REG_R7,
               'r8': A.UC_ARM_REG_R8, 'r9': A.UC_ARM_REG_R9, 'sb': A.UC_ARM_REG_R9, 'r10': A.UC_ARM_REG_R10,
               'sl': A.UC_ARM_REG_R10, 'r11': A.UC_ARM_REG_R11, 'fp': A.UC_ARM_REG_R11,
               'r12': A.UC_ARM_REG_R12, 'ip': A.UC_ARM_REG_R12, 'sp': A.UC_ARM_REG_SP, 'lr': A.UC_ARM_REG_LR}

    def _invalid(self, mu, _):
        """Emulate M-profile-only instructions the A-profile core rejects; returns True to go on."""
        pc = self.pc()
        code = bytes(mu.mem_read(pc, 4))
        ins = next(self.md.disasm(code, pc), None)
        if ins is None:
            self.stopped = f'undecodable instruction at {pc:#x}: {code.hex()}'
            return False
        m, ops = ins.mnemonic, [o.strip() for o in ins.op_str.split(',')]
        self.invalid_seen[pc] = f'{m} {ins.op_str}'
        if m == 'msr' and len(ops) == 2:
            v = mu.reg_read(self.REGNAME[ops[1]])
            r = ops[0]
            if r == 'primask':
                cpsr = mu.reg_read(A.UC_ARM_REG_CPSR)
                mu.reg_write(A.UC_ARM_REG_CPSR, (cpsr | 0x80) if v & 1 else (cpsr & ~0x80))
            elif r == 'msp':
                self.sysregs['msp'] = v
                mu.reg_write(A.UC_ARM_REG_SP, v)
            elif r in self.sysregs:
                self.sysregs[r] = v
            else:
                self.stopped = f'unhandled msr {ins.op_str} at {pc:#x}'
                return False
        elif m == 'mrs' and len(ops) == 2:
            r = ops[1]
            if r == 'primask':
                v = (mu.reg_read(A.UC_ARM_REG_CPSR) >> 7) & 1
            elif r == 'ipsr':
                v = self.ipsr
            elif r == 'msp':
                v = mu.reg_read(A.UC_ARM_REG_SP)
            elif r in self.sysregs:
                v = self.sysregs[r]
            else:
                self.stopped = f'unhandled mrs {ins.op_str} at {pc:#x}'
                return False
            mu.reg_write(self.REGNAME[ops[0]], v)
        elif m in ('wfi', 'wfe', 'sev', 'nop'):
            pass
        else:
            self.stopped = f'invalid instruction {m} {ins.op_str} at {pc:#x}'
            return False
        mu.reg_write(A.UC_ARM_REG_PC, (pc + ins.size) | 1)
        return True

    # ---- interrupts ----------------------------------------------------------------------------
    def irq_masked(self):
        return bool(self.mu.reg_read(A.UC_ARM_REG_CPSR) & 0x80)

    def call_isr(self, vector, max_insns=5_000_000):
        """Run exception handler `vector` (16 + IRQ number) to completion, preserving context."""
        mu = self.mu
        handler = self.r32(self.vtor + 4 * vector)
        saved = [mu.reg_read(r) for r in REGS]
        cpsr = mu.reg_read(A.UC_ARM_REG_CPSR)
        fp = [mu.reg_read(getattr(A, f'UC_ARM_REG_D{i}')) for i in range(16)]
        fpscr = mu.reg_read(A.UC_ARM_REG_FPSCR)
        sp = (saved[13] - 0x68) & ~7
        mu.reg_write(A.UC_ARM_REG_SP, sp)
        mu.reg_write(A.UC_ARM_REG_LR, ISR_RET | 1)
        try:
            mu.emu_start(handler | 1, ISR_RET, count=max_insns)
        finally:
            ok = (self.pc() & ~1) == ISR_RET
            for r, v in zip(REGS, saved):
                if r != A.UC_ARM_REG_PC:
                    mu.reg_write(r, v)
            for i, v in enumerate(fp):
                mu.reg_write(getattr(A, f'UC_ARM_REG_D{i}'), v)
            mu.reg_write(A.UC_ARM_REG_FPSCR, fpscr)
            mu.reg_write(A.UC_ARM_REG_CPSR, cpsr)
            mu.reg_write(A.UC_ARM_REG_PC, saved[15] | 1)
        return ok

    def call_fn(self, addr, *args, max_insns=50_000_000):
        """Call firmware function `addr` with r0-r3 = args from a stopped context; returns r0."""
        mu = self.mu
        saved = [mu.reg_read(r) for r in REGS]
        cpsr = mu.reg_read(A.UC_ARM_REG_CPSR)
        fp = [mu.reg_read(getattr(A, f'UC_ARM_REG_D{i}')) for i in range(16)]
        fpscr = mu.reg_read(A.UC_ARM_REG_FPSCR)
        mu.reg_write(A.UC_ARM_REG_SP, (saved[13] - 0x80) & ~7)
        for r, v in zip((A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3), args):
            mu.reg_write(r, v & 0xFFFFFFFF)
        mu.reg_write(A.UC_ARM_REG_LR, ISR_RET | 1)
        try:
            mu.emu_start(addr | 1, ISR_RET, count=max_insns)
            ret = mu.reg_read(A.UC_ARM_REG_R0)
            ok = (self.pc() & ~1) == ISR_RET
        finally:
            for r, v in zip(REGS, saved):
                if r != A.UC_ARM_REG_PC:
                    mu.reg_write(r, v)
            for i, v in enumerate(fp):
                mu.reg_write(getattr(A, f'UC_ARM_REG_D{i}'), v)
            mu.reg_write(A.UC_ARM_REG_FPSCR, fpscr)
            mu.reg_write(A.UC_ARM_REG_CPSR, cpsr)
            mu.reg_write(A.UC_ARM_REG_PC, saved[15] | 1)
        if not ok:
            self.stopped = self.stopped or f'call {addr:#x} did not return (pc {self.pc():#x})'
        return ret

    def async_call(self, addr, *args, done=None):
        """Start firmware function `addr`(r0-r3 = args) from the current context, as if the code
        at PC had called it, and keep running normally (SysTick and peripherals keep going, so the
        function may block on time or run nested main loops). When it returns, every register is
        restored and `done(r0)` runs. Only valid at a function-call boundary (a safe point)."""
        mu = self.mu
        if self.async_ctx is not None:
            raise RuntimeError('async_call already in progress')
        saved = [mu.reg_read(r) for r in REGS]
        cpsr = mu.reg_read(A.UC_ARM_REG_CPSR)
        fp = [mu.reg_read(getattr(A, f'UC_ARM_REG_D{i}')) for i in range(16)]
        fpscr = mu.reg_read(A.UC_ARM_REG_FPSCR)
        self.async_ctx = (saved, cpsr, fp, fpscr, done)
        mu.reg_write(A.UC_ARM_REG_SP, (saved[13] - 0x80) & ~7)
        for r, v in zip((A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3), args):
            mu.reg_write(r, v & 0xFFFFFFFF)
        mu.reg_write(A.UC_ARM_REG_LR, ASYNC_RET | 1)
        mu.reg_write(A.UC_ARM_REG_PC, addr | 1)

    def _async_ret(self, mu, address, size, _):
        if self.async_ctx is None:
            return
        saved, cpsr, fp, fpscr, done = self.async_ctx
        self.async_ctx = None
        ret = mu.reg_read(A.UC_ARM_REG_R0)
        for r, v in zip(REGS, saved):
            if r != A.UC_ARM_REG_PC:
                mu.reg_write(r, v)
        for i, v in enumerate(fp):
            mu.reg_write(getattr(A, f'UC_ARM_REG_D{i}'), v)
        mu.reg_write(A.UC_ARM_REG_FPSCR, fpscr)
        mu.reg_write(A.UC_ARM_REG_CPSR, cpsr)
        mu.reg_write(A.UC_ARM_REG_PC, saved[15] | 1)
        if done:
            done(ret)

    @property
    def vtor(self):
        v = self.mmio_w.get(0xE000ED08, 0)
        return v or self.base

    # ---- time ----------------------------------------------------------------------------------
    INSNS_PER_MS = 100_000        # simulated time: one SysTick per 100k instructions
    CYCLES_PER_INSN = 6           # DWT CYCCNT scaled to a 600 MHz core

    def cyccnt(self):
        self._cyc_reads = getattr(self, '_cyc_reads', 0) + 1
        return (self.icount * self.CYCLES_PER_INSN + self._cyc_reads * 4) & 0xFFFFFFFF

    def systick_enabled(self):
        csr = self.mmio_w.get(0xE000E010, 0)
        return (csr & 3) == 3

    def tick(self):
        """Advance simulated time by 1 ms: SysTick plus any registered periodic handlers."""
        self.ms = getattr(self, 'ms', 0) + 1
        if self.irq_masked():
            self.pending.add(15)
            return
        if self.systick_enabled() or 15 in self.pending:
            self.pending.discard(15)
            self.ipsr = 15
            ok = self.call_isr(15)
            self.ipsr = 0
            if not ok:
                self.stopped = self.stopped or f'SysTick handler did not return ({self.pc():#x})'
        for fn in getattr(self, 'periodic', []):
            fn(self)

    # ---- run -----------------------------------------------------------------------------------
    def run(self, insns, chunk=None, on_chunk=None):
        mu = self.mu
        chunk = chunk or self.INSNS_PER_MS
        done = 0
        while done < insns and not self.stopped:
            pc = self.pc() | 1
            try:
                mu.emu_start(pc, 0xFFFFFFFF, count=chunk)
            except UcError as e:
                self.stopped = self.stopped or f'{e} at pc {self.pc():#x}'
                break
            if getattr(self, 'at_safe_point', False):
                self.at_safe_point = False
                self.deliver_inputs()
            done += chunk
            self.icount += chunk
            self.cycles += chunk
            self._chunk_acc = getattr(self, '_chunk_acc', 0) + chunk
            while self._chunk_acc >= self.INSNS_PER_MS and not self.stopped:
                self._chunk_acc -= self.INSNS_PER_MS
                self.tick()
            if on_chunk:
                on_chunk(self)
        return done


# ---- input ------------------------------------------------------------------------------------
class Inputs:
    """Queue of input events delivered at the main loop's input-poll call (a safe point)."""
    def __init__(self, vp, listener, vtbl_slots, safe_point):
        self.vp, self.listener, self.slots = vp, listener, vtbl_slots
        self.queue = []
        vp.inputs = self
        vp.delivering = False
        vp.hook_addr(safe_point, self._safe)
        vp.deliver_inputs = self.deliver

    def _safe(self, vp):
        if self.queue and not getattr(vp, 'delivering', False):
            vp.at_safe_point = True
            vp.mu.emu_stop()

    def deliver(self):
        """At the safe point: start the next queued handler as a real call (async_call), so a
        handler that blocks (sample loading, progress loops) runs with time advancing. The next
        event goes in when the main loop reaches the safe point again."""
        vp = self.vp
        if vp.delivering or not self.queue:
            return
        slot, args = self.queue.pop(0)
        vt = vp.r32(self.listener)
        fn = vp.r32(vt + 4 * slot)
        vp.delivering = True
        self.delivered = getattr(self, 'delivered', 0) + 1

        def done(_ret):
            vp.delivering = False
        vp.async_call(fn, self.listener, *args, done=done)

    def idle(self):
        return not self.queue and not self.vp.delivering

    def button(self, btn, state):
        self.queue.append((self.slots['button'], (btn, state)))

    def pad(self, row, col, state):
        self.queue.append((self.slots['pad'], (row * 20 + col, state)))

    def knob(self, knob, delta):
        self.queue.append((self.slots['knob'], (knob, delta & 0xFFFFFFFF)))

    def touch(self, knob, state):
        """knob touch (listener slot 3): state 0 touched, 1 released, 2 held"""
        self.queue.append((self.slots['track'], (knob, state)))

    def encoder(self, delta):
        self.queue.append((self.slots['encoder'], (0, delta & 0xFFFFFFFF)))


# ---- snapshots -------------------------------------------------------------------------------
SNAP_REGIONS = [(0x00000000, 512 * KB), (0x20000000, 512 * KB), (0x20200000, 1 * MB),
                (0x80000000, 32 * MB), (0x40000000, 4 * MB), (0x42000000, 64 * KB), (0xE0000000, 1 * MB)]


def save_snapshot(vp, path):
    """Registers, RAM, peripheral register files and host-side state (flash is reloaded)."""
    import pickle
    import zlib
    mu = vp.mu
    regs = {name: mu.reg_read(getattr(A, name)) for name in dir(A)
            if name.startswith('UC_ARM_REG_') and name[11:] in
            [f'R{i}' for i in range(13)] + ['SP', 'LR', 'PC', 'CPSR', 'FPSCR', 'FPEXC', 'C1_C0_2'] +
            [f'D{i}' for i in range(32)]}
    mem = {base: zlib.compress(bytes(mu.mem_read(base, size)), 1) for base, size in SNAP_REGIONS}
    host = dict(mmio_w=vp.mmio_w, ms=getattr(vp, 'ms', 0), icount=vp.icount, cycles=vp.cycles,
                sysregs=vp.sysregs, ipsr=vp.ipsr, pending=vp.pending, gpio_in=getattr(vp, 'gpio_in', {}),
                chunk_acc=getattr(vp, '_chunk_acc', 0), cyc_reads=getattr(vp, '_cyc_reads', 0),
                spi_rx={b: s.rx for b, s in getattr(vp, 'spi', {}).items()},
                extra=getattr(vp, 'snap_extra', {}),
                async_ctx=vp.async_ctx[:4] if vp.async_ctx else None,
                inputs=(list(vp.inputs.queue), vp.delivering) if getattr(vp, 'inputs', None) else None,
                display=vp.display.state() if getattr(vp, 'display', None) else None,
                eve=eve_state(vp))
    with open(path, 'wb') as f:
        pickle.dump(dict(regs=regs, mem=mem, host=host), f, protocol=4)


def load_snapshot(vp, path):
    import pickle
    import zlib
    with open(path, 'rb') as f:
        d = pickle.load(f)
    mu = vp.mu
    for base, blob in d['mem'].items():
        mu.mem_write(base, zlib.decompress(blob))
    for name, v in d['regs'].items():
        if name != 'UC_ARM_REG_PC':
            mu.reg_write(getattr(A, name), v)
    mu.reg_write(A.UC_ARM_REG_PC, d['regs']['UC_ARM_REG_PC'])
    h = d['host']
    vp.mmio_w, vp.ms, vp.icount, vp.cycles = h['mmio_w'], h['ms'], h['icount'], h['cycles']
    vp.sysregs, vp.ipsr, vp.pending = h['sysregs'], h['ipsr'], h['pending']
    vp.gpio_in = h['gpio_in']
    vp._chunk_acc, vp._cyc_reads = h['chunk_acc'], h['cyc_reads']
    for b, rx in h['spi_rx'].items():
        if b in getattr(vp, 'spi', {}):
            vp.spi[b].rx = rx
    vp.snap_extra = h.get('extra', {})
    if h.get('async_ctx'):
        # a handler started by async_call was still running: finish it the same way
        def done(_ret):
            vp.delivering = False
        vp.async_ctx = tuple(h['async_ctx']) + (done,)
    if h.get('inputs') and getattr(vp, 'inputs', None):
        vp.inputs.queue, vp.delivering = list(h['inputs'][0]), h['inputs'][1]
    if h.get('display') and getattr(vp, 'display', None):
        vp.display.restore(h['display'])
    if h.get('eve') and getattr(vp, 'eve', None):
        eve_restore(vp, h['eve'])


def eve_state(vp):
    import zlib
    e = getattr(vp, 'eve', None)
    if e is None:
        return None
    r = e.renderer
    return dict(mem=zlib.compress(bytes(e.mem), 1), state={k: v for k, v in e.state.items() if k != 'widgets'},
                dl_ptr=e.dl_ptr, swaps=e.swaps,
                handles=[dict((k, getattr(hd, k)) for k in hd.__slots__) for hd in r.handles] if r else None)


def eve_restore(vp, st):
    import zlib
    e = vp.eve
    m = zlib.decompress(st['mem'])
    e.mem[:len(m)] = m
    e.state.update(st['state'])
    e.dl_ptr, e.swaps = st['dl_ptr'], st['swaps']
    if st.get('handles'):
        import eve_render
        e.renderer = eve_render.Renderer()
        for hd, d in zip(e.renderer.handles, st['handles']):
            for k, v in d.items():
                setattr(hd, k, v)
