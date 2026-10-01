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


def list_artifacts(database_file: Path, limit: int) -> list[dict[str, str | None]]:
    if limit <= 0:
        raise DatabaseError("Limit must be greater than zero.")

    try:
        with connect_database(database_file) as connection:
            rows = connection.execute(
                """
                SELECT id, name
                FROM prov_artifact
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to list provenance artifacts: {exc}") from exc

    return [{"id": str(row["id"]), "name": row["name"]} for row in rows]


def list_traits(database_file: Path) -> list[dict[str, str | None]]:
    try:
        with connect_database(database_file) as connection:
            rows = connection.execute(
                """
                SELECT legacy_id, kpn_id, name, description
                FROM prov_trait
                ORDER BY legacy_id ASC
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to list provenance traits: {exc}") from exc

    return [{key: row[key] for key in row.keys()} for row in rows]


def list_bottom_line_by_trait(
    database_file: Path,
    trait_legacy_id: str,
    ancestry_id: str | None = None,
) -> list[dict[str, str | None]]:
    if not trait_legacy_id:
        raise DatabaseError("Trait legacy id must not be empty.")

    params = ["bottom-line", trait_legacy_id]
    ancestry_filter = ""
    if ancestry_id:
        ancestry_filter = "AND artifact.ancestry_id = ?"
        params.append(ancestry_id)

    try:
        with connect_database(database_file) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    artifact.id,
                    artifact.name,
                    artifact.trait_legacy_id,
                    artifact.ancestry_id,
                    trait.name AS trait_name,
                    ancestry.name AS ancestry_name
                FROM prov_artifact AS artifact
                LEFT JOIN prov_trait AS trait
                    ON artifact.trait_legacy_id = trait.legacy_id
                LEFT JOIN prov_ancestry AS ancestry
                    ON artifact.ancestry_id = ancestry.ancestry_id
                WHERE artifact.pipeline_type = ?
                    AND artifact.trait_legacy_id = ?
                    {ancestry_filter}
                ORDER BY ancestry.name ASC, artifact.id ASC
                """,
                params,
            ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to list bottom-line artifacts for trait {trait_legacy_id}: {exc}") from exc

    return [{key: row[key] for key in row.keys()} for row in rows]


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
