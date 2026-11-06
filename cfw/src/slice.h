/* Shared between slice.c (menu + edit) and sliceview.c (waveform preview). */
#ifndef SLICE_H
#define SLICE_H

#include "cfw.h"
#include "target.h"

/* ---- CFW state in scratch RAM (cfw_ram, see symbols.ld) ---------------------------------
 * The scratch RAM is not initialised at boot, so every part carries a magic word. */
#define CFW_MAGIC 0x43465731u  /* "CFW1": selection snapshot taken when the menu was opened */
#define VIEW_MAGIC 0x56494557u /* "VIEW": the slice-preview fields below are valid */

struct cfw_view {
    u32 magic;
    s32 slot;     /* display slot of the preview widget, -1 = none */
    void *widget; /* the widget object in that slot (ownership check before any use) */
    void *disp;   /* display manager it lives in */
    void *ctx;    /* menu controller (selection, sample pool) */
    u32 cursor;   /* highlighted slice count, 0..5 (Start/End panel: hash of what it shows) */
    void *pts;    /* struct cfw_pts (sliceview.c), heap, allocated once; 0 = none yet */
    void *se_obj; /* step / track the Sample Start / End page shows (set by its value formatter) */
    void *se_desc; /* the Start parameter's descriptor (the lower of the two formatter "self"s) */
    s16 se_val[2]; /* Start / End as the page shows them (what a step without its own value plays) */
    u8 mode;      /* 1 while the Slice panel shows the Points editor (input goes to the editor) */
    u8 wmode;     /* what the widget was created for: 0 none, 1 Slice panel, 2 Start/End */
    u8 shift;     /* Shift (button 10) held */
    u8 ext;       /* cfw_ext_ok() cache: 0 not checked, 1 ok, 2 missing */
};
#define WMODE_SLICE 1
#define WMODE_STARTEND 2
#define WMODE_PAGE 3 /* knob-page visuals (pages.c) */
#define WMODE_POOL 4 /* folder colours in the sample pool view (colors.c) */
#define WMODE_LFO 5  /* Menu > LFO (lfoui.c) */
#define WMODE_SLICER 6 /* Menu > Samples > Slice (slicer.c); WMODE_SLICE is its fallback without the ext code */

#define RAND_MAGIC 0x524e4431u /* "RND1": the randomizer fields below are valid */
struct cfw_rand {
    u32 magic;
    u32 seed;   /* xorshift32 state */
    void *undo; /* heap record of the values before the last randomize (allocated once) */
};

struct cfw_tap {
    u32 last; /* fw_millis of the last tap */
    u32 ema;  /* smoothed tap interval, ms */
    u32 taps; /* 0 = no series, 1 = one tap, 2 = interval known */
};


#define DELPROJ_MAGIC 0x44454c50u /* "DELP" */
struct cfw_delproj_ref {
    u32 magic;
    void *p; /* struct delproj (delproj.c), heap, allocated once */
};

struct cfw_screen {
    u32 magic;      /* SCREEN_MAGIC once the settings file was read */
    u32 bri, dim;   /* brightness / dim-timeout choice (0..3); u32: stock option rows bind an int */
    u32 ktouch;     /* Knob Touch: 0 Normal, 1 Turn Only (touch events dropped) */
    u32 last_input; /* fw_millis of the last pad/button/knob/encoder event */
    u32 last_step;  /* fw_millis of the last fade step */
    u8 dimmed, kpad[3];
    u32 kt_dropped; /* knob ids whose touch press was dropped (their release is dropped too) */
};

#ifdef CFW_USB
#define USB_MAGIC 0x55534231u /* "USB1": the USB storage fields below are valid */
struct cfw_usb {     /* 64 bytes: cfw_ram is 256 */
    u32 magic;
    u8 mode;         /* USB_OFF / USB_ON / USB_LEAVING / USB_NOCARD / USB_BUSY (usbdisk.c) */
    u8 confirm;      /* one Back was held back while the computer still had the card */
    u8 back_pending; /* the card is being handed back because of Back: leave the submenu after */
    u8 remounted;    /* the last hand-back found the card reformatted (FatFs mounts it afresh) */
    u16 key;         /* what the status panel shows now (re-rendered when it changes) */
    s16 slot;        /* status panel widget: display slot, -1 = none */
    u32 confirm_ms;  /* fw_millis of that Back */
    u32 seen_ms;     /* fw_millis at the last main-loop pass (time going back = a reset) */
    u32 fp;          /* fingerprint of the volume's boot sector when the card was handed over */
    void *ctx;       /* menu controller (engine, HAL), kept for the hand-back */
    void *widget;    /* status panel widget object and its display manager */
    void *disp;
    u32 pads[5];     /* presses dropped while in storage mode (their releases are dropped too) */
    u32 buttons;
    u32 tracks;
};
#endif

/* cfw_ram is outside every RAM range the firmware initialises: it survives warm resets and
 * firmware updates (and holds noise after power-up). The header below lets cfw_ram_check()
 * (screen.c) zero the whole block before first use after any reboot, and after a CFW update that
 * changed the layout. Bump CFW_RAM_VERSION whenever fields move. Heap pointers kept here (song,
 * delproj, randomize undo, slice points) are only valid within one boot. */
#define CFW_RAM_VERSION 8u
#define CFW_RAM_LAYOUT (0xCF000000u | (CFW_RAM_VERSION << 16) | (u32)sizeof(struct cfw_state))
struct cfw_state {
    u32 layout;              /* CFW_RAM_LAYOUT once checked in this boot */
    u32 alive_ms;            /* fw_millis at the last check: time going backwards = a reboot */
    u32 magic;
    u32 count;               /* steps in the snapshot */
    u32 bits[NUM_TRACKS][2]; /* bit s of track t: step s was held */
    struct cfw_view view;
    struct cfw_rand rand;    /* Play+ synth randomizer (synthrand.c) */
    struct cfw_screen screen; /* brightness / dim (screen.c) */
    struct cfw_tap tap;       /* Menu > Tap Tempo (tap.c) */
    struct cfw_delproj_ref delproj; /* File > Delete Project (delproj.c) */
    void *pages;              /* knob-page visuals' state (pages.c), heap, allocated once per boot */
#ifdef CFW_USB
    struct cfw_usb usb;      /* USB storage mode (usbdisk.c) */
#endif
};
void cfw_ram_check(void);
void cfw_add_option(void *ctx, void *menu, const char *label, u32 *val, u32 max, const char *const *labels,
                    void (*on_change)(void *const *));
#ifdef CFW_USB
void cfw_usb_menu_sync(void *scr, int leaving);
void cfw_usb_tick(void);
int cfw_usb_swallow(u32 kind, u32 id, u32 state); /* kind = input listener slot (0 pad .. 4 encoder) */
void cfw_menu_build_usb(void *ctx, void *menu);
#endif
extern struct cfw_state cfw_ram;
_Static_assert(sizeof(struct cfw_state) <= 256, "cfw_state must fit the 256-byte scratch RAM window");

/* The steps a Slice action would change: trigged steps on sample tracks, taken from the
 * held-steps snapshot, or from the Copy clipboard when nothing was held. */
struct cfw_sel {
    u32 bits[NUM_TRACKS][2];
    u32 per_track[NUM_TRACKS];
    u32 total, skipped, tracks;
    int copied;
};
void cfw_collect(void *ctx, struct cfw_sel *out);                      /* sample-track trigs */
void cfw_collect_trigs(void *ctx, struct cfw_sel *out, int sample_only); /* any trig if !sample_only */
void cfw_edit_begin(void *ctrl, const char *label);
void cfw_refresh_pads(void *ctrl);
void *cfw_step_at(void *ctx, int track, int step);

/* Number of slices for Slice menu item `item` (0..4 -> x4..x64, 5 -> one per step). */
#define SLICE_ITEM_STEPS 5
#define SLICE_ITEMS 6
u32 cfw_item_slices(u32 item, u32 steps);

char *cfw_put_str(char *p, const char *s);
char *cfw_put_uint(char *p, u32 v);

/* Audio-IRQ edit lock, nested like the stock code's (counter fw_edit_lock_depth). */
void cfw_edit_lock(void);
void cfw_edit_unlock(void);

/* Append an action item whose std::function captures ctx and calls inv(&capture). */
void cfw_add_action(void *ctx, void *menu, const char *label, void (*inv)(void *const *));
void cfw_add_actions(void *ctx, void *menu, const char *const *labels, void (*const *inv)(void *const *), u32 n);

void cfw_menu_build_slice(void *ctx, void *menu);
void cfw_apply_slices(void *ctx, u32 n, const u16 *cuts); /* cuts: n-1 points, or 0 for equal */
#ifdef CFW_POINTS
/* Slice > To Folder (export.c, CFW_EXPORT): the n slices (0 = one per chosen step) or the slices
 * between the hand-placed points (cuts, n - 1 of them) as samples in a new pool folder */
void cfw_export_slices(void *ctx, u32 n, const u16 *cuts, int replace); /* replace: the chosen steps
                                                                         then play their slices */

/* a pool sample (sliceview.c): planar int16 channels */
struct sample {
    const s16 *ch[2];
    u32 channels, frames;
    float rate;
    const char *name;
};
int cfw_sample_get(void *ctx, u32 idx, struct sample *out);
int cfw_pool_sample(void *pool, u32 idx, struct sample *out);
int cfw_steps_sample(void *ctx, struct cfw_sel *sl, struct sample *smp, int *have); /* -> track or -1 */

/* Slice > Points: hand-placed points (sliceview.c), heap, allocated once */
#define PTS_MAX 63
struct cfw_pts {
    u32 magic;
    u16 cursor, view0;
    u8 zoom, count, apply_pts, pad; /* apply_pts: the Apply choice is for the points; pad: Start / End key (startend.c) */
    u16 eat;                     /* buttons (bit = id) whose press the editor took: drop their hold / release */
    u16 p[PTS_MAX];
};
struct cfw_pts *cfw_pts(void);
u32 cfw_pts_span(const struct cfw_pts *t);
void cfw_pts_keep_visible(struct cfw_pts *t);
void cfw_pts_move(struct cfw_pts *t, s32 d, u32 unit);
void cfw_pts_zoom(struct cfw_pts *t, s32 d);
int cfw_ext_ok(void); /* the ext region (code past the image end) is there and intact */

/* panel helpers shared with startend.c (sliceview.c) */
struct dlw;
extern const void *const cfw_wave_vt[10];
struct cfw_view *cfw_view_state(void);
int cfw_view_alive(struct cfw_view *v);
void cfw_axis(struct dlw *d, int x, int w, int cy);
/* White label tiles for the two keys left of the screen: the stock soft-key bar's tiles stood on
 * their side, flush with the left edge (x 0..TILE_W), the top key's over the upper half of the panel,
 * the bottom key's over the lower half; the label reads top to bottom (font 12 turned 90 degrees).
 * Grey when the key does nothing now. */
#define TILE_W 22
#define TILE_TOP_Y0 26
#define TILE_TOP_Y1 118
#define TILE_BOT_Y0 124
#define TILE_BOT_Y1 216
#define TILE_TEXT_X 28 /* first text column right of the tiles */
#define ROW1_Y 24      /* FONT_SMALL text rows of a panel: top, bottom */
#define ROW2_Y 192
void cfw_key_tile(struct dlw *d, int bottom, const char *label, int on);
void cfw_draw_wave(struct dlw *d, const struct sample *smp, int x, int wd, int cy, int hh, u32 f0, u32 fspan,
                   int px_used);
void cfw_startend_tick(void *app);  /* startend.c */
void cfw_stock_widgets(void *pscr, void *disp, u32 op); /* show / hide a knob page's six widgets */
void cfw_pages_tick(void *app);     /* pages.c (CFW_PAGES) */
int cfw_pages_render(void *w);
void **cfw_pages_colors(int alloc); /* pages.c: where the folder colours' state pointer lives */
int cfw_pages_input(u32 kind, u32 id, u32 val);
void cfw_encoder_shift(void *l, u32 a, u32 b, u32 c); /* pages.c: Shift pickers */
void cfw_colors_tick(void *app);    /* colors.c (CFW_COLORS) */
int cfw_colors_input(u32 kind, u32 id, u32 val);
int cfw_colors_render(void *w);
const u8 *cfw_colors_pad(u32 led);
int cfw_startend_render(void *w);
int cfw_startend_input(u32 kind, u32 id, u32 val); /* listener slot, a, b: 1 = swallowed */
int cfw_points_input(u32 kind, u32 id, u32 val); /* listener slot, a, b: 1 = swallowed */
#endif
#ifdef CFW_TAP
void cfw_inv_tap(void *const *f); /* Menu > Tap Tempo (tap.c) */
void cfw_tap(void *app);
int cfw_tap_key(void *app, u32 id, u32 val);
#endif
/* the knob page the parameter screen shows: its top title ("Tempo", "Sample Start", ...), or 0
 * when another screen or a menu is up (sliceview.c); pointers checked */
#ifdef CFW_MOD
/* the voice layer (mod.c) and the LFO panel (lfoui.c) */
#define MOD_TRACKS 8   /* the sample tracks */
#define MOD_TARGETS 14
enum { LFO_SINE, LFO_TRI, LFO_UP, LFO_DOWN, LFO_SQUARE, LFO_RANDOM, LFO_DRIFT, LFO_SHAPES };
#define LFO_RATES 80
#define LFO_DIVS 12
struct mod_lfo {
    u8 shape, sync, rate, div, depth, retrig, pad[2];
};
struct mod_save { /* the file, one per project */
    u32 magic;
    u8 sus[MOD_TRACKS], rel[MOD_TRACKS]; /* Sustain (0 = Off), Release: percent */
    struct mod_lfo l[2];
    s8 amt[2][MOD_TRACKS][MOD_TARGETS]; /* LFO, track, parameter: -100 .. 100, 0 = not mapped */
};
struct mod_ui {
    u8 view, lfo, cell, grab;           /* the LFO panel */
    u8 held, used, opened;              /* Map key down; a knob was used meanwhile; panel seen open */
    s8 last;                            /* the parameter the shortcut touched last, -1 none */
    s8 touch_cell;                      /* Matrix: the knob touched last, -1 none */
    u8 focus, pads;                     /* the tracks being edited; select pads held (bit = track) */
    u8 blink, lit;                      /* select pads: blue now; the tracks that were last drawn */
    u32 blink_ms;
    u16 seen, pend, touched;            /* during this hold: parameters acted on, to free on release; knobs whose touch is ours */
    u32 drawn_ms;
    void *scr; /* the menu screen showing the panel */
};
struct mod_save *cfw_mod_save_data(void);
struct mod_ui *cfw_mod_ui(void);
void cfw_mod_touch(void);          /* the settings changed: saved a moment later */
u32 cfw_mod_track(void);           /* the track of the last step pad touched */
void cfw_mod_tick(void *app);
void cfw_mod_block(void *reader);  /* audio interrupt */
s32 cfw_mod_note(void *target, s32 value); /* audio interrupt */
s32 cfw_lfo_eval(u32 shape, u32 phase, u32 cyc);
s32 cfw_lfo_out(u32 l);
s32 cfw_mod_live(u32 i);           /* the LFOs' offset now, for the knob pages */
int cfw_mod_mapped(u32 i);
void cfw_lfo_where(u32 l, u32 *phase, u32 *cyc);
float cfw_lfo_hz(u32 rate);
float cfw_env_gain(float t, float a, float d, float s, float r);
int cfw_voice_heads(const struct sample *smp, u32 *pos, int max);
float cfw_voice_progress(void);
u32 cfw_heads_key(const struct sample *smp);
void cfw_draw_heads(struct dlw *d, const struct sample *smp, int x, int wd, int y0, int y1, u32 v0, u32 sp);
void **cfw_pages_mod(int alloc);   /* pages.c: where the state pointer lives */
void **cfw_pages_cc(int alloc);
void **cfw_pages_slicer(int alloc);
void cfw_slicer_enter(void *scr);  /* slicer.c */
void cfw_slicer_tick(void *app);
int cfw_slicer_input(u32 kind, u32 id, u32 val);
int cfw_slicer_render(void *w);
const u8 *cfw_slicer_led(u32 led);
int cfw_slices_to_folder(void *ctx, const struct sample *smp, u32 n, const u16 *cuts, s16 *idx); /* export.c */
void cfw_pts_toggle(struct cfw_pts *t);      /* sliceview.c */
void cfw_pts_delete_near(struct cfw_pts *t);
void cfw_cc_append(void *ctx, void *menu, u32 channel); /* ccpreset.c */
void cfw_menu_build_lfo(void *ctx, void *menu);
void cfw_menu_build_matrix(void *ctx, void *menu);
void cfw_lfo_enter(int matrix);    /* which view the panel opens on */
int cfw_lfo_render(void *w);
int cfw_lfo_input(u32 kind, u32 id, u32 val);
const u8 *cfw_lfo_led(u32 led);    /* a select pad's colour while the panel is up, else 0 */
void cfw_lfo_blink(void *ctrl);    /* main loop */
void cfw_lfo_opened(void *ctrl);   /* the panel came up: tracks already selected join the LFO */
int cfw_lfo_due(void);             /* time to redraw the panel */
void cfw_lfo_close_if_left(void *scr); /* sliceview.c */
#endif
const char *cfw_page_title(void *app);
void *cfw_param_screen(void *ui); /* the current screen if it shows knob pages, else 0 */
int cfw_str_is(const char *s, const char *prefix); /* s starts with prefix */
int cfw_ptr_ok(const void *p);                     /* RAM or flash, 4-byte aligned or not */

#endif
