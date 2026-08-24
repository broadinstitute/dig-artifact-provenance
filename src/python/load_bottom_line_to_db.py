#!/usr/bin/env python3
"""Load bottom-line provenance JSON files into the SQLite provenance database."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "bottom-line-provenance"
DEFAULT_DATABASE = REPO_ROOT / "data" / "database" / "provenance_db.sqlite"
DEFAULT_LOG_FILE = REPO_ROOT / "logs" / "bottom-line-provenance.log"
PIPELINE_TYPE = "bottom-line"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load bottom-line provenance JSON files into SQLite.")
    parser.add_argument(
        "--in-data-dir",
        default=str(DEFAULT_DATA_DIR),
        help=f"Directory containing provenance JSON files. Default: {DEFAULT_DATA_DIR}",
    )
    parser.add_argument(
        "--in-database",
        default=str(DEFAULT_DATABASE),
        help=f"SQLite database file to load. Default: {DEFAULT_DATABASE}",
    )
    parser.add_argument(
        "--in-log-file",
        default=str(DEFAULT_LOG_FILE),
        help=f"Log file for loader activity. Default: {DEFAULT_LOG_FILE}",
    )
    return parser.parse_args()


def derive_name(document: dict, source_file: Path) -> str:
    drs_objects = document.get("drs_objects", [])
    if drs_objects:
        description = drs_objects[0].get("description")
        if description:
            return str(description)

    root_location = document.get("root_location_path")
    if root_location:
        return f"Bottom-line provenance for {root_location}"

    return f"Bottom-line provenance for {source_file.stem}"


def configure_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_file,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )


def load_documents(data_dir: Path) -> tuple[list[tuple[str, str, str, str]], int]:
    rows: list[tuple[str, str, str, str]] = []
    files_read = 0

    for json_file in sorted(data_dir.glob("*.json")):
        files_read += 1
        try:
            document = json.loads(json_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logging.error("Skipping malformed provenance JSON file %s: %s", json_file.name, exc)
            continue

        provenance = json.dumps(document, separators=(",", ":"))
        artifact_id = json_file.stem
        name = derive_name(document, json_file)
        rows.append((artifact_id, PIPELINE_TYPE, provenance, name))
        logging.info("Prepared database record for provenance file %s", json_file.name)

    return rows, files_read


def ensure_paths(data_dir: Path, database_file: Path, log_file: Path) -> None:
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Input data directory does not exist: {data_dir}")

    if not database_file.exists():
        raise FileNotFoundError(f"Database file does not exist: {database_file}")

    log_file.parent.mkdir(parents=True, exist_ok=True)


def load_to_database(data_dir: Path, database_file: Path, log_file: Path) -> tuple[int, int]:
    ensure_paths(data_dir, database_file, log_file)
    configure_logging(log_file)
    rows, files_read = load_documents(data_dir)

    with sqlite3.connect(database_file) as connection:
        connection.execute("DELETE FROM prov_artifact WHERE pipeline_type = ?", (PIPELINE_TYPE,))
        connection.executemany(
            """
            INSERT INTO prov_artifact (id, pipeline_type, provenance, name, description)
            VALUES (?, ?, ?, ?, NULL)
            """,
            rows,
        )
        connection.commit()

    logging.info("Files read: %s", files_read)
    logging.info("Database records created: %s", len(rows))
    return len(rows), files_read


def main() -> int:
    args = parse_args()
    data_dir = Path(args.in_data_dir).expanduser().resolve()
    database_file = Path(args.in_database).expanduser().resolve()
    log_file = Path(args.in_log_file).expanduser().resolve()

    count, files_read = load_to_database(data_dir, database_file, log_file)
    print(
        f"Read {files_read} files and loaded {count} bottom-line provenance documents into {database_file}. "
        f"Log written to {log_file}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
