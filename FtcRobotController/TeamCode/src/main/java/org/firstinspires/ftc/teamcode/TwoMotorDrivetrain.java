package org.firstinspires.ftc.teamcode;

import com.qualcomm.robotcore.eventloop.opmode.OpMode;
import com.qualcomm.robotcore.eventloop.opmode.TeleOp;
import com.qualcomm.robotcore.hardware.DcMotor;
import com.qualcomm.robotcore.hardware.DcMotorEx;
import com.qualcomm.robotcore.hardware.Gamepad;
import com.qualcomm.robotcore.hardware.VoltageSensor;
import com.qualcomm.robotcore.util.ElapsedTime;
import com.qualcomm.robotcore.util.Range;

import org.firstinspires.ftc.teamcode.data.StructuredRobotDataClient;


/**
 * Small two-motor tank drivetrain for the configured {@code leftMotor} and
 * {@code rightMotor} REV motors.
 *
 * <p>Gamepad 1 left and right stick Y axes control the left and right sides.
 * The sticks are negated so pushing forward commands positive drive power.
 * Both motors run with encoder feedback enabled; the structured telemetry
 * stream records encoder position and {@link DcMotorEx#getVelocity()} in
 * encoder ticks per second on every OpMode loop.</p>
 */
@TeleOp(name = "Two Motor Drivetrain", group = "Drive")
public class TwoMotorDrivetrain extends OpMode {
    private static final String LEFT_MOTOR_NAME = "leftMotor";
    private static final String RIGHT_MOTOR_NAME = "rightMotor";
    private static final String LEFT_DEVICE_ID = "drive.left";
    private static final String RIGHT_DEVICE_ID = "drive.right";

    private final ElapsedTime runtime = new ElapsedTime();
    private DcMotorEx leftMotor;
    private DcMotorEx rightMotor;
    private VoltageSensor batteryVoltageSensor;
    private StructuredRobotDataClient dataClient;
    private double leftCommandedPower;
    private double rightCommandedPower;

    @Override
    public void init() {
        leftMotor = hardwareMap.get(DcMotorEx.class, LEFT_MOTOR_NAME);
        rightMotor = hardwareMap.get(DcMotorEx.class, RIGHT_MOTOR_NAME);
        for (VoltageSensor sensor : hardwareMap.voltageSensor) {
            batteryVoltageSensor = sensor;
            break;
        }

        // The two motor shafts face opposite directions on a differential drive.
        leftMotor.setDirection(DcMotor.Direction.REVERSE);
        rightMotor.setDirection(DcMotor.Direction.FORWARD);
        leftMotor.setZeroPowerBehavior(DcMotor.ZeroPowerBehavior.BRAKE);
        rightMotor.setZeroPowerBehavior(DcMotor.ZeroPowerBehavior.BRAKE);
        leftMotor.setMode(DcMotor.RunMode.RUN_WITHOUT_ENCODER);
        rightMotor.setMode(DcMotor.RunMode.RUN_WITHOUT_ENCODER);
        stopDrive();

        dataClient = new StructuredRobotDataClient("Two Motor Drivetrain")
                .addRuntime(runtime);
        if (batteryVoltageSensor != null) dataClient.addVoltageSensor(batteryVoltageSensor);
        dataClient.addMotor(LEFT_DEVICE_ID, "Left Drive Motor", leftMotor,
                        () -> leftCommandedPower)
                .addMotor(RIGHT_DEVICE_ID, "Right Drive Motor", rightMotor,
                        () -> rightCommandedPower)
                .addGamepads(gamepad1, gamepad2);
        dataClient.start();

        telemetry.setMsTransmissionInterval(50);
        telemetry.addLine("Two-motor drivetrain ready");
        telemetry.addData("Motors", "%s, %s", LEFT_MOTOR_NAME, RIGHT_MOTOR_NAME);
        telemetry.addData("Encoder mode", "RUN_USING_ENCODER");
        telemetry.addLine("Press START; gamepad 1 sticks drive left/right sides.");
        telemetry.update();
    }

    @Override
    public void start() {
        runtime.reset();
        leftCommandedPower = 0.0;
        rightCommandedPower = 0.0;
        stopDrive();
    }

    @Override
    public void loop() {
        leftCommandedPower = Range.clip(-gamepad1.left_stick_y, -1.0, 1.0);
        rightCommandedPower = Range.clip(-gamepad1.right_stick_y, -1.0, 1.0);
        leftMotor.setPower(leftCommandedPower);
        rightMotor.setPower(rightCommandedPower);
        telemetry.addData("gamepad1 sticks", "LX %.2f  LY %.2f  RX %.2f  RY %.2f",
                gamepad1.left_stick_x, gamepad1.left_stick_y,
                gamepad1.right_stick_x, gamepad1.right_stick_y);
        telemetry.addData("commanded power", "L %.2f  R %.2f",
                leftCommandedPower, rightCommandedPower);
        telemetry.addData("Data TCP", "%s  queued=%d  dropped=%d",
                dataClient.isConnected() ? "CONNECTED" : "CONNECTING",
                dataClient.getQueuedPacketCount(), dataClient.getDroppedPacketCount());
        if (!dataClient.isConnected() && dataClient.getLastError() != null) {
            telemetry.addData("Data TCP error", dataClient.getLastError());
        }

        dataClient.publishLoop();
        telemetry.update();
    }

    @Override
    public void stop() {
        stopDrive();
        if (dataClient != null) {
            dataClient.close();
            dataClient = null;
        }
    }

    private void stopDrive() {
        if (leftMotor != null) leftMotor.setPower(0.0);
        if (rightMotor != null) rightMotor.setPower(0.0);
    }

}
