#!/usr/bin/env python3
"""Create bottom-line DAPPER provenance and load it into SQLite in one step.

Run from the repository root:

    python3 src/python/onestep/create_bottom_line_dapper_provenance.py

Required arguments:

- none

Example:

  python3 src/python/onestep/create_bottom_line_dapper_provenance.py \
    --in-database /tmp/onestep_provenance_db.sqlite \
    --in-log-file /tmp/onestep_bottom_line.log \
    --save-provenance-files \
    --out-dir /tmp/onestep_saved_provenance \
    --out-graph-file /tmp/onestep_graph.json


Optional arguments and defaults:

- ``--s3-listing-dir``: ``data/bottom-line/s3``
- ``--in-database``: ``data/database/provenance_db.sqlite``
- ``--in-log-file``: ``logs/bottom-line-provenance.log``
- ``--save-provenance-files``: disabled by default
- ``--generate_yaml``: save and lint YAML provenance files instead of JSON;
  also enables saving provenance files without ``--save-provenance-files``
- ``--out-dir``: ``data/bottom-line-provenance`` when output files are saved
- ``--out-graph-file``: not set by default; when provided, writes the
  intermediate computed-id graph JSON to that path.
- ``--max-endpoints``: not set by default; when provided, only builds the first
  N sorted open-data endpoint documents. Useful for one-endpoint lint tests.
- ``--trait-registry``: ``data/traits/kpn_trait_registry_v002.tsv``
- ``--skip-dapper-lint`` skips the linter provenance check

This combines:

- ``src/python/individualsteps/create_bottom_line_graph.py``
- ``src/python/individualsteps/create_bottom_line_dapper.py``
- ``src/python/individualsteps/load_bottom_line_to_db.py``

The intermediate graph uses legacy DAPPER 0.1.0 identifiers from:

- https://github.com/broadinstitute/dapper/releases/tag/0.1.0

Each generated document uses the DAPPER 0.2.0 group-keyed document shape and
re-mints node identifiers with the local 0.2.0 identity module:

    uv run /Users/mduby/Code/DccWorkspace/DapperSchema/schema/lint/lint_provenance.py <provenance_file_input>

S3 prefixes are represented as Dataset locations. The published object is a
File linked to the terminal Dataset; no C2M2 or DRS registration is asserted.
The emitted documents omit application-only envelope fields because the DAPPER
linter runs in closed mode and treats unknown top-level keys as errors.

Before linting, the script mints DAPPER-ID-1 computed node identifiers by
batch-importing the DAPPER identity module in one ``uv run`` process:

    /Users/mduby/Code/DccWorkspace/DapperSchema/schema/identity/dapper_identity.py
"""

from __future__ import annotations

import argparse
import csv
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
DEFAULT_S3_LISTING_DIR = REPO_ROOT / "data" / "bottom-line" / "s3"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "bottom-line-provenance"
DEFAULT_GRAPH_OUTPUT = REPO_ROOT / "data" / "graph" / "provenance_graph.json"
DEFAULT_DATABASE = REPO_ROOT / "data" / "database" / "provenance_db.sqlite"
DEFAULT_LOG_FILE = REPO_ROOT / "logs" / "bottom-line-provenance.log"
DEFAULT_GRAPH_REFERENCE = "<in-memory bottom-line DAPPER graph>"
DEFAULT_DAPPER_LINTER = Path("/Users/mduby/Code/DccWorkspace/DapperSchema/schema/lint/lint_provenance.py")
DEFAULT_DAPPER_IDENTITY = Path("/Users/mduby/Code/DccWorkspace/DapperSchema/schema/identity/dapper_identity.py")
DEFAULT_TRAIT_REGISTRY = REPO_ROOT / "data" / "traits" / "kpn_trait_registry_v002.tsv"
METHODS_REPO_URL = "https://github.com/broadinstitute/dig-aggregator-methods"


def compact_dict(data: dict) -> dict:
    return {key: value for key, value in data.items() if value is not None and value != []}


def original_node_id(node: dict) -> str:
    """Use non-DAPPER graph ids so the linter does not verify stale digests."""
    return str(node.get("original_id") or node.get("id"))


def final_dataset_id(root_node: dict) -> str:
    location = str(root_node.get("location_path") or root_node.get("id"))
    artifact = filename_from_location_path(location).removesuffix(".json")
    return f"bottom-line-result:{artifact}"


def load_trait_ids(registry_file: Path) -> dict[str, str]:
    with registry_file.open(encoding="utf-8", newline="") as stream:
        rows = csv.DictReader(stream, delimiter="\t")
        return {
            row["legacy_phenotype_id"]: row["portal_id"]
            for row in rows
            if row.get("legacy_phenotype_id") and row.get("portal_id", "").startswith("KPN.TRAIT:")
        }


def source_description(node: dict) -> str:
    parts = [str(node.get("label") or "")]
    if node.get("inferred_from_code"):
        parts.append(f"Source reconstruction: inferred from {node['inferred_from_code']}; execution is not confirmed.")
    elif node.get("observed_from_listing"):
        parts.append("Reported by the S3 listing snapshot; execution and object contents were not independently verified.")
    context = [
        f"{key}={node[key]}"
        for key in ("phenotype", "ancestry", "dataset", "method", "rare")
        if node.get(key) is not None
    ]
    if context:
        parts.append("Source context: " + "; ".join(context) + ".")
    return " ".join(parts)


def scientific_context(node: dict, trait_ids: dict[str, str]) -> dict:
    phenotype = node.get("phenotype")
    return compact_dict({
        "trait": trait_ids.get(str(phenotype)) if phenotype else None,
        "ancestry": node.get("ancestry"),
    })


def dapper_dataset_entry(node: dict, node_id: str, trait_ids: dict[str, str]) -> dict:
    return compact_dict(
        {
            "id": node_id,
            "name": node.get("label"),
            "resource_type": "dataset",
            "description": source_description(node),
            "location": node.get("location_path"),
            **scientific_context(node, trait_ids),
        }
    )


def dapper_file_entry(node: dict, node_id: str) -> dict:
    location = str(node.get("location_path") or "")
    return compact_dict(
        {
            "id": node_id,
            "name": node.get("label"),
            "description": source_description(node),
            "filename": node.get("published_filename") or filename_from_location_path(location).removesuffix(".json"),
            "location": location,
        }
    )


def dapper_activity_entry(node: dict, node_id: str, trait_ids: dict[str, str]) -> dict:
    script_url = node.get("location_path")
    return compact_dict(
        {
            "id": node_id,
            "name": node.get("label"),
            "description": source_description(node),
            "activity_type": node.get("stage_name"),
            "script_url": script_url,
            "repo_url": METHODS_REPO_URL if str(script_url).startswith(METHODS_REPO_URL + "/") else None,
            **scientific_context(node, trait_ids),
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
            "Output directory for saved DAPPER provenance files. "
            f"Default: {DEFAULT_OUTPUT_DIR}"
        ),
    )
    parser.add_argument(
        "--save-provenance-files",
        action="store_true",
        help="Write individual provenance files (JSON by default). Default: disabled.",
    )
    parser.add_argument(
        "--generate_yaml",
        "--generate-yaml",
        action="store_true",
        help="Write and lint YAML provenance files instead of JSON; implies --save-provenance-files.",
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
        "--trait-registry",
        default=str(DEFAULT_TRAIT_REGISTRY),
        help=f"Legacy phenotype to KPN trait mapping. Default: {DEFAULT_TRAIT_REGISTRY}",
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


def make_terminal_dataset(root_node: dict, trait_ids: dict[str, str]) -> dict:
    location = str(root_node.get("location_path") or "")
    phenotype = root_node.get("phenotype")
    ancestry = root_node.get("ancestry")
    name_bits = [bit for bit in (phenotype, ancestry) if bit]
    label = " / ".join(str(bit) for bit in name_bits) or filename_from_location_path(location).removesuffix(".json")
    return {
        "id": final_dataset_id(root_node),
        "name": f"Published open-data bottom-line result for {label}",
        "resource_type": "dataset",
        "description": f"Logical bottom-line dataset distributed as {filename_from_location_path(location).removesuffix('.json')}. "
        "The publication lineage is reconstructed from S3 listing snapshots; execution and object contents were not independently verified.",
        **scientific_context(root_node, trait_ids),
    }


def build_dapper_document(
    root_node: dict,
    subgraph_nodes: list[dict],
    subgraph_edges: list[dict],
    generated_by_map: dict[str, list[str]],
    trait_ids: dict[str, str],
) -> dict:
    id_map = {node["id"]: original_node_id(node) for node in subgraph_nodes}
    root_file_id = id_map[root_node["id"]]
    open_stage_ids = generated_by_map.get(root_node["id"], [])
    open_stage_id = id_map.get(open_stage_ids[0]) if open_stage_ids else None
    terminal_dataset_id = final_dataset_id(root_node)

    document: dict[str, list[dict]] = {
        "activities": [],
        "files": [],
        "datasets": [],
        "used_edges": [],
        "was_generated_by_edges": [],
        "was_derived_from_edges": [],
        "has_file_edges": [],
    }

    for node in subgraph_nodes:
        dapper_class = node.get("dapper_class")
        node_id = id_map[node["id"]]
        if dapper_class == "Dataset":
            document["datasets"].append(dapper_dataset_entry(node, node_id, trait_ids))
        elif dapper_class == "DrsObject":
            document["files"].append(dapper_file_entry(node, node_id))
        elif dapper_class == "Activity":
            document["activities"].append(dapper_activity_entry(node, node_id, trait_ids))
        elif dapper_class == "C2M2File":
            document["datasets"].append(dapper_dataset_entry(node, node_id, trait_ids))

    document["datasets"].append(make_terminal_dataset(root_node, trait_ids))
    if open_stage_id:
        document["was_generated_by_edges"].append(
            {
                "subject": terminal_dataset_id,
                "predicate": "prov:wasGeneratedBy",
                "object": open_stage_id,
            }
        )
    document["has_file_edges"].append(
        {
            "subject": terminal_dataset_id,
            "predicate": "dapper:hasFile",
            "object": root_file_id,
        }
    )

    for edge in subgraph_edges:
        group = relationship_group(edge.get("relationship"))
        if group is None:
            continue
        document[group].append(dapper_edge_entry(make_edge_entry(edge), id_map))

    return {key: value for key, value in document.items() if value}


def build_documents_from_graph(
    graph: dict,
    graph_reference: str,
    max_endpoints: int | None = None,
    trait_ids: dict[str, str] | None = None,
) -> list[tuple[str, dict]]:
    trait_ids = trait_ids or {}
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
        document = build_dapper_document(root_node, subgraph_nodes, subgraph_edges, generated_by_map, trait_ids)

        filename = filename_from_location_path(str(root_node.get("location_path", root_node["id"])))
        documents.append((filename, document))

    return documents


def output_filename(filename: str, generate_yaml: bool) -> str:
    return str(Path(filename).with_suffix(".yaml")) if generate_yaml else filename


def yaml_module():
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise RuntimeError("--generate_yaml requires PyYAML") from exc
    return yaml


def serialize_document(document: dict, generate_yaml: bool) -> str:
    if generate_yaml:
        return yaml_module().safe_dump(document, sort_keys=False, allow_unicode=True)
    return json.dumps(document, indent=2) + "\n"


def save_documents(
    documents: list[tuple[str, dict]], out_dir: Path, generate_yaml: bool = False
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, document in documents:
        out_file = out_dir / output_filename(filename, generate_yaml)
        out_file.write_text(serialize_document(document, generate_yaml), encoding="utf-8")
    return len(documents)


def uv_environment() -> dict[str, str]:
    env = dict(os.environ)
    env["UV_CACHE_DIR"] = "/tmp/artifact_provenance_uv_cache"
    return env


def mint_documents(documents: list[tuple[str, dict]], dapper_identity: Path) -> list[tuple[str, dict]]:
    if not dapper_identity.exists():
        raise FileNotFoundError(f"DAPPER identity script does not exist: {dapper_identity}")

    batch_code = r"""#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml", "rdflib", "linkml-runtime", "ruamel.yaml"]
# ///
import json
import sys
from pathlib import Path

identity_path = Path(sys.argv[1]).resolve()
input_dir = Path(sys.argv[2]).resolve()
output_dir = Path(sys.argv[3]).resolve()
index_file = Path(sys.argv[4]).resolve()

sys.path.insert(0, str(identity_path.parent))
from dapper_identity import assign_ids, load_schema  # noqa: E402

schema = load_schema(identity_path.parent.parent / "dapper.yaml")
filenames = json.loads(index_file.read_text(encoding="utf-8"))
output_dir.mkdir(parents=True, exist_ok=True)

for filename in filenames:
    input_file = input_dir / filename
    output_file = output_dir / filename
    document = json.loads(input_file.read_text(encoding="utf-8"))
    assign_ids(document, schema)
    output_file.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")
"""

    with tempfile.TemporaryDirectory(prefix="bottom_line_dapper_mint_") as temp_dir:
        temp_path = Path(temp_dir)
        input_dir = temp_path / "input"
        output_dir = temp_path / "output"
        input_dir.mkdir()
        filenames = []
        for filename, document in documents:
            filenames.append(filename)
            (input_dir / filename).write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")

        index_file = temp_path / "index.json"
        index_file.write_text(json.dumps(filenames), encoding="utf-8")
        batch_script = temp_path / "batch_mint_dapper.py"
        batch_script.write_text(batch_code, encoding="utf-8")

        result = subprocess.run(
            [
                "uv",
                "run",
                "--managed-python",
                "--python",
                "3.11",
                "--script",
                str(batch_script),
                str(dapper_identity),
                str(input_dir),
                str(output_dir),
                str(index_file),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=uv_environment(),
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Batch DAPPER identity assignment failed: {detail}")

        return [
            (filename, json.loads((output_dir / filename).read_text(encoding="utf-8")))
            for filename in filenames
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


def derive_trait_ancestry_from_filename(source_file: Path) -> tuple[str, str]:
    prefix = "dig-open-bottom-line-analysis-stg_bottom-line_"
    suffix = ".sumstats.tsv.gz.json"
    filename = source_file.name

    if not filename.startswith(prefix) or not filename.endswith(suffix):
        raise ValueError(f"Cannot derive trait and ancestry from provenance filename: {filename}")

    remainder = filename[len(prefix) : -len(suffix)]
    ancestry_id, separator, trait_legacy_id = remainder.partition("_")
    if not separator or not ancestry_id or not trait_legacy_id:
        raise ValueError(f"Cannot derive trait and ancestry from provenance filename: {filename}")

    return trait_legacy_id, ancestry_id


def table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})")}


def validate_database_schema(connection: sqlite3.Connection) -> None:
    artifact_columns = table_columns(connection, "prov_artifact")
    required_artifact_columns = {
        "id",
        "pipeline_type",
        "provenance",
        "name",
        "trait_legacy_id",
        "ancestry_id",
        "description",
    }
    missing_artifact_columns = required_artifact_columns - artifact_columns
    if missing_artifact_columns:
        raise RuntimeError(
            "prov_artifact is missing required column(s): "
            + ", ".join(sorted(missing_artifact_columns))
        )

    trait_columns = table_columns(connection, "prov_trait")
    required_trait_columns = {"legacy_id", "kpn_id", "name", "description"}
    missing_trait_columns = required_trait_columns - trait_columns
    if missing_trait_columns:
        raise RuntimeError(
            "prov_trait is missing required column(s): "
            + ", ".join(sorted(missing_trait_columns))
        )


def validate_ancestry_ids(connection: sqlite3.Connection, ancestry_ids: set[str]) -> None:
    ancestry_columns = table_columns(connection, "prov_ancestry")
    if "ancestry_id" not in ancestry_columns:
        raise RuntimeError("prov_ancestry is missing required column: ancestry_id")

    existing_ids = {
        row[0]
        for row in connection.execute("SELECT ancestry_id FROM prov_ancestry")
    }
    missing_ids = ancestry_ids - existing_ids
    if missing_ids:
        raise RuntimeError(
            "prov_ancestry is missing required ancestry_id value(s): "
            + ", ".join(sorted(missing_ids))
        )


def lint_document(
    filename: str, document: dict, dapper_linter: Path, generate_yaml: bool = False
) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="bottom_line_dapper_lint_") as temp_dir:
        temp_file = Path(temp_dir) / output_filename(filename, generate_yaml)
        temp_file.write_text(serialize_document(document, generate_yaml), encoding="utf-8")
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


def lint_documents(
    documents: list[tuple[str, dict]], dapper_linter: Path, generate_yaml: bool = False
) -> None:
    if not dapper_linter.exists():
        raise FileNotFoundError(f"DAPPER linter does not exist: {dapper_linter}")

    failures = []
    for filename, document in documents:
        messages = lint_document(filename, document, dapper_linter, generate_yaml)
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
    rows: list[tuple[str, str, str, str, str, str]] = []
    traits: dict[str, tuple[str, str, str]] = {}
    ancestry_ids: set[str] = set()

    for filename, document in documents:
        source_file = Path(filename)
        artifact_id = source_file.stem
        trait_legacy_id, ancestry_id = derive_trait_ancestry_from_filename(source_file)
        provenance = json.dumps(document, separators=(",", ":"))
        name = derive_document_name(document, source_file)
        rows.append((artifact_id, PIPELINE_TYPE, provenance, name, trait_legacy_id, ancestry_id))
        traits[trait_legacy_id] = (trait_legacy_id, trait_legacy_id, trait_legacy_id)
        ancestry_ids.add(ancestry_id)
        logging.info("Prepared database record for provenance file %s", filename)

    with sqlite3.connect(database_file) as connection:
        validate_database_schema(connection)
        validate_ancestry_ids(connection, ancestry_ids)
        connection.execute("DELETE FROM prov_artifact WHERE pipeline_type = ?", (PIPELINE_TYPE,))
        connection.executemany(
            """
            INSERT INTO prov_trait (legacy_id, kpn_id, name, description)
            VALUES (?, ?, ?, NULL)
            ON CONFLICT(legacy_id) DO NOTHING
            """,
            sorted(traits.values()),
        )
        connection.executemany(
            """
            INSERT INTO prov_artifact
                (id, pipeline_type, provenance, name, trait_legacy_id, ancestry_id, description)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            rows,
        )
        connection.commit()

    logging.info("Files read: %s", len(documents))
    logging.info("Database records created: %s", len(rows))
    return len(rows)


def main() -> int:
    args = parse_args()
    if args.generate_yaml:
        yaml_module()
    s3_listing_dir = Path(args.s3_listing_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_graph_file = Path(args.out_graph_file).expanduser().resolve() if args.out_graph_file else None
    database_file = Path(args.in_database).expanduser().resolve()
    log_file = Path(args.in_log_file).expanduser().resolve()
    dapper_linter = Path(args.dapper_linter).expanduser().resolve()
    dapper_identity = Path(args.dapper_identity).expanduser().resolve()
    trait_registry = Path(args.trait_registry).expanduser().resolve()

    graph, missing_messages = build_graph(listing_files(s3_listing_dir))

    graph_reference = DEFAULT_GRAPH_REFERENCE
    if out_graph_file is not None:
        out_graph_file.parent.mkdir(parents=True, exist_ok=True)
        out_graph_file.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
        graph_reference = str(out_graph_file)

    documents = build_documents_from_graph(
        graph, graph_reference, args.max_endpoints, load_trait_ids(trait_registry)
    )
    documents = mint_documents(documents, dapper_identity)
    print(f"Minted DAPPER-ID-1 identifiers for {len(documents)} provenance documents with {dapper_identity}")

    if not args.skip_dapper_lint:
        lint_documents(documents, dapper_linter, args.generate_yaml)
        print(f"Validated {len(documents)} DAPPER provenance documents with {dapper_linter}")

    loaded_count = load_documents_to_database(documents, database_file, log_file)
    print(f"Built graph with {len(graph['nodes'])} nodes and {len(graph['edges'])} edges")
    print(f"Loaded {loaded_count} bottom-line provenance documents into {database_file}")
    print(f"Log written to {log_file}")

    if args.save_provenance_files or args.generate_yaml:
        saved_count = save_documents(documents, out_dir, args.generate_yaml)
        output_format = "YAML" if args.generate_yaml else "JSON"
        print(f"Wrote {saved_count} DAPPER {output_format} provenance files to {out_dir}")

    if missing_messages:
        print("Missing S3 listing snapshots:")
        for message in missing_messages:
            print(f"- {message}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
