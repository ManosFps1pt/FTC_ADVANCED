"""Operator-entered details stored alongside a finalized recording."""

from pathlib import Path
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field, StringConstraints, ValidationError

RECORDING_METADATA_FILE = "recording-metadata.json"
DETAILS_REQUIRED = "Save the battery index, robot version, and OpMode type before uploading."

BatteryIndex = Annotated[int, Field(strict=True, ge=0, le=8)]
RobotVersion = Annotated[int, Field(strict=True, ge=1)]
OpModeType = Literal["teleop", "auto", "debugging"]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class RecordingDetails(BaseModel):
    battery_index: BatteryIndex
    robot_version: RobotVersion
    opmode_type: OpModeType


class RecordingNotes(BaseModel):
    title: Title
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] = ""


class CompleteRecordingMetadata(RecordingNotes, RecordingDetails):
    pass


class RecordingMetadata(RecordingNotes):
    battery_index: BatteryIndex | None = None
    robot_version: RobotVersion | None = None
    opmode_type: OpModeType | None = None


class StoredRecordingMetadata(RecordingMetadata):
    title: Title | None = None


class RecordingDetailsValues(TypedDict):
    battery_index: int | None
    robot_version: int | None
    opmode_type: OpModeType | None


def recording_details(metadata: dict | None) -> RecordingDetailsValues:
    metadata = metadata or {}
    return RecordingDetailsValues(
        battery_index=metadata.get("battery_index"),
        robot_version=metadata.get("robot_version"),
        opmode_type=metadata.get("opmode_type"),
    )


def has_recording_details(metadata: dict | None) -> bool:
    return all(value is not None for value in recording_details(metadata).values())


def read_recording_metadata(session_directory: Path) -> dict | None:
    path = session_directory / RECORDING_METADATA_FILE
    if path.is_symlink():
        return None
    try:
        metadata = StoredRecordingMetadata.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None
    return metadata.model_dump(exclude_none=True, exclude_unset=True)
