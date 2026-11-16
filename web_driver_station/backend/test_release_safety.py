import struct
import tempfile
import time
import unittest
import uuid
import zlib
from pathlib import Path
from unittest.mock import patch

from ftc_control_hub import ControlHubClient, ControlHubConfig, ControlHubError, GamepadInput
from .ftclog import FtcLogError, iter_records
from .protocol import robot_data_pb2 as wire
from .protocol_codec import decode as decode_envelope, WireProtocolError


class ControlLeaseTests(unittest.TestCase):
    def setUp(self):
        self.client = ControlHubClient(ControlHubConfig(host="127.0.0.1"))
        self.client._accepted_event.set()
        self.client._last_heartbeat_ns = time.monotonic_ns()

    def test_stale_heartbeat_withdraws_authority(self):
        self.client._last_heartbeat_ns -= 60_000_000_000
        self.assertFalse(self.client.is_connected)
        with self.assertRaises(ControlHubError):
            self.client.acquire_control()

    def test_expiry_neutralizes_both_and_cannot_be_renewed(self):
        token = self.client.acquire_control()
        for user in (1, 2):
            self.client.set_gamepad_input(GamepadInput(user=user, left_stick_y=1), token)
        with patch.object(self.client, "_send"):
            self.client._send_due_gamepads(time.monotonic() + 60)
        self.assertTrue(all(slot.input.is_at_rest for slot in self.client._gamepads.values()))
        with self.assertRaises(ControlHubError):
            self.client.renew_control(token)
        with self.assertRaises(ControlHubError):
            self.client.set_gamepad_input(GamepadInput(user=1, left_stick_y=1), token)
        self.assertNotEqual(token, self.client.acquire_control())

    def test_second_owner_rejected_and_renewal_preserves_held_input(self):
        token = self.client.acquire_control()
        self.client.set_gamepad_input(GamepadInput(user=1, left_stick_y=1), token)
        self.client.renew_control(token)
        self.assertFalse(self.client._gamepads[1].input.is_at_rest)
        with self.assertRaises(ControlHubError):
            self.client.acquire_control()


class AndroidRecordingCompatibilityTests(unittest.TestCase):
    def test_fixture_written_by_real_java_recorder(self):
        path = Path(__file__).parent / "fixtures/java-v3.ftclog"
        records = list(iter_records(path))
        self.assertEqual(1, len(records))
        self.assertEqual(123, records[0].received_monotonic_ns)
        self.assertEqual("java-fixture", wire.Envelope.FromString(records[0].protobuf_frame).hello.robot_id)

    def test_actual_java_layout_and_legacy_size_with_crc(self):
        frame = b"protobuf-fixture"
        prefix = struct.pack("!IQI", len(frame), 123, len(frame))
        record = prefix + frame + struct.pack("!I", zlib.crc32(prefix + frame))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "android.ftclog"
            for size in (32, 36):
                data = struct.pack("!8sHH16sQ", b"FTCLOG\0\0", 1, size, uuid.uuid4().bytes, 100) + record
                path.write_bytes(data)
                self.assertEqual(frame, list(iter_records(path))[0].protobuf_frame)
                self.assertEqual(data, path.read_bytes())
                path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
                with self.assertRaises(FtcLogError):
                    list(iter_records(path))

    def test_quota_failure_keeps_incomplete_file(self):
        from .ftclog import RawFtcLogRecorder
        with tempfile.TemporaryDirectory() as folder:
            recorder = RawFtcLogRecorder(Path(folder), max_bytes=120)
            session = str(uuid.uuid4())
            recorder.append(session, b"first", 1)
            with self.assertRaises(FtcLogError): recorder.append(session, b"x" * 120, 2)
            path = recorder.close_session(session)
            self.assertTrue(path.name.endswith(".partial"))
            self.assertEqual([b"first"], [r.protobuf_frame for r in iter_records(path)])
            recorder.delete_session(session)
            replacement = str(uuid.uuid4())
            recorder.append(replacement, b"recovered", 3)
            recorder.close_all()
            self.assertEqual([], recorder.status()["failed_sessions"])


class SampleAcquisitionTests(unittest.TestCase):
    def test_batch_preserves_individual_times_and_v2_remains_readable(self):
        envelope = wire.Envelope(protocol_version=3, session_id=uuid.uuid4().bytes,
                                 connection_id=uuid.uuid4().bytes, robot_elapsed_ns=900)
        for sequence, timestamp in enumerate((100, 200)):
            sample = envelope.sample_batch.snapshots.add(sample_sequence=sequence, robot_time_ns=timestamp)
            sample.values.add(channel_id=1, float64_value=0.5)
        records = decode_envelope(envelope, {1: ("motor.power", wire.FLOAT64)})
        self.assertEqual(["100", "200"], [record["robotTimeNs"] for record in records])
        envelope.sample_batch.snapshots[0].ClearField("robot_time_ns")
        with self.assertRaises(WireProtocolError):
            decode_envelope(envelope, {1: ("motor.power", wire.FLOAT64)})
        envelope.protocol_version = 2
        records = decode_envelope(envelope, {1: ("motor.power", wire.FLOAT64)})
        self.assertEqual(["900", "900"], [record["robotTimeNs"] for record in records])


class DebuggerAuthorityTests(unittest.TestCase):
    def setUp(self):
        from .main import RobotDataService
        self.service = RobotDataService()
        self.first, self.second = ("127.0.0.1", 1), ("127.0.0.1", 2)
        for peer in (self.first, self.second):
            sid, cid = uuid.uuid4(), uuid.uuid4()
            self.service._peer_states[peer] = dict(session_id=str(sid), session_bytes=sid.bytes,
                connection_bytes=cid.bytes, protocol=3, received_ns=time.perf_counter_ns(),
                robot_id=str(peer), opmode="Debugger", debug_safety_state={"armed": True, "controlLeaseId": "old-arm"})
        self.service.select_debug_target(self.service._peer_states[self.first]["session_id"])

    def command(self, token=None):
        return self.service._debug_envelope(lease_id=token,
            debug_command_request=wire.DebugCommandRequest(command_id="benchmark.run"))

    def test_owner_expiry_and_reacquisition_require_new_local_arm(self):
        token = self.service.acquire_debug_control()
        with self.assertRaises(RuntimeError): self.command(token)
        self.service._debug_safety = {"armed": True, "controlLeaseId": "new-arm"}
        self.assertEqual(self.service._peer_states[self.first]["session_bytes"], self.command(token).session_id)
        with self.assertRaises(RuntimeError): self.command("another-browser")
        with self.assertRaises(RuntimeError): self.service.acquire_debug_control()
        self.service._debug_lease_deadline = time.monotonic()-1
        with self.assertRaises(RuntimeError): self.command(token)
        replacement = self.service.acquire_debug_control()
        with self.assertRaises(RuntimeError): self.command(replacement)
        self.service._debug_safety = {"armed": True, "controlLeaseId": "rearmed"}
        self.assertEqual("rearmed", self.command(replacement).control_lease_id)

    def test_two_sessions_cannot_mix_and_stale_or_v2_has_no_authority(self):
        token = self.service.acquire_debug_control()
        with self.assertRaises(ValueError):
            self.service.select_debug_target(self.service._peer_states[self.second]["session_id"])
        self.service._debug_safety = {"armed": True, "controlLeaseId": "new-arm"}
        self.service._peer_states[self.second]["received_ns"] = time.perf_counter_ns()
        self.assertEqual(self.first, self.service._active_peer)
        self.service._peer_states[self.first]["received_ns"] -= 3_000_000_000
        with self.assertRaises(RuntimeError): self.command(token)
        self.service._peer_states[self.first].update(received_ns=time.perf_counter_ns(), protocol=2)
        with self.assertRaises(RuntimeError): self.command(token)


class BrowserAndSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_overflow_before_worker_starts_still_closes_socket(self):
        import asyncio
        from .broadcast import BrowserBroadcast
        class Socket:
            def __init__(self):
                self.closed = asyncio.Event()
                self.sent = []
            async def send_json(self, message): self.sent.append(message)
            async def close(self, code): self.closed.set()
        socket = Socket()
        sockets = {socket}
        delivery = BrowserBroadcast(capacity=1)
        delivery.publish(sockets, {"sample": 1})
        delivery.publish(sockets, {"sample": 2})
        await delivery.close()
        self.assertTrue(socket.closed.is_set())
        self.assertEqual([], socket.sent)
        self.assertFalse(delivery.workers)
        self.assertNotIn(socket, sockets)

    async def test_second_robot_cannot_rebind_camera_without_selection(self):
        from .main import RobotDataService
        from .robot_data_tcp_server import ReceivedRobotPacket
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"ROBOT_DATA_RECORDINGS_DIR": folder}):
            service = RobotDataService()
            starts, binds, stops = [], [], []
            service.set_capture_callbacks(video_ready_checker=lambda _: True,
                session_bound=binds.append, capture_start=starts.append, capture_stop=stops.append)
            sessions = [str(uuid.uuid4()), str(uuid.uuid4())]
            try:
                for index, sid in enumerate(sessions):
                    envelope = wire.Envelope(protocol_version=3, session_id=uuid.UUID(sid).bytes,
                        connection_id=uuid.uuid4().bytes,
                        hello=wire.Hello(robot_id=f"robot-{index}", robot_name=f"Hub {index}", op_mode_name="Telemetry"))
                    frame, peer, now = envelope.SerializeToString(), ("127.0.0.1", index+1), time.perf_counter_ns()
                    await service._on_raw_packet(envelope, frame, peer, now)
                    await service._on_packet(ReceivedRobotPacket(envelope, frame, decode_envelope(envelope, {}), peer, now))
                self.assertEqual([sessions[0]], starts)
                self.assertEqual([sessions[0]], binds)
                self.assertEqual(sessions[0], service.capture_status()["sessionId"])
                self.assertEqual(2, len(service.recording_sessions()))
                service.select_debug_target(sessions[1])
                await service.select_capture_session(sessions[1])
                self.assertEqual(sessions, starts)
                self.assertEqual(sessions, binds)
                self.assertEqual([sessions[0]], stops)
                self.assertEqual(sessions[1], service.capture_status()["sessionId"])
                self.assertFalse(service.capture_status()["enabled"])
            finally:
                await service.close()

    async def test_slow_browser_never_blocks_producer_and_is_disconnected(self):
        import asyncio
        from .broadcast import BrowserBroadcast
        class Socket:
            closed = False
            def __init__(self): self.closed_event = asyncio.Event()
            async def send_json(self, message): await asyncio.Event().wait()
            async def close(self, code):
                self.closed = True
                self.closed_event.set()
        socket = Socket()
        sockets = {socket}
        delivery = BrowserBroadcast(capacity=2, timeout=.01)
        delivery.publish(sockets, {"sample": 1})
        await asyncio.wait_for(socket.closed_event.wait(), timeout=1)
        self.assertNotIn(socket, sockets)
        self.assertTrue(socket.closed)
        await delivery.close()
