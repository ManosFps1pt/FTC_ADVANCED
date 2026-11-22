import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import { DebuggerLibrary } from "./DebuggerLibrary";
import { TheaterControls, TheaterHeader, useReplayOverlays, type Overlay } from "./ReplayTheater";
import { ReplayVideo } from "./ReplayVideo";
import type { ReplayExportFrame } from "./theater-renderer";
import { RecordingDetailsSummary, type RecordingMetadata } from "../../../web_driver_station/frontend/src/RecordingDetails";

type Device = { id: string; label: string; subsystem: string; deviceType: string };
type Signal = { id: string; label: string; unit: string; valueType: string; role?: string; deviceId?: string; quantity?: string };
type Catalog = { schemaRevision: number; devices?: Device[]; signals: Signal[] };
type Snapshot = { sampleSequence: string; schemaRevision: number; robotTimeNs: string; highlighted?: boolean; highlightSource?: string | null; values: Record<string, unknown> };
type Gamepad = Record<string, boolean | number>;
type GamepadFrame = { robotTimeNs: string; gamepad1: Gamepad; gamepad2: Gamepad };
type Incident = { id: string; firstSampleSequence: string; lastSampleSequence: string; startRobotTimeNs: string; endRobotTimeNs: string; sources: string[] };
type DebugMessage = { type: string; robotTimeNs: string; result?: string; commandId?: string; message?: string };
type Recording = {
  session: { sessionId: string; robotName: string; opModeName: string } | null;
  metadata: RecordingMetadata | null;
  catalog: Catalog | null;
  snapshotCount: number;
  snapshots: Snapshot[];
  gamepadFrames: GamepadFrame[];
  eventCount: number;
  debugMessageCount: number;
  gapCount: number;
  incidentCount: number;
  logFiles: string[];
  invalidFrames: number;
  recordingDirectory: string;
  videoClips: { index: number; name: string; url: string }[];
  incidents: Incident[];
  debugMessages: DebugMessage[];
};
type LibraryRecording = { id: string; metadata: RecordingMetadata | null; logFileCount: number; videoFileCount: number; sizeBytes: number; modifiedAtNs: string };

const COLORS = ["#4bb8ff", "#4cdd9b", "#ffbc52"];
const MAX_TRACES = 3;
const TRACE_HALF_WINDOW_NS = 5_000_000_000;
const PEDRO_FIELD_SIZE_INCHES = 144;
const ROBOT_BOX_SIZE_INCHES = 18;
const MAX_MOTOR_CURRENT_AMPS = 9;
const RGB_LIGHT_COLORS = [
  { label: "Off", dutyCycle: 0.000, color: "#05080d" }, { label: "Red", dutyCycle: 0.277, color: "#ff210b" },
  { label: "Orange", dutyCycle: 0.333, color: "#ff7900" }, { label: "Yellow", dutyCycle: 0.388, color: "#ffe600" },
  { label: "Sage", dutyCycle: 0.444, color: "#9fc85b" }, { label: "Green", dutyCycle: 0.500, color: "#12aa3e" },
  { label: "Azure", dutyCycle: 0.555, color: "#078fd4" }, { label: "Blue", dutyCycle: 0.611, color: "#1465f0" },
  { label: "Indigo", dutyCycle: 0.666, color: "#4720cf" }, { label: "Violet", dutyCycle: 0.722, color: "#8d2de2" },
  { label: "White", dutyCycle: 1.000, color: "#ffffff" },
];
type Pose2dValue = { x: number; y: number; headingRad: number };

function numeric(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : null; }
  return null;
}

function pose2dValue(value: unknown): Pose2dValue | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const x = numeric(candidate.x); const y = numeric(candidate.y); const headingRad = numeric(candidate.headingRad);
  return x === null || y === null || headingRad === null ? null : { x, y, headingRad };
}

function timeNs(snapshot: Snapshot): number { return Number(snapshot.robotTimeNs); }

function snapshotBoundary(snapshots: { robotTimeNs: string }[], targetNs: number, includeEqual: boolean): number {
  let low = 0; let high = snapshots.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    const time = Number(snapshots[middle].robotTimeNs);
    if (time < targetNs || (includeEqual && time === targetNs)) low = middle + 1;
    else high = middle;
  }
  return low;
}

function snapshotsAround(snapshots: Snapshot[], cursorNs: number) {
  return snapshots.slice(snapshotBoundary(snapshots, cursorNs - TRACE_HALF_WINDOW_NS, false),
    snapshotBoundary(snapshots, cursorNs + TRACE_HALF_WINDOW_NS, true));
}

function gamepadAt(frames: GamepadFrame[], time: number) {
  const next = snapshotBoundary(frames, time, false);
  const before = frames[next - 1], after = frames[next];
  if (!before) return after ?? null;
  if (!after) return before;
  return time - Number(before.robotTimeNs) <= Number(after.robotTimeNs) - time ? before : after;
}

function formatTime(elapsedSeconds: number): string {
  const minutes = Math.floor(elapsedSeconds / 60); const seconds = elapsedSeconds - minutes * 60;
  return `${minutes}:${seconds.toFixed(2).padStart(5, "0")}`;
}

function formatValue(value: unknown, unit = ""): string {
  const valueNumber = numeric(value);
  if (valueNumber === null) return value == null ? "—" : String(value);
  const rendered = Math.abs(valueNumber) >= 1000 ? valueNumber.toFixed(0) : valueNumber.toFixed(2).replace(/\.00$/, "");
  return unit && unit !== "none" ? `${rendered} ${unit}` : rendered;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function interpolateColor(start: string, end: string, amount: number): string {
  const startValue = Number.parseInt(start.slice(1), 16); const endValue = Number.parseInt(end.slice(1), 16);
  const channel = (shift: number) => Math.round(((startValue >> shift) & 255) + (((endValue >> shift) & 255) - ((startValue >> shift) & 255)) * amount);
  return `rgb(${channel(16)} ${channel(8)} ${channel(0)})`;
}

function heatStyle(currentAmps: number | null): CSSProperties & { "--current-color": string; "--current-surface": string } {
  const ratio = currentAmps === null ? 0 : Math.max(0, Math.min(1, currentAmps / MAX_MOTOR_CURRENT_AMPS));
  return { "--current-color": currentAmps === null ? "#36536d" : interpolateColor("#42c77a", "#e64f5b", ratio), "--current-surface": currentAmps === null ? "#0b1d33" : interpolateColor("#113c2a", "#4c1e29", ratio) };
}

function rgbLightColor(value: unknown): { label: string; color: string } | null {
  const dutyCycle = numeric(value); if (dutyCycle === null) return null;
  if (dutyCycle < RGB_LIGHT_COLORS[1].dutyCycle) return RGB_LIGHT_COLORS[0];
  if (dutyCycle > RGB_LIGHT_COLORS[9].dutyCycle) return RGB_LIGHT_COLORS[10];
  const exact = RGB_LIGHT_COLORS.find(candidate => Math.abs(dutyCycle - candidate.dutyCycle) < .002);
  if (exact) return exact;
  for (let index = 1; index < RGB_LIGHT_COLORS.length - 1; index += 1) {
    const current = RGB_LIGHT_COLORS[index]; const next = RGB_LIGHT_COLORS[index + 1];
    if (dutyCycle <= next.dutyCycle) return { label: "Color transition", color: interpolateColor(current.color, next.color, (dutyCycle - current.dutyCycle) / (next.dutyCycle - current.dutyCycle)) };
  }
  return RGB_LIGHT_COLORS[10];
}

function recordingDate(modifiedAtNs: string): string {
  return new Date(Number(modifiedAtNs) / 1_000_000).toLocaleString();
}

function Trace({ signal, snapshots, cursorNs, currentValue, color }: {
  signal: Signal; snapshots: Snapshot[]; cursorNs: number; currentValue: unknown; color: string;
}) {
  const graph = useMemo(() => {
    const points = snapshots.map(sample => ({ time: timeNs(sample), value: numeric(sample.values[signal.id]) }));
    let low = Infinity, high = -Infinity;
    for (const { value } of points) {
      if (value === null) continue;
      low = Math.min(low, value);
      high = Math.max(high, value);
    }
    if (low === Infinity) return null;
    const padding = low === high ? Math.max(Math.abs(low) * .08, 1) : (high - low) * .08;
    const min = low - padding, max = high + padding;
    let connected = false;
    const path = points.map(({ time, value }) => {
      if (value === null) { connected = false; return ""; }
      const x = 240 + (time - cursorNs) / TRACE_HALF_WINDOW_NS * 180;
      const y = 7 + (max - value) / (max - min) * 64;
      const command = connected ? "L" : "M";
      connected = true;
      return `${command}${x.toFixed(1)} ${y.toFixed(1)}`;
    }).join(" ");
    return { path, min, max };
  }, [signal.id, snapshots, cursorNs]);
  return <article className="trace panel">
    <div><strong>{signal.label}</strong><span>{formatValue(currentValue, signal.unit)}</span></div>
    {graph ? <svg viewBox="0 0 430 94" role="img" aria-label={`${signal.label}, five seconds before and after the playhead`}>
      <path className="trace-grid" d="M60 7H420 M60 39H420 M60 71H420" />
      <line className="trace-cursor" x1="240" x2="240" y1="7" y2="74" />
      <path d={graph.path} fill="none" stroke={color} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
      <text x="55" y="12" textAnchor="end">{formatValue(graph.max)}</text>
      <text x="55" y="71" textAnchor="end">{formatValue(graph.min)}</text>
      <text x="60" y="88">−5 s</text><text x="240" y="88" textAnchor="middle">Now</text><text x="420" y="88" textAnchor="end">+5 s</text>
    </svg> : <small>No numeric samples in this window.</small>}
  </article>;
}

function Controller({ label, gamepad }: { label: string; gamepad: Gamepad | null }) {
  const bool = (key: string) => Boolean(gamepad?.[key]); const axis = (key: string) => Number(gamepad?.[key] ?? 0);
  return <section className="controller panel"><div><strong>{label}</strong><small>{gamepad ? "Recorded input" : "No frame"}</small></div><div className="controller-body"><i className="stick" style={{ "--x": axis("leftStickX"), "--y": axis("leftStickY") } as CSSProperties} /><i className="stick" style={{ "--x": axis("rightStickX"), "--y": axis("rightStickY") } as CSSProperties} /><div className="buttons"><b className={bool("y") ? "on" : ""}>Y</b><b className={bool("x") ? "on" : ""}>X</b><b className={bool("b") ? "on" : ""}>B</b><b className={bool("a") ? "on" : ""}>A</b></div></div><div className="controller-row"><span className={bool("leftBumper") ? "on" : ""}>LB</span><span>LT {axis("leftTrigger").toFixed(2)}</span><span>RT {axis("rightTrigger").toFixed(2)}</span><span className={bool("rightBumper") ? "on" : ""}>RB</span></div></section>;
}

function motorReadings(device: Device, signals: Signal[], snapshot: Snapshot | null) {
  const forMotor = signals.filter(signal => signal.deviceId === device.id);
  const signalFor = (...quantities: string[]) => forMotor.find(signal => quantities.includes(signal.quantity ?? ""));
  const commandedPower = signalFor("commandedPower", "appliedPower"); const velocity = signalFor("velocity", "velocityTicksPerSecond");
  const current = signalFor("current", "currentAmps"); const electricalPower = signalFor("electricalPower", "electricalPowerWatts");
  const value = (signal?: Signal) => signal && snapshot ? snapshot.values[signal.id] : null;
  const currentAmps = numeric(value(current));
  return {
    active: snapshot && (commandedPower || velocity || current || electricalPower),
    currentAmps,
    units: [commandedPower?.unit ?? "normalized", velocity?.unit ?? "t/s", current?.unit ?? "A", electricalPower?.unit ?? "W"],
    values: [value(commandedPower), value(velocity), currentAmps, value(electricalPower)],
  };
}

function exportFrameAt(recording: Recording, overlays: Record<Overlay, boolean>, selection: {
  lights: { device: Device; signal: Signal }[];
  motors: Device[];
  graphs: Signal[];
  localization: Signal | undefined;
}, startNs: number, duration: number, seconds: number): ReplayExportFrame {
  const position = Math.min(duration, Math.max(0, seconds));
  const cursor = startNs + position * 1e9;
  const snapshot = recording.snapshots[Math.max(0, snapshotBoundary(recording.snapshots, cursor, true) - 1)];
  const graphFrames = overlays.graphs ? snapshotsAround(recording.snapshots, cursor) : [];
  const gamepad = overlays.gamepads && snapshot ? gamepadAt(recording.gamepadFrames, timeNs(snapshot)) : null;
  return {
    timeLabel: `${formatTime(position)} / ${formatTime(duration)}`,
    lights: overlays.lights ? selection.lights.map(({ device, signal }) => {
      const light = rgbLightColor(snapshot?.values[signal.id]);
      return { label: device.label, color: light?.color ?? "#162637", state: light?.label ?? "Unavailable" };
    }) : [],
    motors: overlays.motors ? selection.motors.map(device => {
      const { values, currentAmps } = motorReadings(device, recording.catalog?.signals ?? [], snapshot);
      return {
        name: device.label, values: values.map(value => formatValue(value)),
        currentBackground: currentAmps === null ? undefined : heatStyle(currentAmps)["--current-surface"],
      };
    }) : [],
    graphs: overlays.graphs ? selection.graphs.map((signal, index) => ({
      label: signal.label,
      value: formatValue(snapshot?.values[signal.id], signal.unit),
      color: COLORS[index],
      points: graphFrames.map(sample => [(timeNs(sample) - cursor) / 1e9, numeric(sample.values[signal.id])]),
    })) : [],
    gamepads: gamepad ? [{ label: "Driver 1", values: gamepad.gamepad1 }, { label: "Driver 2", values: gamepad.gamepad2 }] : [],
    position: overlays.map && selection.localization ? pose2dValue(snapshot?.values[selection.localization.id]) : null,
  };
}

function MotorMonitor({ device, signals, snapshot }: { device: Device; signals: Signal[]; snapshot: Snapshot | null }) {
  const { active, currentAmps, units, values } = motorReadings(device, signals, snapshot);
  return <article className="motor-monitor-card">
    <div className="motor-monitor-heading"><strong>{device.label}</strong><span className={active ? "motor-live" : ""}>{active ? "AT CURSOR" : "WAITING"}</span></div>
    <dl className="motor-metrics">
      <div><dt>pow</dt><dd>{formatValue(values[0], units[0])}</dd></div>
      <div><dt>vel</dt><dd>{formatValue(values[1], units[1])}</dd></div>
      <div className="motor-electrical-readout" style={heatStyle(currentAmps)}>
        <div><dt>current</dt><dd>{formatValue(values[2], units[2])}</dd></div>
        <div><dt>power</dt><dd>{formatValue(values[3], units[3])}</dd></div>
      </div>
    </dl>
  </article>;
}

function TheaterMotors({ devices, signals, snapshot }: { devices: Device[]; signals: Signal[]; snapshot: Snapshot | null }) {
  const [page, setPage] = useState(0);
  const [compact, setCompact] = useState(() => window.matchMedia("(max-height: 620px), (max-width: 760px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(max-height: 620px), (max-width: 760px)");
    const resize = () => { setCompact(media.matches); setPage(0); };
    media.addEventListener("change", resize);
    return () => media.removeEventListener("change", resize);
  }, []);
  const pageSize = compact ? 4 : 8;
  const lastPage = Math.max(0, Math.ceil(devices.length / pageSize) - 1);
  const currentPage = Math.min(page, lastPage);
  const start = currentPage * pageSize;
  const end = Math.min(start + pageSize, devices.length);

  return <section className="theater-motors panel" aria-label="Motor telemetry">
    <header><h2>Motors</h2>
      {lastPage > 0 && <div className="motor-page">
        <button type="button" aria-label="Previous motors" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>‹</button>
        <span aria-live="polite">{start + 1}–{end} / {devices.length}</span>
        <button type="button" aria-label="Next motors" disabled={currentPage === lastPage} onClick={() => setPage(currentPage + 1)}>›</button>
      </div>}
    </header>
    <table aria-label="Motor readings at the playhead">
      <colgroup><col className="motor-name-column" /><col span={4} /></colgroup>
      <thead><tr><th scope="col">Motor</th><th scope="col" title="Commanded power">Cmd</th>
        <th scope="col" title="Velocity">Vel</th><th scope="col" title="Current (amps)">A</th>
        <th scope="col" title="Electrical power (watts)">W</th></tr></thead>
      <tbody>{devices.slice(start, end).map(device => {
        const { currentAmps, units, values } = motorReadings(device, signals, snapshot);
        return <tr key={device.id}>
          <th scope="row" title={device.label}>{device.label}</th>
          {values.map((value, index) => <td key={index} title={formatValue(value, units[index])}
            style={index === 2 && currentAmps !== null ? heatStyle(currentAmps) : undefined}>{formatValue(value)}</td>)}
        </tr>;
      })}</tbody>
    </table>
  </section>;
}

function RgbLightPanel({ lights, snapshot }: { lights: Array<{ device: Device; signal: Signal }>; snapshot: Snapshot | null }) {
  return <section className="rgb-light-panel panel"><div className="rgb-light-panel-heading"><div><p className="eyebrow">Recorded indicator output</p><h2>RGB lights</h2></div><span>{lights.length ? `${lights.length} LIGHT${lights.length === 1 ? "" : "S"}` : "WAITING"}</span></div>
    {lights.length ? <div className="rgb-light-grid">{lights.map(({ device, signal }) => { const output = snapshot ? rgbLightColor(snapshot.values[signal.id]) : null; return <article className="rgb-light-card" key={device.id}><span className="rgb-light-swatch" aria-hidden="true" style={{ "--light-color": output?.color ?? "#162637" } as CSSProperties} /><div><strong>{device.label}</strong><small>{output?.label ?? "Waiting for light signal"}</small></div></article>; })}</div> : <p className="instrument-empty">No RGB indicator recorded.</p>}
  </section>;
}

function FieldMap({ signal, snapshot, compact = false }: { signal: Signal | undefined; snapshot: Snapshot | null; compact?: boolean }) {
  const pose = signal && snapshot ? pose2dValue(snapshot.values[signal.id]) : null;
  const headingDegrees = pose ? pose.headingRad * 180 / Math.PI : 0;
  const grid = Array.from({ length: 7 }, (_, index) => index * 24);
  const live = pose !== null;
  return <section className={compact ? "field-map panel theater-map" : "field-map panel"}>
    {compact ? <h2>Position map</h2> : <div className="field-map-heading"><div><p className="eyebrow">PedroPathing field</p><h2>Localization replay</h2></div><span className={live ? "field-map-live" : ""}>{live ? "POSE AT CURSOR" : signal ? "POSE UNAVAILABLE" : "WAITING"}</span></div>}
    {!compact && signal && <p className="field-map-signal">{signal.label} · 0–144 in · +X → · +Y ↑ · heading CCW from +X</p>}
    <div className="field-map-layout">
      <svg className="field-map-canvas" viewBox={compact ? "-3 -3 150 150" : "-12 -8 164 164"} role="img" aria-label={live ? `Robot pose X ${pose.x.toFixed(1)}, Y ${pose.y.toFixed(1)}, heading ${headingDegrees.toFixed(0)} degrees` : "PedroPathing field waiting for a replay pose"}>
        <rect className="field-map-boundary" x="0" y="0" width={PEDRO_FIELD_SIZE_INCHES} height={PEDRO_FIELD_SIZE_INCHES} />
        <g className="field-map-grid">{grid.map((coordinate) => <line key={`vertical-${coordinate}`} x1={coordinate} x2={coordinate} y1="0" y2={PEDRO_FIELD_SIZE_INCHES} />)}{grid.map((coordinate) => <line key={`horizontal-${coordinate}`} x1="0" x2={PEDRO_FIELD_SIZE_INCHES} y1={coordinate} y2={coordinate} />)}</g>
        {!compact && <g className="field-map-labels">{grid.map((coordinate) => <text key={`x-${coordinate}`} x={coordinate} y="153" textAnchor="middle">{coordinate}</text>)}{grid.map((coordinate) => <text key={`y-${coordinate}`} x="-3" y={PEDRO_FIELD_SIZE_INCHES - coordinate + 2} textAnchor="end">{coordinate}</text>)}<text x="72" y="161" textAnchor="middle">X (in)</text><text x="-10" y="72" textAnchor="middle" transform="rotate(-90 -10 72)">Y (in)</text></g>}
        {pose && <g transform={`translate(0 ${PEDRO_FIELD_SIZE_INCHES}) scale(1 -1)`}><g transform={`translate(${pose.x} ${pose.y}) rotate(${headingDegrees})`}><rect className="field-map-robot" x={-ROBOT_BOX_SIZE_INCHES / 2} y={-ROBOT_BOX_SIZE_INCHES / 2} width={ROBOT_BOX_SIZE_INCHES} height={ROBOT_BOX_SIZE_INCHES} rx="1.5" /><path className="field-map-forward" d="M9 0 L2.5 3.5 L2.5 -3.5 Z" /></g></g>}
      </svg>
      {compact ? <p className="theater-map-coordinates">
        {pose ? `X ${pose.x.toFixed(1)} · Y ${pose.y.toFixed(1)} · ${headingDegrees.toFixed(0)}°` : "Pose unavailable"}
      </p> : <dl className="field-map-values"><div><dt>X</dt><dd>{pose ? `${pose.x.toFixed(2)} in` : "—"}</dd></div><div><dt>Y</dt><dd>{pose ? `${pose.y.toFixed(2)} in` : "—"}</dd></div><div><dt>Heading</dt><dd>{pose ? `${headingDegrees.toFixed(1)}°` : "—"}</dd></div><div><dt>Channel</dt><dd>{signal?.id ?? "Waiting"}</dd></div></dl>}
    </div>
  </section>;
}

function RecordingApp() {
  const [library, setLibrary] = useState<LibraryRecording[] | null>(null);
  const [selectedRecordingId, setSelectedRecordingId] = useState(() => new URLSearchParams(window.location.search).get("recording"));
  const [recording, setRecording] = useState<Recording | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [playing, setPlaying] = useState(false);
  const [playbackSeconds, setPlaybackSeconds] = useState(0);
  const playhead = useRef(0);
  const [selectedVideoIndex, setSelectedVideoIndex] = useState(0);
  const [syncedVideo, setSyncedVideo] = useState<{ url: string; delay: number } | null>(null);
  const [theater, setTheater] = useState(false);
  const { overlays, toggleOverlay } = useReplayOverlays();

  useEffect(() => {
    void fetch("api/recordings").then(async response => { if (!response.ok) throw new Error(await response.text()); return response.json() as Promise<LibraryRecording[]>; }).then(setLibrary).catch(reason => setError(reason instanceof Error ? reason.message : "Could not load recordings"));
  }, []);
  useEffect(() => {
    if (!selectedRecordingId) { setRecording(null); return; }
    const request = new AbortController();
    setError(null); setRecording(null);
    void fetch(`api/recordings/${encodeURIComponent(selectedRecordingId)}`, { signal: request.signal })
      .then(async response => { if (!response.ok) throw new Error(await response.text()); return response.json() as Promise<Recording>; })
      .then(value => {
        const target = Number(new URLSearchParams(window.location.search).get("t"));
        const first = value.snapshots.at(0);
        const last = value.snapshots.at(-1);
        const duration = first && last ? (timeNs(last) - timeNs(first)) / 1e9 : 0;
        const initial = Number.isFinite(target) ? Math.max(0, Math.min(target, duration)) : 0;
        playhead.current = initial;
        setPlaybackSeconds(initial);
        setRecording(value);
      })
      .catch(reason => { if (!request.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load recording"); });
    return () => request.abort();
  }, [selectedRecordingId]);

  const selectRecording = (id: string | null) => {
    setPlaying(false);
    setTheater(false);
    setSyncedVideo(null);
    setSelectedVideoIndex(0);
    setSelectedRecordingId(id);
    const url = new URL(window.location.href);
    url.searchParams.delete("t");
    if (id) url.searchParams.set("recording", id); else url.searchParams.delete("recording");
    window.history.pushState({}, "", url);
  };

  const signals = useMemo(() => recording?.catalog?.signals.filter(signal => signal.valueType === "float64" || signal.valueType === "int64") ?? [], [recording]);
  const localizationSignal = useMemo(() => recording?.catalog?.signals.find(signal => signal.valueType === "pose2d" && signal.role === "measured"), [recording]);
  useEffect(() => setSelected(previous => previous.length ? previous.filter(id => signals.some(signal => signal.id === id)) : signals.slice(0, MAX_TRACES).map(signal => signal.id)), [signals]);
  const startNs = recording?.snapshots.length ? timeNs(recording.snapshots[0]) : 0;
  const endNs = recording?.snapshots.length ? timeNs(recording.snapshots.at(-1)!) : 0;
  const duration = (endNs - startNs) / 1_000_000_000;
  const seek = (seconds: number) => {
    playhead.current = Math.min(duration, Math.max(0, seconds));
    setPlaybackSeconds(playhead.current);
  };
  const togglePlayback = () => {
    if (!playing && playhead.current >= duration) seek(0);
    setPlaying(!playing);
  };
  useEffect(() => {
    if (!playing || !recording) return;
    let previousFrame = performance.now();
    let animationFrame = 0;
    const advance = (now: number) => {
      playhead.current = Math.min(duration, playhead.current + Math.max(0, now - previousFrame) / 1000);
      previousFrame = now;
      setPlaybackSeconds(playhead.current);
      if (playhead.current >= duration) setPlaying(false); else animationFrame = requestAnimationFrame(advance);
    };
    animationFrame = requestAnimationFrame(advance);
    return () => cancelAnimationFrame(animationFrame);
  }, [playing, recording, duration]);
  const cursorNs = startNs + playbackSeconds * 1_000_000_000;
  const snapshot = recording?.snapshots[Math.max(0, snapshotBoundary(recording.snapshots, cursorNs, true) - 1)] ?? null;
  const traceWindow = useMemo(() => recording ? snapshotsAround(recording.snapshots, cursorNs) : [], [recording, cursorNs]);
  const elapsed = playbackSeconds;
  const selectedVideo = recording?.videoClips[selectedVideoIndex] ?? null;
  const canEnterTheater = Boolean(selectedVideo && selectedVideo.url === syncedVideo?.url);
  const showTheater = theater && canEnterTheater;
  const gamepadFrame = useMemo(() => {
    if (!recording || !snapshot) return null;
    return gamepadAt(recording.gamepadFrames, timeNs(snapshot));
  }, [recording, snapshot]);
  const catalogDevices = recording?.catalog?.devices ?? [];
  const motorDevices = useMemo(() => catalogDevices.filter(device => device.deviceType.toLowerCase().includes("motor")), [catalogDevices]);
  const rgbLights = useMemo(() => catalogDevices.flatMap(device => {
    const isRgbLight = /rgb|indicator|light/i.test(`${device.id} ${device.label} ${device.deviceType}`);
    const signal = recording?.catalog?.signals.find(candidate => candidate.deviceId === device.id && /duty.?cycle|color|light|indicator|position/i.test(`${candidate.id} ${candidate.quantity ?? ""} ${candidate.label}`));
    return isRgbLight && signal ? [{ device, signal }] : [];
  }), [catalogDevices, recording?.catalog?.signals]);
  const traces = signals.filter(signal => selected.includes(signal.id));
  const commandResponses = recording?.debugMessages.filter(message => message.type === "debug_command_response") ?? [];
  const toggle = (id: string) => setSelected(previous => previous.includes(id) ? previous.filter(value => value !== id) : [...previous, id].slice(-MAX_TRACES));

  if (error) return <main className="error"><h1>Could not open recording</h1><pre>{error}</pre>{selectedRecordingId && <button onClick={() => selectRecording(null)}>Back to recordings</button>}</main>;
  if (!selectedRecordingId) return <main className="library">
    <header className="library-header"><p className="eyebrow">FTC replay library</p><h1>Recordings</h1><p>Select a recording to inspect its robot data, controls, and video.</p></header>
    {library === null ? <p className="loading">Loading recordings…</p> : library.length ? <section className="recording-grid">
      {library.map(item => <button className="recording-card panel" key={item.id} onClick={() => selectRecording(item.id)}>
        <span className="recording-card-top"><strong>{recordingDate(item.modifiedAtNs)}</strong><small>{item.videoFileCount ? "Video included" : "No video"}</small></span>
        {item.metadata?.title && <strong className="recording-card-title">{item.metadata.title}</strong>}
        {item.metadata?.description && <span className="recording-card-description">{item.metadata.description}</span>}
        <RecordingDetailsSummary metadata={item.metadata} />
        <code>{item.id}</code>
        <span>{item.logFileCount} log {item.logFileCount === 1 ? "file" : "files"} · {formatBytes(item.sizeBytes)}</span>
        <b>Open replay →</b>
      </button>)}
    </section> : <section className="empty-library panel"><h2>No recordings yet</h2></section>}
  </main>;
  if (!recording) return <main className="loading">Opening recording…</main>;
  const title = recording.metadata?.title ?? recording.session?.robotName ?? "FTC Recording";
  const tracePicker = <div className="signal-picker">{signals.map(signal => <label key={signal.id}>
    <input type="checkbox" checked={selected.includes(signal.id)} onChange={() => toggle(signal.id)} />{signal.label}
  </label>)}</div>;
  const graphs = <section className="traces">{traces.length ? traces.map((signal, index) =>
    <Trace key={signal.id} signal={signal} snapshots={traceWindow} cursorNs={cursorNs}
      currentValue={snapshot?.values[signal.id]} color={COLORS[index]} />
  ) : <p className="empty">Choose up to three graph signals.</p>}</section>;
  const motorPanel = <section className="motor-monitor-section panel">
    <div className="motor-monitor-section-heading"><h2>Motor visualization</h2><span>{motorDevices.length} MOTORS</span></div>
    {motorDevices.length ? <div className="motor-monitor-grid">{motorDevices.map(device =>
      <MotorMonitor key={device.id} device={device} signals={recording.catalog?.signals ?? []} snapshot={snapshot} />
    )}</div> : <p className="instrument-empty">No motor catalog was recorded.</p>}
  </section>;
  const gamepads = <>
    <Controller label="Driver 1" gamepad={gamepadFrame?.gamepad1 ?? null} />
    <Controller label="Driver 2" gamepad={gamepadFrame?.gamepad2 ?? null} />
  </>;
  const lights = <RgbLightPanel lights={rgbLights} snapshot={snapshot} />;

  return <main className={showTheater ? "replay-theater" : undefined}>
    {!showTheater && <>
    <header className="topbar panel">
      <div className="replay-heading">
        <button className="back-button" onClick={() => selectRecording(null)}>← Recordings</button>
        <p className="eyebrow">Offline replay</p>
        <h1>{title}</h1>
        <p>{[recording.metadata && recording.session?.robotName, recording.session?.opModeName ?? "Unknown OpMode"].filter(Boolean).join(" · ")}</p>
        <p><RecordingDetailsSummary metadata={recording.metadata} /></p>
        {recording.metadata?.description && <p className="replay-description">{recording.metadata.description}</p>}
      </div>
      <div className="replay-view-tools">
        {canEnterTheater && <button type="button" onClick={() => setTheater(true)}>Video view</button>}
        <dl><div><dt>Snapshots</dt><dd>{recording.snapshotCount.toLocaleString()}</dd></div><div><dt>Duration</dt><dd>{formatTime(duration)}</dd></div><div><dt>Log files</dt><dd>{recording.logFiles.length}</dd></div><div><dt>Gaps</dt><dd>{recording.gapCount}</dd></div></dl>
      </div>
    </header>
    {recording.logFiles.some(name => name.endsWith(".partial")) && <section className="panel" role="status"><strong>Interrupted recording</strong><p>The connection ended before a clean stop. Captured telemetry is preserved; some samples may be missing.</p></section>}
    <section className="timeline panel"><div><div><p className="eyebrow">Snapshot master clock · whole recording</p><strong>{snapshot ? `Sample #${snapshot.sampleSequence}${snapshot.highlighted ? ` · Highlighted by ${snapshot.highlightSource === "telemetry_lab" ? "Telemetry Lab" : "Control Hub"}` : ""}` : "No snapshots"}</strong><span>{formatTime(elapsed)} / {formatTime(duration)}</span></div><button onClick={togglePlayback} disabled={!snapshot}>{playing ? "Pause" : "Play"}</button></div><input type="range" aria-label="Replay position" min="0" max={duration} step="0.001" value={playbackSeconds} onChange={event => { setPlaying(false); seek(Number(event.target.value)); }} /><div className="timeline-scale"><span>Start</span><span>End</span></div></section>
    </>}
    <section className={showTheater ? "theater-stage" : "replay-console"}>
      {!showTheater && <aside className="trace-column panel">
        <div className="trace-column-heading"><p className="eyebrow">Telemetry Lab</p><strong>Traces · {traces.length} / {MAX_TRACES}</strong></div>
        {tracePicker}{graphs}
      </aside>}
      {selectedVideo ? <ReplayVideo key={selectedVideo.url} clip={selectedVideo} playbackSeconds={playbackSeconds}
        playing={playing} onSyncedClip={setSyncedVideo} onTogglePlayback={showTheater ? togglePlayback : undefined}>
        {recording.videoClips.length > 1 && <label>Clip<select value={selectedVideoIndex} onChange={event => {
          setPlaying(false);
          setSelectedVideoIndex(Number(event.target.value));
        }}>{recording.videoClips.map(clip => <option value={clip.index} key={clip.index}>{clip.name}</option>)}</select></label>}
      </ReplayVideo> : <section className="replay-video panel">
        <div className="video-heading"><strong>No video in this recording</strong></div>
        <div className="black-frame">Add an MP4 inside this recording’s session folder to enable playback.</div>
      </section>}
      {showTheater && syncedVideo ? <>
        <TheaterHeader title={title} overlays={overlays} toggleOverlay={toggleOverlay} tracePicker={tracePicker}
          exportOptions={{ ...syncedVideo, duration, title, frameAt: seconds => exportFrameAt(recording, overlays, {
            lights: rgbLights, motors: motorDevices, graphs: traces, localization: localizationSignal,
          }, startNs, duration, seconds) }}
          onLeave={() => {
            if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
            setTheater(false);
          }} />
        <div className="theater-overlays">
          <div className="theater-rail theater-left">
            {overlays.lights && rgbLights.length > 0 && <div className="theater-lights">{lights}</div>}
            {overlays.graphs && signals.length > 0 && <div className="theater-graphs">{graphs}</div>}
          </div>
          <div className="theater-rail theater-right">
            {overlays.motors && motorDevices.length > 0 &&
              <TheaterMotors devices={motorDevices} signals={recording.catalog?.signals ?? []} snapshot={snapshot} />}
            <div className="theater-right-bottom">
              {overlays.map && localizationSignal && <FieldMap signal={localizationSignal} snapshot={snapshot} compact />}
              {overlays.gamepads && recording.gamepadFrames.length > 0 && <div className="theater-gamepads">{gamepads}</div>}
            </div>
          </div>
        </div>
        <TheaterControls timeLabel={`${formatTime(elapsed)} / ${formatTime(duration)}`}
          position={elapsed} duration={duration} playing={playing} togglePlayback={togglePlayback} seek={seek} />
      </> : <aside className="instrument-column">{motorPanel}{gamepads}{lights}</aside>}
    </section>
    {!showTheater && <>
    <FieldMap signal={localizationSignal} snapshot={snapshot} />
    {recording.incidents.length > 0 && <section className="replay-activity panel"><p className="eyebrow">Incident capture</p><div>{recording.incidents.map(incident => <button key={incident.id} onClick={() => { setPlaying(false); seek((Number(incident.startRobotTimeNs) - startNs) / 1_000_000_000); }}><strong>{incident.sources.map(source => source === "telemetry_lab" ? "Telemetry Lab" : "Control Hub").join(" + ")}</strong><small>Samples #{incident.firstSampleSequence}–#{incident.lastSampleSequence}</small></button>)}</div></section>}
    {commandResponses.length > 0 && <section className="replay-activity panel"><p className="eyebrow">Telemetry Lab command confirmations</p><div>{commandResponses.map((message, index) => <article key={`${message.robotTimeNs}-${index}`}><strong>{message.commandId ?? "Command"} · {message.result ?? "response"}</strong><small>{message.message ?? "Control Hub response recorded"}</small></article>)}</div></section>}
    {recording.invalidFrames > 0 && <p className="warning">Skipped {recording.invalidFrames} unsupported/corrupt frames while reading this recording.</p>}
    </>}
  </main>;
}

export default function App() {
  const [debuggerPage,setDebuggerPage]=useState(()=>window.location.hash.startsWith("#/debugger"));
  useEffect(()=>{const changed=()=>setDebuggerPage(window.location.hash.startsWith("#/debugger"));window.addEventListener("hashchange",changed);return()=>window.removeEventListener("hashchange",changed);},[]);
  return <><nav className="dbg-page dbg-actions" aria-label="Library sections"><a href="#/recordings" aria-current={!debuggerPage?"page":undefined}>Recordings</a><a href="#/debugger" aria-current={debuggerPage?"page":undefined}>Debugger Results</a></nav>{debuggerPage?<DebuggerLibrary/>:<RecordingApp/>}</>;
}
