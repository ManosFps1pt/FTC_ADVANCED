# Standalone scrcpy backend

[scrcpy_backend.py](scrcpy_backend.py) is an importable Python backend and a
local Flask testing website and command-line entry point. It adds direct camera capture alongside the existing
ADB camera implementation. It is not yet wired into the Driver Station UI.

## Dependencies

Python 3.10+, the official scrcpy distribution, and Flask for the testing website.
The core backend and CLI use the Python standard library. Termux and a permanent
Android app are not needed.

~~~powershell
winget install --exact --id Genymobile.scrcpy
python -m pip install -r requirements-scrcpy.txt
python scrcpy_backend.py doctor
~~~

scrcpy 4.1 was installed and used for discovery validation. The script locates
WinGet installations even before PATH is refreshed. It reuses the Android SDK's
ADB when available. Override executable paths with --scrcpy PATH and --adb PATH
before the subcommand, or set the SCRCPY and ADB environment variables.

Camera capture requires Android 12+, USB debugging and an authorized connection.
scrcpy uploads its temporary server automatically and normally cleans it up on
exit. Supported lenses, codecs, sizes and frame rates depend on the phone.

## Local testing website (default)

~~~powershell
python scrcpy_backend.py
# Optional custom port:
python scrcpy_backend.py serve --port 8766
~~~

Open **http://127.0.0.1:8765** on the computer running Python. The Flask development
server binds to localhost with debugging and the reloader disabled. Ctrl+C stops
the server and its owned scrcpy session. There is one shared session across tabs.

The page uses a dark theme and provides:

- Device selection, camera/sizes/encoders/displays/apps discovery and diagnostics.
- Camera recording, preview, stop, and all camera/audio settings from the backend.
- Wireless pairing, connection and disconnection.
- Presets for screen mirroring, virtual displays, microphone recording and OTG.
- Searchable controls generated from every option in the installed scrcpy help
  (111 options in 4.1), plus additional arguments and full keyboard shortcuts.
- Live status/logs and a recording library with playback and Download / Save as.

Launching the test server automatically opens the connected phone's camera in
**preview-only mode**, initially at 1920x1080 / 30 FPS. It creates no recording
file. If the phone is unavailable or selection is ambiguous, the website still
opens; select a device and click Start preview. Apply preview settings restarts
the preview with the current form settings.

**Start recording** switches the preview to a recording session and starts saving
only then. **Stop recording** finalizes the file and returns to preview-only mode.
A successful timed recording also returns to preview automatically. Duration
limits apply to recording, not the continuous preview. **Stop camera** ends the
session entirely and keeps it off until you explicitly start it again or restart
the test server.

scrcpy chooses its recording destination at launch, so switching between preview
and recording briefly restarts the camera. The page holds the last image while
reconnecting and labels it as waiting for fresh frames. Preview-only footage is
not saved or included in the new recording. The LIVE indicator reflects recent
decoded frames; stale and stopped images are labelled explicitly.

The browser receives silent JPEG previews at up to 10 FPS, with the longest edge
limited to 960 pixels. The original encoded video and audio continue unchanged
to scrcpy's recorder. No growing recording file is reopened and no second phone
camera session is started. Enable **Also open desktop preview** if you want the
native scrcpy window and its keyboard controls as well.

This preview adapter currently requires **scrcpy 4.1**, PyAV and Pillow (installed
by `requirements-scrcpy.txt`). It relays scrcpy's local ADB tunnel, copying only
the video stream to a bounded decoding queue. Decoder failure or overload stops
the browser preview without stopping forwarding to the recorder. Full-resolution
decoding still uses CPU; preview conversion is reduced to 960 pixels / 10 FPS.
Native-option sessions retain their own scrcpy preview controls; the browser
feed currently applies to the Camera capture form.

Completed recordings still support browser playback when the selected container
and codec are supported, or download for a media player. The live preview has no
rewind or audio playback.

Once Start recording is pressed, files save under `web_driver_station/recordings/scrcpy-test/`.
Stop before playback or downloading. The website accepts simple filenames and
does not overwrite existing recordings, including native-mode recordings.
An unsuccessful capture can leave an empty or partial file for inspection.
Native mode uses scrcpy's own device rules. Platform-specific options remain
subject to scrcpy support.

`create_web_app(backend=None, recordings_dir=None)` exposes the isolated Flask app
for testing or later integration. No Driver Station application changes are needed.

## Discover all features

~~~powershell
python scrcpy_backend.py --help
python scrcpy_backend.py devices
python scrcpy_backend.py cameras --serial PHONE_SERIAL
python scrcpy_backend.py sizes --serial PHONE_SERIAL --camera-id 0
python scrcpy_backend.py encoders --serial PHONE_SERIAL
python scrcpy_backend.py displays --serial PHONE_SERIAL
python scrcpy_backend.py apps --serial PHONE_SERIAL
python scrcpy_backend.py preview --help
python scrcpy_backend.py record --help
python scrcpy_backend.py native-help
~~~

**native-help lists every option and keyboard shortcut in the installed scrcpy.**
The native command forwards arguments unchanged, including new upstream features.

Dedicated camera flags cover camera ID/facing, resolution or maximum size/aspect
ratio, FPS/high-speed mode, zoom, torch, orientation, video/audio codecs, bit
rates, encoders and codec parameters, microphone source, optional/required audio,
duration, fullscreen and always-on-top. Defaults: back camera, H.264 at 16 Mbps,
AAC microphone audio, and muted microphone playback on the laptop.

## Preview and record

~~~powershell
python scrcpy_backend.py preview --serial PHONE_SERIAL --size 1920x1080 --fps 30
python scrcpy_backend.py record --serial PHONE_SERIAL --output web_driver_station/recordings/scrcpy/demo.mp4 --size 1920x1080 --fps 30 --duration 10
python scrcpy_backend.py record --serial PHONE_SERIAL --output web_driver_station/recordings/scrcpy/with-preview.mkv --preview --no-audio
~~~

Recording is headless unless --preview is specified. --duration accepts whole seconds. Omit it to record
until **Ctrl+C**, or close the preview window. On Windows, a private hidden
console and scoped control event stop the process gracefully without sending
phone key presses or killing the shared ADB server.

The record command never overwrites an existing file. Failed recordings,
including empty reservations and partial files, remain for inspection. Use a new
filename for a retry. Video is saved directly on the laptop; no independent
phone copy exists.

Camera capture does not block Robot Controller roles, running RC apps, or Control
Hub models. Android 12+ and an authorized connection are still required. Camera
availability is determined by Android; the script does not stop the RC app.
With multiple authorized devices, specify --serial or ANDROID_SERIAL.

In the preview, the default modifier is left Alt or left Super:

- Modifier + Q: quit.
- Modifier + T / Modifier + Shift + T: torch on/off.
- Modifier + Up / Down: camera zoom.
- Modifier + F: fullscreen.
- Modifier + I: toggle FPS reports.
- Modifier + Z: pause the **display**, not recording.

## Complete native CLI access

~~~powershell
python scrcpy_backend.py native -- --serial=PHONE_SERIAL --video-source=display
python scrcpy_backend.py native -- --serial=PHONE_SERIAL --new-display=1920x1080
python scrcpy_backend.py native -- --serial=PHONE_SERIAL --no-video --audio-source=mic --record=audio.opus
python scrcpy_backend.py native -- --otg
~~~

Native mode exposes display mirroring/control, virtual displays, app launching,
clipboard, HID keyboard/mouse/gamepad, audio-only capture, networking, window
settings and all other upstream features. Platform/device restrictions apply;
V4L2 virtual webcam output is Linux-only.

**CLI native mode is direct execution.** It uses scrcpy's own device selection,
validation and overwrite behavior. It does not apply the camera compatibility
preflight, track output files or claim recording verification. Prefer record for
normal camera capture.

## Wireless

Enable Wireless debugging in Android Developer options. Pairing and connection
use different ports displayed in the phone settings:

~~~powershell
python scrcpy_backend.py pair PHONE_IP:PAIRING_PORT
python scrcpy_backend.py connect PHONE_IP:DEBUGGING_PORT
python scrcpy_backend.py cameras --serial PHONE_IP:DEBUGGING_PORT
python scrcpy_backend.py record --serial PHONE_IP:DEBUGGING_PORT --output wireless.mp4 --duration 10
python scrcpy_backend.py disconnect PHONE_IP:DEBUGGING_PORT
~~~

The pairing code is requested privately. The full native --tcpip workflow is
also available through native. Recording requires a continuous connection.
A disconnect retains received footage but cannot continue recording locally.

## Python API

~~~python
from scrcpy_backend import CameraOptions, ScrcpyBackend

with ScrcpyBackend() as camera:
    print(camera.devices())
    print(camera.inspect("sizes", serial="PHONE_SERIAL", camera_id="0"))
    camera.start_camera(
        CameraOptions(
            serial="PHONE_SERIAL", camera_id="0",
            size="1920x1080", fps=30, duration=10, audio=False,
        ),
        output="new-recording.mp4",
        preview=False,
    )
    print(camera.status())
    result = camera.wait(timeout=30)
    print(result)
~~~

- start_camera() launches asynchronously after preflight. Set `browser_preview=True`
  to decode a browser preview; `preview=False` suppresses the desktop window.
- preview_frame() returns the latest JPEG, or None before the first frame.
  status()["browser_preview"] reports freshness, errors and frame count.
- status() returns a JSON-serializable snapshot.
- wait(timeout) raises TimeoutError without stopping the process.
- stop(timeout=15) requests graceful shutdown and waits for finalization.
  Repeated calls are safe; forced termination is reported as failure.
- The context manager stops capture on exit.
- start_native(args) owns a process for arbitrary native scrcpy options.
- doctor(), devices(), inspect() and native_help() expose discovery.
- adb() invokes the selected ADB, including wireless connection operations.

Use separate instances for different cameras and explicitly select each phone.

States: IDLE, STARTING, RUNNING, STOPPING, COMPLETED, STOPPED, DISCONNECTED, FAILED.
COMPLETED requires a zero exit code, scrcpy's recording-finalization message and
a non-empty output. STOPPED describes a successful preview/native exit.
DISCONNECTED preserves scrcpy's disconnect exit code (2).
CLI logs go to stderr; final status is JSON on stdout. The last 100 log lines are
retained in status. Failed camera commands return nonzero exit codes.

**RUNNING does not guarantee video frames.** A "Recording started" message means
only that the output file opened. Positive preview FPS logs set
frame_activity_observed. frame_reports_stale is advisory after five seconds
without another positive report; pausing display or disabling FPS reports can
also cause it. Headless mode provides no FPS evidence: file growth is reported
separately and does not prove frame capture. Poll status() to observe growth.

scrcpy_finalized reflects the muxer's report, not independent decoding or hash
verification. Monotonic timestamps are laptop observations, not camera exposure
times. Automatic telemetry synchronization, reconnect, upload and deletion are
outside this standalone backend.

## Verification

~~~powershell
python -B -m unittest test_scrcpy_backend test_scrcpy_web test_scrcpy_preview -v
~~~

Tests launch a real simulated recorder subprocess to exercise graceful Windows
stop, finalization, duplicate starts, forced-stop timeout, disconnects and file
preservation without activating a phone camera.
Flask tests exercise start/stop, generated feature controls, file scoping,
downloads and byte ranges, request validation, and wireless errors.

Official references:

- [scrcpy](https://github.com/Genymobile/scrcpy)
- [Camera capture](https://github.com/Genymobile/scrcpy/blob/master/doc/camera.md)
- [Recording](https://github.com/Genymobile/scrcpy/blob/master/doc/recording.md)
- [Windows installation](https://github.com/Genymobile/scrcpy/blob/master/doc/windows.md)
