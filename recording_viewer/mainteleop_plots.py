"""Optional diagnostic figures for the Main TeleOp report."""

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np


BLUE = "#1858a8"
ORANGE = "#b75a18"
GREEN = "#18765a"
RED = "#b5394a"
GRAY = "#62666d"


def write_plots(output, traces, tables, settings):
    t = traces["t"]
    rpm, target = traces["rpm"], traces["target"]
    voltage, total = traces["voltage"], traces["total"]
    shooter_power = traces["power"]["shooter"]
    intake_a = traces["amps"]["intake"]
    cuts = np.flatnonzero(~traces["links"]) + 1

    def line(values):
        return np.insert(values, cuts, np.nan)

    plt.rcParams.update({"axes.spines.right": False, "axes.spines.top": False, "font.size": 10,
                         "axes.grid": True, "grid.alpha": .22, "legend.frameon": False})

    fig, axes = plt.subplots(4, 1, sharex=True, figsize=(12, 8.5), layout="constrained")
    minutes = line(t) / 60
    axes[0].plot(minutes, line(target), color=GRAY, lw=.8, alpha=.65, label="Selected target")
    axes[0].plot(minutes, line(rpm), color=BLUE, lw=1, label="Measured")
    axes[0].scatter([row["trough_t_s"] / 60 for row in tables["shooter_disturbances"]],
                    [row["minimum_rpm"] for row in tables["shooter_disturbances"]],
                    s=13, color=ORANGE, zorder=3, label="Detected trough")
    axes[0].set(ylabel="Shooter (RPM)", title="Main TeleOp telemetry")
    axes[0].legend(loc="upper right", ncols=3)
    axes[1].plot(minutes, line(total), color=GRAY, lw=.85, label="Six motor ports")
    axes[1].plot(minutes, line(traces["drive_amps"]), color=BLUE, lw=.85, alpha=.8, label="Drivetrain")
    axes[1].plot(minutes, line(intake_a), color=GREEN, lw=.8, label="Intake")
    axes[1].set(ylabel="Motor-port current (A)")
    axes[1].legend(loc="upper right", ncols=3)
    axes[2].plot(minutes, line(voltage), color=BLUE, lw=.8)
    axes[2].axhline(settings.low_voltage_v, color=RED, lw=.85, ls="--", label="Review threshold")
    axes[2].set(ylabel="Bus voltage (V)")
    axes[2].legend(loc="lower right")
    axes[3].fill_between(minutes, 0, 1, where=line(traces["gate"]) == 1, step="post", color=GREEN,
                         alpha=.26, label="Gate commanded open")
    axes[3].plot(minutes, line(shooter_power), color=ORANGE, lw=.8, label="Shooter applied output")
    axes[3].set(ylabel="Output (0–1)", xlabel="Minutes from first sample", xlim=(0, minutes[-1]))
    axes[3].legend(loc="upper right", ncols=2)
    fig.savefig(output / "overview.png", dpi=145)
    plt.close(fig)
    write_electrical_examples(output, traces, tables)

    episodes = tables["shooter_disturbances"]
    if not episodes:
        return
    recovered = [row for row in episodes if row["outcome"] == "recovered"]
    examples = [max(episodes, key=lambda row: row["drop_rpm"])]
    if recovered:
        typical_drop = np.median([row["drop_rpm"] for row in recovered])
        representative = min(recovered, key=lambda row: abs(row["drop_rpm"] - typical_drop))
        if representative != examples[0]:
            examples.insert(0, representative)
    fig, axes = plt.subplots(2, len(examples), squeeze=False, sharex="col",
                             figsize=(6 * len(examples), 6.3), layout="constrained")
    for column, row in enumerate(examples):
        origin = row["t_s"]
        x = t - origin
        m = (x >= -.65) & (x <= settings.recovery_window_s + .3)
        speed_ax, power_ax = axes[:, column]
        speed_ax.plot(x[m], rpm[m], "o-", ms=3, color=BLUE, label="Measured")
        speed_ax.axhline(row["target_rpm"], color=GRAY, lw=1, ls="--", label="Selected target")
        baseline = row["baseline_rpm"]
        speed_ax.axhspan(baseline - settings.speed_band_rpm, baseline + settings.speed_band_rpm,
                         color=GREEN, alpha=.17, label="Pre-dip recovery band")
        speed_ax.scatter(row["trough_t_s"] - origin, row["minimum_rpm"], color=ORANGE, zorder=4)
        if row["recovered_t_s"] is not None:
            speed_ax.axvline(row["recovered_t_s"] - origin, color=GREEN, lw=1, ls=":")
        speed_ax.set(ylabel="Shooter (RPM)", title=f'{origin:,.2f} s into run | {row["target_rpm"]:,.0f} RPM target')
        speed_ax.legend(loc="best", fontsize=9)
        power_ax.plot(x[m], shooter_power[m], "o-", color=ORANGE, ms=3)
        power_ax.set(ylabel="Shooter applied output", xlabel="Seconds from detected dip",
                     ylim=(0, 1.08), xlim=(-.65, settings.recovery_window_s + .3))
        for ax in (speed_ax, power_ax):
            ax.axvline(0, color=GRAY, lw=1, alpha=.65)
    fig.savefig(output / "shooter_dips.png", dpi=145)
    plt.close(fig)


def write_electrical_examples(output, traces, tables):
    t = traces["t"]
    voltage = traces["voltage"]
    if not np.isfinite(voltage).any():
        return
    intake_episodes = tables["intake_load_episodes"]
    fig, axes = plt.subplots(2, 1 + bool(intake_episodes), squeeze=False, sharex="col",
                             figsize=(12 if intake_episodes else 6, 6), layout="constrained")
    voltage_min_t = t[np.nanargmin(voltage)]
    x = t - voltage_min_t
    m = np.abs(x) <= 1.2
    ax = axes[0, 0]
    ax.plot(x[m], voltage[m], "o-", color=RED, ms=3)
    ax.set(ylabel="Bus voltage (V)", title=f"Lowest bus voltage at {voltage_min_t:.2f} s")
    axes[1, 0].plot(x[m], traces["total"][m], "o-", color=GRAY, ms=3, label="Six motor ports")
    axes[1, 0].plot(x[m], traces["drive_amps"][m], "o-", color=BLUE, ms=3, label="Drivetrain")
    axes[1, 0].set(ylabel="Motor-port current (A)", xlabel="Seconds from voltage minimum")
    axes[1, 0].legend()
    if intake_episodes:
        event = max(intake_episodes, key=lambda row: row["minimum_duration_s"])
        x = t - event["t_s"]
        m = (x >= -.6) & (x <= event["minimum_duration_s"] + .6)
        for ax in axes[:, 1]:
            ax.axvspan(0, event["minimum_duration_s"], color=ORANGE, alpha=.13)
        axes[0, 1].plot(x[m], traces["amps"]["intake"][m], "o-", color=ORANGE, ms=3)
        axes[0, 1].set(ylabel="Intake current (A)", title=f'Longest flagged intake load at {event["t_s"]:.2f} s')
        axes[1, 1].plot(x[m], traces["intake_speed"][m], "o-", color=BLUE, ms=3)
        axes[1, 1].set(ylabel="Intake speed (ticks/s)", xlabel="Seconds from load detection")
    fig.savefig(output / "electrical_events.png", dpi=145)
    plt.close(fig)
