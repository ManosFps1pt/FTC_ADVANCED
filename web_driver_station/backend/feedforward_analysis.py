"""Breakaway-corrected, origin-constrained feedforward regression, matching the Java engine."""
from __future__ import annotations

import math
import statistics as stats


def input_values(settings: dict) -> dict:
    return {v["id"]: next((v[k] for k in ("float64Value", "int64Value", "enumValue", "booleanValue", "stringValue") if k in v), None)
            for v in settings.get("inputs", [])}


def feedforward(samples: list[dict], settings: dict) -> dict:
    inputs = input_values(settings)
    ticks = float(settings["ticksPerRevolution"])
    if not math.isfinite(ticks) or ticks <= 0: raise ValueError("Feedforward requires positive encoder calibration")
    scale = 2*math.pi/ticks
    steps = int(inputs.get("steps", 20))
    window = float(inputs.get("sample_seconds", 1))
    period = float(inputs.get("sample_period_seconds", .02))
    tolerance = float(inputs.get("stability_tolerance", .05))
    minimum = float(inputs.get("min_velocity_ticks_per_second", 5))
    reference = float(inputs.get("reference_voltage", 12))
    onset = next((s for s in samples if s["movement_onset"]), None)
    ks = abs(onset["estimated_voltage"]) if onset else None
    points, warnings = [], []
    for point in range(1, steps+1):
        rows = [s for s in samples if s["phase"] == "sample" and s["point"] == point]
        if not rows: continue
        velocities = [s["velocity"]*s["direction"] for s in rows]
        mean, std = stats.mean(velocities), stats.pstdev(velocities)
        voltage = stats.mean(abs(s["estimated_voltage"]) for s in rows)
        drift = abs(velocities[-1]-velocities[0])
        exclusion = ""
        if len(rows) < 3 or rows[-1]["time"]-rows[0]["time"] < window-1.5*period:
            exclusion = "Incomplete sampling window"
        elif mean < minimum:
            exclusion = "Stationary or wrong direction"
        elif std > mean*tolerance or drift > mean*tolerance:
            exclusion = "Unstable velocity"
        points.append(dict(index=point, count=len(rows), speed=mean*scale, speedStd=std*scale,
                           drift=drift*scale, voltage=voltage, current=stats.mean(s["current"] for s in rows),
                           included=not exclusion, exclusion=exclusion))
    valid = [p for p in points if p["included"]]
    fit = None
    if ks is not None and len({math.floor(p["voltage"]*1000+.5) for p in valid}) >= 3:
        xx = sum(p["speed"]**2 for p in valid)
        if xx > 1e-12:
            kv = sum(p["speed"]*(p["voltage"]-ks) for p in valid)/xx
            residuals = [p["voltage"]-ks-kv*p["speed"] for p in valid]
            sse = sum(r*r for r in residuals)
            total = sum((p["voltage"]-stats.mean(q["voltage"] for q in valid))**2 for p in valid)
            r2 = 1-sse/total if total > 1e-12 else None
            fit = dict(ks=ks, kv=kv, r2=r2, rmse=math.sqrt(sse/len(valid)), residuals=residuals,
                       reliable=kv > 0 and r2 is not None and r2 >= .9)
    if ks is None: warnings.append("Sustained directional movement onset was not observed; kS is unavailable.")
    if fit is None: warnings.append("kV requires kS and at least three distinct valid operating points.")
    elif not fit["reliable"]: warnings.append("Inspect fit quality before using these gains.")
    for p in points:
        if not p["included"]: warnings.append(f"Point {p['index']}: {p['exclusion']}.")
    gains = {}
    if fit:
        for key, unit, factor, method in (("radiansPerSecond", "rad/s", 1, "getRadiansPerSecond"),
                                         ("rpm", "RPM", 60/(2*math.pi), "getRotationsPerMinute"),
                                         ("degreesPerSecond", "deg/s", 180/math.pi, "getDegreesPerSecond")):
            native_ks, native_kv = ks/reference, fit["kv"]/reference/factor
            gains[key] = dict(unit=unit, ks=native_ks, kv=native_kv,
                code=("import dev.nextftc.units.Units;\n\n"
                      f"// Calibrated shaft: {ticks:.12g} encoder counts/revolution.\n"
                      f"// Normalized-power gains at {reference:.12g} V; setpoints must use {unit}.\n"
                      f"motor.setAnglePerCount(Units.getRadians({scale:.12g}));\n"
                      f"motor.getVelocityConstants().withS({native_ks:.12g}).withV({native_kv:.12g});\n"
                      f"motor.setVelocitySetpoint(Units.{method}(targetVelocity));"))
    direction = samples[0]["direction"] if samples else (-1 if inputs.get("direction") == "reverse" else 1)
    return dict(analyzerId="feedforward.v1", version=1, direction=direction, speedUnit="rad/s", kvUnit="V/(rad/s)",
                ks=ks, fit=fit, referenceVoltage=reference, nextMotorGains=gains, points=points, warnings=warnings,
                timeline=[dict(time=s["time"], speed=s["velocity"]*scale, voltage=s["estimated_voltage"], phase=s["phase"]) for s in samples],
                notes=["kS is the measured breakaway estimate, dependent on encoder resolution and ramp settings.",
                       "Voltage is estimated from the selected hub supply voltage × applied duty cycle.",
                       "Each accepted operating point has equal weight; fit: V − kS = kV × angular velocity.",
                       "NextMotor velocity feedforward uses normalized power and the numeric unit of its setpoint. These exported gains reference the displayed voltage; battery compensation is not automatic."])
