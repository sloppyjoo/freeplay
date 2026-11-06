# Contributing to FreePlay

You don't need to write code to help. The most useful thing right now is people trying it and telling me what happened.

## Reporting a bug

Please [open an issue](../../issues/new/choose). The form asks for your device, what you did and what happened instead. A photo of the screen helps a lot, and so does knowing the last thing you pressed before something froze.

If you have an **original Play**, reports are especially welcome, whether it worked or not. It has only ever run on a virtual device.

## Things that would help most

- **Testing on a real RT Play.** Install it and try the checklist in [docs/TESTING.md](docs/TESTING.md).
- **K66 support.** The K66 Play has a different processor. There's a small start in `cfw/targets/play-1.6.0-k66`, which only adds a basic Slice list and has never run on hardware. It isn't in the release yet. The first open question: does the K66 loader program the records added past the end of the image?
- **Checking CC presets.** If you own one of the synths in [`cfw/presets/cc_presets.csv`](cfw/presets/cc_presets.csv), load the preset and tell me if the controls do what the list says.
- **Dropouts.** There's no CPU meter yet, so reports of audio glitches under heavy LFO use are useful. Please say what was playing.
- **Synth tracks and LFOs.** LFOs only move sample tracks at the moment.

## Making your own version

Fork it and go ahead. That's what it's for. [docs/DEVELOPING.md](docs/DEVELOPING.md) explains how to build it and how it fits together, and [docs/FIRMWARE_NOTES.md](docs/FIRMWARE_NOTES.md) lists what's known about the official firmware so far.

If you publish your own build, please make it clear it's your version, and share it the same way this project does: as a patch people apply to their own firmware file.

## Three rules for pull requests

1. **No Polyend code or firmware in this repository.** No `.ppf` or `.bin` files, no pasted decompiler output, and no code copied out of the official firmware. Addresses and notes on how things behave are fine. This is the rule that keeps the project possible.
2. **Don't rebuild a `.ppf` from scratch.** Change only the bytes you need in the official file's text and update that line's checksum. `cfw/tools/ppf.py` does this for you. Polyend's files use varying record lengths, so writing the file back out wouldn't give an identical result.
3. **Test what you change.** Run the build, which verifies every changed byte, and the test for the feature you touched. Say in your pull request what you tested and whether it was on hardware or the virtual device.

## License

By contributing, you agree that your contribution is shared under this project's MIT license. Preset data in `cfw/presets/` stays under CC BY-SA 4.0, as it's derived from MIDI Guide data. See [LICENSE](LICENSE).
