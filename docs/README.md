# Abralia documentation

Use this page to find the right document for a specific task. Each guide
distinguishes supported behavior, offline verification and physical-keyboard
validation where that distinction matters.

## I want to...

| Goal | Go to |
| --- | --- |
| Understand what Abralia does and what is currently supported | [Project overview](../README.md) |
| Contribute while preserving the architecture and device portability | [Contributor and agent instructions](../AGENTS.md) · [Claude Code entry point](../CLAUDE.md) |
| Learn the keyboard shortcuts and designed effects with GIF examples | [User guide](user-guide/README.md) |
| Test a design without a keyboard and export its animation | [Standalone keyboard simulator](../tools/keyboard-simulator/README.md) |
| Find the physical control positions for a particular model | [Keyboard model guides](user-guide/keyboards/README.md) |
| Run the terminal backend with Codex | [Agent backend guide](../abralia/desktop/BACKEND.md) |
| Choose a keyboard and mute projects from the macOS GUI | [macOS control panel](../abralia/desktop/gui/README.md) |
| Enable or disable Abralia for a selected project | [Project-local MCP and skill setup](../abralia/desktop/BACKEND.md#enable-only-a-selected-project) |
| Register or release existing task slots without waking agents | [Host registration and release](../abralia/desktop/BACKEND.md#host-registration-and-release) |
| Learn current keyboard controls, page navigation and attention gestures | [Physical interaction](../abralia/desktop/BACKEND.md#physical-interaction) |
| Understand multi-agent notification fog, pickup, mute and fading | [Notification fog](../abralia/desktop/BACKEND.md#notification-fog) |
| Check the agent-callable operations and ownership rules | [Agent tools](../abralia/desktop/BACKEND.md#agent-tools) |
| Install or use the desktop RGB API and CLI | [Desktop RGB API guide](../abralia/desktop/README.md) |
| Bind keys or encoder actions through Host Interaction Mode | [Host Interaction desktop API](../abralia/desktop/HOST_INTERACTION_API.md) |
| Choose a keyboard profile or supply device metadata | [Shared profile catalog](../abralia/desktop/src/abralia/resources/profiles/README.md) |
| Discover a new HID keyboard and generate/validate a profile draft | [Developer discovery CLI](../abralia/desktop/DEVELOPER_CLI.md) |
| Define user-specific regions with matrix aliases | [User compatibility layouts](../abralia/desktop/COMPATIBILITY_LAYOUTS.md) |
| Build or flash the Keychron V3 8K firmware | [QMK userspace build and flash guide](../abralia/firmware/qmk-userspace/README.md) |
| Build experimental original V3 ANSI firmware, with or without a knob | [Original V3 sibling variants](../abralia/firmware/qmk-userspace/README.md#original-v3-sibling-variants-experimental) |
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

- [Illustrated user guide](user-guide/README.md)
- [Complete controls and mode precedence](user-guide/controls.md)
- [Lighting effects, colors and lifetimes](user-guide/effects.md)
- [Keyboard models and adding a model guide](user-guide/keyboards/README.md)
- [Standalone keyboard simulator, design plugins and GIF export](../tools/keyboard-simulator/README.md)
- [Project overview and current status](../README.md)
- [Agent backend, project-local MCP, task slots and physical controls](../abralia/desktop/BACKEND.md)
- [Desktop RGB API and CLI](../abralia/desktop/README.md)
- [Developer HID discovery, protocol probes and profile authoring](../abralia/desktop/DEVELOPER_CLI.md)
- [Host Interaction desktop API](../abralia/desktop/HOST_INTERACTION_API.md)
- [User compatibility layouts](../abralia/desktop/COMPATIBILITY_LAYOUTS.md)
- [Firmware overview](../abralia/firmware/README.md)
- [QMK userspace setup, build, effect 25, Host Interaction, and flash guide](../abralia/firmware/qmk-userspace/README.md)

### Control ID and default-keymap references

- [Control ID lookup guide](control-id-lookups.md)
- [V3 8K ANSI encoder: Keychron and Abralia defaults](keychron-v3-8k-official-control-id-lookup.md)
- [V3 8K ANSI encoder: Abralia-only defaults](abralia-v3-8k-default-control-id-lookup.md)
- [Original V3 ANSI without knob: Keychron and Abralia defaults](keychron-v3-ansi-control-id-lookup.md)
- [Original V3 ANSI encoder: Keychron and Abralia defaults](keychron-v3-ansi-encoder-control-id-lookup.md)

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
