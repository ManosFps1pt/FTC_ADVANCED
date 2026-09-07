from __future__ import annotations

import asyncio
import tempfile
import unittest
import uuid
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from recording_viewer.backend.main import RecordingLibrary, create_library_app
from .debugger_api import RunRequest, StorageRequest, install_debugger_controls
from .debugger_analysis import friction
from .debugger_results import DebuggerResults
from .protocol import robot_data_pb2 as wire


def synthetic_samples():
    rows=[]
    def add(phase, speed, volts, point=0, onset=False, steady=False):
        rows.append(dict(time=len(rows)*.02, phase=phase, repetition=1, direction=1, point=point,
                         velocity=speed, battery_voltage=12., duty=volts/12, estimated_voltage=volts,
                         current=.2+speed*.001, position=len(rows), movement_onset=onset, steady_window=steady))
    add("ramp",10,.5,onset=True)
    for point,speed in enumerate((100,200,300,400),1):
        for _ in range(55): add("steady",speed,.4+.006*speed,point,steady=True)
    for i in range(201): add("coast",max(0,400-i*2),0)
    return rows


def dataset(run_id=None):
    run_id=run_id or str(uuid.uuid4())
    keys=("battery_voltage","duty","current","position","velocity","estimated_voltage","movement_onset","steady_window")
    header=wire.DebugRunHeader(run_id=run_id,mechanism_id="test.motor",mechanism_label="SIMULATED free-spin motor",
        motor_name="debugMotor",started_robot_ns=1_000_000_000,max_power=.35,current_limit_amps=2,
        benchmark=wire.DebugBenchmarkDefinition(id="motor.friction.v1",version=1,label="Friction & Free-Spin Characterization",
            channels=[wire.Channel(channel_id=i+1,key=k) for i,k in enumerate(keys)]),
        inputs=[wire.DebugCommandArgumentValue(id="repetitions",int64_value=1)])
    samples=[]
    for index,row in enumerate(synthetic_samples()):
        values=[]
        for i,key in enumerate(keys):
            value=wire.ChannelValue(channel_id=i+1)
            if key=="position": value.int64_value=row[key]
            elif key in ("movement_onset","steady_window"): value.boolean_value=row[key]
            else: value.float64_value=row[key]
            values.append(value)
        samples.append(wire.DebugRunSample(sequence=index,robot_time_ns=header.started_robot_ns+round(row["time"]*1e9),
            phase=row["phase"],repetition=1,direction=1,operating_point=row["point"],values=values))
    chunks=[wire.DebugRunChunk(run_id=run_id,chunk_index=i//128,samples=samples[i:i+128]) for i in range(0,len(samples),128)]
    end=wire.DebugRunEnd(run_id=run_id,total_samples=len(samples),total_chunks=len(chunks),outcome="completed",message="Synthetic fixture")
    return header,chunks,end


def ingest(store,body,value):
    envelope=wire.Envelope(protocol_version=2,session_id=uuid.uuid4().bytes,connection_id=uuid.uuid4().bytes,**{body:value})
    return store.ingest(envelope,envelope.SerializeToString(),1)


def finish(store,run_id=None):
    header,chunks,end=dataset(run_id)
    ingest(store,"debug_run_header",header)
    for chunk in chunks: ingest(store,"debug_run_chunk",chunk)
    ingest(store,"debug_run_end",end)
    return header.run_id


class AnalysisTests(unittest.TestCase):
    def test_run_request_allows_twelve_volts_and_rejects_more(self):
        self.assertEqual(12,RunRequest(node_id="motor",tool_instance_id="tool",max_voltage=12).max_voltage)
        with self.assertRaises(ValidationError): RunRequest(node_id="motor",tool_instance_id="tool",max_voltage=12.1)

    def test_known_coefficients_breakaway_and_coast(self):
        result=friction(synthetic_samples(),{})
        fit=result["fits"][0]["fit"]
        self.assertAlmostEqual(.4,fit["ks"])
        self.assertAlmostEqual(.006,fit["kv"])
        self.assertAlmostEqual(1,fit["r2"])
        self.assertAlmostEqual(.5,result["summary"]["breakawayVoltage"]["median"])
        self.assertAlmostEqual(2.4,result["repetitions"][0]["coastTime"])
        self.assertAlmostEqual(100,result["repetitions"][0]["deceleration"])

    def test_rpm_units_and_unstable_samples(self):
        samples=synthetic_samples()
        rpm=friction(samples,{"ticksPerRevolution":600})
        self.assertEqual("RPM",rpm["speedUnit"])
        self.assertAlmostEqual(.06,rpm["fits"][0]["fit"]["kv"])
        for row in samples:
            if row["phase"]=="steady":row["velocity"]+=row["time"]*300
        result=friction(samples,{})
        self.assertIsNone(result["fits"][0]["fit"])

    def test_incomplete_coast_and_no_data(self):
        result=friction(synthetic_samples()[:-180],{})
        self.assertIsNone(result["repetitions"][0]["coastTime"])
        self.assertIsNone(friction([],{})["summary"]["breakawayVoltage"])


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=DebuggerResults(self.root)
    def tearDown(self):
        self.store.recorder.close_all();self.temp.cleanup()

    def test_chunks_timestamps_duplicates_and_ack(self):
        header,chunks,end=dataset()
        ingest(self.store,"debug_run_header",header)
        for chunk in reversed(chunks):
            ingest(self.store,"debug_run_chunk",chunk);ingest(self.store,"debug_run_chunk",chunk)
        ack=ingest(self.store,"debug_run_end",end)
        self.assertEqual(end.total_samples,ack.total_samples)
        report=self.store.report(header.run_id)
        self.assertEqual(end.total_samples,len(report["analysis"]["timeline"]))
        self.assertAlmostEqual(.02,report["analysis"]["timeline"][1]["time"])
        self.assertEqual(ack,ingest(self.store,"debug_run_end",end))
        self.assertEqual([],RecordingLibrary(self.root).list())

    def test_interrupted_transfer_recovers_after_laptop_restart(self):
        header,chunks,end=dataset()
        ingest(self.store,"debug_run_header",header);ingest(self.store,"debug_run_chunk",chunks[0])
        with self.assertRaisesRegex(ValueError,"incomplete"):ingest(self.store,"debug_run_end",end)
        self.assertFalse((self.root/header.run_id/"analysis.json").exists())
        self.store.recorder.close_all()
        self.store=DebuggerResults(self.root)
        ingest(self.store,"debug_run_header",header)
        for chunk in chunks:ingest(self.store,"debug_run_chunk",chunk)
        self.assertIsNotNone(ingest(self.store,"debug_run_end",end))

    def test_conflicting_duplicate_and_bad_timestamp_rejected(self):
        header,chunks,end=dataset();ingest(self.store,"debug_run_header",header)
        ingest(self.store,"debug_run_chunk",chunks[0])
        corrupted=wire.DebugRunChunk();corrupted.CopyFrom(chunks[0]);corrupted.samples[0].robot_time_ns+=1
        with self.assertRaisesRegex(ValueError,"Conflicting"):ingest(self.store,"debug_run_chunk",corrupted)
        rows=[s for c in chunks for s in c.samples];rows[1].robot_time_ns=rows[0].robot_time_ns
        with self.assertRaisesRegex(ValueError,"timestamp"):self.store.decode_samples(header,rows)

    def test_hosted_routes_and_independent_runs(self):
        first=finish(self.store);second=finish(self.store)
        app=create_library_app(self.root)
        endpoints={r.path:r.endpoint for r in app.routes if hasattr(r,"endpoint")}
        self.assertEqual(2,len(endpoints["/api/debug/results"]()))
        self.assertEqual(first,endpoints["/api/debug/results/{run_id}"](first)["manifest"]["runId"])
        self.assertEqual("application/zip",endpoints["/api/debug/results/{run_id}/raw"](second).media_type)
        with self.assertRaises(HTTPException):endpoints["/api/debug/results/{run_id}"]("../secret")

    def test_discarded_run_retry_only_acknowledges_without_recreating_data(self):
        run_id=finish(self.store)
        self.store.forget(run_id)
        self.store=DebuggerResults(self.root)
        header,chunks,end=dataset(run_id)
        ingest(self.store,"debug_run_header",header)
        for chunk in chunks: ingest(self.store,"debug_run_chunk",chunk)
        self.assertEqual(end.total_samples,ingest(self.store,"debug_run_end",end).total_samples)
        self.assertFalse(self.store.path(run_id).exists())

    def test_incomplete_run_is_visible_after_restart(self):
        run_id=str(uuid.uuid4())
        self.store.expect(run_id,{"mechanismLabel":"SIMULATED"})
        self.store.pending[run_id]["state"]="running"
        self.assertEqual("running",self.store.list()[0]["state"])
        restarted=DebuggerResults(self.root)
        self.assertEqual("incomplete",restarted.list()[0]["state"])


class ServiceTransferTests(unittest.IsolatedAsyncioTestCase):
    async def test_tcp_retry_finalizes_one_bundle_without_camera_or_generic_popup(self):
        from .main import RobotDataService
        from .robot_data_tcp_server import RobotDataTcpServer
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"ROBOT_DATA_RECORDINGS_DIR":folder}):
            service=RobotDataService()
            service.set_capture_callbacks(video_ready_checker=lambda _:False, session_bound=lambda _:self.fail("Benchmark must not wait for camera"), capture_stop=lambda _:None)
            server=RobotDataTcpServer("127.0.0.1",0,discovery_port=5813,
                packet_handler=service._on_packet,raw_packet_handler=service._on_raw_packet)
            await server.start()
            session=uuid.uuid4().bytes
            header,chunks,end=dataset()
            async def connect():
                reader,writer=await asyncio.open_connection("127.0.0.1",server.listening_addresses[0][1])
                connection=uuid.uuid4().bytes
                async def send(**body):
                    frame=wire.Envelope(protocol_version=2,session_id=session,connection_id=connection,robot_elapsed_ns=123,**body).SerializeToString()
                    writer.write(struct.pack("!I",len(frame))+frame);await writer.drain()
                async def receive():
                    length=struct.unpack("!I",await asyncio.wait_for(reader.readexactly(4),3))[0]
                    return wire.Envelope.FromString(await reader.readexactly(length))
                await send(hello=wire.Hello(robot_id="simulated",robot_name="SIMULATED",op_mode_name="Debugger",capabilities=["debug-runs-v1"]))
                ack=await receive()
                self.assertIn("debug-runs-v1",ack.hello_ack.capabilities)
                return writer,send,receive
            try:
                writer,send,receive=await connect()
                await send(debug_run_header=header);await send(debug_run_chunk=chunks[0])
                writer.close();await writer.wait_closed();await asyncio.sleep(.05)
                writer,send,receive=await connect()
                await send(debug_run_header=header)
                for chunk in chunks:await send(debug_run_chunk=chunk)
                await send(debug_run_end=end)
                self.assertEqual(end.total_samples,(await receive()).debug_run_ack.total_samples)
                self.assertEqual(1,len(service._debug_results.list()))
                self.assertEqual([],service.recording_sessions())
                self.assertEqual({},service._upload_states)
                self.assertEqual("ready",service._debug_results.report(header.run_id)["manifest"]["state"])
                writer.close();await writer.wait_closed()
            finally:
                await server.close();service._debug_results.recorder.close_all();service._raw_recorder.close_all()


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_controls_and_duplicate_completed_request(self):
        with tempfile.TemporaryDirectory() as folder:
            store=DebuggerResults(Path(folder));sent=[]
            async def send(message,peer): sent.append(message)
            service=SimpleNamespace(_debug_results=store,_recording_uploader=None,_active_peer=None,
                _debug_manifest={"nodes":[{"id":"motor","label":"Registered motor"},{"id":"motor.friction","parentId":"motor"}]},
                _debug_ready={"nodeId":"motor.friction","toolInstanceId":"instance","benchmarks":[{"id":"motor.friction.v1"}]},
                _debug_envelope=lambda **body:wire.Envelope(**body),_server=SimpleNamespace(send=send))
            app=FastAPI();install_debugger_controls(app,service)
            endpoints={(r.path,next(iter(r.methods))):r.endpoint for r in app.routes if hasattr(r,"methods")}
            start=endpoints[("/api/debug/runs","POST")]
            request=RunRequest(node_id="motor.friction",tool_instance_id="instance",repetitions=1)
            run_id=(await start(request))["runId"]
            self.assertEqual("Registered motor",store.list()[0]["mechanismLabel"])
            self.assertEqual("benchmark.run",sent[-1].debug_command_request.command_id)
            await endpoints[("/api/debug/runs/{run_id}/keepalive","POST")](run_id)
            self.assertEqual("benchmark.keepalive",sent[-1].debug_command_request.command_id)
            await endpoints[("/api/debug/runs/{run_id}/abort","POST")](run_id)
            self.assertEqual("benchmark.abort",sent[-1].debug_command_request.command_id)
            finish(store,run_id);before=len(sent)
            self.assertEqual(run_id,(await start(request))["runId"])
            self.assertEqual(before,len(sent));self.assertEqual("ready",store.manifest(run_id)["state"])

    async def test_storage_decisions_and_failed_upload_retention(self):
        with tempfile.TemporaryDirectory() as folder:
            store=DebuggerResults(Path(folder))
            class Uploader:
                fail=False
                def upload(self,path):
                    if self.fail:raise RuntimeError("Upload offline")
            uploader=Uploader()
            service=SimpleNamespace(_debug_results=store,_recording_uploader=uploader)
            app=FastAPI();install_debugger_controls(app,service)
            endpoints={(r.path,next(iter(r.methods))):r.endpoint for r in app.routes if hasattr(r,"methods")}
            upload=endpoints[("/api/debug/runs/{run_id}/upload","POST")]
            listing=endpoints[("/api/debug/runs","GET")]
            async def wait_upload(run_id):
                for _ in range(100):
                    state=next((r.get("storage",{}).get("state") for r in listing() if r["runId"]==run_id),None)
                    if state!="uploading":return
                    await asyncio.sleep(.01)
                self.fail("Upload did not finish")
            run_id=finish(store);uploader.fail=True
            await upload(run_id,StorageRequest());await wait_upload(run_id)
            self.assertTrue(store.path(run_id).exists());self.assertEqual("error",listing()[0]["storage"]["state"])
            uploader.fail=False
            await upload(run_id,StorageRequest(keep_local=True));await wait_upload(run_id)
            self.assertTrue(store.path(run_id).exists());self.assertEqual("complete",listing()[0]["storage"]["state"])
            delete_id=finish(store);await upload(delete_id,StorageRequest());await wait_upload(delete_id)
            self.assertFalse(store.path(delete_id).exists())
            discard_id=finish(store);endpoints[("/api/debug/runs/{run_id}","DELETE")](discard_id)
            self.assertFalse(store.path(discard_id).exists())


if __name__=="__main__":unittest.main()
