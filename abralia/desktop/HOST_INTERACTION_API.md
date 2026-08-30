# Host Interaction desktop API

The `abralia.interaction` Python package implements the complete Abralia Host
Interaction firmware protocol v2. It is synchronous, volatile, and intended to
be owned by one trusted desktop broker. It never flashes firmware, remaps VIA,
or writes keyboard configuration.

## Explicit device metadata

Every client requires a supplied `DeviceProfile`, loaded with
`load_device_profile(source)` or obtained from `LayoutProfile.device_profile`.
The metadata reader does not require RGB elements or regions. Profile-based
opening matches only its USB identity; it never chooses a keyboard model or
profile. Protocol v2 and reported matrix/encoder counts are validated before
claiming a session. The selected `interaction.toggle_matrix` is reserved and
exposed as `HostInteractionController.toggle_control`.

The original V3 toggle is its stock top-right lighting key at `[3,14]`; V3 8K
uses `[0,16]`. Neither the desktop nor compatibility overlays infer or relocate
this control from a keycode. See the
[shared profile catalog](src/abralia/resources/profiles/README.md).

## Control lookup

Two explicit lookup paths produce the same `ControlId` type:

- `ControlId(0x010F)` or `ControlId.key(1, 15)` addresses firmware directly;
  the caller already knows the firmware address and the API makes no assumption
  about the keyboard's physical layout.
- `controller.set_keycode_controls("KC_HOME", ...)` reads the current VIA
  matrix buffer and encoder map, then applies the binding to every Control ID
  carrying that keycode across the queried layers. Duplicate Control IDs found
  on multiple layers are deduplicated, while the returned `keycode_matches`
  records every layer match. This scan uses only firmware-reported matrix and
  encoder capabilities, not a physical-layout profile.

Optional human-readable documentation for the pinned Keychron official
defaults and Abralia defaults is indexed in
[`docs/control-id-lookups.md`](../../docs/control-id-lookups.md). They cover
macOS base layer 0, Windows base layer 2, matrix controls, and both encoder
directions. These tables are not loaded by `abralia.interaction`; live VIA
lookup remains authoritative after any remap.

Developers can discover raw IDs interactively with the separate guarded
[`host-interaction-control-inspector`](../../experiments/host-interaction-control-inspector/README.md),
which prints a unique volatile binding ID and Control ID for every mirrored
press using device metadata without requiring RGB geometry.

## Compatibility regions

User-authored compatibility layouts can define region rows through imported
matrix aliases, direct matrix coordinates, or explicit hardware-profile
elements. They normalize once to Control IDs and can be shared with the RGB
controller without reading the live VIA keymap.

Pass a resolved layout to `HostInteractionController`, then use
`region_controls`, `set_region_controls`, `remove_region_controls`, or
`activate_region`. See [User compatibility layouts](COMPATIBILITY_LAYOUTS.md)
for the schema and complete example.

## Shared Raw HID ownership

For simultaneous RGB and Host Interaction, construct
`HostInteractionProtocolClient` from
`SharedRawHidSession.interaction_transport()` and construct the effect-25 RGB
adapter from the same session's `rgb_transport()`. The session uniquely owns
the physical handle; both protocol views preserve their existing APIs and have
non-owning `close()` methods.

Cooperative mode remains synchronous and should use nonblocking or short event
polls while an animation is active. Threaded mode is explicitly selected and
uses one reader, one in-flight response matcher, and a shared unmatched/event
queue. Standalone Host Interaction construction remains supported.

## Binding and activation

Configuration and activation are deliberately separate. This preserves the
firmware's manual double-tap toggle workflow and makes host-forced behavior
explicit.

```python
from abralia import load_device_profile
from abralia.interaction import (
    BindingPolicy,
    HostInteractionController,
    HostInteractionProtocolClient,
    Lifetime,
    Routing,
)

profile = load_device_profile("builtin:keychron-v3-8k-ansi-encoder-effect25")
with HostInteractionProtocolClient.open_profile(profile) as protocol:
    controller = HostInteractionController(protocol)

    update = controller.set_keycode_controls(
        "KC_HOME",
        binding_id=1001,
        policy=BindingPolicy(
            routing=Routing.CAPTURE,
            lifetime=Lifetime.ONE_SHOT,
            duration_ms=30_000,
            emit_down=True,
            emit_up=True,
        ),
    )
    print(update.controls, update.keycode_matches)

    controller.activate_controls(update.controls, lease_ms=30_000)
    for event in protocol.service(timeout_ms=250):
        print(event)

    controller.deactivate_forced()
    controller.remove_keycode_controls("KC_HOME")
```

Direct-Control-ID operations are available through:

```text
set_controls / remove_controls / activate_controls
```

`activate_all()` selects firmware `ALL_CONFIGURED`; the selected-control
activation methods select firmware `SELECTED`. Repeating an activation with a
new lease renews it. `deactivate_forced()` clears host-forced activation without
removing bindings.

Manual mode is entered or exited only by the firmware's physical double-tap toggle
gesture; protocol v2 has no host command that synthesizes that gesture.
Enabled effect 25 is required for both manual and forced activation. Leaving
effect 25 emits `RGB_EFFECT_CHANGED(false)`, disarms interaction while retaining
the session and bindings, and is followed by `MODE_CHANGED(false)` when needed.
Returning to effect 25 emits availability only and never reactivates input.
`turn_off_all()` uses `RELEASE_SESSION`, the firmware-supported complete reset,
to clear manual and forced activation, bindings, queued events, and the related
effect-25 host state. Pass `reclaim_session=True` to start a fresh empty session
after that reset.

## Complete wire API

`HostInteractionProtocolClient` exposes every implemented command and payload:

```text
get_capabilities
claim_session / keepalive / release_session / reset_session
begin_binding_replace / write_bindings / commit_bindings / clear_bindings
begin_force_scope / write_force_controls / commit_force_scope
clear_force_scope / get_status
read_event / ack_event / service
```

Typed results include all capability fields, common status fields, reset
reasons, binding and force generations, queue/heartbeat state, and every field
of `CONTROL_EDGE`, `MODE_CHANGED`, `QUEUE_OVERFLOW`, and
`RGB_EFFECT_CHANGED` events. `StatusFlags.RGB_EFFECT_25_SELECTED` supplies the
initial state and `DeviceEvent.rgb_effect25_selected` decodes transitions.
Non-OK firmware
responses raise `FirmwareRejectedError` and remain available as
`error.response`.

The broker must call `service()` regularly. It sends the required one-second
keepalive, receives and acknowledges firmware events, and preserves the
firmware's four-second fail-safe behavior if the broker stops.

## Effect-aware producer coordination

`SharedKeyboardCoordinator` keeps RGB and Host Interaction controllers
separate while mapping v2 availability/mode events to a caller-supplied
`RgbProducerLifecycle`. The producer remains alive in `RGB_SUSPENDED`, sends no
device frames while another effect is selected, resumes desktop-controlled
`STANDBY` when effect 25 returns, and enters `ACTIVE` only after a new
activation. No standby scene is built into the API.

Use `EffectSelectionPolicy.REQUIRE_SELECTED` for the shared RGB adapter. It
prevents accidental effect changes; the standalone default remains
`AUTO_SELECT`. On suspension, `RgbController` revokes existing leases and
restores the captured per-key payload while preserving the user's selected
effect. Returning to effect 25 rebases final recovery so context exit does not
undo that choice.

Abralia-authored desktop code and this documentation are licensed under
Apache-2.0. See the repository-root `LICENSE.md`.
