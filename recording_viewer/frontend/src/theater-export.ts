import { BufferTarget, Conversion, Input, MP4, Mp4OutputFormat, Output, Quality, UrlSource } from "mediabunny";
import { drawReplayOverlays, EXPORT_HEIGHT, EXPORT_WIDTH, type ReplayExportFrame } from "./theater-renderer";

export type TheaterExportOptions = {
  url: string;
  delay: number;
  duration: number;
  title: string;
  frameAt: (seconds: number) => ReplayExportFrame;
};

export async function exportTheater(options: TheaterExportOptions, signal: AbortSignal, onProgress: (percent: number) => void) {
  signal.throwIfAborted();
  if (typeof VideoEncoder === "undefined" || typeof VideoDecoder === "undefined") {
    throw new Error("This browser cannot encode MP4 video. Open this replay in Chrome or Edge.");
  }
  const { url, delay, duration, title, frameAt } = options;
  const input = new Input({
    formats: [MP4],
    source: new UrlSource(new URL(url, window.location.href), {
      maxCacheSize: 24 * 1024 * 1024,
      getRetryDelay: attempts => attempts < 2 ? 1 : null,
    }),
  });
  let conversion: Conversion | undefined;
  const cancel = () => {
    void conversion?.cancel().catch(() => undefined);
    input.dispose();
  };
  signal.addEventListener("abort", cancel, { once: true });

  try {
    const [track, audioTrack] = await Promise.all([input.getPrimaryVideoTrack(), input.getPrimaryAudioTrack()]);
    if (!track) throw new Error("The selected clip has no video track.");
    const start = Math.max(await track.getFirstTimestamp(), delay, 0);
    const end = Math.min(await track.computeDuration(), duration + delay);
    if (end <= start) throw new Error("The clip has no video on the telemetry timeline.");
    const audioStart = await audioTrack?.getFirstTimestampOrNull();
    const includeAudio = audioTrack !== null && audioStart != null && audioStart < end
      && await audioTrack.computeDuration() > start;

    const canvas = document.createElement("canvas");
    canvas.width = EXPORT_WIDTH;
    canvas.height = EXPORT_HEIGHT;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) throw new Error("Could not create the video export canvas.");
    const target = new BufferTarget();
    const output = new Output({ format: new Mp4OutputFormat(), target });
    conversion = await Conversion.init({
      input,
      output,
      tracks: "primary",
      trim: { start, end },
      audio: { discard: !includeAudio },
      copy: { boundaryPolicy: "shrink", boundaryTolerance: 0, shiftTolerance: 0 },
      video: {
        codec: "avc",
        width: EXPORT_WIDTH,
        height: EXPORT_HEIGHT,
        fit: "contain",
        quality: new Quality({ bitrate: 6_000_000 }),
        process(sample) {
          ctx.fillStyle = "#04070a";
          ctx.fillRect(0, 0, EXPORT_WIDTH, EXPORT_HEIGHT);
          sample.drawWithFit(ctx, { fit: "contain" });
          // Conversion rebases video samples to the start of the trimmed clip.
          drawReplayOverlays(ctx, title, frameAt(sample.timestamp + start - delay));
          return canvas;
        },
      },
    });
    signal.throwIfAborted();
    if (!conversion.isValid || !conversion.utilizedTracks.includes(track)) {
      throw new Error("This browser could not decode the clip or encode 1080p H.264.");
    }
    if (includeAudio && audioTrack && !conversion.utilizedTracks.includes(audioTrack)) {
      throw new Error("This browser could not include the original audio. Try exporting in Chrome or Edge.");
    }
    conversion.onProgress = progress => onProgress(Math.min(99, Math.floor(progress * 100)));
    await conversion.execute();
    signal.throwIfAborted();
    if (!target.buffer?.byteLength) throw new Error("The browser did not produce an MP4.");
    return new Blob([target.buffer], { type: "video/mp4" });
  } finally {
    signal.removeEventListener("abort", cancel);
    input.dispose();
  }
}
