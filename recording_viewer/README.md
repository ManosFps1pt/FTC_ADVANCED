# FTC Recording Viewer

This is a separate, local React/TypeScript replay app for one saved FTC robot
data session. It reads `.ftclog` files directly; it does not connect to a
Control Hub or require the Driver Station to be running.

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
`video/field.mp4`. The viewer serves the original file; it does not use OpenCV,
duplicate frames, or re-encode it. The snapshot timeline is the master clock:

- A longer video is logically cut off when the final robot snapshot is reached.
- A shorter video becomes a black frame while snapshots continue through the
  end of the recording.
- A 30 FPS video and 100 Hz snapshots remain independent. On each display tick,
  the viewer selects the snapshot for the master-clock time while the browser
  presents video at its native cadence.

The first version treats each discovered MP4 as an independently selectable
clip beginning at recording time zero. Segment-to-timeline timestamp sidecars
and calibrated camera offsets will be added later.

## Recording library and upload

The hosted service treats `/srv/ftc-recordings` as a library root. Each direct
UUID-named child is one replay. The browser lists completed sessions first and
loads a selected session only when requested.

The laptop uploader transfers a finalized session over SFTP using an SSH key,
verifies the known host, uploads to `.incoming/<session-id>`, and renames the
remote directory only after every file completes. That final rename makes a
replay appear in the library.

Install its dependency in the laptop's web environment:

```powershell
.\.venv-web\Scripts\python.exe -m pip install -r .\web_driver_station\backend\requirements.txt
```

For an explicit retry/upload, run:

```powershell
.\.venv-web\Scripts\python.exe -m web_driver_station.backend.recording_uploader `
  .\web_driver_station\recordings\<session-uuid> `
  --host 80.225.93.186 --username ubuntu `
  --private-key '.\ssh-key-2026-08-20 (1).key'
```

For automatic upload, set `FTC_RECORDING_UPLOAD_HOST`,
`FTC_RECORDING_UPLOAD_USERNAME`, and `FTC_RECORDING_UPLOAD_PRIVATE_KEY` before
starting the Driver Station backend. Optional `FTC_RECORDING_UPLOAD_ROOT`,
`FTC_RECORDING_UPLOAD_PORT`, and `FTC_RECORDING_UPLOAD_KNOWN_HOSTS` override
their safe defaults. The SSH host must already be in the selected known-hosts
file; the uploader never silently trusts a new server key.

## Hosted HTTPS

`deploy/enable_ip_https.sh` obtains a trusted short-lived Let's Encrypt
certificate directly for the public IP, redirects HTTP to HTTPS, protects all
viewer routes with one bcrypt-hashed Basic Auth credential, and renews twice
daily. It leaves only the ACME validation path public on HTTP. Run it on the
Oracle instance after deployment:

```bash
bash /opt/ftc-recording-viewer/recording_viewer/deploy/enable_ip_https.sh 80.225.93.186
```
