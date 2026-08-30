# Host Interaction ControlId lookup tables

Choose the exact wired ANSI variant:

| Keyboard | Default-keymap lookup | Physical controls covered |
| --- | --- | --- |
| V3 8K ANSI encoder | [Keychron/Abralia comparison](keychron-v3-8k-official-control-id-lookup.md) · [Abralia-only view](abralia-v3-8k-default-control-id-lookup.md) | 88 matrix controls and two encoder directions |
| Original V3 ANSI, no knob | [Keychron/Abralia comparison](keychron-v3-ansi-control-id-lookup.md) | 87 matrix keys; no encoder |
| Original V3 ANSI encoder | [Keychron/Abralia comparison](keychron-v3-ansi-encoder-control-id-lookup.md) | 88 matrix controls and two encoder directions |

Every comparison covers macOS base layer 0 and Windows base layer 2, with
separate Keychron and Abralia columns. Original V3 ports preserve the upstream
default keymap, so those columns match. Their tables were generated from source
without hardware and do not establish physical validation.

Do not reuse V3 8K matrix addresses for the original V3: the original's reserved
top-right lighting key is `[3,14]` / `0x030E`, not `[0,16]` / `0x0010`. Its
default action is RGB effect cycling, not Pause. These tables do not cover V3
Max, V3 Ultra, ISO, JIS, or ABNT2 variants.

These are human-readable defaults, not persistent runtime truth. The desktop
API's keycode mode always reads the live VIA mapping and fans out to all
matching controls using firmware-reported matrix and encoder capabilities.
Direct-Control-ID mode accepts the developer-supplied ID without loading these
tables or assuming a physical layout.

For live discovery without consulting either table, run the guarded
[`host-interaction-control-inspector`](../experiments/host-interaction-control-inspector/README.md).
It assigns unique volatile binding IDs to all firmware-reported controls and
prints both the binding ID and Control ID whenever a control is pressed.

Each page records its own source revisions and generation method. The original
V3 pages also record the exact JSON profile snapshot hash. See the
[profile catalog](../abralia/desktop/src/abralia/resources/profiles/README.md)
for the corresponding desktop configuration files.
