package org.firstinspires.ftc.teamcode;

import android.os.SystemClock;
import com.qualcomm.robotcore.hardware.DcMotor;
import com.qualcomm.robotcore.hardware.DcMotorEx;
import com.qualcomm.robotcore.hardware.VoltageSensor;
import org.firstinspires.ftc.robotcore.external.navigation.CurrentUnit;
import org.firstinspires.ftc.teamcode.debug.FrictionBenchmark;
import org.firstinspires.ftc.teamcode.data.protocol.*;
import java.util.*;

/** FTC adapter: acquisition on the OpMode loop, dataset delivery after motion. */
public final class FrictionTool implements SelectableOpMode.Tool {
    public static final String ID="motor.friction.v1";
    private final String mechanismId, label, motorName;
    private final double maxPower, currentLimit, ticksPerRevolution;
    private final boolean reverseAllowed;
    private SelectableOpMode.ToolContext context;
    private DcMotorEx motor;
    private VoltageSensor voltage;
    private DcMotor.RunMode savedMode;
    private DcMotor.ZeroPowerBehavior savedZero;
    private FrictionBenchmark run;
    private DebugRunHeader header;
    private String runId="";
    private final Set<String> executed=new HashSet<>();
    private boolean acknowledged=true, restored=true;
    private long generation=-1, lastStatus, lastTransfer;
    private int transfer=-1;
    private static final int CHUNK_SIZE=128;

    public FrictionTool(String mechanismId,String label,String motorName,double maxPower,double currentLimit,
                        double ticksPerRevolution,boolean reverseAllowed) {
        this.mechanismId=mechanismId; this.label=label; this.motorName=motorName; this.maxPower=maxPower;
        this.currentLimit=currentLimit; this.ticksPerRevolution=ticksPerRevolution; this.reverseAllowed=reverseAllowed;
    }
    @Override public void init(SelectableOpMode.ToolContext context) {
        this.context=context;
        if (!Double.isFinite(currentLimit) || currentLimit<=0) throw new IllegalArgumentException("Register a current cutoff before running this benchmark");
        motor=context.hardwareMap.get(DcMotorEx.class,motorName);
        for (VoltageSensor candidate:context.hardwareMap.voltageSensor) { voltage=candidate; break; }
        if (voltage==null) throw new IllegalStateException("Battery voltage sensor unavailable");
    }
    @Override public void start() { motor.setPower(0); }
    public static DebugBenchmarkDefinition definition(boolean reverseAllowed) {
        DebugBenchmarkDefinition.Builder b=DebugBenchmarkDefinition.newBuilder().setId(ID).setVersion(1)
                .setLabel("Friction & Free-Spin Characterization")
                .setDescription("Measure breakaway, steady voltage/current versus speed, and coast-down. Requires encoder feedback.")
                .setReverseAllowed(reverseAllowed).setMaxVoltage(12).addAnalyzerIds("friction.v1")
                .addInputs(DebugCommandArgumentDefinition.newBuilder().setId("repetitions").setLabel("Repetitions").setValueType(ValueType.INT64).setRequired(true).setMinFloat64(1).setMaxFloat64(5))
                .addInputs(DebugCommandArgumentDefinition.newBuilder().setId("max_voltage").setLabel("Maximum voltage").setValueType(ValueType.FLOAT64).setRequired(true).setMinFloat64(0.1).setMaxFloat64(12).setUnit("V"))
                .addInputs(DebugCommandArgumentDefinition.newBuilder().setId("reverse").setLabel("Also test reverse").setValueType(ValueType.BOOLEAN));
        String[] keys={"battery_voltage","duty","current","position","velocity","estimated_voltage","movement_onset","steady_window"};
        String[] units={"V","normalized","A","tick","tick/s","V","none","none"};
        for (int i=0;i<keys.length;i++) b.addChannels(Channel.newBuilder().setChannelId(i+1).setKey(keys[i])
                .setLabel(keys[i].replace('_',' ')).setUnit(units[i]).setValueType(i==3?ValueType.INT64:i>=6?ValueType.BOOLEAN:ValueType.FLOAT64));
        return b.build();
    }
    @Override public List<DebugBenchmarkDefinition> benchmarks() { return Collections.singletonList(definition(reverseAllowed)); }
    @Override public DebugCommandResponse command(DebugCommandRequest request) {
        String command=request.getCommandId();
        DebugCommandResponse.Builder reply=DebugCommandResponse.newBuilder().setRequestId(request.getRequestId())
                .setCommandId(command).setHandledAtRobotTimeNs(SystemClock.elapsedRealtimeNanos());
        try {
            if (command.equals("benchmark.keepalive") || command.equals("benchmark.abort")) {
                if (!request.getRequestId().equals(runId) || run==null) throw new IllegalArgumentException("Run is not active");
                if (command.endsWith("keepalive")) run.keepalive(SystemClock.elapsedRealtimeNanos());
                else stopOutput();
                return reply.setResult(DebugCommandResult.DEBUG_COMMAND_COMPLETED).build();
            }
            if (!command.equals("benchmark.run")) return null;
            UUID.fromString(request.getRequestId());
            if (executed.contains(request.getRequestId())) return reply.setResult(DebugCommandResult.DEBUG_COMMAND_DUPLICATE).setMessage("Run already accepted; it will not execute again").build();
            if (busy()) throw new IllegalArgumentException("A benchmark is running or awaiting data receipt");
            if (!context.dataClient.serverSupportsBenchmarks()) throw new IllegalArgumentException("Laptop does not support debug-runs-v1");
            Map<String,DebugCommandArgumentValue> args=new HashMap<>();
            for (DebugCommandArgumentValue arg:request.getArgumentsList()) {
                if (args.put(arg.getId(),arg)!=null || !Arrays.asList("repetitions","max_voltage","reverse").contains(arg.getId())) throw new IllegalArgumentException("Unknown or duplicate argument");
            }
            if (!args.containsKey("repetitions") || !args.get("repetitions").hasInt64Value()
                    || !args.containsKey("max_voltage") || !args.get("max_voltage").hasFloat64Value()
                    || (args.containsKey("reverse") && !args.get("reverse").hasBooleanValue())) throw new IllegalArgumentException("Invalid argument types");
            long count=args.get("repetitions").getInt64Value();
            double maxV=args.get("max_voltage").getFloat64Value();
            boolean reverse=args.containsKey("reverse") && args.get("reverse").getBooleanValue();
            if (count<1 || count>5 || !Double.isFinite(maxV) || maxV<0.1 || maxV>12 || (reverse && !reverseAllowed)) throw new IllegalArgumentException("Inputs exceed registered limits");
            savedMode=motor.getMode(); savedZero=motor.getZeroPowerBehavior(); restored=false;
            motor.setPower(0); motor.setMode(DcMotor.RunMode.RUN_WITHOUT_ENCODER); motor.setZeroPowerBehavior(DcMotor.ZeroPowerBehavior.FLOAT);
            long now=SystemClock.elapsedRealtimeNanos();
            run=new FrictionBenchmark(new FrictionBenchmark.Motor() {
                public FrictionBenchmark.Reading read() { return new FrictionBenchmark.Reading(voltage.getVoltage(),motor.getCurrent(CurrentUnit.AMPS),motor.getCurrentPosition(),motor.getVelocity()); }
                public void power(double duty) { motor.setPower(duty); }
            },(int)count,reverse,maxV,maxPower,currentLimit,now);
            runId=request.getRequestId(); executed.add(runId); acknowledged=false; transfer=-1; lastTransfer=0;
            header=DebugRunHeader.newBuilder().setRunId(runId).setMechanismId(mechanismId).setMechanismLabel(label)
                    .setMotorName(motorName).setBenchmark(definition(reverseAllowed)).addAllInputs(request.getArgumentsList())
                    .setStartedRobotNs(now).setTicksPerRevolution(ticksPerRevolution).setMaxPower(maxPower).setCurrentLimitAmps(currentLimit).build();
            return reply.setResult(DebugCommandResult.DEBUG_COMMAND_ACCEPTED).setMessage("Benchmark started").build();
        } catch (RuntimeException e) {
            if (run==null || !run.running()) restore();
            return reply.setResult(DebugCommandResult.DEBUG_COMMAND_REJECTED).setMessage(e.getMessage()==null?"Benchmark rejected":e.getMessage()).build();
        }
    }
    private void restore() {
        if (motor!=null && !restored) {
            motor.setPower(0); motor.setMode(savedMode); motor.setZeroPowerBehavior(savedZero); motor.setPower(0); restored=true;
        }
    }
    @Override public void loop() {
        if (run==null) return;
        long now=SystemClock.elapsedRealtimeNanos();
        try {
            if (run.running() && !context.dataClient.isConnected()) run.abort("TCP connection lost");
            run.tick(now);
        } catch (RuntimeException e) { run.abort("Sensor or motor failure: "+e.getClass().getSimpleName()); }
        if (!run.running()) restore();
        if (now-lastStatus>=200_000_000L) {
            lastStatus=now;
            context.dataClient.publishMessage(Envelope.newBuilder().setDebugRunStatus(DebugRunStatus.newBuilder()
                    .setRunId(runId).setState(run.running()?"running":acknowledged?"received":"transferring")
                    .setPhase(run.phase).setRepetition(run.repetition).setDirection(run.direction).setMessage(run.message)));
        }
        if (run.running() || acknowledged || !context.dataClient.isConnected()) return;
        long currentGeneration=context.dataClient.connectionGeneration();
        int chunks=(run.samples.size()+CHUNK_SIZE-1)/CHUNK_SIZE;
        if (generation!=currentGeneration || (transfer>chunks && now-lastTransfer>3_000_000_000L)) { generation=currentGeneration; transfer=-1; }
        Envelope.Builder envelope=Envelope.newBuilder();
        if (transfer<0) envelope.setDebugRunHeader(header);
        else if (transfer<chunks) {
            DebugRunChunk.Builder chunk=DebugRunChunk.newBuilder().setRunId(runId).setChunkIndex(transfer);
            int end=Math.min(run.samples.size(),(transfer+1)*CHUNK_SIZE);
            for (int i=transfer*CHUNK_SIZE;i<end;i++) chunk.addSamples(sample(i,run.samples.get(i)));
            envelope.setDebugRunChunk(chunk);
        } else if (transfer==chunks) envelope.setDebugRunEnd(DebugRunEnd.newBuilder().setRunId(runId)
                .setTotalChunks(chunks).setTotalSamples(run.samples.size()).setOutcome(run.outcome).setMessage(run.message));
        else return;
        if (context.dataClient.publishReliableMessage(envelope)) { transfer++; lastTransfer=now; }
    }
    private DebugRunSample sample(int index,FrictionBenchmark.Sample s) {
        DebugRunSample.Builder row=DebugRunSample.newBuilder().setSequence(index).setRobotTimeNs(s.time)
                .setPhase(s.phase).setRepetition(s.repetition).setDirection(s.direction).setOperatingPoint(s.point);
        row.addValues(number(1,s.reading.voltage)).addValues(number(2,s.duty)).addValues(number(3,s.reading.current))
                .addValues(ChannelValue.newBuilder().setChannelId(4).setInt64Value(s.reading.position))
                .addValues(number(5,s.reading.velocity)).addValues(number(6,s.duty*s.reading.voltage))
                .addValues(ChannelValue.newBuilder().setChannelId(7).setBooleanValue(s.onset))
                .addValues(ChannelValue.newBuilder().setChannelId(8).setBooleanValue(s.steady));
        return row.build();
    }
    private ChannelValue number(int id,double value) { return ChannelValue.newBuilder().setChannelId(id).setFloat64Value(value).build(); }
    @Override public void receive(Envelope e) {
        if (e.hasDebugRunAck() && e.getDebugRunAck().getRunId().equals(runId) && run!=null && !run.running()
                && e.getDebugRunAck().getTotalSamples()==run.samples.size()) acknowledged=true;
    }
    @Override public boolean busy() {
        if (run==null) return false;
        if (!acknowledged) return true;
        try { return !Double.isFinite(motor.getVelocity()) || Math.abs(motor.getVelocity())>=5; }
        catch (RuntimeException error) { return true; }
    }
    @Override public void stopOutput() { if (run!=null && run.running()) run.abort("Operator aborted benchmark"); restore(); }
    @Override public void stop() { stopOutput(); if (motor!=null) motor.setPower(0); }
    @Override public SelectableOpMode.ParameterResult setParameter(String id,double value,int ttl) { return SelectableOpMode.ParameterResult.rejected("READ_ONLY","Use benchmark inputs"); }
    @Override public double requestedOutput() { return run==null?0:run.duty(); }
    @Override public double appliedOutput() { return requestedOutput(); }
    @Override public long encoderPosition() { return motor==null?0:motor.getCurrentPosition(); }
    @Override public boolean outputAllowed() { return run!=null && run.running(); }
    @Override public boolean watchdogHealthy() { return run==null || !run.message.contains("keepalive"); }
}
