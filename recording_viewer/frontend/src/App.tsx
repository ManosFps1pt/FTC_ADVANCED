import { type CSSProperties, useEffect, useMemo, useRef, useState } from "react";
import { DebuggerLibrary } from "./DebuggerLibrary";

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
type LibraryRecording = { id: string; logFileCount: number; videoFileCount: number; sizeBytes: number; modifiedAtNs: string };

const COLORS = ["#4bb8ff", "#4cdd9b", "#ffbc52"];
const MAX_TRACES = 3;
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

function snapshotIndexAtTime(snapshots: Snapshot[], targetNs: number): number {
  let low = 0; let high = snapshots.length;
  while (low < high) { const middle = Math.floor((low + high) / 2); if (timeNs(snapshots[middle]) <= targetNs) low = middle + 1; else high = middle; }
  return Math.max(0, low - 1);
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

function heatStyle(currentAmps: number | null): CSSProperties {
  const ratio = currentAmps === null ? 0 : Math.max(0, Math.min(1, currentAmps / MAX_MOTOR_CURRENT_AMPS));
  return { "--current-color": currentAmps === null ? "#36536d" : interpolateColor("#42c77a", "#e64f5b", ratio), "--current-surface": currentAmps === null ? "#0b1d33" : interpolateColor("#113c2a", "#4c1e29", ratio) } as CSSProperties;
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

function Trace({ signal, snapshots, startNs, endNs, color }: { signal: Signal; snapshots: Snapshot[]; startNs: number; endNs: number; color: string }) {
  const graph = useMemo(() => {
    const points = snapshots.flatMap((snapshot) => { const value = numeric(snapshot.values[signal.id]); return value === null ? [] : [{ time: timeNs(snapshot), value }]; });
    if (!points.length) return null;
    const values = points.map((point) => point.value); const low = Math.min(...values); const high = Math.max(...values); const padding = low === high ? Math.max(Math.abs(low) * .08, 1) : (high - low) * .08;
    const min = low - padding; const max = high + padding; const span = Math.max(max - min, Number.EPSILON); const timeSpan = Math.max(endNs - startNs, 1);
    const path = points.map((point, index) => `${index ? "L" : "M"}${(32 + (point.time - startNs) / timeSpan * 640).toFixed(1)} ${(8 + (1 - (point.value - min) / span) * 62).toFixed(1)}`).join(" ");
    return { path, min, max, latest: points.at(-1)!.value };
  }, [signal.id, snapshots, startNs, endNs]);
  return <article className="trace panel"><div><strong>{signal.label}</strong><span>{graph ? formatValue(graph.latest, signal.unit) : "—"}</span></div>{graph ? <svg viewBox="0 0 700 94" role="img" aria-label={`${signal.label} over the recording`}><line x1="32" x2="672" y1="72" y2="72" /><path d={graph.path} fill="none" stroke={color} strokeWidth="2.5" /><text x="1" y="13">{formatValue(graph.max, signal.unit)}</text><text x="1" y="73">{formatValue(graph.min, signal.unit)}</text></svg> : <small>No numeric samples.</small>}</article>;
}

function Controller({ label, gamepad }: { label: string; gamepad: Gamepad | null }) {
  const bool = (key: string) => Boolean(gamepad?.[key]); const axis = (key: string) => Number(gamepad?.[key] ?? 0);
  return <section className="controller panel"><div><strong>{label}</strong><small>{gamepad ? "Recorded input" : "No frame"}</small></div><div className="controller-body"><i className="stick" style={{ "--x": axis("leftStickX"), "--y": axis("leftStickY") } as CSSProperties} /><i className="stick" style={{ "--x": axis("rightStickX"), "--y": axis("rightStickY") } as CSSProperties} /><div className="buttons"><b className={bool("y") ? "on" : ""}>Y</b><b className={bool("x") ? "on" : ""}>X</b><b className={bool("b") ? "on" : ""}>B</b><b className={bool("a") ? "on" : ""}>A</b></div></div><div className="controller-row"><span className={bool("leftBumper") ? "on" : ""}>LB</span><span>LT {axis("leftTrigger").toFixed(2)}</span><span>RT {axis("rightTrigger").toFixed(2)}</span><span className={bool("rightBumper") ? "on" : ""}>RB</span></div></section>;
}

function MotorMonitor({ device, signals, snapshot }: { device: Device; signals: Signal[]; snapshot: Snapshot | null }) {
  const forMotor = signals.filter(signal => signal.deviceId === device.id);
  const signalFor = (...quantities: string[]) => forMotor.find(signal => quantities.includes(signal.quantity ?? ""));
  const commandedPower = signalFor("commandedPower", "appliedPower"); const velocity = signalFor("velocity", "velocityTicksPerSecond");
  const current = signalFor("current", "currentAmps"); const electricalPower = signalFor("electricalPower", "electricalPowerWatts");
  const value = (signal?: Signal) => signal && snapshot ? snapshot.values[signal.id] : null;
  const currentAmps = numeric(value(current));
  const active = snapshot && (commandedPower || velocity || current || electricalPower);
  return <article className="motor-monitor-card">
    <div className="motor-monitor-heading"><strong>{device.label}</strong><span className={active ? "motor-live" : ""}>{active ? "AT CURSOR" : "WAITING"}</span></div>
    <dl className="motor-metrics">
      <div><dt>pow</dt><dd>{formatValue(value(commandedPower), commandedPower?.unit ?? "normalized")}</dd></div>
      <div><dt>vel</dt><dd>{formatValue(value(velocity), velocity?.unit ?? "t/s")}</dd></div>
      <div className="motor-electrical-readout" style={heatStyle(currentAmps)}><div><dt>current</dt><dd>{formatValue(currentAmps, current?.unit ?? "A")}</dd></div><div><dt>power</dt><dd>{formatValue(value(electricalPower), electricalPower?.unit ?? "W")}</dd></div></div>
    </dl>
  </article>;
}

function RgbLightPanel({ lights, snapshot }: { lights: Array<{ device: Device; signal: Signal }>; snapshot: Snapshot | null }) {
  return <section className="rgb-light-panel panel"><div className="rgb-light-panel-heading"><div><p className="eyebrow">Recorded indicator output</p><h2>RGB lights</h2></div><span>{lights.length ? `${lights.length} LIGHT${lights.length === 1 ? "" : "S"}` : "WAITING"}</span></div>
    {lights.length ? <div className="rgb-light-grid">{lights.map(({ device, signal }) => { const output = snapshot ? rgbLightColor(snapshot.values[signal.id]) : null; return <article className="rgb-light-card" key={device.id}><span className="rgb-light-swatch" aria-hidden="true" style={{ "--light-color": output?.color ?? "#162637" } as CSSProperties} /><div><strong>{device.label}</strong><small>{output?.label ?? "Waiting for light signal"}</small></div></article>; })}</div> : <p className="instrument-empty">No RGB indicator recorded.</p>}
  </section>;
}

function FieldMap({ signal, snapshot }: { signal: Signal | undefined; snapshot: Snapshot | null }) {
  const pose = signal && snapshot ? pose2dValue(snapshot.values[signal.id]) : null;
  const headingDegrees = pose ? pose.headingRad * 180 / Math.PI : 0;
  const grid = Array.from({ length: 7 }, (_, index) => index * 24);
  const live = pose !== null;
  return <section className="field-map panel">
    <div className="field-map-heading"><div><p className="eyebrow">PedroPathing field</p><h2>Localization replay</h2></div><span className={live ? "field-map-live" : ""}>{live ? "POSE AT CURSOR" : signal ? "POSE UNAVAILABLE" : "WAITING"}</span></div>
    {signal && <p className="field-map-signal">{signal.label} · 0–144 in · +X → · +Y ↑ · heading CCW from +X</p>}
    <div className="field-map-layout">
      <svg className="field-map-canvas" viewBox="-12 -8 164 164" role="img" aria-label={live ? `Robot pose X ${pose.x.toFixed(1)}, Y ${pose.y.toFixed(1)}, heading ${headingDegrees.toFixed(0)} degrees` : "PedroPathing field waiting for a replay pose"}>
        <rect className="field-map-boundary" x="0" y="0" width={PEDRO_FIELD_SIZE_INCHES} height={PEDRO_FIELD_SIZE_INCHES} />
        <g className="field-map-grid">{grid.map((coordinate) => <line key={`vertical-${coordinate}`} x1={coordinate} x2={coordinate} y1="0" y2={PEDRO_FIELD_SIZE_INCHES} />)}{grid.map((coordinate) => <line key={`horizontal-${coordinate}`} x1="0" x2={PEDRO_FIELD_SIZE_INCHES} y1={coordinate} y2={coordinate} />)}</g>
        <g className="field-map-labels">{grid.map((coordinate) => <text key={`x-${coordinate}`} x={coordinate} y="153" textAnchor="middle">{coordinate}</text>)}{grid.map((coordinate) => <text key={`y-${coordinate}`} x="-3" y={PEDRO_FIELD_SIZE_INCHES - coordinate + 2} textAnchor="end">{coordinate}</text>)}<text x="72" y="161" textAnchor="middle">X (in)</text><text x="-10" y="72" textAnchor="middle" transform="rotate(-90 -10 72)">Y (in)</text></g>
        {pose && <g transform={`translate(0 ${PEDRO_FIELD_SIZE_INCHES}) scale(1 -1)`}><g transform={`translate(${pose.x} ${pose.y}) rotate(${headingDegrees})`}><rect className="field-map-robot" x={-ROBOT_BOX_SIZE_INCHES / 2} y={-ROBOT_BOX_SIZE_INCHES / 2} width={ROBOT_BOX_SIZE_INCHES} height={ROBOT_BOX_SIZE_INCHES} rx="1.5" /><path className="field-map-forward" d="M9 0 L2.5 3.5 L2.5 -3.5 Z" /></g></g>}
      </svg>
      <dl className="field-map-values"><div><dt>X</dt><dd>{pose ? `${pose.x.toFixed(2)} in` : "—"}</dd></div><div><dt>Y</dt><dd>{pose ? `${pose.y.toFixed(2)} in` : "—"}</dd></div><div><dt>Heading</dt><dd>{pose ? `${headingDegrees.toFixed(1)}°` : "—"}</dd></div><div><dt>Channel</dt><dd>{signal?.id ?? "Waiting"}</dd></div></dl>
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
  const [selectedVideoIndex, setSelectedVideoIndex] = useState(0);
  const [videoDurationSeconds, setVideoDurationSeconds] = useState<number | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    void fetch("/api/recordings").then(async response => { if (!response.ok) throw new Error(await response.text()); return response.json() as Promise<LibraryRecording[]>; }).then(setLibrary).catch(reason => setError(reason instanceof Error ? reason.message : "Could not load recordings"));
  }, []);
  useEffect(() => {
    if (!selectedRecordingId) { setRecording(null); return; }
    setError(null); setRecording(null);
    void fetch(`/api/recordings/${encodeURIComponent(selectedRecordingId)}`).then(async response => { if (!response.ok) throw new Error(await response.text()); return response.json() as Promise<Recording>; }).then(value => { setRecording(value); setPlaybackSeconds(0); }).catch(reason => setError(reason instanceof Error ? reason.message : "Could not load recording"));
  }, [selectedRecordingId]);

  const selectRecording = (id: string | null) => {
    setSelectedRecordingId(id);
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("recording", id); else url.searchParams.delete("recording");
    window.history.pushState({}, "", url);
  };

  const signals = useMemo(() => recording?.catalog?.signals.filter(signal => signal.valueType === "float64" || signal.valueType === "int64") ?? [], [recording]);
  const localizationSignal = useMemo(() => recording?.catalog?.signals.find(signal => signal.valueType === "pose2d" && signal.role === "measured"), [recording]);
  useEffect(() => setSelected(previous => previous.length ? previous.filter(id => signals.some(signal => signal.id === id)) : signals.slice(0, MAX_TRACES).map(signal => signal.id)), [signals]);
  const startNs = recording?.snapshots.length ? timeNs(recording.snapshots[0]) : 0;
  const endNs = recording?.snapshots.length ? timeNs(recording.snapshots.at(-1)!) : 0;
  const duration = (endNs - startNs) / 1_000_000_000;
  useEffect(() => {
    if (!playing || !recording) return;
    const startedAt = performance.now(); const playbackAtStart = playbackSeconds;
    let animationFrame = 0;
    const advance = (now: number) => {
      const next = Math.min(duration, playbackAtStart + (now - startedAt) / 1000);
      setPlaybackSeconds(next);
      if (next >= duration) setPlaying(false); else animationFrame = requestAnimationFrame(advance);
    };
    animationFrame = requestAnimationFrame(advance);
    return () => cancelAnimationFrame(animationFrame);
  }, [playing, recording, duration]);
  const snapshot = recording?.snapshots[snapshotIndexAtTime(recording.snapshots, startNs + playbackSeconds * 1_000_000_000)] ?? null;
  const elapsed = playbackSeconds;
  const selectedVideo = recording?.videoClips[selectedVideoIndex] ?? null;
  const videoHasEnded = videoDurationSeconds !== null && playbackSeconds >= videoDurationSeconds;
  useEffect(() => {
    const video = videoRef.current;
    if (!video || !selectedVideo) return;
    const shouldShowVideo = !videoHasEnded;
    if (playing && shouldShowVideo) void video.play().catch(() => undefined); else video.pause();
    if (shouldShowVideo && Math.abs(video.currentTime - playbackSeconds) > .12) video.currentTime = playbackSeconds;
  }, [playing, playbackSeconds, selectedVideo, videoHasEnded]);
  const gamepadFrame = useMemo(() => {
    if (!recording || !snapshot) return null;
    return recording.gamepadFrames.reduce<GamepadFrame | null>((closest, frame) => !closest || Math.abs(Number(frame.robotTimeNs) - timeNs(snapshot)) < Math.abs(Number(closest.robotTimeNs) - timeNs(snapshot)) ? frame : closest, null);
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
  if (!selectedRecordingId) return <main className="library"><header className="library-header"><p className="eyebrow">FTC replay library</p><h1>Recordings</h1><p>Select a completed upload to inspect its robot data, controls, and video.</p></header>{library === null ? <p className="loading">Loading recordings…</p> : library.length ? <section className="recording-grid">{library.map(item => <button className="recording-card panel" key={item.id} onClick={() => selectRecording(item.id)}><span className="recording-card-top"><strong>{recordingDate(item.modifiedAtNs)}</strong><small>{item.videoFileCount ? "Video included" : "No video"}</small></span><code>{item.id}</code><span>{item.logFileCount} log {item.logFileCount === 1 ? "file" : "files"} · {formatBytes(item.sizeBytes)}</span><b>Open replay →</b></button>)}</section> : <section className="empty-library panel"><h2>No recordings yet</h2><p>Finalized sessions will appear here automatically after the laptop uploader finishes.</p></section>}</main>;
  if (!recording) return <main className="loading">Opening recording…</main>;
  return <main>
    <header className="topbar panel"><div><button className="back-button" onClick={() => selectRecording(null)}>← Recordings</button><p className="eyebrow">Offline replay</p><h1>{recording.session?.robotName ?? "FTC Recording"}</h1><p>{recording.session?.opModeName ?? "Unknown OpMode"}</p></div><dl><div><dt>Snapshots</dt><dd>{recording.snapshotCount.toLocaleString()}</dd></div><div><dt>Duration</dt><dd>{formatTime(duration)}</dd></div><div><dt>Log files</dt><dd>{recording.logFiles.length}</dd></div><div><dt>Gaps</dt><dd>{recording.gapCount}</dd></div></dl></header>
    <section className="timeline panel"><div><div><p className="eyebrow">Snapshot master clock · whole recording</p><strong>{snapshot ? `Sample #${snapshot.sampleSequence}${snapshot.highlighted ? ` · Highlighted by ${snapshot.highlightSource === "telemetry_lab" ? "Telemetry Lab" : "Control Hub"}` : ""}` : "No snapshots"}</strong><span>{formatTime(elapsed)} / {formatTime(duration)}</span></div><button onClick={() => setPlaying(value => !value)} disabled={!snapshot}>{playing ? "Pause" : "Play"}</button></div><input type="range" min="0" max={Math.max(0, duration)} step="0.001" value={playbackSeconds} onChange={event => { setPlaying(false); setPlaybackSeconds(Number(event.target.value)); }} /><div className="timeline-scale"><span>Start</span><span>End</span></div></section>
    <section className="replay-console">
      <aside className="trace-column panel"><div className="trace-column-heading"><p className="eyebrow">Telemetry Lab</p><strong>Traces · {traces.length} / {MAX_TRACES}</strong></div><div className="signal-picker">{signals.map(signal => <label key={signal.id}><input type="checkbox" checked={selected.includes(signal.id)} onChange={() => toggle(signal.id)} />{signal.label}</label>)}</div><section className="traces">{traces.length ? traces.map((signal, traceIndex) => <Trace key={signal.id} signal={signal} snapshots={recording.snapshots} startNs={startNs} endNs={endNs} color={COLORS[traceIndex]} />) : <p className="empty">Choose up to three numeric traces.</p>}</section></aside>
      <section className="replay-video panel"><div className="video-heading"><div><p className="eyebrow">Synchronized camera</p><strong>{selectedVideo ? selectedVideo.name : "No video in this recording"}</strong></div>{recording.videoClips.length > 1 && <label>Clip<select value={selectedVideoIndex} onChange={event => { setPlaying(false); setSelectedVideoIndex(Number(event.target.value)); setVideoDurationSeconds(null); }}>{recording.videoClips.map(clip => <option value={clip.index} key={clip.index}>{clip.name}</option>)}</select></label>}</div>{selectedVideo ? <div className="video-stage">{videoHasEnded ? <div className="black-frame">Video ended · snapshots continue to {formatTime(duration)}</div> : <video ref={videoRef} src={selectedVideo.url} muted playsInline preload="metadata" onLoadedMetadata={event => setVideoDurationSeconds(event.currentTarget.duration)} onEnded={() => setVideoDurationSeconds(videoRef.current?.duration ?? playbackSeconds)} />}</div> : <div className="black-frame">Add an MP4 anywhere inside this recording’s session folder to enable playback.</div>}</section>
      <aside className="instrument-column"><section className="motor-monitor-section panel"><div className="motor-monitor-section-heading"><div><p className="eyebrow">Telemetry Lab</p><h2>Motor visualization</h2></div><span>{motorDevices.length ? `${motorDevices.length} MOTORS` : "WAITING"}</span></div>{motorDevices.length ? <div className="motor-monitor-grid">{motorDevices.map(device => <MotorMonitor key={device.id} device={device} signals={recording.catalog?.signals ?? []} snapshot={snapshot} />)}</div> : <p className="instrument-empty">No motor catalog was recorded.</p>}</section><Controller label="Driver 1" gamepad={gamepadFrame?.gamepad1 ?? null} /><Controller label="Driver 2" gamepad={gamepadFrame?.gamepad2 ?? null} /><RgbLightPanel lights={rgbLights} snapshot={snapshot} /></aside>
    </section>
    <FieldMap signal={localizationSignal} snapshot={snapshot} />
    {recording.incidents.length > 0 && <section className="replay-activity panel"><p className="eyebrow">Incident capture</p><div>{recording.incidents.map(incident => <button key={incident.id} onClick={() => { setPlaying(false); setPlaybackSeconds(Math.max(0, (Number(incident.startRobotTimeNs) - startNs) / 1_000_000_000)); }}><strong>{incident.sources.map(source => source === "telemetry_lab" ? "Telemetry Lab" : "Control Hub").join(" + ")}</strong><small>Samples #{incident.firstSampleSequence}–#{incident.lastSampleSequence}</small></button>)}</div></section>}
    {commandResponses.length > 0 && <section className="replay-activity panel"><p className="eyebrow">Telemetry Lab command confirmations</p><div>{commandResponses.map((message, index) => <article key={`${message.robotTimeNs}-${index}`}><strong>{message.commandId ?? "Command"} · {message.result ?? "response"}</strong><small>{message.message ?? "Control Hub response recorded"}</small></article>)}</div></section>}
    {recording.invalidFrames > 0 && <p className="warning">Skipped {recording.invalidFrames} unsupported/corrupt frames while reading this recording.</p>}
  </main>;
}

export default function App() {
  const [debuggerPage,setDebuggerPage]=useState(()=>window.location.hash.startsWith("#/debugger"));
  useEffect(()=>{const changed=()=>setDebuggerPage(window.location.hash.startsWith("#/debugger"));window.addEventListener("hashchange",changed);return()=>window.removeEventListener("hashchange",changed);},[]);
  return <><nav className="dbg-page dbg-actions" aria-label="Library sections"><a href="#/recordings" aria-current={!debuggerPage?"page":undefined}>Recordings</a><a href="#/debugger" aria-current={debuggerPage?"page":undefined}>Debugger Results</a></nav>{debuggerPage?<DebuggerLibrary/>:<RecordingApp/>}</>;
}
