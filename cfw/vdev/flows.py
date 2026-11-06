"""Multi-step UI flows on the virtual Play+ 1.3.0, built from the firmware's own screens.

Button ids (listener slot 1): 0/3/6 enter (menus: on press), 7 back, 6 Menu (opens on release),
9 third soft key under the display ("Load Wav", "Auto Name"), 10 Shift. Encoder = slot 4."""

BTN_ENTER, BTN_BACK, BTN_MENU, BTN_SOFT3 = 0, 7, 6, 9
MENU_SAMPLES, SAMPLES_LOAD_FILES = 1, 2


def open_menu(s):
    s.tap(BTN_MENU, after_ms=500)


def enter_item(s, index, after_ms=400):
    """In an open menu: highlight item `index` (counted from the top; menus remember their last
    row, so go to the top first), then enter."""
    s.turn(-64, after_ms=120)
    for _ in range(index):
        s.turn(1, after_ms=120)
    s.tap(BTN_ENTER, after_ms=after_ms)


def load_samples(s, file_indices, settle_ms=3000):
    """Menu > Samples > Load Files or Folders, then for each index (rows below "/.." in /Samples,
    ascending) load that file into a new pool folder with an auto-generated name. The browser
    stays open after each load with the loaded file highlighted. Ends back on the pattern screen."""
    open_menu(s)
    enter_item(s, MENU_SAMPLES)
    enter_item(s, SAMPLES_LOAD_FILES, after_ms=800)
    row = 0                           # "/.."
    for idx in file_indices:
        for _ in range(idx + 1 - row):
            s.turn(1, after_ms=150)   # the highlighted file is the marked one
        row = idx + 1
        s.tap(BTN_SOFT3, after_ms=800)        # Load Wav -> "Select Sample Folder"
        s.tap(BTN_ENTER, after_ms=800)        # <Add New Folder> -> keyboard
        s.tap(BTN_SOFT3, after_ms=500)        # Auto Name
        s.tap(BTN_ENTER, after_ms=settle_ms)  # confirm: copy to workspace/samples, load the pool
    close_menu(s)


UI_MENU_SCREEN, UI_BROWSER_SCREEN = 0x10, 0x08


def ui_manager(vp):
    """listener (app + 0x1c) + 8 -> UI manager"""
    return vp.r32(vp.profile['app'] + 0x1c + 8)


def current_screen(s):
    vp = s.vp
    ui = ui_manager(vp)
    cur = vp.r32(ui + vp.profile['ui_cur'])
    return {vp.r32(ui + UI_MENU_SCREEN): 'menu', vp.r32(ui + UI_BROWSER_SCREEN): 'browser'}.get(cur, 'other')


def close_menu(s, tries=6):
    """Back out of the browser and the menu tree (Back at the menu root closes the menu).
    Button 6 can't be used: inside menus it is an enter key."""
    for _ in range(tries):
        if current_screen(s) == 'other':
            return True
        s.tap(BTN_BACK, after_ms=400)
    return current_screen(s) == 'other'
