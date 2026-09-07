package org.firstinspires.ftc.teamcode;

import android.os.SystemClock;

import com.qualcomm.robotcore.eventloop.opmode.OpMode;
import com.qualcomm.robotcore.hardware.DcMotor;
import com.qualcomm.robotcore.hardware.HardwareMap;
import com.qualcomm.robotcore.util.Range;

import org.firstinspires.ftc.robotcore.external.Telemetry;

import org.firstinspires.ftc.teamcode.data.StructuredRobotDataClient;
import org.firstinspires.ftc.teamcode.data.protocol.DebugCommand;
import org.firstinspires.ftc.teamcode.data.protocol.DebugCommandRequest;
import org.firstinspires.ftc.teamcode.data.protocol.DebugCommandResponse;
import org.firstinspires.ftc.teamcode.data.protocol.DebugCommandResult;
import org.firstinspires.ftc.teamcode.data.protocol.DebugManifest;
import org.firstinspires.ftc.teamcode.data.protocol.DebugNode;
import org.firstinspires.ftc.teamcode.data.protocol.DebugNodeKind;
import org.firstinspires.ftc.teamcode.data.protocol.DebugParameter;
import org.firstinspires.ftc.teamcode.data.protocol.DebugParameterSetRequest;
import org.firstinspires.ftc.teamcode.data.protocol.DebugParameterSetResponse;
import org.firstinspires.ftc.teamcode.data.protocol.DebugParameterSetResult;
import org.firstinspires.ftc.teamcode.data.protocol.DebugParameterUpdatePolicy;
import org.firstinspires.ftc.teamcode.data.protocol.DebugRiskClass;
import org.firstinspires.ftc.teamcode.data.protocol.DebugSafetyState;
import org.firstinspires.ftc.teamcode.data.protocol.DebugSelectRequest;
import org.firstinspires.ftc.teamcode.data.protocol.DebugSelectResponse;
import org.firstinspires.ftc.teamcode.data.protocol.DebugSessionState;
import org.firstinspires.ftc.teamcode.data.protocol.DebugToolReady;
import org.firstinspires.ftc.teamcode.data.protocol.DebugToolState;
import org.firstinspires.ftc.teamcode.data.protocol.Envelope;
import org.firstinspires.ftc.teamcode.data.protocol.ValueType;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.function.Consumer;

/**
 * Base OpMode for the FTC Advanced remote debugging workbench.
 *
 * <p>The Driver Station starts this one physical OpMode. The laptop then uses
 * the structured TCP connection to select and control a registered tool. TCP
 * requests are queued by StructuredRobotDataClient and applied here, on the
 * FTC OpMode loop.</p>
 */
public abstract class SelectableOpMode extends OpMode {
    private static final int MANIFEST_REVISION = 1;
    private static final String REGISTRY_ID = "ftc-advanced-debugger";

    private final Registry registry = new Registry();
    private StructuredRobotDataClient dataClient;
    private Tool activeTool;
    private Node activeNode;
    private String toolInstanceId = "";
    private DebugSessionState state = DebugSessionState.DEBUG_IDLE;
    private long loopCount;
    private long connectionGeneration;

    /** Subclasses register folders and leaf tools here. */
    protected abstract void configure(Registry registry);

    @Override
    public final void init() {
        configure(registry);
        dataClient = new StructuredRobotDataClient(
                StructuredRobotDataClient.DEFAULT_DISCOVERY_PORT,
                StructuredRobotDataClient.DEFAULT_PORT,
                "FTC Advanced Debugger");
        configureTelemetry(dataClient);
        dataClient.enableBenchmarks();
        dataClient.start();
        dataClient.publishMessage(Envelope.newBuilder().setDebugManifest(manifest()));
        dataClient.publishMessage(Envelope.newBuilder().setDebugSafetyState(
                DebugSafetyState.newBuilder().setTcpConnected(false).setWatchdogHealthy(true).setOutputAllowed(false)
                        .setBlockedReason("No debug tool selected")));
        telemetry.addLine("FTC Advanced Debugger ready");
        telemetry.addData("TCP", "connecting to laptop");
        telemetry.addData("Tools", registry.tools().size());
        telemetry.update();
    }

    @Override
    public final void start() {
        state = DebugSessionState.DEBUG_IDLE;
    }

    @Override
    public final void loop() {
        if (dataClient == null) return;
        if (dataClient.isConnected() && connectionGeneration != dataClient.connectionGeneration()) {
            connectionGeneration=dataClient.connectionGeneration();
            send(Envelope.newBuilder().setDebugManifest(manifest()));
            if (activeNode != null) send(Envelope.newBuilder().setDebugToolReady(toolReady(activeNode)));
        }
        processIncomingMessages();
        if (activeTool != null) {
            try {
                activeTool.loop();
            } catch (RuntimeException error) {
                String nodeId = activeNode == null ? "" : activeNode.id;
                String instanceId = toolInstanceId;
                String detail = error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage();
                stopActiveTool();
                state = DebugSessionState.DEBUG_FAULTED;
                telemetry.addData("Debug tool fault", detail);
                sendFaultState(nodeId, instanceId, detail);
            }
        }
        publishTelemetry();
        loopCount++;
    }

    @Override
    public final void stop() {
        stopActiveTool();
        if (dataClient != null) {
            dataClient.close();
            dataClient = null;
        }
    }

    private void processIncomingMessages() {
        StructuredRobotDataClient.IncomingMessage incomingMessage;
        int processed = 0;
        while (processed++ < 16 && (incomingMessage = dataClient.pollIncomingMessage()) != null) {
            Envelope message = incomingMessage.envelope();
            switch (message.getBodyCase()) {
                case DEBUG_SELECT_REQUEST:
                    handleSelect(message.getDebugSelectRequest());
                    break;
                case DEBUG_PARAMETER_SET_REQUEST:
                    handleParameterSet(message.getDebugParameterSetRequest(), incomingMessage.receivedRobotTimeNs());
                    break;
                case DEBUG_COMMAND_REQUEST:
                    handleCommand(message.getDebugCommandRequest(), incomingMessage.receivedRobotTimeNs());
                    break;
                default:
                    if (activeTool != null) activeTool.receive(message);
                    break;
            }
        }
    }

    private void handleSelect(DebugSelectRequest request) {
        if (activeTool != null && activeTool.busy()) {
            send(Envelope.newBuilder().setDebugSelectResponse(DebugSelectResponse.newBuilder()
                    .setRequestId(request.getRequestId()).setAccepted(false).setRejectionCode("BUSY")
                    .setMessage("Finish or abort the current benchmark and receive its data first")));
            return;
        }
        Node node = registry.node(request.getNodeId());
        if (request.getManifestRevision() != MANIFEST_REVISION) {
            send(Envelope.newBuilder().setDebugSelectResponse(DebugSelectResponse.newBuilder()
                    .setRequestId(request.getRequestId()).setNodeId(request.getNodeId()).setAccepted(false)
                    .setState(state).setRejectionCode("STALE_MANIFEST").setMessage("Debugger manifest revision is stale")));
            return;
        }
        if (node == null || node.kind != DebugNodeKind.DEBUG_TOOL) {
            send(Envelope.newBuilder().setDebugSelectResponse(DebugSelectResponse.newBuilder()
                    .setRequestId(request.getRequestId()).setNodeId(request.getNodeId()).setAccepted(false)
                    .setState(state).setRejectionCode("UNKNOWN_TOOL").setMessage("The selected node is not a tool")));
            return;
        }
        if (!node.enabled || node.factory == null) {
            send(Envelope.newBuilder().setDebugSelectResponse(DebugSelectResponse.newBuilder()
                    .setRequestId(request.getRequestId()).setNodeId(request.getNodeId()).setAccepted(false)
                    .setState(state).setRejectionCode("TOOL_DISABLED").setMessage(node.disabledReason)));
            return;
        }

        stopActiveTool();
        try {
            activeNode = node;
            activeTool = node.factory.create();
            toolInstanceId = UUID.randomUUID().toString();
            activeTool.init(new ToolContext(hardwareMap, telemetry, node.id, toolInstanceId, dataClient));
            activeTool.start();
            state = DebugSessionState.DEBUG_READY;
            send(Envelope.newBuilder().setDebugSelectResponse(DebugSelectResponse.newBuilder()
                    .setRequestId(request.getRequestId()).setNodeId(node.id).setAccepted(true)
                    .setState(state).setMessage("Tool selected")));
            send(Envelope.newBuilder().setDebugToolReady(toolReady(node)));
            sendToolState();
        } catch (Exception error) {
            String detail = error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage();
            stopActiveTool();
            state = DebugSessionState.DEBUG_FAULTED;
            send(Envelope.newBuilder().setDebugSelectResponse(DebugSelectResponse.newBuilder()
                    .setRequestId(request.getRequestId()).setNodeId(node.id).setAccepted(false)
                    .setState(state).setRejectionCode("TOOL_INIT_FAILED")
                    .setMessage(detail)));
            sendFaultState(node.id, "", detail);
        }
    }

    private void handleParameterSet(DebugParameterSetRequest request, long receivedRobotTimeNs) {
        if (activeTool == null || activeNode == null || !activeNode.id.equals(request.getNodeId())
                || !toolInstanceId.equals(request.getToolInstanceId())) {
            send(Envelope.newBuilder().setDebugParameterSetResponse(parameterResponse(request,
                    DebugParameterSetResult.DEBUG_PARAMETER_REJECTED,
                    0.0, "TOOL_INSTANCE_MISMATCH", "No matching active debug tool")));
            return;
        }
        if (request.getTtlMs() == 0 || request.getTtlMs() > 10_000) {
            send(Envelope.newBuilder().setDebugParameterSetResponse(parameterResponse(request,
                    DebugParameterSetResult.DEBUG_PARAMETER_REJECTED,
                    0.0, "INVALID_TTL", "TTL must be between 1 and 10000 ms")));
            return;
        }
        if (SystemClock.elapsedRealtimeNanos() - receivedRobotTimeNs > request.getTtlMs() * 1_000_000L) {
            send(Envelope.newBuilder().setDebugParameterSetResponse(parameterResponse(request,
                    DebugParameterSetResult.DEBUG_PARAMETER_EXPIRED,
                    0.0, "TTL_EXPIRED", "Parameter update expired before execution")));
            return;
        }
        ParameterResult result = activeTool.setParameter(request.getParameterId(), request.getFloat64Value(), request.getTtlMs());
        send(Envelope.newBuilder().setDebugParameterSetResponse(parameterResponse(request,
                result.accepted ? DebugParameterSetResult.DEBUG_PARAMETER_APPLIED : DebugParameterSetResult.DEBUG_PARAMETER_REJECTED,
                result.effectiveValue, result.code, result.message)));
        sendToolState();
    }

    private void handleCommand(DebugCommandRequest request, long receivedRobotTimeNs) {
        if (activeTool == null || activeNode == null || !activeNode.id.equals(request.getNodeId())
                || !toolInstanceId.equals(request.getToolInstanceId())) {
            send(Envelope.newBuilder().setDebugCommandResponse(DebugCommandResponse.newBuilder()
                    .setRequestId(request.getRequestId()).setCommandId(request.getCommandId())
                    .setResult(DebugCommandResult.DEBUG_COMMAND_REJECTED)
                    .setRejectionCode("TOOL_INSTANCE_MISMATCH").setMessage("No matching active debug tool")));
            return;
        }
        if (request.getTtlMs() == 0 || request.getTtlMs() > 10_000) {
            send(Envelope.newBuilder().setDebugCommandResponse(DebugCommandResponse.newBuilder()
                    .setRequestId(request.getRequestId()).setCommandId(request.getCommandId())
                    .setResult(DebugCommandResult.DEBUG_COMMAND_REJECTED)
                    .setRejectionCode("INVALID_TTL").setMessage("TTL must be between 1 and 10000 ms")
                    .setHandledAtRobotTimeNs(SystemClock.elapsedRealtimeNanos())));
            return;
        }
        if (SystemClock.elapsedRealtimeNanos() - receivedRobotTimeNs > request.getTtlMs() * 1_000_000L) {
            send(Envelope.newBuilder().setDebugCommandResponse(DebugCommandResponse.newBuilder()
                    .setRequestId(request.getRequestId()).setCommandId(request.getCommandId())
                    .setResult(DebugCommandResult.DEBUG_COMMAND_EXPIRED)
                    .setRejectionCode("TTL_EXPIRED").setMessage("Command expired before execution")
                    .setHandledAtRobotTimeNs(SystemClock.elapsedRealtimeNanos())));
            return;
        }
        if ("stop".equals(request.getCommandId()) || "motor.stop".equals(request.getCommandId())) {
            activeTool.stopOutput();
            send(Envelope.newBuilder().setDebugCommandResponse(DebugCommandResponse.newBuilder()
                    .setRequestId(request.getRequestId()).setCommandId(request.getCommandId())
                    .setResult(DebugCommandResult.DEBUG_COMMAND_COMPLETED).setMessage("Output stopped")
                    .setHandledAtRobotTimeNs(SystemClock.elapsedRealtimeNanos())));
            sendToolState();
            return;
        }
        DebugCommandResponse response=activeTool.command(request);
        if (response != null) { send(Envelope.newBuilder().setDebugCommandResponse(response)); return; }
        send(Envelope.newBuilder().setDebugCommandResponse(DebugCommandResponse.newBuilder()
                .setRequestId(request.getRequestId()).setCommandId(request.getCommandId())
                .setResult(DebugCommandResult.DEBUG_COMMAND_REJECTED)
                .setRejectionCode("UNKNOWN_COMMAND").setMessage("Command is not registered by the active tool")));
    }

    private DebugParameterSetResponse.Builder parameterResponse(DebugParameterSetRequest request,
                                                                  DebugParameterSetResult result,
                                                                  double effectiveValue,
                                                                  String code,
                                                                  String message) {
        return DebugParameterSetResponse.newBuilder().setRequestId(request.getRequestId()).setParameterId(request.getParameterId())
                .setResult(result).setEffectiveFloat64(effectiveValue).setRejectionCode(code == null ? "" : code)
                .setMessage(message == null ? "" : message).setAppliedAtRobotTimeNs(SystemClock.elapsedRealtimeNanos());
    }

    private void publishTelemetry() {
        // Benchmark samples stay on the robot until execution ends. Only status is live.
        if (activeTool != null && !activeTool.benchmarks().isEmpty()) {
            if (loopCount % 10 == 0) sendToolState();
            telemetry.addData("Selected tool", activeNode.label);
            telemetry.addData("TCP", dataClient.isConnected() ? "connected" : "connecting");
            telemetry.update();
            return;
        }
        Map<String, Object> values = new LinkedHashMap<>();
        values.put("debug.selectedTool", activeNode == null ? "" : activeNode.id);
        values.put("debug.motor.requestedPower", activeTool == null ? 0.0 : activeTool.requestedOutput());
        values.put("debug.motor.appliedPower", activeTool == null ? 0.0 : activeTool.appliedOutput());
        values.put("debug.motor.encoderPosition", activeTool == null ? 0L : activeTool.encoderPosition());
        values.put("debug.safety.outputAllowed", activeTool != null && activeTool.outputAllowed());
        values.put("debug.safety.watchdogHealthy", activeTool == null || activeTool.watchdogHealthy());
        dataClient.publishSample(values);
        if (loopCount % 5 == 0) sendToolState();
        telemetry.addData("Debug state", state);
        telemetry.addData("Selected tool", activeNode == null ? "none" : activeNode.label);
        telemetry.addData("TCP", dataClient.isConnected() ? "connected" : "connecting");
        telemetry.update();
    }

    private void configureTelemetry(StructuredRobotDataClient client) {
        client.addDevice("debug.motor", "Debug Motor", "debug", "dcMotor")
                .addSignal("debug.selectedTool", "Selected Tool", null, "tool", "none", "string", "status", 20)
                .addSignal("debug.motor.requestedPower", "Requested Power", "debug.motor", "power", "normalized", "float64", "command", 20)
                .addSignal("debug.motor.appliedPower", "Applied Power", "debug.motor", "power", "normalized", "float64", "measured", 20)
                .addSignal("debug.motor.encoderPosition", "Encoder Position", "debug.motor", "position", "tick", "int64", "measured", 20)
                .addSignal("debug.safety.outputAllowed", "Output Allowed", null, "outputAllowed", "none", "boolean", "status", 20)
                .addSignal("debug.safety.watchdogHealthy", "Watchdog Healthy", null, "watchdogHealthy", "none", "boolean", "status", 20);
    }

    private DebugManifest manifest() {
        DebugManifest.Builder result = DebugManifest.newBuilder().setRevision(MANIFEST_REVISION)
                .setRegistryId(REGISTRY_ID).setRegistryLabel("FTC Advanced Debugger");
        for (Node node : registry.nodes) result.addNodes(node.toProto());
        return result.build();
    }

    private DebugToolReady toolReady(Node node) {
        DebugToolReady.Builder result = DebugToolReady.newBuilder().setNodeId(node.id).setToolInstanceId(toolInstanceId)
                .setManifestRevision(MANIFEST_REVISION).setState(DebugSessionState.DEBUG_READY).setMessage("Tool ready");
        for (Parameter parameter : node.parameters) result.addParameters(parameter.toProto());
        for (Command command : node.commands) result.addCommands(command.toProto());
        if (activeTool != null) result.addAllBenchmarks(activeTool.benchmarks());
        return result.build();
    }

    private void sendToolState() {
        if (dataClient == null) return;
        boolean allowed = activeTool != null && activeTool.outputAllowed();
        send(Envelope.newBuilder().setDebugToolState(DebugToolState.newBuilder()
                .setNodeId(activeNode == null ? "" : activeNode.id).setToolInstanceId(toolInstanceId)
                .setState(state).setOutputEnabled(allowed)
                .setRequestedOutput(activeTool == null ? 0.0 : activeTool.requestedOutput())
                .setAppliedOutput(activeTool == null ? 0.0 : activeTool.appliedOutput())
                .setOutputUnit("normalized power").setStatusMessage(allowed ? "Output active" : "Output stopped")));
        send(Envelope.newBuilder().setDebugSafetyState(DebugSafetyState.newBuilder()
                .setTcpConnected(dataClient.isConnected()).setWatchdogHealthy(activeTool == null || activeTool.watchdogHealthy())
                .setOutputAllowed(allowed).setBlockedReason(allowed ? "" : "Output is stopped")));
    }

    private void sendFaultState(String nodeId, String instanceId, String detail) {
        send(Envelope.newBuilder().setDebugToolState(DebugToolState.newBuilder()
                .setNodeId(nodeId).setToolInstanceId(instanceId).setState(DebugSessionState.DEBUG_FAULTED)
                .setOutputEnabled(false).setRequestedOutput(0.0).setAppliedOutput(0.0)
                .setOutputUnit("normalized power").setStatusMessage(detail)));
        send(Envelope.newBuilder().setDebugSafetyState(DebugSafetyState.newBuilder()
                .setTcpConnected(dataClient != null && dataClient.isConnected()).setWatchdogHealthy(true)
                .setOutputAllowed(false).setBlockedReason(detail)));
    }

    private void send(Envelope.Builder message) {
        if (dataClient != null) dataClient.publishMessage(message);
    }

    private void stopActiveTool() {
        if (activeTool != null) {
            try { activeTool.stop(); } catch (RuntimeException ignored) { }
        }
        activeTool = null;
        activeNode = null;
        toolInstanceId = "";
        state = DebugSessionState.DEBUG_IDLE;
    }

    public interface Tool {
        default boolean busy() { return false; }
        default void receive(Envelope envelope) { }
        default DebugCommandResponse command(DebugCommandRequest request) { return null; }
        default List<org.firstinspires.ftc.teamcode.data.protocol.DebugBenchmarkDefinition> benchmarks() { return Collections.emptyList(); }
        void init(ToolContext context) throws Exception;
        void start();
        void loop();
        void stop();
        ParameterResult setParameter(String parameterId, double value, int ttlMs);
        void stopOutput();
        double requestedOutput();
        double appliedOutput();
        long encoderPosition();
        boolean outputAllowed();
        boolean watchdogHealthy();
    }

    public static final class ToolContext {
        public final HardwareMap hardwareMap;
        public final Telemetry telemetry;
        public final String toolId;
        public final String toolInstanceId;
        public final StructuredRobotDataClient dataClient;
        ToolContext(HardwareMap hardwareMap, Telemetry telemetry, String toolId, String toolInstanceId, StructuredRobotDataClient dataClient) {
            this.hardwareMap = hardwareMap; this.telemetry = telemetry; this.toolId = toolId; this.toolInstanceId = toolInstanceId;
            this.dataClient=dataClient;
        }
    }

    public static final class ParameterResult {
        final boolean accepted; final double effectiveValue; final String code; final String message;
        private ParameterResult(boolean accepted, double effectiveValue, String code, String message) {
            this.accepted = accepted; this.effectiveValue = effectiveValue; this.code = code; this.message = message;
        }
        public static ParameterResult accepted(double value) { return new ParameterResult(true, value, "", "Applied"); }
        public static ParameterResult rejected(String code, String message) { return new ParameterResult(false, 0.0, code, message); }
    }

    public static final class Registry {
        private final List<Node> nodes = new ArrayList<>();
        private final Map<String, Node> byId = new LinkedHashMap<>();

        public Registry folder(String id, String label, Consumer<FolderBuilder> children) {
            Node folder = Node.folder(id, label);
            add(folder);
            children.accept(new FolderBuilder(this, id));
            return this;
        }

        public Registry tool(String id, String parentId, String label, String description,
                             DebugRiskClass risk, double maxOutput, boolean enabled,
                             String disabledReason, ToolFactory factory, List<Parameter> parameters,
                             List<Command> commands) {
            Node node = Node.tool(id, parentId, label, description, risk, maxOutput, enabled, disabledReason,
                    factory, parameters, commands);
            add(node);
            return this;
        }

        private void add(Node node) { if (byId.put(node.id, node) != null) throw new IllegalArgumentException("Duplicate debug node " + node.id); nodes.add(node); }
        Node node(String id) { return byId.get(id); }
        List<Node> tools() { List<Node> result = new ArrayList<>(); for (Node node : nodes) if (node.kind == DebugNodeKind.DEBUG_TOOL) result.add(node); return result; }
    }

    public static final class FolderBuilder {
        private final Registry registry; private final String parentId;
        FolderBuilder(Registry registry, String parentId) { this.registry = registry; this.parentId = parentId; }
        public FolderBuilder folder(String id, String label, Consumer<FolderBuilder> children) {
            registry.folder(id, label, nested -> children.accept(new FolderBuilder(registry, id)));
            return this;
        }
        public FolderBuilder tool(String id, String label, String description, DebugRiskClass risk, double maxOutput,
                                  boolean enabled, String disabledReason, ToolFactory factory,
                                  List<Parameter> parameters, List<Command> commands) {
            registry.tool(id, parentId, label, description, risk, maxOutput, enabled, disabledReason, factory, parameters, commands);
            return this;
        }
    }

    public interface ToolFactory { Tool create(); }

    public static final class Parameter {
        final String id, label, description, unit; final double current, min, max, step; final boolean writable, persistent;
        Parameter(String id, String label, String description, double current, double min, double max, double step, String unit, boolean writable, boolean persistent) {
            this.id=id; this.label=label; this.description=description; this.current=current; this.min=min; this.max=max; this.step=step; this.unit=unit; this.writable=writable; this.persistent=persistent;
        }
        DebugParameter toProto() { return DebugParameter.newBuilder().setId(id).setLabel(label).setDescription(description).setValueType(ValueType.FLOAT64).setCurrentFloat64(current).setMinFloat64(min).setMaxFloat64(max).setStepFloat64(step).setUnit(unit).setWritable(writable).setPersistent(persistent).setUpdatePolicy(DebugParameterUpdatePolicy.DEBUG_UPDATE_LOOP_BOUNDARY).build(); }
    }

    public static final class Command {
        final String id, label, description; final boolean acknowledgement;
        Command(String id, String label, String description, boolean acknowledgement) { this.id=id; this.label=label; this.description=description; this.acknowledgement=acknowledgement; }
        DebugCommand toProto() { return DebugCommand.newBuilder().setId(id).setLabel(label).setDescription(description).setRequiresHumanAcknowledgement(acknowledgement).build(); }
    }

    private static final class Node {
        final String id, parentId, label, description, disabledReason; final DebugNodeKind kind; final DebugRiskClass risk; final double maxOutput; final boolean enabled; final ToolFactory factory; final List<Parameter> parameters; final List<Command> commands;
        private Node(String id, String parentId, String label, String description, DebugNodeKind kind, DebugRiskClass risk, double maxOutput, boolean enabled, String disabledReason, ToolFactory factory, List<Parameter> parameters, List<Command> commands) { this.id=id; this.parentId=parentId; this.label=label; this.description=description; this.kind=kind; this.risk=risk; this.maxOutput=maxOutput; this.enabled=enabled; this.disabledReason=disabledReason == null ? "" : disabledReason; this.factory=factory; this.parameters=parameters == null ? Collections.emptyList() : parameters; this.commands=commands == null ? Collections.emptyList() : commands; }
        static Node folder(String id, String label) { return new Node(id, "", label, "", DebugNodeKind.DEBUG_FOLDER, DebugRiskClass.DEBUG_OBSERVE_ONLY, 0.0, true, "", null, null, null); }
        static Node tool(String id, String parentId, String label, String description, DebugRiskClass risk, double maxOutput, boolean enabled, String disabledReason, ToolFactory factory, List<Parameter> parameters, List<Command> commands) { return new Node(id, parentId, label, description, DebugNodeKind.DEBUG_TOOL, risk, maxOutput, enabled, disabledReason, factory, parameters, commands); }
        DebugNode toProto() { return DebugNode.newBuilder().setId(id).setParentId(parentId).setLabel(label).setDescription(description).setKind(kind).setSortOrder(0).setRiskClass(risk).setOpModeLabel(label).setSelectable(kind == DebugNodeKind.DEBUG_TOOL).setEnabled(enabled).setDisabledReason(disabledReason).setMaxOutput(maxOutput).setMaxActiveDurationMs(5000).addAllParameterIds(parameterIds()).addAllCommandIds(commandIds()).build(); }
        List<String> parameterIds() { List<String> result = new ArrayList<>(); for (Parameter value : parameters) result.add(value.id); return result; }
        List<String> commandIds() { List<String> result = new ArrayList<>(); for (Command value : commands) result.add(value.id); return result; }
    }

    /** Simple bounded free-spin motor tool used by the first vertical slice. */
    public static final class MotorPowerTool implements Tool {
        private static final double MAX_POWER = 0.35;
        private static final long COMMAND_TIMEOUT_NS = 500_000_000L;
        private final String motorName;
        private DcMotor motor;
        private double requestedPower, appliedPower;
        private long lastCommandNs;
        private long commandExpiryNs;
        private boolean watchdogHealthy;

        public MotorPowerTool(String motorName) { this.motorName = motorName; }
        @Override public void init(ToolContext context) {
            motor = context.hardwareMap.get(DcMotor.class, motorName);
            motor.setZeroPowerBehavior(DcMotor.ZeroPowerBehavior.BRAKE);
            motor.setMode(DcMotor.RunMode.RUN_WITHOUT_ENCODER);
            requestedPower = 0.0; appliedPower = 0.0;
            lastCommandNs = SystemClock.elapsedRealtimeNanos(); commandExpiryNs = lastCommandNs + COMMAND_TIMEOUT_NS; watchdogHealthy = true;
        }
        @Override public void start() { stopOutput(); }
        @Override public void loop() {
            long now = SystemClock.elapsedRealtimeNanos();
            watchdogHealthy = now <= commandExpiryNs;
            if (!watchdogHealthy) requestedPower = 0.0;
            appliedPower = Range.clip(requestedPower, -MAX_POWER, MAX_POWER);
            motor.setPower(appliedPower);
        }
        @Override public void stop() { if (motor != null) motor.setPower(0.0); requestedPower = 0.0; appliedPower = 0.0; watchdogHealthy = true; }
        @Override public ParameterResult setParameter(String parameterId, double value, int ttlMs) {
            if (!"motor.power".equals(parameterId)) return ParameterResult.rejected("UNKNOWN_PARAMETER", "Only motor.power is writable");
            if (!Double.isFinite(value) || value < -MAX_POWER || value > MAX_POWER) return ParameterResult.rejected("OUTPUT_LIMIT_EXCEEDED", "Power must be between -0.35 and 0.35");
            requestedPower = value;
            lastCommandNs = SystemClock.elapsedRealtimeNanos(); commandExpiryNs = lastCommandNs + ttlMs * 1_000_000L;
            return ParameterResult.accepted(value);
        }
        @Override public void stopOutput() { requestedPower = 0.0; lastCommandNs = SystemClock.elapsedRealtimeNanos(); commandExpiryNs = lastCommandNs + COMMAND_TIMEOUT_NS; if (motor != null) motor.setPower(0.0); appliedPower = 0.0; }
        @Override public double requestedOutput() { return requestedPower; }
        @Override public double appliedOutput() { return appliedPower; }
        @Override public long encoderPosition() { return motor == null ? 0L : motor.getCurrentPosition(); }
        @Override public boolean outputAllowed() { return Math.abs(appliedPower) > 0.0001 && watchdogHealthy; }
        @Override public boolean watchdogHealthy() { return watchdogHealthy; }
    }
}
