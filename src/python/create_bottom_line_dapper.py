#!/usr/bin/env python3
"""Export DAPPER-style provenance JSON files for bottom-line open-data outputs."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Set


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO_ROOT / "data" / "graph" / "provenance_graph.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "bottom-line-provenance"
RECOMMENDATION_REF = "notes/gptRecommendations/bottom-line-dapper.md"

BACKWARD_RELATIONSHIPS = {"WasGeneratedBy", "WasDerivedFrom", "Used"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create bottom-line DAPPER provenance JSON files.")
    parser.add_argument(
        "--in-graph-file",
        required=True,
        help="Path to the bottom-line provenance graph JSON file.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output directory for provenance JSON files. Default: {DEFAULT_OUTPUT_DIR}",
    )
    return parser.parse_args()


def filename_from_location_path(location_path: str) -> str:
    normalized = location_path
    if normalized.startswith("s3://"):
        normalized = normalized[len("s3://") :]
    normalized = normalized.strip("/")
    return normalized.replace("/", "_") + ".json"


def collect_provenance_subgraph(
    root_node_id: str,
    outgoing_edges: Dict[str, List[dict]],
) -> tuple[Set[str], Set[str]]:
    node_ids: Set[str] = set()
    edge_ids: Set[str] = set()
    stack = [root_node_id]

    while stack:
        node_id = stack.pop()
        if node_id in node_ids:
            continue
        node_ids.add(node_id)

        for edge in outgoing_edges.get(node_id, []):
            if edge.get("relationship") not in BACKWARD_RELATIONSHIPS:
                continue
            edge_ids.add(edge["id"])
            target = edge["target"]
            if target not in node_ids:
                stack.append(target)

    return node_ids, edge_ids


def build_outgoing_edges(edges: Iterable[dict]) -> Dict[str, List[dict]]:
    mapping: Dict[str, List[dict]] = defaultdict(list)
    for edge in edges:
        mapping[edge["source"]].append(edge)
    return mapping


def edge_target_map(edges: Iterable[dict], relationship: str) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = defaultdict(list)
    for edge in edges:
        if edge.get("relationship") == relationship:
            mapping[edge["source"]].append(edge["target"])
    return mapping


def infer_access_level(node: dict) -> str:
    if node.get("directory_kind") == "open_data_endpoint":
        return "public"
    return "controlled"


def basename_from_location(location_path: str) -> str:
    return location_path.rstrip("/").split("/")[-1]


def make_dataset_entry(node: dict, generated_by_map: Dict[str, List[str]]) -> dict:
    entry = {
        "id": node["id"],
        "name": node.get("label"),
        "resource_type": "Dataset",
        "description": node.get("label"),
        "access_level": infer_access_level(node),
        "location_path": node.get("location_path"),
        "phenotype": node.get("phenotype"),
        "ancestry": node.get("ancestry"),
        "annotation_source": node.get("annotation_source"),
    }
    generated_by = generated_by_map.get(node["id"], [])
    if generated_by:
        entry["was_generated_by"] = generated_by[0]
    return entry


def make_drs_entry(node: dict, generated_by_map: Dict[str, List[str]]) -> dict:
    location = str(node.get("location_path") or "")
    drs_id = location.removeprefix("s3://").replace("/", "_").rstrip("_")
    entry = {
        "id": node["id"],
        "drs_id": drs_id,
        "self_uri": f"drs://{drs_id}",
        "mime_type": "text/tab-separated-values" if location.endswith(".tsv.gz") else "application/octet-stream",
        "access_methods": ["s3"],
        "location_path": location,
        "description": node.get("label"),
        "published_filename": node.get("published_filename") or basename_from_location(location),
        "annotation_source": node.get("annotation_source"),
    }
    generated_by = generated_by_map.get(node["id"], [])
    if generated_by:
        entry["was_generated_by"] = generated_by[0]
    return entry


def make_activity_entry(node: dict) -> dict:
    return {
        "id": node["id"],
        "name": node.get("label"),
        "activity_type": node.get("stage_name"),
        "description": node.get("label"),
        "repo_url": node.get("location_path"),
        "stage_group": node.get("stage_group"),
        "phenotype": node.get("phenotype"),
        "ancestry": node.get("ancestry"),
        "dataset": node.get("dataset"),
        "method": node.get("method"),
        "annotation_source": node.get("annotation_source"),
    }


def make_c2m2_file_entry(node: dict, generated_by_map: Dict[str, List[str]]) -> dict:
    location = str(node.get("location_path") or "")
    entry = {
        "id": node["id"],
        "name": node.get("label"),
        "description": node.get("label"),
        "filename": basename_from_location(location),
        "local_id": location,
        "location_path": location,
        "directory_kind": node.get("directory_kind"),
        "phenotype": node.get("phenotype"),
        "ancestry": node.get("ancestry"),
        "dataset": node.get("dataset"),
        "method": node.get("method"),
        "rare": node.get("rare"),
        "annotation_source": node.get("annotation_source"),
    }
    generated_by = generated_by_map.get(node["id"], [])
    if generated_by:
        entry["was_generated_by"] = generated_by[0]
    return entry


def make_edge_entry(edge: dict) -> dict:
    entry = {
        "id": edge["id"],
        "source": edge["source"],
        "target": edge["target"],
        "relationship": edge.get("relationship"),
        "predicate": edge.get("predicate"),
        "annotation_source": edge.get("annotation_source"),
    }
    if edge.get("edge_role"):
        entry["edge_role"] = edge["edge_role"]
    if edge.get("description"):
        entry["description"] = edge["description"]
    return entry


def export_documents(graph_path: Path, out_dir: Path) -> int:
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in graph["nodes"]}
    edges = {edge["id"]: edge for edge in graph["edges"]}
    outgoing = build_outgoing_edges(edges.values())
    generated_by_map = edge_target_map(edges.values(), "WasGeneratedBy")

    open_data_nodes = [
        node for node in nodes.values()
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
            "reference_graph_file": str(graph_path),
            "recommendation_reference": RECOMMENDATION_REF,
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
    graph_path = Path(args.in_graph_file).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    count = export_documents(graph_path, out_dir)
    print(f"Wrote {count} DAPPER provenance files to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
