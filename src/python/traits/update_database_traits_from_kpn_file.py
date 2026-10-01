#!/usr/bin/env python3
"""Update prov_trait metadata from a KPN trait registry TSV file.

Run from the repository root:

    python3 src/python/traits/update_database_traits_from_kpn_file.py \
      --in_database data/database/20260925provTraitAncestry02_db.sqlite \
      --in_trait_file data/traits/kpn_trait_registry_v002.tsv \
      --out_report_log logs/kpn_trait_update_report.log

Required arguments:

- ``--in_database``: SQLite database file containing ``prov_trait``
- ``--in_trait_file``: tab-delimited KPN trait registry file

Optional arguments:

- ``--out_report_log``: report file for missing and duplicate match details.
  If omitted, no detailed report file is written.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


REQUIRED_TSV_COLUMNS = {"legacy_phenotype_id", "portal_id", "phenotype_name"}
REQUIRED_DB_COLUMNS = {"legacy_id", "kpn_id", "name", "description"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update prov_trait from a KPN trait registry TSV file.")
    parser.add_argument(
        "--in_database",
        required=True,
        help="SQLite database file containing the prov_trait table.",
    )
    parser.add_argument(
        "--in_trait_file",
        required=True,
        help="Tab-delimited KPN trait registry file.",
    )
    parser.add_argument(
        "--out_report_log",
        default=None,
        help="Optional file for detailed missing/duplicate match report. Default: no report file.",
    )
    return parser.parse_args()


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}


def validate_database(connection: sqlite3.Connection) -> list[str]:
    columns = table_columns(connection, "prov_trait")
    if not columns:
        return ["Database table prov_trait was not found."]

    missing_columns = REQUIRED_DB_COLUMNS - columns
    if missing_columns:
        return ["prov_trait is missing required column(s): " + ", ".join(sorted(missing_columns))]

    return []


def read_trait_rows(trait_file: Path) -> tuple[list[dict[str, str]], list[str]]:
    with trait_file.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        fieldnames = set(reader.fieldnames or [])
        missing_columns = REQUIRED_TSV_COLUMNS - fieldnames
        if missing_columns:
            return [], ["Trait file is missing required column(s): " + ", ".join(sorted(missing_columns))]

        return list(reader), []


def write_report(
    report_file: Path,
    missing_legacy_ids: list[str],
    duplicate_legacy_ids: list[str],
) -> None:
    report_file.parent.mkdir(parents=True, exist_ok=True)
    with report_file.open("w", encoding="utf-8") as handle:
        handle.write("KPN trait update report\n")
        handle.write("=======================\n\n")

        handle.write(f"Missing prov_trait.legacy_id values: {len(missing_legacy_ids)}\n")
        for legacy_id in sorted(set(missing_legacy_ids)):
            handle.write(f"- {legacy_id}\n")

        handle.write("\n")
        handle.write(f"Duplicate prov_trait.legacy_id values updated once: {len(duplicate_legacy_ids)}\n")
        for legacy_id in sorted(set(duplicate_legacy_ids)):
            handle.write(f"- {legacy_id}\n")


def update_traits(database_file: Path, trait_file: Path, report_file: Path | None = None) -> int:
    if not database_file.exists():
        print(f"ERROR: Database file does not exist: {database_file}")
        return 2
    if not trait_file.exists():
        print(f"ERROR: Trait file does not exist: {trait_file}")
        return 2

    rows, trait_errors = read_trait_rows(trait_file)
    if trait_errors:
        for error in trait_errors:
            print(f"ERROR: {error}")
        print("No database updates were applied.")
        return 2

    with sqlite3.connect(database_file) as connection:
        db_errors = validate_database(connection)
        if db_errors:
            for error in db_errors:
                print(f"ERROR: {error}")
            print("No database updates were applied.")
            return 2

        missing_legacy_ids: list[str] = []
        duplicate_legacy_ids: list[str] = []
        updated_legacy_ids: list[str] = []
        skipped_blank_ids = 0

        with connection:
            for row in rows:
                legacy_id = (row.get("legacy_phenotype_id") or "").strip()
                portal_id = (row.get("portal_id") or "").strip()
                phenotype_name = (row.get("phenotype_name") or "").strip()

                if not legacy_id:
                    skipped_blank_ids += 1
                    continue

                matches = connection.execute(
                    "SELECT rowid FROM prov_trait WHERE legacy_id = ?",
                    (legacy_id,),
                ).fetchall()

                if not matches:
                    missing_legacy_ids.append(legacy_id)
                    continue

                if len(matches) > 1:
                    duplicate_legacy_ids.append(legacy_id)

                connection.execute(
                    """
                    UPDATE prov_trait
                    SET kpn_id = ?,
                        name = ?,
                        description = ?
                    WHERE legacy_id = ?
                    """,
                    (portal_id, phenotype_name, phenotype_name, legacy_id),
                )
                updated_legacy_ids.append(legacy_id)

    print("KPN trait update complete.")
    print(f"Trait file rows read: {len(rows)}")
    print(f"Database rows updated: {len(updated_legacy_ids)}")
    print(f"Rows skipped with blank legacy_phenotype_id: {skipped_blank_ids}")
    print(f"Trait file rows with no matching prov_trait.legacy_id: {len(missing_legacy_ids)}")
    print(f"Trait file rows with duplicate database matches: {len(duplicate_legacy_ids)}")

    if report_file is not None:
        write_report(report_file, missing_legacy_ids, duplicate_legacy_ids)
        print(f"Detailed report written to: {report_file}")

    return 0


def main() -> int:
    args = parse_args()
    database_file = Path(args.in_database).expanduser().resolve()
    trait_file = Path(args.in_trait_file).expanduser().resolve()
    report_file = Path(args.out_report_log).expanduser().resolve() if args.out_report_log else None
    return update_traits(database_file, trait_file, report_file)


if __name__ == "__main__":
    raise SystemExit(main())
