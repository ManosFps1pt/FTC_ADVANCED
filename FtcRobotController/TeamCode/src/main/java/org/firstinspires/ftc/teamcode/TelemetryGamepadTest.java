package org.firstinspires.ftc.teamcode;

import android.os.SystemClock;

import com.qualcomm.robotcore.eventloop.opmode.OpMode;
import com.qualcomm.robotcore.eventloop.opmode.TeleOp;
import com.qualcomm.robotcore.util.ElapsedTime;

import org.firstinspires.ftc.teamcode.data.StructuredRobotDataClient;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * A safe Robot Controller/Driver Station telemetry check with no hardware dependencies.
 *
 * <p>Select {@code Telemetry Gamepad Test} from the Driver Station, press INIT and then
 * START. All values come from the FTC gamepad objects; this OpMode never reads the hardware
 * map or drives a motor, servo, or sensor.</p>
 */
@TeleOp(name = "Telemetry Gamepad Test", group = "Testing")
public class TelemetryGamepadTest extends OpMode {
    private static final int DATA_SERVER_PORT = StructuredRobotDataClient.DEFAULT_PORT;
    private static final int DATA_DISCOVERY_PORT = StructuredRobotDataClient.DEFAULT_DISCOVERY_PORT;
    private static final long DATA_PUBLISH_INTERVAL_NS = 20_000_000L; // 50 Hz

    private final ElapsedTime runtime = new ElapsedTime();
    private StructuredRobotDataClient dataClient;
    private long lastDataPublishNs;

    @Override
    public void init() {
        dataClient = new StructuredRobotDataClient(
                DATA_DISCOVERY_PORT,
                DATA_SERVER_PORT,
                "Telemetry Gamepad Test");
        configureTelemetryCatalog(dataClient);
        dataClient.start();

        telemetry.setMsTransmissionInterval(50);
        telemetry.addLine("No-hardware telemetry test");
        telemetry.addLine("Press START, then use either gamepad.");
        telemetry.addData("Data TCP target", "discover UDP :%d → TCP :%d",
                DATA_DISCOVERY_PORT, DATA_SERVER_PORT);
        telemetry.update();
    }

    @Override
    public void start() {
        runtime.reset();
    }

    @Override
    public void loop() {
        double runtimeSeconds = runtime.seconds();
        telemetry.addData("runtime (s)", "%.1f", runtimeSeconds);
        telemetry.addData("gamepad1 sticks", "LX %.2f  LY %.2f  RX %.2f  RY %.2f",
                gamepad1.left_stick_x,
                gamepad1.left_stick_y,
                gamepad1.right_stick_x,
                gamepad1.right_stick_y);
        telemetry.addData("gamepad1 triggers", "L %.2f  R %.2f",
                gamepad1.left_trigger,
                gamepad1.right_trigger);
        telemetry.addData("gamepad1 buttons", "A %b B %b X %b Y %b  dpad UDLR %b%b%b%b",
                gamepad1.a,
                gamepad1.b,
                gamepad1.x,
                gamepad1.y,
                gamepad1.dpad_up,
                gamepad1.dpad_down,
                gamepad1.dpad_left,
                gamepad1.dpad_right);
        telemetry.addData("gamepad2 sticks", "LX %.2f  LY %.2f  RX %.2f  RY %.2f",
                gamepad2.left_stick_x,
                gamepad2.left_stick_y,
                gamepad2.right_stick_x,
                gamepad2.right_stick_y);
        telemetry.addData("gamepad2 buttons", "A %b B %b X %b Y %b",
                gamepad2.a, gamepad2.b, gamepad2.x, gamepad2.y);
        telemetry.addData(
                "Data TCP",
                "%s  queued=%d  dropped=%d",
                dataClient.isConnected() ? "CONNECTED" : "CONNECTING",
                dataClient.getQueuedPacketCount(),
                dataClient.getDroppedPacketCount());
        if (!dataClient.isConnected() && dataClient.getLastError() != null) {
            telemetry.addData("Data TCP error", dataClient.getLastError());
        }

        long now = SystemClock.elapsedRealtimeNanos();
        if (now - lastDataPublishNs >= DATA_PUBLISH_INTERVAL_NS) {
            lastDataPublishNs = now;
            dataClient.publishSample(createDataSnapshot(runtimeSeconds));
        }
        telemetry.update();
    }

    @Override
    public void stop() {
        if (dataClient != null) {
            dataClient.close();
            dataClient = null;
        }
    }

    private Map<String, Object> createDataSnapshot(double runtimeSeconds) {
        Map<String, Object> values = new LinkedHashMap<>();
        values.put("opmode.runtimeSeconds", runtimeSeconds);
        values.put("input.gamepad1.leftStickX", gamepad1.left_stick_x);
        values.put("input.gamepad1.leftStickY", gamepad1.left_stick_y);
        values.put("input.gamepad1.rightStickX", gamepad1.right_stick_x);
        values.put("input.gamepad1.rightStickY", gamepad1.right_stick_y);
        values.put("input.gamepad1.leftTrigger", gamepad1.left_trigger);
        values.put("input.gamepad1.rightTrigger", gamepad1.right_trigger);
        values.put("input.gamepad1.buttonA", gamepad1.a);
        values.put("input.gamepad1.buttonB", gamepad1.b);
        values.put("input.gamepad1.buttonX", gamepad1.x);
        values.put("input.gamepad1.buttonY", gamepad1.y);
        values.put("input.gamepad2.leftStickX", gamepad2.left_stick_x);
        values.put("input.gamepad2.leftStickY", gamepad2.left_stick_y);
        values.put("input.gamepad2.rightStickX", gamepad2.right_stick_x);
        values.put("input.gamepad2.rightStickY", gamepad2.right_stick_y);
        values.put("input.gamepad2.buttonA", gamepad2.a);
        values.put("input.gamepad2.buttonB", gamepad2.b);
        values.put("input.gamepad2.buttonX", gamepad2.x);
        values.put("input.gamepad2.buttonY", gamepad2.y);
        return values;
    }

    private static void configureTelemetryCatalog(StructuredRobotDataClient client) {
        client.addDevice("input.gamepad1", "Gamepad 1", "input", "gamepad")
                .addDevice("input.gamepad2", "Gamepad 2", "input", "gamepad")
                .addSignal("opmode.runtimeSeconds", "OpMode Runtime", null, "runtime", "s", "float64", "diagnostic", 50)
                .addSignal("input.gamepad1.leftStickX", "Gamepad 1 Left Stick X", "input.gamepad1", "leftStickX", "percent", "float64", "measured", 50)
                .addSignal("input.gamepad1.leftStickY", "Gamepad 1 Left Stick Y", "input.gamepad1", "leftStickY", "percent", "float64", "measured", 50)
                .addSignal("input.gamepad1.rightStickX", "Gamepad 1 Right Stick X", "input.gamepad1", "rightStickX", "percent", "float64", "measured", 50)
                .addSignal("input.gamepad1.rightStickY", "Gamepad 1 Right Stick Y", "input.gamepad1", "rightStickY", "percent", "float64", "measured", 50)
                .addSignal("input.gamepad1.leftTrigger", "Gamepad 1 Left Trigger", "input.gamepad1", "leftTrigger", "percent", "float64", "measured", 50)
                .addSignal("input.gamepad1.rightTrigger", "Gamepad 1 Right Trigger", "input.gamepad1", "rightTrigger", "percent", "float64", "measured", 50)
                .addSignal("input.gamepad1.buttonA", "Gamepad 1 A", "input.gamepad1", "buttonA", "none", "boolean", "measured", 50)
                .addSignal("input.gamepad1.buttonB", "Gamepad 1 B", "input.gamepad1", "buttonB", "none", "boolean", "measured", 50)
                .addSignal("input.gamepad1.buttonX", "Gamepad 1 X", "input.gamepad1", "buttonX", "none", "boolean", "measured", 50)
                .addSignal("input.gamepad1.buttonY", "Gamepad 1 Y", "input.gamepad1", "buttonY", "none", "boolean", "measured", 50)
                .addSignal("input.gamepad2.leftStickX", "Gamepad 2 Left Stick X", "input.gamepad2", "leftStickX", "percent", "float64", "measured", 50)
                .addSignal("input.gamepad2.leftStickY", "Gamepad 2 Left Stick Y", "input.gamepad2", "leftStickY", "percent", "float64", "measured", 50)
                .addSignal("input.gamepad2.rightStickX", "Gamepad 2 Right Stick X", "input.gamepad2", "rightStickX", "percent", "float64", "measured", 50)
                .addSignal("input.gamepad2.rightStickY", "Gamepad 2 Right Stick Y", "input.gamepad2", "rightStickY", "percent", "float64", "measured", 50)
                .addSignal("input.gamepad2.buttonA", "Gamepad 2 A", "input.gamepad2", "buttonA", "none", "boolean", "measured", 50)
                .addSignal("input.gamepad2.buttonB", "Gamepad 2 B", "input.gamepad2", "buttonB", "none", "boolean", "measured", 50)
                .addSignal("input.gamepad2.buttonX", "Gamepad 2 X", "input.gamepad2", "buttonX", "none", "boolean", "measured", 50)
                .addSignal("input.gamepad2.buttonY", "Gamepad 2 Y", "input.gamepad2", "buttonY", "none", "boolean", "measured", 50);
    }
}
