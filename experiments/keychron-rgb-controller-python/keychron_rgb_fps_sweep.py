#!/usr/bin/env python3
# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Full-matrix FPS sweep for the current Abralia effect-25 firmware."""

from __future__ import annotations

import argparse
import json
import math
import signal
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from keychron_rgb_demo import (
    DemoError,
    DetectionResult,
    FrameFlags,
    FrameOperation,
    FrameState,
    HSV,
    KeychronProtocol,
    PER_KEY_RGB_INDEPENDENT_V_EFFECT,
    RawHIDConnection,
    detect_candidate,
    enumerate_keychron_candidates,
    print_detection,
    restore,
    snapshot,
    write_frame,
)
from keychron_rgb_stress_test import choose_stress_device, wait_for_frame_status


@dataclass(frozen=True)
class RateResult:
    target_fps: float
    achieved_fps: float
    frames: int
    average_update_ms: float
    p95_update_ms: float
    maximum_update_ms: float
    schedule_overruns: int
    on_target: bool
    sustained: bool
    verdict: str


@dataclass
class CameraRecording:
    process: subprocess.Popen[bytes]
    output_path: Path
    requested_fps: float


@dataclass(frozen=True)
class CameraCaptureResult:
    width: int
    height: int
    frames: int
    duration_seconds: float
    average_fps: float
    size_bytes: int


def parse_rates(value: str) -> tuple[float, ...]:
    try:
        rates = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "rates must be a comma-separated list of numbers"
        ) from error
    if not rates or any(rate <= 0 or rate > 240 for rate in rates):
        raise argparse.ArgumentTypeError("each rate must be in (0, 240]")
    return rates


def parse_camera_size(value: str) -> str:
    parts = value.lower().split("x")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise argparse.ArgumentTypeError("camera size must look like WIDTHxHEIGHT")
    width, height = (int(part) for part in parts)
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("camera dimensions must be positive")
    return f"{width}x{height}"


def default_camera_output() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / f"abralia-rgb-fps-sweep-{timestamp}.mp4"


def parse_fraction(value: str) -> float:
    numerator, separator, denominator = value.partition("/")
    if separator:
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else 0.0
    return float(value)


def start_camera_recording(
    device: str,
    fps: float,
    size: str,
    output_path: Path,
) -> CameraRecording:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise DemoError("ffmpeg is required for synchronized camera recording.")
    if output_path.exists():
        raise DemoError(f"Camera output already exists: {output_path}")
    if not output_path.parent.is_dir():
        raise DemoError(f"Camera output directory does not exist: {output_path.parent}")

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "avfoundation",
        "-framerate",
        f"{fps:g}",
        "-video_size",
        size,
        "-pixel_format",
        "nv12",
        "-i",
        f"{device}:none",
        "-an",
        "-c:v",
        "h264_videotoolbox",
        "-b:v",
        "4M",
        "-y",
        str(output_path),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    # Fail before changing keyboard lighting if the camera cannot open or the
    # requested format is unsupported.
    time.sleep(1.0)
    if process.poll() is not None:
        _stdout, stderr = process.communicate()
        detail = stderr.decode(errors="replace").strip()
        raise DemoError(
            f"Camera recording failed to start: {detail or 'unknown error'}"
        )

    print(f"Camera recording started: {output_path}", flush=True)
    return CameraRecording(
        process=process,
        output_path=output_path,
        requested_fps=fps,
    )


def probe_camera_recording(output_path: Path) -> CameraCaptureResult:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise DemoError("ffprobe is required to verify camera delivery.")
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,nb_frames",
        "-show_entries",
        "format=duration,size",
        "-of",
        "json",
        str(output_path),
    ]
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip()
        raise DemoError(f"Camera recording probe failed: {detail}")
    try:
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        media_format = payload["format"]
        duration = float(media_format["duration"])
        frames = int(stream.get("nb_frames") or 0)
        average_fps = parse_fraction(stream["avg_frame_rate"])
        return CameraCaptureResult(
            width=int(stream["width"]),
            height=int(stream["height"]),
            frames=frames,
            duration_seconds=duration,
            average_fps=average_fps,
            size_bytes=int(media_format["size"]),
        )
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise DemoError(
            "Camera recording probe returned incomplete metadata."
        ) from error


def stop_camera_recording(recording: CameraRecording) -> CameraCaptureResult:
    if recording.process.poll() is None:
        recording.process.send_signal(signal.SIGINT)
    try:
        _stdout, stderr = recording.process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        recording.process.terminate()
        _stdout, stderr = recording.process.communicate(timeout=5)

    if not recording.output_path.exists() or recording.output_path.stat().st_size == 0:
        detail = stderr.decode(errors="replace").strip()
        raise DemoError(
            f"Camera recording was not finalized: {detail or 'empty output'}"
        )
    capture = probe_camera_recording(recording.output_path)
    print(f"Camera recording finalized: {recording.output_path}", flush=True)
    print(
        f"Camera delivery: {capture.width}x{capture.height}, "
        f"{capture.frames} frames over {capture.duration_seconds:.2f}s, "
        f"{capture.average_fps:.2f} FPS average.",
        flush=True,
    )
    if capture.average_fps < recording.requested_fps * 0.9:
        print(
            f"WARNING: camera delivered below 90% of the requested "
            f"{recording.requested_fps:g} FPS.",
            flush=True,
        )
    return capture


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    if not 0 <= fraction <= 1:
        raise DemoError("Percentile fraction must be in 0...1.")
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def smooth_gradient_frame(
    elapsed_seconds: float,
    led_count: int,
    motion_hz: float,
) -> list[HSV]:
    """Generate a smooth, full-board moving gradient without hard flashes."""
    if led_count <= 0:
        raise DemoError("LED count must be positive.")
    if motion_hz <= 0:
        raise DemoError("Motion frequency must be positive.")

    temporal_phase = 2.0 * math.pi * motion_hz * elapsed_seconds
    hue_offset = round(256 * motion_hz * elapsed_seconds)
    frame: list[HSV] = []
    for index in range(led_count):
        spatial_phase = 4.0 * math.pi * index / led_count
        envelope = 0.5 + 0.5 * math.sin(spatial_phase - temporal_phase)
        value = round(64 + 191 * envelope)
        hue = (index * 256 // led_count + hue_offset) & 0xFF
        frame.append(HSV(hue=hue, saturation=220, value=value))
    return frame


def summarize_rate(
    target_fps: float,
    frame_starts: list[float],
    update_durations: list[float],
    schedule_overruns: int,
) -> RateResult:
    frames = len(update_durations)
    if len(frame_starts) >= 2:
        elapsed = frame_starts[-1] - frame_starts[0]
        achieved_fps = (len(frame_starts) - 1) / elapsed if elapsed > 0 else 0.0
    else:
        achieved_fps = 0.0

    average_update = statistics.fmean(update_durations) if update_durations else 0.0
    p95_update = percentile(update_durations, 0.95)
    maximum_update = max(update_durations, default=0.0)
    target_interval = 1.0 / target_fps
    on_target = achieved_fps >= target_fps * 0.95
    sustained = on_target and p95_update <= target_interval
    if not on_target:
        verdict = "SATURATED"
    elif sustained:
        verdict = "SUSTAINED"
    else:
        verdict = "JITTERED"
    return RateResult(
        target_fps=target_fps,
        achieved_fps=achieved_fps,
        frames=frames,
        average_update_ms=average_update * 1000,
        p95_update_ms=p95_update * 1000,
        maximum_update_ms=maximum_update * 1000,
        schedule_overruns=schedule_overruns,
        on_target=on_target,
        sustained=sustained,
        verdict=verdict,
    )


def run_rate_stage(
    protocol: KeychronProtocol,
    target_fps: float,
    seconds_per_rate: float,
    motion_hz: float,
    led_count: int,
    sequence: int,
    sweep_start: float,
) -> tuple[RateResult, int]:
    frame_count = max(2, round(target_fps * seconds_per_rate))
    target_interval = 1.0 / target_fps
    overrun_tolerance = max(0.0005, target_interval * 0.05)
    frame_starts: list[float] = []
    update_durations: list[float] = []
    schedule_overruns = 0
    stage_start = time.monotonic()

    for frame_index in range(frame_count):
        deadline = stage_start + frame_index * target_interval
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

        frame_start = time.monotonic()
        if frame_index > 0 and frame_start - deadline > overrun_tolerance:
            schedule_overruns += 1
        frame_starts.append(frame_start)

        frame = smooth_gradient_frame(
            elapsed_seconds=frame_start - sweep_start,
            led_count=led_count,
            motion_hz=motion_hz,
        )
        update_start = time.monotonic()
        write_frame(protocol, frame)
        protocol.frame_control(FrameOperation.COMMIT, sequence)
        wait_for_frame_status(
            protocol,
            lambda status, expected=sequence: bool(
                status.flags & FrameFlags.ACTIVE_VALID
            )
            and status.active_sequence == expected
            and status.back_buffer_free,
            f"active frame sequence {sequence}",
        )
        update_durations.append(time.monotonic() - update_start)
        sequence = (sequence + 1) & 0xFF

    return (
        summarize_rate(
            target_fps,
            frame_starts,
            update_durations,
            schedule_overruns,
        ),
        sequence,
    )


def print_results(results: list[RateResult]) -> None:
    print("\nFPS sweep results")
    print(
        "target  achieved  frames  avg update  p95 update  max update  "
        "overruns  result"
    )
    for result in results:
        print(
            f"{result.target_fps:6.1f}  {result.achieved_fps:8.2f}  "
            f"{result.frames:6d}  {result.average_update_ms:8.2f}ms  "
            f"{result.p95_update_ms:8.2f}ms  "
            f"{result.maximum_update_ms:8.2f}ms  "
            f"{result.schedule_overruns:8d}  {result.verdict}"
        )

    sustained = [result.target_fps for result in results if result.sustained]
    on_target = [result.target_fps for result in results if result.on_target]
    if sustained:
        print(f"\nHighest low-jitter sustained rate: {max(sustained):g} FPS")
    else:
        print("\nNo requested rate met the low-jitter sustained criteria.")
    if on_target:
        print(f"Highest requested rate achieved on average: {max(on_target):g} FPS")
    else:
        print("No requested rate reached 95% of its target average.")
    print(
        "These figures cover ten full-frame RGB writes plus guarded commit/"
        "display acknowledgement. Physical appearance still requires direct "
        "observation."
    )


def run_fps_sweep(
    result: DetectionResult,
    rates: tuple[float, ...],
    seconds_per_rate: float,
    brightness: int,
    motion_hz: float,
) -> None:
    assert result.led_count is not None
    candidate = result.candidate

    print("\nCurrent-firmware effect-25 FPS sweep")
    print(f"    LEDs per frame: {result.led_count}")
    print("    Packets per frame: 10 RGB + 1 guarded commit + status polling")
    print(f"    Requested rates: {', '.join(f'{rate:g}' for rate in rates)} FPS")
    print(f"    Seconds per rate: {seconds_per_rate:g}")
    print(f"    Temporary brightness: {brightness}/255")
    print(f"    Smooth gradient motion: {motion_hz:g} Hz")

    with RawHIDConnection(candidate.path) as connection:
        protocol = KeychronProtocol(connection)
        original = snapshot(protocol, result.led_count)
        restore_required = False
        results: list[RateResult] = []

        try:
            protocol.set_brightness(0)
            restore_required = True
            protocol.set_effect(PER_KEY_RGB_INDEPENDENT_V_EFFECT)
            protocol.set_brightness(0)
            if protocol.effect() != PER_KEY_RGB_INDEPENDENT_V_EFFECT:
                raise DemoError(
                    "Effect 25 is unavailable; flash the current Abralia "
                    "firmware first."
                )

            status = protocol.frame_status()
            if status.state is not FrameState.AWAITING:
                protocol.frame_control(FrameOperation.AWAIT)
                wait_for_frame_status(
                    protocol,
                    lambda current: current.state is FrameState.AWAITING,
                    "AWAITING state",
                )

            protocol.frame_control(FrameOperation.BEGIN)
            wait_for_frame_status(
                protocol,
                lambda current: current.state is FrameState.GUARDED
                and current.back_buffer_free,
                "GUARDED state with a free back buffer",
            )

            sequence = 0
            sweep_start = time.monotonic()
            protocol.set_brightness(brightness)
            for target_fps in rates:
                print(f"    Sweeping {target_fps:g} FPS...", flush=True)
                rate_result, sequence = run_rate_stage(
                    protocol=protocol,
                    target_fps=target_fps,
                    seconds_per_rate=seconds_per_rate,
                    motion_hz=motion_hz,
                    led_count=result.led_count,
                    sequence=sequence,
                    sweep_start=sweep_start,
                )
                results.append(rate_result)
        finally:
            if restore_required:
                protocol.set_brightness(0)
                try:
                    protocol.frame_control(FrameOperation.AWAIT)
                    wait_for_frame_status(
                        protocol,
                        lambda current: current.state is FrameState.AWAITING,
                        "cleanup AWAITING state",
                    )
                finally:
                    restore(protocol, original)

                restored = snapshot(protocol, result.led_count)
                if restored != original:
                    raise DemoError("FPS-sweep restoration readback mismatch.")
                print(
                    f"    Restored effect={restored.effect}, "
                    f"brightness={restored.brightness}.",
                    flush=True,
                )

    print_results(results)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sweep full 87-key guarded effect-25 frames across increasing "
            "target rates and report the practical host-to-display ceiling."
        )
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="scan support without changing keyboard lighting",
    )
    parser.add_argument(
        "--device",
        type=int,
        help="scanned device index when multiple keyboards are connected",
    )
    parser.add_argument(
        "--rates",
        type=parse_rates,
        default=parse_rates("20,30,40,50,60,70"),
        help="comma-separated target FPS values (default: 20,30,40,50,60,70)",
    )
    parser.add_argument(
        "--seconds-per-rate",
        type=float,
        default=2.0,
        help="duration of each requested rate in seconds (default: 2)",
    )
    parser.add_argument(
        "--brightness",
        type=int,
        default=96,
        help="temporary maximum brightness in 0...255 (default: 96)",
    )
    parser.add_argument(
        "--motion-hz",
        type=float,
        default=0.5,
        help="smooth gradient cycles per second (default: 0.5)",
    )
    parser.add_argument(
        "--no-camera",
        action="store_true",
        help="run without synchronized camera recording",
    )
    parser.add_argument(
        "--camera-device",
        default="0",
        help="AVFoundation video-device index (default: 0)",
    )
    parser.add_argument(
        "--camera-fps",
        type=float,
        default=30.0,
        help="camera recording rate (default: 30)",
    )
    parser.add_argument(
        "--camera-size",
        type=parse_camera_size,
        default="640x480",
        help="camera frame size (default: 640x480 for reliable 30 FPS)",
    )
    parser.add_argument(
        "--camera-output",
        type=Path,
        help="new MP4 path; defaults to a timestamped temporary file",
    )
    parser.add_argument(
        "--camera-tail-seconds",
        type=float,
        default=1.0,
        help="record restored lighting after the sweep (default: 1 second)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the interactive animated-lighting confirmation",
    )
    args = parser.parse_args()

    if args.seconds_per_rate <= 0:
        parser.error("--seconds-per-rate must be positive")
    if not 0 <= args.brightness <= 255:
        parser.error("--brightness must be in 0...255")
    if args.motion_hz <= 0 or args.motion_hz > 2:
        parser.error("--motion-hz must be in (0, 2]")
    if args.camera_fps <= 0 or args.camera_fps > 60:
        parser.error("--camera-fps must be in (0, 60]")
    if args.camera_tail_seconds < 0 or args.camera_tail_seconds > 5:
        parser.error("--camera-tail-seconds must be in [0, 5]")
    return args


def main() -> int:
    args = parse_args()

    def interrupt(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, interrupt)
    signal.signal(signal.SIGTERM, interrupt)

    print("STEP 1 — Scan connected HID interfaces for Keychron devices")
    candidates = enumerate_keychron_candidates()
    if not candidates:
        raise DemoError("No connected Keychron HID device was found.")
    results = [detect_candidate(candidate) for candidate in candidates]
    print_detection(results)

    if args.probe_only:
        print("\nProbe-only run complete. No lighting state was changed.")
        return 0

    selected = choose_stress_device(results, args.device)
    print("\nWARNING — rapid full-matrix animation")
    print(
        "This test continuously updates every LED and may be unsuitable for "
        "people with photosensitivity. It uses a smooth moving gradient; do "
        "not stare directly at the keyboard."
    )
    if not args.yes:
        if not sys.stdin.isatty():
            raise DemoError("Interactive confirmation unavailable; rerun with --yes.")
        confirmation = input("Type FPS to continue, or anything else to cancel: ")
        if confirmation != "FPS":
            print("FPS sweep cancelled. No lighting state was changed.")
            return 0

    recording = None
    try:
        if not args.no_camera:
            recording = start_camera_recording(
                device=args.camera_device,
                fps=args.camera_fps,
                size=args.camera_size,
                output_path=args.camera_output or default_camera_output(),
            )
        run_fps_sweep(
            selected,
            rates=args.rates,
            seconds_per_rate=args.seconds_per_rate,
            brightness=args.brightness,
            motion_hz=args.motion_hz,
        )
        if recording is not None and args.camera_tail_seconds > 0:
            print(
                "Recording restored-lighting tail for "
                f"{args.camera_tail_seconds:g} second(s)...",
                flush=True,
            )
            time.sleep(args.camera_tail_seconds)
    finally:
        if recording is not None:
            stop_camera_recording(recording)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted; restoration was requested.", file=sys.stderr)
        raise SystemExit(130)
    except DemoError as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
