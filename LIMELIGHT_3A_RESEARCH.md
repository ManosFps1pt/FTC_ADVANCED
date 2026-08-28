# Limelight 3A: FTC networking, client access, and Python integration

> Research date: 2026-08-26. This note is for the Limelight 3A (USB-C FTC
> model), not the Ethernet-powered Limelight 3/4 models.

## Executive summary

The Limelight 3A is an onboard vision computer and camera. It performs image
capture and vision processing itself, then sends processed results to the
robot. On an FTC robot it connects to the REV Control Hub with USB-C, but that
link is **USB Ethernet**, not a normal UVC webcam feed. The camera runs DHCP
and gives the Control Hub an address on a private camera network. The FTC SDK
then exposes the supported `Limelight3A` device API to an OpMode.

For a Python dashboard or data-collection tool, the camera also has standard
HTTP services:

| Need | Interface | Usual address |
| --- | --- | --- |
| Configure pipelines / inspect image | Web UI | `http://<camera-host>:5801` |
| Read the latest processed result as JSON | REST | `http://<camera-host>:5807/results` |
| Read the live processed image | MJPEG stream | `http://<camera-host>:5800` |
| Run custom vision directly on the 3A | Python SnapScript | configured in the web UI |
| Use data inside FTC robot code | FTC SDK | `Limelight3A`, `LLResult`, `LLStatus` |

`<camera-host>` is normally `limelight.local` when a computer is directly
connected to the camera. This repository's connected 3A responded at
`172.28.0.1` on 2026-08-26. Limelight's published USB forwarding example uses
`172.29.0.1` for its first attached 3A. Do not hard-code either address in team
software without confirming it in the Limelight/Control Hub configuration.

### Live verification: connected 3A

The unit connected to this development computer was tested on 2026-08-26:

| Service | Verified address | Result |
| --- | --- | --- |
| Web UI | `http://172.28.0.1:5801/` | HTTP 200 |
| Vision results | `http://172.28.0.1:5807/results` | HTTP 200 and JSON response |
| Image stream | `http://172.28.0.1:5800` | OpenCV read a `640 × 480` BGR frame |

At the time of the test, the active pipeline ID was `9` and no valid target was
present. Those are live state values, not defaults to depend on in code.

## What the USB-C connection actually does

```text
Laptop (direct setup)                   Competition robot
---------------------                   -----------------
browser / Python                        Driver Station / laptop
        |                                        |
    USB-C Ethernet                              Wi-Fi
        |                                        |
 Limelight 3A <--DHCP--> Control Hub <--USB Ethernet--> Limelight 3A
        |                                        |
  172.28.0.1*                         FTC SDK Limelight3A API
```

\* This is the address of the 3A connected during this research. Treat it as a
useful development default, not a replacement for the IP shown in the Control
Hub configuration.

The FTC SDK's bundled `SensorLimelight3A` sample states that, when attached to
a Control Hub USB port, the 3A presents an Ethernet interface and its DHCP
server assigns the Control Hub an IP address. In the Robot Controller
configuration, the small value shown below the Limelight device name is the
**Control Hub's** address on this camera network, whereas the Limelight
device's IP is a separate setting. Those addresses are easy to mix up.

### Practical implications

- **Use one Limelight 3A per Control Hub.** The product page says a REV Control
  Hub can support only one 3A at a time.
- **The camera is not a webcam.** FTC `VisionPortal`/webcam APIs are not the
  normal integration point. Configure it as a `Limelight3A` device, then use
  its result API.
- **Direct laptop setup is the simplest way to use Python against the camera.**
  Plug the 3A directly into the laptop's USB-C/USB port (with an appropriate
  data cable/adapter), wait for its Ethernet device and DHCP lease, then open
  `http://limelight.local:5801`. If mDNS does not resolve, use the address
  found by the Limelight Finder or the computer's network adapter.
- **Do not assume the Driver Station Wi-Fi can route to the USB camera subnet.**
  When the 3A is attached to the Control Hub, the Hub itself can reach it, but
  an external dashboard needs an intentional route/proxy/port-forward or it
  should receive the selected results from robot code instead. Test the actual
  deployed network rather than relying on `172.29.0.1` from another machine.
- **For this repository's dashboard, publish selected `LLResult` fields from
  the robot to the existing telemetry transport.** This is the robust FTC
  architecture: the robot program owns the hardware connection and the
  dashboard consumes a compact, versioned data packet. Use direct camera HTTP
  only for pit-side tuning, recording, and development tools.
- **Bypass HTTP proxies for the camera's private IP.** A browser or `curl` may
  work while Python/OpenCV fails if `HTTP_PROXY`, `HTTPS_PROXY`, or `ALL_PROXY`
  point to a corporate/development proxy. During the live test, proxy routing
  redirected traffic to `127.0.0.1:9`; direct traffic to `172.28.0.1` worked.
  The repository's `limelight_preview.py` disables proxy use for REST discovery
  and temporarily removes those variables while it opens the MJPEG stream.

## What a connected client can access

### Vision results

The current FTC sample in this repository demonstrates these result families:

- General target data: validity, `tx`, `ty`, no-crosshair variants `txnc` and
  `tync`, target area, processing/capture latency, and robot pose (`botpose`).
- AprilTags/fiducials: ID, family, and target angles; pose/localization depends
  on a correctly configured camera pose and field/tag map.
- Neural detector results: class name, confidence/area, and target geometry.
- Classifier and barcode results.
- Color/contour results.
- SnapScript output: a numeric `llpython` array, read in FTC as
  `result.getPythonOutput()`.
- Health/status: name, temperature, CPU use, FPS, selected pipeline index/type.

The available fields depend on the active pipeline. Always gate control logic
on `result.isValid()` (or JSON `tv`) and include Limelight-reported latency
when timestamping localization data.

### Controls and configuration

The web UI provides pipeline selection and tuning, image display, calibration,
field/tag-map configuration, snapshot management, and pipeline import/export.
From FTC code, use the supported methods such as `pipelineSwitch(index)`,
`start()`, `pause()`, `stop()`, `setPollRateHz(...)`, and robot-orientation
updates when appropriate to the current SDK. Start polling before calling
`getLatestResult()`.

### Images

Port 5800 exposes an MJPEG stream. It is the right source for an external
snapshot, a recorder, or a development dashboard. The stream reflects the
selected pipeline's output image, so it can show overlays or a SnapScript's
annotated image rather than necessarily being a raw, unprocessed frame.

## Python: read the latest result JSON

Install the two dependencies in the Python environment that runs the tool:

```powershell
python -m pip install requests opencv-python
```

REST response shapes differ between LimelightOS releases. Some return a
top-level `Results` object and `tv`; the 3A tested for this repository returns
the result fields directly and uses `v` as the validity flag. Keep the complete
payload while exploring; its optional result lists also vary by pipeline and
LimelightOS version.

```python
from __future__ import annotations

import json
from typing import Any

import requests

HOST = "172.28.0.1"  # This repository's connected 3A; direct setup can use "limelight.local".
RESULTS_URL = f"http://{HOST}:5807/results"
DIRECT_SESSION = requests.Session()
DIRECT_SESSION.trust_env = False  # Do not send the private camera IP to HTTP_PROXY.


def get_latest_results() -> dict[str, Any]:
    response = DIRECT_SESSION.get(RESULTS_URL, timeout=0.25)
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload.get("Results", payload)


results = get_latest_results()
if not results.get("tv", results.get("v", False)):
    print("No valid target in the current frame")
else:
    print(json.dumps({
        "tx_degrees": results.get("tx"),
        "ty_degrees": results.get("ty"),
        "target_area_percent": results.get("ta"),
        "pipeline_latency_ms": results.get("tl"),
        "capture_latency_ms": results.get("cl"),
        "botpose": results.get("botpose"),
        "fiducials": results.get("Fiducial", []),
        "detectors": results.get("Detector", []),
    }, indent=2))
```

Notes:

- Validity is normally numeric (`0`/`1`): use `tv` when present, otherwise `v`
  for the direct-result response shape. Python treats either correctly as
  false/true.
- Do not blindly map all JSON keys to a fixed schema. A color pipeline,
  AprilTag pipeline, and neural-detector pipeline produce different optional
  fields.
- Use a short timeout and handle `requests.RequestException` in a continuously
  running dashboard. A camera restart or USB disconnect should become a clear
  offline state, not a crashed dashboard.
- Setting `DIRECT_SESSION.trust_env = False` is important on machines with a
  configured HTTP proxy. Without it, `requests` can try the proxy rather than
  the directly attached camera.
- Polling REST is well-suited to a dashboard. The FTC SDK API is the preferred
  path for real-time robot decisions.

## Python: save one image snapshot

OpenCV can consume Limelight's MJPEG stream and save the next frame. This is a
client-side capture; it does not create a managed snapshot inside the
Limelight web UI.

```python
from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path

# Prevent OpenCV/FFmpeg from attempting to use a system HTTP proxy for the
# directly attached Limelight. Do this before opening the stream.
for proxy_name in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY",
                   "all_proxy", "http_proxy", "https_proxy"):
    os.environ.pop(proxy_name, None)

import cv2

HOST = "172.28.0.1"  # This repository's connected 3A; or "limelight.local" during direct setup.
STREAM_URL = f"http://{HOST}:5800"
OUTPUT_DIR = Path("limelight_captures")


def save_snapshot() -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    capture = cv2.VideoCapture(STREAM_URL)
    try:
        if not capture.isOpened():
            raise RuntimeError(f"Cannot open Limelight stream: {STREAM_URL}")

        # Discard a few frames so an HTTP/MJPEG connection has settled.
        frame = None
        for _ in range(10):
            ok, frame = capture.read()
            if ok and frame is not None:
                break
        if frame is None:
            raise RuntimeError("Limelight stream opened but supplied no frame")

        filename = datetime.now().strftime("limelight_%Y%m%d_%H%M%S_%f.jpg")
        destination = OUTPUT_DIR / filename
        if not cv2.imwrite(str(destination), frame):
            raise RuntimeError(f"Could not write {destination}")
        return destination
    finally:
        capture.release()


print(f"Saved {save_snapshot()}")
```

For a high-rate recorder, keep a single `VideoCapture` instance open in a
worker thread and pass the most recent frame to the UI. Reopening the stream
for every frame adds latency and unnecessary connection churn.

## Python SnapScript: run vision *on* the Limelight

This is a separate Python use case. A SnapScript runs inside LimelightOS, not
on the laptop or Control Hub. Set the pipeline type to **Python SnapScript** in
the web UI. Limelight invokes `runPipeline(image, llrobot)` every frame. It
receives an OpenCV BGR image and the robot-provided numeric array, and returns:

1. A contour for Limelight's normal crosshair/target values.
2. The image to display/stream.
3. Up to eight numeric values returned to the robot as `llpython` / FTC
   `getPythonOutput()`.

```python
import cv2
import numpy as np


def runPipeline(image, llrobot):
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (20, 90, 70), (40, 255, 255))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)

    largest = np.array([[]])
    llpython = [0.0] * 8
    if contours:
        largest = max(contours, key=cv2.contourArea)
        x, y, width, height = cv2.boundingRect(largest)
        cv2.rectangle(image, (x, y), (x + width, y + height),
                      (0, 255, 0), 2)
        llpython = [1.0, float(x), float(y), float(width), float(height),
                    0.0, 0.0, 0.0]

    return largest, image, llpython
```

This is ideal for robot-time image processing: it avoids sending full images
to the Control Hub and keeps only the useful numbers on the control path.
Avoid `cv2.imshow`, local file I/O, network calls, or expensive model loading
inside `runPipeline`; it runs once per camera frame.

## Recommended workflow for this project

1. Update the Control Hub/Driver Station and use the current FTC SDK (this
   workspace is currently based on SDK 11.2.1).
2. Connect the 3A to a Control Hub USB port and add it as a `Limelight3A` in
   the Robot Controller configuration. Give it a stable hardware-map name.
3. Start with the SDK's bundled `SensorLimelight3A` sample to verify status,
   valid results, and the pipeline before integrating autonomous logic.
4. Configure/calibrate an AprilTag or color pipeline in the Limelight web UI;
   export the pipeline files into version control after meaningful changes.
5. Publish a deliberately small robot-side vision packet to the dashboard:
   `valid`, timestamp, latencies, pipeline index, target data, fiducial IDs,
   pose, and selected `llpython` values. Include an explicit `online` state.
6. Use the Python REST/MJPEG examples only on a computer that can actually
   route to the camera. They are excellent for pit tools, image capture, and
   offline dataset collection.

## Robot Controller communication-test bridge

The first implementation in this repository is deliberately read-only and has
no frontend. `LimelightBridge.java` adds two routes to the Robot Controller's normal
web server, so a laptop can test the USB-Ethernet path without connecting
directly to the camera:

| Laptop URL | Purpose |
| --- | --- |
| `http://192.168.49.1:8080/api/limelight/health` | Small normalized status response: connectivity, pipeline, target values, and latency, for the phone-based Robot Controller in this setup. |
| `http://192.168.49.1:8080/api/limelight/results` | Complete Limelight REST response wrapped in a bridge envelope. |

For a Control Hub, replace `192.168.49.1` with `192.168.43.1`.

Deploy TeamCode, connect the laptop to the Control Hub Wi-Fi, then run:

```powershell
python .\limelight_control_hub_test.py --watch
```

The script does not need third-party packages. A successful request proves the
full path `laptop → Robot Controller → Limelight → Robot Controller → laptop`.

The currently shown Robot Controller configuration uses `eth0:172.29.0.22`
and an Ethernet-device target of `172.29.0.1`; TeamCode therefore uses
`172.29.0.1` as `CAMERA_HOST`. The screenshot labels the configuration
`(unsaved) test_conf`: tap **Done**, then **Save**, so this address persists
after a restart. If the 3A is configured with a different address, update
`CAMERA_HOST` in `LimelightBridge.java`.

## Sources

- [Limelight 3A product page](https://limelightvision.io/products/limelight-3a)
  — hardware/software capabilities and the one-3A Control Hub limitation.
- [Limelight documentation home](https://docs.limelightvision.io/) — official
  platform protocols and SnapScript capability overview.
- [Official FTC `SensorLimelight3A` sample](https://github.com/FIRST-Tech-Challenge/FtcRobotController/blob/master/FtcRobotController/src/main/java/org/firstinspires/ftc/robotcontroller/external/samples/SensorLimelight3A.java)
  — USB-Ethernet/DHCP behavior and FTC result/status APIs.
- Local copy of that sample:
  [`SensorLimelight3A.java`](FtcRobotController/FtcRobotController/src/main/java/org/firstinspires/ftc/robotcontroller/external/samples/SensorLimelight3A.java).
- [Limelight's USB port-forwarding implementation](https://github.com/LimelightVision/limelightlib-wpijava/blob/main/LimelightHelpers.java)
  — published `172.29.<usbIndex>.1` USB-camera addressing convention and
  5800–5809 service ports.
