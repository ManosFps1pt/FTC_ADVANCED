package org.firstinspires.ftc.teamcode;

import android.app.Activity;
import android.graphics.Color;
import android.os.SystemClock;
import android.view.View;

import com.qualcomm.robotcore.eventloop.opmode.OpMode;
import com.qualcomm.robotcore.eventloop.opmode.TeleOp;
import com.qualcomm.robotcore.util.ElapsedTime;

import org.firstinspires.ftc.teamcode.data.StructuredRobotDataClient;
import org.firstinspires.ftc.teamcode.data.protocol.DebugCommand;
import org.firstinspires.ftc.teamcode.data.protocol.DebugCommandArgumentDefinition;
import org.firstinspires.ftc.teamcode.data.protocol.DebugCommandArgumentValue;
import org.firstinspires.ftc.teamcode.data.protocol.DebugCommandRequest;
import org.firstinspires.ftc.teamcode.data.protocol.DebugCommandResponse;
import org.firstinspires.ftc.teamcode.data.protocol.DebugCommandResult;
import org.firstinspires.ftc.teamcode.data.protocol.DebugManifest;
import org.firstinspires.ftc.teamcode.data.protocol.DebugNode;
import org.firstinspires.ftc.teamcode.data.protocol.DebugNodeKind;
import org.firstinspires.ftc.teamcode.data.protocol.DebugRiskClass;
import org.firstinspires.ftc.teamcode.data.protocol.DebugSessionState;
import org.firstinspires.ftc.teamcode.data.protocol.DebugToolReady;
import org.firstinspires.ftc.teamcode.data.protocol.Envelope;
import org.firstinspires.ftc.teamcode.data.protocol.ValueType;

import java.util.UUID;

/**
 * A safe Robot Controller/Driver Station telemetry check with no hardware dependencies.
 *
 * <p>Select {@code Telemetry Gamepad Test} from the Driver Station, press INIT and then
 * START. All values come from the FTC gamepad objects; this OpMode never reads the hardware
 * map or drives a motor, servo, or sensor.</p>
 */
@TeleOp(name = "Telemetry Gamepad Test", group = "Testing")
public class TelemetryGamepadTest extends OpMode {
    private static final int MANIFEST_REVISION = 1;
    private static final String NODE_ID = "telemetry.gamepad";
    private static final String COMMAND_SET_ALLIANCE = "alliance.set";
    private static final String ARGUMENT_IS_RED = "isRed";
    private static final int DATA_SERVER_PORT = StructuredRobotDataClient.DEFAULT_PORT;
    private static final int DATA_DISCOVERY_PORT = StructuredRobotDataClient.DEFAULT_DISCOVERY_PORT;
    private final ElapsedTime runtime = new ElapsedTime();
    private StructuredRobotDataClient dataClient;
    private View relativeLayout;
    private String toolInstanceId;
    private boolean allianceIsRed;

    @Override
    public void init() {
        toolInstanceId = UUID.randomUUID().toString();
        int relativeLayoutId = hardwareMap.appContext.getResources().getIdentifier(
                "RelativeLayout", "id", hardwareMap.appContext.getPackageName());
        relativeLayout = ((Activity) hardwareMap.appContext).findViewById(relativeLayoutId);
        setAlliance(false);

        dataClient = new StructuredRobotDataClient(
                DATA_DISCOVERY_PORT,
                DATA_SERVER_PORT,
                "Telemetry Gamepad Test")
                .addRuntime(runtime)
                .addGamepads(gamepad1, gamepad2);
        dataClient.start();
        publishCommandCatalog();

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
        processIncomingMessages();
        telemetry.addData("runtime (s)", "%.1f", runtime.seconds());
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

        // Keep this diagnostic OpMode at a human-scale cadence so its graphs
        // remain easy to inspect without producing hundreds of samples a second.
        try {
            Thread.sleep(10);
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        }
    }

    @Override
    public void stop() {
        if (relativeLayout != null) {
            relativeLayout.post(() -> relativeLayout.setBackgroundColor(Color.WHITE));
        }
        if (dataClient != null) {
            dataClient.close();
            dataClient = null;
        }
    }

    private void publishCommandCatalog() {
        DebugNode node = DebugNode.newBuilder()
                .setId(NODE_ID)
                .setLabel("Telemetry Gamepad Test")
                .setDescription("Gamepad telemetry with a safe alliance background command")
                .setKind(DebugNodeKind.DEBUG_TOOL)
                .setSortOrder(0)
                .setRiskClass(DebugRiskClass.DEBUG_OBSERVE_ONLY)
                .setOpModeLabel("Telemetry Gamepad Test")
                .setSelectable(true)
                .setEnabled(true)
                .addCommandIds(COMMAND_SET_ALLIANCE)
                .build();
        dataClient.publishMessage(Envelope.newBuilder().setDebugManifest(DebugManifest.newBuilder()
                .setRevision(MANIFEST_REVISION)
                .setRegistryId("telemetry-gamepad-test")
                .setRegistryLabel("Telemetry Gamepad Test")
                .addNodes(node)));

        DebugCommand setAlliance = DebugCommand.newBuilder()
                .setId(COMMAND_SET_ALLIANCE)
                .setLabel("Set Alliance")
                .setDescription("Set the Control Hub background to red or blue")
                .setRequiresHumanAcknowledgement(false)
                .addArguments(DebugCommandArgumentDefinition.newBuilder()
                        .setId(ARGUMENT_IS_RED)
                        .setLabel("Red alliance")
                        .setDescription("True for red alliance, false for blue alliance")
                        .setValueType(ValueType.BOOLEAN)
                        .setRequired(true))
                .build();
        dataClient.publishMessage(Envelope.newBuilder().setDebugToolReady(DebugToolReady.newBuilder()
                .setNodeId(NODE_ID)
                .setToolInstanceId(toolInstanceId)
                .setManifestRevision(MANIFEST_REVISION)
                .setState(DebugSessionState.DEBUG_READY)
                .setMessage("Telemetry command ready")
                .addCommands(setAlliance)));
    }

    private void processIncomingMessages() {
        if (dataClient == null) return;
        StructuredRobotDataClient.IncomingMessage incomingMessage;
        int processed = 0;
        while (processed++ < 8 && (incomingMessage = dataClient.pollIncomingMessage()) != null) {
            Envelope message = incomingMessage.envelope();
            if (message.getBodyCase() == Envelope.BodyCase.DEBUG_COMMAND_REQUEST) {
                handleCommand(message.getDebugCommandRequest(), incomingMessage.receivedRobotTimeNs());
            }
        }
    }

    private void handleCommand(DebugCommandRequest request, long receivedRobotTimeNs) {
        if (!NODE_ID.equals(request.getNodeId()) || !toolInstanceId.equals(request.getToolInstanceId())) {
            sendCommandResponse(request, DebugCommandResult.DEBUG_COMMAND_REJECTED,
                    "TOOL_INSTANCE_MISMATCH", "No matching telemetry tool instance");
            return;
        }
        if (!COMMAND_SET_ALLIANCE.equals(request.getCommandId())) {
            sendCommandResponse(request, DebugCommandResult.DEBUG_COMMAND_REJECTED,
                    "UNKNOWN_COMMAND", "Command is not registered by this OpMode");
            return;
        }
        if (request.getTtlMs() == 0 || request.getTtlMs() > 10_000) {
            sendCommandResponse(request, DebugCommandResult.DEBUG_COMMAND_REJECTED,
                    "INVALID_TTL", "TTL must be between 1 and 10000 ms");
            return;
        }
        if (SystemClock.elapsedRealtimeNanos() - receivedRobotTimeNs > request.getTtlMs() * 1_000_000L) {
            sendCommandResponse(request, DebugCommandResult.DEBUG_COMMAND_EXPIRED,
                    "TTL_EXPIRED", "Command expired before execution");
            return;
        }
        if (request.getArgumentsCount() != 1
                || !ARGUMENT_IS_RED.equals(request.getArguments(0).getId())
                || request.getArguments(0).getValueCase()
                != DebugCommandArgumentValue.ValueCase.BOOLEAN_VALUE) {
            sendCommandResponse(request, DebugCommandResult.DEBUG_COMMAND_REJECTED,
                    "INVALID_ARGUMENTS", "alliance.set requires boolean argument isRed");
            return;
        }

        setAlliance(request.getArguments(0).getBooleanValue());
        sendCommandResponse(request, DebugCommandResult.DEBUG_COMMAND_COMPLETED, "",
                allianceIsRed ? "Alliance set to RED" : "Alliance set to BLUE");
    }

    private void sendCommandResponse(DebugCommandRequest request, DebugCommandResult result,
                                     String rejectionCode, String message) {
        if (dataClient == null) return;
        dataClient.publishMessage(Envelope.newBuilder().setDebugCommandResponse(
                DebugCommandResponse.newBuilder()
                        .setRequestId(request.getRequestId())
                        .setCommandId(request.getCommandId())
                        .setResult(result)
                        .setRejectionCode(rejectionCode)
                        .setMessage(message)
                        .setHandledAtRobotTimeNs(SystemClock.elapsedRealtimeNanos())));
    }

    private void setAlliance(boolean isRed) {
        allianceIsRed = isRed;
        if (relativeLayout != null) {
            int color = isRed ? Color.RED : Color.BLUE;
            relativeLayout.post(() -> relativeLayout.setBackgroundColor(color));
        }
    }

}
