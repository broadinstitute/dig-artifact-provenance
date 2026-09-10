#!/usr/bin/env python3
"""Create bottom-line DAPPER provenance and load it into SQLite in one step.

Run from the repository root:

    python3 src/python/onestep/create_bottom_line_dapper_provenance.py

Required arguments:

- none

Eaxmple:

  python3 src/python/onestep/create_bottom_line_dapper_provenance.py \
    --in-database /tmp/onestep_provenance_db.sqlite \
    --in-log-file /tmp/onestep_bottom_line.log \
    --save-provenance-files \
    --out-dir /tmp/onestep_saved_provenance \
    --out-graph-file /tmp/onestep_graph.json


Optional arguments and defaults:

- ``--s3-listing-dir``: ``data/s3``
- ``--in-database``: ``data/database/provenance_db.sqlite``
- ``--in-log-file``: ``logs/bottom-line-provenance.log``
- ``--save-provenance-files``: disabled by default
- ``--out-dir``: ``data/bottom-line-provenance`` when
  ``--save-provenance-files`` is used
- ``--out-graph-file``: not set by default; when provided, writes the
  intermediate computed-id graph JSON to that path.

This combines:

- ``src/python/individualsteps/create_bottom_line_graph.py``
- ``src/python/individualsteps/create_bottom_line_dapper.py``
- ``src/python/individualsteps/load_bottom_line_to_db.py``

The generated graph uses DAPPER 0.1.0 computed identifiers from:

- https://github.com/broadinstitute/dapper/releases/tag/0.1.0
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path


PYTHON_DIR = Path(__file__).resolve().parents[1]
INDIVIDUALSTEPS_DIR = PYTHON_DIR / "individualsteps"
for import_dir in (PYTHON_DIR, INDIVIDUALSTEPS_DIR):
    if str(import_dir) not in sys.path:
        sys.path.insert(0, str(import_dir))

from create_bottom_line_dapper import (  # noqa: E402
    RECOMMENDATION_REF,
    build_outgoing_edges,
    collect_provenance_subgraph,
    edge_target_map,
    filename_from_location_path,
    make_activity_entry,
    make_c2m2_file_entry,
    make_dataset_entry,
    make_drs_entry,
    make_edge_entry,
)
from create_bottom_line_graph import (  # noqa: E402
    DAPPER_ID_PROFILE,
    DAPPER_RELEASE,
    build_graph,
    listing_files,
)
from load_bottom_line_to_db import (  # noqa: E402
    PIPELINE_TYPE,
    configure_logging,
    derive_name,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_S3_LISTING_DIR = REPO_ROOT / "data" / "s3"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "bottom-line-provenance"
DEFAULT_GRAPH_OUTPUT = REPO_ROOT / "data" / "graph" / "provenance_graph.json"
DEFAULT_DATABASE = REPO_ROOT / "data" / "database" / "provenance_db.sqlite"
DEFAULT_LOG_FILE = REPO_ROOT / "logs" / "bottom-line-provenance.log"
DEFAULT_GRAPH_REFERENCE = "<in-memory bottom-line DAPPER graph>"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build bottom-line DAPPER provenance and load it into SQLite."
    )
    parser.add_argument(
        "--s3-listing-dir",
        default=str(DEFAULT_S3_LISTING_DIR),
        help=f"Directory containing S3 listing snapshot text files. Default: {DEFAULT_S3_LISTING_DIR}",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=(
            "Output directory for DAPPER provenance JSON files when --save-provenance-files is set. "
            f"Default: {DEFAULT_OUTPUT_DIR}"
        ),
    )
    parser.add_argument(
        "--save-provenance-files",
        action="store_true",
        help="Write individual provenance JSON files. Default: disabled.",
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
    parser.add_argument(
        "--out-graph-file",
        default=None,
        help=(
            "Optional path for writing the intermediate computed-id graph JSON. "
            f"Default: not written. Common value: {DEFAULT_GRAPH_OUTPUT}"
        ),
    )
    return parser.parse_args()


def build_documents_from_graph(graph: dict, graph_reference: str) -> list[tuple[str, dict]]:
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = {edge["id"]: edge for edge in graph["edges"]}
    outgoing = build_outgoing_edges(edges.values())
    generated_by_map = edge_target_map(edges.values(), "WasGeneratedBy")

    open_data_nodes = [
        node
        for node in nodes.values()
        if node.get("directory_kind") == "open_data_endpoint"
        and str(node.get("location_path", "")).startswith("s3://dig-open-bottom-line-analysis-stg/")
    ]

    documents: list[tuple[str, dict]] = []
    for root_node in sorted(open_data_nodes, key=lambda item: str(item.get("location_path", ""))):
        subgraph_node_ids, subgraph_edge_ids = collect_provenance_subgraph(root_node["id"], outgoing)
        subgraph_nodes = [nodes[node_id] for node_id in sorted(subgraph_node_ids)]
        subgraph_edges = [edges[edge_id] for edge_id in sorted(subgraph_edge_ids)]

        datasets = []
        drs_objects = []
        activities = []
        c2m2_files = []
        for node in subgraph_nodes:
            dapper_class = node.get("dapper_class")
            if dapper_class == "Dataset":
                datasets.append(make_dataset_entry(node, generated_by_map))
            elif dapper_class == "DrsObject":
                drs_objects.append(make_drs_entry(node, generated_by_map))
            elif dapper_class == "Activity":
                activities.append(make_activity_entry(node))
            elif dapper_class == "C2M2File":
                c2m2_files.append(make_c2m2_file_entry(node, generated_by_map))

        document = {
            "reference_graph_file": graph_reference,
            "recommendation_reference": RECOMMENDATION_REF,
            "dapper_release": graph.get("dapper_release", DAPPER_RELEASE),
            "dapper_id_profile": graph.get("dapper_id_profile", DAPPER_ID_PROFILE),
            "root_node_id": root_node["id"],
            "root_location_path": root_node.get("location_path"),
            "datasets": datasets,
            "drs_objects": drs_objects,
            "activities": activities,
            "c2m2_files": c2m2_files,
            "edges": [make_edge_entry(edge) for edge in subgraph_edges],
            "graph": {
                "nodes": subgraph_nodes,
                "edges": subgraph_edges,
            },
        }

        filename = filename_from_location_path(str(root_node.get("location_path", root_node["id"])))
        documents.append((filename, document))

    return documents


def save_documents(documents: list[tuple[str, dict]], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, document in documents:
        out_file = out_dir / filename
        out_file.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return len(documents)


def load_documents_to_database(documents: list[tuple[str, dict]], database_file: Path, log_file: Path) -> int:
    if not database_file.exists():
        raise FileNotFoundError(f"Database file does not exist: {database_file}")

    configure_logging(log_file)
    rows: list[tuple[str, str, str, str]] = []

    for filename, document in documents:
        source_file = Path(filename)
        artifact_id = source_file.stem
        provenance = json.dumps(document, separators=(",", ":"))
        name = derive_name(document, source_file)
        rows.append((artifact_id, PIPELINE_TYPE, provenance, name))
        logging.info("Prepared database record for provenance file %s", filename)

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

    logging.info("Files read: %s", len(documents))
    logging.info("Database records created: %s", len(rows))
    return len(rows)


def main() -> int:
    args = parse_args()
    s3_listing_dir = Path(args.s3_listing_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_graph_file = Path(args.out_graph_file).expanduser().resolve() if args.out_graph_file else None
    database_file = Path(args.in_database).expanduser().resolve()
    log_file = Path(args.in_log_file).expanduser().resolve()

    graph, missing_messages = build_graph(listing_files(s3_listing_dir))

    graph_reference = DEFAULT_GRAPH_REFERENCE
    if out_graph_file is not None:
        out_graph_file.parent.mkdir(parents=True, exist_ok=True)
        out_graph_file.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
        graph_reference = str(out_graph_file)

    documents = build_documents_from_graph(graph, graph_reference)
    loaded_count = load_documents_to_database(documents, database_file, log_file)
    print(f"Built graph with {len(graph['nodes'])} nodes and {len(graph['edges'])} edges")
    print(f"Loaded {loaded_count} bottom-line provenance documents into {database_file}")
    print(f"Log written to {log_file}")

    if args.save_provenance_files:
        saved_count = save_documents(documents, out_dir)
        print(f"Wrote {saved_count} DAPPER provenance files to {out_dir}")

    if missing_messages:
        print("Missing S3 listing snapshots:")
        for message in missing_messages:
            print(f"- {message}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
