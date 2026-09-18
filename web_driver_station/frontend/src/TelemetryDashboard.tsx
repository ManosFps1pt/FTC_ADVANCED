import { type CSSProperties, type ReactNode, useEffect, useMemo, useRef, useState } from "react";

type DeviceDefinition = { id: string; label: string; subsystem: string; deviceType: string };
type SignalDefinition = { id: string; label: string; deviceId?: string; quantity: string; unit: string; valueType: string; role?: string };
type Catalog = { schemaRevision: number; devices: DeviceDefinition[]; signals: SignalDefinition[] };
type Snapshot = { sampleSequence: string; robotTimeNs: string; highlighted?: boolean; highlightSource?: "control_hub" | "telemetry_lab" | null; values: Record<string, unknown> };
type GamepadState = { leftStickX: number; leftStickY: number; rightStickX: number; rightStickY: number; leftTrigger: number; rightTrigger: number; a: boolean; b: boolean; x: boolean; y: boolean; dpadUp: boolean; dpadDown: boolean; dpadLeft: boolean; dpadRight: boolean; leftBumper: boolean; rightBumper: boolean; leftStickButton: boolean; rightStickButton: boolean; back: boolean; start: boolean; guide: boolean };
type GamepadFrame = { robotTimeNs: string; gamepad1: GamepadState; gamepad2: GamepadState };
type SessionSummary = { snapshotCount: number; gamepadFrameCount: number; active: boolean };
type TelemetryState = { session: SessionSummary | null; catalog: Catalog | null; snapshots: Snapshot[]; gamepadFrames: GamepadFrame[]; status: { lastError: string | null } };
type DriverStationStatus = { driver_station_error: string | null };
type DebugCommandArgument = { id: string; label: string; description: string; valueType: string; required: boolean; unit?: string; min?: number; max?: number; enumOptions?: { id: string; label: string }[] };
type DebugCommand = { id: string; label: string; description: string; requiresHumanAcknowledgement: boolean; arguments: DebugCommandArgument[] };
type DebugToolReady = { nodeId: string; toolInstanceId: string; commands: DebugCommand[]; state: string; message?: string };
type DebugStatus = { connected: boolean; tool_ready: DebugToolReady | null; last_event: { type: string; data: Record<string, unknown> } | null };
type DebugCommandResponseData = Record<string, unknown> & { requestId?: string; commandId?: string; result?: string; message?: string };
type CommandInputValue = string | boolean;
type CommandFeedback = { tone: "pending" | "success" | "failure"; message: string };
type Pose2dValue = { x: number; y: number; headingRad: number };
type RgbLight = { device: DeviceDefinition; signal: SignalDefinition };
type RgbLightColor = { label: string; color: string };

function isNumericArgument(valueType: string): boolean { return valueType === "int64" || valueType === "float64" || valueType === "int" || valueType === "float" || valueType === "double"; }
function isIntegerArgument(valueType: string): boolean { return valueType === "int64" || valueType === "int"; }

const EMPTY_STATE: TelemetryState = { session: null, catalog: null, snapshots: [], gamepadFrames: [], status: { lastError: null } };
const TRACE_COLORS = ["#4bb8ff", "#4cdd9b", "#ffbc52"];
const MAX_TRACES = 3;
const MAX_MOTOR_CURRENT_AMPS = 9;
const MAX_ROBOT_CURRENT_AMPS = 20;
// Keep enough high-frequency loop snapshots to fill the 10-second graph window.
const MAX_HISTORY = 12_000;
const COMMAND_TTL_MS = 1_000;
const COMMAND_RESPONSE_TIMEOUT_MS = COMMAND_TTL_MS + 1_000;
const LIVE_WINDOW_SECONDS = 10;
const NANOSECONDS_PER_SECOND = 1_000_000_000;
const PEDRO_FIELD_SIZE_INCHES = 144;
const ROBOT_BOX_SIZE_INCHES = 18;
const RGB_LIGHT_COLORS: Array<RgbLightColor & { dutyCycle: number }> = [
  { label: "Off", dutyCycle: 0.000, color: "#05080d" },
  { label: "Red", dutyCycle: 0.277, color: "#ff210b" },
  { label: "Orange", dutyCycle: 0.333, color: "#ff7900" },
  { label: "Yellow", dutyCycle: 0.388, color: "#ffe600" },
  { label: "Sage", dutyCycle: 0.444, color: "#9fc85b" },
  { label: "Green", dutyCycle: 0.500, color: "#12aa3e" },
  { label: "Azure", dutyCycle: 0.555, color: "#078fd4" },
  { label: "Blue", dutyCycle: 0.611, color: "#1465f0" },
  { label: "Indigo", dutyCycle: 0.666, color: "#4720cf" },
  { label: "Violet", dutyCycle: 0.722, color: "#8d2de2" },
  { label: "White", dutyCycle: 1.000, color: "#ffffff" },
];

function createCommandRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `command-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

function snapshotTimeNs(snapshot: Snapshot): number {
  const time = Number(snapshot.robotTimeNs);
  return Number.isFinite(time) ? time : 0;
}

function numericValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null; }
  return null;
}

function pose2dValue(value: unknown): Pose2dValue | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const x = numericValue(candidate.x);
  const y = numericValue(candidate.y);
  const headingRad = numericValue(candidate.headingRad);
  return x === null || y === null || headingRad === null ? null : { x, y, headingRad };
}

function formatValue(value: unknown, unit: string): string {
  const numeric = numericValue(value);
  if (numeric === null) return value == null ? "—" : String(value);
  const rendered = Math.abs(numeric) >= 1000 ? numeric.toFixed(0) : numeric.toFixed(2).replace(/\.00$/, "");
  return unit === "none" ? rendered : `${rendered} ${unit}`;
}

function interpolateColor(start: string, end: string, amount: number): string {
  const startValue = Number.parseInt(start.slice(1), 16);
  const endValue = Number.parseInt(end.slice(1), 16);
  const channel = (shift: number) => Math.round(((startValue >> shift) & 255) + (((endValue >> shift) & 255) - ((startValue >> shift) & 255)) * amount);
  return `rgb(${channel(16)} ${channel(8)} ${channel(0)})`;
}

function heatStyle(currentAmps: number | null, limitAmps: number): CSSProperties {
  const ratio = currentAmps === null ? 0 : Math.max(0, Math.min(1, currentAmps / limitAmps));
  return {
    "--current-color": currentAmps === null ? "#36536d" : interpolateColor("#42c77a", "#e64f5b", ratio),
    "--current-surface": currentAmps === null ? "#0b1d33" : interpolateColor("#113c2a", "#4c1e29", ratio),
  } as CSSProperties;
}

function rgbLightColor(value: unknown): RgbLightColor | null {
  const dutyCycle = numericValue(value);
  if (dutyCycle === null) return null;
  if (dutyCycle < RGB_LIGHT_COLORS[1].dutyCycle) return RGB_LIGHT_COLORS[0];
  if (dutyCycle > RGB_LIGHT_COLORS[9].dutyCycle) return RGB_LIGHT_COLORS[10];
  const exactColor = RGB_LIGHT_COLORS.find((candidate) => Math.abs(dutyCycle - candidate.dutyCycle) < 0.002);
  if (exactColor) return exactColor;
  for (let index = 1; index < RGB_LIGHT_COLORS.length - 1; index += 1) {
    const current = RGB_LIGHT_COLORS[index];
    const next = RGB_LIGHT_COLORS[index + 1];
    if (dutyCycle > next.dutyCycle) continue;
    const amount = (dutyCycle - current.dutyCycle) / (next.dutyCycle - current.dutyCycle);
    return { label: "Color transition", color: interpolateColor(current.color, next.color, amount) };
  }
  return RGB_LIGHT_COLORS[10];
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
  const currentAmps = numericValue(value(metrics.current));
  const electricalPower = value(metrics.electricalPower);
  const motorStatus = snapshot && metrics.commandedPower && metrics.velocity && metrics.current && metrics.electricalPower ? "LIVE" : "WAITING";
  return <article className="motor-monitor-card">
    <div className="motor-monitor-heading"><strong>{device.label}</strong><span className={motorStatus === "LIVE" ? "motor-live" : ""}>{motorStatus}</span></div>
    <dl className="motor-metrics">
      <div><dt>pow</dt><dd>{formatValue(value(metrics.commandedPower), metrics.commandedPower?.unit ?? "normalized")}</dd></div>
      <div><dt>vel</dt><dd>{formatValue(value(metrics.velocity), "t/s")}</dd></div>
      <div className="motor-electrical-readout" style={heatStyle(currentAmps, MAX_MOTOR_CURRENT_AMPS)}><div><dd>{formatValue(currentAmps, metrics.current?.unit ?? "A")}</dd></div><div><dd>{formatValue(electricalPower, metrics.electricalPower?.unit ?? "W")}</dd></div></div>
    </dl>
  </article>;
}

function powerBarLabel(device: DeviceDefinition): string {
  const labels: Record<string, string> = { "drive.leftFront": "LF", "drive.leftBack": "LB", "drive.rightBack": "RB", "drive.rightFront": "RF", intake: "INT", "shooter.follower": "SF", "shooter.primary": "SP", turret: "TUR" };
  return labels[device.id] ?? device.label.slice(0, 3).toUpperCase();
}

function PowerBar({ label, title, currentAmps, watts, maxCurrentAmps }: { label: string; title: string; currentAmps: number | null; watts: number | null; maxCurrentAmps: number }) {
  // Current is the safety-relevant scale: voltage sag must not make a heavily
  // loaded or stalled motor look less severe just because its wattage fell.
  const ratio = currentAmps === null ? 0 : Math.max(0, Math.min(1, currentAmps / maxCurrentAmps));
  const style = { ...heatStyle(currentAmps, maxCurrentAmps), "--power-ratio": String(ratio) } as CSSProperties;
  return <article className="power-bar" style={style} title={`${title}: ${formatValue(watts, "W")} · ${formatValue(currentAmps, "A")}`}><span className="power-bar-name">{label}</span><div className="power-bar-track"><i /></div><strong>{watts === null ? "—" : `${watts.toFixed(0)} W`}</strong><small>{currentAmps === null ? "—" : `${currentAmps.toFixed(1)} A`}</small></article>;
}

function PowerMonitor({ devices, signals, snapshot }: { devices: DeviceDefinition[]; signals: SignalDefinition[]; snapshot: Snapshot | null }) {
  const voltageSignal = signals.find((signal) => signal.id === "robot.voltage" || signal.quantity === "voltage");
  const robotCurrentSignal = signals.find((signal) => signal.id === "robot.currentAmps");
  const voltage = voltageSignal && snapshot ? numericValue(snapshot.values[voltageSignal.id]) : null;
  const motorPowers = devices.slice(0, 8).map((device) => {
    const metrics = motorSignals(device, signals);
    const currentAmps = metrics.current && snapshot ? numericValue(snapshot.values[metrics.current.id]) : null;
    const reportedWatts = metrics.electricalPower && snapshot ? numericValue(snapshot.values[metrics.electricalPower.id]) : null;
    return { device, currentAmps, watts: reportedWatts ?? (currentAmps !== null && voltage !== null ? currentAmps * voltage : null) };
  });
  const knownCurrents = motorPowers.map((motor) => motor.currentAmps).filter((current): current is number => current !== null);
  const knownWatts = motorPowers.map((motor) => motor.watts).filter((watts): watts is number => watts !== null);
  const totalCurrentAmps = knownCurrents.length ? knownCurrents.reduce((sum, current) => sum + current, 0) : null;
  const totalWatts = knownWatts.length ? knownWatts.reduce((sum, watts) => sum + watts, 0) : null;
  const directRobotCurrent = robotCurrentSignal && snapshot ? numericValue(snapshot.values[robotCurrentSignal.id]) : null;
  const robotCurrentAmps = directRobotCurrent ?? totalCurrentAmps;
  const robotWatts = directRobotCurrent !== null && voltage !== null ? directRobotCurrent * voltage : totalWatts;
  const robotPowerTitle = directRobotCurrent !== null ? "Robot current from REV hubs" : "Total streamed motor load";
  return <section className="power-monitor panel"><span className="power-monitor-voltage">{voltage === null ? "WAITING" : `${voltage.toFixed(2)} V`}</span><div className="power-bar-grid">{motorPowers.map(({ device, currentAmps, watts }) => <PowerBar key={device.id} label={powerBarLabel(device)} title={device.label} currentAmps={currentAmps} watts={watts} maxCurrentAmps={MAX_MOTOR_CURRENT_AMPS} />)}<PowerBar label="ALL" title={robotPowerTitle} currentAmps={robotCurrentAmps} watts={robotWatts} maxCurrentAmps={MAX_ROBOT_CURRENT_AMPS} /></div></section>;
}

function RgbLightPanel({ lights, snapshot }: { lights: RgbLight[]; snapshot: Snapshot | null }) {
  return <section className="rgb-light-panel panel">
    <div className="rgb-light-panel-heading"><div><p className="eyebrow">Live indicator output</p><h2>RGB lights</h2></div><span>{lights.length ? `${lights.length} / 2 LIGHT${lights.length === 1 ? "" : "S"}` : "WAITING"}</span></div>
    {lights.length ? <div className="rgb-light-grid">{lights.map(({ device, signal }) => {
      const output = snapshot ? rgbLightColor(snapshot.values[signal.id]) : null;
      return <article className="rgb-light-card" key={device.id}>
        <span className="rgb-light-swatch" aria-hidden="true" style={{ "--light-color": output?.color ?? "#162637" } as CSSProperties} />
        <div><strong>{device.label}</strong><small>{output?.label ?? "Waiting for light signal"}</small></div>
      </article>;
    })}</div> : <p className="empty-state">Waiting for an RGB indicator light to be advertised by the OpMode.</p>}
  </section>;
}

function LiveTrace({ signal, snapshots, windowStartNs, color }: { signal: SignalDefinition; snapshots: Snapshot[]; windowStartNs: number; color: string }) {
  const graph = useMemo(() => {
    const points = snapshots.flatMap((snapshot) => {
      const value = numericValue(snapshot.values[signal.id]);
      return value === null ? [] : [{ timeSeconds: (snapshotTimeNs(snapshot) - windowStartNs) / NANOSECONDS_PER_SECOND, value }];
    });
    if (!points.length) return null;
    const values = points.map((point) => point.value);
    const minRaw = Math.min(...values); const maxRaw = Math.max(...values); const padding = minRaw === maxRaw ? Math.max(Math.abs(minRaw) * .08, 1) : (maxRaw - minRaw) * .08;
    const min = minRaw - padding; const max = maxRaw + padding; const span = Math.max(max - min, Number.EPSILON);
    const path = points.map((point, index) => `${index ? "L" : "M"}${(30 + Math.max(0, Math.min(LIVE_WINDOW_SECONDS, point.timeSeconds)) / LIVE_WINDOW_SECONDS * 650).toFixed(1)} ${(6 + (1 - (point.value - min) / span) * 86).toFixed(1)}`).join(" ");
    const gridRows = Array.from({ length: 5 }, (_, index) => ({
      y: 6 + index * 21.5,
      value: max - (max - min) * index / 4,
    }));
    const timeTicks = Array.from({ length: 5 }, (_, index) => ({
      x: 30 + index * 162.5,
      seconds: LIVE_WINDOW_SECONDS * index / 4,
    }));
    return { path, gridRows, timeTicks, latest: points.at(-1)!.value };
  }, [signal.id, snapshots, windowStartNs]);
  return <article className="replay-trace live-trace"><div><strong>{signal.label}</strong><span>{graph ? formatValue(graph.latest, signal.unit) : "WAITING"}</span></div>{graph ? <svg viewBox="0 0 700 116" preserveAspectRatio="none" role="img" aria-label={`${signal.label} live trace over the last ${LIVE_WINDOW_SECONDS} seconds`}><rect x="30" y="6" width="650" height="86" className="trace-background" />{graph.gridRows.map((row, index) => <g key={`row-${index}`}><line x1="30" x2="680" y1={row.y} y2={row.y} className="trace-grid-line" /><text x="1" y={row.y + 3}>{formatValue(row.value, signal.unit)}</text></g>)}{graph.timeTicks.map((tick, index) => <g key={`time-${index}`}><line x1={tick.x} x2={tick.x} y1="6" y2="92" className="trace-grid-line trace-grid-time" /><text x={tick.x} y="109" textAnchor={index === 0 ? "start" : index === 4 ? "end" : "middle"}>{tick.seconds.toFixed(1)} s</text></g>)}<path d={graph.path} fill="none" stroke={color} strokeWidth="2.5" /></svg> : <small>Waiting for a numeric sample</small>}</article>;
}

function FieldMap({ signal, snapshot }: { signal: SignalDefinition | undefined; snapshot: Snapshot | null }) {
  const pose = signal && snapshot ? pose2dValue(snapshot.values[signal.id]) : null;
  const headingDegrees = pose ? pose.headingRad * 180 / Math.PI : 0;
  const grid = Array.from({ length: 7 }, (_, index) => index * 24);
  const live = pose !== null;
  return <section className="field-map-section panel">
    <div className="field-map-heading"><div><p className="eyebrow">PedroPathing field</p><h2>Localization</h2></div><span className={live ? "field-map-live" : ""}>{live ? "POSE LIVE" : signal ? "POSE UNAVAILABLE" : "WAITING"}</span></div>
    {signal && <p className="field-map-signal">{signal.label} · 0–144 in · +X → · +Y ↑ · heading CCW from +X</p>}
    <div className="field-map-layout">
      <svg className="field-map" viewBox="-12 -8 164 164" role="img" aria-label={live ? `Robot pose X ${pose.x.toFixed(1)}, Y ${pose.y.toFixed(1)}, heading ${headingDegrees.toFixed(0)} degrees` : "PedroPathing field waiting for a pose"}>
        <rect className="field-map-boundary" x="0" y="0" width={PEDRO_FIELD_SIZE_INCHES} height={PEDRO_FIELD_SIZE_INCHES} />
        <g className="field-map-grid">
          {grid.map((coordinate) => <line key={`vertical-${coordinate}`} x1={coordinate} x2={coordinate} y1="0" y2={PEDRO_FIELD_SIZE_INCHES} />)}
          {grid.map((coordinate) => <line key={`horizontal-${coordinate}`} x1="0" x2={PEDRO_FIELD_SIZE_INCHES} y1={coordinate} y2={coordinate} />)}
        </g>
        <g className="field-map-labels">
          {grid.map((coordinate) => <text key={`x-${coordinate}`} x={coordinate} y="153" textAnchor="middle">{coordinate}</text>)}
          {grid.map((coordinate) => <text key={`y-${coordinate}`} x="-3" y={PEDRO_FIELD_SIZE_INCHES - coordinate + 2} textAnchor="end">{coordinate}</text>)}
          <text x="72" y="161" textAnchor="middle">X (in)</text><text x="-10" y="72" textAnchor="middle" transform="rotate(-90 -10 72)">Y (in)</text>
        </g>
        {pose && <g transform={`translate(0 ${PEDRO_FIELD_SIZE_INCHES}) scale(1 -1)`}><g transform={`translate(${pose.x} ${pose.y}) rotate(${headingDegrees})`}>
          <rect className="field-map-robot" x={-ROBOT_BOX_SIZE_INCHES / 2} y={-ROBOT_BOX_SIZE_INCHES / 2} width={ROBOT_BOX_SIZE_INCHES} height={ROBOT_BOX_SIZE_INCHES} rx="1.5" />
          <path className="field-map-forward" d="M9 0 L2.5 3.5 L2.5 -3.5 Z" />
        </g></g>}
      </svg>
      <dl className="field-map-values"><div><dt>X</dt><dd>{pose ? `${pose.x.toFixed(2)} in` : "—"}</dd></div><div><dt>Y</dt><dd>{pose ? `${pose.y.toFixed(2)} in` : "—"}</dd></div><div><dt>Heading</dt><dd>{pose ? `${headingDegrees.toFixed(1)}°` : "—"}</dd></div><div><dt>Channel</dt><dd>{signal?.id ?? "Waiting"}</dd></div></dl>
    </div>
  </section>;
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

export function TelemetryDashboard({ topbar, opModeControl }: { topbar: ReactNode; opModeControl: ReactNode }) {
  const [telemetry, setTelemetry] = useState<TelemetryState>(EMPTY_STATE);
  const [driverStationError, setDriverStationError] = useState<string | null>(null);
  const [selectedSignals, setSelectedSignals] = useState<string[]>([]);
  const [tracePickerOpen, setTracePickerOpen] = useState(false);
  const [debugStatus, setDebugStatus] = useState<DebugStatus>({ connected: false, tool_ready: null, last_event: null });
  const [commandValues, setCommandValues] = useState<Record<string, Record<string, CommandInputValue>>>({});
  const [commandBusy, setCommandBusy] = useState<Record<string, boolean>>({});
  const [commandFeedback, setCommandFeedback] = useState<Record<string, CommandFeedback>>({});
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
  const motorDevices = useMemo(() => {
    const driveOrder = ["drive.leftFront", "drive.leftBack", "drive.rightBack", "drive.rightFront"];
    return (telemetry.catalog?.devices ?? [])
      .filter((device) => device.deviceType.toLowerCase().includes("motor"))
      .sort((left, right) => {
        const leftRank = driveOrder.indexOf(left.id);
        const rightRank = driveOrder.indexOf(right.id);
        if (leftRank >= 0 || rightRank >= 0) return (leftRank < 0 ? driveOrder.length : leftRank) - (rightRank < 0 ? driveOrder.length : rightRank);
        return left.label.localeCompare(right.label);
      });
  }, [telemetry.catalog]);
  const rgbLights = useMemo(() => {
    const devices = telemetry.catalog?.devices ?? [];
    const signals = telemetry.catalog?.signals ?? [];
    return devices.flatMap((device) => {
      const isRgbLight = /rgb|indicator|light/i.test(`${device.id} ${device.label} ${device.deviceType}`);
      const signal = signals.find((candidate) => candidate.deviceId === device.id && /duty.?cycle|color|light/i.test(`${candidate.id} ${candidate.quantity} ${candidate.label}`));
      return isRgbLight && signal ? [{ device, signal }] : [];
    });
  }, [telemetry.catalog]);
  const poseSignal = useMemo(() => telemetry.catalog?.signals.find((signal) => signal.valueType === "pose2d" || signal.quantity === "pose"), [telemetry.catalog]);
  const latestSnapshot = telemetry.snapshots.at(-1) ?? null;
  useEffect(() => { setSelectedSignals((previous) => { const allowed = previous.filter((id) => numericSignals.some((signal) => signal.id === id)); return allowed.length ? allowed : numericSignals.slice(0, MAX_TRACES).map((signal) => signal.id); }); }, [numericSignals]);
  const liveSnapshots = useMemo(() => {
    if (!latestSnapshot) return [];
    const windowStartNs = snapshotTimeNs(latestSnapshot) - LIVE_WINDOW_SECONDS * NANOSECONDS_PER_SECOND;
    return telemetry.snapshots.filter((snapshot) => snapshotTimeNs(snapshot) >= windowStartNs);
  }, [latestSnapshot, telemetry.snapshots]);
  const liveWindowStartNs = liveSnapshots.length ? snapshotTimeNs(liveSnapshots[0]) : 0;
  const traces = numericSignals.filter((signal) => selectedSignals.includes(signal.id));
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

  return <main className="telemetry-dashboard">
    <div className="telemetry-top-shell">{topbar}{opModeControl}</div>
    <div className="telemetry-notices">
      {driverStationError && <p className="telemetry-error"><strong>Robot Controller error:</strong> {driverStationError}</p>}
      {telemetry.status.lastError && <p className="telemetry-error">Latest telemetry packet rejected: {telemetry.status.lastError}</p>}
    </div>
    <div className="telemetry-body">
      <aside className="tcp-command-panel panel"><div className="panel-heading"><strong>Commands</strong><span className={commands.length ? "alliance-ready" : ""}>{commands.length ? `${commands.length} READY` : "WAITING"}</span></div>{commands.length ? <div className="command-card-list">{commands.map((command) => { const key = commandKey(command); return <CommandCard key={command.id} command={command} values={commandValues[key] ?? {}} busy={commandBusy[key] ?? false} feedback={commandFeedback[key]} onValueChange={(argumentId, value) => setCommandValue(command, argumentId, value)} onRun={() => void runCommand(command)} />; })}</div> : null}</aside>
      <section className="telemetry-workbench">
      <section className="motor-monitor-section panel">{motorDevices.length ? <><div className="motor-monitor-grid">{motorDevices.map((device) => <MotorMonitor key={device.id} device={device} signals={telemetry.catalog?.signals ?? []} snapshot={latestSnapshot} />)}</div><div className="motor-monitor-aux"><RgbLightPanel lights={rgbLights} snapshot={latestSnapshot} /><FieldMap signal={poseSignal} snapshot={latestSnapshot} /></div></> : <p className="empty-state">Start an OpMode with DC motors to receive motor telemetry.</p>}</section>
      <div className="telemetry-debug-layout">
        <section className={`telemetry-editor ${tracePickerOpen ? "trace-picker-open" : ""}`}>{tracePickerOpen && <aside className="trace-picker panel"><div><strong>Traces</strong><button className="trace-picker-toggle" type="button" onClick={() => setTracePickerOpen(false)}>Done</button></div><div className="trace-picker-list">{numericSignals.map((signal) => <label key={signal.id}><input type="checkbox" checked={selectedSignals.includes(signal.id)} onChange={() => toggleSignal(signal.id)} /><span title={signal.label}>{signal.label}</span></label>)}</div></aside>}<section className="trace-workspace"><div className="trace-toolbar"><button className="trace-picker-toggle" type="button" onClick={() => setTracePickerOpen((open) => !open)}>{tracePickerOpen ? "Close traces" : "Edit traces"}</button></div><section className="trace-stack">{traces.length ? traces.map((signal, index) => <LiveTrace key={signal.id} signal={signal} snapshots={liveSnapshots} windowStartNs={liveWindowStartNs} color={TRACE_COLORS[index]} />) : <p className="empty-state">Choose a numeric trace.</p>}</section></section></section>
        <PowerMonitor devices={motorDevices} signals={telemetry.catalog?.signals ?? []} snapshot={latestSnapshot} />
      </div>
    </section>
    </div>
  </main>;
}
