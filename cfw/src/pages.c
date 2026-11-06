/* Knob-page visuals (CFW5): while the parameter screen shows one of
 * these pages (no menu open), a CFW panel replaces its six stock widgets:
 *   Master Volume / Limiter     L / R VU meter of the live output (segmented, peak hold), the limiter
 *                               threshold marked on it, and a 3 s level history in which the peaks
 *                               that reach the threshold are red
 *   Note / Microtune            two-octave keyboard with the note lit, microtune needle
 *   Volume / Panning            segmented level meter of the volume (dB scale), pan meter
 *   Filter Cutoff / Resonance   frequency response (low / high pass, resonance peak), log axis
 * The two parameter rows stay where the stock page has them (top / bottom, next to the two keys
 * left of the screen, which still pick the active parameter: bright = active, dim = other). Knobs,
 * keys and everything else work as stock; the panel only reads what the stock page shows:
 * the title and value texts of its widgets and the raw value its bars hold (widget +0x18).
 * The meter reads the audio DMA buffer (the final output, after master volume and limiter):
 * both halves of 128 interleaved int16 frames, from the main loop.
 * On the Play+ / Play RT this file is linked past the image end (ext); the cave only calls it after
 * cfw_ext_ok(). */
#include "slice.h"
#include "dlw.h"

#ifdef CFW_PAGES
extern void *fw_operator_new(u32 size);
extern volatile u32 fw_millis;
extern const volatile s16 fw_audio_out[]; /* audio DMA buffer (symbols.ld) */

#define PG_MAGIC 0x50414731u /* "PAG1" */
enum { PG_NONE, PG_MASTER, PG_NOTE, PG_VOLUME, PG_FILTER, PG_TEMPO, PG_FX, PG_DRIVE, PG_ENV };
/* Shift pickers: what the open list chooses */
enum { PK_NONE, PK_REVERB, PK_DELAY, PK_LIMITER, PK_SAT, PK_SCALE, PK_ROOT };
#define HIST 100
#define SHIFT_SHOW_MS 1000 /* after Shift + encoder the page itself shows this long, not the Shift layer */
#define PARAM_ROW 0x20     /* the parameter table (ctrl): row size, step per click */
#define PARAM_STEP 4
#define PARAM_ATTACK 13
#define PARAM_DECAY 14

struct pg {
    u32 magic;
    u8 page, active, hpos, pad;
    s16 lvl[2], hold[2]; /* meter, -10 x dB below full scale (0 .. -480) */
    u32 hold_ms[2], last_ms, drawn_ms, hist_ms;
    u32 key;
    s32 raw[2];          /* the stock bars' values */
    const char *title[2], *val[2];
    s16 thr;             /* limiter threshold, -10 x dB */
    u8 hist[HIST];       /* level history, -dB (0 = full scale .. 48) */
    void *colors;        /* folder colours' state (colors.c) */
    void *mod;           /* voice layer's state (mod.c) */
    void *cc;            /* CC presets' state (ccpreset.c) */
    void *slicer;        /* the Slice screen's state (slicer.c) */
    void *app;
    u8 pick, sel, orig;  /* the open Shift picker (PK_*), its highlight, the value to restore */
    u8 layer;            /* the Shift layer is what the page shows now */
    u8 env_sel;          /* Attack / Decay page, Shift held: 0 Sustain, 1 Release */
    u8 env_x;            /* its playhead, for the redraw */
    u8 mod_on[2];        /* an LFO moves this row's parameter on the current track */
    u32 enc_ms;          /* last Shift + encoder turn: the page itself shows for a moment */
};

#define C_TEXT 0xffffff
#define C_DIM 0x6a6a6a
#define C_GRID 0x262626
#define C_ACCENT 0xff5540
#define C_GREEN 0x2bd45a
#define C_YELLOW 0xffd23f
#define C_RED 0xff5540
#define C_GREEN_D 0x10331c
#define C_YELLOW_D 0x3d3412
#define C_RED_D 0x3d1712
#define C_CURVE 0xff9a2e
#define C_FILL 0x3a2410
#define PG_ROW1_Y 26
#define PG_ROW2_Y 196
#define VIS_Y0 46
#define VIS_Y1 188
#define FLOOR10 (-480) /* meter floor: -48 dB */

/* the heap state, if cfw_ram's pointer to it is sane (cfw_ram survives resets: never trust it blindly) */
static struct pg *pg_valid(void)
{
    struct pg *g = cfw_ram.pages;
    u32 a = (u32)g;
    int ram = (a >= 0x20000000u && a < 0x20080000u) || (a >= 0x20200000u && a < 0x20300000u) ||
              (a >= 0x80000000u && a < 0x82000000u);
    return ram && !(a & 3) && g->magic == PG_MAGIC ? g : 0;
}

static struct pg *pg(void)
{
    struct pg *g = pg_valid();
    if (!g) {
        g = fw_operator_new(sizeof *g);
        if (!g)
            return 0;
        for (u32 i = 0; i < sizeof *g; i++)
            ((volatile u8 *)g)[i] = 0;
        g->lvl[0] = g->lvl[1] = g->hold[0] = g->hold[1] = FLOOR10;
        for (u32 i = 0; i < HIST; i++)
            g->hist[i] = 48; /* empty history (0 would be full scale: a red block on first show) */
        g->magic = PG_MAGIC;
        cfw_ram.pages = g;
    }
    return g;
}

static int str_eq(const char *a, const char *b)
{
    while (*a && *a == *b)
        a++, b++;
    return *a == *b;
}

/* -10 x dB of a 16-bit peak (32767 = 0 dB), from log2 with a 16-step table */
static s32 db10(u32 p)
{
    static const u8 l2[16] = {0, 6, 11, 16, 21, 25, 29, 34, 37, 41, 45, 48, 52, 55, 58, 61}; /* 64 log2(1+k/16) */
    if (p < 4)
        return FLOOR10;
    s32 e = 31 - __builtin_clz(p);
    u32 f = e >= 4 ? (p >> (e - 4)) & 15 : (p << (4 - e)) & 15;
    s32 d = (e * 64 + l2[f] - 957) * 602 / 640;
    return d > 0 ? 0 : d < FLOOR10 ? FLOOR10 : d;
}

/* "-6 dB", "0.0 dB", "+3 dB" -> -10 x dB (0 if no number) */
static s32 parse_db10(const char *s)
{
    s32 sign = 1, v = 0, frac = 0, seen = 0;
    if (!cfw_ptr_ok(s))
        return 0;
    while (*s == ' ')
        s++;
    if (*s == '-' || *s == '+')
        sign = *s++ == '-' ? -1 : 1;
    while (*s >= '0' && *s <= '9')
        v = v * 10 + (*s++ - '0'), seen = 1;
    if (*s == '.' && s[1] >= '0' && s[1] <= '9')
        frac = s[1] - '0';
    return seen ? sign * (v * 10 + frac) : 0;
}

/* ---- the output meter (main loop): peak of the DMA buffer, fast attack, 24 dB/s release ---- */
static void meter(struct pg *g, u32 now)
{
    const volatile s16 *b = fw_audio_out;
    u32 pk[2] = {0, 0}, dt = now - g->last_ms;
    for (u32 i = 0; i < AUDIO_OUT_FRAMES * 2; i++) {
        s32 x = b[i];
        u32 a = (u32)(x < 0 ? -x : x);
        if (a > pk[i & 1])
            pk[i & 1] = a;
    }
    g->last_ms = now;
    if (dt > 200)
        dt = 200;
    for (u32 c = 0; c < 2; c++) {
        s32 d = db10(pk[c]), l = g->lvl[c] - (s32)(dt * 3 / 2); /* falls 15 dB/s */
        if (d > g->lvl[c])
            l = g->lvl[c] + (d - g->lvl[c] + 1) / 2; /* rises fast but eased: no jumping */
        g->lvl[c] = (s16)(l < FLOOR10 ? FLOOR10 : l > 0 ? 0 : l);
        if (g->lvl[c] >= g->hold[c]) {
            g->hold[c] = g->lvl[c];
            g->hold_ms[c] = now;
        } else if (now - g->hold_ms[c] > 1000) {
            s32 h = g->hold[c] - (s32)(dt * 12 / 5);
            g->hold[c] = (s16)(h < g->lvl[c] ? g->lvl[c] : h);
        }
    }
    if (now - g->hist_ms >= 30) { /* 100 x 30 ms = 3 s of history */
        g->hist_ms = now;
        s32 m = g->lvl[0] > g->lvl[1] ? g->lvl[0] : g->lvl[1];
        g->hist[g->hpos] = (u8)(-m / 10);
        g->hpos = (u8)((g->hpos + 1) % HIST);
    }
}

/* ---- panel lifecycle ------------------------------------------------------------------------- */
static void *wtab(void *disp) { return PTR(disp, DISP_TABLE); }

static void read_page(struct pg *g, void *pscr, void *disp)
{
    void **tab = wtab(disp);
    for (u32 k = 0; k < 2; k++) {
        u32 st = U32(pscr, PSCR_SLOTS + 4 * k), sv = U32(pscr, PSCR_SLOTS + 4 * (k + 2)),
            sb = U32(pscr, PSCR_SLOTS + 4 * (k + 4));
        void *wt = st < DISP_SLOTS ? tab[st] : 0, *wv = sv < DISP_SLOTS ? tab[sv] : 0, *wb = sb < DISP_SLOTS ? tab[sb] : 0;
        g->title[k] = wt && cfw_ptr_ok(PTR(wt, W_TEXT)) ? PTR(wt, W_TEXT) : "";
        g->val[k] = wv && cfw_ptr_ok(PTR(wv, W_TEXT)) ? PTR(wv, W_TEXT) : "";
        g->raw[k] = wb ? (s32)U32(wb, W_BAR_VALUE) : 0;
    }
    g->active = U8(pscr, PSCR_ACTIVE) ? 1 : 0;
}

static u32 hash_str(u32 h, const char *s)
{
    for (u32 i = 0; i < 24 && s[i]; i++)
        h = h * 31 + (u8)s[i];
    return h;
}

static void close_panel(struct cfw_view *v, void *pscr)
{
    if (cfw_view_alive(v)) {
        if (pscr)
            cfw_stock_widgets(pscr, v->disp, DISP_SHOW_SLOT);
        VFN(v->disp, DISP_FLUSH, void)(v->disp);
        VFN(v->disp, DISP_DESTROY, void, u32)(v->disp, (u32)v->slot);
    }
    v->slot = -1;
    v->widget = 0;
    v->wmode = 0;
}

static void fix_titles(struct pg *g, void *pscr, void *disp);

void cfw_pages_tick(void *app)
{
    struct cfw_view *v = cfw_view_state();
    struct pg *g = pg();
    void *ui = PTR((char *)app + APP_LISTENER, LISTENER_UI);
    if (!g || !ui)
        return;
    void *mscr = PTR(ui, UI_MENU_SCREEN), *pscr = cfw_param_screen(ui);
    void *disp = mscr ? PTR(mscr, SCR_DISP) : 0;
    const char *t = cfw_page_title(app);
    /* Sample Attack / Decay: stock moves 10 of 10000 per click, the other pages 100 */
    char *ptab = PTR((char *)app + APP_CTRL, CTRL_PARAM_TABLE);
    if (cfw_ptr_ok(ptab))
        for (u32 p = PARAM_ATTACK; p <= PARAM_DECAY; p++)
            if (U32(ptab, p * PARAM_ROW + PARAM_STEP) == 10)
                U32(ptab, p * PARAM_ROW + PARAM_STEP) = 100;
    g->layer = cfw_ram.view.shift && fw_millis - g->enc_ms >= SHIFT_SHOW_MS;
    int page = !t ? PG_NONE
               : cfw_str_is(t, "Master Volume") ? PG_MASTER
               : str_eq(t, "Note")              ? PG_NOTE
               : str_eq(t, "Volume")            ? PG_VOLUME
               : str_eq(t, "Tempo") && g->layer ? PG_TEMPO /* Shift held: its Shift layer */
               : cfw_str_is(t, "Filter Cutoff") ? PG_FILTER
               : cfw_str_is(t, "Reverb") && (g->layer || g->pick || cfw_mod_mapped(9) || cfw_mod_mapped(8)) ? PG_FX
               : cfw_str_is(t, "Overdrive") && (g->layer || g->pick || cfw_mod_mapped(6) || cfw_mod_mapped(7)) ? PG_DRIVE
               : cfw_str_is(t, "Sample Attack") ? PG_ENV
                                                : PG_NONE;
    g->app = app;
    if (!page)
        g->pick = PK_NONE;
    if (t && pscr && disp)
        fix_titles(g, pscr, disp);
    int mine = v->wmode == WMODE_PAGE && cfw_view_alive(v);
    u32 now = fw_millis;
    if (!page || !disp || !pscr) {
        if (mine)
            close_panel(v, pscr);
        g->page = PG_NONE;
        return;
    }
    if (!mine) {
        if (cfw_view_alive(v)) /* the Slice or Start / End panel */
            return;
        u32 desc[9];
        for (int i = 0; i < 5; i++)
            ((volatile u32 *)desc)[i] = 0;
        desc[5] = 0;
        desc[6] = 24;
        desc[7] = 320;
        desc[8] = 192;
        s32 slot = VFN(disp, DISP_CREATE, s32, u32, u32 *)(disp, WIDGET_TYPE_RECT, desc);
        if (slot < 0 || slot >= DISP_SLOTS)
            return;
        void *w = ((void **)wtab(disp))[slot];
        *(const void **)w = (const void *)cfw_wave_vt;
        v->slot = slot;
        v->widget = w;
        v->disp = disp;
        v->wmode = WMODE_PAGE;
        g->key = 0;
        VFN(disp, DISP_SHOW, void, u32)(disp, (u32)slot);
    }
    g->page = (u8)page;
    cfw_stock_widgets(pscr, disp, DISP_HIDE_SLOT); /* also when the stock code showed one again */
    read_page(g, pscr, disp);
#ifdef CFW_MOD
    { /* a parameter an LFO moves: the visual follows the value the sound has now */
        static const s8 mt[][2] = {{-1, -1}, {-1, -1}, {2, 3}, {0, 1}, {4, 5}, {-1, -1}, {9, 8}, {6, 7}, {12, 13}}; /* by PG_* */
        for (u32 k = 0; k < 2; k++) {
            s32 i = mt[page][k];
            g->mod_on[k] = i >= 0 && cfw_mod_mapped((u32)i);
            if (g->mod_on[k])
                g->raw[k] += i == 2 ? cfw_mod_live(2) / 100 : cfw_mod_live((u32)i); /* Note: cents -> notes */
        }
    }
#endif
    u32 key = (u32)page * 0x9e3779b9u ^ (u32)g->active ^ ((u32)g->raw[0] << 8) ^ ((u32)g->raw[1] << 20);
    key = hash_str(hash_str(hash_str(key, g->title[1]), g->val[0]), g->val[1]);
    key ^= (u32)g->layer << 30 ^ (u32)g->pick << 26 ^ (u32)g->sel << 12;
#ifdef CFW_MOD
    if (page == PG_ENV) {
        struct mod_save *ms = cfw_mod_save_data();
        u32 tr = cfw_mod_track();
        float pr = cfw_voice_progress();
        g->env_x = pr < 0.0f ? 255 : (u8)(pr * 254.0f);
        key ^= (u32)g->env_x * 0x01000193u ^ (u32)cfw_ram.view.shift << 29 ^ (u32)g->env_sel << 28 ^ tr << 22;
        if (ms)
            key ^= (u32)ms->sus[tr] * 0x10001u ^ (u32)ms->rel[tr] * 0x1000193u;
        if (now - g->drawn_ms < 33)
            return;
    }
#endif
    if (page == PG_MASTER) {
        meter(g, now);
        g->thr = (s16)parse_db10(g->val[1]);
        key ^= ((u32)(u16)g->lvl[0] * 7u) ^ ((u32)(u16)g->lvl[1] << 16) ^ ((u32)(u16)g->hold[0] << 3) ^
              (u32)g->hpos << 24;
        if (now - g->drawn_ms < 40)
            return; /* the meter redraws at most 30 times a second */
    }
    if ((g->mod_on[0] || g->mod_on[1]) && now - g->drawn_ms < 33)
        return; /* moving with an LFO: at most 30 times a second */
    if (key != g->key) {
        g->key = key;
        g->drawn_ms = now;
        VFN(disp, DISP_REFRESH, void, u32)(disp, (u32)v->slot);
    }
}

/* ---- drawing ---------------------------------------------------------------------------------- */
static void rows(struct dlw *d, const struct pg *g)
{
    for (u32 k = 0; k < 2; k++) {
        int y = k ? PG_ROW2_Y : PG_ROW1_Y;
        u32 c = g->active == k ? C_TEXT : C_DIM;
        text(d, 10, y, c, 0, g->title[k]);
        text_f(d, 310, y - 2, FONT_LARGE, c, OPT_RIGHTX, g->val[k]);
        if (g->mod_on[k])
            text(d, 200, y, C_CURVE, OPT_CENTERX, "LFO");
    }
}

/* Reverb / Delay, Overdrive / Bit Depth while an LFO moves one: the two values as bars */
static void draw_bars(struct dlw *d, const struct pg *g)
{
    for (u32 k = 0; k < 2; k++) {
        s32 lo = 0, hi = 10000;
        if (g->page == PG_DRIVE && k == 1) { /* Bit Depth */
            lo = 4;
            hi = 16;
        }
        s32 r = g->raw[k] < lo ? lo : g->raw[k] > hi ? hi : g->raw[k];
        int y = k ? 130 : 70, x = 10 + (int)((r - lo) * 300 / (hi - lo));
        dl(d, DL_LINE_WIDTH(SUB));
        dl(d, DL_BEGIN(PRIM_RECTS));
        dl(d, DL_COLOR_RGB(C_GRID));
        dl(d, DL_VERTEX2F(10 * SUB, y * SUB));
        dl(d, DL_VERTEX2F(310 * SUB, (y + 26) * SUB));
        dl(d, DL_COLOR_RGB(g->mod_on[k] ? C_CURVE : g->active == k ? C_TEXT : 0x8a8a8a));
        dl(d, DL_VERTEX2F(10 * SUB, y * SUB));
        dl(d, DL_VERTEX2F(x * SUB, (y + 26) * SUB));
        dl(d, DL_END);
    }
}

static u32 zone(s32 db10v, int lit) /* VU colours: green below -12 dB, yellow to -3 dB, red above */
{
    if (db10v < -120)
        return lit ? C_GREEN : C_GREEN_D;
    if (db10v < -30)
        return lit ? C_YELLOW : C_YELLOW_D;
    return lit ? C_RED : C_RED_D;
}

#define VU_X 26
#define VU_W 284
#define SEG 6
static int vu_x(s32 db10v) { return VU_X + (int)((db10v - FLOOR10) * VU_W / -FLOOR10); }

static void vu_bar(struct dlw *d, int y, int h, s32 lvl, s32 hold)
{
    dl(d, DL_LINE_WIDTH(SUB));
    dl(d, DL_BEGIN(PRIM_RECTS));
    for (int x = VU_X; x + SEG <= VU_X + VU_W; x += SEG) {
        s32 at = FLOOR10 + (s32)(x - VU_X + SEG / 2) * -FLOOR10 / VU_W;
        dl(d, DL_COLOR_RGB(zone(at, at <= lvl)));
        dl(d, DL_VERTEX2F((x + 1) * SUB, (y + 1) * SUB));
        dl(d, DL_VERTEX2F((x + SEG - 2) * SUB, (y + h - 1) * SUB));
    }
    dl(d, DL_END);
    if (hold > FLOOR10 + 10) {
        dl(d, DL_COLOR_RGB(C_TEXT));
        dl(d, DL_LINE_WIDTH(16));
        dl(d, DL_BEGIN(PRIM_LINES));
        vline(d, vu_x(hold) * SUB, y, y + h);
        dl(d, DL_END);
    }
}

static void draw_master(struct dlw *d, const struct pg *g)
{
    static const s16 marks[] = {-480, -360, -240, -120, -60, 0};
    static const char *const lab[] = {"-48", "-36", "-24", "-12", "-6", "0"};
    char buf[12], *p;
    text(d, 10, VIS_Y0 + 2, C_DIM, 0, "L");
    text(d, 10, VIS_Y0 + 24, C_DIM, 0, "R");
    vu_bar(d, VIS_Y0, 20, g->lvl[0], g->hold[0]);
    vu_bar(d, VIS_Y0 + 22, 20, g->lvl[1], g->hold[1]);
    for (u32 i = 0; i < 6; i++)
        text(d, vu_x(marks[i]) - (i == 5 ? 4 : 0), VIS_Y0 + 52, C_DIM, OPT_CENTERX, lab[i]);
    /* the Master Volume setting: a thin bar under the meter, -48 .. +12 dB, tick at 0 dB */
    s32 mv = parse_db10(g->val[0]), mx = VU_X + (int)((mv < -480 ? 0 : mv > 120 ? 600 : mv + 480) * VU_W / 600);
    dl(d, DL_LINE_WIDTH(SUB));
    dl(d, DL_BEGIN(PRIM_RECTS));
    dl(d, DL_COLOR_RGB(C_GRID));
    dl(d, DL_VERTEX2F(VU_X * SUB, (VIS_Y0 + 45) * SUB));
    dl(d, DL_VERTEX2F((VU_X + VU_W) * SUB, (VIS_Y0 + 49) * SUB));
    dl(d, DL_COLOR_RGB(g->active ? C_TEXT : 0x8a8a8a));
    dl(d, DL_VERTEX2F(VU_X * SUB, (VIS_Y0 + 45) * SUB));
    dl(d, DL_VERTEX2F(mx * SUB, (VIS_Y0 + 49) * SUB));
    dl(d, DL_COLOR_RGB(C_DIM));
    dl(d, DL_VERTEX2F((VU_X + VU_W * 4 / 5) * SUB, (VIS_Y0 + 43) * SUB));
    dl(d, DL_VERTEX2F((VU_X + VU_W * 4 / 5 + 1) * SUB, (VIS_Y0 + 51) * SUB));
    dl(d, DL_END);
    /* the limiter threshold across both bars */
    s32 thr = g->thr > 0 ? 0 : g->thr < FLOOR10 ? FLOOR10 : g->thr;
    int tx = vu_x(thr);
    dl(d, DL_COLOR_RGB(C_ACCENT));
    dl(d, DL_LINE_WIDTH(16));
    dl(d, DL_BEGIN(PRIM_LINES));
    vline(d, tx * SUB, VIS_Y0 - 3, VIS_Y0 + 45);
    dl(d, DL_END);
    /* level history: 3 s, newest right; red where it reached the threshold (being limited) */
    int hy0 = VIS_Y0 + 72, hy1 = VIS_Y1 - 2, hh = hy1 - hy0;
    int lim = (int)(-thr / 10);                        /* threshold as -dB */
    int ty = hy0 + lim * hh / 48;
    dl(d, DL_LINE_WIDTH(SUB));
    dl(d, DL_BEGIN(PRIM_RECTS));
    for (u32 i = 0; i < HIST; i++) {
        u32 v = g->hist[(g->hpos + i) % HIST];
        if (v >= 48)
            continue;
        int x = VU_X + (int)i * VU_W / HIST, top = hy0 + (int)v * hh / 48;
        dl(d, DL_COLOR_RGB((int)v <= lim ? C_RED : 0x9a9a9a));
        dl(d, DL_VERTEX2F(x * SUB, top * SUB));
        dl(d, DL_VERTEX2F((x + 2) * SUB, hy1 * SUB));
    }
    dl(d, DL_END);
    dl(d, DL_COLOR_RGB(C_ACCENT));
    dl(d, DL_LINE_WIDTH(8));
    dl(d, DL_BEGIN(PRIM_LINES));
    dl(d, DL_VERTEX2F(VU_X * SUB, ty * SUB));
    dl(d, DL_VERTEX2F((VU_X + VU_W) * SUB, ty * SUB));
    dl(d, DL_END);
    p = cfw_put_str(buf, lim ? "-" : "");
    p = cfw_put_uint(p, (u32)lim);
    p = cfw_put_str(p, " dB");
    *p = 0;
    dl(d, DL_COLOR_RGB(0));             /* the threshold, under its line, on black to stay readable */
    dl(d, DL_LINE_WIDTH(32));
    dl(d, DL_BEGIN(PRIM_RECTS));
    dl(d, DL_VERTEX2F((VU_X + 2) * SUB, (ty + 4) * SUB));
    dl(d, DL_VERTEX2F((VU_X + 4 + (int)slen(buf) * CHAR_W) * SUB, (ty + 16) * SUB));
    dl(d, DL_END);
    text(d, VU_X + 4, ty + 1, C_ACCENT, 0, buf);
    s32 m = g->lvl[0] > g->lvl[1] ? g->lvl[0] : g->lvl[1];
    if (m >= thr - 5 && m > FLOOR10 + 10)
        text(d, 310, hy0 - 2, C_ACCENT, OPT_RIGHTX, "LIMIT");
}

static void key_rect(struct dlw *d, u32 color, int x0, int y0, int x1, int y1)
{
    dl(d, DL_COLOR_RGB(color));
    dl(d, DL_VERTEX2F(x0 * SUB, y0 * SUB));
    dl(d, DL_VERTEX2F(x1 * SUB, y1 * SUB));
}

static void draw_note(struct dlw *d, const struct pg *g)
{
    static const u8 white[7] = {0, 2, 4, 5, 7, 9, 11};
    static const s8 black_after[7] = {1, 3, -1, 6, 8, 10, -1}; /* black key right of each white key */
    s32 note = g->raw[0] < 0 ? 0 : g->raw[0] > 119 ? 119 : g->raw[0];
    s32 oct = note / 12;
    if (oct > 8)
        oct = 8;
    int kx = 10, kw = 300 / 14, y0 = VIS_Y0 + 4, y1 = VIS_Y0 + 76, by1 = y0 + 46;
    char buf[4];
    dl(d, DL_LINE_WIDTH(SUB));
    dl(d, DL_BEGIN(PRIM_RECTS));
    for (int i = 0; i < 14; i++) {
        s32 n = (oct + i / 7) * 12 + white[i % 7];
        key_rect(d, n == note ? C_ACCENT : 0xd8d8d8, kx + i * kw + 1, y0, kx + (i + 1) * kw - 1, y1);
    }
    for (int i = 0; i < 14; i++) {
        s8 b = black_after[i % 7];
        if (b < 0)
            continue;
        s32 n = (oct + i / 7) * 12 + b;
        int cx = kx + (i + 1) * kw;
        key_rect(d, n == note ? C_ACCENT : 0x101010, cx - kw / 3, y0, cx + kw / 3, by1);
    }
    dl(d, DL_END);
    for (int k = 0; k < 2; k++) { /* octave labels under the C keys */
        buf[0] = 'C';
        buf[1] = (char)('0' + oct + k);
        buf[2] = 0;
        text(d, kx + k * 7 * kw + kw / 2, y1 + 2, C_DIM, OPT_CENTERX, buf);
    }
    /* microtune: needle on a +-100 % scale */
    int my = VIS_Y1 - 24, mx0 = 30, mw = 260, cx = mx0 + mw / 2;
    s32 mt = g->raw[1] < -10000 ? -10000 : g->raw[1] > 10000 ? 10000 : g->raw[1];
    dl(d, DL_COLOR_RGB(C_GRID));
    dl(d, DL_LINE_WIDTH(16));
    dl(d, DL_BEGIN(PRIM_LINES));
    dl(d, DL_VERTEX2F(mx0 * SUB, my * SUB));
    dl(d, DL_VERTEX2F((mx0 + mw) * SUB, my * SUB));
    for (int k = 0; k <= 4; k++)
        vline(d, (mx0 + k * mw / 4) * SUB, my - (k == 2 ? 8 : 4), my + (k == 2 ? 8 : 4));
    dl(d, DL_COLOR_RGB(g->active ? C_ACCENT : 0x9a9a9a));
    dl(d, DL_LINE_WIDTH(24));
    vline(d, (cx + (int)(mt * (mw / 2) / 10000)) * SUB, my - 12, my + 12);
    dl(d, DL_END);
    text(d, mx0 - 4, my - 7, C_DIM, OPT_RIGHTX, "-");
    text(d, mx0 + mw + 4, my - 7, C_DIM, 0, "+");
}

static void draw_volume(struct dlw *d, const struct pg *g)
{
    /* raw 0..10000, 7600 = 0 dB, 200 per dB (-38 .. +12 dB) */
    static const s16 marks[] = {1600, 3600, 5600, 7600, 8800, 10000};
    static const char *const lab[] = {"-30", "-20", "-10", "0", "+6", "+12"};
    s32 r = g->raw[0] < 0 ? 0 : g->raw[0] > 10000 ? 10000 : g->raw[0];
    int x0 = 10, w = 300, y0 = VIS_Y0 + 10, h = 26;
    dl(d, DL_LINE_WIDTH(SUB));
    dl(d, DL_BEGIN(PRIM_RECTS));
    for (int x = x0; x + SEG <= x0 + w; x += SEG) {
        s32 at = (s32)(x - x0 + SEG / 2) * 10000 / w;
        int lit = at <= r && r > 0;
        u32 c = at < 7600 ? (lit ? C_GREEN : C_GREEN_D) : at < 8800 ? (lit ? C_YELLOW : C_YELLOW_D)
                                                                     : (lit ? C_RED : C_RED_D);
        dl(d, DL_COLOR_RGB(g->active ? c : (lit ? 0x8a8a8a : 0x202020)));
        dl(d, DL_VERTEX2F((x + 1) * SUB, y0 * SUB));
        dl(d, DL_VERTEX2F((x + SEG - 2) * SUB, (y0 + h) * SUB));
    }
    dl(d, DL_END);
    for (u32 i = 0; i < 6; i++)
        text(d, x0 + (int)(marks[i] * w / 10000) - (i == 5 ? 10 : 0), y0 + h + 4, C_DIM, OPT_CENTERX, lab[i]);
    /* pan: bar from the centre, L .. R */
    s32 pn = g->raw[1] < -10000 ? -10000 : g->raw[1] > 10000 ? 10000 : g->raw[1];
    int py = VIS_Y1 - 30, cx = 160, pw = 130, px = cx + (int)(pn * pw / 10000);
    dl(d, DL_COLOR_RGB(C_GRID));
    dl(d, DL_LINE_WIDTH(SUB));
    dl(d, DL_BEGIN(PRIM_RECTS));
    dl(d, DL_VERTEX2F((cx - pw) * SUB, (py - 6) * SUB));
    dl(d, DL_VERTEX2F((cx + pw) * SUB, (py + 6) * SUB));
    dl(d, DL_COLOR_RGB(g->active ? C_TEXT : 0x8a8a8a));
    dl(d, DL_VERTEX2F((px < cx ? px : cx) * SUB, (py - 6) * SUB));
    dl(d, DL_VERTEX2F((px < cx ? cx : px + 1) * SUB, (py + 6) * SUB));
    dl(d, DL_END);
    dl(d, DL_COLOR_RGB(C_DIM));
    dl(d, DL_LINE_WIDTH(16));
    dl(d, DL_BEGIN(PRIM_LINES));
    vline(d, cx * SUB, py - 11, py + 11);
    dl(d, DL_END);
    text(d, cx - pw - 6, py - 7, C_DIM, OPT_RIGHTX, "L");
    text(d, cx + pw + 6, py - 7, C_DIM, 0, "R");
}

/* log2 for the filter curve (no libm): exponent + quadratic on the mantissa, ~0.01 off */
static float flog2(float x)
{
    union {
        float f;
        u32 i;
    } u = {x};
    float e = (float)((s32)((u.i >> 23) & 255) - 128);
    u.i = (u.i & 0x007fffffu) | 0x3f800000u;
    return e + (-0.33582878f * u.f + 2.0f) * u.f - 0.65871759f;
}

static void draw_filter(struct dlw *d, const struct pg *g)
{
    /* x: 20 Hz .. 20 kHz on 300 px (10 octaves, 30 px per octave); y: +15 .. -45 dB */
    static const s16 gxs[3] = {70, 169, 269};   /* 100 Hz, 1 kHz, 10 kHz: 2.3, 5.6, 8.97 octaves above 20 Hz */
    static const char *const fl[3] = {"100", "1k", "10k"};
    int x0 = 10, w = 300, y0 = VIS_Y0, y1 = VIS_Y1 - 14, zy = y0 + 30;
    float px_db = (float)(y1 - y0) / 60.0f;
    s32 c = g->raw[0] < -10000 ? -10000 : g->raw[0] > 10000 ? 10000 : g->raw[0];
    s32 res = g->raw[1] < 0 ? 0 : g->raw[1] > 10000 ? 10000 : g->raw[1];
    int hp = c > 0, xc = x0 + (int)(hp ? c * w / 10000 : w - (-c) * w / 10000);
    float q = 0.7071f + (float)res * 0.0004f; /* resonance 0..100 % -> Q 0.7 .. 4.7 */
    for (int i = 0; i < 3; i++) {             /* grid */
        int gx = x0 + gxs[i];
        dl(d, DL_COLOR_RGB(C_GRID));
        dl(d, DL_LINE_WIDTH(8));
        dl(d, DL_BEGIN(PRIM_LINES));
        vline(d, gx * SUB, y0, y1);
        dl(d, DL_END);
        text(d, gx, y1 + 1, C_DIM, OPT_CENTERX, fl[i]);
    }
    dl(d, DL_COLOR_RGB(C_GRID));
    dl(d, DL_BEGIN(PRIM_LINES));
    dl(d, DL_VERTEX2F(x0 * SUB, zy * SUB));
    dl(d, DL_VERTEX2F((x0 + w) * SUB, zy * SUB));
    dl(d, DL_END);
    /* the response, point per 2 px: |H|^2 of a 2-pole low / high pass at r = f / fc */
    s32 ys[151];
    float m2 = 1.0470929f; /* 2^(2/30): two pixels */
    float r = 1.0f;
    for (int k = (xc - x0) / 2; k > 0; k--)
        r /= m2;
    for (int k = 0; k <= 150; k++) {
        float yv = (float)zy;
        if (c != 0) {
            float r2 = r * r, a = 1.0f - r2, h2 = a * a + r2 / (q * q);
            float g2 = (hp ? r2 * r2 : 1.0f) / h2;
            yv = (float)zy - 3.0103f * flog2(g2 > 1e-9f ? g2 : 1e-9f) * px_db;
        }
        if (yv < (float)y0)
            yv = (float)y0;
        if (yv > (float)y1)
            yv = (float)y1;
        ys[k] = (s32)(yv * SUB);
        r *= m2;
    }
    dl(d, DL_SAVE_CONTEXT);
    dl(d, 0x1b000000u | ((u32)x0 << 11) | (u32)y0);            /* SCISSOR_XY */
    dl(d, 0x1c000000u | ((u32)w << 12) | (u32)(y1 - y0));      /* SCISSOR_SIZE */
    dl(d, DL_COLOR_RGB(g->active ? C_FILL : 0x1c1c1c));
    dl(d, DL_BEGIN(8));                                        /* EDGE_STRIP_B: fill below the curve */
    for (int k = 0; k <= 150; k++)
        dl(d, DL_VERTEX2F((x0 + 2 * k) * SUB, ys[k]));
    dl(d, DL_END);
    dl(d, DL_RESTORE_CONTEXT);
    dl(d, DL_COLOR_RGB(g->active ? C_CURVE : 0xb0b0b0));
    dl(d, DL_LINE_WIDTH(24));
    dl(d, DL_BEGIN(4));                                        /* LINE_STRIP */
    for (int k = 0; k <= 150; k++)
        dl(d, DL_VERTEX2F((x0 + 2 * k) * SUB, ys[k]));
    dl(d, DL_END);
    if (c != 0) { /* the cutoff */
        dl(d, DL_COLOR_RGB(C_TEXT));
        dl(d, DL_POINT_SIZE(64));
        dl(d, DL_BEGIN(PRIM_POINTS));
        dl(d, DL_VERTEX2F(xc * SUB, ys[(xc - x0) / 2 > 150 ? 150 : (xc - x0) / 2]));
        dl(d, DL_END);
    }
}

/* the folder colours' state pointer, inside this heap state (alloc: create the state if missing) */
void **cfw_pages_colors(int alloc)
{
    struct pg *g = alloc ? pg() : pg_valid();
    return g ? &g->colors : 0;
}

#ifdef CFW_MOD
void **cfw_pages_slicer(int alloc)
{
    struct pg *g = alloc ? pg() : pg_valid();
    return g ? &g->slicer : 0;
}

void **cfw_pages_cc(int alloc)
{
    struct pg *g = alloc ? pg() : pg_valid();
    return g ? &g->cc : 0;
}

void **cfw_pages_mod(int alloc)
{
    struct pg *g = alloc ? pg() : pg_valid();
    return g ? &g->mod : 0;
}
#endif

/* Tempo page while Shift is held: the top key taps the tempo (tap.c) */
static void draw_tempo(struct dlw *d, const struct pg *g)
{
    cfw_key_tile(d, 0, "Tap", 1);
    cfw_key_tile(d, 1, "", 0);
    text(d, TILE_TEXT_X + 2, 26, C_TEXT, 0, "Tap Tempo");
    text_f(d, 310, 24, FONT_LARGE, C_TEXT, OPT_RIGHTX, g->val[0]);
    text(d, TILE_TEXT_X + 2, 90, C_DIM, 0, "Tap the top key in time:");
    text(d, TILE_TEXT_X + 2, 108, C_DIM, 0, "the tempo follows as you play");
}

/* ---- Shift layer and pickers ------------------------------------------------------------------
 * Holding Shift on a page shows its Shift functions on the two key tiles with the current choice
 * next to each; Shift + a key opens a list: encoder = choice (applied at once, so it is heard),
 * encoder click or the key again = keep, Back = the choice before. Presets go through the stock
 * apply function (the one the firmware's own preset rows call); the project is marked changed. */
extern void fw_fx_preset(void *fx, u32 type, u32 index);   /* type 0 reverb, 1 delay, 2 limiter */
extern void fw_fx_saturation(void *fx, u32 type);
extern const u32 fw_delay_presets[], fw_reverb_presets[], fw_limiter_presets[]; /* {params *, name *} */
extern const char *const fw_sat_names[], *const fw_scale_names[];
static const char *const note_names[12] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};

static void *project(const struct pg *g) { return PTR((char *)g->app + APP_PATMGR, PM_PROJECT); }

static u32 pk_count(u32 k)
{
    return k == PK_REVERB ? 11 : k == PK_DELAY ? 20 : k == PK_LIMITER ? 18 : k == PK_SAT ? 5 : k == PK_SCALE ? 39 : 12;
}

static const char *pk_name(u32 k, u32 i)
{
    if (k == PK_REVERB)
        return (const char *)fw_reverb_presets[2 * i + 1];
    if (k == PK_DELAY)
        return (const char *)fw_delay_presets[2 * i + 1];
    if (k == PK_LIMITER)
        return (const char *)fw_limiter_presets[2 * i + 1];
    if (k == PK_SAT)
        return fw_sat_names[i];
    if (k == PK_SCALE)
        return fw_scale_names[i];
    return note_names[i % 12];
}

static const char *pk_title(u32 k)
{
    return k == PK_REVERB ? "Reverb" : k == PK_DELAY ? "Delay" : k == PK_LIMITER ? "Limiter" : k == PK_SAT ? "Saturation"
         : k == PK_SCALE ? "Scale" : "Root";
}

static u32 pk_get(const struct pg *g, u32 k)
{
    void *pr = project(g);
    u32 v = k == PK_REVERB ? U32(pr, 0x50) : k == PK_DELAY ? U32(pr, 0x4c) : k == PK_LIMITER ? U32(pr, 0xd8)
          : k == PK_SAT ? U8(pr, 0x1fc) : k == PK_SCALE ? U32(pr, 0x1e4) : U32(pr, 0x1e8);
    return v < pk_count(k) ? v : 0;
}

static void pk_set(const struct pg *g, u32 k, u32 v)
{
    void *pr = project(g), *ctx = (char *)g->app + APP_MENU_CTX, *fx = PTR(ctx, MENU_CTX_FX);
    if (!cfw_ptr_ok(pr) || !cfw_ptr_ok(fx))
        return;
    if (k == PK_REVERB || k == PK_DELAY || k == PK_LIMITER) {
        U32(pr, k == PK_REVERB ? 0x50 : k == PK_DELAY ? 0x4c : 0xd8) = v;
        fw_fx_preset(fx, k == PK_REVERB ? 0 : k == PK_DELAY ? 1 : 2, v);
    } else if (k == PK_SAT) {
        U8(pr, 0x1fc) = (u8)v;
        fw_fx_saturation(fx, v);
    } else if (k == PK_SCALE) {
        U32(pr, 0x1e4) = v;
        U8(pr, 0x1f8) = v != 0; /* the scale filter: on for any scale but Chromatic */
    } else {
        U32(pr, 0x1e8) = v;
    }
    void *ctrl = (char *)g->app + APP_CTRL;
    U8(PTR(ctrl, CTRL_DIRTY_A), DIRTY_A_OFF) = 1;
}

/* The stock page titles name the current preset ("Reverb (Big Room)") but are only formatted when
 * the page is built, so after a pick the title would show the old one until the page is left. Each
 * title "<Reverb | Delay | Limiter | Overdrive> (<name>)" whose name is an entry of that list but not
 * the current one gets the current name (the text buffer is the stock 33-byte one). */
static int pk_from_title(const char *t, u32 *plen)
{
    static const char *const pre[4] = {"Reverb (", "Delay (", "Limiter (", "Overdrive ("};
    static const u8 kind[4] = {PK_REVERB, PK_DELAY, PK_LIMITER, PK_SAT};
    for (u32 i = 0; i < 4; i++) {
        u32 n = 0;
        while (pre[i][n] && t[n] == pre[i][n])
            n++;
        if (!pre[i][n]) {
            *plen = n;
            return kind[i];
        }
    }
    return PK_NONE;
}

static void fix_titles(struct pg *g, void *pscr, void *disp)
{
    void **tab = wtab(disp);
    void *pr = project(g);
    if (!cfw_ptr_ok(pr))
        return;
    for (u32 k = 0; k < 2; k++) {
        u32 st = U32(pscr, PSCR_SLOTS + 4 * k);
        void *w = st < DISP_SLOTS ? tab[st] : 0;
        char *t = w && cfw_ptr_ok(PTR(w, W_TEXT)) ? PTR(w, W_TEXT) : 0;
        u32 n;
        int pk = t ? pk_from_title(t, &n) : PK_NONE;
        if (!pk)
            continue;
        u32 e = n; /* the name in the parentheses: t[n .. e) */
        while (e < 31 && t[e] && t[e] != ')')
            e++;
        if (t[e] != ')' || t[e + 1])
            continue;
        const char *cur = pk_name((u32)pk, pk_get(g, (u32)pk));
        u32 i, len = e - n, known = 0;
        for (i = 0; i < pk_count((u32)pk) && !known; i++) {
            const char *nm = pk_name((u32)pk, i);
            u32 j = 0;
            while (j < len && nm[j] == t[n + j])
                j++;
            known = j == len && !nm[j];
        }
        u32 cl = 0;
        while (cur[cl])
            cl++;
        if (!known || n + cl + 1 > 32)
            continue;
        u32 same = cl == len;
        for (i = 0; same && i < len; i++)
            same = cur[i] == t[n + i];
        if (same)
            continue;
        for (i = 0; i < cl; i++)
            t[n + i] = cur[i];
        t[n + cl] = ')';
        t[n + cl + 1] = 0;
        VFN(disp, DISP_REFRESH, void, u32)(disp, st);
    }
}

/* the page's Shift functions: top / bottom key */
static u32 shift_top(u32 page) { return page == PG_FX ? PK_REVERB : page == PG_DRIVE ? PK_SAT : page == PG_NOTE ? PK_SCALE : PK_NONE; }
static u32 shift_bot(u32 page) { return page == PG_FX ? PK_DELAY : page == PG_MASTER ? PK_LIMITER : page == PG_NOTE ? PK_ROOT : PK_NONE; }

/* Shift + encoder moves further, everywhere. On a knob page stock's Shift means fine adjustment on
 * some parameters (it would undo the speed-up), so the event goes on without its Shift flag. The page
 * itself shows for a moment, so the value that moved can be seen. */
extern void fw_in_encoder(void *l, u32 a, u32 b, u32 c);
#define LISTENER_SHIFT 0x18

static s32 shift_mult(void *app, void *pscr)
{
    u32 knob = U32((char *)app + APP_CTRL, CTRL_KNOB), bottom = U8(pscr, PSCR_ACTIVE) ? 1 : 0;
    if (knob == 1 && !bottom)
        return 12; /* Note: full octaves */
    /* short lists: Folder, Chance / Action, Bit Depth, Randomize, Track Length / Play Mode, Repeat, Move */
    if ((knob == 2 && bottom) || (knob == 8 && bottom) || knob == 4 || knob == 9 || knob == 10 || knob == 13 || knob == 14)
        return 4;
    return 10;
}

void cfw_encoder_shift(void *l, u32 a, u32 b, u32 c)
{
    void *app = (char *)l - APP_LISTENER, *ui = PTR(l, LISTENER_UI);
    void *pscr = cfw_ptr_ok(ui) ? cfw_param_screen(ui) : 0;
    struct pg *g = pg_valid();
    if (!pscr) { /* menus and lists */
        fw_in_encoder(l, a, (u32)((s32)b * 5), c);
        return;
    }
    if (g)
        g->enc_ms = fw_millis;
    u8 flag = U8(l, LISTENER_SHIFT);
    U8(l, LISTENER_SHIFT) = 0;
    fw_in_encoder(l, a, (u32)((s32)b * shift_mult(app, pscr)), c);
    U8(l, LISTENER_SHIFT) = flag;
}

int cfw_pages_input(u32 kind, u32 id, u32 val)
{
    struct cfw_view *v = &cfw_ram.view;
    struct pg *g = pg_valid();
    if (!g || !g->app || v->magic != VIEW_MAGIC || v->wmode != WMODE_PAGE || !cfw_view_alive(v))
        return 0;
#ifdef CFW_MOD
    if (g->page == PG_ENV && v->shift) { /* Sustain and Release, on the keys, the encoder and knob 12 */
        struct mod_save *ms = cfw_mod_save_data();
        if (!ms)
            return 0;
        if (kind == 1 && (id == 0 || id == 3)) {
            if (val == 0)
                g->env_sel = id == 3;
            return 1;
        }
        if (kind == 4 || (kind == 2 && id == 12)) {
            u8 *p = (g->env_sel ? ms->rel : ms->sus) + cfw_mod_track();
            s32 n = (s32)*p + (s32)val;
            *p = (u8)(n < 0 ? 0 : n > 100 ? 100 : n);
            cfw_mod_touch();
            return 1;
        }
        return 0;
    }
#endif
    if (!g->pick) {
        if (kind != 1 || (id != 0 && id != 3) || !v->shift)
            return 0;
        u32 k = id == 0 ? shift_top(g->page) : shift_bot(g->page);
        if (!k)
            return 0;
        if (val == 0) {
            g->pick = (u8)k;
            g->orig = g->sel = (u8)pk_get(g, k);
            VFN(v->disp, DISP_REFRESH, void, u32)(v->disp, (u32)v->slot);
        }
        return 1;
    }
    if (kind == 4) {
        s32 n = (s32)pk_count(g->pick), s = (s32)g->sel + (s32)val;
        g->sel = (u8)(s < 0 ? 0 : s >= n ? n - 1 : s);
        pk_set(g, g->pick, g->sel); /* heard at once */
        VFN(v->disp, DISP_REFRESH, void, u32)(v->disp, (u32)v->slot);
        return 1;
    }
    if (kind != 1)
        return kind == 2; /* knobs: ignored while the list is open */
    if (id != 0 && id != 3 && id != 6 && id != 7)
        return 0;
    if (val != 0)
        return 1;
    if (id == 7)
        pk_set(g, g->pick, g->orig);
    g->pick = PK_NONE;
    VFN(v->disp, DISP_REFRESH, void, u32)(v->disp, (u32)v->slot);
    return 1;
}

#define PICK_ROWS 7
#define PICK_ROW_H 26
#define PICK_Y0 30
#define C_ROW_SEL 0x111111 /* the stock menus' highlight */

static void draw_shift(struct dlw *d, const struct pg *g)
{
    u32 kt = shift_top(g->page), kb = shift_bot(g->page);
    /* with a list open only its key's tile stays lit: that tile names the list */
    cfw_key_tile(d, 0, kt ? pk_title(kt) : "", kt != 0 && (!g->pick || g->pick == kt));
    cfw_key_tile(d, 1, kb ? pk_title(kb) : "", kb != 0 && (!g->pick || g->pick == kb));
    if (g->pick) { /* the list, drawn like the stock menus: 26 px rows, a 24 px rounded highlight */
        u32 n = pk_count(g->pick), first = g->sel > 3 ? g->sel - 3 : 0;
        if (first + PICK_ROWS > n)
            first = n > PICK_ROWS ? n - PICK_ROWS : 0;
        for (u32 r = 0; r < PICK_ROWS && first + r < n; r++) {
            int y = PICK_Y0 + (int)r * PICK_ROW_H;
            if (first + r == g->sel) {
                dl(d, DL_COLOR_RGB(C_ROW_SEL));
                dl(d, DL_LINE_WIDTH(4 * SUB)); /* the corner radius: it also grows the box by 4 */
                dl(d, DL_BEGIN(PRIM_RECTS));
                dl(d, DL_VERTEX2F((TILE_TEXT_X + 4) * SUB, (y + 4) * SUB));
                dl(d, DL_VERTEX2F(308 * SUB, (y + 20) * SUB));
                dl(d, DL_END);
            }
            text(d, TILE_TEXT_X + 10, y - 1, C_TEXT, 0, pk_name(g->pick, first + r));
        }
        return;
    }
    /* the Shift layer: each key's function and its current choice */
    for (u32 k = 0; k < 2; k++) {
        u32 f = k ? kb : kt;
        int y = k ? 150 : 40;
        if (!f)
            continue;
        text(d, TILE_TEXT_X + 2, y, C_DIM, 0, pk_title(f));
        text_f(d, TILE_TEXT_X + 2, y + 18, FONT_LARGE, C_TEXT, 0, pk_name(f, pk_get(g, f)));
    }
    text(d, 310, 104, C_DIM, OPT_RIGHTX, "Shift + key: choose");
}

/* Sample Attack (fade in) / Decay (fade out) over the sample, with Sustain and Release: the envelope as
 * the audio path applies it (cfw_env_gain), and a playhead on the current track's voice. Holding Shift
 * only changes the two rows of text. */
#define ENV_STEPS 70
static void draw_env(struct dlw *d, const struct pg *g)
{
    float a = (float)(g->raw[0] < 0 ? 0 : g->raw[0] > 10000 ? 10000 : g->raw[0]) / 10000.0f;
    float dc = (float)(g->raw[1] < 0 ? 0 : g->raw[1] > 10000 ? 10000 : g->raw[1]) / 10000.0f;
    float su = 0.0f, re = 0.0f;
    int x0 = 20, w = 280, y0 = VIS_Y0 + 4, y1 = VIS_Y1 - 16, h = y1 - y0;
    u32 shift = cfw_ram.view.shift;
    char buf[24], *p;
#ifdef CFW_MOD
    struct mod_save *ms = cfw_mod_save_data();
    u32 tr = cfw_mod_track();
    if (ms) {
        su = (float)ms->sus[tr] / 100.0f;
        re = (float)ms->rel[tr] / 100.0f;
    }
    if (shift) {
        for (u32 k = 0; k < 2; k++) {
            u32 pct = ms ? (k ? ms->rel[tr] : ms->sus[tr]) : 0, c = g->env_sel == k ? C_TEXT : C_DIM;
            int y = k ? PG_ROW2_Y : PG_ROW1_Y;
            p = cfw_put_str(buf, k ? "Release, track " : "Sustain, track ");
            p = cfw_put_uint(p, tr + 1);
            *p = 0;
            text(d, 10, y, c, 0, buf);
            if (!k && !pct) {
                cfw_put_str(buf, "Off")[0] = 0;
            } else {
                p = cfw_put_uint(buf, pct);
                cfw_put_str(p, " %")[0] = 0;
            }
            text_f(d, 310, y - 2, FONT_LARGE, c, OPT_RIGHTX, buf);
        }
    } else
#endif
    {
        rows(d, g);
    }
    for (int pass = 0; pass < 2; pass++) {
        if (!pass) {
            dl(d, DL_SAVE_CONTEXT);
            dl(d, 0x1b000000u | ((u32)x0 << 11) | (u32)y0);
            dl(d, 0x1c000000u | ((u32)w << 12) | (u32)(y1 - y0));
            dl(d, DL_COLOR_RGB(0x1c1c1c));
            dl(d, DL_BEGIN(8));
        } else {
            dl(d, DL_COLOR_RGB(C_TEXT));
            dl(d, DL_LINE_WIDTH(24));
            dl(d, DL_BEGIN(4));
        }
        for (int i = 0; i <= ENV_STEPS; i++) {
            float t = (float)i / (float)ENV_STEPS;
            dl(d, DL_VERTEX2F((x0 + (int)(t * (float)w)) * SUB, (y1 - (int)(cfw_env_gain(t, a, dc, su, re) * (float)h)) * SUB));
        }
        dl(d, DL_END);
        if (!pass)
            dl(d, DL_RESTORE_CONTEXT);
    }
    if (g->env_x != 255) { /* the voice, moving through the envelope */
        float t = (float)g->env_x / 254.0f;
        int px = x0 + (int)(t * (float)w), py = y1 - (int)(cfw_env_gain(t, a, dc, su, re) * (float)h);
        dl(d, DL_COLOR_RGB(0xffb020));
        dl(d, DL_LINE_WIDTH(14));
        dl(d, DL_BEGIN(PRIM_LINES));
        vline(d, px * SUB, y0, y1);
        dl(d, DL_END);
        dl(d, DL_POINT_SIZE(64));
        dl(d, DL_BEGIN(PRIM_POINTS));
        dl(d, DL_VERTEX2F(px * SUB, py * SUB));
        dl(d, DL_END);
    }
}

int cfw_pages_render(void *w)
{
    struct pg *g = pg_valid();
    struct dlw d = {0};
    (void)w;
    fw_eve_begin();
    fw_eve_dlstart();
    dl(&d, DL_SAVE_CONTEXT);
    dl(&d, DL_VERTEX_FORMAT(4));
    dl(&d, DL_COLOR_RGB(0)); /* cover the stock page */
    dl(&d, DL_BEGIN(PRIM_RECTS));
    dl(&d, DL_VERTEX2F(0, 24 * SUB));
    dl(&d, DL_VERTEX2F(320 * SUB, 216 * SUB));
    dl(&d, DL_END);
    if (g && (shift_top(g->page) || shift_bot(g->page)) && (g->layer || g->pick)) {
        draw_shift(&d, g); /* the page's Shift layer / its list */
    } else if (g && g->page == PG_TEMPO) {
        draw_tempo(&d, g);
    } else if (g) {
        if (g->page != PG_ENV)
            rows(&d, g);
        if (g->page == PG_MASTER)
            draw_master(&d, g);
        else if (g->page == PG_NOTE)
            draw_note(&d, g);
        else if (g->page == PG_VOLUME)
            draw_volume(&d, g);
        else if (g->page == PG_FILTER)
            draw_filter(&d, g);
        else if (g->page == PG_ENV)
            draw_env(&d, g);
        else if (g->page == PG_FX || g->page == PG_DRIVE)
            draw_bars(&d, g);
    }
    dl(&d, DL_RESTORE_CONTEXT);
    fw_eve_end();
    return 0;
}
#endif
