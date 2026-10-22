package org.firstinspires.ftc.teamcode;

import com.qualcomm.robotcore.eventloop.opmode.OpMode;
import com.qualcomm.robotcore.eventloop.opmode.TeleOp;

import org.firstinspires.ftc.teamcode.data.DataTransportMode;

/** Select the data destination for subsequently initialized OpModes. */
@TeleOp(name = "Data Transport Setup", group = "FTC Advanced")
public final class DataTransportSetup extends OpMode {
    private DataTransportMode selected;
    private boolean previousLeft;
    private boolean previousRight;
    private boolean saved;

    @Override
    public void init() {
        selected = DataTransportMode.current();
        render();
    }

    @Override
    public void init_loop() {
        if (gamepad1.dpad_left && !previousLeft) selected = previous(selected);
        if (gamepad1.dpad_right && !previousRight) selected = next(selected);
        previousLeft = gamepad1.dpad_left;
        previousRight = gamepad1.dpad_right;
        render();
    }

    @Override
    public void start() {
        saved = DataTransportMode.select(selected);
        render();
    }

    @Override
    public void loop() {
        render();
    }

    private void render() {
        telemetry.clearAll();
        telemetry.addLine("FTC Advanced data transport");
        telemetry.addData("Selected mode", selected);
        if (!saved) {
            telemetry.addLine("Use D-pad LEFT/RIGHT, then press START to save.");
        } else if (selected == DataTransportMode.LOCAL_MATCH) {
            telemetry.addLine("SAVED: local recording only; laptop stream is hard disabled.");
        } else if (selected == DataTransportMode.OFF) {
            telemetry.addLine("SAVED: no data thread, discovery, TCP, or recording.");
        } else {
            telemetry.addLine("SAVED: pit TCP stream enabled. Do not use during a match.");
        }
        telemetry.update();
    }

    private static DataTransportMode next(DataTransportMode mode) {
        DataTransportMode[] modes = DataTransportMode.values();
        return modes[(mode.ordinal() + 1) % modes.length];
    }

    private static DataTransportMode previous(DataTransportMode mode) {
        DataTransportMode[] modes = DataTransportMode.values();
        return modes[(mode.ordinal() + modes.length - 1) % modes.length];
    }
}
