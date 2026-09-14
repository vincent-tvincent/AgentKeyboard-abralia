# Original Keychron V3 ANSI encoder

[Keyboard models](README.md) · [Controls](../controls.md) · [Lighting](../effects.md)

This original wired V3 ANSI encoder port is **experimental and physically
unverified**. Its profile and key mapping were checked offline against source.
Use `builtin:keychron-v3-ansi-encoder-effect25`, USB **3434:0331**, and firmware
target `keychron/v3/ansi_encoder`. This is not the V3 8K encoder model.

## Locate the controls

| Abralia role | Physical position / profile element | Matrix | Control ID |
| --- | --- | --- | --- |
| Mode: double-tap; hold while active | Top-right **lighting key** / `LIGHTING_KEY` | `[3,14]` | `0x030E` |
| Pick up a call | Screenshot / Print Screen position / `SCREENSHOT` | `[0,14]` | `0x000E` |
| Mute a call | Middle top-right position / `SCROLL_LOCK` | `[0,15]` | `0x000F` |
| Change knob function | Knob press / `KNOB_PRESS` | `[0,13]` | `0x000D` |
| Sort, exit focus or dismiss guide | Escape / `ESC` | `[0,0]` | `0x0000` |
| Visible task slots | Physical F1–F12 | `[0,1]`–`[0,12]` | `0x0001`–`0x000C` |

The three keys above the navigation cluster run left-to-right as **pickup,
mute, mode**, with the knob immediately to their left. The lighting key defaults
to RGB effect cycling (`UG_NEXT`), not Pause. The middle key defaults to
Siri/Cortana despite the profile's `SCROLL_LOCK` position name. Abralia gestures
use these physical positions independently of the current keycodes.

## Keyboard and knob navigation

Double-tap the lighting key for Agent Mode; hold it for about 0.8 s for the
[keyboard navigation layer](../controls.md#browse-pages-and-preview-tasks).
The knob is optional convenience: active rotation flips pages or previews slots,
and knob press switches those functions. Navigation initially selects the
single-agent knob function. Its timeout/hold-disarm returns the knob to page
browsing.

Encoder 0 clockwise/counterclockwise use Control IDs `0x4000`/`0x8000`.
In inactive mode, the knob retains its normal configured behavior; source
defaults use volume rotation and mute on press. F-row defaults also differ by
OS layer: macOS media/system actions versus Windows F-key codes. Live remaps
can differ from these defaults.

## Requirements and verification boundary

The profile declares a 6×16 matrix, one encoder and 87 LEDs. Agent controls need
the matching `abralia_host_interaction` firmware and enabled effect 25.
Source checks and simulated rendering establish the implemented mapping, not
physical input/lighting acceptance on this original V3. Do not infer this
variant's hardware behavior from the reference V3 8K keyboard.

## Detailed references

- [Explicit JSON profile](../../../abralia/desktop/src/abralia/resources/profiles/keychron-v3-ansi-encoder-effect25.json)
- [Complete Control ID and default-keymap table](../../keychron-v3-ansi-encoder-control-id-lookup.md)
- [Experimental original V3 firmware guide](../../../abralia/firmware/qmk-userspace/README.md#original-v3-sibling-variants-experimental)

Documentation: [Apache-2.0](../../../LICENSE.md).
