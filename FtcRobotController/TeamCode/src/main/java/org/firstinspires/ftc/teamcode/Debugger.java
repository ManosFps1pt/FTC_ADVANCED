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
    public static String FREE_SPIN_MOTOR_NAME = "leftMotor";
    /** Set from the actual mechanism's acceptable current before enabling its benchmark. */
    public static double FREE_SPIN_CURRENT_LIMIT_AMPS = 7.0;
    /** Counts per mechanism shaft revolution; zero keeps results in encoder ticks. */
    public static double FREE_SPIN_TICKS_PER_REVOLUTION = 0.0;
    public static boolean FREE_SPIN_REVERSE_ALLOWED = false;

    @Override
    protected void configure(Registry registry) {
        registry.folder("motor-tests", "Motor Tests", motors -> {
            motors.folder("motors.freeSpin", "Free-spin Motors", freeSpin -> {
                boolean configured=Double.isFinite(FREE_SPIN_CURRENT_LIMIT_AMPS) && FREE_SPIN_CURRENT_LIMIT_AMPS>0;
                freeSpin.tool("motors.freeSpin.friction", "Friction & Free-Spin Characterization",
                        "Breakaway, steady running losses and coast-down for "+FREE_SPIN_MOTOR_NAME,
                        DebugRiskClass.DEBUG_FREE_SPIN, 1, configured,
                        "Set FREE_SPIN_CURRENT_LIMIT_AMPS in Debugger.java for this mechanism before testing.",
                        () -> new FrictionTool("motors.freeSpin", "Free-spin Motor", FREE_SPIN_MOTOR_NAME,
                                1, FREE_SPIN_CURRENT_LIMIT_AMPS, FREE_SPIN_TICKS_PER_REVOLUTION, FREE_SPIN_REVERSE_ALLOWED),
                        Collections.emptyList(), java.util.Arrays.asList(
                                new Command("benchmark.run", "Run benchmark", "Execute the advertised benchmark inputs", false),
                                new Command("benchmark.abort", "Abort benchmark", "Stop output and retain acquired samples", false),
                                new Command("benchmark.keepalive", "Keepalive", "Maintain operator presence during execution", false)));
                freeSpin.tool(
                        "motors.freeSpin.power",
                        "Simple Motor Power",
                        "Bounded forward/reverse power control for a registered free-spin motor.",
                        DebugRiskClass.DEBUG_FREE_SPIN,
                        1,
                        true,
                        "",
                        () -> new MotorPowerTool(FREE_SPIN_MOTOR_NAME),
                        Collections.singletonList(new SelectableOpMode.Parameter(
                                "motor.power",
                                "Motor Power",
                                "Normalized motor power. Negative values reverse direction.",
                                0.0,
                                -1,
                                1,
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
