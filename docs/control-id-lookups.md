# Host Interaction ControlId lookup tables

Two generated default-keymap views are available for the Keychron V3 8K ANSI
encoder:

- [Side-by-side Keychron and Abralia defaults](keychron-v3-8k-official-control-id-lookup.md)
- [Abralia-only Host Interaction default](abralia-v3-8k-default-control-id-lookup.md)

Both views cover macOS base layer 0, Windows base layer 2, matrix keys, knob
press, and both encoder directions. The primary table places the pinned
Keychron mappings and this project's Abralia mappings in adjacent columns so
their differences are visible without switching documents. The Abralia-only
view remains available as a compact project-default reference.

These are human-readable defaults, not persistent runtime truth. The desktop
API's keycode mode always reads the live VIA mapping and fans out to all
matching controls using firmware-reported matrix and encoder capabilities.
Direct-Control-ID mode accepts the developer-supplied ID without loading these
tables or assuming a physical layout.

For live discovery without consulting either table, run the guarded
[`host-interaction-control-inspector`](../experiments/host-interaction-control-inspector/README.md).
It assigns unique volatile binding IDs to all firmware-reported controls and
prints both the binding ID and Control ID whenever a control is pressed.

Generated from Keychron/QMK revision `ee7390c3bbdc1f71a1cc8d54323f3f1d97868593` and Abralia public
revision `fee375b4e97179b0e5c7ea7b793241b927afaa8b`.
