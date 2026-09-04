from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from .adb_camera import AdbCameraError, AdbCameraService, _parse_adb_devices, _parse_media_rows


class _DiscoveryRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        args = command[1:]
        if args == ["devices", "-l"]:
            return self._result(
                "List of devices attached\n"
                "RC123 device product:pa1q model:Galaxy_RC device:pa1q transport_id:1\n"
                "CAM456 device product:realme model:RMX3269 device:RED8F6 transport_id:2\n"
            )
        serial = args[1] if len(args) > 1 and args[0] == "-s" else ""
        shell = args[3:] if len(args) > 2 and args[2] == "shell" else []
        if shell[:2] == ["getprop", "ro.product.manufacturer"]:
            return self._result("samsung\n" if serial == "RC123" else "realme\n")
        if shell[:2] == ["getprop", "ro.product.model"]:
            return self._result("Galaxy RC\n" if serial == "RC123" else "RMX3269\n")
        if shell[:2] == ["getprop", "ro.build.version.release"]:
            return self._result("16\n" if serial == "RC123" else "11\n")
        if shell[:2] == ["getprop", "sys.boot_completed"]:
            return self._result("1\n")
        if shell[:3] == ["pm", "list", "features"]:
            return self._result("feature:android.hardware.camera\n")
        if shell[:2] == ["pm", "path"]:
            package = shell[2] if len(shell) > 2 else ""
            installed = package == "com.qualcomm.ftcrobotcontroller" or (
                serial == "RC123" and package == "com.sec.android.app.camera"
            ) or (serial == "CAM456" and package == "com.android.camera2")
            return self._result(f"package:/data/app/{package}/base.apk\n" if installed else "", 0 if installed else 1)
        if shell[:4] == ["cmd", "package", "resolve-activity", "--brief"]:
            package = shell[-1]
            component = (
                "com.sec.android.app.camera/.Camera"
                if package == "com.sec.android.app.camera"
                else "com.android.camera2/com.android.camera.VideoCamera"
            )
            return self._result(component + "\n")
        if shell[:3] == ["dumpsys", "activity", "services"]:
            active = "ServiceRecord com.qualcomm.ftccommon.FtcRobotControllerService\n" if serial == "RC123" else ""
            return self._result(active)
        if shell[:3] == ["dumpsys", "activity", "activities"]:
            return self._result("")
        return self._result("")

    @staticmethod
    def _result(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class AdbCameraTests(unittest.TestCase):
    def test_parse_adb_devices_keeps_serial_and_state(self) -> None:
        parsed = _parse_adb_devices(
            "List of devices attached\nABC device product:test model:Phone device:p transport_id:7\nXYZ unauthorized usb:1-1\n"
        )
        self.assertEqual("Phone", parsed["ABC"]["model"])
        self.assertEqual("unauthorized", parsed["XYZ"]["state"])

    def test_parse_media_rows(self) -> None:
        items = _parse_media_rows(
            "Row: 9 _id=42, _data=/storage/emulated/0/DCIM/Camera/test.mp4, "
            "_display_name=test.mp4, duration=5273, _size=21886409, width=1920, height=1080, "
            "date_added=100, date_modified=101, mime_type=video/mp4\n"
        )
        self.assertEqual(1, len(items))
        self.assertEqual("42", items[0].media_id)
        self.assertEqual(5273, items[0].duration_ms)
        self.assertEqual(1920, items[0].width)

    def test_active_rc_wins_and_other_phone_is_camera_candidate(self) -> None:
        runner = _DiscoveryRunner()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            service = AdbCameraService(
                root / "recordings",
                settings_path=root / "roles.json",
                adb_path="adb",
                command_runner=runner,
            )
            status = service.refresh_devices()
            by_serial = {device["serial"]: device for device in status["devices"]}
            self.assertEqual("robot_controller", by_serial["RC123"]["effective_role"])
            self.assertTrue(by_serial["RC123"]["rc_active"])
            self.assertEqual("unassigned", by_serial["CAM456"]["effective_role"])
            self.assertEqual("camera", by_serial["CAM456"]["suggested_role"])
            self.assertEqual(
                "com.android.camera2/com.android.camera.VideoCamera",
                by_serial["CAM456"]["camera_component"],
            )

            assigned = service.assign_role("CAM456", "camera")
            self.assertEqual("CAM456", assigned["selected_camera_serial"])
            self.assertEqual("READY", assigned["camera"]["state"])
            with self.assertRaises(AdbCameraError):
                service.assign_role("RC123", "camera")
            with self.assertRaises(AdbCameraError):
                service.assign_role("CAM456", "robot_controller")

            targeted = [command for command in runner.commands if "shell" in command]
            self.assertTrue(targeted)
            self.assertTrue(all(command[1] == "-s" and command[2] in {"RC123", "CAM456"} for command in targeted))


if __name__ == "__main__":
    unittest.main()
