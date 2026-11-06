# Notes on the official firmware

What's been worked out so far, as a starting point for your own features. These are observations from reverse engineering and testing on a virtual device. Unless it says otherwise, nothing here has been confirmed by Polyend, so check for yourself before you rely on it. Addresses are for Play+ 1.3.0 unless noted. `cfw/targets/*/symbols.ld` and `target.h` have them for each version.

## The hardware and the file format

- Play+ and RT Play: NXP i.MX RT (Cortex-M7). K66 Play: a different processor and a separate image inside the same firmware file.
- A `.ppf` is Intel HEX text with CRLF line ends and varying record lengths. The bootloader lives below `0x60030000` on the Play+ and `0x60020000` on the RT Play, and isn't in the file.
- The update menu lists names starting with `PlayPlus` (or `PolyendPlay`) and ending in `ppf`. Keep names to 24 characters.
- Records added after the end of the image do get programmed on the Play+. This hasn't been tested on a real RT Play or K66.

**Memory map (Play+):** ITCM at `0x8` is copied from flash `0x6017625C`. DTCM `.data` at `0x20000000` from `0x6018A74C`. BSS from `0x20002800`. SDRAM at `0x80000000`.

## Input

Events arrive at the input listener's vtable (slots: pad, button, knob, touch, encoder).

- **Buttons:** 0 and 3 are the keys left of the screen, 6 the encoder click, 7 Back, 8 Play, 10 Shift. States: 0 press, 1 release, 2 held.
- **Pads:** index = row × 20 + column. Columns 0 to 15 are steps, and 16 to 19 are the side pads. Column 19 is track select.
- **Knobs:** 0 Master / Limiter, 1 Note / Microtune, 2 Sample / Folder, 3 Reverb / Delay, 4 Chance / Action, 5 Tempo / Swing, 6 Volume / Pan, 7 Start / End, 8 Overdrive / Bit Depth, 9 Randomize, 10 Track Length / Play Mode, 11 Filter / Resonance, 12 Attack / Decay, 13 Repeat, 14 Move.
- There's no "current track" in the firmware. FreePlay follows the last step pad touched.

## Steps and tracks

The selection object is at `ctrl + 0x2c`. From there: pattern view → `vt[0](track)` → track → `vt[0](step)` → step. A track's `vt + 4` returns its length.

| Step vtable offset | Does |
|---|---|
| `+0x08` / `+0x0c` | set / get trig on |
| `+0x10` | `setParam(idx, value)` |
| `+0x18` | `getParam(idx, &out)`: false if that step doesn't have its own value |
| `+0x3c` | clear |
| `+0x40` | track type: 0 sample, 1 MIDI or synth |

Sample step parameters: 0 Volume, 1 Pan, 2 Filter, 3 Resonance, 4 Bit Depth, 5 Overdrive, 6 Note, 7 Delay, 8 Reverb, 9 Sample, 10 Start, 11 End (0 to 32767), 12 Microtune, 13 Attack, 14 Decay, 15 Folder.

A stock function toggles a step the way a pad does, copying the default step when turning it on (`fw_step_toggle`).

The audio interrupt reads steps, so edits are wrapped in a lock (`cfw_edit_lock`, `cfw_edit_unlock`).

## The sample voice

Each sample track has one voice: an instrument object with an embedded reader at `+0xd8`. Reader fields, Play+ (RT in brackets):

| Offset | Field |
|---|---|
| `+0x18` | sounding |
| `+0x1c` | sample data |
| `+0x2c` / `+0x30` (`+0x28` / `+0x2c`) | start and end frames |
| `+0x34` / `+0x38` (`+0x30` / `+0x34`) | the same, in 128-frame blocks |
| `+0x3d` (`+0x39`) | reverse |
| `+0x48` (`+0x44`) | current frame |
| `+0x60` / `+0x64` (`+0x58` / `+0x5c`) | block where the fade in ends / the fade out starts |
| `+0x68` (`+0x60`) | gain for this block, 16.16 |

- Attack and Decay are parts of the played region (0 to 10000), not times.
- **There's no note off on a sample track.** A voice ends at its End, or when a new note starts on the same track. Stop doesn't end it.
- The gain goes through a volume curve, so 30% gain is about -26 dB.
- The block function reads the position before it asks for the gain and stores it after the 128 frames. A position written during the gain call is lost.

**Loop points look possible.** Writing the position just after the block stores it kept a 0.5 s loop going past the end of a 2 s sample. It isn't built. Jumps land on a block edge (up to 2.9 ms late), looped notes would need something to stop them, and Decay and Release would need to be based on time.

## Changing sound while a note plays

Each instrument and mixer holds **live targets**: 16-byte objects with the current value at `+2`, min at `+8` and max at `+0xa`. `FUN_6010dd80(target, 1, value)` stores a new value, and the engine picks it up at the next block. No new DSP is needed.

- `instrument->vt[0x18](id)`: 0 Volume, 1 Filter, 2 Resonance, 3 Bit Depth, 4 Overdrive, 5 Note (cents), 6 Microtune, 8 Start, 9 End, 10 Attack, 11 Decay.
- `mixer->vt[0x10](slot, id)`: 1 Pan, 2 Delay, 3 Reverb.
- Start, End, Attack and Decay are only read at note on.
- A note overwrites its targets, so FreePlay treats any value it didn't write itself as the new base.

To find them: the playing pattern buffer (`seq vt + 8`), then `+0x98 + track × stride` holds `{instrument, mixer, slot}`. The stride is `0xbb4` on the Play+ and `0xba8` on the RT Play. Only tracks 1 to 8 are sample tracks.

## The display

An EVE chip, driven with display lists. The display manager holds 50 widget slots and composes them from slot 49 down to 0. Logical screen size is 320 × 240. Stock lists use 26 px rows with the first row centred at y = 42, a 24 px highlight in colour `0x111111`, and 6 visible rows (7 in the root menu).

## MIDI

CC Mapping is a table in the project: `project + 0x201 + channel × 8`, with 8 CC numbers per channel. Channels 0 to 15 are Jack and 16 to 31 are USB. The slots, in order: Cutoff, Resonance, Sample Attack, Sample Decay, Reverb Send, Delay Send, Overdrive, Bit Depth.

## Project files

The open project lives in `/workspace`. Saved projects are in `/Projects/<name>/`. FreePlay keeps its own files next to them, because *Save* only copies stock files: `cfwSettings`, `cfwColors`, `cfwCC` and `cfwMod.<project>`.

## Reading the SD card from a Mac

On at least one card and reader, reads of a file covering more than one 32 KB block sometimes returned wrong data, while one-block reads were right. A fresh copy landing elsewhere on the card usually read back clean. It may be the reader, the card or the driver. If you copy firmware from a Mac and a hash doesn't match, that's worth knowing.
