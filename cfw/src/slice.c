/* On-device sample slicing for Polyend Play / Play+ (CFW patch).
 *
 * Adds Menu > Samples > Slice > x4 / x8 / x16 / x32 / x64 / = Selection.
 * For the chosen sample-track steps, in step order per track, step k gets
 *   Sample Start = round(j * 32767 / N), Sample End = round((j + 1) * 32767 / N), j = k mod N
 * as parameter locks (N = number of steps on that track for "= Selection").
 *
 * Which steps: the Play's step selection only exists while step pads are held, so
 *   1. the steps held when the Menu was opened (snapshot taken by the root-menu builder hook), or
 *   2. if none were held, the steps last copied with Copy (the clipboard selection).
 * Only steps that have a trig on a sample track are changed.
 *
 * The edit follows the stock recipe (FUN_600d08c4): undo snapshot, project dirty flags,
 * audio-IRQ edit lock around each step write, pad refresh, toast.
 *
 * Hooks (data-only, see target.json): root menu builder literal, Samples builder literal,
 * firmware version string literal. */
#include "slice.h"

/* ---- firmware imports (addresses in cfw/targets/<t>/symbols.ld) ----------------------- */
extern void *fw_operator_new(u32 size);
extern void *fw_menu_submenu_init(void *item, void *parent, const char *label, void *builder, u32 adj);
extern void *fw_menu_action_init(void *item, void *menu, const char *label, std_function *fn, u32 flag);
extern void fw_menu_build_root(void *ctx, void *menu);
extern void fw_menu_build_samples(void *ctx, void *menu);
extern void fw_menu_close(void *ctx);
extern int fw_fn_manager_word(void *dst, const void *src, int op);
extern void fw_undo_push(void *undo, const char *label);
extern void fw_toast_show(void *toast, const char *msg);
extern void fw_audio_irq_off(void);
extern void fw_audio_irq_on(void);
extern volatile s32 fw_edit_lock_depth;

void cfw_menu_build_samples(void *ctx, void *menu);

/* The boot screen and Settings > Version show this; it lives in the code cave, so seeing it
 * on the device proves the cave was flashed. */
const char cfw_version[] = CFW_VERSION_STRING;

#ifdef CFW_GUARDED_ENTRY
/* Images whose CFW code lives in appended HEX records (no in-place cave) enter through a tiny
 * in-place trampoline that checks this word first. If the appended records were not flashed the
 * word reads as erased flash (0xFFFFFFFF) and the stock root builder runs instead. */
#define CFW_FLASH_MAGIC 0x43465731u
const u32 cfw_magic = CFW_FLASH_MAGIC;
void cfw_menu_build_root(void *ctx, void *menu);

__attribute__((naked, used, section(".text.cfw_tramp_root"))) void cfw_tramp_root(void)
{
    __asm volatile(
        "ldr r2, 1f\n"
        "ldr r3, 2f\n"
        "ldr r2, [r2]\n"
        "cmp r2, r3\n"
        "ite eq\n"
        "ldreq r3, 3f\n"
        "ldrne r3, 4f\n"
        "bx r3\n"
        ".p2align 2\n"
        "1: .word cfw_magic\n"
        "2: .word 0x43465731\n"
        "3: .word cfw_menu_build_root\n"
        "4: .word fw_menu_build_root\n");
}
#endif

/* ---- helpers --------------------------------------------------------------------------- */
static void *step_at(void *sel, int track, int step)
{
    void *pv = PTR(sel, SEL_PVIEW);
    void *trk = VFN(pv, 0, void *, int)(pv, track);
    return VFN(trk, 0, void *, int)(trk, step);
}

void cfw_edit_lock(void)
{
    if (fw_edit_lock_depth == 0)
        fw_audio_irq_off();
    fw_edit_lock_depth++;
}

void cfw_edit_unlock(void)
{
    fw_edit_lock_depth--;
    if (fw_edit_lock_depth == 0)
        fw_audio_irq_on();
}

/* Unsigned division without libgcc (numbers here are tiny). */
static u32 udiv(u32 n, u32 d)
{
    u32 q = 0, bit = 1;
    if (d == 0)
        return 0;
    while (d < n && !(d & 0x80000000u)) {
        d <<= 1;
        bit <<= 1;
    }
    while (bit) {
        if (n >= d) {
            n -= d;
            q |= bit;
        }
        d >>= 1;
        bit >>= 1;
    }
    return q;
}

/* round(j * PARAM_POS_MAX / n) */
static s32 slice_pos(u32 j, u32 n)
{
    return (s32)udiv(2u * j * PARAM_POS_MAX + n, 2u * n);
}

char *cfw_put_str(char *p, const char *s)
{
    while (*s)
        *p++ = *s++;
    return p;
}

char *cfw_put_uint(char *p, u32 v)
{
    char tmp[10];
    int n = 0;
    do {
        u32 q = udiv(v, 10);
        tmp[n++] = (char)('0' + (v - q * 10));
        v = q;
    } while (v);
    while (n)
        *p++ = tmp[--n];
    return p;
}

static void pairs_to_bits(const u8 *pairs, u32 bits[NUM_TRACKS][2])
{
    u32 n = U32(pairs, PAIRS_COUNT);
    if (n > PAIRS_MAX)
        n = 0; /* never trust a corrupt count */
    for (u32 i = 0; i < n; i++) {
        s32 t = (s8)pairs[2 * i], s = (s8)pairs[2 * i + 1];
        if (t >= 0 && t < NUM_TRACKS && s >= 0 && s < NUM_STEPS)
            bits[t][s >> 5] |= 1u << (s & 31);
    }
}

/* ---- menu hooks ------------------------------------------------------------------------ */

#define MENU_ITEM_LIST 0x28  /* std::vector: begin, end, capacity end */
#define MENU_LABEL_LIST 0x34
#define ROOT_LFO_ROW 2       /* File, Samples, LFO, Matrix, ... */

/* Replaces the root "Menu" builder. Runs before the menu screen is shown when the menu opens,
 * and again when navigating back to the root (then the menu screen is already current). */
void cfw_menu_build_root(void *ctx, void *menu)
{
    void *ui = PTR(ctx, MENU_CTX_UI);
    if (PTR(ui, UI_CUR_SCREEN) != PTR(ui, UI_MENU_SCREEN)) {
        void *sel = (char *)PTR(ctx, MENU_CTX_CTRL) + CTRL_SEL;
        struct cfw_state *st = &cfw_ram;
        for (int t = 0; t < NUM_TRACKS; t++)
            st->bits[t][0] = st->bits[t][1] = 0;
        pairs_to_bits(PTR(sel, SEL_PAIRS), st->bits);
        st->count = U32(PTR(sel, SEL_PAIRS), PAIRS_COUNT);
        st->magic = CFW_MAGIC;
    }
    fw_menu_build_root(ctx, menu);
#ifdef CFW_MOD
    if (cfw_ext_ok()) {
        void *lfo = fw_operator_new(SUBMENU_SIZE);
        fw_menu_submenu_init(lfo, menu, "LFO", (void *)cfw_menu_build_lfo, 0);
        void *mx = fw_operator_new(SUBMENU_SIZE);
        fw_menu_submenu_init(mx, menu, "Matrix", (void *)cfw_menu_build_matrix, 0);
        /* up the list, under Samples: the menu keeps its items and their labels in two parallel lists */
        for (u32 off = MENU_ITEM_LIST; off <= MENU_LABEL_LIST; off += MENU_LABEL_LIST - MENU_ITEM_LIST) {
            void **a = PTR(menu, off), **e = PTR(menu, off + 4);
            u32 n = (u32)(e - a);
            if (n >= ROOT_LFO_ROW + 2) {
                void *x = a[n - 2], *y = a[n - 1];
                for (u32 i = n - 1; i >= ROOT_LFO_ROW + 2; i--)
                    a[i] = a[i - 2];
                a[ROOT_LFO_ROW] = x;
                a[ROOT_LFO_ROW + 1] = y;
            }
        }
    }
#endif
#ifdef CFW_RUNTIME_SAMPLES_HOOK
    /* No flash literal patch for the Samples builder on this image: retarget the freshly built
     * Samples submenu item instead (its pointer-to-member builder at +0x44/+0x48). */
    for (void **it = PTR(menu, MENU_ITEMS_BEGIN); it != PTR(menu, MENU_ITEMS_END); it++) {
        void *item = *it;
        if (item && U8(item, ITEM_TYPE) == 0 && PTR(item, SUBMENU_BUILDER) == (void *)fw_menu_build_samples &&
            U32(item, SUBMENU_BUILDER + 4) == 0)
            PTR(item, SUBMENU_BUILDER) = (void *)cfw_menu_build_samples;
    }
#endif
}

#ifdef CFW_MOD
/* MIDI > CC Mapping > (channel): stock rows, then Load / Save Preset (ccpreset.c) */
extern void fw_menu_build_cc(void *ctx, void *menu, u32 channel);
void cfw_menu_build_cc(void *ctx, void *menu, u32 channel)
{
    fw_menu_build_cc(ctx, menu, channel);
    if (cfw_ext_ok())
        cfw_cc_append(ctx, menu, channel);
}
#endif

static void do_slice(void *ctx, u32 n);

static void inv_x4(void *const *f) { do_slice(f[0], 4); }
static void inv_x8(void *const *f) { do_slice(f[0], 8); }
static void inv_x16(void *const *f) { do_slice(f[0], 16); }
static void inv_x32(void *const *f) { do_slice(f[0], 32); }
static void inv_x64(void *const *f) { do_slice(f[0], 64); }
static void inv_sel(void *const *f) { do_slice(f[0], 0); }

u32 cfw_item_slices(u32 item, u32 steps)
{
    return item < SLICE_ITEM_STEPS ? 4u << item : steps;
}

void cfw_add_action(void *ctx, void *menu, const char *label, void (*inv)(void *const *))
{
    std_function fn = {ctx, 0, fw_fn_manager_word, inv};
    void *item = fw_operator_new(ACTION_SIZE);
    fw_menu_action_init(item, menu, label, &fn, 0);
    fn.manager(&fn, &fn, 3); /* destroy the local copy, as the stock builders do */
}

void cfw_add_actions(void *ctx, void *menu, const char *const *labels, void (*const *inv)(void *const *), u32 n)
{
    for (u32 i = 0; i < n; i++)
        cfw_add_action(ctx, menu, labels[i], inv[i]);
}

void cfw_menu_build_slice(void *ctx, void *menu)
{
    cfw_add_action(ctx, menu, "x4", inv_x4);
    cfw_add_action(ctx, menu, "x8", inv_x8);
    cfw_add_action(ctx, menu, "x16", inv_x16);
    cfw_add_action(ctx, menu, "x32", inv_x32);
    cfw_add_action(ctx, menu, "x64", inv_x64);
    cfw_add_action(ctx, menu, "= Steps", inv_sel); /* one slice per chosen step */
    /* Play+ / Play RT: the list is hidden behind the slice panel (sliceview.c), which shows these
     * six rows as a strip; Points and To Folder are on the two keys left of the screen. */
}

/* Replaces the "Samples" submenu builder: stock items, then our "Slice" submenu. */
void cfw_menu_build_samples(void *ctx, void *menu)
{
    fw_menu_build_samples(ctx, menu);
    void *item = fw_operator_new(SUBMENU_SIZE);
    fw_menu_submenu_init(item, menu, "Slice", (void *)cfw_menu_build_slice, 0);
}

/* ---- the edit -------------------------------------------------------------------------- */
void *cfw_step_at(void *ctx, int track, int step)
{
    return step_at((char *)PTR(ctx, MENU_CTX_CTRL) + CTRL_SEL, track, step);
}

void cfw_collect(void *ctx, struct cfw_sel *out)
{
    cfw_collect_trigs(ctx, out, 1);
}

void cfw_collect_trigs(void *ctx, struct cfw_sel *out, int sample_only)
{
    void *sel = (char *)PTR(ctx, MENU_CTX_CTRL) + CTRL_SEL;

    out->copied = 0;
    out->total = out->skipped = out->tracks = 0;
    for (int t = 0; t < NUM_TRACKS; t++)
        out->bits[t][0] = out->bits[t][1] = 0;
    if (cfw_ram.magic == CFW_MAGIC && cfw_ram.count) {
        for (int t = 0; t < NUM_TRACKS; t++) {
            out->bits[t][0] = cfw_ram.bits[t][0];
            out->bits[t][1] = cfw_ram.bits[t][1];
        }
    } else {
        pairs_to_bits(PTR(sel, SEL_CLIP), out->bits);
        out->copied = 1;
    }

    /* keep only steps that have a trig on a sample track */
    for (int t = 0; t < NUM_TRACKS; t++) {
        out->per_track[t] = 0;
        for (int s = 0; s < NUM_STEPS; s++) {
            u32 m = 1u << (s & 31);
            if (!(out->bits[t][s >> 5] & m))
                continue;
            void *step = step_at(sel, t, s);
            if (!VFN(step, STEP_VT_IS_ON, u32)(step) ||
                (sample_only && VFN(step, STEP_VT_TRACK_TYPE, u32)(step) != 0)) {
                out->bits[t][s >> 5] &= ~m;
                out->skipped++;
                continue;
            }
            out->per_track[t]++;
        }
        out->total += out->per_track[t];
        out->tracks += out->per_track[t] != 0;
    }
}

/* Stock edit recipe (FUN_600d08c4): undo snapshot, then the project's two dirty flags. */
void cfw_edit_begin(void *ctrl, const char *label)
{
    fw_undo_push((char *)ctrl + CTRL_UNDO, label);
    U8(PTR(ctrl, CTRL_DIRTY_A), DIRTY_A_OFF) = 1;
    U8(PTR(ctrl, CTRL_DIRTY_B), DIRTY_B_OFF) = 1;
}

/* Redraw every step pad and side pad from the (edited) pattern, as Delete Steps does. */
void cfw_refresh_pads(void *ctrl)
{
    for (int r = 0; r < PAD_ROWS; r++)
        for (int c = 0; c < PAD_COLS; c++) {
            void *mode = PTR(ctrl, CTRL_MODE);
            VFN(mode, MODE_VT_PAD_REFRESH, void, int, int)(mode, r, c);
        }
    for (int r = 0; r < PAD_ROWS; r++)
        for (int c = 0; c < SIDE_COLS; c++) {
            void *mode = PTR(ctrl, CTRL_MODE);
            VFN(mode, MODE_VT_SIDE_REFRESH, void, int, int, int)(mode, r, c, 0);
        }
}

static void do_slice(void *ctx, u32 n)
{
    cfw_apply_slices(ctx, n, 0);
}

/* Slice the chosen steps: n equal slices (0 = one per step on each track), or, with cuts, the
 * n slices between 0, cuts[0..n-2] and the end (hand-placed points, sliceview.c). */
void cfw_apply_slices(void *ctx, u32 n, const u16 *cuts)
{
    void *ctrl = PTR(ctx, MENU_CTX_CTRL);
    struct cfw_sel sl;
    u32 total, tracks;
    char msg[40], *p;

    cfw_collect(ctx, &sl);
    total = sl.total;
    tracks = sl.tracks;

    fw_menu_close(ctx);
    void *toast = (char *)PTR(ctrl, CTRL_UI) + UI_TOAST;
    if (total == 0) {
        fw_toast_show(toast, sl.skipped ? "Slice: no sample-track trigs" : "Hold or copy steps first");
        return;
    }

    cfw_edit_begin(ctrl, "Slice");

    for (int t = 0; t < NUM_TRACKS; t++) {
        u32 slices = n ? n : sl.per_track[t], k = 0;
        for (int s = 0; s < NUM_STEPS; s++) {
            if (!(sl.bits[t][s >> 5] & (1u << (s & 31))))
                continue;
            u32 j = k - udiv(k, slices) * slices; /* k mod slices */
            s32 a = slice_pos(j, slices), b = slice_pos(j + 1, slices);
            if (cuts) {
                a = j ? cuts[j - 1] : 0;
                b = j + 1 < slices ? cuts[j] : PARAM_POS_MAX;
            }
            void *step = cfw_step_at(ctx, t, s);
            cfw_edit_lock();
            VFN(step, STEP_VT_SET_PARAM, void, int, int)(step, PARAM_SAMPLE_START, a);
            VFN(step, STEP_VT_SET_PARAM, void, int, int)(step, PARAM_SAMPLE_END, b);
            cfw_edit_unlock();
            k++;
        }
    }

    cfw_refresh_pads(ctrl);

    /* "Slice x16: 16 steps", "Slice: 12 copied", "Slice x64: 1024 copied, 16 trks" (<= 31 chars;
     * the toast keeps 32) */
    p = cfw_put_str(msg, "Slice");
    if (cuts) {
        p = cfw_put_str(p, " pts");
    } else if (n) {
        p = cfw_put_str(p, " x");
        p = cfw_put_uint(p, n);
    }
    p = cfw_put_str(p, ": ");
    p = cfw_put_uint(p, total);
    if (sl.copied)
        p = cfw_put_str(p, " copied");
    else if (tracks == 1)
        p = cfw_put_str(p, " steps");
    if (tracks > 1) {
        p = cfw_put_str(p, sl.copied ? ", " : " on ");
        p = cfw_put_uint(p, tracks);
        p = cfw_put_str(p, " trks");
    }
    *p = 0;
    fw_toast_show(toast, msg);
}
