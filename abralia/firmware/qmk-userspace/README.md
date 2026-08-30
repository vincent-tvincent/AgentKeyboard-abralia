# Abralia QMK userspace

QMK External Userspace for the Abralia firmware.

## Requirements

- QMK CLI
- Keychron QMK Firmware version recorded in `qmk-upstream.lock.json`
- A matching firmware target listed below; original V3 ports are experimental

## Setup

From the pinned Keychron QMK checkout, select this directory as the userspace
for the current shell (paths below start at the Abralia repository root):

```sh
export QMK_USERSPACE="/path/to/abralia-repository/abralia/firmware/qmk-userspace"
cd /path/to/keychron-qmk_firmware
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

### Original V3 sibling variants (experimental)

These are firmware-only ports for the original **wired ANSI V3**, not V3 Max,
V3 Ultra, ISO, JIS, or ABNT2. Both variants are hardware-unverified. Compilation
and offline tests do not establish physical compatibility.

| Target | USB VID:PID | Matrix | Rotary encoders |
| --- | --- | --- | --- |
| `keychron/v3/ansi` | `3434:0330` | 6 x 16 | 0 |
| `keychron/v3/ansi_encoder` | `3434:0331` | 6 x 16 | 1 |

Build the RGB-only and Host Interaction versions independently:

```sh
qmk compile -j 10 -kb keychron/v3/ansi -km abralia
qmk compile -j 10 -kb keychron/v3/ansi -km abralia_host_interaction
qmk compile -j 10 -kb keychron/v3/ansi_encoder -km abralia
qmk compile -j 10 -kb keychron/v3/ansi_encoder -km abralia_host_interaction
```

Sources live in `keyboards/keychron/v3/{ansi,ansi_encoder}/keymaps/` in this
userspace. QMK copies the following generated files into this userspace root,
alongside the reference V3 8K binaries:

```text
keychron_v3_ansi_abralia.bin
keychron_v3_ansi_abralia_host_interaction.bin
keychron_v3_ansi_encoder_abralia.bin
keychron_v3_ansi_encoder_abralia_host_interaction.bin
```

Generated binaries remain Git-ignored. Rebuild from the pinned upstream source
and this userspace; never interchange images between models, layouts, or knob
variants. These targets inherit the original STM32 bootloader, scan/LED drivers,
USB descriptors, and stock polling configuration; they do not gain 8K polling.

The new keymaps include their pinned upstream `keychron` keymap directly and
reuse the reference Abralia RGB/interaction sources through small include
wrappers. They do not duplicate the state machines or change QMK common files.
Keep the complete userspace tree when building them.

**Original V3 toggle:** Host Interaction reserves the top-right physical key at
matrix `[3,14]` (Control ID `0x030E`), rather than the 8K's `[0,16]`. Its stock
mapping is RGB effect cycling (`UG_NEXT`), not `KC_PAUS`. The port preserves
that mapping: double-tap toggles interaction while a host session and effect 25
are active; an unmatched tap replays the actual key mapping after the gesture
window. Outside that state the key passes through immediately. Consequently a
single stock-mapped tap can leave effect 25 and disarm interaction normally.
No VIA remapping is performed by this port.

**Desktop support:** desktop API 0.2 consumes an explicit profile for each
variant. Select the matching original V3 JSON from the
[profile catalog](../../desktop/src/abralia/resources/profiles/README.md);
there is no automatic model/profile selection. The current demos accept a
required `--profile` argument. Firmware and desktop coverage remain offline-only
for the original V3 variants.

Before claiming hardware support, an owner must verify ordinary keys/Fn,
encoder where present, VIA/Launcher, RGB geometry and effect IDs, frame timeout,
interaction entry/exit, effect-change disarm, held-key release, heartbeat
recovery, reconnect, and the board's own recovery procedure. See also the
[offline firmware tests](../tests/README.md).

## Independent-V effect

The keymaps append `PER_KEY_RGB_INDEPENDENT_V` as RGB Matrix effect 25.
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
variant. It retains effect 25 and adds protocol-v2 volatile physical-control
bindings on VIA custom channel 0, value 2. Double Pause enters or exits Host
Interaction Mode only while RGB is enabled on effect 25. Outside that state,
Pause is immediate ordinary input without the 300 ms gesture window. Keys,
knob press, and both knob directions support
CAPTURE or MIRROR routing, SESSION/TTL/ONE_SHOT lifetime, atomic force scopes,
ACKed events, and a four-second heartbeat fallback.

Changing away from effect 25 resets its host frame state to AWAITING, reports
`RGB_EFFECT_CHANGED(false)`, and clears manual/forced activation while
retaining the session and bindings. Returning to effect 25 reports availability
but never reactivates input. Host-force commit is rejected with `INVALID_STATE`
while effect 25 is unavailable. No effect-aware path writes EEPROM.

The input protocol never writes EEPROM and does not interpret agent actions.
See the project-root `docs/host-interaction-firmware-protocol.md` for the wire
contract. Host-side force permission is broker policy and defaults to disabled
in the future application; the firmware cannot attest to a computer UI click.

### Current validation status

Both `abralia` and `abralia_host_interaction` compile with 10 jobs. The current
desktop suite covers protocol-v2 parsing, standby coordination, effect-aware
restoration, and both shared-session modes. A historical protocol-v1 physical
run confirmed double-Pause entry, Home CAPTURE + ONE_SHOT, End MIRROR +
SESSION, clockwise knob CAPTURE + ONE_SHOT, repeated counterclockwise knob
MIRROR + SESSION, and consecutive acknowledged events 1–101.

Protocol v2 has not been flashed. Effect-change disarm, immediate Pause outside
effect 25, effects 23/24 handoff, standby resumption, forced-activation gate,
and post-flash VIA/Launcher/8K compatibility remain user-assisted physical
validation tasks.

## Flash

```sh
qmk flash -j 10 -kb keychron/v3_8k/ansi_encoder -km abralia
```

For the Host Interaction variant, replace the keymap argument with
`-km abralia_host_interaction`. Building does not imply that either firmware
has been flashed or physically validated.

## License

Abralia-authored firmware is licensed under GPL-3.0-or-later. Keychron, QMK,
ChibiOS, and other upstream material retains its original notices and
licenses. See the repository-root [`LICENSE.md`](../../../LICENSE.md).
