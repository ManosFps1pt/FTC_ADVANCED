"""Minimal CustomTkinter test UI for one normalized FTC gamepad.

This module deliberately contains no physical-controller support. It only
turns button presses and slider positions into :class:`GamepadInput` values and
passes them to ``ControlHubClient``. A future HID/SDL/pygame adapter can call
the same client method without depending on this UI.

Use only with a controlled test robot. Releasing a UI button sends a neutral
state, but closing the application or losing the Wi-Fi link will also cause
the Robot Controller's normal FTC heartbeat fail-safe to stop the OpMode.
"""

from __future__ import annotations

import argparse
import sys

try:
    import customtkinter as ctk
except ImportError:  # Keep the transport library usable without a GUI dependency.
    ctk = None  # type: ignore[assignment]

from ftc_control_hub import (
    ControlHubClient,
    ControlHubConfig,
    ControlHubError,
    GamepadButton,
    GamepadInput,
)


class GamepadWindow:
    """A mouse-operated gamepad whose state is sent through ``client``."""

    def __init__(self, client: ControlHubClient, user: int, *, stop_on_close: bool = False) -> None:
        if ctk is None:
            raise RuntimeError("CustomTkinter is not installed; run: pip install customtkinter")

        self._client = client
        self._user = user
        self._stop_on_close = stop_on_close
        self._buttons = GamepadButton(0)
        self._root = ctk.CTk()
        self._root.title(f"FTC Gamepad {user} test controller")
        self._root.geometry("780x570")
        self._root.minsize(700, 500)
        self._root.protocol("WM_DELETE_WINDOW", self._close)
        self._root.bind("<FocusOut>", lambda _event: self.release_all())

        self._left_x = ctk.DoubleVar(value=0.0)
        self._left_y = ctk.DoubleVar(value=0.0)
        self._right_x = ctk.DoubleVar(value=0.0)
        self._right_y = ctk.DoubleVar(value=0.0)
        self._left_trigger = ctk.DoubleVar(value=0.0)
        self._right_trigger = ctk.DoubleVar(value=0.0)
        self._build()
        self._publish()

    def run(self) -> None:
        self._root.mainloop()

    def _build(self) -> None:
        root = self._root
        root.grid_columnconfigure((0, 1, 2), weight=1)
        root.grid_rowconfigure(2, weight=1)

        self._title = ctk.CTkLabel(
            root,
            text=self._connection_title(),
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self._title.grid(row=0, column=0, columnspan=3, padx=20, pady=(18, 6))
        self._switch_button = ctk.CTkButton(
            root,
            text=self._switch_button_label(),
            command=self._switch_controller,
            width=190,
        )
        self._switch_button.grid(row=1, column=0, columnspan=3, pady=(0, 6))

        left = ctk.CTkFrame(root)
        center = ctk.CTkFrame(root)
        right = ctk.CTkFrame(root)
        left.grid(row=2, column=0, sticky="nsew", padx=(16, 6), pady=12)
        center.grid(row=2, column=1, sticky="nsew", padx=6, pady=12)
        right.grid(row=2, column=2, sticky="nsew", padx=(6, 16), pady=12)

        self._add_stick(left, "Left stick", self._left_x, self._left_y)
        self._add_dpad(left)
        self._add_stick(right, "Right stick", self._right_x, self._right_y)
        self._add_face_buttons(right)

        ctk.CTkLabel(center, text="Shoulders", font=ctk.CTkFont(weight="bold")).pack(pady=(16, 6))
        shoulder_row = ctk.CTkFrame(center, fg_color="transparent")
        shoulder_row.pack(padx=10, pady=4)
        self._hold_button(shoulder_row, "LB", GamepadButton.LEFT_BUMPER).grid(row=0, column=0, padx=4)
        self._hold_button(shoulder_row, "RB", GamepadButton.RIGHT_BUMPER).grid(row=0, column=1, padx=4)

        self._add_trigger(center, "Left trigger", self._left_trigger)
        self._add_trigger(center, "Right trigger", self._right_trigger)

        ctk.CTkLabel(center, text="Menu", font=ctk.CTkFont(weight="bold")).pack(pady=(12, 5))
        menu_row = ctk.CTkFrame(center, fg_color="transparent")
        menu_row.pack(padx=10, pady=4)
        self._hold_button(menu_row, "BACK", GamepadButton.BACK).grid(row=0, column=0, padx=4)
        self._hold_button(menu_row, "START", GamepadButton.START).grid(row=0, column=1, padx=4)
        self._hold_button(menu_row, "GUIDE", GamepadButton.GUIDE).grid(row=1, column=0, columnspan=2, padx=4, pady=6)

        ctk.CTkButton(center, text="Release all controls", command=self.release_all).pack(
            side="bottom", padx=18, pady=18
        )

    def _add_stick(
        self,
        parent: "ctk.CTkFrame",
        title: str,
        x_value: "ctk.DoubleVar",
        y_value: "ctk.DoubleVar",
    ) -> None:
        ctk.CTkLabel(parent, text=title, font=ctk.CTkFont(weight="bold")).pack(pady=(15, 3))
        ctk.CTkLabel(parent, text="X").pack()
        self._axis_slider(parent, x_value).pack(fill="x", padx=18)
        ctk.CTkLabel(parent, text="Y").pack(pady=(8, 0))
        self._axis_slider(parent, y_value).pack(fill="x", padx=18)

    def _axis_slider(
        self, parent: "ctk.CTkFrame", value: "ctk.DoubleVar"
    ) -> "ctk.CTkSlider":
        return ctk.CTkSlider(
            parent,
            from_=-1,
            to=1,
            number_of_steps=20,
            variable=value,
            command=lambda _amount: self._publish(),
        )

    def _add_dpad(self, parent: "ctk.CTkFrame") -> None:
        ctk.CTkLabel(parent, text="D-pad", font=ctk.CTkFont(weight="bold")).pack(pady=(24, 6))
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack()
        self._hold_button(grid, "↑", GamepadButton.DPAD_UP).grid(row=0, column=1, padx=3, pady=3)
        self._hold_button(grid, "←", GamepadButton.DPAD_LEFT).grid(row=1, column=0, padx=3, pady=3)
        self._hold_button(grid, "↓", GamepadButton.DPAD_DOWN).grid(row=1, column=1, padx=3, pady=3)
        self._hold_button(grid, "→", GamepadButton.DPAD_RIGHT).grid(row=1, column=2, padx=3, pady=3)

    def _add_face_buttons(self, parent: "ctk.CTkFrame") -> None:
        ctk.CTkLabel(parent, text="Face buttons", font=ctk.CTkFont(weight="bold")).pack(pady=(24, 6))
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack()
        self._hold_button(grid, "Y", GamepadButton.Y).grid(row=0, column=1, padx=3, pady=3)
        self._hold_button(grid, "X", GamepadButton.X).grid(row=1, column=0, padx=3, pady=3)
        self._hold_button(grid, "B", GamepadButton.B).grid(row=1, column=2, padx=3, pady=3)
        self._hold_button(grid, "A", GamepadButton.A).grid(row=2, column=1, padx=3, pady=3)

    def _add_trigger(self, parent: "ctk.CTkFrame", title: str, value: "ctk.DoubleVar") -> None:
        ctk.CTkLabel(parent, text=title).pack(pady=(14, 0))
        ctk.CTkSlider(
            parent,
            from_=0,
            to=1,
            number_of_steps=20,
            variable=value,
            command=lambda _amount: self._publish(),
        ).pack(fill="x", padx=20)

    def _hold_button(
        self, parent: "ctk.CTkFrame", label: str, bit: GamepadButton
    ) -> "ctk.CTkButton":
        button = ctk.CTkButton(parent, text=label, width=58)
        button.bind("<ButtonPress-1>", lambda _event: self._set_button(bit, True), add="+")
        button.bind("<ButtonRelease-1>", lambda _event: self._set_button(bit, False), add="+")
        return button

    def _set_button(self, bit: GamepadButton, pressed: bool) -> None:
        if pressed:
            self._buttons |= bit
        else:
            self._buttons &= ~bit
        self._publish()

    def _publish(self) -> None:
        self._client.set_gamepad_input(
            GamepadInput(
                user=self._user,
                left_stick_x=self._left_x.get(),
                left_stick_y=self._left_y.get(),
                right_stick_x=self._right_x.get(),
                right_stick_y=self._right_y.get(),
                left_trigger=self._left_trigger.get(),
                right_trigger=self._right_trigger.get(),
                buttons=self._buttons,
            )
        )

    def release_all(self) -> None:
        """Immediately place every control in its neutral state."""
        self._buttons = GamepadButton(0)
        for value in (
            self._left_x,
            self._left_y,
            self._right_x,
            self._right_y,
            self._left_trigger,
            self._right_trigger,
        ):
            value.set(0.0)
        self._publish()

    def _switch_controller(self) -> None:
        """Safely move this virtual controller between FTC slots 1 and 2."""
        self._client.clear_gamepad_input(self._user)
        self._user = 2 if self._user == 1 else 1
        self.release_all()
        self._title.configure(text=self._connection_title())
        self._switch_button.configure(text=self._switch_button_label())

    def _connection_title(self) -> str:
        return (
            f"Gamepad {self._user}  •  connected to "
            f"{self._client.config.host}:{self._client.config.port}"
        )

    def _switch_button_label(self) -> str:
        next_user = 2 if self._user == 1 else 1
        return f"Switch to Gamepad {next_user}"

    def _close(self) -> None:
        self.release_all()
        # Allow the 40 ms Robocol loop to transmit the released state once.
        self._root.after(80, self._shutdown)

    def _shutdown(self) -> None:
        if self._stop_on_close:
            try:
                self._client.stop_opmode(timeout_s=2.0)
            except ControlHubError:
                # The connection-loss fail-safe still stops the RC if an ACK
                # cannot be received during shutdown.
                pass
        self._client.close()
        self._root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description="Mouse-operated FTC Robocol gamepad test UI")
    parser.add_argument("host", help="Robot Controller IP, usually 192.168.43.1 or 192.168.49.1")
    parser.add_argument("--user", type=int, choices=(1, 2), default=1, help="FTC driver slot to control")
    parser.add_argument("--local-address", help="Wi-Fi IPv4 address to bind (normally auto-detected)")
    parser.add_argument("--local-port", type=int, default=20884)
    parser.add_argument("--timezone", help="IANA timezone used in heartbeats, e.g. Europe/Athens")
    parser.add_argument("--timeout", type=float, default=5.0, help="connection timeout in seconds")
    parser.add_argument("--opmode", help="exact OpMode name to use with --init and --start")
    parser.add_argument("--init", action="store_true", help="initialize --opmode before opening the UI")
    parser.add_argument("--start", action="store_true", help="start --opmode before opening the UI")
    args = parser.parse_args()

    if ctk is None:
        print("CustomTkinter is not installed. Install it with: pip install customtkinter", file=sys.stderr)
        return 1
    if (args.init or args.start) and not args.opmode:
        parser.error("--opmode is required with --init or --start")
    if args.start and not args.init:
        parser.error("--start requires --init so the UI starts from a known safe state")

    client = ControlHubClient(
        ControlHubConfig(
            host=args.host,
            local_address=args.local_address,
            local_port=args.local_port,
            timezone_id=args.timezone,
        )
    )
    try:
        client.connect(timeout_s=args.timeout)
        if args.init:
            opmodes = client.list_opmodes(timeout_s=args.timeout)
            available_names = {str(opmode.get("name", "")) for opmode in opmodes}
            if args.opmode not in available_names:
                raise ControlHubError(
                    f"OpMode {args.opmode!r} is not advertised by the RC; "
                    "run ftc_control_hub.py --list-opmodes to inspect the available names"
                )
            client.init_opmode(args.opmode, timeout_s=args.timeout)
        if args.start:
            client.start_opmode(args.opmode, timeout_s=args.timeout)
        GamepadWindow(client, args.user, stop_on_close=args.start).run()
        return 0
    except (ControlHubError, OSError, RuntimeError) as error:
        print(f"Could not start gamepad UI: {error}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
