/* Play+ 1.3.0: object layouts used by the CFW patches. Verified in docs/FIRMWAfirmware notes.md
 * ("Slice-feature RE, verified"). Change only together with symbols.ld. */
#ifndef TARGET_H
#define TARGET_H

#define TARGET_NAME "Play+ 1.3.0"
#define CFW_VERSION_STRING "1.3.0-FreePlay0.1b"

/* menu controller (ctx passed to every menu builder; app+0x4460), ctor FUN_600e6824 */
#define MENU_CTX_UI 0x00   /* UI manager (app+0x3cd8) */
#define MENU_CTX_CTRL 0x1c /* HW controller (app+0x38) */

/* UI manager */
#define UI_MENU_SCREEN 0x10 /* menu screen object, FUN_600e69d0 */
#define UI_TOAST 0x148      /* toast widget for fw_toast_show */
#define UI_CUR_SCREEN 0x300 /* currently shown screen */

/* HW controller (button handler FUN_600d08c4) */
#define CTRL_UI 0x0c
#define CTRL_DIRTY_A 0x1c /* *(*(ctrl+0x1c)+0x1c4) = 1 */
#define DIRTY_A_OFF 0x1c4
#define CTRL_DIRTY_B 0x24 /* *(*(ctrl+0x24)+0x24) = 1 */
#define DIRTY_B_OFF 0x24
#define CTRL_SEL 0x2c     /* selection object (embedded) */
#define CTRL_UNDO 0x23a8  /* undo ring (embedded) for fw_undo_push */
#define CTRL_MODE 0x24b4  /* current pad mode object */
#define CTRL_PARAM_TABLE 0x24b8 /* the parameter table: 0x20-byte rows, +4 = step per encoder / knob click */
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
#define OPTION_SIZE 0x60  /* option row (Settings > Grid Brightness style) */

/* ---- slice preview (sliceview.c); docs/FIRMWAfirmware notes.md ---------------------- */
#define MENU_CTX_PROJ 0x04  /* project / sample manager (app+?) */
#define PROJ_POOL 0x418     /* sample pool object */
#define POOL_ENTRIES 0x04   /* -> entry[POOL_SLOTS], 0x38 bytes each */
#define POOL_META 0x08     /* -> folder index per pool slot, u32[256] (folder count +0x400) */
#define POOL_ENTRY_SIZE 0x38
#define POOL_SLOTS 255
#define ENT_CH_BEGIN 0x00   /* std::vector<s16*>: one planar int16 buffer per channel */
#define ENT_CH_END 0x04
#define ENT_FRAMES 0x0c     /* length in frames */
#define ENT_RATE 0x10       /* float, Hz */
#define ENT_NAME 0x14       /* NUL-terminated */
#define STEP_VT_GET_PARAM 0x18 /* (step, idx, s16 *out) -> bool (false if not locked) */
#define PARAM_SAMPLE 9      /* pool index (every trig locks it) */

/* menu screen (vtable 0x600860d4: +0x08 hide FUN_60104e30, +0x0c update FUN_601057c0) */
#define SCR_DISP 0x04       /* display manager */
#define SCR_VISIBLE 0x28    /* u8, 1 while shown */
#define SCR_SUBMENU 0x2c    /* submenu item being shown */
#define SCR_CTX 0x34        /* menu controller */
#define SCR_LIST_SLOT 0x3c  /* display slot of the item list widget */
#define SUBMENU_BUILDER 0x44 /* pointer-to-member {ptr, adj} */
#define SUB_CURSOR 0x54     /* highlighted row */

/* display manager (vtable 0x60076b88), FUN_600ab198 composes frames from 50 cached widgets */
#define DISP_CREATE 0x00    /* (disp, type, u32 desc[9]) -> slot */
#define DISP_DESTROY 0x04   /* (disp, slot) */
#define DISP_REFRESH 0x08   /* (disp, slot): queue a re-render */
#define DISP_FLUSH 0x14     /* (disp): run until the queue and the coprocessor are idle */
#define DISP_SET_SIZE 0x1c  /* (disp, slot, w, h), negative = keep */
#define DISP_SHOW 0x24      /* (disp, slot) */
#define DISP_TABLE 0x08     /* widget *[DISP_SLOTS] */
#define DISP_SLOTS 50
#define WIDGET_TYPE_RECT 9  /* 0x38-byte gradient rect, all setters no-ops */
#define W_X 0x1c
#define W_Y 0x20
#define W_W 0x24
#define W_H 0x28

/* ---- synth patch randomizer (synthrand.c); docs/FIRMWAfirmware notes.md ------------------ */
#define MENU_CTX_APP 0x18        /* app object (0x20018c18) */
#define MENU_CTX_SYNTH_SLOT 0x40 /* synth slot of the open synth submenu (set by its builder) */
#define APP_SYNTHMGR 0x270c      /* synth manager inside the app */
#define SYNTH_SLOTS 3
#define SYNTH_SLOT_SIZE 0x438
#define SLOT_PATCH 0x0c          /* shared_ptr<PolySynth::Patch> (raw pointer) */
#define SLOT_MODIFIED 0x435      /* u8: patch changed, saved by FUN_600dba20 */
#define PATCH_VT_GET_VALUE 0x00  /* (out*, this, id): raw value, tiny_optional<float> */
#define PATCH_VT_SET_VALUE 0x10  /* (this, id, const float *v, source) */
#define PATCH_VT_GET_MAPPED 0x04 /* (out*, this, id): edit-mapped value 0..1, tiny_optional */
#define PATCH_VT_SET_MAPPED 0x08 /* (this, id, const float *v, source) */
#define PATCH_VT_BATCH_BEGIN 0x1c
#define PATCH_VT_BATCH_END 0x20
#define PATCH_VT_METADATA 0x50   /* -> Metadata, +0 std::vector<Param> */
#define PATCH_SRC_KNOB 0
#define PARAM_SIZE 0x38
#define PARAM_NAME 0x00          /* std::string: +0 data, +4 length */
#define PARAM_MIN 0x1c           /* __fp16 */
#define PARAM_MAX 0x1e           /* __fp16 */
#define PARAM_IS_ENUM 0x22
#define PARAM_INVALIDATES 0x24   /* bool: changing it rewrites other params' metadata (LFO Sync) */

/* ---- screen brightness / dim (screen.c) ---------------------------------------------------- */
#define CFW_SCREEN 1
#define CFW_POINTS 1 /* Samples > Slice > Points (sliceview.c) */
#define CFW_EXPORT 1 /* Samples > Slice > To Folder: slices as samples in a new pool folder (export.c) */
#define CFW_STARTEND 1 /* Sample Start / End knob page as a waveform (startend.c) */
#define APP_MENU_CTX 0x4460  /* the menu controller inside the app (its +4 = project, +0x10 = effects) */
#define MENU_CTX_FX 0x10   /* effects object: fw_fx_preset / fw_fx_saturation */
#define CFW_COLORS 1   /* folder colours: pool view + step pads (colors.c) */
#define UI_BROWSER_SCREEN 0x08 /* file browser screen; View Sample Pool uses it */
#define CTRL_TRACK_PAGE 0x2410 /* u32[16]: each track's step page shown on the grid (pad refresh) */
#define MODE_BANK 0x112        /* step mode: u8, tracks 9..16 on the grid */
#define LED_COLS 20            /* LED index = row * 20 + column (16 steps + 4 side pads) */
#define CFW_PAGES 1    /* knob-page visuals: Master VU, Note keyboard, Volume / Pan, Filter curve (pages.c) */
#define PSCR_ACTIVE 0xc8 /* parameter screen: which of the page's two parameters is active (0 top, 1 bottom) */
#define W_BAR_VALUE 0x18 /* bar widget: the raw parameter value it shows */
#define AUDIO_OUT_FRAMES 256 /* fw_audio_out (symbols.ld): 2 x 128 interleaved int16 stereo frames */
#define APP_CTRL 0x38          /* HW controller inside the app */
#define CTRL_KNOB 0x240c       /* knob whose page is shown (touch / turn sets it) */
#define SE_KNOB 7              /* the Sample Start / Sample End page */
#define UI_PARAM_SCREEN 0x00   /* the two screens that show knob pages (ui+0, ui+4; same class) */
#define UI_PARAM_SCREEN2 0x04
#define PSCR_SLOTS 0xdc        /* its 6 widget slots: name 1/2, value 1/2, bar 1/2 (u32 each) */
#define WG_FLAGS 0x0c          /* widget flags, bit 0 = visible */
#define W_TEXT 0x10            /* widget text pointer (desc[0]) */
#define DISP_SHOW_SLOT 0x24    /* disp vt: show(slot) / hide(slot) */
#define DISP_HIDE_SLOT 0x28
/* ext region: code past the image end (new HEX records, target.json "ext"); used only when intact */
#define CFW_EXT_BASE 0x60190B00u
#define CFW_EXT_MAGIC 0x58574643u /* "CFWX" */
#define CFW_EXT_MAX 0x10000u

/* ---- engine layout (Tap Tempo: project tempo; pattern manager) -------------------------------- */
#define CFW_TAP 1 /* Menu > Tap Tempo (tap.c); Song mode was dropped in CFW4 (song.c kept in git history) */
#define CFW_DELPROJ 1 /* File > Delete Project (delproj.c) */
#define APP_PATMGR 0x4430     /* pattern manager inside the app (ctor FUN_600db0d4) */
#define PM_PLAYING 0x1c
#define PM_QUEUED 0x28        /* pm[10]: queued switch, -1 = none */       /* pm[7]: pattern playing now (0-based) */
#define APP_SEQ_PTR 0x10      /* -> sequencer singleton 0x2001e30c (FUN_601087d4) */
#define BUF_VT_SET_TEMPO 0x08 /* (buf, float bpm): the playing tempo (FUN_600db228) */
#define SEQ_VT_PLAY_BUF 0x08  /* (seq) -> pattern buffer being played */
#define SEQ_VT_PLAYING 0x28   /* (seq) -> bool */
#define BUF_PLAYHEAD 0x24     /* u32: track 1's current step in that buffer */
#define SONG_PATTERNS 128
#define PM_PROJECT 0x00      /* pm[0]: project (Play+ app+0x3ff0) */
#define PROJ_TEMPO 0x1f0
#define PROJ_NAME 0x2c       /* char[0x20]: open project name (FUN_600cd1d4) */
#define PROJ_DIR 0xe4        /* char[0x100]: its folder, "/" if never saved */     /* float BPM (SetParameter "tempo" writes it, 0x600d4bd4) */
#define APP_CHAIN_ACTIVE 0x45a8 /* u8: stock Play Chain playing (song state, firmware notes) */
#define APP_LISTENER 0x1c   /* input listener inside the app; listener + 8 -> UI manager */
#define LISTENER_UI 0x08
#define DISP_PWM_DIRTY 0x38 /* display manager: apply the wanted backlight duty on the next pass */
#define DISP_PWM_WANT 0x3c  /* wanted REG_PWM_DUTY 0..128 (+0x40 = current) */

/* ---- the voice layer (mod.c): sample instrument, its reader, the playing buffer's per-track context ---- */
#define CFW_MOD 1
#define INST_READER 0xd8
#define BUF_TRACK_STRIDE 0xbb4
#define RD_ACTIVE 0x18      /* u8 sounding */
#define RD_DATA 0x1c        /* s16 * left channel */
#define RD_START 0x2c      /* frames, low .. high (swapped for reverse) */
#define RD_END 0x30
#define RD_BLOCK0 0x34     /* the same / 128 */
#define RD_BLOCK1 0x38
#define RD_REVERSE 0x3d    /* u8 */
#define RD_POS 0x48        /* frame now */
#define RD_ATTACK_END 0x60 /* block the fade in ends */
#define RD_DECAY_START 0x64
#define RD_GAIN 0x68       /* 16.16, this block */

#endif
