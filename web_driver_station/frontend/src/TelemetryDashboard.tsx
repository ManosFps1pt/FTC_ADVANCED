import { type CSSProperties, type ReactNode, useEffect, useMemo, useRef, useState } from "react";

type DeviceDefinition = { id: string; label: string; subsystem: string; deviceType: string };
type SignalDefinition = { id: string; label: string; deviceId?: string; quantity: string; unit: string; valueType: string; role?: string };
type Catalog = { schemaRevision: number; devices: DeviceDefinition[]; signals: SignalDefinition[] };
type Snapshot = { sampleSequence: string; robotTimeNs: string; highlighted?: boolean; highlightSource?: "control_hub" | "telemetry_lab" | null; values: Record<string, unknown> };
type GamepadState = { leftStickX: number; leftStickY: number; rightStickX: number; rightStickY: number; leftTrigger: number; rightTrigger: number; a: boolean; b: boolean; x: boolean; y: boolean; dpadUp: boolean; dpadDown: boolean; dpadLeft: boolean; dpadRight: boolean; leftBumper: boolean; rightBumper: boolean; leftStickButton: boolean; rightStickButton: boolean; back: boolean; start: boolean; guide: boolean };
type GamepadFrame = { robotTimeNs: string; gamepad1: GamepadState; gamepad2: GamepadState };
type SessionSummary = { snapshotCount: number; gamepadFrameCount: number; active: boolean };
type CaptureState = { enabled: boolean; sessionId: string | null };
type TelemetryState = { session: SessionSummary | null; catalog: Catalog | null; snapshots: Snapshot[]; gamepadFrames: GamepadFrame[]; status: { lastError: string | null }; capture?: CaptureState };
type DriverStationStatus = { driver_station_error: string | null };
type DebugCommandArgument = { id: string; label: string; description: string; valueType: string; required: boolean; unit?: string; min?: number; max?: number; enumOptions?: { id: string; label: string }[] };
type DebugCommand = { id: string; label: string; description: string; requiresHumanAcknowledgement: boolean; arguments: DebugCommandArgument[] };
type DebugToolReady = { nodeId: string; toolInstanceId: string; commands: DebugCommand[]; state: string; message?: string };
type DebugStatus = { connected: boolean; tool_ready: DebugToolReady | null; last_event: { type: string; data: Record<string, unknown> } | null };
type DebugCommandResponseData = Record<string, unknown> & { requestId?: string; commandId?: string; result?: string; message?: string };
type CommandInputValue = string | boolean;
type CommandFeedback = { tone: "pending" | "success" | "failure"; message: string };

function isNumericArgument(valueType: string): boolean { return valueType === "int64" || valueType === "float64" || valueType === "int" || valueType === "float" || valueType === "double"; }
function isIntegerArgument(valueType: string): boolean { return valueType === "int64" || valueType === "int"; }

const EMPTY_STATE: TelemetryState = { session: null, catalog: null, snapshots: [], gamepadFrames: [], status: { lastError: null } };
const TRACE_COLORS = ["#4bb8ff", "#4cdd9b", "#ffbc52"];
const MAX_TRACES = 3;
// Keep enough high-frequency loop snapshots to fill the 10-second graph window.
const MAX_HISTORY = 12_000;
const COMMAND_TTL_MS = 1_000;
const COMMAND_RESPONSE_TIMEOUT_MS = COMMAND_TTL_MS + 1_000;
const REPLAY_WINDOW_SECONDS = 10;
const NANOSECONDS_PER_SECOND = 1_000_000_000;
const TIMELINE_PIXELS_PER_SECOND = 96;
const TIMELINE_SUBDIVISIONS_PER_SECOND = 4;
const LOOP_TIME_SIGNAL_ID = "opmode.loopTimeMs";

function createCommandRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `command-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function snapshotTimeNs(snapshot: Snapshot): number {
  const time = Number(snapshot.robotTimeNs);
  return Number.isFinite(time) ? time : 0;
}

function firstSnapshotAtOrAfter(snapshots: Snapshot[], timeNs: number): number {
  const index = snapshots.findIndex((snapshot) => snapshotTimeNs(snapshot) >= timeNs);
  return index === -1 ? Math.max(0, snapshots.length - 1) : index;
}

function numericValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null; }
  return null;
}

function formatValue(value: unknown, unit: string): string {
  const numeric = numericValue(value);
  if (numeric === null) return value == null ? "—" : String(value);
  const rendered = Math.abs(numeric) >= 1000 ? numeric.toFixed(0) : numeric.toFixed(2).replace(/\.00$/, "");
  return unit === "none" ? rendered : `${rendered} ${unit}`;
}

type MotorSignals = {
  commandedPower?: SignalDefinition;
  position?: SignalDefinition;
  velocity?: SignalDefinition;
  current?: SignalDefinition;
  electricalPower?: SignalDefinition;
};

function motorSignals(device: DeviceDefinition, signals: SignalDefinition[]): MotorSignals {
  const forMotor = signals.filter((signal) => signal.deviceId === device.id);
  const find = (quantity: string) => forMotor.find((signal) => signal.quantity === quantity);
  return {
    commandedPower: find("commandedPower"),
    position: find("position"),
    velocity: find("velocity"),
    current: find("current"),
    electricalPower: find("electricalPower"),
  };
}

function MotorMonitor({ device, signals, snapshot }: { device: DeviceDefinition; signals: SignalDefinition[]; snapshot: Snapshot | null }) {
  const metrics = motorSignals(device, signals);
  const value = (signal?: SignalDefinition) => signal && snapshot ? snapshot.values[signal.id] : null;
  const motorStatus = snapshot && metrics.commandedPower && metrics.position && metrics.velocity && metrics.current && metrics.electricalPower ? "LIVE" : "WAITING";
  return <article className="motor-monitor-card">
    <div className="motor-monitor-heading"><div><p className="eyebrow">DC motor</p><strong>{device.label}</strong></div><span className={motorStatus === "LIVE" ? "motor-live" : ""}>{motorStatus}</span></div>
    <dl className="motor-metrics">
      <div><dt>Commanded power</dt><dd>{formatValue(value(metrics.commandedPower), metrics.commandedPower?.unit ?? "normalized")}</dd></div>
      <div><dt>Position</dt><dd>{formatValue(value(metrics.position), metrics.position?.unit ?? "ticks")}</dd></div>
      <div><dt>Velocity</dt><dd>{formatValue(value(metrics.velocity), metrics.velocity?.unit ?? "ticks/s")}</dd></div>
      <div><dt>Current</dt><dd>{formatValue(value(metrics.current), metrics.current?.unit ?? "A")}</dd></div>
      <div><dt>Electrical power</dt><dd>{formatValue(value(metrics.electricalPower), metrics.electricalPower?.unit ?? "W")}</dd></div>
    </dl>
  </article>;
}

function Trace({ signal, snapshots, windowStartNs, color }: { signal: SignalDefinition; snapshots: Snapshot[]; windowStartNs: number; color: string }) {
  const graph = useMemo(() => {
    const points = snapshots.flatMap((snapshot) => {
      const value = numericValue(snapshot.values[signal.id]);
      return value === null ? [] : [{ timeSeconds: (snapshotTimeNs(snapshot) - windowStartNs) / NANOSECONDS_PER_SECOND, value }];
    });
    if (!points.length) return null;
    const values = points.map((point) => point.value);
    const minRaw = Math.min(...values); const maxRaw = Math.max(...values); const padding = minRaw === maxRaw ? Math.max(Math.abs(minRaw) * .08, 1) : (maxRaw - minRaw) * .08;
    const min = minRaw - padding; const max = maxRaw + padding; const span = Math.max(max - min, Number.EPSILON);
    const path = points.map((point, index) => `${index ? "L" : "M"}${(30 + Math.max(0, Math.min(REPLAY_WINDOW_SECONDS, point.timeSeconds)) / REPLAY_WINDOW_SECONDS * 650).toFixed(1)} ${(8 + (1 - (point.value - min) / span) * 62).toFixed(1)}`).join(" ");
    return { path, min, max, latest: points.at(-1)!.value };
  }, [signal.id, snapshots, windowStartNs]);
  return <article className="replay-trace"><div><strong>{signal.label}</strong><span>{graph ? formatValue(graph.latest, signal.unit) : "WAITING"}</span></div>{graph ? <svg viewBox="0 0 700 94" role="img" aria-label={`${signal.label} replay trace over ${REPLAY_WINDOW_SECONDS} seconds`}><line x1="30" x2="680" y1="72" y2="72" className="trace-axis" /><line x1="30" x2="30" y1="5" y2="72" className="replay-marker" /><path d={graph.path} fill="none" stroke={color} strokeWidth="2.5" /><text x="1" y="13">{formatValue(graph.max, signal.unit)}</text><text x="1" y="73">{formatValue(graph.min, signal.unit)}</text><text x="30" y="89">0.0 s</text><text x="635" y="89">{REPLAY_WINDOW_SECONDS.toFixed(1)} s</text></svg> : <small>Waiting for a numeric sample</small>}</article>;
}

function ControllerMonitor({ title, state }: { title: string; state: GamepadState | null }) {
  if (!state) return <section className="controller-monitor"><strong>{title}</strong><small>Waiting for gamepad data</small></section>;
  const pressed = (value: boolean) => value ? "pressed" : "";
  return <section className="controller-monitor"><div><strong>{title}</strong><small>Live input</small></div><div className="controller-monitor-body"><span className={`mini-stick ${pressed(state.leftStickButton)}`} style={{ "--x": state.leftStickX, "--y": state.leftStickY } as CSSProperties} /><span className={`mini-stick ${pressed(state.rightStickButton)}`} style={{ "--x": state.rightStickX, "--y": state.rightStickY } as CSSProperties} /><div className="mini-buttons"><i className={pressed(state.dpadUp)}>↑</i><i className={pressed(state.dpadLeft)}>←</i><i className={pressed(state.dpadDown)}>↓</i><i className={pressed(state.dpadRight)}>→</i></div><div className="mini-buttons face"><i className={pressed(state.y)}>Y</i><i className={pressed(state.x)}>X</i><i className={pressed(state.b)}>B</i><i className={pressed(state.a)}>A</i></div></div><div className="mini-shoulders"><span className={pressed(state.leftBumper)}>LB</span><label className={state.leftTrigger > 0.01 ? "pressed" : ""}><i style={{ width: `${state.leftTrigger * 100}%` }} /><b>LT {state.leftTrigger.toFixed(2)}</b></label><label className={state.rightTrigger > 0.01 ? "pressed" : ""}><i style={{ width: `${state.rightTrigger * 100}%` }} /><b>RT {state.rightTrigger.toFixed(2)}</b></label><span className={pressed(state.rightBumper)}>RB</span></div><div className="mini-system"><span className={pressed(state.back)}>BACK</span><span className={pressed(state.start)}>START</span><span className={pressed(state.guide)}>GUIDE</span></div></section>;
}

function LoopTimeMonitor({ snapshot }: { snapshot: Snapshot | null }) {
  const loopTime = snapshot ? numericValue(snapshot.values[LOOP_TIME_SIGNAL_ID]) : null;
  return <section className="loop-time-monitor"><div><p className="eyebrow">Robot loop</p><strong>Loop time</strong></div><output>{loopTime === null ? "WAITING" : formatValue(loopTime, "ms")}</output></section>;
}

function defaultCommandValue(argument: DebugCommandArgument): CommandInputValue {
  if (argument.valueType === "boolean") return false;
  if (argument.valueType === "enum") return argument.enumOptions?.[0]?.id ?? "";
  return "";
}

function CommandArgumentEditor({ argument, value, onChange }: { argument: DebugCommandArgument; value: CommandInputValue; onChange: (value: CommandInputValue) => void }) {
  const label = argument.label || argument.id;
  const description = argument.description ? <small>{argument.description}</small> : null;
  if (argument.valueType === "boolean") return <div className="command-argument"><span className="command-argument-label">{label}{argument.required ? " *" : ""}</span><button className={`boolean-toggle ${value ? "active" : ""}`} type="button" aria-pressed={value === true} onClick={() => onChange(!(value === true))}>{value === true ? "true" : "false"}</button>{description}</div>;
  if (argument.valueType === "enum") return <fieldset className="command-argument command-enum"><legend>{label}{argument.required ? " *" : ""}</legend><div className="enum-options">{(argument.enumOptions ?? []).map((option) => <button className={value === option.id ? "selected" : ""} type="button" key={option.id} aria-pressed={value === option.id} onClick={() => onChange(option.id)}>{option.label || option.id}</button>)}</div>{description}</fieldset>;
  const numeric = isNumericArgument(argument.valueType);
  return <label className="command-argument"><span className="command-argument-label">{label}{argument.required ? " *" : ""}</span><input type={numeric ? "number" : "text"} step={isIntegerArgument(argument.valueType) ? "1" : numeric ? "any" : undefined} min={numeric && argument.min !== 0 ? argument.min : undefined} max={numeric && argument.max !== 0 ? argument.max : undefined} inputMode={numeric ? "decimal" : undefined} value={String(value)} onChange={(event) => onChange(event.currentTarget.value)} />{argument.unit && <small className="command-argument-unit">Unit: {argument.unit}</small>}{description}</label>;
}

function CommandCard({ command, values, busy, feedback, onValueChange, onRun }: { command: DebugCommand; values: Record<string, CommandInputValue>; busy: boolean; feedback?: CommandFeedback; onValueChange: (argumentId: string, value: CommandInputValue) => void; onRun: () => void }) {
  const buttonLabel = !busy ? "Run" : feedback?.message.startsWith("Sending") ? "Sending…" : "Awaiting…";
  return <article className="command-card"><div className="command-card-heading"><div><p className="eyebrow">Function</p><strong>{command.label || command.id}</strong><code>{command.id}</code></div><button className="command-run" type="button" disabled={busy} onClick={onRun}>{buttonLabel}</button></div>{command.description && <p className="command-description">{command.description}</p>}{command.arguments.length ? <div className="command-arguments">{command.arguments.map((argument) => <CommandArgumentEditor key={argument.id} argument={argument} value={values[argument.id] ?? defaultCommandValue(argument)} onChange={(value) => onValueChange(argument.id, value)} />)}</div> : <p className="command-no-arguments">This function has no arguments.</p>}{feedback && <p className={`command-feedback ${feedback.tone}`} role="status" aria-live="polite">{feedback.message}</p>}</article>;
}

export function TelemetryDashboard({ topbar }: { topbar: ReactNode }) {
  const [telemetry, setTelemetry] = useState<TelemetryState>(EMPTY_STATE);
  const [driverStationError, setDriverStationError] = useState<string | null>(null);
  const [selectedSignals, setSelectedSignals] = useState<string[]>([]);
  const [replayStartTimeNs, setReplayStartTimeNs] = useState<number | null>(null);
  const [followingLatest, setFollowingLatest] = useState(false);
  const [debugStatus, setDebugStatus] = useState<DebugStatus>({ connected: false, tool_ready: null, last_event: null });
  const [commandValues, setCommandValues] = useState<Record<string, Record<string, CommandInputValue>>>({});
  const [commandBusy, setCommandBusy] = useState<Record<string, boolean>>({});
  const [commandFeedback, setCommandFeedback] = useState<Record<string, CommandFeedback>>({});
  const [captureBusy, setCaptureBusy] = useState(false);
  const timelineRef = useRef<HTMLDivElement>(null);
  const pendingCommandKeysByRequestIdRef = useRef<Record<string, string>>({});
  // Older already-running backends may replace the browser request ID. Keep a
  // response briefly so it can be correlated when their HTTP reply arrives.
  const unmatchedCommandResponsesByRequestIdRef = useRef<Record<string, DebugCommandResponseData>>({});
  const commandTimeoutsRef = useRef<Record<string, number>>({});
  const commandResponseHandlerRef = useRef<(response: DebugCommandResponseData) => void>(() => undefined);

  const clearCommandTimeout = (key: string) => {
    const timeout = commandTimeoutsRef.current[key];
    if (timeout !== undefined) {
      window.clearTimeout(timeout);
      delete commandTimeoutsRef.current[key];
    }
  };

  const armCommandTimeout = (requestId: string, key: string) => {
    clearCommandTimeout(key);
    commandTimeoutsRef.current[key] = window.setTimeout(() => {
      if (pendingCommandKeysByRequestIdRef.current[requestId] !== key) return;
      delete pendingCommandKeysByRequestIdRef.current[requestId];
      delete commandTimeoutsRef.current[key];
      setCommandBusy((previous) => ({ ...previous, [key]: false }));
      setCommandFeedback((previous) => ({
        ...previous,
        [key]: { tone: "failure", message: "Not executed — no confirmation from the Control Hub." },
      }));
    }, COMMAND_RESPONSE_TIMEOUT_MS);
  };

  commandResponseHandlerRef.current = (response) => {
    const requestId = typeof response.requestId === "string" ? response.requestId : "";
    const key = requestId ? pendingCommandKeysByRequestIdRef.current[requestId] : undefined;
    if (!key) {
      if (requestId) {
        const unmatched = unmatchedCommandResponsesByRequestIdRef.current;
        unmatched[requestId] = response;
        const oldestRequestId = Object.keys(unmatched)[0];
        if (Object.keys(unmatched).length > 20 && oldestRequestId) delete unmatched[oldestRequestId];
      }
      return;
    }

    const result = typeof response.result === "string" ? response.result : "";
    const robotMessage = typeof response.message === "string" && response.message.trim() ? response.message : "";
    if (result === "debug_command_accepted") {
      armCommandTimeout(requestId, key);
      setCommandFeedback((previous) => ({
        ...previous,
        [key]: { tone: "pending", message: robotMessage ? `Accepted by Control Hub — ${robotMessage}` : "Accepted by Control Hub. Executing…" },
      }));
      return;
    }

    clearCommandTimeout(key);
    delete pendingCommandKeysByRequestIdRef.current[requestId];
    const completed = result === "debug_command_completed";
    const rejectionCode = typeof response.rejectionCode === "string" ? response.rejectionCode : "";
    const detail = robotMessage || rejectionCode || result || "unknown response";
    setCommandBusy((previous) => ({ ...previous, [key]: false }));
    setCommandFeedback((previous) => ({
      ...previous,
      [key]: completed
        ? { tone: "success", message: robotMessage ? `✓ Executed on Control Hub — ${robotMessage}` : "✓ Executed on Control Hub" }
        : { tone: "failure", message: `Not executed — ${detail}` },
    }));
  };

  useEffect(() => {
    let active = true;
    void fetch("/api/data/telemetry").then((response) => response.ok ? response.json() as Promise<TelemetryState> : Promise.reject()).then((next) => { if (active) setTelemetry({ ...EMPTY_STATE, ...next, gamepadFrames: next.gamepadFrames ?? [] }); }).catch(() => undefined);
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${location.host}/ws/telemetry`);
    socket.onmessage = (event: MessageEvent<string>) => {
      const message = JSON.parse(event.data) as { kind: string; data: unknown };
      if (!active) return;
      if (message.kind === "telemetry_state") setTelemetry({ ...EMPTY_STATE, ...message.data as TelemetryState, gamepadFrames: (message.data as TelemetryState).gamepadFrames ?? [] });
      else if (message.kind === "telemetry_catalog") setTelemetry((previous) => ({ ...previous, catalog: message.data as Catalog }));
      else if (message.kind === "telemetry_snapshot") setTelemetry((previous) => ({ ...previous, snapshots: [...previous.snapshots, message.data as Snapshot].slice(-MAX_HISTORY) }));
      else if (message.kind === "telemetry_gamepad") setTelemetry((previous) => ({ ...previous, gamepadFrames: [...previous.gamepadFrames, message.data as GamepadFrame].slice(-MAX_HISTORY) }));
      else if (message.kind === "telemetry_capture") setTelemetry((previous) => ({ ...previous, capture: message.data as CaptureState }));
    };
    return () => { active = false; socket.close(); };
  }, []);

  useEffect(() => {
    let active = true;
    void fetch("/api/debug/status")
      .then((response) => response.ok ? response.json() as Promise<DebugStatus> : Promise.reject())
      .then((status) => { if (active) setDebugStatus(status); })
      .catch(() => undefined);
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${location.host}/ws/debug`);
    socket.onmessage = (event: MessageEvent<string>) => {
      const message = JSON.parse(event.data) as { kind: string; data: DebugStatus | DebugToolReady | { type: string; data: Record<string, unknown> } };
      if (!active) return;
      if (message.kind === "debug_state") setDebugStatus(message.data as DebugStatus);
      else if (message.kind === "debug") {
        const eventData = message.data as { type: string; data: Record<string, unknown> };
        if (eventData.type === "debug_command_response") {
          commandResponseHandlerRef.current(eventData.data as DebugCommandResponseData);
        }
        setDebugStatus((previous) => ({
          ...previous,
          last_event: eventData,
          tool_ready: eventData.type === "debug_tool_ready" ? eventData.data as unknown as DebugToolReady : previous.tool_ready,
        }));
      }
    };
    return () => { active = false; socket.close(); };
  }, []);

  useEffect(() => {
    let active = true;
    const refreshError = () => {
      void fetch("/api/status")
        .then((response) => response.ok ? response.json() as Promise<DriverStationStatus> : Promise.reject())
        .then((status) => { if (active) setDriverStationError(status.driver_station_error ?? null); })
        .catch(() => { if (active) setDriverStationError(null); });
    };
    refreshError();
    const timer = window.setInterval(refreshError, 500);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  useEffect(() => () => {
    Object.values(commandTimeoutsRef.current).forEach((timeout) => window.clearTimeout(timeout));
  }, []);

  useEffect(() => {
    Object.values(commandTimeoutsRef.current).forEach((timeout) => window.clearTimeout(timeout));
    commandTimeoutsRef.current = {};
    pendingCommandKeysByRequestIdRef.current = {};
    unmatchedCommandResponsesByRequestIdRef.current = {};
    setCommandBusy({});
    setCommandFeedback({});
  }, [debugStatus.tool_ready?.nodeId, debugStatus.tool_ready?.toolInstanceId]);

  const numericSignals = useMemo(() => telemetry.catalog?.signals.filter((signal) => signal.valueType === "float64" || signal.valueType === "int64") ?? [], [telemetry.catalog]);
  const motorDevices = useMemo(() => telemetry.catalog?.devices.filter((device) => device.deviceType.toLowerCase().includes("motor")) ?? [], [telemetry.catalog]);
  const latestSnapshot = telemetry.snapshots.at(-1) ?? null;
  useEffect(() => { setSelectedSignals((previous) => { const allowed = previous.filter((id) => numericSignals.some((signal) => signal.id === id)); return allowed.length ? allowed : numericSignals.slice(0, MAX_TRACES).map((signal) => signal.id); }); }, [numericSignals]);
  const latestWindowStartIndex = useMemo(() => telemetry.snapshots.length ? firstSnapshotAtOrAfter(telemetry.snapshots, snapshotTimeNs(telemetry.snapshots.at(-1)!) - REPLAY_WINDOW_SECONDS * NANOSECONDS_PER_SECOND) : 0, [telemetry.snapshots]);
  useEffect(() => {
    if (replayStartTimeNs === null && telemetry.snapshots.length) {
      const startTimeNs = snapshotTimeNs(telemetry.snapshots[latestWindowStartIndex]);
      setReplayStartTimeNs(startTimeNs);
      requestAnimationFrame(() => timelineRef.current?.scrollTo({ left: Math.max(0, (startTimeNs - snapshotTimeNs(telemetry.snapshots[0])) / NANOSECONDS_PER_SECOND * TIMELINE_PIXELS_PER_SECOND) }));
    }
  }, [latestWindowStartIndex, replayStartTimeNs, telemetry.snapshots]);

  const replayStartIndex = replayStartTimeNs === null ? 0 : firstSnapshotAtOrAfter(telemetry.snapshots, replayStartTimeNs);
  const replaySnapshot = telemetry.snapshots[replayStartIndex] ?? null;
  const replaySnapshots = useMemo(() => {
    if (!replaySnapshot) return [];
    const windowEndNs = snapshotTimeNs(replaySnapshot) + REPLAY_WINDOW_SECONDS * NANOSECONDS_PER_SECOND;
    return telemetry.snapshots.slice(replayStartIndex).filter((snapshot) => snapshotTimeNs(snapshot) <= windowEndNs);
  }, [replaySnapshot, replayStartIndex, telemetry.snapshots]);
  const controllerSnapshot = followingLatest ? replaySnapshots.at(-1) ?? replaySnapshot : replaySnapshot;
  const replayGamepads = useMemo(() => controllerSnapshot ? telemetry.gamepadFrames.reduce<GamepadFrame | null>((closest, frame) => !closest || Math.abs(Number(frame.robotTimeNs) - Number(controllerSnapshot.robotTimeNs)) < Math.abs(Number(closest.robotTimeNs) - Number(controllerSnapshot.robotTimeNs)) ? frame : closest, null) : null, [controllerSnapshot, telemetry.gamepadFrames]);
  const traces = numericSignals.filter((signal) => selectedSignals.includes(signal.id));
  const timeline = useMemo(() => {
    if (!telemetry.snapshots.length) return { originNs: 0, durationSeconds: REPLAY_WINDOW_SECONDS, width: REPLAY_WINDOW_SECONDS * TIMELINE_PIXELS_PER_SECOND };
    const originNs = snapshotTimeNs(telemetry.snapshots[0]);
    const endNs = snapshotTimeNs(telemetry.snapshots.at(-1)!);
    const durationSeconds = Math.max(REPLAY_WINDOW_SECONDS, (endNs - originNs) / NANOSECONDS_PER_SECOND);
    return { originNs, durationSeconds, width: durationSeconds * TIMELINE_PIXELS_PER_SECOND };
  }, [telemetry.snapshots]);
  useEffect(() => {
    if (!followingLatest || !telemetry.snapshots.length) return;
    const startTimeNs = snapshotTimeNs(telemetry.snapshots[latestWindowStartIndex]);
    setReplayStartTimeNs(startTimeNs);
    const frame = requestAnimationFrame(() => timelineRef.current?.scrollTo({ left: Math.max(0, (startTimeNs - timeline.originNs) / NANOSECONDS_PER_SECOND * TIMELINE_PIXELS_PER_SECOND) }));
    return () => cancelAnimationFrame(frame);
  }, [followingLatest, latestWindowStartIndex, telemetry.snapshots, timeline.originNs]);
  const selectReplay = (index: number, scroll = true) => {
    const next = Math.max(0, Math.min(index, telemetry.snapshots.length - 1));
    const nextTimeNs = snapshotTimeNs(telemetry.snapshots[next]);
    setReplayStartTimeNs(nextTimeNs);
    if (scroll) requestAnimationFrame(() => timelineRef.current?.scrollTo({ left: Math.max(0, (nextTimeNs - timeline.originNs) / NANOSECONDS_PER_SECOND * TIMELINE_PIXELS_PER_SECOND), behavior: "smooth" }));
  };
  const showLatest = () => {
    setFollowingLatest(true);
    selectReplay(latestWindowStartIndex, true);
  };
  const onTimelineScroll = () => {
    if (followingLatest) return;
    const element = timelineRef.current;
    if (!element || !telemetry.snapshots.length) return;
    const timeAtLeftEdge = timeline.originNs + element.scrollLeft / TIMELINE_PIXELS_PER_SECOND * NANOSECONDS_PER_SECOND;
    setReplayStartTimeNs(snapshotTimeNs(telemetry.snapshots[firstSnapshotAtOrAfter(telemetry.snapshots, timeAtLeftEdge)]));
  };
  const toggleSignal = (id: string) => setSelectedSignals((previous) => previous.includes(id) ? previous.filter((value) => value !== id) : [...previous, id].slice(-MAX_TRACES));
  const commands = debugStatus.tool_ready?.commands ?? [];
  const commandKey = (command: DebugCommand) => `${debugStatus.tool_ready?.nodeId ?? ""}:${debugStatus.tool_ready?.toolInstanceId ?? ""}:${command.id}`;
  const valueFor = (command: DebugCommand, argument: DebugCommandArgument) => commandValues[commandKey(command)]?.[argument.id] ?? defaultCommandValue(argument);
  const setCommandValue = (command: DebugCommand, argumentId: string, value: CommandInputValue) => setCommandValues((previous) => ({ ...previous, [commandKey(command)]: { ...previous[commandKey(command)], [argumentId]: value } }));
  const encodeCommandArguments = (command: DebugCommand): Record<string, unknown> => {
    const encoded: Record<string, unknown> = {};
    for (const argument of command.arguments) {
      const value = valueFor(command, argument);
      if (isNumericArgument(argument.valueType) && typeof value === "string") {
        if (!value.trim()) { if (argument.required) throw new Error(`${argument.label || argument.id} is required`); continue; }
        const numeric = Number(value);
        if (!Number.isFinite(numeric) || (isIntegerArgument(argument.valueType) && !Number.isInteger(numeric))) throw new Error(`${argument.label || argument.id} must be a valid ${isIntegerArgument(argument.valueType) ? "integer" : "number"}`);
        encoded[argument.id] = numeric;
      } else encoded[argument.id] = value;
    }
    return encoded;
  };
  const runCommand = async (command: DebugCommand) => {
    const ready = debugStatus.tool_ready;
    const key = commandKey(command);
    if (!ready) {
      setCommandFeedback((previous) => ({ ...previous, [key]: { tone: "failure", message: "Not executed — start an OpMode to advertise TCP commands." } }));
      return;
    }
    let argumentsForCommand: Record<string, unknown>;
    try {
      argumentsForCommand = encodeCommandArguments(command);
    } catch (error) {
      setCommandFeedback((previous) => ({
        ...previous,
        [key]: { tone: "failure", message: `Not executed — ${error instanceof Error ? error.message : "invalid command arguments"}` },
      }));
      return;
    }
    Object.entries(pendingCommandKeysByRequestIdRef.current).forEach(([requestId, pendingKey]) => {
      if (pendingKey === key) delete pendingCommandKeysByRequestIdRef.current[requestId];
    });
    clearCommandTimeout(key);
    const requestId = createCommandRequestId();
    pendingCommandKeysByRequestIdRef.current[requestId] = key;
    armCommandTimeout(requestId, key);
    setCommandBusy((previous) => ({ ...previous, [key]: true }));
    setCommandFeedback((previous) => ({ ...previous, [key]: { tone: "pending", message: "Sending to Control Hub…" } }));
    try {
      const response = await fetch("/api/debug/commands", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          node_id: ready.nodeId,
          tool_instance_id: ready.toolInstanceId,
          command_id: command.id,
          arguments: argumentsForCommand,
          ttl_ms: COMMAND_TTL_MS,
          request_id: requestId,
        }),
      });
      const body = await response.json() as { request_id?: string; detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "The robot rejected the command request");
      const confirmedRequestId = body.request_id || requestId;
      if (confirmedRequestId !== requestId && pendingCommandKeysByRequestIdRef.current[requestId] === key) {
        delete pendingCommandKeysByRequestIdRef.current[requestId];
        pendingCommandKeysByRequestIdRef.current[confirmedRequestId] = key;
        armCommandTimeout(confirmedRequestId, key);
      }
      // A fast Control Hub may have already completed the command while this
      // HTTP response was in flight. This also supports an older backend that
      // generated its own request ID before it was upgraded to echo ours.
      const earlyResponse = unmatchedCommandResponsesByRequestIdRef.current[confirmedRequestId];
      if (earlyResponse) {
        delete unmatchedCommandResponsesByRequestIdRef.current[confirmedRequestId];
        commandResponseHandlerRef.current(earlyResponse);
      } else if (pendingCommandKeysByRequestIdRef.current[confirmedRequestId] === key) {
        setCommandFeedback((previous) => ({ ...previous, [key]: { tone: "pending", message: "Awaiting Control Hub confirmation…" } }));
      }
    } catch (error) {
      if (pendingCommandKeysByRequestIdRef.current[requestId] !== key) return;
      clearCommandTimeout(key);
      delete pendingCommandKeysByRequestIdRef.current[requestId];
      setCommandBusy((previous) => ({ ...previous, [key]: false }));
      setCommandFeedback((previous) => ({
        ...previous,
        [key]: { tone: "failure", message: `Not executed — ${error instanceof Error ? error.message : "could not send command"}` },
      }));
    }
  };

  const toggleCapture = async () => {
    const enabled = !telemetry.capture?.enabled;
    setCaptureBusy(true);
    try {
      const response = await fetch("/api/data/telemetry/capture", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }),
      });
      const body = await response.json() as CaptureState & { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "Could not change capture state");
      setTelemetry((previous) => ({ ...previous, capture: body }));
    } catch (error) {
      setDriverStationError(error instanceof Error ? error.message : "Could not change capture state");
    } finally {
      setCaptureBusy(false);
    }
  };

  return <main className="telemetry-dashboard">
    {topbar}
    {driverStationError && <p className="telemetry-error"><strong>Robot Controller error:</strong> {driverStationError}</p>}
    {telemetry.status.lastError && <p className="telemetry-error">Latest telemetry packet rejected: {telemetry.status.lastError}</p>}
    <section className="tcp-command-panel panel"><div className="panel-heading"><div><p className="eyebrow">TCP command</p><strong>Robot functions</strong></div><span className={commands.length ? "alliance-ready" : ""}>{commands.length ? `${commands.length} READY` : "WAITING"}</span></div><p className="notice">Every function advertised by the running OpMode appears here with its typed arguments.</p>{commands.length ? <div className="command-card-list">{commands.map((command) => { const key = commandKey(command); return <CommandCard key={command.id} command={command} values={commandValues[key] ?? {}} busy={commandBusy[key] ?? false} feedback={commandFeedback[key]} onValueChange={(argumentId, value) => setCommandValue(command, argumentId, value)} onRun={() => void runCommand(command)} />; })}</div> : <p className="empty-state">Waiting for an OpMode to advertise its TCP functions.</p>}</section>
    <section className="editor-timeline panel"><div><p className="eyebrow">Instant replay · {REPLAY_WINDOW_SECONDS}-second window</p><strong>{replaySnapshot ? `Starts at sample #${replaySnapshot.sampleSequence} · ${replaySnapshots.length} frames` : "Waiting for samples"}</strong><button className="capture-button" type="button" disabled={!telemetry.snapshots.length || captureBusy} onClick={() => void toggleCapture()}>{telemetry.capture?.enabled ? "Capture override · ON" : "Capture override"}</button><button className="secondary" type="button" disabled={!telemetry.snapshots.length} onClick={showLatest}>{followingLatest ? `Following latest ${REPLAY_WINDOW_SECONDS} s` : `Show latest ${REPLAY_WINDOW_SECONDS} s`}</button></div><div className="timeline-viewport" ref={timelineRef} onPointerDown={() => setFollowingLatest(false)} onScroll={onTimelineScroll} onWheel={(event) => { setFollowingLatest(false); if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) { event.currentTarget.scrollLeft += event.deltaY; event.preventDefault(); } }}><div className="timeline-track" style={{ width: `${timeline.width}px` }}>{Array.from({ length: Math.floor(timeline.durationSeconds) + 1 }, (_, second) => <span className="timeline-label" style={{ left: `${second * TIMELINE_PIXELS_PER_SECOND}px` }} key={second}>{second}s</span>)}{Array.from({ length: Math.floor(timeline.durationSeconds * TIMELINE_SUBDIVISIONS_PER_SECOND) + 1 }, (_, tick) => <i aria-hidden="true" className={`timeline-ruler-tick ${tick % TIMELINE_SUBDIVISIONS_PER_SECOND === 0 ? "major" : ""}`} style={{ left: `${tick / TIMELINE_SUBDIVISIONS_PER_SECOND * TIMELINE_PIXELS_PER_SECOND}px` }} key={tick} />)}</div></div></section>
    <section className="motor-monitor-section panel"><div className="motor-monitor-section-heading"><div><p className="eyebrow">Live TCP stream</p><h2>Motor telemetry</h2></div><span>{motorDevices.length ? `${motorDevices.length} motors` : "Waiting for motor catalog"}</span></div>{motorDevices.length ? <div className="motor-monitor-grid">{motorDevices.map((device) => <MotorMonitor key={device.id} device={device} signals={telemetry.catalog?.signals ?? []} snapshot={latestSnapshot} />)}</div> : <p className="empty-state">Start an OpMode with DC motors to receive motor telemetry.</p>}</section>
    <section className="telemetry-editor"><aside className="trace-picker panel"><div><p className="eyebrow">Traces</p><strong>{traces.length} / {MAX_TRACES}</strong></div>{numericSignals.map((signal) => <label key={signal.id}><input type="checkbox" checked={selectedSignals.includes(signal.id)} onChange={() => toggleSignal(signal.id)} />{signal.label}</label>)}</aside><section className="trace-stack">{traces.length ? traces.map((signal, index) => <Trace key={signal.id} signal={signal} snapshots={replaySnapshots} windowStartNs={replaySnapshot ? snapshotTimeNs(replaySnapshot) : 0} color={TRACE_COLORS[index]} />) : <p className="empty-state">Choose a numeric trace.</p>}</section><aside className="controller-stack panel"><div><p className="eyebrow">Driver inputs</p><strong>{telemetry.session?.gamepadFrameCount ?? 0} frames</strong></div><ControllerMonitor title="Driver 1" state={replayGamepads?.gamepad1 ?? null} /><ControllerMonitor title="Driver 2" state={replayGamepads?.gamepad2 ?? null} /><LoopTimeMonitor snapshot={controllerSnapshot} /></aside></section>
  </main>;
}
