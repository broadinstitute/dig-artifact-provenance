#!/usr/bin/env python3
"""Ingest gene-set DAPPER provenance YAML files from S3 into SQLite.

Run from the repository root:

    python3 src/python/gene_set/ingest_gene_set_prov_from_s3.py \
      --in_s3_bucket s3://dig-gene-set-data/marc-test/ \
      --in_sqlite_db python-flask-server/data/provenance_db.sqlite \
      --in_log_file logs/gene_set_provenance_ingest.log

Required arguments:

- ``--in_s3_bucket``: S3 URI for the bucket or bucket/prefix to search.
- ``--in_sqlite_db``: SQLite database file containing ``prov_artifact`` and
  ``prov_document``.

Optional arguments:

- ``--in_log_file``: log file path. If omitted, logs are written to stdout.
- ``--in_prov_file_name``: provenance YAML file basename to search for.
  Default: ``geneset.provenance.dapper.yaml``.
  Only exact filename matches under an ``extractor`` directory segment are loaded.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

try:
    import yaml
except ModuleNotFoundError as exc:
    raise SystemExit("ERROR: PyYAML is required to parse provenance YAML files.") from exc


PIPELINE_TYPE = "geneset"
NAN_VALUE = "NaN"

REQUIRED_ARTIFACT_COLUMNS = {
    "id",
    "pipeline_type",
    "provenance",
    "name",
    "document_id",
    "trait_legacy_id",
    "ancestry_id",
}
REQUIRED_DOCUMENT_COLUMNS = {
    "document_id",
    "pipeline_type",
    "name",
    "document_text",
    "description",
}


@dataclass(frozen=True)
class S3Location:
    bucket: str
    prefix: str


@dataclass(frozen=True)
class DocumentRow:
    document_id: str
    name: str
    document_text: str
    description: str | None


@dataclass(frozen=True)
class ArtifactRow:
    artifact_id: str
    document_id: str
    name: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load gene-set provenance YAML files from S3 into provenance SQLite tables."
    )
    parser.add_argument(
        "--in_s3_bucket",
        required=True,
        help="S3 bucket or bucket/prefix URI to recursively search, for example s3://bucket/prefix/.",
    )
    parser.add_argument(
        "--in_sqlite_db",
        required=True,
        help="SQLite database file containing prov_artifact and prov_document.",
    )
    parser.add_argument(
        "--in_log_file",
        default=None,
        help="Optional log file path. Default: log to stdout.",
    )
    parser.add_argument(
        "--in_prov_file_name",
        default="geneset.provenance.dapper.yaml",
        help="Provenance YAML basename to search for. Default: geneset.provenance.dapper.yaml.",
    )
    return parser.parse_args()


def configure_logging(log_file: Path | None) -> None:
    handlers: list[logging.Handler]
    if log_file is None:
        handlers = [logging.StreamHandler(sys.stdout)]
    else:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers = [logging.FileHandler(log_file, encoding="utf-8")]

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
        force=True,
    )


def parse_s3_uri(s3_uri: str) -> S3Location:
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Expected S3 URI like s3://bucket/prefix, got: {s3_uri}")

    return S3Location(bucket=parsed.netloc, prefix=parsed.path.lstrip("/"))


def run_aws_command(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"AWS command failed: {' '.join(command)}\n{detail}")
    return result.stdout


def list_matching_s3_files(s3_uri: str, file_name: str) -> list[str]:
    location = parse_s3_uri(s3_uri)
    output = run_aws_command(["aws", "s3", "ls", s3_uri, "--recursive"])
    matches: list[str] = []

    for line in output.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) != 4:
            continue
        key = parts[3]
        if is_matching_provenance_key(key, file_name):
            matches.append(f"s3://{location.bucket}/{key}")

    return sorted(matches)


def is_matching_provenance_key(s3_key: str, file_name: str) -> bool:
    path_parts = Path(s3_key).parts
    return Path(s3_key).name == file_name and "extractor" in path_parts[:-1]


def read_s3_text(s3_uri: str) -> str:
    return run_aws_command(["aws", "s3", "cp", "--no-progress", s3_uri, "-"])


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}


def validate_database_schema(connection: sqlite3.Connection) -> None:
    artifact_columns = table_columns(connection, "prov_artifact")
    if not artifact_columns:
        raise RuntimeError("Database table prov_artifact was not found.")

    missing_artifact_columns = REQUIRED_ARTIFACT_COLUMNS - artifact_columns
    if missing_artifact_columns:
        raise RuntimeError(
            "prov_artifact is missing required column(s): "
            + ", ".join(sorted(missing_artifact_columns))
        )

    document_columns = table_columns(connection, "prov_document")
    if not document_columns:
        raise RuntimeError("Database table prov_document was not found.")

    missing_document_columns = REQUIRED_DOCUMENT_COLUMNS - document_columns
    if missing_document_columns:
        raise RuntimeError(
            "prov_document is missing required column(s): "
            + ", ".join(sorted(missing_document_columns))
        )


def required_text(record: dict, key: str, record_type: str, source: str) -> str:
    value = record.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"{source}: {record_type} is missing required field '{key}'.")
    return str(value).strip()


def optional_text(record: dict, key: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_yaml_document(yaml_text: str, s3_uri: str) -> dict:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    data = yaml.load(yaml_text, Loader=loader)
    if not isinstance(data, dict):
        raise ValueError(f"{s3_uri}: YAML root must be a mapping.")
    if not isinstance(data.get("gene_set_collections"), list):
        raise ValueError(f"{s3_uri}: YAML must contain a gene_set_collections list.")
    gene_sets = data.get("gene_sets", [])
    if gene_sets is None:
        gene_sets = []
    if not isinstance(gene_sets, list):
        raise ValueError(f"{s3_uri}: gene_sets must be a list when present.")
    return data


def collection_gene_set_ids(collection: dict, gene_sets: list[dict], s3_uri: str) -> list[str]:
    collection_id = required_text(collection, "id", "gene_set_collection", s3_uri)
    ordered_ids: list[str] = []
    seen_ids: set[str] = set()

    members = collection.get("members", [])
    if members is None:
        members = []
    if not isinstance(members, list):
        raise ValueError(f"{s3_uri}: collection {collection_id} members must be a list when present.")

    for member_id in members:
        member_text = str(member_id).strip()
        if member_text and member_text not in seen_ids:
            ordered_ids.append(member_text)
            seen_ids.add(member_text)

    for gene_set in gene_sets:
        if not isinstance(gene_set, dict):
            raise ValueError(f"{s3_uri}: gene_sets entries must be mappings.")
        refs = gene_set.get("in_gene_set_collection", [])
        if refs is None:
            refs = []
        if isinstance(refs, str):
            refs = [refs]
        if not isinstance(refs, list):
            raise ValueError(
                f"{s3_uri}: gene set in_gene_set_collection must be a list or string when present."
            )
        gene_set_id = required_text(gene_set, "id", "gene_set", s3_uri)
        if collection_id in {str(ref).strip() for ref in refs} and gene_set_id not in seen_ids:
            ordered_ids.append(gene_set_id)
            seen_ids.add(gene_set_id)

    return ordered_ids


def rows_from_yaml(s3_uri: str, yaml_text: str) -> tuple[list[DocumentRow], list[ArtifactRow], list[str]]:
    data = load_yaml_document(yaml_text, s3_uri)
    collections = data["gene_set_collections"]
    gene_sets = data.get("gene_sets") or []
    gene_set_by_id: dict[str, dict] = {}

    for gene_set in gene_sets:
        if not isinstance(gene_set, dict):
            raise ValueError(f"{s3_uri}: gene_sets entries must be mappings.")
        gene_set_id = required_text(gene_set, "id", "gene_set", s3_uri)
        gene_set_by_id[gene_set_id] = gene_set

    documents: list[DocumentRow] = []
    artifacts: list[ArtifactRow] = []
    collection_logs: list[str] = []

    for collection in collections:
        if not isinstance(collection, dict):
            raise ValueError(f"{s3_uri}: gene_set_collections entries must be mappings.")

        collection_id = required_text(collection, "id", "gene_set_collection", s3_uri)
        collection_name = required_text(collection, "name", "gene_set_collection", s3_uri)
        collection_description = optional_text(collection, "description")
        gene_set_ids = collection_gene_set_ids(collection, gene_sets, s3_uri)

        documents.append(
            DocumentRow(
                document_id=collection_id,
                name=collection_name,
                document_text=yaml_text,
                description=collection_description,
            )
        )

        for gene_set_id in gene_set_ids:
            gene_set = gene_set_by_id.get(gene_set_id)
            if gene_set is None:
                raise ValueError(
                    f"{s3_uri}: collection {collection_id} references missing gene set {gene_set_id}."
                )
            artifacts.append(
                ArtifactRow(
                    artifact_id=gene_set_id,
                    document_id=collection_id,
                    name=required_text(gene_set, "name", "gene_set", s3_uri),
                )
            )

        collection_logs.append(
            f"Inserted gene set collection id={collection_id} name={collection_name!r} "
            f"associated_gene_sets={len(gene_set_ids)}"
        )

    return documents, artifacts, collection_logs


def existing_ids(connection: sqlite3.Connection, table_name: str, id_column: str) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            f"SELECT {id_column} FROM {table_name} WHERE pipeline_type != ?",
            (PIPELINE_TYPE,),
        )
    }


def ingest_gene_set_provenance(
    s3_uri: str,
    database_file: Path,
    prov_file_name: str,
) -> tuple[int, int, int]:
    if not database_file.exists():
        raise FileNotFoundError(f"SQLite database file does not exist: {database_file}")

    s3_files = list_matching_s3_files(s3_uri, prov_file_name)
    if not s3_files:
        raise RuntimeError(f"No files named {prov_file_name!r} found under {s3_uri}")

    all_documents: list[DocumentRow] = []
    all_artifacts: list[ArtifactRow] = []
    collection_logs: list[str] = []

    for s3_file in s3_files:
        logging.info("Reading provenance file from S3: %s", s3_file)
        yaml_text = read_s3_text(s3_file)
        documents, artifacts, logs = rows_from_yaml(s3_file, yaml_text)
        all_documents.extend(documents)
        all_artifacts.extend(artifacts)
        collection_logs.extend(logs)

    with sqlite3.connect(database_file) as connection:
        validate_database_schema(connection)
        existing_document_ids = existing_ids(connection, "prov_document", "document_id")
        existing_artifact_ids = existing_ids(connection, "prov_artifact", "id")
        inserted_document_ids: set[str] = set()
        inserted_artifact_ids: set[str] = set()
        skipped_document_ids: set[str] = set()
        document_rows: list[tuple[str, str, str, str, str | None]] = []
        artifact_rows: list[tuple[str, str, str, str, str, str, str]] = []

        for document in all_documents:
            if document.document_id in existing_document_ids or document.document_id in inserted_document_ids:
                skipped_document_ids.add(document.document_id)
                logging.error(
                    "Skipping duplicate gene set collection id=%s name=%r",
                    document.document_id,
                    document.name,
                )
                continue

            inserted_document_ids.add(document.document_id)
            document_rows.append(
                (
                    document.document_id,
                    PIPELINE_TYPE,
                    document.name,
                    document.document_text,
                    document.description,
                )
            )

        for artifact in all_artifacts:
            if artifact.document_id in skipped_document_ids:
                continue
            if artifact.artifact_id in existing_artifact_ids or artifact.artifact_id in inserted_artifact_ids:
                logging.error(
                    "Skipping duplicate gene set id=%s name=%r",
                    artifact.artifact_id,
                    artifact.name,
                )
                continue

            inserted_artifact_ids.add(artifact.artifact_id)
            artifact_rows.append(
                (
                    artifact.artifact_id,
                    PIPELINE_TYPE,
                    NAN_VALUE,
                    artifact.name,
                    artifact.document_id,
                    NAN_VALUE,
                    NAN_VALUE,
                )
            )

        with connection:
            connection.execute("DELETE FROM prov_artifact WHERE pipeline_type = ?", (PIPELINE_TYPE,))
            connection.execute("DELETE FROM prov_document WHERE pipeline_type = ?", (PIPELINE_TYPE,))
            connection.executemany(
                """
                INSERT INTO prov_document
                    (document_id, pipeline_type, name, document_text, description)
                VALUES (?, ?, ?, ?, ?)
                """,
                document_rows,
            )
            connection.executemany(
                """
                INSERT INTO prov_artifact
                    (id, pipeline_type, provenance, name, document_id, trait_legacy_id, ancestry_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                artifact_rows,
            )

    for message in collection_logs:
        logging.info(message)
    logging.info("S3 provenance files read: %s", len(s3_files))
    logging.info("Gene set collections inserted: %s", len(document_rows))
    logging.info("Gene set artifacts inserted: %s", len(artifact_rows))

    return len(s3_files), len(document_rows), len(artifact_rows)


def main() -> int:
    args = parse_args()
    database_file = Path(args.in_sqlite_db).expanduser().resolve()
    log_file = Path(args.in_log_file).expanduser().resolve() if args.in_log_file else None
    configure_logging(log_file)

    try:
        file_count, document_count, artifact_count = ingest_gene_set_provenance(
            args.in_s3_bucket,
            database_file,
            args.in_prov_file_name,
        )
    except Exception as exc:
        logging.exception("Gene-set provenance ingest failed: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Read {file_count} S3 provenance file(s).")
    print(f"Inserted {document_count} gene-set collection document row(s).")
    print(f"Inserted {artifact_count} gene-set artifact row(s).")
    if log_file is not None:
        print(f"Log written to {log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
