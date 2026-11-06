/* Menu > File > Delete Project (Backstage "Delete projects directly from the Play", 25 votes).
 *
 * Lists the saved projects (the folders in /Projects). Press a name once: a toast asks to press
 * it again; press it again within 3 s: the folder is deleted with the firmware's own recursive
 * delete (FUN_600aa6cc: f_opendir / f_readdir, f_unlink of every file and folder, then the folder
 * itself), and the menu goes back to File. The open project lives in /workspace, which this never
 * touches; deleting the saved copy of the open project is allowed (Save writes it again).
 *
 * With no saved projects the list is empty. Names longer than NAME_MAX, or with characters that would leave /Projects ('/', '\', "..",
 * a leading '.'), are not listed. */
#include "slice.h"

#ifdef CFW_DELPROJ

extern void *fw_operator_new(u32 size);
extern void *fw_menu_submenu_init(void *item, void *parent, const char *label, void *builder, u32 adj);
extern void *fw_menu_action_init(void *item, void *menu, const char *label, std_function *fn, u32 flag);
extern void fw_menu_build_file(void *ctx, void *menu);
extern void fw_menu_back(void *scr);
extern void fw_toast_show(void *toast, const char *msg);
extern int fw_fn_manager_word(void *dst, const void *src, int op);
extern int fw_f_opendir(void *dir, const char *path);
extern int fw_f_readdir(void *dir, void *fno);
extern int fw_f_closedir(void *dir);
extern int fw_rmtree(void *unused, char *path, char *path_end, void *fno);
extern volatile u32 fw_millis;

#define ROWS 24
#define NAME_MAX 31
#define ARM_MS 3000
#define DIR_SIZE 0x60      /* FatFs DIR (the firmware allocates 0x50) */
#define FNO_SIZE 0x120     /* FatFs FILINFO with LFN: fsize, fdate, ftime, fattrib +0xc, altname, fname +0x1a */
#define FNO_ATTR 0x0c
#define FNO_NAME 0x1a
#define AM_HID 0x02
#define AM_DIR 0x10
#define PROJECTS "/Projects"

struct row {
    u32 index;
};

struct delproj {
    void *ctx;
    u32 n;
    u32 armed; /* row + 1 that was pressed once, 0 = none */
    u32 armed_ms;
    struct row rows[ROWS];
    char names[ROWS][NAME_MAX + 1];
};

static struct delproj *state(void)
{
    /* CFW code has no writable sections: the block pointer lives in scratch RAM */
    struct cfw_delproj_ref *r = &cfw_ram.delproj;
    if (r->magic != DELPROJ_MAGIC || !r->p) {
        struct delproj *d = fw_operator_new(sizeof(struct delproj));
        if (!d)
            return 0;
        for (u32 i = 0; i < sizeof *d; i++)
            ((volatile u8 *)d)[i] = 0;
        r->p = d;
        r->magic = DELPROJ_MAGIC;
    }
    return r->p;
}

static void *toast_of(void *ctx)
{
    return (char *)PTR(PTR(ctx, MENU_CTX_CTRL), CTRL_UI) + UI_TOAST;
}

static int name_ok(const char *s)
{
    u32 n = 0;
    if (s[0] == 0 || s[0] == '.')
        return 0;
    for (; s[n]; n++)
        if (s[n] == '/' || s[n] == '\\' || n >= NAME_MAX)
            return 0;
    return 1;
}

static void row_pressed(void *const *storage)
{
    struct row *r = (struct row *)storage[0];
    struct delproj *d = state();
    if (!d || !r || r->index >= d->n)
        return;
    void *ctx = d->ctx;
    const char *name = d->names[r->index];
    char msg[48], *p;
    u32 now = fw_millis;
    if (d->armed != r->index + 1 || now - d->armed_ms > ARM_MS) {
        d->armed = r->index + 1;
        d->armed_ms = now;
        p = cfw_put_str(msg, "Again to delete ");
        p = cfw_put_str(p, name);
        *p = 0;
        msg[31] = 0; /* the toast keeps 32 */
        fw_toast_show(toast_of(ctx), msg);
        return;
    }
    d->armed = 0;
    char path[sizeof PROJECTS + NAME_MAX + 2 + 256];
    u8 fno[FNO_SIZE] __attribute__((aligned(4)));
    p = cfw_put_str(path, PROJECTS "/");
    p = cfw_put_str(p, name);
    *p = 0;
    int err = fw_rmtree(0, path, path + sizeof path - 1, fno);
    p = cfw_put_str(msg, err ? "Delete failed: " : "Deleted ");
    p = cfw_put_str(p, name);
    *p = 0;
    msg[31] = 0;
    fw_toast_show(toast_of(ctx), msg);
    fw_menu_back(PTR(PTR(ctx, MENU_CTX_UI), UI_MENU_SCREEN)); /* back to File: the list is rebuilt */
}

static void add_row(void *menu, const char *label, struct row *r)
{
    std_function fn = {r, 0, fw_fn_manager_word, row_pressed};
    void *item = fw_operator_new(ACTION_SIZE);
    fw_menu_action_init(item, menu, label, &fn, 0);
    fn.manager(&fn, &fn, 3);
}

void cfw_menu_build_delproj(void *ctx, void *menu)
{
    struct delproj *d = state();
    if (!d)
        return;
    u8 dir[DIR_SIZE] __attribute__((aligned(4)));
    u8 fno[FNO_SIZE] __attribute__((aligned(4)));
    d->ctx = ctx;
    d->n = 0;
    d->armed = 0;
    if (fw_f_opendir(dir, PROJECTS) == 0) {
        while (d->n < ROWS && fw_f_readdir(dir, fno) == 0) {
            const char *nm = (const char *)fno + FNO_NAME;
            if (nm[0] == 0)
                break;
            if (!(fno[FNO_ATTR] & AM_DIR) || (fno[FNO_ATTR] & AM_HID) || !name_ok(nm))
                continue;
            char *q = cfw_put_str(d->names[d->n], nm);
            *q = 0;
            d->rows[d->n].index = d->n;
            d->n++;
        }
        fw_f_closedir(dir);
    }
    for (u32 i = 0; i < d->n; i++)
        add_row(menu, d->names[i], &d->rows[i]); /* no saved projects: an empty list */
}

/* Replaces the "File" submenu builder: stock items, then "Delete Project". */
void cfw_menu_build_file(void *ctx, void *menu)
{
    fw_menu_build_file(ctx, menu);
    void *item = fw_operator_new(SUBMENU_SIZE);
    fw_menu_submenu_init(item, menu, "Delete Project", (void *)cfw_menu_build_delproj, 0);
}

#endif
