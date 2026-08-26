"""SQLite utility methods for the provenance Flask service."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class DatabaseError(Exception):
    """Raised when a database operation fails."""


def connect_database(database_file: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(database_file)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error as exc:
        raise DatabaseError(f"Unable to connect to database {database_file}: {exc}") from exc


def list_artifact_ids(database_file: Path, limit: int) -> list[str]:
    if limit <= 0:
        raise DatabaseError("Limit must be greater than zero.")

    try:
        with connect_database(database_file) as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM prov_artifact
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to list provenance artifact ids: {exc}") from exc

    return [str(row["id"]) for row in rows]


def get_provenance_by_id(database_file: Path, artifact_id: str) -> dict[str, str | None] | None:
    if not artifact_id:
        raise DatabaseError("Artifact id must not be empty.")

    try:
        with connect_database(database_file) as connection:
            row = connection.execute(
                """
                SELECT id, pipeline_type, provenance, name, description
                FROM prov_artifact
                WHERE id = ?
                """,
                (artifact_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to fetch provenance artifact {artifact_id}: {exc}") from exc

    if row is None:
        return None

    artifact = {key: row[key] for key in row.keys()}

    provenance_text = artifact.get("provenance")
    if provenance_text:
        try:
            artifact["provenance"] = json.loads(provenance_text)
        except json.JSONDecodeError as exc:
            raise DatabaseError(f"Stored provenance for artifact {artifact_id} is not valid JSON: {exc}") from exc

    return artifact
