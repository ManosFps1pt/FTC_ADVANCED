import { useEffect, useRef, useState } from "react";
import { parseRecordingDetails, RecordingDetailsFields, RecordingDetailsSummary, recordingDetailsDraft, type RecordingMetadata } from "./RecordingDetails";

type Upload = { state: string; detail?: string; uploadedBytes?: number; totalBytes?: number };
type Run = RecordingMetadata & {
  session_id: string;
  title: string;
  description: string;
  replay_path: string;
  size_bytes: number;
  interrupted: boolean;
  video_finalizing: boolean;
  video_warning: string | null;
  needs_details: boolean;
  prompt_for_details: boolean;
  details_locked: boolean;
  upload: Upload;
};
type RunList = { runs: Run[]; upload_configured: boolean };
type Finding = { t_s: number; replay_path: string; drop_rpm?: number; min_v?: number; peak_a?: number };
type Overview = { result: { quality: { mean_sample_hz: number | null }; flatlined_encoders: string[] } };

async function request<T>(url: string, method = "GET", data?: object): Promise<T> {
  const response = await fetch(url, {
    method, cache: "no-store", headers: data ? { "Content-Type": "application/json" } : undefined,
    body: data ? JSON.stringify(data) : undefined,
  });
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Check the recording details and try again.");
  return body;
}

function uploadLabel(upload: Upload): string {
  const labels: Record<string, string> = {
    local: "Saved locally", queued: "Queued", uploading: "Uploading",
    retrying: "Retrying", uploaded: "Uploaded", failed: "Upload failed", deleting: "Deleting",
  };
  const label = labels[upload.state] ?? upload.state;
  if (upload.state !== "uploading" || !upload.totalBytes) return label;
  return `${label} ${Math.min(100, Math.round(100 * (upload.uploadedBytes ?? 0) / upload.totalBytes))}%`;
}

function ManageRecording({ run, onChange, onClose }: { run: Run; onChange: () => Promise<void>; onClose?: () => void }) {
  const [title, setTitle] = useState(run.title);
  const [description, setDescription] = useState(run.description);
  const [draft, setDraft] = useState(() => recordingDetailsDraft(run));
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const path = `/api/reviews/${run.session_id}`;
  const locked = busy || run.details_locked;
  const deleteLocked = busy || run.video_finalizing || ["queued", "uploading", "retrying", "deleting"].includes(run.upload.state);
  const details = parseRecordingDetails(draft);

  const update = async (method: "PATCH" | "DELETE") => {
    if (method === "PATCH" && (!details || locked)) return;
    setBusy(true);
    setNotice("");
    try {
      await request(path, method, method === "PATCH" ? { title: title.trim(), description, ...details } : undefined);
      await onChange();
      if (method === "PATCH") {
        setNotice("Recording details saved.");
        onClose?.();
      }
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return <div className="recording-manage">
    <form onSubmit={event => { event.preventDefault(); void update("PATCH"); }}>
      <label>Title<input value={title} maxLength={120} required disabled={locked}
        onChange={event => setTitle(event.target.value)} /></label>
      <label>Notes<textarea value={description} maxLength={4000} disabled={locked}
        onChange={event => setDescription(event.target.value)} /></label>
      <RecordingDetailsFields value={draft} onChange={setDraft} disabled={locked} />
      <div className="recording-actions">
        <button type="submit" className="primary" disabled={locked || !title.trim() || !details}>Save details</button>
        {onClose ? <button type="button" disabled={busy} onClick={onClose}>Fill in later</button>
          : <button type="button" className="danger" disabled={deleteLocked} onClick={() => {
          if (window.confirm(`Delete the laptop copy of “${run.title}”?`)) void update("DELETE");
        }}>Delete local copy</button>}
      </div>
    </form>
    {notice && <small role="status">{notice}</small>}
  </div>;
}

function RecordingDetailsDialog({ run, onChange, onClose }: { run: Run; onChange: () => Promise<void>; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className="recording-details-dialog" aria-labelledby="recording-details-title"
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <h2 id="recording-details-title">Recording details</h2>
    <ManageRecording run={run} onChange={onChange} onClose={onClose} />
  </dialog>;
}

export function useRecordingLibrary(robotState: string) {
  const [library, setLibrary] = useState<RunList>();
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let mounted = true;
    let timer: number | undefined;
    const update = async () => {
      try {
        const value = await request<RunList>("/api/reviews?limit=256");
        if (mounted) { setLibrary(value); setLoadError(""); }
      } catch (reason) {
        if (mounted) setLoadError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        if (mounted) timer = window.setTimeout(update, 2500);
      }
    };
    void update();
    return () => { mounted = false; window.clearTimeout(timer); };
  }, [robotState]);

  return {
    library, loadError,
    refresh: async () => setLibrary(await request<RunList>("/api/reviews?limit=256")),
  };
}

export function RecordingDetailsPrompt({ library, refresh }: Pick<ReturnType<typeof useRecordingLibrary>, "library" | "refresh">) {
  const [deferred, setDeferred] = useState<string[]>([]);
  const prompt = library?.runs.find(run => run.prompt_for_details && !run.details_locked && !deferred.includes(run.session_id));
  return prompt ? <RecordingDetailsDialog key={prompt.session_id} run={prompt} onChange={refresh}
    onClose={() => setDeferred(current => [...current, prompt.session_id])} /> : null;
}

export function RecordingsPanel({ library, loadError, refresh }: ReturnType<typeof useRecordingLibrary>) {
  const [error, setError] = useState("");
  const [queueing, setQueueing] = useState(false);
  const [reviewId, setReviewId] = useState("");
  const [manageId, setManageId] = useState("");
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [reviewError, setReviewError] = useState("");
  const [missingSignals, setMissingSignals] = useState<string[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);

  useEffect(() => {
    if (!reviewId) return;
    let mounted = true;
    setFindings(null);
    setReviewError("");
    setMissingSignals([]);
    setOverview(null);
    const requests = ["shooter_dips", "voltage_sags", "intake_loads"].map(kind =>
      request<{ events: Finding[]; missing_signals: string[] }>(`/api/reviews/${reviewId}/events/${kind}?limit=3`));
    void Promise.all([
      request<Overview>(`/api/reviews/${reviewId}/report`),
      Promise.all(requests),
    ]).then(([report, results]) => {
      if (mounted) {
        setOverview(report);
        setFindings(results.flatMap(result => result.events));
        setMissingSignals([...new Set(results.flatMap(result => result.missing_signals))]);
      }
    }).catch(reason => { if (mounted) setReviewError(reason.message); });
    return () => { mounted = false; };
  }, [reviewId]);

  const upload = async () => {
    setQueueing(true);
    setError("");
    try {
      await request("/api/reviews/upload-pending", "POST");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setQueueing(false);
    }
  };

  const runs = library?.runs ?? [];
  const pending = runs.filter(run => !run.needs_details && ["local", "failed"].includes(run.upload.state));
  const missingDetails = runs.filter(run => run.needs_details && run.upload.state !== "uploaded").length;
  const pendingMb = Math.ceil(pending.reduce((sum, run) => sum + run.size_bytes, 0) / 1e6);
  const transfers = runs.filter(run => ["queued", "uploading", "retrying"].includes(run.upload.state)).length;

  const row = (run: Run) => <li key={run.session_id}>
    <div><strong>{run.title}</strong><small title={run.upload.detail}>
      {[uploadLabel(run.upload), run.needs_details && run.upload.state !== "uploaded" && "Details required",
        run.interrupted && "Interrupted", run.video_finalizing && "Finishing video"]
        .filter(Boolean).join(" · ")}
    </small><small><RecordingDetailsSummary metadata={run} /></small>{run.video_warning && <small>{run.video_warning}</small>}
    {run.upload.detail && run.upload.state !== "uploading" && <small>{run.upload.detail}</small>}</div>
    <div className="recording-actions">
      <a href={run.replay_path}>Replay</a>
      <button type="button" onClick={() => setReviewId(reviewId === run.session_id ? "" : run.session_id)}>
        {reviewId === run.session_id ? "Close report" : "Find events"}
      </button>
      <button type="button" onClick={() => setManageId(manageId === run.session_id ? "" : run.session_id)}>
        {manageId === run.session_id ? "Close details" : "Details"}
      </button>
    </div>
    {reviewId === run.session_id && <div className="recording-findings" aria-live="polite">
      {overview && <small className="recording-quality">
        {overview.result.quality.mean_sample_hz != null && `${overview.result.quality.mean_sample_hz.toFixed(1)} Hz measured. `}
        {overview.result.flatlined_encoders.length > 0 && `Powered encoder velocity stayed at zero: ${overview.result.flatlined_encoders.join(", ")}.`}
      </small>}
      {reviewError ? <small>{reviewError}</small> : findings === null ? <small>Analyzing telemetry…</small> :
        findings.length === 0 ? <small>No events found by the current analysis.</small> : findings.map(finding =>
          <a key={`${finding.t_s}-${finding.drop_rpm}-${finding.min_v}-${finding.peak_a}`} href={finding.replay_path}>
            {finding.t_s.toFixed(3)} s · {finding.drop_rpm !== undefined ? `${Math.round(finding.drop_rpm)} RPM drop`
              : finding.min_v !== undefined ? `${finding.min_v.toFixed(2)} V sag` : `${finding.peak_a?.toFixed(2)} A intake load`}
          </a>)}
      {missingSignals.length > 0 && <small title={missingSignals.join(", ")}>Some checks lack usable telemetry.</small>}
    </div>}
    {manageId === run.session_id && <ManageRecording key={run.session_id} run={run} onChange={refresh} />}
  </li>;

  return <section className="panel local-recordings" aria-label="Local recordings">
    <div className="recordings-heading">
      <div><h2>Recordings</h2><small>{transfers ? `${transfers} in upload queue` : `${pending.length} pending upload`}
        {missingDetails > 0 && ` · ${missingDetails} need details`}</small></div>
      <div className="recording-actions">
        <a href="/replays/">Replay library</a>
        <button type="button" className="primary" disabled={!pending.length || queueing || !library?.upload_configured} onClick={() => void upload()}>
          {pending.length ? `Upload pending (${pending.length} · ${pendingMb} MB)` : "Upload pending"}
        </button>
      </div>
    </div>
    {library && !library.upload_configured && <small>Uploads are unavailable until a server is configured.</small>}
    {(error || loadError) && <small role="alert">{error || loadError}</small>}
    {runs.length > 0 && <ul>{row(runs[0])}</ul>}
    {runs.length > 1 && <details><summary>Previous recordings ({runs.length - 1})</summary><ul>{runs.slice(1).map(row)}</ul></details>}
  </section>;
}
