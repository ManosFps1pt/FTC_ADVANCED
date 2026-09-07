package org.firstinspires.ftc.teamcode.debug;

import java.util.List;

/** One bounded execution, created with validated inputs by its registered Tool.
 * The Tool advertises the definition and owns hardware configuration and transfer.
 * Constructing an execution starts it; tick never blocks the OpMode loop.
 */
public interface Benchmark<S> {
    void tick(long robotTimeNs);
    void keepalive(long robotTimeNs);
    void abort(String reason);
    boolean running();
    String phase();
    String outcome();
    List<S> completedDataset();
}
