const opModeLabels = { teleop: "TeleOp", auto: "Auto", debugging: "Debugging" } as const;
type OpModeType = keyof typeof opModeLabels;

export type RecordingDetails = { battery_index: number; robot_version: number; opmode_type: OpModeType };
export type RecordingMetadata = { [Key in keyof RecordingDetails]?: RecordingDetails[Key] | null } & {
  title?: string; description?: string;
};
type DetailsDraft = { battery: string; version: string; opMode: OpModeType | "" };

export function recordingDetailsDraft(metadata: RecordingMetadata | null | undefined, opMode: DetailsDraft["opMode"] = ""): DetailsDraft {
  return {
    battery: metadata?.battery_index?.toString() ?? "",
    version: metadata?.robot_version?.toString() ?? "1",
    opMode: metadata?.opmode_type ?? opMode,
  };
}

export function parseRecordingDetails(draft: DetailsDraft): RecordingDetails | null {
  const battery = Number(draft.battery);
  const version = Number(draft.version);
  if (!draft.battery || !Number.isInteger(battery) || battery < 0 || battery > 8 ||
      !Number.isSafeInteger(version) || version < 1 || !draft.opMode) return null;
  return { battery_index: battery, robot_version: version, opmode_type: draft.opMode };
}

export function RecordingDetailsFields({ value, onChange, disabled }: {
  value: DetailsDraft; onChange: (value: DetailsDraft) => void; disabled?: boolean;
}) {
  return <fieldset className="recording-details-fields" disabled={disabled} aria-label="Recording details">
    <label>Battery index
      <select value={value.battery} required onChange={event => onChange({ ...value, battery: event.target.value })}>
        <option value="">Select battery</option>
        {Array.from({ length: 9 }, (_, battery) => <option key={battery} value={battery}>{battery}</option>)}
      </select>
    </label>
    <label>Robot version
      <input type="number" min="1" step="1" value={value.version} required
        onChange={event => onChange({ ...value, version: event.target.value })} />
    </label>
    <label>OpMode type
      <select value={value.opMode} required onChange={event => onChange({ ...value, opMode: event.target.value as DetailsDraft["opMode"] })}>
        <option value="">Select type</option>
        {Object.entries(opModeLabels).map(([type, label]) => <option key={type} value={type}>{label}</option>)}
      </select>
    </label>
  </fieldset>;
}

export function RecordingDetailsSummary({ metadata }: { metadata?: RecordingMetadata | null }) {
  const battery = metadata?.battery_index ?? "Unknown";
  const version = metadata?.robot_version == null ? "Unknown" : `v${metadata.robot_version}`;
  const opMode = metadata?.opmode_type ? opModeLabels[metadata.opmode_type] : "Unknown";
  return <span className="recording-details-summary">Battery {battery} · Robot {version} · OpMode type {opMode}</span>;
}
