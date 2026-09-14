# Keychron V3 8K ANSI encoder

[Keyboard models](README.md) · [Controls](../controls.md) · [Lighting](../effects.md)

This wired ANSI encoder variant is Abralia's reference hardware. Use profile
`builtin:keychron-v3-8k-ansi-encoder-effect25`, USB **3434:0F30**, with the matching
`keychron/v3_8k/ansi_encoder` Host Interaction firmware. This page does not apply
to original V3, V3 Max/Ultra or another layout.

## Locate the controls

| Abralia role | Physical position / profile element | Matrix | Control ID |
| --- | --- | --- | --- |
| Mode: double-tap; hold while active | Top-right **Pause** / `PAUSE` | `[0,16]` | `0x0010` |
| Pick up a call | Screenshot / Print Screen / `SCREENSHOT` | `[0,14]` | `0x000E` |
| Mute a call | Scroll Lock / `SCROLL_LOCK` | `[0,15]` | `0x000F` |
| Change knob function | Knob press / `KNOB_PRESS` | `[0,13]` | `0x000D` |
| Sort, exit focus or dismiss guide | Escape / `ESC` | `[0,0]` | `0x0000` |
| Visible task slots | Physical F1–F12 | `[0,1]`–`[0,12]` | `0x0001`–`0x000C` |

The three keys above the navigation cluster run left-to-right as **pickup,
mute, mode**. The knob is immediately to their left. Its clockwise and
counterclockwise controls are encoder 0 IDs `0x4000` and `0x8000`; knob press is
a separate matrix control.

Double-tap Pause to enter Agent Mode, then hold that same physical key for about
0.8 s to enable keyboard navigation. The shared
[navigation table](../controls.md#browse-pages-and-preview-tasks) applies in full.
No VIA keycode search is needed to find the mode position.

## Ordinary input and requirements

Outside Agent Mode the knob uses its ordinary configured behavior. Reference
defaults use volume rotation and mute on knob press; live remaps can differ.
The Abralia V3 8K default keymap exposes ordinary F-key codes on the base layers;
the complete tables show the Keychron/Abralia differences.

The profile declares a 6×17 matrix, one encoder and 87 LEDs. These counts describe
the profile, not a reason to choose it for a different keyboard with similar
dimensions. Agent input requires matching Host Interaction firmware, its
single-tap capture capability, and effect 25 enabled. The host preserves the
keyboard's brightness upper bound.

Reference-hardware status applies to this exact model; it does not establish
physical validation for sibling profiles or for every future feature.
The guide's animated illustrations are simulated host output.

## Detailed references

- [Explicit JSON profile](../../../abralia/desktop/src/abralia/resources/profiles/keychron-v3-8k-ansi-encoder-effect25.json)
- [Keychron/Abralia default Control ID comparison](../../keychron-v3-8k-official-control-id-lookup.md)
- [Abralia default Control ID table](../../abralia-v3-8k-default-control-id-lookup.md)
- [Firmware build and flash guide](../../../abralia/firmware/qmk-userspace/README.md)

Documentation: [Apache-2.0](../../../LICENSE.md).
