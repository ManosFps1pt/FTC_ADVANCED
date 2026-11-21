"""Bounded, deterministic measurements over saved recordings."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

import numpy as np

from recording_viewer.mainteleop_analysis import Settings, analyze, distribution, load_run
from web_driver_station.backend.debugger_results import atomic_json

# Increment when measurement semantics or generated plots change.
ANALYSIS_VERSION = 1
SETTINGS = Settings()
EVENTS = {
    "shooter_dips": ("shooter_disturbances", "drop_rpm", True),
    "shooter_transitions": ("shooter_transitions", "t_s", False),
    "intake_loads": ("intake_load_episodes", "peak_a", True),
    "voltage_sags": ("low_voltage_episodes", "min_v", False),
    "gate_windows": ("gate_windows", "t_s", False),
}
REQUIRED_SIGNALS = {
    "shooter_dips": ("shooter.targetRpm", "shooter.commandedRpm", "shooter.measuredRpm"),
    "shooter_transitions": ("shooter.targetRpm", "shooter.commandedRpm", "shooter.measuredRpm"),
    "intake_loads": ("intake.appliedPower", "intake.currentAmps", "intake.velocityTicksPerSecond"),
    "voltage_sags": ("robot.voltage",),
    "gate_windows": ("fos.dutyCycle",),
}
PLOTS = {"overview": "overview.png", "shooter": "shooter_dips.png", "electrical": "electrical_events.png"}


def fingerprint(streams: list[Path]) -> str:
    files = [(p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in streams]
    return hashlib.sha256(repr((ANALYSIS_VERSION, SETTINGS, files)).encode()).hexdigest()[:24]


@lru_cache(maxsize=2)
def _run(directory: Path, version: str):
    return load_run(directory)


def replay_path(session_id: str, seconds: float | None = None) -> str:
    suffix = "" if seconds is None else f"&t={seconds:.3f}"
    return f"/replays/?recording={session_id}{suffix}"


class RunAnalysis:
    def __init__(self) -> None:
        self._lock = Lock()

    def report(self, directory: Path, version: str) -> dict:
        cache = directory.parent / ".review-cache" / directory.name
        with self._lock:
            path = cache / "report.json"
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                if report.get("fingerprint") == version:
                    return report
            except (OSError, ValueError):
                pass
            summary, tables, _ = analyze(_run(directory, version), SETTINGS)
            report = {"fingerprint": version, "summary": summary, "tables": tables}
            cache.mkdir(parents=True, exist_ok=True)
            atomic_json(path, report)
            return report

    def plot(self, directory: Path, version: str, kind: str) -> Path:
        from recording_viewer.mainteleop_plots import write_plots

        with self._lock:
            cache = directory.parent / ".review-cache" / directory.name / version
            if not cache.is_dir():
                _, tables, traces = analyze(_run(directory, version), SETTINGS)
                cache.parent.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(dir=cache.parent) as temporary:
                    write_plots(Path(temporary), traces, tables, SETTINGS)
                    cache.mkdir(exist_ok=True)
                    for plot in Path(temporary).glob("*.png"):
                        plot.replace(cache / plot.name)
            path = cache / PLOTS[kind]
            if not path.is_file():
                raise ValueError("This recording has no qualifying data for that plot")
            return path

    def signals(self, directory: Path, version: str) -> list[dict]:
        run = _run(directory, version)
        return [
            {"id": name, "units": run.info["units"][name], "samples": int(np.isfinite(values).sum())}
            for name, values in sorted(run.columns.items())
        ]

    def window(self, directory: Path, version: str, signals: list[str], start: float, end: float, points: int) -> dict:
        if end <= start or end - start > 120:
            raise ValueError("Choose a positive time window of at most 120 seconds")
        run = _run(directory, version)
        missing = [name for name in signals if name not in run.columns]
        if missing:
            raise ValueError(f"Unavailable numeric signals: {', '.join(missing)}")
        mask = (run.t >= start) & (run.t <= end)
        indices = np.flatnonzero(mask)
        chosen = indices[np.linspace(0, len(indices) - 1, min(points, len(indices)), dtype=int)]
        return {
            "sample_count": len(indices),
            "seconds": run.t[chosen].tolist(),
            "sequences": run.seq[chosen].tolist(),
            "signals": {
                name: {
                    "units": run.info["units"][name],
                    "statistics": distribution(run.columns[name][indices]),
                    "values": [float(v) if np.isfinite(v) else None for v in run.columns[name][chosen]],
                }
                for name in signals
            },
        }


def event_page(report: dict, session_id: str, kind: str, offset: int, limit: int) -> dict:
    table, key, worst_first = EVENTS[kind]
    rows = sorted(report["tables"][table], key=lambda row: row[key], reverse=worst_first)
    coverage = report["summary"]["quality"]["channel_coverage_fraction"]
    return {
        "kind": kind,
        "total": len(rows),
        "missing_signals": [signal for signal in REQUIRED_SIGNALS[kind] if not coverage.get(signal)],
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "events": [
            {**row, "replay_path": replay_path(session_id, row["t_s"])}
            for row in rows[offset:offset + limit]
        ],
    }
