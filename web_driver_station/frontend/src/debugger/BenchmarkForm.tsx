import { useState, type FormEvent } from "react";
import type { Benchmark } from "./Report";

export type InputValue = string | number | boolean;
export type InputDefinition = {
  id: string; label: string; description?: string; valueType: string; required?: boolean; unit?: string;
  minFloat64?: number; maxFloat64?: number;
  enumOptions?: { id: string; label: string }[];
  defaultValue?: { float64Value?: number; int64Value?: string | number; enumValue?: string; booleanValue?: boolean; stringValue?: string };
};

function definitions(benchmark: Benchmark): InputDefinition[] {
  const legacy: Record<string, InputDefinition["defaultValue"]> = {
    repetitions: { int64Value: 3 }, max_voltage: { float64Value: 12 }, reverse: { booleanValue: false },
  };
  return (benchmark.inputs ?? []).filter(d => d.id !== "reverse" || benchmark.reverseAllowed).map(d => ({
    ...d, defaultValue: d.defaultValue ?? (benchmark.id === "motor.friction.v1" ? legacy[d.id] : undefined),
  }));
}

export function BenchmarkForm({ benchmark, disabled, onRun }: {
  benchmark: Benchmark; disabled: boolean; onRun: (inputs: Record<string, InputValue>) => void;
}) {
  const fields = definitions(benchmark);
  const [values, setValues] = useState<Record<string, InputValue>>(() => Object.fromEntries(fields.map(d => {
    const v = d.defaultValue;
    const value = v?.float64Value ?? (v?.int64Value !== undefined ? Number(v.int64Value) : undefined)
      ?? v?.enumValue ?? v?.booleanValue ?? v?.stringValue ?? (d.valueType === "BOOLEAN" ? false : "");
    return [d.id, value];
  })));
  const control = (d: InputDefinition) => {
    const kind = d.valueType.toLowerCase(), value = values[d.id] ?? "";
    return <label key={d.id} title={d.description}>{d.label}{d.unit && ` (${d.unit})`}
      {kind === "enum" ? <select required={d.required} value={String(value)} disabled={disabled}
        onChange={e => setValues(v => ({ ...v, [d.id]: e.target.value }))}>
        <option value="">Select…</option>{d.enumOptions?.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select> : kind === "boolean" ? <input type="checkbox" checked={Boolean(value)} disabled={disabled}
        onChange={e => setValues(v => ({ ...v, [d.id]: e.target.checked }))} /> :
        <input type={kind === "string" ? "text" : "number"} value={typeof value === "boolean" ? "" : value}
          required={d.required} disabled={disabled} min={d.minFloat64 ?? 0} max={d.maxFloat64}
          step={kind === "int64" ? 1 : "any"}
          onChange={e => setValues(v => ({ ...v, [d.id]: kind === "string" || e.target.value === "" ? e.target.value : Number(e.target.value) }))} />}
    </label>;
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onRun(Object.fromEntries(Object.entries(values).filter(([, v]) => v !== "")));
  };
  const primary = fields.filter(d => d.required || !d.defaultValue);
  const advanced = fields.filter(d => !primary.includes(d));
  return <form onSubmit={submit}><h3>{benchmark.label}</h3><p>{benchmark.description}</p>
    <div className="dbg-inputs">{primary.map(control)}</div>
    {advanced.length > 0 && <details><summary>Run settings and advanced measurement options</summary>
      <div className="dbg-inputs">{advanced.map(control)}</div></details>}
    <button className="primary" type="submit" disabled={disabled}>Run benchmark</button>
  </form>;
}
