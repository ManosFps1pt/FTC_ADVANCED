import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RobotConfiguration } from "./RobotConfiguration";

type Status = {
  connected: boolean;
  host: string | null;
  robot_state: string;
  started_opmode: boolean;
  telemetry: Telemetry | null;
};

type PingResult = { latency_ms: number | null };

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

function App() {
  const [host, setHost] = useState("192.168.43.1");
  const [status, setStatus] = useState<Status>({
    connected: false,
    host: null,
    robot_state: "UNKNOWN",
    started_opmode: false,
    telemetry: null,
  });
  const [opmodes, setOpmodes] = useState<Record<string, unknown>[]>([]);
  const [opmode, setOpmode] = useState("");
  const [user, setUser] = useState<1 | 2>(1);
  const [gamepad, setGamepad] = useState<GamepadState>(neutralGamepad);
  const [physicalControllers, setPhysicalControllers] = useState<PhysicalController[]>([]);
  const [assignments, setAssignments] = useState<DriverAssignments>({ 1: null, 2: null });
  const [notice, setNotice] = useState("Connect to a controlled test Robot Controller.");
  const [busy, setBusy] = useState(false);
  const [pingMs, setPingMs] = useState<number | null>(null);
  const assignmentsRef = useRef<DriverAssignments>({ 1: null, 2: null });
  const shortcutKeysRef = useRef<Set<string>>(new Set());
  const lastPhysicalStatesRef = useRef<Record<1 | 2, GamepadState | null>>({ 1: null, 2: null });

  const connected = status.connected;
  const physicalMode = physicalControllers.length > 0;

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
    setOpmode((previous) => previous || String(list[0]?.name ?? ""));
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
      setNotice("Stop requested and both gamepad slots released.");
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

  const controllerName = (controllerIndex: number | null) => {
    if (controllerIndex === null) return null;
    return physicalControllers.find((controller) => controller.index === controllerIndex)?.id ?? "Disconnected controller";
  };

  return (
    <main>
      <header className="hero">
        <div>
          <p className="eyebrow">Local lab dashboard</p>
          <h1>FTC Driver Station</h1>
          <p className="subhead">Robocol stays in Python. This browser only renders state and sends desired controls.</p>
        </div>
        <div className={`connection ${connected ? "online" : "offline"}`}>
          <span className="status-dot" />
          {connected ? `Connected · ${status.robot_state}` : "Disconnected"}
          {connected && <span className="ping">Ping {pingMs === null ? "—" : `${pingMs.toFixed(1)} ms`}</span>}
        </div>
      </header>

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

      <RobotConfiguration connected={connected} robotState={status.robot_state} startedOpmode={status.started_opmode} />

      <section className="driver-status panel" aria-label="Driver controller status">
        <div className="driver-status-copy">
          <p className="eyebrow">Driver input</p>
          <h2>{physicalMode ? "Physical controllers" : "Virtual controller"}</h2>
          <p>{physicalMode ? "Hold Start + A for Driver 1 or Start + B for Driver 2." : "Connect a controller to switch to physical input."}</p>
        </div>
        <div className="driver-indicators">
          {([1, 2] as const).map((driver) => {
            const assigned = controllerName(assignments[driver]);
            return (
              <div className={`driver-indicator ${assigned ? "active" : "inactive"}`} key={driver}>
                <span className="driver-dot" />
                <div><strong>Driver {driver}</strong><small>{assigned ? assigned : physicalMode ? "Awaiting assignment" : user === driver ? "Virtual control selected" : "Virtual control available"}</small></div>
              </div>
            );
          })}
        </div>
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

        <aside className={`telemetry panel ${physicalMode ? "telemetry-wide" : ""}`}>
          <div className="panel-heading"><div><p className="eyebrow">Live stream</p><h2>Telemetry</h2></div><span>{status.telemetry?.state ?? "WAITING"}</span></div>
          {status.telemetry ? (
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
  );
}

export default App;
