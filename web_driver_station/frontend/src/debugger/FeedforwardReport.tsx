import { useState } from "react";
import { Plot } from "./Report";

export type FeedforwardAnalysis = {
  analyzerId: "feedforward.v1"; direction: number; ks: number | null; referenceVoltage: number;
  fit: { ks: number; kv: number; r2: number | null; rmse: number; reliable: boolean } | null;
  points: { index: number; count: number; speed: number; speedStd: number; drift: number; voltage: number;
    current: number; included: boolean; exclusion: string }[];
  nextMotorGains: Record<string, { unit: string; ks: number; kv: number; code: string }>;
  warnings: string[]; notes: string[];
  timeline: { time: number; speed: number; voltage: number; phase: string }[];
};
const fmt = (v: number | null, precision = 6) => v == null ? "Unavailable" : v.toLocaleString(undefined, { maximumSignificantDigits: precision });

export function FeedforwardReport({ analysis: a }: { analysis: FeedforwardAnalysis }) {
  const [unit, setUnit] = useState("radiansPerSecond"), [copyState, setCopyState] = useState("");
  const gain = a.nextMotorGains[unit];
  const valid = a.points.filter(p => p.included), rejected = a.points.filter(p => !p.included);
  const max = Math.max(0, ...a.points.map(p => p.speed));
  const series = [
    { label: "Accepted means", points: valid.map(p => [p.speed, p.voltage]), scatter: true },
    { label: "Excluded means", points: rejected.map(p => [p.speed, p.voltage]), scatter: true },
    ...(a.fit ? [{ label: "kS + kV × speed", points: [[0, a.fit.ks], [max, a.fit.ks + a.fit.kv * max]] }] : []),
  ];
  return <>
    <p>Characterized direction: {a.direction === -1 ? "Reverse" : "Forward"}. Gains describe this direction and the measured motor/load.</p>
    <div className="dbg-metrics">
      <section><span>Measured breakaway kS</span><strong>{fmt(a.ks)} V</strong></section>
      <section><span>kV · corrected origin fit</span><strong>{fmt(a.fit?.kv ?? null)} V/(rad/s)</strong></section>
      <section><span>Fit quality</span><strong>R² {fmt(a.fit?.r2 ?? null)}</strong><small>Residual RMS {fmt(a.fit?.rmse ?? null)} V</small></section>
      <section><span>Operating points</span><strong>{valid.length} accepted · {rejected.length} excluded</strong></section>
    </div>
    <div className="dbg-plots"><Plot title="Estimated voltage versus angular velocity" xLabel="rad/s" yLabel="Estimated V" series={series} />
      <Plot title="Speed during acquisition" xLabel="Run time (s)" yLabel="rad/s"
        series={[{ label: "Measured", points: a.timeline.map(r => [r.time, r.speed]) }]} /></div>
    {gain && <section><h3>NextMotor gains at {a.referenceVoltage} V reference</h3>
      <label>Velocity setpoint unit <select value={unit} onChange={e => { setUnit(e.target.value); setCopyState(""); }}>
        {Object.entries(a.nextMotorGains).map(([key, g]) => <option key={key} value={key}>{g.unit}</option>)}
      </select></label>
      <p>kS = {fmt(gain.ks, 10)} normalized · kV = {fmt(gain.kv, 10)} normalized/({gain.unit})</p>
      {!a.fit?.reliable && <p className="dbg-notice">Inspect the fit and excluded points before using these gains.</p>}
      <pre><code>{gain.code}</code></pre><button onClick={async () => {
        try {
          await navigator.clipboard.writeText(gain.code); setCopyState("Copied");
        } catch { setCopyState("Select and copy the code above."); }
      }}>Copy NextMotor code</button>
      <span role="status">{copyState}</span>
    </section>}
    <h3>Operating-point measurements</h3><div className="dbg-table-wrap"><table><thead><tr>
      <th>Point</th><th>Speed (rad/s)</th><th>SD / drift (rad/s)</th><th>Estimated V</th><th>Current A</th><th>Fit use</th>
    </tr></thead><tbody>{a.points.map(p => <tr key={p.index}><td>{p.index}</td><td>{fmt(p.speed)}</td>
      <td>{fmt(p.speedStd)} / {fmt(p.drift)}</td><td>{fmt(p.voltage)}</td><td>{fmt(p.current)}</td>
      <td>{p.included ? "Included" : p.exclusion}</td></tr>)}</tbody></table></div>
    {a.warnings.length > 0 && <div className="dbg-notice"><ul>{a.warnings.map(w => <li key={w}>{w}</li>)}</ul></div>}
    <footer>{a.notes.map(n => <p key={n}>{n}</p>)}</footer>
  </>;
}
