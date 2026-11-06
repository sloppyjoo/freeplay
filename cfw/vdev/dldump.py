"""Decode an EVE display list to text."""
import struct

PRIMS = {1: 'BITMAPS', 2: 'POINTS', 3: 'LINES', 4: 'LINE_STRIP', 5: 'EDGE_R', 6: 'EDGE_L', 7: 'EDGE_A', 8: 'EDGE_B', 9: 'RECTS'}
OPS = {0x00: 'DISPLAY', 0x01: 'BITMAP_SOURCE', 0x02: 'CLEAR_COLOR_RGB', 0x03: 'TAG', 0x04: 'COLOR_RGB',
       0x05: 'BITMAP_HANDLE', 0x06: 'CELL', 0x07: 'BITMAP_LAYOUT', 0x08: 'BITMAP_SIZE', 0x09: 'ALPHA_FUNC',
       0x0A: 'STENCIL_FUNC', 0x0B: 'BLEND_FUNC', 0x0C: 'STENCIL_OP', 0x0D: 'POINT_SIZE', 0x0E: 'LINE_WIDTH',
       0x0F: 'CLEAR_COLOR_A', 0x10: 'COLOR_A', 0x11: 'CLEAR_STENCIL', 0x12: 'CLEAR_TAG', 0x13: 'STENCIL_MASK',
       0x14: 'TAG_MASK', 0x15: 'XF_A', 0x16: 'XF_B', 0x17: 'XF_C', 0x18: 'XF_D', 0x19: 'XF_E', 0x1A: 'XF_F',
       0x1B: 'SCISSOR_XY', 0x1C: 'SCISSOR_SIZE', 0x1D: 'CALL', 0x1E: 'JUMP', 0x1F: 'BEGIN', 0x20: 'COLOR_MASK',
       0x21: 'END', 0x22: 'SAVE_CONTEXT', 0x23: 'RESTORE_CONTEXT', 0x24: 'RETURN', 0x25: 'MACRO', 0x26: 'CLEAR',
       0x27: 'VERTEX_FORMAT', 0x28: 'BITMAP_LAYOUT_H', 0x29: 'BITMAP_SIZE_H', 0x2A: 'PALETTE_SOURCE',
       0x2B: 'VTRANSLATE_X', 0x2C: 'VTRANSLATE_Y', 0x2D: 'NOP', 0x2E: 'BITMAP_EXT_FORMAT', 0x2F: 'BITMAP_SWIZZLE'}


def decode(dl, limit=2048):
    out = []
    for i in range(min(len(dl) // 4, limit)):
        w = struct.unpack_from('<I', dl, i * 4)[0]
        top = w >> 30
        if top == 1:
            x, y = (w >> 15) & 0x7FFF, w & 0x7FFF
            x = x - 0x8000 if x & 0x4000 else x
            y = y - 0x8000 if y & 0x4000 else y
            out.append(f'{i:4} VERTEX2F({x}, {y})')
        elif top == 2:
            c = w & 127
            out.append(f'{i:4} VERTEX2II({(w >> 21) & 511}, {(w >> 12) & 511}, h={(w >> 7) & 31}, c={c}'
                       f"{' ' + repr(chr(c + 32)) if c + 32 < 127 else ''})")
        else:
            op = w >> 24
            name = OPS.get(op, f'op{op:#x}')
            arg = w & 0xFFFFFF
            if op == 0x1F:
                out.append(f'{i:4} BEGIN({PRIMS.get(arg & 15, arg)})')
            elif op in (0x04, 0x02):
                out.append(f'{i:4} {name}(#{arg:06x})')
            else:
                out.append(f'{i:4} {name}({arg:#x})')
            if op == 0x00:
                break
    return out
