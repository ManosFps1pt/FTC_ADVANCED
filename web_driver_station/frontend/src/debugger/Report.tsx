import "./debugger.css";

export type Benchmark = { id: string; label: string; description: string; version: number; reverseAllowed?: boolean; maxVoltage?: number };
export type RunManifest = { runId: string; mechanismId?: string; mechanismLabel?: string; motorName?: string;
  createdAt: string; state: string; phase?: string; repetition?: number; direction?: number; message?: string;
  outcome?: string; sampleCount?: number; benchmark?: Benchmark; inputs?: {id: string; int64Value?: string; float64Value?: number; booleanValue?: boolean}[];
  storage?: {state: string; detail?: string} };
type Range = {median: number; min: number; max: number} | null;
type Repetition = {direction: number; repetition: number; breakawayVoltage: number|null; breakawayCurrent: number|null; coastTime: number|null; deceleration: number|null; coastInitialSpeed?: number};
type OperatingPoint = {direction: number; repetition: number; point: number; speed: number; voltage: number; current: number; currentStd: number; speedStd: number};
type Fit = {ks: number; kv: number; r2: number|null; rmse: number; residuals: number[]; reliable: boolean};
export type Analysis = {analyzerId: string; speedUnit: string; kvUnit: string; notes: string[]; warnings: string[];
  summary: Record<string, Range>; repetitions: Repetition[]; operatingPoints: OperatingPoint[];
  fits: {direction: number; fit: Fit|null}[]; coasts: {direction: number; repetition: number; points: number[][]}[];
  timeline: {time: number; speed: number; voltage: number; current: number; phase: string}[]};
export type Result = {manifest: RunManifest; analysis: Analysis|null};
export const directionName = (value: number) => value === 1 ? "Forward" : "Reverse";
const fmt = (value: number|null|undefined, digits=3) => value == null ? "Unavailable" : value.toLocaleString(undefined,{maximumFractionDigits:digits});
const colors = ["#5cbaff", "#56d5a0", "#e5b864", "#bf9fff", "#f78792", "#6bdbdf"];
type Series = {label: string; points: number[][]; scatter?: boolean};

function Plot({title,xLabel,yLabel,series}: {title:string;xLabel:string;yLabel:string;series:Series[]}) {
  const all=series.flatMap(s=>s.points).filter(p=>Number.isFinite(p[0])&&Number.isFinite(p[1]));
  if (!all.length) return <section className="dbg-chart"><h3>{title}</h3><p>No valid measurements for this plot.</p></section>;
  const xmin=Math.min(0,...all.map(p=>p[0])), xmax=Math.max(...all.map(p=>p[0]),xmin+0.001);
  const ymin=Math.min(0,...all.map(p=>p[1])), ymax=Math.max(...all.map(p=>p[1]),ymin+0.001);
  const x=(v:number)=>64+(v-xmin)/(xmax-xmin)*500, y=(v:number)=>230-(v-ymin)/(ymax-ymin)*190;
  return <section className="dbg-chart"><h3>{title}</h3><svg viewBox="0 0 600 295" role="img" aria-label={`${title}: ${yLabel} versus ${xLabel}`}>
    {[0,.25,.5,.75,1].map(t=><g key={t}><line x1="64" x2="564" y1={40+190*t} y2={40+190*t} stroke="currentColor" opacity=".12"/><text x="58" y={44+190*t} textAnchor="end">{fmt(ymax-(ymax-ymin)*t,2)}</text><text x={64+500*t} y="249" textAnchor="middle">{fmt(xmin+(xmax-xmin)*t,2)}</text></g>)}
    {series.map((s,i)=><g key={s.label} stroke={colors[i%colors.length]} fill={colors[i%colors.length]}>{s.scatter?s.points.map((p,k)=><circle key={k} cx={x(p[0])} cy={y(p[1])} r="4"><title>{s.label}: {fmt(p[0])}, {fmt(p[1])}</title></circle>):<polyline fill="none" strokeWidth="2" points={s.points.map(p=>`${x(p[0])},${y(p[1])}`).join(" ")}/>}</g>)}
    <text x="315" y="279" textAnchor="middle">{xLabel}</text><text transform="translate(15,140) rotate(-90)" textAnchor="middle">{yLabel}</text>
  </svg><div className="dbg-legend">{series.map((s,i)=><span key={s.label} style={{color:colors[i%colors.length]}}>● {s.label}</span>)}</div></section>;
}

export function DebuggerReport({result,apiRoot="/api/debug/results"}: {result:Result;apiRoot?:string}) {
  const {manifest:m,analysis:a}=result;
  if (!a) return <div className="dbg-notice">Analysis is unavailable for this result.</div>;
  const voltageSeries:Series[]=[], currentSeries:Series[]=[];
  for (const d of [1,-1]) {
    const points=a.operatingPoints.filter(p=>p.direction===d);
    if (!points.length) continue;
    voltageSeries.push({label:`${directionName(d)} measured`,points:points.map(p=>[p.speed,p.voltage]),scatter:true});
    currentSeries.push({label:directionName(d),points:points.map(p=>[p.speed,p.current]),scatter:true});
    const fit=a.fits.find(f=>f.direction===d)?.fit;
    if (fit) { const max=Math.max(...points.map(p=>p.speed)); voltageSeries.push({label:`${directionName(d)} fit`,points:[[0,fit.ks],[max,fit.ks+fit.kv*max]]}); }
  }
  return <article className="dbg-report">
    <header className="dbg-report-heading"><div><p className="dbg-eyebrow">Completed dataset · {m.sampleCount} samples</p><h2>{m.mechanismLabel} · {m.benchmark?.label}</h2><p>{m.outcome} · {m.message}</p></div><div className="dbg-actions"><a href={`${apiRoot}/${m.runId}/raw`}>Download raw data</a><a href={`${apiRoot}/${m.runId}/report`}>Download report</a></div></header>
    <div className="dbg-metrics">{[["breakawayVoltage","Breakaway voltage","V"],["breakawayCurrent","Current near breakaway","A"],["coastTime","Coast 80% → 20%","s"],["deceleration","Coast deceleration",`${a.speedUnit}/s`]].map(([key,label,unit])=><section key={key}><span>{label}</span><strong>{fmt(a.summary[key]?.median)} {a.summary[key]&&<small>{unit}</small>}</strong><small>{a.summary[key]?`Range ${fmt(a.summary[key]?.min)}–${fmt(a.summary[key]?.max)}`:"No complete measurement"}</small></section>)}</div>
    <div className="dbg-metrics">{a.fits.map(({direction,fit})=><section key={direction}><span>{directionName(direction)} running fit</span>{fit?<><strong>kS {fmt(fit.ks)} <small>V</small></strong><span>kV {fmt(fit.kv,6)} {a.kvUnit}</span><small>R² {fmt(fit.r2)} · residual RMS {fmt(fit.rmse)} V{!fit.reliable?" · inspect fit quality":""}</small></>:<p>Insufficient steady operating points</p>}</section>)}</div>
    <div className="dbg-plots"><Plot title="Estimated voltage versus speed" xLabel={a.speedUnit} yLabel="Estimated V" series={voltageSeries}/><Plot title="Motor current versus speed" xLabel={a.speedUnit} yLabel="Current (A)" series={currentSeries}/><Plot title="Coast-down repetitions" xLabel="Time since coast began (s)" yLabel={a.speedUnit} series={a.coasts.map(c=>({label:`${directionName(c.direction)} · ${c.repetition}`,points:c.points}))}/></div>
    <h3>Repetitions</h3><div className="dbg-table-wrap"><table><thead><tr><th>Direction / repetition</th><th>Breakaway V</th><th>Onset current A</th><th>Initial coast speed ({a.speedUnit})</th><th>80–20% time (s)</th><th>Deceleration ({a.speedUnit}/s)</th></tr></thead><tbody>{a.repetitions.map(r=><tr key={`${r.direction}-${r.repetition}`}><td>{directionName(r.direction)} / {r.repetition}</td><td>{fmt(r.breakawayVoltage)}</td><td>{fmt(r.breakawayCurrent)}</td><td>{fmt(r.coastInitialSpeed)}</td><td>{fmt(r.coastTime)}</td><td>{fmt(r.deceleration)}</td></tr>)}</tbody></table></div>
    <details><summary>Steady measurements and fit residuals</summary><div className="dbg-table-wrap"><table><thead><tr><th>Direction / repetition / point</th><th>Speed ({a.speedUnit})</th><th>Estimated V</th><th>Current mean ± SD (A)</th><th>Voltage residual (V)</th></tr></thead><tbody>{a.operatingPoints.map(p=>{const f=a.fits.find(f=>f.direction===p.direction)?.fit;return <tr key={`${p.direction}-${p.repetition}-${p.point}`}><td>{directionName(p.direction)} / {p.repetition} / {p.point}</td><td>{fmt(p.speed)}</td><td>{fmt(p.voltage)}</td><td>{fmt(p.current)} ± {fmt(p.currentStd)}</td><td>{fmt(f?p.voltage-f.ks-f.kv*p.speed:null)}</td></tr>;})}</tbody></table></div></details>
    <details><summary>Full acquisition timeline</summary><div className="dbg-plots"><Plot title="Speed" xLabel="Run time (s)" yLabel={a.speedUnit} series={[{label:"Measured",points:a.timeline.map(r=>[r.time,r.speed])}]}/><Plot title="Estimated voltage" xLabel="Run time (s)" yLabel="V" series={[{label:"Estimated voltage",points:a.timeline.map(r=>[r.time,r.voltage])}]}/><Plot title="Current" xLabel="Run time (s)" yLabel="A" series={[{label:"Measured current",points:a.timeline.map(r=>[r.time,r.current])}]}/></div></details>
    {a.warnings.length>0&&<details className="dbg-notice"><summary>Measurement availability and quality ({a.warnings.length})</summary><ul>{a.warnings.map((w,i)=><li key={i}>{w}</li>)}</ul></details>}
    <footer>{a.notes.map(n=><p key={n}>{n}</p>)}<small>Run {m.runId} · Analyzer {a.analyzerId} · {new Date(m.createdAt).toLocaleString()}</small></footer>
  </article>;
}
