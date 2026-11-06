"""ILI9341 model for the virtual Play+: the firmware drives it over LPSPI3, byte by byte, with
GPIO1.15 as data/command (0 = command). 320x240 landscape (MADCTL 0x20), RGB565."""

W, H = 320, 240


class ILI9341:
    def __init__(self, vp, dc=(1, 15)):
        self.vp = vp
        self.dc_port, self.dc_bit = dc
        self.fb = [[0] * W for _ in range(H)]
        self.cmd = None
        self.args = []
        self.x0, self.x1, self.y0, self.y1 = 0, W - 1, 0, H - 1
        self.x = self.y = 0
        self.pix_hi = None
        self.madctl = 0
        self.colmod = 0x55
        self.frames = 0          # RAMWR commands seen
        self.pixels = 0
        self.on = False
        self.log = []

    def dc(self):
        """D/C level; GPIO1-4 pins move to the fast GPIO6-9 aliases when IOMUXC_GPR26-29 say so."""
        from periph import GPIO_BASES
        port = self.dc_port
        gpr = self.vp.mmio_w.get(0x400AC068 + 4 * (port - 1), 0) if port <= 4 else 0
        if (gpr >> self.dc_bit) & 1:
            port += 5
        return (self.vp.mmio_w.get(GPIO_BASES[port], 0) >> self.dc_bit) & 1

    def spi(self, bus, word, tcr):
        b = word & 0xFF
        if not self.dc():
            self.cmd, self.args = b, []
            self.pix_hi = None
            if b == 0x2C:
                self.x, self.y = self.x0, self.y0
                self.frames += 1
            elif b == 0x29:
                self.on = True
            elif b == 0x28:
                self.on = False
            if len(self.log) < 2000:
                self.log.append(b)
            return 0
        if self.cmd in (0x2C, 0x3C):
            if self.pix_hi is None:
                self.pix_hi = b
                return 0
            px = (self.pix_hi << 8) | b
            self.pix_hi = None
            if 0 <= self.y < H and 0 <= self.x < W:
                self.fb[self.y][self.x] = px
            self.pixels += 1
            self.x += 1
            if self.x > self.x1:
                self.x = self.x0
                self.y += 1
                if self.y > self.y1:
                    self.y = self.y0
            return 0
        self.args.append(b)
        if self.cmd == 0x2A and len(self.args) == 4:
            self.x0 = (self.args[0] << 8) | self.args[1]
            self.x1 = (self.args[2] << 8) | self.args[3]
        elif self.cmd == 0x2B and len(self.args) == 4:
            self.y0 = (self.args[0] << 8) | self.args[1]
            self.y1 = (self.args[2] << 8) | self.args[3]
        elif self.cmd == 0x36 and len(self.args) == 1:
            self.madctl = b
        elif self.cmd == 0x3A and len(self.args) == 1:
            self.colmod = b
        return 0

    def image(self, scale=2):
        from PIL import Image
        im = Image.new('RGB', (W, H))
        px = im.load()
        for y in range(H):
            row = self.fb[y]
            for x in range(W):
                v = row[x]
                px[x, y] = (((v >> 11) & 0x1F) * 255 // 31, ((v >> 5) & 0x3F) * 255 // 63, (v & 0x1F) * 255 // 31)
        if scale != 1:
            im = im.resize((W * scale, H * scale), Image.NEAREST)
        return im

    def save(self, path, scale=2):
        self.image(scale).save(path)

    # snapshot support
    def state(self):
        return dict(fb=self.fb, win=(self.x0, self.x1, self.y0, self.y1), xy=(self.x, self.y), cmd=self.cmd,
                    madctl=self.madctl, colmod=self.colmod, on=self.on)

    def restore(self, st):
        self.fb = st['fb']
        self.x0, self.x1, self.y0, self.y1 = st['win']
        self.x, self.y = st['xy']
        self.cmd, self.madctl, self.colmod, self.on = st['cmd'], st['madctl'], st['colmod'], st['on']


def attach(vp):
    d = ILI9341(vp)
    vp.spi[3].device = d.spi
    vp.display = d
    return d
