"""FTDI/Bridgetek EVE (FT81x/BT81x) model for the virtual Play+.

The Play+ draws its UI with an EVE graphics controller on LPSPI3 (chip select GPIO1.28; the
ILI9341 panel behind it is configured on GPIO1.18 and fed by the EVE over RGB). The MCU uploads
fonts/images to RAM_G, writes coprocessor commands into RAM_CMD and display lists into RAM_DL.
This model implements the host SPI protocol, the memory map, the registers the firmware uses and
the coprocessor command stream; rendering of display lists is in eve_render.py.
"""
import struct

RAM_G, RAM_G_SIZE = 0x000000, 0x100000
ROM_CHIPID = 0x0C0000
RAM_DL, RAM_DL_SIZE = 0x300000, 0x2000
RAM_REG = 0x302000
RAM_CMD, RAM_CMD_SIZE = 0x308000, 0x1000

REG = dict(ID=0x302000, FRAMES=0x302004, CLOCK=0x302008, FREQUENCY=0x30200C, CPURESET=0x302020,
           HCYCLE=0x30202C, HOFFSET=0x302030, HSIZE=0x302034, HSYNC0=0x302038, HSYNC1=0x30203C,
           VCYCLE=0x302040, VOFFSET=0x302044, VSIZE=0x302048, VSYNC0=0x30204C, VSYNC1=0x302050,
           DLSWAP=0x302054, ROTATE=0x302058, OUTBITS=0x30205C, DITHER=0x302060, SWIZZLE=0x302064,
           CSPREAD=0x302068, PCLK_POL=0x30206C, PCLK=0x302070, TAG_X=0x302074, TAG_Y=0x302078,
           TAG=0x30207C, GPIO_DIR=0x302090, GPIO=0x302094, GPIOX_DIR=0x302098, GPIOX=0x30209C,
           INT_FLAGS=0x3020A8, INT_EN=0x3020AC, INT_MASK=0x3020B0, PWM_HZ=0x3020D0, PWM_DUTY=0x3020D4,
           CMD_READ=0x3020F8, CMD_WRITE=0x3020FC, CMD_DL=0x302100, TOUCH_MODE=0x302104,
           CMDB_SPACE=0x302574, CMDB_WRITE=0x302578, SPI_WIDTH=0x302188)

CMD_NAMES = {
    0x00: 'DLSTART', 0x01: 'SWAP', 0x02: 'INTERRUPT', 0x09: 'BGCOLOR', 0x0A: 'FGCOLOR', 0x0B: 'GRADIENT',
    0x0C: 'TEXT', 0x0D: 'BUTTON', 0x0E: 'KEYS', 0x0F: 'PROGRESS', 0x10: 'SLIDER', 0x11: 'SCROLLBAR',
    0x12: 'TOGGLE', 0x13: 'GAUGE', 0x14: 'CLOCK', 0x15: 'CALIBRATE', 0x16: 'SPINNER', 0x17: 'STOP',
    0x18: 'MEMCRC', 0x19: 'REGREAD', 0x1A: 'MEMWRITE', 0x1B: 'MEMSET', 0x1C: 'MEMZERO', 0x1D: 'MEMCPY',
    0x1E: 'APPEND', 0x1F: 'SNAPSHOT', 0x22: 'INFLATE', 0x23: 'GETPTR', 0x24: 'LOADIMAGE', 0x25: 'GETPROPS',
    0x26: 'LOADIDENTITY', 0x27: 'TRANSLATE', 0x28: 'SCALE', 0x29: 'ROTATE', 0x2A: 'SETMATRIX',
    0x2B: 'SETFONT', 0x2C: 'TRACK', 0x2D: 'DIAL', 0x2E: 'NUMBER', 0x2F: 'SCREENSAVER', 0x30: 'SKETCH',
    0x31: 'LOGO', 0x32: 'COLDSTART', 0x33: 'GETMATRIX', 0x34: 'GRADCOLOR', 0x36: 'SETROTATE',
    0x37: 'SNAPSHOT2', 0x38: 'SETBASE', 0x39: 'MEDIAFIFO', 0x3A: 'PLAYVIDEO', 0x3B: 'SETFONT2',
    0x3C: 'SETSCRATCH', 0x3F: 'ROMFONT', 0x40: 'VIDEOSTART', 0x41: 'VIDEOFRAME', 0x42: 'SYNC',
    0x43: 'SETBITMAP', 0x50: 'INFLATE2', 0x51: 'ROTATEAROUND', 0x52: 'RESETFONTS', 0x57: 'GRADIENTA',
    0x58: 'FILLWIDTH', 0x5B: 'NOP', 0x63: 'APILEVEL',
}
# argument word counts (fixed part) for commands with inline data handled specially
CMD_ARGS = {0x00: 0, 0x01: 0, 0x02: 1, 0x09: 1, 0x0A: 1, 0x0B: 4, 0x0C: 2, 0x0D: 3, 0x0E: 3, 0x0F: 4,
            0x10: 4, 0x11: 4, 0x12: 3, 0x13: 4, 0x14: 4, 0x15: 1, 0x16: 2, 0x17: 0, 0x18: 3, 0x19: 2,
            0x1A: 2, 0x1B: 3, 0x1C: 2, 0x1D: 3, 0x1E: 2, 0x1F: 1, 0x22: 1, 0x23: 1, 0x24: 2, 0x25: 2,
            0x26: 0, 0x27: 2, 0x28: 2, 0x29: 1, 0x2A: 0, 0x2B: 2, 0x2C: 3, 0x2D: 3, 0x2E: 3, 0x2F: 0,
            0x30: 4, 0x31: 0, 0x32: 0, 0x33: 6, 0x34: 1, 0x36: 1, 0x37: 4, 0x38: 1, 0x39: 2, 0x3A: 1,
            0x3B: 3, 0x3C: 1, 0x3F: 2, 0x40: 0, 0x41: 2, 0x42: 0, 0x43: 3, 0x50: 1, 0x51: 4, 0x52: 0,
            0x57: 4, 0x58: 1, 0x5B: 0, 0x63: 1}


def BITMAP_HANDLE(h): return (0x05 << 24) | (h & 31)
def BITMAP_SOURCE(a): return (0x01 << 24) | (a & 0xFFFFFF)
def BITMAP_LAYOUT(f, stride, h): return (0x07 << 24) | ((f & 31) << 19) | ((stride & 1023) << 9) | (h & 511)
def BITMAP_LAYOUT_H(stride, h): return (0x28 << 24) | (((stride >> 10) & 3) << 2) | ((h >> 9) & 3)
def BITMAP_SIZE(flt, wx, wy, w, h): return (0x08 << 24) | ((flt & 1) << 20) | ((wx & 1) << 19) | ((wy & 1) << 18) | ((w & 511) << 9) | (h & 511)
def BITMAP_SIZE_H(w, h): return (0x29 << 24) | (((w >> 9) & 3) << 2) | ((h >> 9) & 3)
def VERTEX2II(x, y, h, c): return (2 << 30) | ((x & 511) << 21) | ((y & 511) << 12) | ((h & 31) << 7) | (c & 127)
def VERTEX2F(x, y): return (1 << 30) | ((x & 0x7FFF) << 15) | (y & 0x7FFF)
def BEGIN(p): return (0x1F << 24) | p
def CELL(c): return (0x06 << 24) | (c & 127)
def VERTEX_FORMAT(f): return (0x27 << 24) | (f & 7)
SAVE_CONTEXT, RESTORE_CONTEXT = 0x22 << 24, 0x23 << 24
BPP = {0: 16, 1: 1, 2: 4, 3: 8, 4: 8, 5: 8, 6: 16, 7: 16, 17: 2}
OPT_CENTERX, OPT_CENTERY, OPT_RIGHTX, OPT_FORMAT, OPT_FILL = 512, 1024, 2048, 4096, 8192


def s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class EVE:
    def __init__(self, vp, cs=(1, 28)):
        self.vp = vp
        self.cs_port, self.cs_bit = cs
        self.mem = bytearray(0x400000)
        struct.pack_into('<I', self.mem, REG['ID'], 0x7C)
        struct.pack_into('<I', self.mem, ROM_CHIPID, 0x00011508)   # BT815-style chip id
        self.tx = None           # bytes of the current transaction
        self.addr = 0
        self.kind = None
        self.cmd_log = []        # (name, args) of coprocessor commands
        self.frames = []         # rendered display lists (bytes of RAM_DL at swap)
        self.swaps = 0
        self.dl_ptr = 0
        self.state = {}          # coprocessor state (fonts, colors) for the renderer
        self.renderer = None
        self.max_log = 20000

    # ---- SPI ---------------------------------------------------------------------------------
    def cs_edge(self, vp, base, val):
        from periph import GPIO_BASES
        if base != GPIO_BASES[self.cs_port]:
            return
        lvl = (val >> self.cs_bit) & 1
        if lvl == 0 and self.tx is None:
            self.tx = bytearray()
        elif lvl == 1 and self.tx is not None:
            self.end_transaction()
            self.tx = None

    def spi(self, bus, word, tcr):
        if self.tx is None:
            return 0
        b = word & 0xFF
        t = self.tx
        t.append(b)
        n = len(t)
        if n == 1:
            self.kind = 'r' if b & 0xC0 == 0 else 'w' if b & 0xC0 == 0x80 else 'h'
        if n == 3 and self.kind in ('r', 'w'):
            self.addr = ((t[0] & 0x3F) << 16) | (t[1] << 8) | t[2]
        if self.kind == 'r' and n >= 5:
            a = self.addr + n - 5
            return self.read8(a)
        if self.kind == 'w' and n >= 4:
            a = self.addr + n - 4
            if RAM_CMD <= self.addr < RAM_CMD + RAM_CMD_SIZE:
                # bursts into the command ring wrap at its end (the firmware never splits them)
                a = RAM_CMD + ((a - RAM_CMD) & (RAM_CMD_SIZE - 1))
            self.write8(a, b)
        return 0

    def end_transaction(self):
        t = self.tx
        if self.kind == 'w' and len(t) >= 4:
            lo = self.addr
            hi = self.addr + len(t) - 4
            self.after_write(lo, hi)

    # ---- memory ------------------------------------------------------------------------------
    def wmem(self, a, data):
        """Length-safe write (never resizes the buffer)."""
        a &= 0x3FFFFF
        data = bytes(data)
        n = max(0, min(len(data), len(self.mem) - a))
        if n != len(data):
            self.log('OOB-write', [hex(a), len(data)])
        self.mem[a:a + n] = data[:n]

    def bounded(self, n):
        """Clamp a memory-command length to RAM_G's size (a bad length must not wipe the regs)."""
        if n > RAM_G_SIZE:
            self.log('OOB-length', [hex(n)])
            return 0
        return n

    def rmem(self, a, n):
        a &= 0x3FFFFF
        out = bytes(self.mem[a:a + n])
        return out + bytes(n - len(out))

    def read8(self, a):
        a &= 0x3FFFFF
        if a in (REG['CMDB_SPACE'], REG['CMDB_SPACE'] + 1):
            space = 0xFFC - ((self.reg('CMD_WRITE') - self.reg('CMD_READ')) & 0xFFF)
            return (space >> (8 * (a - REG['CMDB_SPACE']))) & 0xFF
        if REG['FRAMES'] <= a < REG['FRAMES'] + 4:
            self.frames_ctr = getattr(self, 'frames_ctr', 0) + 1
            struct.pack_into('<I', self.mem, REG['FRAMES'], self.frames_ctr // 4)
        if REG['CLOCK'] <= a < REG['CLOCK'] + 4:
            struct.pack_into('<I', self.mem, REG['CLOCK'], (self.vp.cyccnt() // 10) & 0xFFFFFFFF)
        return self.mem[a]

    def write8(self, a, b):
        a &= 0x3FFFFF
        if REG['CMDB_WRITE'] <= a < REG['CMDB_WRITE'] + 4:
            # bulk command writes: append to the FIFO at CMD_WRITE
            w = self.reg('CMD_WRITE')
            self.mem[RAM_CMD + ((w + (a - REG['CMDB_WRITE'])) & 0xFFF)] = b
            if a == REG['CMDB_WRITE'] + 3:
                self.set_reg('CMD_WRITE', (w + 4) & 0xFFF)
                self.run_coprocessor()
            return
        self.mem[a] = b

    def reg(self, name):
        return struct.unpack_from('<I', self.mem, REG[name])[0]

    def set_reg(self, name, v):
        struct.pack_into('<I', self.mem, REG[name], v & 0xFFFFFFFF)

    def after_write(self, lo, hi):
        if lo <= REG['CMD_WRITE'] < hi:
            self.run_coprocessor()
        if lo <= REG['DLSWAP'] < hi and self.reg('DLSWAP'):
            self.swap()
            self.set_reg('DLSWAP', 0)
        if lo <= REG['CPURESET'] < hi and self.reg('CPURESET') == 0:
            pass

    def swap(self):
        self.swaps += 1
        dl = bytes(self.mem[RAM_DL:RAM_DL + RAM_DL_SIZE])
        self.last_dl = dl
        if self.renderer is None:
            import eve_render
            self.renderer = eve_render.Renderer()
        self.renderer.render(dl, self.mem)
        for fn in getattr(self, 'on_swap', []):
            fn(self)

    def screenshot(self, path, scale=2, backlight=True):
        """Save the last frame. backlight: scale it by REG_PWM_DUTY (0..128), as the panel looks."""
        if self.renderer:
            im = self.renderer.image(scale)
            duty = min(self.reg('PWM_DUTY') & 0xff, 128)
            if backlight and duty < 128:
                im = im.point(lambda v: v * duty // 128)
            im.save(path)

    # ---- coprocessor -------------------------------------------------------------------------
    def cmd_word(self, off):
        return struct.unpack_from('<I', self.mem, RAM_CMD + (off & 0xFFF))[0]

    def cmd_bytes(self, off, n):
        return bytes(self.mem[RAM_CMD + ((off + i) & 0xFFF)] for i in range(n))

    def dl_append(self, word):
        struct.pack_into('<I', self.mem, RAM_DL + (self.dl_ptr & 0x1FFC), word & 0xFFFFFFFF)
        self.dl_ptr = (self.dl_ptr + 4) & 0x1FFF
        self.set_reg('CMD_DL', self.dl_ptr)

    STRING_CMDS = (0x0C, 0x0D, 0x0E, 0x12)   # TEXT, BUTTON, KEYS, TOGGLE: fixed args + C string

    def cmd_complete(self, rd, avail, op, nargs):
        """Bytes the command at `rd` occupies, or None while the host hasn't written all of it
        (the real coprocessor stalls on CMD_WRITE; executing early reads stale FIFO words)."""
        fixed = 4 + 4 * nargs
        if avail < fixed:
            return None
        if op in self.STRING_CMDS:
            for i in range(fixed, avail):
                if self.mem[RAM_CMD + ((rd + i) & 0xFFF)] == 0:
                    return fixed
            return None
        if op == 0x1A:                                  # MEMWRITE ptr, num, data
            num = self.cmd_word(rd + 8)
            return fixed if avail >= fixed + ((num + 3) & ~3) else None
        if op in (0x22, 0x50):                          # INFLATE: wait for the end of the stream
            import zlib
            d = zlib.decompressobj()
            try:
                d.decompress(self.cmd_bytes(rd + fixed, avail - fixed))
            except zlib.error:
                return fixed
            return fixed if d.eof else None
        return fixed

    def run_coprocessor(self):
        rd, wr = self.reg('CMD_READ'), self.reg('CMD_WRITE')
        guard = 0
        while rd != wr and guard < 4096:
            guard += 1
            avail = (wr - rd) & 0xFFF
            w = self.cmd_word(rd)
            if (w & 0xFFFFFF00) != 0xFFFFFF00:
                if (w >> 24) == 0x27 and (w >> 30) == 0:
                    self.vfmt = w & 7          # track VERTEX_FORMAT for VERTEX2F encoding
                self.dl_append(w)      # plain display list command
                rd = (rd + 4) & 0xFFF
                continue
            op = w & 0xFF
            nargs = CMD_ARGS.get(op)
            if nargs is None:
                self.log('UNKNOWN', [hex(w)])
                rd = (rd + 4) & 0xFFF
                continue
            if self.cmd_complete(rd, avail, op, nargs) is None:
                break                  # rest of the command not written yet
            args = [self.cmd_word(rd + 4 + 4 * i) for i in range(nargs)]
            size = 4 + 4 * nargs
            extra = self.exec_cmd(op, args, rd + size)
            size += extra
            rd = (rd + size) & 0xFFF
        self.set_reg('CMD_READ', rd)

    def log(self, name, args):
        if len(self.cmd_log) < self.max_log:
            self.cmd_log.append((name, args))

    def cstring(self, off):
        s = bytearray()
        while len(s) < 256:
            c = self.mem[RAM_CMD + ((off + len(s)) & 0xFFF)]
            if c == 0:
                break
            s.append(c)
        n = len(s) + 1
        return s.decode('latin1'), (n + 3) & ~3

    def exec_cmd(self, op, args, data_off):
        """Execute command `op`; returns extra bytes consumed after the fixed arguments."""
        name = CMD_NAMES.get(op, hex(op))
        extra = 0
        if op == 0x00:                       # DLSTART
            self.dl_ptr = 0
            self.vfmt = 4
            self.set_reg('CMD_DL', 0)
        elif op == 0x01:                     # SWAP
            self.dl_append(0)                # DISPLAY
            self.swap()
        elif op in (0x0C, 0x0D):             # TEXT x,y,font,options,string / BUTTON x,y,w,h,font,opt,s
            text, extra = self.cstring(data_off)
            args = args + [text]
            self.widget(name, args)
        elif op == 0x2E:                     # NUMBER x,y,font,options,n
            self.widget(name, args)
        elif op == 0x1A:                     # MEMWRITE ptr,num,data...
            ptr, num = args
            self.wmem(ptr, self.cmd_bytes(data_off, num))
            extra = (num + 3) & ~3
        elif op == 0x1B:                     # MEMSET ptr,value,num
            ptr, val, num = args
            self.wmem(ptr, bytes([val & 0xFF]) * self.bounded(num))
        elif op == 0x1C:                     # MEMZERO ptr,num
            ptr, num = args
            self.wmem(ptr, bytes(self.bounded(num)))
        elif op == 0x1D:                     # MEMCPY dst,src,num
            dst, src, num = args
            self.wmem(dst, self.rmem(src, self.bounded(num)))
        elif op == 0x1E:                     # APPEND ptr,num: copy RAM_G to display list
            ptr, num = args
            for i in range(0, num, 4):
                self.dl_append(struct.unpack_from('<I', self.mem, ptr + i)[0])
        elif op in (0x22, 0x50):             # INFLATE ptr (+options), then zlib stream
            extra = self.inflate(args[0], data_off)
        elif op == 0x24:                     # LOADIMAGE ptr, options, then image data
            extra = self.loadimage(args, data_off)
        elif op == 0x3B:                     # SETFONT2 font, ptr, firstchar
            self.state.setdefault('fonts', {})[args[0]] = (args[1], args[2])
            self.font_bitmap_setup(args[0], args[1], args[2])
        elif op == 0x2B:                     # SETFONT font, ptr
            self.state.setdefault('fonts', {})[args[0]] = (args[1], 32)
        elif op == 0x09:
            self.state['bgcolor'] = args[0]
        elif op == 0x0A:
            self.state['fgcolor'] = args[0]
        elif op == 0x34:
            self.state['gradcolor'] = args[0]
        elif op in (0x23, 0x25):             # GETPTR / GETPROPS: results written back in place
            pass
        elif op == 0x26 or op == 0x27 or op == 0x28 or op == 0x29 or op == 0x2A:
            self.matrix_cmd(op, args)
        elif op == 0x43:                     # SETBITMAP addr, fmt, w|h<<16
            self.setbitmap(args)
        self.log(name, args)
        return extra

    # hooks filled in by the renderer module
    def font_metrics(self, font):
        """(width(ch), height, first char) for a font handle. Custom fonts registered with
        CMD_SETFONT2 draw cell = char - firstchar from BITMAP_SOURCE = glyph base (as the chip does)."""
        fonts = self.state.get('fonts', {})
        if font in fonts:
            ptr, first = fonts[font]
            widths = self.mem[ptr:ptr + 128]
            fmt, stride, fw, fh, gptr = struct.unpack_from('<5I', self.mem, ptr + 128)
            return (lambda ch: widths[ch & 127]), fh, first
        import eve_render
        return (lambda ch: eve_render.rom_width(font, ch)), eve_render.ROM_HEIGHT.get(font, 16), 0

    def widget(self, name, args):
        if name == 'TEXT':
            xy, fo, text = args[0], args[1], args[2]
            x, y = s16(xy), s16(xy >> 16)
            font, opts = fo & 0xFFFF, fo >> 16
            self.text(x, y, font, opts, text)
        elif name == 'NUMBER':
            xy, fo, n = args
            self.text(s16(xy), s16(xy >> 16), fo & 0xFFFF, fo >> 16, str(n if n < 2**31 else n - 2**32))
        else:
            self.log('widget-unsupported', [name])

    def text(self, x, y, font, opts, text):
        width_of, fh, first = self.font_metrics(font)
        total = sum(width_of(ord(c)) for c in text)
        if opts & OPT_CENTERX:
            x -= total // 2
        elif opts & OPT_RIGHTX:
            x -= total
        if opts & OPT_CENTERY:
            y -= fh // 2
        if not text:
            return
        self.dl_append(BEGIN(1))
        for c in text:
            ch = ord(c) & 127
            cell = ch            # CMD_SETFONT2 handles: CELL = character code (source starts firstchar cells early)
            if 0 <= x < 512 and 0 <= y < 512:
                self.dl_append(VERTEX2II(x, y, font, cell))
            else:
                for wd in (SAVE_CONTEXT, VERTEX_FORMAT(0), BITMAP_HANDLE(font), CELL(cell), VERTEX2F(x, y),
                           RESTORE_CONTEXT):
                    self.dl_append(wd)
            x += width_of(ch)

    def font_bitmap_setup(self, font, ptr, first):
        """CMD_SETFONT2 as the chip does it: BITMAP_SOURCE = glyph base - firstchar cells, so CELL is the
        character code (a CFW drawing CELL(code - firstchar) shows the wrong glyphs, as on the Play+)."""
        fmt, stride, fw, fh, gptr = struct.unpack_from('<5I', self.mem, ptr + 128)
        src = (gptr - first * stride * fh) & 0xFFFFFF
        for wd in (BITMAP_HANDLE(font), BITMAP_SOURCE(src), BITMAP_LAYOUT(fmt, stride, fh),
                   BITMAP_LAYOUT_H(stride, fh), BITMAP_SIZE(0, 0, 0, fw, fh), BITMAP_SIZE_H(fw, fh)):
            self.dl_append(wd)

    def matrix_cmd(self, op, args):
        pass

    def setbitmap(self, args):
        addr, fw, hh = args
        fmt, w, h = fw & 0xFFFF, fw >> 16, hh & 0xFFFF
        stride = (w * BPP.get(fmt, 8) + 7) // 8
        for wd in (BITMAP_SOURCE(addr), BITMAP_LAYOUT(fmt, stride, h), BITMAP_LAYOUT_H(stride, h),
                   BITMAP_SIZE(0, 0, 0, w, h), BITMAP_SIZE_H(w, h)):
            self.dl_append(wd)

    def inflate(self, ptr, off):
        import zlib
        d = zlib.decompressobj()
        out = bytearray()
        consumed = 0
        while not d.eof and consumed < 0x100000:
            chunk = self.cmd_bytes(off + consumed, 64)
            consumed += 64
            out += d.decompress(chunk)
        used = consumed - len(d.unused_data)
        self.wmem(ptr, out)
        self.state['last_inflate'] = (ptr, len(out))
        return (used + 3) & ~3

    def loadimage(self, args, off):
        self.log('LOADIMAGE-unsupported', args)
        return 0


def attach(vp):
    e = EVE(vp)
    disp = getattr(vp, 'display', None)
    prev = vp.spi[3].device

    def mux(bus, word, tcr):
        r = prev(bus, word, tcr) if prev else 0
        if e.tx is not None:
            return e.spi(bus, word, tcr)
        return r
    vp.spi[3].device = mux
    vp.gpio_watch.append(e.cs_edge)
    vp.eve = e
    return e
