/* Play 1.6.0 (Kinetis K66 image): object layouts used by the CFW patches. Verified in docs/FIRMWAfirmware notes.md ("Play 1.6.0 port")
 * ("Slice-feature RE, verified"). Change only together with symbols.ld. */
#ifndef TARGET_H
#define TARGET_H

#define TARGET_NAME "Play 1.6.0 K66"
#define CFW_VERSION_STRING "1.6.0-FreePlay0.1b"

/* menu controller (ctx passed to every menu builder; app+0x4460), ctor FUN_600e6824 */
#define MENU_CTX_UI 0x00   /* UI manager (app+0x3cd8) */
#define MENU_CTX_CTRL 0x1c /* HW controller (app+0x38) */

/* UI manager */
#define UI_MENU_SCREEN 0x10 /* menu screen object, FUN_600e69d0 */
#define UI_TOAST 0x144      /* toast widget for fw_toast_show (Play+: 0x148) */
#define UI_CUR_SCREEN 0x2fc /* currently shown screen (Play+: 0x300) */

/* HW controller (button handler FUN_600d08c4) */
#define CTRL_UI 0x0c
#define CTRL_DIRTY_A 0x1c /* *(*(ctrl+0x1c)+0x1c4) = 1 */
#define DIRTY_A_OFF 0x1c4
#define CTRL_DIRTY_B 0x24 /* *(*(ctrl+0x24)+0x24) = 1 */
#define DIRTY_B_OFF 0x24
#define CTRL_SEL 0x2c     /* selection object (embedded) */
#define CTRL_UNDO 0x23a8  /* undo ring (embedded) for fw_undo_push */
#define CTRL_MODE 0x24b4  /* current pad mode object */
#define MODE_VT_PAD_REFRESH 0x2c  /* (mode, row, col) */
#define MODE_VT_SIDE_REFRESH 0x30 /* (mode, row, col, 0) */
#define PAD_ROWS 8
#define PAD_COLS 16
#define SIDE_COLS 4

/* selection object */
#define SEL_PAIRS 0x04  /* -> {s8 track, s8 step}[1024], u32 count at +0x800 */
#define SEL_CLIP 0x08   /* Copy clipboard pairs, same layout */
#define SEL_PVIEW 0x5c  /* pattern view: vt[0](pv, track) -> track; track vt[0](trk, step) -> step */
#define PAIRS_COUNT 0x800
#define PAIRS_MAX 1024

/* step object (42-byte engine step) */
#define STEP_VT_IS_ON 0x0c
#define STEP_VT_SET_PARAM 0x10
#define STEP_VT_TRACK_TYPE 0x40 /* 0 = sample track */
#define PARAM_SAMPLE_START 10
#define PARAM_SAMPLE_END 11
#define PARAM_POS_MAX 32767

#define NUM_TRACKS 16
#define NUM_STEPS 64

/* menu item object sizes (operator new) */
#define SUBMENU_SIZE 0x5c
#define ACTION_SIZE 0x38

/* K66 image: no in-place cave, so the code lives in appended HEX records and is entered through
 * a guarded trampoline; the Samples builder is retargeted at runtime (no second flash literal). */
#define CFW_GUARDED_ENTRY 1
#define CFW_RUNTIME_SAMPLES_HOOK 1
#define MENU_ITEMS_BEGIN 0x28 /* std::vector<item*> begin/end in a submenu */
#define MENU_ITEMS_END 0x2c
#define ITEM_TYPE 0x08        /* 0 = submenu, 2 = action */
#define SUBMENU_BUILDER 0x44  /* pointer-to-member {ptr, adj} */

#endif
