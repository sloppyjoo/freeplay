/* Display-list output for CFW panels (sliceview.c, usbdisk.c).
 *
 * A CFW panel is a stock "rect" widget (type 9) of the firmware's display manager whose vtable is
 * replaced, so its render() streams our own EVE display list into RAM_CMD (between CMD_DLSTART
 * and the widget cache copy the stock code does). These helpers keep every burst well inside the
 * 4 KB command ring by waiting for the coprocessor whenever a burst gets long. */
#ifndef DLW_H
#define DLW_H

#include "cfw.h"

/* ---- firmware imports (symbols.ld) ------------------------------------------------------- */
extern void fw_eve_begin(void);             /* open a burst at RAM_CMD + wp */
extern void fw_eve_end(void);               /* close it, REG_CMD_WRITE = wp */
extern void fw_eve_word(u32 w);             /* stream one word (caller advances wp) */
extern int fw_eve_idle(void);               /* coprocessor caught up (resyncs wp) */
extern void fw_eve_text(u32 x, s32 y, u32 font, u32 opts, const char *s, u32 maxlen);
extern void fw_eve_dlstart(void);
extern volatile u16 fw_eve_wp;              /* RAM_CMD write offset */
extern char fw_w9_append[], fw_w9_cache[], fw_w9_nop[], fw_w9_set_colors[], fw_w9_delete[];

/* ---- display list words ---------------------------------------------------------------- */
#define DL_VERTEX2F(x, y) (0x40000000u | (((u32)(x) & 0x7fff) << 15) | ((u32)(y) & 0x7fff))
#define DL_BEGIN(p) (0x1f000000u | (p))
#define DL_END 0x21000000u
#define DL_COLOR_RGB(c) (0x04000000u | ((c) & 0xffffff))
#define DL_LINE_WIDTH(w) (0x0e000000u | ((w) & 0xfff))
#define DL_POINT_SIZE(s) (0x0d000000u | ((s) & 0x1fff))
#define DL_VERTEX_FORMAT(f) (0x27000000u | (f))
#define DL_SAVE_CONTEXT 0x22000000u
#define DL_RESTORE_CONTEXT 0x23000000u
#define PRIM_POINTS 2
#define PRIM_LINES 3
#define PRIM_RECTS 9
#define OPT_CENTERX 512
#define OPT_RIGHTX 2048
#define SUB 16 /* VERTEX_FORMAT(4): 1/16 pixel */

/* fonts the firmware loads (CMD_SETFONT2, handles 8..12): 8 = list items, 12 = menu header */
#define FONT_SMALL 8
#define FONT_LARGE 12
#define CHAR_W 9 /* conservative advance of FONT_SMALL (digits 9-10 px, lower case 7-9 px) */

struct dlw {
    u32 n; /* words in the current burst */
};

static inline u32 slen(const char *s)
{
    u32 n = 0;
    while (s[n])
        n++;
    return n;
}

static inline void eve_sync(struct dlw *d)
{
    fw_eve_end();
    while (!fw_eve_idle())
        ;
    fw_eve_begin();
    d->n = 0;
}

static inline void dl(struct dlw *d, u32 w)
{
    if (d->n >= 640) /* keep each burst well inside the 4 KB command ring */
        eve_sync(d);
    fw_eve_word(w);
    fw_eve_wp = (u16)((fw_eve_wp + 4) & 0xfff);
    d->n++;
}

/* CMD_TEXT: 3 words + the string padded to a word */
static inline void text_f(struct dlw *d, int x, int y, u32 font, u32 color, u32 opts, const char *s)
{
    dl(d, DL_COLOR_RGB(color));
    if (d->n >= 600)
        eve_sync(d);
    fw_eve_text((u32)x & 0xffff, y, font, opts, s, 0);
    d->n += 3 + (slen(s) + 4) / 4;
}

static inline void text(struct dlw *d, int x, int y, u32 color, u32 opts, const char *s)
{
    text_f(d, x, y, FONT_SMALL, color, opts, s);
}

static inline void vline(struct dlw *d, int x16, int y0, int y1)
{
    dl(d, DL_VERTEX2F(x16, y0 * SUB));
    dl(d, DL_VERTEX2F(x16, y1 * SUB));
}

#endif
