"""Run-scoped raw recording and durable, immutable analysis bundles."""
from __future__ import annotations

import json
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from google.protobuf.json_format import MessageToDict

from .debugger_analysis import ANALYZERS
from .ftclog import FtcLogError, RawFtcLogRecorder, iter_records
from .protocol import robot_data_pb2 as wire

MAX_SAMPLES = 30_000


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, allow_nan=False, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


class DebuggerResults:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.recorder = RawFtcLogRecorder(self.root)
        self.receiving: dict[str, dict] = {}
        self.pending: dict[str, dict] = {}

    def path(self, run_id: str) -> Path:
        run_id = str(uuid.UUID(run_id))
        path = (self.root / run_id).resolve()
        if path.parent != self.root:
            raise ValueError("Invalid result path")
        return path

    def list(self) -> list[dict]:
        result = dict(self.pending)
        if self.root.is_dir():
            for path in self.root.iterdir():
                try:
                    manifest = self.manifest(path.name)
                    if manifest.get("kind") == "debugger":
                        if manifest.get("state") != "ready" and path.name not in self.pending:
                            manifest.update(state="incomplete", message="Previous acquisition or transfer did not finish; retained robot data may reconnect.")
                        result[path.name] = {**manifest, **self.pending.get(path.name, {})}
                except (ValueError, OSError, json.JSONDecodeError):
                    continue
        return sorted(result.values(), key=lambda r: r.get("createdAt", ""), reverse=True)

    def manifest(self, run_id: str) -> dict:
        return json.loads((self.path(run_id)/"manifest.json").read_text(encoding="utf-8"))

    def report(self, run_id: str) -> dict:
        manifest = self.manifest(run_id)
        if manifest.get("kind") != "debugger": raise ValueError("Not a debugger result")
        report_path = self.path(run_id)/"analysis.json"
        return {"manifest": manifest, "analysis": json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None}

    def expect(self, run_id: str, metadata: dict) -> None:
        path = self.path(run_id)
        self.pending[run_id] = {"runId": run_id, "kind": "debugger", "state": "awaiting_acceptance",
                               "createdAt": datetime.now(timezone.utc).isoformat(), **metadata}
        path.mkdir(parents=True, exist_ok=True)
        atomic_json(path/"manifest.json", self.pending[run_id])

    def disconnected(self) -> None:
        for record in self.pending.values():
            if record["state"] not in ("ready", "rejected", "discarded"):
                record.update(state="incomplete", message="Connection lost; awaiting retained dataset or retry. If the OpMode stopped, untransferred samples are unavailable.")
                atomic_json(self.path(record["runId"])/"manifest.json", record)

    def forget(self, run_id: str) -> None:
        # Keep only a receipt so a lost ACK cannot resurrect a discarded/upload-only run.
        manifest = self.manifest(run_id)
        receipts = self.root/".debugger-receipts"
        receipts.mkdir(parents=True, exist_ok=True)
        atomic_json(receipts/f"{uuid.UUID(run_id)}.json", {"sampleCount": manifest["sampleCount"]})
        self.recorder.delete_session(run_id)
        self.pending.pop(run_id, None)

    def ingest(self, envelope: wire.Envelope, encoded: bytes, received_ns: int) -> wire.DebugRunAck | None:
        body = envelope.WhichOneof("body")
        if body not in ("debug_run_header", "debug_run_chunk", "debug_run_end"):
            return None
        value = getattr(envelope, body)
        run_id = str(uuid.UUID(value.run_id))
        path = self.path(run_id)
        receipt = self.root/".debugger-receipts"/f"{run_id}.json"
        if receipt.is_file():
            count = json.loads(receipt.read_text(encoding="utf-8"))["sampleCount"]
            return wire.DebugRunAck(run_id=run_id, total_samples=count) if body == "debug_run_end" and value.total_samples == count else None
        if (path/"manifest.json").is_file():
            existing = self.manifest(run_id)
            if existing.get("kind") != "debugger": raise ValueError("Run ID belongs to another recording")
            if existing.get("state") == "ready":
                if body == "debug_run_end" and existing["sampleCount"] == value.total_samples:
                    return wire.DebugRunAck(run_id=run_id, total_samples=value.total_samples)
                return None
        if body == "debug_run_header":
            if value.benchmark.version != 1 or value.benchmark.id not in ANALYZERS:
                raise ValueError("Unsupported benchmark or procedure version")
            channels = {c.channel_id: c.key for c in value.benchmark.channels}
            required = {"battery_voltage", "duty", "current", "position", "velocity", "estimated_voltage", "movement_onset", "steady_window"}
            if len(channels) != 8 or set(channels.values()) != required:
                raise ValueError("Unexpected friction dataset channels")
            if not math.isfinite(value.ticks_per_revolution) or value.ticks_per_revolution < 0:
                raise ValueError("Invalid encoder calibration")
            previous = self.receiving.get(run_id)
            if previous:
                if previous["header"] != value: raise ValueError("Run metadata changed during retry")
                return None
            # After a laptop restart, reconstruct any accepted chunks from the raw log.
            state = {"header": value, "chunks": {}, "channels": channels}
            raw_dir = path/"raw"
            for log in sorted(raw_dir.glob("stream-*")) if raw_dir.is_dir() else []:
                try:
                    for record in iter_records(log):
                        old = wire.Envelope.FromString(record.protobuf_frame)
                        if old.HasField("debug_run_header") and old.debug_run_header != value:
                            raise ValueError("Recovered metadata differs from retry")
                        if old.HasField("debug_run_chunk"):
                            chunk = old.debug_run_chunk
                            if chunk.run_id != run_id: raise ValueError("Recovered chunk belongs to a different run")
                            previous_chunk = state["chunks"].get(chunk.chunk_index)
                            if previous_chunk is not None and previous_chunk != chunk:
                                raise ValueError("Conflicting recovered chunk")
                            state["chunks"][chunk.chunk_index] = chunk
                except (FtcLogError, OSError):
                    pass  # A partial last record is recovered by robot retransmission.
            self.pending.setdefault(run_id, {"createdAt": datetime.now(timezone.utc).isoformat()})
            self.pending[run_id].update(runId=run_id, kind="debugger", state="receiving", mechanismLabel=value.mechanism_label)
            self.recorder.append(run_id, encoded, received_ns)
            self.receiving[run_id] = state
            atomic_json(path/"manifest.json", {**MessageToDict(value), **self.pending[run_id]})
            return None
        elif run_id not in self.receiving:
            raise ValueError("Dataset header is required before chunks")
        state = self.receiving[run_id]
        if body == "debug_run_chunk":
            if value.chunk_index >= MAX_SAMPLES or len(value.samples)>128:
                raise ValueError("Oversized dataset chunk")
            previous = state["chunks"].get(value.chunk_index)
            if previous is not None:
                if previous != value: raise ValueError("Conflicting duplicate dataset chunk")
                return None
            if sum(len(c.samples) for c in state["chunks"].values())+len(value.samples)>MAX_SAMPLES:
                raise ValueError("Dataset sample limit exceeded")
        self.recorder.append(run_id, encoded, received_ns)
        if body == "debug_run_chunk":
            state["chunks"][value.chunk_index] = value
        if body != "debug_run_end": return None
        if value.outcome not in ("completed", "aborted", "inconclusive"):
            raise ValueError("Invalid benchmark outcome")
        if value.total_samples > MAX_SAMPLES or value.total_chunks > MAX_SAMPLES or set(state["chunks"]) != set(range(value.total_chunks)):
            raise ValueError("Dataset chunks are incomplete")
        rows = [sample for i in range(value.total_chunks) for sample in state["chunks"][i].samples]
        if len(rows) != value.total_samples: raise ValueError("Dataset sample count mismatch")
        samples = self.decode_samples(state["header"], rows)
        settings = MessageToDict(state["header"], preserving_proto_field_name=False)
        analysis = ANALYZERS[state["header"].benchmark.id](samples, settings)
        manifest = {**settings, "kind": "debugger", "formatVersion": 1, "state": "ready",
                    "createdAt": self.pending[run_id]["createdAt"], "outcome": value.outcome,
                    "message": value.message, "sampleCount": len(samples), "analysisVersion": 1,
                    "acquisition": {"targetHz": 50, "clock": "robot monotonic nanoseconds", "voltage": "battery_voltage * commanded_duty_cycle (estimated)", "sampleLimit": MAX_SAMPLES}}
        self.recorder.close_session(run_id)
        # Remove no raw evidence: old interrupted logs remain readable and downloadable.
        for partial in (path/"raw").glob("*.ftclog.partial"):
            partial.rename(partial.with_suffix(".interrupted"))
        atomic_json(path/"analysis.json", analysis)
        atomic_json(path/"manifest.json", manifest)
        self.pending.pop(run_id, None)
        self.receiving.pop(run_id, None)
        return wire.DebugRunAck(run_id=run_id, total_samples=len(rows))

    @staticmethod
    def decode_samples(header: wire.DebugRunHeader, rows: list) -> list[dict]:
        channels = {c.channel_id: c.key for c in header.benchmark.channels}
        samples, last_time = [], -1
        for index, row in enumerate(rows):
            if row.phase not in ("rest", "ramp", "steady", "coast") or row.direction not in (-1, 1) or not 1 <= row.repetition <= 5:
                raise ValueError("Invalid acquisition phase or repetition")
            if row.sequence != index or row.robot_time_ns <= last_time or row.robot_time_ns < header.started_robot_ns:
                raise ValueError("Invalid acquisition timestamp or sample sequence")
            last_time = row.robot_time_ns
            sample = {"time": (row.robot_time_ns-header.started_robot_ns)/1e9,
                      "phase": row.phase, "repetition": row.repetition, "direction": row.direction, "point": row.operating_point}
            seen = set()
            for val in row.values:
                if val.channel_id not in channels or val.channel_id in seen: raise ValueError("Invalid sample channels")
                seen.add(val.channel_id)
                kind = val.WhichOneof("value")
                key = channels[val.channel_id]
                expected = "int64_value" if key == "position" else "boolean_value" if key in ("movement_onset", "steady_window") else "float64_value"
                if kind != expected: raise ValueError("Invalid sample value type")
                data = getattr(val, kind)
                if isinstance(data, float) and not math.isfinite(data): raise ValueError("Non-finite measurement")
                sample[channels[val.channel_id]] = data
            if seen != set(channels): raise ValueError("Incomplete sample")
            samples.append(sample)
        return samples
