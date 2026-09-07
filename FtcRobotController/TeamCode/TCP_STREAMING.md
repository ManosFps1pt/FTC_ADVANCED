# TCP streaming in an OpMode

`StructuredRobotDataClient` is the Control Hub-side TCP client. The laptop runs
the listener; during `init()` the Control Hub discovers it with UDP port 5811,
then opens a framed TCP connection to the listener's returned port (5810 by
default). Its worker thread handles discovery, reconnects, and network I/O, so
never perform socket work or sleeps in an OpMode loop.

## Standard pattern

Add the client as an OpMode field. Configure every binding before `start()`,
start it in `init()`, publish exactly once in `loop()`, and close it in
`stop()`.

```java
private final ElapsedTime runtime = new ElapsedTime();
private StructuredRobotDataClient dataClient;

@Override
public void init() {
    // Configure hardware first.
    leftMotor = hardwareMap.get(DcMotorEx.class, "leftMotor");

    dataClient = new StructuredRobotDataClient("My TeleOp")
            .addRuntime(runtime)
            .addMotor("drive.left", "Left Drive Motor", leftMotor,
                    () -> leftCommandedPower)
            .addGamepads(gamepad1, gamepad2);
    dataClient.start();
}

@Override
public void start() {
    runtime.reset();
}

@Override
public void loop() {
    leftCommandedPower = -gamepad1.left_stick_y;
    leftMotor.setPower(leftCommandedPower);

    dataClient.publishLoop(); // exactly once per FTC loop
}

@Override
public void stop() {
    if (dataClient != null) {
        dataClient.close();
        dataClient = null;
    }
}
```

`publishLoop()` automatically emits the configured runtime, voltage, motor,
and gamepad data, plus `opmode.loopTimeMs`. `addGamepads()` is optional.

## Custom data signals

Register each custom signal before `start()`. When using `publishSample()` or
the `publishLoop(values, gamepad1, gamepad2)` overload, the map must include
every registered custom signal exactly once; `opmode.loopTimeMs` is added by
the client.

```java
dataClient = new StructuredRobotDataClient("My TeleOp")
        .addDevice("arm", "Arm", "arm", "motor")
        .addSignal("arm.targetTicks", "Arm Target", "arm", "position",
                "ticks", "int64", "command", 50)
        .addSignal("arm.atTarget", "Arm At Target", "arm", "state",
                "", "boolean", "measured", 50);
dataClient.start();

// Once per loop after computing the values:
Map<String, Object> values = new LinkedHashMap<>();
values.put("arm.targetTicks", (long) targetTicks);
values.put("arm.atTarget", armAtTarget);
dataClient.publishLoop(values, gamepad1, gamepad2);
```

Use `new StructuredRobotDataClient("My TeleOp")` unless the listener uses
non-default ports. The two reference OpModes are `TwoMotorDrivetrain` (motor
data) and `TelemetryGamepadTest` (gamepad-only data).

## PedroPathing localization pose

`addPose()` sends one typed `pose2d` signal named `<deviceId>.pose` on every
`publishLoop()`. It uses PedroPathing coordinates directly: the field is 144
by 144 inches, `(0, 0)` is bottom-left, +X points right, +Y points up, and
heading is radians counter-clockwise from +X. Do not convert a pose that
already came from Pedro or `Follower.getPose()`.

```java
// Most PedroPathing OpModes: follower.getPose() is captured every loop.
dataClient.addPose("localization", "Pinpoint / Pedro Pose", follower);

// Equivalent when your code owns a supplier.
dataClient.addPose("localization", "Pedro Pose", () -> follower.getPose());

// A live x, y, heading sequence; heading must be radians.
dataClient.addPose("localization", "Pinpoint Pose",
        () -> poseX, () -> poseY, () -> poseHeadingRad);

// A custom localizer without a Pedro dependency.
dataClient.addPose("localization", "Custom Pose", () ->
        StructuredRobotDataClient.PoseValue.of(x, y, headingRad));
```

For a fixed diagnostic pose, pass a Pedro `Pose` object directly. The
Telemetry Lab map renders the first measured `pose2d` signal as an 18 by 18
inch oriented robot box and follows the current replay selection.
