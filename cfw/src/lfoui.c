/* Menu > LFO and Menu > Matrix (Play+, Play RT): the panels of mod.c's two LFOs.
 *
 * Pick a track, then turn knobs: each LFO has its own amount per track and parameter, so LFO 1 can move track 1's Filter
 * and track 3's Pan at once, and both LFOs can move the same parameter. Rate, Shape and Depth belong to the LFO.
 *   select pads       (the rightmost pad of a row) the track being edited. Hold one and tap others: several.
 *                     Shift + pad: all of them. A row of boxes shows them, and which tracks this LFO moves
 *   top key           LFO 1 / LFO 2
 *   Back              lets go when something is taken, else leaves
 *
 * The LFO view: a scope with the shape and a dot on the live position, under it Rate, Shape, Depth, Sync, Restart.
 *   encoder           move between the cells
 *   encoder click     take the cell, turn to change, click to let go
 *   Shift + encoder   change the highlighted cell at once; taken: five times as far
 *   bottom key (Map)  tap: the Matrix. Held: touch or turn a knob to map its parameter on the tracks being edited, turn
 *                     on to set the amount, touch a mapped knob and let go to free it. With Shift: the knob's second one
 *
 * The Matrix: 5 x 3 cells laid out as the 15 knobs, one knob per cell. Every knob sets its cell's amount for the shown
 * LFO and the tracks being edited (the last knob: the LFO's Rate) and does nothing else until Back leaves the screen.
 * A parameter stays on or next to its own knob. The encoder does the same one cell at a time: turn to move, click to take
 * the cell, turn to change it (Shift: five times as far), click to let go. Shift + click: the cell back to 0.
 * Bottom key: the LFO view.
 *
 * Play, Perform and the other pads stay stock, so it can be heard while it is set. */
#include "slice.h"
#include "dlw.h"

#ifdef CFW_MOD
extern volatile u32 fw_millis;
extern void fw_menu_back(void *scr);
extern void fw_toast_show(void *toast, const char *msg);

#define BTN_TOP 0
#define BTN_BOT 3
#define BTN_CLICK 6
#define BTN_BACK 7
#define VIEW_LFO 0
#define VIEW_MATRIX 1
#define CELLS 5
#define SELECT_COL 19       /* the rightmost side pad of a row: track select */
#define SEL_TRACK_MASK 0x0c /* selection object: selected tracks */
#define REDRAW_MS 33
#define KNOBS 15

#define C_WHITE 0xffffff
#define C_DIM 0x7a7a7a
#define C_CARD 0x121212
#define C_CELL 0x1e1e1e
#define C_GRID 0x2a2a2a
#define C_LFO1 0xff9a2e
#define C_LFO2 0x35c8ff
#define C_LFO1_D 0x33200c
#define C_LFO2_D 0x122a36

#define DL_COLOR_A(a) (0x10000000u | (a))
#define DL_SCISSOR_XY(x, y) (0x1b000000u | ((u32)(x) << 11) | (u32)(y))
#define DL_SCISSOR_SIZE(w, h) (0x1c000000u | ((u32)(w) << 12) | (u32)(h))
#define PRIM_LINE_STRIP 4
#define PRIM_EDGE_STRIP_B 8

static const char *const shape_name[LFO_SHAPES] = {"Sine", "Triangle", "Ramp Up", "Ramp Down", "Square", "Random", "Drift"};
static const char *const div_name[LFO_DIVS] = {"8 bars", "4 bars", "2 bars", "1 bar", "1/2", "1/4", "1/4 T", "1/8", "1/8 T", "1/16", "1/16 T", "1/32"};
static const char *const tgt_name[MOD_TARGETS] = {"Volume", "Pan", "Note", "Microtune", "Filter", "Resonance", "Overdrive",
                                                  "Bit Depth", "Delay", "Reverb", "Sample Start", "Sample End", "Attack", "Decay"};
static const char *const cell_name[CELLS] = {"Rate", "Shape", "Depth", "Sync", "Restart"};

/* the Matrix, by knob: 0..4 top row, 5..9 middle, 10..14 bottom. MOD_TARGETS order; -1 = the LFO's Rate */
static const s8 matrix_cell[KNOBS] = {3, 2, 11, 9, 8, 1, 0, 10, 6, 7, 5, 4, 12, 13, -1};
static const char *const matrix_name[KNOBS] = {"Micro", "Note", "End", "Verb", "Delay", "Pan", "Volume", "Start",
                                               "Drive", "Bits", "Reso", "Filter", "Attack", "Decay", "Rate"};

/* the (empty) submenus whose screen the panel covers */
void cfw_menu_build_lfo(void *ctx, void *menu)
{
    (void)ctx;
    (void)menu;
}

void cfw_menu_build_matrix(void *ctx, void *menu)
{
    (void)ctx;
    (void)menu;
    /* a different function from the one above, whatever the compiler would like to merge */
    __asm__ volatile("" : : : "memory");
}

void cfw_lfo_enter(int matrix)
{
    struct mod_ui *u = cfw_mod_ui();
    if (u) {
        u->view = matrix ? VIEW_MATRIX : VIEW_LFO;
        u->grab = 0;
        u->held = 0;
        u->touch_cell = 0;
    }
}

int cfw_lfo_due(void)
{
    struct mod_ui *u = cfw_mod_ui();
    if (!u || fw_millis - u->drawn_ms < REDRAW_MS)
        return 0;
    u->drawn_ms = fw_millis;
    return 1;
}

static s32 clamp(s32 v, s32 lo, s32 hi) { return v < lo ? lo : v > hi ? hi : v; }

static void edit_cell(struct mod_lfo *f, u32 cell, s32 d)
{
    static const u8 off[CELLS] = {__builtin_offsetof(struct mod_lfo, rate), __builtin_offsetof(struct mod_lfo, shape),
                                  __builtin_offsetof(struct mod_lfo, depth), __builtin_offsetof(struct mod_lfo, sync),
                                  __builtin_offsetof(struct mod_lfo, retrig)};
    static const u8 top[CELLS] = {LFO_RATES - 1, LFO_SHAPES - 1, 100, 1, 1};
    if (cell == 0 && f->sync) {
        f->div = (u8)clamp((s32)f->div - d, 0, LFO_DIVS - 1); /* clockwise = faster */
    } else if (cell < CELLS) {
        u8 *p = (u8 *)f + off[cell];
        *p = (u8)clamp((s32)*p + d, 0, top[cell]);
    }
}

/* the first track being edited: its values are the ones shown */
static u32 first_track(const struct mod_ui *u)
{
    u32 t = 0;
    while (t < MOD_TRACKS - 1 && !(u->focus & (1u << t)))
        t++;
    return t;
}

/* a parameter's amount on all tracks being edited */
static void set_amt(const struct mod_ui *u, struct mod_save *s, u32 i, s32 v)
{
    for (u32 t = 0; t < MOD_TRACKS; t++)
        if (u->focus & (1u << t))
            s->amt[u->lfo][t][i] = (s8)v;
    cfw_mod_touch();
}

static void add_amt(const struct mod_ui *u, struct mod_save *s, u32 i, s32 d)
{
    for (u32 t = 0; t < MOD_TRACKS; t++)
        if (u->focus & (1u << t))
            s->amt[u->lfo][t][i] = (s8)clamp((s32)s->amt[u->lfo][t][i] + d, -100, 100);
    cfw_mod_touch();
}

/* a message on the stock toast */
static void say(const struct mod_ui *u, const char *a, const char *b)
{
    char msg[32], *p = msg;
    if (!u->scr)
        return;
    for (const char *q = a; *q && p < msg + 30; q++)
        *p++ = *q;
    for (const char *q = b; *q && p < msg + 30; q++)
        *p++ = *q;
    *p = 0;
    void *ctx = PTR(u->scr, SCR_CTX);
    fw_toast_show((char *)PTR(PTR(ctx, MENU_CTX_CTRL), CTRL_UI) + UI_TOAST, msg);
}

/* what a knob's page moves: its two parameters (MOD_TARGETS order), -1 none */
static s32 knob_target(u32 knob, u32 second)
{
    static const s8 map[KNOBS][2] = {
        {-1, -1}, {2, 3},   {-1, -1}, {9, 8},  {-1, -1}, {-1, -1}, {0, 1}, {10, 11},
        {6, 7},   {-1, -1}, {-1, -1}, {4, 5},  {12, 13}, {-1, -1}, {-1, -1},
    };
    return knob < KNOBS ? map[knob][second ? 1 : 0] : -1;
}

/* Map held, a knob touched (0 press / 1 release / 2 held) or turned */
static void map_knob(struct mod_ui *u, struct mod_save *s, u32 knob, int turned, s32 v)
{
    s32 i = knob_target(knob, cfw_ram.view.shift);
    u->used = 1;
    if (!turned && v == 1) { /* let go: a mapped knob only touched is freed (Shift may be up by now) */
        for (u32 k = 0; k < 2; k++) {
            s32 j = knob_target(knob, k);
            if (j >= 0 && (u->pend & (1u << j))) {
                set_amt(u, s, (u32)j, 0);
                u->pend &= (u16)~(1u << j);
                u->last = (s8)j;
                say(u, tgt_name[j], ": off");
            }
        }
        return;
    }
    if (i < 0) {
        if (!(u->seen & (1u << 15)) && (turned || v == 0))
            say(u, "No LFO for this knob", "");
        u->seen |= 1u << 15;
        return;
    }
    u32 bit = 1u << i; /* by parameter: Shift gets to the knob's other one within the same hold */
    if (!turned && v != 0)
        return; /* still held */
    if (!(u->seen & bit)) {
        u->seen |= (u16)bit;
        u->last = (s8)i;
        if (!s->amt[u->lfo][first_track(u)][i]) {
            set_amt(u, s, (u32)i, 50);
            say(u, tgt_name[i], u->lfo ? ": LFO 2" : ": LFO 1");
            return;
        }
        if (!turned) {
            u->pend |= (u16)bit;
            return;
        }
    }
    if (turned) { /* the knob goes on setting the amount */
        u->pend &= (u16)~bit;
        add_amt(u, s, (u32)i, v);
    }
}

/* the Matrix owns every knob */
static void matrix_knob(struct mod_ui *u, struct mod_save *s, u32 knob, s32 d)
{
    s32 i;
    if (knob >= KNOBS)
        return;
    i = matrix_cell[knob];
    u->touch_cell = (s8)knob;
    if (i < 0) {
        edit_cell(&s->l[u->lfo], 0, d);
        cfw_mod_touch();
    } else {
        add_amt(u, s, (u32)i, d);
    }
}

int cfw_lfo_input(u32 kind, u32 id, u32 val)
{
    struct cfw_view *v = &cfw_ram.view;
    struct mod_ui *u = cfw_mod_ui();
    struct mod_save *s = cfw_mod_save_data();
    if (v->magic == VIEW_MAGIC && v->wmode == WMODE_SLICER && cfw_view_alive(v))
        return cfw_slicer_input(kind, id, val);
    if (!u || !s || v->magic != VIEW_MAGIC || v->wmode != WMODE_LFO || !cfw_view_alive(v))
        return 0;
    if (kind == 0) { /* a row's select pad: the track being edited */
        if (id % LED_COLS != SELECT_COL)
            return 0;
        u32 row = id / LED_COLS, bit = row < MOD_TRACKS ? 1u << row : 0;
        void *ctx = u->scr ? PTR(u->scr, SCR_CTX) : 0;
        void *mode = ctx ? PTR(PTR(ctx, MENU_CTX_CTRL), CTRL_MODE) : 0;
        if (val == 1) {
            u->pads &= (u8)~bit;
        } else if (val == 0) {
            if (!bit || (mode && U8(mode, MODE_BANK)))
                say(u, "Sample tracks only", "");
            else if (v->shift)
                u->focus = 0xff;
            else if (u->pads) /* another select pad is held: this one as well */
                u->focus = (u8)(u->focus | u->pads | bit);
            else
                u->focus = (u8)bit;
            u->pads |= (u8)bit;
        }
        return 1;
    }
    if (u->view == VIEW_MATRIX && (kind == 2 || kind == 3)) {
        if (kind == 2)
            matrix_knob(u, s, id, (s32)val);
        else if (val == 0 && id < KNOBS)
            u->touch_cell = (s8)id;
        return 1;
    }
    if (kind == 2 || kind == 3) { /* LFO view: knobs only while Map is held */
        u32 bit = id < 16 ? 1u << id : 0;
        if (kind == 3 && !u->held && (u->touched & bit)) {
            if (val == 1) /* its touch began while Map was held */
                u->touched &= (u16)~bit;
            return 1;
        }
        if (!u->held || !bit)
            return 0;
        if (kind == 3)
            u->touched = val == 1 ? (u16)(u->touched & ~bit) : (u16)(u->touched | bit);
        map_knob(u, s, id, kind == 2, (s32)val);
        return 1;
    }
    if (kind == 4) {
        s32 d = (s32)val;
        if (u->view == VIEW_MATRIX) { /* the same cells, one at a time */
            if (u->grab)
                matrix_knob(u, s, (u32)u->touch_cell, v->shift ? d * 5 : d);
            else
                u->touch_cell = (s8)clamp((s32)u->touch_cell + d, 0, KNOBS - 1);
            return 1;
        }
        if (u->grab || v->shift) {
            edit_cell(&s->l[u->lfo], u->cell, v->shift && u->grab ? d * 5 : d);
            cfw_mod_touch();
        } else {
            u->cell = (u8)clamp((s32)u->cell + d, 0, CELLS - 1);
        }
        return 1;
    }
    if (kind != 1)
        return 0;
    if (id == BTN_BACK) { /* the submenu is empty: stock's Back has no row to act on, so it is done here */
        if (val == 0) {
            if (u->grab)
                u->grab = 0;
            else
                fw_menu_back(u->scr);
        }
        return 1;
    }
    if (id == BTN_BOT) { /* tap: the other view. Held in the LFO view: the shortcut */
        if (val == 0) {
            u->held = u->view == VIEW_LFO;
            u->used = 0;
            u->seen = u->pend = 0;
            u->last = -1;
        } else if (val == 1) {
            u->held = 0;
            if (!u->used) {
                u->view = u->view == VIEW_LFO ? VIEW_MATRIX : VIEW_LFO;
                u->grab = 0;
            }
        }
        return 1;
    }
    if (id != BTN_TOP && id != BTN_CLICK)
        return 0;
    if (val != 0)
        return 1;
    if (id == BTN_TOP) {
        u->lfo ^= 1;
        u->grab = 0;
    } else if (u->view == VIEW_MATRIX) {
        if (!v->shift)
            u->grab = !u->grab; /* take the cell / let go */
        else if (u->touch_cell >= 0 && matrix_cell[u->touch_cell] >= 0)
            set_amt(u, s, (u32)matrix_cell[u->touch_cell], 0); /* back to 0 */
    } else {
        u->grab = !u->grab;
    }
    return 1;
}

/* the panel came up: edit the tracks selected with the select pads, else stay where it was, else the last one touched */
void cfw_lfo_opened(void *ctrl)
{
    struct mod_ui *u = cfw_mod_ui();
    u32 mask = U32((char *)ctrl + CTRL_SEL, SEL_TRACK_MASK) & 0xffu;
    if (!u)
        return;
    u->pads = 0;
    if (mask)
        u->focus = (u8)mask;
    else if (!u->focus)
        u->focus = (u8)(1u << cfw_mod_track());
}

/* ---- the select pads: the tracks being edited blink blue -------------------------------------------- */
#define BLINK_MS 250
#define SELECT_SIDE 3 /* side pad 3 = LED column 19 */
static const u8 led_blue[3] = {0x00, 0x28, 0xc0};

const u8 *cfw_lfo_led(u32 led)
{
    struct cfw_view *v = &cfw_ram.view;
    struct mod_ui *u = cfw_mod_ui();
    u32 row = led / LED_COLS;
    if (v->magic != VIEW_MAGIC || (v->wmode != WMODE_LFO && v->wmode != WMODE_SLICER))
        return 0;
    if (led % LED_COLS != SELECT_COL)
        return v->wmode == WMODE_SLICER ? cfw_slicer_led(led) : 0;
    if (!u || !u->blink || row >= MOD_TRACKS || !(u->focus & (1u << row)))
        return 0;
    return led_blue;
}

static void refresh_rows(void *ctrl, u32 rows)
{
    void *mode = PTR(ctrl, CTRL_MODE);
    if (!cfw_ptr_ok(mode) || U8(mode, MODE_BANK))
        return;
    for (u32 r = 0; r < MOD_TRACKS; r++)
        if (rows & (1u << r))
            VFN(mode, MODE_VT_SIDE_REFRESH, void, int, int, int)(mode, (int)r, SELECT_SIDE, 0);
}

/* every pass: while the panel is up the pads blink; when it is gone they go back to stock */
void cfw_lfo_blink(void *ctrl)
{
    struct cfw_view *v = &cfw_ram.view;
    struct mod_ui *u = cfw_mod_ui();
    if (!u)
        return;
    if (v->magic != VIEW_MAGIC || (v->wmode != WMODE_LFO && v->wmode != WMODE_SLICER) || !cfw_view_alive(v)) {
        if (u->lit) {
            u32 rows = u->lit;
            u->lit = 0;
            u->blink = 0;
            refresh_rows(ctrl, rows);
        }
        return;
    }
    u32 now = fw_millis, rows = u->lit;
    if (now - u->blink_ms >= BLINK_MS) {
        u->blink_ms = now;
        u->blink = !u->blink;
        rows |= u->focus;
    } else if (u->focus != u->lit) { /* another track: at once */
        rows |= u->focus;
    } else {
        return;
    }
    u->lit = u->focus;
    refresh_rows(ctrl, rows);
}

/* ---- drawing ------------------------------------------------------------------------------------- */
static void card(struct dlw *d, u32 color, int x0, int y0, int x1, int y1, int r)
{
    dl(d, DL_COLOR_RGB(color));
    dl(d, DL_LINE_WIDTH((u32)r * SUB));
    dl(d, DL_BEGIN(PRIM_RECTS));
    dl(d, DL_VERTEX2F((x0 + r) * SUB, (y0 + r) * SUB));
    dl(d, DL_VERTEX2F((x1 - r) * SUB, (y1 - r) * SUB));
    dl(d, DL_END);
}

static char *put_hz(char *p, const struct mod_lfo *f) /* "1.25" or from 10 Hz "12.5" */
{
    u32 c = (u32)(cfw_lfo_hz(f->rate) * 100.0f + 0.5f);
    p = cfw_put_uint(p, c / 100);
    *p++ = '.';
    *p++ = (char)('0' + c / 10 % 10);
    if (c < 1000)
        *p++ = (char)('0' + c % 10);
    *p = 0;
    return p;
}

static void cell_value(const struct mod_lfo *f, u32 cell, char *b)
{
    char *p = b;
    if (cell == 0) {
        if (f->sync) {
            p = cfw_put_str(p, div_name[f->div]);
        } else {
            p = put_hz(p, f);
            p = cfw_put_str(p, " Hz");
        }
    } else if (cell == 1) {
        p = cfw_put_str(p, shape_name[f->shape]);
    } else if (cell == 2) {
        p = cfw_put_uint(p, f->depth);
        p = cfw_put_str(p, " %");
    } else if (cell == 3) {
        p = cfw_put_str(p, f->sync ? "Tempo" : "Free");
    } else if (cell == 4) {
        p = cfw_put_str(p, f->retrig ? "With Play" : "Never");
    }
    *p = 0;
}

/* a box per sample track: lit when this LFO moves it, framed white when it is being edited */
static void track_boxes(struct dlw *d, const struct mod_ui *u, const struct mod_save *s, u32 accent, int x, int y0, int y1)
{
    for (u32 t = 0; t < MOD_TRACKS; t++) {
        int bx = x + (int)t * 11, moved = 0;
        for (u32 i = 0; i < MOD_TARGETS; i++)
            moved |= s->amt[u->lfo][t][i] != 0;
        if (u->focus & (1u << t))
            card(d, C_WHITE, bx - 1, y0 - 1, bx + 9, y1 + 1, 1);
        card(d, moved ? accent : C_GRID, bx, y0, bx + 8, y1, 1);
    }
}

/* "Track 3", "3 tracks", "All tracks" */
static void focus_name(const struct mod_ui *u, char *b)
{
    u32 n = 0;
    char *p = b;
    for (u32 t = 0; t < MOD_TRACKS; t++)
        n += (u->focus >> t) & 1u;
    if (n == MOD_TRACKS) {
        p = cfw_put_str(p, "All tracks");
    } else if (n > 1) {
        p = cfw_put_uint(p, n);
        p = cfw_put_str(p, " tracks");
    } else {
        p = cfw_put_str(p, "Track ");
        p = cfw_put_uint(p, first_track(u) + 1);
    }
    *p = 0;
}

#define SC_X0 TILE_TEXT_X
#define SC_X1 312
#define SC_Y0 26
#define SC_Y1 118
#define SC_W (SC_X1 - SC_X0)
#define SC_STEP 3
/* two cycles fit; a shape that jumps gets its edges from the steps being short */
static void draw_scope(struct dlw *d, const struct mod_lfo *f, u32 l, const struct mod_save *s, const struct mod_ui *u)
{
    u32 accent = l ? C_LFO2 : C_LFO1, dark = l ? C_LFO2_D : C_LFO1_D, phase, cyc;
    int cy = (SC_Y0 + 22 + SC_Y1) / 2, hh = (SC_Y1 - SC_Y0 - 22) / 2 - 6, depth = f->depth;
    cfw_lfo_where(l, &phase, &cyc);
    u32 c0 = cyc & ~1u;
    card(d, C_CARD, SC_X0, SC_Y0, SC_X1, SC_Y1, 6);
    dl(d, DL_SAVE_CONTEXT);
    dl(d, DL_SCISSOR_XY(SC_X0 + 1, SC_Y0 + 1));
    dl(d, DL_SCISSOR_SIZE(SC_W - 2, SC_Y1 - SC_Y0 - 2));
    /* the area under the curve, then the grid over it */
    dl(d, DL_COLOR_RGB(dark));
    for (int pass = 0; pass < 2; pass++) {
        if (pass) {
            dl(d, DL_COLOR_RGB(C_GRID));
            dl(d, DL_LINE_WIDTH(8));
            dl(d, DL_BEGIN(PRIM_LINES));
            dl(d, DL_VERTEX2F(SC_X0 * SUB, cy * SUB));
            dl(d, DL_VERTEX2F(SC_X1 * SUB, cy * SUB));
            vline(d, (SC_X0 + SC_W / 2) * SUB, SC_Y0 + 22, SC_Y1);
            dl(d, DL_END);
            dl(d, DL_COLOR_RGB(accent));
            dl(d, DL_LINE_WIDTH(22));
        }
        dl(d, DL_BEGIN(pass ? PRIM_LINE_STRIP : PRIM_EDGE_STRIP_B));
        for (int x = 0; x <= SC_W; x += SC_STEP) {
            u32 t = (u32)x * 2u * 65536u / SC_W, c = c0 + (t >> 16); /* cycles, 16 bits of fraction */
            s32 w = cfw_lfo_eval(f->shape, t << 16, c);
            int y = cy - (w >> 8) * hh * depth / 100 / 128;
            dl(d, DL_VERTEX2F((SC_X0 + x) * SUB, y * SUB));
        }
        dl(d, DL_END);
    }
    /* the dot: where the LFO is now, with a soft ring */
    {
        u32 c = cyc & 1u;
        int x = SC_X0 + (int)(((c << 16) + (phase >> 16)) * (u32)SC_W / 131072u);
        s32 w = cfw_lfo_eval(f->shape, phase, cyc);
        int y = cy - (w >> 8) * hh * depth / 100 / 128;
        dl(d, DL_COLOR_RGB(accent));
        dl(d, DL_COLOR_A(70));
        dl(d, DL_POINT_SIZE(9 * SUB));
        dl(d, DL_BEGIN(PRIM_POINTS));
        dl(d, DL_VERTEX2F(x * SUB, y * SUB));
        dl(d, DL_END);
        dl(d, DL_COLOR_A(255));
        dl(d, DL_COLOR_RGB(C_WHITE));
        dl(d, DL_POINT_SIZE(4 * SUB));
        dl(d, DL_BEGIN(PRIM_POINTS));
        dl(d, DL_VERTEX2F(x * SUB, y * SUB));
        dl(d, DL_END);
    }
    dl(d, DL_RESTORE_CONTEXT);
    /* the header: what it needs next, or how many parameters it moves on this track */
    u32 n = 0, hint = C_DIM, ft = first_track(u);
    char b[28], *p = b;
    for (u32 i = 0; i < MOD_TARGETS; i++)
        n += s->amt[l][ft][i] != 0;
    if (u->held) {
        hint = accent;
        if (u->last >= 0) {
            s32 am = s->amt[l][ft][u->last];
            p = cfw_put_str(p, tgt_name[u->last]);
            p = cfw_put_str(p, "  ");
            if (am) {
                if (am < 0)
                    *p++ = '-';
                p = cfw_put_uint(p, (u32)(am < 0 ? -am : am));
            } else {
                p = cfw_put_str(p, "off");
            }
            *p = 0;
        } else {
            cfw_put_str(p, "Touch a knob to map")[0] = 0;
        }
    } else if (!n) {
        cfw_put_str(p, "Hold Map + touch a knob")[0] = 0;
    } else {
        p = cfw_put_uint(p, n);
        cfw_put_str(p, n == 1 ? " target" : " targets")[0] = 0;
    }
    text(d, SC_X0 + 8, SC_Y0 + 3, accent, 0, l ? "LFO 2" : "LFO 1");
    text(d, SC_X1 - 8, SC_Y0 + 3, hint, OPT_RIGHTX, b);
}

static void draw_lfo(struct dlw *d, const struct mod_ui *u, const struct mod_save *s)
{
    const struct mod_lfo *f = &s->l[u->lfo];
    u32 accent = u->lfo ? C_LFO2 : C_LFO1;
    char b[20];
    cfw_key_tile(d, 0, u->lfo ? "LFO 2" : "LFO 1", 1);
    cfw_key_tile(d, 1, "Map", 1);
    draw_scope(d, f, u->lfo, s, u);
    for (u32 c = 0; c <= CELLS; c++) {
        int w = SC_W / 3, x0 = SC_X0 + (int)(c % 3) * w, y0 = 122 + (int)(c / 3) * 47, x1 = x0 + w - 3, y1 = y0 + 44;
        if (c == CELLS) { /* not a cell to move to: the tracks being edited */
            focus_name(u, b);
            text(d, x0 + 8, y0 + 3, C_DIM, 0, b);
            track_boxes(d, u, s, accent, x0 + 7, y0 + 25, y0 + 37);
            break;
        }
        int sel = u->cell == c, taken = sel && u->grab;
        if (sel)
            card(d, taken ? accent : C_CELL, x0, y0, x1, y1, 5);
        text(d, x0 + 8, y0 + 3, taken ? 0x000000 : C_DIM, 0, cell_name[c]);
        cell_value(f, c, b);
        text(d, x0 + 8, y0 + 21, taken ? 0x000000 : C_WHITE, 0, b);
    }
}

#define MX_X0 TILE_TEXT_X
#define MX_Y0 50
#define MX_W 57
#define MX_H 55
static void draw_matrix(struct dlw *d, const struct mod_ui *u, const struct mod_save *s)
{
    const struct mod_lfo *f = &s->l[u->lfo];
    const s8 *amt = s->amt[u->lfo][first_track(u)];
    u32 accent = u->lfo ? C_LFO2 : C_LFO1, dark = u->lfo ? C_LFO2_D : C_LFO1_D;
    char b[12];
    cfw_key_tile(d, 0, u->lfo ? "LFO 2" : "LFO 1", 1);
    cfw_key_tile(d, 1, "Scope", 1);
    text(d, MX_X0 + 2, 25, accent, 0, u->lfo ? "LFO 2" : "LFO 1");
    focus_name(u, b);
    text(d, MX_X0 + 62, 25, C_WHITE, 0, b);
    track_boxes(d, u, s, accent, 224, 29, 43);
    s32 out = (cfw_lfo_out(u->lfo) >> 8) * (s32)f->depth / 100; /* -128 .. 127 */
    for (u32 k = 0; k < KNOBS; k++) {
        int x0 = MX_X0 + (int)(k % 5) * MX_W, y0 = MX_Y0 + (int)(k / 5) * MX_H, x1 = x0 + MX_W - 3, y1 = y0 + MX_H - 3;
        s32 i = matrix_cell[k], a = i >= 0 ? amt[i] : 0;
        int israte = i < 0, touched = u->touch_cell == (s8)k, lit = a != 0 || israte, taken = touched && u->grab;
        if (touched) /* the highlighted cell: a white frame; taken: filled with the LFO's colour */
            card(d, C_WHITE, x0 - 1, y0 - 1, x1 + 1, y1 + 1, 4);
        card(d, taken ? accent : touched ? C_CELL : lit ? dark : C_CARD, x0, y0, x1, y1, 4);
        text(d, x0 + 4, y0 + 1, taken ? 0x000000 : lit || touched ? C_WHITE : C_DIM, 0, matrix_name[k]);
        char *p = b;
        if (israte) {
            if (f->sync)
                p = cfw_put_str(p, div_name[f->div]);
            else
                p = put_hz(p, f);
        } else if (a) {
            if (a < 0)
                *p++ = '-';
            p = cfw_put_uint(p, (u32)(a < 0 ? -a : a));
        }
        *p = 0;
        if (b[0])
            text(d, x0 + 4, y0 + 18, taken ? 0x000000 : israte ? C_WHITE : accent, 0, b);
        if (israte)
            continue;
        /* the amount around the middle, and a mark that moves with the modulation */
        int mid = (x0 + x1) / 2, half = (x1 - x0) / 2 - 4, ly = y1 - 7, bx = mid + a * half / 100;
        dl(d, DL_COLOR_RGB(C_GRID));
        dl(d, DL_LINE_WIDTH(1 * SUB));
        dl(d, DL_BEGIN(PRIM_LINES));
        dl(d, DL_VERTEX2F((mid - half) * SUB, ly * SUB));
        dl(d, DL_VERTEX2F((mid + half) * SUB, ly * SUB));
        if (a) {
            dl(d, DL_COLOR_RGB(accent));
            dl(d, DL_LINE_WIDTH(2 * SUB));
            dl(d, DL_VERTEX2F(mid * SUB, ly * SUB));
            dl(d, DL_VERTEX2F(bx * SUB, ly * SUB));
        }
        dl(d, DL_END);
        if (a) {
            dl(d, DL_COLOR_RGB(C_WHITE));
            dl(d, DL_POINT_SIZE(3 * SUB));
            dl(d, DL_BEGIN(PRIM_POINTS));
            dl(d, DL_VERTEX2F((mid + out * a * half / (128 * 100)) * SUB, ly * SUB));
            dl(d, DL_END);
        }
    }
}

int cfw_lfo_render(void *w)
{
    struct mod_ui *u = cfw_mod_ui();
    struct mod_save *s = cfw_mod_save_data();
    struct dlw d = {0};
    (void)w;
    fw_eve_begin();
    fw_eve_dlstart();
    dl(&d, DL_SAVE_CONTEXT);
    dl(&d, DL_VERTEX_FORMAT(4));
    dl(&d, DL_COLOR_RGB(0));
    dl(&d, DL_BEGIN(PRIM_RECTS));
    dl(&d, DL_VERTEX2F(0, 24 * SUB));
    dl(&d, DL_VERTEX2F(320 * SUB, 216 * SUB));
    dl(&d, DL_END);
    if (u && s) {
        if (u->view == VIEW_MATRIX)
            draw_matrix(&d, u, s);
        else
            draw_lfo(&d, u, s);
    }
    dl(&d, DL_RESTORE_CONTEXT);
    fw_eve_end();
    return 0;
}
#endif
