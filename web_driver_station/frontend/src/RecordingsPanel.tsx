import { useEffect, useState } from "react";

type Upload = { state: string; detail?: string; uploadedBytes?: number; totalBytes?: number };
type Run = {
  session_id: string;
  title: string;
  description: string;
  replay_path: string;
  size_bytes: number;
  interrupted: boolean;
  video_finalizing: boolean;
  video_warning: string | null;
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
  if (!response.ok) throw new Error(body.detail || "Could not load recordings");
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

function ManageRecording({ run, onChange }: { run: Run; onChange: () => Promise<void> }) {
  const [title, setTitle] = useState(run.title);
  const [description, setDescription] = useState(run.description);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const path = `/api/reviews/${run.session_id}`;
  const locked = busy || run.video_finalizing || ["queued", "uploading", "retrying", "deleting"].includes(run.upload.state);

  const update = async (method: "PATCH" | "DELETE") => {
    setBusy(true);
    setNotice("");
    try {
      await request(path, method, method === "PATCH" ? { title: title.trim(), description } : undefined);
      await onChange();
      if (method === "PATCH") setNotice("Notes saved.");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return <div className="recording-manage">
    <form onSubmit={event => { event.preventDefault(); void update("PATCH"); }}>
      <label>Title<input value={title} maxLength={120} required disabled={locked || run.upload.state === "uploaded"}
        onChange={event => setTitle(event.target.value)} /></label>
      <label>Notes<textarea value={description} maxLength={4000} disabled={locked || run.upload.state === "uploaded"}
        onChange={event => setDescription(event.target.value)} /></label>
      <div className="recording-actions">
        <button type="submit" disabled={locked || run.upload.state === "uploaded" || !title.trim()}>Save notes</button>
        <button type="button" className="danger" disabled={locked} onClick={() => {
          if (window.confirm(`Delete the laptop copy of “${run.title}”?`)) void update("DELETE");
        }}>Delete local copy</button>
      </div>
    </form>
    {notice && <small role="status">{notice}</small>}
  </div>;
}

export function RecordingsPanel({ robotState }: { robotState: string }) {
  const [library, setLibrary] = useState<RunList>();
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [queueing, setQueueing] = useState(false);
  const [reviewId, setReviewId] = useState("");
  const [manageId, setManageId] = useState("");
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [reviewError, setReviewError] = useState("");
  const [missingSignals, setMissingSignals] = useState<string[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);

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

  const refresh = async () => setLibrary(await request<RunList>("/api/reviews?limit=256"));
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
  const pending = runs.filter(run => ["local", "failed"].includes(run.upload.state));
  const pendingMb = Math.ceil(pending.reduce((sum, run) => sum + run.size_bytes, 0) / 1e6);
  const transfers = runs.filter(run => ["queued", "uploading", "retrying"].includes(run.upload.state)).length;

  const row = (run: Run) => <li key={run.session_id}>
    <div><strong>{run.title}</strong><small title={run.upload.detail}>
      {[uploadLabel(run.upload), run.interrupted && "Interrupted", run.video_finalizing && "Finishing video"]
        .filter(Boolean).join(" · ")}
    </small>{run.video_warning && <small>{run.video_warning}</small>}
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
    {manageId === run.session_id && <ManageRecording run={run} onChange={refresh} />}
  </li>;

  return <section className="panel local-recordings" aria-label="Local recordings">
    <div className="recordings-heading">
      <div><h2>Recordings</h2><small>{transfers ? `${transfers} in upload queue` : `${pending.length} pending upload`}</small></div>
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
