/* Menu > Samples > Slice (Play+, Play RT): pick a track, set the slices, choose where they land, apply.
 *
 *   select pads     the rightmost pad of a row picks the track (it blinks blue, as in the LFO screens)
 *   encoder         the number of slices. Zoomed in: scrolls the waveform
 *   Shift + encoder zooms the waveform
 *   encoder click   applies, and the screen stays: press Play, listen, change it, apply again
 *   top key         Layout: In time (each slice where it sits in the sample), Every step, My trigs
 *   Shift + top     Sample: choose the track's sample from the pool (without a sample: the top key alone)
 *   bottom key      Points: place the cuts by hand
 *   Shift + click   applies with every slice as its own sample in a new pool folder
 *   Back            leaves (from Points or the sample list: back to the slices)
 *
 * What will happen is always on the screen before it does: the cuts on the waveform, a strip of the track's steps with
 * the ones that will get a slice lit, and the same on the step pads. Applying replaces the track's trigs (In time, Every
 * step) or gives the existing trigs their slices in order (My trigs); it is one undo step. */
#include "slice.h"
#include "dlw.h"

#ifdef CFW_MOD
extern volatile u32 fw_millis;
extern void fw_toast_show(void *toast, const char *msg);
extern void *fw_operator_new(u32 size);
extern u32 fw_step_toggle(void *sel, int track, int step);

#define SL_MAGIC 0x534c4331u /* "SLC1" */
#define BTN_TOP 0
#define BTN_BOT 3
#define BTN_CLICK 6
#define BTN_BACK 7
#define SELECT_COL 19
#define MODE_MAIN 0
#define MODE_POINTS 1
#define MODE_PICK 2
enum { LAY_TIME, LAY_STEPS, LAY_TRIGS, LAYOUTS };
#define PICK_ROWS 7
#define TRK_VT_LENGTH 0x04 /* (track) -> steps */

#define C_WHITE 0xffffff
#define C_DIM 0x7a7a7a
#define C_BAND 0x141414
#define C_CUT 0x8a8a8a
#define C_BLUE 0x3a7bff
#define C_STEP 0x2a2a2a
#define C_STEP4 0x3c3c3c
#define C_ROW 0x1e1e1e
#define WX TILE_TEXT_X
#define WW (320 - 4 - TILE_TEXT_X)
#define WY0 48
#define WY1 136
#define STRIP_Y0 142
#define STRIP_Y1 158

static const u8 counts[] = {2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64};
#define COUNTS (sizeof counts / sizeof counts[0])
static const char *const layout_name[LAYOUTS] = {"In time", "Every step", "My trigs"};
static const u8 led_lit[3] = {0x00, 0x28, 0xc0}, led_off[3] = {0, 0, 0};

struct slicer {
    u32 magic;
    u8 mode, track, cnt, layout;
    u8 use_pts, applied, lit, pad;
    s16 choice[MOD_TRACKS];   /* the sample chosen here, -1: the one its trigs play */
    s16 pick, source;         /* the list's highlight; the sample the points were placed on */
    u32 key, drawn_ms, led_key;
    u32 bits[2];              /* the steps that will get a slice */
    void *scr;
    s8 lit_row;               /* the row of pads previewing, -1 none */
};

static struct slicer *peek(void)
{
    void **slot = cfw_pages_slicer(0);
    struct slicer *l = slot ? *slot : 0;
    return l && l->magic == SL_MAGIC ? l : 0;
}

static struct slicer *state(void)
{
    void **slot = cfw_pages_slicer(1);
    if (!slot)
        return 0;
    struct slicer *l = *slot;
    if (!l || l->magic != SL_MAGIC) {
        l = fw_operator_new(sizeof *l);
        if (!l)
            return 0;
        for (u32 i = 0; i < sizeof *l; i++)
            ((volatile u8 *)l)[i] = 0;
        for (u32 t = 0; t < MOD_TRACKS; t++)
            l->choice[t] = -1;
        l->cnt = 6; /* 16 */
        l->source = -1;
        l->lit_row = -1;
        l->magic = SL_MAGIC;
        *slot = l;
    }
    return l;
}

static void *ctx_of(const struct slicer *l) { return l->scr ? PTR(l->scr, SCR_CTX) : 0; }
static void *ctrl_of(void *ctx) { return PTR(ctx, MENU_CTX_CTRL); }

static u32 track_len(void *ctx, u32 t)
{
    void *pv = PTR((char *)ctrl_of(ctx) + CTRL_SEL, SEL_PVIEW);
    void *trk = VFN(pv, 0, void *, int)(pv, (int)t);
    s32 n = VFN(trk, TRK_VT_LENGTH, s32)(trk);
    return n < 1 ? 1 : n > NUM_STEPS ? NUM_STEPS : (u32)n;
}

/* the track's sample: the one chosen here, else the one its first trig plays */
static int track_sample(const struct slicer *l, void *ctx, struct sample *smp, s16 *idx)
{
    s16 i = l->choice[l->track];
    for (u32 s = 0; i < 0 && s < NUM_STEPS; s++) {
        void *st = cfw_step_at(ctx, (int)l->track, (int)s);
        if (VFN(st, STEP_VT_IS_ON, u32)(st) & 0xff)
            VFN(st, STEP_VT_GET_PARAM, u32, int, s16 *)(st, PARAM_SAMPLE, &i);
    }
    *idx = i;
    return i >= 0 && cfw_sample_get(ctx, (u32)i, smp);
}

/* the slice count the user asked for */
static u32 wanted(const struct slicer *l, const struct cfw_pts *t)
{
    return l->use_pts && t && t->count ? t->count + 1u : counts[l->cnt];
}

static u32 cut_pos(const struct slicer *l, const struct cfw_pts *t, u32 j, u32 n)
{
    if (l->use_pts && t && t->count)
        return j == 0 ? 0 : j >= n ? PARAM_POS_MAX : t->p[j - 1];
    return (2u * j * PARAM_POS_MAX + n) / (2u * n);
}

/* where the slices land: steps[k] gets slice k mod n. Returns how many steps. */
static u32 plan(const struct slicer *l, const struct cfw_pts *t, void *ctx, u32 n, u8 *steps)
{
    u32 len = track_len(ctx, l->track), m = 0;
    if (l->layout == LAY_TRIGS) {
        for (u32 s = 0; s < len; s++) {
            void *st = cfw_step_at(ctx, (int)l->track, (int)s);
            if (VFN(st, STEP_VT_IS_ON, u32)(st) & 0xff)
                steps[m++] = (u8)s;
        }
        return m;
    }
    s32 prev = -1;
    for (u32 k = 0; k < n; k++) {
        /* In time: the step that sits where the slice starts in the sample */
        s32 s = l->layout == LAY_STEPS ? (s32)k : (s32)((cut_pos(l, t, k, n) * 2u * len + 32767u) / (2u * 32767u));
        if (s <= prev)
            s = prev + 1;
        if (s >= (s32)len)
            break;
        steps[m++] = (u8)s;
        prev = s;
    }
    return m;
}

static void say(void *ctx, const char *msg)
{
    fw_toast_show((char *)PTR(ctrl_of(ctx), CTRL_UI) + UI_TOAST, msg);
}

/* idx: each slice's own sample (the folder), or 0: the track's sample with Start / End */
static void place(struct slicer *l, const struct cfw_pts *t, void *ctx, s16 sample, u32 n, const s16 *idx)
{
    u8 steps[NUM_STEPS];
    u32 m = plan(l, t, ctx, n, steps), len = track_len(ctx, l->track);
    void *ctrl = ctrl_of(ctx), *sel = (char *)ctrl + CTRL_SEL;
    u32 want[2] = {0, 0};
    for (u32 k = 0; k < m; k++)
        want[steps[k] >> 5] |= 1u << (steps[k] & 31);
    cfw_edit_begin(ctrl, "Slice");
    for (u32 s = 0; s < len; s++) { /* the trigs, as the pads do it */
        void *st = cfw_step_at(ctx, (int)l->track, (int)s);
        u32 on = VFN(st, STEP_VT_IS_ON, u32)(st) & 0xff, w = (want[s >> 5] >> (s & 31)) & 1u;
        if (on != w)
            fw_step_toggle(sel, (int)l->track, (int)s);
    }
    for (u32 k = 0; k < m; k++) {
        u32 j = k % n;
        void *st = cfw_step_at(ctx, (int)l->track, steps[k]);
        cfw_edit_lock();
        VFN(st, STEP_VT_SET_PARAM, void, int, int)(st, PARAM_SAMPLE, idx ? idx[j] : sample);
        VFN(st, STEP_VT_SET_PARAM, void, int, int)(st, PARAM_SAMPLE_START, idx ? 0 : (int)cut_pos(l, t, j, n));
        VFN(st, STEP_VT_SET_PARAM, void, int, int)(st, PARAM_SAMPLE_END, idx ? PARAM_POS_MAX : (int)cut_pos(l, t, j + 1, n));
        cfw_edit_unlock();
    }
    cfw_refresh_pads(ctrl);
    char msg[32], *p = cfw_put_uint(msg, m < n ? m : n);
    p = cfw_put_str(p, " slices on track ");
    p = cfw_put_uint(p, l->track + 1u);
    *p = 0;
    say(ctx, msg);
    l->applied = 1;
}

static void apply(struct slicer *l, void *ctx, int folder)
{
    struct cfw_pts *t = cfw_pts();
    struct sample smp;
    s16 sample;
    if (!t || !track_sample(l, ctx, &smp, &sample))
        return;
    u32 n = wanted(l, t);
    u8 steps[NUM_STEPS];
    if (!plan(l, t, ctx, n, steps)) {
        say(ctx, "No trigs on this track");
        return;
    }
    if (l->layout != LAY_TRIGS && n > track_len(ctx, l->track))
        n = track_len(ctx, l->track);
    if (folder) {
        s16 idx[NUM_STEPS];
        if (n > NUM_STEPS)
            n = NUM_STEPS;
        if (cfw_slices_to_folder(ctx, &smp, n, l->use_pts && t->count == n - 1 ? t->p : 0, idx))
            place(l, t, ctx, sample, n, idx);
        return;
    }
    place(l, t, ctx, sample, n, 0);
}

/* ---- the sample list ------------------------------------------------------------------------------ */
static int valid(void *ctx, s32 i)
{
    struct sample s;
    return i >= 0 && i < POOL_SLOTS && cfw_sample_get(ctx, (u32)i, &s);
}

static s32 step_valid(void *ctx, s32 i, s32 dir)
{
    for (s32 k = i + dir; k >= 0 && k < POOL_SLOTS; k += dir)
        if (valid(ctx, k))
            return k;
    return -1;
}

/* ---- input ---------------------------------------------------------------------------------------- */
static void set_track(struct slicer *l, u32 row)
{
    l->track = (u8)row;
    l->applied = 0;
    l->use_pts = 0;
}

int cfw_slicer_input(u32 kind, u32 id, u32 val)
{
    struct slicer *l = peek();
    struct cfw_pts *t = cfw_pts();
    struct cfw_view *v = &cfw_ram.view;
    void *ctx = l ? ctx_of(l) : 0;
    if (!l || !t || !ctx)
        return 0;
    if (kind == 0) { /* the pads: the select column picks the track, the steps only show the plan */
        u32 col = id % LED_COLS;
        if (col == SELECT_COL) {
            if (val == 0 && l->mode == MODE_MAIN) {
                void *mode = PTR(ctrl_of(ctx), CTRL_MODE);
                if (id / LED_COLS >= MOD_TRACKS || U8(mode, MODE_BANK))
                    say(ctx, "Sample tracks only");
                else
                    set_track(l, id / LED_COLS);
            }
            return 1;
        }
        return col < PAD_COLS;
    }
    if (kind == 2 || kind == 3)
        return 1; /* knobs would edit parameters under the menu */
    if (kind == 4) {
        s32 d = (s32)val;
        if (l->mode == MODE_PICK) {
            s32 i = l->pick;
            for (s32 k = d < 0 ? -d : d; k > 0; k--) {
                s32 j = step_valid(ctx, i, d < 0 ? -1 : 1);
                if (j < 0)
                    break;
                i = j;
            }
            l->pick = (s16)i;
        } else if (v->shift) {
            cfw_pts_zoom(t, d);
        } else if (l->mode == MODE_POINTS) {
            cfw_pts_move(t, d, cfw_pts_span(t) / 128 + 1);
        } else if (t->zoom) { /* zoomed in: the encoder scrolls */
            cfw_pts_move(t, d, cfw_pts_span(t) / 16 + 1);
        } else {
            s32 c = l->use_pts ? l->cnt : (s32)l->cnt + d;
            u32 max = l->layout == LAY_TRIGS ? NUM_STEPS : track_len(ctx, l->track);
            c = c < 0 ? 0 : c >= (s32)COUNTS ? (s32)COUNTS - 1 : c;
            while (c > 0 && counts[c] > max)
                c--;
            l->cnt = (u8)c;
            l->use_pts = 0;
            l->applied = 0;
        }
        return 1;
    }
    if (kind != 1)
        return 0;
    if (id == BTN_BACK && l->mode == MODE_MAIN)
        return 0; /* leaves, as stock */
    if (id != BTN_TOP && id != BTN_BOT && id != BTN_CLICK && id != BTN_BACK)
        return 0;
    if (val != 0)
        return 1;
    struct sample smp;
    s16 sample;
    int have = track_sample(l, ctx, &smp, &sample);
    if (l->mode == MODE_PICK) {
        if (id == BTN_CLICK && l->pick >= 0 && valid(ctx, l->pick)) {
            l->choice[l->track] = l->pick;
            l->applied = 0;
        }
        if (id == BTN_CLICK || id == BTN_BACK)
            l->mode = MODE_MAIN;
        return 1;
    }
    if (l->mode == MODE_POINTS) {
        if (id == BTN_CLICK) {
            cfw_pts_toggle(t);
        } else if (id == BTN_TOP) {
            if (v->shift)
                t->count = 0;
            else
                cfw_pts_delete_near(t);
        } else { /* Done or Back: the points are the slices now, if there are any */
            l->mode = MODE_MAIN;
            l->use_pts = t->count != 0;
            l->applied = 0;
        }
        return 1;
    }
    if (id == BTN_TOP) {
        if (v->shift || !have) { /* the sample list */
            s32 i = have ? sample : step_valid(ctx, -1, 1);
            l->pick = (s16)i;
            l->mode = MODE_PICK;
        } else {
            l->layout = (u8)((l->layout + 1u) % LAYOUTS);
            l->applied = 0;
            if (l->layout != LAY_TRIGS) {
                u32 max = track_len(ctx, l->track);
                while (l->cnt > 0 && counts[l->cnt] > max)
                    l->cnt--;
            }
        }
    } else if (id == BTN_BOT) {
        if (have) {
            if (l->source != sample) { /* points belong to one sample */
                t->count = 0;
                l->source = sample;
            }
            t->cursor = t->count ? t->cursor : 16384;
            cfw_pts_keep_visible(t);
            l->mode = MODE_POINTS;
        }
    } else if (id == BTN_CLICK && have) {
        apply(l, ctx, v->shift);
    }
    return 1;
}

/* ---- the pads: the select pad blinks (lfoui.c), the step pads show the plan --------------------------- */
const u8 *cfw_slicer_led(u32 led)
{
    struct slicer *l = peek();
    struct cfw_view *v = &cfw_ram.view;
    void *ctx = l ? ctx_of(l) : 0;
    u32 col = led % LED_COLS, row = led / LED_COLS;
    if (!ctx || col >= PAD_COLS || row != l->track || v->wmode != WMODE_SLICER)
        return 0;
    u32 s = col + U32(ctrl_of(ctx), CTRL_TRACK_PAGE + 4 * l->track) * 16u;
    return s < NUM_STEPS && (l->bits[s >> 5] >> (s & 31)) & 1u ? led_lit : led_off;
}

static void refresh_row(void *ctrl, u32 row)
{
    void *mode = PTR(ctrl, CTRL_MODE);
    if (U8(mode, MODE_BANK))
        return;
    for (u32 c = 0; c < PAD_COLS; c++)
        VFN(mode, MODE_VT_PAD_REFRESH, void, int, int)(mode, (int)row, (int)c);
}

/* the cave's menu update: this screen is up */
void cfw_slicer_enter(void *scr)
{
    struct slicer *l = state();
    if (!l)
        return;
    if (!l->scr || !l->lit) { /* a fresh open */
        struct cfw_pts *t = cfw_pts();
        u32 mask = U32((char *)PTR(PTR(scr, SCR_CTX), MENU_CTX_CTRL) + CTRL_SEL, 0x0c) & 0xffu;
        l->mode = MODE_MAIN;
        l->applied = 0;
        l->use_pts = 0;
        l->track = (u8)cfw_mod_track();
        for (u32 k = 0; k < MOD_TRACKS; k++)
            if (mask & (1u << k)) {
                l->track = (u8)k;
                break;
            }
        if (t) {
            t->zoom = 0;
            t->view0 = 0;
            t->cursor = 16384;
        }
        l->lit = 1;
    }
    l->scr = scr;
}

/* main loop */
void cfw_slicer_tick(void *app)
{
    struct slicer *l = peek();
    struct cfw_view *v = &cfw_ram.view;
    if (!l)
        return;
    void *ctrl = (char *)app + APP_CTRL;
    if (v->magic != VIEW_MAGIC || v->wmode != WMODE_SLICER || !cfw_view_alive(v)) {
        if (l->lit) {
            l->lit = 0;
            if (l->lit_row >= 0)
                refresh_row(ctrl, (u32)l->lit_row);
            l->lit_row = -1;
        }
        return;
    }
    void *ctx = ctx_of(l);
    struct cfw_pts *t = cfw_pts();
    if (!ctx || !t)
        return;
    cfw_lfo_close_if_left(l->scr);
    if (v->wmode != WMODE_SLICER)
        return;
    struct mod_ui *mu = cfw_mod_ui();
    if (mu)
        mu->focus = (u8)(1u << l->track); /* the select pad blinks */
    struct sample smp;
    s16 sample = -1;
    int have = track_sample(l, ctx, &smp, &sample);
    u8 steps[NUM_STEPS];
    u32 n = wanted(l, t), m = have ? plan(l, t, ctx, n, steps) : 0, b0 = 0, b1 = 0;
    for (u32 k = 0; k < m; k++) {
        if (steps[k] < 32)
            b0 |= 1u << steps[k];
        else
            b1 |= 1u << (steps[k] - 32);
    }
    l->bits[0] = b0;
    l->bits[1] = b1;
    u32 led_key = b0 ^ (b1 * 31u) ^ ((u32)l->track << 28);
    if (led_key != l->led_key || l->lit_row != (s8)l->track) {
        l->led_key = led_key;
        if (l->lit_row >= 0 && l->lit_row != (s8)l->track)
            refresh_row(ctrl, (u32)l->lit_row);
        l->lit_row = (s8)l->track;
        refresh_row(ctrl, l->track);
    }
    u32 key = led_key * 33u ^ (u32)l->mode ^ (u32)l->cnt << 8 ^ (u32)l->layout << 16 ^ (u32)l->use_pts << 20 ^
              (u32)l->applied << 21 ^ (u32)v->shift << 22 ^ (u32)(u16)sample << 4 ^ (u32)(u16)l->pick * 7u ^
              (u32)t->cursor * 0x9e3779b1u ^ (u32)t->view0 * 257u ^ (u32)t->zoom << 25 ^ (u32)t->count << 12;
    if (have)
        key ^= cfw_heads_key(&smp) * 0x01000193u;
    if (key != l->key && fw_millis - l->drawn_ms >= 33) {
        l->key = key;
        l->drawn_ms = fw_millis;
        VFN(v->disp, DISP_REFRESH, void, u32)(v->disp, (u32)v->slot);
    }
}

/* ---- drawing -------------------------------------------------------------------------------------- */
static void box(struct dlw *d, u32 color, int x0, int y0, int x1, int y1)
{
    dl(d, DL_COLOR_RGB(color));
    dl(d, DL_LINE_WIDTH(8));
    dl(d, DL_BEGIN(PRIM_RECTS));
    dl(d, DL_VERTEX2F(x0 * SUB + 8, y0 * SUB + 8));
    dl(d, DL_VERTEX2F(x1 * SUB - 8, y1 * SUB - 8));
    dl(d, DL_END);
}

/* the waveform with the cuts over it */
static void draw_wave(struct dlw *d, const struct slicer *l, const struct cfw_pts *t, const struct sample *smp, u32 n)
{
    u32 v0 = t->view0, sp = cfw_pts_span(t);
    u32 f0 = (u32)(((u64)v0 * smp->frames) >> 15), fs = (u32)(((u64)sp * smp->frames) >> 15);
    int cy = (WY0 + WY1) / 2, hh = (WY1 - WY0) / 2 - 2;
    /* every other slice on a darker band */
    for (u32 k = 0; k < n; k += 2) {
        u32 a = cut_pos(l, t, k, n), b = cut_pos(l, t, k + 1, n);
        if (b <= v0 || a >= v0 + sp)
            continue;
        a = a < v0 ? v0 : a;
        b = b > v0 + sp ? v0 + sp : b;
        box(d, C_BAND, WX + (int)((a - v0) * (u32)WW / sp), WY0, WX + (int)((b - v0) * (u32)WW / sp), WY1);
    }
    cfw_axis(d, WX, WW, cy);
    dl(d, DL_END);
    dl(d, DL_BEGIN(PRIM_LINES));
    cfw_draw_wave(d, smp, WX, WW, cy, hh, f0, fs ? fs : 1, WW);
    dl(d, DL_END);
    dl(d, DL_COLOR_RGB(C_CUT));
    dl(d, DL_LINE_WIDTH(8));
    dl(d, DL_BEGIN(PRIM_LINES));
    for (u32 k = 1; k < n; k++) {
        u32 a = cut_pos(l, t, k, n);
        if (a >= v0 && a < v0 + sp)
            vline(d, (WX + (int)((a - v0) * (u32)WW / sp)) * SUB + 8, WY0, WY1);
    }
    dl(d, DL_END);
    cfw_draw_heads(d, smp, WX, WW, WY0, WY1, v0, sp);
}

/* the track's steps, the ones that get a slice lit */
static void draw_strip_steps(struct dlw *d, const struct slicer *l, void *ctx)
{
    u32 len = track_len(ctx, l->track);
    for (u32 s = 0; s < len; s++) {
        int x0 = WX + (int)(s * (u32)WW / len), x1 = WX + (int)((s + 1) * (u32)WW / len);
        int on = (l->bits[s >> 5] >> (s & 31)) & 1u;
        box(d, on ? C_BLUE : s % 4 == 0 ? C_STEP4 : C_STEP, x0, STRIP_Y0, x1, STRIP_Y1);
    }
}

static void put_time(char *b, u32 pos, const struct sample *smp)
{
    u32 ms = smp->rate > 0.0f ? (u32)((float)(u32)(((u64)pos * smp->frames) >> 15) * 1000.0f / smp->rate) : 0;
    char *p = cfw_put_uint(b, ms / 1000);
    *p++ = '.';
    *p++ = (char)('0' + ms / 100 % 10);
    *p++ = (char)('0' + ms / 10 % 10);
    *p++ = (char)('0' + ms % 10);
    cfw_put_str(p, " s")[0] = 0;
}

int cfw_slicer_render(void *w)
{
    struct slicer *l = peek();
    struct cfw_view *v = &cfw_ram.view;
    struct cfw_pts *t = cfw_pts();
    struct dlw d = {0};
    void *ctx = l ? ctx_of(l) : 0;
    char b[40], *p;
    (void)w;
    fw_eve_begin();
    fw_eve_dlstart();
    dl(&d, DL_SAVE_CONTEXT);
    dl(&d, DL_VERTEX_FORMAT(4));
    box(&d, 0, 0, 24, 320, 216);
    if (!l || !t || !ctx)
        goto done;
    struct sample smp;
    s16 sample = -1;
    int have = track_sample(l, ctx, &smp, &sample);
    p = cfw_put_str(b, "Track ");
    p = cfw_put_uint(p, l->track + 1u);
    *p = 0;
    if (l->mode == MODE_PICK) {
        cfw_key_tile(&d, 0, "Sample", 1);
        cfw_key_tile(&d, 1, "", 0);
        s32 first = l->pick;
        for (u32 k = 0; k < 3 && first >= 0; k++) {
            s32 j = step_valid(ctx, first, -1);
            if (j < 0)
                break;
            first = j;
        }
        if (first < 0) {
            text(&d, 170, 96, C_WHITE, OPT_CENTERX, "No samples loaded");
            text(&d, 170, 120, C_DIM, OPT_CENTERX, "Samples > Load Files or Folders");
            goto done;
        }
        s32 i = first;
        for (u32 r = 0; r < PICK_ROWS && i >= 0; r++) {
            struct sample s;
            int y = 30 + (int)r * 26;
            if (cfw_sample_get(ctx, (u32)i, &s)) {
                if (i == l->pick)
                    box(&d, C_ROW, WX + 2, y, 312, y + 24);
                text(&d, WX + 10, y - 1, C_WHITE, 0, s.name);
            }
            i = step_valid(ctx, i, 1);
        }
        goto done;
    }
    if (!have) { /* nothing to slice yet: one thing to do */
        cfw_key_tile(&d, 0, "Sample", 1);
        cfw_key_tile(&d, 1, "Points", 0);
        text(&d, WX + 2, ROW1_Y, C_WHITE, 0, b);
        text_f(&d, 170, 84, FONT_LARGE, C_WHITE, OPT_CENTERX, "No sample yet");
        text(&d, 170, 116, C_DIM, OPT_CENTERX, "Press Sample to choose one");
        text(&d, 170, 140, C_DIM, OPT_CENTERX, "Or pick another track: select pads");
        goto done;
    }
    u32 n = wanted(l, t);
    /* the top row: the track, the sample */
    text(&d, WX + 2, ROW1_Y, C_WHITE, 0, b);
    text(&d, 312, ROW1_Y, C_DIM, OPT_RIGHTX, smp.name);
    draw_wave(&d, l, t, &smp, l->mode == MODE_POINTS ? t->count + 1u : n);
    if (l->mode == MODE_POINTS) {
        u32 sp = cfw_pts_span(t);
        cfw_key_tile(&d, 0, v->shift ? "Clear" : "Delete", t->count != 0);
        cfw_key_tile(&d, 1, "Done", 1);
        dl(&d, DL_COLOR_RGB(C_WHITE));
        dl(&d, DL_LINE_WIDTH(12));
        dl(&d, DL_BEGIN(PRIM_LINES));
        if (t->cursor >= t->view0 && t->cursor < t->view0 + sp)
            vline(&d, (WX + (int)((t->cursor - t->view0) * (u32)WW / sp)) * SUB + 8, WY0 - 4, WY1 + 4);
        dl(&d, DL_END);
        p = cfw_put_uint(b, t->count + 1u);
        cfw_put_str(p, t->count ? " slices" : " slice")[0] = 0;
        text_f(&d, WX + 2, 146, FONT_LARGE, C_WHITE, 0, b);
        put_time(b, t->cursor, &smp);
        text(&d, 312, 150, C_DIM, OPT_RIGHTX, b);
        text(&d, WX + 2, ROW2_Y, C_DIM, 0, "Turn: move   Click: add / remove");
        if (t->zoom) {
            p = cfw_put_str(b, "x");
            p = cfw_put_uint(p, 1u << t->zoom);
            *p = 0;
            text(&d, 312, ROW2_Y, C_DIM, OPT_RIGHTX, b);
        }
        goto done;
    }
    cfw_key_tile(&d, 0, v->shift ? "Sample" : "Layout", 1);
    cfw_key_tile(&d, 1, "Points", 1);
    draw_strip_steps(&d, l, ctx);
    u8 steps[NUM_STEPS];
    u32 m = plan(l, t, ctx, n, steps), shown = l->layout == LAY_TRIGS ? (m < n ? m : n) : m;
    p = cfw_put_uint(b, shown);
    p = cfw_put_str(p, shown == 1 ? " slice" : " slices");
    if (l->use_pts)
        p = cfw_put_str(p, " by hand");
    *p = 0;
    text_f(&d, WX + 2, 162, FONT_LARGE, C_WHITE, 0, b);
    text(&d, 312, 166, C_BLUE, OPT_RIGHTX, layout_name[l->layout]);
    {
        const char *hint = "Turn: slices   Click: apply";
        if (v->shift)
            hint = "Turn: zoom   Click: slices to a folder";
        else if (l->layout == LAY_TRIGS && !m)
            hint = "No trigs on this track";
        else if (l->applied)
            hint = "Applied. Press Play to hear it";
        else if (t->zoom)
            hint = "Turn: scroll   Click: apply";
        text(&d, WX + 2, ROW2_Y, l->applied && !v->shift ? C_WHITE : C_DIM, 0, hint);
    }
    if (t->zoom) {
        p = cfw_put_str(b, "x");
        p = cfw_put_uint(p, 1u << t->zoom);
        *p = 0;
        text(&d, 312, ROW2_Y, C_DIM, OPT_RIGHTX, b);
    }
done:
    dl(&d, DL_RESTORE_CONTEXT);
    fw_eve_end();
    return 0;
}
#endif
