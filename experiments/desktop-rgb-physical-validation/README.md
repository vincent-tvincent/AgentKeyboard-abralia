# Desktop RGB physical validation

These bounded hardware experiments exercise the production `abralia.rgb`
desktop API against a compatible keyboard running Abralia effect-25 firmware.
They use volatile guarded frames, do not flash firmware, and request restoration
of the pre-command RGB snapshot on normal completion or interruption.

Install the desktop package from the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e abralia/desktop
```

Every keyboard command below supplies a profile explicitly. Select a different
JSON or bundled ID for another target; there is no automatic profile selection.
The breathing indicator follows the profile toggle matrix (the lighting key on
original V3), not a hardcoded Pause legend. Original V3 profiles are
hardware-unverified.

Close Keychron Launcher and VIA before a hardware run because only one process
can own the Raw HID interface.

All display demos in this directory currently default to the maximum temporary
brightness ceiling of 255. Pass `--brightness` explicitly to reduce it.

## Multi-agent concurrency stress test (simulated)

`multi_agent_race_stress.py` runs synthetic agent identities through real STDIO
MCP bridge processes into an isolated simulated backend. It does not connect to
the keyboard, open conversations, or use an already running backend.

Run from the repository root, with an existing directory for the report:

```sh
.venv/bin/python -m pip install -e 'abralia/desktop[backend]'
.venv/bin/python -B \
  experiments/desktop-rgb-physical-validation/multi_agent_race_stress.py \
  --agents 32 --bridges 8 --rounds 40 --seed 11 \
  --report /path/to/output/race-stress.json
```

The cases cover duplicate acquisition, conflicting retries, ownership checks,
concurrent notifications, FIFO mute handling, progress during paging, pickup
while other calls arrive, delayed attention/F-key releases, question replacement,
slot reuse, and last-connection cleanup. Extra audit actions exist only in this
experiment's service subclass. The disconnect case uses real Unix socket
clients directly; the other agent operations use MCP. Bridge heartbeats and
the production command worker remain enabled.

The report records assertions, request counts, latency, sampled queue depth,
remaining allocations, and socket/worker cleanup. Exit status is nonzero on a
failed assertion. `--seed` controls request jitter; OS scheduling still varies.
`--agents` counts synthetic logical callers, while `--bridges` controls the
number of independent socket-producing MCP processes. Pending client requests
may exceed the number reaching the serialized worker at once. This exercises
concurrency correctness; it does not establish a hardware throughput limit or
verify native UI answers. Test-only notification cooldown and expiry settings
are listed in the JSON report.

## Maximum-brightness main-area notification

`main_area_notification_flash.py` puts the complete matrix into a refreshed
black guarded frame, waits for five seconds plus an unrevealed random interval
from zero to 30 seconds, flashes the profile's `alphanumeric_block` once in
yellow-green at brightness 255, returns to black, and restores the pre-command
RGB snapshot. The default notification is one bounded 160 ms flash rather than
a repeating strobe. It is intended to test whether edge lighting remains
noticeable while touch typing with opaque keycaps.

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/main_area_notification_flash.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25
```

Type `FLASH` at the warning prompt. Use `--dry-run` to validate the selected
profile and region without opening the keyboard. One to three flashes are
available through `--flashes`; avoid maximum-brightness testing around anyone
with photosensitive epilepsy or other light sensitivity.

## Main-area sweep-and-pulse notification

`main_area_sweep_notification.py` provides a motion-enhanced comparison for
peripheral attention. After the same refreshed-black randomized wait, a broad
single-color yellow-green band crosses the typing area in 250 ms, the complete
area reaches brightness 255 for 120 ms, and it fades to black over 150 ms.

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/main_area_sweep_notification.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25
```

Type `SWEEP` at the warning prompt. The default animation lasts about 520 ms
after its unpredictable 5–35 second black wait. Use `--dry-run` for validation
without opening the keyboard.

## Center-outward main-area notification

`main_area_expanding_notification.py` tests a direction-neutral motion cue for
opaque favorite keycaps. After the refreshed-black randomized wait, a broad
elliptical yellow-green wave expands from the center of the typing area to its
outer keys over 350 ms. Previously reached keys retain a temporal trail that
halves on every movement frame. The whole area then reaches brightness 255 for
100 ms and fades to black over 150 ms.

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/main_area_expanding_notification.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25
```

Type `EXPAND` at the prompt. Use `--dry-run` to validate without opening the
keyboard. Add `--flashes 2` or `--flashes 3` to repeat the complete expansion,
peak, and fade with a short dim gap.

## Adjustable white-idle expanding notification

`white_idle_expanding_notification.py` keeps the entire keyboard white while
waiting, with `--idle-brightness-percent 50` as the default. The idle setting
controls per-key white value only; it never lowers the notification's fixed
brightness-255 peak. If idle is above 10%, the demo fades it down to 10% before
the center-outward yellow-green notification. Each movement frame leaves the
previously reached keys at 50% of their prior notification intensity, producing
a `100% -> 50% -> 25%` temporal trail. After reaching the outer boundary, a
soft 220 ms edge-to-center fill raises every key from its current tail value to
brightness 255. The demo then uses the original fast 150 ms notification fade
and 180 ms idle transition. It holds the restored idle for three seconds before
restoring the pre-command RGB snapshot.

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/white_idle_expanding_notification.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25
```

For example, use `--idle-brightness-percent 75 --flashes 2` to test the
pre-notification fade, two complete notification cycles, and gentle return.
Type `IDLE` at the warning prompt. Add `--preview-after-5s` to replace the
random interval with a fixed five-second preview wait.

## F1 notification: expand, fill, breathe and blend

`f1_breathing_notification.py` starts with white on every RGB key for five
seconds. It then assigns F1 a demo status color (blue `#2080FF` by default),
lowers only the notification region to 10% white, and plays the existing
center-outward expansion and edge-to-center fill in yellow-green. The filled
frame continues directly into breathing, with no intermediate fade to black.
Over four seconds the reminder blends toward F1's color and settles to a gentler
intensity. Unassigned keys outside the region stay white; F1 stays at its status
color. After twelve seconds of breathing, the demo clears the reminder, briefly
shows white plus F1 status, and restores the original RGB snapshot.

```sh
.venv/bin/python experiments/desktop-rgb-physical-validation/f1_breathing_notification.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25
```

Type `BREATHE` to run. Use `--dry-run` to check without hardware. Change the
example status color with `--status-color FF4020`; quote colors that include
`#`. `--white-brightness-percent`, `--breath-period`, `--color-blend-seconds`,
`--breath-min-percent` and `--breath-max-percent` allow comparison. Breathing
intensity and color blending use linear RGB. These are demo settings, not a
final theme. No Host Interaction bindings or app focus operations are installed
by this RGB-only script.

## Interactive F1 notification with captured physical controls

`f1_notification_interaction_demo.py` uses the Host Interaction firmware's
optional single-action capture capability. The demo derives the activation/mute
position from the explicit profile; it does not look up a Pause keycode or use
an OS listener. On V3 8K that position is `[0,16]`; original V3 profiles use
`[3,14]`. Print Screen is not bound. Older firmware is rejected before claiming
a session or displaying frames. Install the matching updated Host Interaction
image first; this script never flashes firmware itself.

```sh
.venv/bin/python experiments/desktop-rgb-physical-validation/f1_notification_interaction_demo.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --pickup-background status
```

Select enabled RGB effect 25 first and type `DEMO`. The initial keyboard is
white everywhere, then F1 shows the demo status color and the main region plays
expand/fill followed directly by breathing and a gradual status-color blend.
Double-tap the configured toggle to enter active mode. A single tap on the breathing red
toggle mutes, F1 selects the main-region status color even after mute, and green
Scroll Lock picks up the same notification. Captured single taps and holds do
not reach the foreground app. Double tap changes activation without also muting.
Mute does not dismiss the notification, release F1, or trigger another onset.

While inactive with a pending notification, the toggle breathes in saturation
between white and full yellow-green at fixed HSV value (255), including after
mute while pickup remains available. While active, it breathes in brightness,
red with notification controls present, or white after pickup, as a reminder of
the double-tap exit gesture. Inactive after pickup, it returns to idle white.
Routine inactive colors, including F1, retain 75% of their saturation by default
and therefore look whiter. Notification onset/breathing and the inactive toggle
cue bypass that reduction. `--inactive-saturation-percent 100` disables routine
whitening for comparison. Neutral white is unchanged. Both toggle modes use
`--breath-period`; only active toggle breathing uses the minimum/maximum
intensity settings. The main notification animation retains its own brightness
breath and gradual status-color blend.

Supply `--pickup-task-id <task-uuid>` to dispatch that exact local Codex task URL
on pickup; otherwise pickup logs a simulation. Dispatch is not verified focus.
`--pickup-background white` compares white immediately after pickup; F1
selection still shows the status color. `--idle-return-seconds` and
`--return-fade-seconds` affect backlighting only, never activation or bindings.
Double tap controls activation separately. Idle white backlighting remains on
while inactive. `--timeout` bounds the demo; completion or Ctrl-C releases the
session and restores the original RGB snapshot.

To inspect all interactions, use `--timeout 240 --post-seconds 5`:

1. Wait for the five-second all-white start, F1 status, then the notification.
   Observe the inactive white/yellow-green toggle and full-color notification.
2. Double-tap the physical toggle: F1 regains full saturation, the toggle
   breathes red, and Scroll Lock turns green.
3. Single-tap the toggle to mute, then tap F1 to select the status-colored main
   region. Double-tap the toggle to leave active mode and check the saturation
   cue still advertises pickup of the muted notification.
4. Double-tap to re-enter, then single-tap Scroll Lock to pick up. With
   `--pickup-background status`, the main region shows status color and the
   toggle breathes white. After 15 seconds without an Abralia action, the main
   region fades to white; active mode remains enabled.
5. Double-tap to exit. The toggle returns to idle white and the demo ends after
   the post-exit interval. Ctrl-C also stops and restores the original snapshot.

Use `--dry-run` for the offline state/rendering simulation. No Input Monitoring
permission is required by this firmware-based input path. The earlier
`macos_pause_listener.py` remains a separate experimental observer and is not
used by this demo. Original V3 builds/profiles remain physically unverified.

```sh
.venv/bin/python -B -m unittest discover \
  -s experiments/desktop-rgb-physical-validation -p 'test_f1_breathing_notification.py'
```

These scripts use the host-side [Apache-2.0 license](../../LICENSE.md).

## Focused knob paging test

`knob_paging_demo.py` uses the backend's actual `DeviceDriver`, bindings, and page
dispatch with 25 simulated agent slots. It owns the keyboard directly during the
test, so stop another backend/demo first. It requires the matching Host Interaction
firmware and enabled effect 25; it never flashes firmware or changes the keyboard's
global brightness limit.

```sh
.venv/bin/python -B experiments/desktop-rgb-physical-validation/knob_paging_demo.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 --timeout 180
```

The fixture colors are static: page 1 has twelve blue F-keys, page 2 twelve green
F-keys, and page 3 only amber F1 (empty F-keys use 50% of the current white
background value because multiple logical slots are registered). This freezes
only the test fixture rendering; backend paging and physical routing are reused.

1. While inactive, turn the knob and verify its ordinary function without paging.
2. Double-tap the physical mode position. Turn one detent left at page 1: remain
   on page 1. Turn right once to page 2, then once to page 3.
3. Turn right again: stay on page 3. Turn left twice: page 2, then page 1.
4. Double-tap to exit and verify ordinary knob behavior again. Type `q` and Enter
   in the demo terminal to stop, or use Ctrl-C.

Each encoder UP event delivered after protocol retry filtering is logged with its
sequence and direction, followed by the expected and actual page. Boundary detents
are logged even when the page does not change. A summary checks both directions,
both boundaries, mode entry/exit, selection stability and unhandled/duplicate
delivery. It separately reports `duplicate_retries_filtered` across all event
types. Normal inactive volume/mapped
behavior and absence of active-mode leakage still require human confirmation.
The timeout also ends the test and restores the saved RGB/input state.

Use `--dry-run` for synthetic events through the real dispatch path without HID.
Use `--log /path/to/trace.jsonl` for an append-only trace. `--background-percent`
defaults to 50 and controls the background only. Scripts use the
[Apache-2.0 license](../../LICENSE.md).

## Knob overview and Enter confirmation

`knob_overview_demo.py` demonstrates two overview functions: press the physical
knob centre to switch between page browsing and candidate selection on the
current page. It uses the shared device worker and an experimental controller;
the production backend retains its existing behavior. Stop other keyboard
backends/demos first and select enabled effect 25.

```sh
.venv/bin/python -B experiments/desktop-rgb-physical-validation/knob_overview_demo.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 --timeout 240
```

1. Double-tap the physical mode key to activate. Rotate in both directions to
   browse pages. Empty F-keys use 50% of the current background value while
   multiple slots are registered; occupied keys show brighter, static status colors.
2. Press the knob centre: the selection background appears, a candidate receives
   a yellow-green-dominant highlight, and Enter lights yellow-green. Empty slot
   backgrounds retain the relative 50% trial value using this mode's tint. Rotate to preview
   agents on this page. The demo skips holes and stops at the first/last agent.
3. Press the centre again to return to pages and clear the Enter cue; press again
   to try selection. Enter confirms the candidate. An occupied F-key also selects
   its agent directly, in either overview function.
4. Confirmation shows the agent's static status for three seconds, then resets
   to page browsing for another attempt. Escape returns immediately while keeping
   Agent Mode active. The timed reset is a demo convenience.
5. Double-tap the mode key to leave active mode and verify normal knob and Enter
   behavior. Ctrl-C stops at any time; `q` then Enter also stops after mode exit.

The fixture allocates 25 logical slots, then releases 4, 8, 16 and 20 to show
stable holes. It opens no conversations and has no native question. Knob events
are consumed during the brief confirmation display; post-selection knob behavior
is outside this experiment. Enter is captured only for a valid overview candidate.
Delayed confirmations whose candidate or generation changed are ignored and
reported in the trace. A fresh press confirms the current candidate.

Trial appearance is configurable: `--background-percent 25`,
`--selection-tint DDD2FF`, `--selection-background-percent 16`,
`--selection-background-scope slots` (the default; `full` is a legacy trial override), `--highlight-percent 85`, and
`--enter-color A0FF00`. Enter uses the selected yellow-green confirmation color;
the other appearance values remain demo defaults awaiting physical feedback. The
keyboard's global brightness remains the upper bound, including during cleanup;
percentages describe host frame values and do not change that hardware setting.

Use `--dry-run` for synthetic input without opening HID, and
`--log /path/to/trace.jsonl` for optional event/result evidence. The summary keeps
visual confirmation separate from received input and simulated checks. Quit,
timeout and interruption release the session and restore saved RGB state using
the backend's existing recovery path. This script uses
[Apache-2.0](../../LICENSE.md).

## Native question focus experiment

`question_focus_experiment.py` starts a bounded backend session and records the
host side of a user-assisted pickup, Tab, arrow and Enter trial. It does not
inspect app accessibility, synthesize UI keys, or create its own question widget.
Stop another keyboard backend/demo before running it.

```sh
.venv/bin/python -B experiments/desktop-rgb-physical-validation/question_focus_experiment.py \
  --project /path/to/enabled-project \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --thread-id <existing-helper-task-id> --timeout 600 \
  --output /path/to/trace.jsonl
```

After `READY`, coordinate a fresh native async question in the selected helper
task. For this focused trial, the helper acquires its own slot, uses an ordinary
notification for pickup, and asks a native question with ALPHA, BRAVO and CHARLIE.
It does not call `report_question`: this isolates focus/submission from the
unverified numeric-hint mapping. The target answer is BRAVO and has no project
effect.

Start in another conversation's normal composer. Pick up with physical Scroll
Lock while active, press Tab once without clicking the question, use arrows until
BRAVO is highlighted, then press Enter once. Record whether Tab entered the
panel, whether arrows changed selection, and whether Enter submitted BRAVO or
closed the card. Do not switch to the logging terminal between those steps.

The helper must report the actual native reply separately and withdraw/release
its test notification afterward. No reply is an inconclusive/failed submission,
not proof of a particular timeout or button action. The host log alone cannot
establish native focus or submission. `--mode simulated` is available for checking
the runner without opening HID. Ctrl-C or the experiment timeout shuts down the
owned backend and restores saved keyboard state. This experiment uses
[Apache-2.0](../../LICENSE.md).

## Full-keyboard geometry bands

`display_full_geometry.py` maps a 19×7 physical-coordinate canvas to the full
keyboard. Red, green, and blue bands make incorrect geometry coverage or LED
addressing visually obvious.

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/display_full_geometry.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --seconds 15
```

## 6U x 6U geometry square

`display_geometry_square.py` creates a 19U x 7U row-major canvas, draws one
filled 6U x 6U square, and maps it through the production
`geometry_resample` path onto the `full_keyboard` target. It prints the source
canvas, mapped non-black keys, blended keys, large-key selections, and
uncovered cells before displaying the result.

The default square begins at canvas coordinate `(6U, 0U)` so its upper edge
includes the function-row band. Use `--x` and `--y` to move it while keeping
the exact 6U x 6U canvas size. Because the mapping follows physical U-space,
keyboard row gaps and stagger can make the number of illuminated keys differ
from the 36 colored source cells.

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/display_geometry_square.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --seconds 15 \
  --verbose-map
```

## Guarded-frame timeout

`display_without_refresh.py` submits a cyan F-row frame once and deliberately
does not refresh its guarded lease. The firmware should leave the frame and
return to its local awaiting animation after the lease expires. The desktop
snapshot is then restored when the script exits.

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/display_without_refresh.py --profile builtin:keychron-v3-8k-ansi-encoder-effect25
```

## Fog-orb animation

`fog_orb_animation.py` renders a drifting volumetric object with layered
turbulence, a luminous core, chromatic shell, trailing wisps, a warm orbiting
spark, sparse embers, and moving occlusion. It is a gradient, texture, frame
cadence, and complete-frame stress test rather than a product scene.

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/fog_orb_animation.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --seconds 20 \
  --fps 20
```

The script prints its achieved controller frame rate. Start conservatively at
20 FPS before trying 30 FPS on a new host or keyboard.

## Six-cell canvas fixture

`rgb-six-cells.json` is a 3×2 RGB/YMC canvas for the navigation cluster. It can
exercise exact row-index and anchored-row mappings through the production CLI:

```sh
.venv/bin/abralia-rgb render-canvas \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  experiments/desktop-rgb-physical-validation/rgb-six-cells.json \
  --target navigation_cluster \
  --strategy row_key_index \
  --seconds 5

.venv/bin/abralia-rgb render-canvas \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  experiments/desktop-rgb-physical-validation/rgb-six-cells.json \
  --target navigation_cluster \
  --strategy anchored_row_grid \
  --seconds 5
```

## Shared-HID RGB and camera validation

`shared_hid_rgb_validation.py` uses one physical HID owner and exercises every
profile region, layered all-region composition, a moving animation, refreshed
black standby, timeout return to the firmware-local awaiting animation, and
snapshot restoration. It can test cooperative and threaded modes without
enabling Host Interaction bindings:

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/shared_hid_rgb_validation.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --mode both
```

For a non-mirrored Brio 100 recording, enumerate AVFoundation devices first,
resolve the exact camera index, and record while the RGB script runs:

```sh
ffmpeg -f avfoundation \
  -pixel_format <supported-pixel-format> \
  -framerate <supported-camera-fps> \
  -video_size <supported-width>x<supported-height> \
  -i '<brio100-index>:none' \
  -an shared-hid-rgb-validation.mov
```

The Brio footage validates placement, transitions, black/off regions, obvious
stalls, and relative brightness. Auto-exposure and camera color processing do
not establish exact hue, white point, luminance, or color-channel accuracy.

## User-assisted shared-HID interaction validation

`shared_hid_region_interaction_validation.py` renders and activates every
profile region sequentially, then tests their deduplicated union. Before each
activation it installs inactive CAPTURE bindings and shows a breathing Pause
key. It waits for the user’s double-Pause gesture and switches to the region
display only after firmware reports manual mode active. A second double-Pause
must report manual mode inactive before the phase advances. Reserved Pause is
excluded, no host force scope is used, and RGB/input state is restored. The
default run has no host-side activation or phase deadline, so it cannot pull
the keyboard back to inactive during a phase. The script requires physical key
presses and is not part of unattended validation:

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/shared_hid_region_interaction_validation.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --mode cooperative
```

Use `--mode threaded` or `--mode both` for the other shared-session paths, and
`--require-all-controls` for exhaustive physical input coverage. Use
`--routing mirror` when ordinary key behavior should remain enabled during a
regression pass. Positive `--activation-timeout`, `--seconds-per-region`, and
`--combined-seconds` values opt into bounded cleanup deadlines; their default
value of zero waits for the user’s double-Pause gestures.

With protocol-v2 firmware, changing to another RGB effect suspends device
writes without destroying the producer, restores the captured per-key payload,
and keeps the current phase waiting. Returning to effect 25 restarts the
breathing-Pause standby scene, but a new double-Pause is required before the
active region display returns. Outside effect 25, Pause is immediate ordinary
input. The script logs `RGB_EFFECT ... effect25=false/true` for this handoff.

## Inactive/active interaction animation

`effect25_inactive_active_animation.py` installs volatile bindings on the
navigation cluster and continuously animates the rest of the keyboard. While
bindings are configured but interaction is inactive, Pause breathes slowly in
mint green. Double-Pause activates the bindings and changes Pause to an amber
breathing animation. The moving background must continue in both states,
proving that inactive input routing does not block desktop RGB output.

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/effect25_inactive_active_animation.py \
  --profile builtin:keychron-v3-8k-ansi-encoder-effect25 \
  --mode cooperative \
  --seconds 45
```

Type `ANIMATE`, observe several mint standby frames, double-Pause, press the
navigation keys, and observe several amber active frames. The script passes
only after rendering frames in both states. `MIRROR` is the default so tested
navigation keys retain their ordinary behavior. The single-Pause static flash
idea is intentionally omitted because this test uses only existing Raw HID
mode/effect events.

## Optional direct camera capture on macOS

List AVFoundation inputs and select the intended camera explicitly:

```sh
ffmpeg -hide_banner -f avfoundation -list_devices true -i ''
```

Record without a mirrored preview, replacing placeholders with a supported
mode reported by the camera:

```sh
ffmpeg -f avfoundation \
  -pixel_format <pixel-format> \
  -framerate <camera-fps> \
  -video_size <width>x<height> \
  -i '<camera-index>:none' \
  -t <seconds> \
  -an \
  output.mp4
```

Check the delivered frame rate rather than assuming the requested rate:

```sh
ffprobe -v error \
  -select_streams v:0 \
  -show_entries stream=avg_frame_rate,nb_frames,width,height \
  -show_entries format=duration \
  output.mp4
```

Camera footage can establish placement, transitions, obvious stalls, and
relative brightness within one frame. It is not a colorimeter and does not
prove exact RGB values, luminance, PWM behavior, or USB report timing.

Abralia-authored experiment code and documentation are licensed under
Apache-2.0. See the repository-root `LICENSE.md`.
