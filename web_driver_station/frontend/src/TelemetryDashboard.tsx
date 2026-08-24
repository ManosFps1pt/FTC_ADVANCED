import { useEffect, useMemo, useState } from "react";

type DeviceDefinition = {
  id: string;
  label: string;
  subsystem: string;
  deviceType: string;
};

type SignalDefinition = {
  id: string;
  label: string;
  deviceId?: string;
  quantity: string;
  unit: string;
  valueType: string;
  role: string;
};

type Catalog = {
  schemaRevision: number;
  devices: DeviceDefinition[];
  signals: SignalDefinition[];
};

type Snapshot = {
  sessionId: string;
  sampleSequence: string;
  schemaRevision: number;
  robotTimeNs: string;
  receivedAtMs: number;
  values: Record<string, unknown>;
};

type TelemetryEvent = {
  name: string;
  severity: string;
  robotTimeNs: string;
  attributes: Record<string, string | number | boolean | null>;
};

type SessionSummary = {
  sessionId: string;
  robotId: string | null;
  robotName: string | null;
  opModeName: string | null;
  active: boolean;
  ended: boolean;
  schemaRevision: number | null;
  signalCount: number;
  snapshotCount: number;
  eventCount: number;
  gapCount: number;
  droppedSamples: string;
  lastSeenAtMs: number | null;
};

type TelemetryState = {
  session: SessionSummary | null;
  catalog: Catalog | null;
  snapshots: Snapshot[];
  events: TelemetryEvent[];
  status: {
    activeSession: SessionSummary | null;
    sessionCount: number;
    lastError: string | null;
  };
};

const EMPTY_STATE: TelemetryState = {
  session: null,
  catalog: null,
  snapshots: [],
  events: [],
  status: { activeSession: null, sessionCount: 0, lastError: null },
};

const GRAPH_COLORS = ["#4bb8ff", "#4cdd9b", "#ffbc52", "#bf8cff"];
const MAX_BROWSER_SNAPSHOTS = 400;

function numericValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function formatValue(value: unknown, unit: string): string {
  if (value === null || value === undefined) return "Unavailable";
  const numeric = numericValue(value);
  if (numeric !== null) {
    const rendered = Math.abs(numeric) >= 1000 ? numeric.toFixed(0) : numeric.toFixed(3).replace(/\.?(0+)$/, "");
    return unit === "none" ? rendered : `${rendered} ${unit}`;
  }
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function sameIds(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function SignalGraph({ signal, snapshots, color }: { signal: SignalDefinition; snapshots: Snapshot[]; color: string }) {
  const graph = useMemo(() => {
    const points = snapshots.flatMap((snapshot) => {
      const value = numericValue(snapshot.values[signal.id]);
      const timeNs = Number(snapshot.robotTimeNs);
      return value === null || !Number.isFinite(timeNs) ? [] : [{ timeNs, value }];
    });
    if (!points.length) return null;
    const firstTime = points[0].timeNs;
    const lastTime = points[points.length - 1].timeNs;
    const rawMin = Math.min(...points.map((point) => point.value));
    const rawMax = Math.max(...points.map((point) => point.value));
    const padding = rawMin === rawMax ? Math.max(Math.abs(rawMin) * 0.08, 1) : (rawMax - rawMin) * 0.08;
    const min = rawMin - padding;
    const max = rawMax + padding;
    const timeSpan = Math.max(lastTime - firstTime, 1);
    const valueSpan = Math.max(max - min, Number.EPSILON);
    const path = points.map((point, index) => {
      const x = 40 + ((point.timeNs - firstTime) / timeSpan) * 620;
      const y = 15 + (1 - (point.value - min) / valueSpan) * 140;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
    }).join(" ");
    return { path, min, max, latest: points[points.length - 1].value, elapsedSeconds: (lastTime - firstTime) / 1_000_000_000 };
  }, [signal.id, snapshots]);

  return (
    <article className="signal-graph">
      <div className="signal-graph-heading">
        <div><strong>{signal.label}</strong><small>{signal.id}</small></div>
        <span>{graph ? formatValue(graph.latest, signal.unit) : "WAITING"}</span>
      </div>
      {graph ? (
        <svg className="signal-chart" viewBox="0 0 700 185" role="img" aria-label={`${signal.label} over time`}>
          <line x1="40" y1="15" x2="660" y2="15" className="chart-grid" />
          <line x1="40" y1="85" x2="660" y2="85" className="chart-grid" />
          <line x1="40" y1="155" x2="660" y2="155" className="chart-grid" />
          <line x1="40" y1="15" x2="40" y2="155" className="chart-axis" />
          <line x1="40" y1="155" x2="660" y2="155" className="chart-axis" />
          <path d={graph.path} fill="none" stroke={color} strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" />
          <text x="4" y="22">{formatValue(graph.max, signal.unit)}</text>
          <text x="4" y="158">{formatValue(graph.min, signal.unit)}</text>
          <text x="40" y="178">{graph.elapsedSeconds.toFixed(2)} s ago</text>
          <text x="615" y="178">now</text>
        </svg>
      ) : <p className="empty-state">No numeric samples are available for this signal yet.</p>}
    </article>
  );
}

export function TelemetryDashboard() {
  const [telemetry, setTelemetry] = useState<TelemetryState>(EMPTY_STATE);
  const [selectedSignals, setSelectedSignals] = useState<string[]>([]);

  useEffect(() => {
    let active = true;
    const applyState = (next: TelemetryState) => {
      if (active) setTelemetry(next);
    };
    void fetch("/api/data/telemetry")
      .then((response) => response.ok ? response.json() as Promise<TelemetryState> : Promise.reject(new Error("Telemetry unavailable")))
      .then(applyState)
      .catch(() => undefined);

    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${scheme}://${window.location.host}/ws/telemetry`);
    socket.onmessage = (event: MessageEvent<string>) => {
      const message = JSON.parse(event.data) as { kind: string; data: unknown };
      if (!active) return;
      if (message.kind === "telemetry_state") {
        setTelemetry(message.data as TelemetryState);
      } else if (message.kind === "telemetry_catalog") {
        setTelemetry((previous) => ({ ...previous, catalog: message.data as Catalog }));
      } else if (message.kind === "telemetry_snapshot") {
        const snapshot = message.data as Snapshot;
        setTelemetry((previous) => ({
          ...previous,
          snapshots: [...previous.snapshots, snapshot].slice(-MAX_BROWSER_SNAPSHOTS),
        }));
      } else if (message.kind === "telemetry_event") {
        const nextEvent = message.data as TelemetryEvent;
        setTelemetry((previous) => ({ ...previous, events: [...previous.events, nextEvent].slice(-100) }));
      } else if (message.kind === "telemetry_session") {
        const session = message.data as SessionSummary;
        setTelemetry((previous) => ({
          ...previous,
          session,
          status: { ...previous.status, activeSession: session },
        }));
      }
    };
    return () => {
      active = false;
      socket.close();
    };
  }, []);

  const numericSignals = useMemo(
    () => telemetry.catalog?.signals.filter((signal) => signal.valueType === "float64" || signal.valueType === "int64") ?? [],
    [telemetry.catalog],
  );

  useEffect(() => {
    const available = new Set(numericSignals.map((signal) => signal.id));
    setSelectedSignals((previous) => {
      const retained = previous.filter((id) => available.has(id));
      const next = retained.length ? retained : numericSignals.slice(0, 4).map((signal) => signal.id);
      return sameIds(previous, next) ? previous : next;
    });
  }, [numericSignals]);

  const latestSnapshot = telemetry.snapshots[telemetry.snapshots.length - 1] ?? null;
  const deviceById = useMemo(
    () => new Map((telemetry.catalog?.devices ?? []).map((device) => [device.id, device])),
    [telemetry.catalog],
  );
  const selectedDefinitions = numericSignals.filter((signal) => selectedSignals.includes(signal.id));
  const signalGroups = useMemo(() => {
    const groups = new Map<string, SignalDefinition[]>();
    for (const signal of telemetry.catalog?.signals ?? []) {
      const device = signal.deviceId ? deviceById.get(signal.deviceId) : undefined;
      const group = device?.subsystem ?? "system";
      groups.set(group, [...(groups.get(group) ?? []), signal]);
    }
    return [...groups.entries()].sort(([left], [right]) => left.localeCompare(right));
  }, [deviceById, telemetry.catalog]);

  const toggleSignal = (signalId: string) => {
    setSelectedSignals((previous) => previous.includes(signalId)
      ? previous.filter((id) => id !== signalId)
      : [...previous, signalId].slice(-4));
  };

  const connectionLabel = telemetry.session?.active ? "LIVE" : telemetry.session?.ended ? "ENDED" : "WAITING";

  return (
    <main className="telemetry-workspace">
      <header className="hero">
        <div>
          <p className="eyebrow">Structured robot data</p>
          <h1>Telemetry Lab</h1>
          <p className="subhead">Inspect full robot snapshots and graph numeric variables without stopping the OpMode.</p>
        </div>
        <div className={`connection ${telemetry.session?.active ? "online" : "offline"}`}>
          <span className="status-dot" />
          {connectionLabel}
          {telemetry.session && <span className="ping">{telemetry.session.snapshotCount} snapshots</span>}
        </div>
      </header>

      <nav className="workspace-nav" aria-label="Application pages">
        <a href="#/">Driver Station</a>
        <a className="current" href="#/telemetry">Telemetry Lab</a>
      </nav>

      {telemetry.status.lastError && <p className="telemetry-error">Latest telemetry packet rejected: {telemetry.status.lastError}</p>}

      <section className="telemetry-summary panel">
        <div>
          <p className="eyebrow">Current data session</p>
          <h2>{telemetry.session?.opModeName ?? "Waiting for a structured robot session"}</h2>
          <p>{telemetry.session?.robotName ?? "The Robot Controller must send hello, catalog, then full sample frames."}</p>
        </div>
        <dl>
          <div><dt>Signals</dt><dd>{telemetry.session?.signalCount ?? 0}</dd></div>
          <div><dt>Gaps</dt><dd>{telemetry.session?.gapCount ?? 0}</dd></div>
          <div><dt>Dropped</dt><dd>{telemetry.session?.droppedSamples ?? "0"}</dd></div>
          <div><dt>Revision</dt><dd>{telemetry.session?.schemaRevision ?? "—"}</dd></div>
        </dl>
      </section>

      <section className="graph-workspace">
        <div className="panel graph-picker">
          <div className="panel-heading"><div><p className="eyebrow">Numeric signals</p><h2>Graph selection</h2></div><span>UP TO 4</span></div>
          {numericSignals.length ? numericSignals.map((signal) => (
            <label className="signal-toggle" key={signal.id}>
              <input type="checkbox" checked={selectedSignals.includes(signal.id)} onChange={() => toggleSignal(signal.id)} />
              <span><strong>{signal.label}</strong><small>{signal.id} · {signal.unit}</small></span>
            </label>
          )) : <p className="empty-state">Numeric signals appear after the server receives a catalog.</p>}
        </div>

        <div className="signal-graphs">
          {selectedDefinitions.length
            ? selectedDefinitions.map((signal, index) => <SignalGraph key={signal.id} signal={signal} snapshots={telemetry.snapshots} color={GRAPH_COLORS[index % GRAPH_COLORS.length]} />)
            : <section className="panel no-graphs"><h2>No graphs selected</h2><p className="empty-state">Select up to four numeric values once a structured catalog arrives.</p></section>}
        </div>
      </section>

      <section className="signal-inspector panel">
        <div className="panel-heading"><div><p className="eyebrow">Latest full snapshot</p><h2>Signal Inspector</h2></div><span>{latestSnapshot ? `#${latestSnapshot.sampleSequence}` : "WAITING"}</span></div>
        {signalGroups.length && latestSnapshot ? (
          <div className="signal-groups">
            {signalGroups.map(([subsystem, signals]) => (
              <section className="signal-group" key={subsystem}>
                <h3>{subsystem}</h3>
                <dl>
                  {signals.map((signal) => {
                    const device = signal.deviceId ? deviceById.get(signal.deviceId) : undefined;
                    return <div key={signal.id}><dt><strong>{signal.label}</strong><small>{device?.label ?? signal.id} · {signal.role}</small></dt><dd>{formatValue(latestSnapshot.values[signal.id], signal.unit)}</dd></div>;
                  })}
                </dl>
              </section>
            ))}
          </div>
        ) : <p className="empty-state">This table will show every variable from the newest complete snapshot.</p>}
      </section>

      <section className="event-log panel">
        <div className="panel-heading"><div><p className="eyebrow">State transitions and faults</p><h2>Events</h2></div><span>{telemetry.events.length}</span></div>
        {telemetry.events.length ? <ol>{[...telemetry.events].reverse().map((event, index) => <li key={`${event.robotTimeNs}-${index}`}><span className={`event-severity ${event.severity}`}>{event.severity}</span><div><strong>{event.name}</strong><small>{Object.entries(event.attributes).map(([key, value]) => `${key}=${String(value)}`).join(" · ") || "No attributes"}</small></div></li>)}</ol> : <p className="empty-state">State changes, commands, and faults sent as event frames will appear here.</p>}
      </section>
    </main>
  );
}
