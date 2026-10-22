package org.firstinspires.ftc.teamcode.data;

import org.firstinspires.ftc.robotcore.internal.system.AppUtil;

import java.io.BufferedOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.UUID;
import java.util.zip.CRC32;

/**
 * Shared-storage, crash-tolerant raw recorder for {@link DataTransportMode#LOCAL_MATCH}.
 * Files live under {@code /sdcard/FIRST/data/ftc-advanced-recordings}, where a
 * post-match laptop can retrieve them through USB or wireless ADB.
 */
final class LocalMatchRecorder implements AutoCloseable {
    private static final byte[] MAGIC = {'F', 'T', 'C', 'L', 'O', 'G', 0, 0};
    private static final int FORMAT_VERSION = 1;
    private static final long SYNC_INTERVAL_NS = 1_000_000_000L;

    private final File partialFile;
    private final File finalFile;
    private final FileOutputStream file;
    private final DataOutputStream output;
    private long lastSyncNs;
    private boolean closed;

    LocalMatchRecorder(UUID sessionId) throws IOException {
        File root = new File(AppUtil.ROBOT_DATA_DIR, "ftc-advanced-recordings");
        File raw = new File(new File(root, sessionId.toString()), "raw");
        AppUtil.getInstance().ensureDirectoryExists(raw);
        if (!raw.isDirectory()) throw new IOException("could not create local recording directory");
        finalFile = new File(raw, "stream-00000.ftclog");
        partialFile = new File(raw, "stream-00000.ftclog.partial");
        if (partialFile.exists() && !partialFile.delete()) throw new IOException("could not replace incomplete local recording");
        file = new FileOutputStream(partialFile, false);
        output = new DataOutputStream(new BufferedOutputStream(file));
        output.write(MAGIC);
        output.writeShort(FORMAT_VERSION);
        output.writeShort(32);
        output.writeLong(sessionId.getMostSignificantBits());
        output.writeLong(sessionId.getLeastSignificantBits());
        output.writeLong(System.nanoTime());
        sync();
    }

    synchronized void append(byte[] protobufFrame, long robotTimeNs) throws IOException {
        if (closed) throw new IOException("local recorder is closed");
        if (protobufFrame == null || protobufFrame.length == 0 || protobufFrame.length > 1_048_576) {
            throw new IOException("invalid local protobuf frame length");
        }
        output.writeInt(protobufFrame.length);
        output.writeLong(robotTimeNs);
        output.writeInt(protobufFrame.length);
        output.write(protobufFrame);
        CRC32 crc = new CRC32();
        updateInt(crc, protobufFrame.length);
        updateLong(crc, robotTimeNs);
        updateInt(crc, protobufFrame.length);
        crc.update(protobufFrame);
        output.writeInt((int) crc.getValue());
        if (System.nanoTime() - lastSyncNs >= SYNC_INTERVAL_NS) sync();
    }

    @Override
    public synchronized void close() throws IOException {
        if (closed) return;
        closed = true;
        sync();
        output.close();
        if (!partialFile.renameTo(finalFile)) throw new IOException("could not finalize local recording");
    }

    private void sync() throws IOException {
        output.flush();
        file.getFD().sync();
        lastSyncNs = System.nanoTime();
    }

    private static void updateInt(CRC32 crc, int value) {
        crc.update((value >>> 24) & 0xff); crc.update((value >>> 16) & 0xff);
        crc.update((value >>> 8) & 0xff); crc.update(value & 0xff);
    }

    private static void updateLong(CRC32 crc, long value) {
        for (int shift = 56; shift >= 0; shift -= 8) crc.update((int) (value >>> shift) & 0xff);
    }
}
