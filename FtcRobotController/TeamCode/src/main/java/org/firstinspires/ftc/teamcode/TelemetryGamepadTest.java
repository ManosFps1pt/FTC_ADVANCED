package org.firstinspires.ftc.teamcode;

import com.qualcomm.robotcore.eventloop.opmode.OpMode;
import com.qualcomm.robotcore.eventloop.opmode.TeleOp;
import com.qualcomm.robotcore.util.ElapsedTime;

import org.firstinspires.ftc.teamcode.data.StructuredRobotDataClient;

import java.util.Random;

/**
 * A safe Robot Controller/Driver Station telemetry check with no hardware dependencies.
 *
 * <p>Select {@code Telemetry Gamepad Test} from the Driver Station, press INIT and then
 * START. All values come from the FTC gamepad objects; this OpMode never reads the hardware
 * map or drives a motor, servo, or sensor.</p>
 */
@TeleOp(name = "Telemetry Gamepad Test", group = "Testing")
public class TelemetryGamepadTest extends OpMode {
    private static final double FIELD_SIZE_INCHES = 144.0;
    private static final double ROBOT_HALF_WIDTH_INCHES = 9.0;
    private final ElapsedTime runtime = new ElapsedTime();
    private final Random random = new Random();
    private StructuredRobotDataClient dataClient;
    private double mockX;
    private double mockY;
    private double mockHeadingRad;
    private double lastMockPoseUpdateSeconds = Double.NEGATIVE_INFINITY;

    @Override
    public void init() {
        dataClient = new StructuredRobotDataClient("Telemetry Gamepad Test")
                .addRuntime(runtime)
                .addPose("localization", "Mock Localization", () ->
                        StructuredRobotDataClient.PoseValue.of(mockX, mockY, mockHeadingRad))
                .addGamepads(gamepad1, gamepad2);
        dataClient.start();

        telemetry.setMsTransmissionInterval(50);
        telemetry.addLine("No-hardware telemetry test");
        telemetry.addLine("Press START, then use either gamepad.");
        telemetry.addData("Data TCP target", "discover UDP :%d → TCP :%d",
                StructuredRobotDataClient.DEFAULT_DISCOVERY_PORT,
                StructuredRobotDataClient.DEFAULT_PORT);
        telemetry.update();
    }

    @Override
    public void start() {
        runtime.reset();
        lastMockPoseUpdateSeconds = Double.NEGATIVE_INFINITY;
        updateMockPose();
    }

    @Override
    public void loop() {
        updateMockPose();
        telemetry.addData("runtime (s)", "%.1f", runtime.seconds());
        telemetry.addData("mock pose", "X %.1f  Y %.1f  H %.0f°",
                mockX, mockY, Math.toDegrees(mockHeadingRad));
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

        // Call once per FTC loop. The bound client captures runtime and both gamepads.
        dataClient.publishLoop();
        telemetry.update();
    }

    @Override
    public void stop() {
        if (dataClient != null) {
            dataClient.close();
            dataClient = null;
        }
    }

    private void updateMockPose() {
        if (runtime.seconds() - lastMockPoseUpdateSeconds < 1.0) return;
        mockX = ROBOT_HALF_WIDTH_INCHES + random.nextDouble()
                * (FIELD_SIZE_INCHES - 2.0 * ROBOT_HALF_WIDTH_INCHES);
        mockY = ROBOT_HALF_WIDTH_INCHES + random.nextDouble()
                * (FIELD_SIZE_INCHES - 2.0 * ROBOT_HALF_WIDTH_INCHES);
        mockHeadingRad = random.nextDouble() * 2.0 * Math.PI;
        lastMockPoseUpdateSeconds = runtime.seconds();
    }
}
