# FTC Advanced Driver Station: App Mental Model

## What we are building

FTC Advanced is a customizable web-based driver station and robot-development
workspace. It is not intended to be a traditional dashboard that attempts to
show every robot value at the same time. Instead, it gives the team a stable
control environment where each robot mechanism has focused tools for operating,
testing, tuning, and diagnosing that mechanism.

The application should be useful in both rapid pit work and deliberate robot
development. A student should be able to select a subsystem, open the task they
need, see only the controls and telemetry relevant to that task, and stop the
robot safely at any point.

## The central idea: a toolbox and workbench

Think of the application as a workshop rather than a dashboard:

- The **toolbox** is a fixed left rail. It lists the robot's mechanisms and
  essential system tools.
- The **workbench** is the main content area. It displays the selected,
  purpose-built tool.
- The **safety layer** is always present. It owns connection state, robot
  status, battery information, notifications, and an immediately accessible
  stop action.

The fixed shell makes the app predictable. The workbench makes it adaptable to
the exact robot the team has built this season.

```text
┌─────────────────────┬────────────────────────────────────────────────┐
│ Robot and safety    │ Current tool                                   │
│ Connection / battery│                                                │
│ Stop                │ Custom controls, telemetry, graphs, and steps │
│                     │ for the selected mechanism and task.           │
│ Mechanisms          │                                                │
│ • Drive             │                                                │
│ • Shooter           │                                                │
│ • Intake            │                                                │
│ • Arm               │                                                │
│                     │                                                │
│ System tools        │                                                │
│ • Logs              │                                                │
│ • Settings          │                                                │
└─────────────────────┴────────────────────────────────────────────────┘
```

## Navigation model

The left rail lists high-level robot mechanisms, not generic dashboard pages.
Selecting a mechanism reveals the tasks available for it. For example:

```text
Shooter
├── Test
├── Tune PID
├── Characterize
└── Diagnostics
```

Opening one of these tasks loads its own interface into the workbench. A
Shooter Test tool may have a speed setpoint, feeder controls, and an RPM graph.
An Arm Homing tool may instead be a guarded, step-by-step calibration process.
These tools can share a visual language, but they do not have to share a layout.

The default home state should stay quiet: it can show core connection and safety
information and prompt the user to select a mechanism. It should not try to
summarize every subsystem.

## Mechanisms own tools; tools own their UI

This is the design rule that makes the product extensible:

> **Mechanisms own tools. Tools own their user interface. The app owns the
> shell, safety, navigation, and reusable components.**

For instance, the shooter mechanism may provide Test and Tune PID tools. The
drivetrain may provide a custom TeleOp tool and a characterization tool. The
application does not need special-case knowledge of shooter RPM or arm encoders;
the corresponding tool supplies that knowledge.

When the team adds a new mechanism during the season, the intended workflow is:

1. Expose the mechanism's safe robot-side commands and telemetry.
2. Register the mechanism and its available tools.
3. Build only the task-specific tool panels it needs.

The fixed shell automatically provides navigation, visual consistency, and safe
session handling. Adding a mechanism should not require rewriting a central
dashboard.

## Tool plugin model

Each mechanism is described by a small definition that identifies its label,
tools, UI component, backend command, and supported telemetry. Conceptually:

```ts
registerMechanism({
  id: "shooter",
  label: "Shooter",
  tools: [
    {
      id: "test",
      label: "Test",
      component: ShooterTestTool,
      backendCommand: "shooter.test",
    },
    {
      id: "tune",
      label: "Tune PID",
      component: ShooterTuneTool,
      backendCommand: "shooter.tune",
    },
  ],
});
```

This is a plugin-style architecture within the app, not necessarily a separate
installable plugin system. It gives each robot season the flexibility to add or
remove tools without changing the core application structure.

## Shared building blocks

To keep new tools quick to build, the app should provide reusable primitives:

- Live-value badges and status indicators
- Number inputs, sliders, and bounded setpoint controls
- Time-series charts and telemetry cards
- Enable, disable, and stop controls
- Confirmation dialogs for consequential actions
- Calibration and multi-step procedure layouts
- Manual-control / specialized TeleOp controls
- Consistent error, warning, and connection-state messaging

Most new tools should be compositions of these building blocks. Fully custom
UI is reserved for tasks where a standard layout would obscure the workflow.

## Example: Shooter PID tuning

```text
Shooter — Tune PID

[ Enable tuning ]  [ STOP ]

Target speed: [ 3500 RPM ───────────── ]
P [ 0.12 ]   I [ 0.00 ]   D [ 0.004 ]

┌──────────────── Live RPM vs target graph ────────────────┐
└──────────────────────────────────────────────────────────┘

[ Apply temporarily ]  [ Save constants ]
```

This is not a general dashboard widget. It is a focused, safe workflow designed
for the work being done at the robot.

## Backend and safety model

A running tool communicates with the robot through a typed **command session**:

1. The UI requests a validated action, such as starting Shooter PID Tune.
2. The backend checks preconditions and starts the corresponding robot mode.
3. The robot streams the telemetry the tool requires, such as actual RPM,
   target RPM, motor voltage, and PID terms.
4. The tool renders live controls and telemetry.
5. Pressing Stop, closing the tool, disconnecting, or losing the session always
   executes the tool's defined cleanup action.

Every tool should declare the commands it may invoke, the telemetry it consumes,
its required safety state, and how it stops or cleans up. The backend remains
the authority for validation and safe motor behavior; the UI must never be the
only safety mechanism.

## Product principles

- Focus on the current task, not an overwhelming all-robot overview.
- Make common pit and tuning tasks fast to open and hard to misuse.
- Keep navigation and safety consistent across every custom tool.
- Treat live robot control as session-based and fail-safe.
- Optimize for seasonal change: adding a mechanism should be routine.
- Prefer purpose-built workflows over a collection of generic widgets.
