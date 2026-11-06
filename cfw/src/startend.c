/* Sample Start / End editor on the "Sample Start / Sample End" knob page (Backstage / Reddit request).
 *
 * While the parameter screen shows that page (its title widget reads "Sample Start", no menu
 * open), a CFW panel covers it: the sample of the step (held) or track the page shows, a window
 * [view0, view0 + span) of it with the part between Start and End bright, a green line at Start,
 * a red one at End and a grey playhead (cursor). The two keys left of the screen (which normally
 * pick the page's top / bottom parameter) set them; white key tiles label them (top "Start" with
 * the value in green, bottom "End" with the value in red). Controls while it is up:
 *   encoder           move the playhead          Shift + encoder   zoom (x1 .. x128)
 *   top-left key      Start = playhead           bottom-left key   End = playhead
 *   knob 7            as stock (edits Start / End; the panel follows); everything else as stock
 * Setting Start / End is a normal edit (undo entry "Start/End", project marked changed).
 *
 * Which step / track: the stock value formatter of these two parameters (descriptor vtable slot 0,
 * FUN_600da3dc, RT 0x60050964) gets that object; the cave wraps it (cfw_fmt_startend) and keeps
 * the pointer, used only after a sanity check of its vtable. The sample pool comes from the app
 * (pattern manager -> project), never from the menu (its context is 0 until a menu was opened:
 * the cause of the first build's crash on hardware).
 * On the Play+ / Play RT this file is linked past the image end ("ext"); the cave only calls it
 * after cfw_ext_ok(). */
#include "slice.h"
#include "dlw.h"

#ifdef CFW_STARTEND
#define SE_X 0
#define SE_Y 24
#define SE_W 320
#define SE_H 192
#define C_TEXT 0xffffff
#define C_START 0x00cc66
#define C_DIM 0x8c8c8c
#define C_END 0xff5540
#define C_HEAD 0x6a6a6a
#define BTN_LTOP 0 /* the keys left of the screen: top / bottom */
#define BTN_LBOT 3

/* a step / track object as the formatter passed it: in RAM, with a vtable whose getParam is code */
static int obj_ok(void *obj)
{
    u32 o = (u32)obj, vt, fn;
    if ((o & 3) || !((o >= 0x20000000u && o < 0x20080000u) || (o >= 0x20200000u && o < 0x20300000u) ||
                     (o >= 0x80000000u && o < 0x82000000u)))
        return 0;
    vt = U32(obj, 0);
    if ((vt & 3) || !((vt >= 0x20000000u && vt < 0x20080000u) || (vt >= 0x60000000u && vt < 0x61000000u)))
        return 0;
    fn = U32((void *)vt, STEP_VT_GET_PARAM);
    return (fn & 1) && fn >= 0x60000000u && fn < 0x61000000u;
}

static int getp(void *obj, u32 idx, s16 *out)
{
    return VFN(obj, STEP_VT_GET_PARAM, u32, int, s16 *)(obj, (int)idx, out) & 0xff;
}

/* the object's sample (pool index), Start and End (0..32767; not set = whole sample) */
static void values(void *obj, s16 *smp, s16 *a, s16 *b)
{
    const struct cfw_view *v = &cfw_ram.view;
    *smp = -1;
    getp(obj, PARAM_SAMPLE, smp);
    if (!getp(obj, PARAM_SAMPLE_START, a)) /* not set on this step: what the page shows (track's) */
        *a = v->se_val[0];
    if (!getp(obj, PARAM_SAMPLE_END, b))
        *b = v->se_val[1] ? v->se_val[1] : PARAM_POS_MAX;
}

static void *pool_of(void *app)
{
    void *proj = PTR((char *)app + APP_PATMGR, PM_PROJECT);
    return proj ? PTR(proj, PROJ_POOL) : 0;
}

/* The parameter screen's own widgets (two names, two values, two bars) are drawn above ours (the
 * frame is composed from slot 49 down to 0 and ours gets a higher slot), so they are hidden while
 * the panel is up and shown again after: disp vt show / hide (slot), which only flip bit 0. */
void cfw_stock_widgets(void *scr, void *disp, u32 op)
{
    void **tab = PTR(disp, DISP_TABLE);
    for (u32 i = 0; i < 6; i++) {
        u32 slot = U32(scr, PSCR_SLOTS + 4 * i);
        if (slot < DISP_SLOTS && tab[slot] && ((U32(tab[slot], WG_FLAGS) & 1) != (op == DISP_SHOW_SLOT)))
            VFN(disp, op, void, u32)(disp, slot);
    }
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

/* Start (1) or End (2) = playhead, as a normal edit of the step / track */
static void set_point(void *app, void *obj, struct cfw_pts *t, u32 which)
{
    s16 si, a, b;
    values(obj, &si, &a, &b);
    u32 c = t->cursor; /* End before Start is allowed: the sample plays in reverse (as stock) */
    (void)si;
    (void)a;
    (void)b;
    cfw_edit_begin((char *)app + APP_CTRL, "Start/End");
    cfw_edit_lock();
    VFN(obj, STEP_VT_SET_PARAM, void, int, int)(obj, which == 1 ? PARAM_SAMPLE_START : PARAM_SAMPLE_END, (int)c);
    cfw_edit_unlock();
}

/* main loop (cfw_app_update): show / hide / refresh the panel, apply a soft-key press */
void cfw_startend_tick(void *app)
{
    struct cfw_view *v = cfw_view_state();
    void *ui = PTR((char *)app + APP_LISTENER, LISTENER_UI);
    if (!ui)
        return;
    void *mscr = PTR(ui, UI_MENU_SCREEN), *pscr = cfw_param_screen(ui);
    void *disp = mscr ? PTR(mscr, SCR_DISP) : 0, *pool = pool_of(app);
    struct cfw_pts *t = cfw_pts();
    int mine = v->wmode == WMODE_STARTEND && cfw_view_alive(v);
    const char *title = cfw_page_title(app);
    int want = t && disp && pscr && pool && title && cfw_str_is(title, "Sample Start") && obj_ok(v->se_obj);
    if (!want) {
        if (mine) /* give the parameter screen its widgets back if one is on screen */
            close_panel(v, pscr);
        if (t)
            t->pad = 0;
        return;
    }
    s16 si, a, b;
    if (!mine) {
        if (cfw_view_alive(v)) /* the Slice preview's widget */
            return;
        u32 desc[9];
        for (int i = 0; i < 5; i++)
            ((volatile u32 *)desc)[i] = 0;
        desc[5] = SE_X;
        desc[6] = SE_Y;
        desc[7] = SE_W;
        desc[8] = SE_H;
        s32 slot = VFN(disp, DISP_CREATE, s32, u32, u32 *)(disp, WIDGET_TYPE_RECT, desc);
        if (slot < 0 || slot >= DISP_SLOTS)
            return;
        void *w = ((void **)PTR(disp, DISP_TABLE))[slot];
        *(const void **)w = (const void *)cfw_wave_vt;
        v->slot = slot;
        v->widget = w;
        v->disp = disp;
        v->wmode = WMODE_STARTEND;
        v->cursor = 0xffffffffu;
        values(v->se_obj, &si, &a, &b);
        t->cursor = (u16)a; /* the playhead starts at Start */
        t->pad = 0;
        cfw_pts_keep_visible(t);
        VFN(disp, DISP_SHOW, void, u32)(disp, (u32)slot);
    }
    v->ctx = pool; /* the render reads the pool from here */
    if (t->pad) {
        set_point(app, v->se_obj, t, t->pad);
        t->pad = 0;
    }
    cfw_stock_widgets(pscr, disp, DISP_HIDE_SLOT); /* also when the stock code showed one again */
    values(v->se_obj, &si, &a, &b);
    u32 key = (u32)v->se_obj ^ ((u32)(u16)a << 16 | (u16)b) ^ ((u32)(u16)si << 8) ^ t->cursor ^ (u32)t->zoom << 28;
#ifdef CFW_MOD
    struct sample hs;
    if (si >= 0 && cfw_pool_sample(pool, (u32)si, &hs))
        key ^= cfw_heads_key(&hs) * 0x01000193u;
    key ^= (u32)cfw_mod_live(10) * 0x9e3779b1u ^ (u32)cfw_mod_live(11) * 0x85ebca77u;
#endif
    if (key != v->cursor) {
        v->cursor = key;
        VFN(disp, DISP_REFRESH, void, u32)(disp, (u32)v->slot);
    }
}

/* input while the panel is up (cave input hooks): encoder = playhead / Shift zoom, top / bottom
 * key left of the screen = Start / End at the playhead (applied by the next tick). 1 = swallowed. */
int cfw_startend_input(u32 kind, u32 id, u32 val)
{
    struct cfw_view *v = &cfw_ram.view;
    struct cfw_pts *t = cfw_pts();
    if (!t)
        return 0;
    if (kind == 4) {
        if (v->shift)
            cfw_pts_zoom(t, (s32)val);
        else
            cfw_pts_move(t, (s32)val, cfw_pts_span(t) / 128 + 1);
        return 1; /* the tick redraws */
    }
    if (kind != 1 || (id != BTN_LTOP && id != BTN_LBOT) || v->shift)
        return 0;
    if (val == 0)
        t->pad = id == BTN_LTOP ? 1 : 2;
    return 1; /* press, release or hold of a key we own */
}

static char *put_ms(char *p, const char *label, u32 pos, const struct sample *smp)
{
    u32 f = (u32)(((u64)pos * smp->frames) >> 15);
    p = cfw_put_str(p, label);
    p = cfw_put_uint(p, smp->rate > 0.0f ? (u32)((float)f * 1000.0f / smp->rate + 0.5f) : 0);
    p = cfw_put_str(p, " ms");
    *p = 0;
    return p;
}

/* a position (0..32768) in the window [v0, v0 + sp] -> x, or -1 outside */
static int px(u32 pos, u32 v0, u32 sp, int x0, int ww)
{
    if (pos < v0 || pos > v0 + sp)
        return -1;
    return x0 + (int)((pos - v0) * (u32)ww / sp);
}

/* widget render (cfw_wave_render dispatches here for WMODE_STARTEND) */
int cfw_startend_render(void *w)
{
    struct cfw_view *v = &cfw_ram.view;
    struct cfw_pts *t = cfw_pts();
    int x = (s32)U32(w, W_X), y = (s32)U32(w, W_Y), wd = (s32)U32(w, W_W), h = SE_H;
    struct dlw d = {0};
    struct sample smp;
    char buf[32];
    s16 si = -1, a = 0, b = PARAM_POS_MAX;
    if (obj_ok(v->se_obj))
        values(v->se_obj, &si, &a, &b);
    int have = t && si >= 0 && cfw_pool_sample(v->ctx, (u32)si, &smp);
    fw_eve_begin();
    fw_eve_dlstart();
    dl(&d, DL_SAVE_CONTEXT);
    dl(&d, DL_VERTEX_FORMAT(4));
    dl(&d, DL_COLOR_RGB(0)); /* cover the two stock parameter blocks */
    dl(&d, DL_BEGIN(PRIM_RECTS));
    dl(&d, DL_VERTEX2F(x * SUB, y * SUB));
    dl(&d, DL_VERTEX2F((x + wd) * SUB, (y + h) * SUB));
    dl(&d, DL_END);
    cfw_key_tile(&d, 0, "Start", have);
    cfw_key_tile(&d, 1, "End", have);
    if (!have) {
        text(&d, TILE_TEXT_X, ROW1_Y, C_DIM, 0, "Sample Start / End");
        text(&d, x + wd / 2, y + h / 2 - 8, C_DIM, OPT_CENTERX, "No Sample");
    } else {
        int x0 = x + TILE_TEXT_X, ww = wd - TILE_TEXT_X - 4, cy = y + 96, hh = 56;
        int rev = (u16)b < (u16)a; /* reverse: the part between End and Start plays backwards */
        u32 v0 = t->view0, sp = cfw_pts_span(t), ua = rev ? (u16)b : (u16)a, ub = (rev ? (u16)a : (u16)b) + 1u;
        if (ub > PARAM_POS_MAX + 1u)
            ub = PARAM_POS_MAX + 1u;
        /* window [v0, v0 + sp): dim before Start, bright Start..End, dim after End */
        u32 lo = ua > v0 ? ua : v0, hi = ub < v0 + sp ? ub : v0 + sp;
        if (lo > v0 + sp)
            lo = v0 + sp;
        if (hi < lo)
            hi = lo;
        int pl = (int)((lo - v0) * (u32)ww / sp), ph = (int)((hi - v0) * (u32)ww / sp);
        u32 f0 = (u32)(((u64)v0 * smp.frames) >> 15), fs = (u32)(((u64)sp * smp.frames) >> 15);
        u32 fpl = (u32)(((u64)lo * smp.frames) >> 15), fph = (u32)(((u64)hi * smp.frames) >> 15);
        text(&d, x0 + ww, ROW1_Y, C_DIM, OPT_RIGHTX, smp.name);
        cfw_axis(&d, x0, ww, cy);
        if (pl > 0 && fpl > f0)
            cfw_draw_wave(&d, &smp, x0, pl, cy, hh, f0, fpl - f0, 0);
        if (ph > pl && fph > fpl)
            cfw_draw_wave(&d, &smp, x0 + pl, ph - pl, cy, hh, fpl, fph - fpl, ph - pl);
        if (ww > ph && f0 + fs > fph)
            cfw_draw_wave(&d, &smp, x0 + ph, ww - ph, cy, hh, fph, f0 + fs - fph, 0);
        u32 sa = (u16)a, sb = rev ? (u16)b : ub; /* the Start / End lines stay where they are set */
        int xa = px(sa, v0, sp, x0, ww), xb = px(sb, v0, sp, x0, ww), xc = px(t->cursor, v0, sp, x0, ww);
        dl(&d, DL_LINE_WIDTH(8));
        dl(&d, DL_COLOR_RGB(C_HEAD));
        if (xc >= 0)
            vline(&d, xc * SUB + 8, cy - hh - 8, cy + hh + 8);
        dl(&d, DL_LINE_WIDTH(12));
        dl(&d, DL_COLOR_RGB(C_START));
        if (xa >= 0)
            vline(&d, xa * SUB + 8, cy - hh - 6, cy + hh + 6);
        dl(&d, DL_COLOR_RGB(C_END));
        if (xb >= 0)
            vline(&d, xb * SUB - 8, cy - hh - 6, cy + hh + 6);
        dl(&d, DL_END);
#ifdef CFW_MOD
        cfw_draw_heads(&d, &smp, x0, ww, cy - hh - 8, cy + hh + 8, v0, sp); /* the voices playing it now */
        for (u32 k = 0; k < 2; k++) { /* an LFO on Start or End: where the next note will start or end */
            if (!cfw_mod_mapped(10 + k))
                continue;
            s32 at = (k ? (s32)(u16)b : (s32)(u16)a) + cfw_mod_live(10 + k);
            at = at < 0 ? 0 : at > PARAM_POS_MAX ? PARAM_POS_MAX : at;
            int lx = px((u32)at, v0, sp, x0, ww);
            if (lx >= 0) {
                dl(&d, DL_COLOR_RGB(k ? C_END : C_START));
                dl(&d, DL_LINE_WIDTH(8));
                dl(&d, DL_BEGIN(PRIM_LINES));
                vline(&d, lx * SUB + 8, cy - hh / 2, cy + hh / 2);
                dl(&d, DL_END);
            }
        }
#endif
        put_ms(buf, "", sa, &smp); /* right of the Start tile (top key) */
        text(&d, TILE_TEXT_X, ROW1_Y, C_START, 0, buf);
        char *q = put_ms(buf, "", sb, &smp); /* right of the End tile (bottom key) */
        if (rev)
            cfw_put_str(q, "  reverse")[0] = 0;
        text(&d, TILE_TEXT_X, ROW2_Y, C_END, 0, buf);
        char *p = put_ms(buf, "", t->cursor, &smp); /* the playhead */
        if (t->zoom) {
            p = cfw_put_str(p, "  x");
            p = cfw_put_uint(p, 1u << t->zoom);
            *p = 0;
        }
        text(&d, x0 + ww, ROW2_Y, C_DIM, OPT_RIGHTX, buf);
    }
    dl(&d, DL_RESTORE_CONTEXT);
    fw_eve_end();
    return 0;
}
#endif
