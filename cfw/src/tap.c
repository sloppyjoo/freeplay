/* Tap Tempo (Backstage "Implement Tap Tempo"), Play+ and Play RT:
 *   on the Tempo / Track Swing knob page: Shift + the top key left of the screen, in time.
 * Two or more taps: the project tempo follows them (30..300 BPM) and a toast shows it. A pause of more than 2 s
 * starts a new series. The interval is smoothed (each new tap counts a quarter), so a few taps
 * settle it and a changed pulse is followed within a few taps.
 *
 * The project's tempo is a float at project + PROJ_TEMPO (project = pattern manager[0]); the remote
 * API's "SetParameter project tempo" writes only that field and the engine follows it. */
#include "slice.h"

#ifdef CFW_TAP
extern volatile u32 fw_millis;
extern void fw_toast_show(void *toast, const char *msg);

#define TAP_GAP_MS 2000
#define TAP_MIN_MS 200  /* 300 BPM */
#define TAP_MAX_MS 2000 /* 30 BPM */

/* one tap */
void cfw_tap(void *app)
{
    struct cfw_tap *tp = &cfw_ram.tap;
    void *ctrl = (char *)app + APP_CTRL;
    void *toast = (char *)PTR(ctrl, CTRL_UI) + UI_TOAST;
    u32 now = fw_millis, iv = now - tp->last;
    tp->last = now;
    if (tp->taps == 0 || iv > TAP_GAP_MS) { /* first tap of a series */
        tp->taps = 1;
        fw_toast_show(toast, "Tap again in time");
        return;
    }
    if (iv < TAP_MIN_MS)
        iv = TAP_MIN_MS;
    tp->ema = tp->taps == 1 ? iv : (tp->ema * 3 + iv + 2) / 4;
    tp->taps = 2;
    u32 bpm10 = (600000u + tp->ema / 2) / tp->ema; /* tenths of a BPM */
    if (bpm10 < 300)
        bpm10 = 300;
    void *project = PTR((char *)app + APP_PATMGR, PM_PROJECT);
    if (!project)
        return;
    float bpm = (float)bpm10 / 10.0f;
    AT(project, PROJ_TEMPO, float) = bpm;
    /* and the running sequencer: the playing pattern buffer's tempo setter (vt+8, as the stock
     * pattern switch FUN_600db228 does), so the new tempo is heard at once, not after Stop / Play */
    void *seq = PTR(app, APP_SEQ_PTR);
    if (cfw_ptr_ok(seq) && cfw_ptr_ok(*(void **)seq)) {
        void *buf = VFN(seq, SEQ_VT_PLAY_BUF, void *)(seq);
        if (cfw_ptr_ok(buf) && cfw_ptr_ok(*(void **)buf)) {
            cfw_edit_lock();
            VFN(buf, BUF_VT_SET_TEMPO, void, float)(buf, bpm);
            cfw_edit_unlock();
        }
    }
    U8(PTR(ctrl, CTRL_DIRTY_A), DIRTY_A_OFF) = 1;
    char msg[24], *p = cfw_put_str(msg, "Tempo ");
    p = cfw_put_uint(p, bpm10 / 10);
    *p++ = '.';
    *p++ = (char)('0' + bpm10 % 10);
    p = cfw_put_str(p, " BPM");
    *p = 0;
    fw_toast_show(toast, msg);
}

/* input hook (screen.c): Shift + top-left key on the Tempo page taps; 1 = swallowed */
int cfw_tap_key(void *app, u32 id, u32 val)
{
    const char *t;
    if (id != 0 || !cfw_ram.view.shift || !(t = cfw_page_title(app)) || !cfw_str_is(t, "Tempo"))
        return 0;
    if (val == 0)
        cfw_tap(app);
    return 1;
}
#endif
