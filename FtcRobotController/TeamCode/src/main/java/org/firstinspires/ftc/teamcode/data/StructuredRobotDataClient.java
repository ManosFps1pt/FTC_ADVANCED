package org.firstinspires.ftc.teamcode.data;

import android.os.Build;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

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
import java.nio.charset.StandardCharsets;
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

/**
 * Publishes {@code hello -> catalog -> full sample} telemetry frames without
 * blocking an OpMode. Hardware must be read by the OpMode before values are
 * copied into {@link #publishSample(Map)}.
 */
public final class StructuredRobotDataClient implements Closeable {
    public static final int DEFAULT_PORT = 5810;
    public static final int DEFAULT_DISCOVERY_PORT = 5811;

    private static final int VERSION = 1;
    private static final int MAX_FRAME_BYTES = 1_048_576;
    private static final int QUEUE_CAPACITY = 256;
    private static final long HEARTBEAT_INTERVAL_NS = 1_000_000_000L;

    private final String opModeName;
    private final int tcpPort;
    private final int discoveryPort;
    private final String sessionId = UUID.randomUUID().toString();
    private final List<Device> devices = new ArrayList<>();
    private final List<Signal> signals = new ArrayList<>();
    private final LinkedBlockingDeque<Sample> samples = new LinkedBlockingDeque<>(QUEUE_CAPACITY);
    private final AtomicLong nextSampleSequence = new AtomicLong();
    private final AtomicLong droppedSamples = new AtomicLong();
    private final AtomicBoolean running = new AtomicBoolean();
    private final AtomicBoolean connected = new AtomicBoolean();

    private volatile Socket socket;
    private volatile Thread worker;
    private volatile String lastError;

    public StructuredRobotDataClient(int discoveryPort, int tcpPort, String opModeName) {
        if (discoveryPort <= 0 || discoveryPort > 65_535 || tcpPort <= 0 || tcpPort > 65_535) {
            throw new IllegalArgumentException("ports must be between 1 and 65535");
        }
        if (opModeName == null || opModeName.trim().isEmpty()) {
            throw new IllegalArgumentException("opModeName must not be blank");
        }
        this.discoveryPort = discoveryPort;
        this.tcpPort = tcpPort;
        this.opModeName = opModeName;
    }

    public synchronized StructuredRobotDataClient addDevice(
            String id, String label, String subsystem, String deviceType) {
        ensureNotStarted();
        devices.add(new Device(id, label, subsystem, deviceType));
        return this;
    }

    public synchronized StructuredRobotDataClient addSignal(
            String id,
            String label,
            String deviceId,
            String quantity,
            String unit,
            String valueType,
            String role,
            double sampleHintHz) {
        ensureNotStarted();
        signals.add(new Signal(id, label, deviceId, quantity, unit, valueType, role, sampleHintHz));
        return this;
    }

    public synchronized void start() {
        validateCatalog();
        if (!running.compareAndSet(false, true)) return;
        worker = new Thread(this::run, "StructuredRobotDataClient");
        worker.setDaemon(true);
        worker.start();
    }

    /** Copies one complete current-value map into the bounded network queue. */
    public boolean publishSample(Map<String, ?> values) {
        if (!running.get()) return false;
        Map<String, Object> copy = new LinkedHashMap<>();
        for (Map.Entry<String, ?> entry : values.entrySet()) copy.put(entry.getKey(), entry.getValue());
        Set<String> expected = signalIds();
        if (!expected.equals(copy.keySet())) {
            throw new IllegalArgumentException("sample must contain every registered signal exactly once");
        }
        Sample sample = new Sample(SystemClock.elapsedRealtimeNanos(), nextSampleSequence.getAndIncrement(), copy);
        if (samples.offerLast(sample)) return true;
        samples.pollFirst(); // Keep the newest robot state if the laptop is slow or unavailable.
        droppedSamples.incrementAndGet();
        return samples.offerLast(sample);
    }

    public boolean isConnected() { return connected.get(); }
    public int getQueuedPacketCount() { return samples.size(); }
    public long getDroppedPacketCount() { return droppedSamples.get(); }
    public String getLastError() { return lastError; }

    @Override
    public synchronized void close() {
        if (!running.getAndSet(false)) return;
        connected.set(false);
        closeSocket(socket);
        if (worker != null) worker.interrupt();
        samples.clear();
    }

    private void run() {
        long reconnectDelayMs = 250;
        while (running.get()) {
            Socket activeSocket = null;
            try {
                InetSocketAddress endpoint = discoverServer();
                activeSocket = new Socket();
                socket = activeSocket;
                activeSocket.connect(endpoint, 1_000);
                activeSocket.setKeepAlive(true);
                activeSocket.setTcpNoDelay(true);
                stream(activeSocket, new Connection(UUID.randomUUID().toString()));
                reconnectDelayMs = 250;
            } catch (IOException | JSONException error) {
                if (running.get()) lastError = error.getClass().getSimpleName() + ": " + error.getMessage();
            } finally {
                connected.set(false);
                closeSocket(activeSocket);
            }
            if (running.get()) {
                try {
                    Thread.sleep(reconnectDelayMs);
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                    return;
                }
                reconnectDelayMs = Math.min(reconnectDelayMs * 2, 2_000);
            }
        }
    }

    private void stream(Socket activeSocket, Connection connection) throws IOException, JSONException {
        DataOutputStream output = new DataOutputStream(new BufferedOutputStream(activeSocket.getOutputStream()));
        DataInputStream input = new DataInputStream(new BufferedInputStream(activeSocket.getInputStream()));
        writeFrame(output, connection, "hello", SystemClock.elapsedRealtimeNanos(), helloData());
        output.flush();
        activeSocket.setSoTimeout(1_500);
        validateHelloAck(readFrame(input), connection.connectionId);
        activeSocket.setSoTimeout(0);
        writeFrame(output, connection, "catalog", SystemClock.elapsedRealtimeNanos(), catalogData());
        output.flush();
        connected.set(true);
        lastError = null;

        long lastHeartbeat = SystemClock.elapsedRealtimeNanos();
        while (running.get() && !activeSocket.isClosed()) {
            Sample sample;
            try {
                sample = samples.pollFirst(250, TimeUnit.MILLISECONDS);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return;
            }
            if (sample != null) {
                writeFrame(output, connection, "sample", sample.robotTimeNs, sample.toJson());
                output.flush();
            }
            long now = SystemClock.elapsedRealtimeNanos();
            if (now - lastHeartbeat >= HEARTBEAT_INTERVAL_NS) {
                JSONObject heartbeat = new JSONObject();
                heartbeat.put("queuedFrames", samples.size());
                heartbeat.put("droppedSamples", Long.toString(droppedSamples.get()));
                heartbeat.put("lastSampleSequence", Long.toString(Math.max(-1L, nextSampleSequence.get() - 1)));
                writeFrame(output, connection, "heartbeat", now, heartbeat);
                output.flush();
                lastHeartbeat = now;
            }
        }
    }

    private InetSocketAddress discoverServer() throws IOException, JSONException {
        String requestId = UUID.randomUUID().toString();
        JSONObject request = new JSONObject();
        request.put("protocol_version", VERSION);
        request.put("kind", "where_is_data_server");
        request.put("request_id", requestId);
        byte[] encoded = request.toString().getBytes(StandardCharsets.UTF_8);
        try (DatagramSocket udp = new DatagramSocket()) {
            udp.setBroadcast(true);
            udp.setSoTimeout(1_000);
            for (InetAddress broadcast : broadcastAddresses()) {
                udp.send(new DatagramPacket(encoded, encoded.length, broadcast, discoveryPort));
            }
            byte[] replyBytes = new byte[1024];
            DatagramPacket reply = new DatagramPacket(replyBytes, replyBytes.length);
            while (running.get()) {
                udp.receive(reply);
                JSONObject response = new JSONObject(new String(reply.getData(), reply.getOffset(), reply.getLength(), StandardCharsets.UTF_8));
                if (VERSION == response.optInt("protocol_version", -1)
                        && "data_server".equals(response.optString("kind"))
                        && requestId.equals(response.optString("request_id"))) {
                    String host = response.optString("host");
                    int port = response.optInt("port", -1);
                    if (!host.isEmpty() && port > 0 && port <= 65_535) return new InetSocketAddress(host, port);
                }
            }
        }
        throw new IOException("telemetry discovery stopped");
    }

    /** Send discovery over every active network, including the FTC Wi-Fi Direct adapter. */
    private Iterable<InetAddress> broadcastAddresses() throws IOException {
        LinkedHashMap<String, InetAddress> directAddresses = new LinkedHashMap<>();
        LinkedHashMap<String, InetAddress> fallbackAddresses = new LinkedHashMap<>();
        Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
        if (interfaces != null) {
            for (NetworkInterface networkInterface : Collections.list(interfaces)) {
                if (!networkInterface.isUp() || networkInterface.isLoopback()) continue;
                boolean isWifiDirect = networkInterface.getName().toLowerCase().contains("p2p");
                for (InterfaceAddress interfaceAddress : networkInterface.getInterfaceAddresses()) {
                    InetAddress broadcast = interfaceAddress.getBroadcast();
                    if (broadcast != null) {
                        fallbackAddresses.put(broadcast.getHostAddress(), broadcast);
                        if (isWifiDirect) directAddresses.put(broadcast.getHostAddress(), broadcast);
                    }
                }
            }
        }
        // A phone can have normal Wi-Fi and Wi-Fi Direct active at the same
        // time. Telemetry must stay on the robot's Wi-Fi Direct network; do
        // not race a home-network discovery reply when Direct is available.
        if (!directAddresses.isEmpty()) return directAddresses.values();
        InetAddress globalBroadcast = InetAddress.getByName("255.255.255.255");
        fallbackAddresses.put(globalBroadcast.getHostAddress(), globalBroadcast);
        return fallbackAddresses.values();
    }

    private JSONObject helloData() throws JSONException {
        JSONObject data = new JSONObject();
        data.put("robotId", "android-" + Build.MODEL.replace(' ', '-'));
        data.put("robotName", "FTC Robot Controller");
        data.put("opModeName", opModeName);
        data.put("startedAtRobotTimeNs", Long.toString(SystemClock.elapsedRealtimeNanos()));
        data.put("queueCapacity", QUEUE_CAPACITY);
        JSONArray capabilities = new JSONArray();
        capabilities.put("catalog"); capabilities.put("full-snapshot"); capabilities.put("events");
        data.put("capabilities", capabilities);
        return data;
    }

    private JSONObject catalogData() throws JSONException {
        JSONObject data = new JSONObject();
        data.put("schemaRevision", 1);
        JSONArray encodedDevices = new JSONArray();
        for (Device device : devices) encodedDevices.put(device.toJson());
        JSONArray encodedSignals = new JSONArray();
        for (Signal signal : signals) encodedSignals.put(signal.toJson());
        data.put("devices", encodedDevices);
        data.put("signals", encodedSignals);
        return data;
    }

    private void writeFrame(DataOutputStream output, Connection connection, String type, long robotTimeNs, JSONObject data)
            throws IOException, JSONException {
        JSONObject envelope = new JSONObject();
        envelope.put("protocol", "ftc-telemetry");
        envelope.put("version", VERSION);
        envelope.put("type", type);
        envelope.put("sessionId", sessionId);
        envelope.put("connectionId", connection.connectionId);
        envelope.put("sequence", Long.toString(connection.nextSequence++));
        envelope.put("robotTimeNs", Long.toString(robotTimeNs));
        envelope.put("data", data);
        byte[] bytes = envelope.toString().getBytes(StandardCharsets.UTF_8);
        output.writeInt(bytes.length);
        output.write(bytes);
    }

    private static JSONObject readFrame(DataInputStream input) throws IOException, JSONException {
        int length = input.readInt();
        if (length <= 0 || length > MAX_FRAME_BYTES) throw new IOException("invalid server frame length");
        byte[] bytes = new byte[length];
        input.readFully(bytes);
        return new JSONObject(new String(bytes, StandardCharsets.UTF_8));
    }

    private void validateHelloAck(JSONObject response, String connectionId) throws IOException {
        JSONObject data = response.optJSONObject("data");
        if (!"ftc-telemetry".equals(response.optString("protocol"))
                || response.optInt("version", -1) != VERSION
                || !"hello_ack".equals(response.optString("type"))
                || !sessionId.equals(response.optString("sessionId"))
                || !connectionId.equals(response.optString("connectionId"))
                || data == null || !data.optBoolean("accepted", false)) {
            throw new IOException("telemetry server rejected hello");
        }
    }

    private void validateCatalog() {
        if (signals.isEmpty()) throw new IllegalStateException("at least one signal is required");
        Set<String> deviceIds = new LinkedHashSet<>();
        for (Device device : devices) if (!deviceIds.add(device.id)) throw new IllegalStateException("duplicate device " + device.id);
        Set<String> signalIds = new LinkedHashSet<>();
        for (Signal signal : signals) {
            if (!signalIds.add(signal.id)) throw new IllegalStateException("duplicate signal " + signal.id);
            if (signal.deviceId != null && !deviceIds.contains(signal.deviceId)) throw new IllegalStateException("unknown device " + signal.deviceId);
        }
    }

    private Set<String> signalIds() {
        Set<String> ids = new LinkedHashSet<>();
        for (Signal signal : signals) ids.add(signal.id);
        return ids;
    }

    private void ensureNotStarted() {
        if (running.get()) throw new IllegalStateException("catalog cannot change after start");
    }

    private void closeSocket(Socket candidate) {
        if (candidate == null) return;
        if (socket == candidate) socket = null;
        try { candidate.close(); } catch (IOException ignored) { }
    }

    private static Object jsonValue(Object value) throws JSONException {
        if (value == null) return JSONObject.NULL;
        if (value instanceof Double && !Double.isFinite((Double) value)) return JSONObject.NULL;
        if (value instanceof Float && !Float.isFinite((Float) value)) return JSONObject.NULL;
        if (value instanceof Number || value instanceof String || value instanceof Boolean || value instanceof JSONObject || value instanceof JSONArray) return value;
        return String.valueOf(value);
    }

    private static final class Device {
        final String id, label, subsystem, deviceType;
        Device(String id, String label, String subsystem, String deviceType) { this.id = text(id); this.label = text(label); this.subsystem = text(subsystem); this.deviceType = text(deviceType); }
        JSONObject toJson() throws JSONException { JSONObject value = new JSONObject(); value.put("id", id); value.put("label", label); value.put("subsystem", subsystem); value.put("deviceType", deviceType); return value; }
    }

    private static final class Signal {
        final String id, label, deviceId, quantity, unit, valueType, role;
        final double sampleHintHz;
        Signal(String id, String label, String deviceId, String quantity, String unit, String valueType, String role, double sampleHintHz) {
            this.id = text(id); this.label = text(label); this.deviceId = deviceId; this.quantity = text(quantity); this.unit = text(unit); this.valueType = text(valueType); this.role = text(role); this.sampleHintHz = sampleHintHz;
        }
        JSONObject toJson() throws JSONException { JSONObject value = new JSONObject(); value.put("id", id); value.put("label", label); if (deviceId != null) value.put("deviceId", deviceId); value.put("quantity", quantity); value.put("unit", unit); value.put("valueType", valueType); value.put("role", role); if (sampleHintHz > 0) value.put("sampleHintHz", sampleHintHz); return value; }
    }

    private static final class Sample {
        final long robotTimeNs, sequence;
        final Map<String, Object> values;
        Sample(long robotTimeNs, long sequence, Map<String, Object> values) { this.robotTimeNs = robotTimeNs; this.sequence = sequence; this.values = values; }
        JSONObject toJson() throws JSONException { JSONObject data = new JSONObject(); data.put("sampleSequence", Long.toString(sequence)); data.put("schemaRevision", 1); JSONObject encoded = new JSONObject(); for (Map.Entry<String, Object> entry : values.entrySet()) encoded.put(entry.getKey(), jsonValue(entry.getValue())); data.put("values", encoded); return data; }
    }

    private static final class Connection {
        final String connectionId;
        long nextSequence;
        Connection(String connectionId) { this.connectionId = connectionId; }
    }

    private static String text(String value) {
        if (value == null || value.trim().isEmpty()) throw new IllegalArgumentException("catalog text must not be blank");
        return value;
    }
}
