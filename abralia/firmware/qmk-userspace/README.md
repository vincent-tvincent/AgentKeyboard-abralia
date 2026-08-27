# Abralia QMK userspace

QMK External Userspace for the Abralia firmware.

## Requirements

- QMK CLI
- Keychron QMK Firmware version recorded in `qmk-upstream.lock.json`
- Keychron V3 8K ANSI encoder

## Setup

Configure this directory as the QMK userspace:

```sh
qmk config user.overlay_dir="/path/to/abralia/firmware/qmk-userspace"
```

## Build

RGB-only firmware:

```sh
qmk compile -j 10 -kb keychron/v3_8k/ansi_encoder -km abralia
```

RGB plus Host Interaction firmware:

```sh
qmk compile -j 10 -kb keychron/v3_8k/ansi_encoder -km abralia_host_interaction
```

## Independent-V effect

The V3 8K keymap appends `PER_KEY_RGB_INDEPENDENT_V` as RGB Matrix effect 25.
Effects 0 through 24 and Keychron's `0xA8/0x0A` per-key write command are
unchanged.

Effect 25 starts in a firmware-generated random-key breathing halo. VIA custom
channel 0, value 1 controls its volatile rendering state:

```text
SET [0x07, 0x00, 0x01, operation, sequence]
GET [0x08, 0x00, 0x01]

operation 0: await/idle halo
operation 1: direct shared-buffer rendering
operation 2: begin guarded streaming
operation 3: commit guarded frame
```

GET and SET responses place state, active sequence, pending sequence, flags,
and result in bytes 3 through 7. Flag bits report active-valid,
pending-valid, back-buffer-free, and transition-queued. Results are OK, BUSY,
or INVALID_STATE.

Guarded frames use two display buffers, a one-frame pending queue, and an
opaque 8-bit sequence. A guarded stream returns to the idle halo two seconds
after its last accepted commit. The protocol is volatile and defines no save
command.

Effect 25 is also the compiled RGB Matrix default. A fresh or build-date-reset
EEPROM therefore starts in the idle halo. Standard effect 0 remains available
when completely black lighting is required.

## Host Interaction variant

`abralia_host_interaction` is a separate keymap copied from the RGB-only
variant. It retains effect 25 and adds volatile physical-control bindings on
VIA custom channel 0, value 2. Double Pause enters or exits Host Interaction
Mode while an unmatched single Pause remains an ordinary QMK action after the
300 ms gesture window. Keys, knob press, and both knob directions support
CAPTURE or MIRROR routing, SESSION/TTL/ONE_SHOT lifetime, atomic force scopes,
ACKed events, and a four-second heartbeat fallback.

The input protocol never writes EEPROM and does not interpret agent actions.
See the project-root `docs/host-interaction-firmware-protocol.md` for the wire
contract. Host-side force permission is broker policy and defaults to disabled
in the future application; the firmware cannot attest to a computer UI click.

## Flash

```sh
qmk flash -j 10 -kb keychron/v3_8k/ansi_encoder -km abralia
```

For the Host Interaction variant, replace the keymap argument with
`-km abralia_host_interaction`. Building does not imply that either firmware
has been flashed or physically validated.
