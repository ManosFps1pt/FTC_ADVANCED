package org.firstinspires.ftc.teamcode.data;

import android.content.Context;
import android.content.SharedPreferences;

import org.firstinspires.ftc.robotcore.internal.system.AppUtil;

/**
 * The persistent destination policy for structured robot data.
 *
 * <p>The safe default is {@link #OFF}.  {@link #LOCAL_MATCH} never opens a
 * socket or sends a discovery packet; it only writes a local recording.
 * {@link #PIT_TCP} is for deliberate, off-field laptop work.</p>
 */
public enum DataTransportMode {
    OFF,
    LOCAL_MATCH,
    PIT_TCP;

    private static final String PREFERENCES = "ftcAdvancedDataTransport";
    private static final String MODE_KEY = "mode";

    /** Read the last explicitly selected mode. A missing or corrupt value is safely OFF. */
    public static DataTransportMode current() {
        try {
            SharedPreferences preferences = AppUtil.getDefContext().getSharedPreferences(
                    PREFERENCES, Context.MODE_PRIVATE);
            return parse(preferences.getString(MODE_KEY, OFF.name()));
        } catch (RuntimeException ignored) {
            return OFF;
        }
    }

    /** Persist a mode selection before the next robot OpMode is initialized. */
    public static boolean select(DataTransportMode mode) {
        if (mode == null) throw new IllegalArgumentException("mode must not be null");
        try {
            return AppUtil.getDefContext().getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
                    .edit().putString(MODE_KEY, mode.name()).commit();
        } catch (RuntimeException ignored) {
            return false;
        }
    }

    private static DataTransportMode parse(String value) {
        if (value == null) return OFF;
        try {
            return valueOf(value);
        } catch (IllegalArgumentException ignored) {
            return OFF;
        }
    }
}
