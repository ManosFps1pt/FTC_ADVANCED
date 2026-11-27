import {
  type CSSProperties, type KeyboardEvent, type PointerEvent,
  useEffect, useEffectEvent, useLayoutEffect, useRef, useState,
} from "react";
import "./replay-markers.css";

type ReplayMarker = {
  id: string;
  t_s: number;
  end_s?: number | null;
  title: string;
  note: string;
  category: string;
  color: string;
};

export function useReplayMarkers(recordingId: string | null) {
  const [result, setResult] = useState<{ recordingId: string; markers: ReplayMarker[] }>();
  useEffect(() => {
    if (!recordingId) return;
    const endpoint = `api/recordings/${encodeURIComponent(recordingId)}/markers`;
    const controller = new AbortController();
    let timer: number;
    let previous: string | undefined;
    const refresh = async () => {
      try {
        const response = await fetch(endpoint, { signal: controller.signal });
        if (!response.ok) return;
        const body = await response.text();
        if (!controller.signal.aborted && body !== previous) {
          setResult({ recordingId, markers: JSON.parse(body) as ReplayMarker[] });
          previous = body;
        }
      } catch {
        // Keep the last successful marker list during a transient request failure.
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(refresh, 8000);
      }
    };
    void refresh();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [recordingId]);
  return result?.recordingId === recordingId ? result.markers : [];
}

type MarkerProps = {
  markers: ReplayMarker[];
  jumpTo: (seconds: number) => void;
  formatTime: (seconds: number) => string;
};

const markerStyle = (marker: ReplayMarker) => ({ "--marker-color": marker.color } as CSSProperties);

function rangeDuration(start: number, end: number) {
  const seconds = end - start;
  return seconds < 1 ? `${Math.max(1, Math.round(seconds * 1000))} ms` : `${Number(seconds.toFixed(3))} s`;
}

function markerTime(marker: ReplayMarker, formatTime: MarkerProps["formatTime"]) {
  const start = formatTime(marker.t_s);
  return marker.end_s == null ? start
    : `${start}–${formatTime(marker.end_s)} (${rangeDuration(marker.t_s, marker.end_s)})`;
}

export function MarkerRail({ markers, start = 0, end, separateLanes = false, jumpTo, formatTime }: MarkerProps & {
  start?: number;
  end: number;
  separateLanes?: boolean;
}) {
  const visible = markers.filter(marker => marker.t_s <= end && (marker.end_s ?? marker.t_s) >= start);
  if (!visible.length) return null;
  const percent = (seconds: number) => end > start ? (seconds - start) / (end - start) * 100 : 0;
  const useTwoLanes = separateLanes || (
    visible.some(marker => marker.end_s == null) && visible.some(marker => marker.end_s != null)
  );
  return <div className={useTwoLanes ? "marker-rail marker-rail-two-lanes" : "marker-rail"}
    role="group" aria-label="Analysis markers on the timeline">
    {visible.map(marker => {
      const label = `${markerTime(marker, formatTime)} · ${marker.category} · ${marker.title}`;
      const left = percent(Math.max(start, marker.t_s));
      const width = marker.end_s == null ? undefined : `${percent(Math.min(end, marker.end_s)) - left}%`;
      return <button type="button" key={marker.id} className={width === undefined ? "marker-tick" : "marker-range"}
        style={{ ...markerStyle(marker), left: `${left}%`, width }} title={label} aria-label={label}
        onClick={() => jumpTo(marker.t_s)} />;
    })}
  </div>;
}

const MIN_TIMELINE_SCALE = 12;
const MAX_TIMELINE_SCALE = 1500;

function rulerInterval(scale: number) {
  const target = 94 / scale;
  const magnitude = 10 ** Math.floor(Math.log10(target));
  const step = [1, 2, 5, 10].find(factor => factor * magnitude >= target) ?? 10;
  return step * magnitude;
}

export function ReplayScrubber({ position, duration, ...markerProps }: MarkerProps & {
  position: number;
  duration: number;
}) {
  const { formatTime, jumpTo, markers } = markerProps;
  const viewport = useRef<HTMLDivElement>(null);
  const ruler = useRef<HTMLDivElement>(null);
  const cursor = useRef(position);
  const drag = useRef<{ pointerId: number; lastX: number; moved: boolean }>(null);
  const [scale, setScale] = useState(100);
  const [width, setWidth] = useState(0);
  const [dragging, setDragging] = useState(false);

  useLayoutEffect(() => {
    cursor.current = position;
  }, [position]);

  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const resize = () => setWidth(element.clientWidth);
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    resize();
    return () => observer.disconnect();
  }, []);

  const seek = (seconds: number) => {
    const next = Math.min(duration, Math.max(0, seconds));
    if (next === cursor.current) return false;
    cursor.current = next;
    jumpTo(next);
    return true;
  };

  const onWheel = useEffectEvent((event: WheelEvent) => {
    if (event.ctrlKey) return;
    const mode = event.deltaMode;
    const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
    const pixels = delta * (mode === 1 ? 16 : mode === 2 ? width : 1);
    if (seek(cursor.current + pixels / scale)) event.preventDefault();
  });

  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const listener = (event: WheelEvent) => onWheel(event);
    element.addEventListener("wheel", listener, { passive: false });
    return () => element.removeEventListener("wheel", listener);
  }, []);

  const startDrag = (event: PointerEvent<HTMLDivElement>) => {
    if (duration === 0 || !event.isPrimary || event.button !== 0 || (event.target as Element).closest("button")) return;
    ruler.current?.focus({ preventScroll: true });
    drag.current = { pointerId: event.pointerId, lastX: event.clientX, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const moveDrag = (event: PointerEvent<HTMLDivElement>) => {
    const gesture = drag.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    const delta = event.clientX - gesture.lastX;
    if (!gesture.moved) {
      if (Math.abs(delta) < 4) return;
      gesture.moved = true;
      setDragging(true);
    }
    gesture.lastX = event.clientX;
    seek(cursor.current - delta / scale);
  };

  const endDrag = (event: PointerEvent<HTMLDivElement>) => {
    const gesture = drag.current;
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    drag.current = null;
    setDragging(false);
    if (!gesture.moved && event.type === "pointerup") {
      const bounds = event.currentTarget.getBoundingClientRect();
      seek(cursor.current + (event.clientX - bounds.left - bounds.width / 2) / scale);
    }
  };

  const handleKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    let next: number;
    switch (event.key) {
      case "ArrowLeft":
      case "ArrowDown": next = cursor.current - 10 / scale; break;
      case "ArrowRight":
      case "ArrowUp": next = cursor.current + 10 / scale; break;
      case "PageUp": next = cursor.current - width / scale; break;
      case "PageDown": next = cursor.current + width / scale; break;
      case "Home": next = 0; break;
      case "End": next = duration; break;
      default: return;
    }
    event.preventDefault();
    seek(next);
  };

  const halfSpan = Math.max(1, width) / (2 * scale);
  const start = position - halfSpan;
  const end = position + halfSpan;
  const percent = (seconds: number) => (seconds - start) / (end - start) * 100;
  const firstVisible = Math.max(0, start);
  const lastVisible = Math.min(duration, end);
  const majorInterval = rulerInterval(scale);
  const minorInterval = majorInterval / 5;
  const firstTick = Math.ceil(firstVisible / minorInterval);
  const lastTick = Math.floor(lastVisible / minorInterval);
  const ticks = Array.from({ length: Math.max(0, lastTick - firstTick + 1) }, (_, index) => firstTick + index);
  const activeRange = markers.find(marker => marker.end_s != null && marker.t_s <= position && position <= marker.end_s);

  return <section className="replay-scrubber" aria-label="Timeline scrubber">
    <header>
      <h2>Timeline scrubber</h2>
      <span className="scrubber-time">{formatTime(position)}</span>
      {activeRange?.end_s != null && <span className="scrubber-active-marker" style={markerStyle(activeRange)}>
        {activeRange.title} · {rangeDuration(activeRange.t_s, activeRange.end_s)}
      </span>}
      <div className="scrubber-tools">
        <small>Scroll or drag to scrub</small>
        <button type="button" aria-label="Zoom out" disabled={duration === 0 || scale <= MIN_TIMELINE_SCALE}
          onClick={() => setScale(Math.max(MIN_TIMELINE_SCALE, scale / 2))}>−</button>
        <button type="button" aria-label="Zoom in" disabled={duration === 0 || scale >= MAX_TIMELINE_SCALE}
          onClick={() => setScale(Math.min(MAX_TIMELINE_SCALE, scale * 2))}>+</button>
      </div>
    </header>
    <div ref={viewport} className={dragging ? "scrubber-viewport is-dragging" : "scrubber-viewport"}
      onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={endDrag}
      onPointerCancel={endDrag} onLostPointerCapture={endDrag}>
      <div className="scrubber-recorded" aria-hidden="true"
        style={{ left: `${percent(firstVisible)}%`, right: `${100 - percent(lastVisible)}%` }} />
      <div ref={ruler} className="scrubber-ruler" role="slider" tabIndex={duration > 0 ? 0 : -1}
        aria-label="Scrub replay position" aria-valuemin={0} aria-valuemax={duration} aria-valuenow={position}
        aria-valuetext={formatTime(position)} aria-disabled={duration === 0} onKeyDown={handleKeys}>
        {ticks.map(tick => {
          const time = tick * minorInterval;
          const major = tick % 5 === 0;
          return <span key={tick} aria-hidden="true" style={{ left: `${percent(time)}%` }}
            className={major ? "scrubber-tick scrubber-tick-major" : "scrubber-tick"}>
            {major && formatTime(time)}
          </span>;
        })}
      </div>
      <MarkerRail {...markerProps} start={start} end={end} separateLanes />
      <span className="scrubber-playhead" aria-hidden="true" />
    </div>
  </section>;
}

export function MarkerList({ markers, jumpTo, formatTime }: MarkerProps) {
  if (!markers.length) return null;
  return <section className="analysis-markers panel" aria-labelledby="analysis-markers-heading">
    <h2 id="analysis-markers-heading">Analysis markers</h2>
    <ul>
      {markers.map(marker => <li key={marker.id}>
        <button type="button" onClick={() => jumpTo(marker.t_s)} style={markerStyle(marker)}>
          <span className="marker-time">{markerTime(marker, formatTime)}</span>
          <span className="marker-description">
            <span className="marker-category">{marker.category}</span>
            <strong>{marker.title}</strong>
            {marker.note && <small>{marker.note}</small>}
          </span>
        </button>
      </li>)}
    </ul>
  </section>;
}
