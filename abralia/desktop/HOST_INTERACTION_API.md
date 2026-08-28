# Host Interaction desktop API

The `abralia.interaction` Python package implements the complete Abralia Host
Interaction firmware protocol v1. It is synchronous, volatile, and intended to
be owned by one trusted desktop broker. It never flashes firmware, remaps VIA,
or writes keyboard configuration.

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
press without loading a layout profile.

## Binding and activation

Configuration and activation are deliberately separate. This preserves the
firmware's manual double-Pause workflow and makes host-forced behavior
explicit.

```python
from abralia.interaction import (
    BindingPolicy,
    HostInteractionController,
    HostInteractionProtocolClient,
    Lifetime,
    Routing,
)

with HostInteractionProtocolClient.open_keychron_v3_8k() as protocol:
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

Manual mode is entered or exited only by the firmware's physical double-Pause
gesture; protocol v1 has no host command that synthesizes that gesture.
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
of `CONTROL_EDGE`, `MODE_CHANGED`, and `QUEUE_OVERFLOW` events. Non-OK firmware
responses raise `FirmwareRejectedError` and remain available as
`error.response`.

The broker must call `service()` regularly. It sends the required one-second
keepalive, receives and acknowledges firmware events, and preserves the
firmware's four-second fail-safe behavior if the broker stops.

Abralia-authored desktop code and this documentation are licensed under
Apache-2.0. See the repository-root `LICENSE.md`.
