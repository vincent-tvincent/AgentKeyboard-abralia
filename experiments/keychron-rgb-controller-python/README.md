# Capability-driven Keychron RGB controller demo

This Python hardware spike demonstrates a direct, capability-driven controller
for stock Keychron firmware. It does not choose Python as Abralia's production
host language.

See [`BUILT_IN_EFFECTS.md`](BUILT_IN_EFFECTS.md) for the complete V3 8K stock
effect list, visual explanations, applicable controls, per-key subtypes, host
command fields, and persistence/restoration rules.

The demo performs four steps:

1. scan connected HID interfaces for Keychron devices;
2. negotiate support levels with read-only commands;
3. print the complete detection result;
4. when a known Level 2+ device is ready, display a temporary background and
   repeatedly rotate the colors of W, A, S, D, and Space.

No firmware is built or flashed. No keymap is changed. Every lighting write is
volatile; the program snapshots the full per-key color buffer and restores the
original effect, brightness, per-key type, and colors after the demonstration
or interruption. A normal completed run reads the entire state back and checks
that it matches the original snapshot. It never sends VIA custom-save or
Keychron RGB-save commands.

## Host Interaction protocol harness

`host_interaction_protocol.py` is the packet codec for the separate
`abralia_host_interaction` firmware. Its unit tests use no hardware.

After that firmware has been flashed manually, read capabilities without
changing keyboard behavior:

```sh
.venv/bin/python keychron_host_interaction_demo.py --probe-only
```

The guarded manual demo requires typing `INTERACT`. It claims a volatile
session, stages Home/End/knob bindings, sends one-second heartbeats, prints and
ACKs events, and releases the session on exit:

```sh
.venv/bin/python keychron_host_interaction_demo.py --seconds 30
```

Force-selected testing is separately opt-in:

```sh
.venv/bin/python keychron_host_interaction_demo.py \
  --seconds 30 \
  --force-selected
```

The harness does not implement an agent broker, change RGB, persist settings,
or flash firmware. Close Launcher/VIA before a physical run because they share
the Raw HID interface.

## Support levels

| Level | Meaning |
| --- | --- |
| 0 | Keychron detected; compatible RGB control not established |
| 1 | Global RGB effect/brightness control |
| 2 | Individual key colors |
| 3 | Individual key colors plus true per-key brightness/off |

The stock V3 8K firmware is Level 2: individual hue/saturation works, but its
solid renderer replaces per-key brightness with global brightness. Therefore
the demo's `auto` background chooses white. Black background with colored keys
requires Level 3 or a later firmware change.

## Environment

```sh
cd experiments/keychron-rgb-controller-python
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Close Keychron Launcher and VIA before running. On macOS, the terminal or host
application may need Input Monitoring permission. Linux requires an appropriate
HID `udev` rule.

Read-only detection:

```sh
python keychron_rgb_demo.py --probe-only
```

Interactive lighting demonstration:

```sh
python keychron_rgb_demo.py
```

Non-interactive demonstration with three two-second color cycles:

```sh
python keychron_rgb_demo.py --yes --cycles 3 --hold-seconds 2
```

Use `--background white`, `--background black`, or the default
`--background auto`. If Level 3 is unavailable, a black request falls back to
white rather than claiming unsupported individual-off behavior.

`--cycles` controls how many times the five foreground colors rotate, while
`--hold-seconds` controls how long each assignment remains visible. The full
original RGB state is restored only after the last cycle or after interruption.

## Full-frame stress test

`keychron_rgb_stress_test.py` generates a new random color for every LED on
every update. Hues are unique within a frame, and the generator rejects a frame
if any physical LED would retain its previous hue. It reports packet timing,
the achieved update rate, and scheduling overruns before verifying restoration.

Rapid randomized full-key colors may be unsuitable for people with
photosensitivity. The default temporary brightness is limited to 96/255, and
an interactive run requires typing `STRESS` before any lighting write.

Read-only stress-test preflight:

```sh
python keychron_rgb_stress_test.py --probe-only
```

Run 100 frames with a target interval of 0.05 seconds:

```sh
python keychron_rgb_stress_test.py --cycles 100 --hold-seconds 0.05
```

All timing and brightness arguments are configurable. `--yes` skips the
interactive warning for a supervised non-interactive run.

After effect 25 firmware has been flashed manually, its direct and guarded
independent-V paths can be exercised explicitly:

```sh
python keychron_rgb_stress_test.py --custom-independent-v --cycles 100 --hold-seconds 0.05 --yes
```

This option does not alter stock-firmware detection. It selects effect 25,
tests one unchanged direct `0xA8/0x0A` write, streams guarded frames containing
independent zero-to-255 V values, checks sequence/status responses, waits for
the two-second return to the firmware idle halo, and restores the original
volatile RGB state.

## Current-firmware FPS sweep

`keychron_rgb_fps_sweep.py` benchmarks the current Abralia effect-25 firmware
with complete 87-key guarded frames. It requests 20, 30, 40, 50, 60, and 70
FPS by default, measures achieved rate, average/p95/maximum frame-update time,
counts schedule overruns, and reports both the highest low-jitter rate and the
highest requested rate achieved on average.
The 70 FPS stage intentionally exceeds QMK's approximately 62.5 FPS render
ceiling so the report can reveal saturation.

The visual workload is a smoothly moving full-board gradient rather than
random hard flashes. Default brightness is limited to 96/255. Synchronized
AVFoundation camera recording is enabled by default at 640x480 and 30 FPS;
this format was selected because the current Brio delivered approximately 29
FPS, while its tested 720p and 1080p modes delivered only approximately 5 FPS.
Recording must start successfully before the script changes keyboard lighting.
After cleanup, the script prints the finalized MP4 path and probes the file to
report its actual delivered frame rate. Camera footage is visual evidence for
smoothness or stalls; protocol timing remains the FPS measurement. A one-second
camera tail records the restored lighting state after the sweep.

Read-only device detection:

```sh
python keychron_rgb_fps_sweep.py --probe-only
```

Run the complete sweep with camera recording:

```sh
python keychron_rgb_fps_sweep.py
```

Use `--rates`, `--seconds-per-rate`, `--brightness`, and `--motion-hz` to tune
the workload. Camera input, frame rate, size, and output are configurable with
`--camera-device`, `--camera-fps`, `--camera-size`, and `--camera-output`.
The restored-state tail is configurable with `--camera-tail-seconds`.
`--no-camera` is available only for an intentional timing-only run. The script
uses volatile commands and restores the complete original lighting state on
completion or interruption.

## Random-key idle halo

`keychron_rgb_idle_halo.py` is a host-driven prototype for effect 25. It picks
one random key, breathes smoothly from off to the configured global brightness
and back, and uses the V3 8K's physical LED coordinates to create a finite
radial per-key V gradient. Each complete breath uses one randomly selected,
reduced-saturation palette color: coral red, mint green, soft azure, or warm
amber. Keys outside the halo radius remain exactly black throughout the
animation.

Run until interrupted, using ten seconds per breath and a brightness ceiling
of 112:

```sh
python keychron_rgb_idle_halo.py --duration 10 --brightness 112
```

Use `--cycles 3` for a bounded demonstration. Halo radius, center-weighted
falloff power, update rate, keepalive interval, random seed, and device index
are configurable. Increase `--halo-power` for a tighter, more contrasted halo.
The default final-V cutoff of 8 turns extremely dim mixed colors fully off
before PWM quantization can shift them toward a pure primary. The script uses
volatile commands only and restores the complete original RGB state after
completion or interruption.

## F-row agent-status display

`keychron_agent_status_demo.py` uses F1 through F12 as twelve independent
agent indicators. Every non-agent key remains black. One complete rotation
starts with all twelve states visible at once, then shifts the states across
the F-row until every agent key has demonstrated every state.

The demo vocabulary combines Abralia's canonical session states with common
agent-harness request states:

| State | Visual behavior | Meaning |
| --- | --- | --- |
| `unknown` | dim white, five-second breath | no authoritative state yet |
| `idle` | dim blue, steady | ready with no active turn |
| `queued` | violet, steady | accepted and waiting to start |
| `running` | cyan, 2.4-second breath | reasoning, tools, commands, or edits |
| `waiting_user` | amber, 1.6-second breath | general user input required |
| `waiting_approval` | bright yellow, 0.9-second breath | live approval request |
| `waiting_choice` | purple, 1.4-second breath | structured choice/elicitation |
| `completed_unread` | bright green, 1.1-second breath | finished and needs attention |
| `completed_seen` | dim green, steady | finished and acknowledged |
| `interrupted` | orange, steady | cancelled or interrupted |
| `error` | bright red, 0.75-second breath | turn or harness failure |
| `stale` | very dim gray, 4.5-second breath | disconnected or heartbeat lost |

`running` intentionally includes thinking, tool execution, commands, and file
changes because those are item-level details rather than distinct session
lifecycle states. The demo uses smooth breathing rather than hard flashing.

Print the complete legend without accessing the keyboard:

```sh
python keychron_agent_status_demo.py --list-statuses
```

Run one bounded 12-step rotation at the maximum brightness of 255:

```sh
python keychron_agent_status_demo.py
```

Use `--step-seconds`, `--brightness`, `--fps`, and `--cycles` to tune the
demonstration. `--cycles 0` runs until interrupted. The script requires the
Abralia firmware with effect 25, uses guarded volatile frames, and restores the
complete original RGB state after completion or interruption.

## Saturation-fade demo

`keychron_rgb_saturation_fade.py` fills the keyboard with white, assigns
W/A/S/D/Space distinct colors, then gradually reduces only those keys'
saturation until they merge into the white background. This uses the stock
renderer successfully because it changes per-key saturation rather than
per-key brightness.

Run the default three-second fade using 30 steps at 0.1-second intervals:

```sh
python keychron_rgb_saturation_fade.py --steps 30 --step-seconds 0.1
```

The selected keys, starting saturation, brightness, fade interval, final hold,
and target device are configurable. As with the other demos, the complete
original state is restored and verified after the last step or interruption.

## Codex hero frame

`keychron_codex_logo_frame.py` displays the final photographed Codex-style
`>_` composition on effect 25. The mint-green chevron uses F4, 5, T, F, and C;
the cool-white underscore uses N, M, Comma, and Dot. Every other LED remains
exactly black to demonstrate independent per-key brightness.

Run until interrupted, then restore the complete prior lighting snapshot:

```sh
python keychron_codex_logo_frame.py
```

Type `LOGO` when prompted. Use `--seconds 30` for a bounded display or
`--brightness` to adjust the temporary global ceiling. The script keeps the
guarded-frame watchdog alive while running, restores on a clean exit, and
returns to the firmware idle halo if the host process disappears.

## License

Abralia-authored experiment code is licensed under Apache-2.0. See the
repository-root [`LICENSE.md`](../../LICENSE.md).
