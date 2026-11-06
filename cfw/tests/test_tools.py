#!/usr/bin/env python3
"""Unit tests for the CFW toolchain (ppf.py, link.py). Run: python3 cfw/tests/test_tools.py"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOP = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
from ppf import PPF, HexError, diff  # noqa: E402
from link import Linker, decode_bl, encode_bl, decode_movw, encode_movw  # noqa: E402

PLAYPLUS = os.path.join(TOP, 'PlayPlus_v1.3.0.ppf')
PLAY = os.path.join(TOP, 'stock_firmware', 'PolyendPlay_v1.6.0.ppf')


class TestPPF(unittest.TestCase):
    def test_identity(self):
        for p in (PLAYPLUS, PLAY):
            a = PPF(p)
            self.assertEqual(a.patched({}).text, a.text)

    def test_patch_changes_only_target_record(self):
        a = PPF(PLAYPLUS)
        b = a.patched({0x6007820C: b'Z'})
        ch, lines, ins = diff(a, b)
        self.assertEqual(ch, [(0x6007820C, 0x30, 0x5A)])
        self.assertEqual(len(lines), 1)
        self.assertEqual(ins, [])
        self.assertEqual(len(a.text), len(b.text))

    def test_uncovered_patch_rejected(self):
        with self.assertRaises(HexError):
            PPF(PLAYPLUS).patched({0x60190A64: b'\0'})

    def test_append_inserts_only_new_lines(self):
        a = PPF(PLAY)
        b = a.appended(0xB069C, {0xB0700: bytes(range(40))})
        ch, lines, ins = diff(a, b)
        self.assertEqual((ch, lines), ([], []))
        self.assertEqual([r.addr for _, r in ins], [0xB0700, 0xB0710, 0xB0720])
        self.assertEqual(b.read(0xB0700, 40), bytes(range(40)))
        self.assertEqual(b.image(0x60020000), a.image(0x60020000))
        # inserted right before the K66 EOF record
        self.assertEqual(b.lines[ins[-1][0] + 1], b':00000001FF')

    def test_append_overlap_rejected(self):
        with self.assertRaises(HexError):
            PPF(PLAY).appended(0xB069C, {0xB0690: b'\0' * 4})


class TestThumbFields(unittest.TestCase):
    def test_out_of_range_branch_rejected(self):
        from link import LinkError
        buf = bytearray(4)
        with self.assertRaises(LinkError):
            encode_bl(buf, 0, -0x6015968e, True)

    def test_bl_roundtrip(self):
        buf = bytearray(4)
        for v in (-4, 0, 2, 0x1234, -0x123456, 0xFFFFFE, -0x1000000):
            encode_bl(buf, 0, v, True)
            self.assertEqual(decode_bl(buf, 0), v)

    def test_movw_roundtrip(self):
        buf = bytearray(struct.pack('<HH', 0xF240, 0x0000))
        for v in (0, 1, 0x7FFF, 0x1234, 0xABCD):
            encode_movw(buf, 0, v)
            self.assertEqual(decode_movw(buf, 0) & 0xFFFF, v)


@unittest.skipUnless(shutil.which('clang') and shutil.which('ld.lld'), 'needs clang and ld.lld')
class TestLinkerAgainstLld(unittest.TestCase):
    def test_slice_objects_match_lld(self):
        src = os.path.join(HERE, '..', 'src', 'slice.c')
        for inc, cpu, base in (('playplus-1.3.0', 'cortex-m7', '0x6018cc00'),
                               ('play-1.6.0/rt1062', 'cortex-m7', '0x600d5200'),
                               ('play-1.6.0/k66', 'cortex-m4', '0xb0700')):
            with tempfile.TemporaryDirectory() as d:
                o = os.path.join(d, 'x.o')
                subprocess.run(['clang', '-target', 'thumbv7em-none-eabihf', f'-mcpu={cpu}', '-mfloat-abi=hard',
                                '-mthumb', '-Os', '-ffreestanding', '-fno-builtin', '-ffunction-sections',
                                '-fdata-sections', '-I', os.path.join(HERE, '..', 'src'), '-I',
                                os.path.join(HERE, '..', 'targets', inc), '-c', src, '-o', o], check=True)
                syms = os.path.join(HERE, '..', 'targets', inc, 'symbols.ld')
                r = subprocess.run([sys.executable, os.path.join(HERE, 'lld_crosscheck.py'), '--base', base,
                                    '--syms', syms, o], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
