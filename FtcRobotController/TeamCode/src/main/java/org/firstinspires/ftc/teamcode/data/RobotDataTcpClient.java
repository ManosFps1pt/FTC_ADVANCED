package org.firstinspires.ftc.teamcode.data;

import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedOutputStream;
import java.io.Closeable;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.Enumeration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.LinkedBlockingDeque;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Non-blocking OpMode-side publisher for the independent robot-data TCP stream.
 *
 * <p>The OpMode only copies values into a bounded queue. A dedicated daemon thread owns
 * connection attempts, JSON serialization, socket writes, heartbeats, and reconnection. If the
 * laptop is unavailable and the queue fills, the oldest unsent sample is discarded so robot
 * control can continue and the newest state remains available.</p>
 *
 * <p>Protocol version 1 uses a four-byte unsigned big-endian length followed by one UTF-8 JSON
 * object. It is a transport-test format; the framing allows a later protobuf payload without
 * changing the threading or connection lifecycle.</p>
 */
public final class RobotDataTcpClient implements Closeable {
    public static final int DEFAULT_PORT = 5810;
    public static final int DEFAULT_DISCOVERY_PORT = 5811;

    private static final int PROTOCOL_VERSION = 1;
    private static final String DISCOVERY_REQUEST_KIND = "where_is_data_server";
    private static final String DISCOVERY_RESPONSE_KIND = "data_server";
    private static final int DEFAULT_QUEUE_CAPACITY = 256;
    private static final int CONNECT_TIMEOUT_MS = 1_000;
    private static final int DISCOVERY_TIMEOUT_MS = 1_000;
    private static final long HEARTBEAT_INTERVAL_NS = 1_000_000_000L;
    private static final long INITIAL_RECONNECT_DELAY_MS = 250;
    private static final long MAX_RECONNECT_DELAY_MS = 2_000;

    private final String host;
    private final int port;
    private final int discoveryPort;
    private final String opModeName;
    private final String sessionId = UUID.randomUUID().toString();
    private final LinkedBlockingDeque<OutboundPacket> outbound;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicBoolean connected = new AtomicBoolean(false);
    private final AtomicLong nextSampleSequence = new AtomicLong();
    private final AtomicLong nextConnectionSequence = new AtomicLong();
    private final AtomicLong droppedPackets = new AtomicLong();

    private volatile Thread workerThread;
    private volatile Socket socket;
    private volatile String lastError;

    public RobotDataTcpClient(String host, int port, String opModeName) {
        this(host, port, 0, opModeName, DEFAULT_QUEUE_CAPACITY);
    }

    public RobotDataTcpClient(String host, int port, String opModeName, int queueCapacity) {
        this(host, port, 0, opModeName, queueCapacity);
    }

    /**
     * Discover the laptop on the current robot network before opening the TCP stream.
     * The laptop must run {@code RobotDataTcpServer}, which replies to the UDP broadcast.
     */
    public RobotDataTcpClient(int discoveryPort, int port, String opModeName) {
        this(null, port, discoveryPort, opModeName, DEFAULT_QUEUE_CAPACITY);
    }

    private RobotDataTcpClient(
            String host,
            int port,
            int discoveryPort,
            String opModeName,
            int queueCapacity) {
        if (host != null && host.trim().isEmpty()) {
            throw new IllegalArgumentException("host must not be blank");
        }
        if (port <= 0 || port > 65_535) {
            throw new IllegalArgumentException("port must be between 1 and 65535");
        }
        if (host == null && (discoveryPort <= 0 || discoveryPort > 65_535)) {
            throw new IllegalArgumentException("discoveryPort must be between 1 and 65535");
        }
        if (opModeName == null || opModeName.trim().isEmpty()) {
            throw new IllegalArgumentException("opModeName must not be blank");
        }
        if (queueCapacity <= 0) {
            throw new IllegalArgumentException("queueCapacity must be positive");
        }

        this.host = host;
        this.port = port;
        this.discoveryPort = discoveryPort;
        this.opModeName = opModeName;
        this.outbound = new LinkedBlockingDeque<>(queueCapacity);
    }

    /** Start the connector thread without waiting for the laptop. */
    public synchronized void start() {
        if (!running.compareAndSet(false, true)) {
            return;
        }
        Thread thread = new Thread(this::runConnector, "RobotDataTcpClient");
        thread.setDaemon(true);
        workerThread = thread;
        thread.start();
    }

    /**
     * Copy and enqueue one coherent robot sample.
     *
     * @return true when this sample is queued; false when the session is closed
     */
    public boolean publishSample(Map<String, ?> values) {
        if (!running.get()) {
            return false;
        }

        Map<String, Object> copy = new LinkedHashMap<>();
        for (Map.Entry<String, ?> entry : values.entrySet()) {
            if (entry.getKey() == null) {
                throw new IllegalArgumentException("sample keys must not be null");
            }
            copy.put(entry.getKey(), entry.getValue());
        }
        OutboundPacket packet = new OutboundPacket(
                "sample",
                SystemClock.elapsedRealtimeNanos(),
                nextSampleSequence.getAndIncrement(),
                copy);

        if (outbound.offerLast(packet)) {
            return true;
        }

        // Keep recent state when the laptop is disconnected or slow. There is a small race in
        // which the writer can free a slot between offer and poll; both paths remain bounded.
        OutboundPacket discarded = outbound.pollFirst();
        if (discarded != null) {
            droppedPackets.incrementAndGet();
        }
        if (outbound.offerLast(packet)) {
            return true;
        }

        droppedPackets.incrementAndGet();
        return false;
    }

    public boolean isConnected() {
        return connected.get();
    }

    public long getDroppedPacketCount() {
        return droppedPackets.get();
    }

    public int getQueuedPacketCount() {
        return outbound.size();
    }

    public String getLastError() {
        return lastError;
    }

    public String getSessionId() {
        return sessionId;
    }

    @Override
    public synchronized void close() {
        if (!running.getAndSet(false)) {
            return;
        }

        connected.set(false);
        closeSocket(socket);
        Thread thread = workerThread;
        if (thread != null) {
            thread.interrupt();
            if (thread != Thread.currentThread()) {
                try {
                    thread.join(250);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                }
            }
        }
        workerThread = null;
        outbound.clear();
    }

    private void runConnector() {
        long reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
        try {
            while (running.get()) {
                InetSocketAddress endpoint;
                try {
                    endpoint = host == null
                            ? discoverDataServer()
                            : new InetSocketAddress(host, port);
                } catch (IOException | JSONException error) {
                    if (running.get()) {
                        lastError = error.getClass().getSimpleName() + ": " + error.getMessage();
                    }
                    if (running.get() && !sleepForReconnect(reconnectDelayMs)) {
                        break;
                    }
                    reconnectDelayMs = Math.min(reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS);
                    continue;
                }
                Socket activeSocket = new Socket();
                socket = activeSocket;
                if (!running.get()) {
                    closeSocket(activeSocket);
                    break;
                }
                try {
                    activeSocket.connect(endpoint, CONNECT_TIMEOUT_MS);
                    if (!running.get()) {
                        continue;
                    }
                    activeSocket.setKeepAlive(true);
                    activeSocket.setTcpNoDelay(true);
                    connected.set(true);
                    lastError = null;
                    reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
                    streamPackets(activeSocket);
                } catch (IOException | JSONException error) {
                    if (running.get()) {
                        lastError = error.getClass().getSimpleName() + ": " + error.getMessage();
                    }
                } finally {
                    connected.set(false);
                    closeSocket(activeSocket);
                }

                if (running.get() && !sleepForReconnect(reconnectDelayMs)) {
                    break;
                }
                reconnectDelayMs = Math.min(reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS);
            }
        } finally {
            connected.set(false);
            closeSocket(socket);
        }
    }

    private void streamPackets(Socket activeSocket) throws IOException, JSONException {
        DataOutputStream output = new DataOutputStream(
                new BufferedOutputStream(activeSocket.getOutputStream()));
        writeEnvelope(output, "hello", SystemClock.elapsedRealtimeNanos(), helloData());

        long lastHeartbeatNs = SystemClock.elapsedRealtimeNanos();
        while (running.get() && !activeSocket.isClosed()) {
            OutboundPacket packet;
            try {
                packet = outbound.pollFirst(250, TimeUnit.MILLISECONDS);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return;
            }

            if (packet != null) {
                try {
                    writeEnvelope(
                            output,
                            packet.kind,
                            packet.robotElapsedNs,
                            packet.toJson(droppedPackets.get()));
                } catch (IOException | JSONException error) {
                    // Delivery is ambiguous if the socket fails during a write. Retrying the same
                    // sample can create a duplicate, so the sample_sequence is retained for later
                    // de-duplication by the recorder.
                    if (!outbound.offerFirst(packet)) {
                        droppedPackets.incrementAndGet();
                    }
                    throw error;
                }
            }

            long now = SystemClock.elapsedRealtimeNanos();
            if (now - lastHeartbeatNs >= HEARTBEAT_INTERVAL_NS) {
                JSONObject heartbeat = new JSONObject();
                heartbeat.put("queued_packets", outbound.size());
                heartbeat.put("dropped_packets", droppedPackets.get());
                writeEnvelope(output, "heartbeat", now, heartbeat);
                lastHeartbeatNs = now;
            }
        }
    }

    private InetSocketAddress discoverDataServer() throws IOException, JSONException {
        String requestId = UUID.randomUUID().toString();
        JSONObject request = new JSONObject();
        request.put("protocol_version", PROTOCOL_VERSION);
        request.put("kind", DISCOVERY_REQUEST_KIND);
        request.put("request_id", requestId);
        byte[] encodedRequest = request.toString().getBytes(StandardCharsets.UTF_8);

        try (DatagramSocket discoverySocket = new DatagramSocket()) {
            discoverySocket.setBroadcast(true);
            discoverySocket.setSoTimeout(DISCOVERY_TIMEOUT_MS);
            for (InetAddress broadcast : broadcastAddresses()) {
                discoverySocket.send(new DatagramPacket(
                        encodedRequest, encodedRequest.length, broadcast, discoveryPort));
            }

            byte[] responseBytes = new byte[1024];
            DatagramPacket responsePacket = new DatagramPacket(responseBytes, responseBytes.length);
            while (running.get()) {
                discoverySocket.receive(responsePacket);
                JSONObject response = new JSONObject(new String(
                        responsePacket.getData(),
                        responsePacket.getOffset(),
                        responsePacket.getLength(),
                        StandardCharsets.UTF_8));
                if (response.getInt("protocol_version") != PROTOCOL_VERSION
                        || !DISCOVERY_RESPONSE_KIND.equals(response.optString("kind"))
                        || !requestId.equals(response.optString("request_id"))) {
                    continue;
                }
                String discoveredHost = response.optString("host");
                int discoveredPort = response.optInt("port", -1);
                if (discoveredHost.isEmpty() || discoveredPort <= 0 || discoveredPort > 65_535) {
                    throw new IOException("data-server discovery reply was malformed");
                }
                return new InetSocketAddress(discoveredHost, discoveredPort);
            }
        }
        throw new IOException("data-server discovery stopped");
    }

    private Iterable<InetAddress> broadcastAddresses() throws IOException {
        LinkedHashMap<String, InetAddress> addresses = new LinkedHashMap<>();
        Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
        if (interfaces != null) {
            for (NetworkInterface networkInterface : Collections.list(interfaces)) {
                if (!networkInterface.isUp() || networkInterface.isLoopback()) {
                    continue;
                }
                for (InterfaceAddress interfaceAddress : networkInterface.getInterfaceAddresses()) {
                    InetAddress broadcast = interfaceAddress.getBroadcast();
                    if (broadcast != null) {
                        addresses.put(broadcast.getHostAddress(), broadcast);
                    }
                }
            }
        }
        InetAddress globalBroadcast = InetAddress.getByName("255.255.255.255");
        addresses.put(globalBroadcast.getHostAddress(), globalBroadcast);
        return addresses.values();
    }

    private JSONObject helloData() throws JSONException {
        JSONObject data = new JSONObject();
        data.put("client", "FTC Control Hub");
        data.put("opmode_name", opModeName);
        data.put("queue_capacity", outbound.remainingCapacity() + outbound.size());
        data.put("dropped_packets", droppedPackets.get());
        return data;
    }

    private void writeEnvelope(
            DataOutputStream output,
            String kind,
            long robotElapsedNs,
            JSONObject data) throws IOException, JSONException {
        JSONObject envelope = new JSONObject();
        envelope.put("protocol_version", PROTOCOL_VERSION);
        envelope.put("session_id", sessionId);
        envelope.put("connection_sequence", nextConnectionSequence.getAndIncrement());
        envelope.put("robot_elapsed_ns", robotElapsedNs);
        envelope.put("kind", kind);
        envelope.put("data", data);

        byte[] encoded = envelope.toString().getBytes(StandardCharsets.UTF_8);
        output.writeInt(encoded.length);
        output.write(encoded);
        output.flush();
    }

    private boolean sleepForReconnect(long delayMs) {
        try {
            Thread.sleep(delayMs);
            return true;
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            return false;
        }
    }

    private void closeSocket(Socket candidate) {
        if (candidate == null) {
            return;
        }
        if (socket == candidate) {
            socket = null;
        }
        try {
            candidate.close();
        } catch (IOException ignored) {
            // Closing is best-effort and must not escape into an OpMode callback.
        }
    }

    private static Object toJsonValue(Object value) throws JSONException {
        if (value == null) {
            return JSONObject.NULL;
        }
        if (value instanceof JSONObject || value instanceof JSONArray
                || value instanceof Boolean || value instanceof String) {
            return value;
        }
        if (value instanceof Number) {
            if (value instanceof Double && !Double.isFinite((Double) value)) {
                return JSONObject.NULL;
            }
            if (value instanceof Float && !Float.isFinite((Float) value)) {
                return JSONObject.NULL;
            }
            return value;
        }
        if (value instanceof Map<?, ?>) {
            JSONObject object = new JSONObject();
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                object.put(String.valueOf(entry.getKey()), toJsonValue(entry.getValue()));
            }
            return object;
        }
        if (value instanceof Iterable<?>) {
            JSONArray array = new JSONArray();
            for (Object item : (Iterable<?>) value) {
                array.put(toJsonValue(item));
            }
            return array;
        }
        return String.valueOf(value);
    }

    private static final class OutboundPacket {
        final String kind;
        final long robotElapsedNs;
        final long sampleSequence;
        final Map<String, Object> values;

        OutboundPacket(
                String kind,
                long robotElapsedNs,
                long sampleSequence,
                Map<String, Object> values) {
            this.kind = kind;
            this.robotElapsedNs = robotElapsedNs;
            this.sampleSequence = sampleSequence;
            this.values = values;
        }

        JSONObject toJson(long droppedPacketCount) throws JSONException {
            JSONObject data = new JSONObject();
            data.put("sample_sequence", sampleSequence);
            data.put("dropped_packets", droppedPacketCount);
            JSONObject encodedValues = new JSONObject();
            for (Map.Entry<String, Object> entry : values.entrySet()) {
                encodedValues.put(entry.getKey(), toJsonValue(entry.getValue()));
            }
            data.put("values", encodedValues);
            return data;
        }
    }
}
