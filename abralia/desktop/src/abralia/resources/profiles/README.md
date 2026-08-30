# Keyboard profiles

These shared JSON profiles configure both desktop controllers. They are not
RGB-only configuration, firmware images, or live VIA keymaps. The caller must
select a profile explicitly; the library does not identify models or choose
profiles automatically.

| Profile | Firmware target | USB VID:PID | Validation |
| --- | --- | --- | --- |
| [V3 8K ANSI encoder](keychron-v3-8k-ansi-encoder-effect25.json) | `keychron/v3_8k/ansi_encoder` | `3434:0F30` | Reference hardware |
| [Original V3 ANSI](keychron-v3-ansi-effect25.json) | `keychron/v3/ansi` | `3434:0330` | Offline only; hardware-unverified |
| [Original V3 ANSI encoder](keychron-v3-ansi-encoder-effect25.json) | `keychron/v3/ansi_encoder` | `3434:0331` | Offline only; hardware-unverified |

Each target has `abralia` RGB-only and `abralia_host_interaction` firmware
variants. RGB-only use does not probe Host Interaction. Interaction use requires
the second firmware variant and protocol v2. These profiles do not cover ISO,
JIS, V3 Max, or V3 Ultra.

For physical Control IDs and side-by-side Keychron/Abralia default keycodes,
see the [lookup-table index](../../../../../../docs/control-id-lookups.md).

## Load explicitly

```python
from abralia import load_device_profile
from abralia.rgb import load_profile

source = "builtin:keychron-v3-ansi-encoder-effect25"
device = load_device_profile(source)  # No RGB geometry/regions required.
layout = load_profile(source)        # Full RGB geometry and region validation.
assert device == layout.device_profile
```

Both readers also accept an explicit JSON filesystem path. Bundled IDs are
`builtin:` followed by the JSON filename without `.json`. There is no default.

## Contents and boundaries

- `device_match` selects the USB identity and Raw HID interface.
- `adapter`, `keymap`, `expected_led_count`, and `capabilities` declare protocol
  requirements and expected hardware dimensions.
- Optional `interaction.toggle_matrix` declares the reserved physical toggle.
  It is required for interaction, never inferred from a keycode or alias.
- `elements`, `regions`, and LED geometry are consumed by RGB. Element names
  describe stable physical positions, not their current VIA assignments.
- User compatibility layouts remain separate and cannot override hardware
  metadata or move the firmware-reserved toggle.

The original V3 toggle is `[3,14]`, labeled `LIGHTING_KEY` here; its default
firmware action is RGB effect cycling. V3 8K uses `[0,16]`, labeled `PAUSE`.
Demo indicators resolve this position through the profile instead of assuming
either label. LED placement and toggle identity cannot be independently
discovered through the current firmware protocol; select the correct profile.

Original V3 matrix/physical geometry and LED tables were extracted from the
`ansi/keyboard.json`, `ansi_encoder/keyboard.json`, and corresponding `.c`
tables in the [pinned Keychron source](https://github.com/Keychron/qmk_firmware/tree/ee7390c3bbdc1f71a1cc8d54323f3f1d97868593/keyboards/keychron/v3).
LED points use the reference profile's coordinate conversion:
`x = qmk_x * 17.75 / 224`, `y = qmk_y * 5.25 / 64`. Runtime loading needs no
QMK checkout. The JSON schema is in [../schemas/](../schemas/).

See the [desktop API guide](../../../../README.md) and
[firmware build guide](../../../../../firmware/qmk-userspace/README.md).
Profiles and this documentation are Apache-2.0; see
[`LICENSE.md`](../../../../../../LICENSE.md).
