"""EVE display-list renderer (FT81x/BT81x subset) for the virtual Play+.

Executes RAM_DL into a 320x240 logical canvas (the Play+ panel is 240x320 with REG_ROTATE=6).
Implements what the Play+ firmware and the CFW use: CLEAR*, COLOR_RGB/A, BEGIN/END with BITMAPS,
POINTS, LINES, LINE_STRIP, RECTS, EDGE_STRIP_*, VERTEX2F/2II, VERTEX_FORMAT/TRANSLATE, bitmap
handle state (persistent across frames, like the hardware handle table), BITMAP_TRANSFORM (scale
and translate only), SCISSOR, SAVE/RESTORE_CONTEXT, CALL/JUMP/RETURN, BLEND_FUNC, COLOR_MASK.
ROM fonts (handles 16-31) are approximated with DejaVu Sans; custom fonts render exactly.
"""
import struct

import numpy as np

W, H = 320, 240
FMT_BPP = {0: 16, 1: 1, 2: 4, 3: 8, 4: 8, 5: 8, 6: 16, 7: 16, 9: 8, 14: 8, 15: 8, 16: 8, 17: 2}
ROM_HEIGHT = {16: 8, 17: 8, 18: 16, 19: 16, 20: 13, 21: 17, 22: 20, 23: 22, 24: 29, 25: 38, 26: 16,
              27: 20, 28: 25, 29: 28, 30: 36, 31: 49}


_ROM_W = {}
_FONT_PATHS = ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',          # Linux (cloud runs)
               '/Library/Fonts/DejaVuSans.ttf',
               '/System/Library/Fonts/Supplemental/Verdana.ttf',            # macOS: Vera-like metrics
               '/System/Library/Fonts/Supplemental/Arial.ttf')


def rom_font(size):
    from PIL import ImageFont
    for p in _FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def rom_width(handle, ch):
    key = (handle, ch)
    if key not in _ROM_W:
        from PIL import ImageFont
        h = ROM_HEIGHT.get(handle, 16)
        f = rom_font(max(6, int(h * 0.72)))
        _ROM_W[key] = max(1, int(f.getlength(chr(ch)) + 1))
    return _ROM_W[key]


def sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


class Handle:
    __slots__ = ('source', 'fmt', 'stride', 'lheight', 'width', 'height', 'filter', 'wrapx', 'wrapy')

    def __init__(self):
        self.source = 0
        self.fmt = 3
        self.stride = 0
        self.lheight = 0
        self.width = 0
        self.height = 0
        self.filter = 0
        self.wrapx = self.wrapy = 0


class Ctx:
    def __init__(self):
        self.color = (255, 255, 255)
        self.alpha = 255
        self.clear_color = (0, 0, 0)
        self.clear_alpha = 0
        self.point_size = 16
        self.line_width = 16
        self.handle = 0
        self.cell = 0
        self.scissor = (0, 0, W, H)
        self.vfmt = 4
        self.tx = 0
        self.ty = 0
        self.blend = (2, 4)
        self.cmask = (1, 1, 1, 1)
        self.tag = 255
        self.xform = (256, 0, 0, 0, 256, 0)

    def copy(self):
        c = Ctx()
        c.__dict__.update(self.__dict__)
        return c


class Renderer:
    def __init__(self):
        self.handles = [Handle() for _ in range(32)]
        self.rom_fonts = {}
        self.frames = 0
        self.img = np.zeros((H, W, 3), dtype=np.float32)
        self.unknown = set()

    # ---- glyph sources --------------------------------------------------------------------
    def rom_glyph(self, handle, ch):
        from PIL import Image, ImageDraw, ImageFont
        key = (handle, ch)
        if key in self.rom_fonts:
            return self.rom_fonts[key]
        h = ROM_HEIGHT.get(handle, 16)
        f = rom_font(max(6, int(h * 0.72)))
        w = max(1, int(f.getlength(chr(ch)) + 1))
        im = Image.new('L', (w, h), 0)
        ImageDraw.Draw(im).text((0, int(h * 0.08)), chr(ch), fill=255, font=f)
        a = np.asarray(im, dtype=np.float32) / 255.0
        self.rom_fonts[key] = a
        return a

    def bitmap(self, mem, hd, cell):
        """Returns (rgb or None, alpha) arrays of the cell's pixels, shape (h, w)."""
        w = hd.width or 512
        h = hd.height or 512
        stride = hd.stride
        lh = hd.lheight or h
        base = (hd.source + cell * stride * lh) & 0xFFFFFF   # EVE address arithmetic wraps
        rows = min(h, lh)
        fmt = hd.fmt
        if stride == 0 or base + stride * rows > len(mem):
            return None, None
        raw = np.frombuffer(bytes(mem[base:base + stride * rows]), dtype=np.uint8).reshape(rows, stride)
        if fmt == 2:      # L4, high nibble first
            px = np.empty((rows, stride * 2), dtype=np.uint8)
            px[:, 0::2] = raw >> 4
            px[:, 1::2] = raw & 0xF
            a = px[:, :w].astype(np.float32) / 15.0
            return None, a
        if fmt == 3:      # L8
            return None, raw[:, :w].astype(np.float32) / 255.0
        if fmt == 1:      # L1, msb first
            bits = np.unpackbits(raw, axis=1)[:, :w]
            return None, bits.astype(np.float32)
        if fmt == 17:     # L2
            px = np.empty((rows, stride * 4), dtype=np.uint8)
            for k in range(4):
                px[:, k::4] = (raw >> (6 - 2 * k)) & 3
            return None, px[:, :w].astype(np.float32) / 3.0
        if fmt == 7:      # RGB565
            v = raw.view('<u2')[:, :w].astype(np.uint32)
            rgb = np.stack([((v >> 11) & 31) * 255 / 31, ((v >> 5) & 63) * 255 / 63, (v & 31) * 255 / 31], -1)
            return rgb.astype(np.float32), np.ones((rows, w), dtype=np.float32)
        if fmt == 0:      # ARGB1555
            v = raw.view('<u2')[:, :w].astype(np.uint32)
            rgb = np.stack([((v >> 10) & 31) * 255 / 31, ((v >> 5) & 31) * 255 / 31, (v & 31) * 255 / 31], -1)
            return rgb.astype(np.float32), ((v >> 15) & 1).astype(np.float32)
        if fmt == 6:      # ARGB4
            v = raw.view('<u2')[:, :w].astype(np.uint32)
            rgb = np.stack([((v >> 8) & 15) * 17, ((v >> 4) & 15) * 17, (v & 15) * 17], -1)
            return rgb.astype(np.float32), ((v >> 12) & 15).astype(np.float32) / 15.0
        if fmt == 4:      # RGB332
            v = raw[:, :w].astype(np.uint32)
            rgb = np.stack([((v >> 5) & 7) * 255 / 7, ((v >> 2) & 7) * 255 / 7, (v & 3) * 255 / 3], -1)
            return rgb.astype(np.float32), np.ones((rows, w), dtype=np.float32)
        self.unknown.add(f'bitmap format {fmt}')
        return None, None

    # ---- compositing ------------------------------------------------------------------------
    def blit(self, ctx, x0, y0, rgb, a):
        h, w = a.shape
        sx0, sy0, sw, sh = ctx.scissor
        cx0, cy0 = max(x0, sx0, 0), max(y0, sy0, 0)
        cx1, cy1 = min(x0 + w, sx0 + sw, W), min(y0 + h, sy0 + sh, H)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        a = a[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0] * (ctx.alpha / 255.0)
        col = np.array(ctx.color, dtype=np.float32)
        src = col if rgb is None else rgb[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0] * (col / 255.0)
        dst = self.img[cy0:cy1, cx0:cx1]
        sf, df = ctx.blend
        a3 = a[..., None]
        if (sf, df) == (2, 4):
            out = src * a3 + dst * (1 - a3)
        elif (sf, df) == (2, 1):
            out = dst + src * a3
        elif (sf, df) == (1, 0):
            out = np.broadcast_to(src, dst.shape).copy()
        else:
            out = src * a3 + dst * (1 - a3)
        mask = np.array(ctx.cmask[:3], dtype=bool)
        dst[..., mask] = np.clip(out, 0, 255)[..., mask]

    def coverage(self, ctx, fn, bbox):
        x0, y0, x1, y1 = bbox
        x0, y0 = max(int(np.floor(x0)), 0), max(int(np.floor(y0)), 0)
        x1, y1 = min(int(np.ceil(x1)) + 1, W), min(int(np.ceil(y1)) + 1, H)
        if x1 <= x0 or y1 <= y0:
            return
        ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        a = np.clip(fn(xs + 0.5, ys + 0.5), 0, 1)
        self.blit(ctx, x0, y0, None, a)

    # ---- primitives ---------------------------------------------------------------------------
    def draw_points(self, ctx, v):
        r = ctx.point_size / 16.0
        for x, y in v:
            self.coverage(ctx, lambda X, Y: r + 0.5 - np.hypot(X - x, Y - y), (x - r - 1, y - r - 1, x + r + 1, y + r + 1))

    def draw_line(self, ctx, a, b):
        hw = ctx.line_width / 16.0
        (x0, y0), (x1, y1) = a, b
        dx, dy = x1 - x0, y1 - y0
        L2 = dx * dx + dy * dy or 1e-9

        def fn(X, Y):
            t = np.clip(((X - x0) * dx + (Y - y0) * dy) / L2, 0, 1)
            return hw + 0.5 - np.hypot(X - (x0 + t * dx), Y - (y0 + t * dy))
        self.coverage(ctx, fn, (min(x0, x1) - hw - 1, min(y0, y1) - hw - 1, max(x0, x1) + hw + 1, max(y0, y1) + hw + 1))

    def draw_rect(self, ctx, a, b):
        r = max(ctx.line_width / 16.0, 1.0)
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        # EVE rects: corners at vertex centers, rounded by the line width
        rx0, ry0, rx1, ry1 = x0 - r + 1, y0 - r + 1, x1 + r - 1, y1 + r - 1
        cr = r

        def fn(X, Y):
            qx = np.maximum(np.maximum(rx0 + cr - X, X - (rx1 - cr)), 0)
            qy = np.maximum(np.maximum(ry0 + cr - Y, Y - (ry1 - cr)), 0)
            d = np.hypot(qx, qy) - cr
            inside = np.minimum(np.minimum(X - rx0, rx1 - X), np.minimum(Y - ry0, ry1 - Y))
            return np.where((qx > 0) & (qy > 0), 0.5 - d, inside + 0.5)
        self.coverage(ctx, fn, (rx0 - 1, ry0 - 1, rx1 + 1, ry1 + 1))

    def draw_edge_strip(self, ctx, v, kind):
        if len(v) < 2:
            return
        ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
        xs += 0.5
        ys += 0.5
        a = np.zeros((H, W), dtype=np.float32)
        for (x0, y0), (x1, y1) in zip(v, v[1:]):
            if kind in ('R', 'L'):
                lo, hi = sorted((y0, y1))
                if hi == lo:
                    continue
                t = (ys - y0) / (y1 - y0)
                xe = x0 + t * (x1 - x0)
                band = (ys >= lo) & (ys < hi)
                a = np.where(band & ((xs >= xe) if kind == 'R' else (xs <= xe)), 1.0, a)
            else:
                lo, hi = sorted((x0, x1))
                if hi == lo:
                    continue
                t = (xs - x0) / (x1 - x0)
                ye = y0 + t * (y1 - y0)
                band = (xs >= lo) & (xs < hi)
                a = np.where(band & ((ys <= ye) if kind == 'A' else (ys >= ye)), 1.0, a)
        self.blit(ctx, 0, 0, None, a)

    def draw_bitmap(self, ctx, mem, x, y, handle, cell):
        if 16 <= handle <= 31 and self.handles[handle].stride == 0:
            a = self.rom_glyph(handle, cell)
            self.blit(ctx, int(x), int(y), None, a)
            return
        hd = self.handles[handle]
        rgb, a = self.bitmap(mem, hd, cell)
        if a is None:
            return
        A, B, C, D, E, F = ctx.xform
        if (A, B, D, E) != (256, 0, 0, 256):
            # scale-only transforms (zoom); rotation is not used by the firmware
            if B == 0 and D == 0 and A > 0 and E > 0:
                from PIL import Image
                nw, nh = max(1, int(a.shape[1] * 256 / A)), max(1, int(a.shape[0] * 256 / E))
                a = np.asarray(Image.fromarray((a * 255).astype(np.uint8)).resize((nw, nh)), dtype=np.float32) / 255
                if rgb is not None:
                    rgb = np.asarray(Image.fromarray(rgb.astype(np.uint8)).resize((nw, nh)), dtype=np.float32)
            elif (A, B, D, E) in ((0, 256, -256, 0), (0, -256, 256, 0)):
                # exact quarter turns (CFW key tiles: u = y, v = h - 1 - x is clockwise). The draw box
                # (BITMAP_SIZE) is the turned one, so read the cell with the box un-swapped, then turn it.
                hd2 = Handle()
                for f in Handle.__slots__:
                    setattr(hd2, f, getattr(hd, f))
                hd2.width, hd2.height = hd.height, hd.width
                rgb, a = self.bitmap(mem, hd2, cell)
                if a is None:
                    return
                k = -1 if B > 0 else 1
                a = np.ascontiguousarray(np.rot90(a, k))
                if rgb is not None:
                    rgb = np.ascontiguousarray(np.rot90(rgb, k))
            else:
                self.unknown.add('rotating bitmap transform')
        self.blit(ctx, int(round(x)), int(round(y)), rgb, a)

    # ---- display list -----------------------------------------------------------------------
    def render(self, dl, mem):
        self.frames += 1
        ctx = Ctx()
        stack = []
        calls = []
        prim = None
        verts = []
        pc = 0
        n = len(dl) // 4
        steps = 0

        def flush():
            nonlocal verts
            if prim == 9:
                for a, b in zip(verts[0::2], verts[1::2]):
                    self.draw_rect(ctx, a[:2], b[:2])
            elif prim == 3:
                for a, b in zip(verts[0::2], verts[1::2]):
                    self.draw_line(ctx, a[:2], b[:2])
            elif prim == 4:
                for a, b in zip(verts, verts[1:]):
                    self.draw_line(ctx, a[:2], b[:2])
            elif prim in (5, 6, 7, 8):
                self.draw_edge_strip(ctx, [v[:2] for v in verts], 'RLAB'[prim - 5])
            verts = []

        while pc < n and steps < 20000:
            steps += 1
            w = struct.unpack_from('<I', dl, pc * 4)[0]
            pc += 1
            top = w >> 30
            if top == 1 or top == 2:
                if top == 1:
                    f = 1 << ctx.vfmt
                    x = sx(w >> 15, 15) / f + ctx.tx / 16.0
                    y = sx(w, 15) / f + ctx.ty / 16.0
                    handle, cell = ctx.handle, ctx.cell
                else:
                    x = ((w >> 21) & 0x1FF) + ctx.tx / 16.0
                    y = ((w >> 12) & 0x1FF) + ctx.ty / 16.0
                    handle, cell = (w >> 7) & 0x1F, w & 0x7F
                if prim == 1:
                    self.draw_bitmap(ctx, mem, x, y, handle, cell)
                elif prim == 2:
                    self.draw_points(ctx, [(x, y)])
                elif prim is not None:
                    verts.append((x, y))
                    if prim == 9 and len(verts) == 2 or prim == 3 and len(verts) == 2:
                        flush()
                continue
            op = w >> 24
            if op == 0x00:
                break
            elif op == 0x01:
                self.handles[ctx.handle].source = w & 0xFFFFFF
            elif op == 0x02:
                ctx.clear_color = ((w >> 16) & 255, (w >> 8) & 255, w & 255)
            elif op == 0x03:
                ctx.tag = w & 0xFF
            elif op == 0x04:
                ctx.color = ((w >> 16) & 255, (w >> 8) & 255, w & 255)
            elif op == 0x05:
                ctx.handle = w & 0x1F
            elif op == 0x06:
                ctx.cell = w & 0x7F
            elif op == 0x07:
                hd = self.handles[ctx.handle]
                hd.fmt, hd.stride, hd.lheight = (w >> 19) & 0x1F, (hd.stride & ~0x3FF) | ((w >> 9) & 0x3FF), (hd.lheight & ~0x1FF) | (w & 0x1FF)
            elif op == 0x28:
                hd = self.handles[ctx.handle]
                hd.stride = (hd.stride & 0x3FF) | (((w >> 2) & 3) << 10)
                hd.lheight = (hd.lheight & 0x1FF) | ((w & 3) << 9)
            elif op == 0x08:
                hd = self.handles[ctx.handle]
                hd.filter, hd.wrapx, hd.wrapy = (w >> 20) & 1, (w >> 19) & 1, (w >> 18) & 1
                hd.width = (hd.width & ~0x1FF) | ((w >> 9) & 0x1FF)
                hd.height = (hd.height & ~0x1FF) | (w & 0x1FF)
            elif op == 0x29:
                hd = self.handles[ctx.handle]
                hd.width = (hd.width & 0x1FF) | (((w >> 2) & 3) << 9)
                hd.height = (hd.height & 0x1FF) | ((w & 3) << 9)
            elif op == 0x0B:
                ctx.blend = ((w >> 3) & 7, w & 7)
            elif op == 0x0D:
                ctx.point_size = w & 0x1FFF
            elif op == 0x0E:
                ctx.line_width = w & 0xFFF
            elif op == 0x0F:
                ctx.clear_alpha = w & 0xFF
            elif op == 0x10:
                ctx.alpha = w & 0xFF
            elif 0x15 <= op <= 0x1A:
                k = op - 0x15
                x = list(ctx.xform)
                x[k] = sx(w, 24 if k in (2, 5) else 17)
                ctx.xform = tuple(x)
            elif op == 0x1B:
                x, y = (w >> 11) & 0x7FF, w & 0x7FF
                ctx.scissor = (x, y, ctx.scissor[2], ctx.scissor[3])
            elif op == 0x1C:
                ctx.scissor = (ctx.scissor[0], ctx.scissor[1], (w >> 12) & 0xFFF, w & 0xFFF)
            elif op == 0x1D:
                calls.append(pc)
                pc = w & 0xFFFF
            elif op == 0x1E:
                pc = w & 0xFFFF
            elif op == 0x24:
                if calls:
                    pc = calls.pop()
            elif op == 0x1F:
                flush()
                prim = w & 0xF
                verts = []
            elif op == 0x21:
                flush()
                prim = None
            elif op == 0x20:
                ctx.cmask = ((w >> 3) & 1, (w >> 2) & 1, (w >> 1) & 1, w & 1)
            elif op == 0x22:
                stack.append(ctx.copy())
            elif op == 0x23:
                if stack:
                    ctx = stack.pop()
            elif op == 0x26:
                if (w >> 2) & 1:
                    x, y, sw, sh = ctx.scissor
                    self.img[max(y, 0):min(y + sh, H), max(x, 0):min(x + sw, W)] = ctx.clear_color
            elif op == 0x27:
                ctx.vfmt = w & 7
            elif op == 0x2B:
                ctx.tx = sx(w, 17)
            elif op == 0x2C:
                ctx.ty = sx(w, 17)
            elif op in (0x09, 0x0A, 0x0C, 0x11, 0x12, 0x13, 0x14, 0x2D, 0x2A, 0x2E, 0x2F, 0x25):
                pass
            else:
                self.unknown.add(f'dl op {op:#x}')
        flush()
        return self.img

    def image(self, scale=2):
        from PIL import Image
        im = Image.fromarray(np.clip(self.img, 0, 255).astype(np.uint8), 'RGB')
        return im.resize((W * scale, H * scale), Image.NEAREST) if scale != 1 else im
