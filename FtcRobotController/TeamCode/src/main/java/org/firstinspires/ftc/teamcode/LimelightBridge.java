package org.firstinspires.ftc.teamcode;

import android.content.Context;

import com.qualcomm.robotcore.util.RobotLog;
import com.qualcomm.robotcore.util.WebHandlerManager;

import org.firstinspires.ftc.ftccommon.external.WebHandlerRegistrar;
import org.firstinspires.ftc.robotcore.internal.webserver.WebHandler;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

import fi.iki.elonen.NanoHTTPD;

/**
 * Read-only HTTP bridge between the Robot Controller Wi-Fi network and a USB-connected Limelight 3A.
 *
 * <p>The 3A lives on the Robot Controller's separate USB-Ethernet network, so a laptop connected to
 * the Robot Controller Wi-Fi cannot contact it directly. These handlers run on the Robot Controller and
 * fetch Limelight's REST endpoint on behalf of a laptop tool.</p>
 *
 * <p>This first version intentionally does not proxy the Limelight settings UI or stream video.
 * It is a small communication test with bounded response sizes and short timeouts.</p>
 */
public final class LimelightBridge {
    /**
     * Address observed for the team's directly connected 3A. Change this only if the Limelight's
     * configured USB-Ethernet address changes.
     */
    private static final String CAMERA_HOST = "172.29.0.1";
    private static final int CAMERA_REST_PORT = 5807;
    private static final int CONNECT_TIMEOUT_MS = 500;
    private static final int READ_TIMEOUT_MS = 750;
    private static final int MAX_RESPONSE_BYTES = 256 * 1024;

    private static final String HEALTH_PATH = "/api/limelight/health";
    private static final String RESULTS_PATH = "/api/limelight/results";
    private static final String ROBOT_ERROR_PATH = "/api/robot/error";

    private LimelightBridge() {
    }

    /** Register the bridge when the Robot Controller web server starts. */
    @WebHandlerRegistrar
    public static void registerWebHandlers(Context context, WebHandlerManager manager) {
        manager.register(HEALTH_PATH, new WebHandler() {
            @Override
            public NanoHTTPD.Response getResponse(NanoHTTPD.IHTTPSession session) {
                return healthResponse();
            }
        });
        manager.register(RESULTS_PATH, new WebHandler() {
            @Override
            public NanoHTTPD.Response getResponse(NanoHTTPD.IHTTPSession session) {
                return resultsResponse();
            }
        });
        manager.register(ROBOT_ERROR_PATH, new WebHandler() {
            @Override
            public NanoHTTPD.Response getResponse(NanoHTTPD.IHTTPSession session) {
                return robotErrorResponse();
            }
        });
    }

    private static NanoHTTPD.Response healthResponse() {
        try {
            JSONObject rawResults = fetchResults();
            JSONObject results = extractResultObject(rawResults);
            JSONObject health = new JSONObject();
            health.put("connected", true);
            health.put("cameraHost", CAMERA_HOST);
            health.put("validTarget", results.optInt("tv", results.optInt("v", 0)) != 0);
            health.put("pipeline", results.opt("pID"));
            health.put("tx", results.opt("tx"));
            health.put("ty", results.opt("ty"));
            health.put("targetArea", results.opt("ta"));
            health.put("captureLatencyMs", results.opt("cl"));
            health.put("targetingLatencyMs", results.opt("tl"));
            return jsonResponse(NanoHTTPD.Response.Status.OK, health);
        } catch (IOException | JSONException error) {
            return bridgeError(error);
        }
    }

    private static NanoHTTPD.Response resultsResponse() {
        try {
            JSONObject payload = new JSONObject();
            payload.put("connected", true);
            payload.put("cameraHost", CAMERA_HOST);
            payload.put("results", fetchResults());
            return jsonResponse(NanoHTTPD.Response.Status.OK, payload);
        } catch (IOException | JSONException error) {
            return bridgeError(error);
        }
    }

    /**
     * Exposes the same persistent global error that the FTC SDK sends to the Driver Station.
     *
     * <p>This is deliberately read-only. It neither sets nor clears the SDK error state.</p>
     */
    private static NanoHTTPD.Response robotErrorResponse() {
        String message = RobotLog.getGlobalErrorMsg();
        boolean active = message != null && !message.trim().isEmpty();
        JSONObject payload = new JSONObject();
        try {
            payload.put("active", active);
            payload.put("message", active ? message : JSONObject.NULL);
        } catch (JSONException ignored) {
            // The payload contains only strings and booleans.
        }
        return jsonResponse(NanoHTTPD.Response.Status.OK, payload);
    }

    private static JSONObject fetchResults() throws IOException, JSONException {
        URL url = new URL("http://" + CAMERA_HOST + ":" + CAMERA_REST_PORT + "/results");
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setRequestMethod("GET");
        connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
        connection.setReadTimeout(READ_TIMEOUT_MS);
        connection.setUseCaches(false);
        connection.setRequestProperty("Accept", "application/json");

        try {
            int statusCode = connection.getResponseCode();
            if (statusCode != HttpURLConnection.HTTP_OK) {
                throw new IOException("Limelight returned HTTP " + statusCode);
            }
            try (InputStream input = connection.getInputStream()) {
                return new JSONObject(readUtf8(input));
            }
        } finally {
            connection.disconnect();
        }
    }

    private static String readUtf8(InputStream input) throws IOException {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] buffer = new byte[4096];
        int count;
        while ((count = input.read(buffer)) != -1) {
            if (output.size() + count > MAX_RESPONSE_BYTES) {
                throw new IOException("Limelight REST response exceeded " + MAX_RESPONSE_BYTES + " bytes");
            }
            output.write(buffer, 0, count);
        }
        return output.toString(StandardCharsets.UTF_8.name());
    }

    private static JSONObject extractResultObject(JSONObject rawResults) {
        JSONObject nestedResults = rawResults.optJSONObject("Results");
        return nestedResults != null ? nestedResults : rawResults;
    }

    private static NanoHTTPD.Response bridgeError(Exception error) {
        JSONObject payload = new JSONObject();
        try {
            payload.put("connected", false);
            payload.put("cameraHost", CAMERA_HOST);
            payload.put("error", error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage());
        } catch (JSONException ignored) {
            // JSONObject only rejects non-finite numbers; this payload contains strings and booleans.
        }
        return jsonResponse(NanoHTTPD.Response.Status.SERVICE_UNAVAILABLE, payload);
    }

    private static NanoHTTPD.Response jsonResponse(NanoHTTPD.Response.IStatus status, JSONObject payload) {
        NanoHTTPD.Response response = NanoHTTPD.newFixedLengthResponse(
                status,
                "application/json; charset=utf-8",
                payload.toString());
        response.addHeader("Cache-Control", "no-store");
        return response;
    }
}
