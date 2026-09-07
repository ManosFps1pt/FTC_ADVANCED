package org.firstinspires.ftc.teamcode.debug;

import java.util.ArrayList;
import java.util.List;

/** Hardware-independent procedure. All times are acquisition times, in nanoseconds. */
public final class FrictionBenchmark implements Benchmark<FrictionBenchmark.Sample> {
    public interface Motor {
        Reading read();
        void power(double duty);
    }
    public static final class Reading {
        public final double voltage, current, velocity;
        public final long position;
        public Reading(double voltage, double current, long position, double velocity) {
            this.voltage=voltage; this.current=current; this.position=position; this.velocity=velocity;
        }
    }
    public static final class Sample {
        public final long time;
        public final String phase;
        public final int repetition, direction, point;
        public final Reading reading;
        public final double duty;
        public boolean onset, steady;
        Sample(long time, String phase, int repetition, int direction, int point, Reading reading, double duty) {
            this.time=time; this.phase=phase; this.repetition=repetition; this.direction=direction;
            this.point=point; this.reading=reading; this.duty=duty;
        }
    }
    public final List<Sample> samples = new ArrayList<>();
    public String phase="rest", outcome="running", message="";
    public int repetition=1, direction=1, point=0;
    private final Motor motor;
    private final int repetitions;
    private final boolean reverse;
    private final double maxVoltage, maxPower, currentLimit;
    private long phaseStart, lastSample, lastKeepalive, restSince=-1, onsetSince=-1, origin;
    private int onsetIndex=-1;
    private double duty, breakaway;
    private boolean originSet;

    public FrictionBenchmark(Motor motor, int repetitions, boolean reverse, double maxVoltage,
                             double maxPower, double currentLimit, long now) {
        if (repetitions<1 || repetitions>5 || !Double.isFinite(maxVoltage) || maxVoltage<=0 || maxVoltage>12
                || !Double.isFinite(maxPower) || maxPower<=0 || maxPower>1
                || !Double.isFinite(currentLimit) || currentLimit<=0) throw new IllegalArgumentException("Invalid benchmark limits");
        this.motor=motor; this.repetitions=repetitions; this.reverse=reverse;
        this.maxVoltage=maxVoltage; this.maxPower=maxPower; this.currentLimit=currentLimit;
        phaseStart=now; lastKeepalive=now; lastSample=now-20_000_000L;
        motor.power(0);
    }
    public boolean running() { return outcome.equals("running"); }
    public String phase() { return phase; }
    public String outcome() { return outcome; }
    public List<Sample> completedDataset() {
        if (running()) throw new IllegalStateException("Acquisition is still active");
        return java.util.Collections.unmodifiableList(samples);
    }
    public double duty() { return duty; }
    public void keepalive(long now) { lastKeepalive=now; }
    public void abort(String reason) { finish("aborted", reason); }
    private void finish(String result, String reason) {
        duty=0; outcome=result; message=reason; motor.power(0);
    }
    private void transition(String next, long now) {
        phase=next; phaseStart=now; restSince=-1; originSet=false;
    }
    private void volts(double value, double battery) {
        duty=direction*Math.min(maxPower, Math.max(0, value)/battery);
        motor.power(duty);
    }
    public void tick(long now) {
        if (!running()) return;
        if (now-lastKeepalive>2_000_000_000L) { abort("Operator keepalive expired"); return; }
        if (now-lastSample<20_000_000L) return;
        lastSample=now;
        Reading r=motor.read();
        if (!Double.isFinite(r.voltage) || r.voltage<=0 || !Double.isFinite(r.current) || r.current<0
                || !Double.isFinite(r.velocity)) { abort("Invalid battery/current/encoder reading"); return; }
        samples.add(new Sample(now,phase,repetition,direction,point,r,duty));
        if (r.current>currentLimit) { abort("Registered current limit exceeded"); return; }
        if (samples.size()>=30000) { abort("Acquisition buffer limit reached"); return; }
        double elapsed=(now-phaseStart)/1e9;
        if (phase.equals("rest")) {
            if (!originSet) { origin=r.position; originSet=true; }
            if (Math.abs(r.velocity)<5 && Math.abs(r.position-origin)<3) {
                if (restSince<0) restSince=now;
                if (now-restSince>=1_000_000_000L) {
                    transition("ramp",now); origin=r.position; originSet=true; onsetSince=-1;
                }
            } else { restSince=-1; origin=r.position; }
            if (elapsed>=5) abort("Mechanism did not come to rest");
        } else if (phase.equals("ramp")) {
            boolean moving=(r.position-origin)*direction>=3 && r.velocity*direction>0;
            if (moving) {
                if (onsetSince<0) { onsetSince=now; onsetIndex=samples.size()-1; }
                if (now-onsetSince>=150_000_000L) {
                    Sample first=samples.get(onsetIndex); first.onset=true;
                    breakaway=Math.abs(first.duty*first.reading.voltage);
                    point=1; transition("steady",now);
                    volts(breakaway+(maxVoltage-breakaway)/4,r.voltage);
                    return;
                }
            } else { onsetSince=-1; onsetIndex=-1; }
            volts(Math.min(maxVoltage,elapsed*0.25),r.voltage);
            if (elapsed>=maxVoltage/0.25+0.5) finish("inconclusive","Movement not observed within the voltage limit");
        } else if (phase.equals("steady")) {
            volts(breakaway+(maxVoltage-breakaway)*point/4,r.voltage);
            int first=stableWindow(now);
            if (first>=0 || elapsed>=5) {
                if (first>=0) for (int i=first;i<samples.size();i++) samples.get(i).steady=true;
                if (point<4) { point++; transition("steady",now); volts(breakaway+(maxVoltage-breakaway)*point/4,r.voltage); }
                else { duty=0; motor.power(0); transition("coast",now); }
            }
        } else if (phase.equals("coast")) {
            if (!originSet) { origin=r.position; originSet=true; }
            if (Math.abs(r.velocity)<5 && Math.abs(r.position-origin)<3) {
                if (restSince<0) restSince=now;
                if (now-restSince>=1_000_000_000L) {
                    if (repetition<repetitions) repetition++;
                    else if (reverse && direction==1) { direction=-1; repetition=1; }
                    else { finish("completed","Benchmark complete"); return; }
                    point=0; transition("rest",now);
                }
            } else { restSince=-1; origin=r.position; }
            if (elapsed>=15 && running()) finish("inconclusive","Coast-down did not reach rest within 15 seconds");
        }
    }
    private int stableWindow(long now) {
        if (now-phaseStart<1_000_000_000L) return -1;
        int start=samples.size()-1;
        while (start>0 && samples.get(start).time>now-1_000_000_000L) start--;
        Sample first=samples.get(start);
        if (!first.phase.equals("steady") || first.point!=point || first.time<phaseStart) return -1;
        int n=samples.size()-start;
        if (n<10 || (now-first.time)<1_000_000_000L) return -1;
        double sum=0, sum2=0;
        for (int i=start;i<samples.size();i++) { double v=samples.get(i).reading.velocity*direction; sum+=v; sum2+=v*v; }
        double mean=sum/n;
        if (mean<5) return -1;
        double std=Math.sqrt(Math.max(0,sum2/n-mean*mean));
        double drift=Math.abs(samples.get(samples.size()-1).reading.velocity-first.reading.velocity);
        return std<=mean*0.05 && drift<=mean*0.05 ? start : -1;
    }
}
