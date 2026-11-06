# Testing checklist

If you've installed FreePlay, try these and [tell me what happened](../../../issues/new/choose). You don't need to do all of them. If you have an original Play, even "it booted and the menu looks right" is really useful.

Start with a project you don't mind losing, and load a loop or break into it.

## Basics

- [ ] It updates, restarts and works as normal.
- [ ] *Settings → Firmware* shows a version with `FreePlay` in it.
- [ ] Your existing projects load and sound the same.
- [ ] The Menu has LFO and Matrix rows, and *Samples* has a Slice row.

## Slicer

- [ ] Press a select pad: its pad blinks blue.
- [ ] On an empty track, press Sample and choose a sample.
- [ ] Turn the encoder: the slices and the blue squares change. The step pads show the same thing.
- [ ] Shift + encoder zooms. Turning the encoder then scrolls.
- [ ] Click. Press Play. Does it sound like your loop? (Check the tempo matches.)
- [ ] Try the Every step and My trigs layouts.
- [ ] Use Points to place cuts by hand, then apply.
- [ ] Undo brings your trigs back.
- [ ] Shift + click makes a folder of slices.

## LFOs and Matrix

- [ ] Pick track 1, hold Map and touch the Filter knob. Press Play: does the filter move?
- [ ] Pick another track and map something else to the same LFO. Each should move only on its own track.
- [ ] Open the Matrix. Does each knob change the square in the same position?
- [ ] Do the same thing with the encoder instead of the knobs.
- [ ] Open the Filter knob page while it's playing. Does the curve move?
- [ ] **Try fast rates and Volume. Do you hear stepping or zipper noise?**
- [ ] **Map lots of things across all tracks. Do you hear dropouts?**
- [ ] Save, then reload the project. Are your LFOs still there?

## Sample tools

- [ ] Turn the Start / End knob while it plays: does an amber line move through the waveform?
- [ ] On Attack / Decay, does the line move through the envelope?
- [ ] Hold Shift there and set Sustain and Release. Do they sound right?

## CC presets

- [ ] In *MIDI → CC Mapping → (channel) → Load Preset*, pick your synth.
- [ ] Do the CC knobs control what `cfw/presets/cc_presets.csv` says?
- [ ] Save a preset, then load it into another channel.

## Everything else

- [ ] Hold Shift on a knob page and pick a preset.
- [ ] Colour a folder in *View Sample Pool*.
- [ ] Tap tempo on the Tempo page with Shift + top key.
- [ ] Screen Brightness, Screen Dim and Knob Touch in Settings.
- [ ] Delete a project you don't need from *File → Delete Project*.

## Going back

- [ ] Flash the official firmware. Does it all work as before?
