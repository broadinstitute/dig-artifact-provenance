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
                ORDER BY name ASC, legacy_id ASC
                """
            ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to list provenance traits: {exc}") from exc

    return [{key: row[key] for key in row.keys()} for row in rows]


def list_traits_full(database_file: Path) -> list[dict[str, object]]:
    """List every trait with its bottom-line artifacts nested under 'ancestries'."""
    try:
        with connect_database(database_file) as connection:
            trait_rows = connection.execute(
                """
                SELECT legacy_id, kpn_id, name, description
                FROM prov_trait
                ORDER BY name ASC, legacy_id ASC
                """
            ).fetchall()
            artifact_rows = connection.execute(
                """
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
                ORDER BY ancestry.name ASC, artifact.id ASC
                """,
                ("bottom-line",),
            ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to list provenance traits with ancestries: {exc}") from exc

    artifacts_by_trait: dict[str, list[dict[str, str | None]]] = {}
    for row in artifact_rows:
        artifact = {key: row[key] for key in row.keys()}
        artifacts_by_trait.setdefault(artifact["trait_legacy_id"], []).append(artifact)

    traits: list[dict[str, object]] = []
    for row in trait_rows:
        trait = {key: row[key] for key in row.keys()}
        trait["ancestries"] = artifacts_by_trait.get(trait["legacy_id"], [])
        traits.append(trait)

    return traits


def get_trait_by_legacy_id(database_file: Path, legacy_id: str) -> dict[str, str | None] | None:
    if not legacy_id:
        raise DatabaseError("Trait legacy id must not be empty.")

    try:
        with connect_database(database_file) as connection:
            row = connection.execute(
                """
                SELECT legacy_id, kpn_id, name, description
                FROM prov_trait
                WHERE legacy_id = ?
                """,
                (legacy_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to fetch provenance trait {legacy_id}: {exc}") from exc

    if row is None:
        return None

    return {key: row[key] for key in row.keys()}


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
                SELECT
                    artifact.id,
                    artifact.pipeline_type,
                    artifact.provenance,
                    artifact.name,
                    artifact.trait_legacy_id,
                    artifact.ancestry_id,
                    trait.name AS trait_name,
                    ancestry.name AS ancestry_name,
                    artifact.description
                FROM prov_artifact AS artifact
                LEFT JOIN prov_trait AS trait
                    ON artifact.trait_legacy_id = trait.legacy_id
                LEFT JOIN prov_ancestry AS ancestry
                    ON artifact.ancestry_id = ancestry.ancestry_id
                WHERE artifact.id = ?
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


def get_gene_set_details_by_id(database_file: Path, gene_set_id: str) -> dict[str, str | None] | None:
    if not gene_set_id:
        raise DatabaseError("Gene set id must not be empty.")

    try:
        with connect_database(database_file) as connection:
            row = connection.execute(
                """
                SELECT
                    artifact.id,
                    artifact.name,
                    COALESCE(pipeline.name, artifact.pipeline_type) AS pipeline_type,
                    artifact.pipeline_type AS pipeline_id,
                    artifact.document_id,
                    artifact.description
                FROM prov_artifact AS artifact
                LEFT JOIN prov_pipeline AS pipeline
                    ON artifact.pipeline_type = pipeline.pipeline_id
                WHERE artifact.id = ?
                    AND artifact.pipeline_type = ?
                """,
                (gene_set_id, "geneset"),
            ).fetchone()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to fetch gene set artifact {gene_set_id}: {exc}") from exc

    if row is None:
        return None

    return {key: row[key] for key in row.keys()}
