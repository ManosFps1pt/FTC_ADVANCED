"""Measure video delay using the robot's recorded RGB indicator commands.

Run from the repository root, for example:
    python -m recording_viewer.backend.estimate_video_delay VIDEO.mp4 SESSION/raw

Telemetry can also be a saved JSON response from the viewer's recording API.
The optional ROI is a normalized x y width height rectangle covering the LED's
path through the frame. Positive delay means video_time = telemetry_time + delay.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re

import cv2
import numpy as np


# Servo positions from the viewer RGB palette. Quantize to the closest color
# within the active range so rounded commands such as 0.33 still count as orange.
HUES = {"red": 0, "orange": 15, "yellow": 30, "sage": 43, "green": 60,
        "azure": 95, "blue": 115, "indigo": 128, "violet": 150}
POSITIONS = (0.277, 0.333, 0.388, 0.444, 0.500, 0.555, 0.611, 0.666, 0.722)


@dataclass(frozen=True)
class Edge:
    seconds: float
    color: str
    rising: bool
    before: float
    after: float


def command_color(value: object) -> str | None:
    try:
        position = float(value)
    except (TypeError, ValueError):
        return None
    if not 0 <= position <= 1:
        return None
    if position < POSITIONS[0] - 0.02:
        return "off"
    if position > POSITIONS[-1] + 0.02:
        return "white"
    index = min(range(len(POSITIONS)), key=lambda i: abs(position - POSITIONS[i]))
    return tuple(HUES)[index]


def load_telemetry(path: Path) -> dict:
    if path.is_file() and path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8-sig"))

    from recording_viewer.backend.main import Recording

    if path.is_file() and path.name.lower().endswith((".ftclog", ".ftclog.partial")):
        return Recording(path.parent, log_file=path).browser_model()
    return Recording(path).browser_model()


def find_flash_edges(
    model: dict, signal_id: str | None, color: str | None,
) -> tuple[str, list[str], list[Edge]]:
    snapshots = model["snapshots"]
    if not snapshots:
        raise ValueError("Telemetry contains no snapshots")
    start_ns = int(snapshots[0]["robotTimeNs"])
    end = (int(snapshots[-1]["robotTimeNs"]) - start_ns) / 1e9
    signals = (model.get("catalog") or {}).get("signals", [])
    indicator = re.compile(r"rgb|indicator|(?<![a-z])led(?![a-z])", re.IGNORECASE)
    signal_ids = [signal_id] if signal_id else [
        signal["id"] for signal in signals
        if indicator.search(f"{signal['id']} {signal.get('label', '')} {signal.get('deviceId', '')}")
        and signal.get("valueType") in ("float64", "int64")
    ]
    best_edges: list[Edge] = []
    best_signal = None
    best_colors: list[str] = []
    for candidate in signal_ids:
        runs: list[tuple[float, str | None]] = []
        for sample in snapshots:
            state = command_color(sample["values"].get(candidate))
            if not runs or runs[-1][1] != state:
                runs.append(((int(sample["robotTimeNs"]) - start_ns) / 1e9, state))
        edges = []
        for index, (seconds, state) in enumerate(runs[1:], 1):
            start, previous = runs[index - 1]
            stop = runs[index + 1][0] if index + 1 < len(runs) else end
            if None in (previous, state) or min(seconds - start, stop - seconds) < 0.09:
                continue
            for hue, rising in ((previous, False), (state, True)):
                if hue in HUES and (color is None or hue == color):
                    edges.append(Edge(seconds, hue, rising, seconds - start, stop - seconds))
        # A color seen only once cannot independently identify a flash pattern.
        colors = sorted({edge.color for edge in edges})
        colors = [hue for hue in colors if sum(edge.color == hue for edge in edges) >= 2]
        edges = [edge for edge in edges if edge.color in colors]
        if len(edges) > len(best_edges):
            best_signal, best_colors, best_edges = candidate, colors, edges
    if best_signal is None or len({edge.seconds for edge in best_edges}) < 3:
        raise ValueError("Telemetry needs at least three distinct RGB indicator transitions")
    return best_signal, best_colors, best_edges


def read_video(path: Path, roi: list[float], colors: list[str]) -> tuple[np.ndarray, dict[str, np.ndarray], str]:
    capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 2])
    try:
        if not capture.isOpened():
            raise ValueError(f"Cannot open video {path}")
        fps = capture.get(cv2.CAP_PROP_FPS)
        times = []
        scores = {color: [] for color in colors}
        x, y, width, height = roi
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            times.append(capture.get(cv2.CAP_PROP_POS_MSEC) / 1000)
            frame_height, frame_width = frame.shape[:2]
            crop = frame[round(y * frame_height):round((y + height) * frame_height),
                         round(x * frame_width):round((x + width) * frame_width)]
            if not crop.size:
                raise ValueError("ROI is smaller than a video pixel")
            scale = min(1, 1024 / frame_width)
            if scale < 1:
                crop = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)),
                                         max(1, round(crop.shape[0] * scale))),
                                  interpolation=cv2.INTER_AREA)
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            # Use the colored halo around the LED's often white, clipped center.
            # Weight bright pixels more heavily to suppress colored field objects.
            brightness = (hsv[:, :, 2].astype(np.float32) / 255) ** 5
            for color in colors:
                hue = HUES[color]
                mask = cv2.inRange(hsv, (max(0, hue - 16), 45, 150), (min(179, hue + 16), 255, 255))
                if hue < 16:
                    mask |= cv2.inRange(hsv, (180 + hue - 16, 45, 150), (179, 255, 255))
                evidence = (mask.astype(np.float32) / 255) * brightness
                patch = cv2.GaussianBlur(evidence, (0, 0), 1.5)
                scores[color].append(float(patch.max()))
    finally:
        capture.release()

    if len(times) < 3:
        raise ValueError("Video has fewer than three readable frames")
    times = np.array(times)
    clock = "video_pts"
    if not np.all(np.isfinite(times)) or np.any(np.diff(times) <= 0):
        if not np.isfinite(fps) or fps <= 0:
            raise ValueError("Video has no usable frame timestamps or FPS")
        times = np.arange(len(times)) / fps
        clock = "nominal_fps"
    return times - times[0], {color: np.array(values) for color, values in scores.items()}, clock


def estimate_delay(
    times: np.ndarray, scores: dict[str, np.ndarray], edges: list[Edge], max_delay: float,
) -> dict[str, int | float]:
    frame_interval = float(np.median(np.diff(times)))
    offsets = np.linspace(-max_delay, max_delay, 2 * int(np.ceil(max_delay / min(0.02, frame_interval))) + 1)
    # Compare several frames on each side of each expected edge. Equal event
    # weights keep the long steady parts of the recording from dominating.
    # Rare color changes get more weight because they disambiguate repeated flashes.
    normalized = {}
    contrast_rows, weights, usable_edges = [], [], []
    for color, evidence in scores.items():
        dark, low, high = np.quantile(evidence, (0.05, 0.5, 0.99))
        if high - low < 0.02:
            low = dark
        if high - low < 0.02:
            continue
        normalized[color] = np.clip((evidence - low) / (high - low), -0.25, 1.25)
        color_edges = [edge for edge in edges if edge.color == color]
        for edge in color_edges:
            candidate = edge.seconds + offsets
            before = min(edge.before * 0.75, 0.13)
            after = min(edge.after * 0.75, 0.13)
            left = np.mean([np.interp(candidate - before * factor, times, normalized[color])
                            for factor in (0.45, 0.72, 1.0)], axis=0)
            right = np.mean([np.interp(candidate + after * factor, times, normalized[color])
                             for factor in (0.45, 0.72, 1.0)], axis=0)
            valid = (candidate - before >= times[0]) & (candidate + after <= times[-1])
            contrast_rows.append(np.where(valid, (right - left) * (1 if edge.rising else -1), np.nan))
            weights.append(1 / np.sqrt(len(color_edges)))
            usable_edges.append(edge)
    if not contrast_rows:
        raise ValueError("Little visible LED activity. Try an ROI covering the robot's path")
    contrast = np.array(contrast_rows)
    weights = np.array(weights)[:, None]
    support = (np.isfinite(contrast) * weights).sum(axis=0)
    agreement = np.nansum(contrast * weights, axis=0) / np.maximum(support, 1e-9)
    agreement[support < weights.sum() * 0.6] = -np.inf
    best = int(np.argmax(agreement))
    if agreement[best] < 0.18:
        raise ValueError("No reliable correspondence between video and telemetry flashes")
    if best in (0, len(offsets) - 1):
        raise ValueError("Best match is at the search boundary. Increase --max-delay")
    alternative = agreement[np.abs(offsets - offsets[best]) >= 0.22]
    if alternative.size and np.max(alternative) >= agreement[best] - 0.05:
        raise ValueError("Several delays match the flashes. Try a tighter ROI or --max-delay")

    # Refine the coarse match with individual frame-to-frame edges and use the
    # median to reduce the effect of occlusions and telemetry sampling jitter.
    coarse = offsets[best]
    matches_by_time: dict[float, list[float]] = {}
    midpoints = (times[:-1] + times[1:]) / 2
    deltas = {color: np.diff(evidence) for color, evidence in normalized.items()}
    for edge in usable_edges:
        near = np.flatnonzero(np.abs(midpoints - edge.seconds - coarse) <= 0.10)
        if not near.size:
            continue
        changes = deltas[edge.color][near] * (1 if edge.rising else -1)
        peak = int(np.argmax(changes))
        if changes[peak] >= 0.13:
            matches_by_time.setdefault(edge.seconds, []).append(float(midpoints[near[peak]] - edge.seconds))
    matches = np.array([np.median(values) for values in matches_by_time.values()])
    edge_count = len({edge.seconds for edge in edges})
    visible_edge_count = len({edge.seconds for edge in usable_edges})
    if len(matches) < max(3, int(np.ceil(visible_edge_count * 0.25))):
        raise ValueError("Too few visible frame transitions to refine the delay")
    delay = float(np.median(matches))
    return {
        "video_delay_seconds": round(delay, 4),
        "matched_edges": len(matches),
        "telemetry_edges": edge_count,
        "median_absolute_deviation_seconds": round(float(np.median(np.abs(matches - delay))), 4),
        "median_video_frame_interval_seconds": round(frame_interval, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path)
    parser.add_argument("telemetry", type=Path, help="Session folder, raw folder, .ftclog file, or viewer API JSON")
    parser.add_argument("--signal", help="Override automatic RGB indicator channel selection")
    parser.add_argument("--color", choices=HUES, help="Use only this LED color")
    parser.add_argument("--roi", type=float, nargs=4, default=[0, 0, 1, 1],
                        metavar=("X", "Y", "WIDTH", "HEIGHT"), help="Search rectangle in fractions of the frame")
    parser.add_argument("--max-delay", type=float, default=4, help="Search both directions within this many seconds")
    args = parser.parse_args()
    x, y, width, height = args.roi
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 - x and 0 < height <= 1 - y):
        parser.error("--roi must be a positive rectangle inside the frame")
    if not np.isfinite(args.max_delay) or args.max_delay <= 0:
        parser.error("--max-delay must be positive and finite")

    try:
        signal, colors, edges = find_flash_edges(load_telemetry(args.telemetry), args.signal, args.color)
        times, scores, clock = read_video(args.video, args.roi, colors)
        result = estimate_delay(times, scores, edges, args.max_delay)
    except (OSError, ValueError, KeyError, TypeError, cv2.error) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps({**result, "signal": signal, "colors": colors, "video_clock": clock}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
