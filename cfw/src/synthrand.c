/* Randomize synth patch (Play+): Menu > Synths > <synth> > Randomize > Subtle / Medium / Wild / Undo.
 *
 * Backstage "Randomise function for synth patch edit". Works on any PolySynth engine through the
 * patch's own metadata, the same way a knob turn in the patch editor does: for each parameter,
 * getParamValueEditMapped / setParamValueEditMapped (0..1 along the parameter's edit curve), each
 * set inside its own audio-IRQ edit lock like the patch editor's knob path, then the slot is marked
 * modified so the firmware saves it with the project.
 *
 *   Subtle  continuous params move up to +-8 % of their range around the current value
 *   Medium  continuous params move up to +-25 %
 *   Wild    every param (enums and the mod matrix too) gets a new value; cutoff, attack and
 *           release stay in a playable part of their range
 * Never touched: volume / gain, coarse and fine tune, glide, oversampling, unnamed placeholders.
 * Undo puts back exactly the values from before the last randomize of that synth.
 *
 * The menu stays open so you can press again while playing the synth. */
#include "slice.h"

extern void fw_menu_build_synth(void *ctx, void *menu, u32 slot);
extern void *fw_operator_new(u32 size);
extern void *fw_menu_submenu_init(void *item, void *parent, const char *label, void *builder, u32 adj);
extern void fw_toast_show(void *toast, const char *msg);
extern const u32 fw_topt_none; /* tiny_optional<float> "no value" bit pattern */

#define RAND_MAX_PARAMS 128
#define UNDO_MAGIC 0x554e444fu /* "UNDO" */

struct rand_undo {
    u32 magic;
    void *patch; /* the patch object the values belong to */
    u32 slot;
    u32 n;
    u32 v[RAND_MAX_PARAMS]; /* raw parameter values before the last randomize (float bits,
                               fw_topt_none = no value) */
};

enum { MODE_SUBTLE, MODE_MEDIUM, MODE_WILD };

/* ---- helpers ----------------------------------------------------------------------------- */
static struct cfw_rand *rand_state(void)
{
    struct cfw_rand *r = &cfw_ram.rand;
    if (r->magic != RAND_MAGIC) {
        r->magic = RAND_MAGIC;
        r->seed = 0;
        r->undo = 0;
    }
    return r;
}

static u32 next_rand(struct cfw_rand *r)
{
    u32 x = r->seed;
    if (x == 0) /* first use: seed from the cycle counter */
        x = *(volatile u32 *)0xE0001004u ^ 0x9e3779b9u;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    if (x == 0)
        x = 0x2545f491u;
    r->seed = x;
    return x;
}

static float rand01(struct cfw_rand *r) /* [0, 1) */
{
    return (float)(next_rand(r) >> 8) * (1.0f / 16777216.0f);
}

static int has(const char *s, u32 len, const char *pat)
{
    for (u32 i = 0; i < len; i++) {
        u32 k = 0;
        while (pat[k] && i + k < len && s[i + k] == pat[k])
            k++;
        if (!pat[k])
            return 1;
    }
    return 0;
}

static float clamp01(float v)
{
    return v < 0.0f ? 0.0f : v > 1.0f ? 1.0f : v;
}

static void *slot_base(void *ctx, u32 slot)
{
    return (char *)PTR(ctx, MENU_CTX_APP) + APP_SYNTHMGR + slot * SYNTH_SLOT_SIZE;
}

static float get_mapped(void *patch, u32 id, u32 *raw)
{
    u32 out = fw_topt_none;
    VFN(patch, PATCH_VT_GET_MAPPED, void, void *, u32)(&out, patch, id);
    *raw = out;
    return AT(&out, 0, float);
}

/* Each set is one stock-style knob change: its own audio-IRQ lock (as the patch editor does), so
 * the audio interrupt is never held off for more than one parameter update. */
static void set_mapped(void *patch, u32 id, u32 raw)
{
    cfw_edit_lock();
    VFN(patch, PATCH_VT_SET_MAPPED, void, u32, const u32 *, u32)(patch, id, &raw, PATCH_SRC_KNOB);
    cfw_edit_unlock();
}

static u32 get_raw(void *patch, u32 id)
{
    u32 out = fw_topt_none;
    VFN(patch, PATCH_VT_GET_VALUE, void, void *, u32)(&out, patch, id);
    return out;
}

static void set_raw(void *patch, u32 id, u32 raw)
{
    cfw_edit_lock();
    VFN(patch, PATCH_VT_SET_VALUE, void, u32, const u32 *, u32)(patch, id, &raw, PATCH_SRC_KNOB);
    cfw_edit_unlock();
}

static char *param_at(void *patch, u32 i)
{
    char *meta = VFN(patch, PATCH_VT_METADATA, char *)(patch);
    return (char *)PTR(meta, 0) + i * PARAM_SIZE;
}

static u32 param_count(void *patch)
{
    char *meta = VFN(patch, PATCH_VT_METADATA, char *)(patch);
    u32 n = (u32)((char *)PTR(meta, 4) - (char *)PTR(meta, 0)) / PARAM_SIZE;
    return n > RAND_MAX_PARAMS ? RAND_MAX_PARAMS : n;
}

static void toast(void *ctx, const char *msg)
{
    fw_toast_show((char *)PTR(ctx, MENU_CTX_UI) + UI_TOAST, msg);
}

/* ---- the edit ----------------------------------------------------------------------------
 * Some parameters (an LFO's "Sync") change other parameters' metadata: "Ratio" turns from a free
 * 0..100 rate into a 0..15 tempo-division enum and the firmware remaps its value. So both the
 * randomize and the undo run in two batches: those switches first, then everything else read
 * against the final metadata. */
static u32 randomize_pass(struct cfw_rand *r, void *patch, int mode, int switches)
{
    u32 n = param_count(patch), changed = 0;
    for (u32 i = 0; i < n; i++) {
        char *p = param_at(patch, i);
        if ((U8(p, PARAM_INVALIDATES) != 0) != (switches != 0))
            continue;
        const char *name = PTR(p, PARAM_NAME);
        u32 len = U32(p, PARAM_NAME + 4);
        u32 raw;
        float cur = get_mapped(patch, i, &raw);
        if (raw == fw_topt_none || !name || len == 0 || len > 64 ||
            AT(p, PARAM_MIN, u16) == AT(p, PARAM_MAX, u16))
            continue;
        if (has(name, len, "Volume") || has(name, len, "Gain") || has(name, len, "Tune") ||
            has(name, len, "Glide") || has(name, len, "Oversampling"))
            continue;
        float v;
        if (mode == MODE_WILD) {
            float lo = 0.0f, hi = 1.0f;
            if (has(name, len, "Cutoff"))
                lo = 0.3f; /* keep the filter audible */
            else if (has(name, len, "Attack"))
                hi = 0.45f;
            else if (has(name, len, "Release"))
                hi = 0.6f;
            v = lo + (hi - lo) * rand01(r);
        } else {
            if (U8(p, PARAM_IS_ENUM))
                continue;
            float amt = mode == MODE_SUBTLE ? 0.08f : 0.25f;
            v = clamp01(cur + (rand01(r) * 2.0f - 1.0f) * amt);
        }
        set_mapped(patch, i, AT(&v, 0, u32));
        changed++;
    }
    return changed;
}

static void restore_pass(void *patch, const struct rand_undo *u, int switches)
{
    for (u32 i = 0; i < u->n; i++)
        if ((U8(param_at(patch, i), PARAM_INVALIDATES) != 0) == (switches != 0) && u->v[i] != fw_topt_none)
            set_raw(patch, i, u->v[i]);
}

static void do_randomize(void *ctx, int mode)
{
    struct cfw_rand *r = rand_state();
    u32 slot = U32(ctx, MENU_CTX_SYNTH_SLOT);
    if (slot >= SYNTH_SLOTS)
        return;
    void *sb = slot_base(ctx, slot);
    void *patch = PTR(sb, SLOT_PATCH);
    if (!patch) {
        toast(ctx, "No patch on this synth");
        return;
    }
    if (!r->undo)
        r->undo = fw_operator_new(sizeof(struct rand_undo));
    struct rand_undo *u = r->undo;
    if (u) {
        u->n = param_count(patch);
        for (u32 i = 0; i < u->n; i++)
            u->v[i] = get_raw(patch, i);
    }

    u32 changed = 0;
    for (int pass = 1; pass >= 0; pass--) /* switches first */
        changed += randomize_pass(r, patch, mode, pass);
    U8(sb, SLOT_MODIFIED) = 1;

    if (u) {
        u->magic = UNDO_MAGIC;
        u->patch = patch;
        u->slot = slot;
    }
    char msg[40], *q = cfw_put_str(msg, mode == MODE_SUBTLE ? "Subtle: " : mode == MODE_MEDIUM ? "Medium: " : "Wild: ");
    q = cfw_put_uint(q, changed);
    q = cfw_put_str(q, " params changed");
    *q = 0;
    toast(ctx, msg);
}

static void do_undo(void *ctx)
{
    struct cfw_rand *r = rand_state();
    struct rand_undo *u = r->undo;
    u32 slot = U32(ctx, MENU_CTX_SYNTH_SLOT);
    if (slot >= SYNTH_SLOTS || !u || u->magic != UNDO_MAGIC || u->slot != slot) {
        toast(ctx, "Nothing to undo");
        return;
    }
    void *sb = slot_base(ctx, slot);
    void *patch = PTR(sb, SLOT_PATCH);
    if (patch != u->patch) { /* the synth got another patch (Init, load, model change) */
        u->magic = 0;
        toast(ctx, "Patch changed, can't undo");
        return;
    }
    for (int pass = 1; pass >= 0; pass--)
        restore_pass(patch, u, pass);
    U8(sb, SLOT_MODIFIED) = 1;
    u->magic = 0; /* one level */
    toast(ctx, "Randomize undone");
}

static void inv_subtle(void *const *f) { do_randomize(f[0], MODE_SUBTLE); }
static void inv_medium(void *const *f) { do_randomize(f[0], MODE_MEDIUM); }
static void inv_wild(void *const *f) { do_randomize(f[0], MODE_WILD); }
static void inv_undo(void *const *f) { do_undo(f[0]); }

/* ---- menus ------------------------------------------------------------------------------- */
void cfw_menu_build_rand(void *ctx, void *menu)
{
    cfw_add_action(ctx, menu, "Subtle", inv_subtle);
    cfw_add_action(ctx, menu, "Medium", inv_medium);
    cfw_add_action(ctx, menu, "Wild", inv_wild);
    cfw_add_action(ctx, menu, "Undo", inv_undo);
}

/* Replaces the per-synth submenu builder (3-argument pointer-to-member call, slot in r2). */
void cfw_menu_build_synth(void *ctx, void *menu, u32 slot)
{
    fw_menu_build_synth(ctx, menu, slot);
    void *item = fw_operator_new(SUBMENU_SIZE);
    fw_menu_submenu_init(item, menu, "Randomize", (void *)cfw_menu_build_rand, 0);
}
