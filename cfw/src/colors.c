/* Folder colours (CFW5.1): every sample-pool folder has a colour, White by default (= stock look).
 *   Menu > Samples > View Sample Pool: a stripe in the folder's colour left of each folder row; on a
 *   highlighted folder the top key left of the screen opens a palette where the "Memory" row is:
 *   encoder = colour (pads follow live), encoder click or the top key = set, Back = cancel.
 *   Step pads: a trig whose sample sits in a coloured folder lights in that colour instead of the
 *   stock trig colour (per sample, not per track). Playhead, held / selected steps stay stock.
 * Colours are kept by folder name in /Projects/cfwColors (one file for all projects: a folder name
 * keeps its colour everywhere; stock Save copies only its own files into a project).
 * The pad part runs from the LED set hook (cfw_led_set in the cave); everything here lives in the
 * ext region and is only called after cfw_ext_ok(). */
#include "slice.h"
#include "dlw.h"

#ifdef CFW_COLORS
extern void *fw_operator_new(u32 size);
extern int fw_file_open(void *f, const char *path, u32 mode);
extern int fw_file_read(void *f, void *buf, u32 n);
extern int fw_file_write(void *f, const void *buf, u32 n);
extern int fw_file_close(void *f);

#define COL_MAGIC 0x434f4c31u /* "COL1" */
#define COL_FILE "/Projects/cfwColors"
#define COL_MAX 96
#define FA_READ 0x01
#define FA_WRITE_NEW 0x0a
#define FILE_OBJ 0x70
#define NCOL 10
#define POOL_FOLDERS 0x400 /* pool meta: folder count, names char[20][17] at +0x404 */
#define POOL_NAMES 0x404
#define POOL_NAME_LEN 17
#define LIST_COUNT 0x34      /* the folder list widget (browser, pool view): rows */
#define LIST_SEL 0x40        /* highlighted row */
#define LIST_SCREEN_ROW 0x44 /* its row on screen (0..5) */
#define LIST_ROWS 6           /* rows the stock list shows */
#define LIST_ROW_H 26         /* stock row pitch */
#define LIST_ROW0_CY 42       /* centre of the first row */
#define C_TEXT_DIM 0x8c8c8c
#define C_TEXT 0xffffff
extern const char fw_step_mode_vt[]; /* the step-grid pad mode's vtable (symbols.ld) */

/* pad LED colours (R, G, B; the stock trig is 55 77 55) and the same colours on screen */
static const u8 led_rgb[NCOL][3] = {
    {0x55, 0x77, 0x55}, {0xa0, 0x08, 0x04}, {0xa0, 0x38, 0x00}, {0x90, 0x78, 0x00}, {0x48, 0xa0, 0x00},
    {0x00, 0xa0, 0x20}, {0x00, 0x8c, 0x8c}, {0x00, 0x28, 0xc0}, {0x60, 0x08, 0xc0}, {0xb0, 0x14, 0x60}};
static const u32 scr_rgb[NCOL] = {0xffffff, 0xff4a3d, 0xff9a2e, 0xffd23f, 0xa8e03a,
                                  0x2bd45a, 0x2ad4d4, 0x3a7bff, 0x9b5cff, 0xff5aa8};
static const char *const col_name[NCOL] = {"White", "Red", "Orange", "Yellow", "Lime",
                                           "Green", "Cyan", "Blue", "Purple", "Pink"};

struct col_entry {
    char name[POOL_NAME_LEN];
    u8 color;
    u8 pad[2];
};
struct colors {
    u32 magic;
    void *app;
    u32 n;
    u8 loaded, open, sel, folder; /* picker: open, highlighted colour, folder index */
    u8 orig, nhid, pad2[2];
    u8 hid[4];                    /* stock widgets hidden while the picker is up */
    u32 key;
    struct col_entry e[COL_MAX];
};

/* the state hangs off the knob pages' heap state (cfw_ram is full); 0 = not there (yet) */
static struct colors *col_peek(void)
{
    void **slot = cfw_pages_colors(0);
    struct colors *c = slot ? *slot : 0;
    u32 a = (u32)c;
    int ram = (a >= 0x20000000u && a < 0x20080000u) || (a >= 0x20200000u && a < 0x20300000u) ||
              (a >= 0x80000000u && a < 0x82000000u);
    return ram && !(a & 3) && c->magic == COL_MAGIC ? c : 0;
}

static struct colors *col_state(void)
{
    struct colors *c = col_peek();
    void **slot = cfw_pages_colors(1);
    if (!c && slot) {
        c = fw_operator_new(sizeof *c);
        if (!c)
            return 0;
        for (u32 i = 0; i < sizeof *c; i++)
            ((volatile u8 *)c)[i] = 0;
        c->magic = COL_MAGIC;
        *slot = c;
    }
    return c;
}

static void load(struct colors *c)
{
    u8 f[FILE_OBJ] __attribute__((aligned(4)));
    u32 hdr[2];
    c->loaded = 1;
    c->n = 0;
    for (u32 i = 0; i < FILE_OBJ; i++)
        ((volatile u8 *)f)[i] = 0;
    if (!fw_file_open(f, COL_FILE, FA_READ))
        return;
    if (fw_file_read(f, hdr, sizeof hdr) == (int)sizeof hdr && hdr[0] == COL_MAGIC && hdr[1] <= COL_MAX &&
        fw_file_read(f, c->e, hdr[1] * sizeof c->e[0]) == (int)(hdr[1] * sizeof c->e[0]))
        c->n = hdr[1];
    fw_file_close(f);
    for (u32 i = 0; i < c->n; i++)
        if (c->e[i].color >= NCOL)
            c->e[i].color = 0;
}

static void save(const struct colors *c)
{
    u8 f[FILE_OBJ] __attribute__((aligned(4)));
    u32 hdr[2] = {COL_MAGIC, c->n};
    for (u32 i = 0; i < FILE_OBJ; i++)
        ((volatile u8 *)f)[i] = 0;
    if (!fw_file_open(f, COL_FILE, FA_WRITE_NEW))
        return;
    fw_file_write(f, hdr, sizeof hdr);
    fw_file_write(f, c->e, c->n * sizeof c->e[0]);
    fw_file_close(f);
}

static int name_eq(const char *a, const char *b)
{
    for (u32 i = 0; i < POOL_NAME_LEN; i++) {
        if (a[i] != b[i])
            return 0;
        if (!a[i])
            return 1;
    }
    return 1;
}

static u32 color_of(struct colors *c, const char *name)
{
    if (!c->loaded)
        load(c);
    for (u32 i = 0; i < c->n; i++)
        if (name_eq(c->e[i].name, name))
            return c->e[i].color;
    return 0;
}

static void set_color(struct colors *c, const char *name, u32 col)
{
    u32 i;
    for (i = 0; i < c->n && !name_eq(c->e[i].name, name); i++)
        ;
    if (i == c->n) {
        if (!col || c->n >= COL_MAX)
            return;
        c->n++;
    }
    for (u32 k = 0; k < POOL_NAME_LEN; k++)
        c->e[i].name[k] = name[k];
    c->e[i].name[POOL_NAME_LEN - 1] = 0;
    c->e[i].color = (u8)col;
}

static void *pool_of(void *app)
{
    void *proj = PTR((char *)app + APP_PATMGR, PM_PROJECT);
    return cfw_ptr_ok(proj) ? PTR(proj, PROJ_POOL) : 0;
}

/* ---- pads (from the LED hook) ----------------------------------------------------------------- */
/* the colour for LED idx (row * 20 + column) showing a plain trig, or 0 = stock */
const u8 *cfw_colors_pad(u32 idx)
{
    struct colors *c = col_peek();
    if (!c || !c->app || !c->loaded || !c->n)
        return 0; /* the tick sets app and loads the file; nothing coloured yet = stock */
    u32 row = idx / LED_COLS, col = idx % LED_COLS;
    if (row >= PAD_ROWS || col >= PAD_COLS)
        return 0;
    void *ctrl = (char *)c->app + APP_CTRL, *mode = PTR(ctrl, CTRL_MODE);
    if (!cfw_ptr_ok(mode) || PTR(mode, 0) != (void *)fw_step_mode_vt)
        return 0;
    u32 track = row + (U8(mode, MODE_BANK) ? 8u : 0u);
    u32 step = col + U32(ctrl, CTRL_TRACK_PAGE + 4 * track) * 16u;
    if (track >= NUM_TRACKS || step >= NUM_STEPS)
        return 0;
    void *pv = PTR((char *)ctrl + CTRL_SEL, SEL_PVIEW);
    void *trk = VFN(pv, 0, void *, int)(pv, (int)track);
    void *st = VFN(trk, 0, void *, int)(trk, (int)step);
    if (!VFN(st, STEP_VT_IS_ON, u32)(st) || VFN(st, STEP_VT_TRACK_TYPE, u32)(st) != 0)
        return 0;
    s16 si = -1;
    if (!(VFN(st, STEP_VT_GET_PARAM, u32, int, s16 *)(st, PARAM_SAMPLE, &si) & 0xff) || si < 0 || si >= POOL_SLOTS)
        return 0;
    void *pool = pool_of(c->app), *meta = pool ? PTR(pool, POOL_META) : 0;
    if (!cfw_ptr_ok(meta))
        return 0;
    u32 fo = U32(meta, 4 * (u32)si);
    if (fo >= U32(meta, POOL_FOLDERS))
        return 0;
    u32 k = color_of(c, (const char *)meta + POOL_NAMES + fo * POOL_NAME_LEN);
    return k ? led_rgb[k] : 0;
}

/* ---- the pool view ---------------------------------------------------------------------------- */
#define BTN_LTOP 0
#define BTN_CLICK 6
#define BTN_BACK 7

/* the browser screen is up showing the pool's folders: its folder list widget, else 0 */
static void *pool_list(void *app, void **disp_out, void **meta_out)
{
    void *ui = PTR((char *)app + APP_LISTENER, LISTENER_UI);
    if (!cfw_ptr_ok(ui) || PTR(ui, UI_CUR_SCREEN) != PTR(ui, UI_BROWSER_SCREEN))
        return 0;
    void *mscr = PTR(ui, UI_MENU_SCREEN), *disp = cfw_ptr_ok(mscr) ? PTR(mscr, SCR_DISP) : 0;
    void *pool = pool_of(app), *meta = pool ? PTR(pool, POOL_META) : 0;
    if (!cfw_ptr_ok(disp) || !cfw_ptr_ok(meta))
        return 0;
    void **tab = PTR(disp, DISP_TABLE), *list = 0;
    int title = 0;
    for (u32 i = 0; i < DISP_SLOTS; i++) {
        void *w = tab[i];
        if (!cfw_ptr_ok(w) || !(U32(w, WG_FLAGS) & 1))
            continue;
        const char *t = PTR(w, W_TEXT);
        if (cfw_ptr_ok(t) && cfw_str_is(t, "Sample Pool"))
            title = 1;
        if (U32(w, W_X) == 10 && U32(w, W_Y) == 28 && U32(w, W_H) == 150)
            list = w;
    }
    *disp_out = disp;
    *meta_out = meta;
    return title ? list : 0;
}

static void show_slots(void *disp, struct colors *c, int show)
{
    for (u32 i = 0; i < c->nhid; i++)
        VFN(disp, show ? DISP_SHOW_SLOT : DISP_HIDE_SLOT, void, u32)(disp, c->hid[i]);
    if (show)
        c->nhid = 0;
}

static void close_widget(struct cfw_view *v)
{
    if (cfw_view_alive(v)) {
        VFN(v->disp, DISP_FLUSH, void)(v->disp);
        VFN(v->disp, DISP_DESTROY, void, u32)(v->disp, (u32)v->slot);
    }
    v->slot = -1;
    v->widget = 0;
    v->wmode = 0;
}

void cfw_colors_tick(void *app)
{
    struct cfw_view *v = cfw_view_state();
    struct colors *c = col_state();
    if (!c)
        return;
    c->app = app;
    if (!c->loaded)
        load(c);
    void *disp = 0, *meta = 0, *list = pool_list(app, &disp, &meta);
    int mine = v->wmode == WMODE_POOL && cfw_view_alive(v);
    if (!list) {
        if (c->nhid && mine)
            show_slots(v->disp, c, 1);
        c->nhid = 0;
        c->open = 0;
        if (mine)
            close_widget(v);
        return;
    }
    if (!mine) {
        if (cfw_view_alive(v))
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
        void *w = ((void **)PTR(disp, DISP_TABLE))[slot];
        *(const void **)w = (const void *)cfw_wave_vt;
        v->slot = slot;
        v->widget = w;
        v->disp = disp;
        v->wmode = WMODE_POOL;
        c->key = 0;
        VFN(disp, DISP_SHOW, void, u32)(disp, (u32)slot);
    }
    v->ctx = list;
    if (c->open && !c->nhid) { /* the palette takes the "Memory" row: hide what is there */
        void **tab = PTR(disp, DISP_TABLE);
        for (u32 i = 0; i < DISP_SLOTS && c->nhid < 4; i++) {
            void *w = tab[i];
            if (cfw_ptr_ok(w) && (U32(w, WG_FLAGS) & 1) && w != v->widget && (s32)U32(w, W_Y) >= 180 &&
                (s32)U32(w, W_Y) < 210)
                c->hid[c->nhid++] = (u8)i;
        }
        show_slots(disp, c, 0);
    } else if (!c->open && c->nhid)
        show_slots(disp, c, 1);
    u32 key = U32(list, LIST_SEL) * 131u ^ U32(list, LIST_COUNT) * 7u ^ (u32)c->open << 20 ^ (u32)c->sel << 24 ^ c->n;
    for (u32 i = 0; i < c->n; i++)
        key = key * 31u + c->e[i].color;
    if (key != c->key) {
        c->key = key;
        VFN(disp, DISP_REFRESH, void, u32)(disp, (u32)v->slot);
    }
}

int cfw_colors_input(u32 kind, u32 id, u32 val)
{
    struct cfw_view *v = &cfw_ram.view;
    struct colors *c = col_peek();
    if (v->magic != VIEW_MAGIC || v->wmode != WMODE_POOL || !cfw_view_alive(v) || !c || !c->app)
        return 0;
    void *disp = 0, *meta = 0, *list = pool_list(c->app, &disp, &meta);
    if (!list)
        return 0;
    u32 sel = U32(list, LIST_SEL), nf = U32(meta, POOL_FOLDERS);
    const char *name = (const char *)meta + POOL_NAMES;
    if (!c->open) {
        if (kind != 1 || id != BTN_LTOP)
            return 0;
        if (val == 0 && sel < nf) {
            c->folder = (u8)sel;
            c->orig = c->sel = (u8)color_of(c, name + sel * POOL_NAME_LEN);
            c->open = 1;
        }
        return 1; /* the top key is ours in the pool view (no accidental Enter) */
    }
    if (kind == 4) {
        s32 s = (s32)c->sel + (s32)val;
        c->sel = (u8)(s < 0 ? 0 : s >= NCOL ? NCOL - 1 : s);
        set_color(c, name + c->folder * POOL_NAME_LEN, c->sel); /* live: the pads follow */
        return 1;
    }
    if (kind == 2)
        return 1;
    if (kind != 1)
        return 0;
    if (val != 0)
        return id == BTN_CLICK || id == BTN_LTOP || id == BTN_BACK;
    if (id == BTN_CLICK || id == BTN_LTOP) {
        set_color(c, name + c->folder * POOL_NAME_LEN, c->sel);
        save(c);
        c->open = 0;
        return 1;
    }
    if (id == BTN_BACK) {
        set_color(c, name + c->folder * POOL_NAME_LEN, c->orig);
        c->open = 0;
        return 1;
    }
    return 0;
}

int cfw_colors_render(void *w)
{
    struct cfw_view *v = &cfw_ram.view;
    struct colors *c = col_peek();
    struct dlw d = {0};
    (void)w;
    fw_eve_begin();
    fw_eve_dlstart();
    dl(&d, DL_SAVE_CONTEXT);
    dl(&d, DL_VERTEX_FORMAT(4));
    void *list = v->ctx, *disp = 0, *meta = 0;
    if (c && c->app && cfw_ptr_ok(list) &&
        pool_list(c->app, &disp, &meta) == list) {
        u32 sel = U32(list, LIST_SEL), nf = U32(meta, POOL_FOLDERS), scr = U32(list, LIST_SCREEN_ROW);
        u32 first = scr <= sel && scr < LIST_ROWS ? sel - scr : 0;
        const char *names = (const char *)meta + POOL_NAMES;
        dl(&d, DL_LINE_WIDTH(16));
        dl(&d, DL_BEGIN(PRIM_RECTS));
        for (u32 k = 0; k < LIST_ROWS && first + k < nf; k++) { /* a stripe left of every folder row */
            u32 col = c->open && first + k == c->folder ? c->sel : color_of(c, names + (first + k) * POOL_NAME_LEN);
            int y = LIST_ROW0_CY + (int)k * LIST_ROW_H; /* the row's centre, as the stock list draws it */
            dl(&d, DL_COLOR_RGB(col ? scr_rgb[col] : 0x3a3a3a));
            dl(&d, DL_VERTEX2F(2 * SUB, (y - 7) * SUB));
            dl(&d, DL_VERTEX2F(6 * SUB, (y + 7) * SUB));
        }
        dl(&d, DL_END);
        if (c->open) { /* the palette where the Memory row was */
            int x = 32, y = 186;
            text(&d, 10, y + 2, C_TEXT, 0, col_name[c->sel]); /* the chosen colour's name */
            for (u32 k = 0; k < NCOL; k++) {
                int cx = x + 40 + (int)k * 24;
                if (k == c->sel) {
                    dl(&d, DL_COLOR_RGB(0xffffff));
                    dl(&d, DL_LINE_WIDTH(4 * SUB));
                    dl(&d, DL_BEGIN(PRIM_RECTS));
                    dl(&d, DL_VERTEX2F((cx - 1) * SUB, (y - 1) * SUB));
                    dl(&d, DL_VERTEX2F((cx + 17) * SUB, (y + 19) * SUB));
                    dl(&d, DL_END);
                }
                dl(&d, DL_COLOR_RGB(k == c->sel ? 0x000000 : 0x000000));
                dl(&d, DL_LINE_WIDTH(3 * SUB));
                dl(&d, DL_BEGIN(PRIM_RECTS));
                dl(&d, DL_COLOR_RGB(scr_rgb[k]));
                dl(&d, DL_VERTEX2F((cx + 1) * SUB, (y + 1) * SUB));
                dl(&d, DL_VERTEX2F((cx + 15) * SUB, (y + 17) * SUB));
                dl(&d, DL_END);
            }
        }
    }
    dl(&d, DL_RESTORE_CONTEXT);
    fw_eve_end();
    return 0;
}
#endif
