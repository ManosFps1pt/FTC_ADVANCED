import { type ReactNode, useEffect, useRef, useState } from "react";
import type { TheaterExportOptions } from "./theater-export";

const OVERLAYS = { lights: "RGB lights", motors: "Motor telemetry", gamepads: "Gamepads", graphs: "Graphs", map: "Position map" };
export type Overlay = keyof typeof OVERLAYS;
type OverlaySettings = Record<Overlay, boolean>;
const SETTINGS_KEY = "ftc.replay.theater.overlays";
const defaults: OverlaySettings = { lights: true, motors: true, gamepads: true, graphs: true, map: true };

export function useReplayOverlays() {
  const [overlays, setOverlays] = useState<OverlaySettings>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "null");
      return {
        lights: typeof saved?.lights === "boolean" ? saved.lights : true,
        motors: typeof saved?.motors === "boolean" ? saved.motors : true,
        gamepads: typeof saved?.gamepads === "boolean" ? saved.gamepads : true,
        graphs: typeof saved?.graphs === "boolean" ? saved.graphs : true,
        map: typeof saved?.map === "boolean" ? saved.map : true,
      };
    } catch {
      return defaults;
    }
  });
  useEffect(() => {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(overlays)); } catch { /* Settings are optional. */ }
  }, [overlays]);
  const toggleOverlay = (key: Overlay) => setOverlays(previous => ({ ...previous, [key]: !previous[key] }));
  return { overlays, toggleOverlay };
}

export function TheaterHeader({ title, overlays, toggleOverlay, tracePicker, onLeave, exportOptions }: {
  title: string;
  overlays: OverlaySettings;
  toggleOverlay: (key: Overlay) => void;
  tracePicker: ReactNode;
  onLeave: () => void;
  exportOptions: TheaterExportOptions;
}) {
  return <header className="theater-header">
    <h1>{title}</h1>
    <div className="theater-header-actions">
      <TheaterExport options={exportOptions} />
      <details className="theater-settings">
        <summary>Overlays</summary>
        <div className="theater-settings-panel">
          <fieldset><legend>Show on video</legend>
            {(Object.keys(OVERLAYS) as Overlay[]).map(key => <label key={key}>
              <input type="checkbox" checked={overlays[key]} onChange={() => toggleOverlay(key)} />{OVERLAYS[key]}
            </label>)}
          </fieldset>
          <fieldset><legend>Graph signals</legend>{tracePicker}</fieldset>
        </div>
      </details>
      <button type="button" onClick={onLeave}>Console view</button>
    </div>
  </header>;
}

function TheaterExport({ options }: { options: TheaterExportOptions }) {
  const job = useRef<AbortController | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [download, setDownload] = useState<{ url: string; filename: string } | null>(null);
  useEffect(() => () => job.current?.abort(), []);
  useEffect(() => () => { if (download) URL.revokeObjectURL(download.url); }, [download]);

  const start = async () => {
    if (job.current) { job.current.abort(); return; }
    const controller = new AbortController();
    job.current = controller;
    setError("");
    setDownload(null);
    setProgress(0);
    try {
      const { exportTheater } = await import("./theater-export");
      const blob = await exportTheater(options, controller.signal, setProgress);
      const filename = (options.title.trim().replace(/[<>:"/\\|?*]/g, "_").slice(0, 100) || "FTC replay") + " - Video view.mp4";
      const url = URL.createObjectURL(blob);
      setDownload({ url, filename });
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "Could not export the video.");
    } finally {
      job.current = null;
      setProgress(null);
    }
  };

  return <div className="theater-export">
    <button type="button" onClick={() => void start()} aria-label={progress === null ? undefined : "Cancel video export"}>
      {progress === null ? "Export MP4" : `Cancel · ${progress}%`}
    </button>
    {(download || error) && <div className="theater-export-result" role={error ? "alert" : "status"}>
      {error || (download && <a href={download.url} download={download.filename}>Download 1080p MP4</a>)}
    </div>}
  </div>;
}

export function TheaterControls({ timeLabel, position, duration, playing, togglePlayback, seek }: {
  timeLabel: string;
  position: number;
  duration: number;
  playing: boolean;
  togglePlayback: () => void;
  seek: (seconds: number) => void;
}) {
  const [fullscreen, setFullscreen] = useState(Boolean(document.fullscreenElement));
  useEffect(() => {
    const update = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", update);
    return () => document.removeEventListener("fullscreenchange", update);
  }, []);

  return <section className="theater-controls" aria-label="Video playback">
    <input type="range" aria-label="Replay position" min={0} max={duration} step="0.001"
      value={position} onChange={event => seek(Number(event.target.value))} />
    <div className="theater-control-row">
      <button type="button" className="theater-play" onClick={togglePlayback}>{playing ? "Pause" : "Play"}</button>
      <button type="button" aria-label="Back five seconds" onClick={() => seek(position - 5)}>−5 s</button>
      <button type="button" aria-label="Forward five seconds" onClick={() => seek(position + 5)}>+5 s</button>
      <span>{timeLabel}</span>
      {document.fullscreenEnabled && <button type="button" className="theater-fullscreen" onClick={event => {
        if (document.fullscreenElement) void document.exitFullscreen().catch(() => undefined);
        else void event.currentTarget.closest(".replay-theater")?.requestFullscreen().catch(() => undefined);
      }}>{fullscreen ? "Exit fullscreen" : "Fullscreen"}</button>}
    </div>
  </section>;
}
