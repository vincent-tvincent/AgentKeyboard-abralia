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

Close Keychron Launcher and VIA before a hardware run because only one process
can own the Raw HID interface.

## Full-keyboard geometry bands

`display_full_geometry.py` maps a 19×7 physical-coordinate canvas to the full
keyboard. Red, green, and blue bands make incorrect geometry coverage or LED
addressing visually obvious.

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/display_full_geometry.py \
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
  experiments/desktop-rgb-physical-validation/display_without_refresh.py
```

## Fog-orb animation

`fog_orb_animation.py` renders a drifting volumetric object with layered
turbulence, a luminous core, chromatic shell, trailing wisps, a warm orbiting
spark, sparse embers, and moving occlusion. It is a gradient, texture, frame
cadence, and complete-frame stress test rather than a product scene.

```sh
.venv/bin/python \
  experiments/desktop-rgb-physical-validation/fog_orb_animation.py \
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
  experiments/desktop-rgb-physical-validation/rgb-six-cells.json \
  --target navigation_cluster \
  --strategy row_key_index \
  --seconds 5

.venv/bin/abralia-rgb render-canvas \
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
  --mode cooperative
```

Use `--mode threaded` or `--mode both` for the other shared-session paths, and
`--require-all-controls` for exhaustive physical input coverage. Use
`--routing mirror` when ordinary key behavior should remain enabled during a
regression pass. Positive `--activation-timeout`, `--seconds-per-region`, and
`--combined-seconds` values opt into bounded cleanup deadlines; their default
value of zero waits for the user’s double-Pause gestures.

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
