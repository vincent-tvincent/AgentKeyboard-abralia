# Abralia documentation

Use this page to find the right document for a specific task. Abralia is
currently pre-alpha, so each guide distinguishes documented design, offline
verification, and physical-keyboard validation where that distinction matters.

## I want to...

| Goal | Go to |
| --- | --- |
| Understand what Abralia does and what is currently supported | [Project overview](../README.md) |
| Install or use the desktop RGB API and CLI | [Desktop RGB API guide](../abralia/desktop/README.md) |
| Bind keys or encoder actions through Host Interaction Mode | [Host Interaction desktop API](../abralia/desktop/HOST_INTERACTION_API.md) |
| Build or flash the Keychron V3 8K firmware | [QMK userspace build and flash guide](../abralia/firmware/qmk-userspace/README.md) |
| Understand the firmware directory and licensing boundary | [Firmware overview](../abralia/firmware/README.md) |
| Find a physical key's Control ID or compare default mappings | [Control ID lookup guide](control-id-lookups.md) |
| Discover live matrix and encoder Control IDs | [Host Interaction control inspector](../experiments/host-interaction-control-inspector/README.md) |
| Reproduce the geometry, timeout, fog-orb, or camera validation | [Desktop RGB physical validation](../experiments/desktop-rgb-physical-validation/README.md) |
| Run the capability-driven RGB and status demonstrations | [Keychron RGB controller experiments](../experiments/keychron-rgb-controller-python/README.md) |
| Understand Keychron's built-in RGB effects and host controls | [Built-in RGB effects reference](../experiments/keychron-rgb-controller-python/BUILT_IN_EFFECTS.md) |
| Reproduce the direct host-RGB protocol experiment | [Keychron V3 8K host RGB experiment](../experiments/keychron-v3-8k-host-rgb/README.md) |
| Review the OpenRGB compatibility result | [OpenRGB Python experiment](../experiments/keychron-v3-8k-openrgb-python/README.md) |
| Check which license applies to a file | [Licensing guide](../LICENSE.md) |

## Complete document map

### Project and component guides

- [Project overview and current status](../README.md)
- [Desktop RGB API and CLI](../abralia/desktop/README.md)
- [Host Interaction desktop API](../abralia/desktop/HOST_INTERACTION_API.md)
- [Firmware overview](../abralia/firmware/README.md)
- [QMK userspace setup, build, effect 25, Host Interaction, and flash guide](../abralia/firmware/qmk-userspace/README.md)

### Control ID and default-keymap references

- [Control ID lookup guide](control-id-lookups.md)
- [Side-by-side Keychron and Abralia default mappings](keychron-v3-8k-official-control-id-lookup.md)
- [Abralia-only default mapping](abralia-v3-8k-default-control-id-lookup.md)

The lookup tables are default snapshots for development and documentation.
The desktop keycode API reads the live VIA mapping because users can remap the
keyboard after flashing.

### Experiments and validation guides

- [Desktop RGB physical validation](../experiments/desktop-rgb-physical-validation/README.md)
- [Host Interaction control inspector](../experiments/host-interaction-control-inspector/README.md)
- [Capability-driven Keychron RGB controller demonstrations](../experiments/keychron-rgb-controller-python/README.md)
- [Keychron built-in RGB effects reference](../experiments/keychron-rgb-controller-python/BUILT_IN_EFFECTS.md)
- [Keychron V3 8K direct host-RGB experiment](../experiments/keychron-v3-8k-host-rgb/README.md)
- [Keychron V3 8K OpenRGB Python experiment](../experiments/keychron-v3-8k-openrgb-python/README.md)

### Licensing

- [Repository licensing guide](../LICENSE.md)
- Full license texts are stored in [`LICENSES/`](../LICENSES/).

