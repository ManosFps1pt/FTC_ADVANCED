package org.firstinspires.ftc.teamcode.debug;

/** Run directly with javac/java; needs no Android runtime or attached hardware. */
public final class FrictionBenchmarkHarness {
    private static final class FakeMotor implements FrictionBenchmark.Motor {
        double duty, velocity, position, extraCurrent;
        boolean jammed, invalid;
        public void power(double value) { duty=value; }
        public FrictionBenchmark.Reading read() {
            double target=jammed?0:Math.signum(duty)*Math.max(0,Math.abs(duty)*12-.4)*100;
            velocity+=(target-velocity)*.25;
            position+=velocity*.02;
            return new FrictionBenchmark.Reading(invalid?Double.NaN:12,.1+Math.abs(velocity)*.001+extraCurrent,Math.round(position),velocity);
        }
    }
    private static void check(boolean condition,String message) { if(!condition) throw new AssertionError(message); }
    private static long advance(FrictionBenchmark b,long now,int ticks) {
        for(int i=0;i<ticks&&b.running();i++){now+=20_000_000L;b.keepalive(now);b.tick(now);} return now;
    }
    public static void main(String[] args) {
        FakeMotor m=new FakeMotor();
        FrictionBenchmark b=new FrictionBenchmark(m,3,true,3,.35,2,0);
        advance(b,0,20000);
        check(b.outcome.equals("completed"),"repeated bidirectional run: "+b.message);
        check(b.samples.stream().filter(s->s.onset).count()==6,"six movement onsets");
        check(b.samples.stream().anyMatch(s->s.direction==-1&&s.steady),"reverse steady windows");
        check(b.samples.stream().allMatch(s->Math.abs(s.duty)<=.35),"power bounded");
        check(m.duty==0,"completion output zero");
        long previous=-1; for(FrictionBenchmark.Sample s:b.samples){check(s.time>previous,"monotonic samples");previous=s.time;}
        m=new FakeMotor();m.jammed=true;b=new FrictionBenchmark(m,1,false,3,.35,2,0);advance(b,0,1000);
        check(b.outcome.equals("inconclusive")&&b.message.contains("Movement not observed"),"missing movement is not diagnosed");
        check(m.duty==0,"no movement output zero");
        m=new FakeMotor();m.extraCurrent=3;b=new FrictionBenchmark(m,1,false,3,.35,2,0);b.tick(20_000_000L);
        check(!b.running()&&m.duty==0&&b.message.contains("current"),"current cutoff");
        m=new FakeMotor();m.invalid=true;b=new FrictionBenchmark(m,1,false,3,.35,2,0);b.tick(20_000_000L);
        check(!b.running()&&m.duty==0,"invalid sensor stops");
        m=new FakeMotor();b=new FrictionBenchmark(m,1,false,3,.35,2,0);b.tick(2_100_000_000L);
        check(!b.running()&&m.duty==0&&b.message.contains("keepalive"),"watchdog");
        m=new FakeMotor();b=new FrictionBenchmark(m,1,false,3,.35,2,0);advance(b,0,150);int count=b.samples.size();b.abort("test abort");
        check(b.samples.size()==count&&m.duty==0,"abort retains acquisition");
        System.out.println("PASS: 6 engine scenarios (repetitions/reverse, missing movement, current, sensor, watchdog, abort)");
    }
}
