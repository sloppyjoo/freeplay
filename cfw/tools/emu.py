#!/usr/bin/env python3
"""Unicorn harness that runs patched firmware code (stock + CFW) off-device.

Maps the application image from a .ppf at its link address, mirrors the ITCM and DTCM .data
init images where the startup code would copy them, gives the code RAM, and lets tests replace
individual firmware functions with Python stubs. Calls use the AAPCS: r0-r3 then stack.

Only code paths that do not touch peripherals can run here. Anything that would (allocator,
toast widget, undo snapshot of the live pattern, NVIC writes) is stubbed and recorded.
"""
import struct

from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, \
    UC_HOOK_MEM_UNMAPPED, UC_HOOK_MEM_WRITE
from unicorn import arm_const as A

PAGE = 0x1000
RET = 0x0FFF0000          # return trampoline: calls end when PC reaches it
STUBS = 0x0F000000        # fake code addresses for Python stubs (vtable entries)
HEAP = 0x81000000         # fake heap (SDRAM2 window, unused by the stubs' callers)
STACK_TOP = 0x2005F000


def up(n):
    return (n + PAGE - 1) & ~(PAGE - 1)


class EmuError(Exception):
    pass


class Emu:
    def __init__(self, image, base, mirrors=(), ram=(), cpu=A.UC_CPU_ARM_CORTEX_M7, stack_top=STACK_TOP):
        self.mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        self.mu.ctl_set_cpu_model(cpu)
        self.base = base
        self.stack_top = stack_top
        self.mu.mem_map(base & ~(PAGE - 1), up(len(image) + (base & (PAGE - 1))))
        self.mu.mem_write(base, bytes(image))
        for rbase, size in ram:
            self.mu.mem_map(rbase, up(size))
        for dst, src, n in mirrors:
            self.mu.mem_write(dst, bytes(image[src - base:src - base + n]))
        self.mu.mem_map(RET, PAGE)
        self.mu.mem_write(RET, b'\x00\xbf' * (PAGE // 2))
        self.mu.mem_map(STUBS, 0x10000)
        self.mu.mem_write(STUBS, b'\x00\xbf' * 0x8000)
        self.mu.mem_map(HEAP, 0x100000)
        self.heap = HEAP
        self.stubs = {}
        self.next_stub = STUBS
        self.trace = []           # (name, args) of stub calls in order
        self.icount = 0
        self.mu.hook_add(UC_HOOK_MEM_UNMAPPED, self._unmapped)
        self.mu.hook_add(UC_HOOK_CODE, self._count)

    # ---- memory ---------------------------------------------------------------------------
    def r32(self, a): return struct.unpack('<I', self.mu.mem_read(a, 4))[0]
    def r16s(self, a): return struct.unpack('<h', self.mu.mem_read(a, 2))[0]
    def r8(self, a): return self.mu.mem_read(a, 1)[0]
    def w32(self, a, v): self.mu.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))
    def w8(self, a, v): self.mu.mem_write(a, bytes([v & 0xFF]))
    def write(self, a, b): self.mu.mem_write(a, bytes(b))

    def cstr(self, a, n=128):
        b = bytes(self.mu.mem_read(a, n))
        return b[:b.index(0)].decode('latin1') if 0 in b else b.decode('latin1')

    def alloc(self, size, align=8, fill=0):
        self.heap = (self.heap + align - 1) & ~(align - 1)
        a = self.heap
        self.heap += size
        self.write(a, bytes([fill]) * size)
        return a

    def string(self, s):
        b = s.encode() + b'\0'
        a = self.alloc(len(b), 1)
        self.write(a, b)
        return a

    # ---- stubs ----------------------------------------------------------------------------
    def stub(self, addr, fn, name=None):
        """Replace the Thumb function at `addr` (odd or even) with Python `fn(emu, r0..r3)`."""
        a = addr & ~1
        self.stubs[a] = (fn, name or hex(addr))
        self.mu.hook_add(UC_HOOK_CODE, self._stub_hit, begin=a, end=a)
        return a | 1

    def fake_fn(self, fn, name):
        a = self.next_stub
        self.next_stub += 4
        return self.stub(a | 1, fn, name)

    def fake_vtable(self, entries, size=0x80):
        """entries: {byte_offset: (python_fn, name) or int address}. Returns vtable address."""
        vt = self.alloc(size, 4)
        for off, e in entries.items():
            self.w32(vt + off, e if isinstance(e, int) else self.fake_fn(*e))
        return vt

    def _stub_hit(self, mu, addr, size, _):
        if addr not in self.stubs:
            return
        fn, name = self.stubs[addr]
        args = [mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3)]
        self.trace.append((name, args))
        ret = fn(self, *args)
        mu.reg_write(A.UC_ARM_REG_R0, (ret or 0) & 0xFFFFFFFF)
        mu.reg_write(A.UC_ARM_REG_PC, mu.reg_read(A.UC_ARM_REG_LR))

    def _count(self, mu, addr, size, _):
        self.icount += 1

    def _unmapped(self, mu, access, addr, size, value, _):
        pc = mu.reg_read(A.UC_ARM_REG_PC)
        raise EmuError(f'unmapped access {addr:#x} (size {size}) at pc {pc:#x}')

    # ---- calls ----------------------------------------------------------------------------
    def call(self, addr, *args, max_insns=2_000_000):
        mu = self.mu
        regs = (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3)
        sp = self.stack_top
        extra = list(args[4:])
        sp -= 4 * len(extra)
        sp &= ~7
        for i, v in enumerate(extra):
            self.w32(sp + 4 * i, v)
        for r, v in zip(regs, args[:4]):
            mu.reg_write(r, v & 0xFFFFFFFF)
        mu.reg_write(A.UC_ARM_REG_SP, sp)
        mu.reg_write(A.UC_ARM_REG_LR, RET | 1)
        try:
            mu.emu_start(addr | 1, RET, count=max_insns)
        except UcError as e:
            pc = mu.reg_read(A.UC_ARM_REG_PC)
            raise EmuError(f'{e} at pc {pc:#x} (lr {mu.reg_read(A.UC_ARM_REG_LR):#x})') from e
        pc = mu.reg_read(A.UC_ARM_REG_PC)
        if pc & ~1 != RET:
            raise EmuError(f'did not return (pc {pc:#x}), instruction limit?')
        if mu.reg_read(A.UC_ARM_REG_SP) != sp:
            raise EmuError(f'stack not balanced: {mu.reg_read(A.UC_ARM_REG_SP):#x} != {sp:#x}')
        return mu.reg_read(A.UC_ARM_REG_R0)
