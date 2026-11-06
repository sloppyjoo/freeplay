/* Play 1.6.0, Kinetis K66 image (0x00000000..0x000B069C): firmware symbols used by CFW patches.
 * Found by matching the Play RT / Play+ code; evidence in docs/FIRMWAfirmware notes.md.
 * Functions carry the Thumb bit. */

/* functions */
fw_operator_new        = 0x000030c1; /* thunk -> malloc 0x56f5c */
fw_menu_submenu_init   = 0x0003350d;
fw_menu_action_init    = 0x00033a05;
fw_menu_build_root     = 0x000335ed;
fw_menu_build_samples  = 0x00033c3d;
fw_menu_close          = 0x00032dbd;
fw_fn_manager_word     = 0x00031675;
fw_undo_push           = 0x00037865;
fw_toast_show          = 0x0002a395;
fw_audio_irq_off       = 0x0000859d; /* cpsid i (K66 build masks all IRQs for step edits) */
fw_audio_irq_on        = 0x00008599; /* cpsie i */

/* data */
fw_edit_lock_depth     = 0x20001db4;
cfw_ram                = 0x1fff9000; /* 256 B in the unused SRAM_L tail 0x1FFF869C..0x1FFFFFFF */
