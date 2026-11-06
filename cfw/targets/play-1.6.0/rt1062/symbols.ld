/* Play 1.6.0, RT1062 image (0x60020000..0x600D9064): firmware symbols used by CFW patches.
 * Found by matching the Play+ 1.3.0 code (identical instruction shapes); evidence in
 * docs/FIRMWAfirmware notes.md. Functions carry the Thumb bit. */

/* functions */
fw_operator_new        = 0x60021df5; /* thunk -> malloc 0x60024894 */
fw_menu_submenu_init   = 0x600591b1; /* (item, parent, label, builder_ptr, builder_adj) */
fw_menu_action_init    = 0x600596b1; /* (item, menu, label, std::function*, flag) */
fw_menu_option_init    = 0x6005abe5; /* 0x6005abe4(item 0x60, menu, label, u32 *val, max index, labels[], std::function *on_change): stock option row ("Grid Brightness  High") */
fw_menu_build_root     = 0x60059295; /* stock root "Menu" builder */
fw_menu_build_samples  = 0x600598e9; /* stock "Samples" builder */
fw_menu_close          = 0x60058a75; /* (ctx) */
fw_fn_manager_word     = 0x6005723d; /* std::function manager, 1-word capture */
fw_undo_push           = 0x6005d4f9; /* (ctrl+0x23a8, label) */
fw_toast_show          = 0x6004ffe9; /* (ui+0x144, msg) */
fw_audio_irq_off       = 0x60028209; /* NVIC ICER0 = 1 */
fw_audio_irq_on        = 0x600281fd; /* NVIC ISER0 = 1 */

/* data */
fw_edit_lock_depth     = 0x2000e320; /* audio-IRQ edit lock nesting counter */
cfw_ram                = 0x20001700; /* 256 B scratch in the unused DTCM gap 0x20001478..0x200017FF */

/* slice preview (sliceview.c), found with cfw/tools/sigmatch.py against Play+ 1.3.0 */
fw_menu_screen_update  = 0x6006a2cd; /* menu screen vtable 0x600ceecc +0x0c (Play+ FUN_601057c0) */
fw_menu_screen_hide    = 0x60069a49; /* vtable +0x08 (Play+ FUN_60104e30) */
fw_eve_begin           = 0x60020751; /* Play+ FUN_600a95f8 */
fw_eve_end             = 0x60020771; /* FUN_600a9618 */
fw_eve_word            = 0x60020de9; /* FUN_600a9c90 */
fw_eve_idle            = 0x60020791; /* FUN_600a9638 */
fw_eve_text            = 0x60020b75; /* FUN_600a9a1c */
fw_eve_dlstart         = 0x60020d1d; /* FUN_600a9bc4 */
fw_w9_append           = 0x6002873d; /* rect widget vtable 0x600c1718 [1] */
fw_w9_cache            = 0x60028711; /* [2] */
fw_w9_nop              = 0x600286bd; /* [3]: bx lr */
fw_w9_set_colors       = 0x600286c5; /* [6]: str r1, [r0, #0x30] */
fw_w9_delete           = 0x600286c9; /* [9]: operator delete(this, 0x38) */
fw_eve_wp              = 0x20001816; /* u16 RAM_CMD write offset (Play+ 0x20002816) */
fw_audio_out           = 0x2000181c; /* s16[2][128][2] audio DMA buffer (IRQ 0x60021240; Play+ 0x2000281c) */

/* screen brightness / dim (screen.c), sigmatch / string anchors against Play+ 1.3.0 */
fw_app_update          = 0x6003fb45; /* app vtable 0x600c2810 slot 1 */
fw_menu_build_settings = 0x6005bdfd; /* root builder literal 0x60059370 */
fw_menu_back           = 0x60069c1d; /* Play+ FUN_60105084 */
fw_file_open           = 0x60021b61; /* Play+ FUN_600aa8d0 */
fw_file_read           = 0x60021bb1; /* 'read error: %s' */
fw_file_write          = 0x60021bed; /* 'write error: %s' */
fw_file_close          = 0x60021b25;
fw_in_pad              = 0x600502fd; /* input listener vtable 0x600c5844 [0..4] */
fw_in_button           = 0x600503bd;
fw_in_knob             = 0x60050365;
fw_in_track            = 0x60050395;
fw_in_encoder          = 0x60050491;
fw_millis              = 0x200038c8;

/* USB storage mode (usbdisk.c): the Play+ 1.3.0 feature's stock pieces, present in this build too
 * (docs/FIRMWAfirmware notes.md) */
fw_usb_flags           = 0x200005d4; /* u8[6] (.data): [3] storage, [5] reconnect request; USB object vt 0x6007cc18 */
fw_usb_composite       = 0x200080a4; /* NXP composite device: +0x141 attach, +0x45 ejected (callback 0x6003d414) */
fw_usb_cdc             = 0x20009400; /* CDC ACM state: +0x10 line state (USB task 0x6003dc60 waits for DTR = 0) */
fw_sd_card             = 0x20003c60; /* NXP sd_card_t g_sd (MSC glue 0x6003d638 / 0x6003d664) */
fw_ff_vols             = 0x20004188; /* FatFs FATFS *FatFs[3] (f_mount 0x60035ae0), drive 2 = SD */
fw_ff_files            = 0x2000419c; /* FatFs Files[16] (FF_FS_LOCK), +0 = FATFS * of an open object */
fw_ff_sync_window      = 0x60033571; /* FatFs sync_window body (its callers test wflag first) */
fw_ff_sync_fs          = 0x60033621; /* FatFs sync_fs: window, FSINFO, disk_ioctl(CTRL_SYNC) */
fw_disk_read           = 0x600332c5; /* FatFs diskio disk_read(pdrv, buf, sector, count) */
fw_engine_stop_voices  = 0x6005d0bd; /* Play+ FUN_600ecb98 */
fw_engine_enable       = 0x6005d0b5; /* Play+ FUN_600ecb90: engine+0x1b8 = on (note triggering) */
fw_song_stop           = 0x60049689; /* Play+ FUN_600d2a74 (song, active, 0) */
fw_pattern_switch      = 0x60051781; /* = Play+ FUN_600db228 (sigmatch, 0x98 bytes) */
fw_menu_build_file     = 0x600597e5; /* stock "File" builder (root builder literal 0x60059350) */
fw_f_opendir           = 0x600367ed; /* = Play+ 0x600bf1f8 (sigmatch) */
fw_f_readdir           = 0x6003690d; /* = Play+ 0x600bf318 */
fw_f_closedir          = 0x600368a1; /* = Play+ 0x600bf2ac */
fw_rmtree              = 0x6002195d; /* = Play+ 0x600aa6cc */
fw_f_mkdir             = 0x60036a85; /* FatFs f_mkdir(path) -> FRESULT (0 ok, 8 exists) */
fw_pool_add_folder     = 0x60040dcd; /* (pool, name) -> 0 added, 2 exists, 3 full: pool folder (meta = *(pool+8)) */
fw_pool_find_folder    = 0x60040f29; /* (pool, name) -> folder index or -1 */
fw_import_files        = 0x60043a0d; /* (project, dir, char *names[], count, folder) -> 0 ok, 1 no memory, 2 255 samples */
fw_fmt_startend        = 0x60050965; /* Sample Start / End descriptors vt[0]: (self, value, mode, buf, len, step-or-track) value text */
fw_led_set             = 0x60023811; /* LED object vt+4: (leds, led, rgb *, rgb2 *, mode) */
fw_led_trig_color      = 0x600c3394; /* u8[3] the stock colour of a plain trig pad (55 77 55) */
fw_step_mode_vt        = 0x600cb140; /* vtable of the step-grid pad mode */
fw_fx_preset           = 0x6005cecd; /* (fx, type 0 reverb / 1 delay / 2 limiter, index): the stock preset rows' apply */
fw_fx_saturation       = 0x6005cfd1; /* (fx, type): the stock Saturation row's apply */
fw_delay_presets       = 0x600c2c78; /* {params *, name *}[20] */
fw_reverb_presets      = 0x600c2d18; /* [11] */
fw_limiter_presets     = 0x600c2d70; /* [18] */
fw_sat_names           = 0x600c5238; /* const char *[5] */
fw_scale_names         = 0x600c4df0; /* const char *[39] */
fw_reader_gain         = 0x6007612d; /* (reader): sets this block's gain from the fade in / out */
fw_tgt_get             = 0x6007143d; /* (target) -> current value */
fw_tgt_set             = 0x600713e5; /* (target, mask, value) */
fw_inst_vt_sample      = 0x600d28fc; /* sample instrument vtable */
fw_menu_build_cc       = 0x60059ab1; /* (ctx, menu, channel): a channel's 8 CC rows (MIDI > CC Mapping) */
fw_step_toggle         = 0x60052051; /* (selection, track, step): as a step pad: default step + on, or off */
