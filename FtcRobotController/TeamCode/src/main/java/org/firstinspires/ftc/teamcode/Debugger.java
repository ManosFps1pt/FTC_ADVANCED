package org.firstinspires.ftc.teamcode;

import com.qualcomm.robotcore.eventloop.opmode.TeleOp;

import org.firstinspires.ftc.teamcode.data.protocol.DebugRiskClass;

import java.util.Collections;

/**
 * General FTC Advanced debugging selector.
 *
 * <p>Change {@link #FREE_SPIN_MOTOR_NAME} to the configured hardware name of
 * a motor that is safe to run without a mechanism attached. The robot-side
 * risk classification is intentionally explicit; the laptop cannot promote a
 * locked motor into an actuator-capable tool.</p>
 */
@TeleOp(name = "FTC Advanced Debugger", group = "FTC Advanced")
public final class Debugger extends SelectableOpMode {
    /** Team-specific hardware configuration for the first free-spin test. */
    public static String FREE_SPIN_MOTOR_NAME = "debugMotor";

    @Override
    protected void configure(Registry registry) {
        registry.folder("motor-tests", "Motor Tests", motors -> {
            motors.folder("motors.freeSpin", "Free-spin Motors", freeSpin -> {
                freeSpin.tool(
                        "motors.freeSpin.power",
                        "Simple Motor Power",
                        "Bounded forward/reverse power control for a registered free-spin motor.",
                        DebugRiskClass.DEBUG_FREE_SPIN,
                        0.35,
                        true,
                        "",
                        () -> new MotorPowerTool(FREE_SPIN_MOTOR_NAME),
                        Collections.singletonList(new SelectableOpMode.Parameter(
                                "motor.power",
                                "Motor Power",
                                "Normalized motor power. Negative values reverse direction.",
                                0.0,
                                -0.35,
                                0.35,
                                0.01,
                                "normalized",
                                true,
                                false)),
                        Collections.singletonList(new SelectableOpMode.Command(
                                "motor.stop", "Stop Motor", "Immediately set motor power to zero.", true)));
            });
            motors.folder("motors.drivetrain", "Drivetrain Motors", drivetrain -> {
                drivetrain.tool(
                        "motors.drivetrain.locked",
                        "Drivetrain Test (floor required)",
                        "Reserved for a future guarded drivetrain test.",
                        DebugRiskClass.DEBUG_DRIVETRAIN,
                        0.15,
                        false,
                        "Place the robot on the floor and enable the dedicated drivetrain procedure first.",
                        null,
                        Collections.<SelectableOpMode.Parameter>emptyList(),
                        Collections.<SelectableOpMode.Command>emptyList());
            });
            motors.folder("motors.mechanism", "Mechanism Motors", mechanism -> {
                mechanism.tool(
                        "motors.mechanism.locked",
                        "Mechanism Test (locked)",
                        "Mechanism actuator testing is intentionally unavailable in this first release.",
                        DebugRiskClass.DEBUG_MECHANISM,
                        0.0,
                        false,
                        "Mechanism motor testing is locked for safety.",
                        null,
                        Collections.<SelectableOpMode.Parameter>emptyList(),
                        Collections.<SelectableOpMode.Command>emptyList());
            });
        });
    }
}
