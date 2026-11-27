"""Analysis annotations shared by the Oracle MCP server and replay viewer."""

from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import sqlite3
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


MarkerKind = Literal[
    "shooter_dip", "shooter_transition", "voltage_sag", "intake_load",
    "feed_gate", "drive", "driver_input", "capture_quality", "other",
]
MARKER_STYLES: dict[MarkerKind, tuple[str, str]] = {
    "shooter_dip": ("Shooter dip", "#fb923c"),
    "shooter_transition": ("Shooter transition", "#60a5fa"),
    "voltage_sag": ("Voltage sag", "#f87171"),
    "intake_load": ("Intake load", "#facc15"),
    "feed_gate": ("Feed gate", "#2dd4bf"),
    "drive": ("Drive", "#c084fc"),
    "driver_input": ("Driver input", "#a3e635"),
    "capture_quality": ("Capture quality", "#f472b6"),
    "other": ("Other", "#cbd5e1"),
}
MAX_MARKERS_PER_RUN = 500
MARKER_DB_NAME = ".review-markers/markers.sqlite3"


class ReplayMarkerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t_s: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    kind: MarkerKind
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""
    end_s: Annotated[float | None, Field(
        ge=0, allow_inf_nan=False,
        description="Optional range end in seconds since the first snapshot. It must be after t_s.",
    )] = None

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.end_s is not None and self.end_s <= self.t_s:
            raise ValueError("A range marker must end after it starts.")
        return self


class ReplayMarker(ReplayMarkerInput):
    id: str
    category: str
    color: str


class ReplayMarkerStore:
    def __init__(self, archive: Path) -> None:
        configured = os.getenv("FTC_REPLAY_MARKERS_DB")
        self.path = Path(configured).resolve() if configured else archive.resolve() / MARKER_DB_NAME

    def list_for_run(self, session_id: str) -> list[ReplayMarker]:
        if not self.path.is_file():
            return []
        with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as connection:
            columns = _marker_columns(connection)
            if not columns:
                return []
            end_column = "end_s" if "end_s" in columns else "NULL"
            rows = connection.execute(
                f"SELECT id, t_s, kind, title, note, {end_column} FROM markers WHERE session_id = ? ORDER BY t_s, id",
                (session_id,),
            ).fetchall()
        return [_marker(*row) for row in rows]

    def add(self, session_id: str, events: list[ReplayMarkerInput], duration_s: float) -> tuple[list[ReplayMarker], int]:
        if any((event.end_s if event.end_s is not None else event.t_s) > duration_s for event in events):
            raise ValueError(f"Choose timestamps within the recording (0–{duration_s:.3f} s).")
        markers = {}
        for event in events:
            marker_id = sha256(event.model_dump_json(exclude_none=True).encode()).hexdigest()[:24]
            markers[marker_id] = _marker(marker_id, event.t_s, event.kind, event.title, event.note, event.end_s)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.path.chmod(0o640)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS markers (
                    session_id TEXT NOT NULL, id TEXT NOT NULL, t_s REAL NOT NULL,
                    kind TEXT NOT NULL, title TEXT NOT NULL, note TEXT NOT NULL, end_s REAL,
                    PRIMARY KEY (session_id, id)
                )
            """)
            if "end_s" not in _marker_columns(connection):
                connection.execute("ALTER TABLE markers ADD COLUMN end_s REAL")
            connection.executemany(
                "INSERT OR IGNORE INTO markers (session_id, id, t_s, kind, title, note, end_s) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(session_id, m.id, m.t_s, m.kind, m.title, m.note, m.end_s) for m in markers.values()],
            )
            count, = connection.execute(
                "SELECT COUNT(*) FROM markers WHERE session_id = ?", (session_id,),
            ).fetchone()
            if count > MAX_MARKERS_PER_RUN:
                raise ValueError(f"A recording can have at most {MAX_MARKERS_PER_RUN} analysis markers.")
            added_count = connection.total_changes
        return sorted(markers.values(), key=lambda marker: (marker.t_s, marker.id)), added_count


def _marker_columns(connection: sqlite3.Connection) -> set[str]:
    return {column[1] for column in connection.execute("PRAGMA table_info(markers)")}


def _marker(marker_id: str, t_s: float, kind: MarkerKind, title: str, note: str, end_s: float | None) -> ReplayMarker:
    category, color = MARKER_STYLES[kind]
    return ReplayMarker(
        id=marker_id, t_s=t_s, kind=kind, title=title, note=note, end_s=end_s, category=category, color=color,
    )
