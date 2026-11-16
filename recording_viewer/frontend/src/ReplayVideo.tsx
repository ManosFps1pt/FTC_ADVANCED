import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

type Operation = "sync" | "export";
type VideoStatus = {
  running: Operation | null;
  error: string | null;
  sync: { video_delay_seconds: number; matched_edges: number } | null;
  export: { telemetry_start_seconds: number; telemetry_end_seconds: number } | null;
};

async function videoRequest(url: string, operation?: Operation): Promise<VideoStatus> {
  const response = await fetch(`${url}/${operation ?? "status"}`, operation ? {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  } : { cache: "no-store" });
  if (!response.ok) {
    const message = await response.json().catch(() => null);
    throw new Error(typeof message?.detail === "string" ? message.detail : "Could not contact the video service.");
  }
  return response.json();
}

export function ReplayVideo({ clip, children, playbackSeconds, playing, onSyncedClip, onTogglePlayback }: {
  clip: { name: string; url: string };
  children?: ReactNode;
  playbackSeconds: number;
  playing: boolean;
  onSyncedClip: (clip: { url: string; delay: number } | null) => void;
  onTogglePlayback?: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [duration, setDuration] = useState<number | null>(null);
  const [mediaError, setMediaError] = useState(false);
  const [status, setStatus] = useState<VideoStatus | null>(null);
  const [pending, setPending] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [statusAttempt, setStatusAttempt] = useState(0);
  const running = status?.running;
  const needsStatus = status === null || Boolean(running);
  const savedDelay = status?.sync?.video_delay_seconds ?? null;

  useEffect(() => {
    onSyncedClip(savedDelay === null ? null : { url: clip.url, delay: savedDelay });
  }, [clip.url, onSyncedClip, savedDelay]);

  useEffect(() => {
    let mounted = true;
    let timer: number | undefined;
    const check = async () => {
      try {
        const next = await videoRequest(clip.url);
        if (!mounted) return;
        setStatus(next);
        setRequestError(null);
        if (next.running) timer = window.setTimeout(check, 2000);
      } catch (error) {
        if (!mounted) return;
        setRequestError(String((error as Error).message));
        if (running) timer = window.setTimeout(check, 4000);
      }
    };
    if (needsStatus) void check();
    return () => { mounted = false; window.clearTimeout(timer); };
  }, [clip.url, running, needsStatus, statusAttempt]);

  const delay = status?.sync?.video_delay_seconds ?? 0;
  const videoTime = playbackSeconds + delay;
  const loading = duration === null || (status === null && requestError === null);
  const visible = !mediaError && !loading && videoTime >= 0 && videoTime < duration;
  useLayoutEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (!visible) {
      video.pause();
      return;
    }
    if (Math.abs(video.currentTime - videoTime) > (playing ? 0.12 : 0.001)) video.currentTime = videoTime;
    if (playing && video.paused) void video.play().catch(() => undefined);
    if (!playing && !video.paused) video.pause();
  }, [playing, videoTime, visible]);

  const start = async (operation: Operation) => {
    setPending(true);
    setRequestError(null);
    try {
      setStatus(await videoRequest(clip.url, operation));
    } catch (error) {
      setRequestError((error as Error).message);
    } finally {
      setPending(false);
    }
  };
  const busy = pending || Boolean(running);

  return <section className="replay-video panel">
    <div className="replay-video-header">
      <div className="video-heading">
        <div><p className="eyebrow">Synchronized camera</p><strong>{clip.name}</strong></div>
        {children}
      </div>
      <div className="video-actions">
        <button type="button" onClick={() => status ? void start("sync") : setStatusAttempt(value => value + 1)}
          disabled={busy || (!status && !requestError)}>{running === "sync" ? "Syncing…" : status ? "Sync video" : "Retry loading sync"}</button>
        <button type="button" onClick={() => void start("export")} disabled={busy || !status?.sync}>
          {running === "export" ? "Exporting…" : "Export trimmed MP4"}
        </button>
        {status?.export && <a href={`${clip.url}/download`}>Download trimmed MP4</a>}
      </div>
      <div className="video-sync-status" aria-live="polite">
        {status?.sync && <small>
          {delay === 0 ? "No video delay" : `Video ${Math.abs(delay).toFixed(3)} s ${delay > 0 ? "behind" : "ahead"}`}
          {" · "}{status.sync.matched_edges} LED matches
        </small>}
        {(requestError || status?.error) && <small className="video-sync-error">{requestError || status?.error}</small>}
      </div>
    </div>
    <div className="video-stage" onClick={onTogglePlayback}>
      <video ref={videoRef} src={clip.url} muted playsInline preload="metadata" hidden={!visible}
        onLoadedMetadata={event => {
          const length = event.currentTarget.duration;
          setDuration(Number.isFinite(length) ? length : null);
        }}
        onError={() => setMediaError(true)} />
      {!visible && <div className="black-frame">
        {mediaError ? "Video could not be loaded" : loading ? "Loading video…" : videoTime < 0 ? "Video has not started" : "Video ended"}
      </div>}
    </div>
  </section>;
}
