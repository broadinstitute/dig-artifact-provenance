#!/usr/bin/env python3
"""Create bottom-line DAPPER provenance files in one step.

Run from the repository root:

    python3 src/python/onestep/create_bottom_line_dapper_provenance.py

Required arguments:

- none

Optional arguments and defaults:

- ``--s3-listing-dir``: ``data/s3``
- ``--out-dir``: ``data/bottom-line-provenance``
- ``--out-graph-file``: not set by default; when provided, writes the
  intermediate computed-id graph JSON to that path.

This combines:

- ``src/python/create_bottom_line_graph.py``
- ``src/python/create_bottom_line_dapper.py``

The generated graph uses DAPPER 0.1.0 computed identifiers from:

- https://github.com/broadinstitute/dapper/releases/tag/0.1.0
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))

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
    OUTPUT_PATH,
    S3_DIR,
    build_graph,
    listing_files,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "bottom-line-provenance"
DEFAULT_GRAPH_REFERENCE = "<in-memory bottom-line DAPPER graph>"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the bottom-line provenance graph and export DAPPER provenance JSON files."
    )
    parser.add_argument(
        "--s3-listing-dir",
        default=str(S3_DIR),
        help=f"Directory containing S3 listing snapshot text files. Default: {S3_DIR}",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output directory for DAPPER provenance JSON files. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--out-graph-file",
        default=None,
        help=(
            "Optional path for writing the intermediate computed-id graph JSON. "
            f"Default: not written. Common value: {OUTPUT_PATH}"
        ),
    )
    return parser.parse_args()


def export_documents_from_graph(graph: dict, out_dir: Path, graph_reference: str) -> int:
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

    out_dir.mkdir(parents=True, exist_ok=True)

    count = 0
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

        out_file = out_dir / filename_from_location_path(str(root_node.get("location_path", root_node["id"])))
        out_file.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        count += 1

    return count


def main() -> int:
    args = parse_args()
    s3_listing_dir = Path(args.s3_listing_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_graph_file = Path(args.out_graph_file).expanduser().resolve() if args.out_graph_file else None

    graph, missing_messages = build_graph(listing_files(s3_listing_dir))

    graph_reference = DEFAULT_GRAPH_REFERENCE
    if out_graph_file is not None:
        out_graph_file.parent.mkdir(parents=True, exist_ok=True)
        out_graph_file.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
        graph_reference = str(out_graph_file)

    count = export_documents_from_graph(graph, out_dir, graph_reference)
    print(f"Built graph with {len(graph['nodes'])} nodes and {len(graph['edges'])} edges")
    print(f"Wrote {count} DAPPER provenance files to {out_dir}")

    if missing_messages:
        print("Missing S3 listing snapshots:")
        for message in missing_messages:
            print(f"- {message}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
