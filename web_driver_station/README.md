# Local web Driver Station dashboard

This is a local FastAPI and TypeScript replacement for the CustomTkinter test
window. It keeps Robocol, heartbeats, and gamepad transmission in Python; the
browser only sends desired gamepad state and renders telemetry.

Use it only on a controlled test robot. It is not an FTC event-legal Driver
Station and the browser must never be exposed beyond the local computer.

## Development

The easiest Windows launch is to double-click
`run_web_driver_station.bat` in the repository root. It prepares the local
environment, builds the dashboard, starts the local server, and opens the
browser. Python 3.11+ and Node.js LTS with pnpm are required the first time.

From the repository root, create a virtual environment and install the Python
dependencies:

```powershell
python -m venv .venv-web
.\.venv-web\Scripts\python.exe -m pip install -r .\web_driver_station\backend\requirements.txt
```

Install the frontend dependencies (Node.js LTS and pnpm are required):

```powershell
cd .\web_driver_station\frontend
pnpm install
pnpm run build
cd ..\..
```

Then run one local process and open `http://127.0.0.1:8000` in a browser:

```powershell
.\.venv-web\Scripts\python.exe -m uvicorn web_driver_station.backend.main:app --host 127.0.0.1 --port 8000
```

For frontend development, run the backend command above and `pnpm run dev` in
`frontend`; the Vite server proxies `/api` and `/ws` to the backend.

The dashboard lets the operator connect to a Robot Controller, list and start
an OpMode, switch between virtual gamepad slots, view telemetry, and stop the
OpMode. A physical USB or Bluetooth gamepad is read directly by the browser;
no extra Python or Node process is needed. When at least one physical gamepad
is connected, virtual controls are hidden. Hold **Start + A** to assign that
controller to Driver 1, or **Start + B** to assign it to Driver 2. The driver
status strip shows which slots are active. Closing the browser is not a
guaranteed stop action, so use the Stop button before closing it.

## Robot configuration

The **Robot configuration** panel lists the RC's configuration files and loads
the selected file as its canonical FTC XML. The XML editor is intentional: it
preserves hardware definitions and custom-device attributes that this dashboard
does not yet understand. Use **Save & activate** to write the XML to the RC and
make that configuration active. The backend rejects saves while an OpMode is
active or the RC is not in `STOPPED`/`NOT_STARTED`, validates the XML before it
is sent, requires a confirmation in the browser, and does not overwrite
read-only SDK templates. Saving a configuration can change the active hardware
map, so test this feature with the robot disabled or safely supported first.
