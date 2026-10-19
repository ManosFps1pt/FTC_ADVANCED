from __future__ import annotations

import csv
import math
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI, HTTPException
from google.protobuf.json_format import MessageToDict
from .debugger_api import RunRequest, install_debugger_controls
from .debugger_inputs import encode_inputs
from .debugger_results import DebuggerResults
from .feedforward_analysis import feedforward
from .protocol import robot_data_pb2 as wire


def fixture():
    rows = []
    with (Path(__file__).parent/"fixtures/feedforward.csv").open(newline="") as stream:
        for r in csv.DictReader(stream):
            rows.append(dict(time=int(r["time_ns"])/1e9, phase=r["phase"], point=int(r["point"]), direction=int(r["direction"]),
                repetition=1, battery_voltage=float(r["battery"]), duty=float(r["duty"]), current=float(r["current"]),
                position=int(r["position"]), velocity=float(r["velocity"]), movement_onset=r["onset"] == "true",
                steady_window=r["phase"] == "sample", estimated_voltage=float(r["battery"])*float(r["duty"])))
    return rows


def definition():
    return {"id": "motor.feedforward.v1", "version": 1, "inputs": [
        {"id": "hub", "valueType": "ENUM", "enumOptions": [{"id": "control_hub"}, {"id": "expansion_hub"}], "defaultValue": {"enumValue": "control_hub"}},
        {"id": "port", "valueType": "INT64", "required": True, "minFloat64": 0, "maxFloat64": 3},
        {"id": "ticks_per_revolution", "valueType": "FLOAT64", "required": True, "minFloat64": .000001, "maxFloat64": 1e9},
        {"id": "current_limit_amps", "valueType": "FLOAT64", "required": True, "minFloat64": .1, "maxFloat64": 100},
        {"id": "steps", "valueType": "INT64", "minFloat64": 3, "maxFloat64": 100, "defaultValue": {"int64Value": "20"}},
    ]}


class FeedforwardTests(unittest.TestCase):
    def test_shared_fixture_and_native_units(self):
        report = feedforward(fixture(), {"ticksPerRevolution": 600, "inputs": [{"id": "steps", "int64Value": "5"}]})
        self.assertAlmostEqual(.4, report["ks"])
        self.assertAlmostEqual(.006*600/(2*math.pi), report["fit"]["kv"])
        self.assertAlmostEqual(1, report["fit"]["r2"])
        self.assertFalse(report["points"][-1]["included"])
        self.assertAlmostEqual(.006*10/12, report["nextMotorGains"]["rpm"]["kv"])
        self.assertIn("Units.getRadiansPerSecond", report["nextMotorGains"]["radiansPerSecond"]["code"])
        self.assertIsNone(feedforward(fixture()[:20], {"ticksPerRevolution": 600})["fit"])
        self.assertIsNone(feedforward([], {"ticksPerRevolution": 600})["ks"])

    def test_typed_defaults_ranges_and_choices(self):
        valid = dict(port=0, ticks_per_revolution=600, current_limit_amps=7)
        encoded = encode_inputs(definition(), valid)
        self.assertEqual(20, next(a.int64_value for a in encoded if a.id == "steps"))
        for changed in ({"port": 4}, {"port": True}, {"port": 1.5}, {"hub": "absent"},
                        {"current_limit_amps": float("nan")}, {"unknown": 1}):
            with self.assertRaises(ValueError): encode_inputs(definition(), {**valid, **changed})
        with self.assertRaises(ValueError): encode_inputs(definition(), {})

    def test_stable_points_below_breakaway_are_not_silently_removed(self):
        samples = fixture()
        samples[0]["estimated_voltage"] = 2
        report = feedforward(samples, {"ticksPerRevolution": 600, "inputs": [{"id": "steps", "int64Value": "4"}]})
        self.assertTrue(all(p["included"] for p in report["points"]))
        self.assertAlmostEqual(.2/math.pi, report["fit"]["kv"])
        self.assertFalse(report["fit"]["reliable"])

    def test_reverse_preserves_positive_gains_and_records_tested_direction(self):
        samples = fixture()
        for row in samples:
            row["direction"] = -1
            for key in ("velocity", "estimated_voltage", "duty", "position"):
                row[key] = -row[key]
        report = feedforward(samples, {"ticksPerRevolution": 600, "inputs": [{"id": "steps", "int64Value": "5"}]})
        self.assertEqual(-1, report["direction"])
        self.assertAlmostEqual(.006*600/(2*math.pi), report["fit"]["kv"])

    def test_completed_feedforward_dataset_uses_existing_durable_transfer(self):
        keys = ("battery_voltage", "duty", "current", "position", "velocity", "estimated_voltage", "movement_onset", "steady_window")
        run_id = str(uuid.uuid4())
        header = wire.DebugRunHeader(run_id=run_id, ticks_per_revolution=600, max_power=1, current_limit_amps=7,
            benchmark=wire.DebugBenchmarkDefinition(id="motor.feedforward.v1", version=1,
                channels=[wire.Channel(channel_id=i+1, key=k) for i, k in enumerate(keys)]),
            inputs=[wire.DebugCommandArgumentValue(id="steps", int64_value=5)])
        samples = []
        for i, r in enumerate(fixture()):
            values = []
            for channel, key in enumerate(keys, 1):
                value = wire.ChannelValue(channel_id=channel)
                if key == "position": value.int64_value = r[key]
                elif key in ("movement_onset", "steady_window"): value.boolean_value = r[key]
                else: value.float64_value = r[key]
                values.append(value)
            samples.append(wire.DebugRunSample(sequence=i, robot_time_ns=round(r["time"]*1e9), phase=r["phase"],
                repetition=1, direction=1, operating_point=r["point"], values=values))
        with tempfile.TemporaryDirectory() as directory:
            store = DebuggerResults(Path(directory))
            def ingest(body, value):
                e = wire.Envelope(**{body: value})
                return store.ingest(e, e.SerializeToString(), 1)
            ingest("debug_run_header", header)
            for i in range(0, len(samples), 128):
                chunk = wire.DebugRunChunk(run_id=run_id, chunk_index=i//128, samples=samples[i:i+128])
                ingest("debug_run_chunk", chunk)
                ingest("debug_run_chunk", chunk)
            ack = ingest("debug_run_end", wire.DebugRunEnd(run_id=run_id, total_samples=len(samples),
                total_chunks=(len(samples)+127)//128, outcome="completed"))
            self.assertEqual(len(samples), ack.total_samples)
            self.assertAlmostEqual(.006*600/(2*math.pi), store.report(run_id)["analysis"]["fit"]["kv"])


class FeedforwardApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_input_map_is_forwarded_and_legacy_requests_remain_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DebuggerResults(Path(directory))
            service = SimpleNamespace(_debug_results=store, _debug_ready={"nodeId": "motor", "toolInstanceId": "tool", "benchmarks": [definition()]},
                _debug_manifest={}, _server=SimpleNamespace(send=AsyncMock()), _active_peer=None,
                _debug_envelope=lambda **kw: wire.Envelope(**kw))
            app = FastAPI(); install_debugger_controls(app, service)
            route = next(r.endpoint for r in app.routes if r.path == "/api/debug/runs" and "POST" in r.methods)
            request = RunRequest(node_id="motor", tool_instance_id="tool", benchmark_id="motor.feedforward.v1",
                inputs=dict(port=0, ticks_per_revolution=600, current_limit_amps=7))
            result = await route(request)
            self.assertEqual(str(request.request_id), result["runId"])
            args = service._server.send.call_args.args[0].debug_command_request.arguments
            self.assertEqual(20, next(a.int64_value for a in args if a.id == "steps"))
            with self.assertRaises(HTTPException):
                await route(RunRequest(node_id="motor", tool_instance_id="tool", benchmark_id="motor.feedforward.v1", inputs={"port": 8}))
