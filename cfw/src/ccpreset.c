/* MIDI > CC Mapping > (channel) > Load Preset / Save Preset (Backstage "CC presets for MIDI mode", Play+, Play RT).
 *
 * The stock CC Mapping keeps 8 CC numbers per channel in the project (project + 0x201 + channel * 8, channels
 * Jack 1..16 then USB 1..16). A preset is those 8 numbers. Save Preset writes the channel's numbers to one of
 * eight slots, kept in /Projects/cfwCC for every project. Load Preset lists the used slots, then the synths
 * (ccpresets.h, slots marked 255 stay as they are). The stock rows show the new numbers when the list is
 * opened again: the menu goes back after a load. */
#include "slice.h"

#ifdef CFW_MOD
#include "ccpresets.h"

extern void *fw_operator_new(u32 size);
extern void *fw_menu_submenu_init(void *item, void *parent, const char *label, void *builder, u32 adj);
extern void *fw_menu_action_init(void *item, void *menu, const char *label, std_function *fn, u32 flag);
extern void fw_menu_back(void *scr);
extern void fw_toast_show(void *toast, const char *msg);
extern int fw_fn_manager_word(void *dst, const void *src, int op);
extern int fw_file_open(void *f, const char *path, u32 mode);
extern int fw_file_read(void *f, void *buf, u32 n);
extern int fw_file_write(void *f, const void *buf, u32 n);
extern void fw_file_close(void *f);

#define FILE_OBJ 0x70
#define FA_READ 0x01
#define FA_WRITE_NEW 0x0a
#define CC_FILE "/Projects/cfwCC"
#define CC_MAGIC 0x43434631u /* "CCF1" */
#define CC_SLOTS 8
#define PROJ_CC 0x201 /* FUN_600e7cf8: project + 0x201 + channel * 8 */
#define MENU_CTX_PROJECT 0x04
#define FACTORY (sizeof cc_factory / sizeof cc_factory[0])
#define ROW_NAME_LEN 8

struct cc_store {
    u32 magic;
    u32 used; /* bit = slot */
    u8 cc[CC_SLOTS][8];
};

/* state: the channel whose menu is open, the slots, the row labels */
struct cc_ui {
    u32 magic;
    void *ctx;
    u32 channel;
    u32 loaded;
    struct cc_store s;
    char names[CC_SLOTS][ROW_NAME_LEN];
};

static struct cc_ui *ui(void)
{
    void **slot = cfw_pages_cc(1);
    if (!slot)
        return 0;
    struct cc_ui *u = *slot;
    if (!u || u->magic != CC_MAGIC) {
        u = fw_operator_new(sizeof *u);
        if (!u)
            return 0;
        for (u32 i = 0; i < sizeof *u; i++)
            ((volatile u8 *)u)[i] = 0;
        u->magic = CC_MAGIC;
        *slot = u;
    }
    return u;
}

static void store_load(struct cc_ui *u)
{
    u8 f[FILE_OBJ] __attribute__((aligned(4)));
    if (u->loaded)
        return;
    u->loaded = 1;
    for (u32 i = 0; i < FILE_OBJ; i++)
        ((volatile u8 *)f)[i] = 0;
    if (!fw_file_open(f, CC_FILE, FA_READ))
        return;
    struct cc_store r;
    if (fw_file_read(f, &r, sizeof r) == (int)sizeof r && r.magic == CC_MAGIC)
        for (u32 i = 0; i < sizeof r; i++)
            ((volatile u8 *)&u->s)[i] = ((const u8 *)&r)[i];
    fw_file_close(f);
}

static void store_save(struct cc_ui *u)
{
    u8 f[FILE_OBJ] __attribute__((aligned(4)));
    for (u32 i = 0; i < FILE_OBJ; i++)
        ((volatile u8 *)f)[i] = 0;
    u->s.magic = CC_MAGIC;
    if (!fw_file_open(f, CC_FILE, FA_WRITE_NEW))
        return;
    fw_file_write(f, &u->s, sizeof u->s);
    fw_file_close(f);
}

static u8 *table(struct cc_ui *u)
{
    return (u8 *)PTR(u->ctx, MENU_CTX_PROJECT) + PROJ_CC + u->channel * 8;
}

static void *toast_of(void *ctx)
{
    return (char *)PTR(PTR(ctx, MENU_CTX_CTRL), CTRL_UI) + UI_TOAST;
}

static void user_name(struct cc_ui *u, u32 k)
{
    char *p = cfw_put_str(u->names[k], "User ");
    p = cfw_put_uint(p, k + 1);
    *p = 0;
}

/* a row: the capture word is its number */
static void add_row(void *menu, const char *label, u32 index, void (*inv)(void *const *))
{
    std_function fn = {(void *)index, 0, fw_fn_manager_word, inv};
    void *item = fw_operator_new(ACTION_SIZE);
    fw_menu_action_init(item, menu, label, &fn, 0);
    fn.manager(&fn, &fn, 3);
}

/* the project is marked changed, as a stock edit does */
static void changed(void *ctx)
{
    void *ctrl = PTR(ctx, MENU_CTX_CTRL);
    U8(PTR(ctrl, CTRL_DIRTY_A), DIRTY_A_OFF) = 1;
    U8(PTR(ctrl, CTRL_DIRTY_B), DIRTY_B_OFF) = 1;
}

/* index: 0..7 a user slot, then the synths */
static void inv_load(void *const *fnp)
{
    struct cc_ui *u = ui();
    u32 idx = (u32)fnp[0];
    if (!u || !u->ctx)
        return;
    const u8 *src = idx < CC_SLOTS ? u->s.cc[idx] : cc_factory[idx - CC_SLOTS].cc;
    u8 *dst = table(u);
    for (u32 i = 0; i < 8; i++)
        if (src[i] < 128)
            dst[i] = src[i];
    changed(u->ctx);
    char msg[32];
    char *p = cfw_put_str(msg, "Loaded ");
    const char *nm = idx < CC_SLOTS ? u->names[idx] : cc_factory[idx - CC_SLOTS].name;
    for (u32 i = 0; nm[i] && p < msg + sizeof msg - 1; i++)
        *p++ = nm[i];
    *p = 0;
    fw_toast_show(toast_of(u->ctx), msg);
    fw_menu_back(PTR(PTR(u->ctx, MENU_CTX_UI), UI_MENU_SCREEN));
}

static void inv_save(void *const *fnp)
{
    struct cc_ui *u = ui();
    u32 k = (u32)fnp[0];
    if (!u || !u->ctx || k >= CC_SLOTS)
        return;
    const u8 *src = table(u);
    for (u32 i = 0; i < 8; i++)
        u->s.cc[k][i] = src[i];
    u->s.used |= 1u << k;
    store_save(u);
    char msg[32];
    char *p = cfw_put_str(msg, "Saved as ");
    user_name(u, k);
    p = cfw_put_str(p, u->names[k]);
    *p = 0;
    fw_toast_show(toast_of(u->ctx), msg);
    fw_menu_back(PTR(PTR(u->ctx, MENU_CTX_UI), UI_MENU_SCREEN));
}

static void build_load(void *ctx, void *menu)
{
    struct cc_ui *u = ui();
    (void)ctx;
    if (!u)
        return;
    store_load(u);
    for (u32 k = 0; k < CC_SLOTS; k++)
        if (u->s.used & (1u << k)) {
            user_name(u, k);
            add_row(menu, u->names[k], k, inv_load);
        }
    for (u32 i = 0; i < FACTORY; i++)
        add_row(menu, cc_factory[i].name, CC_SLOTS + i, inv_load);
}

static void build_save(void *ctx, void *menu)
{
    struct cc_ui *u = ui();
    (void)ctx;
    if (!u)
        return;
    store_load(u);
    for (u32 k = 0; k < CC_SLOTS; k++) {
        user_name(u, k);
        add_row(menu, u->names[k], k, inv_save);
    }
}

/* after the stock rows of a channel's list (cfw_menu_build_cc, slice.c) */
void cfw_cc_append(void *ctx, void *menu, u32 channel)
{
    struct cc_ui *u = ui();
    if (!u || channel >= 32)
        return;
    u->ctx = ctx;
    u->channel = channel;
    fw_menu_submenu_init(fw_operator_new(SUBMENU_SIZE), menu, "Load Preset", (void *)build_load, 0);
    fw_menu_submenu_init(fw_operator_new(SUBMENU_SIZE), menu, "Save Preset", (void *)build_save, 0);
}
#endif
