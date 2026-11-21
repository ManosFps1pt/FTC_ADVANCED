"""Operator-entered notes stored alongside a finalized OpMode recording."""

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, StringConstraints, ValidationError

RECORDING_METADATA_FILE = "recording-metadata.json"


class RecordingMetadata(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] = ""


def read_recording_metadata(session_directory: Path) -> dict[str, str] | None:
    path = session_directory / RECORDING_METADATA_FILE
    if path.is_symlink():
        return None
    try:
        metadata = RecordingMetadata.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        return None
    return metadata.model_dump()
