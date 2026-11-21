"""Measure a Main TeleOp run from the original FTCLOG telemetry."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np
from google.protobuf.message import DecodeError

from web_driver_station.backend.ftclog import FtcLogError, iter_records
from web_driver_station.backend.protocol import robot_data_pb2 as wire
from web_driver_station.backend.protocol_codec import WireProtocolError, decode


WHEELS = tuple(f"drivetrain.{wheel}" for wheel in ("frontLeft", "frontRight", "backLeft", "backRight"))
MOTORS = ("shooter", "intake", *WHEELS)


@dataclass(frozen=True)
class Settings:
    max_gap_s: float = 0.20
    speed_band_rpm: float = 100.0
    hold_s: float = 0.10
    baseline_s: float = 0.45
    baseline_spread_rpm: float = 150.0
    drop_rpm: float = 150.0
    recovery_window_s: float = 2.0
    intake_load_a: float = 6.0
    intake_slow_ticks_s: float = 800.0
    intake_startup_s: float = 0.25
    intake_min_episode_s: float = 0.12
    low_voltage_v: float = 10.5
    gate_open_duty: float = 0.279
    gate_closed_duty: float = 0.500


@dataclass
class Run:
    t: np.ndarray
    seq: np.ndarray
    columns: dict[str, np.ndarray]
    info: dict[str, Any]

    def get(self, key):
        if key in self.columns:
            return self.columns[key]
        return np.full_like(self.t, np.nan)


def load_run(source: Path) -> Run:
    """Decode raw files with their original connection and schema revision."""
    raw = source / "raw" if (source / "raw").is_dir() else source
    paths = sorted(raw.glob("stream-*.ftclog*")) if raw.is_dir() else [raw]
    paths = [path for path in paths if path.name.endswith((".ftclog", ".ftclog.partial"))]
    if not paths:
        raise ValueError(f"No FTCLOG streams in {source}")
    metadata_path = paths[0].parent.parent / "recording-metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    schema_maps = {}
    units = {}
    samples = {}
    counts = Counter()
    versions = Counter()
    session_ids = set()
    starts, endings, modes = [], [], set()
    invalid_frames = 0
    max_heartbeat_drops = 0
    for path in paths:
        for record in iter_records(path):
            try:
                envelope = wire.Envelope.FromString(record.protobuf_frame)
                versions[envelope.protocol_version] += 1
                body = envelope.WhichOneof("body")
                connection = (envelope.session_id, envelope.connection_id)
                if body == "sample_batch":
                    revisions = {s.schema_revision for s in envelope.sample_batch.snapshots}
                    if not revisions:
                        raise WireProtocolError("sample batch must not be empty")
                    payloads = [
                        payload for revision in revisions
                        for payload in decode_for_revision(envelope, revision, schema_maps, connection)
                    ]
                else:
                    channels = {}
                    payloads = decode(envelope, channels)
                    if body == "schema":
                        schema_maps[(*connection, envelope.schema.revision)] = channels
            except (DecodeError, ValueError, WireProtocolError):
                invalid_frames += 1
                continue
            for payload in payloads:
                kind, data = payload["type"], payload["data"]
                ns = int(payload["robotTimeNs"])
                counts[kind] += 1
                session_ids.add(payload["sessionId"])
                if kind == "sample":
                    sequence = int(data["sampleSequence"])
                    sample = (ns, data["values"])
                    if sequence in samples and samples[sequence] != sample:
                        raise ValueError(f"Conflicting samples at sequence {sequence}")
                    samples[sequence] = sample
                elif kind == "catalog":
                    for signal in data["signals"]:
                        units.setdefault(signal["id"], set()).add(signal["unit"])
                elif kind == "hello":
                    modes.add(data["opModeName"])
                elif kind == "event" and data["name"] == "opmode.started":
                    starts.append(ns)
                elif kind == "session_end":
                    endings.append(data)
                elif kind == "heartbeat":
                    max_heartbeat_drops = max(max_heartbeat_drops, int(data["droppedSamples"]))
    if len(session_ids) != 1 or len(modes) != 1 or len(samples) < 2:
        raise ValueError("Expected one OpMode session with a hello message and at least two decoded samples")
    seq = np.array(sorted(samples), dtype=np.int64)
    times, rows = zip(*(samples[i] for i in seq))
    t = (np.array(times, dtype=np.int64) - times[0]) * 1e-9
    if np.any(np.diff(t) < 0):
        raise ValueError("Robot acquisition clock goes backward in sample sequence order")
    columns = {}
    for key in units:
        try:
            columns[key] = np.array([row.get(key) for row in rows], dtype=float)
        except (TypeError, ValueError):
            continue
    info = {
        "session_id": session_ids.pop(), "opmode": modes.pop(), "metadata": metadata,
        "files": [p.name for p in paths], "interrupted_file": any(p.suffix == ".partial" for p in paths),
        "protocol_frames": dict(versions), "records": dict(counts), "invalid_frames": invalid_frames,
        "duplicate_samples": counts["sample"] - len(samples), "max_heartbeat_drops": max_heartbeat_drops,
        "first_sample_after_start_s": (times[0] - starts[0]) * 1e-9 if starts else None,
        "session_end": endings,
        "units": {key: sorted(value) for key, value in units.items()},
    }
    return Run(t, seq, columns, info)


def decode_for_revision(envelope, revision, schema_maps, connection):
    snapshots = envelope.sample_batch.snapshots
    channels = schema_maps.get((*connection, revision), {})
    if all(sample.schema_revision == revision for sample in snapshots):
        return decode(envelope, channels)
    subset = wire.Envelope()
    subset.CopyFrom(envelope)
    subset.sample_batch.ClearField("snapshots")
    subset.sample_batch.snapshots.extend(s for s in snapshots if s.schema_revision == revision)
    return decode(subset, channels)


def intervals(mask, links):
    """Yield half-open ranges of true samples joined by valid time intervals."""
    joined = mask[:-1] & mask[1:] & links
    starts = np.flatnonzero(mask & ~np.r_[False, joined])
    ends = np.flatnonzero(mask & ~np.r_[joined, False]) + 1
    return zip(starts, ends)


def spans(t, mask, links):
    for i, j in intervals(mask, links):
        yield i, j, t[j - 1] - t[i]


def first_held(t, mask, links, seconds):
    return next((i for i, _, duration in spans(t, mask, links) if duration >= seconds), None)


def finite(value):
    return float(value) if np.isfinite(value) else None


def ratio(numerator, denominator):
    return float(numerator / denominator) if denominator else None


def distribution(values):
    valid = np.asarray(values, dtype=float)
    valid = valid[np.isfinite(valid)]
    result = {"n": len(valid)}
    for label, quantile in (("min", 0), ("p05", .05), ("median", .5), ("p95", .95), ("p99", .99), ("max", 1)):
        result[label] = float(np.quantile(valid, quantile)) if len(valid) else None
    return result


def weighted_fraction(mask, population, weights):
    return ratio(weights[mask & population].sum(), weights[population].sum())


def current_stats(t, values, links, weights, active):
    measurable = np.isfinite(values)
    valid = links & measurable[:-1] & measurable[1:]
    mid = (values[:-1][valid] + values[1:][valid]) / 2
    amp_seconds = float(np.dot(mid, np.diff(t)[valid]))
    coverage_s = float(np.diff(t)[valid].sum())
    peak_index = np.nanargmax(values) if measurable.any() else None
    return {
        "covered_s": coverage_s, "motor_port_amp_seconds": amp_seconds if coverage_s else None,
        "mean_a": ratio(amp_seconds, coverage_s), "rms_a": (
            float(np.sqrt(np.dot((values[:-1][valid]**2 + values[1:][valid]**2) / 2, np.diff(t)[valid]) / coverage_s))
            if coverage_s else None
        ),
        "sampled_a": distribution(values), "peak_t_s": float(t[peak_index]) if peak_index is not None else None,
        "active_fraction": weighted_fraction(active, measurable, weights),
        "active_sampled_a": distribution(values[active]),
    }


def spinups(t, rpm, command, target, on, links, settings):
    unchanged = links & (target[1:] == target[:-1])
    rows = []
    for i, j in intervals(on & np.isfinite(target) & np.isfinite(rpm), unchanged):
        indices = slice(i, j)
        tt, rr, cc = t[indices], rpm[indices], command[indices]
        ramp_done = np.abs(cc - target[i]) <= 1
        reference = first_held(tt, ramp_done, links[i:j-1], 0)
        ready = first_held(tt, ramp_done & (np.abs(rr - target[i]) <= settings.speed_band_rpm),
                           links[i:j-1], settings.hold_s)
        hits_95 = first_held(tt, rr >= target[i] * .95, links[i:j-1], 0)
        kind = "left_censored"
        if i and links[i-1]:
            if not on[i-1] and command[i-1] == 0:
                kind = "cold_start" if abs(rpm[i]) <= 150 else "rolling_restart"
            elif on[i-1] and target[i] != target[i-1]:
                kind = "target_up" if target[i] > target[i-1] else "target_down"
        rows.append({
            "t_s": float(t[i]), "kind": kind, "target_rpm": float(target[i]),
            "starting_rpm": float(rpm[i]), "observed_s": float(tt[-1] - tt[0]),
            "reference_at_target_s": float(tt[reference] - tt[0]) if reference is not None else None,
            "first_95pct_s": float(tt[hits_95] - tt[0]) if hits_95 is not None else None,
            "ready_s": float(tt[ready] - tt[0]) if ready is not None else None,
            "peak_rpm": float(np.max(rr)), "overshoot_rpm": float(max(0, np.max(rr) - target[i])),
        })
    return rows


def disturbances(t, rpm, stable, target, gate, power, current, voltage, links, settings):
    # Join only valid samples at the same settled command. The pre-event window
    # can use up to one sample of timestamp rounding beyond the requested lookback.
    rows = []
    for start, end in intervals(stable & np.isfinite(rpm), links & (target[1:] == target[:-1])):
        i = start + 1
        while i < end:
            left = max(start, np.searchsorted(t, t[i] - settings.baseline_s, side="right") - 1)
            before = rpm[left:i]
            if len(before) < 3 or t[i - 1] - t[left] < settings.baseline_s / 2:
                i += 1
                continue
            baseline = np.median(before)
            if (t[i] - t[left] < settings.baseline_s or np.ptp(before) > settings.baseline_spread_rpm
                    or baseline - rpm[i] < settings.drop_rpm):
                i += 1
                continue
            stop = min(end, np.searchsorted(t, t[i] + settings.recovery_window_s, side="right"))
            recovered = first_held(t[i:stop], np.abs(rpm[i:stop] - baseline) <= settings.speed_band_rpm,
                                  links[i:stop-1], settings.hold_s)
            recovered = i + recovered if recovered is not None else None
            window_end = recovered + 1 if recovered is not None else stop
            trough = i + int(np.argmin(rpm[i:window_end]))
            target_ready = first_held(t[trough:stop], np.abs(rpm[trough:stop] - target[i]) <= settings.speed_band_rpm,
                                      links[trough:stop-1], settings.hold_s)
            gate_state = "open" if gate[i] == 1 else ("closed" if gate[i] == 0 else "unknown")
            rows.append({
                "t_s": float(t[i]), "gate": gate_state, "target_rpm": float(target[i]),
                "baseline_rpm": float(baseline), "minimum_rpm": float(rpm[trough]),
                "drop_rpm": float(baseline - rpm[trough]),
                "drop_pct": ratio(100 * (baseline - rpm[trough]), baseline),
                "trough_t_s": float(t[trough]), "voltage_at_trough_v": finite(voltage[trough]),
                "current_at_trough_a": finite(current[trough]),
                "power_at_trough": finite(power[trough]),
                "pre_power_median": distribution(power[left:i])["median"],
                "min_voltage_v": distribution(voltage[i:window_end])["min"],
                "peak_current_a": distribution(current[i:window_end])["max"],
                "peak_power": distribution(power[i:window_end])["max"],
                "recovered_t_s": float(t[recovered]) if recovered is not None else None,
                "trough_to_recovery_s": float(t[recovered] - t[trough]) if recovered is not None else None,
                "onset_to_recovery_s": float(t[recovered] - t[i]) if recovered is not None else None,
                "trough_to_target_band_s": float(t[trough + target_ready] - t[trough]) if target_ready is not None else None,
                "observed_s": float(t[window_end - 1] - t[i]),
                "outcome": ("recovered" if recovered is not None else
                            "interrupted" if stop == end else "timed_out"),
            })
            i = window_end
    return rows


def analyze(run: Run, settings: Settings) -> tuple[dict, dict, dict]:
    t, get = run.t, run.get
    expected_units = {
        "shooter.targetRpm": "RPM", "shooter.commandedRpm": "RPM", "shooter.measuredRpm": "RPM",
        "robot.voltage": "V", "fos.dutyCycle": "normalized",
        **{f"{name}.currentAmps": "A" for name in MOTORS},
        **{f"{name}.appliedPower": "normalized" for name in MOTORS},
        **{f"{name}.velocityTicksPerSecond": "ticks/s" for name in MOTORS},
    }
    for key, unit in expected_units.items():
        if key in run.info["units"] and run.info["units"][key] != [unit]:
            raise ValueError(f"{key}: expected {unit}, found {run.info['units'][key]}")
    dt = np.diff(t)
    seq_steps = np.diff(run.seq)
    links = (dt > 0) & (dt <= settings.max_gap_s) & (seq_steps == 1)
    weights = np.r_[np.where(links, dt, 0), 0]
    voltage = get("robot.voltage")
    rpm, target, command = (get(f"shooter.{key}") for key in ("measuredRpm", "targetRpm", "commandedRpm"))
    power = {name: get(f"{name}.appliedPower") for name in MOTORS}
    amps = {name: get(f"{name}.currentAmps") for name in MOTORS}
    known_power = {name: np.isfinite(power[name]) for name in MOTORS}
    active = {name: np.abs(power[name]) >= .1 for name in MOTORS}
    on = command > 0
    stable = on & (np.abs(command - target) <= 1) & np.isfinite(rpm)
    saturated = np.abs(power["shooter"]) >= .995
    indicator = get("fos.dutyCycle")
    gate = np.full(t.shape, np.nan)
    gate[np.isclose(indicator, settings.gate_open_duty, atol=.005)] = 1
    gate[np.isclose(indicator, settings.gate_closed_duty, atol=.005)] = 0
    drive = np.any([active[name] for name in WHEELS], axis=0)
    drive_state_known = drive | np.all([known_power[name] for name in WHEELS], axis=0)
    total = np.sum(list(amps.values()), axis=0)
    drive_amps = np.sum([amps[name] for name in WHEELS], axis=0)
    motors = {name: current_stats(t, amps[name], links, weights, active[name]) for name in MOTORS}
    for name, motor in motors.items():
        velocity = get(f"{name}.velocityTicksPerSecond")
        commanded = np.abs(power[name]) >= .2
        measured = velocity[commanded & np.isfinite(velocity)]
        motor["powered_velocity_sample_count"] = len(measured)
        motor["powered_nonzero_velocity_fraction"] = ratio(np.count_nonzero(measured), len(measured))
        motor["powered_velocity_all_zero"] = bool(len(measured) >= 10 and not np.any(measured))
    total_stats = current_stats(t, total, links, weights, drive | on | active["intake"])
    drive_stats = current_stats(t, drive_amps, links, weights, drive)
    # Use common coverage for current breakdowns so a missing channel cannot inflate a share.
    shared_current = np.isfinite(total)
    shared_weights = links & shared_current[1:] & shared_current[:-1]
    for name, motor in motors.items():
        readings = amps[name][:-1][shared_weights] + amps[name][1:][shared_weights]
        share_num = np.dot(readings / 2, dt[shared_weights])
        motor["share_of_six_on_common_coverage"] = ratio(share_num, total_stats["motor_port_amp_seconds"])
    per_target = {}
    for value in np.unique(target[stable]):
        m = stable & (target == value)
        per_target[str(float(value))] = {
            "steady_command_s": float(weights[m].sum()), "rpm": distribution(rpm[m]),
            "error_rpm": distribution((rpm - target)[m]), "measured_in_band_fraction": weighted_fraction(
                np.abs(rpm - target) <= settings.speed_band_rpm, m, weights),
            "saturated_fraction": weighted_fraction(saturated, m & known_power["shooter"], weights),
            "voltage_v": distribution(voltage[m]),
        }
    dips = disturbances(t, rpm, stable, target, gate, power["shooter"], amps["shooter"], voltage, links, settings)
    starts = spinups(t, rpm, command, target, on, links, settings)
    gate_rows = [
        {"t_s": float(t[i]), "last_open_sample_s": float(t[j-1]), "observed_open_s": float(duration),
         "shooter_command_positive_s": float(weights[i:j][on[i:j]].sum()),
         "stable_shooter_s": float(weights[i:j][stable[i:j]].sum()),
         "detected_disturbances": sum(bool(t[i] <= row["t_s"] <= t[j-1]) for row in dips)}
        for i, j, duration in spans(t, gate == 1, links)
    ]
    forward = power["intake"] >= .5
    intake_speed = np.abs(get("intake.velocityTicksPerSecond"))
    past_startup = np.zeros(t.shape, dtype=bool)
    for i, j in intervals(forward, links):
        past_startup[i:j] = (t[i:j] - t[i]) >= settings.intake_startup_s
    loaded = past_startup & (amps["intake"] >= settings.intake_load_a) & (intake_speed <= settings.intake_slow_ticks_s)
    intake_rows = [
        {"t_s": float(t[i]), "last_flagged_sample_s": float(t[j-1]), "minimum_duration_s": float(duration),
         "peak_a": float(np.max(amps["intake"][i:j])), "min_abs_speed_ticks_s": float(np.min(intake_speed[i:j])),
         "min_voltage_v": distribution(voltage[i:j])["min"],
         "gate_open_at_onset": bool(gate[i] == 1) if np.isfinite(gate[i]) else None,
         "drive_at_onset": bool(drive[i]) if drive_state_known[i] else None}
        for i, j, duration in spans(t, loaded, links) if duration >= settings.intake_min_episode_s
    ]
    low_bouts = [
        {"t_s": float(t[i]), "last_low_sample_s": float(t[j-1]), "minimum_duration_s": float(duration),
         "min_v": float(np.min(voltage[i:j])), "peak_six_motor_a": distribution(total[i:j])["max"],
         "drive_at_min": bool(drive[i + np.argmin(voltage[i:j])]) if drive_state_known[i + np.argmin(voltage[i:j])] else None}
        for i, j, duration in spans(t, voltage < settings.low_voltage_v, links)
    ]
    modes = {}
    mode_known = drive_state_known & known_power["intake"] & np.isfinite(command)
    for state in range(8):
        flags = [bool(state & bit) for bit in (1, 2, 4)]
        m = mode_known & (drive == flags[0]) & (active["intake"] == flags[1]) & (on == flags[2])
        name = "+".join(label for label, flag in zip(("drive", "intake", "shooter"), flags) if flag) or "idle"
        modes[name] = {"observed_s": float(weights[m].sum()), "six_motor_a": distribution(total[m]),
                       "voltage_v": distribution(voltage[m])}
    groups = {}
    for state in ("open", "closed", "unknown"):
        rows = [row for row in dips if row["gate"] == state]
        gate_mask = ~np.isfinite(gate) if state == "unknown" else gate == (state == "open")
        groups[state] = {
            "stable_exposure_s": float(weights[stable & gate_mask].sum()), "n": len(rows),
            "drop_rpm": distribution([row["drop_rpm"] for row in rows]),
            "drop_pct": distribution([row["drop_pct"] for row in rows]),
            "trough_to_recovery_s": distribution([row["trough_to_recovery_s"] for row in rows]),
            "onset_to_recovery_s": distribution([row["onset_to_recovery_s"] for row in rows]),
            "trough_to_target_band_s": distribution([row["trough_to_target_band_s"] for row in rows]),
            "outcomes": dict(Counter(row["outcome"] for row in rows)),
        }
    def at_extreme(values, selector):
        if not np.isfinite(values).any():
            return None
        index = selector(values)
        return {"t_s": float(t[index]), "voltage_v": finite(voltage[index]),
                "motor_port_sum_a": finite(total[index]), "motor_a": {name: finite(series[index]) for name, series in amps.items()},
                "shooter_rpm": finite(rpm[index]), "shooter_target_rpm": finite(target[index])}

    summary = {
        "analysis_version": 1, "settings": asdict(settings), "source": run.info,
        "quality": {
            "recorded_span_s": float(t[-1]), "valid_interval_s": float(weights.sum()),
            "samples": len(t), "missing_between_sequences": int(np.sum(seq_steps - 1)),
            "unusable_intervals": int((~links).sum()), "sample_interval_s": distribution(dt),
            "mean_sample_hz": ratio(len(dt), t[-1]),
            "channel_coverage_fraction": {key: ratio(weights[np.isfinite(get(key))].sum(), weights.sum())
                                          for key in expected_units},
        },
        "electrical": {
            "six_motor_sum": total_stats, "drive_sum": drive_stats, "per_motor": motors, "by_activity": modes,
            "voltage_v": distribution(voltage), "below_voltage_review_level_s": float(weights[voltage < settings.low_voltage_v].sum()),
            "lowest_voltage": at_extreme(voltage, np.nanargmin), "highest_motor_sum": at_extreme(total, np.nanargmax),
        },
        "shooter": {
            "positive_reference_s": float(weights[on].sum()),
            "steady_command_s": float(weights[stable].sum()), "by_target": per_target,
            "observed_starts": sum(row["kind"] in ("cold_start", "rolling_restart") for row in starts),
            "steady_measured_in_band_fraction": weighted_fraction(
                np.abs(rpm - target) <= settings.speed_band_rpm, stable, weights),
            "gate_open_measured_in_band_fraction_while_commanded": weighted_fraction(
                stable & (np.abs(rpm - target) <= settings.speed_band_rpm),
                (gate == 1) & on & np.isfinite(rpm) & np.isfinite(target), weights),
            "compensation_voltage_valid_fraction_while_on": weighted_fraction(
                get("shooter.voltageValid") == 1, on & np.isfinite(get("shooter.voltageValid")), weights),
            "disturbances_by_gate": groups,
        },
        "gate": {"known_state_fraction": ratio(weights[np.isfinite(gate)].sum(), weights.sum()),
                 "observed_open_windows": len(gate_rows)},
        "intake": {
            "forward_s": float(weights[forward].sum()), "forward_current_a": distribution(amps["intake"][forward]),
            "forward_abs_speed_ticks_s": distribution(intake_speed[forward]), "sustained_load_episodes": len(intake_rows),
        },
    }
    tables = {"shooter_transitions": starts, "shooter_disturbances": dips, "gate_windows": gate_rows,
              "intake_load_episodes": intake_rows, "low_voltage_episodes": low_bouts}
    traces = {"t": t, "links": links, "rpm": rpm, "target": target, "power": power, "voltage": voltage, "amps": amps,
              "total": total, "drive_amps": drive_amps, "gate": gate, "intake_speed": intake_speed}
    return summary, tables, traces


def write_outputs(output, summary, tables):
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    empty_columns = {
        "shooter_transitions": "t_s kind target_rpm starting_rpm observed_s reference_at_target_s first_95pct_s ready_s peak_rpm overshoot_rpm",
        "shooter_disturbances": "t_s gate target_rpm baseline_rpm minimum_rpm drop_rpm drop_pct trough_t_s voltage_at_trough_v "
        "current_at_trough_a power_at_trough pre_power_median min_voltage_v peak_current_a peak_power recovered_t_s "
        "trough_to_recovery_s onset_to_recovery_s trough_to_target_band_s observed_s outcome",
        "gate_windows": "t_s last_open_sample_s observed_open_s shooter_command_positive_s stable_shooter_s detected_disturbances",
        "intake_load_episodes": "t_s last_flagged_sample_s minimum_duration_s peak_a min_abs_speed_ticks_s min_voltage_v gate_open_at_onset drive_at_onset",
        "low_voltage_episodes": "t_s last_low_sample_s minimum_duration_s min_v peak_six_motor_a drive_at_min",
    }
    for name, rows in tables.items():
        path = output / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=empty_columns[name].split())
            writer.writeheader()
            writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Session folder, raw folder, or one .ftclog")
    parser.add_argument("--out", required=True, type=Path, help="Directory for summary JSON and event CSVs")
    parser.add_argument("--settings", type=Path, help="JSON object overriding Settings defaults")
    parser.add_argument("--plots", action="store_true", help="Write diagnostic PNGs using Matplotlib")
    args = parser.parse_args()
    try:
        overrides = json.loads(args.settings.read_text(encoding="utf-8")) if args.settings else {}
        settings = Settings(**overrides)
        for name, value in asdict(settings).items():
            if type(value) not in (int, float) or not np.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
            if name.endswith("_duty"):
                if not 0 <= value <= 1:
                    raise ValueError(f"{name} must be between 0 and 1")
            elif value <= 0:
                raise ValueError(f"{name} must be positive")
        if (settings.recovery_window_s <= settings.hold_s or settings.drop_rpm <= settings.speed_band_rpm
                or abs(settings.gate_open_duty - settings.gate_closed_duty) <= .01):
            raise ValueError("Choose distinct gate duties, a drop larger than the recovery band and a window longer than the hold")
        summary, tables, traces = analyze(load_run(args.source), settings)
        write_outputs(args.out, summary, tables)
        if args.plots:
            from recording_viewer.mainteleop_plots import write_plots

            write_plots(args.out, traces, tables, settings)
    except (FtcLogError, ImportError, OSError, TypeError, ValueError) as error:
        parser.exit(2, f"Main TeleOp analysis: {error}\n")
    quality = summary["quality"]
    print(f'{summary["source"]["opmode"]} | {quality["samples"]:,} samples | {quality["recorded_span_s"]:.2f} s')
    print(f"Report written to {args.out.resolve()}")


if __name__ == "__main__":
    main()
