# Host Interaction control inspector

This guarded helper prints the volatile `binding_id` and firmware `control_id`
for each observed key or encoder press. It makes no assumption about the
keyboard's physical layout: matrix dimensions and encoder count come from the
Host Interaction capabilities response.

Install the desktop package from the repository root, then run:

```sh
python3 -m pip install -e abralia/desktop
python3 experiments/host-interaction-control-inspector/inspect_controls.py
```

Type `INSPECT` when prompted. Use `--json` for JSON Lines, `--seconds N` for a
bounded run, or `--yes` in an already guarded development workflow.

The helper atomically installs unique SESSION bindings for every reported
matrix address and encoder direction, excluding the firmware-reserved Pause
position `[0,16]`. It uses MIRROR routing and emits DOWN events only, so normal
key behavior remains available. It forces all configured controls with a
renewable bounded lease, sends heartbeats, acknowledges events, and releases
the volatile session on exit. It never flashes firmware, saves a keymap, or
writes EEPROM.

Close Keychron Launcher and VIA before running because the Raw HID interface is
owned by one process at a time. Press Ctrl-C to stop and trigger cleanup.

Abralia-authored experiment code and documentation are licensed under
Apache-2.0. See the repository-root `LICENSE.md`.
