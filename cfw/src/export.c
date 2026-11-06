/* Slice > To Folder: each slice of the steps' sample as its own sample in a new sample-pool folder
 * (Backstage / Reddit request; see cfw/README.md). The Slice panel runs it from the bottom key left
 * of the screen (the highlighted count), the Points editor with Shift + that key (the points).
 * On the Play+ / Play RT this file is linked past the image end (target.json "ext"); the cave code
 * checks the region header (cfw_ext_ok) before calling it. */
#include "slice.h"

#ifdef CFW_EXPORT
extern void fw_menu_close(void *ctx);
extern void fw_toast_show(void *toast, const char *msg);

#ifdef CFW_EXT_BASE
/* first word of the ext region; the build fills in the length and the sum of the words after it */
__attribute__((section(".ext_hdr"), used)) const u32 cfw_ext_header[3] = {CFW_EXT_MAGIC, 0, 0};
#endif

/* ---- Slice > To Folder: the slices as their own samples in a new sample-pool folder -----------
 * Writes each slice of the steps' sample as a 16-bit WAV (01.wav, 02.wav, ...) into
 * /Samples/<name> x<N>/ on the card, then does what the stock browser does when you load a whole
 * folder: pool folder of the same name (FUN_600ca258 add / FUN_600ca3b4 find), then the stock
 * importer FUN_600ccea0(project, dir, names, count, folder), which copies the files into the
 * workspace and loads them (its own errors: 1 = sample memory full, 2 = 255 samples). */
extern int fw_f_mkdir(const char *path);
extern int fw_pool_add_folder(void *pool, const char *name);
extern int fw_pool_find_folder(void *pool, const char *name);
extern int fw_import_files(void *proj, const char *dir, char *const *names, int count, int folder);
extern int fw_file_open(void *f, const char *path, u32 mode);
extern int fw_file_write(void *f, const void *buf, u32 n);
extern int fw_file_close(void *f);
#define FILE_OBJ 0x70     /* firmware File wrapper */
#define FA_WRITE_NEW 0x0a /* FA_WRITE | FA_CREATE_ALWAYS */
#define CHUNK 128         /* frames per write */

static int write_wav(const char *path, const struct sample *smp, u32 f0, u32 n)
{
    u8 f[FILE_OBJ] __attribute__((aligned(4)));
    s16 buf[CHUNK * 2];
    u32 ch = smp->channels, rate = smp->rate > 0.0f ? (u32)smp->rate : 44100u, bytes = n * ch * 2;
    u32 h[11] = {0x46464952u, 36 + bytes, 0x45564157u, 0x20746d66u, 16, 1u | ch << 16, rate, rate * ch * 2,
                 ch * 2 | 16u << 16, 0x61746164u, bytes}; /* RIFF....WAVEfmt  PCM, data */
    for (u32 i = 0; i < FILE_OBJ; i++)
        ((volatile u8 *)f)[i] = 0;
    if (!fw_file_open(f, path, FA_WRITE_NEW))
        return 0;
    int ok = fw_file_write(f, h, sizeof h) == (int)sizeof h;
    for (u32 i = 0; ok && i < n; i += CHUNK) {
        u32 k = n - i < CHUNK ? n - i : CHUNK;
        for (u32 j = 0; j < k; j++)
            for (u32 c = 0; c < ch; c++)
                buf[j * ch + c] = smp->ch[c][f0 + i + j];
        ok = fw_file_write(f, buf, k * ch * 2) == (int)(k * ch * 2);
    }
    fw_file_close(f);
    return ok;
}

/* n slices (0 = one per chosen step); cuts = the hand-placed points, or 0 for equal parts */
/* After the import: every chosen step that plays the source sample gets slice k mod n of the new
 * folder (in step order per track, like Slice) with Start / End over the whole slice. One undo step. */
static void replace_steps(void *ctx, struct cfw_sel *sl, int track, void *pool, int folder, u32 n)
{
    s16 idx[NUM_STEPS], src = -1;
    for (u32 k = 0; k < n; k++)
        idx[k] = -1;
    const u32 *fold = PTR(pool, POOL_META);
    for (u32 i = 0; i < POOL_SLOTS; i++) { /* the folder's samples "01" .. "64" -> slice index */
        const char *e = (const char *)PTR(pool, POOL_ENTRIES) + i * POOL_ENTRY_SIZE;
        const char *nm = e + ENT_NAME;
        if ((s32)fold[i] != folder || !U32(e, ENT_FRAMES) || nm[0] < '0' || nm[0] > '9' || nm[1] < '0' || nm[1] > '9')
            continue;
        u32 k = (u32)(nm[0] - '0') * 10 + (u32)(nm[1] - '0');
        if (k >= 1 && k <= n)
            idx[k - 1] = (s16)i;
    }
    for (u32 k = 0; k < n; k++)
        if (idx[k] < 0)
            return; /* not all slices loaded: leave the steps alone */
    for (int s = 0; s < NUM_STEPS && src < 0; s++)
        if (sl->bits[track][s >> 5] & (1u << (s & 31))) {
            void *st = cfw_step_at(ctx, track, s);
            VFN(st, STEP_VT_GET_PARAM, u32, int, s16 *)(st, PARAM_SAMPLE, &src);
        }
    void *ctrl = PTR(ctx, MENU_CTX_CTRL);
    cfw_edit_begin(ctrl, "Slice");
    for (int t = 0; t < NUM_TRACKS; t++) {
        u32 k = 0;
        for (int s = 0; s < NUM_STEPS; s++) {
            if (!(sl->bits[t][s >> 5] & (1u << (s & 31))))
                continue;
            void *st = cfw_step_at(ctx, t, s);
            s16 cur = -1;
            VFN(st, STEP_VT_GET_PARAM, u32, int, s16 *)(st, PARAM_SAMPLE, &cur);
            if (cur != src)
                continue;
            u32 j = k % n;
            cfw_edit_lock();
            VFN(st, STEP_VT_SET_PARAM, void, int, int)(st, PARAM_SAMPLE, idx[j]);
            VFN(st, STEP_VT_SET_PARAM, void, int, int)(st, PARAM_SAMPLE_START, 0);
            VFN(st, STEP_VT_SET_PARAM, void, int, int)(st, PARAM_SAMPLE_END, PARAM_POS_MAX);
            cfw_edit_unlock();
            k++;
        }
    }
    cfw_refresh_pads(ctrl);
}

#ifdef CFW_MOD
/* the Slice screen: the slices of a sample as a new pool folder; idx[k] = slice k's pool slot. 1 = done, else a toast. */
int cfw_slices_to_folder(void *ctx, const struct sample *smp, u32 n, const u16 *cuts, s16 *idx)
{
    char name[20], dir[48], path[56], nm[NUM_STEPS][8], *names[NUM_STEPS], *p;
    void *toast = (char *)PTR(PTR(ctx, MENU_CTX_CTRL), CTRL_UI) + UI_TOAST;
    const char *msg = "Card write failed";
    for (p = name; p < name + 11 && smp->name[p - name]; p++)
        *p = smp->name[p - name];
    p = cfw_put_str(p, cuts ? " pts" : " x");
    if (!cuts)
        p = cfw_put_uint(p, n);
    *p = 0;
    p = cfw_put_str(cfw_put_str(dir, "/Samples/"), name);
    *p = 0;
    fw_f_mkdir("/Samples");
    fw_f_mkdir(dir);
    for (u32 k = 0; k < n; k++) {
        u32 a = k ? (cuts ? cuts[k - 1] : k * PARAM_POS_MAX / n) : 0;
        u32 b = k + 1 < n ? (cuts ? cuts[k] : (k + 1) * PARAM_POS_MAX / n) : PARAM_POS_MAX + 1;
        u32 f0 = (u32)(((u64)a * smp->frames) >> 15), f1 = (u32)(((u64)b * smp->frames) >> 15);
        char *q = nm[k];
        *q++ = (char)('0' + (k + 1) / 10);
        *q++ = (char)('0' + (k + 1) % 10);
        *cfw_put_str(q, ".wav") = 0;
        names[k] = nm[k];
        q = cfw_put_str(cfw_put_str(cfw_put_str(path, dir), "/"), nm[k]);
        *q = 0;
        if (f1 <= f0 || !write_wav(path, smp, f0, f1 - f0))
            goto fail;
    }
    void *proj = PTR(ctx, MENU_CTX_PROJ), *pool = PTR(proj, PROJ_POOL);
    fw_pool_add_folder(pool, name);
    int folder = fw_pool_find_folder(pool, name);
    msg = "No free sample folder";
    if (folder < 0)
        goto fail;
    int r = fw_import_files(proj, dir, names, (int)n, folder);
    msg = r == 1 ? "Not enough sample memory" : "Limit of 255 samples";
    if (r != 0)
        goto fail;
    for (u32 k = 0; k < n; k++)
        idx[k] = -1;
    const u32 *fold = PTR(pool, POOL_META);
    for (u32 i = 0; i < POOL_SLOTS; i++) {
        const char *e = (const char *)PTR(pool, POOL_ENTRIES) + i * POOL_ENTRY_SIZE, *nmx = e + ENT_NAME;
        if ((s32)fold[i] != folder || !U32(e, ENT_FRAMES) || nmx[0] < '0' || nmx[0] > '9' || nmx[1] < '0' || nmx[1] > '9')
            continue;
        u32 k = (u32)(nmx[0] - '0') * 10 + (u32)(nmx[1] - '0');
        if (k >= 1 && k <= n)
            idx[k - 1] = (s16)i;
    }
    msg = "Slices not loaded";
    for (u32 k = 0; k < n; k++)
        if (idx[k] < 0)
            goto fail;
    return 1;
fail:
    fw_toast_show(toast, msg);
    return 0;
}
#endif

void cfw_export_slices(void *ctx, u32 n, const u16 *cuts, int replace)
{
    struct cfw_sel sl;
    struct sample smp;
    char name[20], dir[48], path[56], nm[NUM_STEPS][8], *names[NUM_STEPS], *p;
    void *toast = (char *)PTR(PTR(ctx, MENU_CTX_CTRL), CTRL_UI) + UI_TOAST;
    const char *msg = "Hold or copy steps first";
    int have, track = cfw_steps_sample(ctx, &sl, &smp, &have);
    fw_menu_close(ctx);
    if (track < 0 || !have)
        goto out;
    if (!n)
        n = sl.per_track[track];
    if (n > NUM_STEPS)
        n = NUM_STEPS;
    /* "<name> x16" (pool folder names are at most 16 characters) */
    for (p = name; p < name + 11 && smp.name[p - name]; p++)
        *p = smp.name[p - name];
    p = cfw_put_str(p, cuts ? " pts" : " x");
    if (!cuts)
        p = cfw_put_uint(p, n);
    *p = 0;
    p = cfw_put_str(cfw_put_str(dir, "/Samples/"), name);
    *p = 0;
    fw_f_mkdir("/Samples");
    fw_f_mkdir(dir);
    msg = "Card write failed";
    for (u32 k = 0; k < n; k++) {
        u32 a = k ? (cuts ? cuts[k - 1] : k * PARAM_POS_MAX / n) : 0;
        u32 b = k + 1 < n ? (cuts ? cuts[k] : (k + 1) * PARAM_POS_MAX / n) : PARAM_POS_MAX + 1;
        u32 f0 = (u32)(((u64)a * smp.frames) >> 15), f1 = (u32)(((u64)b * smp.frames) >> 15);
        char *q = nm[k];
        *q++ = (char)('0' + (k + 1) / 10);
        *q++ = (char)('0' + (k + 1) % 10);
        *cfw_put_str(q, ".wav") = 0;
        names[k] = nm[k];
        q = cfw_put_str(cfw_put_str(cfw_put_str(path, dir), "/"), nm[k]);
        *q = 0;
        if (f1 <= f0 || !write_wav(path, &smp, f0, f1 - f0))
            goto out;
    }
    void *proj = PTR(ctx, MENU_CTX_PROJ), *pool = PTR(proj, PROJ_POOL);
    fw_pool_add_folder(pool, name); /* 2 = exists already: the slices go into it */
    int folder = fw_pool_find_folder(pool, name);
    msg = "No free sample folder";
    if (folder < 0)
        goto out;
    int r = fw_import_files(proj, dir, names, (int)n, folder);
    msg = r == 0 ? name : r == 1 ? "Not enough sample memory" : "Limit of 255 samples";
    if (r == 0 && replace)
        replace_steps(ctx, &sl, track, pool, folder, n);
out:
    fw_toast_show(toast, msg);
}

#endif
