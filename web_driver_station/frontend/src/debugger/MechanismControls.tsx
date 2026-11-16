import { useEffect, useState } from "react";
import { rgbLightColor } from "../TelemetryDashboard";

export type DebugParameter = {
  id: string; label: string; description: string; current: number; min: number; max: number;
  step: number; unit: string; writable: boolean;
};
type OutputValue = string | number;
type MechanismAction = { parameterId: string; value: number } | { commandId: string };

export type SendMechanismAction = (action: MechanismAction) => Promise<Record<string, OutputValue>>;

export function MechanismControls({ parameters, authorized, send, onNotice }: {
  parameters: DebugParameter[];
  authorized: boolean;
  send: SendMechanismAction;
  onNotice: (message: string) => void;
}) {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [values, setValues] = useState<Record<string, OutputValue>>({});
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!authorized) {
      setValues(previous => Object.fromEntries(Object.entries(previous)
        .filter(([id]) => id === "gate.state" || id === "indicator.position" || id === "hood.position")));
      return;
    }
    let current = true;
    let reading = false;
    const refresh = async () => {
      if (reading) return;
      reading = true;
      try {
        const outputs = await send({ commandId: "mechanisms.status" });
        if (current) setValues(outputs);
      } catch {
        if (current) setValues({});
      } finally {
        reading = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 500);
    return () => { current = false; window.clearInterval(timer); };
  }, [authorized, send]);

  const apply = async (action: Parameters<SendMechanismAction>[0]) => {
    setPending(true);
    try {
      const outputs = await send(action);
      setValues(previous => ({ ...previous, ...outputs }));
      if ("parameterId" in action) {
        setDrafts(previous => ({ ...previous, [action.parameterId]: String(outputs[action.parameterId]) }));
      }
      onNotice("Control Hub confirmed the command.");
    } catch (error) {
      onNotice(String(error));
    } finally {
      setPending(false);
    }
  };

  const light = rgbLightColor(values["indicator.position"]);
  const gate = values["gate.state"];
  const rpm = values["shooter.measuredRpm"];
  return <section className="dbg-manual">
    <h3>Mechanism controls</h3>
    {!authorized && <p>Acquire debugger control and arm this tool to enable the controls.</p>}
    {parameters.filter(parameter => parameter.writable).map(parameter => <form key={parameter.id} className="dbg-inputs" onSubmit={event => {
      event.preventDefault();
      if (!authorized || pending) return;
      const data = new FormData(event.currentTarget);
      void apply({ parameterId: parameter.id, value: Number(data.get("value")) });
    }}>
      <label title={parameter.description}>{parameter.label}{parameter.unit && ` (${parameter.unit})`}
        <input name="value" type="number" required min={parameter.min} max={parameter.max} step={parameter.step}
          value={drafts[parameter.id] ?? values[parameter.id] ?? parameter.current} disabled={!authorized || pending}
          onChange={event => setDrafts(previous => ({ ...previous, [parameter.id]: event.target.value }))} />
      </label>
      <button type="submit" disabled={!authorized || pending}>Set {parameter.label.toLowerCase()}</button>
    </form>)}
    <p>Shooter velocity {typeof rpm === "number" ? `${Math.round(rpm)} RPM` : "—"}</p>
    <p>Gate {gate === "OPEN" ? "open" : gate === "CLOSED" ? "closed" : "—"} · RGB indicator {light ? <>
      <span aria-hidden="true" style={{ color: light.color }}>●</span> {light.label}
    </> : "—"}</p>
    <div className="dbg-actions">
      <button disabled={!authorized || pending} onClick={() => void apply({ commandId: "gate.open" })}>Open gate</button>
      <button disabled={!authorized || pending} onClick={() => void apply({ commandId: "gate.close" })}>Close gate</button>
      <button className="danger" disabled={!authorized} onClick={() => void apply({ commandId: "stop" })}>Stop motors</button>
    </div>
  </section>;
}
