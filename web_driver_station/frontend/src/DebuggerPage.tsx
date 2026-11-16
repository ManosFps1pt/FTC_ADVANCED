import { BenchmarkForm, type InputValue } from "./debugger/BenchmarkForm";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { MechanismControls, type DebugParameter, type SendMechanismAction } from "./debugger/MechanismControls";
import { DebuggerReport, type Benchmark, type Result, type RunManifest } from "./debugger/Report";

type Node = {id:string;parentId:string;label:string;description:string;kind:string;enabled:boolean;disabledReason:string};
type Status = {connected:boolean;selected_session?:string;sessions?:{session_id:string;robot_id:string;opmode:string;protocol:number}[];safety?:{armed?:boolean};manifest?:{revision:number;nodes:Node[]};tool_ready?:{nodeId:string;toolInstanceId:string;commands?:{id:string}[];parameters?:DebugParameter[];benchmarks?:Benchmark[]};last_event?:{type:string;data:Record<string,unknown>}};
type DebugReply = { requestId: string; result: string; message?: string; parameterId?: string; effectiveFloat64?: number; outputs?: { id: string; value: string | number }[] };
let browserLease:string|null=null;
async function api<T>(path:string,body?:unknown,method="GET"):Promise<T> {
  const response=await fetch(`/api/debug${path}`,{method,headers:{"Content-Type":"application/json"},...(body!==undefined?{body:JSON.stringify(typeof body==="object"&&body!==null?{...body,lease_id:browserLease}:body)}:{})});
  const value=await response.json();
  if (!response.ok) throw new Error(value.detail??"Debugger request failed");
  return value as T;
}

/** Manual output is available only after this exact registered tool is ready. */
function ManualMotorControls({toolId,ready,onNotice,authorized}:{authorized:boolean;toolId:string;ready:Status["tool_ready"];onNotice:(message:string)=>void}) {
  const [power,setPower]=useState(.2),[running,setRunning]=useState(false);
  const interval=useRef<number|null>(null);
  const parameter=ready?.parameters?.find(item=>item.id==="motor.power"&&item.writable);
  const canControl=authorized&&ready?.nodeId===toolId&&Boolean(parameter);
  useEffect(()=>{if(parameter)setPower(value=>Math.min(parameter.max,Math.max(parameter.min,value)));},[parameter?.min,parameter?.max]);
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
  if(!canControl)return <p className="dbg-notice" role="status">{ready?.nodeId!==toolId?"Waiting for the selected tool to become ready.":!parameter?"This tool has not advertised a writable motor output.":"Acquire debugger control, then click Arm selected motor."}</p>;
  return <section className="dbg-manual"><h3>Simple Motor Power</h3><p>Hold to run only the selected registered free-spin motor. Releasing the button sends an explicit stop.</p>
      <label>Motor power<input aria-label="Motor power" type="range" min={parameter!.min} max={parameter!.max} step={.01} value={power} disabled={running} onChange={event=>setPower(Number(event.target.value))}/><output>{power.toFixed(2)}</output></label>
    <div className="dbg-actions"><button className="primary" onPointerDown={event=>{event.currentTarget.setPointerCapture(event.pointerId);start();}} onPointerUp={stop} onPointerCancel={stop} onPointerLeave={event=>{if(event.currentTarget.hasPointerCapture(event.pointerId))stop();}}>Hold to run at {power.toFixed(2)}</button><button className="danger" onClick={stop}>Stop motor</button></div>
    {running&&<p role="status">Motor power command is active.</p>}
  </section>;
}

export function DebuggerPage({ opModeControl, onDebuggerLaunched }: { opModeControl: ReactNode; onDebuggerLaunched: () => void }) {
  const [manual,setManual]=useState(false),[status,setStatus]=useState<Status>({connected:false});
  const [mechanism,setMechanism]=useState(""),[tool,setTool]=useState(""),[runs,setRuns]=useState<RunManifest[]>([]);
  const [runId,setRunId]=useState(""),[result,setResult]=useState<Result|null>(null),[notice,setNotice]=useState("");
  const [busy,setBusy]=useState(false),[dismissed,setDismissed]=useState<string[]>([]);
  const selectedRun=runs.find(r=>r.runId===runId),runState=selectedRun?.state;
  const active=runs.find(r=>["awaiting_acceptance","running","receiving","transferring"].includes(r.state));
  const [lease,setLease]=useState<string|null>(null);
  const alive=useRef(true);
  const replies=useRef(new Map<string, (reply: DebugReply) => void>());
  useEffect(()=>{
    if(!lease)return;
    let pending=false,disposed=false;
    const release=()=>{
      if(disposed)return;disposed=true;
      void api("/control/release",{},"POST").catch(()=>{});
      browserLease=null;setLease(null);
    };
    const tick=async()=>{
      if(pending||disposed)return;
      if(document.hidden||!document.hasFocus()){release();return;}
      pending=true;
      try{await api("/control/renew",{},"POST");}catch(e){setNotice(String(e));release();}
      finally{pending=false;}
    };
    const timer=window.setInterval(()=>void tick(),100);
    window.addEventListener("blur",release);
    return()=>{window.clearInterval(timer);window.removeEventListener("blur",release);release();};
  },[lease]);
  const acquire=async()=>{
    try{const response=await api<{lease_id:string}>("/control/acquire",{},"POST");
      browserLease=response.lease_id;setLease(response.lease_id);
      setNotice("Control acquired. Arm the selected tool to enable its controls.");
    }catch(e){setNotice(String(e));}
  };
  const authorized=Boolean(lease&&status.connected&&status.safety?.armed);
  const launch=async()=>{
    setBusy(true);setNotice("");
    try{await api("/launch",{name:"FTC Advanced Debugger"},"POST");onDebuggerLaunched();setTool("");setManual(false);setNotice("Debugger started. Select its robot session and a tool.");}
    catch(e){setNotice(String(e));}finally{setBusy(false);}
  };
  const arm=async()=>{
    setBusy(true);
    try{await api("/control/arm",{},"POST");setNotice("Arm requested. Waiting for the robot to enable the test.");await refresh();}
    catch(e){setNotice(String(e));}finally{setBusy(false);}
  };
  const refresh=async()=>{
    try { const [s,r]=await Promise.all([api<Status>("/status"),api<RunManifest[]>("/runs")]); if(alive.current){setStatus(s);setRuns(r);} }
    catch(e){if(alive.current)setNotice(String(e));}
  };
  useEffect(()=>{
    alive.current=true; void refresh(); const timer=window.setInterval(()=>void refresh(),500);
    const socket=new WebSocket(`${location.protocol==="https:"?"wss":"ws"}://${location.host}/ws/debug`);
    socket.onmessage=(event)=>{
      const m=JSON.parse(event.data);
      if(m.kind!=="debug")return;
      if(m.data.type==="debug_select_response"&&m.data.data.accepted===false)setNotice(m.data.data.message);
      if(m.data.type==="debug_command_response"||m.data.type==="debug_parameter_set_response") {
        const reply=m.data.data as DebugReply;
        replies.current.get(reply.requestId)?.(reply);
      }
    };
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
  const sendMechanismAction=useCallback<SendMechanismAction>(async action=>{
    if(!ready?.toolInstanceId)throw new Error("Select Mechanism Controls first.");
    // Register before dispatch so a quick robot reply can arrive before HTTP completes.
    const requestId=crypto.randomUUID();
    const body={node_id:tool,tool_instance_id:ready.toolInstanceId,request_id:requestId,ttl_ms:500};
    const isParameter="parameterId" in action;
    const reply=await new Promise<DebugReply>((resolve,reject)=>{
      const timer=window.setTimeout(()=>{
        replies.current.delete(requestId);
        reject(new Error("Control Hub did not confirm the request."));
      },1500);
      const cleanup=()=>{window.clearTimeout(timer);replies.current.delete(requestId);};
      replies.current.set(requestId,response=>{cleanup();resolve(response);});
      void (isParameter
        ? api("/parameters",{...body,parameter_id:action.parameterId,value:action.value},"POST")
        : api("/commands",{...body,command_id:action.commandId},"POST")
      ).catch(error=>{cleanup();reject(error);});
    });
    if(reply.result==="debug_parameter_applied"&&reply.parameterId&&reply.effectiveFloat64!==undefined) {
      return {[reply.parameterId]:reply.effectiveFloat64};
    }
    if(reply.result==="debug_command_completed") {
      return Object.fromEntries((reply.outputs??[]).map(output=>[output.id,output.value]));
    }
    throw new Error(reply.message||"The Control Hub rejected the request.");
  },[tool,ready?.toolInstanceId]);
  const choose=async(node:Node)=>{
    setBusy(true);setNotice("");setTool(node.id);setManual(node.id.endsWith(".power"));
    try{await api("/select",{node_id:node.id,manifest_revision:status.manifest?.revision},"POST");await refresh();}
    catch(e){setManual(false);setNotice(String(e));}finally{setBusy(false);}
  };
  const run=async(inputs:Record<string,InputValue>)=>{
    if(!ready||!benchmark)return;
    setBusy(true);setNotice("");setResult(null);
    try{const next=await api<{runId:string}>("/runs",{node_id:ready.nodeId,tool_instance_id:ready.toolInstanceId,
      benchmark_id:benchmark.id,request_id:crypto.randomUUID(),inputs},"POST");setRunId(next.runId);await refresh();}
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
    <header className="dbg-header"><div><p className="dbg-eyebrow">FTC Advanced</p><h1>Debugger</h1></div><div className="dbg-actions"><a href="#/">Driver Station</a><a href="#/telemetry">Telemetry Lab</a></div></header>
    <p>Connect to the robot, start the debugger, then select a tool, acquire control and arm it here. Loss of focus or connection stops motor output and requires rearming.</p>
    <div className="dbg-actions"><button disabled={busy||Boolean(lease||active)} onClick={()=>void launch()}>Start debugger from laptop</button></div>
    <details open><summary>Laptop robot controls</summary><div className="debugger-opmode-control">{opModeControl}</div></details>
    <div className="dbg-actions"><label>Robot session <select aria-label="Robot session" value={status.selected_session??""} disabled={Boolean(lease||active)} onChange={event=>void api("/target",{session_id:event.target.value},"POST").then(()=>{setTool("");setManual(false);void refresh();}).catch(e=>setNotice(String(e)))}>
      <option value="" disabled>Select a connected robot</option>{status.sessions?.map(session=><option key={session.session_id} value={session.session_id}>{session.robot_id} · {session.opmode} · v{session.protocol}</option>)}</select></label>
      <button disabled={!status.connected||Boolean(lease)||!status.tool_ready} onClick={()=>void acquire()}>{lease?"Control acquired":"Acquire debugger control"}</button>
      {lease&&<button disabled={busy||authorized||!ready} onClick={()=>void arm()}>{tool==="biobuzz.mechanisms"?"Arm mechanism controls":"Arm selected motor"}</button>}
      {lease&&<button onClick={()=>setLease(null)}>Release control</button>}<span>{authorized?"Armed":"Observation only"}</span></div>
    {notice&&<div className="dbg-notice" role="status">{notice}</div>}
    <div className="dbg-layout"><aside className="dbg-nav"><p className="dbg-eyebrow">Registered mechanisms · {status.connected?"Connected":"Offline"}</p>{mechanisms.map(m=><button className={selectedMechanism===m.id?"selected":""} key={m.id} disabled={Boolean(active)} onClick={()=>{if(selectedMechanism===m.id)return;if(tool==="biobuzz.mechanisms")setLease(null);setMechanism(m.id);setTool("");setManual(false);}}>{m.label}</button>)}{!mechanisms.length&&<p>{status.connected?"The robot is connected but has not sent a debugger registry yet.":"No robot data connection. Connect to the robot from the Driver Station page, then click Start debugger from laptop."}</p>}</aside>
    <section className="dbg-workbench"><h2>{nodes.find(n=>n.id===selectedMechanism)?.label??"Select a mechanism"}</h2><div className="dbg-tools">{tools.map(t=><article className="dbg-tool" key={t.id}><h3>{t.label}</h3><p>{t.description}</p>{!t.enabled&&<p>{t.disabledReason}</p>}<button disabled={!t.enabled||busy||Boolean(active)} onClick={()=>void choose(t)}>{t.id===tool?"Selected":"Select tool"}</button></article>)}</div>
      {manual&&<ManualMotorControls toolId={tool} ready={ready} authorized={authorized} onNotice={setNotice}/>} {benchmark&&!manual&&<BenchmarkForm key={ready?.toolInstanceId} benchmark={benchmark}
        disabled={busy||Boolean(active)||!authorized} onRun={inputs=>void run(inputs)}/>}
      {ready&&tool==="biobuzz.mechanisms"&&<MechanismControls key={ready.toolInstanceId} parameters={ready.parameters??[]}
        authorized={authorized} send={sendMechanismAction} onNotice={setNotice}/>}
      {selectedRun&&selectedRun.state!=="ready"&&<div className="dbg-progress" role="status"><span>{selectedRun.state.replaceAll("_"," ")}</span><strong>{selectedRun.phase??"Waiting for Control Hub"}</strong><p>{selectedRun.repetition?`Repetition ${selectedRun.repetition} · ${selectedRun.direction===-1?"Reverse":"Forward"}`:""}</p><p>{selectedRun.message}</p>{["awaiting_acceptance","running"].includes(selectedRun.state)&&<button className="danger" onClick={()=>void api(`/runs/${runId}/abort`,{},"POST").catch(e=>setNotice(String(e)))}>Abort benchmark</button>}</div>}
    </section></div>
    {result&&<><DebuggerReport result={result}/>{selectedRun?.storage?.state==="uploading"&&<p role="status">Uploading result… The local copy is retained until upload succeeds.</p>}{selectedRun?.storage?.state==="error"&&<div className="dbg-notice">{selectedRun.storage.detail}<button onClick={()=>setDismissed(d=>d.filter(id=>id!==runId))}>Retry storage decision</button></div>}{selectedRun?.storage?.state==="complete"&&<p>Uploaded successfully. Laptop copy retained.</p>}</>}
    {result&&selectedRun?.storage?.state!=="complete"&&selectedRun?.storage?.state!=="uploading"&&<button onClick={()=>setDismissed(d=>d.filter(id=>id!==runId))}>Choose storage option</button>}
    {runs.length>0&&<section className="dbg-history"><h3>Laptop results</h3>{runs.map(r=><button key={r.runId} disabled={Boolean(active)&&r.runId!==active?.runId} className={runId===r.runId?"selected":""} onClick={()=>setRunId(r.runId)}>{r.mechanismLabel} · {new Date(r.createdAt).toLocaleTimeString()} · {r.outcome??r.state}</button>)}</section>}
    {popup&&<div className="dbg-modal-backdrop"><section className="dbg-modal" role="dialog" aria-modal="true" aria-labelledby="debugger-storage-title"><p className="dbg-eyebrow">Benchmark result ready</p><h2 id="debugger-storage-title">Choose where to keep this result</h2><p>The raw dataset and report are saved on this laptop. Upload only removes them after the Oracle upload succeeds.</p><div className="dbg-actions"><button disabled={busy} onClick={()=>void storage("upload")}>Upload only</button><button disabled={busy} onClick={()=>void storage("keep")}>Upload and keep laptop copy</button><button disabled={busy} className="danger" onClick={()=>void storage("discard")}>Discard</button></div><button onClick={()=>setDismissed(d=>[...d,runId])}>Review measurements first</button></section></div>}
  </main>;
}

