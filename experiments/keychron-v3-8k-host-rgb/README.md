# Keychron V3 8K host RGB experiment

This small macOS experiment exercises the stock Keychron V3 8K ANSI encoder
Raw HID RGB commands from the computer. It serves Abralia Phase 2 tasks
P2-001 through P2-004 without selecting the production host language or the
future firmware RGB architecture.

It targets the exact USB identity `3434:0F30` and Raw HID usage `FF60:61`.
The script:

1. verifies the Keychron RGB protocol and the expected 87-LED layout;
2. asks the firmware for the LED indices of W, A, S, D, and Space;
3. snapshots the current effect, brightness, per-key type, and touched colors;
4. shows two different per-key color scenes;
5. requests `V=0` on only those keys to test individual off behavior;
6. briefly sets global brightness to zero for a guaranteed visible off phase;
7. restores the original in-memory RGB state, including after interruption.

The stock `2025q3` per-key solid renderer replaces every stored per-key V with
global brightness. Therefore per-key hue/saturation changes are expected to
work, while individual `V=0` may remain visibly lit. That result is useful
evidence for the later D-005 firmware-architecture decision; it is not a script
failure. Global off should still work.

No firmware is flashed. The script never sends VIA custom-save or Keychron
RGB-save commands, so it does not intentionally write the tested RGB state to
EEPROM. Close Keychron Launcher and VIA first so they do not contend for the
Raw HID interface.

## Verified read-only probe

On 2026-08-23, the connected board reported Keychron RGB protocol `1.0`, 87
LEDs, and these mappings:

| Key | Matrix | LED index |
| --- | --- | ---: |
| W | 2,2 | 35 |
| A | 3,1 | 51 |
| S | 3,2 | 52 |
| D | 3,3 | 53 |
| Space | 5,6 | 79 |

This probe did not change the keyboard's lighting state. The full color/off
sequence is intentionally left for a maintainer-invoked run.

## Run

Make the script executable once:

```sh
chmod +x v3_8k_rgb_experiment.swift
```

Read-only probe:

```sh
./v3_8k_rgb_experiment.swift --probe
```

Run the full experiment:

```sh
./v3_8k_rgb_experiment.swift
```

Shorter scenes:

```sh
./v3_8k_rgb_experiment.swift --hold-seconds 1 --off-seconds 0.5
```

Press Control-C at any point to request cleanup and restoration. If the process
is forcibly killed or the computer loses power, reopen Launcher/VIA or unplug
and reconnect the keyboard to reload the persistent normal RGB configuration.
