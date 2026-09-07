import { useEffect, useState } from "react";
import { DebuggerReport, type Result, type RunManifest } from "../../../web_driver_station/frontend/src/debugger/Report";

export function DebuggerLibrary() {
  const [runs,setRuns]=useState<RunManifest[]>([]),[result,setResult]=useState<Result|null>(null);
  const [mechanism,setMechanism]=useState(""),[benchmark,setBenchmark]=useState(""),[error,setError]=useState("");
  const load=()=>void fetch("/api/debug/results").then(r=>{if(!r.ok)throw new Error("Unable to load debugger results");return r.json();}).then(setRuns).catch(e=>setError(String(e)));
  useEffect(load,[]);
  const open=(runId:string)=>{setError("");setResult(null);void fetch(`/api/debug/results/${runId}`).then(r=>{if(!r.ok)throw new Error("Unable to open result");return r.json();}).then(setResult).catch(e=>setError(String(e)));};
  const visible=runs.filter(r=>(!mechanism||r.mechanismId===mechanism)&&(!benchmark||r.benchmark?.id===benchmark));
  return <main className="dbg-page"><header className="dbg-header"><div><p className="dbg-eyebrow">FTC results library</p><h1>Debugger Results</h1><p>Saved mechanism experiments and their laptop-generated reports.</p></div><button onClick={load}>Refresh results</button></header>
    {error&&<p className="dbg-notice" role="alert">{error}</p>}
    <div className="dbg-inputs"><label>Mechanism<select value={mechanism} onChange={e=>setMechanism(e.target.value)}><option value="">All mechanisms</option>{[...new Map(runs.map(r=>[r.mechanismId,r.mechanismLabel])).entries()].map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label><label>Benchmark<select value={benchmark} onChange={e=>setBenchmark(e.target.value)}><option value="">All benchmarks</option>{[...new Map(runs.map(r=>[r.benchmark?.id,r.benchmark?.label])).entries()].map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label></div>
    <section className="dbg-library-list">{visible.map(r=><button className={r.runId===result?.manifest.runId?"selected":""} key={r.runId} onClick={()=>open(r.runId)}><strong>{r.mechanismLabel}</strong><span>{r.benchmark?.label}</span><span>{new Date(r.createdAt).toLocaleString()}</span><span>{r.outcome} · {r.inputs?.find(i=>i.id==="repetitions")?.int64Value??"?"} repetitions per direction</span></button>)}{!visible.length&&<p>No debugger results match these filters. Upload a completed benchmark from the laptop to add one.</p>}</section>
    {result&&<DebuggerReport result={result}/>}
  </main>;
}
