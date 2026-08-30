# Abralia desktop RGB API

This directory contains Abralia's Python 3.11+ desktop developer API and CLI
for profile-driven keyboard RGB control. Version 0.2 requires an explicit JSON profile and supports the Keychron
effect-25 protocol family. Bundled profiles cover V3 8K ANSI encoder and the
experimental original V3 ANSI / ANSI encoder ports; original V3 hardware has
not been physically validated. macOS remains the primary desktop platform.

The implementation follows this fixed data flow:

```text
physical/canvas/semantic scene builders
-> abstract scene model
-> layout compatibility and scene compilation
-> resolved physical-key scenes
-> priority and overlay composer
-> physical-element to LED mapper
-> device adapter interface
-> Keychron effect-25 Raw HID adapter
-> firmware RGB endpoint
```

The semantic scene builder and temporary-binding overlay are extension
boundaries only. Version 0.2 intentionally defines no agent-specific slot
vocabulary or priority policy.

## Profiles and migration to 0.2

The caller or a higher application layer chooses the JSON. This library never
identifies a model to select a profile, and has no model/PID allowlist. USB
matching uses the selected profile's identity fields; ambiguous matches require
an explicit device index. The protocol adapter checks available firmware
capabilities before writing. RGB-only firmware does not require Host Interaction.

Bundled JSON and schemas now live in shared package resources, outside the RGB
package. See the [profile catalog](src/abralia/resources/profiles/README.md).
Use `load_device_profile(source)` for metadata-only interaction use, or
`load_profile(source).device_profile` alongside RGB geometry. Compatibility
layouts cannot replace hardware identity or the reserved interaction toggle.

This is a breaking API update: profile sources are mandatory, model-specific
open/find helpers and the implicit profile constant are removed, and both
`KeychronEffect25Adapter(..., profile=metadata)` and
`HostInteractionProtocolClient(..., profile=metadata)` require explicit metadata.
Use `SharedRawHidSession.open_profile(metadata, ...)` or
`HostInteractionProtocolClient.open_profile(metadata, ...)` instead. Existing
explicit `builtin:` IDs and schema-v1 RGB-only JSON remain valid; interaction
use additionally requires `interaction.toggle_matrix`.

## Install for development

From this directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

The runtime dependencies are `hidapi` and `jsonschema`.

## Read-only commands

Validate the bundled profile without accessing hardware:

```sh
.venv/bin/abralia-rgb profile validate builtin:keychron-v3-8k-ansi-encoder-effect25
```

List matching Raw HID interfaces without changing keyboard state:

```sh
.venv/bin/abralia-rgb devices --profile builtin:keychron-v3-8k-ansi-encoder-effect25
```

## Temporary physical-key scene

The following command writes immediately, keeps the guarded frame alive for
five seconds, and then restores the pre-command RGB snapshot:

```sh
.venv/bin/abralia-rgb render-keys \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --color W=#FF0000 \
  --color A=#00FF00 \
  --seconds 5
```

Every device command requires `--profile /path/to/profile.json` or an explicit
`builtin:` ID. Use
`--device-index N` when several matching keyboards are connected. Write
commands do not save persistent VIA or Launcher settings.

## Rectangular canvas

A canvas file uses tagged sRGB or HSV colors:

```json
{
  "width": 3,
  "height": 2,
  "cells": [
    {"space": "srgb", "red": 255, "green": 0, "blue": 0},
    {"space": "srgb", "red": 0, "green": 255, "blue": 0},
    {"space": "srgb", "red": 0, "green": 0, "blue": 255},
    {"space": "hsv", "hue": 43, "saturation": 255, "value": 255},
    {"space": "hsv", "hue": 213, "saturation": 255, "value": 255},
    {"space": "srgb", "red": 255, "green": 255, "blue": 255}
  ]
}
```

Render it onto the six-key navigation cluster:

```sh
.venv/bin/abralia-rgb render-canvas canvas.json \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --target navigation_cluster \
  --strategy row_key_index \
  --seconds 5
```

Supported explicit strategies are `geometry_resample`, `row_key_index`, and
`anchored_row_grid`. Each profile declares which strategies are safe for each
target. Geometry color averaging occurs in linear RGB; keys at least 4U wide
select the overlapping source cell nearest the recorded LED point.

## Python API

```python
import time

from abralia.rgb import PhysicalSceneBuilder, RgbController, Srgb8

scene = PhysicalSceneBuilder().build(
    "status",
    {"HOME": Srgb8(0, 255, 0)},
    background=Srgb8(0, 0, 0),
    owner="example",
    priority=10,
)

with RgbController.open("builtin:keychron-v3-8k-ansi-encoder-effect25") as controller:
    lease = controller.display([scene], brightness_ceiling=180)
    time.sleep(1)
    lease.refresh()
```

## User compatibility layouts

A separate compatibility JSON can replace or add logical regions without
editing the bundled hardware profile. Region rows may use imported matrix
aliases, direct `{"matrix": [row, column]}` selectors, or explicit physical
elements. The loader resolves them once to shared Control IDs for RGB and Host
Interaction.

See [User compatibility layouts](COMPATIBILITY_LAYOUTS.md) for the schema,
safe alias-import rules, F-row navigation example, CLI validation/export, and
Python usage.

## Shared Raw HID session

RGB and Host Interaction can retain separate controllers while borrowing two
protocol-specific views from one physical Raw HID owner:

```python
from abralia import SharedRawHidSession
from abralia.rgb import load_profile
from abralia.interaction import HostInteractionProtocolClient
from abralia.rgb.adapters.keychron_effect25 import (
    EffectSelectionPolicy,
    KeychronEffect25Adapter,
)

profile = load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
with SharedRawHidSession.open_profile(profile.device_profile, mode="cooperative") as session:
    rgb_adapter = KeychronEffect25Adapter(
        session.rgb_transport(),
        session.device_info,
        profile=profile.device_profile,
        effect_selection_policy=EffectSelectionPolicy.REQUIRE_SELECTED,
    )
    interaction_protocol = HostInteractionProtocolClient(
        session.interaction_transport(), profile=profile.device_profile
    )
```

`cooperative` is synchronous and creates no worker thread. `threaded` starts
one bounded reader that routes the single in-flight response and queues
unsolicited events. Both views are non-owning: closing an adapter or protocol
client does not close the keyboard; the `SharedRawHidSession` context owns the
only physical close. Standalone opening remains available through explicit-profile factories.

Protocol v2 reports enabled effect-25 availability. A synchronous
`SharedKeyboardCoordinator` can keep a producer alive while suspending device
writes, restore the user's ordinary per-key payload without changing their
selected effect, and resume a desktop-defined standby scene when effect 25
returns. The coordinator owns no HID handle or worker thread and never
reactivates interaction automatically.

### Stable physical IDs and optional live keycode lookup

Physical scenes, canvas targets, regions, and future animations use stable
profile element IDs such as `HOME`, `W`, or `KNOB_CLOCKWISE`. Configured
keycodes are not physical identities. Switchable Mac/Windows modifier
positions therefore use positional names such as `LEFT_MODIFIER_2`, with
familiar names retained only as profile aliases.

When a caller intentionally starts from an exact raw 16-bit QMK/VIA keycode,
the controller can read the current live VIA matrix and encoder maps across
explicitly requested layers:

```python
with RgbController.open("builtin:keychron-v3-8k-ansi-encoder-effect25") as controller:
    scene, resolution = controller.build_keycode_scene(
        "all-live-kc-a-positions",
        keycode=0x0004,
        layers=[0, 1, 2, 3],
        color=Srgb8(255, 80, 0),
        owner="example",
    )
    lease = controller.display([scene])
```

Lookup first scans every raw matrix address and encoder direction reported by
the firmware adapter. Raw matches are deduplicated across queried layers before
the profile is consulted. The second phase joins those raw controls to stable
profile elements and LED addresses. A raw match missing from the profile, or a
known element without an RGB address such as a V3 knob direction, is surfaced
as explicitly unrenderable instead of being omitted or misreported as
`UNBOUND`.

The equivalent temporary CLI operation is:

```sh
.venv/bin/abralia-rgb render-keycode 0x0004 \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --layers 0,1,2,3 \
  --color '#FF5000' \
  --seconds 5
```

The shared profile is also the physical lookup table for other desktop
components:

```python
profile = load_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
home = profile.element_by_id["HOME"]
print(home.matrix, home.led_address, home.encoder)
```

Host-side action or binding IDs remain opaque correlation values. They are not
accepted as physical element IDs or keycode-lookup values.

I/O is synchronous and no background refresh thread is created. The current
firmware lease expires after two seconds, so a long-running caller must refresh
more frequently than that. The controller refuses to preempt an already active
guarded RGB session and attempts snapshot restoration on context exit.

Profiles are versioned JSON validated by the bundled JSON Schema. The bundled
V3 profile contains 90 physical controls, of which 87 have RGB addresses. Knob
press, clockwise rotation, and counterclockwise rotation are represented as
separate physical controls; none has a separate LED address.

## Verification boundary

Offline tests cover profile/schema invariants, all mapping strategies, scene
composition, complete LED frames, recovery orchestration, Raw HID report
handling, and the effect-25 frame protocol through fakes. A successful offline
test or acknowledged HID command does not prove the physical colors look
correct. Flashing firmware and changing hardware state require separate,
explicit action.

Abralia-authored desktop code is licensed under Apache-2.0. See the
repository-root [`LICENSE.md`](../../LICENSE.md).
