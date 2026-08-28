package org.firstinspires.ftc.teamcode;

import com.qualcomm.hardware.limelightvision.LLResult;
import com.qualcomm.hardware.limelightvision.LLStatus;
import com.qualcomm.hardware.limelightvision.Limelight3A;
import com.qualcomm.robotcore.eventloop.opmode.LinearOpMode;
import com.qualcomm.robotcore.eventloop.opmode.TeleOp;
import com.qualcomm.robotcore.util.ElapsedTime;

import org.firstinspires.ftc.teamcode.data.StructuredRobotDataClient;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Read-only Limelight communication test that publishes tx and ty to FTC telemetry and the dashboard.
 *
 * <p>The configured device is currently named {@code Ethernet Device}. If its name changes in the
 * Robot Controller configuration, update {@link #LIMELIGHT_NAME} to match exactly.</p>
 */
@TeleOp(name = "Limelight TX/TY Telemetry", group = "Testing")
public final class LimelightTxTyTelemetry extends LinearOpMode {
    private static final String LIMELIGHT_NAME = "limelight";
    private static final int PIPELINE_INDEX = 0;

    private final ElapsedTime runtime = new ElapsedTime();
    private StructuredRobotDataClient dataClient;

    @Override
    public void runOpMode() throws InterruptedException {
        Limelight3A limelight;
        try {
            limelight = hardwareMap.get(Limelight3A.class, LIMELIGHT_NAME);
        } catch (RuntimeException error) {
            telemetry.addData("Limelight configuration error",
                    "No Limelight3A named '%s'", LIMELIGHT_NAME);
            telemetry.addData("Detail", error.getMessage());
            telemetry.update();
            return;
        }

        dataClient = new StructuredRobotDataClient(
                StructuredRobotDataClient.DEFAULT_DISCOVERY_PORT,
                StructuredRobotDataClient.DEFAULT_PORT,
                "Limelight TX/TY Telemetry");
        configureTelemetryCatalog(dataClient);
        dataClient.start();

        telemetry.setMsTransmissionInterval(50);
        limelight.pipelineSwitch(PIPELINE_INDEX);
        limelight.start();

        telemetry.addLine("Limelight started.");
        telemetry.addData("Configured device", LIMELIGHT_NAME);
        telemetry.addData("Pipeline", PIPELINE_INDEX);
        telemetry.addLine("Press START. This OpMode does not control robot hardware.");
        telemetry.update();

        waitForStart();
        runtime.reset();

        try {
            while (opModeIsActive()) {
                LLResult result = limelight.getLatestResult();
                boolean validTarget = result != null && result.isValid();
                // Zero is a placeholder only when validTarget is false. Always inspect validTarget
                // alongside tx and ty so a no-target frame is never mistaken for a centered target.
                double tx = validTarget ? result.getTx() : 0.0;
                double ty = validTarget ? result.getTy() : 0.0;
                double latencyMs = validTarget
                        ? result.getCaptureLatency() + result.getTargetingLatency()
                        : 0.0;

                LLStatus status = limelight.getStatus();
                telemetry.addData("Limelight name", status.getName());
                telemetry.addData("Pipeline", "%d (%s)",
                        status.getPipelineIndex(), status.getPipelineType());
                telemetry.addData("Target valid", validTarget);
                telemetry.addData("tx (degrees)", "%.2f", tx);
                telemetry.addData("ty (degrees)", "%.2f", ty);
                telemetry.addData("Latency (ms)", "%.1f", latencyMs);
                telemetry.addData("Camera", "FPS %d  Temp %.1f C",
                        (int) status.getFps(), status.getTemp());
                telemetry.addData("Dashboard link", dataClient.isConnected()
                        ? "CONNECTED" : "CONNECTING");
                if (!dataClient.isConnected() && dataClient.getLastError() != null) {
                    telemetry.addData("Dashboard error", dataClient.getLastError());
                }

                dataClient.publishLoop(createDataSnapshot(
                        runtime.seconds(), validTarget, tx, ty, latencyMs, status), gamepad1, gamepad2);
                telemetry.update();
                idle();
            }
        } finally {
            limelight.stop();
            dataClient.close();
            dataClient = null;
        }
    }

    private static Map<String, Object> createDataSnapshot(
            double runtimeSeconds,
            boolean validTarget,
            double tx,
            double ty,
            double latencyMs,
            LLStatus status) {
        Map<String, Object> values = new LinkedHashMap<>();
        values.put("opmode.runtimeSeconds", runtimeSeconds);
        values.put("vision.limelight.targetValid", validTarget);
        values.put("vision.limelight.txDegrees", tx);
        values.put("vision.limelight.tyDegrees", ty);
        values.put("vision.limelight.latencyMs", latencyMs);
        values.put("vision.limelight.pipelineIndex", status.getPipelineIndex());
        values.put("vision.limelight.fps", status.getFps());
        return values;
    }

    private static void configureTelemetryCatalog(StructuredRobotDataClient client) {
        client.addDevice("vision.limelight", "Limelight 3A", "vision", "camera")
                .addSignal("opmode.runtimeSeconds", "OpMode Runtime", null, "runtime", "s",
                        "float64", "diagnostic", 50)
                .addSignal("vision.limelight.targetValid", "Target Valid", "vision.limelight",
                        "targetValid", "none", "boolean", "measured", 50)
                .addSignal("vision.limelight.txDegrees", "Limelight TX", "vision.limelight",
                        "tx", "degrees", "float64", "measured", 50)
                .addSignal("vision.limelight.tyDegrees", "Limelight TY", "vision.limelight",
                        "ty", "degrees", "float64", "measured", 50)
                .addSignal("vision.limelight.latencyMs", "Limelight Latency", "vision.limelight",
                        "latency", "ms", "float64", "measured", 50)
                .addSignal("vision.limelight.pipelineIndex", "Limelight Pipeline", "vision.limelight",
                        "pipelineIndex", "none", "int64", "measured", 50)
                .addSignal("vision.limelight.fps", "Limelight FPS", "vision.limelight",
                        "fps", "frames/s", "float64", "measured", 50);
    }
}
