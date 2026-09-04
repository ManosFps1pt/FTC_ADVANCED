# Native Android Camera Recording over ADB

**Project:** FTC Advanced local Driver Station  
**Primary test device:** Samsung Galaxy S25 (SM-S931B)  
**Status:** Agreed architecture; implementation not started  
**Updated:** September 1, 2026

## Decision

Use the phone's native camera application for recording and control it from the laptop through wired ADB.

This design preserves Samsung Pro Video features such as manual ISO, shutter speed, focus, white balance, high-frame-rate modes, native stabilization, microphone options, and Samsung's camera processing. The original stays on the phone until the laptop copy is verified and the user approves phone deletion. The laptop copy stays until the complete run is verified on Oracle and the user approves local deletion.

No phone-side software installation is required.

## Validated proof of concept

The complete basic workflow has been tested successfully on the Galaxy S25:

1. ADB detected the phone over USB.
2. ADB woke the screen and dismissed a non-secure keyguard.
3. The normal Samsung Camera activity opened directly in Pro Video mode.
4. An injected Volume Up event started recording.
5. A second Volume Up event stopped recording after five seconds.
6. Samsung Camera finalized a new MP4 in `DCIM/Camera`.
7. The laptop pulled the MP4 through ADB.
8. Phone and laptop SHA-256 hashes matched.

Test result:

- Duration: 5.273 seconds
- Resolution: 1920 × 1080
- File size: 21,886,409 bytes
- Native mode at test time: Pro Video, FHD 30
- Phone original retained: yes

This proves the chosen workflow on the current S25 configuration. Other Android devices must pass the calibration described below because native camera applications differ between manufacturers.

## Requirements

### Device and software requirements

- The camera is an Android phone connected to the laptop through wired, authorized ADB.
- A REV Control Hub or a phone running the FTC Robot Controller application may also be connected through ADB.
- The backend must support multiple simultaneous ADB devices.
- The camera phone must use its preinstalled native camera application.
- No additional phone software may be required.
- Any Android phone may be used after it passes a one-time native-camera calibration.

### Role requirements

- The backend must distinguish Robot Controller devices from camera devices.
- A phone with an active FTC Robot Controller service or activity is a Robot Controller, not a camera.
- If that phone is the only ADB device, it must still be classified as the Robot Controller.
- Merely having the FTC Robot Controller package installed must not reserve a phone as the RC; the application must be active or the device must be recognizable as Control Hub hardware.
- Ambiguous devices must remain unassigned until the operator chooses a role.
- Confirmed role assignments must persist across disconnects and backend restarts.
- A single device must never receive RC-management and camera-control commands concurrently.
- Every ADB command must explicitly target the selected device serial.

### Recording requirements

- Recording must occur inside the native camera application.
- The operator controls ISO, shutter, exposure, focus, white balance, lens, resolution, frame rate, stabilization, Log/HDR, and audio settings on the phone.
- The backend may launch the camera, start recording, stop recording, detect the finalized file, and copy it.
- The native camera should reopen in its last configured video or Pro Video mode when the OEM supports that setting.
- The backend must not claim to know Pro Video settings that Android does not expose reliably.
- The recording must remain on the phone if ADB disconnects.
- The backend must preserve the phone original until a laptop copy has been completely verified.
- After laptop verification, the user must be offered a popup to delete or keep the phone original.
- Phone deletion must occur only after explicit approval for that exact recording.
- The laptop video must be retained for the future telemetry-based processing stage.
- Processing is a future feature and is not specified in this document beyond its place in the lifecycle.
- After processing is complete, the complete run folder must upload to the Oracle recording library using the existing `.ftclog` folder workflow.
- The laptop copy must remain until Oracle publication has been verified.
- After Oracle verification, the user must be offered a second popup to delete or keep the laptop run copy.
- Neither phone nor laptop deletion may happen automatically or be implied by dismissing a popup.
- Recordings must be associated with the corresponding robot run and telemetry session.

### UX and safety requirements

- Device assignment should normally require no action after first setup.
- The UI must clearly distinguish Ready, Recording, Finalizing, Copying, Laptop verified, Phone cleanup pending, Processing pending, Uploading, Oracle verified, Laptop cleanup pending, Disconnected, and Error states.
- Secure phone unlock must remain a user action; the backend must not attempt to bypass a PIN, password, or biometric challenge.
- Robot Stop and gamepad neutralization must never wait for camera finalization or file transfer.
- The UI must not show camera controls for a device currently reserved as an RC.
- Errors must give a specific recovery action such as unlock the phone, open Pro Video, reconnect USB, free storage, or retry import.

## ADB and Robocol are separate connections

ADB is the device-management and camera-control connection. It provides device discovery, shell commands, key injection, screenshots, file metadata, hashes, and file transfer.

The existing Robot Controller session still uses Robocol over the robot network. Standard ADB does not forward Robocol's bidirectional UDP traffic. A healthy ADB connection therefore does not mean that the Driver Station is connected to the Robot Controller.

The backend should expose these as independent statuses:

- **Robot:** Robocol connection and robot lifecycle state
- **RC device:** optional ADB management connection to the RC hardware
- **Camera:** ADB connection and native-camera recording state
- **Robot data:** independent telemetry TCP connection

The current `ControlHubClient` and `AdbUsbClient` already establish this conceptual separation. The new camera service should preserve it.

## Device discovery and role assignment

### Device record

For each ADB transport, the backend should maintain:

- ADB serial;
- connection state: authorized, unauthorized, offline, booting, or ready;
- transport type;
- manufacturer, model, product, and Android version;
- current Android user;
- whether the device appears to be REV Control Hub hardware;
- whether `com.qualcomm.ftcrobotcontroller` is installed;
- whether an FTC Robot Controller activity or service is active;
- whether Android reports camera hardware;
- remembered user assignment; and
- effective role and the evidence that produced it.

The wired ADB serial is the persistent identity key. Display a friendly model name and only a short serial suffix in the normal UI.

### Strong RC evidence

Treat either of the following as strong Robot Controller evidence:

- manufacturer/model properties identify REV Control Hub hardware; or
- the FTC Robot Controller foreground activity or long-lived controller service is active.

Do not use package installation by itself. A camera phone may have the RC application installed but inactive.

Process presence by itself may also be insufficient because Android can retain a cached process. Prefer an active service or foreground/resumed activity, using process state only as supporting evidence.

### Role resolution order

Resolve effective roles in this order:

1. Active FTC RC service/activity reserves the device as `ROBOT_CONTROLLER`.
2. Recognized REV Control Hub hardware is `ROBOT_CONTROLLER`.
3. Apply a previously confirmed role stored for the wired serial.
4. If exactly one strong RC and one other camera-capable device exist, present the second device as the camera candidate.
5. Leave all other ambiguous devices `UNASSIGNED`.

An active RC signal is a safety override. If a phone remembered as a camera later starts the RC application, suspend its camera role immediately. Do not launch Camera or inject keys into it. Its remembered camera assignment can become available again after the RC service stops.

### Expected outcomes

| Connected devices | Effective outcome |
|---|---|
| Control Hub only | Control Hub is RC; no camera |
| One phone with active RC app | Phone is RC; no camera |
| One previously confirmed camera phone, RC inactive | Phone is camera |
| Control Hub plus one ordinary phone | Hub is RC; phone is camera candidate |
| Two phones with no strong evidence | Both unassigned until the operator chooses |
| Two devices with active RC services | Conflict; do not infer a camera |
| Remembered camera starts the RC app | Camera role suspended; device becomes RC-reserved |

### Persistent assignment

Store a local role registry containing:

- stable wired ADB serial;
- friendly device name;
- confirmed preferred role;
- camera profile identifier, if calibrated;
- last-seen identity information; and
- last successful calibration/build versions.

Transient connection state must not be stored as configuration. A disconnected device remains remembered but is reported offline.

Reject role changes while the selected camera is recording, finalizing, or importing. Reject camera assignment while strong RC evidence is active.

### ADB targeting invariant

Every operation must include the exact device serial: discovery follow-up, property query, wake, keyguard check, camera launch, screenshot, key injection, MediaStore query, hashing, and pull.

Never rely on ADB's single-device default, even when only one device is currently present. This prevents commands from switching targets when a second device connects between operations. Android's official documentation also requires an explicit serial when multiple devices are attached: [Android Debug Bridge](https://developer.android.com/tools/adb).

## Zero-install native-camera calibration

Native Android camera applications do not share one guaranteed package, activity, video-mode layout, volume-button behavior, or output location. A one-time calibration turns those OEM-specific facts into a remembered device profile.

The camera profile should contain:

- ADB serial and friendly name;
- native camera package and resolved launch activity;
- launch strategy;
- working record key, normally Volume Up or Volume Down;
- media collection or expected output directory;
- last successful test result;
- Android build fingerprint;
- camera application version; and
- whether manual unlock or mode confirmation is normally required.

### Test camera workflow

1. Confirm that the device is camera-capable and not RC-active.
2. Wake the phone.
3. If secure unlock is required, tell the operator to unlock it.
4. Launch the normal native camera application, not the generic `ACTION_VIDEO_CAPTURE` flow.
5. Ask the operator to select and configure the desired video or Pro Video mode.
6. Ask the operator to configure the volume button to record video when required by the OEM.
7. Snapshot the latest MediaStore video IDs and metadata.
8. Inject the candidate record key.
9. Wait briefly and inject the same key again.
10. Detect the new finalized video.
11. Copy it to a temporary laptop location and compare phone/laptop hashes.
12. Save the profile only after the test succeeds.

Recalibration should be requested if the Android build or native camera application version changes, or if a later start/stop test fails.

## Backend architecture

### ADB device manager

Add a long-lived device manager responsible for:

- monitoring devices as they connect, disconnect, authorize, reboot, or become offline;
- collecting identity and role evidence;
- maintaining one serial-bound session per device;
- publishing device changes to the frontend; and
- enforcing command timeouts.

The existing ADB helper assumes one selected device. It should be refactored into an inventory plus serial-bound device sessions. A per-device command queue must serialize camera launch, start/stop, status probes, hashing, and transfer so they cannot race.

ADB subprocess work must not block FastAPI's event loop.

### Role resolver

The role resolver combines live evidence with the persistent registry. It should return both the effective role and human-readable reasons, for example:

- `Robot Controller — REV Control Hub hardware`
- `Robot Controller — FTC RC service active`
- `Camera — remembered assignment`
- `Camera candidate — only non-RC camera device`
- `Unassigned — two ambiguous Android phones`
- `Conflict — multiple active Robot Controllers`

The frontend should display these reasons rather than presenting the result as unexplained automation.

### Native camera recorder

Implement a dedicated Android native-camera recorder that owns the serial-bound ADB recording lifecycle.

Recommended state model:

```text
DISCONNECTED
NEEDS_AUTHORIZATION
NEEDS_UNLOCK
NEEDS_SETUP
READY
STARTING
RECORDING_COMMAND_SENT
RECORDING
RECORDING_UNVERIFIED
STOPPING
FINALIZING
IMPORTING
LAPTOP_VERIFIED
ERROR
```

ADB key injection has no official native-camera acknowledgement. The backend must distinguish **command sent** from **recording confirmed**. A calibrated device profile may confirm recording from an OEM-specific UI state or a growing temporary media file. If no reliable evidence exists, report `RECORDING_UNVERIFIED` instead of claiming certainty.

### Preparation

Before recording:

1. Verify the selected serial is connected and authorized.
2. Re-run the RC-active safety check.
3. Verify Android has completed booting.
4. Check secure-keyguard state.
5. Read battery level and available storage.
6. Resolve the calibrated native camera activity.
7. Snapshot recent MediaStore videos.
8. Launch the native camera.
9. Confirm that the expected video mode is visible or ask the operator to confirm it.

`READY` means the ADB and camera-control path is ready. It does not mean that the backend has read or verified ISO, shutter, focus, frame rate, or other Pro Video values.

### Start

To start recording:

1. Revalidate the serial and effective camera role.
2. Ensure the native camera remains foreground.
3. Record the laptop wall-clock and monotonic command timestamps.
4. Inject the calibrated record key.
5. Attempt recording confirmation using the device profile.
6. Start a maximum-duration watchdog in the backend.

If ADB disconnects after start, assume that the phone may still be recording. Show `Recording — device disconnected`; do not claim that recording stopped.

### Stop and finalization

To stop recording:

1. Revalidate that the same serial is still selected.
2. Inject the calibrated stop key.
3. Wait for the native camera to return to its idle state when detectable.
4. Poll for a new finalized MediaStore video entry.
5. Require stable file size and nonzero duration.
6. Record stop/finalization timestamps.

Android MediaStore exposes the video's display name, duration, size, width, height, and media timestamps: [MediaStore.Video.Media](https://developer.android.com/reference/android/provider/MediaStore.Video.Media.html).

### Import and verification

1. Select the new video by comparing its MediaStore ID and timestamps against the pre-start snapshot.
2. Pull it into a `.partial` path under the run directory.
3. Preserve the partial file if the transfer fails, but retry from a known-safe transfer boundary.
4. Calculate SHA-256 on the phone and laptop.
5. Compare file size and hash.
6. Atomically rename the local file after verification.
7. Write the final manifest.
8. Mark the laptop copy `LAPTOP_VERIFIED`.
9. Present the phone-retention popup.
10. Keep the phone original unless the user explicitly approves deletion.

### Phone deletion gate

The first deletion decision appears only after the laptop copy has passed size and SHA-256 verification.

Popup:

**Video safely copied to the laptop**

- **Delete from phone**
- **Keep on phone**
- **Decide later**

Before deleting, revalidate all of the following:

- the same camera ADB serial is connected;
- the stored MediaStore identity/path still refers to a video;
- filename, size, duration, and SHA-256 still match the imported recording; and
- the verified laptop file still exists and has the expected hash.

Delete only the exact recorded item. Never use a wildcard or delete the camera directory. Afterward, verify that the phone file is absent and update or remove its MediaStore row as required by the device.

If deletion fails or ADB disconnects, retain the verified laptop file, keep the decision pending, and offer retry. Selecting **Keep on phone** or **Decide later** must not block processing or upload.

## Processing and Oracle upload lifecycle

Telemetry-based video processing is intentionally deferred. The current design reserves its lifecycle position without specifying algorithms, outputs, or synchronization behavior.

The custody flow is:

```text
PHONE_RECORDING
  -> LAPTOP_IMPORT_PARTIAL
  -> LAPTOP_VERIFIED
  -> PHONE_DELETE_DECISION
  -> PROCESSING_PENDING
  -> PROCESSING_COMPLETE
  -> READY_TO_UPLOAD
  -> ORACLE_UPLOAD_STAGING
  -> ORACLE_VERIFIED
  -> LAPTOP_DELETE_DECISION
```

Phone retention is independent of later work. The run may proceed whether the user deletes or keeps the phone original.

### Run-folder upload

Upload the complete run folder as one unit, including finalized telemetry logs, the imported video, manifests, and future processed outputs. This matches the existing `.ftclog` recording-folder model and avoids publishing telemetry and video as unrelated recordings.

Upload requirements:

1. Refuse upload while any recording, import, or processing file is partial.
2. Upload into Oracle's `.incoming/<run-id>` staging directory.
3. Resume files that have a verified safe remote prefix or restart that file cleanly.
4. Verify every remote file's size and the video's SHA-256.
5. Atomically publish the staging directory as the final run directory.
6. Treat the upload as successful only after the final Oracle directory is visible and verified.
7. Persist the Oracle verification result before offering laptop deletion.

An interrupted or failed upload leaves the laptop run untouched and remains retryable. A run already present and verified on Oracle should be treated idempotently as uploaded.

### Laptop deletion gate

The second deletion decision appears only after Oracle publication is verified.

Popup:

**Run safely uploaded to Oracle**

- **Delete laptop copy**
- **Keep laptop copy**
- **Decide later**

Deletion should target the exact verified local run directory, matching the existing `.ftclog` upload behavior. Revalidate the run ID, local path, Oracle publication record, and expected manifest before deletion. Never delete a parent recordings directory, use a wildcard, or infer a target from the most recent folder.

If the user keeps or defers the laptop copy, the run remains complete and uploaded. Pending retention decisions must survive browser closure and backend restart and reappear non-destructively later.

### Required change to the existing upload workflow

The current telemetry upload flow asks **Upload · remove laptop copy** before upload and performs local cleanup immediately after success. It also exposes **Discard both copies**, which removes both the remote and local recording. Replace that combined behavior for run folders:

1. **Upload to Oracle** performs upload and verification only. It never deletes local data.
2. Successful publication transitions to `ORACLE_VERIFIED` and creates a separate pending laptop-retention decision.
3. **Delete laptop copy** is a distinct endpoint/action allowed only from `ORACLE_VERIFIED` after a new confirmation.
4. Normal laptop cleanup deletes only the exact local run. It never deletes the Oracle copy.
5. Remote deletion, if retained for administration, must be a separate exceptional operation and not part of the ordinary recording popup.

This separation makes the two guarantees independently auditable: Oracle publication succeeded, and the user later authorized local cleanup.

The manifest should contain:

- backend run ID;
- camera serial, manufacturer, and model;
- Android build and camera application version;
- camera profile and injected key;
- requested, sent, and observed lifecycle timestamps;
- original phone path or MediaStore identity;
- duration, resolution, MIME type, and size;
- phone and laptop SHA-256; and
- warnings such as unverified start state or an ADB interruption;
- phone-retention decision and deletion result;
- processing state;
- Oracle upload/publication verification; and
- laptop-retention decision and deletion result.

Recommended layout:

```text
recordings/<run-id>/
|-- run-manifest.json
|-- raw/
|   `-- stream-00000.ftclog
`-- video/
    `-- <camera-id>/
        |-- recording.mp4
        `-- manifest.json
```

## Run and OpMode coordination

The Robocol lifecycle, robot-data telemetry session, camera recording, and upload flow currently have separate identities. Add a backend-generated `run_id` that groups them without assuming their session IDs are identical.

The run manifest should map:

- OpMode name;
- RC host and optional RC ADB serial;
- robot-data session ID;
- `.ftclog` stream files;
- selected camera serial;
- MP4 and camera manifest;
- lifecycle timestamps; and
- phone and laptop custody state;
- processing state; and
- Oracle upload/publication state.

### Automatic recording sequence

1. **Init** prepares the run ID and performs camera preflight.
2. **Start + record** triggers camera recording first.
3. After a short configurable lead-in, start the OpMode.
4. Preserve the camera and robot start timestamps for later synchronization.
5. **Stop** immediately stops the OpMode and clears both gamepads.
6. Trigger camera stop after the robot stop command.
7. Finalize and import the MP4 asynchronously.
8. Offer the phone deletion decision after laptop verification.
9. Leave the run waiting for the future processing stage.
10. After processing, upload the complete run to Oracle.
11. Offer the laptop deletion decision after Oracle verification.

The recording itself is complete after import verification. Retention decisions, processing, and Oracle upload are separate durable substates so choosing **Keep** or **Decide later** does not leave the camera recorder falsely active.

Robot safety actions must never wait for camera operations. Emergency Stop always proceeds even when the camera is unavailable or ADB is stalled.

### Recording policies

- **Off:** no automatic camera action.
- **Best effort:** start the OpMode even if camera preparation or recording fails; show a prominent warning.
- **Required:** refuse normal OpMode Start until the selected camera passes preflight.

Emergency Stop bypasses every policy.

## Backend API and events

The backend should expose operations to:

- list detected ADB devices and their role evidence;
- assign, rename, or ignore a device;
- calibrate/test a native camera;
- request a one-off framing screenshot;
- prepare, start, stop, and recover recording;
- obtain current camera and run status;
- retry an interrupted import; and
- submit the confirmed phone-retention decision;
- retry the complete run-folder Oracle upload;
- submit the confirmed laptop-retention decision; and
- list pending decisions after a browser or backend restart.

Publish ADB-device and camera-state changes through a dedicated WebSocket. Do not mix frequent discovery/import progress with the existing Robocol status stream.

## Frontend UX

### Device assignment

Show an **Android devices** setup panel only when attention is required. Each device card should contain:

- friendly model name and short serial suffix;
- USB, authorization, and online state;
- evidence such as `REV Control Hub` or `FTC RC active`;
- effective role and explanation; and
- allowed actions: **Use as RC**, **Use as camera**, or **Ignore**.

High-confidence RC classification should happen automatically. A message such as **Galaxy S25 reserved as Robot Controller — FTC RC service is active** makes the decision understandable. Do not render camera controls for that device.

Ambiguous assignments should require one choice and then persist. Reconnecting known devices should not reopen setup.

### Camera status

Add a camera pill beside the existing robot, TCP, and driver indicators:

- `Camera · Not selected`
- `Camera · Unauthorized`
- `Camera · Unlock phone`
- `Camera · Check Pro Video`
- `Camera · Ready`
- `Camera · Starting`
- `Camera · Recording 00:23`
- `Camera · Recording unverified`
- `Camera · Device disconnected`
- `Camera · Finalizing`
- `Camera · Copying 54%`
- `Camera · Laptop verified`
- `Camera · Phone cleanup pending`
- `Camera · Error`

Expanding the pill should show:

- device model and USB status;
- battery and free storage;
- native camera application;
- last calibration result;
- current automatic-recording policy;
- newest error and recovery action;
- **Test camera**;
- **Check framing**; and
- manual **Record** or **Stop**.

The UI must state that Pro Video exposure, ISO, shutter, focus, lens, frame rate, stabilization, and audio settings are controlled on the phone.

### Framing and setup

Use **Check framing** to request a one-off ADB screenshot. Do not add a continuous preview initially. A permanent preview increases USB load and privacy exposure without improving the reliability of the native recording.

Perform camera preflight during **Init**, not when the operator is already trying to Start. This leaves time to unlock the phone, choose Pro Video, correct framing, or free storage.

When automatic recording is enabled, label the lifecycle action **Start + record** and show a persistent red timer while recording. On Stop, return robot controls to the stopped state immediately and show finalization/import progress in the background.

The run/recording area should then show the post-capture stages independently of the camera pill:

- `Video · Verified on laptop`
- `Phone copy · Decision pending / Kept / Deleted`
- `Processing · Pending / Running / Complete` (future)
- `Oracle · Ready / Uploading / Verified / Error`
- `Laptop copy · Decision pending / Kept / Deleted`

Show transfer and upload progress inline. The operator should be free to keep using the Driver Station while either operation continues.

Do not use modal dialogs for normal progress. Reserve them for:

- ambiguous role assignment;
- Required-policy camera failure before Start; and
- the verified phone-copy deletion decision; and
- the verified laptop-copy deletion decision.

The two retention popups are independent and appear only when their prerequisites are satisfied. Closing either popup is equivalent to **Decide later**, never approval. The backend must persist the pending decision and show it again from a recordings/decisions area.

Use actionable errors rather than generic failures:

- **Unlock the camera phone**
- **Open Pro Video and confirm settings**
- **Reconnect the USB cable**
- **Authorize this computer on the phone**
- **Free at least 3.2 GB**
- **Retry video import**
- **Retry phone deletion**
- **Retry Oracle upload**
- **Retry laptop cleanup**
- **FTC Robot Controller is active; this device cannot be the camera**

## Implementation order

1. Multi-device ADB inventory and serial-bound command sessions.
2. RC evidence probes, role resolver, and persistent role registry.
3. Device-role API, WebSocket events, and assignment UI.
4. Camera profile and one-time Test camera workflow.
5. Native camera preparation, start, stop, and state machine.
6. MediaStore finalization detection, ADB pull, hashing, and manifests.
7. Verified phone-deletion decision and retry flow.
8. Run coordinator linking OpMode, telemetry, and video.
9. Complete run-folder Oracle upload and publication verification.
10. Verified laptop-deletion decision and retry flow.
11. Camera status pill, framing screenshot, policies, progress, and pending-decision UI.
12. Failure-recovery and multi-device test suite.

The telemetry-based processing implementation will be inserted between steps 8 and 9 later. Until it exists, a development-only passthrough may mark processing complete, but production UI must not imply that telemetry processing occurred.

## Acceptance criteria

The first production-ready version should demonstrate all of the following:

- A Control Hub and camera phone can remain connected simultaneously.
- Every ADB command is serial-targeted.
- The Control Hub is automatically classified as RC.
- A phone with the active FTC RC service is classified as RC even when it is the only device.
- An inactive but installed RC package does not prevent a remembered camera assignment.
- Ambiguous devices remain unassigned.
- Role choices persist after reconnect and backend restart.
- The calibrated native camera opens in the expected mode.
- Volume-key start and stop produce a finalized MP4.
- The exact new MP4 is detected through before/after media identity.
- Laptop and phone size/hash match.
- The phone original remains after import until the user explicitly approves its deletion.
- Phone deletion revalidates serial, media identity, and hashes and removes only the exact recording.
- Keeping or deferring the phone copy does not block later stages.
- The complete run folder, not an isolated MP4, uploads through Oracle staging and atomic publication.
- Failed or interrupted Oracle upload leaves the laptop run intact and retryable.
- Laptop deletion is unavailable until Oracle publication is verified.
- The laptop run remains after upload until the user explicitly approves its deletion.
- Closing either deletion popup does not authorize deletion.
- Pending deletion decisions survive browser and backend restart.
- Disconnect during recording produces an honest uncertain state and recoverable import.
- Robot Stop remains immediate during camera failure or transfer.
- The frontend always identifies which physical device is RC and which is camera.
