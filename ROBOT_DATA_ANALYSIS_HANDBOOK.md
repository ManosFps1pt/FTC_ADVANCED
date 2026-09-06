# FTC robot data analysis handbook

Research and repository audit: 5 September 2026.

This document specifies an analysis layer that could become tools in the FTC Advanced MCP server. It explains what to calculate, why the calculation answers a useful engineering question, and when the available evidence is too weak to answer. It does **not** implement those tools or authorize robot experiments.

The intended reader knows algorithms, regression, and basic machine learning. Control and electrical concepts are introduced where needed. Every numerical example and JSON result below is **synthetic and illustrative**, not a finding about a recorded robot run. Proposed interfaces, thresholds, and policies are design choices unless explicitly identified as existing behavior.

## Contents

1. [Architecture and current evidence](#1-architecture-and-current-evidence)
2. [Data quality and timing](#2-data-quality-and-timing)
3. [Battery behavior](#3-battery-behavior)
4. [Power and energy](#4-power-and-energy)
5. [Comparing runs fairly](#5-comparing-runs-fairly)
6. [Controller performance](#6-controller-performance)
7. [System identification and PID tuning](#7-system-identification-and-pid-tuning)
8. [Motor and drivetrain diagnostics](#8-motor-and-drivetrain-diagnostics)
9. [Loop and communication performance](#9-loop-and-communication-performance)
10. [Mechanisms and autonomous routines](#10-mechanisms-and-autonomous-routines)
11. [Vision and localization](#11-vision-and-localization)
12. [Anomalies and evidence-backed summaries](#12-anomalies-and-evidence-backed-summaries)
13. [Backend and MCP interfaces](#13-backend-and-mcp-interfaces)
14. [Implementation priorities and acceptance tests](#14-implementation-priorities-and-acceptance-tests)
15. [Sources and further reading](#15-sources-and-further-reading)

## 1. Architecture and current evidence

An LLM should select an analysis, clarify the engineering question, and explain the result. Reproducible numerical code should calculate the metrics. The robot should execute time-critical control locally. A language model's conversation latency is unsuitable as a PID sampling period.

```text
Robot signals + configuration + events
                  |
          protobuf telemetry
                  |
     backend recorder -> durable .ftclog
                              |
                  validated analysis dataset
                              |
                    numerical analysis engine
                              |
                    backend analysis API
                              |
                    bounded MCP tool results
                              |
                    LLM explanation / proposal

Future authorized experiment request
    -> backend command validation -> robot-local experiment executor
    -> new recording -> independent evaluation -> retain / roll back
```

### 1.1 What is actually in this repository

The following findings describe the working tree at the audit date, including existing local changes. They do not claim every OpMode emits every supported signal. Paths in code spans are repository-relative source identifiers.

| Source | Observed behavior | Consequence for analysis |
|---|---|---|
| `mcp_server/server.py` | Six bounded, read-only live status/catalog/snapshot tools | Historical analysis and command execution are future work; the current MCP cannot compare runs or tune a robot. |
| `protocol/robot_data.proto` | Version 2 protobuf envelopes; sessions, connections, schemas, samples, events, gaps, gamepads, and debug messages | Keep both session and connection provenance. A normalized backend object saying version 1 is not evidence that the wire protocol is version 1. |
| `web_driver_station/backend/ftclog.py` | Stores protobuf bytes and laptop monotonic receipt times, with integrity checks and finalized/partial files | Raw recordings are the reproducible source; display traces are derived views. |
| `web_driver_station/backend/protocol_codec.py` | Resolves numeric channels using the active schema | Channel numbers alone are not permanent identities. Decode in stream order with the applicable schema. |
| `web_driver_station/backend/telemetry_store.py` | Catalog units, roles, session information, and bounded live history | The live store is useful for recent status, not a substitute for a complete run. |
| `recording_viewer/backend/main.py` | Reconstructs snapshots, gamepads, events, gaps, debug messages, and incident overlays; keeps one latest catalog | Reuse decoding concepts, but an analysis dataset must preserve schema history. Do not apply the last catalog blindly to earlier samples. |
| `FtcRobotController/TeamCode/src/main/java/org/firstinspires/ftc/teamcode/data/StructuredRobotDataClient.java` | Optional voltage/motor/gamepad bindings; automatic publication-interval signal | Capabilities must be discovered from the selected recording rather than inferred from class availability. |
| `FtcRobotController/TeamCode/src/main/java/org/firstinspires/ftc/teamcode/SelectableOpMode.java` | Debug manifest, parameter/command handling, bounded motor-power tool and watchdog behavior | Useful command infrastructure exists, but a general PID experiment executor and gain adapter are not implemented. |

The Java binding registers `robot.voltage`, and for each bound motor registers `commandedPower`, `appliedPower`, `encoderPosition`, `velocityTicksPerSecond`, `currentAmps`, and `electricalPowerWatts`. It computes:

```text
appliedPower          = motor.getPower()
currentAmps           = motor.getCurrent(AMPS)
electricalPowerWatts  = currentAmps * robotVoltage
```

These names overstate what is independently measured. `getPower()` is a software-level motor power value, not a terminal-voltage probe or direct observation of the bridge duty cycle. The default motor binding uses the same getter for commanded and applied power, so equality between those channels can be tautological. Even the overload with a separate command supplier does not make `getPower()` a physical-output measurement.

The current sensor's location, averaging, bandwidth, and PWM behavior must be established before interpreting voltage times motor current as battery-side or motor-terminal power. Summing registered motors also omits other loads and possibly other motors. Section 4 provides a defensible measurement plan. REV documents accessible battery voltage and per-motor current monitoring, but that availability alone does not establish an energy-accounting boundary. [REV integrated sensors](https://docs.revrobotics.com/duo-control/control-system-overview/integrated-sensors)

Two particularly useful audit findings:

- `TwoMotorDrivetrain.java` currently sets both motors to `RUN_WITHOUT_ENCODER`, although descriptive text/display output suggests encoder speed control. Reading encoders does not imply the motor is running a velocity feedback loop. Prefer runtime configuration readback over either documentation or display labels.
- `opmode.loopTimeMs` is the interval between calls to sample publication, with a zero first sample. It is not directly the duration of the control computation. Signal `sampleHintHz` values are hints, not verified sensor update rates.

The dedicated Limelight OpMode records target validity, `tx`/`ty`, capture-plus-targeting latency, pipeline index, and camera FPS. Invalid target angles and latency use zero placeholders. It does not log a unique camera-frame identity or capture timestamp. Video replay currently treats clips as starting at recording time zero; that assumption is insufficient for calibrated latency or ground-truth comparisons.

### 1.2 Capability and priority vocabulary

| Label | Meaning |
|---|---|
| **Supported** | Necessary signal content exists in an appropriate current producer; the analysis still needs implementation and per-recording checks. |
| **Conditional** | Some required information may exist, but semantics, metadata, excitation, coverage, or calibration must be verified. |
| **Additional instrumentation** | Required observations are absent from the audited standard producers. |
| **P0 / P1 / P2 / P3** | Foundations / useful descriptive analyses / controlled experiments and richer instrumentation / historical or learned extensions. |

Hardware support and telemetry support are different. A hub may expose a sensor through an SDK while the current recording never includes it.

## 2. Data quality and timing

**Question:** Is this recording adequate for this particular calculation? A trace suitable for a run-level voltage summary may be inadequate for a fast controller model.

**Inputs and readiness:** Supported for timestamps, sequences, gaps, schemas, and finite-value checks. Freshness and inter-sensor acquisition skew are conditional without sensor timestamps. Priority **P0**, required by every later section.

### Method

Parse nanosecond timestamps as integers first. Subtract an origin before converting to floating-point seconds:

$$
t_i = (n_i-n_0)10^{-9},\qquad \Delta t_i=t_{i+1}-t_i.
$$

Otherwise large absolute integers can lose precision in a JavaScript number. Encoder values serialized as decimal strings likewise need integer parsing before calibrated conversion. Never convert encoder ticks to meters without the encoder counts convention, gearing, and wheel geometry.

Preserve raw order and diagnose duplicate sequences, conflicting duplicate values, clock resets, and out-of-order records before constructing a sorted view. Split independent clock epochs. A reconnect is not necessarily a clock reset; examine session, connection, and timestamp evidence. Do not silently join separate boots.

Associate every sample with its schema revision. Reject unit/meaning changes within a calculation or split the window. Treat null and nonfinite measurements as unavailable, never zero. A constant sequence of readings might be a stationary robot rather than a frozen sensor; definitive freshness requires update timestamps or sequence counters.

Construct **valid intervals**, not just a mask of valid rows. Both endpoints must be usable, the applicable schemas compatible, and the interval must not cross a known gap or reset. A maximum interpolation interval is analysis-specific. A proposed descriptive default is five times the median positive publication interval, reported in the output; controller identification requires a stricter experiment-specific gate. The default is not proof that an unmarked interval contains no missing samples.

$$
T_{\mathrm{valid}}=\sum_{i\in A}\Delta t_i,\qquad
C_t=T_{\mathrm{valid}}/T_{\mathrm{requested}}.
$$

Report time coverage and sequence coverage separately. A small number of missing samples can span a large fraction of a short event. For multiple signals, use the intersection of their valid intervals.

```python
# Conceptual pseudocode; boundary interpolation is allowed only inside valid edges.
edges = validate_edges(samples, schema_history, gaps, max_interval)
integral = duration = 0.0
for left, right in edges:
    a, b = clip_edge_to_requested_window(left, right)
    if b.time <= a.time:
        continue
    integral += 0.5 * (a.value + b.value) * (b.time - a.time)
    duration += b.time - a.time
mean = integral / duration if duration > 0 else None
# Return excluded intervals as well as the answer.
```

Linear interpolation is a model for continuous measurements. Use a bounded zero-order hold for discrete states or commands known to remain in force. Neither model licenses filling long unknown intervals. Record filters and resampling parameters. Downsampling for a plot must not precede peak detection or energy integration. Numerical integration with supplied sample coordinates is supported by [SciPy's trapezoid routine](https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.trapezoid.html); the validity policy is our additional responsibility.

### Worked example and output

Samples at 0, 0.02, 0.04, and 0.20 seconds contain a marked gap across the final interval. The two accepted intervals cover 0.04 seconds out of 0.20: **20% time coverage**. The last sample remains a valid point observation, but does not make the missing 0.16 seconds observed.

```json
{"status":"partial","validDurationS":0.04,"requestedDurationS":0.20,
 "timeCoverage":0.20,"excludedIntervalsS":[[0.04,0.20]],
 "limitations":["Whole-window average is unavailable; valid-interval average only."]}
```

**Failure modes:** timestamps assigned after sequential sensor reads are not simultaneous acquisitions; laptop and robot monotonic clocks have unrelated origins; full snapshots can contain repeated sensor data; interpolation can invent a smooth recovery across an outage.

**Validation:** use fixtures with duplicates, conflicting duplicates, gaps, clock resets, changing units, nulls, large integers, and irregular intervals. Verify valid duration by hand. An all-invalid or zero-duration window must produce `insufficient_data`, not zero metrics. This is the first acceptance gate, not an optional cleaning step.

## 3. Battery behavior

**Question:** When did supply voltage fall, how large and sustained was the fall, and under what operating conditions? “Sagged the most” must identify whether it ranks dips within one run or comparable runs within an explicit cohort.

**Inputs and readiness:** Voltage minima and threshold exposure are **supported** when `robot.voltage` is present. Sag is **conditional** on a defensible baseline. Resistance and battery-health inference require additional current/metadata verification or instrumentation. Priority **P1** for voltage events, **P2** for controlled electrical characterization.

### Voltage minimum is not voltage sag

Minimum voltage is simply the smallest accepted observation, with its timestamp. Sag is a change relative to a specified reference:

$$
S=V_{\mathrm{baseline}}-V_{\min},\qquad
S_{\%}=100S/V_{\mathrm{baseline}}.
$$

For each load event, choose a nearby pre-event low-load interval and use a robust location estimate such as its median. Record its duration, spread, and load condition. It is a **low-load baseline**, not necessarily open-circuit voltage. Reject baselines that are already dipping or come from a different battery state. If no baseline exists, report the minimum and say sag is unavailable.

Detect events using a voltage-drop entry threshold, a smaller exit threshold, and minimum duration. This hysteresis prevents noise from splitting one dip into many. Thresholds are mechanism/test settings, not universal battery-health cutoffs. Retain the raw minimum alongside a duration-qualified event estimate so filtering cannot hide brief dips.

Useful event measurements are depth, start/end, time below a configured voltage, and voltage-deficit area:

$$
A_V=\int_{\mathrm{event}}\max(0,V_b-V(t))\,dt\quad[\mathrm{V\,s}].
$$

This area describes voltage depression, **not energy**. Recovery time is measured from load release to sustained return within a declared band around the baseline. If the recording ends first, report “recovery not observed,” not a fabricated time.

### What resistance estimates mean

A simplified short-time model is

$$
V_{\mathrm{hub}}(t)=V_{\mathrm{source}}(t)-I_{\mathrm{battery}}(t)R_{\mathrm{path}}-V_{\mathrm{dynamic}}(t).
$$

For a sufficiently fast, well-resolved load change with approximately unchanged source state:

$$
\hat R_{\mathrm{path}}\approx-\Delta V/\Delta I.
$$

At the hub, this can include cells, connectors, switch, and wiring. It is not isolated cell resistance. A multi-event fit `V = a - R I` can improve robustness, but only if current changes independently enough to identify the slope, voltage and current are aligned, and slow state-of-charge/temperature trends are controlled. Report the pulse timescale: immediate and sustained voltage drops characterize different dynamics. Never divide by a current change near measurement noise.

Battery monitoring depends on load and measurement conditions; TI discusses synchronized voltage/current measurement and the broader inputs required for fuel gauging. These are measurement principles, not chemistry-specific calibration for an FTC pack. [TI voltage measurement](https://www.ti.com/document-viewer/lit/html/sszt315), [TI fuel gauging overview](https://www.ti.com/product-category/battery-management-ics/battery-fuel-gauges/overview.html)

### Worked example and output

A stable pre-event baseline is 12.8 V; the accepted dip minimum is 11.6 V. Sag is **1.2 V**, or **9.375%**. In a separately validated pulse measurement, total battery current rises from 2 A to 14 A over the voltage transition: the effective path estimate is `1.2 / 12 = 0.10 ohm`. Without that current measurement, do not report resistance.

```json
{"status":"ok","baselineV":12.8,"minimumV":11.6,"sagV":1.2,
 "sagPercent":9.375,"minimumTimeS":14.20,
 "baselineMethod":"median of stable pre-load window",
 "interpretation":"Largest qualified dip among events in this selected run",
 "cause":"undetermined"}
```

**Failure modes:** the lowest run voltage may occur with a nearly discharged pack at low load; a different starting charge can reverse run rankings; command overlap is association, not proof a particular motor caused the dip; sampling can miss the true transient minimum. Voltage alone cannot establish remaining capacity, state of health, or a bad connector.

**Validation:** synthetic rectangular/triangular dips with noise and known area; no-baseline cases; truncated recovery; small-current-change rejection. For physical validation compare to a calibrated synchronized measurement under controlled loads, battery identity, charge preparation, temperature, and rest time.

## 4. Power and energy

**Question:** How much electrical input did a defined part of the robot consume, and did a change reduce demand or merely change the duration of the task?

**Inputs and readiness:** The existing `electricalPowerWatts` channel is a **conditional proxy**, not established whole-robot power. Credible whole-robot accounting needs verified battery-side current covering all loads, synchronized voltage, and documented sensor behavior. Priority **P1** for honestly labeled proxy summaries, **P2** for calibrated physical accounting.

### Measurement boundary before mathematics

Physical instantaneous electrical power at a boundary is

$$
P(t)=V(t)I(t),\quad E=\int P(t)\,dt,\quad \bar P=E/T,\quad E_{\mathrm{Wh}}=E_{\mathrm{J}}/3600.
$$

A watt is a joule per second. Average watts describe demand over a window; joules describe total energy over it. Average power is not the arithmetic mean of irregularly spaced samples. Average voltage times average current is not generally average power either, because voltage and current can covary.

For synchronized point samples of power with an accepted linear interpolation model:

$$
E\approx\sum_{i\in A}\frac{P_i+P_{i+1}}{2}\Delta t_i.
$$

The result is observed-interval energy when coverage is incomplete, not a full-run total. If physical bounds on missing power are known, report energy bounds separately; otherwise keep the missing contribution unknown. If sensors report interval averages rather than point samples, use their actual acquisition model.

Motor-terminal current can recirculate through an H-bridge during PWM. Consequently, battery current and winding current are not interchangeable. A software power setting need not equal actual PWM duty, particularly under internal feedback or limiting. Multiplying the current channel by either bus voltage or `abs(getPower()) * bus voltage` is not a universally valid correction. The exact energy balance also includes controller losses and other loads.

Verification should establish:

1. Where each current measurement sits electrically, its sign convention, and what its averaging window represents.
2. Which hub, motor, and other loads it covers; avoid summing overlapping total and branch measurements.
3. Bias, scale, timing, and bandwidth against a calibrated instrument over duty cycles and load changes.
4. Whether the resulting metric is battery input, a subsystem estimate, or just a consistent proxy.

Do not infer mechanical efficiency from electrical input alone. Mechanical output requires torque and angular speed (`P_mech = torque * angular_velocity`) or another justified work measurement. Lower energy per successful cycle is a useful task metric even when mechanical efficiency is unmeasured.

### Worked examples and output

Power samples `(time_s, watts) = (0,20), (1,40), (3,40)` give `30*1 + 40*2 = 110 J`; average power is `110/3 = 36.667 W`. The arithmetic sample mean is 33.333 W and is wrong for the chosen interpolation model.

Run A averages 60 W for 20 s: **1200 J**. Run B averages 50 W for 30 s: **1500 J**. B uses 10 W less on average but **25% more total energy**. Both statements can be true.

```json
{"status":"ok","measurementBoundary":"calibrated battery input",
 "averagePowerW":50,"energyJ":1500,"energyWh":0.416667,
 "validDurationS":30,"timeCoverage":1.0,
 "comparison":{"averagePowerDeltaW":-10,"energyDeltaJ":300}}
```

With only current repository motor channels, use a boundary such as `registered_motor_bus_voltage_times_current_proxy`, and explicitly decline a whole-robot consumption claim.

**Failure modes:** missing loads, current clipping, sequential reads during a transient, mismatched averaging periods, negative-current sign errors, and different definitions of active time. Use unrounded values internally; reporting hundredths of a watt is unjustified if calibration uncertainty is much larger.

**Validation:** constant power, a linear ramp, the irregular example above, missing intervals, and mixed sensor coverage. Verify a physical energy total against a reference meter across a whole controlled task. Electrical sensor verification precedes claims of small improvements.

## 5. Comparing runs fairly

**Question:** What changed between two executions, and does the difference support a useful engineering conclusion?

**Inputs and readiness:** Recorded signals are **supported**, but matched task boundaries and configuration metadata are **conditional**. Battery identity, payload, software/gain versions, and task outcome generally need additional explicit recording. Priority **P1** for descriptive comparison; **P2** for repeatable experiments.

### Method

Resolve “previous run” to an explicit session before analysis. File modification time is not a reliable run-start timestamp because copying/uploading can change it. In the absence of reliable chronological metadata, require explicit IDs. Automatically selected baselines must return the selection rule and candidate exclusions.

Create a comparison manifest: robot/mechanism identity, task and trajectory version, physical units/calibration, payload, battery preparation, controller configuration, operating mode, and analysis windows. Unknown metadata means comparability is unknown, not that the values match.

Prefer task events to align phases: for example, command accepted → mechanism reached target → object released. Report whole-run and matched-phase metrics separately. Do not stretch two timelines to equal length before calculating energy or duration. Dynamic time warping can help visualize similar path shapes, but can conceal timing differences and does not establish equal work.

For a metric `m`:

$$
\Delta m=m_B-m_A,\qquad \Delta m_{\%}=100(m_B-m_A)/m_A.
$$

If the baseline is zero or close to resolution, return the absolute difference and omit the percentage. State whether lower is desirable; lower current during a failed lift is not success.

For repeated paired trials, analyze one metric difference per pair. Neighboring telemetry samples are correlated: 10,000 samples from one run are not 10,000 independent repetitions. Bootstrap complete independent run pairs, or use blocks only when a justified within-run uncertainty question is being asked. Separate sensor uncertainty from between-run variation. SciPy provides bootstrap confidence intervals, but choosing the independent resampling unit is part of the experimental design. [SciPy bootstrap](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html)

Randomize or alternate A/B trials to reduce order effects, with controlled battery preparation. If multiple trials share a battery/day, model or resample that grouping where appropriate. For many exploratory comparisons, flag selection effects: the most extreme difference among hundreds of metrics is expected to look unusual by chance.

### Worked example and output

Five matched task-energy differences in joules are `[-80,-100,-60,-90,-70]`. Their mean is **-80 J** and sample standard deviation is **15.811 J**. This is evidence of a repeated observed reduction under that protocol, not proof it persists under every payload. With only one pair, report its difference without a population-level certainty claim.

```json
{"status":"ok","metric":"battery_input_energy_j",
 "pairCount":5,"meanPairedDelta":-80,"pairedDeltaSampleStd":15.811,
 "comparability":"matched protocol",
 "limitations":["Five pairs; tested payload and battery preparation only."]}
```

**Failure modes:** different idle periods, an easier path, lower payload, a failed action, changed encoder scaling, incomplete recordings, battery aging over the test order, and choosing only successful attempts after seeing results. Report failure rates and energy across attempts, not just successful-run averages.

**Validation:** compare a run with itself; swap A/B and verify delta signs; test incompatible units, missing metadata, zero baselines, and unequal windows. A deliberately easier trajectory must prevent the summary “the new PID is better” even if its error is lower.

## 6. Controller performance

**Question:** Does a mechanism follow its reference accurately and promptly, without excessive overshoot, oscillation, or actuator saturation?

**Inputs and readiness:** Requires timestamped reference `r`, measured output `y`, actual controller effort/limits, and controller configuration. Ordinary encoder and normalized-power channels do not supply all of these. **Additional instrumentation** for the standard producers; **conditional** if custom telemetry already records them. Priority **P2**, with descriptive tracking metrics available as soon as the paired signals exist.

### Metrics and interpretation

A reference is the desired position or speed; control effort is the command sent to the actuator. Define `e(t)=r(t)-y(t)` in one physical coordinate system. Useful time-weighted metrics are:

$$
\mathrm{MAE}=\frac1T\int|e|dt,\quad
\mathrm{RMSE}=\sqrt{\frac1T\int e^2dt},\quad
\mathrm{IAE}=\int|e|dt.
$$

MAE measures typical error; RMSE penalizes larger excursions more strongly; IAE combines error and duration. Record the quadrature convention for transformed signals such as `e^2`. A signed mean can hide alternating positive and negative errors, so report it only alongside absolute measures.

For an isolated step from `r0` to `r1`, set `d = sign(r1-r0)` and `A = abs(r1-r0)`:

$$
\mathrm{overshoot}_{\%}=100\max(0,\max_t[d(y(t)-r_1)])/A.
$$

This handles upward and downward steps. Percentage overshoot is undefined for zero-amplitude steps. Choose a minimum meaningful step amplitude above noise before detecting steps.

Define rise time as the 10%→90% transition in the step direction. Define settling time as the first entry into `abs(e) <= epsilon` that remains inside through the remainder of the unchanged-reference observation window, with at least a declared dwell time. If there is a later excursion, move the settling time later. If the window or coverage cannot establish the dwell, mark it unobserved. A finite recording can establish only observed settling, not perpetual future behavior.

For ramps or motion profiles, use tracking error and endpoint accuracy instead of forcing step-response metrics. If the implemented controller tracks a profiled internal setpoint, record that setpoint as well as the operator's final goal.

Saturation fraction is time at the configured actuator limit divided by valid active time. The strongest evidence is an explicit clipping flag plus unclipped/clipped controller output; a normalized command near 1 is only a proxy. Long saturation may mean the task demands unavailable acceleration, not that gains are too small. Steady-state error needs a constant target and sufficiently stationary response. Disturbance recovery needs an identified disturbance time.

Oscillation assessment combines amplitude, persistence, and frequency. Remove reference motion before spectral analysis. For sufficiently uniform valid sampling, Welch's spectrum averages windowed periodograms; it is useful for finding sustained periodic content, not proving a control-loop cause. Record sample rate, segment length, detrending, and usable bandwidth. A 20 Hz logger cannot resolve a 15 Hz oscillation uniquely. Irregular data need appropriate resampling with stated limits or a method designed for irregular sampling. [SciPy Welch](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html)

### Worked example and output

A 0→1000 ticks/s step peaks at 1080 ticks/s: **8% overshoot**. It crosses 100 ticks/s at 0.05 s and 900 at 0.30 s: **0.25 s rise time**. From 0.80 s through the observation end it stays within ±20 ticks/s, including the required 0.30 s dwell: observed settling is **0.80 s**. These metrics are different questions; a faster rise is not automatically the preferred controller.

```json
{"status":"ok","stepAmplitudeTicksPerS":1000,"overshootPercent":8,
 "riseTimeS":0.25,"settlingTimeS":0.80,
 "settlingBandTicksPerS":20,"minimumDwellS":0.30,
 "recommendation":"Evaluate the tradeoff against the configured overshoot limit."}
```

**Failure modes:** confusing motor command with a velocity target; missed peaks between samples; angle wraparound; derivative noise; including disabled time; internal versus external controller confusion; claiming stability from a short quiet trace. For angles use the shortest signed angular difference when the task is periodic, not ordinary subtraction across ±pi.

**Validation:** synthetic monotone and underdamped responses, downward steps, ramps, late excursions, never-settled traces, missing data at peaks, and clipped commands. Recompute against hand-labeled step boundaries. Physical validation needs multiple targets and disturbances, not one favorable trace.

## 7. System identification and PID tuning

**Question:** Which controller structure and parameters are justified by the mechanism, and how can we test an improvement without treating an arbitrary gain guess as a verified result?

**Inputs and readiness:** **Additional instrumentation and executor support** for a complete workflow. Log reference, measured state, effective input, gains, controller period, motor mode, output limits, clipping, feedforward, and experiment events. Log position for velocity-model integration and physical limits for bounded motion. Priority **P2**, after data quality and controller assessment.

### 7.1 Understand the controller before adjusting gains

A continuous parallel PID expression is

$$
u(t)=u_{\mathrm{ff}}(t)+K_Pe(t)+K_I\int e(t)dt+K_D\frac{de}{dt}.
$$

Proportional action responds to error, integral action accumulates it, and derivative action responds to its rate of change. Feedforward supplies a predicted input from desired motion or load. These concepts are introduced in [WPILib PID](https://docs.wpilib.org/en/stable/docs/software/advanced-controls/introduction/introduction-to-pid.html) and [feedforward control](https://docs.wpilib.org/en/latest/docs/software/advanced-controls/controllers/feedforward.html). The implementation below is a proposed explicit convention, not a claim about FTC firmware internals.

For a custom discrete controller:

```python
dt = robot_local_time - previous_time
e = reference - measurement
candidate_integral = integral + e * dt
# Derivative on measurement avoids a kick from a discontinuous reference.
d_measurement = low_pass((measurement - previous_measurement) / dt)
u_raw = feedforward + kp * e + ki * candidate_integral - kd * d_measurement
u = clip(u_raw, lower_limit, upper_limit)
# Conditional integration: keep changes that do not deepen saturation.
if u_raw == u or (u_raw > upper_limit and e < 0) or (u_raw < lower_limit and e > 0):
    integral = candidate_integral
```

This assumes a positive actuator sign and positive integral gain. A production adapter must define behavior on invalid `dt`, enable/disable transitions, target changes, and output units. Anti-windup prevents a long saturated command from storing an integral that keeps pushing after the error reverses. Derivative filtering reduces noise but adds delay; log the actual filter, not just `K_D`.

Gain units follow the equation. For velocity error in ticks/s and effort in volts, `K_P` has units V/(ticks/s), `K_I` has V/tick, and `K_D` has V/(ticks/s²). If the implementation sums errors without multiplying by `dt`, its numerical integral coefficient has a different convention. “Use these PID gains” is incomplete without units, discretization, and controller identity.

FTC motor modes also matter:

- `RUN_WITHOUT_ENCODER` permits power commands without the SDK motor speed loop; encoder readings may still be available.
- `RUN_USING_ENCODER` uses internal velocity regulation. A power setting is not automatically an externally logged physical velocity reference.
- `RUN_TO_POSITION` includes position behavior coupled to the velocity-control configuration. Do not assume it is a user-accessible generic three-term position PID.

The published FTC SDK API documents mode-specific coefficient semantics and notes that position mode also uses velocity-mode coefficients. [FTC DcMotorEx API](https://javadoc.io/static/org.firstinspires.ftc/RobotCore/10.2.0/com/qualcomm/robotcore/hardware/DcMotorEx.html)

The repository declares SDK **11.2.1**; the accessible reference above is **10.2.0**. Treat it as conceptual API background and verify the exact runtime SDK/firmware behavior before implementing gain writes. A custom voltage-output controller's gains must not be copied into hub PIDF fields. A cascaded controller can be appropriate, but its inner and outer loops must be identified and tuned as such.

### 7.2 Fit a model with informative experiments

System identification means estimating how input causes state to evolve. Unlike ordinary prediction, data collection matters: constant speed provides little information about acceleration response. Slow forward/reverse ramps help identify friction and speed dependence; bounded dynamic changes help identify inertia and delay. WPILib describes fitting motor models from such input/output data. [System identification introduction](https://docs.wpilib.org/en/stable/docs/software/advanced-controls/system-identification/introduction.html)

A useful candidate model for a motor-driven mechanism is

$$
V=k_S\operatorname{sgn}(v)+k_Vv+k_Aa+g(q).
$$

Here `k_S` represents an approximate friction voltage, `k_V` speed-dependent voltage, `k_A` acceleration-dependent voltage, and `g(q)` gravity compensation. An elevator may use a constant gravity term; an arm can require an angle-dependent term such as `k_G cos(q)` with a documented angle origin. Do not force this linear-in-parameters model onto backlash, changing contact, or strongly varying geometry without testing residuals.

For measured rows, form `X = [sign(v), v, a, gravity_basis]` and solve a scaled least-squares or robust regression problem. Check excitation and the conditioning of `X`; poor rank means multiple parameter combinations explain the same data. Avoid treating regularization as evidence that unidentifiable coefficients became physically known.

Acceleration from finite differences amplifies noise. Fit a local smooth curve within continuous intervals, document the derivative/filter window, and exclude boundaries/gaps. Alternatively fit the integrated dynamic model directly, which avoids explicit differentiation. Input delay and sensor delay must be distinguished where possible; a phase-shifted fit can produce plausible but misleading coefficients.

Closed-loop input is correlated with disturbances because the controller reacts to them. Naive ordinary least squares may therefore be biased. Prefer designed experiments, and test multi-step predictions on entire held-out trials. With closed-loop-only data, explicitly limit causal interpretation and consider an identification method appropriate to feedback data.

Use actual effective voltage if available. `command * battery_voltage` is an approximation only after verifying the command-to-output path. Reject hidden inner-loop operation or label the model as an empirical command-to-response model. Do not call its coefficients motor voltage constants.

Model validation includes held-out trajectory prediction, residual structure, coefficient stability across repeated trials, and applicability across direction/load. A high training R² alone is insufficient. Gain estimates based on an identified model remain starting points requiring physical tuning and validation. [WPILib analysis guidance](https://docs.wpilib.org/en/stable/docs/software/advanced-controls/system-identification/analyzing-gains.html)

### 7.3 A transparent numerical candidate

Suppose validated synthetic velocity dynamics are

$$
k_A\dot v+k_Vv=u,\quad k_A=0.002\ \mathrm{V/(ticks/s^2)},\quad
k_V=0.020\ \mathrm{V/(ticks/s)}.
$$

For a constant target, use velocity feedforward plus proportional feedback:

$$
u=k_Vr+K_P(r-v).
$$

Substitution gives `k_A * de/dt = -(k_V + K_P)e`, so the ideal error time constant is

$$
\tau=\frac{k_A}{k_V+K_P},\qquad K_P=\frac{k_A}{\tau}-k_V.
$$

For a chosen `tau = 0.05 s`, **K_P = 0.020 V/(ticks/s)**. At a target of 200 ticks/s from rest, feedforward is 4 V and initial feedback is 4 V: total **8 V**, within a hypothetical 10 V effort limit. The ideal unsaturated model reaches 2% error after `-tau*ln(0.02) = 0.1956 s`.

This is a **P plus feedforward candidate**, not a claim that every controller needs all three PID terms. Delays, friction, changing voltage, discretization, and load can invalidate the predicted response. If the logger only samples every 20 ms, a 50 ms time constant has few samples per time constant; improve instrumentation or choose a slower validated target. Never present these illustrative gains as settings for the actual robot.

### 7.4 Search and evaluate candidates

For more complex plants, simulate a bounded set of candidates on the validated model before testing. Use a declared objective, for example

$$
J=w_e\frac{\mathrm{IAE}}{E_0T_0}+w_t\frac{t_s}{T_0}
+w_u\frac{1}{T U_0^2}\int u^2dt.
$$

The normalizers make the terms dimensionless; the weights express task preferences. Electrical energy is not generally `integral(u²)`: that term is an effort penalty. Apply hard rejection constraints for overshoot, current, travel, duration, and failed completion before ranking feasible candidates. A penalty score must never trade a violated physical limit for a faster response.

Start with the simplest justified controller, often P/PD plus feedforward. Add integral only to address a demonstrated residual bias with anti-windup. For empirical tuning, use small bounded parameter changes and repeated test cases. Do not make sustained ultimate-gain oscillation or aggressive relay tests the default on an assembled mechanism.

### 7.5 Full future experiment lifecycle

1. **Inspect:** identify robot, mechanism, mode, units, software version, gain readback, limits, and available observations. Reject ambiguous controller mappings.
2. **Propose an experiment:** specify reference profile, input bounds, physical travel envelope, maximum duration/current, telemetry needs, and explicit completion/abort conditions. Mechanism limits come from validated configuration, not generic constants in this handbook.
3. **Arm and execute:** use an authorized, exclusive backend operation bound to the robot/session/configuration. The robot executes the entire bounded profile locally. Network interruption must not extend motion.
4. **Record and fit:** retain experiment identity, requested and acknowledged settings, timing, stop reason, and resulting recording. Failed/aborted trials remain in the dataset with their outcomes.
5. **Propose gains:** return model evidence, objective, expected tradeoffs, validity range, and candidate settings. A proposal has no hardware side effect.
6. **Apply and read back:** use a separate authorized operation, verify the current configuration still matches the proposal, snapshot previous settings, apply at a supported boundary, and confirm actual values. Do not blindly retry a timed-out write; reconcile state by operation ID/readback.
7. **Validate on fresh trials:** use the same task suite for baseline and candidate, including direction changes, load changes, and representative battery conditions. Stop on hard-limit violations.
8. **Retain or roll back:** keep a candidate only when hard constraints pass and repeatable improvement meets the declared criterion. On failure restore the previous configuration and verify it; if restoration cannot be confirmed, keep the mechanism disabled and report unresolved state. Persistence across reboot is a separate explicit setting.

The backend remains authoritative for ownership, command validation, and authorization. Robot-local watchdogs, output limits, duration limits, physical stop logic, and a human stop control must work without MCP or the LLM. Analysis-only tools must never implicitly arm or apply anything.

```json
{"status":"ok","proposalOnly":true,"controllerType":"custom_voltage_velocity_p_ff",
 "candidate":{"kp":0.020,"ki":0,"kd":0},
 "kpUnit":"V/(ticks/s)","predictedTimeConstantS":0.05,
 "validationState":"simulation_only",
 "requiredNextStep":"Bounded physical validation under the specified experiment profile"}
```

**Failure modes:** wrong feedback sign, degree/radian or tick scaling errors, unobserved saturation, nested loops, friction mistaken for integral need, model leakage between training and validation, tuning to one trajectory, and stochastic optimizer confidence mistaken for physical safety.

**Validation:** recover known synthetic model parameters under sufficient excitation; reject rank-deficient constant-speed data; inject delay/noise/clipping; verify the numerical example; compare predicted and observed held-out responses. Executor tests must cover stale proposals, duplicate requests, disconnects, aborts, readback mismatch, and rollback failure before any mechanism experiment.

## 8. Motor and drivetrain diagnostics

**Question:** Which behaviors warrant mechanical or electrical inspection, and what observation would discriminate among possible causes?

**Inputs and readiness:** Commands, encoder position/velocity, motor current, and voltage are **supported** in bound motor telemetry. Mechanical diagnosis is **conditional** on operating context and calibration. Proving wheel slip needs an independent motion reference. Priority **P1** for candidate incidents, **P2** for calibrated characterization.

### Method

A candidate stall can be defined as sustained command demand, low speed, and elevated current:

$$
|u|>u_{\min}\ \land\ |v|<v_{\min}\ \land\ I>I_{\mathrm{high}}.
$$

Require a configurable persistence interval and exclude intended holding, disabled states, and known end stops. Thresholds depend on the motor, gearing, load, sensor resolution, and allowed task. Record each condition separately so a reviewer can inspect why it triggered. With low speed but no current measurement, say “low response to command,” not “confirmed stall.”

Drivetrain asymmetry should be evaluated only in comparable maneuvers. With calibrated forward velocity signs, a normalized difference is

$$
A_v=\frac{v_L-v_R}{\max((|v_L|+|v_R|)/2,v_{\mathrm{floor}})}.
$$

Require straight-motion intent and speeds above a useful floor. A turning command intentionally creates asymmetry. Compare current at matched speed/load as well as speed at matched command. Wheel radius, gear ratio, reversal configuration, and encoder counts must be consistent.

For dead-zone characterization, use bounded slowly increasing commands in each direction and record the first sustained motion above noise. Report separate breakaway and motion-maintenance thresholds; static friction and hysteresis can make them different. Gravity-loaded mechanisms require direction/load context.

To investigate increased resistance, compare current residuals relative to a baseline model conditioned on speed, acceleration, voltage, direction, and payload. For example, `I_observed - I_expected` persistently above the baseline range flags extra load. It cannot distinguish a binding bearing from heavier payload without further evidence. Motor current alone also does not prove overheating; thermal state depends on temperature and cooling history.

### Worked example and output

In a straight-command segment, calibrated wheel speeds are 0.60 and 0.50 m/s. The normalized difference is `0.10/0.55 = 18.18%`. This supports “left wheel encoder speed was higher,” not “robot drifted right,” because wheel slip and external motion remain unmeasured.

Separately, a mechanism holds command 0.6, speed under 2 ticks/s, and 4 A current for 0.8 s. If those exceed its configured demand/current gates, fall below its speed gate, and no hold/end-stop state applies, return a candidate stall.

```json
{"status":"ok","finding":"candidate_stall","windowS":[8.0,8.8],
 "evidence":{"command":0.6,"speedUpperTicksPerS":2,"currentA":4},
 "hypotheses":["blocked mechanism","unexpected end stop","encoder fault with load"],
 "nextCheck":"Inspect the mechanism and independently verify encoder movement."}
```

**Failure modes:** a robot pushing against a wall, intended arm holding, acceleration transients, an unplugged encoder, wheel slip, differing motor types, and current-sensor bias. A correlation among these signals narrows investigation; it does not identify a failed part by itself.

**Validation:** fixtures for true low-response/high-current episodes, ordinary starts, intentional holds, turns, reversed encoder signs, and missing current. Ground-truth physical fault labels should come from inspection or controlled benign test conditions; do not deliberately damage mechanisms to obtain them.

## 9. Loop and communication performance

**Question:** Is timing irregularity happening in robot publication, sensor acquisition, transport, or backend handling, and does it affect the observed task?

**Inputs and readiness:** Publication intervals, sample timestamps, receipt times, and gap evidence are **supported**. Exact control execution duration, one-way network latency, sensor acquisition delay, and end-to-end command response require **additional instrumentation**. Priority **P1** for publication/recording health, **P2** for detailed timing attribution.

### Method

Calculate median, p95/p99, maximum, and dispersion of robot-local publication intervals. Exclude the first zero-valued interval from the standard signal. If a required publication period `h` is explicitly configured, report the fraction of intervals exceeding `h + tolerance`. Call these publication deadline exceedances, not proven control deadline misses.

Separately measure actual loop execution using robot-local timestamps around the work being profiled. Sensor reads, waiting, publication, and other tasks need named spans if the purpose is to locate the cause. A 30 ms publication interval does not prove the controller spent 30 ms calculating.

Let robot timestamp be `t_r` and laptop receipt time be `t_l`. Their difference is not one-way latency without clock calibration. A model is

$$
t_l=a t_r+b+d(t),
$$

where `a` captures relative clock rate, `b` clock origin, and `d(t)` transmission/queueing delay. With only one-way observations, constant delay and clock offset are confounded. An estimated lower-envelope clock mapping can reveal relative delay excursions under assumptions, but not absolute latency. Re-estimate across relevant clock epochs and report synchronization uncertainty.

Batching makes many samples share a receipt timestamp. Use robot sample times for signal dynamics and envelope receipt times for transport observations. Sequence gaps, sender queue drops, and decode failures represent different problems and should not be collapsed into one “packet loss” percentage. Inspect counter semantics: a queue-drop counter may count discarded frames, not exclusively telemetry samples.

For command latency, future logging should include request ID, laptop send time, robot receive time, robot apply time, and acknowledgment receipt. Same-clock round trip and robot receive-to-apply duration are directly interpretable. Cross-clock components still need synchronization.

### Worked example and output

Publication intervals `[20,20,20,60]` ms have mean **30 ms**, median **20 ms**, and maximum **60 ms**. With a declared 25 ms threshold, one of four intervals exceeds it: **25%**. The correct statement is “one long publication interval,” not “a 40 ms network stall.”

```json
{"status":"ok","measurement":"telemetry_publication_interval",
 "medianMs":20,"maximumMs":60,"thresholdMs":25,
 "exceedanceFraction":0.25,"controlExecutionDurationMs":null,
 "absoluteOneWayLatencyMs":null}
```

**Failure modes:** attributing receipt bursts to control jitter, treating sensor sample hints as measured rates, subtracting unrelated clocks, interpreting first-sample zero as exceptionally fast execution, and estimating stable p99 from very few intervals. Always include sample count and percentile convention.

**Validation:** synthetic periodic robot timestamps with delayed batched receipt; genuine publication stalls with regular transport; clock drift; reconnects; and sparse samples. The transport-delay fixture must leave robot-local dynamics unchanged.

## 10. Mechanisms and autonomous routines

**Question:** Which task phase takes time, which action fails, and how repeatable is the robot's execution?

**Inputs and readiness:** Explicit mechanism state transitions, action IDs, outcome events, reference trajectories, and pose estimates require **additional instrumentation** in the standard producers. Gamepad/encoder data permit **conditional** approximate segmentation. Priority **P2**; simple explicitly marked task durations can be an early extension.

### Method

Use a finite-state task model with recorded transitions, for example `idle → acquire → transport → release → return`. A complete cycle is identified by start/end events and a task ID, not by a guessed time interval. Compute phase durations, retry counts, timeouts, and outcomes. Preserve incomplete attempts as incomplete or failed; excluding them biases cycle-time statistics toward easy runs.

If events are unavailable, use threshold/hysteresis segmentation on observed signals and mark the boundaries as inferred. A gamepad button press shows operator intent, not successful acquisition. A servo command shows a requested state, not the physical arrival of the mechanism.

$$
T_{\mathrm{cycle}}=t_{\mathrm{end}}-t_{\mathrm{start}},\qquad
p_{\mathrm{success}}=N_{\mathrm{success}}/N_{\mathrm{attempts}}.
$$

Use a binomial interval only when the trial-independence assumptions are reasonable; repeated cycles within one run can share conditions. For energy productivity, `total attempt energy / number of successes` includes unsuccessful attempts. Also report success rate so a ratio does not hide failures. If there are no successes, the ratio is unavailable.

For trajectory tracking with desired pose `(x_d,y_d,theta_d)` and estimated pose `(x,y,theta)`, rotate position error into the desired heading frame:

$$
e_{\parallel}=\cos\theta_d(x-x_d)+\sin\theta_d(y-y_d),
$$

$$
e_{\perp}=-\sin\theta_d(x-x_d)+\cos\theta_d(y-y_d),\qquad
e_\theta=\operatorname{wrap}(\theta-\theta_d).
$$

Time-indexed errors reveal schedule tracking. Nearest-path cross-track error answers a different question and must use progress constraints to avoid matching the wrong branch at a self-intersection. Endpoint repeatability is the distribution of endpoints across trials. Accuracy requires independent ground truth: an estimator can consistently report the same wrong endpoint.

### Worked example and output

A successful cycle has phase durations 1.2, 2.0, 0.4, and 1.4 s: **5.0 s total**. In 10 attempts, 8 succeed and 2 time out: **80% observed success**, not “10 cycles at the successful average time.” If all attempts consume 1000 J in total, energy per success is **125 J**, including failed attempts.

```json
{"status":"ok","attempts":10,"successes":8,"timeouts":2,
 "observedSuccessFraction":0.8,"attemptEnergyJ":1000,
 "energyPerSuccessJ":125,"segmentation":"explicit task events"}
```

**Failure modes:** silently discarded partial attempts, changing task geometry, duplicate outcome events, a pose estimate evaluated against itself, and comparing path shapes after eliminating the timing difference of interest.

**Validation:** a state-machine fixture with retries, repeated starts, missing ends, and timeouts; heading wrap at ±pi; self-intersecting paths; known endpoint offsets. Review inferred boundaries against labeled events or properly aligned video before using them for performance claims.

## 11. Vision and localization

**Question:** How often is usable vision available, how stable are its outputs, and how much do latency or localization errors affect control?

**Inputs and readiness:** Logged target validity, angles, reported processing latency, pipeline index, and FPS are **supported** for the Limelight OpMode. Unique-frame availability and freshness need **additional instrumentation**. Absolute localization accuracy needs an independent, calibrated reference. Priority **P1** for valid-target summaries, **P2** for timing/pose evaluation.

### Method

Always mask angles and latency using validity. The producer writes zero when a target is invalid; including those zeros would make missing targets look perfectly centered and instantaneous. Split analyses across pipeline changes and document the intended target-selection policy.

With bounded state-hold intervals, compute

$$
\mathrm{availability}=T_{\mathrm{valid\ target}}/T_{\mathrm{observed}},
$$

along with longest observed dropout, reacquisition times, and valid-only angular summaries. Treat unobserved gaps separately from explicit no-target states. Without unique frame IDs, this is availability of the **reported state**, not the fraction of independent camera frames that detected a target.

For a stationary target and robot, estimate jitter using standard deviation or a robust median absolute deviation. Jitter is repeatability, not accuracy. With movement, angle variation can be desired motion rather than sensor noise. For target changes, analyze each target separately.

The existing latency channel is capture plus targeting latency; it omits other possible age components and is zero-filled on invalid results. Log camera timestamp/frame identity, robot retrieval time, and staleness before claiming end-to-end age. The SDK's Limelight result API distinguishes capture/targeting/parse latency, timestamps, and staleness. Verify exact definitions against the installed version. [FTC LLResult API](https://javadoc.io/static/org.firstinspires.ftc/Hardware/10.2.0/com/qualcomm/hardware/limelightvision/LLResult.html)

For localization, transform observations into the same spatial frame and align acquisition times. An uncorrected 0.10 s timing offset at 1 m/s can appear as **0.10 m** position disagreement. Comparison of two sensors is disagreement, not ground-truth error. If valid independent covariances exist, normalized innovation can be evaluated with `r^T S^-1 r`; using it as a statistical gate requires a justified residual distribution and accounting for shared information between estimates.

### Worked example and output

In ten equally supported intervals, eight are target-valid and their angles are `[2,2,2,2,4,4,4,4]` degrees. Valid-only mean is **3 degrees**. The other two intervals contain invalid placeholders of 0; including them gives **2.4 degrees**, falsely suggesting better centering. Reported-state availability is **80%**.

```json
{"status":"ok","reportedTargetAvailability":0.8,
 "validOnlyMeanTxDegrees":3,"independentFrameCount":null,
 "endToEndAgeMs":null,
 "limitations":["No unique frame IDs or calibrated camera-to-robot time mapping."]}
```

**Failure modes:** repeated camera frames counted as independent data, invalid zeros, target switching, lighting-dependent detections, miscalibrated extrinsics, incorrect coordinate frames, and interpreting video time-zero alignment as precise synchronization.

**Validation:** invalid-zero fixture above; repeated frame identities after instrumentation; pipeline switches; controlled stationary target jitter; known moving-target timing offset. For accuracy use surveyed poses or another suitable independent measurement, and include calibration uncertainty.

## 12. Anomalies and evidence-backed summaries

**Question:** What deserves attention in a run, and what evidence supports the explanation?

**Inputs and readiness:** Earlier validated metrics provide **conditional** summary support. Historical learned anomaly detection requires a sufficiently representative recording collection and metadata. Priority **P1** for rule-based findings, **P3** for learned baselines.

### Start with interpretable findings

Generate structured findings from prior sections: a sustained voltage dip, a publication gap, increased matched-task energy, or excessive tracking error. Each finding contains an observation, its engineering relevance, evidence interval, quality status, and possible next check. Keep these separate:

| Level | Example |
|---|---|
| Observation | Voltage fell 1.2 V relative to the declared pre-load baseline. |
| Association | The dip overlapped a high-demand drivetrain interval. |
| Hypothesis | Load or supply-path resistance may contribute. |
| Confirmed cause | Requires an appropriate intervention or independent inspection. |

Rank findings by configured task impact, magnitude relative to a relevant baseline, persistence, and evidence quality. Group overlapping manifestations of one event so a voltage dip does not become five apparently independent failures. Quality problems can outrank performance findings when they invalidate the conclusion.

Do not invent a single numeric “confidence” by combining arbitrary scores. Report measurement coverage, uncertainty intervals where justified, sample/run counts, and the assumptions behind diagnostic language. “Largest sag” requires an explicit ranked event set; “largest ever” requires an explicitly complete eligible history.

### Historical baselines and learning

A robust exploratory score for a metric within a matched operating regime is

$$
z_{\mathrm{robust}}=\frac{x-\operatorname{median}(X)}{1.4826\operatorname{MAD}(X)}.
$$

The scale factor aligns MAD with standard deviation for an approximately normal distribution. It does not turn the result into a calibrated fault probability. If MAD is zero or below sensor resolution, use a documented physical tolerance or declare the score unavailable.

Condition baselines on task, robot configuration, and relevant load/battery variables. A model trained mostly on idle periods can label every normal acceleration an anomaly. Begin with these contextual baselines; consider isolation forests or reconstruction models only if they add validated detection value. Learned output means “unusual under the training distribution,” not a named mechanical fault.

Split evaluation by whole run and preferably by later time periods, not random neighboring samples. Exclude future data from a run's historical baseline. Track false alarms per run, missed independently labeled incidents, detection delay, and changes after new hardware/software. Recalibrate or invalidate a baseline when the mechanism changes.

### Worked example and output

Matched historical cycle-energy median is 100 J, MAD is 5 J, and the new value is 130 J. The robust score is `30/(1.4826*5) = 4.047`. This supports a review-worthy high energy value. It does not imply a 99.99% probability of a fault.

```json
{"status":"ok","finding":"unusually_high_cycle_energy",
 "valueJ":130,"baselineMedianJ":100,"baselineMadJ":5,
 "robustScore":4.047,"cause":"undetermined",
 "nextCheck":"Compare matched phase durations, payload, and motor-current residuals."}
```

An LLM summary should preserve limitations: “Among the three eligible completed runs, this run had the deepest measured voltage dip. Its matched transport phase also used more measured input energy. Payload metadata is missing, so the cause of the energy difference is unresolved.” A tool should supply the eligible run IDs, actual figures, and evidence intervals behind that sentence.

**Failure modes:** multiple-testing false alarms, threshold tuning on evaluation data, sensor drift, training on unlabeled faults as normal, causal storytelling, selective summaries that hide failures, and treating free-text telemetry as instructions. Event text is untrusted data; it must never trigger commands or override analysis policy.

**Validation:** known benign events, labeled incidents, duplicated correlated alerts, unseen configurations, absent baselines, zero MAD, and incomplete cohorts. Review generated summaries for number/unit consistency and unsupported causal or superlative claims. The same numerical findings should support the same factual summary regardless of wording.

## 13. Backend and MCP interfaces

These interfaces are **proposals**, not existing endpoints. Implement the numerical engine as a reusable backend library, then expose it through HTTP and MCP adapters. It should also serve the recording viewer without duplicating formulas. Preserve the existing MCP pattern of calling backend APIs rather than importing backend globals or opening a competing robot connection.

### 13.1 Dataset and execution model

Load recordings from an allowlisted library by session ID, not an arbitrary model-supplied filesystem path. Preserve raw envelope provenance, connection/schema history, validity masks, original timestamps, and recording integrity status. Retain numerical results separately from plot downsampling. An analysis dataset should be immutable for a particular recording revision.

The present replay loader sorts snapshots and retains a single catalog, which is useful for a browser but insufficient as the complete analysis contract. Build a schema-aware analysis representation from the raw stream; reuse the codec and log reader where their guarantees fit. Validate that each selected sample actually has the schema it claims. Preserve rejected-record counts and reasons instead of silently producing a clean-looking dataset.

Use finalized recordings by default. Explicit partial/live analysis must pin a cutoff sequence/time and label results provisional. A crash-recovered recording may be useful but cannot silently pass as a complete run. Do not infer completion solely from the presence of some samples or trust upload modification time for chronology.

A versioned result/cache key should include recording content identity, analysis method/version, signal mappings, calibration/configuration identity, windows, and parameters. Record a random seed for bootstrap or optimization. Large analysis jobs can return a job ID and be polled through a bounded status interface; repeated reads must not launch duplicate computation. Start with simple synchronous analyses and introduce jobs only when measured work duration requires them.

Proposed run metadata additions include robot/mechanism identity, source revision/build, runtime SDK/firmware, gain snapshot, motor mode, sensor calibration, battery ID and preparation, payload, task/trajectory version, and outcome. Unknown values remain unknown. This does not require changing the protobuf immediately: a versioned recording sidecar can attach metadata, with provenance distinguishing automatic capture from operator input.

### 13.2 Focused tool surface

| Proposed tool | Essential input | Bounded output / behavior |
|---|---|---|
| `list_recordings` | Filters and pagination | Session IDs, trustworthy chronology if available, completion status, task/config metadata. |
| `get_analysis_capabilities` | Recording ID(s), optional mechanism | Eligible analyses, resolved signals, missing observations, semantic caveats. |
| `analyze_recording` | Recording ID, enumerated analysis kind, window, typed parameters | Quality, battery, energy, timing, diagnostic, task, or vision findings. Reject arbitrary code/formula execution. |
| `compare_recordings` | Explicit baseline/candidate IDs, metric kinds, matched windows/tasks | Deltas, units, comparability report, exclusions, repeated-trial statistics when supported. |
| `assess_controller` | Recording IDs, explicit reference/measurement mapping, controller identity | Tracking metrics and diagnostic evidence; no writes. |
| `propose_controller_tuning` | Identification recordings, controller configuration, objective and constraints | Model validation, candidate gains, required experiment, proposal ID; no writes. |
| `get_analysis_evidence` | Result ID, finding ID, bounded point/event limit | Original evidence references plus limited traces/events. |
| `summarize_recording` | Recording ID and explicit optional comparison set | Ranked structured findings for the LLM to explain; no new unsupported calculations. |

These can map to backend routes such as `POST /api/analysis/recording`, `POST /api/analysis/compare`, and `POST /api/analysis/controller/proposals`. Read-only computation can use POST without becoming a robot-control operation. Keep current status tools compatible.

Use a discriminated parameter schema per analysis kind rather than an unvalidated dictionary. Configure numeric bounds and signal compatibility in the backend. The model may request a different analysis window; it should not be able to disable integrity checks or relabel a proxy as calibrated input power.

For a future control phase, add separate narrow operations such as `run_controller_experiment`, `apply_controller_proposal`, `get_controller_operation`, and `restore_controller_configuration`. The stop/abort path must remain available independently of the LLM. Mutating operations require backend-enforced ownership, authorization, limits, idempotency/reconciliation, and readback. A proposal ID binds the candidate to its robot, controller, configuration revision, and experiment bounds; changes invalidate the proposal.

MCP supports tool input schemas, structured results, and output schemas. Use these for machine-readable metrics and a brief textual explanation. Read-only/destructive annotations describe behavior but do not enforce authorization. Match the implemented server SDK and negotiated protocol version rather than silently adopting a newer protocol from an unrelated example. [MCP tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

### 13.3 Representative request and result

```json
{
  "tool": "compare_recordings",
  "arguments": {
    "baselineRecordingId": "11111111-1111-4111-8111-111111111111",
    "candidateRecordingId": "22222222-2222-4222-8222-222222222222",
    "metrics": ["average_input_power", "input_energy"],
    "baselineWindowS": [0, 20],
    "candidateWindowS": [0, 30],
    "measurementBoundary": "battery_input"
  }
}
```

An illustrative successful result for the calibrated scenario in section 4:

```json
{
  "resultId": "analysis-example-001",
  "status": "ok",
  "method": {"name": "electrical_run_comparison", "version": "1.0"},
  "parameters": {"integration": "trapezoid_on_power", "maxIntervalS": 0.1},
  "inputs": [
    {"recordingId": "11111111-1111-4111-8111-111111111111",
     "contentIdentity": "illustrative-baseline-content",
     "windowS": [0, 20], "schemaRevisions": [1]},
    {"recordingId": "22222222-2222-4222-8222-222222222222",
     "contentIdentity": "illustrative-candidate-content",
     "windowS": [0, 30], "schemaRevisions": [1]}
  ],
  "signals": {"voltage": "battery.voltage", "current": "battery.inputCurrent"},
  "measurementBoundary": "battery_input",
  "calibrationId": "illustrative-reference-calibration",
  "quality": {"baselineTimeCoverage": 1.0, "candidateTimeCoverage": 1.0},
  "comparability": {"task": "matched", "duration": "different"},
  "metrics": [
    {"name": "average_input_power", "unit": "W", "baseline": 60,
     "candidate": 50, "delta": -10},
    {"name": "input_energy", "unit": "J", "baseline": 1200,
     "candidate": 1500, "delta": 300}
  ],
  "evidence": [
    {"findingId": "energy-increase", "baselineWindowS": [0, 20],
     "candidateWindowS": [0, 30]}
  ],
  "limitations": ["Illustrative values; no between-run uncertainty inferred from one pair."],
  "summary": "Average power decreased by 10 W; total energy increased by 300 J."
}
```

`battery.inputCurrent` in this example is a **proposed additional signal**, not an existing audited standard channel. With current unverified motor-current proxies, the same whole-robot request should instead return something like:

```json
{
  "status": "insufficient_data",
  "requestedAnalysis": "whole_robot_input_energy",
  "metrics": [],
  "missingRequirements": ["Verified total battery-side current"],
  "availableAlternative": "registered_motor_voltage_current_proxy_summary",
  "limitations": ["Motor channel sums have unverified measurement boundaries and incomplete load coverage."]
}
```

### 13.4 Result semantics

Use `ok`, `partial`, `insufficient_data`, `incompatible`, and `error` consistently. `partial` means a clearly bounded subset is valid; `insufficient_data` means the requested conclusion cannot be supported; `incompatible` means inputs cannot be compared under the requested semantics. None means the robot performed badly.

Every result must identify inputs, signal mappings, units, physical/temporal frame, window origin, method version, parameters, calibration assumptions, coverage, exclusions, and evidence timestamps or sequence ranges. Controller proposals additionally include exact controller conventions and validation state. Represent unavailable metrics as omitted or null with reasons, never NaN, infinity, or invented zero.

Return aggregate metrics and a capped set of findings by default; proposed defaults are 10 findings and no raw traces. Evidence requests may return up to 1000 display points per signal and 100 events, with explicit truncation and aggregation metadata. Full-resolution calculations remain on the backend. Pagination or result resources expose more evidence without sending an entire recording into the model context.

A summary must be traceable to metric IDs. The backend enforces eligibility; the LLM explains it. “No eligible comparison” is a useful result. It is better than a persuasive sentence whose denominator, scope, or sensor meaning is unknown.

## 14. Implementation priorities and acceptance tests

### Delivery sequence

| Stage | Deliverable | Exit criterion |
|---|---|---|
| **P0: trustworthy dataset** | Schema-aware raw decoding, clock/interval validity, units, immutable provenance, capability checks | Missing/corrupt/incompatible data cannot silently become valid metrics. |
| **P1: useful read-only tools** | Voltage events, labeled power proxies, fair descriptive comparisons, publication timing, candidate motor incidents, valid-target summaries | Every statement has an eligible window, clear measurement boundary, and inspectable evidence. |
| **P2: instrumentation and experiments** | Calibrated power, controller references/configuration, task/pose/frame metadata, bounded executor, model fitting and gain validation | Numerical validation and command lifecycle checks pass; physical trials validate any retained settings. |
| **P3: historical analysis** | Contextual baselines, learned anomalies where justified, trend reports | Held-out run evaluation demonstrates value at a measured false-alarm rate. |

Build one vertical slice first: load a finalized recording → assess voltage data quality → extract minimum and qualified sag events → return evidence via MCP. This tests the storage-to-explanation path without needing controller writes. Next add comparisons with explicit recording IDs. Avoid beginning with an unconstrained “analyze everything and fix it” tool.

### Numerical and behavioral test matrix

These are tests for the future implementation, not tests added by this documentation change.

| Scenario | Expected outcome |
|---|---|
| Irregular power samples from section 4 | 110 J and 36.6667 W under the stated interpolation; never the arithmetic sample mean. |
| 60 W for 20 s versus 50 W for 30 s | Report both -10 W and +300 J; do not claim energy savings. |
| Known gap from 0.04 to 0.20 s | 20% valid-time coverage in the example; no integration across the gap. |
| One voltage sample, no baseline | Point minimum can be reported; sag, duration, and energy cannot. |
| Unit/schema change or clock reset | Split or reject affected analysis; preserve provenance. |
| Missing total battery current | Decline whole-robot energy; identify the available proxy honestly. |
| Pair of identical runs | Zero metric deltas and no invented improvement. |
| Candidate executes an easier trajectory | Descriptive differences allowed; controller-improvement claim withheld. |
| Up/down steps and late excursions | Correct directional overshoot and observed settling definition. |
| Known first-order velocity plant | Recover identifiable coefficients; reproduce the candidate calculation before adding delay/noise. |
| Constant-speed identification data | Reject or restrict acceleration-parameter identification. |
| Robot timestamps regular, receipts batched | Do not report robot-control jitter from receipt bursts. |
| Invalid vision values represented as zero | Exclude placeholders; valid-only mean is 3 degrees in section 11. |
| Eight successes and two timeouts | Preserve all ten attempts and their energy/outcomes. |
| Zero-MAD or unseen-configuration baseline | No infinite anomaly scores or automatic fault diagnoses. |
| MCP request with unsupported signals/oversized evidence | Typed failure or bounded response, no arbitrary-code fallback. |
| Apply timeout, stale proposal, disconnect, duplicate request | Reconcile operation state; never extend/repeat motion implicitly. |

For recorded fixtures, choose small finalized recordings with known producer configurations and manually checked evidence windows. Cross-check a small numerical slice independently of the production implementation. Do not write tests that merely call the same calculation twice. Keep future analysis tests separate from hardware execution tests.

For physical trials, predeclare task, limits, metric definitions, and retention criteria. Use held-out repetitions and representative conditions. A fitted controller model is accepted only over the regime it predicts adequately; a gain change is retained only after fresh measurements meet the constraints and improvement criterion. Record failures and aborted trials as part of the evidence.

### Documentation acceptance

This handbook's deliverable is one Markdown file. It makes no runtime changes. Before implementing a capability, recheck the audited source behavior because the working tree and instrumentation will evolve. Documentation examples provide methods and expected arithmetic, not measured performance of this repository's robot.

## 15. Sources and further reading

Repository observations in section 1 come from direct source inspection. Equations, worked examples, tool names, priorities, and operating policies are explanations/proposals in this handbook. External sources establish underlying concepts or API behavior; they do not certify the proposed analyses on this robot.

| Primary source | Use and limits |
|---|---|
| [REV integrated sensors](https://docs.revrobotics.com/duo-control/control-system-overview/integrated-sensors) | Sensor availability; not proof of telemetry coverage or wattage calibration. The page was discoverable in search, but full-page retrieval failed during this audit. |
| [FTC DcMotorEx API, 10.2.0](https://javadoc.io/static/org.firstinspires.ftc/RobotCore/10.2.0/com/qualcomm/robotcore/hardware/DcMotorEx.html) | Motor current, velocity, and mode-specific PIDF API concepts; verify against repository SDK 11.2.1 before implementation. |
| [FTC LLResult API, 10.2.0](https://javadoc.io/static/org.firstinspires.ftc/Hardware/10.2.0/com/qualcomm/hardware/limelightvision/LLResult.html) | Distinct latency/freshness fields; exact installed-version semantics still require checking. |
| [WPILib introduction to PID](https://docs.wpilib.org/en/stable/docs/software/advanced-controls/introduction/introduction-to-pid.html) | Feedback terminology and controller terms; not an FTC gain conversion guide. |
| [WPILib feedforward control](https://docs.wpilib.org/en/latest/docs/software/advanced-controls/controllers/feedforward.html) | Mechanism-aware feedforward; APIs target WPILib rather than this Java FTC project. |
| [WPILib system identification](https://docs.wpilib.org/en/stable/docs/software/advanced-controls/system-identification/introduction.html) | Input/output model fitting and excitation concepts. |
| [WPILib analyzing identification data](https://docs.wpilib.org/en/stable/docs/software/advanced-controls/system-identification/analyzing-gains.html) | Fit interpretation, filtering/delay concerns, and gain estimates as starting points. |
| [TI improving voltage measurement accuracy](https://www.ti.com/document-viewer/lit/html/sszt315) | Measurement timing and synchronized voltage/current concerns; does not specify REV sensor implementation. |
| [TI fuel gauging overview](https://www.ti.com/product-category/battery-management-ics/battery-fuel-gauges/overview.html) | Why battery state estimation involves more than a voltage minimum. |
| [SciPy trapezoid](https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.trapezoid.html) | Numerical integration with explicit sample coordinates. |
| [SciPy Welch](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html) | Windowed spectral estimation; sampling/interpretation gates are application responsibilities. |
| [SciPy bootstrap](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html) | Confidence interval computation; does not choose independent experimental units for us. |
| [MCP tools specification, 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) | Typed tools, structured results, annotations, and execution responsibilities; implementation must match its supported protocol. |

References were checked or located on the audit date. Full 11.2.1 API pages were not retrievable through the research tool; this version gap is intentionally explicit rather than silently treating older documentation as exact runtime verification.
