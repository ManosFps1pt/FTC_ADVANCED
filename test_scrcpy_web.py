"""Test the Flask commands and file serving without activating phone hardware."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import time

from scrcpy_backend import ScrcpyBackend, ScrcpyError, create_web_app, main, parse_native_options


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.backend = Mock(spec=ScrcpyBackend)
        self.state = {"state": "IDLE", "running": False, "logs": [], "error": None}
        self.backend.status.side_effect = lambda: dict(self.state)
        self.backend.stop.side_effect = self.stop_backend
        self.backend.start_camera.side_effect = self.start_camera
        self.backend.start_native.side_effect = self.start_native
        self.backend.native_help.return_value = (
            "Options:\n\n    --camera-zoom=zoom\n        Set camera zoom.\n\n"
            "    -r, --record=file.mp4\n        Record video.\n\n"
            "    --tcpip[=[+]ip[:port]]\n        Connect wirelessly.\n\n"
            "    -K\n        HID keyboard.\n\nShortcuts:\n\n    MOD+q\n        Quit\n"
        )
        self.app = create_web_app(self.backend, self.directory)
        self.client = self.app.test_client()
        self.headers = {"X-Scrcpy-Token": self.app.extensions["scrcpy_token"]}

    def stop_backend(self):
        self.state.update(state="COMPLETED", running=False)
        return dict(self.state)

    def start_camera(self, options, *, output, preview, browser_preview=False):
        if output:
            with output.open("xb") as stream:
                stream.write(b"test video bytes")
        self.state.update(state="RUNNING", running=True)
        return dict(self.state)

    def start_native(self, args):
        self.state.update(state="RUNNING", running=True)
        return dict(self.state)

    def post(self, path, data):
        return self.client.post(path, json=data, headers=self.headers)

    def test_page_and_feature_catalog(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        result = self.client.get("/api/features").get_json()
        self.assertEqual({"facing", "aspect_ratio", "fps", "flip"}, {f["name"] for f in result["camera"]})
        self.assertEqual([], result["native"])

    def test_browser_preview_camera_and_frame_endpoint(self):
        result = self.post('/api/camera', {'mode':'preview', 'preview':False, 'browser_preview':True})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(self.backend.start_camera.call_args.kwargs['browser_preview'])
        self.assertFalse(self.backend.start_camera.call_args.kwargs['preview'])
        self.backend.preview_frame.return_value = None
        self.assertEqual(self.client.get('/api/preview/frame').status_code, 204)
        self.backend.preview_frame.return_value = b'jpeg-frame'
        frame = self.client.get('/api/preview/frame')
        self.assertEqual(frame.mimetype, 'image/jpeg')
        self.assertEqual(frame.data, b'jpeg-frame')
        self.assertEqual(frame.headers['Cache-Control'], 'no-store')

    def test_no_argument_main_starts_local_server(self):
        with patch("scrcpy_backend.ScrcpyBackend", return_value=self.backend), \
             patch("scrcpy_backend.create_web_app", return_value=Mock()) as factory:
            factory.return_value.extensions = {"start_automatic_preview":Mock(), "close_camera":self.backend.stop}
            self.assertEqual(main([]), 0)
            factory.return_value.extensions["start_automatic_preview"].assert_called_once()
            factory.return_value.run.assert_called_once_with(
                host="127.0.0.1", port=8765, debug=False, use_reloader=False, threaded=True)
            self.backend.stop.assert_called_once()

    def test_automatic_preview_saves_nothing_and_record_switches(self):
        self.app.extensions['start_automatic_preview']()
        self.addCleanup(self.app.extensions['close_camera'])
        deadline = time.monotonic()+2
        while not self.state['running'] and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertEqual(self.client.get('/api/status').json['session_kind'], 'preview')
        self.assertIsNone(self.backend.start_camera.call_args.kwargs['output'])
        self.assertIsNone(self.backend.start_camera.call_args.args[0].duration)
        self.assertEqual(list(self.directory.iterdir()), [])
        result = self.post('/api/camera', {'mode':'record', 'filename':'clicked.mp4', 'browser_preview':True})
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.backend.stop.call_count,1)
        self.assertTrue((self.directory/'clicked.mp4').exists())
        self.assertEqual(self.client.get('/api/status').json['session_kind'],'record')
        result=self.post('/api/recording/stop',{})
        self.assertEqual(result.status_code,200)
        self.assertEqual(self.client.get('/api/status').json['session_kind'],'preview')
        self.assertIsNone(self.backend.start_camera.call_args.kwargs['output'])
        self.assertIsNone(self.backend.start_camera.call_args.args[0].duration)
        self.post('/api/stop',{})
        time.sleep(.6)
        self.assertFalse(self.state['running'])

    def test_advanced_camera_options_are_rejected(self):
        for options in ({"duration": 1}, {"size": "1920x1080"}, {"audio": False}, {"zoom": 2.0}):
            with self.subTest(options=options):
                self.assertEqual(self.post('/api/camera', {'mode': 'preview', 'options': options}).status_code, 400)

    def test_invalid_record_request_keeps_preview_running(self):
        self.post('/api/camera', {'mode':'preview','browser_preview':True})
        result=self.post('/api/camera', {'mode':'record','filename':'invalid.mp4',
                                       'options':{'size':'invalid'}})
        self.assertEqual(result.status_code,400)
        self.backend.stop.assert_not_called()
        self.assertTrue(self.state['running'])

    def test_automatic_preview_failure_keeps_server_available(self):
        self.backend.start_camera.side_effect=ScrcpyError('No phone connected')
        self.app.extensions['start_automatic_preview']()
        self.addCleanup(self.app.extensions['close_camera'])
        deadline=time.monotonic()+2
        while not self.client.get('/api/status').json['startup_error'] and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertIn('No phone connected',self.client.get('/api/status').json['startup_error'])
        self.assertEqual(self.client.get('/').status_code,200)

    def test_record_stop_list_download_and_range(self):
        result = self.post("/api/camera", {"mode": "record", "options": {"serial": "PHONE"},
                                           "filename": "clip.mp4", "preview": False})
        self.assertEqual(result.status_code, 200)
        call = self.backend.start_camera.call_args
        self.assertFalse(call.kwargs["preview"])
        self.assertEqual(self.client.get("/api/recordings/clip.mp4").status_code, 409)
        self.assertTrue(self.client.get("/api/recordings").get_json()["files"][0]["active"])
        self.assertEqual(self.post("/api/stop", {}).status_code, 200)
        response = self.client.get("/api/recordings/clip.mp4?download=1")
        self.addCleanup(response.close)
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(response.data, b"test video bytes")
        response = self.client.get("/api/recordings/clip.mp4", headers={"Range": "bytes=0-3"})
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b"test")

    def test_preview_does_not_create_recording(self):
        self.assertEqual(self.post("/api/camera", {"mode": "preview", "preview": False}).status_code, 200)
        self.assertIsNone(self.backend.start_camera.call_args.kwargs["output"])
        self.assertTrue(self.backend.start_camera.call_args.kwargs["preview"])
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_double_start_is_rejected(self):
        self.state["running"] = True
        self.assertEqual(self.post("/api/camera", {"mode": "preview"}).status_code, 409)
        self.backend.start_camera.assert_not_called()

    def test_native_recording_is_scoped_and_never_overwritten(self):
        self.assertEqual(self.post("/api/native", {"args": ["--no-video", "-r", "audio.opus"]}).status_code, 200)
        args = self.backend.start_native.call_args.args[0]
        self.assertEqual(args, ["--no-video", f"--record={self.directory / 'audio.opus'}"])
        self.post("/api/stop", {})
        self.assertEqual(self.post("/api/native", {"args": ["--record=audio.opus"]}).status_code, 409)

    def test_recording_paths_cannot_escape_library(self):
        for name in ("../secret.mp4", "C:\\secret.mp4", "folder/clip.mp4"):
            with self.subTest(name=name):
                self.assertEqual(self.post("/api/camera", {"mode": "record", "filename": name}).status_code, 400)
                self.assertEqual(self.post("/api/native", {"args": ["--record=" + name]}).status_code, 400)
        self.backend.start_camera.assert_not_called()
        self.backend.start_native.assert_not_called()

    def test_invalid_camera_types_are_rejected(self):
        for options in ({"audio": "false"}, {"fps": 1.5}, {"unknown": True}, {"duration": float("nan")}):
            self.assertEqual(self.post("/api/camera", {"mode": "preview", "options": options}).status_code, 400)

    def test_backend_error_is_readable_json(self):
        self.backend.start_camera.side_effect = ScrcpyError("Phone is assigned as Robot Controller")
        response = self.post("/api/camera", {"mode": "preview"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Robot Controller", response.get_json()["error"])

    def test_external_posts_and_untrusted_hosts_are_rejected(self):
        self.assertEqual(self.client.post("/api/stop", json={}).status_code, 403)
        self.assertEqual(self.client.get("/", headers={"Host": "untrusted.example"}).status_code, 400)
        self.backend.stop.assert_not_called()

    def test_native_arguments_stay_arguments(self):
        args = ["--window-title=literal & text", "--future-flag=value with spaces"]
        self.assertEqual(self.post("/api/native", {"args": args}).status_code, 200)
        self.backend.start_native.assert_called_once_with(args)

    def test_wireless_commands_and_failed_connection(self):
        self.backend.adb.return_value = "Successfully paired"
        result = self.post("/api/wireless", {"action": "pair", "address": "192.168.1.20:37000", "code": "123456"})
        self.assertEqual(result.status_code, 200)
        self.backend.adb.assert_called_once_with("pair", "192.168.1.20:37000", "123456", timeout=60)
        self.backend.adb.return_value = "failed to connect"
        self.assertEqual(self.post("/api/wireless", {"action": "connect", "address": "192.168.1.20:37001"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
