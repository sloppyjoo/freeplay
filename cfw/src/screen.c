/* Screen brightness and dim-when-idle (Play+ and Play RT units).
 *
 * Backstage "Screen brightness setting" and "Automatic sleep mode". Adds option rows to Menu > Settings,
 * built with the stock option-row widget (the one "Grid Brightness" uses), so they look and work like
 * the stock settings:
 *   Screen Brightness  100% / 75% / 50% / 25%   (applied as soon as it changes)
 *   Screen Dim         Off / 30 s / 2 min / 10 min
 * Dimming fades the backlight down to a glow when no pad, button, knob or encoder was touched for
 * that long, and any input fades it back up (the input still does its normal job).
 *
 * The backlight is the EVE's PWM (REG_PWM_DUTY 0..128). The firmware's display manager already
 * applies a wanted duty (+0x3c, with the flag +0x38) on its next pass, and nothing else in the
 * stock firmware changes it after boot, so the CFW only moves that wanted value. It runs from
 * the app's per-loop update (app vtable slot 1, main-loop context), and the input listener's
 * vtable is wrapped to note activity.
 *
 * Settings > Knob Touch  Normal / Turn Only (Backstage "Avoid accidental parameter switches by
 * touching knobs"). The input listener's slot 3 (+0x0c, FUN_600d9d6c -> FUN_600cf308) is the knob
 * *touch* event (id, state 0 touched / 1 released / 2 held): on touch the controller shows that
 * knob's parameter and makes it the current one (ctrl+0x240c). Turn Only drops those events, so
 * only turning a knob (slot 2, FUN_600cf1ec, which also selects the parameter) changes the page.
 *
 * The choices persist in /Projects/cfwSettings (8 bytes, next to the stock globalSettings),
 * read once at boot and written when you pick a value. Defaults (no file) = stock: 100 %, no dim,
 * knob touch normal (older files have 0 in that byte). */
#include "slice.h"

extern void fw_app_update(void *app);
extern void fw_menu_build_settings(void *ctx, void *menu);
extern void *fw_menu_option_init(void *item, void *menu, const char *label, u32 *val, u32 max,
                                 const char *const *labels, const std_function *on_change);
extern int fw_fn_manager_word(void *dst, const void *src, int op);
extern void *fw_operator_new(u32 size);
extern int fw_file_open(void *f, const char *path, u32 mode);
extern int fw_file_read(void *f, void *buf, u32 n);
extern int fw_file_write(void *f, const void *buf, u32 n);
extern int fw_file_close(void *f);
extern volatile u32 fw_millis;
extern void fw_in_pad(void *l, u32 a, u32 b, u32 c);
extern void fw_in_button(void *l, u32 a, u32 b, u32 c);
extern void fw_in_knob(void *l, u32 a, u32 b, u32 c);
extern void fw_in_track(void *l, u32 a, u32 b, u32 c);
extern void fw_in_encoder(void *l, u32 a, u32 b, u32 c);

#define SCREEN_MAGIC 0x53435233u /* "SCR3": brightness 0..9 (10 .. 100 %) */
#define CFG_MAGIC 0x43464732u    /* "CFG2" in the file: bri = 0..9 for 10 .. 100 % */
#define CFG_MAGIC_OLD 0x43464731u /* "CFG1" (CFW2-5: bri 0..3 = 100 / 75 / 50 / 25 %) */
#define CFG_PATH "/Projects/cfwSettings"
#define FA_READ 0x01
#define FA_WRITE_NEW 0x0a        /* FA_WRITE | FA_CREATE_ALWAYS, as the firmware's writers use */
#define FILE_OBJ_SIZE 0x70       /* firmware File wrapper; +0x68 = FatFs FIL* (0 = closed) */
#define DUTY_FULL 128
#define DUTY_DIM 6               /* a glow: still readable up close */
#define FADE_UP_MS 150           /* full range */
#define FADE_DOWN_MS 1200

#define BRI_STEPS 10 /* 10 % .. 100 % (index 9 = 100 %, the default) */
static const char *const bri_item[BRI_STEPS] = {"10%", "20%", "30%", "40%", "50%", "60%", "70%", "80%", "90%", "100%"};
static const u32 dim_ms[4] = {0, 30000, 120000, 600000};
static const char *const dim_item[4] = {"Off", "30 s", "2 min", "10 min"};

struct cfg_file {
    u32 magic;
    u8 bri, dim, ktouch, pad1;
};

static const char *const kt_item[2] = {"Normal", "Turn Only"};

/* ---- settings ----------------------------------------------------------------------------- */
static void file_obj_init(u8 *f)
{
    for (u32 i = 0; i < FILE_OBJ_SIZE; i++)
        ((volatile u8 *)f)[i] = 0;
}

static void cfg_load(struct cfw_screen *sc)
{
    u8 f[FILE_OBJ_SIZE] __attribute__((aligned(4)));
    struct cfg_file c;
    sc->dim = sc->ktouch = 0;
    sc->bri = BRI_STEPS - 1;
    file_obj_init(f);
    if (!fw_file_open(f, CFG_PATH, FA_READ))
        return;
    c.magic = 0;
    int n = fw_file_read(f, &c, sizeof c);
    if (n == (int)sizeof c && c.magic == CFG_MAGIC_OLD && c.bri < 4 && c.dim < 4) {
        static const u8 conv[4] = {9, 7, 4, 1}; /* CFW4 file: 100 / 75 / 50 / 25 % */
        c.bri = conv[c.bri];
        c.magic = CFG_MAGIC;
    }
    if (n == (int)sizeof c && c.magic == CFG_MAGIC && c.bri < BRI_STEPS && c.dim < 4) {
        sc->bri = c.bri;
        sc->dim = c.dim;
        sc->ktouch = c.ktouch < 2 ? c.ktouch : 0;
    }
    fw_file_close(f);
}

static void cfg_save(const struct cfw_screen *sc)
{
    u8 f[FILE_OBJ_SIZE] __attribute__((aligned(4)));
    struct cfg_file c;
    c.magic = CFG_MAGIC;
    c.bri = sc->bri;
    c.dim = sc->dim;
    c.ktouch = sc->ktouch;
    c.pad1 = 0;
    file_obj_init(f);
    if (!fw_file_open(f, CFG_PATH, FA_WRITE_NEW))
        return;
    fw_file_write(f, &c, sizeof c);
    fw_file_close(f);
}

/* Valid state in scratch RAM; the file is read once per power-up (scratch RAM is not cleared on
 * boot, so a warm reset keeps the cached copy, which always matches the file). */
static struct cfw_screen *screen(void)
{
    struct cfw_screen *sc = &cfw_ram.screen;
    if (sc->magic != SCREEN_MAGIC) {
        cfg_load(sc);
        sc->dimmed = 0;
        sc->last_input = fw_millis;
        sc->last_step = fw_millis;
        sc->kt_dropped = 0;
        sc->magic = SCREEN_MAGIC;
    }
    return sc;
}

/* ---- backlight ------------------------------------------------------------------------------ */
static void *disp_of(void *app)
{
    void *ui = PTR((char *)app + APP_LISTENER, LISTENER_UI);
    void *scr = ui ? PTR(ui, UI_MENU_SCREEN) : 0;
    return scr ? PTR(scr, SCR_DISP) : 0;
}

static void screen_tick(void *app)
{
    void *disp = disp_of(app);
    if (!disp)
        return;
    struct cfw_screen *sc = screen();
    u32 now = fw_millis, dt = now - sc->last_step;
    sc->last_step = now;
    u32 target = (sc->bri < BRI_STEPS ? sc->bri + 1 : BRI_STEPS) * DUTY_FULL / BRI_STEPS;
    sc->dimmed = sc->dim && now - sc->last_input >= dim_ms[sc->dim & 3];
    if (sc->dimmed && target > DUTY_DIM)
        target = DUTY_DIM;
    u32 cur = U32(disp, DISP_PWM_WANT);
    if (cur > DUTY_FULL)
        cur = DUTY_FULL;
    if (cur == target)
        return;
    u32 step = (dt * DUTY_FULL) / (target > cur ? FADE_UP_MS : FADE_DOWN_MS);
    if (step < 1)
        step = 1;
    if (target > cur)
        cur = target - cur > step ? cur + step : target;
    else
        cur = cur - target > step ? cur - step : target;
    U32(disp, DISP_PWM_WANT) = cur;
    U32(disp, DISP_PWM_DIRTY) = 1;
}

/* Before any CFW state is used in this boot: zero cfw_ram if it is from another boot or another
 * CFW build (see slice.h). Called from the main-loop hook and every input hook: all CFW code
 * (menus, editors, playback) runs after one of them. */
void cfw_ram_check(void)
{
    u32 now = fw_millis;
    if (cfw_ram.layout != CFW_RAM_LAYOUT || now < cfw_ram.alive_ms) {
        for (u32 i = 0; i < sizeof cfw_ram; i++)
            ((volatile u8 *)&cfw_ram)[i] = 0;
        cfw_ram.layout = CFW_RAM_LAYOUT;
    }
    cfw_ram.alive_ms = now;
}

#ifdef CFW_MOD
/* The voice layer's hooks (mod.c, past the image end). They run in the audio interrupt, also while the
 * firmware boots, before the main loop has checked cfw_ram: only a checked block of this boot counts. */
extern void fw_reader_gain(void *reader);
extern s32 fw_tgt_get(void *target);
static inline int mod_live(void)
{
    return cfw_ram.layout == CFW_RAM_LAYOUT && cfw_ram.view.ext == 1 && fw_millis >= cfw_ram.alive_ms;
}

void cfw_reader_gain(void *reader)
{
    fw_reader_gain(reader);
    if (mod_live())
        cfw_mod_block(reader);
}

s32 cfw_tgt_note(void *target)
{
    s32 v = fw_tgt_get(target);
    return mod_live() ? cfw_mod_note(target, v) : v;
}
#endif

/* app vtable slot 1: called once per main-loop pass */
void cfw_app_update(void *app)
{
    cfw_ram_check();
    fw_app_update(app);
    screen_tick(app);
#ifdef CFW_STARTEND
    if (cfw_ext_ok())
        cfw_startend_tick(app);
#endif
#ifdef CFW_MOD
    if (cfw_ext_ok())
        cfw_mod_tick(app);
#endif
#ifdef CFW_PAGES
    if (cfw_ext_ok())
        cfw_pages_tick(app);
#endif
#ifdef CFW_COLORS
    if (cfw_ext_ok())
        cfw_colors_tick(app);
#endif
}

/* ---- input activity: listener vtable wrappers (arguments pass through untouched) ----------- */
static void activity(void)
{
    cfw_ram_check();
    cfw_ram.screen.last_input = fw_millis;
}

#ifdef CFW_COLORS
#define POINTS(slot, a, b)                                                                             \
    if (cfw_points_input(slot, a, b) ||                                                            \
        (cfw_ext_ok() && (cfw_lfo_input(slot, a, b) || cfw_colors_input(slot, a, b) ||             \
                          cfw_pages_input(slot, a, b))))                                           \
    return
#elif defined(CFW_POINTS)
#define POINTS(slot, a, b) if (cfw_points_input(slot, a, b)) return
#else
#define POINTS(slot, a, b) do { } while (0)
#endif

void cfw_in_pad(void *l, u32 a, u32 b, u32 c) { activity(); POINTS(0, a, b); fw_in_pad(l, a, b, c); }
void cfw_in_button(void *l, u32 a, u32 b, u32 c)
{
    activity();
    POINTS(1, a, b);
#ifdef CFW_TAP
    if (cfw_tap_key((char *)l - APP_LISTENER, a, b))
        return;
#endif
    fw_in_button(l, a, b, c);
}
void cfw_in_knob(void *l, u32 a, u32 b, u32 c) { activity(); POINTS(2, a, b); fw_in_knob(l, a, b, c); }
/* knob touch (a = knob id, b = 0 touched / 1 released / 2 held) */
void cfw_in_track(void *l, u32 a, u32 b, u32 c)
{
    activity();
#ifdef CFW_MOD
    if (cfw_ext_ok() && cfw_lfo_input(3, a, b)) /* Map held + touch a knob */
        return;
#endif
    struct cfw_screen *sc = screen();
    u32 m = a < 32 ? 1u << a : 0;
    if (b == 0 && sc->ktouch && m) {
        sc->kt_dropped |= m;
        return;
    }
    if (m && (sc->kt_dropped & m)) {
        if (b == 1)
            sc->kt_dropped &= ~m;
        return; /* hold / release of a dropped touch */
    }
    fw_in_track(l, a, b, c);
}
void cfw_in_encoder(void *l, u32 a, u32 b, u32 c)
{
    activity();
    POINTS(4, a, b);
#ifdef CFW_PAGES
    if (cfw_ram.view.shift && cfw_ext_ok()) { /* Shift + encoder moves further (pages.c) */
        cfw_encoder_shift(l, a, b, c);
        return;
    }
#endif
    fw_in_encoder(l, a, b, c);
}

#ifdef CFW_COLORS
/* LED object vtable +4: set one pad's colour. A plain trig (the stock trig colour) of a sample in a
 * coloured pool folder gets the folder's colour (colors.c); everything else passes untouched. */
extern void fw_led_set(void *leds, u32 led, const u8 *rgb, const u8 *rgb2, int mode);
extern const u8 fw_led_trig_color[];
void cfw_led_set(void *leds, u32 led, const u8 *rgb, const u8 *rgb2, int mode)
{
    if (rgb == fw_led_trig_color && cfw_ext_ok()) {
        const u8 *k = cfw_colors_pad(led);
        if (k)
            rgb = k;
    }
#ifdef CFW_MOD
    if (cfw_ext_ok()) {
        const u8 *k = cfw_lfo_led(led); /* the track being edited blinks on its select pad */
        if (k) {
            rgb = k;
            rgb2 = 0;
            mode = 0;
        }
    }
#endif
    fw_led_set(leds, led, rgb, rgb2, mode);
}
#endif

/* ---- menus ------------------------------------------------------------------------------------ */
/* A stock option row: label on the left, labels[*val] on the right; the firmware changes *val
 * (0..max) itself and then runs on_change (capture = ctx). */
void cfw_add_option(void *ctx, void *menu, const char *label, u32 *val, u32 max, const char *const *labels,
                    void (*on_change)(void *const *))
{
    std_function fn = {ctx, 0, fw_fn_manager_word, on_change};
    fw_menu_option_init(fw_operator_new(OPTION_SIZE), menu, label, val, max, labels, &fn);
}

static void changed(void *const *f)
{
    (void)f;
    struct cfw_screen *sc = screen();
    sc->last_input = fw_millis;
    cfg_save(sc);
}

void cfw_menu_build_settings(void *ctx, void *menu)
{
    fw_menu_build_settings(ctx, menu);
    struct cfw_screen *sc = screen();
    cfw_add_option(ctx, menu, "Screen Brightness", &sc->bri, BRI_STEPS - 1, bri_item, changed);
    cfw_add_option(ctx, menu, "Screen Dim", &sc->dim, 3, dim_item, changed);
    cfw_add_option(ctx, menu, "Knob Touch", &sc->ktouch, 1, kt_item, changed);
}
