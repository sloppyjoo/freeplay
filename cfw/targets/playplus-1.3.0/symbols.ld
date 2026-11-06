/* Play+ 1.3.0 firmware symbols used by CFW patches (our names). Functions carry the Thumb bit.
 * Evidence for each entry: docs/FIRMWAfirmware notes.md. */

/* functions */
fw_operator_new        = 0x600aab65; /* thunk_FUN_600ad664(size) -> void* */
fw_menu_submenu_init   = 0x600e9d75; /* FUN_600e9d74(item, parent, label, builder_ptr, builder_adj) */
fw_menu_action_init    = 0x600e7861; /* FUN_600e7860(item, menu, label, std::function*, flag) */
fw_menu_build_root     = 0x600e9e59; /* stock root "Menu" builder (ctx, menu) */
fw_menu_option_init    = 0x600e84a1; /* FUN_600e84a0(item 0x60, menu, label, u32 *val, max index, labels[], std::function *on_change): stock option row ("Grid Brightness  High") */
fw_menu_build_samples  = 0x600e7995; /* stock "Samples" builder FUN_600e7994(ctx, menu) */
fw_menu_close          = 0x600e6a1d; /* FUN_600e6a1c(ctx): close menu, back to previous screen */
fw_fn_manager_word     = 0x600e44cd; /* std::function manager for a 1-word capture */
fw_undo_push           = 0x600ecfb9; /* FUN_600ecfb8(ctrl+0x23a8, label) */
fw_toast_show          = 0x600d99c1; /* FUN_600d99c0(ui+0x148, msg) */
fw_audio_irq_off       = 0x600b1121; /* FUN_600b1120: NVIC ICER0 = 1 */
fw_audio_irq_on        = 0x600b1115; /* FUN_600b1114: NVIC ISER0 = 1 */

fw_menu_screen_update  = 0x601057c1; /* menu screen vt+0x0c FUN_601057c0(scr) */
fw_menu_screen_hide    = 0x60104e31; /* menu screen vt+0x08 FUN_60104e30(scr): destroys its widgets */
fw_eve_begin           = 0x600a95f9; /* FUN_600a95f8: CS low, write address RAM_CMD + wp */
fw_eve_end             = 0x600a9619; /* FUN_600a9618: CS high, REG_CMD_WRITE = wp */
fw_eve_word            = 0x600a9c91; /* FUN_600a9c90(word): 4 bytes into the open burst */
fw_eve_idle            = 0x600a9639; /* FUN_600a9638: !=0 when CMD_READ == CMD_WRITE (resyncs wp) */
fw_eve_text            = 0x600a9a1d; /* FUN_600a9a1c(x, y, font, opts, str, maxlen): CMD_TEXT */
fw_eve_dlstart         = 0x600a9bc5; /* FUN_600a9bc4: CMD_DLSTART */
fw_w9_append           = 0x600b1655; /* rect widget (type 9) vtable 0x600770fc: [1] append */
fw_w9_cache            = 0x600b1629; /* [2] cache: MEMCPY RAM_DL -> RAM_G slot, size = REG_CMD_DL */
fw_w9_nop              = 0x600b15d5; /* [3],[4],[5],[7],[8]: bx lr */
fw_w9_set_colors       = 0x600b15dd; /* [6]: str r1, [r0, #0x30] */
fw_w9_delete           = 0x600b15e1; /* [9]: operator delete(this, 0x38) */

fw_menu_build_synth    = 0x600ea4c9; /* FUN_600ea4c8(ctx, menu, slot): per-synth submenu builder */

/* data */
fw_topt_none           = 0x60078204; /* const u32 0x7fedcba9: tiny_optional<float> without value */
fw_eve_wp              = 0x20002816; /* u16 RAM_CMD write offset (DAT_20002816) */
fw_audio_out           = 0x2000281c; /* s16[2][128][2] audio DMA buffer, final output (audio IRQ 0x13a18) */
fw_edit_lock_depth     = 0x2001daf4; /* nesting counter for the audio-IRQ edit lock */
cfw_ram                = 0x20002700; /* 256 B scratch in the unused DTCM gap 0x200022E8..0x200027FF */

/* screen brightness / dim (screen.c) */
fw_app_update          = 0x600c84f5; /* app vtable 0x60078224 slot 1: called once per main-loop pass */
fw_menu_build_settings = 0x600eac41; /* FUN_600eac40(ctx, menu): stock Settings builder */
fw_menu_back           = 0x60105085; /* FUN_60105084(menu screen): one menu level back */
fw_file_open           = 0x600aa8d1; /* FUN_600aa8d0(file, path, mode) -> 1 ok (File wrapper, 0x70 B) */
fw_file_read           = 0x600aa921; /* FUN_600aa920(file, buf, n) -> bytes or -1 */
fw_file_write          = 0x600aa95d; /* FUN_600aa95c(file, buf, n) -> bytes or -1 */
fw_file_close          = 0x600aa895; /* FUN_600aa894(file) */
fw_in_pad              = 0x600d9cd5; /* input listener vtable 0x6007b27c [0..4] */
fw_in_button           = 0x600d9d95;
fw_in_knob             = 0x600d9d3d;
fw_in_track            = 0x600d9d6d;
fw_in_encoder          = 0x600d9e5d;
fw_millis              = 0x200064c8; /* u32 ms counter (FUN_600b1394) */
fw_pattern_switch      = 0x600db229; /* FUN_600db228(pm, pattern, mode 1 nextstep / 2 trackend, 0) */
fw_menu_build_file     = 0x600e9fd9; /* stock "File" builder FUN_600e9fd8(ctx, menu) */
fw_f_opendir           = 0x600bf1f9; /* FatFs f_opendir(DIR*, path) (wrapper FUN_600aa804) */
fw_f_readdir           = 0x600bf319; /* FatFs f_readdir(DIR*, FILINFO*): fattrib +0xc, fname +0x1a */
fw_f_closedir          = 0x600bf2ad; /* FatFs f_closedir(DIR*) */
fw_rmtree              = 0x600aa6cd; /* FUN_600aa6cc(_, char *path, char *path_end, FILINFO*): recursive delete */
fw_f_mkdir             = 0x600bf491; /* FatFs f_mkdir(path) -> FRESULT (0 ok, 8 exists) */
fw_pool_add_folder     = 0x600ca259; /* (pool, name) -> 0 added, 2 exists, 3 full: pool folder (meta = *(pool+8)) */
fw_pool_find_folder    = 0x600ca3b5; /* (pool, name) -> folder index or -1 */
fw_import_files        = 0x600ccea1; /* (project, dir, char *names[], count, folder) -> 0 ok, 1 no memory, 2 255 samples */
fw_fmt_startend        = 0x600da3dd; /* Sample Start / End descriptors vt[0]: (self, value, mode, buf, len, step-or-track) value text */
fw_led_set             = 0x600ac581; /* LED object vt+4: (leds, led, rgb *, rgb2 *, mode) */
fw_led_trig_color      = 0x60078dac; /* u8[3] the stock colour of a plain trig pad (55 77 55) */
fw_step_mode_vt        = 0x60080eb8; /* vtable of the step-grid pad mode */
fw_fx_preset           = 0x600ec9a9; /* (fx, type 0 reverb / 1 delay / 2 limiter, index): the stock preset rows' apply */
fw_fx_saturation       = 0x600ecaad; /* (fx, type): the stock Saturation row's apply */
fw_delay_presets       = 0x6007868c; /* {params *, name *}[20] */
fw_reverb_presets      = 0x6007872c; /* [11] */
fw_limiter_presets     = 0x60078784; /* [18] */
fw_sat_names           = 0x6007ac70; /* const char *[5] */
fw_scale_names         = 0x6007a828; /* const char *[39] */
fw_reader_gain         = 0x60112b75; /* (reader): sets this block's gain from the fade in / out */
fw_tgt_get             = 0x6010ddd9; /* (target) -> current value */
fw_tgt_set             = 0x6010dd81; /* (target, mask, value) */
fw_inst_vt_sample      = 0x600892e4; /* sample instrument vtable */
fw_menu_build_cc       = 0x600e7cf9; /* (ctx, menu, channel): a channel's 8 CC rows (MIDI > CC Mapping) */
fw_step_toggle         = 0x600de6f1; /* (selection, track, step): as a step pad: default step + on, or off */
