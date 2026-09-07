from __future__ import annotations

import unittest
import uuid

from .telemetry_store import TelemetryProtocolError, TelemetryStore


class TelemetryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = TelemetryStore(max_snapshots_per_session=3)
        self.session_id = str(uuid.uuid4())
        self.connection_id = str(uuid.uuid4())
        self.sequence = 0

    def envelope(self, message_type: str, data: dict[object, object]) -> dict[str, object]:
        envelope = {
            "protocol": "ftc-telemetry",
            "version": 1,
            "type": message_type,
            "sessionId": self.session_id,
            "connectionId": self.connection_id,
            "sequence": str(self.sequence),
            "robotTimeNs": str(1_000_000 + self.sequence),
            "data": data,
        }
        self.sequence += 1
        return envelope

    def start_session(self) -> None:
        self.store.ingest(
            self.envelope("hello", {"robotId": "ftc-1", "robotName": "Test Robot", "opModeName": "Test"}),
            received_monotonic_ns=1,
        )
        self.store.ingest(
            self.envelope(
                "catalog",
                {
                    "schemaRevision": 1,
                    "devices": [{"id": "drive.left", "label": "Left", "subsystem": "drivetrain", "deviceType": "dcMotor"}],
                    "signals": [
                        {
                            "id": "drive.left.currentA",
                            "label": "Left Current",
                            "deviceId": "drive.left",
                            "quantity": "current",
                            "unit": "A",
                            "valueType": "float64",
                            "role": "measured",
                        },
                        {
                            "id": "debug.drive.state",
                            "label": "Drive State",
                            "quantity": "state",
                            "unit": "none",
                            "valueType": "enum",
                            "role": "diagnostic",
                        },
                    ],
                },
            ),
            received_monotonic_ns=2,
        )

    def test_catalog_and_full_snapshot_are_stored_as_objects(self) -> None:
        self.start_session()
        updates = self.store.ingest(
            self.envelope(
                "sample",
                {
                    "sampleSequence": "0",
                    "schemaRevision": 1,
                    "values": {"drive.left.currentA": 3.2, "debug.drive.state": "DRIVING"},
                },
            ),
            received_monotonic_ns=3,
            received_at_ms=100,
        )

        self.assertEqual(updates[0].kind, "telemetry_snapshot")
        state = self.store.live_state()
        self.assertEqual(state["session"]["signalCount"], 2)
        self.assertEqual(state["snapshots"][0]["values"]["drive.left.currentA"], 3.2)
        self.assertEqual(state["snapshots"][0]["values"]["debug.drive.state"], "DRIVING")

    def test_snapshot_requires_every_catalog_signal(self) -> None:
        self.start_session()
        with self.assertRaises(TelemetryProtocolError):
            self.store.ingest(
                self.envelope(
                    "sample",
                    {"sampleSequence": "0", "schemaRevision": 1, "values": {"drive.left.currentA": 3.2}},
                ),
                received_monotonic_ns=3,
            )

    def test_latest_state_does_not_include_replay_history(self) -> None:
        self.start_session()
        self.store.ingest(
            self.envelope(
                "sample",
                {
                    "sampleSequence": "0",
                    "schemaRevision": 1,
                    "values": {"drive.left.currentA": 3.2, "debug.drive.state": "DRIVING"},
                },
            ),
            received_monotonic_ns=3,
        )

        latest = self.store.latest_state()

        self.assertIsNotNone(latest["snapshot"])
        self.assertEqual("3.2", str(latest["snapshot"]["values"]["drive.left.currentA"]))
        self.assertNotIn("snapshots", latest)
        self.assertNotIn("gamepadFrames", latest)

    def test_disconnect_marks_matching_session_inactive(self) -> None:
        self.start_session()

        updates = self.store.disconnect_connection(self.connection_id)

        self.assertEqual(["telemetry_session"], [update.kind for update in updates])
        self.assertFalse(self.store.status()["activeSession"]["active"])

    def test_gamepad_frames_are_replayed_with_the_session(self) -> None:
        self.start_session()
        state = {
            "leftStickX": 0.25, "leftStickY": -0.5, "rightStickX": 0.0, "rightStickY": 1.0,
            "leftTrigger": 0.0, "rightTrigger": 0.75,
            "a": True, "b": False, "x": False, "y": False,
            "dpadUp": False, "dpadDown": False, "dpadLeft": True, "dpadRight": False,
            "leftBumper": False, "rightBumper": True,
            "leftStickButton": False, "rightStickButton": False,
            "back": False, "start": False, "guide": False,
        }

        updates = self.store.ingest(
            self.envelope("gamepad", {"gamepad1": state, "gamepad2": {**state, "a": False, "b": True}}),
            received_monotonic_ns=3,
        )

        self.assertEqual("telemetry_gamepad", updates[0].kind)
        replay = self.store.live_state()["gamepadFrames"]
        self.assertEqual(1, len(replay))
        self.assertTrue(replay[0]["gamepad1"]["a"])
        self.assertTrue(replay[0]["gamepad2"]["b"])

    def test_pose2d_snapshot_is_preserved_for_field_rendering(self) -> None:
        self.store.ingest(
            self.envelope("hello", {"robotId": "ftc-1", "robotName": "Test Robot", "opModeName": "Test"}),
            received_monotonic_ns=1,
        )
        self.store.ingest(
            self.envelope("catalog", {
                "schemaRevision": 1,
                "devices": [{"id": "localization", "label": "Localization", "subsystem": "localization", "deviceType": "pose estimator"}],
                "signals": [{"id": "localization.pose", "label": "Localization Pose", "deviceId": "localization", "quantity": "pose", "unit": "in,rad", "valueType": "pose2d", "role": "measured"}],
            }),
            received_monotonic_ns=2,
        )
        self.store.ingest(
            self.envelope("sample", {
                "sampleSequence": "0", "schemaRevision": 1,
                "values": {"localization.pose": {"x": 24.0, "y": 48.0, "headingRad": 0.5}},
            }),
            received_monotonic_ns=3,
        )
        self.assertEqual(
            {"x": 24.0, "y": 48.0, "headingRad": 0.5},
            self.store.live_state()["snapshots"][0]["values"]["localization.pose"],
        )

    def test_pose2d_snapshot_rejects_missing_or_non_finite_coordinates(self) -> None:
        self.store.ingest(
            self.envelope("hello", {"robotId": "ftc-1", "robotName": "Test Robot", "opModeName": "Test"}),
            received_monotonic_ns=1,
        )
        self.store.ingest(
            self.envelope("catalog", {
                "schemaRevision": 1,
                "devices": [],
                "signals": [{"id": "localization.pose", "label": "Localization Pose", "quantity": "pose", "unit": "in,rad", "valueType": "pose2d", "role": "measured"}],
            }),
            received_monotonic_ns=2,
        )
        with self.assertRaises(TelemetryProtocolError):
            self.store.ingest(
                self.envelope("sample", {"sampleSequence": "0", "schemaRevision": 1, "values": {"localization.pose": {"x": 1.0, "y": 2.0}}}),
                received_monotonic_ns=3,
            )
        with self.assertRaises(TelemetryProtocolError):
            self.store.ingest(
                self.envelope("sample", {"sampleSequence": "1", "schemaRevision": 1, "values": {"localization.pose": {"x": 1.0, "y": float("inf"), "headingRad": 0.0}}}),
                received_monotonic_ns=4,
            )


if __name__ == "__main__":
    unittest.main()
