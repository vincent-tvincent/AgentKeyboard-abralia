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
