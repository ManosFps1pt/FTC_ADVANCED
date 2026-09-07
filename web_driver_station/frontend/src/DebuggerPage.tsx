import { useEffect, useRef, useState } from "react";
import { DebuggerReport, type Benchmark, type Result, type RunManifest } from "./debugger/Report";

type Node = {id:string;parentId:string;label:string;description:string;kind:string;enabled:boolean;disabledReason:string};
type Status = {connected:boolean;manifest?:{revision:number;nodes:Node[]};tool_ready?:{nodeId:string;toolInstanceId:string;benchmarks?:Benchmark[]};last_event?:{type:string;data:Record<string,unknown>}};
async function api<T>(path:string,body?:unknown,method="GET"):Promise<T> {
  const response=await fetch(`/api/debug${path}`,{method,headers:{"Content-Type":"application/json"},...(body!==undefined?{body:JSON.stringify(body)}:{})});
  const value=await response.json();
  if (!response.ok) throw new Error(value.detail??"Debugger request failed");
  return value as T;
}

/** Manual output is available only after this exact registered tool is ready. */
function ManualMotorControls({toolId,ready,onNotice}:{toolId:string;ready:Status["tool_ready"];onNotice:(message:string)=>void}) {
  const [power,setPower]=useState(.2),[running,setRunning]=useState(false);
  const interval=useRef<number|null>(null);
  const canControl=ready?.nodeId===toolId;
  const stop=()=>{
    setRunning(false);
    if(interval.current!==null)window.clearInterval(interval.current);
    interval.current=null;
    if(!canControl||!ready)return;
    void api("/commands",{node_id:ready.nodeId,tool_instance_id:ready.toolInstanceId,command_id:"motor.stop",ttl_ms:1000,
      request_id:crypto.randomUUID()},"POST").catch(error=>onNotice(String(error)));
  };
  const start=()=>{
    if(!canControl||!ready||running)return;
    const send=()=>void api("/parameters",{node_id:ready.nodeId,tool_instance_id:ready.toolInstanceId,parameter_id:"motor.power",value:power,ttl_ms:500},"POST").catch(error=>onNotice(String(error)));
    setRunning(true);send();interval.current=window.setInterval(send,200);
  };
  useEffect(()=>()=>stop(),[canControl,ready?.toolInstanceId]);
  if(!canControl)return <p className="dbg-notice" role="status">Initializing Simple Motor Power on the Control Hub… controls remain disabled until that tool confirms readiness.</p>;
  return <section className="dbg-manual"><h3>Simple Motor Power</h3><p>Hold to run only the selected registered free-spin motor. Releasing the button sends an explicit stop.</p>
    <label>Motor power<input aria-label="Motor power" type="range" min={-.35} max={.35} step={.01} value={power} onChange={event=>setPower(Number(event.target.value))}/><output>{power.toFixed(2)}</output></label>
    <div className="dbg-actions"><button className="primary" onPointerDown={event=>{event.currentTarget.setPointerCapture(event.pointerId);start();}} onPointerUp={stop} onPointerCancel={stop} onPointerLeave={event=>{if(event.currentTarget.hasPointerCapture(event.pointerId))stop();}}>Hold to run at {power.toFixed(2)}</button><button className="danger" onClick={stop}>Stop motor</button></div>
    {running&&<p role="status">Motor power command is active.</p>}
  </section>;
}

export function DebuggerPage() {
  const [manual,setManual]=useState(false),[status,setStatus]=useState<Status>({connected:false});
  const [mechanism,setMechanism]=useState(""),[tool,setTool]=useState(""),[runs,setRuns]=useState<RunManifest[]>([]);
  const [runId,setRunId]=useState(""),[result,setResult]=useState<Result|null>(null),[notice,setNotice]=useState("");
  const [repetitions,setRepetitions]=useState(3),[voltage,setVoltage]=useState(12),[reverse,setReverse]=useState(false);
  const [busy,setBusy]=useState(false),[dismissed,setDismissed]=useState<string[]>([]);
  const selectedRun=runs.find(r=>r.runId===runId),runState=selectedRun?.state;
  const active=runs.find(r=>["awaiting_acceptance","running","receiving","transferring"].includes(r.state));
  const alive=useRef(true);
  const refresh=async()=>{
    try { const [s,r]=await Promise.all([api<Status>("/status"),api<RunManifest[]>("/runs")]); if(alive.current){setStatus(s);setRuns(r);} }
    catch(e){if(alive.current)setNotice(String(e));}
  };
  useEffect(()=>{
    alive.current=true; void refresh(); const timer=window.setInterval(()=>void refresh(),500);
    const socket=new WebSocket(`${location.protocol==="https:"?"wss":"ws"}://${location.host}/ws/debug`);
    socket.onmessage=(event)=>{const m=JSON.parse(event.data); if(m.data?.type==="debug_select_response"&&m.data.data.accepted===false)setNotice(m.data.data.message);};
    return()=>{alive.current=false;window.clearInterval(timer);socket.close();};
  },[]);
  useEffect(()=>{
    if (!runId || !["awaiting_acceptance","running"].includes(runState??""))return;
    const tick=()=>void api(`/runs/${runId}/keepalive`,{},"POST").catch(e=>setNotice(String(e)));
    tick();const timer=window.setInterval(tick,400);return()=>window.clearInterval(timer);
  },[runId,runState]);
  useEffect(()=>{
    let valid=true;
    if(runState==="ready")void api<Result>(`/results/${runId}`).then(r=>{if(valid)setResult(r);}).catch(e=>setNotice(String(e)));
    else setResult(null);
    return()=>{valid=false;};
  },[runId,runState]);
  const nodes=status.manifest?.nodes??[];
  const mechanisms=nodes.filter(n=>n.kind==="debug_folder"&&nodes.some(child=>child.parentId===n.id&&child.kind==="debug_tool"));
  const selectedMechanism=mechanism||mechanisms[0]?.id;
  const tools=nodes.filter(n=>n.parentId===selectedMechanism&&n.kind==="debug_tool");
  const ready=status.tool_ready?.nodeId===tool?status.tool_ready:undefined;
  const benchmark=ready?.benchmarks?.[0];
  const choose=async(node:Node)=>{
    setBusy(true);setNotice("");setTool(node.id);setManual(node.id.endsWith(".power"));setReverse(false);
    try{await api("/select",{node_id:node.id,manifest_revision:status.manifest?.revision},"POST");await refresh();}
    catch(e){setManual(false);setNotice(String(e));}finally{setBusy(false);}
  };
  const run=async()=>{
    if(!ready||!benchmark)return;
    setBusy(true);setNotice("");setResult(null);
    try{const next=await api<{runId:string}>("/runs",{node_id:ready.nodeId,tool_instance_id:ready.toolInstanceId,
      benchmark_id:benchmark.id,request_id:crypto.randomUUID(),repetitions,max_voltage:voltage,reverse},"POST");setRunId(next.runId);await refresh();}
    catch(e){setNotice(String(e));}finally{setBusy(false);}
  };
  const storage=async(action:"discard"|"upload"|"keep")=>{
    if(action==="discard"&&!window.confirm("Discard this benchmark's raw data and report from the laptop?"))return;
    setBusy(true);
    try{
      if(action==="discard"){await api(`/runs/${runId}`,undefined,"DELETE");setRunId("");setResult(null);}
      else await api(`/runs/${runId}/upload`,{keep_local:action==="keep"},"POST");
      setDismissed(d=>[...d,runId]);await refresh();
    }catch(e){setNotice(String(e));}finally{setBusy(false);}
  };
  const popup=result&&selectedRun?.storage?.state!=="complete"&&selectedRun?.storage?.state!=="uploading"&&!dismissed.includes(runId);
  return <main className="dbg-page">
    <header className="dbg-header"><div><p className="dbg-eyebrow">FTC Advanced · Debugger</p><h1>Mechanism benchmarks</h1><p>Run a repeatable experiment. Review the measurements that matter.</p></div><div className="dbg-actions"><button disabled={Boolean(active)} onClick={()=>void api("/launch",{name:"FTC Advanced Debugger"},"POST").then(()=>setNotice("Debugger launched. Waiting for its mechanism registry…")).catch(e=>setNotice(String(e)))}>Launch Debugger OpMode</button><a href="#/">Driver Station</a><a href="#/telemetry">Telemetry Lab</a></div></header>
    {notice&&<div className="dbg-notice" role="status">{notice}</div>}
    <div className="dbg-layout"><aside className="dbg-nav"><p className="dbg-eyebrow">Registered mechanisms · {status.connected?"Connected":"Offline"}</p>{mechanisms.map(m=><button className={selectedMechanism===m.id?"selected":""} key={m.id} disabled={Boolean(active)} onClick={()=>{setMechanism(m.id);setTool("");setManual(false);}}>{m.label}</button>)}{!mechanisms.length&&<p>Launch the Debugger to receive the mechanism list.</p>}</aside>
    <section className="dbg-workbench"><h2>{nodes.find(n=>n.id===selectedMechanism)?.label??"Select a mechanism"}</h2><div className="dbg-tools">{tools.map(t=><article className="dbg-tool" key={t.id}><h3>{t.label}</h3><p>{t.description}</p>{!t.enabled&&<p>{t.disabledReason}</p>}<button disabled={!t.enabled||busy||Boolean(active)} onClick={()=>void choose(t)}>{t.id===tool?"Selected":"Select tool"}</button></article>)}</div>
      {manual&&<ManualMotorControls toolId={tool} ready={ready} onNotice={setNotice}/>} {benchmark&&!manual&&<section><h3>{benchmark.label}</h3><p>{benchmark.description}</p><div className="dbg-inputs"><label>Repetitions<input type="number" min={1} max={5} step={1} value={repetitions} disabled={Boolean(active)} onChange={e=>setRepetitions(Number(e.target.value))}/></label><label>Maximum estimated voltage (V)<input type="number" min={.1} max={12} step={.1} value={voltage} disabled={Boolean(active)} onChange={e=>setVoltage(Number(e.target.value))}/></label>{benchmark.reverseAllowed&&<label>Also test reverse<input type="checkbox" checked={reverse} disabled={Boolean(active)} onChange={e=>setReverse(e.target.checked)}/></label>}</div><button className="primary" disabled={busy||Boolean(active)||!status.connected||!Number.isInteger(repetitions)||repetitions<1||repetitions>5||voltage<.1||voltage>12} onClick={()=>void run()}>Run benchmark</button></section>}
      {selectedRun&&selectedRun.state!=="ready"&&<div className="dbg-progress" role="status"><span>{selectedRun.state.replaceAll("_"," ")}</span><strong>{selectedRun.phase??"Waiting for Control Hub"}</strong><p>{selectedRun.repetition?`Repetition ${selectedRun.repetition} · ${selectedRun.direction===-1?"Reverse":"Forward"}`:""}</p><p>{selectedRun.message}</p>{["awaiting_acceptance","running"].includes(selectedRun.state)&&<button className="danger" onClick={()=>void api(`/runs/${runId}/abort`,{},"POST").catch(e=>setNotice(String(e)))}>Abort benchmark</button>}</div>}
    </section></div>
    {result&&<><DebuggerReport result={result}/>{selectedRun?.storage?.state==="uploading"&&<p role="status">Uploading result… The local copy is retained until upload succeeds.</p>}{selectedRun?.storage?.state==="error"&&<div className="dbg-notice">{selectedRun.storage.detail}<button onClick={()=>setDismissed(d=>d.filter(id=>id!==runId))}>Retry storage decision</button></div>}{selectedRun?.storage?.state==="complete"&&<p>Uploaded successfully. Laptop copy retained.</p>}</>}
    {result&&selectedRun?.storage?.state!=="complete"&&selectedRun?.storage?.state!=="uploading"&&<button onClick={()=>setDismissed(d=>d.filter(id=>id!==runId))}>Choose storage option</button>}
    {runs.length>0&&<section className="dbg-history"><h3>Laptop results</h3>{runs.map(r=><button key={r.runId} disabled={Boolean(active)&&r.runId!==active?.runId} className={runId===r.runId?"selected":""} onClick={()=>setRunId(r.runId)}>{r.mechanismLabel} · {new Date(r.createdAt).toLocaleTimeString()} · {r.outcome??r.state}</button>)}</section>}
    {popup&&<div className="dbg-modal-backdrop"><section className="dbg-modal" role="dialog" aria-modal="true" aria-labelledby="debugger-storage-title"><p className="dbg-eyebrow">Benchmark result ready</p><h2 id="debugger-storage-title">Choose where to keep this result</h2><p>The raw dataset and report are saved on this laptop. Upload only removes them after the Oracle upload succeeds.</p><div className="dbg-actions"><button disabled={busy} onClick={()=>void storage("upload")}>Upload only</button><button disabled={busy} onClick={()=>void storage("keep")}>Upload and keep laptop copy</button><button disabled={busy} className="danger" onClick={()=>void storage("discard")}>Discard</button></div><button onClick={()=>setDismissed(d=>[...d,runId])}>Review measurements first</button></section></div>}
  </main>;
}
