export const EXPORT_WIDTH = 1920;
export const EXPORT_HEIGHT = 1080;

export type ReplayExportFrame = {
  timeLabel: string;
  lights: { label: string; color: string; state: string }[];
  motors: { name: string; values: string[]; currentBackground?: string }[];
  graphs: { label: string; value: string; color: string; points: [number, number | null][] }[];
  gamepads: { label: string; values: Record<string, boolean | number> | null }[];
  position: { x: number; y: number; headingRad: number } | null;
};

const INK = "#e6edf7";
const MUTED = "#a1bbd0";
const PANEL = "rgba(9,19,31,.88)";
const BORDER = "#38536b";
const LEFT = 30, RIGHT = 1506, TOP = 77, BOTTOM = 1037;
const LEFT_WIDTH = 320, RIGHT_WIDTH = 384;
type Context = CanvasRenderingContext2D;

function panel(ctx: Context, x: number, y: number, width: number, height: number) {
  ctx.beginPath();
  ctx.roundRect(x, y, width, height, 10);
  ctx.fillStyle = PANEL;
  ctx.fill();
  ctx.strokeStyle = BORDER;
  ctx.lineWidth = 1;
  ctx.stroke();
}

function label(ctx: Context, value: string, x: number, y: number, options: {
  size?: number; color?: string; align?: CanvasTextAlign; bold?: boolean; mono?: boolean; width?: number;
} = {}) {
  const { size = 14, color = INK, align = "left", bold = false, mono = false, width } = options;
  ctx.font = `${bold ? 700 : 400} ${size}px ${mono ? "monospace" : "system-ui, sans-serif"}`;
  ctx.fillStyle = color;
  ctx.textAlign = align;
  ctx.textBaseline = "middle";
  if (width === undefined) ctx.fillText(value, x, y);
  else ctx.fillText(value, x, y, width);
}

function line(ctx: Context, x1: number, y1: number, x2: number, y2: number, color = BORDER) {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.stroke();
}

function dot(ctx: Context, x: number, y: number, radius: number, color: string) {
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

function drawLights(ctx: Context, lights: ReplayExportFrame["lights"]) {
  if (!lights.length) return;
  const visible = lights.slice(0, 8);
  panel(ctx, LEFT, TOP, 220, 31 + visible.length * 35);
  label(ctx, "RGB lights", LEFT + 10, TOP + 17, { bold: true });
  visible.forEach((light, index) => {
    const y = TOP + 39 + index * 35;
    dot(ctx, LEFT + 21, y, 9, "#b8cce0");
    dot(ctx, LEFT + 21, y, 7.5, light.color);
    label(ctx, light.label, LEFT + 37, y - 4, { size: 12, bold: true, width: 171 });
    label(ctx, light.state, LEFT + 37, y + 9, { size: 10, color: MUTED });
  });
}

function drawMotors(ctx: Context, motors: ReplayExportFrame["motors"]) {
  if (!motors.length) return;
  const visible = motors.slice(0, 22);
  const y = TOP;
  const columns = [RIGHT + 11, RIGHT + 169, RIGHT + 226, RIGHT + 283, RIGHT + 347];
  panel(ctx, RIGHT, y, RIGHT_WIDTH, 53 + visible.length * 29);
  label(ctx, "Motors", columns[0], y + 17, { bold: true });
  if (motors.length > visible.length) label(ctx, `${visible.length} of ${motors.length}`, RIGHT + RIGHT_WIDTH - 10, y + 17, { align: "right", color: MUTED, size: 11 });
  ["Motor", "Cmd", "Vel", "A", "W"].forEach((name, index) =>
    label(ctx, name, columns[index], y + 39, { align: index ? "center" : "left", color: MUTED, size: 11 }));
  visible.forEach((motor, index) => {
    const row = y + 47 + index * 29;
    line(ctx, RIGHT + 10, row, RIGHT + RIGHT_WIDTH - 10, row);
    if (motor.currentBackground) {
      ctx.fillStyle = motor.currentBackground;
      ctx.fillRect(columns[3] - 27, row + 1, 54, 28);
    }
    label(ctx, motor.name, columns[0], row + 15, { size: 12, bold: true, width: 127 });
    motor.values.forEach((value, column) =>
      label(ctx, value, columns[column + 1], row + 15, { size: 12, align: "center", mono: true, width: 52 }));
  });
}

function drawGraphs(ctx: Context, graphs: ReplayExportFrame["graphs"]) {
  const height = 91;
  graphs.forEach((graph, index) => {
    const y = BOTTOM - (graphs.length - index) * (height + 5) + 5;
    panel(ctx, LEFT, y, LEFT_WIDTH, height);
    label(ctx, graph.label, LEFT + 8, y + 13, { bold: true, size: 12, width: 207 });
    label(ctx, graph.value, LEFT + LEFT_WIDTH - 8, y + 13, { mono: true, size: 11, align: "right", color: "#a6eed1", width: 90 });
    const x1 = LEFT + 45, x2 = LEFT + LEFT_WIDTH - 12, middle = (x1 + x2) / 2;
    const top = y + 26, bottom = y + 72;
    for (const gridY of [top, (top + bottom) / 2, bottom]) line(ctx, x1, gridY, x2, gridY);
    ctx.setLineDash([3, 3]);
    line(ctx, middle, top, middle, bottom + 1, "#d3e5f4");
    ctx.setLineDash([]);
    label(ctx, "−5 s", x1, y + 82, { mono: true, size: 9, color: MUTED });
    label(ctx, "Now", middle, y + 82, { mono: true, size: 9, color: MUTED, align: "center" });
    label(ctx, "+5 s", x2, y + 82, { mono: true, size: 9, color: MUTED, align: "right" });
    let min = Infinity, max = -Infinity;
    for (const [, value] of graph.points) {
      if (value === null) continue;
      min = Math.min(min, value);
      max = Math.max(max, value);
    }
    if (!Number.isFinite(min)) return;
    const padding = min === max ? Math.max(Math.abs(min) * .08, 1) : (max - min) * .08;
    min -= padding;
    max += padding;
    label(ctx, max.toPrecision(3), x1 - 4, top, { mono: true, size: 9, width: 31, align: "right", color: MUTED });
    label(ctx, min.toPrecision(3), x1 - 4, bottom, { mono: true, size: 9, width: 31, align: "right", color: MUTED });
    ctx.beginPath();
    let connected = false;
    for (const [seconds, value] of graph.points) {
      if (value === null) { connected = false; continue; }
      const x = middle + seconds * (x2 - x1) / 10;
      const pointY = bottom - (value - min) / (max - min) * (bottom - top);
      if (connected) ctx.lineTo(x, pointY);
      else ctx.moveTo(x, pointY);
      connected = true;
    }
    ctx.strokeStyle = graph.color;
    ctx.lineWidth = 1.6;
    ctx.stroke();
  });
}

function drawGamepads(ctx: Context, gamepads: ReplayExportFrame["gamepads"]) {
  const width = 187;
  const x = RIGHT + RIGHT_WIDTH - width;
  const height = 83;
  gamepads.forEach(({ label: name, values }, index) => {
    const y = BOTTOM - (gamepads.length - index) * (height + 6) + 6;
    panel(ctx, x, y, width, height);
    label(ctx, name, x + 9, y + 14, { bold: true, size: 12 });
    if (!values) return;
    const centerY = y + 41;
    ["left", "right"].forEach((stick, i) => {
      const centerX = x + 24 + 49 * i;
      dot(ctx, centerX, centerY, 15, "#416887");
      dot(ctx, centerX, centerY, 14, "#0a192b");
      dot(ctx, centerX + Number(values[`${stick}StickX`] ?? 0) * 9,
        centerY + Number(values[`${stick}StickY`] ?? 0) * 9, 4, "#77cfff");
    });
    const keys = ["y", "x", "b", "a"];
    keys.forEach((key, i) => {
      const keyX = x + 128 + (i % 2) * 20, keyY = y + 25 + Math.floor(i / 2) * 16;
      ctx.fillStyle = values[key] ? "#85d5ff" : "#203b54";
      ctx.fillRect(keyX, keyY, 17, 13);
      label(ctx, key.toUpperCase(), keyX + 8.5, keyY + 7, { size: 10, align: "center", color: values[key] ? "#061522" : MUTED });
    });
    const triggers = ["LB", `LT ${Number(values.leftTrigger ?? 0).toFixed(2)}`,
      `RT ${Number(values.rightTrigger ?? 0).toFixed(2)}`, "RB"];
    triggers.forEach((value, i) => {
      const x1 = x + 8 + 43 * i;
      const active = (i === 0 && values.leftBumper) || (i === 3 && values.rightBumper);
      ctx.fillStyle = active ? "#85d5ff" : "#203b54";
      ctx.fillRect(x1, y + 63, 39, 13);
      label(ctx, value, x1 + 19.5, y + 70, { size: 9, align: "center", color: active ? "#061522" : MUTED, mono: true });
    });
  });
}

function drawPosition(ctx: Context, position: NonNullable<ReplayExportFrame["position"]>) {
  const width = 187, height = 176, size = 126, x = RIGHT, y = BOTTOM - height;
  const left = x + (width - size) / 2, top = y + 27;
  panel(ctx, x, y, width, height);
  label(ctx, "Position map", x + 9, y + 15, { bold: true, size: 12 });
  ctx.fillStyle = "#08192c";
  ctx.fillRect(left, top, size, size);
  for (let index = 0; index <= 6; index++) {
    const step = index * size / 6;
    line(ctx, left + step, top, left + step, top + size);
    line(ctx, left, top + step, left + size, top + step);
  }
  ctx.save();
  ctx.beginPath();
  ctx.rect(left, top, size, size);
  ctx.clip();
  ctx.translate(left + position.x / 144 * size, top + size - position.y / 144 * size);
  ctx.rotate(-position.headingRad);
  ctx.fillStyle = "#4bb8ff";
  ctx.fillRect(-8, -8, 16, 16);
  dot(ctx, 4, 0, 2.5, "#ffcc64");
  ctx.restore();
  label(ctx, `X ${position.x.toFixed(1)} · Y ${position.y.toFixed(1)} · ${Math.round(position.headingRad * 180 / Math.PI)}°`,
    x + width / 2, y + 165, { size: 11, align: "center", mono: true });
}

export function drawReplayOverlays(ctx: Context, title: string, frame: ReplayExportFrame) {
  const shade = ctx.createLinearGradient(0, 0, 0, 66);
  shade.addColorStop(0, "rgba(4,8,15,.94)");
  shade.addColorStop(1, "rgba(4,8,15,0)");
  ctx.fillStyle = shade;
  ctx.fillRect(0, 0, EXPORT_WIDTH, 66);
  label(ctx, title, LEFT, 34, { bold: true, size: 24, width: EXPORT_WIDTH - LEFT * 2 });
  drawLights(ctx, frame.lights);
  drawMotors(ctx, frame.motors);
  drawGraphs(ctx, frame.graphs);
  drawGamepads(ctx, frame.gamepads);
  if (frame.position) drawPosition(ctx, frame.position);
  panel(ctx, LEFT, 1044, 166, 27);
  label(ctx, frame.timeLabel, LEFT + 9, 1058, { size: 12, mono: true });
}
