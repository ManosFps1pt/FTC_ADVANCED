# FTC Recording Viewer

This is a separate, local React/TypeScript replay app for one saved FTC robot
data session. It reads `.ftclog` files directly; it does not connect to a
Control Hub or require the Driver Station to be running.

The Driver Station also serves its saved local sessions at
<http://127.0.0.1:8000/replays/>. A replay URL can include `t` in seconds
from the first sample alongside the `recording` session ID.

From the repository root, build the frontend once:

```powershell
cd .\recording_viewer\frontend
pnpm install
pnpm run build
cd ..\..
```

Then point the viewer at a saved session folder (or its `raw` folder):

```powershell
.\.venv-web\Scripts\python.exe -m recording_viewer.backend.main `
  --recording-dir .\web_driver_station\recordings\<session-uuid>
```

Open `http://127.0.0.1:8002`. The first version shows the full recorded
timeline, up to three numeric traces, recorded gamepad states, and all values
at the scrubber position. It is deliberately read-only, so adding analysis,
video, annotations, and export features later will not affect robot control.

## Video playback model

Put an MP4 anywhere inside the selected session folder, for example
`video/field.mp4`. Replay streams the original file using the snapshot timeline
as its master clock:

- A longer video is logically cut off when the final robot snapshot is reached.
- A shorter video becomes a black frame while snapshots continue through the
  end of the recording.
- A 30 FPS video and 100 Hz snapshots remain independent. On each display tick,
  the viewer selects the snapshot for the master-clock time while the browser
  presents video at its native cadence.

Each original MP4 is independently selectable. **Sync video** uses the recorded
RGB indicator commands and the visible flashes to calibrate the selected clip.
The signed delay is saved beside the original in `<clip-stem>.sync.json`.
The viewer skips footage outside the telemetry timeline and keeps the full set
of snapshots available for scrubbing. Use **Export trimmed MP4** to save the
overlapping footage as `<clip-stem>.synced.mp4`. Each successful export replaces
the previous trimmed copy for that clip. The server needs FFmpeg and the Python
dependencies in `recording_viewer/backend/requirements.txt`.

## Recording library and upload

The hosted service treats `/srv/ftc-recordings` as a library root. Each direct
UUID-named child is one replay. The browser lists completed sessions first and
loads a selected session only when requested. The library and replay page show
the title, notes, battery index, robot version, and OpMode type saved in the
Driver Station. Older recordings remain accessible by date and session ID, with
missing details shown as unknown.

The laptop uploader transfers a finalized session over SFTP using an SSH key,
verifies the known host, uploads to `.incoming/<session-id>`, and renames the
remote directory only after every file completes. That final rename makes a
replay appear in the library.

Install its dependency in the laptop's web environment:

```powershell
.\.venv-web\Scripts\python.exe -m pip install -r .\web_driver_station\backend\requirements.txt
```

After saving the recording details in the Driver Station, you can retry an
upload from PowerShell:

```powershell
.\.venv-web\Scripts\python.exe -m web_driver_station.backend.recording_uploader `
  .\web_driver_station\recordings\<session-uuid> `
  --host your-oracle-public-ip --username ubuntu `
  --private-key '.\your-upload-key.key'
```

For uploads from the Driver Station, copy `ftc_advanced.local.example.json` to the ignored
`ftc_advanced.local.json` file and fill in `recording_upload`. Environment
variables (`FTC_RECORDING_UPLOAD_*`) still override the local file. The SSH
host is verified against the configured `host_key_fingerprint`; the uploader
never silently trusts a new server key. The legacy `known_hosts` option remains
available for environments that need it.

## Hosted HTTPS

`deploy/enable_ip_https.sh` obtains a trusted short-lived Let's Encrypt
certificate directly for the public IP, redirects HTTP to HTTPS, protects all
viewer routes with one bcrypt-hashed Basic Auth credential, and renews twice
daily. It leaves only the ACME validation path public on HTTP. Run it on the
Oracle instance after deployment:

```bash
bash /opt/ftc-recording-viewer/recording_viewer/deploy/enable_ip_https.sh 80.225.93.186
```
