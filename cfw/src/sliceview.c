/* Slice panel: Menu > Samples > Slice shows a full-width panel instead of the item list:
 *   top row      [Points] key tile, the sample's name, track and length
 *   middle       the waveform cut into the slices the highlighted count would make: slices that
 *                steps will receive are bright, the rest dim; slice numbers under it
 *   info row     which steps (count, held or copied) and how many slices ("16 slices, loop")
 *   bottom row   [To Folder] key tile and the count strip 4 8 16 32 64 Steps
 * Controls: encoder = count (the stock menu cursor: the six rows still exist, hidden behind the
 * panel), encoder click = Slice (the stock row action), top key left of the screen = Points (the
 * point editor, in the same panel), bottom key = To Folder (the highlighted count's slices as their
 * own samples, export.c). Back leaves as stock.
 * Points editor: encoder = cursor, Shift + encoder = zoom, encoder click = add / remove a point,
 * top key = Delete (Shift: Clear all), bottom key = Apply (Shift: To Folder), Back = the count view.
 * The key tiles are white like the stock soft-key bar and show the Shift functions while Shift is
 * held; a grey tile means the key does nothing now.
 *
 * It is a widget in the firmware's own display manager (50 retained display-list widgets, each
 * cached in RAM_G and APPENDed into every frame): a stock "rect" widget (type 9) whose vtable is
 * replaced by cfw_wave_vt, so the stock code owns allocation, caching, frame composition and
 * destruction, and we only supply render(). Created and refreshed from the menu screen's update
 * method, destroyed from its hide method (both vtable entries hooked in target.json).
 *
 * Nothing here writes project data except the actions themselves (Slice, Apply: slice.c; To Folder:
 * export.c); the panel only reads (selection, steps, sample pool). */
#include "slice.h"

#include "dlw.h"

/* ---- firmware imports ------------------------------------------------------------------ */
extern void fw_menu_screen_update(void *scr);
extern void fw_menu_screen_hide(void *scr);

/* palette: the stock UI is white on black with #111111 bars; accent = the browser's mark red */
#define C_TEXT 0xffffff
#define C_DIM_TEXT 0x8c8c8c
#define C_WAVE 0xe6e6e6
#define C_WAVE_UNUSED 0x4a4a4a
#define C_BAND 0x0b0b0b /* the real panel shows dark greys much lighter than the vdev */
#define C_AXIS 0x2a2a2a
#define C_MARK 0xff5540  /* Polyend's accent: kept for small things only */
#define C_SEP 0x3a3a3a   /* slice separators (Elektron-style grid) */
#define C_SEP_UNUSED 0x1c1c1c
#define C_TICK 0xffffff  /* ticks above / below the boundaries of slices steps will get */
#define C_POINT 0x9a9a9a /* hand-placed points */
#define C_TILE 0xffffff        /* key tiles: as the stock soft-key bar */
#define C_TILE_TEXT 0x000000
#define C_TILE_OFF 0x2c2c2c
#define C_TILE_OFF_TEXT 0x6a6a6a
#define C_SEL 0x2e2e2e         /* the highlighted count: like the stock list's highlight */

/* ---- layout: screen coordinates (320x240: header 0..22, soft-key bar 218..240) ------------ */
#define PANEL_X 0
#define PANEL_Y 24
#define PANEL_W 320
#define PANEL_H 192
#define WAVE_X TILE_TEXT_X
#define WAVE_W (PANEL_W - 4 - TILE_TEXT_X)
#define WAVE_Y0 48
#define WAVE_H 124  /* 48 .. 172 */
#define LABEL_Y 176  /* editor: cursor time / point count (bottom row: ROW2_Y) */

/* stock type-9 vtable (0x600770fc) with render replaced */
static int cfw_wave_render(void *w);
const void *const cfw_wave_vt[10] = {
    (const void *)cfw_wave_render, fw_w9_append, fw_w9_cache, fw_w9_nop, fw_w9_nop,
    fw_w9_nop, fw_w9_set_colors, fw_w9_nop, fw_w9_nop, fw_w9_delete,
};

/* ---- sample pool ------------------------------------------------------------------------- */

int cfw_sample_get(void *ctx, u32 idx, struct sample *out)
{
    return cfw_pool_sample(PTR(PTR(ctx, MENU_CTX_PROJ), PROJ_POOL), idx, out);
}

int cfw_pool_sample(void *pool, u32 idx, struct sample *out)
{
    if (!pool || idx >= POOL_SLOTS)
        return 0;
    char *e = (char *)PTR(pool, POOL_ENTRIES) + idx * POOL_ENTRY_SIZE;
    const s16 **b = PTR(e, ENT_CH_BEGIN), **en = PTR(e, ENT_CH_END);
    u32 n = (u32)(en - b);
    out->frames = U32(e, ENT_FRAMES);
    if (n == 0 || n > 2 || out->frames == 0 || !b[0])
        return 0;
    out->channels = n;
    out->ch[0] = b[0];
    out->ch[1] = n > 1 && b[1] ? b[1] : b[0];
    out->rate = AT(e, ENT_RATE, float);
    out->name = e + ENT_NAME;
    return 1;
}

/* The chosen steps (held at Menu, or the Copy clipboard): first sample track with any, its count
 * of chosen steps, and the sample of its first chosen step (param 9 = pool index).
 * Returns the track (-1 = none); *have = that sample is in the pool. */
int cfw_steps_sample(void *ctx, struct cfw_sel *sl, struct sample *smp, int *have)
{
    int track = -1;
    *have = 0;
    cfw_collect(ctx, sl);
    for (int t = 0; t < NUM_TRACKS && track < 0; t++)
        if (sl->per_track[t])
            track = t;
    if (track >= 0)
        for (int s = 0; s < NUM_STEPS; s++) {
            if (!(sl->bits[track][s >> 5] & (1u << (s & 31))))
                continue;
            void *step = cfw_step_at(ctx, track, s);
            s16 idx = -1;
            if (VFN(step, STEP_VT_GET_PARAM, u32, int, s16 *)(step, PARAM_SAMPLE, &idx) & 0xff)
                *have = idx >= 0 && cfw_sample_get(ctx, (u32)idx, smp);
            break;
        }
    return track;
}

/* ---- drawing helpers --------------------------------------------------------------------- */
void cfw_axis(struct dlw *d, int x, int w, int cy)
{
    dl(d, DL_COLOR_RGB(C_AXIS));
    dl(d, DL_LINE_WIDTH(8));
    dl(d, DL_BEGIN(PRIM_LINES));
    dl(d, DL_VERTEX2F(x * SUB, cy * SUB + 8));
    dl(d, DL_VERTEX2F((x + w) * SUB, cy * SUB + 8));
}

static void rect(struct dlw *d, u32 color, int x0, int y0, int x1, int y1, int r)
{
    dl(d, DL_COLOR_RGB(color));
    dl(d, DL_LINE_WIDTH(r * SUB));
    dl(d, DL_BEGIN(PRIM_RECTS));
    dl(d, DL_VERTEX2F((x0 + r) * SUB, (y0 + r) * SUB));
    dl(d, DL_VERTEX2F((x1 - r) * SUB, (y1 - r) * SUB));
    dl(d, DL_END);
}

/* Font 12 (the soft-key bar's bold font, CMD_SETFONT2 handle 12: L4 glyphs, 16 x 22 cells, first
 * char 32, caps on rows 7..18): advance widths of ' '..'~', read from its metric block (RAM_G
 * 0x33250, identical on the Play+ and the Play). The chip points the handle's BITMAP_SOURCE
 * firstchar cells before the first glyph, so CELL = the character code itself (seen on the Play+:
 * CELL(code - 32) drew "0OINTS" for "Points"). */
static const u8 f12w[95] = {
    4, 5, 7, 11, 10, 15, 13, 4, 6, 6, 7, 9, 5, 7, 5, 8, 10, 7, 10, 10, 10, 10, 10, 10, 10, 10, 5, 5, 9, 9, 9, 9,
    14, 12, 10, 12, 11, 10, 9, 12, 11, 4, 6, 11, 9, 16, 12, 12, 10, 12, 11, 11, 11, 11, 11, 16, 11, 11, 10, 6, 8, 5,
    9, 8, 6, 9, 9, 9, 10, 9, 7, 10, 9, 3, 6, 9, 4, 14, 9, 10, 9, 10, 6, 9, 7, 9, 10, 13, 10, 10, 9, 5, 5, 5, 10};
#define F12_W 16
#define F12_H 22
#define DL_BITMAP_HANDLE(h) (0x05000000u | (h))
#define DL_CELL(c) (0x06000000u | ((c) & 127))
#define DL_BITMAP_SIZE(w, h) (0x08000000u | (((u32)(w) & 511) << 9) | ((u32)(h) & 511)) /* NEAREST, BORDER */
#define DL_XFORM(k, v) (((0x15u + (k)) << 24) | ((u32)(v) & ((k) == 2 || (k) == 5 ? 0xffffffu : 0x1ffffu)))

/* flush against the screen's left edge (the keys sit just outside it), rounded on the right */
void cfw_key_tile(struct dlw *d, int bottom, const char *label, int on)
{
    int y0 = bottom ? TILE_BOT_Y0 : TILE_TOP_Y0, y1 = bottom ? TILE_BOT_Y1 : TILE_TOP_Y1, len = 0;
    for (const char *p = label; *p; p++)
        len += *p >= 32 && *p < 127 ? f12w[*p - 32] : 0;
    rect(d, on ? C_TILE : C_TILE_OFF, -8, y0, TILE_W, y1, 4);
    /* the label turned 90 degrees clockwise: each glyph of handle 12 drawn as a bitmap cell with the
     * transform u = y, v = 21 - x, its draw box swapped to 22 x 16 meanwhile and put back after */
    dl(d, DL_SAVE_CONTEXT);
    dl(d, DL_COLOR_RGB(on ? C_TILE_TEXT : C_TILE_OFF_TEXT));
    dl(d, DL_BITMAP_HANDLE(12));
    dl(d, DL_BITMAP_SIZE(F12_H, F12_W));
    dl(d, DL_XFORM(0, 0));
    dl(d, DL_XFORM(1, 256));
    dl(d, DL_XFORM(2, 0));
    dl(d, DL_XFORM(3, -256));
    dl(d, DL_XFORM(4, 0));
    dl(d, DL_XFORM(5, (F12_H - 1) * 256));
    dl(d, DL_BEGIN(1)); /* BITMAPS */
    int y = (y0 + y1 - len) / 2, x = (TILE_W - F12_H) / 2 + 2; /* caps (rows 7..18) centred across */
    for (const char *p = label; *p; p++) {
        if (*p < 32 || *p >= 127)
            continue;
        dl(d, DL_CELL(*p)); /* cell = character code (see f12w) */
        dl(d, DL_VERTEX2F(x * SUB, y * SUB));
        y += f12w[*p - 32];
    }
    dl(d, DL_END);
    dl(d, DL_BITMAP_SIZE(F12_W, F12_H)); /* handle 12's own box again (the bar and headers use it) */
    dl(d, DL_RESTORE_CONTEXT);
}

/* min/max columns of frames [f0, f0 + fspan) over wd pixels; columns from px_used on are dim */
void cfw_draw_wave(struct dlw *d, const struct sample *smp, int x, int wd, int cy, int hh, u32 f0, u32 fspan,
                      int px_used)
{
    u32 cur_color = 0xffffffff, q = fspan / (u32)wd, r = fspan % (u32)wd; /* a = f0 + c * fspan / wd */
    for (int c = 0; c < wd; c++) {
        u32 a = f0 + (u32)c * q + (u32)c * r / (u32)wd, b = f0 + (u32)(c + 1) * q + (u32)(c + 1) * r / (u32)wd;
        if (b <= a)
            b = a + 1;
        if (b > smp->frames)
            b = smp->frames;
        if (a >= smp->frames)
            break;
        u32 stride = (b - a) / 48 + 1;
        s32 lo = 0, hi = 0;
        for (u32 f = a; f < b; f += stride)
            for (u32 ch = 0; ch < smp->channels; ch++) {
                s32 v = smp->ch[ch][f];
                if (v < lo)
                    lo = v;
                if (v > hi)
                    hi = v;
            }
        u32 color = c < px_used ? C_WAVE : C_WAVE_UNUSED;
        if (color != cur_color) {
            dl(d, DL_COLOR_RGB(color));
            cur_color = color;
        }
        int ytop = cy - (hi * hh >> 15), ybot = cy - (lo * hh >> 15);
        if (ybot - ytop < 1)
            ybot = ytop + 1;
        vline(d, (x + c) * SUB + 8, ytop, ybot);
    }
}

#ifdef CFW_POINTS
/* ---- Points: hand-placed slice points (Tracker-style slicing) ------------------------------
 * Points are positions 0..32767 (the Sample Start / End scale), sorted, at most PTS_MAX. The
 * window shows [view0, view0 + span) with span = 32768 >> zoom; the cursor stays inside it. */
#define PTS_MAGIC 0x50545331u /* "PTS1" */
#define ZOOM_MAX 7
#define BTN_SHIFT 10
#define BTN_LTOP 0       /* the two buttons left of the screen: top / bottom (a knob page's main /  */
#define BTN_LBOT 3       /* second parameter, FUN_600e16c8); in menus both act as Enter             */
#define BTN_CLICK 6      /* encoder click (Menu; Enter inside menus) */
#define BTN_BACK 7       /* left soft key: Back in menus */
#define BTN_DELETE 1
#define MODE_CHOOSE 2 /* v->mode: the Apply choice (Folder / Start / End) */

extern void *fw_operator_new(u32 size);

struct cfw_pts *cfw_pts(void)
{
    struct cfw_view *v = &cfw_ram.view;
    struct cfw_pts *t = v->pts;
    if (!t || t->magic != PTS_MAGIC) {
        t = fw_operator_new(sizeof *t);
        if (!t)
            return 0;
        for (u32 i = 0; i < sizeof *t; i++)
            ((volatile u8 *)t)[i] = 0;
        t->cursor = 16384;
        t->magic = PTS_MAGIC;
        v->pts = t;
    }
    return t;
}

u32 cfw_pts_span(const struct cfw_pts *t) { return 32768u >> t->zoom; }

void cfw_pts_keep_visible(struct cfw_pts *t)
{
    u32 sp = cfw_pts_span(t), c = t->cursor, v0 = t->view0;
    if (c < v0)
        v0 = c;
    if (c >= v0 + sp)
        v0 = c - sp + 1;
    if (v0 + sp > 32768u)
        v0 = 32768u - sp;
    t->view0 = (u16)v0;
}

void cfw_pts_move(struct cfw_pts *t, s32 d, u32 unit) /* unit: position units per detent */
{
    s32 c = (s32)t->cursor + d * (s32)unit;
    t->cursor = (u16)(c < 0 ? 0 : c > 32767 ? 32767 : c);
    cfw_pts_keep_visible(t);
}

void cfw_pts_zoom(struct cfw_pts *t, s32 d)
{
    s32 z = (s32)t->zoom + (d > 0 ? 1 : -1);
    if (z < 0 || z > ZOOM_MAX)
        return;
    u32 old = cfw_pts_span(t), rel = t->cursor - t->view0; /* keep the cursor at the same screen spot */
    t->zoom = (u8)z;
    u32 sp = cfw_pts_span(t);
    u32 off = rel * sp / old; /* < 2^30 */
    t->view0 = (u16)(t->cursor > off ? t->cursor - off : 0);
    cfw_pts_keep_visible(t);
}

/* the point nearest the cursor, with its distance */
static u32 nearest(const struct cfw_pts *t, u32 *dist)
{
    u32 best = 0, bd = 0xffffffffu;
    for (u32 i = 0; i < t->count; i++) {
        u32 dlt = t->p[i] > t->cursor ? t->p[i] - t->cursor : t->cursor - t->p[i];
        if (dlt < bd)
            bd = dlt, best = i;
    }
    *dist = bd;
    return best;
}

static void remove_at(struct cfw_pts *t, u32 i)
{
    for (; i + 1 < t->count; i++)
        t->p[i] = t->p[i + 1];
    t->count--;
}

/* Enter: remove the point under the cursor, or add one there */
static void toggle(struct cfw_pts *t)
{
    u32 dlt, i = nearest(t, &dlt);
    if (t->count && dlt <= cfw_pts_span(t) / 100 + 1) {
        remove_at(t, i);
        return;
    }
    if (t->count >= PTS_MAX || t->cursor == 0)
        return;
    for (i = t->count; i > 0 && t->p[i - 1] > t->cursor; i--)
        t->p[i] = t->p[i - 1];
    t->p[i] = t->cursor;
    t->count++;
}

void cfw_pts_toggle(struct cfw_pts *t) { toggle(t); }

void cfw_pts_delete_near(struct cfw_pts *t)
{
    u32 dlt;
    if (t->count)
        remove_at(t, nearest(t, &dlt));
}

/* The input never reaches the menu while editing, so the menu screen does not update: ask the
 * display manager to re-render the panel ourselves. */
static int redraw(struct cfw_view *v)
{
    if (cfw_view_alive(v))
        VFN(v->disp, DISP_REFRESH, void, u32)(v->disp, (u32)v->slot);
    return 1;
}

/* the panel has a sample to work on (the chosen steps' sample is in the pool) */
static int have_sample(struct cfw_view *v)
{
    struct cfw_sel sl;
    struct sample smp;
    int have, track = cfw_steps_sample(v->ctx, &sl, &smp, &have);
    return track >= 0 && have;
}

/* the count view: the two keys left of the screen are Points / To Folder, nothing else is ours
 * (encoder and encoder click move and run the stock rows). In menus those keys act as Enter, so
 * they are always taken here, even when there is nothing to do (no accidental Slice). */
static int count_input(struct cfw_view *v, struct cfw_pts *t, u32 id)
{
    if (!have_sample(v))
        return 1;
    if (id == BTN_LTOP) {
        v->mode = 1;
        return redraw(v);
    }
    v->mode = MODE_CHOOSE; /* Apply: Folder or Start / End (choose_input) */
    t->apply_pts = 0;
    return redraw(v);
}

/* the Apply choice: top key = Folder (the slices as a new pool folder, the chosen steps then play
 * them), bottom key = Start / End (classic Slice), Back = cancel. Both close the menu. */
static int choose_input(struct cfw_view *v, struct cfw_pts *t, u32 kind, u32 id)
{
    if (kind == 4 || kind == 2)
        return 1;
    if (kind != 1 || (id != BTN_LTOP && id != BTN_LBOT && id != BTN_BACK && id != BTN_CLICK && id != BTN_DELETE))
        return 0;
    u32 n = t->apply_pts ? t->count + 1u : v->cursor < SLICE_ITEM_STEPS ? 4u << v->cursor : 0;
    const u16 *cuts = t->apply_pts ? t->p : 0;
    if (id == BTN_BACK) {
        v->mode = t->apply_pts ? 1 : 0;
        return redraw(v);
    }
    if (id == BTN_LBOT) {
        cfw_apply_slices(v->ctx, n, cuts);
        return 1;
    }
#ifdef CFW_EXPORT
    if (id == BTN_LTOP && cfw_ext_ok())
        cfw_export_slices(v->ctx, n, cuts, 1);
#endif
    return 1;
}

/* input listener wrappers (screen.c): kind = slot (0 pad, 1 button, 2 knob, 4 encoder) */
int cfw_points_input(u32 kind, u32 id, u32 val)
{
    struct cfw_view *v = &cfw_ram.view;
    int ours = v->magic == VIEW_MAGIC && cfw_view_alive(v);
    if (kind == 1 && id == BTN_SHIFT && val < 2) { /* tracked always (press / release), passed on */
        u8 sh = val == 0;
        if (sh != v->shift) {
            v->shift = sh;
            if (ours && v->wmode == WMODE_SLICE && v->mode)
                redraw(v); /* the editor's key tiles show the Shift functions */
        }
    }
#ifdef CFW_STARTEND
    if (ours && v->wmode == WMODE_STARTEND)
        return cfw_startend_input(kind, id, val);
#endif
    struct cfw_pts *t = cfw_pts();
    if (!t)
        return 0;
    if (kind == 1 && id < 16) {
        u16 m = (u16)(1u << id);
        if (val != 0 && (t->eat & m)) { /* hold / release of a press we took */
            if (val == 1)
                t->eat &= (u16)~m;
            return 1;
        }
        if (val == 0)
            t->eat &= (u16)~m; /* a new press: taken or not, decided below */
    }
    if (!ours || v->wmode != WMODE_SLICE)
        return 0;
    if (kind == 1 && val == 0 && id < 16 &&
        (id == BTN_LTOP || id == BTN_LBOT || (v->mode && (id == BTN_CLICK || id == BTN_BACK || id == BTN_DELETE))))
        t->eat |= (u16)(1u << id);
    if (!v->mode)
        return kind == 1 && (id == BTN_LTOP || id == BTN_LBOT) ? (val == 0 ? count_input(v, t, id) : 1) : 0;
    if (v->mode == MODE_CHOOSE)
        return kind == 1 && val != 0 ? 1 : choose_input(v, t, kind, id);

    /* the Points editor */
    s32 d = (s32)val;
    if (kind == 4) { /* encoder: cursor, Shift + encoder: zoom */
        if (v->shift)
            cfw_pts_zoom(t, d);
        else
            cfw_pts_move(t, d, cfw_pts_span(t) / 128 + 1);
        return redraw(v);
    }
    if (kind == 2)
        return 1; /* knobs would edit the parameters under the menu: ignored meanwhile */
    if (kind != 1 || val != 0 ||
        (id != BTN_CLICK && id != BTN_LTOP && id != BTN_LBOT && id != BTN_DELETE && id != BTN_BACK))
        return 0;
    if (id == BTN_BACK) { /* back to the count view (the menu stays in Slice) */
        v->mode = 0;
        return redraw(v);
    }
    if (id == BTN_LBOT) { /* Apply: Folder or Start / End */
        if (!t->count)
            return 1;
        v->mode = MODE_CHOOSE;
        t->apply_pts = 1;
        return redraw(v);
    }
    if (id == BTN_CLICK)
        toggle(t);
    else if (v->shift) /* Shift + Delete: all */
        t->count = 0;
    else if (t->count) {
        u32 dlt;
        remove_at(t, nearest(t, &dlt));
    }
    return redraw(v);
}

/* the editor: waveform window, points, cursor */
static void draw_points(struct dlw *d, const struct sample *smp)
{
    struct cfw_pts *t = cfw_pts();
    if (!t)
        return;
    u32 v0 = t->view0, sp = cfw_pts_span(t);
    int x = WAVE_X, wd = WAVE_W, wy0 = WAVE_Y0, wy1 = wy0 + WAVE_H, cy = wy0 + WAVE_H / 2;
    char buf[40], *p;
    u32 f0 = (u32)(((u64)v0 * smp->frames) >> 15), fsp = (u32)(((u64)sp * smp->frames) >> 15);
    cfw_axis(d, x, wd, cy);
    cfw_draw_wave(d, smp, x, wd, cy, WAVE_H / 2 - 4, f0, fsp ? fsp : 1, wd);
#ifdef CFW_MOD
    if (cfw_ext_ok())
        cfw_draw_heads(d, smp, x, wd, wy0, wy1, v0, sp);
#endif
    dl(d, DL_LINE_WIDTH(8));
    dl(d, DL_COLOR_RGB(C_POINT));
    for (u32 i = 0; i < t->count; i++)
        if (t->p[i] >= v0 && t->p[i] < v0 + sp)
            vline(d, (x + (int)((t->p[i] - v0) * (u32)wd / sp)) * SUB + 8, wy0, wy1);
    dl(d, DL_END);
    dl(d, DL_BEGIN(PRIM_RECTS)); /* a small flag on each point */
    for (u32 i = 0; i < t->count; i++)
        if (t->p[i] >= v0 && t->p[i] < v0 + sp) {
            int px = x + (int)((t->p[i] - v0) * (u32)wd / sp);
            dl(d, DL_VERTEX2F(px * SUB, (wy0 - 4) * SUB));
            dl(d, DL_VERTEX2F((px + 6) * SUB, wy0 * SUB));
        }
    dl(d, DL_END);
    dl(d, DL_COLOR_RGB(C_TEXT));
    dl(d, DL_LINE_WIDTH(12));
    dl(d, DL_BEGIN(PRIM_LINES));
    vline(d, (x + (int)((t->cursor - v0) * (u32)wd / sp)) * SUB + 8, wy0 - 4, wy1 + 4);
    dl(d, DL_END);
    /* cursor time and zoom (left), point count (right) */
    u32 ms = smp->rate > 0.0f ? (u32)((float)(u32)(((u64)t->cursor * smp->frames) >> 15) * 1000.0f / smp->rate) : 0;
    p = cfw_put_uint(buf, ms / 1000);
    *p++ = '.';
    *p++ = (char)('0' + ms / 100 % 10);
    *p++ = (char)('0' + ms / 10 % 10);
    *p++ = (char)('0' + ms % 10);
    p = cfw_put_str(p, " s");
    if (t->zoom) {
        p = cfw_put_str(p, "  zoom x");
        p = cfw_put_uint(p, 1u << t->zoom);
    }
    *p = 0;
    text(d, x, LABEL_Y, C_DIM_TEXT, 0, buf);
    p = cfw_put_uint(buf, t->count);
    p = cfw_put_str(p, t->count == 1 ? " point" : " points");
    *p = 0;
    text(d, x + wd, LABEL_Y, C_DIM_TEXT, OPT_RIGHTX, buf);
    text(d, x + wd, ROW2_Y, C_DIM_TEXT, OPT_RIGHTX, t->count ? "Click: add / remove" : "Click: add a point");
    /* what Apply makes, next to its tile */
    p = cfw_put_uint(buf, t->count + 1u);
    p = cfw_put_str(p, t->count ? " slices" : " slice");
    *p = 0;
    text(d, TILE_TEXT_X, ROW2_Y, t->count ? C_MARK : C_DIM_TEXT, 0, buf);
}
#endif

int cfw_ptr_ok(const void *p)
{
    u32 a = (u32)p;
    return (a >= 0x20000000u && a < 0x20080000u) || (a >= 0x20200000u && a < 0x20300000u) ||
           (a >= 0x60000000u && a < 0x61000000u) || (a >= 0x80000000u && a < 0x82000000u);
}

int cfw_str_is(const char *s, const char *prefix)
{
    while (*prefix && *s == *prefix)
        s++, prefix++;
    return !*prefix;
}

/* the screen showing knob pages, if it is the current one: ui+0 or ui+4 (two screens of the same
 * class; which one depends on context, e.g. ui+4 after boot, ui+0 once a pad was used) */
void *cfw_param_screen(void *ui)
{
    void *cur = PTR(ui, UI_CUR_SCREEN);
    return cur && (cur == PTR(ui, UI_PARAM_SCREEN) || cur == PTR(ui, UI_PARAM_SCREEN2)) ? cur : 0;
}

const char *cfw_page_title(void *app)
{
    void *ui = PTR((char *)app + APP_LISTENER, LISTENER_UI);
    if (!cfw_ptr_ok(ui))
        return 0;
    void *mscr = PTR(ui, UI_MENU_SCREEN), *pscr = cfw_param_screen(ui);
    if (!cfw_ptr_ok(mscr) || !cfw_ptr_ok(pscr) || U8(mscr, SCR_VISIBLE))
        return 0;
    void *disp = PTR(mscr, SCR_DISP);
    u32 slot = U32(pscr, PSCR_SLOTS);
    if (!cfw_ptr_ok(disp) || slot >= DISP_SLOTS)
        return 0;
    void *w = ((void **)PTR(disp, DISP_TABLE))[slot];
    const char *t = cfw_ptr_ok(w) ? PTR(w, W_TEXT) : 0;
    return cfw_ptr_ok(t) ? t : 0;
}

/* Code linked past the image end (Play+: export.c) is only used when the updater has written it:
 * header magic, length, and the sum of the words after the header must all match. */
int cfw_ext_ok(void)
{
#ifdef CFW_EXT_BASE
    struct cfw_view *v = cfw_view_state();
    if (!v->ext) {
        const u32 *w = (const u32 *)CFW_EXT_BASE;
        u32 n = w[1] / 4, sum = 0;
        v->ext = 2;
        if (w[0] == CFW_EXT_MAGIC && n >= 3 && n <= CFW_EXT_MAX / 4) {
            for (u32 i = 3; i < n; i++)
                sum += w[i];
            if (sum == w[2])
                v->ext = 1;
        }
    }
    return v->ext == 1;
#else
    return 1;
#endif
}

#ifdef CFW_STARTEND
/* vtable slot 0 of the Sample Start / End parameter descriptors: their value text. Notes which
 * step / track the page shows (startend.c draws it), then formats as before. */
extern void fw_fmt_startend(void *self, u32 value, u32 mode, char *buf, u32 len, void *obj);
void cfw_fmt_startend(void *self, u32 value, u32 mode, char *buf, u32 len, void *obj)
{
    struct cfw_view *v = cfw_view_state();
    if (obj)
        v->se_obj = obj;
    if (!v->se_desc || (u32)self < (u32)v->se_desc)
        v->se_desc = self;
    v->se_val[self != v->se_desc] = (s16)value;
    fw_fmt_startend(self, value, mode, buf, len, obj);
}
#endif

/* message in the waveform area */
static void draw_message(struct dlw *d, const char *l1, const char *l2, const char *l3)
{
    int cx = WAVE_X + WAVE_W / 2, cy = WAVE_Y0 + WAVE_H / 2;
    cfw_axis(d, WAVE_X, WAVE_W, cy);
    dl(d, DL_END);
    text(d, cx, cy - 40, C_TEXT, OPT_CENTERX, l1);
    if (l2)
        text(d, cx, cy + 8, C_DIM_TEXT, OPT_CENTERX, l2);
    if (l3)
        text(d, cx, cy + 28, C_DIM_TEXT, OPT_CENTERX, l3);
}

/* the count strip (bottom row, right): the six Slice rows, the highlighted one on a dark tile */
static void draw_strip(struct dlw *d, u32 sel)
{
    static const char *const lab[SLICE_ITEMS] = {"4", "8", "16", "32", "64", "Steps"};
    int w[SLICE_ITEMS], x = 0;
    for (u32 i = 0; i < SLICE_ITEMS; i++)
        x += w[i] = (int)slen(lab[i]) * CHAR_W + 6;
    x = PANEL_W - 4 - x;
    for (u32 i = 0; i < SLICE_ITEMS; i++) {
        if (i == sel)
            rect(d, C_SEL, x, ROW2_Y - 2, x + w[i], ROW2_Y + 21, 4);
        text(d, x + w[i] / 2, ROW2_Y, i == sel ? C_TEXT : C_DIM_TEXT, OPT_CENTERX, lab[i]);
        x += w[i];
    }
}

static int cfw_wave_render(void *w)
{
    struct cfw_view *v = &cfw_ram.view;
#ifdef CFW_STARTEND
    if (v->wmode == WMODE_STARTEND)
        return cfw_startend_render(w);
#endif
#ifdef CFW_PAGES
    if (v->wmode == WMODE_PAGE)
        return cfw_pages_render(w);
#endif
#ifdef CFW_COLORS
    if (v->wmode == WMODE_POOL)
        return cfw_colors_render(w);
#endif
#ifdef CFW_MOD
    if (v->wmode == WMODE_LFO)
        return cfw_lfo_render(w);
    if (v->wmode == WMODE_SLICER)
        return cfw_slicer_render(w);
#endif
    struct cfw_sel sl;
    struct sample smp;
    struct dlw d = {0};
    char buf[40], *p;
    int have, track = cfw_steps_sample(v->ctx, &sl, &smp, &have), ok = track >= 0 && have;
    u32 sel = v->cursor < SLICE_ITEMS ? v->cursor : 2;
    u32 steps = track >= 0 ? sl.per_track[track] : 0;
    u32 n = cfw_item_slices(sel, steps);
    if (n < 1)
        n = 1;
    if (n > NUM_STEPS)
        n = NUM_STEPS;
    u32 used = steps < n ? steps : n;
    int edit = 0, choose = 0;

    fw_eve_begin();
    fw_eve_dlstart();
    dl(&d, DL_SAVE_CONTEXT);
    dl(&d, DL_VERTEX_FORMAT(4));
    rect(&d, 0x000000, PANEL_X, PANEL_Y, PANEL_X + PANEL_W, PANEL_Y + PANEL_H, 1); /* cover the hidden list */

    /* the two keys left of the screen */
#ifdef CFW_POINTS
    struct cfw_pts *t = cfw_pts();
    choose = v->mode == MODE_CHOOSE && t;
    edit = t && (v->mode == 1 || (choose && t->apply_pts));
    if (choose) {
        cfw_key_tile(&d, 0, "Folder", ok && cfw_ext_ok());
        cfw_key_tile(&d, 1, "Start/End", ok);
    } else if (edit) {
        cfw_key_tile(&d, 0, v->shift ? "Clear" : "Delete", ok && t->count);
        cfw_key_tile(&d, 1, "Apply", ok && t->count);
    } else {
        cfw_key_tile(&d, 0, "Points", ok);
        cfw_key_tile(&d, 1, "Apply", ok);
    }
#endif
    if (!edit)
        draw_strip(&d, sel);

    if (track < 0) {
        if (sl.skipped)
            draw_message(&d, "No sample-track trigs", "Slice only changes trigs", "on sample tracks");
        else
            draw_message(&d, "No steps chosen", "Hold steps, then Menu,", "or Copy steps first");
    } else if (!have) {
        draw_message(&d, "Sample not loaded", "the steps' sample is", "not in the pool");
    } else {
        int wy0 = WAVE_Y0, wy1 = wy0 + WAVE_H, cy = wy0 + WAVE_H / 2, hh = WAVE_H / 2 - 4;
        int x = WAVE_X, wd = WAVE_W;

        /* top row, right of the Points / Delete tile: sample name, then track and length */
        u32 cs = smp.rate > 0.0f ? (u32)((float)smp.frames * 100.0f / smp.rate + 0.5f) : 0;
        p = cfw_put_str(buf, "T");
        p = cfw_put_uint(p, (u32)track + 1);
        if (sl.tracks > 1) {
            *p++ = '+';
            p = cfw_put_uint(p, sl.tracks - 1);
        }
        p = cfw_put_str(p, "  ");
        p = cfw_put_uint(p, cs / 100);
        *p++ = '.';
        *p++ = (char)('0' + cs / 10 % 10);
        *p++ = (char)('0' + cs % 10);
        p = cfw_put_str(p, smp.channels > 1 ? "s st" : "s");
        *p = 0;
        text(&d, PANEL_W - 4, ROW1_Y, C_DIM_TEXT, OPT_RIGHTX, buf);
        {
            u32 room = (PANEL_W - 4 - TILE_TEXT_X - slen(buf) * CHAR_W - 8) / CHAR_W, k = 0;
            char nm[40];
            while (k < room && k < sizeof(nm) - 3 && smp.name[k])
                nm[k] = smp.name[k], k++;
            if (smp.name[k] && k >= 2)
                nm[k - 2] = '.', nm[k - 1] = '.';
            nm[k] = 0;
            text(&d, TILE_TEXT_X, ROW1_Y, C_TEXT, 0, nm);
        }

#ifdef CFW_POINTS
        if (edit) {
            draw_points(&d, &smp);
            goto done;
        }
#endif
        /* Elektron-style slice grid: alternating dark bands behind the slices steps will get, thin
         * separators, white ticks above / below their boundaries; the rest dimmed */
        dl(&d, DL_COLOR_RGB(C_BAND));
        dl(&d, DL_LINE_WIDTH(SUB));
        dl(&d, DL_BEGIN(PRIM_RECTS));
        for (u32 k = 0; k < used; k += 2) {
            int bx0 = x + (int)(k * (u32)wd / n), bx1 = x + (int)((k + 1) * (u32)wd / n);
            dl(&d, DL_VERTEX2F(bx0 * SUB + SUB, wy0 * SUB + SUB));
            dl(&d, DL_VERTEX2F(bx1 * SUB - SUB, wy1 * SUB - SUB));
        }
        dl(&d, DL_END);
        cfw_axis(&d, x, wd, cy);
        cfw_draw_wave(&d, &smp, x, wd, cy, hh, 0, smp.frames, (int)(used * (u32)wd / n));
#ifdef CFW_MOD
        if (cfw_ext_ok())
            cfw_draw_heads(&d, &smp, x, wd, wy0, wy1, 0, 32768);
#endif
        dl(&d, DL_LINE_WIDTH(8));
        dl(&d, DL_BEGIN(PRIM_LINES));
        for (u32 k = 0; k <= n; k++) {
            int mx = (x + (int)(k * (u32)wd / n)) * SUB + 8 - (k == n ? SUB : 0);
            dl(&d, DL_COLOR_RGB(k <= used ? C_SEP : C_SEP_UNUSED));
            vline(&d, mx, wy0, wy1);
            if (k <= used) {
                dl(&d, DL_COLOR_RGB(C_TICK));
                vline(&d, mx, wy0 - 5, wy0);
                vline(&d, mx, wy1, wy1 + 5);
            }
        }
        dl(&d, DL_END);

        /* bottom row, left of the count strip: the steps, and "loop" when they cycle the slices */
        p = cfw_put_uint(buf, sl.total);
        p = cfw_put_str(p, sl.copied ? " copied" : (sl.total == 1 ? " step" : " steps"));
        if (steps > n)
            p = cfw_put_str(p, ", loop");
        *p = 0;
        text(&d, x, ROW2_Y, C_DIM_TEXT, 0, buf);
    }

#ifdef CFW_POINTS
done:
    if (choose && ok) { /* the Apply choice over the view */
        u32 na = t->apply_pts ? t->count + 1u : n;
        /* the view stays; one line says what is asked, the two key tiles are the answers */
        int by = WAVE_Y0 + WAVE_H / 2 - 16;
        rect(&d, 0x000000, WAVE_X + 24, by, WAVE_X + WAVE_W - 24, by + 32, 6);
        p = cfw_put_str(buf, "Apply ");
        p = cfw_put_uint(p, na);
        p = cfw_put_str(p, na == 1 ? " slice as" : " slices as");
        *p = 0;
        text_f(&d, WAVE_X + WAVE_W / 2, by + 3, FONT_LARGE, C_TEXT, OPT_CENTERX, buf);
    }
#endif
    dl(&d, DL_RESTORE_CONTEXT);
    fw_eve_end();
    (void)w;
    return 0;
}

/* ---- lifecycle --------------------------------------------------------------------------- */
struct cfw_view *cfw_view_state(void)
{
    struct cfw_view *v = &cfw_ram.view;
    if (v->magic != VIEW_MAGIC) {
        for (u32 i = 0; i < sizeof *v; i++)
            ((volatile u8 *)v)[i] = 0;
        v->magic = VIEW_MAGIC;
        v->slot = -1;
    }
    return v;
}

/* Our widget still exists exactly where we left it (never touch a slot we don't own). */
int cfw_view_alive(struct cfw_view *v)
{
    if (v->slot < 0 || v->slot >= DISP_SLOTS || !v->disp || !v->widget)
        return 0;
    void **table = PTR(v->disp, DISP_TABLE);
    return table && table[v->slot] == v->widget && *(const void **)v->widget == (const void *)cfw_wave_vt;
}

/* the menu's item list: hidden behind the slice panel (it would be drawn over it), shown again after */
static void list_show(void *scr, void *disp, int show)
{
    u32 ls = U32(scr, SCR_LIST_SLOT);
    void **tab = PTR(disp, DISP_TABLE);
    if (ls < DISP_SLOTS && tab[ls] && (int)(U32(tab[ls], WG_FLAGS) & 1) != show)
        VFN(disp, show ? DISP_SHOW_SLOT : DISP_HIDE_SLOT, void, u32)(disp, ls);
}

static void view_close(void *scr, struct cfw_view *v)
{
    if (cfw_view_alive(v)) {
        void *disp = v->disp;
        VFN(disp, DISP_FLUSH, void)(disp); /* never destroy a widget the manager still has queued */
        VFN(disp, DISP_DESTROY, void, u32)(disp, (u32)v->slot);
        if ((v->wmode == WMODE_SLICE || v->wmode == WMODE_LFO || v->wmode == WMODE_SLICER) && PTR(scr, SCR_DISP) == disp) {
            list_show(scr, disp, 1);
#ifdef CFW_MOD
            /* stock's Back already drew its frame with the panel in it: one without */
            u32 ls = U32(scr, SCR_LIST_SLOT);
            if ((v->wmode == WMODE_LFO || v->wmode == WMODE_SLICER) && ls < DISP_SLOTS && ((void **)PTR(disp, DISP_TABLE))[ls])
                VFN(disp, DISP_REFRESH, void, u32)(disp, ls);
#endif
        }
    }
    v->slot = -1;
    v->widget = 0;
    v->wmode = 0;
    v->mode = 0;
}

#ifdef CFW_MOD
/* the LFO panel, when its submenu has been left: the menu screen's update doesn't always run (cfw_mod_tick) */
void cfw_lfo_close_if_left(void *scr)
{
    struct cfw_view *v = &cfw_ram.view;
    void *sub = PTR(scr, SCR_SUBMENU), *b = sub ? PTR(sub, SUBMENU_BUILDER) : 0;
    if (U8(scr, SCR_VISIBLE) && (b == (void *)cfw_menu_build_lfo || b == (void *)cfw_menu_build_matrix ||
                                 b == (void *)cfw_menu_build_slice))
        return;
    view_close(scr, v);
}
#endif

/* the menu screen shows Menu > Samples > Slice */
static int in_slice_menu(void *scr)
{
    void *sub = PTR(scr, SCR_SUBMENU);
    return U8(scr, SCR_VISIBLE) && sub && PTR(sub, SUBMENU_BUILDER) == (void *)cfw_menu_build_slice;
}

void cfw_menu_screen_update(void *scr)
{
    fw_menu_screen_update(scr);
    struct cfw_view *v = cfw_view_state();
    u32 want = WMODE_SLICE;
#ifdef CFW_MOD
    void *lsub = PTR(scr, SCR_SUBMENU);
    int lmatrix = lsub && PTR(lsub, SUBMENU_BUILDER) == (void *)cfw_menu_build_matrix;
    if (U8(scr, SCR_VISIBLE) && lsub && (lmatrix || PTR(lsub, SUBMENU_BUILDER) == (void *)cfw_menu_build_lfo) && cfw_ext_ok())
        want = WMODE_LFO;
    else if (in_slice_menu(scr) && cfw_ext_ok())
        want = WMODE_SLICER;
    else
#endif
    if (!in_slice_menu(scr)) {
        view_close(scr, v); /* also takes down the Start / End panel while a menu is up */
        return;
    }
    void *disp = PTR(scr, SCR_DISP);
    if (!cfw_view_alive(v) || v->wmode != want) {
        if (cfw_view_alive(v))
            view_close(scr, v);
        u32 desc[9]; /* {text, value, data, flags, colors, x, y, w, h}; no memset in a CFW */
        for (int i = 0; i < 5; i++)
            ((volatile u32 *)desc)[i] = 0;
        desc[5] = PANEL_X;
        desc[6] = PANEL_Y;
        desc[7] = PANEL_W;
        desc[8] = PANEL_H;
        s32 slot = VFN(disp, DISP_CREATE, s32, u32, u32 *)(disp, WIDGET_TYPE_RECT, desc);
        if (slot < 0 || slot >= DISP_SLOTS)
            return;
        void *w = ((void **)PTR(disp, DISP_TABLE))[slot];
        *(const void **)w = (const void *)cfw_wave_vt;
        v->slot = slot;
        v->widget = w;
        v->disp = disp;
        v->wmode = (u8)want;
        v->mode = 0;
#ifdef CFW_MOD
        if (want == WMODE_LFO)
            cfw_lfo_enter(lmatrix);
#endif
        VFN(disp, DISP_SHOW, void, u32)(disp, (u32)slot);
    }
    list_show(scr, disp, 0); /* every pass: the stock code shows its list again on its own */
#ifdef CFW_MOD
    if (want == WMODE_LFO) {
        struct mod_ui *mu = cfw_mod_ui();
        if (mu)
            mu->scr = scr;
        return; /* cfw_mod_tick keeps it drawn */
    }
    if (want == WMODE_SLICER) {
        cfw_slicer_enter(scr);
        return; /* cfw_slicer_tick keeps it drawn */
    }
#endif
    v->ctx = PTR(scr, SCR_CTX);
    v->cursor = U32(PTR(scr, SCR_SUBMENU), SUB_CURSOR);
    VFN(disp, DISP_REFRESH, void, u32)(disp, (u32)v->slot);
}

void cfw_menu_screen_hide(void *scr)
{
    struct cfw_view *v = cfw_view_state();
    view_close(scr, v);
    fw_menu_screen_hide(scr);
}
