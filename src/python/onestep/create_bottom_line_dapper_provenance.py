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
- ``--max-endpoints``: not set by default; when provided, only builds the first
  N sorted open-data endpoint documents. Useful for one-endpoint lint tests.
- ``-skip-dapper-lint`` skips the linter provenance check

This combines:

- ``src/python/individualsteps/create_bottom_line_graph.py``
- ``src/python/individualsteps/create_bottom_line_dapper.py``
- ``src/python/individualsteps/load_bottom_line_to_db.py``

The generated graph uses DAPPER 0.1.0 computed identifiers from:

- https://github.com/broadinstitute/dapper/releases/tag/0.1.0

Each generated document uses the group-keyed DAPPER document shape expected by
the DAPPER 0.1.0 linter:

    uv run /Users/mduby/Code/DccWorkspace/DapperSchema/schema/lint/lint_provenance.py <provenance_file_input>

The emitted documents intentionally do not include application-only envelope
fields such as ``graph`` or ``root_location_path`` because the DAPPER linter runs
in closed mode and treats unknown top-level keys as errors.

Before linting, the script mints DAPPER-ID-1 computed node identifiers with:

    uv run /Users/mduby/Code/DccWorkspace/DapperSchema/schema/identity/dapper_identity.py assign <provenance_file_input>
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import subprocess
import tempfile
from pathlib import Path


PYTHON_DIR = Path(__file__).resolve().parents[1]
INDIVIDUALSTEPS_DIR = PYTHON_DIR / "individualsteps"
for import_dir in (PYTHON_DIR, INDIVIDUALSTEPS_DIR):
    if str(import_dir) not in sys.path:
        sys.path.insert(0, str(import_dir))

from create_bottom_line_dapper import (  # noqa: E402
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
    build_graph,
    listing_files,
)
from load_bottom_line_to_db import (  # noqa: E402
    PIPELINE_TYPE,
    configure_logging,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_S3_LISTING_DIR = REPO_ROOT / "data" / "s3"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "bottom-line-provenance"
DEFAULT_GRAPH_OUTPUT = REPO_ROOT / "data" / "graph" / "provenance_graph.json"
DEFAULT_DATABASE = REPO_ROOT / "data" / "database" / "provenance_db.sqlite"
DEFAULT_LOG_FILE = REPO_ROOT / "logs" / "bottom-line-provenance.log"
DEFAULT_GRAPH_REFERENCE = "<in-memory bottom-line DAPPER graph>"
DEFAULT_DAPPER_LINTER = Path("/Users/mduby/Code/DccWorkspace/DapperSchema/schema/lint/lint_provenance.py")
DEFAULT_DAPPER_IDENTITY = Path("/Users/mduby/Code/DccWorkspace/DapperSchema/schema/identity/dapper_identity.py")


def compact_dict(data: dict) -> dict:
    return {key: value for key, value in data.items() if value is not None and value != []}


def original_node_id(node: dict) -> str:
    """Use non-DAPPER graph ids so the linter does not verify stale digests."""
    return str(node.get("original_id") or node.get("id"))


def final_dataset_id(root_node: dict) -> str:
    location = str(root_node.get("location_path") or root_node.get("id"))
    artifact = filename_from_location_path(location).removesuffix(".json")
    return f"bottom-line-result:{artifact}"


def dapper_dataset_entry(record: dict, node_id: str) -> dict:
    return compact_dict(
        {
            "id": node_id,
            "name": record.get("name"),
            "resource_type": "dataset",
            "description": record.get("description"),
            "access_level": record.get("access_level"),
        }
    )


def dapper_drs_object_entry(record: dict, node_id: str) -> dict:
    return compact_dict(
        {
            "id": node_id,
            "name": record.get("published_filename") or record.get("description"),
            "drs_id": record.get("drs_id"),
            "self_uri": record.get("self_uri"),
            "mime_type": record.get("mime_type"),
            "access_methods": record.get("access_methods"),
        }
    )


def dapper_activity_entry(record: dict, node_id: str) -> dict:
    return compact_dict(
        {
            "id": node_id,
            "name": record.get("name"),
            "description": record.get("description"),
            "activity_type": record.get("activity_type"),
            "repo_url": record.get("repo_url"),
        }
    )


def dapper_c2m2_file_entry(record: dict, node_id: str) -> dict:
    return compact_dict(
        {
            "id": node_id,
            "name": record.get("name"),
            "description": record.get("description"),
            "filename": record.get("filename"),
            "local_id": record.get("local_id"),
        }
    )


def dapper_edge_entry(record: dict, id_map: dict[str, str]) -> dict:
    return compact_dict(
        {
            "subject": id_map.get(str(record.get("source")), record.get("source")),
            "predicate": record.get("predicate"),
            "object": id_map.get(str(record.get("target")), record.get("target")),
            "edge_role": record.get("edge_role"),
        }
    )


def relationship_group(relationship: str | None) -> str | None:
    return {
        "Used": "used_edges",
        "WasGeneratedBy": "was_generated_by_edges",
        "WasDerivedFrom": "was_derived_from_edges",
        "HasDrsObject": "has_drs_object_edges",
    }.get(str(relationship))


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
    parser.add_argument(
        "--dapper-linter",
        default=str(DEFAULT_DAPPER_LINTER),
        help=(
            "DAPPER provenance linter command target. "
            f"Default: {DEFAULT_DAPPER_LINTER}"
        ),
    )
    parser.add_argument(
        "--dapper-identity",
        default=str(DEFAULT_DAPPER_IDENTITY),
        help=(
            "DAPPER identity script used to mint DAPPER-ID-1 node ids. "
            f"Default: {DEFAULT_DAPPER_IDENTITY}"
        ),
    )
    parser.add_argument(
        "--skip-dapper-lint",
        action="store_true",
        help="Skip running the DAPPER linter before loading/writing documents. Default: disabled.",
    )
    parser.add_argument(
        "--max-endpoints",
        type=int,
        default=None,
        help="Optional maximum number of sorted open-data endpoints to export. Default: all endpoints.",
    )
    return parser.parse_args()


def make_terminal_dataset(root_node: dict, open_stage_id: str | None) -> dict:
    location = str(root_node.get("location_path") or "")
    phenotype = root_node.get("phenotype")
    ancestry = root_node.get("ancestry")
    name_bits = [bit for bit in (phenotype, ancestry) if bit]
    label = " / ".join(str(bit) for bit in name_bits) or filename_from_location_path(location).removesuffix(".json")
    dataset = {
        "id": final_dataset_id(root_node),
        "name": f"Published open-data bottom-line result for {label}",
        "version": "1",
        "resource_type": "dataset",
        "description": f"Published open-data bottom-line endpoint at {location}",
        "access_level": "public",
    }
    return dataset


def build_dapper_document(
    root_node: dict,
    subgraph_nodes: list[dict],
    subgraph_edges: list[dict],
    generated_by_map: dict[str, list[str]],
) -> dict:
    id_map = {node["id"]: original_node_id(node) for node in subgraph_nodes}
    root_drs_id = id_map[root_node["id"]]
    open_stage_ids = generated_by_map.get(root_node["id"], [])
    open_stage_id = id_map.get(open_stage_ids[0]) if open_stage_ids else None
    terminal_dataset_id = final_dataset_id(root_node)

    document: dict[str, list[dict]] = {
        "activities": [],
        "drs_objects": [],
        "datasets": [],
        "c2m2_files": [],
        "used_edges": [],
        "was_generated_by_edges": [],
        "was_derived_from_edges": [],
        "has_drs_object_edges": [],
    }

    for node in subgraph_nodes:
        dapper_class = node.get("dapper_class")
        node_id = id_map[node["id"]]
        if dapper_class == "Dataset":
            document["datasets"].append(dapper_dataset_entry(make_dataset_entry(node, generated_by_map), node_id))
        elif dapper_class == "DrsObject":
            document["drs_objects"].append(dapper_drs_object_entry(make_drs_entry(node, generated_by_map), node_id))
        elif dapper_class == "Activity":
            document["activities"].append(dapper_activity_entry(make_activity_entry(node), node_id))
        elif dapper_class == "C2M2File":
            document["c2m2_files"].append(dapper_c2m2_file_entry(make_c2m2_file_entry(node, generated_by_map), node_id))

    document["datasets"].append(make_terminal_dataset(root_node, open_stage_id))
    if open_stage_id:
        document["was_generated_by_edges"].append(
            {
                "subject": terminal_dataset_id,
                "predicate": "prov:wasGeneratedBy",
                "object": open_stage_id,
            }
        )
    document["has_drs_object_edges"].append(
        {
            "subject": terminal_dataset_id,
            "predicate": "dapper:hasDrsObject",
            "object": root_drs_id,
        }
    )

    class_by_id = {node["id"]: node.get("dapper_class") for node in subgraph_nodes}
    for edge in subgraph_edges:
        group = relationship_group(edge.get("relationship"))
        if group is None:
            continue
        if edge.get("relationship") == "WasGeneratedBy" and class_by_id.get(edge.get("source")) == "DrsObject":
            # The bottom-line profile models the published artifact as a Dataset
            # with a DRS object, not as a DRS object generated directly.
            continue
        document[group].append(dapper_edge_entry(make_edge_entry(edge), id_map))

    return {key: value for key, value in document.items() if value}


def build_documents_from_graph(
    graph: dict,
    graph_reference: str,
    max_endpoints: int | None = None,
) -> list[tuple[str, dict]]:
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

    sorted_open_data_nodes = sorted(open_data_nodes, key=lambda item: str(item.get("location_path", "")))
    if max_endpoints is not None:
        sorted_open_data_nodes = sorted_open_data_nodes[:max_endpoints]

    documents: list[tuple[str, dict]] = []
    for root_node in sorted_open_data_nodes:
        subgraph_node_ids, subgraph_edge_ids = collect_provenance_subgraph(root_node["id"], outgoing)
        subgraph_nodes = [nodes[node_id] for node_id in sorted(subgraph_node_ids)]
        subgraph_edges = [edges[edge_id] for edge_id in sorted(subgraph_edge_ids)]
        document = build_dapper_document(root_node, subgraph_nodes, subgraph_edges, generated_by_map)

        filename = filename_from_location_path(str(root_node.get("location_path", root_node["id"])))
        documents.append((filename, document))

    return documents


def save_documents(documents: list[tuple[str, dict]], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, document in documents:
        out_file = out_dir / filename
        out_file.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return len(documents)


def uv_environment() -> dict[str, str]:
    env = dict(os.environ)
    env["UV_CACHE_DIR"] = "/tmp/artifact_provenance_uv_cache"
    return env


def mint_document(filename: str, document: dict, dapper_identity: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="bottom_line_dapper_mint_") as temp_dir:
        temp_file = Path(temp_dir) / filename
        temp_file.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

        assign_result = subprocess.run(
            ["uv", "run", str(dapper_identity), "assign", str(temp_file)],
            capture_output=True,
            text=True,
            check=False,
            env=uv_environment(),
        )
        if assign_result.returncode != 0:
            detail = assign_result.stderr.strip() or assign_result.stdout.strip()
            raise RuntimeError(f"DAPPER identity assignment failed for {filename}: {detail}")

        convert_code = (
            "import json,sys,yaml;"
            "print(json.dumps(yaml.safe_load(open(sys.argv[1], encoding='utf-8')), separators=(',',':')))"
        )
        convert_result = subprocess.run(
            ["uv", "run", "--with", "pyyaml", "python", "-c", convert_code, str(temp_file)],
            capture_output=True,
            text=True,
            check=False,
            env=uv_environment(),
        )
        if convert_result.returncode != 0:
            detail = convert_result.stderr.strip() or convert_result.stdout.strip()
            raise RuntimeError(f"Could not read minted DAPPER document for {filename}: {detail}")

        return json.loads(convert_result.stdout)


def mint_documents(documents: list[tuple[str, dict]], dapper_identity: Path) -> list[tuple[str, dict]]:
    if not dapper_identity.exists():
        raise FileNotFoundError(f"DAPPER identity script does not exist: {dapper_identity}")

    return [
        (filename, mint_document(filename, document, dapper_identity))
        for filename, document in documents
    ]


def derive_document_name(document: dict, source_file: Path) -> str:
    terminal_datasets = []
    used_objects = {edge.get("object") for edge in document.get("used_edges", [])}
    derived_objects = {edge.get("object") for edge in document.get("was_derived_from_edges", [])}
    upstream_ids = used_objects | derived_objects

    for dataset in document.get("datasets", []):
        if dataset.get("id") not in upstream_ids:
            terminal_datasets.append(dataset)

    if terminal_datasets:
        return str(terminal_datasets[0].get("name") or terminal_datasets[0].get("description"))

    drs_objects = document.get("drs_objects", [])
    if drs_objects:
        return str(drs_objects[0].get("name") or drs_objects[0].get("drs_id"))

    return f"Bottom-line provenance for {source_file.stem}"


def lint_document(filename: str, document: dict, dapper_linter: Path) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="bottom_line_dapper_lint_") as temp_dir:
        temp_file = Path(temp_dir) / filename
        temp_file.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        command = ["uv", "run", str(dapper_linter), str(temp_file)]
        result = subprocess.run(command, capture_output=True, text=True, check=False, env=uv_environment())
        if result.returncode == 0:
            return []

        messages = []
        if result.stdout.strip():
            messages.append(result.stdout.strip())
        if result.stderr.strip():
            messages.append(result.stderr.strip())
        return messages or [f"DAPPER linter failed with exit code {result.returncode}"]


def lint_documents(documents: list[tuple[str, dict]], dapper_linter: Path) -> None:
    if not dapper_linter.exists():
        raise FileNotFoundError(f"DAPPER linter does not exist: {dapper_linter}")

    failures = []
    for filename, document in documents:
        messages = lint_document(filename, document, dapper_linter)
        if messages:
            failures.append((filename, messages))

    if failures:
        rendered = ["DAPPER linter found errors in generated provenance documents:"]
        for filename, messages in failures[:10]:
            rendered.append(f"\n{filename}")
            rendered.extend(messages)
        if len(failures) > 10:
            rendered.append(f"\n... {len(failures) - 10} additional file(s) failed linting")
        raise RuntimeError("\n".join(rendered))


def load_documents_to_database(documents: list[tuple[str, dict]], database_file: Path, log_file: Path) -> int:
    if not database_file.exists():
        raise FileNotFoundError(f"Database file does not exist: {database_file}")

    configure_logging(log_file)
    rows: list[tuple[str, str, str, str]] = []

    for filename, document in documents:
        source_file = Path(filename)
        artifact_id = source_file.stem
        provenance = json.dumps(document, separators=(",", ":"))
        name = derive_document_name(document, source_file)
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
    dapper_linter = Path(args.dapper_linter).expanduser().resolve()
    dapper_identity = Path(args.dapper_identity).expanduser().resolve()

    graph, missing_messages = build_graph(listing_files(s3_listing_dir))

    graph_reference = DEFAULT_GRAPH_REFERENCE
    if out_graph_file is not None:
        out_graph_file.parent.mkdir(parents=True, exist_ok=True)
        out_graph_file.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
        graph_reference = str(out_graph_file)

    documents = build_documents_from_graph(graph, graph_reference, args.max_endpoints)
    documents = mint_documents(documents, dapper_identity)
    print(f"Minted DAPPER-ID-1 identifiers for {len(documents)} provenance documents with {dapper_identity}")

    if not args.skip_dapper_lint:
        lint_documents(documents, dapper_linter)
        print(f"Validated {len(documents)} DAPPER provenance documents with {dapper_linter}")

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
