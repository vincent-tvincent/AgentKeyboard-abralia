# Keyboard models

[User guide](../README.md) · [Shared controls](../controls.md) · [Lighting](../effects.md)

Choose the exact model and layout. Similar names do not imply compatible USB
identity, matrix positions, LED geometry or firmware. These profiles cover the
wired ANSI variants listed below, not V3 Max, V3 Ultra, ISO or JIS keyboards.

| Model | Mode-key position | Knob | Current evidence |
| --- | --- | --- | --- |
| [Keychron V3 8K ANSI encoder](keychron-v3-8k-ansi-encoder.md) | Top-right Pause position | Yes | Reference hardware |
| [Original Keychron V3 ANSI](keychron-v3-ansi.md) | Top-right lighting-key position | No | Experimental; offline verification only |
| [Original Keychron V3 ANSI encoder](keychron-v3-ansi-encoder.md) | Top-right lighting-key position | Yes | Experimental; offline verification only |

All share the [control vocabulary](../controls.md). Model pages identify the
physical differences and link to complete Control ID/default-keymap tables.
A knob is an optional browsing convenience; hold-mode keyboard navigation does
not require one.

## Profiles are the source of physical placement

The selected profile records the reserved mode key in
`interaction.toggle_matrix`, other physical elements in `elements`, and encoder
availability in `keymap.encoder_count`. The backend resolves these physical
controls rather than searching for a key currently mapped to `KC_PAUS`, Print
Screen or another OS keycode.

The original V3's lighting key is in the same top-right visual area as the V3
8K's Pause key, but has a different matrix address and ordinary action. A VIA
key remap does not move Abralia's mode gesture or its physical pickup/mute
positions. The low-level keycode lookup API is separate from these backend
physical bindings.

Matching a connected device descriptor does not prove that compatible firmware
is installed. Agent controls require the matching `abralia_host_interaction`
firmware and enabled effect 25. The `led_only_not_interactable` variant supports
lighting only and cannot provide agent input controls; ordinary keyboard input
still works. The backend
checks capabilities and refuses an already owned control session. See the
[profile catalog](../../../abralia/desktop/src/abralia/resources/profiles/README.md)
and [firmware guide](../../../abralia/firmware/qmk-userspace/README.md).

## Adding another model to this guide

Keep common actions in [controls](../controls.md) and common effects in
[lighting](../effects.md). Add one model page using this checklist:

1. Record exact product/layout, USB identity, explicit profile ID and firmware
   target. Link the profile and its source/validation status.
2. Locate the mode key from `interaction.toggle_matrix`. State its physical
   position, profile element name and matrix/Control ID; never assume Pause.
3. List pickup/mute positions, Escape, F-row and optional knob differences.
   Link a complete generated lookup table instead of duplicating it here.
4. Explain ordinary inactive actions that differ from the reference keyboard.
   Use source defaults as defaults; do not present them as a user's live keymap.
5. Mark what is implemented, tested offline, and physically verified separately.
   A simulator illustration or successful firmware build is not hardware proof.
6. Add the model to this index. Reuse the shared guide, adding only genuine
   layout-specific limitations or instructions to the model page.

The [compatibility-layout API](../../../abralia/desktop/COMPATIBILITY_LAYOUTS.md)
can define logical regions, but cannot relocate the firmware-reserved mode key.

Documentation: [Apache-2.0](../../../LICENSE.md).
