#!/usr/bin/env python3
"""Create a provenance dependency graph for the intake and bottom-line pipelines.

The graph is exported to ``data/graph/provenance_graph.json`` and contains:

- ``nodes``: stage and S3 directory/file artifact nodes
- ``edges``: provenance-style dependency links

The implementation uses:

- the current S3 listing snapshots under ``data/s3``
- bottom-line/intake path conventions validated against the public
  ``broadinstitute/dig-aggregator-methods`` repository
- DAPPER-oriented node and edge annotations from the local recommendations
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Optional, Set, Tuple


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
S3_DIR = DATA_DIR / "s3"
OUTPUT_PATH = DATA_DIR / "graph" / "provenance_graph.json"
GITHUB_REPO_ROOT = "https://github.com/broadinstitute/dig-aggregator-methods/blob/master"

LISTING_FILES = {
    "variants_raw": S3_DIR / "dig-anal-variants_raw.txt",
    "variants_processed": S3_DIR / "dig-anal-variants_processed.txt",
    "variants": S3_DIR / "dig-anal-variants.txt",
    "partitioned_variants": S3_DIR / "dig-anal-out-meta-variants.txt",
    "bottom_line": S3_DIR / "dig-anal-out-meta-bottom-line.txt",
    "min_p": S3_DIR / "dig-anal-out-meta-minp.txt",
    "largest": S3_DIR / "dig-anal-out-meta-largest.txt",
    "open_data": S3_DIR / "dig-open-bottom-line-analysis-stg.txt",
}

REQUIRED_LISTINGS = {
    "variants_raw": "s3://dig-analysis-data/variants_raw/",
    "variants_processed": "s3://dig-analysis-data/variants_processed/",
    "variants": "s3://dig-analysis-data/variants/",
    "partitioned_variants": "s3://dig-analysis-data/out/metaanalysis/variants/",
    "bottom_line": "s3://dig-analysis-data/out/metaanalysis/bottom-line/",
    "min_p": "s3://dig-analysis-data/out/metaanalysis/min_p/",
    "largest": "s3://dig-analysis-data/out/metaanalysis/largest/",
    "open_data": "s3://dig-open-bottom-line-analysis-stg/bottom-line/",
}

DAPPER_NOTE = "notes/gptRecommendations/bottom-line-dapper.md"

VARIANTS_RE = re.compile(
    r"^s3://dig-analysis-data/variants/"
    r"(?P<method>[^/]+)/(?P<dataset>[^/]+)/(?P<phenotype>[^/]+)/$"
)
VARIANTS_RAW_RE = re.compile(
    r"^s3://dig-analysis-data/variants_raw/"
    r"(?P<method>[^/]+)/(?P<dataset>[^/]+)/(?P<phenotype>[^/]+)/$"
)
VARIANTS_PROCESSED_RE = re.compile(
    r"^s3://dig-analysis-data/variants_processed/"
    r"(?P<method>[^/]+)/(?P<dataset>[^/]+)/(?P<phenotype>[^/]+)/$"
)
PARTITIONED_RE = re.compile(
    r"^s3://dig-analysis-data/out/metaanalysis/variants/"
    r"(?P<phenotype>[^/]+)/dataset=(?P<dataset>[^/]+)/"
    r"ancestry=(?P<ancestry>[^/]+)/rare=(?P<rare>true|false)/$"
)
ANCESTRY_RESULT_RE = re.compile(
    r"^s3://dig-analysis-data/out/metaanalysis/"
    r"(?P<family>bottom-line|min_p|largest)/ancestry-specific/"
    r"(?P<phenotype>[^/]+)/ancestry=(?P<ancestry>[^/]+)/$"
)
TRANS_RESULT_RE = re.compile(
    r"^s3://dig-analysis-data/out/metaanalysis/"
    r"(?P<family>bottom-line|min_p|largest)/trans-ethnic/(?P<phenotype>[^/]+)/$"
)
OPEN_DATA_RE = re.compile(
    r"^s3://dig-open-bottom-line-analysis-stg/bottom-line/"
    r"(?P<ancestry>[^/]+)/(?P<phenotype>[^/.]+)\.sumstats\.tsv\.gz$"
)


@dataclass(frozen=True)
class VariantInput:
    method: str
    dataset: str
    phenotype: str
    uri: str


@dataclass(frozen=True)
class VariantPartition:
    phenotype: str
    dataset: str
    ancestry: str
    rare: str
    uri: str


@dataclass(frozen=True)
class ResultDir:
    family: str
    phenotype: str
    ancestry: Optional[str]
    uri: str


@dataclass(frozen=True)
class OpenDataOutput:
    ancestry: str
    phenotype: str
    uri: str


class GraphBuilder:
    def __init__(self) -> None:
        self.nodes: Dict[str, dict] = {}
        self.edges: Dict[str, dict] = {}

    def add_node(self, node_id: str, **attrs: object) -> str:
        payload = {"id": node_id, **attrs}
        existing = self.nodes.get(node_id)
        if existing is None:
            self.nodes[node_id] = payload
            return node_id
        merged = dict(existing)
        for key, value in payload.items():
            if key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = value
        self.nodes[node_id] = merged
        return node_id

    def add_edge(self, edge_id: str, **attrs: object) -> None:
        if edge_id not in self.edges:
            self.edges[edge_id] = {"id": edge_id, **attrs}

    def render(self) -> dict:
        return {
            "nodes": sorted(self.nodes.values(), key=lambda item: str(item["id"])),
            "edges": sorted(self.edges.values(), key=lambda item: str(item["id"])),
        }


def safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", value)


def read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stage_id(stage_name: str, *parts: str) -> str:
    suffix = "__".join(safe_token(part) for part in parts if part)
    return f"stage:{stage_name}" if not suffix else f"stage:{stage_name}__{suffix}"


def node_id(kind: str, *parts: str) -> str:
    suffix = "__".join(safe_token(part) for part in parts if part)
    return f"node:{kind}" if not suffix else f"node:{kind}__{suffix}"


def add_stage(
    graph: GraphBuilder,
    stage_name: str,
    label: str,
    *,
    stage_group: str,
    method: Optional[str] = None,
    dataset: Optional[str] = None,
    phenotype: Optional[str] = None,
    ancestry: Optional[str] = None,
    observed: bool = False,
) -> str:
    location_path = stage_location_path(stage_name)
    return graph.add_node(
        stage_id(stage_name, stage_group, method or "", dataset or "", phenotype or "", ancestry or ""),
        node_type="stage",
        dapper_class="Activity",
        label=label,
        location_path=location_path,
        stage_name=stage_name,
        stage_group=stage_group,
        method=method,
        dataset=dataset,
        phenotype=phenotype,
        ancestry=ancestry,
        observed_from_listing=observed,
        annotation_source=DAPPER_NOTE,
    )


def add_directory(
    graph: GraphBuilder,
    kind: str,
    label: str,
    uri: str,
    *,
    dapper_class: str,
    method: Optional[str] = None,
    dataset: Optional[str] = None,
    phenotype: Optional[str] = None,
    ancestry: Optional[str] = None,
    rare: Optional[str] = None,
    family: Optional[str] = None,
    observed: bool,
    inferred_from: Optional[str] = None,
    published_filename: Optional[str] = None,
) -> str:
    return graph.add_node(
        node_id(kind, family or "", method or "", dataset or "", phenotype or "", ancestry or "", rare or ""),
        node_type="directory",
        dapper_class=dapper_class,
        label=label,
        uri=uri,
        location_path=uri,
        directory_kind=kind,
        family=family,
        method=method,
        dataset=dataset,
        phenotype=phenotype,
        ancestry=ancestry,
        rare=rare,
        observed_from_listing=observed,
        inferred_from_code=inferred_from,
        published_filename=published_filename,
        annotation_source=DAPPER_NOTE,
    )


def add_edge(
    graph: GraphBuilder,
    source: str,
    target: str,
    *,
    edge_class: str,
    predicate: str,
    edge_role: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    edge_key = f"edge:{edge_class}__{safe_token(source)}__{safe_token(target)}"
    payload = {
        "source": source,
        "target": target,
        "relationship": edge_class,
        "dapper_edge_class": edge_class,
        "predicate": predicate,
        "annotation_source": DAPPER_NOTE,
    }
    if edge_role:
        payload["edge_role"] = edge_role
    if description:
        payload["description"] = description
    graph.add_edge(edge_key, **payload)


def stage_location_path(stage_name: str) -> str:
    path_by_stage = {
        "VariantProcessingStage": "intake/src/main/scala/VariantProcessingStage.scala",
        "VariantQCStage": "intake/src/main/scala/VariantQCStage.scala",
        "VariantScalingStage": "intake/src/main/scala/VariantScalingStage.scala",
        "PartitionStage": "bottom-line/src/main/scala/PartitionStage.scala",
        "AncestrySpecificStage": "bottom-line/src/main/scala/AncestrySpecificStage.scala",
        "LoadAncestrySpecificStage": "bottom-line/src/main/scala/LoadAncestrySpecificStage.scala",
        "TransEthnicStage": "bottom-line/src/main/scala/TransEthnicStage.scala",
        "LoadTransEthnicStage": "bottom-line/src/main/scala/LoadTransEthnicStage.scala",
        "MinPStage": "bottom-line/src/main/scala/MinPStage.scala",
        "MinPTransEthnicStage": "bottom-line/src/main/scala/MinPTransEthnicStage.scala",
        "LargestStage": "bottom-line/src/main/scala/LargestStage.scala",
        "OpenDataTransferStage": "bottom-line/src/main/scala/OpenDataTransferStage.scala",
    }
    suffix = path_by_stage.get(stage_name, "")
    return f"{GITHUB_REPO_ROOT}/{suffix}" if suffix else GITHUB_REPO_ROOT


def normalize_uri(uri: str) -> str:
    text = uri.strip().rstrip("/") + "/"
    replacements = [
        (
            "s3://dig-analysis-data/out/metaanalysis/bottom-line/out/metaanalysis/bottom-line/",
            "s3://dig-analysis-data/out/metaanalysis/bottom-line/",
        ),
        (
            "s3://dig-analysis-data/out/metaanalysis/min_p/out/metaanalysis/min_p/",
            "s3://dig-analysis-data/out/metaanalysis/min_p/",
        ),
        (
            "s3://dig-analysis-data/out/metaanalysis/largest/out/metaanalysis/largest/",
            "s3://dig-analysis-data/out/metaanalysis/largest/",
        ),
        (
            "s3://dig-analysis-data/out/metaanalysis/variants/out/metaanalysis/variants/",
            "s3://dig-analysis-data/out/metaanalysis/variants/",
        ),
        (
            "s3://dig-analysis-data/variants_raw/variants_raw/",
            "s3://dig-analysis-data/variants_raw/",
        ),
        (
            "s3://dig-analysis-data/variants_processed/variants_processed/",
            "s3://dig-analysis-data/variants_processed/",
        ),
        (
            "s3://dig-analysis-data/variants/variants/",
            "s3://dig-analysis-data/variants/",
        ),
        (
            "s3://dig-open-bottom-line-analysis-stg/bottom-line/bottom-line/",
            "s3://dig-open-bottom-line-analysis-stg/bottom-line/",
        ),
    ]
    for old, new in replacements:
        if text.startswith(old):
            text = new + text[len(old):]
            break
    return text


def parse_variant_inputs(lines: Iterable[str], pattern: re.Pattern[str]) -> List[VariantInput]:
    items: List[VariantInput] = []
    for line in lines:
        uri = normalize_uri(line)
        match = pattern.match(uri)
        if not match:
            continue
        items.append(
            VariantInput(
                method=match.group("method"),
                dataset=match.group("dataset"),
                phenotype=match.group("phenotype"),
                uri=uri,
            )
        )
    return items


def parse_partitions(lines: Iterable[str]) -> List[VariantPartition]:
    items: List[VariantPartition] = []
    for line in lines:
        uri = normalize_uri(line)
        match = PARTITIONED_RE.match(uri)
        if not match:
            continue
        items.append(
            VariantPartition(
                phenotype=match.group("phenotype"),
                dataset=match.group("dataset"),
                ancestry=match.group("ancestry"),
                rare=match.group("rare"),
                uri=uri,
            )
        )
    return items


def parse_results(lines: Iterable[str], family: str) -> Tuple[Set[Tuple[str, str]], Set[str], Dict[Tuple[str, str], str], Dict[str, str]]:
    ancestry: Set[Tuple[str, str]] = set()
    trans: Set[str] = set()
    ancestry_uri: Dict[Tuple[str, str], str] = {}
    trans_uri: Dict[str, str] = {}
    for line in lines:
        uri = normalize_uri(line)
        match = ANCESTRY_RESULT_RE.match(uri)
        if match and match.group("family") == family:
            key = (match.group("phenotype"), match.group("ancestry"))
            ancestry.add(key)
            ancestry_uri[key] = uri
            continue
        match = TRANS_RESULT_RE.match(uri)
        if match and match.group("family") == family:
            phenotype = match.group("phenotype")
            trans.add(phenotype)
            trans_uri[phenotype] = uri
    return ancestry, trans, ancestry_uri, trans_uri


def parse_open_data(lines: Iterable[str]) -> List[OpenDataOutput]:
    outputs: List[OpenDataOutput] = []
    for line in lines:
        uri = normalize_uri(line).rstrip("/")
        match = OPEN_DATA_RE.match(uri)
        if not match:
            continue
        outputs.append(
            OpenDataOutput(
                ancestry=match.group("ancestry"),
                phenotype=match.group("phenotype"),
                uri=uri,
            )
        )
    return outputs


def build_graph() -> Tuple[dict, List[str]]:
    missing_messages: List[str] = []
    for key, bucket in REQUIRED_LISTINGS.items():
        if not LISTING_FILES[key].exists():
            missing_messages.append(
                f"Missing S3 directory listing snapshot for {bucket} "
                f"(expected local file {LISTING_FILES[key].relative_to(REPO_ROOT)})"
            )

    raw_inputs = parse_variant_inputs(read_lines(LISTING_FILES["variants_raw"]), VARIANTS_RAW_RE)
    processed_inputs = parse_variant_inputs(read_lines(LISTING_FILES["variants_processed"]), VARIANTS_PROCESSED_RE)
    variants_inputs = parse_variant_inputs(read_lines(LISTING_FILES["variants"]), VARIANTS_RE)
    partitions = parse_partitions(read_lines(LISTING_FILES["partitioned_variants"]))

    bottom_ancestry, bottom_trans, bottom_ancestry_uri, bottom_trans_uri = parse_results(
        read_lines(LISTING_FILES["bottom_line"]),
        "bottom-line",
    )
    minp_ancestry, minp_trans, minp_ancestry_uri, minp_trans_uri = parse_results(
        read_lines(LISTING_FILES["min_p"]),
        "min_p",
    )
    largest_ancestry, largest_trans, largest_ancestry_uri, largest_trans_uri = parse_results(
        read_lines(LISTING_FILES["largest"]),
        "largest",
    )
    open_outputs = parse_open_data(read_lines(LISTING_FILES["open_data"]))

    raw_by_key = {(item.method, item.dataset, item.phenotype): item for item in raw_inputs}
    processed_by_key = {(item.method, item.dataset, item.phenotype): item for item in processed_inputs}
    variants_by_key = {(item.method, item.dataset, item.phenotype): item for item in variants_inputs}

    partitions_by_combo: DefaultDict[Tuple[str, str], List[VariantPartition]] = defaultdict(list)
    partitions_by_dataset: DefaultDict[Tuple[str, str], List[VariantPartition]] = defaultdict(list)
    methods_by_dataset_phenotype: DefaultDict[Tuple[str, str], Set[str]] = defaultdict(set)
    for part in partitions:
        partitions_by_combo[(part.phenotype, part.ancestry)].append(part)
        partitions_by_dataset[(part.dataset, part.phenotype)].append(part)
    for item in variants_inputs:
        methods_by_dataset_phenotype[(item.dataset, item.phenotype)].add(item.method)

    open_by_combo = {(item.phenotype, item.ancestry): item for item in open_outputs}

    graph = GraphBuilder()

    # Intake path: variants_raw -> processing -> variants_processed -> QC -> variants_qc(inferred) -> scaling -> variants
    for key in sorted(variants_by_key):
        method, dataset, phenotype = key
        raw = raw_by_key.get(key)
        processed = processed_by_key.get(key)
        scaled = variants_by_key[key]

        raw_node = add_directory(
            graph,
            "variants_raw",
            f"Raw variants input for {method} / {dataset} / {phenotype}",
            raw.uri if raw else f"s3://dig-analysis-data/variants_raw/{method}/{dataset}/{phenotype}/",
            dapper_class="C2M2File",
            method=method,
            dataset=dataset,
            phenotype=phenotype,
            observed=raw is not None,
            inferred_from=None if raw else "intake VariantProcessingStage input convention",
        )
        processing_stage = add_stage(
            graph,
            "VariantProcessingStage",
            f"Intake variant processing for {method} / {dataset} / {phenotype}",
            stage_group="intake",
            method=method,
            dataset=dataset,
            phenotype=phenotype,
            observed=processed is not None,
        )
        processed_node = add_directory(
            graph,
            "variants_processed",
            f"Processed intake variants for {method} / {dataset} / {phenotype}",
            processed.uri if processed else f"s3://dig-analysis-data/variants_processed/{method}/{dataset}/{phenotype}/",
            dapper_class="C2M2File",
            method=method,
            dataset=dataset,
            phenotype=phenotype,
            observed=processed is not None,
            inferred_from=None if processed else "intake VariantProcessingStage output convention",
        )
        qc_stage = add_stage(
            graph,
            "VariantQCStage",
            f"Intake QC for {method} / {dataset} / {phenotype}",
            stage_group="intake",
            method=method,
            dataset=dataset,
            phenotype=phenotype,
        )
        qc_node = add_directory(
            graph,
            "variants_qc",
            f"QC variants for {method} / {dataset} / {phenotype}",
            f"s3://dig-analysis-data/variants_qc/{method}/{dataset}/{phenotype}/",
            dapper_class="C2M2File",
            method=method,
            dataset=dataset,
            phenotype=phenotype,
            observed=False,
            inferred_from="intake VariantQCStage output convention",
        )
        scaling_stage = add_stage(
            graph,
            "VariantScalingStage",
            f"Intake scaling for {method} / {dataset} / {phenotype}",
            stage_group="intake",
            method=method,
            dataset=dataset,
            phenotype=phenotype,
            observed=True,
        )
        variants_node = add_directory(
            graph,
            "variants",
            f"Bottom-line variants input for {method} / {dataset} / {phenotype}",
            scaled.uri,
            dapper_class="C2M2File",
            method=method,
            dataset=dataset,
            phenotype=phenotype,
            observed=True,
        )
        partition_stage = add_stage(
            graph,
            "PartitionStage",
            f"Bottom-line partitioning for {dataset} / {phenotype}",
            stage_group="bottom-line",
            method=method,
            dataset=dataset,
            phenotype=phenotype,
            observed=bool(partitions_by_dataset.get((dataset, phenotype))),
        )

        add_edge(graph, processing_stage, raw_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
        add_edge(graph, processed_node, processing_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")
        add_edge(graph, qc_stage, processed_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
        add_edge(graph, qc_node, qc_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")
        add_edge(graph, scaling_stage, qc_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
        add_edge(graph, variants_node, scaling_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")
        add_edge(graph, partition_stage, variants_node, edge_class="Used", predicate="prov:used", edge_role="data_input")

        for part in sorted(partitions_by_dataset.get((dataset, phenotype), []), key=lambda item: (item.ancestry, item.rare)):
            partition_node = add_directory(
                graph,
                "partitioned_variants",
                f"Partitioned variants for {dataset} / {phenotype} / {part.ancestry} / rare={part.rare}",
                part.uri,
                dapper_class="C2M2File",
                dataset=dataset,
                phenotype=phenotype,
                ancestry=part.ancestry,
                rare=part.rare,
                observed=True,
            )
            add_edge(graph, partition_node, partition_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

    # Ancestry-specific outputs, min_p, largest, and optional open-data endpoints.
    ancestry_combos = sorted(bottom_ancestry | minp_ancestry | largest_ancestry | set(open_by_combo))
    for phenotype, ancestry in ancestry_combos:
        combo_partitions = partitions_by_combo.get((phenotype, ancestry), [])
        common_partitions = [part for part in combo_partitions if part.rare == "false"]
        rare_partitions = [part for part in combo_partitions if part.rare == "true"]

        if ancestry != "Mixed" and (phenotype, ancestry) in bottom_ancestry:
            ancestry_stage = add_stage(
                graph,
                "AncestrySpecificStage",
                f"Bottom-line ancestry-specific METAL for {phenotype} / {ancestry}",
                stage_group="bottom-line",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=True,
            )
            for part in common_partitions:
                part_node = node_id("partitioned_variants", "", "", part.dataset, part.phenotype, part.ancestry, part.rare)
                add_edge(graph, ancestry_stage, part_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            ancestry_staging = add_directory(
                graph,
                "bottom_line_staging_ancestry_specific",
                f"Bottom-line ancestry-specific staging for {phenotype} / {ancestry}",
                f"s3://dig-analysis-data/out/metaanalysis/bottom-line/staging/ancestry-specific/{phenotype}/ancestry={ancestry}/",
                dapper_class="C2M2File",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=False,
                inferred_from="bottom-line runAncestrySpecific.sh output convention",
            )
            add_edge(graph, ancestry_staging, ancestry_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

            load_stage = add_stage(
                graph,
                "LoadAncestrySpecificStage",
                f"Bottom-line ancestry-specific load for {phenotype} / {ancestry}",
                stage_group="bottom-line",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=True,
            )
            add_edge(graph, load_stage, ancestry_staging, edge_class="Used", predicate="prov:used", edge_role="data_input")
            for part in rare_partitions:
                part_node = node_id("partitioned_variants", "", "", part.dataset, part.phenotype, part.ancestry, part.rare)
                add_edge(graph, load_stage, part_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            ancestry_output = add_directory(
                graph,
                "bottom_line_ancestry_specific",
                f"Bottom-line ancestry-specific result for {phenotype} / {ancestry}",
                bottom_ancestry_uri[(phenotype, ancestry)],
                dapper_class="Dataset",
                family="bottom-line",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=True,
            )
            add_edge(graph, ancestry_output, load_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

        if (phenotype, ancestry) in minp_ancestry:
            minp_stage = add_stage(
                graph,
                "MinPStage",
                f"Bottom-line min_p for {phenotype} / {ancestry}",
                stage_group="bottom-line",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=True,
            )
            for part in combo_partitions:
                part_node = node_id("partitioned_variants", "", "", part.dataset, part.phenotype, part.ancestry, part.rare)
                add_edge(graph, minp_stage, part_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            minp_node = add_directory(
                graph,
                "min_p_ancestry_specific",
                f"min_p ancestry-specific result for {phenotype} / {ancestry}",
                minp_ancestry_uri[(phenotype, ancestry)],
                dapper_class="Dataset",
                family="min_p",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=True,
            )
            add_edge(graph, minp_node, minp_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

        if (phenotype, ancestry) in largest_ancestry:
            largest_stage = add_stage(
                graph,
                "LargestStage",
                f"Bottom-line largest for {phenotype} / {ancestry}",
                stage_group="bottom-line",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=True,
            )
            for part in combo_partitions:
                part_node = node_id("partitioned_variants", "", "", part.dataset, part.phenotype, part.ancestry, part.rare)
                add_edge(graph, largest_stage, part_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            largest_node = add_directory(
                graph,
                "largest_ancestry_specific",
                f"largest ancestry-specific result for {phenotype} / {ancestry}",
                largest_ancestry_uri[(phenotype, ancestry)],
                dapper_class="Dataset",
                family="largest",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=True,
            )
            add_edge(graph, largest_node, largest_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

        output = open_by_combo.get((phenotype, ancestry))
        if output and ancestry != "Mixed" and (phenotype, ancestry) in bottom_ancestry:
            open_stage = add_stage(
                graph,
                "OpenDataTransferStage",
                f"Open-data publication for {phenotype} / {ancestry}",
                stage_group="bottom-line",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=True,
            )
            bottom_node = node_id("bottom_line_ancestry_specific", "bottom-line", "", "", phenotype, ancestry, "")
            minp_node = node_id("min_p_ancestry_specific", "min_p", "", "", phenotype, ancestry, "")
            add_edge(graph, open_stage, bottom_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            if (phenotype, ancestry) in minp_ancestry:
                add_edge(graph, open_stage, minp_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            if (phenotype, ancestry) in largest_ancestry:
                largest_node = node_id("largest_ancestry_specific", "largest", "", "", phenotype, ancestry, "")
                add_edge(graph, open_stage, largest_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            endpoint = add_directory(
                graph,
                "open_data_endpoint",
                f"Open-data bottom-line endpoint for {phenotype} / {ancestry}",
                output.uri,
                dapper_class="DrsObject",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=True,
                published_filename=f"{phenotype}.sumstats.tsv.gz",
            )
            add_edge(graph, endpoint, open_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

    # Trans-ethnic branch and Mixed open-data outputs.
    trans_combos = sorted(bottom_trans | minp_trans | largest_trans | {item.phenotype for item in open_outputs if item.ancestry == "Mixed"})
    for phenotype in trans_combos:
        ancestry_inputs = sorted(combo for combo in bottom_ancestry if combo[0] == phenotype and combo[1] != "Mixed")
        if phenotype in bottom_trans:
            trans_stage = add_stage(
                graph,
                "TransEthnicStage",
                f"Bottom-line trans-ethnic METAL for {phenotype}",
                stage_group="bottom-line",
                phenotype=phenotype,
                observed=True,
            )
            for _, ancestry in ancestry_inputs:
                bottom_node = node_id("bottom_line_ancestry_specific", "bottom-line", "", "", phenotype, ancestry, "")
                add_edge(graph, trans_stage, bottom_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            trans_staging = add_directory(
                graph,
                "bottom_line_staging_trans_ethnic",
                f"Bottom-line trans-ethnic staging for {phenotype}",
                f"s3://dig-analysis-data/out/metaanalysis/bottom-line/staging/trans-ethnic/{phenotype}/",
                dapper_class="C2M2File",
                phenotype=phenotype,
                observed=False,
                inferred_from="bottom-line runTransEthnic.sh output convention",
            )
            add_edge(graph, trans_staging, trans_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

            load_stage = add_stage(
                graph,
                "LoadTransEthnicStage",
                f"Bottom-line trans-ethnic load for {phenotype}",
                stage_group="bottom-line",
                phenotype=phenotype,
                observed=True,
            )
            add_edge(graph, load_stage, trans_staging, edge_class="Used", predicate="prov:used", edge_role="data_input")
            for item in sorted([value for value in variants_inputs if value.phenotype == phenotype], key=lambda x: (x.method, x.dataset)):
                variants_node = node_id("variants", "", item.method, item.dataset, item.phenotype, "", "")
                add_edge(
                    graph,
                    load_stage,
                    variants_node,
                    edge_class="Used",
                    predicate="prov:used",
                    edge_role="data_input",
                    description="Used to recover Mixed ancestry variants during trans-ethnic load",
                )
            trans_node = add_directory(
                graph,
                "bottom_line_trans_ethnic",
                f"Bottom-line trans-ethnic result for {phenotype}",
                bottom_trans_uri[phenotype],
                dapper_class="Dataset",
                family="bottom-line",
                phenotype=phenotype,
                observed=True,
            )
            add_edge(graph, trans_node, load_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

        if phenotype in minp_trans:
            minp_stage = add_stage(
                graph,
                "MinPTransEthnicStage",
                f"Bottom-line min_p trans-ethnic for {phenotype}",
                stage_group="bottom-line",
                phenotype=phenotype,
                observed=True,
            )
            for _, ancestry in ancestry_inputs:
                minp_node = node_id("min_p_ancestry_specific", "min_p", "", "", phenotype, ancestry, "")
                add_edge(graph, minp_stage, minp_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            minp_trans_node = add_directory(
                graph,
                "min_p_trans_ethnic",
                f"min_p trans-ethnic result for {phenotype}",
                minp_trans_uri[phenotype],
                dapper_class="Dataset",
                family="min_p",
                phenotype=phenotype,
                observed=True,
            )
            add_edge(graph, minp_trans_node, minp_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

        if phenotype in largest_trans:
            largest_stage = add_stage(
                graph,
                "LargestStage",
                f"Bottom-line largest trans-ethnic for {phenotype}",
                stage_group="bottom-line",
                phenotype=phenotype,
                observed=True,
            )
            for part in sorted([item for item in partitions if item.phenotype == phenotype], key=lambda x: (x.dataset, x.ancestry, x.rare)):
                part_node = node_id("partitioned_variants", "", "", part.dataset, part.phenotype, part.ancestry, part.rare)
                add_edge(graph, largest_stage, part_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            largest_trans_node = add_directory(
                graph,
                "largest_trans_ethnic",
                f"largest trans-ethnic result for {phenotype}",
                largest_trans_uri[phenotype],
                dapper_class="Dataset",
                family="largest",
                phenotype=phenotype,
                observed=True,
            )
            add_edge(graph, largest_trans_node, largest_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

        output = open_by_combo.get((phenotype, "Mixed"))
        if output and phenotype in bottom_trans:
            open_stage = add_stage(
                graph,
                "OpenDataTransferStage",
                f"Open-data publication for {phenotype} / Mixed",
                stage_group="bottom-line",
                phenotype=phenotype,
                ancestry="Mixed",
                observed=True,
            )
            trans_node = node_id("bottom_line_trans_ethnic", "bottom-line", "", "", phenotype, "", "")
            add_edge(graph, open_stage, trans_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            if phenotype in minp_trans:
                minp_trans_node = node_id("min_p_trans_ethnic", "min_p", "", "", phenotype, "", "")
                add_edge(graph, open_stage, minp_trans_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            if phenotype in largest_trans:
                largest_trans_node = node_id("largest_trans_ethnic", "largest", "", "", phenotype, "", "")
                add_edge(graph, open_stage, largest_trans_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            endpoint = add_directory(
                graph,
                "open_data_endpoint",
                f"Open-data bottom-line endpoint for {phenotype} / Mixed",
                output.uri,
                dapper_class="DrsObject",
                phenotype=phenotype,
                ancestry="Mixed",
                observed=True,
                published_filename=f"{phenotype}.sumstats.tsv.gz",
            )
            add_edge(graph, endpoint, open_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

    return graph.render(), missing_messages


def main() -> int:
    graph, missing_messages = build_graph()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote graph with {len(graph['nodes'])} nodes and {len(graph['edges'])} edges to {OUTPUT_PATH}")
    if missing_messages:
        print("Missing S3 listing snapshots:")
        for message in missing_messages:
            print(f"- {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
