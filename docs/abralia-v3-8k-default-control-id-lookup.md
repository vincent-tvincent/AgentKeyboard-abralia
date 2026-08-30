# Abralia V3 8K default ControlId lookup

- **Target:** `abralia_host_interaction` on Keychron V3 8K ANSI encoder
- **Snapshot layers:** macOS base layer 0 and Windows base layer 2
- **Public-repository revision:** `fee375b4e97179b0e5c7ea7b793241b927afaa8b`
- **Default keymap source:** [Abralia keymap.c](../abralia/firmware/qmk-userspace/keyboards/keychron/v3_8k/ansi_encoder/keymaps/abralia_host_interaction/keymap.c)
- **Shared physical profile:** [V3 8K layout profile](../abralia/desktop/src/abralia/resources/profiles/keychron-v3-8k-ansi-encoder-effect25.json)
- **Pinned matrix source:** [Keychron keyboard.json](https://github.com/Keychron/qmk_firmware/blob/ee7390c3bbdc1f71a1cc8d54323f3f1d97868593/keyboards/keychron/v3_8k/ansi_encoder/keyboard.json)
- **Generated with:** QMK `c2json --no-cpp` plus the shared physical profile

## How to use this table

This is a default-keymap snapshot for human development and documentation. It
does not replace live VIA lookup. After a user remaps the keyboard, the desktop
API reads the current VIA keymap and encoder map and applies a keycode operation
to **every** matching physical control across the queried layers, deduplicating
identical Control IDs.

`binding_id` is not part of this static table. It is a volatile, host-assigned
action correlation value. The table maps default **keycodes** and physical
positions to firmware **Control IDs**.

Matrix-key Control IDs use `(row << 8) | column`. Encoder clockwise uses kind 1
(`0x4000` for encoder 0); counterclockwise uses kind 2 (`0x8000` for encoder 0).
The knob press is the ordinary matrix control `KNOB_PRESS` at `[0,13]`. The
physical Pause control at `[0,16]` is shown for completeness but is reserved
from Host Interaction bindings by firmware.

## Lookup table

| Physical position | Matrix / encoder | Control ID | macOS layer 0 | Windows layer 2 |
| --- | --- | ---: | --- | --- |
| `ESC` | `[0,0]` | `0x0000` | `KC_ESC` | `KC_ESC` |
| `F1` | `[0,1]` | `0x0001` | `KC_F1` | `KC_F1` |
| `F2` | `[0,2]` | `0x0002` | `KC_F2` | `KC_F2` |
| `F3` | `[0,3]` | `0x0003` | `KC_F3` | `KC_F3` |
| `F4` | `[0,4]` | `0x0004` | `KC_F4` | `KC_F4` |
| `F5` | `[0,5]` | `0x0005` | `KC_F5` | `KC_F5` |
| `F6` | `[0,6]` | `0x0006` | `KC_F6` | `KC_F6` |
| `F7` | `[0,7]` | `0x0007` | `KC_F7` | `KC_F7` |
| `F8` | `[0,8]` | `0x0008` | `KC_F8` | `KC_F8` |
| `F9` | `[0,9]` | `0x0009` | `KC_F9` | `KC_F9` |
| `F10` | `[0,10]` | `0x000A` | `KC_F10` | `KC_F10` |
| `F11` | `[0,11]` | `0x000B` | `KC_F11` | `KC_F11` |
| `F12` | `[0,12]` | `0x000C` | `KC_F12` | `KC_F12` |
| `KNOB_PRESS` | `[0,13]` | `0x000D` | `KC_MUTE` | `KC_MUTE` |
| `SCREENSHOT` | `[0,14]` | `0x000E` | `KC_SNAP` | `KC_PSCR` |
| `SCROLL_LOCK` | `[0,15]` | `0x000F` | `KC_SCRL` | `KC_SCRL` |
| `PAUSE` | `[0,16]` | `0x0010` | `KC_PAUS` | `KC_PAUS` |
| `GRAVE` | `[1,0]` | `0x0100` | `KC_GRV` | `KC_GRV` |
| `1` | `[1,1]` | `0x0101` | `KC_1` | `KC_1` |
| `2` | `[1,2]` | `0x0102` | `KC_2` | `KC_2` |
| `3` | `[1,3]` | `0x0103` | `KC_3` | `KC_3` |
| `4` | `[1,4]` | `0x0104` | `KC_4` | `KC_4` |
| `5` | `[1,5]` | `0x0105` | `KC_5` | `KC_5` |
| `6` | `[1,6]` | `0x0106` | `KC_6` | `KC_6` |
| `7` | `[1,7]` | `0x0107` | `KC_7` | `KC_7` |
| `8` | `[1,8]` | `0x0108` | `KC_8` | `KC_8` |
| `9` | `[1,9]` | `0x0109` | `KC_9` | `KC_9` |
| `0` | `[1,10]` | `0x010A` | `KC_0` | `KC_0` |
| `MINUS` | `[1,11]` | `0x010B` | `KC_MINS` | `KC_MINS` |
| `EQUAL` | `[1,12]` | `0x010C` | `KC_EQL` | `KC_EQL` |
| `BACKSPACE` | `[1,13]` | `0x010D` | `KC_BSPC` | `KC_BSPC` |
| `INSERT` | `[1,14]` | `0x010E` | `KC_APP` | `KC_INS` |
| `HOME` | `[1,15]` | `0x010F` | `KC_HOME` | `KC_HOME` |
| `PAGE_UP` | `[1,16]` | `0x0110` | `KC_PGUP` | `KC_PGUP` |
| `TAB` | `[2,0]` | `0x0200` | `KC_TAB` | `KC_TAB` |
| `Q` | `[2,1]` | `0x0201` | `KC_Q` | `KC_Q` |
| `W` | `[2,2]` | `0x0202` | `KC_W` | `KC_W` |
| `E` | `[2,3]` | `0x0203` | `KC_E` | `KC_E` |
| `R` | `[2,4]` | `0x0204` | `KC_R` | `KC_R` |
| `T` | `[2,5]` | `0x0205` | `KC_T` | `KC_T` |
| `Y` | `[2,6]` | `0x0206` | `KC_Y` | `KC_Y` |
| `U` | `[2,7]` | `0x0207` | `KC_U` | `KC_U` |
| `I` | `[2,8]` | `0x0208` | `KC_I` | `KC_I` |
| `O` | `[2,9]` | `0x0209` | `KC_O` | `KC_O` |
| `P` | `[2,10]` | `0x020A` | `KC_P` | `KC_P` |
| `LEFT_BRACKET` | `[2,11]` | `0x020B` | `KC_LBRC` | `KC_LBRC` |
| `RIGHT_BRACKET` | `[2,12]` | `0x020C` | `KC_RBRC` | `KC_RBRC` |
| `BACKSLASH` | `[2,13]` | `0x020D` | `KC_BSLS` | `KC_BSLS` |
| `DELETE` | `[2,14]` | `0x020E` | `KC_DEL` | `KC_DEL` |
| `END` | `[2,15]` | `0x020F` | `KC_END` | `KC_END` |
| `PAGE_DOWN` | `[2,16]` | `0x0210` | `KC_PGDN` | `KC_PGDN` |
| `CAPS_LOCK` | `[3,0]` | `0x0300` | `KC_CAPS` | `KC_CAPS` |
| `A` | `[3,1]` | `0x0301` | `KC_A` | `KC_A` |
| `S` | `[3,2]` | `0x0302` | `KC_S` | `KC_S` |
| `D` | `[3,3]` | `0x0303` | `KC_D` | `KC_D` |
| `F` | `[3,4]` | `0x0304` | `KC_F` | `KC_F` |
| `G` | `[3,5]` | `0x0305` | `KC_G` | `KC_G` |
| `H` | `[3,6]` | `0x0306` | `KC_H` | `KC_H` |
| `J` | `[3,7]` | `0x0307` | `KC_J` | `KC_J` |
| `K` | `[3,8]` | `0x0308` | `KC_K` | `KC_K` |
| `L` | `[3,9]` | `0x0309` | `KC_L` | `KC_L` |
| `SEMICOLON` | `[3,10]` | `0x030A` | `KC_SCLN` | `KC_SCLN` |
| `QUOTE` | `[3,11]` | `0x030B` | `KC_QUOT` | `KC_QUOT` |
| `ENTER` | `[3,13]` | `0x030D` | `KC_ENT` | `KC_ENT` |
| `LEFT_SHIFT` | `[4,0]` | `0x0400` | `KC_LSFT` | `KC_LSFT` |
| `Z` | `[4,2]` | `0x0402` | `KC_Z` | `KC_Z` |
| `X` | `[4,3]` | `0x0403` | `KC_X` | `KC_X` |
| `C` | `[4,4]` | `0x0404` | `KC_C` | `KC_C` |
| `V` | `[4,5]` | `0x0405` | `KC_V` | `KC_V` |
| `B` | `[4,6]` | `0x0406` | `KC_B` | `KC_B` |
| `N` | `[4,7]` | `0x0407` | `KC_N` | `KC_N` |
| `M` | `[4,8]` | `0x0408` | `KC_M` | `KC_M` |
| `COMMA` | `[4,9]` | `0x0409` | `KC_COMM` | `KC_COMM` |
| `PERIOD` | `[4,10]` | `0x040A` | `KC_DOT` | `KC_DOT` |
| `SLASH` | `[4,11]` | `0x040B` | `KC_SLSH` | `KC_SLSH` |
| `RIGHT_SHIFT` | `[4,13]` | `0x040D` | `KC_RSFT` | `KC_RSFT` |
| `UP` | `[4,15]` | `0x040F` | `KC_UP` | `KC_UP` |
| `LEFT_CONTROL` | `[5,0]` | `0x0500` | `KC_LCTL` | `KC_LCTL` |
| `LEFT_MODIFIER_2` | `[5,1]` | `0x0501` | `KC_LOPTN` | `KC_LGUI` |
| `LEFT_MODIFIER_3` | `[5,2]` | `0x0502` | `KC_LCMMD` | `KC_LALT` |
| `SPACE` | `[5,6]` | `0x0506` | `KC_SPC` | `KC_SPC` |
| `RIGHT_MODIFIER_1` | `[5,10]` | `0x050A` | `KC_RCMMD` | `KC_RALT` |
| `RIGHT_MODIFIER_2` | `[5,11]` | `0x050B` | `KC_ROPTN` | `KC_RGUI` |
| `FN` | `[5,12]` | `0x050C` | `FN_MAC` | `FN_WIN` |
| `RIGHT_CONTROL` | `[5,13]` | `0x050D` | `KC_RCTL` | `KC_RCTL` |
| `LEFT` | `[5,14]` | `0x050E` | `KC_LEFT` | `KC_LEFT` |
| `DOWN` | `[5,15]` | `0x050F` | `KC_DOWN` | `KC_DOWN` |
| `RIGHT` | `[5,16]` | `0x0510` | `KC_RGHT` | `KC_RGHT` |
| `KNOB_COUNTERCLOCKWISE` | `encoder 0 CCW` | `0x8000` | `KC_VOLD` | `KC_VOLD` |
| `KNOB_CLOCKWISE` | `encoder 0 CW` | `0x4000` | `KC_VOLU` | `KC_VOLU` |

## License and provenance

Abralia-authored host-side documentation is Apache-2.0. Firmware remains under
its GPL-3.0-or-later scope, and the pinned Keychron/QMK layout source retains
its original notices and license. See `LICENSE.md`.
