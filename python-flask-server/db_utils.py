"""SQLite utility methods for the provenance Flask service."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    yaml = None


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


def list_gene_set_documents(database_file: Path) -> list[dict[str, str | None]]:
    try:
        with connect_database(database_file) as connection:
            rows = connection.execute(
                """
                SELECT document_id, name, description
                FROM prov_document
                WHERE pipeline_type = ?
                ORDER BY name ASC, document_id ASC
                """,
                ("geneset",),
            ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to list gene-set provenance documents: {exc}") from exc

    return [{key: row[key] for key in row.keys()} for row in rows]


def get_gene_set_document_by_id(database_file: Path, document_id: str) -> dict[str, str | None] | None:
    if not document_id:
        raise DatabaseError("Gene-set collection id must not be empty.")

    try:
        with connect_database(database_file) as connection:
            row = connection.execute(
                """
                SELECT document_id, name, description
                FROM prov_document
                WHERE document_id = ?
                    AND pipeline_type = ?
                """,
                (document_id, "geneset"),
            ).fetchone()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to fetch gene-set provenance document {document_id}: {exc}") from exc

    if row is None:
        return None

    return {key: row[key] for key in row.keys()}


def iter_gene_set_document_text_chunks(
    database_file: Path,
    document_id: str,
    chunk_size: int = 1024 * 1024,
):
    if not document_id:
        raise DatabaseError("Gene-set collection id must not be empty.")
    if chunk_size <= 0:
        raise DatabaseError("Document text chunk size must be greater than zero.")

    try:
        connection = connect_database(database_file)
        length_row = connection.execute(
            """
            SELECT LENGTH(document_text) AS document_length
            FROM prov_document
            WHERE document_id = ?
                AND pipeline_type = ?
            """,
            (document_id, "geneset"),
        ).fetchone()

        if length_row is None:
            raise DatabaseError(f"No gene-set provenance document found for id {document_id}.")

        document_length = int(length_row["document_length"] or 0)
        offset = 1
        while offset <= document_length:
            row = connection.execute(
                """
                SELECT SUBSTR(document_text, ?, ?) AS document_chunk
                FROM prov_document
                WHERE document_id = ?
                    AND pipeline_type = ?
                """,
                (offset, chunk_size, document_id, "geneset"),
            ).fetchone()
            if row is None:
                break
            yield row["document_chunk"] or ""
            offset += chunk_size
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to stream gene-set provenance document {document_id}: {exc}") from exc
    finally:
        try:
            connection.close()
        except UnboundLocalError:
            pass


def list_gene_sets_by_document_id(database_file: Path, document_id: str) -> list[dict[str, str | None]]:
    if not document_id:
        raise DatabaseError("Gene-set collection id must not be empty.")

    try:
        with connect_database(database_file) as connection:
            rows = connection.execute(
                """
                SELECT id, name, description
                FROM prov_artifact
                WHERE document_id = ?
                    AND pipeline_type = ?
                ORDER BY name ASC, id ASC
                """,
                (document_id, "geneset"),
            ).fetchall()
    except sqlite3.Error as exc:
        raise DatabaseError(f"Failed to list gene sets for collection {document_id}: {exc}") from exc

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
                    document.document_text,
                    artifact.description
                FROM prov_artifact AS artifact
                LEFT JOIN prov_document AS document
                    ON artifact.document_id = document.document_id
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

    artifact = {key: row[key] for key in row.keys()}
    document_text = artifact.pop("document_text", None)
    artifact["graph"] = build_gene_set_graph(gene_set_id, document_text)
    return artifact


def build_gene_set_graph(gene_set_id: str, document_text: str | None) -> dict[str, list[dict[str, str | None]]]:
    if not document_text:
        return {"nodes": [], "edges": []}
    if yaml is None:
        raise DatabaseError("PyYAML is required to parse gene-set provenance document_text.")

    gene_set = extract_yaml_item_by_id(document_text, "gene_sets", gene_set_id)
    if gene_set is None:
        return {"nodes": [{"id": gene_set_id, "name": gene_set_id, "dapper_class": "GeneSet"}], "edges": []}

    used_edges = parse_yaml_section(document_text, "used_edges")
    generated_edges = parse_yaml_section(document_text, "was_generated_by_edges")
    matching_generated_edges = [
        edge
        for edge in generated_edges
        if isinstance(edge, dict) and edge.get("subject") == gene_set_id and edge.get("object")
    ]

    activity_ids = {str(edge["object"]) for edge in matching_generated_edges}
    if gene_set.get("was_generated_by"):
        activity_id = str(gene_set["was_generated_by"])
        activity_ids.add(activity_id)
        if not any(edge.get("object") == activity_id for edge in matching_generated_edges):
            matching_generated_edges.append(
                {
                    "subject": gene_set_id,
                    "predicate": "prov:wasGeneratedBy",
                    "object": activity_id,
                }
            )

    matching_used_edges = [
        edge
        for edge in used_edges
        if isinstance(edge, dict) and edge.get("subject") in activity_ids and edge.get("object")
    ]
    file_ids = {str(edge["object"]) for edge in matching_used_edges}

    nodes = [
        normalize_gene_set_graph_node(gene_set, gene_set_id, "GeneSet", "gene_sets"),
    ]
    for activity_id in sorted(activity_ids):
        activity = extract_yaml_item_by_id(document_text, "activities", activity_id) or {"id": activity_id}
        nodes.append(normalize_gene_set_graph_node(activity, activity_id, "Activity", "activities"))
    for file_id in sorted(file_ids):
        file_node = extract_yaml_item_by_id(document_text, "files", file_id) or {"id": file_id}
        nodes.append(normalize_gene_set_graph_node(file_node, file_id, "File", "files"))

    graph_edges = matching_generated_edges + matching_used_edges
    edges = [
        {
            "id": f"{edge.get('predicate', 'edge')}-{index}-{edge['subject']}-{edge['object']}",
            "source": str(edge["subject"]),
            "target": str(edge["object"]),
            "predicate": edge.get("predicate"),
            "edge_role": edge.get("edge_role"),
        }
        for index, edge in enumerate(graph_edges)
    ]

    return {"nodes": nodes, "edges": edges}


def normalize_gene_set_graph_node(
    node: dict,
    node_id: str,
    dapper_class: str,
    node_group: str,
) -> dict[str, str | None]:
    return {
        "id": node_id,
        "name": node.get("name") or node.get("filename") or node_id,
        "description": node.get("description"),
        "dapper_class": dapper_class,
        "node_group": node_group,
    }


def parse_yaml_section(document_text: str, section_name: str) -> list[dict]:
    section_text = extract_top_level_section(document_text, section_name)
    if not section_text.strip():
        return []
    try:
        loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
        parsed = yaml.load(section_text, Loader=loader)
    except yaml.YAMLError as exc:
        raise DatabaseError(f"Stored gene-set provenance section {section_name} is not valid YAML: {exc}") from exc

    if parsed is None:
        return []
    if not isinstance(parsed, list):
        raise DatabaseError(f"Stored gene-set provenance section {section_name} must be a YAML list.")
    return parsed


def extract_yaml_item_by_id(document_text: str, section_name: str, item_id: str) -> dict | None:
    section_text = extract_top_level_section(document_text, section_name)
    if not section_text:
        return None

    marker = f"- id: {item_id}\n"
    item_start = section_text.find(marker)
    if item_start < 0:
        return None

    next_item_start = section_text.find("\n- id:", item_start + len(marker))
    item_text = section_text[item_start:] if next_item_start < 0 else section_text[item_start:next_item_start]

    try:
        loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
        parsed = yaml.load(item_text, Loader=loader)
    except yaml.YAMLError as exc:
        raise DatabaseError(f"Stored gene-set provenance item {item_id} is not valid YAML: {exc}") from exc

    if not isinstance(parsed, list) or not parsed or not isinstance(parsed[0], dict):
        raise DatabaseError(f"Stored gene-set provenance item {item_id} must be a YAML mapping.")
    return parsed[0]


def extract_top_level_section(document_text: str, section_name: str) -> str:
    section_marker = f"{section_name}:\n"
    section_start = document_text.find(section_marker)
    if section_start < 0:
        return ""

    content_start = section_start + len(section_marker)
    next_section = re.search(r"\n(?=[A-Za-z_][A-Za-z0-9_]*:\n)", document_text[content_start:])
    next_section_start = len(document_text)
    if next_section is not None:
        next_section_start = content_start + next_section.start() + 1

    return document_text[content_start:next_section_start]
