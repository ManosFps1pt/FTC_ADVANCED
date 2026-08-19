# FTC Driver Station connectivity and Robocol research

**Research target:** FTC SDK 11.2 / Robocol 124  
**Date checked:** 2026-08-18  
**Purpose:** Step 0 research for a lab-only desktop Driver Station and live dashboard

> [!CAUTION]
> This document is about controlled development and testing. It is not a recommendation to replace the official Driver Station at an FTC event. The 2025-2026 Competition Manual requires the match wireless link to be controlled by the official Robot Controller and Driver Station apps, and requires an approved Android Driver Station device. A desktop replacement is therefore not match-legal under rules R901 and R905. A protocol bug can also remove the RC's safety heartbeat, stop an OpMode, or, in the worst case, send unintended gamepad or lifecycle commands. Keep the robot off the floor or on blocks, remove mechanisms that can cause injury, and add an independent physical power cutoff while developing.

## Executive answer

The Driver Station does not normally control the Control Hub through USB or ADB. It joins the Robot Controller's Wi-Fi network and exchanges **Robocol**, a private, versioned, big-endian binary protocol over **UDP port 20884**. The normal Control Hub address is `192.168.43.1`; a phone-based RC acting as a Wi-Fi Direct group owner is normally `192.168.49.1`. Both peers bind UDP 20884.

The high-level sequence is:

1. The DS first joins the Control Hub access point, or the RC phone's Wi-Fi Direct group.
2. The DS sends a 13-byte peer-discovery datagram to the network owner about once per second.
3. The RC replies with its Robocol and SDK versions and either accepts the DS or reports that another DS is already connected.
4. The DS begins 100 ms heartbeats, requests the OpMode list and other UI state, and sends gamepad packets.
5. Selecting an OpMode only changes DS-local state. **INIT** sends `CMD_INIT_OP_MODE`; **START** sends `CMD_RUN_OP_MODE`.
6. **STOP** is not a separate Robocol message. The DS initializes the SDK system OpMode named `$Stop$Robot$`.
7. Commands are acknowledged and retried. Telemetry and robot state flow from RC to DS.
8. If the link is silent for about two seconds, the connection is declared lost and the RC initializes `$Stop$Robot$` as a safety action.

ADB is a separate management and debugging path. Wireless ADB is useful for logs, package inspection, deployment, and shell access, but it does not carry UDP Robocol traffic. A desktop replacement should connect directly to the RC's Wi-Fi network and use UDP. If USB-only operation is required, a custom relay on the Android side would be necessary.

## Scope, versions, and evidence

FTC SDK [v11.2](https://github.com/FIRST-Tech-Challenge/FtcRobotController/releases/tag/v11.2) was released on 2026-07-15 as the 2025-2026 offseason release. It adds the `UTILITY` OpMode flavor and built-in hardware/gamepad test utilities. Its official [`RobotCore-11.2.0.aar`](https://repo.maven.apache.org/maven2/org/firstinspires/ftc/RobotCore/11.2.0/RobotCore-11.2.0.aar) reports:

- `ROBOCOL_VERSION = 124`
- `PORT_NUMBER = 20884`
- `HEADER_LENGTH = 5`
- gamepad wire version `5`, payload size 60, total datagram size 65

The attached Android device was inspected read-only over USB ADB. It is a Samsung `SM-S931B`, not a REV Control Hub. It has both `com.qualcomm.ftcrobotcontroller` and `com.qualcomm.ftcdriverstation` version 11.0 installed; the RC process was running. Its interfaces showed ordinary Wi-Fi at `192.168.1.29` and an active Wi-Fi Direct group-owner interface at `192.168.49.1`. This matches the phone-RC topology described below. No OpMode was initialized or started and no packet was transmitted to it during this research.

The core serializer constants and public layouts in SDK 11.0 and 11.2 match, and both report Robocol 124. That means an 11.0 RC and 11.2 implementation agree at the core wire-format level, but this is **not** a promise of complete app compatibility: 11.2 adds `UTILITY` to the OpMode metadata and the official manual recommends matching RC and DS major/minor versions. The safe implementation rule is to parse the peer's SDK metadata, reject unknown Robocol versions, and tolerate added JSON fields and enum values.

### Confidence levels

| Level | Meaning | Sources used here |
|---|---|---|
| Verified | Direct official documentation or inspected official 11.2 AAR bytecode | FTC Docs, Competition Manual, Maven AARs, FTC Javadocs |
| Strongly corroborated | FTC-authorized extracted SDK source, checked against the 11.2 binary constants | [OpenFTC Extracted-RC 11.0](https://github.com/OpenFTC/Extracted-RC/tree/11.0) |
| Observed | Read-only result from the USB-attached Samsung RC phone | `adb devices`, `getprop`, `dumpsys`, `ip address`, `ip route`, `logcat` |
| Reference only | Independent reverse-engineered software; useful as a test oracle, not authoritative | [Epiteugma/FtcDriverStation](https://github.com/Epiteugma/FtcDriverStation), [OpenFTC DSktop](https://github.com/OpenFTC/DSktop) |

Independent implementations must not be copied blindly. For example, the currently inspected TypeScript `librobocol` writes the total datagram size into the normal header's payload-length field and places part of the heartbeat timezone metadata at a different offset than the SDK serializer. The Android receiver happens not to rely heavily on that header field, so an implementation may appear to work while still being wire-inaccurate. The official serializer is the source of truth.

## Network topology and exact setup

### Control Hub topology

The Control Hub contains the Android Robot Controller. It creates a WPA2 Wi-Fi access point whose SSID is the configured robot name. A factory name normally begins with `FTC-`; teams rename it and should change the default password. The DS and a programming laptop join that access point as clients. The standard addresses and services are:

| Endpoint | Transport | Purpose |
|---|---:|---|
| `192.168.43.1:20884` | UDP | Robocol control, gamepads, commands, telemetry |
| `http://192.168.43.1:8080` | TCP/HTTP | Program & Manage, Blocks, OnBot Java, configuration and downloads |
| `192.168.43.1:5555` | TCP/ADB | Wireless Android debugging and management |

FTC documents the Control Hub pairing process and notes that joining this network usually removes Internet access from the DS or laptop. See [Configuring your Android Devices](https://ftc-docs.firstinspires.org/en/latest/programming_resources/shared/configuring_android/Configuring-Your-Android-Devices.html), [Connecting a Laptop to Program & Manage](https://ftc-docs.firstinspires.org/en/latest/programming_resources/shared/program_and_manage_network/Connecting-a-Laptop-to-the-Program-%26-Manage-Network.html), and [Managing a Control Hub](https://ftc-docs.firstinspires.org/en/latest/programming_resources/shared/managing_control_hub/Managing-a-Control-Hub.html).

Desktop setup for a future Robocol client:

1. Power the Control Hub and wait for it to finish booting.
2. Join the Control Hub SSID from Windows using its configured password.
3. Verify `http://192.168.43.1:8080` opens.
4. Verify the route with `Get-NetRoute -AddressFamily IPv4` or `route print`.
5. Bind a native UDP socket to the local Wi-Fi address on port `20884`.
6. Send peer discovery to `192.168.43.1:20884` and listen on the same socket.

Do not bind to `127.0.0.1`, an unrelated Ethernet interface, or a VPN interface. The SDK selects a local address on the same `/24` subnet as the connection-owner address. A desktop transport should do the same and should let the user override the interface only for troubleshooting.

### Phone-based Robot Controller topology

A phone RC creates a Wi-Fi Direct group. Its network name normally starts with `DIRECT-`, the RC is the group owner at `192.168.49.1`, and the DS is a peer/client. Program & Manage is available at `http://192.168.49.1:8080`; FTC Docs list both the Control Hub and phone-RC HTTP addresses in the [programming documentation](https://ftc-docs.firstinspires.org/en/latest/programming_resources/shared/myblocks/simple_example/simple-example.html).

The Robocol transport is otherwise the same: UDP 20884, peer discovery to the group owner, heartbeats, commands, gamepads, and telemetry. The important difference is how the network is created and joined. On Android, the SDK obtains the group-owner address from Wi-Fi Direct APIs. A Windows client can either join the published Program & Manage network and use the known `192.168.49.1` address, or determine the default gateway after joining.

### One RC accepts one DS peer

Peer discovery is not a broadcast search for every robot. The DS already knows the connection owner's address from the selected network and sends directly to it. The RC associates Robocol with the first accepted peer address. If a second address tries to discover while the first is connected, the RC returns peer type `NOT_CONNECTED_DUE_TO_PREEXISTING_CONNECTION` (wire value 3). A future client must show this as “another Driver Station is connected,” not repeatedly attempt to steal the session.

## ADB: what it is and is not

[Android Debug Bridge](https://developer.android.com/tools/adb) is a client/server debugging system: the host-side `adb` client talks to a local ADB server, which communicates with `adbd` on the Android device. It can use USB or TCP/IP. This is independent from the RC app's UDP socket.

### USB ADB

For a Control Hub, connect the Windows machine to the Hub's USB-C programming port with a data cable. For a phone, enable developer options and USB debugging and accept the host's RSA key. The local Android SDK contains ADB at:

```text
C:\Users\Lefteris Dragasakis\AppData\Local\Android\Sdk\platform-tools\adb.exe
```

The following PowerShell commands were exercised read-only against the attached phone:

```powershell
$adb = 'C:\Users\Lefteris Dragasakis\AppData\Local\Android\Sdk\platform-tools\adb.exe'

& $adb version
& $adb devices -l
& $adb shell getprop ro.product.manufacturer
& $adb shell getprop ro.product.model
& $adb shell getprop ro.build.version.release
& $adb shell getprop ro.build.version.sdk
& $adb shell dumpsys package com.qualcomm.ftcrobotcontroller |
    Select-String 'versionCode=|versionName=|enabled='
& $adb shell ip -brief address
& $adb shell ip route
& $adb shell pidof com.qualcomm.ftcrobotcontroller
& $adb logcat -d -v time |
    Select-String 'Robocol|PeerDiscovery|CMD_|OpMode'
```

Useful diagnostic or deployment actions include:

- inspect packages, processes, properties, routes, and sockets;
- stream or save `logcat` output;
- pull `robotControllerLog.txt` and match logs;
- install a development RC APK;
- inspect or transfer permitted files;
- run a shell-side diagnostic or a deliberately installed relay.

The FTC [Robot Troubleshooting Guide](https://ftc-resources.firstinspires.org/ftc/archive/2026/team/robot-troubleshooting) documents `robotControllerLog.txt`, `driverStationLog.txt`, and using `adb pull` to copy them.

### Wireless ADB

The Control Hub is configured to listen for ADB on TCP 5555 automatically. After the laptop joins the Hub Wi-Fi:

```powershell
$adb = 'C:\Users\Lefteris Dragasakis\AppData\Local\Android\Sdk\platform-tools\adb.exe'
& $adb connect 192.168.43.1:5555
& $adb devices -l
```

No preceding `adb tcpip 5555` is normally required on a Control Hub. FTC's exact procedure is in [Managing a Control Hub](https://ftc-docs.firstinspires.org/en/latest/programming_resources/shared/managing_control_hub/Managing-a-Control-Hub.html#connecting-to-the-control-hub-using-wireless-adb).

For an ordinary phone RC, wireless ADB depends on the Android version and device settings. The older workflow is: connect over USB, run `adb tcpip 5555`, ensure host and phone share the RC network, then run `adb connect 192.168.49.1:5555`. Android documents this flow in [Connect to a device over Wi-Fi](https://developer.android.com/tools/adb#wireless). It is not guaranteed that every approved phone or event configuration permits it.

If USB and TCP transports are both listed, select explicitly:

```powershell
& $adb -s 192.168.43.1:5555 shell getprop ro.product.model
```

### Can ADB carry the Driver Station connection?

**Not directly.** Robocol requires bidirectional UDP 20884. The normal `adb forward` and `adb reverse` endpoint syntax supports TCP and Android local sockets, not transparent UDP forwarding. The official example is `adb forward tcp:6100 tcp:7100`, documented in the [ADB port-forwarding section](https://developer.android.com/tools/adb#forwardports).

USB ADB also does not automatically give Windows a routable IP interface on the RC's Wi-Fi subnet. Therefore:

- **Laptop joined to Hub Wi-Fi:** send Robocol directly over LAN; ADB is optional.
- **Wireless ADB over Hub Wi-Fi:** ADB and Robocol share the physical Wi-Fi link but remain separate TCP and UDP application protocols.
- **USB cable only:** ADB works, but a desktop Robocol socket cannot reach the RC merely because `adb devices` sees it.
- **Possible USB-only experiment:** install or launch a small Android-side UDP-to-TCP/WebSocket relay and reach the TCP side through ADB forwarding. This changes the RC-side software, adds latency and a new failure mode, and is a later research project—not a transparent use of ADB.

## Robocol transport and framing

The official 11.2 constants were inspected from the Maven AAR and compared with the FTC-authorized 11.0 extracted source. Useful source anchors are [`RobocolConfig`](https://github.com/OpenFTC/Extracted-RC/blob/11.0/RobotCore/src/main/java/com/qualcomm/robotcore/robocol/RobocolConfig.java), [`RobocolParsable`](https://github.com/OpenFTC/Extracted-RC/blob/11.0/RobotCore/src/main/java/com/qualcomm/robotcore/robocol/RobocolParsable.java), and [`SendOnceRunnable`](https://github.com/OpenFTC/Extracted-RC/blob/11.0/RobotCore/src/main/java/org/firstinspires/ftc/robotcore/internal/network/SendOnceRunnable.java).

### Socket behavior

- IPv4 UDP, destination and local port `20884`.
- The socket binds to the local interface on the same subnet as the network-owner address.
- Receive timeout: 300 ms, allowing the receive loop to wake periodically.
- Maximum internal packet cap: 65,520 bytes, additionally limited by the OS socket buffer.
- Java `ByteBuffer` default order is big-endian; all multibyte values below are big-endian.
- Sequence numbers are unsigned 16-bit values on the wire. DS and RC maintain independent monotonically increasing sequence spaces and wrap modulo 65,536.

### Normal five-byte header

All packet types except peer discovery use this header:

| Absolute bytes | Type | Meaning |
|---:|---|---|
| 0 | `uint8` | Message type |
| 1-2 | `uint16` | **Payload** length, excluding the five-byte header |
| 3-4 | `uint16` | Sequence number |
| 5+ | bytes | Type-specific payload |

Message types are stable numeric IDs:

| ID | Name | Direction/purpose |
|---:|---|---|
| 0 | `EMPTY` | Reserved/empty packet |
| 1 | `HEARTBEAT` | DS originates; RC echoes with state and timing |
| 2 | `GAMEPAD` | DS to RC |
| 3 | `PEER_DISCOVERY` | Connection handshake; special header layout |
| 4 | `COMMAND` | Reliable, acknowledged application commands both ways |
| 5 | `TELEMETRY` | Primarily RC to DS |
| 6 | `KEEPALIVE` | Conditional filler when no other packet was sent |

Do not put total datagram length in bytes 1-2. A 65-byte gamepad has payload length 60.

## Peer discovery and session establishment

Peer discovery deliberately preserves a historical 13-byte format, so its sequence number is not at the normal header offset. The source documents this exception in [`PeerDiscovery`](https://github.com/OpenFTC/Extracted-RC/blob/11.0/RobotCore/src/main/java/com/qualcomm/robotcore/robocol/PeerDiscovery.java).

| Absolute bytes | Type | Value/meaning |
|---:|---|---|
| 0 | `uint8` | Message type 3 |
| 1-2 | `uint16` | Historical payload length 10 |
| 3 | `uint8` | Robocol version, 124 for SDK 11.0-11.2 |
| 4 | `uint8` | Peer type: 0 unset, 1 peer, 2 deprecated group owner, 3 rejected because another peer exists |
| 5-6 | `uint16` | Sequence number |
| 7 | `uint8` | SDK build/release month |
| 8-9 | `uint16` | SDK build/release year |
| 10 | `uint8` | SDK major version |
| 11 | `uint8` | SDK minor version |
| 12 | `uint8` | Ignored/padding |

The DS sends peer type 1 to the connection owner once per second. The RC parses the packet and requires exact Robocol-version equality. It sends a second peer-discovery packet back with its own SDK information and peer type 1 if accepted. If a different IP is already the active peer, the reply uses type 3.

Once accepted, the connection handler remembers the remote IP, starts a 40 ms scheduled send loop, and considers packets only from that peer. A robust desktop session should:

1. keep sending discovery until it receives a valid accepted reply;
2. reject a version other than 124 instead of guessing;
3. stop discovery after connection, but be able to restart it after a timeout or interface change;
4. clear pending commands, gamepad assignments, timers, and stale telemetry on disconnect;
5. never fight a type-3 rejection from an existing DS.

## Heartbeat, keepalive, liveness, and time sync

The Driver Station originates a heartbeat approximately every 100 ms. The RC immediately echoes the same heartbeat after filling in its robot state and timing values. The format is:

| Absolute bytes | Type | Meaning |
|---:|---|---|
| 0-4 | header | Type 1 and normal header |
| 5-12 | `int64` | `System.nanoTime()` creation timestamp; only meaningful on its originating device |
| 13 | `int8` | Robot state |
| 14-21 | `int64` | `t0`: DS wall-clock milliseconds when sent |
| 22-29 | `int64` | `t1`: RC wall-clock milliseconds when received |
| 30-37 | `int64` | `t2`: RC wall-clock milliseconds immediately before echo |
| 38 | `uint8` | UTF-8 timezone ID length, limited to 127 |
| 39+ | UTF-8 | Timezone ID |

Robot-state values are `UNKNOWN=-1`, `NOT_STARTED=0`, `INIT=1`, `RUNNING=2`, `STOPPED=3`, and `EMERGENCY_STOP=4`. Heartbeats and telemetry both carry robot state, while `CMD_NOTIFY_ROBOT_STATE` is also available for state-change notification.

On first sane heartbeat from a new DS, the Control Hub treats the DS clock and timezone as authoritative and can set its clock. A desktop implementation should send a real IANA/Java timezone ID and wall-clock milliseconds, not zeros copied from a sample.

Liveness constants in 11.2 are:

| Behavior | Value |
|---|---:|
| Scheduled send loop | 40 ms |
| DS heartbeat interval | 100 ms |
| Receive timeout | 300 ms |
| Peer-disconnect assumption | 2.0 s |
| RC forced stop on heartbeat loss | approximately 2.0 s |
| Conditional keepalive interval | 20 ms |

Keepalive has the normal header followed by one ID byte. It is not normally a second mandatory heartbeat: the SDK sends it only when configured to originate keepalives and when that send-loop pass transmitted neither a heartbeat nor gamepad data.

Two safety layers matter. The generic network handler declares the peer disconnected after roughly two seconds without received packets. The RC event loop also tracks DS heartbeats. On peer loss it initializes `$Stop$Robot$`, which stops the current OpMode. A replacement must prioritize heartbeat scheduling over UI work and must not let browser throttling, a blocked event loop, or garbage collection pause the sender for seconds. Put liveness in a native/background service, not in `requestAnimationFrame`.

## Reliable commands

Commands carry UTF-8 command names and UTF-8 `extra` data; `extra` is frequently JSON but is not inherently JSON. The normal command packet is:

| Absolute bytes | Type | Meaning |
|---:|---|---|
| 0-4 | header | Type 4 and normal header |
| 5-12 | `int64` | Originating `System.nanoTime()` timestamp/command identity |
| 13 | `uint8` | ACK flag: 0 request, 1 acknowledgement |
| 14-15 | `uint16` | Command-name byte length |
| 16... | UTF-8 | Command name |
| next 2 | `uint16` | `extra` byte length; omitted from ACK packets |
| remainder | UTF-8 | `extra`; omitted from ACK packets |

An incoming unacknowledged command is immediately changed to ACK state and queued back to the sender, while also being dispatched once to the relevant handler. An ACK preserves the command name, timestamp, and sequence number, but omits `extra`. Commands compare equal by **name plus timestamp**, which is how the sender removes the pending item. The RC keeps a small cache of recently handled commands to avoid executing retransmissions twice.

Originated commands are eligible for retransmission every 200 ms. The send loop abandons one after more than ten serialization attempts or after an optional deadline expires. A future command layer therefore needs:

- a pending map keyed by `(name, timestamp)`;
- immediate ACK generation before slow UI work;
- retry scheduling independent of rendering;
- duplicate suppression on received commands;
- a bounded retry/deadline result exposed to the UI;
- idempotent handlers, because UDP can duplicate or reorder datagrams.

## Gamepad packets and feedback

SDK 11.2 uses gamepad wire version 5. The packet is always 65 bytes: a normal five-byte header plus 60-byte payload.

| Absolute bytes | Type | Meaning |
|---:|---|---|
| 5 | `uint8` | Gamepad wire version, 5 |
| 6-9 | `int32` | Device ID; `-1` unassociated, `-2` synthetic |
| 10-17 | `int64` | Android uptime milliseconds of last input event |
| 18-21 | `float32` | Left stick X |
| 22-25 | `float32` | Left stick Y |
| 26-29 | `float32` | Right stick X |
| 30-33 | `float32` | Right stick Y |
| 34-37 | `float32` | Left trigger |
| 38-41 | `float32` | Right trigger |
| 42-45 | `uint32` | Button bitmask |
| 46 | `uint8` | User/driver slot, normally 1 or 2 |
| 47 | `uint8` | Legacy gamepad-type ordinal |
| 48 | `uint8` | Current gamepad-type ordinal |
| 49-52 | `float32` | Touchpad finger 1 X |
| 53-56 | `float32` | Touchpad finger 1 Y |
| 57-60 | `float32` | Touchpad finger 2 X |
| 61-64 | `float32` | Touchpad finger 2 Y |

The button bits are:

| Bit | Mask | Input |
|---:|---:|---|
| 17 | `0x20000` | Touchpad finger 1 present |
| 16 | `0x10000` | Touchpad finger 2 present |
| 15 | `0x08000` | Touchpad button |
| 14 | `0x04000` | Left stick button |
| 13 | `0x02000` | Right stick button |
| 12-9 | `0x01000`...`0x00200` | D-pad up, down, left, right |
| 8-5 | `0x00100`...`0x00020` | A, B, X, Y; aliases cross, circle, square, triangle |
| 4 | `0x00010` | Guide/PS |
| 3 | `0x00008` | Start/options |
| 2 | `0x00004` | Back/share |
| 1-0 | `0x00002`, `0x00001` | Left and right bumpers |

The DS sends assigned gamepads in the 40 ms send loop. At-rest data older than one second is skipped; active data continues to be transmitted. When a physical controller disappears or changes driver slot, the DS sends a synthetic, at-rest gamepad for the former user so the RC does not retain pressed buttons or nonzero axes. This behavior is safety-critical.

Controller enumeration, VID/PID quirks, assignment gestures, dead zones, and raw OS events are DS-local concerns. A reusable `GamepadProvider` should normalize SDL/HID/browser/native input into the SDK layout, while a separate assignment layer owns slots 1 and 2. Do not make the wire codec depend on a specific controller library.

Gamepad feedback travels in the opposite direction as reliable commands:

- `CMD_RUMBLE_EFFECT`
- `CMD_GAMEPAD_LED_EFFECT`

Their `extra` bodies are JSON-serialized SDK effect objects and identify the target user. The desktop input backend must either implement effects for the selected controller API or report that the controller does not support them.

## Telemetry

Telemetry is message type 5 with the normal header. All lengths are byte lengths after UTF-8 encoding.

| Payload order | Type | Meaning |
|---:|---|---|
| 1 | `int64` | `System.currentTimeMillis()` timestamp |
| 2 | `uint8` | Sorted flag |
| 3 | `int8` | Robot state |
| 4 | `uint8` + bytes | Tag length and UTF-8 tag, maximum 255 bytes |
| 5 | `uint8` | Count of string entries, maximum 255 |
| 6 | repeated | `uint16 keyLen`, key bytes, `uint16 valueLen`, value bytes |
| 7 | `uint8` | Count of numeric entries, maximum 255 |
| 8 | repeated | `uint16 keyLen`, key bytes, `float32` value |

Keys and string values are limited to 65,535 UTF-8 bytes. The default tag is `TELEMETRY_DATA`. SDK-reserved tags and keys include:

- `$System$None$`, `$System$Error$`, `$System$Warning$`
- `$Robot$Battery$Level$`
- `$RobotController$Battery$Status$`

The dashboard should preserve the distinction between native numeric entries and strings. Parsing numbers back out of formatted strings is a compatibility convenience, not the primary data model. Store receipt time, packet timestamp, sequence number, tag, robot state, string map, and float map for every update. A UI can then display the latest state while a recorder retains a time series.

Telemetry is UDP and is not acknowledged. It can be lost or reordered. A graph should use receipt time or validate packet timestamps, and it should not assume every loop iteration produces a packet.

## OpMode discovery and lifecycle

### OpMode list

After connection, the DS sends `CMD_REQUEST_OP_MODE_LIST`. The RC responds with `CMD_NOTIFY_OP_MODE_LIST`; its `extra` is a Gson JSON array of `OpModeMeta` records. In SDK 11.2 those records include:

- `name`
- `flavor`: `AUTONOMOUS`, `TELEOP`, `UTILITY`, or `SYSTEM`
- `group`
- `autoTransition`
- `source`: Android Studio, Blocks, OnBot Java, external library, or built-in
- `systemOpModeBaseDisplayName`
- `description`

SDK 11.0 does not have `UTILITY` or the built-in source value. Use string-tolerant JSON parsing so new enum values do not crash an older dashboard. SDK 11.2's Utility support is described in the [v11.2 release notes](https://github.com/FIRST-Tech-Challenge/FtcRobotController/releases/tag/v11.2).

The normal initial UI sync also requests:

- `CMD_REQUEST_ACTIVE_CONFIG` → `CMD_NOTIFY_ACTIVE_CONFIGURATION`
- `CMD_REQUEST_USER_DEVICE_TYPES` → `CMD_NOTIFY_USER_DEVICE_LIST`
- optionally `CMD_REQUEST_CONFIGURATIONS` → `CMD_REQUEST_CONFIGURATIONS_RESP`

### State machine

```text
DISCONNECTED
    │ accepted peer discovery + live heartbeat
    ▼
CONNECTED / STOP SENTINEL
    │ local selection only (no RC action)
    │ CMD_INIT_OP_MODE(name)
    ▼
INITIALIZING / INITIALIZED
    │ RC: initOpMode(name)
    │ RC → DS: CMD_NOTIFY_INIT_OP_MODE(name)
    │ CMD_RUN_OP_MODE(name)
    ▼
RUNNING
    │ RC defensively initializes name first if its active name differs
    │ RC → DS: CMD_NOTIFY_RUN_OP_MODE(name)
    │
    ├── STOP: CMD_INIT_OP_MODE("$Stop$Robot$") ──► CONNECTED / STOP SENTINEL
    ├── peer lost for ~2 s ──────────────────────► CONNECTED / STOP SENTINEL
    └── user-code/internal failure ──────────────► EMERGENCY_STOP
```

Selection is intentionally separate from initialization. Changing a dropdown should not touch the robot. The controls should be gated as follows:

- **INIT enabled:** accepted session, compatible version, fresh heartbeat, valid selected name, safe local state.
- **START enabled:** selected OpMode has been confirmed by `CMD_NOTIFY_INIT_OP_MODE`, session remains fresh, and no emergency stop is active.
- **STOP always prominent:** sends the sentinel-init command once, then retries through the normal reliable-command layer until ACK or disconnect.
- **RESTART separated and confirmed:** `CMD_RESTART_ROBOT` is not a normal stop and temporarily tears down application state.

`CMD_SET_MATCH_NUMBER` carries a decimal number as `extra`. The RC caches it for match logging.

`CMD_NOTIFY_INIT_OP_MODE` and `CMD_NOTIFY_RUN_OP_MODE` are authoritative RC confirmations. The UI must not claim “running” merely because a UDP send returned successfully or because the command was ACKed; ACK means the command arrived, while the notification reports the resulting lifecycle transition.

### Stop, disconnect, and emergency stop are different

- **Normal stop:** initialize `$Stop$Robot$`. The OpMode manager stops the active OpMode and sends hub fail-safe commands.
- **Disconnect stop:** the RC automatically initializes the same sentinel after losing the peer. The desktop should also clear local gamepad state immediately.
- **Emergency stop:** `RobotState.EMERGENCY_STOP` is an RC-reported fault state, not a normal “stop packet.” It may require fixing user code or hardware and restarting the robot. Do not automatically clear or mask it.

### Timers and autonomous-to-TeleOp preselection

Practice timers, sounds, the selected dropdown, and the queued TeleOp are mostly DS-local features. At their boundaries they use the same init/run/stop commands. The automatic TeleOp-selection feature is documented in the [FTC SDK wiki](https://github.com/FIRST-Tech-Challenge/FtcRobotController/wiki/Automatically-Loading-a-Driver-Controlled-Op-Mode).

For a replacement, keep timer policy out of the protocol layer. A timer service can request lifecycle transitions, but the safety state machine must validate them and the UI must display RC confirmations. Exact official audio cues and automatic-start behavior should be captured as a later UI-compatibility test; they are not part of the Robocol wire format.

## Driver Station capability map

This is a layered map rather than an exhaustive schema for every configuration or firmware JSON object.

| Capability | Main mechanism | Important messages/interfaces | Desktop feasibility / notes |
|---|---|---|---|
| Pair/select robot network | Android/Windows Wi-Fi UI | SoftAP or Wi-Fi Direct | Feasible; initially let Windows own joining and show detected gateway/SSID |
| Discover/claim RC | Robocol UDP | Peer discovery type 3 | Core requirement; single active DS peer |
| Connection health | Robocol + local Wi-Fi stats | Heartbeat, keepalive, ping calculation | Core; raw RSSI/channel/link speed are OS/interface-specific |
| Robot/OpMode state | Robocol | Heartbeat, telemetry, `CMD_NOTIFY_ROBOT_STATE` | Core |
| List/select OpModes | Robocol command + local UI | `CMD_REQUEST_OP_MODE_LIST`, `CMD_NOTIFY_OP_MODE_LIST` | Core; parse 11.2 Utility metadata |
| Init/start/stop | Robocol command | `CMD_INIT_OP_MODE`, `CMD_RUN_OP_MODE`, sentinel | Core; safety-gated |
| Match number and timers | Robocol + DS-local | `CMD_SET_MATCH_NUMBER`; timer/audio local | Feasible after core lifecycle |
| Auto-to-TeleOp queue | DS-local policy + Robocol | init/run commands | Feasible; keep policy separate |
| Gamepad input | Native HID/SDL + Robocol | Type-2 packets | Core for control; browser-only Gamepad API is insufficient for all FTC controllers/platforms |
| Rumble and LEDs | Robocol + native controller API | `CMD_RUMBLE_EFFECT`, `CMD_GAMEPAD_LED_EFFECT` | Feasible where hardware/backend supports effects |
| Telemetry and warnings | Robocol | Type-5 packets, system tags | Best first dashboard feature |
| Battery status | Robocol telemetry | Reserved battery keys | Feasible; distinguish robot and RC battery |
| Configuration list/load | Robocol or HTTP | configuration request/response commands | Feasible; prefer read-only first |
| Hardware scan/edit/save | Robocol, XML/JSON, HTTP | `CMD_SCAN`, `CMD_SAVE_CONFIGURATION`, user device types | High risk; validate schemas and preserve unknown devices |
| Activate/delete config | Robocol | `CMD_ACTIVATE_CONFIGURATION`, `CMD_DELETE_CONFIGURATION` | Feasible, but changes RC state and often requires restart |
| Hub/module discovery | Robocol | `CMD_DISCOVER_LYNX_MODULES` | Feasible with SDK payload schemas |
| Change module address | Robocol + hub operations | `CMD_LYNX_ADDRESS_CHANGE` | Dangerous; defer until protocol/client is mature |
| Hub firmware update | Robocol/HTTP + updater service | candidate image, accessible module, update commands | Dangerous and failure-sensitive; use official tools first |
| Camera preview | Robocol commands/chunks | `CMD_STREAM_CHANGE`, `CMD_REQUEST_FRAME`, frame begin/chunk | Feasible but bandwidth-heavy; not core control |
| Sound playback | Robocol control + possible TCP asset transfer | `CMD_PLAY_SOUND`, `CMD_REQUEST_SOUND`, stop sounds | Mixed transport; request includes a TCP port for missing sound data |
| Text-to-speech | Robocol + DS Android/native speech | `CMD_TEXT_TO_SPEECH` | Desktop backend can substitute native TTS |
| Toast/dialog/progress/stack trace | Robocol + UI | show/dismiss commands | Straightforward after command reliability |
| Program & Manage | Robocol handoff + HTTP 8080 | `CMD_START_DS_PROGRAM_AND_MANAGE` | Open the RC web server in a browser/webview |
| About/inspection | Robocol + DS-local checks | request about/inspection report | Feasible; some checks depend on Android device policy and have no desktop equivalent |
| Wi-Fi name/password/band | HTTP, RC preferences, Android APIs | preference and visual-confirm commands | Prefer official Manage page; changing network drops the current connection |
| Remembered Wi-Fi Direct groups | Robocol + Android APIs | request/clear groups, changed notification | Mainly phone-RC/Android-specific |
| Robot restart | Robocol | `CMD_RESTART_ROBOT` | Feasible but disruptive; require confirmation |
| Logs and package diagnostics | ADB or HTTP downloads | `logcat`, log files, `dumpsys` | Good separate diagnostics adapter |

The complete command-name inventory visible in the SDK also includes firmware-image discovery, USB-accessible Lynx modules, configuration templates, Bluetooth disable, Wi-Fi reset/band confirmation, visual module identification, dialogs, progress, toasts, camera frames, inspection, about information, and Robot Controller preference synchronization. Many extras are Gson JSON documents whose Java types may evolve without changing the outer Robocol command packet.

## Recommended reusable architecture

A browser cannot bind raw UDP, and heartbeat scheduling must not depend on the browser event loop. Use a native desktop backend with a web UI:

```text
USB gamepads ─► GamepadProvider ─┐
                                │
Browser UI ◄── WebSocket/API ◄── DashboardBackend
                                │
ADB diagnostics ─► AdbAdapter   ├─► SafetyStateMachine
                                ├─► ReliableCommandSession
                                ├─► RobocolCodec
                                └─► UdpTransport ── Wi-Fi ──► RC :20884

RC telemetry/commands ◄──────────── same UDP session
        │
        └─► TelemetryStore / recorder / charts
```

Suggested module boundaries:

1. **`UdpTransport`** — interface selection, bind/send/receive, remote filtering, counters, timestamps; no packet semantics.
2. **`RobocolCodec`** — versioned pure encode/decode functions and byte-level tests for all seven message types.
3. **`DiscoverySession`** — peer-discovery scheduling, single-peer rules, compatibility, connection timeout, reconnect.
4. **`ReliableCommandSession`** — timestamp identities, ACKs, retry deadlines, duplicate cache, typed command events.
5. **`SafetyStateMachine`** — fresh-heartbeat requirement, legal lifecycle transitions, stop sentinel, stale-input clearing, restart confirmation.
6. **`GamepadProvider`** — native controller enumeration/normalization and effects; independent from the codec.
7. **`TelemetryStore`** — immutable events, latest-value view, time-series recording, warnings and battery extraction.
8. **`AdbAdapter`** — optional diagnostics, logs, packages, network information; never hidden inside the control transport.
9. **`DashboardBackend`** — owns services and exposes a local authenticated WebSocket/HTTP API.
10. **Browser UI** — connection status, OpMode controls, gamepad slots, telemetry, graphs and logs; it never sends UDP itself.

Public application events should be transport-neutral, for example `PeerAccepted`, `PeerRejected`, `HeartbeatReceived`, `RobotStateChanged`, `OpModeListUpdated`, `OpModeInitialized`, `OpModeStarted`, `CommandTimedOut`, `TelemetryReceived`, and `Disconnected`. This will allow later replay tests and an alternate USB relay without rewriting the dashboard.

## Recommended implementation order after step 0

1. **Golden packet fixtures:** generate/record official 11.2 discovery, heartbeat, gamepad, command, ACK, telemetry, and keepalive byte arrays; test encode/decode round trips.
2. **Passive decoder:** listen and log datagrams without claiming the RC where the network setup permits capture; build PCAP/hex fixtures.
3. **Read-only session:** claim the lab RC, maintain heartbeats, request state/OpMode list, and render telemetry. Keep motors unpowered.
4. **Command reliability:** implement ACK/retry/duplicate handling and validate harmless metadata requests.
5. **Safe lifecycle control:** add selection and INIT, then STOP; test timeout and process-kill behavior before adding START.
6. **Gamepad path:** add one controller with synthetic neutralization, then two-player assignment and effects.
7. **Dashboard recording:** structured telemetry history, graphs, warnings, latency and packet-loss metrics.
8. **Secondary DS features:** configuration read-only view, Program & Manage integration, camera/sound, then carefully scoped writes.
9. **Dangerous maintenance features last:** configuration writes, network changes, module addresses, firmware updates, and restart.

Each control milestone needs a hardware-off or wheels-up test for: backend crash, Wi-Fi loss, duplicate/reordered command, stuck gamepad, controller unplug, UI freeze, RC restart, second-DS rejection, mismatched Robocol version, and emergency-stop reporting.

## Direct answers to the original questions

**How does the DS find the RC?** It first joins the RC-owned Wi-Fi network, obtains/knows the connection-owner IP, binds UDP 20884, and sends a 13-byte peer-discovery datagram to that IP once per second until accepted.

**How does it maintain the connection?** The DS originates heartbeats about every 100 ms and the RC echoes them. Gamepad and other traffic shares the same UDP socket. Roughly two seconds of silence triggers disconnect handling and a robot stop.

**How is a TeleOp selected?** The RC sends a JSON OpMode metadata list. Dropdown selection is local to the DS. Pressing INIT sends `CMD_INIT_OP_MODE` with the exact OpMode name.

**How is it started?** START sends `CMD_RUN_OP_MODE` with the name. The RC initializes it first if the active name differs, starts it, and reports `CMD_NOTIFY_RUN_OP_MODE`.

**How is it stopped?** The DS sends `CMD_INIT_OP_MODE` with `$Stop$Robot$`. Link loss causes the RC to do the same automatically.

**How do driver controls reach user code?** Up to two 65-byte gamepad v5 packets carry normalized axes, triggers, buttons, touchpad data, user slot, ID and type. The RC copies the latest packet for each user into `gamepad1`/`gamepad2` for the active OpMode.

**Can ADB communicate over LAN?** Yes. A Control Hub automatically offers ADB at `192.168.43.1:5555` while the host is on the Hub network. An ordinary phone may be switched to TCP/IP ADB after an initial USB connection.

**Can ADB replace or carry the DS protocol?** No, not transparently. Robocol is UDP 20884, while standard ADB forwarding provides TCP/local-socket forwarding. Use direct Wi-Fi UDP, or deliberately build and install a relay as a separate future component.

**What should be built next?** A pure Robocol codec with golden fixtures, then native UDP discovery/heartbeat and a read-only telemetry session. Lifecycle writes and gamepads should come only after disconnect and stop behavior is proven.

## Primary references

- [FTC SDK v11.2 release and APK/source assets](https://github.com/FIRST-Tech-Challenge/FtcRobotController/releases/tag/v11.2)
- [Official RobotCore 11.2 Javadocs](https://javadoc.io/doc/org.firstinspires.ftc/RobotCore/11.2.0)
- [Official RobotCore 11.2 AAR](https://repo.maven.apache.org/maven2/org/firstinspires/ftc/RobotCore/11.2.0/RobotCore-11.2.0.aar)
- [Official FtcCommon 11.2 AAR](https://repo.maven.apache.org/maven2/org/firstinspires/ftc/FtcCommon/11.2.0/FtcCommon-11.2.0.aar)
- [FTC Control System introduction](https://ftc-docs.firstinspires.org/programming_resources/shared/control_system_intro/The-FTC-Control-System.html)
- [FTC Control Hub pairing](https://ftc-docs.firstinspires.org/en/latest/programming_resources/shared/configuring_android/Configuring-Your-Android-Devices.html)
- [FTC Control Hub management and wireless ADB](https://ftc-docs.firstinspires.org/en/latest/programming_resources/shared/managing_control_hub/Managing-a-Control-Hub.html)
- [Android Debug Bridge documentation](https://developer.android.com/tools/adb)
- [2025-2026 FTC Competition Manual, latest archived update](https://ftc-resources.firstinspires.org/ftc/archive/2026/game/cm-html/DECODE_Competition_Manual_TU32.htm)
- [FTC-authorized OpenFTC Extracted-RC 11.0 source](https://github.com/OpenFTC/Extracted-RC/tree/11.0)

