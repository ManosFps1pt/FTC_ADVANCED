import time
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from .main import DriverStationService, OpModeRequest, RobotDataService
from .protocol import robot_data_pb2 as wire


class LaptopArmTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = RobotDataService()
        self.peer = ("127.0.0.1", 12345)
        sid, cid = uuid.uuid4(), uuid.uuid4()
        self.service._peer_states[self.peer] = dict(
            session_id=str(sid), session_bytes=sid.bytes, connection_bytes=cid.bytes,
            protocol=3, received_ns=time.perf_counter_ns(), robot_id="test", opmode="Debugger",
            debug_safety_state={"armed": False},
            debug_tool_ready={"nodeId": "motor", "toolInstanceId": "instance",
                              "commands": [{"id": "control.arm"}]})
        self.service.select_debug_target(str(sid))
        self.service._server = SimpleNamespace(send=AsyncMock())

    async def test_laptop_arm_requires_owner_then_waits_for_robot_acknowledgement(self):
        token = self.service.acquire_debug_control()
        with self.assertRaises(RuntimeError):
            await self.service.arm_debug_control("wrong-owner")
        self.service._server.send.assert_not_called()
        await self.service.arm_debug_control(token)
        frame, peer = self.service._server.send.call_args.args
        self.assertEqual(self.peer, peer)
        self.assertEqual("control.arm", frame.debug_command_request.command_id)
        self.assertEqual("instance", frame.debug_command_request.tool_instance_id)
        self.assertEqual(500, frame.debug_command_request.ttl_ms)
        def run():
            return self.service._debug_envelope(lease_id=token,
                debug_command_request=wire.DebugCommandRequest(command_id="benchmark.run"))
        with self.assertRaises(RuntimeError): run()
        self.service._debug_safety = {"armed": True, "controlLeaseId": "new-robot-arm"}
        self.assertEqual("new-robot-arm", run().control_lease_id)
        await self.service.renew_debug_control(token)
        keepalive = self.service._server.send.call_args.args[0]
        self.assertEqual("control.keepalive", keepalive.debug_command_request.command_id)
        self.assertEqual("new-robot-arm", keepalive.control_lease_id)
        self.service._debug_lease_deadline = time.monotonic() - 1
        with self.assertRaises(RuntimeError):
            await self.service.arm_debug_control(token)

    async def test_old_firmware_reports_update_required(self):
        token = self.service.acquire_debug_control()
        self.service._debug_ready["commands"] = []
        with self.assertRaisesRegex(RuntimeError, "updated BIOBUZZ APK"):
            await self.service.arm_debug_control(token)
        self.service._server.send.assert_not_called()


class LaptopLaunchTests(unittest.TestCase):
    def test_debugger_init_start_use_existing_laptop_lifecycle(self):
        service = DriverStationService()
        client = Mock(is_connected=True)
        service._client = client
        request = OpModeRequest(name="FTC Advanced Debugger")
        service.init_opmode(request)
        service.start_opmode(request)
        client.init_opmode.assert_called_once_with(request.name, request.timeout_s)
        client.start_opmode.assert_called_once_with(request.name, request.timeout_s)
