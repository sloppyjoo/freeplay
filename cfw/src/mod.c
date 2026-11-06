/* The live voice layer (Play+, Play RT): where each sample voice is now, Sustain / Release, two LFOs.
 *
 * A sample track has one voice: an instrument object with an embedded sample reader (inst + INST_READER).
 * The reader's block function asks FUN_60112b74 (fw_reader_gain) once per 128 frames for a 16.16 gain, the
 * stock fade in / fade out; the cave wraps those two calls (cfw_reader_gain, screen.c), so cfw_mod_block
 * runs in the audio interrupt once per sounding voice and block. It does two things:
 *
 *   Sustain / Release, per track. All times are parts of the played region (Start .. End), as the stock
 *   Attack and Decay are: there is no note off on a sample track. Sustain Off (0) keeps the stock Decay,
 *   a fade out at the end. Sustain 1..100 %: after the Attack the gain falls to that level over the Decay
 *   and stays there. Release: the last part of the region fades to silence.
 *
 *   The LFOs. Each has an amount per track and parameter (MOD_TARGETS). The value goes to the
 *   instrument's or mixer's live target, the object a note writes its value to and the engine reads from
 *   (firmware notes "Live parameter path"). A note overwrites the target, so a value other than the one the LFO
 *   wrote last is the new base. Start, End, Attack and Decay are only read at note on: those go through
 *   the note-on read (cfw_tgt_note, screen.c, cfw_mod_note here) and move the next note.
 *
 * Settings are per project: /Projects/cfwMod.<name>, written a moment after the last change. No file = all
 * off. Heap state, allocated once per boot, its pointer on the knob pages' state (cfw_ram is full). */
#include "slice.h"
#include "dlw.h"

#ifdef CFW_MOD
extern void *fw_operator_new(u32 size);
extern volatile u32 fw_millis;
extern void fw_tgt_set(void *target, u32 mask, s32 value);
extern const char fw_inst_vt_sample[]; /* the sample instrument's vtable */
extern int fw_file_open(void *f, const char *path, u32 mode);
extern int fw_file_read(void *f, void *buf, u32 n);
extern int fw_file_write(void *f, const void *buf, u32 n);
extern void fw_file_close(void *f);

#define FILE_OBJ 0x70     /* firmware File wrapper */
#define FA_READ 0x01
#define FA_WRITE_NEW 0x0a /* FA_WRITE | FA_CREATE_ALWAYS */
#define PROJ_NAME_LEN 0x20
#define INST_VT_TARGET 0x18 /* (inst, id) -> live target */
#define MIX_VT_TARGET 0x10  /* (mixer, slot, id) -> live target */
#define TG_CUR 2            /* s16 current value */
#define TG_MIN 8
#define TG_MAX 0x0a
#define BUF_CTX 0x98 /* playing buffer: per track {inst, mixer, slot} */

#define MOD_MAGIC 0x4d4f4431u /* "MOD1" */
#define SAVE_MAGIC 0x4d4f4634u
#define SAVE_MS 1500
#define BLOCK_MS 2 /* the LFOs move about once per audio block (2.9 ms) */

/* sink: 0 instrument, 1 mixer; live 0: read at note on only */
static const struct {
    u8 sink, id, live;
} tgt[MOD_TARGETS] = {
    {0, 0, 1},  /* Volume */
    {1, 1, 1},  /* Pan */
    {0, 5, 1},  /* Note: cents, the amount's 100 % is an octave */
    {0, 6, 1},  /* Microtune */
    {0, 1, 1},  /* Filter */
    {0, 2, 1},  /* Resonance */
    {0, 4, 1},  /* Overdrive */
    {0, 3, 1},  /* Bit Depth */
    {1, 2, 1},  /* Delay */
    {1, 3, 1},  /* Reverb */
    {0, 8, 0},  /* Sample Start */
    {0, 9, 0},  /* Sample End */
    {0, 10, 0}, /* Attack */
    {0, 11, 0}, /* Decay */
};
#define TGT_NOTE 2
#define NOTE_RANGE 1200

struct cfw_mod {
    u32 magic;
    struct mod_save s;
    /* the voices, from the playing buffer (main loop) */
    void *inst[MOD_TRACKS], *mix[MOD_TRACKS];
    u8 slot[MOD_TRACKS];
    u8 cur;      /* the track of the last step pad touched */
    u8 playing;
    u8 loaded;   /* s holds this project's settings */
    u8 dirty;
    u32 dirty_ms;
    char name[PROJ_NAME_LEN];
    /* LFOs */
    u32 phase[2], inc[2], cyc[2];
    s32 out[2];
    u32 ms;
    struct mod_ui ui;
    u32 heads_ms, heads_key;
    /* what each target's value would be without the LFO, and what it wrote last */
    u8 active[MOD_TARGETS]; /* bit = track */
    s16 base[MOD_TARGETS][MOD_TRACKS], last[MOD_TARGETS][MOD_TRACKS];
};

static inline struct cfw_mod *peek(void)
{
    void **slot = cfw_pages_mod(0);
    struct cfw_mod *m = slot ? *slot : 0;
    return m && m->magic == MOD_MAGIC ? m : 0;
}

struct mod_save *cfw_mod_save_data(void)
{
    struct cfw_mod *m = peek();
    return m ? &m->s : 0;
}

void cfw_mod_touch(void)
{
    struct cfw_mod *m = peek();
    if (m) {
        m->dirty = 1;
        m->dirty_ms = fw_millis;
    }
}

struct mod_ui *cfw_mod_ui(void)
{
    struct cfw_mod *m = peek();
    return m ? &m->ui : 0;
}

u32 cfw_mod_track(void)
{
    struct cfw_mod *m = peek();
    return m ? m->cur : 0;
}

/* ---- LFO shapes --------------------------------------------------------------------------------- */
static s32 rnd_of(u32 c)
{
    c = (c + 1) * 0x9e3779b1u;
    c ^= c >> 15;
    c *= 0x85ebca77u;
    c ^= c >> 13;
    return (s32)(c >> 16) - 32768;
}

/* one LFO's value at a phase of cycle `cyc`: -32768 .. 32767 */
s32 cfw_lfo_eval(u32 shape, u32 phase, u32 cyc)
{
    s32 p = (s32)(phase >> 16); /* 0 .. 65535 */
    switch (shape) {
    case LFO_SINE: { /* a parabola per half cycle, corrected towards the sine */
        s32 a = p < 32768 ? p : p - 32768, h = (a * (32768 - a)) >> 13; /* 0 .. 32768 */
        h = (h * (25395 + ((7373 * h) >> 15))) >> 15;
        h = h > 32767 ? 32767 : h;
        return p < 32768 ? h : -h;
    }
    case LFO_TRI:
        return p < 16384 ? p * 2 : p < 49152 ? 65535 - p * 2 : p * 2 - 131070;
    case LFO_UP:
        return p - 32768;
    case LFO_DOWN:
        return 32767 - p;
    case LFO_SQUARE:
        return p < 32768 ? 32767 : -32768;
    case LFO_RANDOM:
        return rnd_of(cyc);
    default: { /* LFO_DRIFT: smooth steps between random values */
        s32 a = rnd_of(cyc), b = rnd_of(cyc + 1), q = p >> 1;  /* q: 0 .. 32767 */
        u32 t2 = ((u32)q * (u32)q) >> 15, f = (t2 * (u32)(3 * 32768 - 2 * q)) >> 15; /* (3 - 2t) t^2 */
        return a + ((((b - a) >> 1) * (s32)f) >> 14);
    }
    }
}

s32 cfw_lfo_out(u32 l)
{
    struct cfw_mod *m = peek();
    return m && l < 2 ? m->out[l] : 0;
}

void cfw_lfo_where(u32 l, u32 *phase, u32 *cyc)
{
    struct cfw_mod *m = peek();
    *phase = m && l < 2 ? m->phase[l] : 0;
    *cyc = m && l < 2 ? m->cyc[l] : 0;
}

static void lfo_advance(struct cfw_mod *m, u32 dt)
{
    for (u32 l = 0; l < 2; l++) {
        u32 before = m->phase[l], step = m->inc[l];
        m->phase[l] = before + step * dt;
        if (m->phase[l] < before)
            m->cyc[l]++;
        m->out[l] = cfw_lfo_eval(m->s.l[l].shape, m->phase[l], m->cyc[l]);
    }
}

/* what the LFOs add to target i of track t, in the target's units; both may move the same parameter */
static s32 mod_of(const struct cfw_mod *m, u32 i, u32 t, s32 lo, s32 hi)
{
    s32 range = i == TGT_NOTE ? NOTE_RANGE : (hi - lo) / 2, sum = 0;
    for (u32 l = 0; l < 2; l++)
        if (m->s.amt[l][t][i])
            sum += (m->out[l] >> 1) * (s32)m->s.l[l].depth / 100 * (s32)m->s.amt[l][t][i] / 100; /* 14 bits + sign */
    return (sum * range) >> 14;
}

static void *target_of(const struct cfw_mod *m, u32 i, u32 t)
{
    if (tgt[i].sink == 0)
        return m->inst[t] ? VFN(m->inst[t], INST_VT_TARGET, void *, int)(m->inst[t], tgt[i].id) : 0;
    return m->mix[t] ? VFN(m->mix[t], MIX_VT_TARGET, void *, int, int)(m->mix[t], m->slot[t], tgt[i].id) : 0;
}

/* for the knob pages: what the LFOs add to parameter i of the current track right now, in its units */
s32 cfw_mod_live(u32 i)
{
    struct cfw_mod *m = peek();
    if (!m || i >= MOD_TARGETS || (!m->s.amt[0][m->cur][i] && !m->s.amt[1][m->cur][i]))
        return 0;
    void *g = target_of(m, i, m->cur);
    return g ? mod_of(m, i, m->cur, AT(g, TG_MIN, s16), AT(g, TG_MAX, s16)) : 0;
}

/* the same, as a yes / no: it is mapped, even when the LFO passes zero */
int cfw_mod_mapped(u32 i)
{
    struct cfw_mod *m = peek();
    return m && i < MOD_TARGETS && (m->s.amt[0][m->cur][i] || m->s.amt[1][m->cur][i]);
}

/* the live targets: interrupt only */
static void lfo_write(struct cfw_mod *m)
{
    for (u32 i = 0; i < MOD_TARGETS; i++) {
        if (!tgt[i].live)
            continue;
        for (u32 t = 0; t < MOD_TRACKS; t++) {
            u32 bit = 1u << t;
            u32 on = m->s.amt[0][t][i] || m->s.amt[1][t][i];
            if (!on && !(m->active[i] & bit))
                continue;
            void *g = target_of(m, i, t);
            if (!g)
                continue;
            s32 cur = AT(g, TG_CUR, s16), lo = AT(g, TG_MIN, s16), hi = AT(g, TG_MAX, s16);
            if (!(m->active[i] & bit)) { /* just mapped: what is there is the base */
                m->base[i][t] = (s16)cur;
                m->active[i] |= (u8)bit;
            } else if (cur != m->last[i][t]) { /* a note or the knob set a new value */
                m->base[i][t] = (s16)cur;
            }
            if (!on) { /* unmapped: give the value back */
                fw_tgt_set(g, 1, m->base[i][t]);
                m->active[i] &= (u8)~bit;
                continue;
            }
            s32 v = m->base[i][t] + mod_of(m, i, t, lo, hi);
            v = v < lo ? lo : v > hi ? hi : v;
            if (v != cur)
                fw_tgt_set(g, 1, v);
            m->last[i][t] = (s16)v;
        }
    }
}

/* ---- the envelope ------------------------------------------------------------------------------- */
/* the gain at t (0..1 of the region), the same in the audio path and on the screen. a, d: the stock Attack
 * and Decay as parts of the region; s: Sustain (0 = Off); r: Release. */
float cfw_env_gain(float t, float a, float d, float s, float r)
{
    float ga = a > 0.0f && t < a ? t / a : 1.0f, g;
    if (s > 0.0f) {
        float k = d > 0.0f ? (t - a) / d : 1.0f;
        k = k < 0.0f ? 0.0f : k > 1.0f ? 1.0f : k;
        g = 1.0f - (1.0f - s) * k;
    } else {
        g = d > 0.0f && 1.0f - t < d ? (1.0f - t) / d : 1.0f;
    }
    if (ga < g)
        g = ga;
    if (r > 0.0f && 1.0f - t < r)
        g *= (1.0f - t) / r;
    return g < 0.0f ? 0.0f : g;
}

/* the played region of a reader, in blocks, from the first block played to the last */
static inline void region(void *rd, s32 *first, s32 *last)
{
    s32 lo = (s32)U32(rd, RD_BLOCK0), hi = (s32)U32(rd, RD_BLOCK1);
    *first = U8(rd, RD_REVERSE) ? hi : lo;
    *last = U8(rd, RD_REVERSE) ? lo : hi;
}

/* per voice, per block, in the audio interrupt; after fw_reader_gain */
void cfw_mod_block(void *rd)
{
    struct cfw_mod *m = peek();
    if (!m)
        return;
    u32 now = fw_millis, dt = now - m->ms;
    if (dt >= BLOCK_MS) {
        m->ms = now;
        if (dt < 100)
            lfo_advance(m, dt);
        lfo_write(m);
    }
    u32 t = 0;
    while (t < MOD_TRACKS && (char *)m->inst[t] + INST_READER != (char *)rd)
        t++;
    if (t == MOD_TRACKS || (!m->s.sus[t] && !m->s.rel[t]))
        return;
    s32 first, last;
    region(rd, &first, &last);
    s32 total = last - first, p = (s32)(U32(rd, RD_POS) >> 7) + 1;
    if (total < 0) {
        total = -total;
        p = first - p;
    } else {
        p -= first;
    }
    if (total <= 0)
        return;
    s32 ab = (s32)U32(rd, RD_ATTACK_END) - first, db = last - (s32)U32(rd, RD_DECAY_START);
    ab = ab < 0 ? -ab : ab;
    db = db < 0 ? -db : db;
    if (db <= 3) /* the stock 3 blocks against a click count as no Decay */
        db = 0;
    float ft = (float)total, tt = (float)p / ft;
    tt = tt < 0.0f ? 0.0f : tt > 1.0f ? 1.0f : tt;
    float g;
    if (m->s.sus[t]) {
        g = cfw_env_gain(tt, (float)ab / ft, (float)db / ft, (float)m->s.sus[t] / 100.0f, (float)m->s.rel[t] / 100.0f);
        float min_r = 3.0f / ft; /* no click at the end */
        if (1.0f - tt < min_r)
            g *= (1.0f - tt) / min_r;
    } else { /* the stock gain, faded */
        float r = (float)m->s.rel[t] / 100.0f;
        g = (float)U32(rd, RD_GAIN) / 65536.0f;
        if (1.0f - tt < r)
            g *= (1.0f - tt) / r;
    }
    U32(rd, RD_GAIN) = (u32)(g * 65536.0f);
}

/* a note reads Start / End / Attack / Decay (audio interrupt): the LFO's value for this note */
s32 cfw_mod_note(void *target, s32 value)
{
    struct cfw_mod *m = peek();
    if (!m)
        return value;
    for (u32 t = 0; t < MOD_TRACKS; t++) {
        u32 off = (u32)((char *)target - (char *)m->inst[t]);
        if (!m->inst[t] || off >= 0x100)
            continue;
        for (u32 i = 0; i < MOD_TARGETS; i++) {
            if (tgt[i].live || (!m->s.amt[0][t][i] && !m->s.amt[1][t][i]))
                continue;
            void *g = VFN(m->inst[t], INST_VT_TARGET, void *, int)(m->inst[t], tgt[i].id);
            if (g != target)
                continue;
            s32 lo = AT(g, TG_MIN, s16), hi = AT(g, TG_MAX, s16), v = value + mod_of(m, i, t, lo, hi);
            return v < lo ? lo : v > hi ? hi : v;
        }
        return value;
    }
    return value;
}

/* ---- where the voices are (the moving playheads) ---------------------------------------------- */
/* the voices playing this sample now: positions 0 .. 32768 of the sample. Returns how many (at most max). */
int cfw_voice_heads(const struct sample *smp, u32 *pos, int max)
{
    struct cfw_mod *m = peek();
    int n = 0;
    if (!m || !smp->frames)
        return 0;
    float k = 32768.0f / (float)smp->frames;
    for (u32 t = 0; t < MOD_TRACKS && n < max; t++) {
        void *rd = m->inst[t] ? (char *)m->inst[t] + INST_READER : 0;
        if (!rd || !U8(rd, RD_ACTIVE) || PTR(rd, RD_DATA) != (const void *)smp->ch[0])
            continue;
        u32 f = U32(rd, RD_POS);
        pos[n++] = f >= smp->frames ? 32768u : (u32)((float)f * k);
    }
    return n;
}

/* how far the current track's voice is through its region: 0 .. 1, or -1 when it isn't sounding */
float cfw_voice_progress(void)
{
    struct cfw_mod *m = peek();
    void *rd = m && m->inst[m->cur] ? (char *)m->inst[m->cur] + INST_READER : 0;
    if (!rd || !U8(rd, RD_ACTIVE))
        return -1.0f;
    u32 lo = U32(rd, RD_START), hi = U32(rd, RD_END), f = U32(rd, RD_POS);
    if (hi <= lo)
        return -1.0f;
    float t = ((float)f - (float)lo) / (float)(hi - lo);
    if (U8(rd, RD_REVERSE))
        t = 1.0f - t;
    return t < 0.0f ? 0.0f : t > 1.0f ? 1.0f : t;
}

/* the moving playheads over a waveform window [v0, v0 + sp) of the sample, at x .. x + wd */
#define C_PLAY 0xffb020
void cfw_draw_heads(struct dlw *d, const struct sample *smp, int x, int wd, int y0, int y1, u32 v0, u32 sp)
{
    u32 pos[4];
    int n = cfw_voice_heads(smp, pos, 4);
    if (!n || !sp)
        return;
    dl(d, DL_COLOR_RGB(C_PLAY));
    dl(d, DL_LINE_WIDTH(16));
    dl(d, DL_BEGIN(PRIM_LINES));
    for (int i = 0; i < n; i++)
        if (pos[i] >= v0 && pos[i] < v0 + sp)
            vline(d, (x + (int)((pos[i] - v0) * (u32)wd / sp)) * SUB + 8, y0, y1);
    dl(d, DL_END);
}

/* something that shows a playhead has to redraw: a value that changes as the voices move, at most
 * 30 times a second (the waveform is drawn again each time) */
#define HEADS_MS 33
u32 cfw_heads_key(const struct sample *smp)
{
    struct cfw_mod *m = peek();
    u32 pos[4], key = 0, now = fw_millis;
    if (!m)
        return 0;
    if (now - m->heads_ms < HEADS_MS)
        return m->heads_key;
    int n = cfw_voice_heads(smp, pos, 4);
    for (int i = 0; i < n; i++)
        key = key * 31 + pos[i] + 1;
    m->heads_ms = now;
    m->heads_key = key;
    return key;
}

/* ---- settings: one small file per project -------------------------------------------------------- */
static void defaults(struct mod_save *s)
{
    for (u32 i = 0; i < sizeof *s; i++)
        ((volatile u8 *)s)[i] = 0;
    s->magic = SAVE_MAGIC;
    for (u32 l = 0; l < 2; l++) {
        s->l[l].shape = l ? LFO_TRI : LFO_SINE;
        s->l[l].rate = l ? 24 : 40;
        s->l[l].div = 5;
        s->l[l].depth = 100;
    }
}

static void copy(void *dst, const void *src, u32 n) /* a struct assignment would call memcpy */
{
    for (u32 i = 0; i < n; i++)
        ((volatile u8 *)dst)[i] = ((const u8 *)src)[i];
}

static char *file_path(char *buf, const char *name)
{
    char *p = cfw_put_str(buf, "/Projects/cfwMod.");
    for (u32 i = 0; i < PROJ_NAME_LEN - 1 && name[i]; i++)
        *p++ = name[i] == '/' || name[i] == '\\' ? '_' : name[i];
    *p = 0;
    return buf;
}

static void load(struct cfw_mod *m)
{
    u8 f[FILE_OBJ] __attribute__((aligned(4)));
    char path[64];
    struct mod_save s;
    defaults(&s);
    for (u32 i = 0; i < FILE_OBJ; i++)
        ((volatile u8 *)f)[i] = 0;
    if (fw_file_open(f, file_path(path, m->name), FA_READ)) {
        struct mod_save r;
        if (fw_file_read(f, &r, sizeof r) == (int)sizeof r && r.magic == SAVE_MAGIC) {
            int ok = 1;
            for (u32 l = 0; l < 2; l++)
                ok &= r.l[l].shape < LFO_SHAPES && r.l[l].rate < LFO_RATES && r.l[l].div < LFO_DIVS && r.l[l].depth <= 100 &&
                      r.l[l].sync < 2 && r.l[l].retrig < 2;
            for (u32 i = 0; i < sizeof r.amt; i++) {
                s32 am = ((const s8 *)r.amt)[i];
                ok &= am >= -100 && am <= 100;
            }
            for (u32 t = 0; t < MOD_TRACKS; t++)
                ok &= r.sus[t] <= 100 && r.rel[t] <= 100;
            if (ok)
                copy(&s, &r, sizeof s);
        }
        fw_file_close(f);
    }
    cfw_edit_lock(); /* no half-written settings under the audio interrupt */
    copy(&m->s, &s, sizeof s);
    cfw_edit_unlock();
}

static void save(struct cfw_mod *m)
{
    u8 f[FILE_OBJ] __attribute__((aligned(4)));
    char path[64];
    for (u32 i = 0; i < FILE_OBJ; i++)
        ((volatile u8 *)f)[i] = 0;
    if (!fw_file_open(f, file_path(path, m->name), FA_WRITE_NEW))
        return;
    fw_file_write(f, &m->s, sizeof m->s);
    fw_file_close(f);
}

static const float frac8[8] = {1.0f, 1.0905f, 1.1892f, 1.2968f, 1.4142f, 1.5422f, 1.6818f, 1.8340f};
static const float div_beats[LFO_DIVS] = {32.0f, 16.0f, 8.0f, 4.0f, 2.0f, 1.0f, 0.6667f, 0.5f, 0.3333f, 0.25f, 0.1667f, 0.125f};

float cfw_lfo_hz(u32 rate)
{
    return 0.05f * frac8[rate & 7] * (float)(1u << (rate >> 3)); /* 0.05 Hz .. 42 Hz */
}

/* main loop */
void cfw_mod_tick(void *app)
{
    void **slot = cfw_pages_mod(1);
    if (!slot)
        return;
    struct cfw_mod *m = *slot;
    if (!m || m->magic != MOD_MAGIC) {
        m = fw_operator_new(sizeof *m);
        if (!m)
            return;
        for (u32 i = 0; i < sizeof *m; i++)
            ((volatile u8 *)m)[i] = 0;
        defaults(&m->s);
        m->ms = fw_millis;
        m->magic = MOD_MAGIC;
        *slot = m;
    }
    /* the voices */
    void *seq = PTR(app, APP_SEQ_PTR);
    if (cfw_ptr_ok(seq)) {
        char *buf = VFN(seq, SEQ_VT_PLAY_BUF, void *)(seq);
        if (cfw_ptr_ok(buf))
            for (u32 t = 0; t < MOD_TRACKS; t++) {
                void **ctx = (void **)(buf + BUF_CTX + t * BUF_TRACK_STRIDE);
                void *inst = ctx[0], *mix = ctx[1];
                if (!cfw_ptr_ok(inst) || !cfw_ptr_ok(mix) || U32(inst, 0) != (u32)fw_inst_vt_sample)
                    continue;
                m->inst[t] = inst;
                m->mix[t] = mix;
                m->slot[t] = (u8)(u32)ctx[2];
            }
        /* a new play from the start: phases that restart with it */
        u8 pl = VFN(seq, SEQ_VT_PLAYING, u8)(seq) & 1;
        if (pl && !m->playing)
            for (u32 l = 0; l < 2; l++)
                if (m->s.l[l].retrig) {
                    m->phase[l] = 0;
                    m->cyc[l] = 0;
                }
        m->playing = pl;
    }
    /* the track of the last step pad touched */
    void *ctrl = (char *)app + APP_CTRL;
    void *pairs = PTR((char *)ctrl + CTRL_SEL, SEL_PAIRS);
    if (cfw_ptr_ok(pairs) && U32(pairs, PAIRS_COUNT)) {
        u32 t = U8(pairs, 0);
        if (t < MOD_TRACKS)
            m->cur = (u8)t;
    }
    /* the track being worked on in the LFO screens is the one the knob pages follow */
    struct mod_ui *fu = &m->ui;
    if (cfw_ram.view.wmode == WMODE_LFO && fu->focus)
        for (u32 t = 0; t < MOD_TRACKS; t++)
            if (fu->focus & (1u << t)) {
                m->cur = (u8)t;
                break;
            }
    /* rates */
    void *proj = PTR((char *)app + APP_PATMGR, PM_PROJECT);
    float bpm = cfw_ptr_ok(proj) ? AT(proj, PROJ_TEMPO, float) : 120.0f;
    if (!(bpm > 10.0f && bpm < 1000.0f))
        bpm = 120.0f;
    for (u32 l = 0; l < 2; l++) {
        const struct mod_lfo *f = &m->s.l[l];
        float hz = f->sync ? bpm / 60.0f / div_beats[f->div] : cfw_lfo_hz(f->rate);
        m->inc[l] = (u32)(hz * 4294967.0f); /* per ms */
    }
    /* nothing sounding: the interrupt doesn't run this; keep the LFOs moving for the screen */
    u32 now = fw_millis, dt = now - m->ms;
    if (dt > 20) {
        cfw_edit_lock();
        m->ms = now;
        lfo_advance(m, dt < 1000 ? dt : 0);
        cfw_edit_unlock();
    }
    cfw_lfo_blink(ctrl);
    cfw_slicer_tick(app);
    /* the LFO panel moves: the menu screen's own update doesn't run every pass */
    struct cfw_view *pv = &cfw_ram.view;
    if (pv->magic != VIEW_MAGIC || pv->wmode != WMODE_LFO)
        m->ui.opened = 0;
    if (pv->magic == VIEW_MAGIC && pv->wmode == WMODE_LFO && cfw_view_alive(pv) && m->ui.scr) {
        if (!m->ui.opened) {
            m->ui.opened = 1;
            m->ui.held = 0;
            cfw_lfo_opened(ctrl);
        }
        cfw_lfo_close_if_left(m->ui.scr);
        if (pv->wmode == WMODE_LFO && cfw_lfo_due())
            VFN(pv->disp, DISP_REFRESH, void, u32)(pv->disp, (u32)pv->slot);
    }
    /* the project's settings */
    if (cfw_ptr_ok(proj)) {
        const char *name = (const char *)proj + PROJ_NAME;
        int same = m->loaded;
        for (u32 i = 0; same && i < PROJ_NAME_LEN; i++) {
            if (m->name[i] != name[i])
                same = 0;
            if (!name[i])
                break;
        }
        if (!same) {
            if (m->dirty && m->loaded)
                save(m);
            for (u32 i = 0; i < PROJ_NAME_LEN; i++)
                m->name[i] = i + 1 < PROJ_NAME_LEN ? name[i] : 0;
            m->dirty = 0;
            load(m);
            m->loaded = 1;
        } else if (m->dirty && now - m->dirty_ms >= SAVE_MS) {
            m->dirty = 0;
            save(m);
        }
    }
}
#endif
