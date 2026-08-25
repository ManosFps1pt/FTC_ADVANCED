import { type CSSProperties, type ReactNode, useEffect, useMemo, useRef, useState } from "react";

type DeviceDefinition = { id: string; label: string; subsystem: string; deviceType: string };
type SignalDefinition = { id: string; label: string; deviceId?: string; unit: string; valueType: string };
type Catalog = { schemaRevision: number; devices: DeviceDefinition[]; signals: SignalDefinition[] };
type Snapshot = { sampleSequence: string; robotTimeNs: string; values: Record<string, unknown> };
type GamepadState = { leftStickX: number; leftStickY: number; rightStickX: number; rightStickY: number; leftTrigger: number; rightTrigger: number; a: boolean; b: boolean; x: boolean; y: boolean; dpadUp: boolean; dpadDown: boolean; dpadLeft: boolean; dpadRight: boolean; leftBumper: boolean; rightBumper: boolean; leftStickButton: boolean; rightStickButton: boolean; back: boolean; start: boolean; guide: boolean };
type GamepadFrame = { robotTimeNs: string; gamepad1: GamepadState; gamepad2: GamepadState };
type SessionSummary = { snapshotCount: number; gamepadFrameCount: number; active: boolean };
type TelemetryState = { session: SessionSummary | null; catalog: Catalog | null; snapshots: Snapshot[]; gamepadFrames: GamepadFrame[]; status: { lastError: string | null } };

const EMPTY_STATE: TelemetryState = { session: null, catalog: null, snapshots: [], gamepadFrames: [], status: { lastError: null } };
const TRACE_COLORS = ["#4bb8ff", "#4cdd9b", "#ffbc52"];
const MAX_TRACES = 3;
// Keep enough high-frequency loop snapshots to fill the 10-second graph window.
const MAX_HISTORY = 12_000;
const REPLAY_WINDOW_SECONDS = 10;
const NANOSECONDS_PER_SECOND = 1_000_000_000;
const TIMELINE_PIXELS_PER_SECOND = 96;
const TIMELINE_SUBDIVISIONS_PER_SECOND = 4;
const LOOP_TIME_SIGNAL_ID = "opmode.loopTimeMs";

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

export function TelemetryDashboard({ topbar }: { topbar: ReactNode }) {
  const [telemetry, setTelemetry] = useState<TelemetryState>(EMPTY_STATE);
  const [selectedSignals, setSelectedSignals] = useState<string[]>([]);
  const [replayStartTimeNs, setReplayStartTimeNs] = useState<number | null>(null);
  const [followingLatest, setFollowingLatest] = useState(false);
  const timelineRef = useRef<HTMLDivElement>(null);

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

  const numericSignals = useMemo(() => telemetry.catalog?.signals.filter((signal) => signal.valueType === "float64" || signal.valueType === "int64") ?? [], [telemetry.catalog]);
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

  return <main className="telemetry-dashboard">
    {topbar}
    {telemetry.status.lastError && <p className="telemetry-error">Latest telemetry packet rejected: {telemetry.status.lastError}</p>}
    <section className="editor-timeline panel"><div><p className="eyebrow">Instant replay · {REPLAY_WINDOW_SECONDS}-second window</p><strong>{replaySnapshot ? `Starts at sample #${replaySnapshot.sampleSequence} · ${replaySnapshots.length} frames` : "Waiting for samples"}</strong><button className="secondary" type="button" disabled={!telemetry.snapshots.length} onClick={showLatest}>{followingLatest ? `Following latest ${REPLAY_WINDOW_SECONDS} s` : `Show latest ${REPLAY_WINDOW_SECONDS} s`}</button></div><div className="timeline-viewport" ref={timelineRef} onPointerDown={() => setFollowingLatest(false)} onScroll={onTimelineScroll} onWheel={(event) => { setFollowingLatest(false); if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) { event.currentTarget.scrollLeft += event.deltaY; event.preventDefault(); } }}><div className="timeline-track" style={{ width: `${timeline.width}px` }}>{Array.from({ length: Math.floor(timeline.durationSeconds) + 1 }, (_, second) => <span className="timeline-label" style={{ left: `${second * TIMELINE_PIXELS_PER_SECOND}px` }} key={second}>{second}s</span>)}{Array.from({ length: Math.floor(timeline.durationSeconds * TIMELINE_SUBDIVISIONS_PER_SECOND) + 1 }, (_, tick) => <i aria-hidden="true" className={`timeline-ruler-tick ${tick % TIMELINE_SUBDIVISIONS_PER_SECOND === 0 ? "major" : ""}`} style={{ left: `${tick / TIMELINE_SUBDIVISIONS_PER_SECOND * TIMELINE_PIXELS_PER_SECOND}px` }} key={tick} />)}</div></div></section>
    <section className="telemetry-editor"><aside className="trace-picker panel"><div><p className="eyebrow">Traces</p><strong>{traces.length} / {MAX_TRACES}</strong></div>{numericSignals.map((signal) => <label key={signal.id}><input type="checkbox" checked={selectedSignals.includes(signal.id)} onChange={() => toggleSignal(signal.id)} />{signal.label}</label>)}</aside><section className="trace-stack">{traces.length ? traces.map((signal, index) => <Trace key={signal.id} signal={signal} snapshots={replaySnapshots} windowStartNs={replaySnapshot ? snapshotTimeNs(replaySnapshot) : 0} color={TRACE_COLORS[index]} />) : <p className="empty-state">Choose a numeric trace.</p>}</section><aside className="controller-stack panel"><div><p className="eyebrow">Driver inputs</p><strong>{telemetry.session?.gamepadFrameCount ?? 0} frames</strong></div><ControllerMonitor title="Driver 1" state={replayGamepads?.gamepad1 ?? null} /><ControllerMonitor title="Driver 2" state={replayGamepads?.gamepad2 ?? null} /><LoopTimeMonitor snapshot={controllerSnapshot} /></aside></section>
  </main>;
}
