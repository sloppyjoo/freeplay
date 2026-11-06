"""High-level emulation hooks for the virtual Play+ 1.3.0: replace hardware-facing firmware
functions with Python. Addresses verified in docs/FIRMWAfirmware notes.md ("Virtual Play+")."""
from unicorn import arm_const as A

PLAYPLUS_130 = dict(
    name='playplus-1.3.0', base=0x60030000,
    app=0x20018c18, ui_cur=0x300, cfw_ram=0x20002700,   # app object, UI manager current-screen field
    disk_status=0x600bbcb8, disk_initialize=0x600bbcc4, disk_read=0x600bbcd0, disk_write=0x600bbcdc,
    g_sd=0x20006d28,
    usb_task=0x600aacf8,      # main-loop USB device task (MIDI/MSC); no USB controller model
    usb_midi_tx=0x600c6688,   # USB MIDI transmit (callers 0x600c6892 / 0x600c69a2): reads the device
    usb_midi_dev=0x200175cc,  # handle set by USB init (FUN_600c6610); 0 in the vdev -> skip the send
    led_dma_start=0x600afc8c,   # pad-LED chain: cache clean + LPSPI4 eDMA of the 0x1e30-byte frame
    led_dma_done=0x20005e4c,    # set by the eDMA ISR FUN_600afe48; FUN_600afe0c busy-waits on it
    led_poll=0x600afd74,        # LED state machine (r0 = state byte ptr; 2 = waiting for the eDMA)
    input_listener=0x20018c34,  # app (0x20018c18) + 0x1c, vtable 0x6007b27c
    input_poll=0x600aab28,      # main loop: polls input hardware -> listener (safe point)
    input_slots=dict(pad=0, button=1, knob=2, track=3, encoder=4),
    audio_cb=0x00013a18,        # audio DMA ch0 completion (ITCM): flips the buffer, calls the render
                                # callback *(0x2000301c)(L, R) = voices + sequencer, interleaves
    seq=0x2001e30c,             # sequencer singleton (FUN_601087d4); playing buffer's tracks hold the playhead
)

# Play 1.6.0, i.MX RT image: the same HAL code, found with cfw/tools/sigmatch.py against Play+ 1.3.0
# (docs/FIRMWAfirmware notes.md).
PLAY_160_RT = dict(
    name='play-1.6.0-rt', base=0x60020000,
    app=0x2000aa68, ui_cur=0x2fc, cfw_ram=0x20001700,   # app ctor 0x600407f4
    disk_status=0x600332ac, disk_initialize=0x600332b8, disk_read=0x600332c4, disk_write=0x600332d0,
    g_sd=0x20003c60,
    usb_task=None,
    led_dma_start=0x60026ebc, led_dma_done=0x2000324c, led_poll=0x60026fa4,
    input_listener=0x2000aa84, input_poll=0x60021db8,   # app + 0x1c, vtable 0x600c5844
    input_slots=dict(pad=0, button=1, knob=2, track=3, encoder=4),
    audio_cb=0x60021240,
)

PROFILES = {p['name']: p for p in (PLAYPLUS_130, PLAY_160_RT)}


def detect_profile(ppf_path):
    """Pick the profile from the image: Play+ images start at 0x60030000, Play RT at 0x60020000."""
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
    from ppf import PPF
    bases = {a for a, _ in PPF(ppf_path).segments()}
    return 'play-1.6.0-rt' if 0x60020000 in bases else 'playplus-1.3.0'


def hle(vp, addr, fn, name=None):
    """At `addr` (function entry) run fn(vp, r0, r1, r2, r3) -> r0, then return to LR."""
    def hit(vp):
        mu = vp.mu
        args = [mu.reg_read(r) for r in (A.UC_ARM_REG_R0, A.UC_ARM_REG_R1, A.UC_ARM_REG_R2, A.UC_ARM_REG_R3)]
        ret = fn(vp, *args)
        mu.reg_write(A.UC_ARM_REG_R0, (ret or 0) & 0xFFFFFFFF)
        mu.reg_write(A.UC_ARM_REG_PC, mu.reg_read(A.UC_ARM_REG_LR))
        if name:
            vp.hle_log.append((name, args, ret))
    vp.hook_addr(addr, hit)


def install_sd(vp, card, syms=PLAYPLUS_130):
    vp.hle_log = getattr(vp, 'hle_log', [])
    g = syms['g_sd']

    def init(vp, pdrv, *_):
        if pdrv != 2:
            return 1
        vp.w32(g + 0x2ac, card.sectors)   # GET_SECTOR_COUNT
        vp.w32(g + 0x2b0, 512)            # GET_SECTOR_SIZE
        vp.mu.mem_write(g + 0x285, b'\x01')  # GET_BLOCK_SIZE (erase block, sectors)
        vp.mu.mem_write(g + 0x2c4, b'\x01')   # card initialised
        return 0

    def status(vp, pdrv, *_):
        return 0 if pdrv == 2 else 1

    def read(vp, pdrv, buff, sector, count):
        if pdrv != 2:
            return 4
        vp.mu.mem_write(buff, card.read(sector, count))
        return 0

    def write(vp, pdrv, buff, sector, count):
        if pdrv != 2:
            return 4
        card.write(sector, bytes(vp.mu.mem_read(buff, count * 512)))
        return 0

    hle(vp, syms['disk_initialize'], init, 'disk_initialize')
    hle(vp, syms['disk_status'], status)
    hle(vp, syms['disk_read'], read)
    hle(vp, syms['disk_write'], write, 'disk_write')


def install_stubs(vp, syms=PLAYPLUS_130):
    vp.hle_log = getattr(vp, 'hle_log', [])
    if syms.get('usb_task'):
        hle(vp, syms['usb_task'], lambda vp, *a: 0)
    if syms.get('usb_midi_tx'):   # MIDI out over USB (transport/clock on Play/Stop) without a USB device
        def midi_tx(vp):
            if vp.r32(syms['usb_midi_dev']) == 0:
                vp.mu.reg_write(A.UC_ARM_REG_R0, 0)
                vp.mu.reg_write(A.UC_ARM_REG_PC, vp.mu.reg_read(A.UC_ARM_REG_LR))
        vp.hook_addr(syms['usb_midi_tx'], midi_tx)
    # No eDMA model: an LED frame transfer completes at once (what the ISR would report).
    vp.led_frames = 0

    def led_dma(vp, *a):
        vp.led_frames += 1
        vp.mu.mem_write(syms['led_dma_done'], b'\x01')
        return 0
    hle(vp, syms['led_dma_start'], led_dma)

    def led_poll(vp):   # also covers snapshots taken while a (never-finishing) transfer was pending
        st = vp.mu.reg_read(A.UC_ARM_REG_R0)
        if vp.mu.mem_read(st, 1)[0] == 2:
            vp.mu.mem_write(syms['led_dma_done'], b'\x01')
    vp.hook_addr(syms['led_poll'], led_poll)


def install_inputs(vp, syms=PLAYPLUS_130):
    from vplay import Inputs
    if not syms.get('input_listener'):
        return None
    return Inputs(vp, syms['input_listener'], syms['input_slots'], syms['input_poll'])


NVIC_ISER0, NVIC_ICER0 = 0xE000E100, 0xE000E180
AUDIO_BLOCKS_PER_MS = 44100 / 128 / 1000   # 128-sample blocks at 44.1 kHz


def enable_audio(vp, syms=None):
    """Audio clock: run the audio DMA completion routine at the block rate (~344 Hz) whenever IRQ 0
    is enabled in the NVIC and interrupts are not masked (the firmware's edit lock disables IRQ 0
    through ICER0). There is no DMA or codec model: the routine renders into its buffers and the
    output is dropped. This is what drives the sequencer (playback, step playheads, pattern changes).
    Opt-in: rendering voices costs emulation time."""
    syms = syms or vp.profile
    vp.nvic_en = vp.mmio_w.get(NVIC_ISER0, 0)

    def iser(vp, a, s, v):
        vp.nvic_en |= v

    def icer(vp, a, s, v):
        vp.nvic_en &= ~v
    vp.write_rules[NVIC_ISER0] = iser
    vp.write_rules[NVIC_ICER0] = icer

    def tick(vp):
        vp._audio_acc = getattr(vp, '_audio_acc', 0.0) + AUDIO_BLOCKS_PER_MS
        while vp._audio_acc >= 1:
            vp._audio_acc -= 1
            if (vp.nvic_en & 1) and not vp.irq_masked():
                vp.audio_blocks = getattr(vp, 'audio_blocks', 0) + 1
                vp.call_fn(syms['audio_cb'] | 1)
    vp.periodic = list(getattr(vp, 'periodic', [])) + [tick]
