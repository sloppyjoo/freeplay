/* USB storage mode on the original Play (RT units): Menu > Settings > Enter USB Storage Mode.
 *
 * The Play+ has this item stock (action FUN_600e7050, return FUN_600e5dac). Play 1.6.0 (RT) ships
 * the same USB stack: a composite device (MIDI, mass storage, CDC serial), the MSC class, the SCSI
 * callback 0x6003d414 that serves the SD card through SD_ReadBlocks / SD_WriteBlocks on g_sd, and
 * the descriptor builder that adds the mass-storage interface while USB flag 3 is set. Only the
 * menu entry is missing. This adds it as a submenu that *is* the storage mode: while it is open the
 * card is a drive on the computer; leaving it gives the card back to the Play.
 *
 * The Play+ steps (stop playback, silence and hold the voice engine, flag 3, re-enumerate), plus:
 *  - a status panel: connecting / connect a computer / card on the computer / ejected (the host's
 *    START STOP UNIT, which the stock SCSI callback already records), card size and format;
 *  - modal input: pads, knobs, the encoder and every button but Back do nothing meanwhile (presses
 *    are dropped, and the releases of dropped presses; releases of earlier presses still pass);
 *  - Back while the computer still has the drive mounted (not ejected) asks for a second Back;
 *  - the card comes back only once the USB task has re-enumerated without the drive (that is when
 *    flags[5] clears), so the computer and the Play never use the card at the same time;
 *  - FatFs: what FatFs caches is written out before the computer gets the card; when it comes
 *    back, the cached sector and the free-space info are dropped and re-read the way a fresh mount
 *    reads them, so the Play never writes an old copy of a sector the computer changed. If the
 *    computer reformatted the card, FatFs mounts it afresh on its next access.
 *
 * Runs from the menu screen's update / hide (sliceview.c), the app's per-loop update and the input
 * listener (screen.c); all main-loop context, like the USB task and FatFs. */
#include "slice.h"
#include "dlw.h"

#ifdef CFW_USB

/* ---- firmware imports (symbols.ld) ------------------------------------------------------- */
extern volatile u32 fw_millis;
extern void fw_menu_back(void *scr);
extern u8 fw_usb_flags[];      /* USB object flags: [3] storage, [5] reconnect request */
extern u8 fw_usb_composite[];  /* NXP composite device: attach, ejected */
extern u8 fw_usb_cdc[];        /* CDC ACM state: line state (DTR) */
extern u8 fw_sd_card[];        /* NXP sd_card_t g_sd */
extern void *fw_ff_vols[];     /* FatFs FATFS *FatFs[] */
extern u8 fw_ff_files[];       /* FatFs FF_FS_LOCK table, 16 bytes per entry, +0 = FATFS * */
extern int fw_ff_sync_window(void *fs);
extern int fw_ff_sync_fs(void *fs);
extern int fw_disk_read(u32 pdrv, void *buf, u32 sector, u32 count);
extern void fw_engine_stop_voices(void *engine);
extern void fw_engine_enable(void *engine, u32 on);
extern void fw_song_stop(void *song, u32 active, u32 b);

enum { USB_OFF, USB_ON, USB_LEAVING, USB_NOCARD, USB_BUSY };
enum { IN_PAD, IN_BUTTON, IN_KNOB, IN_TRACK, IN_ENCODER }; /* input listener vtable slots */
#define ST_PRESSED 0
#define ST_RELEASED 1
#define PAD_IDS 160      /* row * 20 + col, 8 rows */
#define CONFIRM_MS 4000  /* a second Back within this returns without the eject */
#define FS_FAT32 3
#define FS_EXFAT 4

/* palette and layout (logical 320x240, below the menu header; the submenu has no rows) */
#define C_TEXT 0xffffff
#define C_DIM 0x8c8c8c
#define C_RULE 0x2a2a2a
#define C_WARN 0xff5540
#define C_OK 0x5fd35f
#define UP_X 12
#define UP_Y 30
#define UP_W 296
#define UP_H 178

static int cfw_usb_render(void *w);
const void *const cfw_usb_vt[10] = {
    (const void *)cfw_usb_render, fw_w9_append, fw_w9_cache, fw_w9_nop, fw_w9_nop,
    fw_w9_nop, fw_w9_set_colors, fw_w9_nop, fw_w9_nop, fw_w9_delete,
};

/* ---- state ------------------------------------------------------------------------------- */
static void usb_reset(struct cfw_usb *u)
{
    for (u32 i = 0; i < sizeof *u / 4; i++)
        ((volatile u32 *)u)[i] = 0;
    u->magic = USB_MAGIC;
    u->slot = -1;
    u->seen_ms = fw_millis;
}

static struct cfw_usb *usb_state(void)
{
    struct cfw_usb *u = &cfw_ram.usb;
    if (u->magic != USB_MAGIC)
        usb_reset(u);
    return u;
}

static int confirm_active(const struct cfw_usb *u)
{
    return u->confirm && fw_millis - u->confirm_ms < CONFIRM_MS;
}

/* the computer has the drive mounted: enumerated with it, configured, not ejected */
static int host_has_card(void)
{
    return !fw_usb_flags[USB_FLAG_RECONNECT] && (fw_usb_composite[COMP_ATTACH] & 1) &&
           !(fw_usb_composite[COMP_EJECTED] & 1);
}

static int serial_open(void)
{
    return AT(fw_usb_cdc, CDC_LINE_STATE, u16) & 1;
}

/* ---- the Play+ steps --------------------------------------------------------------------- */
static void usb_storage(void *ctx, u32 on) /* USB flag 3, then re-enumerate (FUN_600e7050 / 5dac) */
{
    void *hal = PTR(ctx, MENU_CTX_HAL);
    void *usb = VFN(hal, HAL_VT_USB, void *)(hal);
    VFN(usb, USB_VT_SET_FLAG, void, u32, u32)(usb, USB_FLAG_STORAGE, on);
    VFN(usb, USB_VT_RECONNECT, void)(usb);
}

static void stop_playback(void *app) /* Play+ FUN_600e7014 */
{
    u8 *song = (u8 *)app + APP_SONG;
    void *seq = (u8 *)app + APP_SEQ;
    if (!(VFN(seq, SEQ_VT_IS_PLAYING, u32)(seq) & 0xff))
        return;
    if (!*song)
        VFN(seq, SEQ_VT_SET_PLAYING, void, u32, u32)(seq, 0, 0);
    else
        fw_song_stop(song, 0, 0);
}

/* ---- FatFs ------------------------------------------------------------------------------- */
static void *ff_volume(void) /* the SD card's FATFS, if mounted */
{
    void *fs = fw_ff_vols[FF_DRIVE];
    return fs && U8(fs, FS_TYPE) ? fs : 0;
}

static int ff_files_open(void *fs)
{
    for (u32 i = 0; i < FF_LOCKS; i++)
        if (PTR(fw_ff_files, i * 16) == fs)
            return 1;
    return 0;
}

static u32 ld32(const u8 *p)
{
    return p[0] | (u32)p[1] << 8 | (u32)p[2] << 16 | (u32)p[3] << 24;
}

/* FatFs move_window() that always reads: the sector lands in fs->win, valid and clean */
static int ff_load(void *fs, u32 sect)
{
    if (U8(fs, FS_WFLAG) && fw_ff_sync_window(fs))
        return 1;
    if (fw_disk_read(U8(fs, FS_PDRV), (u8 *)fs + FS_WIN, sect, 1)) {
        U32(fs, FS_WINSECT) = 0xffffffffu;
        return 1;
    }
    U32(fs, FS_WINSECT) = sect;
    return 0;
}

static u32 fnv(u32 h, const u8 *b, u32 lo, u32 hi)
{
    for (u32 i = lo; i < hi; i++)
        h = (h ^ b[i]) * 16777619u;
    return h;
}

/* What a reformat changes in the volume boot sector: geometry and serial number, not the
 * dirty / in-use flags hosts update while the volume is mounted. 0 = unreadable. */
static u32 ff_fingerprint(void *fs)
{
    if (ff_load(fs, U32(fs, FS_VOLBASE)))
        return 0;
    const u8 *b = (const u8 *)fs + FS_WIN;
    u32 h = 2166136261u;
    if (U8(fs, FS_TYPE) == FS_EXFAT) /* offsets, lengths, root, serial, revision; shifts, FAT count */
        h = fnv(fnv(h, b, 0x40, 0x6a), b, 0x6c, 0x6f);
    else if (U8(fs, FS_TYPE) == FS_FAT32) /* BPB up to BkBootSec; volume serial */
        h = fnv(fnv(h, b, 0x0b, 0x34), b, 0x43, 0x47);
    else
        h = fnv(fnv(h, b, 0x0b, 0x24), b, 0x27, 0x2b);
    return h | 1;
}

/* Before the computer gets the card: nothing FatFs holds may reach it later. */
static u32 ff_release(void)
{
    void *fs = ff_volume();
    if (!fs)
        return 0;
    fw_ff_sync_fs(fs); /* window and FSINFO out, CTRL_SYNC */
    return ff_fingerprint(fs);
}

/* The card is back: drop what FatFs read before (a fresh mount reads the same things again), or
 * have FatFs mount it afresh if the computer reformatted it. */
static void ff_reclaim(struct cfw_usb *u)
{
    void *fs = ff_volume();
    u->remounted = 0;
    if (!fs)
        return;
    U8(fs, FS_WFLAG) = 0; /* never write back a sector from before */
    U32(fs, FS_WINSECT) = 0xffffffffu;
    u32 fp = ff_fingerprint(fs);
    if (!fp || fp != u->fp) {
        U32(fs, FS_WINSECT) = 0xffffffffu;
        U8(fs, FS_TYPE) = 0; /* FatFs mounts the volume again on its next access */
        u->remounted = 1;
        return;
    }
    /* allocation hints as mount_volume() takes them: FSINFO on FAT32, otherwise unknown */
    u32 free = 0xffffffffu, last = U32(fs, FS_LAST_CLST);
    if (U8(fs, FS_TYPE) == FS_FAT32 && !(U8(fs, FS_FSI_FLAG) & 0x80) && !ff_load(fs, U32(fs, FS_VOLBASE) + 1)) {
        const u8 *w = (const u8 *)fs + FS_WIN;
        if (w[510] == 0x55 && w[511] == 0xaa && ld32(w) == 0x41615252u && ld32(w + 484) == 0x61417272u) {
            free = ld32(w + 488);
            last = ld32(w + 492);
        }
    }
    U32(fs, FS_FREE_CLST) = free;
    U32(fs, FS_LAST_CLST) = last;
    U8(fs, FS_FSI_FLAG) &= 0x80;
}

/* ---- storage mode ------------------------------------------------------------------------ */
static void usb_enter(void *ctx, struct cfw_usb *u)
{
    void *fs = ff_volume();
    u->ctx = ctx;
    u->confirm = 0;
    u->remounted = 0;
    if (!U8(fw_sd_card, SD_READY)) {
        u->mode = USB_NOCARD;
        return;
    }
    if (fs && ff_files_open(fs)) {
        u->mode = USB_BUSY;
        return;
    }
    stop_playback(PTR(ctx, MENU_CTX_APP));
    void *engine = PTR(ctx, MENU_CTX_ENGINE);
    fw_engine_stop_voices(engine);
    fw_engine_enable(engine, 0);
    u->fp = ff_release();
    fw_usb_composite[COMP_EJECTED] = 0; /* bus reset clears it too; no stale "ejected" meanwhile */
    usb_storage(ctx, 1);
    u->mode = USB_ON;
}

static void usb_start_leave(struct cfw_usb *u)
{
    usb_storage(u->ctx, 0);
    u->mode = USB_LEAVING;
    u->confirm = 0;
}

static void usb_finish_leave(struct cfw_usb *u)
{
    ff_reclaim(u);
    fw_engine_enable(PTR(u->ctx, MENU_CTX_ENGINE), 1);
    u->mode = USB_OFF;
}

/* app per-loop update: the hand-back completes once the USB task re-enumerated without the drive */
void cfw_usb_tick(void)
{
    struct cfw_usb *u = usb_state();
    u32 now = fw_millis;
    if ((s32)(now - u->seen_ms) < 0) {
        usb_reset(u); /* time went back: a reset, nothing here describes this boot */
        return;
    }
    u->seen_ms = now;
    if (u->mode == USB_ON && !fw_usb_flags[USB_FLAG_STORAGE])
        u->mode = USB_LEAVING; /* the drive was switched off elsewhere: take the card back */
    if (u->mode == USB_LEAVING && !fw_usb_flags[USB_FLAG_RECONNECT])
        usb_finish_leave(u);
}

/* ---- input ------------------------------------------------------------------------------- */
/* Input is held while the status panel is up (storage mode, or handing the card back after Back).
 * A hand-back started because the menu closed does not hold input: nothing on screen would say why. */
static int modal(const struct cfw_usb *u)
{
    return u->mode != USB_OFF && (u->mode != USB_LEAVING || u->back_pending);
}

/* Back in storage mode: 1 = the press is ours (dropped), 0 = the menu gets it. */
static int back_pressed(struct cfw_usb *u)
{
    if (u->mode == USB_LEAVING)
        return 1;
    if (u->mode != USB_ON)
        return 0; /* no card / busy: nothing was handed over */
    if (host_has_card() && !confirm_active(u)) {
        u->confirm = 1;
        u->confirm_ms = fw_millis;
        return 1;
    }
    usb_start_leave(u);
    u->back_pending = 1; /* cfw_usb_menu_sync leaves the submenu once the card is back */
    return 1;
}

int cfw_usb_swallow(u32 kind, u32 id, u32 state)
{
    struct cfw_usb *u = usb_state();
    u32 *bits = 0, lim = 32;
    if (kind == IN_PAD)
        bits = u->pads, lim = PAD_IDS;
    else if (kind == IN_BUTTON)
        bits = &u->buttons;
    else if (kind == IN_TRACK)
        bits = &u->tracks;
    if (!bits)
        return modal(u); /* knobs and the encoder: relative moves, dropped */
    if (id >= lim)
        return 0;
    u32 *wp = &bits[id >> 5], m = 1u << (id & 31);
    if (state != ST_PRESSED) {
        if (state == ST_RELEASED && (*wp & m)) {
            *wp &= ~m;
            return 1;
        }
        return 0;
    }
    if (!modal(u))
        return 0;
    if (kind == IN_BUTTON && id == BTN_BACK && !back_pressed(u))
        return 0;
    *wp |= m;
    return 1;
}

/* ---- status panel ------------------------------------------------------------------------ */
static void line_text(struct dlw *d, int cx, int y, u32 color, const char *s)
{
    if (s)
        text(d, cx, y, color, OPT_CENTERX, s);
}

static int cfw_usb_render(void *w)
{
    struct cfw_usb *u = &cfw_ram.usb;
    int x = (s32)U32(w, W_X), y = (s32)U32(w, W_Y), wd = (s32)U32(w, W_W), cx = x + wd / 2;
    struct dlw d = {0};
    const char *title, *l1 = 0, *l2 = 0, *l3 = 0, *warn = 0;
    u32 tcol = C_TEXT;
    char buf[32], *p;

    switch (u->mode) {
    case USB_NOCARD:
        title = "No SD card", tcol = C_WARN;
        l1 = "Insert a card, then open", l2 = "this item again.";
        break;
    case USB_BUSY:
        title = "SD card busy", tcol = C_WARN;
        l1 = "The Play is using the card.", l2 = "Go back and try again.";
        break;
    case USB_LEAVING:
        title = "Returning the card";
        l1 = "Disconnecting the drive...";
        if (serial_open())
            l2 = "Waiting for the computer to", l3 = "close the Play's serial port.";
        break;
    default:
        if (fw_usb_flags[USB_FLAG_RECONNECT]) {
            title = "Connecting";
            l1 = "Starting the USB drive...";
            if (serial_open())
                l2 = "Waiting for the computer to", l3 = "close the Play's serial port.";
        } else if (fw_usb_composite[COMP_EJECTED] & 1) {
            title = "Ejected", tcol = C_OK;
            l1 = "The computer is done with", l2 = "the card. Press Back to", l3 = "return to the Play.";
        } else if (!(fw_usb_composite[COMP_ATTACH] & 1)) {
            title = "Connect a computer";
            l1 = "Connect the Play to a", l2 = "computer with a USB cable.", l3 = "The card shows up as a drive.";
        } else if (confirm_active(u)) {
            title = "Not ejected yet", tcol = C_WARN;
            l1 = "Eject the drive on the", l2 = "computer first, or press", l3 = "Back again to return anyway.";
        } else {
            title = "Card on the computer";
            l1 = "Copy your files, eject the", l2 = "drive on the computer, then", l3 = "press Back.";
            warn = "Do not unplug the USB cable";
        }
    }

    fw_eve_begin();
    fw_eve_dlstart();
    dl(&d, DL_SAVE_CONTEXT);
    dl(&d, DL_VERTEX_FORMAT(4));

    text_f(&d, cx, y + 8, FONT_LARGE, tcol, OPT_CENTERX, title);
    dl(&d, DL_COLOR_RGB(tcol == C_TEXT ? C_RULE : tcol));
    dl(&d, DL_LINE_WIDTH(8));
    dl(&d, DL_BEGIN(PRIM_LINES));
    dl(&d, DL_VERTEX2F((x + 32) * SUB, (y + 42) * SUB));
    dl(&d, DL_VERTEX2F((x + wd - 32) * SUB, (y + 42) * SUB));
    dl(&d, DL_END);
    line_text(&d, cx, y + 56, C_TEXT, l1);
    line_text(&d, cx, y + 78, C_TEXT, l2);
    line_text(&d, cx, y + 100, C_TEXT, l3);
    line_text(&d, cx, y + 128, C_WARN, warn);

    /* footer: card size (decimal GB, as cards are sold) and format */
    u32 blocks = U32(fw_sd_card, SD_BLOCKS), bsz = U32(fw_sd_card, SD_BLOCK_SIZE);
    if (U8(fw_sd_card, SD_READY) && blocks && bsz) {
        u32 tenths = blocks / 1000 * bsz / 100000;
        p = cfw_put_str(buf, "SD card ");
        p = cfw_put_uint(p, tenths / 10);
        *p++ = '.';
        *p++ = (char)('0' + tenths % 10);
        p = cfw_put_str(p, " GB");
        *p = 0;
        text(&d, x + 4, y + UP_H - 22, C_DIM, 0, buf);
        void *fs = fw_ff_vols[FF_DRIVE];
        u32 t = fs ? U8(fs, FS_TYPE) : 0;
        const char *fmt = t == FS_EXFAT ? "exFAT" : t == FS_FAT32 ? "FAT32" : t == 2 ? "FAT16" : t == 1 ? "FAT12" : 0;
        if (fmt)
            text(&d, x + wd - 4, y + UP_H - 22, C_DIM, OPT_RIGHTX, fmt);
    }

    dl(&d, DL_RESTORE_CONTEXT);
    fw_eve_end();
    return 0;
}

static u32 panel_key(const struct cfw_usb *u)
{
    u32 k = u->mode;
    k |= (u32)(fw_usb_composite[COMP_ATTACH] & 1) << 3;
    k |= (u32)(fw_usb_composite[COMP_EJECTED] & 1) << 4;
    k |= (u32)(fw_usb_flags[USB_FLAG_RECONNECT] & 1) << 5;
    k |= (u32)serial_open() << 6;
    k |= (u32)confirm_active(u) << 7;
    k |= (u32)U8(fw_sd_card, SD_READY) << 8;
    return k | 0x8000;
}

/* our widget is still exactly where we left it (never touch a slot we don't own) */
static int panel_alive(const struct cfw_usb *u)
{
    if (u->slot < 0 || u->slot >= DISP_SLOTS || !u->disp || !u->widget)
        return 0;
    void **table = PTR(u->disp, DISP_TABLE);
    return table && table[u->slot] == u->widget && *(const void **)u->widget == (const void *)cfw_usb_vt;
}

static void panel_close(struct cfw_usb *u)
{
    if (panel_alive(u)) {
        void *disp = u->disp;
        VFN(disp, DISP_FLUSH, void)(disp); /* never destroy a widget the manager still has queued */
        VFN(disp, DISP_DESTROY, void, u32)(disp, (u32)u->slot);
    }
    u->slot = -1;
    u->widget = 0;
    u->key = 0;
}

static void panel_show(void *scr, struct cfw_usb *u)
{
    void *disp = PTR(scr, SCR_DISP);
    u32 key = panel_key(u);
    if (!panel_alive(u)) {
        u32 desc[9]; /* {text, value, data, flags, colors, x, y, w, h}; no memset in a CFW */
        for (int i = 0; i < 5; i++)
            ((volatile u32 *)desc)[i] = 0;
        desc[5] = UP_X;
        desc[6] = UP_Y;
        desc[7] = UP_W;
        desc[8] = UP_H;
        s32 slot = VFN(disp, DISP_CREATE, s32, u32, u32 *)(disp, WIDGET_TYPE_RECT, desc);
        if (slot < 0 || slot >= DISP_SLOTS)
            return;
        void *w = ((void **)PTR(disp, DISP_TABLE))[slot];
        *(const void **)w = (const void *)cfw_usb_vt;
        u->slot = (s16)slot;
        u->widget = w;
        u->disp = disp;
        u->key = 0;
        VFN(disp, DISP_SHOW, void, u32)(disp, (u32)slot);
    }
    if ((u16)key != u->key) {
        u->key = (u16)key;
        VFN(disp, DISP_REFRESH, void, u32)(disp, (u32)u->slot);
    }
}

/* ---- menu --------------------------------------------------------------------------------- */
void cfw_menu_build_usb(void *ctx, void *menu)
{
    (void)ctx;
    (void)menu; /* no rows: the submenu is the storage mode and its status panel */
}

static int in_usb_menu(void *scr)
{
    void *sub = PTR(scr, SCR_SUBMENU);
    return U8(scr, SCR_VISIBLE) && sub && PTR(sub, SUBMENU_BUILDER) == (void *)cfw_menu_build_usb;
}

/* menu screen update (leaving = 0) and hide (leaving = 1) */
void cfw_usb_menu_sync(void *scr, int leaving)
{
    struct cfw_usb *u = usb_state();
    if (leaving || !in_usb_menu(scr)) {
        if (u->mode == USB_ON)
            usb_start_leave(u); /* the menu closed or moved on: hand the card back now */
        else if (u->mode == USB_NOCARD || u->mode == USB_BUSY)
            u->mode = USB_OFF;
        u->back_pending = 0;
        panel_close(u);
        return;
    }
    if (u->back_pending) {
        if (u->mode == USB_OFF) { /* the card is back: now do what Back does */
            u->back_pending = 0;
            panel_close(u);
            fw_menu_back(scr);
            return;
        }
    } else if (u->mode == USB_OFF) {
        usb_enter(PTR(scr, SCR_CTX), u);
    }
    panel_show(scr, u);
}

#endif
