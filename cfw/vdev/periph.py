"""Peripheral behaviour for the virtual Play+ (i.MX RT1062). Each rule makes a status register read
the way the firmware's wait loops expect. Rules are added only for hangs actually observed."""


def install(vp):
    R = vp.read_rules

    def orbits(bits):
        return lambda vp, a, size, stored: stored | bits

    def clearbits(bits):
        return lambda vp, a, size, stored: stored & ~bits

    def const(v):
        return lambda vp, a, size, stored: v

    # DWT CYCCNT (micros()), SysTick current value
    R[0xE0001004] = lambda vp, a, s, st: vp.cyccnt()
    R[0xE000E018] = lambda vp, a, s, st: (600000 - (vp.cyccnt() % 600000)) & 0xFFFFFF

    # CCM_ANALOG / XTALOSC24M (0x400D8000): PLL LOCK (bit 31) on every PLL control register,
    # XTALOSC24M_LOWPWR_CTRL... status reads.
    for off in (0x000, 0x010, 0x020, 0x030, 0x070, 0x0A0, 0x0E0, 0x0F0, 0x100, 0x110):
        R[0x400D8000 + off] = orbits(1 << 31)
    R[0x400D8270] = orbits(1 << 16)          # XTALOSC24M_OSC_CONFIG? (polled bit 16)

    # SET/CLR/TOG aliases (+4/+8/+0xC) in the analog, PMU and USB PHY blocks
    def setclr(vp, a, size, v):
        base, kind = a & ~0xF, a & 0xC
        cur = vp.mmio_w.get(base, 0)
        if kind == 4:
            cur |= v
        elif kind == 8:
            cur &= ~v
        elif kind == 0xC:
            cur ^= v
        else:
            return
        vp.mmio_w[base] = cur & 0xFFFFFFFF
        vp.mmio_w[a] = 0
    for lo, hi in ((0x400D8000, 0x400D9000), (0x400D9000, 0x400DA000), (0x400DA000, 0x400DB000)):
        vp.range_rules.append((lo, hi, None, setclr))
    R[0x400D8150] = orbits(1 << 15)          # CCM_ANALOG_MISC0: polled bit 15

    # SEMC (SDRAM controller, 0x402F0000): MCR.SWRST self-clears, STS0.IDLE, INTR.IPCMDDONE
    R[0x402F0000] = clearbits(1)
    R[0x402F00C0] = orbits(1)
    R[0x402F003C] = lambda vp, a, s, st: (st | 1) & ~2
    # CCM (0x400FC000): CDHIPR handshake busy bits clear
    R[0x400FC048] = const(0)
    # DCDC REG0.STS_DC_OK
    R[0x40080000] = orbits(1 << 31)
    # LPUART1-8 STAT: TDRE | TC; no RX data
    for k in range(8):
        R[0x40184000 + k * 0x4000 + 0x14] = lambda vp, a, s, st: (st | (1 << 23) | (1 << 22)) & ~(1 << 21)
    # LPSPI1-4 SR: TDF, RDF, TCF, FCF set; FSR: TX FIFO empty
    for b in (0x40394000, 0x40398000, 0x4039C000, 0x403A0000):
        R[b + 0x14] = orbits((1 << 0) | (1 << 1) | (1 << 9) | (1 << 10))
        R[b + 0x5C] = lambda vp, a, s, st: 0x00000000
    # LPI2C1-4 MSR: TDF, RDF, EPF, SDF set; busy and error flags clear. MRDR: data present (0)
    for b in (0x403F0000, 0x403F4000, 0x403F8000, 0x403FC000):
        R[b + 0x14] = lambda vp, a, s, st: 0x00000303
        R[b + 0x70] = const(0)
    # USB OTG1/OTG2 USBCMD.RST self-clears
    R[0x402E0140] = clearbits(1 << 1)
    R[0x402E0340] = clearbits(1 << 1)
    # ADC1/ADC2: HS.COCO0 always set, R0 = mid-scale result
    for b in (0x400C4000, 0x400C8000):
        R[b + 0x20] = orbits(1)
        R[b + 0x24] = const(0x800)
    # ADC_ETC / GPT / PIT counters free-run with simulated time
    R[0x401EC024] = lambda vp, a, s, st: (vp.cyccnt() // 25) & 0xFFFFFFFF   # GPT1 CNT (24 MHz)
    R[0x401F0024] = lambda vp, a, s, st: (vp.cyccnt() // 25) & 0xFFFFFFFF   # GPT2 CNT

    install_lpspi(vp)
    install_gpio(vp)


class LPSPI:
    """LPSPI with TX/RX FIFOs. Each word written to TDR is passed to `device(bus, word, tcr)`;
    its return value is queued in RX unless TCR.RXMSK is set."""
    NAMES = {0x40394000: 1, 0x40398000: 2, 0x4039C000: 3, 0x403A0000: 4}

    def __init__(self, vp, base):
        self.vp, self.base, self.bus = vp, base, self.NAMES[base]
        self.rx = []
        self.device = None
        self.log = []

    def tdr(self, vp, a, size, v):
        tcr = vp.mmio_w.get(self.base + 0x60, 0)
        frame = (tcr & 0xFFF) + 1
        word = v & ((1 << min(frame, 32)) - 1)
        self.log.append(word)
        if len(self.log) > 200000:
            del self.log[:100000]
        resp = self.device(self.bus, word, tcr) if self.device else 0
        if not (tcr >> 19) & 1:  # RXMSK
            self.rx.append(resp or 0)

    def fsr(self, vp, a, size, st):
        return min(len(self.rx), 16) << 16

    def rdr(self, vp, a, size, st):
        return self.rx.pop(0) if self.rx else 0

    def rsr(self, vp, a, size, st):
        return 0 if self.rx else 2

    def sr(self, vp, a, size, st):
        return (1 << 0) | (1 << 9) | (1 << 10) | ((1 << 1) if self.rx else 0)

    def cr(self, vp, a, size, v):
        if v & (1 << 9):   # RRF: reset RX FIFO
            self.rx.clear()


def install_lpspi(vp):
    vp.spi = {}
    for base in LPSPI.NAMES:
        s = LPSPI(vp, base)
        vp.spi[s.bus] = s
        vp.write_rules[base + 0x64] = s.tdr
        vp.write_rules[base + 0x10] = s.cr
        vp.read_rules[base + 0x5C] = s.fsr
        vp.read_rules[base + 0x74] = s.rdr
        vp.read_rules[base + 0x70] = s.rsr
        vp.read_rules[base + 0x14] = s.sr


GPIO_BASES = {1: 0x401B8000, 2: 0x401BC000, 3: 0x401C0000, 4: 0x401C4000, 5: 0x400C0000,
              6: 0x42000000, 7: 0x42004000, 8: 0x42008000, 9: 0x4200C000}


def install_gpio(vp):
    """GPIO DR/PSR: outputs read back, inputs read the simulated pin level (default high: buttons
    are active-low with pull-ups). vp.gpio_in[(port, bit)] = 0/1 overrides a pin."""
    # observed: GPIO1.20 is a busy/handshake input that must read low (FUN_600ab5d8);
    # GPIO2.20 low makes fw_checkPinThenUpdate reset into the updater, so it stays high.
    # GPIO3.2 is the SD card-detect input (0x600c09f4), low = inserted.
    vp.gpio_in = {(1, 20): 0, (3, 2): 0}

    def make(port, base):
        def rd(vp, a, size, st):
            gdir = vp.mmio_w.get(base + 4, 0)
            pins = 0xFFFFFFFF
            for (p, b), lvl in vp.gpio_in.items():
                if p == port or (p + 5 == port) or (p == port + 5):   # GPIO1-4 alias GPIO6-9
                    pins = (pins | (1 << b)) if lvl else (pins & ~(1 << b))
            return (vp.mmio_w.get(base, 0) & gdir) | (pins & ~gdir)
        return rd
    def setclr(base, kind):
        def wr(vp, a, size, v):
            cur = vp.mmio_w.get(base, 0)
            cur = cur | v if kind == 'set' else cur & ~v if kind == 'clr' else cur ^ v
            vp.mmio_w[base] = cur & 0xFFFFFFFF
            vp.mmio_w[a] = 0
            if vp.gpio_watch:
                for fn in vp.gpio_watch:
                    fn(vp, base, cur)
        return wr

    def drw(base):
        def wr(vp, a, size, v):
            for fn in vp.gpio_watch:
                fn(vp, base, v)
        return wr
    vp.gpio_watch = []
    for port, base in GPIO_BASES.items():
        vp.read_rules[base + 0x00] = make(port, base)
        vp.read_rules[base + 0x08] = make(port, base)
        vp.write_rules[base + 0x00] = drw(base)
        vp.write_rules[base + 0x84] = setclr(base, 'set')
        vp.write_rules[base + 0x88] = setclr(base, 'clr')
        vp.write_rules[base + 0x8C] = setclr(base, 'tog')
