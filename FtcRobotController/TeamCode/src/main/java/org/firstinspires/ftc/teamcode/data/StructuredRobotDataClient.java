package org.firstinspires.ftc.teamcode.data;

import android.os.Build;
import android.os.SystemClock;

import com.google.protobuf.ByteString;
import com.qualcomm.robotcore.hardware.DcMotorEx;
import com.qualcomm.robotcore.hardware.Gamepad;
import com.qualcomm.robotcore.hardware.VoltageSensor;
import com.qualcomm.robotcore.util.ElapsedTime;

import org.firstinspires.ftc.robotcore.external.navigation.CurrentUnit;

import org.firstinspires.ftc.teamcode.data.protocol.Channel;
import org.firstinspires.ftc.teamcode.data.protocol.ChannelRole;
import org.firstinspires.ftc.teamcode.data.protocol.ChannelValue;
import org.firstinspires.ftc.teamcode.data.protocol.Envelope;
import org.firstinspires.ftc.teamcode.data.protocol.GamepadSnapshot;
import org.firstinspires.ftc.teamcode.data.protocol.Heartbeat;
import org.firstinspires.ftc.teamcode.data.protocol.Hello;
import org.firstinspires.ftc.teamcode.data.protocol.SampleBatch;
import org.firstinspires.ftc.teamcode.data.protocol.Schema;
import org.firstinspires.ftc.teamcode.data.protocol.Snapshot;
import org.firstinspires.ftc.teamcode.data.protocol.ValueType;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.Closeable;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Enumeration;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.LinkedBlockingDeque;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.DoubleSupplier;

/** Publishes protobuf telemetry without blocking the OpMode thread. */
public final class StructuredRobotDataClient implements Closeable {
    public static final int DEFAULT_PORT = 5810;
    public static final int DEFAULT_DISCOVERY_PORT = 5811;
    /** Standard diagnostic channel automatically added to every structured OpMode session. */
    public static final String LOOP_TIME_SIGNAL_ID = "opmode.loopTimeMs";
    private static final int VERSION = 2, MAX_FRAME_BYTES = 1_048_576, QUEUE_CAPACITY = 256, MAX_BATCH = 32;
    private static final long BATCH_WINDOW_MS = 20, HEARTBEAT_INTERVAL_NS = 1_000_000_000L;
    private static final byte[] DISCOVERY_MAGIC = {'F', 'T', 'R', 'D'};
    private static final int DISCOVERY_REQUEST_BYTES = 21, DISCOVERY_RESPONSE_BYTES = 23;

    private final String opModeName;
    private final int tcpPort, discoveryPort;
    private final UUID sessionId = UUID.randomUUID();
    private final List<Device> devices = new ArrayList<>();
    private final List<Signal> signals = new ArrayList<>();
    private final List<MotorBinding> motors = new ArrayList<>();
    private final LinkedBlockingDeque<Outbound> frames = new LinkedBlockingDeque<>(QUEUE_CAPACITY);
    private final LinkedBlockingDeque<IncomingMessage> incoming = new LinkedBlockingDeque<>(64);
    private final AtomicLong nextSample = new AtomicLong(), droppedSamples = new AtomicLong(), lastLoopPublishNs = new AtomicLong();
    private final AtomicBoolean running = new AtomicBoolean(), connected = new AtomicBoolean();
    private volatile Socket socket;
    private volatile Thread worker;
    private volatile String lastError;
    private VoltageSensor voltageSensor;
    private DoubleSupplier runtimeSecondsSource;
    private Gamepad boundGamepad1, boundGamepad2;

    /** Creates a client using the standard laptop discovery and TCP ports. */
    public StructuredRobotDataClient(String opModeName) {
        this(DEFAULT_DISCOVERY_PORT, DEFAULT_PORT, opModeName);
    }

    public StructuredRobotDataClient(int discoveryPort, int tcpPort, String opModeName) {
        if (discoveryPort <= 0 || discoveryPort > 65535 || tcpPort <= 0 || tcpPort > 65535) throw new IllegalArgumentException("ports must be between 1 and 65535");
        this.discoveryPort = discoveryPort; this.tcpPort = tcpPort; this.opModeName = text(opModeName);
    }

    /** Adds the standard OpMode runtime signal and captures it on every publishLoop call. */
    public synchronized StructuredRobotDataClient addRuntime(ElapsedTime runtime) {
        if (runtime == null) throw new IllegalArgumentException("runtime must not be null");
        ensureNotStarted();
        addSignal("opmode.runtimeSeconds", "OpMode Runtime", null,
                "runtime", "s", "float64", "diagnostic", 50);
        runtimeSecondsSource = runtime::seconds;
        return this;
    }

    /** Adds robot voltage so motor electrical power can be reported in watts. */
    public synchronized StructuredRobotDataClient addVoltageSensor(VoltageSensor sensor) {
        if (sensor == null) throw new IllegalArgumentException("voltage sensor must not be null");
        ensureNotStarted();
        addSignal("robot.voltage", "Robot Voltage", null,
                "voltage", "V", "float64", "measured", 50);
        voltageSensor = sensor;
        return this;
    }

    /**
     * Registers a DcMotorEx and automatically adds its common diagnostic signals.
     * The motor's current power is used for both commandedPower and appliedPower.
     */
    public synchronized StructuredRobotDataClient addMotor(String deviceId, String label, DcMotorEx motor) {
        if (motor == null) throw new IllegalArgumentException("motor must not be null");
        return addMotor(deviceId, label, motor, motor::getPower);
    }

    /**
     * Registers a DcMotorEx and captures the supplied requested power separately
     * from the motor's measured/applied power.
     */
    public synchronized StructuredRobotDataClient addMotor(
            String deviceId, String label, DcMotorEx motor, DoubleSupplier commandedPower) {
        if (motor == null) throw new IllegalArgumentException("motor must not be null");
        if (commandedPower == null) throw new IllegalArgumentException("commandedPower must not be null");
        ensureNotStarted();
        addDevice(deviceId, label, "drivetrain", "REV DC motor");
        addSignal(deviceId + ".commandedPower", label + " Commanded Power", deviceId,
                "commandedPower", "normalized", "float64", "command", 50);
        addSignal(deviceId + ".appliedPower", label + " Applied Power", deviceId,
                "appliedPower", "normalized", "float64", "measured", 50);
        addSignal(deviceId + ".encoderPosition", label + " Encoder Position", deviceId,
                "position", "ticks", "int64", "measured", 50);
        addSignal(deviceId + ".velocityTicksPerSecond", label + " Encoder Velocity", deviceId,
                "velocity", "ticks/s", "float64", "measured", 50);
        addSignal(deviceId + ".currentAmps", label + " Motor Current", deviceId,
                "current", "A", "float64", "measured", 50);
        addSignal(deviceId + ".electricalPowerWatts", label + " Motor Electrical Power", deviceId,
                "electricalPower", "W", "float64", "measured", 50);
        motors.add(new MotorBinding(deviceId, motor, commandedPower));
        return this;
    }

    /**
     * Registers both FTC gamepad objects and publishes their complete state as
     * the compact dedicated GamepadSnapshot frame.
     */
    public synchronized StructuredRobotDataClient addGamepads(Gamepad gamepad1, Gamepad gamepad2) {
        if (gamepad1 == null || gamepad2 == null) throw new IllegalArgumentException("gamepads must not be null");
        ensureNotStarted();
        addDevice("input.gamepad1", "Gamepad 1", "input", "gamepad");
        addDevice("input.gamepad2", "Gamepad 2", "input", "gamepad");
        boundGamepad1 = gamepad1;
        boundGamepad2 = gamepad2;
        return this;
    }

    public synchronized StructuredRobotDataClient addDevice(String id, String label, String subsystem, String deviceType) {
        ensureNotStarted(); devices.add(new Device(id, label, subsystem, deviceType)); return this;
    }

    public synchronized StructuredRobotDataClient addSignal(String id, String label, String deviceId, String quantity, String unit, String valueType, String role, double sampleHintHz) {
        if (LOOP_TIME_SIGNAL_ID.equals(id)) throw new IllegalArgumentException(LOOP_TIME_SIGNAL_ID + " is a reserved standard signal");
        ensureNotStarted(); signals.add(new Signal(id, label, deviceId, quantity, unit, valueType, role, sampleHintHz)); return this;
    }

    public synchronized void start() {
        installStandardSignals();
        validateCatalog();
        if (!running.compareAndSet(false, true)) return;
        worker = new Thread(this::run, "StructuredRobotDataClient"); worker.setDaemon(true); worker.start();
    }

    /** Copies a complete snapshot and returns immediately. Encoding occurs on the worker. */
    public boolean publishSample(Map<String, ?> values) {
        return publishSample(values, false);
    }

    /**
     * Copies one complete snapshot with the Control Hub's selected incident state.
     * The laptop may apply a live local override, but this original value remains
     * in the raw recording for audit and later replay.
     */
    public boolean publishSample(Map<String, ?> values, boolean highlighted) {
        if (!running.get()) return false;
        Map<String, Object> copy = new LinkedHashMap<>();
        for (Map.Entry<String, ?> entry : values.entrySet()) copy.put(entry.getKey(), entry.getValue());
        Set<String> expected = signalIds(); expected.remove(LOOP_TIME_SIGNAL_ID);
        if (!expected.equals(copy.keySet())) throw new IllegalArgumentException("sample must contain every user-registered signal exactly once");
        long now = SystemClock.elapsedRealtimeNanos();
        long previous = lastLoopPublishNs.getAndSet(now);
        copy.put(LOOP_TIME_SIGNAL_ID, previous == 0 ? 0.0 : (now - previous) / 1_000_000.0);
        return enqueue(new Sample(now, nextSample.getAndIncrement(), copy, highlighted));
    }

    /**
     * Standard once-per-OpMode-loop publisher. It records loop time in the TCP snapshot
     * and independently publishes the complete FTC gamepad state.
     */
    public boolean publishLoop(Map<String, ?> values, Gamepad gamepad1, Gamepad gamepad2) {
        return publishLoop(values, gamepad1, gamepad2, false);
    }

    /** Publishes a loop snapshot and gamepads with the supplied incident state. */
    public boolean publishLoop(Map<String, ?> values, Gamepad gamepad1, Gamepad gamepad2, boolean highlighted) {
        boolean sampleQueued = publishSample(values, highlighted);
        boolean gamepadsQueued = publishGamepads(gamepad1, gamepad2);
        return sampleQueued && gamepadsQueued;
    }

    /** Captures all bound runtime, voltage, motor, and gamepad values and publishes one loop. */
    public boolean publishLoop() {
        return publishLoop(false);
    }

    /** Captures configured values and gamepads with the supplied incident state. */
    public boolean publishLoop(boolean highlighted) {
        if (!running.get()) return false;
        boolean sampleQueued = publishSample(createDataSnapshot(), highlighted);
        boolean gamepadsQueued = boundGamepad1 == null
                ? true
                : publishGamepads(boundGamepad1, boundGamepad2);
        return sampleQueued && gamepadsQueued;
    }

    /** Captures the values configured through the convenience bindings. */
    public Map<String, Object> createDataSnapshot() {
        Map<String, Object> values = new LinkedHashMap<>();
        if (runtimeSecondsSource != null) values.put("opmode.runtimeSeconds", runtimeSecondsSource.getAsDouble());
        double robotVoltage = readVoltage();
        if (voltageSensor != null) values.put("robot.voltage", robotVoltage);
        for (MotorBinding motor : motors) motor.addValues(values, robotVoltage);
        return values;
    }

    public boolean publishGamepads(Gamepad gamepad1, Gamepad gamepad2) {
        if (!running.get()) return false;
        if (gamepad1 == null || gamepad2 == null) throw new IllegalArgumentException("gamepads must not be null");
        return enqueue(new GamepadFrame(SystemClock.elapsedRealtimeNanos(), gamepad1, gamepad2));
    }

    /** Queue a control/debug envelope for the TCP writer. The common envelope header is added here. */
    public boolean publishMessage(Envelope.Builder message) {
        if (message == null || message.getBodyCase() == Envelope.BodyCase.BODY_NOT_SET) return false;
        return enqueue(new Message(SystemClock.elapsedRealtimeNanos(), message.build()));
    }

    /** Returns the next robot-originated message with its robot-local receipt time. */
    public IncomingMessage pollIncomingMessage() { return incoming.poll(); }

    public boolean isConnected() { return connected.get(); }
    public int getQueuedPacketCount() { return frames.size(); }
    public long getDroppedPacketCount() { return droppedSamples.get(); }
    public String getLastError() { return lastError; }

    @Override public synchronized void close() {
        if (!running.getAndSet(false)) return;
        connected.set(false); closeSocket(socket); if (worker != null) worker.interrupt(); frames.clear(); incoming.clear();
    }

    private boolean enqueue(Outbound frame) {
        if (frames.offerLast(frame)) return true;
        frames.pollFirst(); droppedSamples.incrementAndGet(); return frames.offerLast(frame);
    }

    private void run() {
        long backoff = 250;
        while (running.get()) {
            Socket active = null;
            try {
                active = new Socket(); socket = active; active.connect(discoverServer(), 1000);
                active.setKeepAlive(true); active.setTcpNoDelay(true); stream(active, new Connection(UUID.randomUUID())); backoff = 250;
            } catch (IOException error) {
                if (running.get()) lastError = error.getClass().getSimpleName() + ": " + error.getMessage();
            } finally { connected.set(false); closeSocket(active); }
            if (running.get()) try { Thread.sleep(backoff); backoff = Math.min(backoff * 2, 2000); }
            catch (InterruptedException ignored) { Thread.currentThread().interrupt(); return; }
        }
    }

    private void stream(Socket active, Connection connection) throws IOException {
        DataOutputStream output = new DataOutputStream(new BufferedOutputStream(active.getOutputStream()));
        DataInputStream input = new DataInputStream(new BufferedInputStream(active.getInputStream()));
        send(output, connection, SystemClock.elapsedRealtimeNanos(), value -> value.setHello(hello())); output.flush();
        active.setSoTimeout(1500); validateHelloAck(readFrame(input), connection.id); active.setSoTimeout(0);
        send(output, connection, SystemClock.elapsedRealtimeNanos(), value -> value.setSchema(schema())); output.flush();
        connected.set(true); lastError = null;
        Thread reader = new Thread(() -> readIncoming(active, input), "StructuredRobotDataClient-reader");
        reader.setDaemon(true);
        reader.start();
        long lastHeartbeat = SystemClock.elapsedRealtimeNanos();
        try {
            while (running.get() && !active.isClosed()) {
                Outbound first;
                try { first = frames.pollFirst(250, TimeUnit.MILLISECONDS); }
                catch (InterruptedException ignored) { Thread.currentThread().interrupt(); return; }
                if (first instanceof Sample) {
                    List<Sample> batch = new ArrayList<>(); batch.add((Sample) first);
                    long deadline = SystemClock.elapsedRealtime() + BATCH_WINDOW_MS;
                    while (batch.size() < MAX_BATCH && SystemClock.elapsedRealtime() < deadline) {
                        Outbound next = frames.pollFirst();
                        if (!(next instanceof Sample)) { if (next != null) frames.offerFirst(next); break; }
                        batch.add((Sample) next);
                    }
                    send(output, connection, batch.get(batch.size() - 1).robotTimeNs, value -> value.setSampleBatch(sampleBatch(batch))); output.flush();
                } else if (first != null) { send(output, connection, first.robotTimeNs(), first::apply); output.flush(); }
                long now = SystemClock.elapsedRealtimeNanos();
                if (now - lastHeartbeat >= HEARTBEAT_INTERVAL_NS) {
                    Heartbeat.Builder heartbeat = Heartbeat.newBuilder().setQueuedFrames(frames.size()).setDroppedSamples(droppedSamples.get());
                    long last = nextSample.get() - 1; if (last >= 0) heartbeat.setLastSampleSequence(last);
                    send(output, connection, now, value -> value.setHeartbeat(heartbeat)); output.flush(); lastHeartbeat = now;
                }
            }
        } finally {
            reader.interrupt();
        }
    }

    private void readIncoming(Socket active, DataInputStream input) {
        try {
            while (running.get() && !active.isClosed()) {
                Envelope message = readFrame(input);
                if (message.getBodyCase() == Envelope.BodyCase.HELLO_ACK) continue;
                IncomingMessage queued = new IncomingMessage(message, SystemClock.elapsedRealtimeNanos());
                if (!incoming.offerLast(queued)) incoming.pollFirst();
            }
        } catch (IOException ignored) {
            // The writer observes the closed socket and reconnects. A TCP loss
            // must never block the OpMode loop.
        } finally {
            closeSocket(active);
        }
    }

    private InetSocketAddress discoverServer() throws IOException {
        UUID requestId = UUID.randomUUID(); byte[] request = discoveryRequest(requestId);
        try (DatagramSocket udp = new DatagramSocket()) {
            udp.setBroadcast(true); udp.setSoTimeout(1000);
            // A robot can have multiple active interfaces, including virtual or
            // point-to-point interfaces with no usable route for broadcast.
            // One failed send must not prevent discovery on the robot/laptop
            // Wi-Fi network.
            for (InetAddress broadcast : broadcastAddresses()) {
                try {
                    udp.send(new DatagramPacket(request, request.length, broadcast, discoveryPort));
                } catch (IOException ignored) {
                    // Continue probing the remaining interface broadcasts.
                }
            }
            byte[] bytes = new byte[DISCOVERY_RESPONSE_BYTES]; DatagramPacket response = new DatagramPacket(bytes, bytes.length);
            while (running.get()) { udp.receive(response); int port = discoveryResponsePort(response.getData(), response.getOffset(), response.getLength(), requestId); if (port > 0) return new InetSocketAddress(response.getAddress(), port); }
        }
        throw new IOException("telemetry discovery stopped");
    }

    private Iterable<InetAddress> broadcastAddresses() throws IOException {
        LinkedHashMap<String, InetAddress> direct = new LinkedHashMap<>(), fallback = new LinkedHashMap<>();
        Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
        if (interfaces != null) for (NetworkInterface network : Collections.list(interfaces)) {
            if (!network.isUp() || network.isLoopback()) continue;
            boolean wifiDirect = network.getName().toLowerCase().contains("p2p");
            for (InterfaceAddress address : network.getInterfaceAddresses()) if (address.getBroadcast() != null) {
                InetAddress broadcast = address.getBroadcast(); fallback.put(broadcast.getHostAddress(), broadcast); if (wifiDirect) direct.put(broadcast.getHostAddress(), broadcast);
            }
        }
        if (!direct.isEmpty()) return direct.values();
        InetAddress global = InetAddress.getByName("255.255.255.255"); fallback.put(global.getHostAddress(), global); return fallback.values();
    }

    private Hello hello() {
        return Hello.newBuilder().setRobotId("android-" + Build.MODEL.replace(' ', '-')).setRobotName("FTC Robot Controller")
                .setOpModeName(opModeName).setStartedAtRobotTimeNs(SystemClock.elapsedRealtimeNanos()).setQueueCapacity(QUEUE_CAPACITY)
                .addCapabilities("schema").addCapabilities("sample-batches").addCapabilities("events").addCapabilities("gaps")
                .addCapabilities("debugger-v1").build();
    }

    private Schema schema() {
        Schema.Builder result = Schema.newBuilder().setRevision(1); Map<String, Integer> numbers = new LinkedHashMap<>(); int nextDevice = 1;
        for (Device device : devices) { numbers.put(device.id, nextDevice); result.addDevices(org.firstinspires.ftc.teamcode.data.protocol.Device.newBuilder().setDeviceId(nextDevice++).setKey(device.id).setLabel(device.label).setSubsystem(device.subsystem).setDeviceType(device.deviceType)); }
        int nextChannel = 1;
        for (Signal signal : signals) {
            signal.channelId = nextChannel++;
            Channel.Builder channel = Channel.newBuilder().setChannelId(signal.channelId).setKey(signal.id).setLabel(signal.label).setQuantity(signal.quantity).setUnit(signal.unit).setValueType(signal.valueType).setRole(signal.role);
            if (signal.deviceId != null) channel.setDeviceId(numbers.get(signal.deviceId)); if (signal.sampleHintHz > 0) channel.setSampleHintHz(signal.sampleHintHz); result.addChannels(channel);
        }
        return result.build();
    }

    private SampleBatch sampleBatch(List<Sample> samples) {
        SampleBatch.Builder batch = SampleBatch.newBuilder();
        for (Sample sample : samples) {
            Snapshot.Builder snapshot = Snapshot.newBuilder().setSampleSequence(sample.sequence).setSchemaRevision(1).setHighlighted(sample.highlighted);
            for (Signal signal : signals) snapshot.addValues(value(signal, sample.values.get(signal.id))); batch.addSnapshots(snapshot);
        }
        return batch.build();
    }

    private double readVoltage() {
        if (voltageSensor == null) return 0.0;
        double voltage = voltageSensor.getVoltage();
        return Double.isFinite(voltage) && voltage > 0.0 ? voltage : 0.0;
    }

    private static ChannelValue value(Signal signal, Object raw) {
        ChannelValue.Builder result = ChannelValue.newBuilder().setChannelId(signal.channelId);
        if (raw == null || (raw instanceof Double && !Double.isFinite((Double) raw)) || (raw instanceof Float && !Float.isFinite((Float) raw))) return result.setUnavailable(true).build();
        switch (signal.valueType) {
            case FLOAT64: if (!(raw instanceof Number)) throw invalid(signal, "numeric"); return result.setFloat64Value(((Number) raw).doubleValue()).build();
            case INT64: if (!(raw instanceof Number)) throw invalid(signal, "integer"); return result.setInt64Value(((Number) raw).longValue()).build();
            case BOOLEAN: if (!(raw instanceof Boolean)) throw invalid(signal, "boolean"); return result.setBooleanValue((Boolean) raw).build();
            case STRING: case ENUM: return result.setStringValue(String.valueOf(raw)).build();
            default: throw new IllegalArgumentException(signal.id + " uses an unsupported structured value type");
        }
    }

    private static IllegalArgumentException invalid(Signal signal, String expected) { return new IllegalArgumentException(signal.id + " must be " + expected); }
    private void send(DataOutputStream output, Connection connection, long robotTimeNs, Populator body) throws IOException {
        Envelope.Builder envelope = Envelope.newBuilder().setProtocolVersion(VERSION).setSessionId(uuid(sessionId)).setConnectionId(uuid(connection.id)).setConnectionSequence(connection.nextSequence++).setRobotElapsedNs(robotTimeNs);
        body.apply(envelope); byte[] bytes = envelope.build().toByteArray();
        if (bytes.length == 0 || bytes.length > MAX_FRAME_BYTES) throw new IOException("invalid telemetry frame size"); output.writeInt(bytes.length); output.write(bytes);
    }
    private static Envelope readFrame(DataInputStream input) throws IOException { int length = input.readInt(); if (length <= 0 || length > MAX_FRAME_BYTES) throw new IOException("invalid server frame length"); byte[] bytes = new byte[length]; input.readFully(bytes); return Envelope.parseFrom(bytes); }
    private void validateHelloAck(Envelope response, UUID connectionId) throws IOException { if (response.getProtocolVersion() != VERSION || response.getBodyCase() != Envelope.BodyCase.HELLO_ACK || !response.getSessionId().equals(uuid(sessionId)) || !response.getConnectionId().equals(uuid(connectionId)) || !response.getHelloAck().getAccepted()) throw new IOException("telemetry server rejected hello"); }

    private void validateCatalog() {
        if (signals.isEmpty()) throw new IllegalStateException("at least one signal is required"); Set<String> deviceIds = new LinkedHashSet<>(), signalIds = new LinkedHashSet<>();
        for (Device device : devices) if (!deviceIds.add(device.id)) throw new IllegalStateException("duplicate device " + device.id);
        for (Signal signal : signals) { if (!signalIds.add(signal.id)) throw new IllegalStateException("duplicate signal " + signal.id); if (signal.deviceId != null && !deviceIds.contains(signal.deviceId)) throw new IllegalStateException("unknown device " + signal.deviceId); }
    }
    private void installStandardSignals() {
        for (Signal signal : signals) if (LOOP_TIME_SIGNAL_ID.equals(signal.id)) return;
        signals.add(new Signal(LOOP_TIME_SIGNAL_ID, "Loop Time", null, "loopTime", "ms", "float64", "diagnostic", 0));
    }
    private Set<String> signalIds() { Set<String> ids = new LinkedHashSet<>(); for (Signal signal : signals) ids.add(signal.id); return ids; }
    private void ensureNotStarted() { if (running.get()) throw new IllegalStateException("catalog cannot change after start"); }
    private void closeSocket(Socket candidate) { if (candidate != null) try { candidate.close(); } catch (IOException ignored) { } }

    private static byte[] discoveryRequest(UUID id) { return ByteBuffer.allocate(DISCOVERY_REQUEST_BYTES).order(ByteOrder.BIG_ENDIAN).put(DISCOVERY_MAGIC).put((byte) VERSION).putLong(id.getMostSignificantBits()).putLong(id.getLeastSignificantBits()).array(); }
    private static int discoveryResponsePort(byte[] data, int offset, int length, UUID id) {
        if (length != DISCOVERY_RESPONSE_BYTES) return -1; ByteBuffer buffer = ByteBuffer.wrap(data, offset, length).order(ByteOrder.BIG_ENDIAN);
        for (byte expected : DISCOVERY_MAGIC) if (buffer.get() != expected) return -1;
        if ((buffer.get() & 0xff) != VERSION || buffer.getLong() != id.getMostSignificantBits() || buffer.getLong() != id.getLeastSignificantBits()) return -1;
        int port = buffer.getShort() & 0xffff; return port == 0 ? -1 : port;
    }
    private static ByteString uuid(UUID id) { return ByteString.copyFrom(ByteBuffer.allocate(16).order(ByteOrder.BIG_ENDIAN).putLong(id.getMostSignificantBits()).putLong(id.getLeastSignificantBits()).array()); }

    public static final class IncomingMessage {
        private final Envelope envelope;
        private final long receivedRobotTimeNs;

        private IncomingMessage(Envelope envelope, long receivedRobotTimeNs) {
            this.envelope = envelope;
            this.receivedRobotTimeNs = receivedRobotTimeNs;
        }

        public Envelope envelope() { return envelope; }
        public long receivedRobotTimeNs() { return receivedRobotTimeNs; }
    }

    private interface Populator { void apply(Envelope.Builder envelope); }
    private interface Outbound { long robotTimeNs(); void apply(Envelope.Builder envelope); }
    private static final class Sample implements Outbound { final long robotTimeNs, sequence; final Map<String, Object> values; final boolean highlighted; Sample(long robotTimeNs, long sequence, Map<String, Object> values, boolean highlighted) { this.robotTimeNs = robotTimeNs; this.sequence = sequence; this.values = values; this.highlighted = highlighted; } public long robotTimeNs() { return robotTimeNs; } public void apply(Envelope.Builder ignored) { throw new UnsupportedOperationException("samples are batched"); } }
    private static final class GamepadFrame implements Outbound { final long time; final GamepadSnapshot snapshot; GamepadFrame(long time, Gamepad one, Gamepad two) { this.time = time; snapshot = GamepadSnapshot.newBuilder().setGamepad1(gamepad(one)).setGamepad2(gamepad(two)).build(); } public long robotTimeNs() { return time; } public void apply(Envelope.Builder envelope) { envelope.setGamepad(snapshot); } }
    private static final class Message implements Outbound { final long time; final Envelope message; Message(long time, Envelope message) { this.time = time; this.message = message; } public long robotTimeNs() { return time; } public void apply(Envelope.Builder envelope) { envelope.mergeFrom(message); } }
    private static org.firstinspires.ftc.teamcode.data.protocol.Gamepad gamepad(Gamepad source) { return org.firstinspires.ftc.teamcode.data.protocol.Gamepad.newBuilder().setLeftStickX(source.left_stick_x).setLeftStickY(source.left_stick_y).setRightStickX(source.right_stick_x).setRightStickY(source.right_stick_y).setLeftTrigger(source.left_trigger).setRightTrigger(source.right_trigger).setA(source.a).setB(source.b).setX(source.x).setY(source.y).setDpadUp(source.dpad_up).setDpadDown(source.dpad_down).setDpadLeft(source.dpad_left).setDpadRight(source.dpad_right).setLeftBumper(source.left_bumper).setRightBumper(source.right_bumper).setLeftStickButton(source.left_stick_button).setRightStickButton(source.right_stick_button).setBack(source.back).setStart(source.start).setGuide(source.guide).build(); }
    private static final class MotorBinding {
        final String deviceId;
        final DcMotorEx motor;
        final DoubleSupplier commandedPower;

        MotorBinding(String deviceId, DcMotorEx motor, DoubleSupplier commandedPower) {
            this.deviceId = text(deviceId);
            this.motor = motor;
            this.commandedPower = commandedPower;
        }

        void addValues(Map<String, Object> values, double robotVoltage) {
            double appliedPower = motor.getPower();
            double currentAmps = motor.getCurrent(CurrentUnit.AMPS);
            values.put(deviceId + ".commandedPower", commandedPower.getAsDouble());
            values.put(deviceId + ".appliedPower", appliedPower);
            values.put(deviceId + ".encoderPosition", (long) motor.getCurrentPosition());
            values.put(deviceId + ".velocityTicksPerSecond", motor.getVelocity());
            values.put(deviceId + ".currentAmps", currentAmps);
            values.put(deviceId + ".electricalPowerWatts", currentAmps * robotVoltage);
        }
    }
    private static final class Device { final String id, label, subsystem, deviceType; Device(String id, String label, String subsystem, String deviceType) { this.id = text(id); this.label = text(label); this.subsystem = text(subsystem); this.deviceType = text(deviceType); } }
    private static final class Signal { final String id, label, deviceId, quantity, unit; final ValueType valueType; final ChannelRole role; final double sampleHintHz; int channelId; Signal(String id, String label, String deviceId, String quantity, String unit, String type, String role, double sampleHintHz) { this.id = text(id); this.label = text(label); this.deviceId = deviceId; this.quantity = text(quantity); this.unit = text(unit); this.valueType = ValueType.valueOf(text(type).toUpperCase()); this.role = ChannelRole.valueOf(text(role).toUpperCase()); this.sampleHintHz = sampleHintHz; } }
    private static final class Connection { final UUID id; long nextSequence; Connection(UUID id) { this.id = id; } }
    private static String text(String value) { if (value == null || value.trim().isEmpty()) throw new IllegalArgumentException("catalog text must not be blank"); return value; }
}
