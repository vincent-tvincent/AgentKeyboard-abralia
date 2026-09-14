# Original Keychron V3 ANSI — no knob

[Keyboard models](README.md) · [Controls](../controls.md) · [Lighting](../effects.md)

This original wired V3 ANSI port is **experimental and physically unverified**.
Its profile and key mapping were checked offline against source. Use
`builtin:keychron-v3-ansi-effect25`, USB **3434:0330**, and firmware target
`keychron/v3/ansi`. Do not substitute the V3 8K or ANSI encoder profile.

## Locate the controls

| Abralia role | Physical position / profile element | Matrix | Control ID |
| --- | --- | --- | --- |
| Mode: double-tap; hold while active | Top-right **lighting key** / `LIGHTING_KEY` | `[3,14]` | `0x030E` |
| Pick up a call | Screenshot / Print Screen position / `SCREENSHOT` | `[0,14]` | `0x000E` |
| Mute a call | Middle top-right position / `SCROLL_LOCK` | `[0,15]` | `0x000F` |
| Sort, exit focus or dismiss guide | Escape / `ESC` | `[0,0]` | `0x0000` |
| Visible task slots | Physical F1–F12 | `[0,1]`–`[0,12]` | `0x0001`–`0x000C` |

The three keys above the navigation cluster run left-to-right as **pickup,
mute, mode**. The mode key's default action is RGB effect cycling (`UG_NEXT`),
not Pause. It is a physical lighting-key gesture at `[3,14]`, even after key
remapping. The middle key defaults to Siri/Cortana in the source keymap despite
the profile's `SCROLL_LOCK` position name.

## Browse without a knob

Double-tap the lighting key to enter Agent Mode, then hold the same key for about
0.8 s. Use Page Up/Down or Up/Down for pages, Left/Right for slots, Home/End for
the global first/last task, and Enter to confirm. Escape switches sorting while
navigation is armed. See the full [controls reference](../controls.md).

This model has no encoder or knob press. Their absence does not remove keyboard
navigation, call controls, timed mutes, sorting or gap closing. Navigation ends
after its 15 s idle timeout, another lighting-key hold, or leaving Agent Mode.

## Ordinary input and verification boundary

The original V3 firmware ports retain the source's default keymap. macOS base
F-row defaults include media/system actions, while Windows base defaults use
F-key codes. Abralia binds the physical F-row only in Agent Mode; ordinary
inactive behavior follows the current keymap.

The profile declares a 6×16 matrix, zero encoders and 87 LEDs. Matching
`abralia_host_interaction` firmware and enabled effect 25 are required for agent
controls. A built firmware image or simulator rendering does not establish that
these controls or LEDs work on a physical original V3; that validation remains
outstanding.

## Detailed references

- [Explicit JSON profile](../../../abralia/desktop/src/abralia/resources/profiles/keychron-v3-ansi-effect25.json)
- [Complete Control ID and default-keymap table](../../keychron-v3-ansi-control-id-lookup.md)
- [Experimental original V3 firmware guide](../../../abralia/firmware/qmk-userspace/README.md#original-v3-sibling-variants-experimental)

Documentation: [Apache-2.0](../../../LICENSE.md).
