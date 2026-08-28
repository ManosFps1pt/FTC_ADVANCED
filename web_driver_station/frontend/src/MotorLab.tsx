import { useEffect, useMemo, useRef, useState, type ReactElement } from "react";

type DebugNode = {
  id: string;
  parentId: string;
  label: string;
  description: string;
  kind: "debug_folder" | "debug_tool" | string;
  riskClass: string;
  enabled: boolean;
  disabledReason: string;
  maxOutput: number;
};

type DebugManifest = { revision: number; registryLabel: string; nodes: DebugNode[] };
type DebugParameter = { id: string; label: string; description: string; current: number; min: number; max: number; step: number; unit: string; writable: boolean };
type DebugReady = { nodeId: string; toolInstanceId: string; parameters: DebugParameter[]; state: string };
type DebugToolState = { nodeId: string; toolInstanceId: string; state: string; outputEnabled: boolean; requestedOutput: number; appliedOutput: number; outputUnit: string; statusMessage: string };
type DebugSafety = { tcpConnected: boolean; watchdogHealthy: boolean; outputAllowed: boolean; blockedReason: string };
type DebugEvent = { type: string; data: Record<string, unknown>; robotTimeNs?: string };
type DebugStatus = { connected: boolean; manifest: DebugManifest | null; tool_ready: DebugReady | null; tool_state: DebugToolState | null; safety: DebugSafety | null; last_event: DebugEvent | null };
type DebugCommandResponse = Record<string, unknown> & { requestId?: string; result?: string; message?: string };
type DebugCommandHttpResponse = { request_id?: string; command_id?: string };

const emptyStatus: DebugStatus = { connected: false, manifest: null, tool_ready: null, tool_state: null, safety: null, last_event: null };
const STOP_RESPONSE_TIMEOUT_MS = 2_000;

function createCommandRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `motor-stop-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { headers: { "Content-Type": "application/json", ...options?.headers }, ...options });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function MotorLab() {
  const [status, setStatus] = useState<DebugStatus>(emptyStatus);
  const [selectedId, setSelectedId] = useState("");
  const [power, setPower] = useState(0.2);
  const [notice, setNotice] = useState("Start FTC Advanced Debugger through the Driver Station.");
  const runningRef = useRef(false);
  const intervalRef = useRef<number | null>(null);
  const readyRef = useRef<DebugReady | null>(null);
  const pendingStopRequestIdRef = useRef<string | null>(null);
  const unmatchedStopResponsesByRequestIdRef = useRef<Record<string, DebugCommandResponse>>({});
  const stopTimeoutRef = useRef<number | null>(null);
  const stopResponseHandlerRef = useRef<(response: DebugCommandResponse) => void>(() => undefined);

  const clearStopTimeout = () => {
    if (stopTimeoutRef.current !== null) window.clearTimeout(stopTimeoutRef.current);
    stopTimeoutRef.current = null;
  };

  const armStopTimeout = (requestId: string) => {
    clearStopTimeout();
    stopTimeoutRef.current = window.setTimeout(() => {
      if (pendingStopRequestIdRef.current !== requestId) return;
      pendingStopRequestIdRef.current = null;
      stopTimeoutRef.current = null;
      setNotice("Motor stop not confirmed — no response from the Control Hub.");
    }, STOP_RESPONSE_TIMEOUT_MS);
  };

  stopResponseHandlerRef.current = (response) => {
    const requestId = typeof response.requestId === "string" ? response.requestId : "";
    if (!requestId || pendingStopRequestIdRef.current !== requestId) {
      if (requestId) unmatchedStopResponsesByRequestIdRef.current[requestId] = response;
      return;
    }
    const result = typeof response.result === "string" ? response.result : "";
    const robotMessage = typeof response.message === "string" && response.message.trim() ? response.message : "";
    if (result === "debug_command_accepted") {
      armStopTimeout(requestId);
      setNotice(robotMessage ? `Motor stop accepted by Control Hub — ${robotMessage}` : "Motor stop accepted by Control Hub. Executing…");
      return;
    }
    clearStopTimeout();
    pendingStopRequestIdRef.current = null;
    if (result === "debug_command_completed") {
      setNotice(robotMessage ? `✓ Control Hub confirmed: ${robotMessage}` : "✓ Control Hub confirmed the motor is stopped.");
      return;
    }
    const rejectionCode = typeof response.rejectionCode === "string" ? response.rejectionCode : "";
    setNotice(`Motor stop not executed — ${robotMessage || rejectionCode || result || "unknown response"}`);
  };

  useEffect(() => {
    readyRef.current = status.tool_ready;
  }, [status.tool_ready]);

  const refresh = () => api<DebugStatus>("/debug/status").then(setStatus).catch((error: Error) => setNotice(error.message));

  const launchDebugger = async () => {
    try {
      await api("/debug/launch", { method: "POST", body: JSON.stringify({ name: "FTC Advanced Debugger" }) });
      setNotice("Debugger OpMode started; waiting for its TCP manifest…");
      window.setTimeout(() => void refresh(), 250);
    } catch (error) { setNotice(error instanceof Error ? error.message : "Unable to launch debugger OpMode"); }
  };

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 1000);
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws/debug`);
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data) as { kind: string; data: DebugStatus | { type: string; data: Record<string, unknown> } };
      if (message.kind === "debug_state") setStatus(message.data as DebugStatus);
      if (message.kind === "debug") {
        const update = message.data as { type: string; data: Record<string, unknown> };
        if (update.type === "debug_select_response") {
          const accepted = Boolean(update.data.accepted);
          const messageText = String(update.data.message ?? "");
          setNotice(accepted ? "Tool initialized successfully." : `Tool initialization failed (${String(update.data.rejectionCode ?? "UNKNOWN")}): ${messageText}`);
        }
        if (update.type === "debug_command_response") {
          stopResponseHandlerRef.current(update.data as DebugCommandResponse);
        }
        setStatus((previous) => {
          const next = { ...previous, last_event: update as DebugEvent };
          if (update.type === "debug_manifest") return { ...next, manifest: update.data as unknown as DebugManifest };
          if (update.type === "debug_tool_ready") return { ...next, tool_ready: update.data as unknown as DebugReady };
          if (update.type === "debug_tool_state") return { ...next, tool_state: update.data as unknown as DebugToolState };
          if (update.type === "debug_safety_state") return { ...next, safety: update.data as unknown as DebugSafety };
          return next;
        });
      }
    };
    return () => {
      window.clearInterval(timer);
      socket.close();
      stopPower(true);
      clearStopTimeout();
      pendingStopRequestIdRef.current = null;
      unmatchedStopResponsesByRequestIdRef.current = {};
    };
  }, []);

  const nodes = status.manifest?.nodes ?? [];
  const children = useMemo(() => {
    const grouped = new Map<string, DebugNode[]>();
    for (const node of nodes) grouped.set(node.parentId, [...(grouped.get(node.parentId) ?? []), node]);
    return grouped;
  }, [nodes]);

  const select = async (node: DebugNode) => {
    if (!node.enabled || node.kind !== "debug_tool" || !status.manifest) return;
    try {
      setSelectedId(node.id);
      await api("/debug/select", { method: "POST", body: JSON.stringify({ node_id: node.id, manifest_revision: status.manifest.revision }) });
      setNotice(`Selecting ${node.label}…`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "Unable to select tool"); }
  };

  const sendPower = async (value: number) => {
    const ready = readyRef.current;
    if (!ready) return;
    await api("/debug/parameters", { method: "POST", body: JSON.stringify({ node_id: ready.nodeId, tool_instance_id: ready.toolInstanceId, parameter_id: "motor.power", value, ttl_ms: 500 }) });
  };

  const stopPower = (silent = false) => {
    runningRef.current = false;
    if (intervalRef.current !== null) window.clearInterval(intervalRef.current);
    intervalRef.current = null;
    const ready = readyRef.current;
    if (!ready || pendingStopRequestIdRef.current) return;
    const requestId = createCommandRequestId();
    pendingStopRequestIdRef.current = requestId;
    armStopTimeout(requestId);
    if (!silent) setNotice("Sending motor stop to Control Hub…");
    void api<DebugCommandHttpResponse>("/debug/commands", {
      method: "POST",
      body: JSON.stringify({
        node_id: ready.nodeId,
        tool_instance_id: ready.toolInstanceId,
        command_id: "motor.stop",
        ttl_ms: 1000,
        request_id: requestId,
      }),
    }).then((response) => {
      const confirmedRequestId = response.request_id || requestId;
      if (confirmedRequestId !== requestId && pendingStopRequestIdRef.current === requestId) {
        pendingStopRequestIdRef.current = confirmedRequestId;
        armStopTimeout(confirmedRequestId);
      }
      const earlyResponse = unmatchedStopResponsesByRequestIdRef.current[confirmedRequestId];
      if (earlyResponse) {
        delete unmatchedStopResponsesByRequestIdRef.current[confirmedRequestId];
        stopResponseHandlerRef.current(earlyResponse);
      } else if (!silent && pendingStopRequestIdRef.current === confirmedRequestId) {
        setNotice("Awaiting Control Hub confirmation that the motor stopped…");
      }
    }).catch((error: Error) => {
      if (pendingStopRequestIdRef.current !== requestId) return;
      clearStopTimeout();
      pendingStopRequestIdRef.current = null;
      if (!silent) setNotice(`Motor stop not executed — ${error.message}`);
    });
  };

  const startPower = () => {
    if (runningRef.current || !status.tool_ready) return;
    runningRef.current = true;
    void sendPower(power).catch((error: Error) => setNotice(error.message));
    intervalRef.current = window.setInterval(() => void sendPower(power).catch((error: Error) => setNotice(error.message)), 200);
  };

  const selectedNode = nodes.find((node) => node.id === selectedId) ?? null;
  const rootNodes = children.get("") ?? [];

  const renderTree = (parentId: string, depth = 0): ReactElement[] => (children.get(parentId) ?? []).map((node) => (
    <div key={node.id} className="debug-tree-node" style={{ marginLeft: `${depth * 1.1}rem` }}>
      <button type="button" className={`debug-tree-item ${node.id === selectedId ? "selected" : ""} ${!node.enabled ? "locked" : ""}`} disabled={node.kind === "debug_folder" || !node.enabled} onClick={() => void select(node)}>
        <span className="debug-tree-icon">{node.kind === "debug_folder" ? "▸" : node.enabled ? "●" : "🔒"}</span>
        <span><strong>{node.label}</strong><small>{node.riskClass.replace("DEBUG_", "").replaceAll("_", " ")}</small></span>
      </button>
      {renderTree(node.id, depth + 1)}
      {!node.enabled && <p className="debug-disabled-reason" style={{ marginLeft: `${(depth + 1) * 1.1}rem` }}>{node.disabledReason}</p>}
    </div>
  ));

  return (
    <main className="debugger-workspace">
      <header className="hero debug-hero"><div><p className="eyebrow">FTC Advanced laboratory</p><h1>Motor Lab</h1><p className="subhead">Select a registered debugging tool, then control it through the structured TCP session.</p></div><div className="debug-hero-actions"><button className="secondary" type="button" onClick={() => void launchDebugger()}>Launch Debugger OpMode</button><a className="secondary topbar-link" href="#/">← Driver Station</a></div></header>
      <nav className="workspace-nav" aria-label="Application pages"><a href="#/">Driver Station</a><a className="current" href="#/debugger">Debugger</a><a href="#/telemetry">Telemetry Lab</a></nav>
      <section className="debugger-grid">
        <aside className="debug-navigator panel">
          <div className="panel-heading"><div><p className="eyebrow">Robot manifest</p><h2>{status.manifest?.registryLabel ?? "Waiting for debugger"}</h2></div><span className={status.connected && status.safety?.tcpConnected ? "debug-online" : ""}>{status.connected && status.safety?.tcpConnected ? "TCP ONLINE" : "OFFLINE"}</span></div>
          <div className="debug-tree">{rootNodes.length ? renderTree("") : <p className="empty-state">Initialize and start <strong>FTC Advanced Debugger</strong> to receive the tool tree.</p>}</div>
        </aside>
        <section className="debug-workbench panel">
          <div className="panel-heading"><div><p className="eyebrow">Selected tool</p><h2>{selectedNode?.label ?? status.tool_ready?.nodeId ?? "No tool selected"}</h2></div><span>{status.tool_state?.state ?? "IDLE"}</span></div>
          {selectedNode?.riskClass === "DEBUG_FREE_SPIN" && <div className="notice debug-warning"><strong>Free-spin motor test.</strong> Confirm the motor is safe to rotate freely and keep clear of moving parts.</div>}
          {status.last_event?.type === "debug_select_response" && status.last_event.data.accepted === false && <div className="notice debug-error"><strong>Robot error:</strong> {String(status.last_event.data.message ?? "Tool initialization failed.")} <code>{String(status.last_event.data.rejectionCode ?? "UNKNOWN")}</code></div>}
          {status.safety && !status.safety.outputAllowed && <div className="notice">{status.safety.blockedReason || "Output is stopped."}</div>}
          {status.tool_ready ? <>
            <div className="debug-tool-meta"><span>Instance <code>{status.tool_ready.toolInstanceId.slice(0, 8)}</code></span><span>Applied <code>{(status.tool_state?.appliedOutput ?? 0).toFixed(3)}</code></span><span>Watchdog <code>{status.safety?.watchdogHealthy ? "healthy" : "expired"}</code></span></div>
            <div className="motor-control-card">
              <div className="panel-heading"><div><p className="eyebrow">Simple power control</p><h3>Motor output</h3></div><output>{power.toFixed(2)}</output></div>
              <input className="power-slider" aria-label="Motor power" type="range" min="-0.35" max="0.35" step="0.01" value={power} onChange={(event) => setPower(Number(event.target.value))} />
              <div className="power-scale"><span>-0.35 reverse</span><span>0</span><span>+0.35 forward</span></div>
              <button className="primary hold-to-run" type="button" onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); startPower(); }} onPointerUp={() => stopPower()} onPointerCancel={() => stopPower()} onPointerLeave={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) stopPower(); }}>Hold to run at {power.toFixed(2)}</button>
              <button className="danger full-width" type="button" onClick={() => stopPower()}>STOP MOTOR</button>
            </div>
            <p className="notice" aria-live="polite">{notice}</p>
          </> : <p className="empty-state">Select the enabled free-spin tool from the manifest.</p>}
        </section>
      </section>
    </main>
  );
}

export default MotorLab;
