import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RobotConfiguration } from "./RobotConfiguration";
import { TelemetryDashboard } from "./TelemetryDashboard";
import { DebuggerPage } from "./DebuggerPage";

type Status = {
  connected: boolean;
  host: string | null;
  robot_state: string;
  started_opmode: boolean;
  telemetry: Telemetry | null;
  driver_station_error: string | null;
};

type PingResult = { latency_ms: number | null };

type RobotDataStatus = {
  listening: boolean;
  connected: boolean;
  connection_count: number;
  peers: { host: string; port: number }[];
  recording: {
    uploads: Record<string, {
      state: string;
      detail?: string;
      uploadedFiles?: number;
      skippedFiles?: number;
      totalBytes?: number;
      uploadedBytes?: number;
      currentFile?: string | null;
    }>;
  };
};

type AdbDeviceRole = "auto" | "robot_controller" | "camera" | "ignored";

type AdbDeviceStatus = {
  serial: string;
  serial_suffix: string;
  adb_state: string;
  model: string;
  manufacturer: string | null;
  android_version: string | null;
  camera_capable: boolean;
  camera_package: string | null;
  rc_installed: boolean;
  rc_active: boolean;
  is_control_hub: boolean;
  preferred_role: AdbDeviceRole;
  effective_role: string;
  suggested_role: string | null;
  role_reason: string;
};

type NativeCameraStatus = {
  state: string;
  recording: boolean;
  confirmed: boolean;
  session_id: string | null;
  device_serial: string | null;
  device_model: string | null;
  detail: string;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  bytes: number | null;
  local_path: string | null;
  phone_path: string | null;
  phone_copy_pending: boolean;
  phone_copy_deleted: boolean;
  transfer_progress: number | null;
  battery_percent: number | null;
  free_storage_bytes: number | null;
  error: string | null;
};

type AdbCameraStatus = {
  adb_available: boolean;
  adb_error: string | null;
  devices: AdbDeviceStatus[];
  selected_camera_serial: string | null;
  camera: NativeCameraStatus;
};

type CaptureMode = "scrcpy_direct" | "adb_volume_up";

type DirectCaptureSettings = {
  facing: "front" | "back" | "external" | null;
  aspect_ratio: string | null;
  fps: number;
  flip: boolean;
};

type CameraCaptureStatus = {
  config: {
    mode: CaptureMode;
    direct: DirectCaptureSettings;
  };
  recording: boolean;
  capture_id: string | null;
  telemetry_session_id: string | null;
  preview: { enabled: boolean; running: boolean; detail: string | null };
  error: string | null;
};

type DashboardCameraStatus = { capture: CameraCaptureStatus; adb: AdbCameraStatus };

type Telemetry = {
  timestamp_ms: number;
  state: string;
  tag: string;
  strings: [string, string][];
  numbers: [string, number][];
};

type GamepadState = {
  left_stick_x: number;
  left_stick_y: number;
  right_stick_x: number;
  right_stick_y: number;
  left_trigger: number;
  right_trigger: number;
  buttons: number;
};

type PhysicalController = {
  index: number;
  id: string;
  mapping: string;
};

type DriverAssignments = Record<1 | 2, number | null>;

const BUTTON = {
  RIGHT_BUMPER: 0x00001,
  LEFT_BUMPER: 0x00002,
  BACK: 0x00004,
  START: 0x00008,
  GUIDE: 0x00010,
  Y: 0x00020,
  X: 0x00040,
  B: 0x00080,
  A: 0x00100,
  DPAD_RIGHT: 0x00200,
  DPAD_LEFT: 0x00400,
  DPAD_DOWN: 0x00800,
  DPAD_UP: 0x01000,
} as const;

const neutralGamepad = (): GamepadState => ({
  left_stick_x: 0,
  left_stick_y: 0,
  right_stick_x: 0,
  right_stick_y: 0,
  left_trigger: 0,
  right_trigger: 0,
  buttons: 0,
});

const DEAD_ZONE = 0.08;

const emptyAdbCameraStatus = (): AdbCameraStatus => ({
  adb_available: true,
  adb_error: null,
  devices: [],
  selected_camera_serial: null,
  camera: {
    state: "IDLE",
    recording: false,
    confirmed: false,
    session_id: null,
    device_serial: null,
    device_model: null,
    detail: "Discovering Android devices",
    duration_ms: null,
    width: null,
    height: null,
    bytes: null,
    local_path: null,
    phone_path: null,
    phone_copy_pending: false,
    phone_copy_deleted: false,
    transfer_progress: null,
    battery_percent: null,
    free_storage_bytes: null,
    error: null,
  },
});

const emptyCaptureStatus = (): CameraCaptureStatus => ({
  config: { mode: "scrcpy_direct", direct: { facing: "back", aspect_ratio: null, fps: 60, flip: false } },
  recording: false,
  capture_id: null,
  telemetry_session_id: null,
  preview: { enabled: false, running: false, detail: null },
  error: null,
});

function cameraStateLabel(state: string): string {
  const labels: Record<string, string> = {
    IDLE: "Idle",
    NO_CAMERA: "Not selected",
    READY: "Ready",
    STARTING: "Starting",
    RECORDING_UNVERIFIED: "Recording",
    RECORDING: "Recording",
    STOPPING: "Stopping",
    FINALIZING: "Finalizing",
    IMPORTING: "Copying",
    LAPTOP_VERIFIED: "Verified",
    VERIFIED: "Verified",
    ERROR: "Error",
  };
  return labels[state] ?? state.replaceAll("_", " ").toLowerCase();
}

function bytesLabel(value: number | null): string {
  if (value === null) return "—";
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${Math.round(value / 1024)} KB`;
}

function clampAxis(value: number | undefined): number {
  const safeValue = Math.max(-1, Math.min(1, value ?? 0));
  return Math.abs(safeValue) < DEAD_ZONE ? 0 : safeValue;
}

function buttonPressed(gamepad: Gamepad, index: number): boolean {
  return Boolean(gamepad.buttons[index]?.pressed || (gamepad.buttons[index]?.value ?? 0) > 0.5);
}

function triggerValue(gamepad: Gamepad, buttonIndex: number, fallbackAxis: number): number {
  const button = gamepad.buttons[buttonIndex];
  if (button) return Math.max(0, Math.min(1, button.value));
  // Older non-standard mappings occasionally expose triggers as -1 to 1 axes.
  return Math.max(0, Math.min(1, ((gamepad.axes[fallbackAxis] ?? -1) + 1) / 2));
}

function stateFromPhysicalGamepad(gamepad: Gamepad): GamepadState {
  let buttons = 0;
  if (buttonPressed(gamepad, 5)) buttons |= BUTTON.RIGHT_BUMPER;
  if (buttonPressed(gamepad, 4)) buttons |= BUTTON.LEFT_BUMPER;
  if (buttonPressed(gamepad, 8)) buttons |= BUTTON.BACK;
  if (buttonPressed(gamepad, 9)) buttons |= BUTTON.START;
  if (buttonPressed(gamepad, 16)) buttons |= BUTTON.GUIDE;
  if (buttonPressed(gamepad, 3)) buttons |= BUTTON.Y;
  if (buttonPressed(gamepad, 2)) buttons |= BUTTON.X;
  if (buttonPressed(gamepad, 1)) buttons |= BUTTON.B;
  if (buttonPressed(gamepad, 0)) buttons |= BUTTON.A;
  if (buttonPressed(gamepad, 15)) buttons |= BUTTON.DPAD_RIGHT;
  if (buttonPressed(gamepad, 14)) buttons |= BUTTON.DPAD_LEFT;
  if (buttonPressed(gamepad, 13)) buttons |= BUTTON.DPAD_DOWN;
  if (buttonPressed(gamepad, 12)) buttons |= BUTTON.DPAD_UP;
  // Assignment chords are consumed locally so they cannot accidentally invoke
  // a robot action as the controller is being assigned.
  if (buttonPressed(gamepad, 9) && (buttonPressed(gamepad, 0) || buttonPressed(gamepad, 1))) {
    buttons &= ~(BUTTON.START | BUTTON.A | BUTTON.B);
  }

  return {
    left_stick_x: clampAxis(gamepad.axes[0]),
    left_stick_y: clampAxis(gamepad.axes[1]),
    right_stick_x: clampAxis(gamepad.axes[2]),
    right_stick_y: clampAxis(gamepad.axes[3]),
    left_trigger: triggerValue(gamepad, 6, 4),
    right_trigger: triggerValue(gamepad, 7, 5),
    buttons,
  };
}

function sameGamepadState(left: GamepadState | null, right: GamepadState): boolean {
  return left !== null
    && left.left_stick_x === right.left_stick_x
    && left.left_stick_y === right.left_stick_y
    && left.right_stick_x === right.right_stick_x
    && left.right_stick_y === right.right_stick_y
    && left.left_trigger === right.left_trigger
    && left.right_trigger === right.right_trigger
    && left.buttons === right.buttons;
}

function sameTelemetry(left: Telemetry | null, right: Telemetry | null): boolean {
  return left === right || (left !== null && right !== null && left.timestamp_ms === right.timestamp_ms);
}

function sameStatus(left: Status, right: Status): boolean {
  return left.connected === right.connected
    && left.host === right.host
    && left.robot_state === right.robot_state
    && left.started_opmode === right.started_opmode
    && left.driver_station_error === right.driver_station_error
    && sameTelemetry(left.telemetry, right.telemetry);
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function HoldButton({
  label,
  mask,
  disabled,
  setPressed,
}: {
  label: string;
  mask: number;
  disabled: boolean;
  setPressed: (mask: number, pressed: boolean) => void;
}) {
  return (
    <button
      className="control-button"
      type="button"
      disabled={disabled}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        setPressed(mask, true);
      }}
      onPointerUp={() => setPressed(mask, false)}
      onPointerCancel={() => setPressed(mask, false)}
      onPointerLeave={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) setPressed(mask, false);
      }}
    >
      {label}
    </button>
  );
}

function Axis({
  label,
  value,
  onChange,
  trigger = false,
  disabled,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  trigger?: boolean;
  disabled: boolean;
}) {
  return (
    <label className="axis">
      <span>{label}</span>
      <input
        aria-label={label}
        type="range"
        min={trigger ? 0 : -1}
        max="1"
        step="0.05"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <output>{value.toFixed(2)}</output>
    </label>
  );
}

function RecordingUploadModal({
  sessionId,
  uploadState,
  busy,
  onUploadOnly,
  onUploadAndKeepLocal,
  onDiscard,
}: {
  sessionId: string;
  uploadState: { state: string; detail?: string };
  busy: boolean;
  onUploadOnly: () => void;
  onUploadAndKeepLocal: () => void;
  onDiscard: () => void;
}) {
  const failed = uploadState.state === "error";
  return (
    <div className="recording-modal-backdrop" role="presentation">
      <section className="recording-modal panel" role="dialog" aria-modal="true" aria-labelledby="recording-modal-title">
        <p className="eyebrow">Telemetry recording</p>
        <h2 id="recording-modal-title">{failed ? "Recording needs attention" : "Recording ready"}</h2>
        <p>{failed ? uploadState.detail : "Choose what to do before this telemetry and video recording is uploaded."}</p>
        <code className="recording-session-id">{sessionId}</code>
        {failed && <p className="recording-modal-error">The local telemetry and video remain intact. You can retry the upload or discard the recording.</p>}
        <div className="recording-modal-actions">
          <button className="primary" type="button" disabled={busy} onClick={onUploadOnly}>{failed ? "Retry upload only" : "Upload only"}</button>
          <button className="secondary" type="button" disabled={busy} onClick={onUploadAndKeepLocal}>{failed ? "Retry and keep laptop copy" : "Upload and keep laptop copy"}</button>
          <button className="danger" type="button" disabled={busy} onClick={onDiscard}>Discard recording</button>
        </div>
      </section>
    </div>
  );
}

function DirectCameraPreview({ active, detail }: { active: boolean; detail: string | null }) {
  const [source, setSource] = useState<string | null>(null);

  useEffect(() => {
    if (!active) {
      setSource(null);
      return;
    }
    let disposed = false;
    let current: string | null = null;
    const load = async () => {
      try {
        const response = await fetch("/api/camera/preview/frame", { cache: "no-store" });
        if (response.status === 204 || !response.ok) return;
        const next = URL.createObjectURL(await response.blob());
        if (disposed) { URL.revokeObjectURL(next); return; }
        if (current) URL.revokeObjectURL(current);
        current = next;
        setSource(next);
      } catch {
        // Camera status carries the actionable error; transient polling errors
        // should not erase the last useful preview frame.
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 100);
    return () => { disposed = true; window.clearInterval(timer); if (current) URL.revokeObjectURL(current); };
  }, [active]);

  return <div className="direct-camera-preview">
    <div className="panel-heading"><div><p className="eyebrow">Direct scrcpy</p><h3>Live camera</h3></div><span>{active ? "LIVE" : "WAITING"}</span></div>
    {source ? <img src={source} alt="Live Android camera preview" /> : <p className="empty-state">{detail ?? "Waiting for camera frames."}</p>}
  </div>;
}

function DriverStationPage({ page }: { page: "driver" | "configuration" | "telemetry" | "debug" }) {
  const [host, setHost] = useState("192.168.43.1");
  const [status, setStatus] = useState<Status>({
    connected: false,
    host: null,
    robot_state: "UNKNOWN",
    started_opmode: false,
    telemetry: null,
    driver_station_error: null,
  });
  const [opmodes, setOpmodes] = useState<Record<string, unknown>[]>([]);
  const [opmode, setOpmode] = useState("");
  const [user, setUser] = useState<1 | 2>(1);
  const [gamepad, setGamepad] = useState<GamepadState>(neutralGamepad);
  const [physicalControllers, setPhysicalControllers] = useState<PhysicalController[]>([]);
  const [assignments, setAssignments] = useState<DriverAssignments>({ 1: null, 2: null });
  const [notice, setNotice] = useState("Connect to a controlled test Robot Controller.");
  const [busy, setBusy] = useState(false);
  const [recordingBusy, setRecordingBusy] = useState(false);
  const [adbBusy, setAdbBusy] = useState(false);
  const [pingMs, setPingMs] = useState<number | null>(null);
  const [robotData, setRobotData] = useState<RobotDataStatus>({
    listening: false,
    connected: false,
    connection_count: 0,
    peers: [],
    recording: { uploads: {} },
  });
  const [adbCamera, setAdbCamera] = useState<AdbCameraStatus>(emptyAdbCameraStatus);
  const [captureCamera, setCaptureCamera] = useState<CameraCaptureStatus>(emptyCaptureStatus);
  const [directDraft, setDirectDraft] = useState<DirectCaptureSettings>(emptyCaptureStatus().config.direct);
  const assignmentsRef = useRef<DriverAssignments>({ 1: null, 2: null });
  const shortcutKeysRef = useRef<Set<string>>(new Set());
  const lastPhysicalStatesRef = useRef<Record<1 | 2, GamepadState | null>>({ 1: null, 2: null });
  const directDraftDirtyRef = useRef(false);

  const connected = status.connected;
  const physicalMode = physicalControllers.length > 0;
  const recordingUploads = Object.entries(robotData.recording?.uploads ?? {}).filter(([sessionId]) => sessionId !== "configuration");
  const pendingRecording = recordingUploads.find(([, upload]) => upload.state === "ready_to_upload" || upload.state === "error");
  const latestUpload = recordingUploads.length ? recordingUploads[recordingUploads.length - 1] : null;

  const updateAssignments = useCallback((next: DriverAssignments) => {
    assignmentsRef.current = next;
    setAssignments(next);
  }, []);

  const assignController = useCallback((controllerIndex: number, driver: 1 | 2) => {
    const current = assignmentsRef.current;
    const next: DriverAssignments = { ...current };
    // One physical controller intentionally owns one FTC gamepad slot at a time.
    if (next[1] === controllerIndex) next[1] = null;
    if (next[2] === controllerIndex) next[2] = null;
    next[driver] = controllerIndex;
    updateAssignments(next);
    if (connected) {
      for (const previousDriver of [1, 2] as const) {
        if (current[previousDriver] === controllerIndex && next[previousDriver] === null) {
          lastPhysicalStatesRef.current[previousDriver] = null;
          void api<void>(`/gamepads/${previousDriver}/clear`, { method: "POST" });
        }
      }
    }
    setNotice(`Controller assigned to Driver ${driver}.`);
  }, [connected, updateAssignments]);

  const applyStatus = useCallback((incoming: Status) => {
    setStatus((previous) => {
      // Telemetry has its own ordered WebSocket message. Keeping the latest
      // received telemetry prevents an older HTTP/status snapshot from briefly
      // painting the dashboard backwards.
      const keepLiveTelemetry = previous.connected && incoming.connected && previous.host === incoming.host;
      const next = keepLiveTelemetry
        ? { ...incoming, telemetry: previous.telemetry ?? incoming.telemetry }
        : incoming;
      return sameStatus(previous, next) ? previous : next;
    });
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      applyStatus(await api<Status>("/status"));
    } catch {
      // The UI remains usable while the backend is starting or restarting.
    }
  }, [applyStatus]);

  useEffect(() => {
    void refreshStatus();
    const timer = window.setInterval(() => void refreshStatus(), 1000);
    return () => window.clearInterval(timer);
  }, [refreshStatus]);

  const refreshAdbCamera = useCallback(async () => {
    try {
      const next = await api<DashboardCameraStatus>("/camera/status");
      setAdbCamera(next.adb);
      setCaptureCamera(next.capture);
      if (!directDraftDirtyRef.current) setDirectDraft(next.capture.config.direct);
    } catch {
      // Keep the last useful device state during a backend restart.
    }
  }, []);

  useEffect(() => {
    void refreshAdbCamera();
    const timer = window.setInterval(() => void refreshAdbCamera(), 1000);
    return () => window.clearInterval(timer);
  }, [refreshAdbCamera]);

  useEffect(() => {
    if (!connected) {
      setPingMs(null);
      return;
    }
    let active = true;
    const samplePing = () => {
      void api<PingResult>("/ping")
        .then((result) => { if (active) setPingMs(result.latency_ms); })
        .catch(() => { if (active) setPingMs(null); });
    };
    samplePing();
    const timer = window.setInterval(samplePing, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [connected]);

  useEffect(() => {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${window.location.host}/ws`);
    socket.onmessage = (event: MessageEvent<string>) => {
      const message = JSON.parse(event.data) as { kind: string; data: Status | Telemetry };
      if (message.kind === "status") applyStatus(message.data as Status);
      if (message.kind === "telemetry") {
        const telemetry = message.data as Telemetry;
        setStatus((previous) => sameTelemetry(previous.telemetry, telemetry)
          ? previous
          : { ...previous, telemetry });
      }
    };
    return () => socket.close();
  }, [applyStatus]);

  useEffect(() => {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${window.location.host}/ws/data`);
    socket.onmessage = (event: MessageEvent<string>) => {
      const message = JSON.parse(event.data) as { kind: string; data: RobotDataStatus };
      if (message.kind === "robot_data_status") setRobotData(message.data);
    };
    return () => socket.close();
  }, []);

  useEffect(() => {
    let active = true;
    const pollControllers = () => {
      if (!active) return;
      const gamepads = Array.from(navigator.getGamepads?.() ?? []).filter(
        (gamepad): gamepad is Gamepad => gamepad !== null && gamepad.connected,
      );
      const discovered = gamepads.map(({ index, id, mapping }) => ({ index, id, mapping }));
      setPhysicalControllers((previous) => {
        const unchanged = previous.length === discovered.length && previous.every((item, index) =>
          item.index === discovered[index].index && item.id === discovered[index].id && item.mapping === discovered[index].mapping,
        );
        return unchanged ? previous : discovered;
      });

      const available = new Set(gamepads.map((gamepad) => gamepad.index));
      const current = assignmentsRef.current;
      const next: DriverAssignments = {
        1: current[1] !== null && available.has(current[1]) ? current[1] : null,
        2: current[2] !== null && available.has(current[2]) ? current[2] : null,
      };
      if (next[1] !== current[1] || next[2] !== current[2]) {
        updateAssignments(next);
        if (connected) {
          if (next[1] === null && current[1] !== null) {
            lastPhysicalStatesRef.current[1] = null;
            void api<void>("/gamepads/1/clear", { method: "POST" });
          }
          if (next[2] === null && current[2] !== null) {
            lastPhysicalStatesRef.current[2] = null;
            void api<void>("/gamepads/2/clear", { method: "POST" });
          }
        }
      }

      const heldShortcuts = new Set<string>();
      for (const controller of gamepads) {
        const start = buttonPressed(controller, 9);
        const a = buttonPressed(controller, 0);
        const b = buttonPressed(controller, 1);
        const driver: 1 | 2 | null = start && a && !b ? 1 : start && b && !a ? 2 : null;
        if (driver !== null) {
          const shortcutKey = `${controller.index}:${driver}`;
          heldShortcuts.add(shortcutKey);
          if (!shortcutKeysRef.current.has(shortcutKey)) assignController(controller.index, driver);
        }
      }
      shortcutKeysRef.current = heldShortcuts;

      if (connected && document.hasFocus()) {
        const activeAssignments = assignmentsRef.current;
        for (const driver of [1, 2] as const) {
          const controllerIndex = activeAssignments[driver];
          const controller = gamepads.find((item) => item.index === controllerIndex);
          if (controller) {
            const nextState = stateFromPhysicalGamepad(controller);
            if (!sameGamepadState(lastPhysicalStatesRef.current[driver], nextState)) {
              lastPhysicalStatesRef.current[driver] = nextState;
              void api<void>(`/gamepads/${driver}`, {
                method: "PUT",
                body: JSON.stringify(nextState),
              }).catch((error: Error) => {
                // Do not let a temporary backend restart make the controller
                // appear sent forever; retry on the next animation frame.
                lastPhysicalStatesRef.current[driver] = null;
                setNotice(error.message);
              });
            }
          }
        }
      }
      window.requestAnimationFrame(pollControllers);
    };

    const animationFrame = window.requestAnimationFrame(pollControllers);
    return () => {
      active = false;
      window.cancelAnimationFrame(animationFrame);
    };
  }, [assignController, connected, updateAssignments]);

  useEffect(() => {
    if (!connected || physicalMode) return;
    const timer = window.setTimeout(() => {
      void api<void>(`/gamepads/${user}`, {
        method: "PUT",
        body: JSON.stringify(gamepad),
      }).catch((error: Error) => setNotice(error.message));
    }, 15);
    return () => window.clearTimeout(timer);
  }, [connected, gamepad, physicalMode, user]);

  useEffect(() => {
    if (!physicalMode) return;
    setGamepad(neutralGamepad());
    if (connected) {
      lastPhysicalStatesRef.current = { 1: null, 2: null };
      void api<void>("/gamepads/1/clear", { method: "POST" });
      void api<void>("/gamepads/2/clear", { method: "POST" });
    }
  }, [connected, physicalMode]);

  useEffect(() => {
    const release = () => {
      if (connected) {
        if (physicalMode) {
          lastPhysicalStatesRef.current = { 1: null, 2: null };
          void api<void>("/gamepads/1/clear", { method: "POST" });
          void api<void>("/gamepads/2/clear", { method: "POST" });
        } else {
          void api<void>(`/gamepads/${user}/clear`, { method: "POST" });
        }
      }
      setGamepad(neutralGamepad());
    };
    window.addEventListener("blur", release);
    return () => window.removeEventListener("blur", release);
  }, [connected, physicalMode, user]);

  const updateAxis = (axis: keyof GamepadState, value: number) => {
    setGamepad((previous) => ({ ...previous, [axis]: value }));
  };

  const setPressed = (mask: number, pressed: boolean) => {
    setGamepad((previous) => ({
      ...previous,
      buttons: pressed ? previous.buttons | mask : previous.buttons & ~mask,
    }));
  };

  const loadOpmodes = async () => {
    const list = await api<Record<string, unknown>[]>("/opmodes");
    setOpmodes(list);
    // The SDK's first entry is commonly its internal "$Stop$Robot$" sentinel.
    // It is not a user OpMode, so never preselect it when a session connects.
    setOpmode("");
  };

  const runAction = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Unexpected error");
    } finally {
      setBusy(false);
      void refreshStatus();
    }
  };

  const recordingAction = async (action: "upload_only" | "upload_keep_local" | "discard") => {
    if (!pendingRecording) return;
    const [sessionId] = pendingRecording;
    if (action === "discard" && !window.confirm("Discard this telemetry and video recording from the laptop? It will not be uploaded and cannot be recovered.")) return;
    setRecordingBusy(true);
    try {
      await api(`/data/recordings/${encodeURIComponent(sessionId)}${action === "discard" ? "/discard" : "/upload"}`, {
        method: action === "discard" ? "DELETE" : "POST",
        ...(action === "discard" ? {} : { body: JSON.stringify({ keep_local: action === "upload_keep_local" }) }),
      });
      setNotice(action === "discard" ? "Recording discarded from this laptop." : action === "upload_keep_local" ? "Recording upload started; the laptop copy will be kept." : "Recording upload started; the laptop copy will be removed after a successful upload.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Recording action failed");
    } finally {
      setRecordingBusy(false);
    }
  };

  const assignAdbRole = async (serial: string, role: AdbDeviceRole) => {
    setAdbBusy(true);
    try {
      const next = await api<AdbCameraStatus>(`/adb-camera/devices/${encodeURIComponent(serial)}/role`, {
        method: "PUT",
        body: JSON.stringify({ role }),
      });
      setAdbCamera(next);
      const device = next.devices.find((item) => item.serial === serial);
      setNotice(`${device?.model ?? serial} role set to ${role.replace("_", " ")}.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not assign Android device role");
    } finally {
      setAdbBusy(false);
      void refreshAdbCamera();
    }
  };

  const saveCaptureConfig = async (mode = captureCamera.config.mode, direct = directDraft) => {
    setAdbBusy(true);
    try {
      const next = await api<DashboardCameraStatus>("/camera/config", {
        method: "PUT",
        body: JSON.stringify({ mode, direct }),
      });
      setCaptureCamera(next.capture);
      setAdbCamera(next.adb);
      directDraftDirtyRef.current = false;
      setDirectDraft(next.capture.config.direct);
      setNotice(mode === "scrcpy_direct" ? "Direct scrcpy camera selected." : "ADB Volume Up camera selected.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not update camera settings");
      void refreshAdbCamera();
    } finally {
      setAdbBusy(false);
    }
  };

  const editDirectCapture = (update: Partial<DirectCaptureSettings>) => {
    directDraftDirtyRef.current = true;
    setDirectDraft((previous) => ({ ...previous, ...update }));
  };

  const stopDirectPreview = async () => {
    setAdbBusy(true);
    try {
      const next = await api<{ capture: CameraCaptureStatus }>("/camera/preview/stop", { method: "POST" });
      setCaptureCamera(next.capture);
      setNotice("Direct camera preview stopped.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not stop the camera preview");
    } finally {
      setAdbBusy(false);
    }
  };

  const stopNativeCamera = async () => {
    setAdbBusy(true);
    try {
      setAdbCamera(await api<AdbCameraStatus>("/adb-camera/stop", { method: "POST" }));
      setNotice("Native camera stop requested; finalization and transfer continue in the background.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not stop the native camera");
    } finally {
      setAdbBusy(false);
    }
  };

  const deletePhoneCopy = async () => {
    if (!window.confirm("Delete this exact verified recording from the camera phone? The verified laptop copy will be kept.")) return;
    setAdbBusy(true);
    try {
      setAdbCamera(await api<AdbCameraStatus>("/adb-camera/delete-phone-copy", { method: "POST" }));
      setNotice("The verified phone recording was deleted; the laptop copy was kept.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not delete the phone copy");
    } finally {
      setAdbBusy(false);
    }
  };

  const connect = () =>
    runAction(async () => {
      const nextStatus = await api<Status>("/connect", {
        method: "POST",
        body: JSON.stringify({ host }),
      });
      setStatus(nextStatus);
      setNotice(`Connected to ${host}. Select an OpMode before initializing.`);
      await loadOpmodes();
    });

  const disconnect = () =>
    runAction(async () => {
      const nextStatus = await api<Status>("/disconnect", { method: "POST" });
      setStatus(nextStatus);
      setGamepad(neutralGamepad());
      setNotice("Disconnected and stop requested.");
    });

  const init = () =>
    runAction(async () => {
      if (!opmode) throw new Error("Select an OpMode first");
      await api<Status>("/opmodes/init", { method: "POST", body: JSON.stringify({ name: opmode }) });
      setNotice(`Initialized ${opmode}.`);
    });

  const start = () =>
    runAction(async () => {
      if (!opmode) throw new Error("Select an OpMode first");
      await api<Status>("/opmodes/start", { method: "POST", body: JSON.stringify({ name: opmode }) });
      setNotice(`${opmode} is running. Inputs start neutral.`);
    });

  const stop = () =>
    runAction(async () => {
      await api<Status>("/opmodes/stop", { method: "POST" });
      setGamepad(neutralGamepad());
      setNotice("Robot stopped and gamepads released.");
    });

  const runLifecycleAction = () => {
    if (status.started_opmode || status.robot_state === "RUNNING") return stop();
    if (status.robot_state === "INIT") return start();
    return init();
  };

  const lifecycleAction = status.started_opmode || status.robot_state === "RUNNING"
    ? { label: "Stop", className: "danger", requiresOpmode: false }
    : status.robot_state === "INIT"
      ? { label: "Start", className: "primary", requiresOpmode: true }
      : { label: "Init", className: "primary", requiresOpmode: true };

  const releaseAll = () =>
    runAction(async () => {
      await api<void>(`/gamepads/${user}/clear`, { method: "POST" });
      setGamepad(neutralGamepad());
      setNotice(`Released Gamepad ${user}.`);
    });

  const switchGamepad = () =>
    runAction(async () => {
      const nextUser: 1 | 2 = user === 1 ? 2 : 1;
      await api<void>("/gamepads/switch", {
        method: "POST",
        body: JSON.stringify({ from_user: user, to_user: nextUser }),
      });
      setUser(nextUser);
      setGamepad(neutralGamepad());
      setNotice(`Switched safely to Gamepad ${nextUser}.`);
    });

  const telemetryEntries = useMemo(() => {
    if (!status.telemetry) return [];
    return [
      ...status.telemetry.strings.map(([key, value]) => [key, value] as const),
      ...status.telemetry.numbers.map(([key, value]) => [key, value.toFixed(3)] as const),
    ].sort(([left], [right]) => left.localeCompare(right));
  }, [status.telemetry]);
  const opmodeError = status.driver_station_error;
  const nativeCamera = adbCamera.camera;
  const directMode = captureCamera.config.mode === "scrcpy_direct";
  const nativeCameraWorking = ["STARTING", "RECORDING_UNVERIFIED", "RECORDING", "STOPPING", "FINALIZING", "IMPORTING"].includes(nativeCamera.state);
  const cameraWorking = directMode ? captureCamera.recording : nativeCameraWorking;
  const cameraHealthy = directMode
    ? !captureCamera.error && (captureCamera.recording || captureCamera.preview.running)
    : ["READY", "RECORDING_UNVERIFIED", "RECORDING", "LAPTOP_VERIFIED", "VERIFIED"].includes(nativeCamera.state);
  const roleChangesLocked = captureCamera.recording || nativeCameraWorking || adbBusy;
  const cameraSummary = directMode
    ? captureCamera.recording ? "Recording" : captureCamera.preview.running ? "Preview" : captureCamera.error ? "Error" : "Waiting"
    : nativeCamera.state === "IMPORTING" && nativeCamera.transfer_progress !== null
      ? `Copying ${nativeCamera.transfer_progress}%`
      : cameraStateLabel(nativeCamera.state);
  const cameraDetail = directMode
    ? captureCamera.error ?? captureCamera.preview.detail ?? "Direct camera preview is waiting for its assigned Android phone."
    : nativeCamera.detail;

  const controllerName = (controllerIndex: number | null) => {
    if (controllerIndex === null) return null;
    return physicalControllers.find((controller) => controller.index === controllerIndex)?.id ?? "Disconnected controller";
  };

  const topbar = <header className="driver-topbar panel">
    <div className="driver-brand"><p className="eyebrow">Local lab dashboard</p><h1>FTC Driver Station</h1></div>
    <div className="topbar-status" aria-label="Driver Station status">
      <div className={`connection ${connected ? "online" : "offline"}`}><span className="status-dot" />{connected ? `Connected · ${status.robot_state}` : "Disconnected"}{connected && <span className="ping">Ping {pingMs === null ? "—" : `${pingMs.toFixed(1)} ms`}</span>}</div>
      <div className="topbar-drivers" aria-label="Controller status">
        {([1, 2] as const).map((driver) => { const assigned = controllerName(assignments[driver]); return <span className={assigned ? "active" : ""} key={driver}><i className="driver-dot" />D{driver}: {assigned ? "Ready" : physicalMode ? "Unassigned" : user === driver ? "Virtual" : "Available"}</span>; })}
      </div>
      <div className={`topbar-tcp ${robotData.connected ? "active" : ""}`}><i className="driver-dot" />TCP {robotData.connected ? "Connected" : robotData.listening ? "Listening" : "Offline"}</div>
      <div className={`topbar-camera ${cameraHealthy ? "active" : ""} ${cameraWorking ? "recording" : ""}`} title={cameraDetail}><i className="driver-dot" />Camera · {cameraSummary}</div>
      <a className={`topbar-link ${page === "configuration" ? "current" : ""}`} href="#/configure">Configure Robot</a><a className={`topbar-link ${page === "debug" ? "current" : ""}`} href="#/debugger">Debugger</a><a className="topbar-link" href={page === "telemetry" ? "#/" : "#/telemetry"}>{page === "telemetry" ? "Driver Station" : "Telemetry Lab"}</a>
    </div>
  </header>;

  return (
    <>
    <main className="driver-dashboard" hidden={page !== "driver"}>
      {topbar}

      <section className="driver-command-row">
      <section className="connection-panel panel">
        <label>
          Robot Controller address
          <input value={host} onChange={(event) => setHost(event.target.value)} disabled={connected || busy} />
        </label>
        {connected ? (
          <button className="secondary" type="button" disabled={busy} onClick={disconnect}>Disconnect</button>
        ) : (
          <button className="primary" type="button" disabled={busy} onClick={connect}>Connect</button>
        )}
        <p className="notice" aria-live="polite">{notice}</p>
      </section>

      <section className="lifecycle panel">
        <div className="panel-heading"><h2>OpMode</h2><span>{status.started_opmode ? "RUNNING" : status.robot_state}</span></div>
        <select value={opmode} disabled={!connected || busy} onChange={(event) => setOpmode(event.target.value)}>
          <option value="">Select an OpMode</option>
          {opmodes.map((item) => (
            <option key={String(item.name)} value={String(item.name)}>
              {String(item.name)} · {String(item.flavor ?? "UNKNOWN")}
            </option>
          ))}
        </select>
        <div className="action-row">
          <button className={lifecycleAction.className} type="button" disabled={!connected || busy || (lifecycleAction.requiresOpmode && !opmode)} onClick={() => void runLifecycleAction()}>{lifecycleAction.label}</button>
        </div>
      </section>

      <section className="adb-camera-panel panel">
        <div className="panel-heading">
          <div><p className="eyebrow">Camera capture</p><h2>{directMode ? "Direct scrcpy" : "ADB Volume Up"}</h2></div>
          <span>{cameraSummary.toUpperCase()}</span>
        </div>
        <div className="camera-mode-controls">
          <label>Capture mode
            <select value={captureCamera.config.mode} disabled={roleChangesLocked} onChange={(event) => void saveCaptureConfig(event.target.value as CaptureMode)}>
              <option value="scrcpy_direct">Direct scrcpy</option>
              <option value="adb_volume_up">ADB Volume Up</option>
            </select>
          </label>
          <small>Optional sidecar capture. It never affects Robot Controller connection, Init, Start, or Stop.</small>
        </div>
        {directMode ? <div className="direct-camera-settings">
          <label>Lens
            <select value={directDraft.facing ?? "back"} disabled={roleChangesLocked} onChange={(event) => editDirectCapture({ facing: event.target.value as DirectCaptureSettings["facing"] })}>
              <option value="back">Rear</option><option value="front">Front</option><option value="external">External</option>
            </select>
          </label>
          <label>Aspect ratio
            <select value={directDraft.aspect_ratio ?? ""} disabled={roleChangesLocked} onChange={(event) => editDirectCapture({ aspect_ratio: event.target.value || null })}>
              <option value="">Camera default</option><option value="16:9">16:9</option><option value="4:3">4:3</option><option value="1:1">1:1</option>
            </select>
          </label>
          <label>Recording FPS
            <input type="number" min="1" max="120" step="1" value={directDraft.fps} disabled={roleChangesLocked} onChange={(event) => editDirectCapture({ fps: Number(event.target.value) || 1 })} />
          </label>
          <label className="camera-flip"><input type="checkbox" checked={directDraft.flip} disabled={roleChangesLocked} onChange={(event) => editDirectCapture({ flip: event.target.checked })} /> Flip video</label>
          <button className="secondary" type="button" disabled={roleChangesLocked} onClick={() => void saveCaptureConfig("scrcpy_direct", directDraft)}>Save camera settings</button>
        </div> : <p className="camera-preflight">Camera capture is optional. Assign an Android camera only when you want to use the native Camera controls.</p>}
        {adbCamera.adb_error && <p className="camera-error">{adbCamera.adb_error}</p>}
        {directMode && <DirectCameraPreview active={captureCamera.preview.running && !captureCamera.recording} detail={cameraDetail} />}
        {latestUpload && <div className={`camera-upload-status upload-${latestUpload[1].state}`}>
          <strong>Telemetry upload · {latestUpload[1].state.replaceAll("_", " ")}</strong>
          <span>{latestUpload[0]}</span>
          {latestUpload[1].detail && <small>{latestUpload[1].detail}</small>}
          {latestUpload[1].state === "uploading" && typeof latestUpload[1].totalBytes === "number" && latestUpload[1].totalBytes > 0 && (() => {
            const progress = Math.min(100, Math.round((latestUpload[1].uploadedBytes ?? 0) / latestUpload[1].totalBytes * 100));
            return <div className="upload-progress" role="progressbar" aria-label="Telemetry upload progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}>
              <div className="upload-progress-fill" style={{ width: `${progress}%` }} />
              <span>{progress}% · {bytesLabel(latestUpload[1].uploadedBytes ?? 0)} / {bytesLabel(latestUpload[1].totalBytes)}</span>
            </div>;
          })()}
        </div>}
        <div className="adb-device-list">
          {adbCamera.devices.length ? adbCamera.devices.map((device) => (
            <article className={`adb-device-card role-${device.effective_role}`} key={device.serial}>
              <div className="adb-device-heading">
                <div><strong>{device.model}</strong><small>{device.manufacturer ?? "Android"} · …{device.serial_suffix} · Android {device.android_version ?? "?"}</small></div>
                <span>{device.effective_role.replace("_", " ")}</span>
              </div>
              <p>{device.role_reason}</p>
              <div className="adb-evidence">
                <span>{device.adb_state === "device" ? "USB ready" : device.adb_state}</span>
                {device.rc_active && <span className="warning">FTC RC active</span>}
                {!device.rc_active && device.rc_installed && <span>FTC RC installed</span>}
                {device.camera_capable && <span>Camera available</span>}
                {device.suggested_role === "camera" && <span className="suggested">Suggested camera</span>}
              </div>
              <div className="adb-role-actions">
                <button className={device.preferred_role === "robot_controller" ? "selected" : ""} type="button" disabled={roleChangesLocked || device.adb_state !== "device"} onClick={() => void assignAdbRole(device.serial, "robot_controller")}>Use as RC</button>
                <button className={device.preferred_role === "camera" ? "selected" : ""} type="button" disabled={roleChangesLocked || device.adb_state !== "device" || !device.camera_capable || device.rc_active || device.is_control_hub} onClick={() => void assignAdbRole(device.serial, "camera")}>Use as camera</button>
                <button type="button" disabled={roleChangesLocked || device.preferred_role === "auto"} onClick={() => void assignAdbRole(device.serial, "auto")}>Auto</button>
                <button type="button" disabled={roleChangesLocked || device.preferred_role === "ignored" || device.rc_active || device.is_control_hub} onClick={() => void assignAdbRole(device.serial, "ignored")}>Ignore</button>
              </div>
            </article>
          )) : <p className="empty-state">Connect and authorize an Android device with USB debugging enabled.</p>}
        </div>
        {!directMode && <div className="camera-runtime">
          <div>
            <strong>{nativeCamera.device_model ?? "No camera selected"}</strong>
            <small>{nativeCamera.detail}</small>
            {nativeCamera.error && <small className="camera-error">{nativeCamera.error}</small>}
          </div>
          <div className="camera-runtime-meta">
            {nativeCamera.battery_percent !== null && <span>Battery {nativeCamera.battery_percent}%</span>}
            {nativeCamera.free_storage_bytes !== null && <span>Free {bytesLabel(nativeCamera.free_storage_bytes)}</span>}
            {nativeCamera.width && nativeCamera.height && <span>{nativeCamera.width}×{nativeCamera.height}</span>}
            {nativeCamera.duration_ms !== null && <span>{(nativeCamera.duration_ms / 1000).toFixed(1)} s</span>}
          </div>
          <div className="camera-runtime-actions">
            {nativeCameraWorking && <button className="danger" type="button" disabled={adbBusy} onClick={() => void stopNativeCamera()}>Stop camera</button>}
            {nativeCamera.phone_copy_pending && <button className="secondary" type="button" disabled={adbBusy} onClick={() => void deletePhoneCopy()}>Delete phone copy…</button>}
          </div>
        </div>}
        {directMode && captureCamera.preview.running && <div className="camera-runtime-actions direct-preview-actions"><button className="secondary" type="button" disabled={adbBusy} onClick={() => void stopDirectPreview()}>Stop preview</button></div>}
      </section>
      </section>

      <section className="dashboard-grid">
        {!physicalMode && <section className="gamepad panel">
          <div className="panel-heading">
            <div><p className="eyebrow">Virtual input</p><h2>Gamepad {user}</h2></div>
            <button className="secondary" type="button" disabled={!connected || busy} onClick={() => void switchGamepad()}>
              Switch to Gamepad {user === 1 ? 2 : 1}
            </button>
          </div>

          <div className="controller-layout">
            <div className="control-cluster">
              <h3>Left stick</h3>
              <Axis label="X" value={gamepad.left_stick_x} disabled={!connected} onChange={(value) => updateAxis("left_stick_x", value)} />
              <Axis label="Y" value={gamepad.left_stick_y} disabled={!connected} onChange={(value) => updateAxis("left_stick_y", value)} />
              <h3>D-pad</h3>
              <div className="dpad">
                <HoldButton label="↑" mask={BUTTON.DPAD_UP} disabled={!connected} setPressed={setPressed} />
                <HoldButton label="←" mask={BUTTON.DPAD_LEFT} disabled={!connected} setPressed={setPressed} />
                <HoldButton label="↓" mask={BUTTON.DPAD_DOWN} disabled={!connected} setPressed={setPressed} />
                <HoldButton label="→" mask={BUTTON.DPAD_RIGHT} disabled={!connected} setPressed={setPressed} />
              </div>
            </div>

            <div className="control-cluster central-controls">
              <h3>Shoulders</h3>
              <div className="two-buttons"><HoldButton label="LB" mask={BUTTON.LEFT_BUMPER} disabled={!connected} setPressed={setPressed} /><HoldButton label="RB" mask={BUTTON.RIGHT_BUMPER} disabled={!connected} setPressed={setPressed} /></div>
              <Axis label="Left trigger" trigger value={gamepad.left_trigger} disabled={!connected} onChange={(value) => updateAxis("left_trigger", value)} />
              <Axis label="Right trigger" trigger value={gamepad.right_trigger} disabled={!connected} onChange={(value) => updateAxis("right_trigger", value)} />
              <h3>Menu</h3>
              <div className="two-buttons"><HoldButton label="Back" mask={BUTTON.BACK} disabled={!connected} setPressed={setPressed} /><HoldButton label="Start" mask={BUTTON.START} disabled={!connected} setPressed={setPressed} /></div>
              <HoldButton label="Guide" mask={BUTTON.GUIDE} disabled={!connected} setPressed={setPressed} />
              <button className="release" type="button" disabled={!connected || busy} onClick={() => void releaseAll()}>Release all controls</button>
            </div>

            <div className="control-cluster">
              <h3>Right stick</h3>
              <Axis label="X" value={gamepad.right_stick_x} disabled={!connected} onChange={(value) => updateAxis("right_stick_x", value)} />
              <Axis label="Y" value={gamepad.right_stick_y} disabled={!connected} onChange={(value) => updateAxis("right_stick_y", value)} />
              <h3>Face buttons</h3>
              <div className="face-buttons">
                <HoldButton label="Y" mask={BUTTON.Y} disabled={!connected} setPressed={setPressed} />
                <HoldButton label="X" mask={BUTTON.X} disabled={!connected} setPressed={setPressed} />
                <HoldButton label="B" mask={BUTTON.B} disabled={!connected} setPressed={setPressed} />
                <HoldButton label="A" mask={BUTTON.A} disabled={!connected} setPressed={setPressed} />
              </div>
            </div>
          </div>
        </section>
        }

        <aside className={`telemetry panel ${physicalMode ? "telemetry-wide" : ""} ${opmodeError ? "telemetry-opmode-error" : ""}`} aria-live="polite">
          <div className="panel-heading"><div><p className="eyebrow">{opmodeError ? "Robot Controller diagnostic" : "Live stream"}</p><h2>{opmodeError ? "OpMode Error" : "Telemetry"}</h2></div><span>{opmodeError ? "EXCEPTION" : status.telemetry?.state ?? "WAITING"}</span></div>
          {opmodeError ? (
            <div className="opmode-error-body">
              <p className="opmode-error-summary">OpMode threw an uncaught exception.</p>
              <p className="opmode-error-hint">The stack trace below came from the Robot Controller. Click <strong>Stop</strong> to dismiss it and return to telemetry.</p>
              <pre className="opmode-error-stack">{opmodeError}</pre>
            </div>
          ) : status.telemetry ? (
            <>
              <p className="telemetry-tag">{status.telemetry.tag}</p>
              <dl>
                {telemetryEntries.length ? telemetryEntries.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>) : <p>No telemetry entries.</p>}
              </dl>
            </>
          ) : <p className="empty-state">Telemetry will appear here after the Robot Controller sends it.</p>}
        </aside>
      </section>
    </main>
    {page === "configuration" && <main className="configuration-workspace">
      <header className="hero"><div><p className="eyebrow">Robot setup</p><h1>Configure Robot</h1><p className="subhead">Edit and activate the Robot Controller hardware configuration.</p></div><a className="secondary topbar-link" href="#/">← Driver Station</a></header>
      <nav className="workspace-nav" aria-label="Application pages"><a href="#/">Driver Station</a><a className="current" href="#/configure">Configure Robot</a><a href="#/telemetry">Telemetry Lab</a></nav>
      <RobotConfiguration connected={connected} robotState={status.robot_state} startedOpmode={status.started_opmode} />
    </main>}
    {page === "telemetry" && <TelemetryDashboard topbar={topbar} />}
    {page === "debug" && <DebuggerPage />}
    {pendingRecording && <RecordingUploadModal
      sessionId={pendingRecording[0]}
      uploadState={pendingRecording[1]}
      busy={recordingBusy}
      onUploadOnly={() => void recordingAction("upload_only")}
      onUploadAndKeepLocal={() => void recordingAction("upload_keep_local")}
      onDiscard={() => void recordingAction("discard")}
    />}
    </>
  );
}

function App() {
  const [page, setPage] = useState<"driver" | "configuration" | "telemetry" | "debug">(() => {
    if (window.location.hash === "#/telemetry") return "telemetry";
    if (window.location.hash === "#/debugger") return "debug";
    return window.location.hash === "#/configure" ? "configuration" : "driver";
  });

  useEffect(() => {
    const updatePage = () => setPage(window.location.hash === "#/telemetry" ? "telemetry" : window.location.hash === "#/configure" ? "configuration" : window.location.hash === "#/debugger" ? "debug" : "driver");
    window.addEventListener("hashchange", updatePage);
    return () => window.removeEventListener("hashchange", updatePage);
  }, []);

  // Keep the Driver Station mounted while viewing telemetry. Its physical
  // gamepad polling and Robocol forwarding are part of the control session,
  // not the visible Driver Station page.
  return (
    <>
      <DriverStationPage page={page} />
    </>
  );
}

export default App;
