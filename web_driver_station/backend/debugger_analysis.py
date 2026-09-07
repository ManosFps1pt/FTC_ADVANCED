"""Versioned, deterministic analyzers. Inputs are saved samples, never live UI history."""
from __future__ import annotations

import math
import statistics as stats
from collections import defaultdict
from typing import Any


def spread(values: list[float]) -> dict[str, float] | None:
    return {"median": stats.median(values), "min": min(values), "max": max(values)} if values else None


def stable(rows: list[dict]) -> bool:
    if len(rows) < 10 or rows[-1]["time"] - rows[0]["time"] < 0.99:
        return False
    velocities = [abs(r["velocity"]) for r in rows]
    mean = stats.mean(velocities)
    return mean >= 5 and stats.pstdev(velocities) <= mean * .05 and abs(velocities[-1] - velocities[0]) <= mean * .05


def fit(points: list[dict]) -> dict | None:
    # One observation per stable window: slow acquisition must not weight a point less.
    if len({round(p["voltage"], 3) for p in points}) < 3:
        return None
    xs, ys = [p["speed"] for p in points], [p["voltage"] for p in points]
    xbar, ybar = stats.mean(xs), stats.mean(ys)
    denominator = sum((x - xbar) ** 2 for x in xs)
    if denominator <= 1e-12:
        return None
    kv = sum((x-xbar)*(y-ybar) for x, y in zip(xs, ys)) / denominator
    ks = ybar - kv*xbar
    residuals = [y - (ks + kv*x) for x, y in zip(xs, ys)]
    total = sum((y-ybar)**2 for y in ys)
    r2 = 1-sum(r*r for r in residuals)/total if total > 1e-12 else None
    return {"ks": ks, "kv": kv, "r2": r2, "residuals": residuals,
            "rmse": math.sqrt(stats.mean([r*r for r in residuals])),
            "reliable": ks >= 0 and kv > 0 and r2 is not None and r2 >= .9}


def crossing(rows: list[dict], threshold: float) -> float | None:
    for a, b in zip(rows, rows[1:]):
        va, vb = abs(a["velocity"]), abs(b["velocity"])
        if va >= threshold >= vb and va > vb:
            return a["time"] + (b["time"]-a["time"])*(va-threshold)/(va-vb)
    return None


def friction(samples: list[dict], settings: dict) -> dict[str, Any]:
    calibration = float(settings.get("ticksPerRevolution", 0))
    scale = 60/calibration if calibration > 0 else 1
    speed_unit = "RPM" if calibration > 0 else "tick/s"
    groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in samples:
        groups[(row["direction"], row["repetition"])].append(row)
    repetitions, points, coasts, warnings = [], [], [], []
    for (direction, repetition), rows in groups.items():
        entry: dict[str, Any] = {"direction": direction, "repetition": repetition,
            "breakawayVoltage": None, "breakawayCurrent": None, "coastTime": None, "deceleration": None}
        onset = next((r for r in rows if r["movement_onset"]), None)
        if onset:
            entry["breakawayVoltage"] = abs(onset["estimated_voltage"])
            current_window = [r["current"] for r in rows if abs(r["time"]-onset["time"]) <= .075]
            entry["breakawayCurrent"] = stats.mean(current_window)
        else:
            warnings.append(f"Direction {direction}, repetition {repetition}: movement onset unavailable.")
        for point in range(1, 5):
            window = [r for r in rows if r["phase"] == "steady" and r["point"] == point and r["steady_window"]]
            if stable(window):
                points.append({"direction": direction, "repetition": repetition, "point": point,
                    "speed": stats.mean(abs(r["velocity"]) for r in window)*scale,
                    "speedStd": stats.pstdev(abs(r["velocity"]) for r in window)*scale,
                    "voltage": stats.mean(abs(r["estimated_voltage"]) for r in window),
                    "current": stats.mean(r["current"] for r in window),
                    "currentStd": stats.pstdev(r["current"] for r in window)})
            else:
                warnings.append(f"Direction {direction}, repetition {repetition}, point {point}: no valid steady window.")
        coast = [r for r in rows if r["phase"] == "coast"]
        if len(coast) > 1 and abs(coast[0]["velocity"]) > 5:
            initial = abs(coast[0]["velocity"])
            t80, t20 = crossing(coast, initial*.8), crossing(coast, initial*.2)
            entry["coastInitialSpeed"] = initial*scale
            if t80 is not None and t20 is not None and t20 > t80:
                entry["coastTime"] = t20-t80
                entry["deceleration"] = .6*initial*scale/(t20-t80)
            else:
                warnings.append(f"Direction {direction}, repetition {repetition}: coast-down did not cover 80% to 20% speed.")
            coasts.append({"direction": direction, "repetition": repetition,
                           "points": [[r["time"]-coast[0]["time"], abs(r["velocity"])*scale] for r in coast]})
        repetitions.append(entry)
    fits = []
    for direction in sorted({r["direction"] for r in samples}, reverse=True):
        selected = [p for p in points if p["direction"] == direction]
        result = fit(selected)
        fits.append({"direction": direction, "fit": result})
        if result is None:
            warnings.append(f"Direction {direction}: running fit needs at least three distinct steady operating points.")
        elif not result["reliable"]:
            warnings.append(f"Direction {direction}: running fit is poorly supported; inspect its residuals.")
    summary = {name: spread([r[name] for r in repetitions if r[name] is not None])
               for name in ("breakawayVoltage", "breakawayCurrent", "coastTime", "deceleration")}
    return {"analyzerId": "friction.v1", "version": 1, "speedUnit": speed_unit,
            "kvUnit": f"V/({speed_unit})", "summary": summary, "repetitions": repetitions,
            "operatingPoints": points, "fits": fits, "coasts": coasts, "warnings": warnings,
            "timeline": [{"time": s["time"], "speed": s["velocity"]*scale,
                "voltage": s["estimated_voltage"], "current": s["current"], "phase": s["phase"]} for s in samples],
            "notes": ["Voltage is estimated from battery voltage × commanded duty cycle.",
                      "Breakaway voltage and fitted running kS are different measurements.",
                      "kV includes back-EMF and speed-dependent losses. Coast-down also depends on inertia.",
                      "Current is the motor-controller reading, not calibrated friction torque. No health verdict is assigned."]}


ANALYZERS = {"motor.friction.v1": friction}
