# Main TeleOp post-run analysis

From the repository root, point the Python script at a replay session folder, its
`raw` folder, or a single `.ftclog` file. Use the full session folder when a
recording has multiple stream files. A working Python environment needs
`numpy` and `protobuf`. Install `matplotlib` to enable the optional PNGs.

For an upload-only replay, copy the `raw` folder and
`recording-metadata.json` if present from the matching
`/srv/ftc-recordings/<session-id>/` directory using the configured SFTP
account. The analysis only needs those telemetry files.

```powershell
python -m pip install numpy protobuf matplotlib
python -m recording_viewer.mainteleop_analysis "path/to/session" --out "output/this-run" --plots
```

Keep the raw recording and output folder together. Reuse the same analysis
settings for comparisons. To change a threshold, pass `--settings path/to/settings.json`
with a JSON object such as `{"low_voltage_v": 11.0, "drop_rpm": 200}`.
`summary.json` includes the effective settings and source session ID.

| Output | Post-run check |
| --- | --- |
| `summary.json` | Sample cadence, missing sequences, channel coverage, clean close, motor current and peak timing, voltage, activity combinations, shooter duty and tracking by RPM target, and powered encoder flatlines |
| `shooter_transitions.csv` | Cold starts, coasting restarts and target steps. Review command ramp time, first sample at 95% of target, and time to hold the final speed band |
| `shooter_disturbances.csv` | Stable-command RPM drops, gate state, trough and drop percentage, voltage/current/output around each dip, and return to the pre-dip speed or selected target |
| `gate_windows.csv` | Gate-open windows and their positive-command overlap, steady-command overlap, and detected dips |
| `intake_load_episodes.csv` | Sustained high current with a slow encoder at forward power, with peak current and voltage for each event |
| `low_voltage_episodes.csv` | Time, duration, voltage floor, concurrent six-motor current and drivetrain activity |
| `overview.png`, `shooter_dips.png`, `electrical_events.png` | Run overview and close-ups of representative events when the required signals are available |

## Measurement definitions

- Time starts at the first robot snapshot, matching the plotted timeline.
  Sample intervals come from acquisition timestamps. Ties, skipped sequence
  numbers and intervals over 200 ms break the time series by default. Duration
  and state fractions use valid intervals. Continuous current totals use
  trapezoidal integration with valid endpoints. Reported distribution
  percentiles describe the recorded samples.
- The current sum covers the six registered motor ports when all six readings
  are present. Check the motor-port amp-seconds or RMS current for sustained
  exposure and the peak for transients. Pack-side current requires an
  independent pack-side measurement. Servos, hubs and other loads are outside
  the sum.
- Spin-up timing starts on the first logged positive shooter reference at a
  given target. A start with measured speed at most 150 RPM is cold. Timing
  for a fixed target ends when the reference turns off or the target changes.
  Readiness requires the reference to finish ramping and measured speed to
  stay within ±100 RPM for at least 100 ms. A blank timing field means that
  crossing or hold was unobserved within the segment. Peak and overshoot
  describe the whole target segment. A downward setpoint step can inherit
  speed above its new target.
- The dip detector uses a roughly 450 ms pre-dip median with at most 150 RPM
  of spread. The selected target must stay constant and the ramped reference
  must be within 1 RPM of it. The default dip trigger is 150 RPM below that
  median. Recovery starts at the first sample in a sustained ±100 RPM band
  for at least 100 ms, searched for up to two seconds from detection.
  Baseline recovery measures return to the speed the robot had before the
  dip. Target-band recovery measures return to the selected speed. Close
  loads can appear as one episode before baseline recovery, and sampling can
  miss short troughs. An episode does not establish how many pieces exited.
- On this BIOBUZZ configuration `fos.dutyCycle` is a gate command proxy.
  Red at 0.279 means commanded open, green at 0.500 means commanded closed.
  Coverage and unknown states are reported. Use the corresponding gate
  settings when a different robot uses different duty values.
- Intake load flags require forward applied power of at least 0.5, current of
  at least 6 A, and speed at most 800 ticks/s, past the first 250 ms of the
  forward command. At least 120 ms must elapse between flagged samples.
  Treat this as a review threshold for this robot. Use the timeline to check
  whether the motor was obstructed, heavily loaded or still starting.
- A 10.5 V review threshold highlights sag episodes. Logged minimums describe
  sampled bus voltage, and the threshold is an operating comparison for this
  analysis. Current demand, loaded voltage and idle voltage are more
  informative together than a single raw minimum.

## Repeatable run review

1. Check the run closed cleanly, sample timing is adequate for the question,
   all expected channels have coverage, and all powered encoders respond.
2. Check time from cold and coasting starts to stable speed. Review sustained
   RPM error and full-duty time by target with and without feeding.
3. Compare median and worst RPM drop, time to recover, attempts that never
   reached target, and the spacing of feed/load events. Look at their traces
   before changing feed cadence.
4. Check the deepest and longest voltage sags, the active mechanisms at each
   sag, and the peak and integrated current of each motor. Review the intake
   near-stall list before comparing motors only by mean current.
5. Compare equivalent test conditions across runs. Record pack identity and
   starting voltage, controller gains and build, hood position, target RPM
   and intended test sequence. For direct shot counts and feed timing, log
   the feeder/flower command and a piece-passage sensor on the robot clock.
