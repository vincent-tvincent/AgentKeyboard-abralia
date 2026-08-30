# User compatibility layouts

Abralia hardware profiles remain stable descriptions of physical geometry,
matrix positions, LED addresses, regions, and adapter capabilities. A user
compatibility layout is a separate JSON overlay that can add or replace region
definitions without editing the bundled hardware profile.

Compatibility regions are shared by RGB and Host Interaction. The loader
normalizes every selector once to a firmware `ControlId`. RGB then joins that
control to the hardware profile's geometry and LED address; Host Interaction
uses the same Control ID directly. The overlay does not read the live VIA
keymap or change persistent keyboard configuration.

## Authoring format

The complete F-row navigation example is in
[`examples/compatibility/f-row-navigation.json`](examples/compatibility/f-row-navigation.json).

```json
{
  "schema_version": 1,
  "profile_id": "keychron-v3-8k-ansi-encoder-effect25",
  "alias_imports": ["./f-row-navigation-aliases.json"],
  "matrix_aliases": {
    "LOCAL_ACTION": [0, 7]
  },
  "regions": [
    {
      "id": "navigation_cluster",
      "rows": [
        ["NAV_INSERT", "NAV_HOME", {"matrix": [0, 3]}],
        [
          {"element": "F4"},
          {"matrix": [0, 5]},
          "LOCAL_ACTION"
        ]
      ],
      "strategies": ["row_key_index"]
    }
  ]
}
```

Selectors are explicit:

- `"NAV_HOME"` uses a name declared in `matrix_aliases` or an imported alias
  file.
- `{"matrix": [0, 3]}` directly selects a matrix row and column.
- `{"element": "F4"}` selects a canonical hardware-profile element. Profile
  aliases are accepted through this explicit form.

Rows define both region membership and row/key-index ordering. Repeating the
same resolved control in one region is an error. A region with an existing ID
replaces that hardware-profile region only in the resolved runtime view; a new
ID adds a region.

## Separate alias files

An imported alias file contains only aliases for one hardware profile:

```json
{
  "schema_version": 1,
  "profile_id": "keychron-v3-8k-ansi-encoder-effect25",
  "matrix_aliases": {
    "NAV_INSERT": [0, 1],
    "NAV_HOME": [0, 2]
  }
}
```

Import paths are resolved relative to the compatibility layout. Imports must
remain within that directory, must use the same `profile_id`, and cannot import
other files. Absolute paths, URLs, directory escapes, and duplicate alias names
are rejected. Inline and imported aliases use the same validation rules; no
definition silently overrides another.

## Validate and inspect

These commands do not access hardware:

```sh
.venv/bin/abralia-layout validate \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  abralia/desktop/examples/compatibility/f-row-navigation.json

.venv/bin/abralia-layout resolve \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  abralia/desktop/examples/compatibility/f-row-navigation.json \
  --output resolved-layout.json
```

The resolved export records every Control ID, matrix address, matched profile
element, LED address, RGB compatibility issue, and alias source file. It is an
inspection artifact, not an automatic cache, and the source overlay is never
rewritten.

## RGB usage

Pass the overlay to `RgbController.open("builtin:keychron-v3-8k-ansi-encoder-effect25")` or the CLI:

```python
with RgbController.open(compatibility_source="my-layout.json") as controller:
    lease = controller.display([scene])
```

```sh
.venv/bin/abralia-rgb render-canvas canvas.json \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --compatibility my-layout.json \
  --target navigation_cluster \
  --strategy row_key_index
```

Controls missing from the hardware profile, or profile elements without an RGB
address, remain in the shared resolved layout for Host Interaction. RGB skips
them without shifting later row cells and returns explicit
`unrenderable_controls` diagnostics. Rendering fails only when a requested
target contains no RGB-capable controls.

## Host Interaction usage

```python
from abralia.interaction import HostInteractionController
from abralia.layout import load_compatibility_layout
from abralia.rgb import load_profile

profile = load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
layout = load_compatibility_layout(profile, "my-layout.json")
# protocol was constructed with profile=profile.device_profile
controller = HostInteractionController(protocol, compatibility=layout)

controls = controller.region_controls("navigation_cluster")
controller.set_region_controls("navigation_cluster", binding_id=1001)
controller.activate_region("navigation_cluster", lease_ms=30_000)
```

Matrix compatibility aliases are user-authored physical addresses. They are
not QMK keycodes and do not follow live VIA remaps automatically. Existing live
keycode lookup remains a separate opt-in API.

Abralia-authored desktop code and this documentation are Apache-2.0. See the
repository-root `LICENSE.md`.
