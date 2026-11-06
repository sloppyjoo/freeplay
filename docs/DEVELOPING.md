# Developing FreePlay

How to build it, test it and add to it. You'll need to be comfortable with C and a command line, but you don't need any special hardware to start: a virtual Play runs on your computer.

## What's where

| Path | What it is |
|---|---|
| `cfw/src/` | The firmware code, in C. This is where features live. |
| `cfw/targets/` | One folder per firmware version: addresses (`symbols.ld`), offsets (`target.h`) and where each hook goes (`target.json`). |
| `cfw/tools/` | The build: linker, HEX patcher, verifier, disassembler and function matcher. |
| `cfw/vdev/` | The virtual Play. It boots the real firmware in an emulator. |
| `cfw/tests/` | Tests that drive the virtual Play through its own button and pad handlers. |
| `cfw/presets/` | The CC preset list and its source of truth. |
| `patches/` | The built patches the installer uses. They contain FreePlay's bytes only. |
| `patcher/` | The Python patcher and the browser patcher. |
| `index.html` | The installer page. |

## Setting up

You need Python 3.8 or newer and `clang`. On a Mac, `clang` comes with the Xcode command line tools.

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The tests also want `mtools` and `mkfs.fat` on your PATH, for building virtual SD cards.

Download the official firmware from Polyend and put the files here. Git ignores them, so they can't be committed by accident:

```
PlayPlus_v1.3.0.ppf
stock_firmware/PolyendPlay_v1.6.0.ppf
```

## Building

```
CC=clang .venv/bin/python cfw/build_all.py
```

Your files land in `cfw/release/`, each with a `.md` report listing every changed byte, every hook and the verification results. Builds from Apple's clang and other clang versions may differ in their bytes. That's expected. The installer checks the hash of the build in `patches/`, so regenerate `patches/` if you publish your own.

The build fails on purpose when something doesn't add up: a hook whose stock value isn't what it should be, a changed byte outside the expected areas, or a value in the code that looks like an address but doesn't match a known symbol.

## How a feature gets in

FreePlay doesn't replace the firmware. It adds code and redirects a few things to it:

- **Hooks.** A hook swaps one pointer in a table in the official firmware, or retargets one function call, so the firmware calls FreePlay's code. That code usually calls the original function too. They're all listed in `target.json`.
- **The cave.** A run of unused space inside the official image. All hook targets live here, so a hook always lands on code that exists.
- **The ext region.** New HEX records after the end of the image. Most features live here. The cave checks a header and a checksum (`cfw_ext_ok()`) before calling anything in it, so if an updater ever dropped those records, the device would fall back to stock behaviour and wouldn't crash.
- **Scratch RAM.** `cfw_ram` is a small unused RAM window of 256 bytes, and it's full. Larger state is allocated from the heap once per boot and hangs off the knob pages' state in `pages.c`. The RT Play's heap is small, so keep state small.

There are no writable sections and no libc. Avoid struct assignment, because the compiler turns it into a `memcpy` call that doesn't exist. `switch` statements can generate jump tables the verifier can't size. If the build complains, write it as `if` chains or a lookup table.

## Adding a screen

A panel is a stock display widget with its vtable replaced, so the firmware handles caching and drawing it into the frame. `cfw/src/slicer.c` is the best example to copy. It has:

- a `tick` function that runs in the main loop and asks for a redraw when something changed,
- a `render` function that writes an EVE display list,
- an `input` function that returns 1 for every event it uses.

Two things to watch for. The menu screen's update function doesn't run every pass, so drive redraws from your tick. And the display only draws a new frame when a widget changes, so closing a panel needs a redraw of whatever's under it, or a ghost of it stays on screen.

The audio path runs in an interrupt. Code called from `cfw_reader_gain` and `cfw_tgt_note` has to be quick and can't touch the file system or allocate memory.

## Testing

Each test boots a firmware image on the virtual Play and presses the real buttons. A test needs the image's `link.map` next to it, so copy both to a folder first:

```
mkdir -p /tmp/fp && cp cfw/build/playplus-1.3.0/*.ppf /tmp/fp/img.ppf && cp cfw/build/playplus-1.3.0/link.map /tmp/fp/
VPLAY_DIR=/tmp/fp .venv/bin/python cfw/tests/vdev_slicer.py /tmp/fp/img.ppf
```

The first run has to boot the firmware, which takes about 10 minutes. After that, it starts from a snapshot. Use a fresh `VPLAY_DIR` for a new image, since a test can leave files on its virtual card.

| Test | Covers |
|---|---|
| `vdev_slicer` | The Slicer |
| `vdev_mod` | Playheads, Sustain / Release, LFOs, Matrix, live knob pages, CC presets |
| `vdev_startend` | Sample Start / End editor |
| `vdev_pages`, `vdev_shift` | Knob-page visuals, Shift layers, Shift + encoder |
| `vdev_colors` | Folder colours |
| `vdev_tap`, `vdev_screen`, `vdev_delproj`, `vdev_synthrand` | Tap tempo, screen settings, Delete Project, synth randomizer |
| `vdev_menutree` | Walks every menu in stock and FreePlay and compares them. Rows you add need whitelisting here. |
| `vdev_slice`, `vdev_sliceview`, `vdev_points`, `vdev_export` | The older Slice panel, used only if the ext region is missing. These tests can't reach it in a normal build, so they're not part of a normal run. Other tests borrow their helpers. |

What the virtual Play **can't** tell you: real CPU load, audible problems like zipper noise, how pads and the screen really look, and anything about the physical knobs. It runs instructions, not cycles. Test on hardware before you trust something that touches audio.

**A faster loop:** while you're working, build only your target and run only the test you need:

```python
import sys; sys.path.insert(0, 'cfw/tools')
import build
build.build('cfw/targets/playplus-1.3.0/target.json')
```

## Updating the CC presets

Presets live in `cfw/presets/cc_presets.csv`. To regenerate `cfw/src/ccpresets.h`, clone [MIDI Guide](https://github.com/pencilresearch/midi) and run this. It refuses any line whose CC number and parameter name aren't in that device's file:

```
python3 cfw/tools/gen_cc_presets.py path/to/midi
```

## Porting to another firmware version

Most functions in a new firmware are the same as in an old one, apart from addresses. `cfw/tools/sigmatch.py` finds a function from one image in another by its masked bytes. Make a new `cfw/targets/` folder, fill in `symbols.ld` and `target.h`, and let the build tell you what doesn't match. Field offsets can change between versions, even when the code looks the same, so check them. The sample reader's are a good example: they differ between the Play+ and the Play.

## Publishing your own build

1. Build with your changes.
2. Copy `cfw/release/*.patch.json` into `patches/` under the names `index.html` expects.
3. Host `index.html`, `patcher/` and `patches/` anywhere that serves static files. GitHub Pages works.

Never publish the finished `.ppf`. It contains Polyend's firmware.
