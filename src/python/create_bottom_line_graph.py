#!/usr/bin/env python3
"""Create a provenance dependency graph for the bottom-line pipeline.

The graph is exported to data/graph/provenance_graph.json and contains two
top-level arrays:

- nodes: pipeline stage instances and S3 directory artifacts
- edges: dependency links annotated with DAPPER-inspired provenance semantics

The script uses local S3 listing snapshots under data/s3 plus path conventions
encoded in the intake and bottom-line pipelines.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
S3_DIR = DATA_DIR / "s3"
OUTPUT_PATH = DATA_DIR / "graph" / "provenance_graph.json"

# The open-data bucket is explicit in openDataTransfer.py.
OPEN_DATA_BUCKET = "s3://dig-open-bottom-line-analysis-stg"
# The analysis bucket is not explicit in the checked-in listing files, but the
# user provided an example dig-analysis-data raw input prefix; use that as the
# canonical bucket prefix for inferred analysis paths.
ANALYSIS_BUCKET = "s3://dig-analysis-data"

LISTING_FILES = {
    "partitioned_variants": S3_DIR / "out-variants.txt",
    "bottom_line": S3_DIR / "out-bottom-line.txt",
    "min_p": S3_DIR / "out-minp.txt",
    "largest": S3_DIR / "out-largest.txt",
}

EXPECTED_MISSING_LISTINGS = {
    "variants_raw": {
        "path": S3_DIR / "variants-raw.txt",
        "bucket": f"{ANALYSIS_BUCKET}/variants_raw/",
    },
    "variants_input": {
        "path": S3_DIR / "variants-inputs.txt",
        "bucket": f"{ANALYSIS_BUCKET}/variants/",
    },
    "open_data": {
        "path": S3_DIR / "open-data-bottom-line.txt",
        "bucket": f"{OPEN_DATA_BUCKET}/bottom-line/",
    },
}

VARIANT_RE = re.compile(
    r"^out/metaanalysis/variants/(?P<phenotype>[^/]+)/"
    r"dataset=(?P<dataset>[^/]+)/ancestry=(?P<ancestry>[^/]+)/"
    r"rare=(?P<rare>true|false)/$"
)
ANCESTRY_DIR_RE = re.compile(
    r"^out/metaanalysis/(?P<family>bottom-line|min_p|largest)/"
    r"ancestry-specific/(?P<phenotype>[^/]+)/ancestry=(?P<ancestry>[^/]+)/$"
)
TRANS_DIR_RE = re.compile(
    r"^out/metaanalysis/(?P<family>bottom-line|min_p|largest)/"
    r"trans-ethnic/(?P<phenotype>[^/]+)/$"
)


@dataclass(frozen=True)
class VariantPartition:
    phenotype: str
    dataset: str
    ancestry: str
    rare: str


def read_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class GraphBuilder:
    def __init__(self) -> None:
        self.nodes: Dict[str, dict] = {}
        self.edges: Dict[str, dict] = {}

    def add_node(self, node_id: str, **attrs: object) -> None:
        payload = {"id": node_id, **attrs}
        existing = self.nodes.get(node_id)
        if existing is None:
            self.nodes[node_id] = payload
            return
        merged = dict(existing)
        for key, value in payload.items():
            if key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = value
        self.nodes[node_id] = merged

    def add_edge(self, edge_id: str, **attrs: object) -> None:
        payload = {"id": edge_id, **attrs}
        if edge_id not in self.edges:
            self.edges[edge_id] = payload

    def to_dict(self) -> dict:
        return {
            "nodes": sorted(self.nodes.values(), key=lambda item: str(item["id"])),
            "edges": sorted(self.edges.values(), key=lambda item: str(item["id"])),
        }


def safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.=-]+", "_", value)


def make_stage_node_id(stage_name: str, *parts: str) -> str:
    suffix = "__".join(safe_token(part) for part in parts if part)
    return f"stage:{stage_name}" if not suffix else f"stage:{stage_name}__{suffix}"


def make_dir_node_id(kind: str, *parts: str) -> str:
    suffix = "__".join(safe_token(part) for part in parts if part)
    return f"dir:{kind}" if not suffix else f"dir:{kind}__{suffix}"


def add_stage_node(
    graph: GraphBuilder,
    stage_name: str,
    *,
    label: str,
    stage_group: str,
    phenotype: Optional[str] = None,
    ancestry: Optional[str] = None,
    dataset: Optional[str] = None,
    observed: bool = False,
) -> str:
    node_id = make_stage_node_id(
        stage_name,
        stage_group,
        phenotype or "",
        ancestry or "",
        dataset or "",
    )
    graph.add_node(
        node_id,
        node_type="stage",
        dapper_class="Activity",
        label=label,
        stage_name=stage_name,
        stage_group=stage_group,
        phenotype=phenotype,
        ancestry=ancestry,
        dataset=dataset,
        observed_from_listing=observed,
        annotation_source="notes/gptRecommendations/bottom-line-dapper.md",
    )
    return node_id


def add_directory_node(
    graph: GraphBuilder,
    node_id: str,
    *,
    label: str,
    uri: str,
    directory_kind: str,
    dapper_class: str,
    phenotype: Optional[str] = None,
    ancestry: Optional[str] = None,
    dataset: Optional[str] = None,
    rare: Optional[str] = None,
    observed: bool,
    inferred_from: Optional[str] = None,
    published_filename: Optional[str] = None,
) -> str:
    graph.add_node(
        node_id,
        node_type="directory",
        dapper_class=dapper_class,
        label=label,
        uri=uri,
        directory_kind=directory_kind,
        phenotype=phenotype,
        ancestry=ancestry,
        dataset=dataset,
        rare=rare,
        observed_from_listing=observed,
        inferred_from_code=inferred_from,
        published_filename=published_filename,
        annotation_source="notes/gptRecommendations/bottom-line-dapper.md",
    )
    return node_id


def add_prov_edge(
    graph: GraphBuilder,
    source: str,
    target: str,
    *,
    edge_class: str,
    predicate: str,
    edge_role: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    edge_id = f"edge:{edge_class}__{safe_token(source)}__{safe_token(target)}"
    payload = {
        "source": source,
        "target": target,
        "relationship": edge_class,
        "dapper_edge_class": edge_class,
        "predicate": predicate,
        "annotation_source": "notes/gptRecommendations/bottom-line-dapper.md",
    }
    if edge_role:
        payload["edge_role"] = edge_role
    if description:
        payload["description"] = description
    graph.add_edge(edge_id, **payload)


def parse_variant_partitions(lines: Iterable[str]) -> List[VariantPartition]:
    partitions: List[VariantPartition] = []
    for line in lines:
        match = VARIANT_RE.match(line)
        if not match:
            continue
        partitions.append(
            VariantPartition(
                phenotype=match.group("phenotype"),
                dataset=match.group("dataset"),
                ancestry=match.group("ancestry"),
                rare=match.group("rare"),
            )
        )
    return partitions


def parse_result_dirs(lines: Iterable[str]) -> Tuple[Set[Tuple[str, str]], Set[str]]:
    ancestry_specific: Set[Tuple[str, str]] = set()
    trans_ethnic: Set[str] = set()
    for line in lines:
        ancestry_match = ANCESTRY_DIR_RE.match(line)
        if ancestry_match:
            ancestry_specific.add((ancestry_match.group("phenotype"), ancestry_match.group("ancestry")))
            continue
        trans_match = TRANS_DIR_RE.match(line)
        if trans_match:
            trans_ethnic.add(trans_match.group("phenotype"))
    return ancestry_specific, trans_ethnic


def build_graph() -> Tuple[dict, List[str]]:
    missing_messages: List[str] = []
    graph = GraphBuilder()

    for name, metadata in EXPECTED_MISSING_LISTINGS.items():
        if not metadata["path"].exists():
            missing_messages.append(
                f"Missing S3 directory listing snapshot for {metadata['bucket']} "
                f"(expected local file {metadata['path'].relative_to(REPO_ROOT)})"
            )

    variant_lines = read_lines(LISTING_FILES["partitioned_variants"])
    bottom_lines = read_lines(LISTING_FILES["bottom_line"])
    minp_lines = read_lines(LISTING_FILES["min_p"])
    largest_lines = read_lines(LISTING_FILES["largest"])

    partitions = parse_variant_partitions(variant_lines)
    bottom_ancestry, bottom_trans = parse_result_dirs(bottom_lines)
    minp_ancestry, minp_trans = parse_result_dirs(minp_lines)
    largest_ancestry, largest_trans = parse_result_dirs(largest_lines)

    partitions_by_dataset: Dict[Tuple[str, str], Set[Tuple[str, str]]] = defaultdict(set)
    partitions_by_combo: Dict[Tuple[str, str], List[VariantPartition]] = defaultdict(list)
    datasets_by_phenotype: Dict[str, Set[str]] = defaultdict(set)
    for part in partitions:
        partitions_by_dataset[(part.dataset, part.phenotype)].add((part.ancestry, part.rare))
        partitions_by_combo[(part.phenotype, part.ancestry)].append(part)
        datasets_by_phenotype[part.phenotype].add(part.dataset)

    # Intake + bottom-line partition path up to partitioned metaanalysis variants.
    for (dataset, phenotype), ancestry_rare_pairs in sorted(partitions_by_dataset.items()):
        raw_node = add_directory_node(
            graph,
            make_dir_node_id("variants_raw", dataset, phenotype),
            label=f"Raw variants input for {dataset} / {phenotype}",
            uri=f"{ANALYSIS_BUCKET}/variants_raw/*/{dataset}/{phenotype}/",
            directory_kind="variants_raw_input",
            dapper_class="C2M2File",
            phenotype=phenotype,
            dataset=dataset,
            observed=False,
            inferred_from="intake VariantProcessingStage path convention",
        )
        processing_stage = add_stage_node(
            graph,
            "VariantProcessingStage",
            label=f"Intake variant processing for {dataset} / {phenotype}",
            stage_group="intake",
            phenotype=phenotype,
            dataset=dataset,
        )
        processed_node = add_directory_node(
            graph,
            make_dir_node_id("variants_processed", dataset, phenotype),
            label=f"Processed intake variants for {dataset} / {phenotype}",
            uri=f"{ANALYSIS_BUCKET}/variants_processed/*/{dataset}/{phenotype}/",
            directory_kind="variants_processed",
            dapper_class="C2M2File",
            phenotype=phenotype,
            dataset=dataset,
            observed=False,
            inferred_from="intake VariantProcessingStage output convention",
        )
        qc_stage = add_stage_node(
            graph,
            "VariantQCStage",
            label=f"Intake QC for {dataset} / {phenotype}",
            stage_group="intake",
            phenotype=phenotype,
            dataset=dataset,
        )
        qc_node = add_directory_node(
            graph,
            make_dir_node_id("variants_qc", dataset, phenotype),
            label=f"QC variants for {dataset} / {phenotype}",
            uri=f"{ANALYSIS_BUCKET}/variants_qc/*/{dataset}/{phenotype}/",
            directory_kind="variants_qc",
            dapper_class="C2M2File",
            phenotype=phenotype,
            dataset=dataset,
            observed=False,
            inferred_from="intake VariantQCStage output convention",
        )
        scaling_stage = add_stage_node(
            graph,
            "VariantScalingStage",
            label=f"Intake scaling for {dataset} / {phenotype}",
            stage_group="intake",
            phenotype=phenotype,
            dataset=dataset,
        )
        variants_input_node = add_directory_node(
            graph,
            make_dir_node_id("variants_input", dataset, phenotype),
            label=f"Bottom-line variants input for {dataset} / {phenotype}",
            uri=f"{ANALYSIS_BUCKET}/variants/*/{dataset}/{phenotype}/",
            directory_kind="variants_input",
            dapper_class="C2M2File",
            phenotype=phenotype,
            dataset=dataset,
            observed=False,
            inferred_from="intake VariantScalingStage output and bottom-line PartitionStage input convention",
        )
        partition_stage = add_stage_node(
            graph,
            "PartitionStage",
            label=f"Bottom-line partitioning for {dataset} / {phenotype}",
            stage_group="bottom-line",
            phenotype=phenotype,
            dataset=dataset,
        )

        add_prov_edge(graph, processing_stage, raw_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
        add_prov_edge(graph, processed_node, processing_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")
        add_prov_edge(graph, qc_stage, processed_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
        add_prov_edge(graph, qc_node, qc_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")
        add_prov_edge(graph, scaling_stage, qc_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
        add_prov_edge(graph, variants_input_node, scaling_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")
        add_prov_edge(graph, partition_stage, variants_input_node, edge_class="Used", predicate="prov:used", edge_role="data_input")

        for ancestry, rare in sorted(ancestry_rare_pairs):
            partition_node = add_directory_node(
                graph,
                make_dir_node_id("partitioned_variants", phenotype, dataset, ancestry, rare),
                label=f"Partitioned variants for {phenotype} / {dataset} / {ancestry} / rare={rare}",
                uri=(
                    f"{ANALYSIS_BUCKET}/out/metaanalysis/variants/{phenotype}/"
                    f"dataset={dataset}/ancestry={ancestry}/rare={rare}/"
                ),
                directory_kind="partitioned_variants",
                dapper_class="C2M2File",
                phenotype=phenotype,
                ancestry=ancestry,
                dataset=dataset,
                rare=rare,
                observed=True,
            )
            add_prov_edge(
                graph,
                partition_node,
                partition_stage,
                edge_class="WasGeneratedBy",
                predicate="prov:wasGeneratedBy",
            )

    # Ancestry-specific block, min_p, largest, and open-data ancestry outputs.
    ancestry_combos = sorted(bottom_ancestry | minp_ancestry | largest_ancestry)
    for phenotype, ancestry in ancestry_combos:
        combo_partitions = partitions_by_combo.get((phenotype, ancestry), [])
        common_partitions = [p for p in combo_partitions if p.rare == "false"]
        rare_partitions = [p for p in combo_partitions if p.rare == "true"]

        if ancestry != "Mixed":
            ancestry_stage = add_stage_node(
                graph,
                "AncestrySpecificStage",
                label=f"Bottom-line ancestry-specific METAL for {phenotype} / {ancestry}",
                stage_group="bottom-line",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=(phenotype, ancestry) in bottom_ancestry,
            )
            ancestry_staging_node = add_directory_node(
                graph,
                make_dir_node_id("bottom_line_staging_ancestry_specific", phenotype, ancestry),
                label=f"Bottom-line ancestry-specific staging for {phenotype} / {ancestry}",
                uri=(
                    f"{ANALYSIS_BUCKET}/out/metaanalysis/bottom-line/staging/"
                    f"ancestry-specific/{phenotype}/ancestry={ancestry}/"
                ),
                directory_kind="bottom_line_staging_ancestry_specific",
                dapper_class="C2M2File",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=False,
                inferred_from="runAncestrySpecific.sh output convention",
            )
            for part in common_partitions:
                part_node = make_dir_node_id("partitioned_variants", part.phenotype, part.dataset, part.ancestry, part.rare)
                add_prov_edge(graph, ancestry_stage, part_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            add_prov_edge(graph, ancestry_staging_node, ancestry_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

            load_stage = add_stage_node(
                graph,
                "LoadAncestrySpecificStage",
                label=f"Bottom-line ancestry-specific load for {phenotype} / {ancestry}",
                stage_group="bottom-line",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=(phenotype, ancestry) in bottom_ancestry,
            )
            add_prov_edge(graph, load_stage, ancestry_staging_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            for part in rare_partitions:
                part_node = make_dir_node_id("partitioned_variants", part.phenotype, part.dataset, part.ancestry, part.rare)
                add_prov_edge(graph, load_stage, part_node, edge_class="Used", predicate="prov:used", edge_role="data_input")

            ancestry_output_node = add_directory_node(
                graph,
                make_dir_node_id("bottom_line_ancestry_specific", phenotype, ancestry),
                label=f"Bottom-line ancestry-specific result for {phenotype} / {ancestry}",
                uri=f"{ANALYSIS_BUCKET}/out/metaanalysis/bottom-line/ancestry-specific/{phenotype}/ancestry={ancestry}/",
                directory_kind="bottom_line_ancestry_specific",
                dapper_class="Dataset",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=(phenotype, ancestry) in bottom_ancestry,
                inferred_from=None if (phenotype, ancestry) in bottom_ancestry else "loadAnalysis.py output convention",
            )
            add_prov_edge(graph, ancestry_output_node, load_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

        minp_stage = add_stage_node(
            graph,
            "MinPStage",
            label=f"Bottom-line min_p for {phenotype} / {ancestry}",
            stage_group="bottom-line",
            phenotype=phenotype,
            ancestry=ancestry,
            observed=(phenotype, ancestry) in minp_ancestry,
        )
        for part in combo_partitions:
            part_node = make_dir_node_id("partitioned_variants", part.phenotype, part.dataset, part.ancestry, part.rare)
            add_prov_edge(graph, minp_stage, part_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
        minp_node = add_directory_node(
            graph,
            make_dir_node_id("min_p_ancestry_specific", phenotype, ancestry),
            label=f"min_p ancestry-specific result for {phenotype} / {ancestry}",
            uri=f"{ANALYSIS_BUCKET}/out/metaanalysis/min_p/ancestry-specific/{phenotype}/ancestry={ancestry}/",
            directory_kind="min_p_ancestry_specific",
            dapper_class="Dataset",
            phenotype=phenotype,
            ancestry=ancestry,
            observed=(phenotype, ancestry) in minp_ancestry,
            inferred_from=None if (phenotype, ancestry) in minp_ancestry else "runMinP.py output convention",
        )
        add_prov_edge(graph, minp_node, minp_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

        largest_node_id = None
        if (phenotype, ancestry) in largest_ancestry:
            largest_stage = add_stage_node(
                graph,
                "LargestStage",
                label=f"Bottom-line largest for {phenotype} / {ancestry}",
                stage_group="bottom-line",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=True,
            )
            for part in combo_partitions:
                part_node = make_dir_node_id("partitioned_variants", part.phenotype, part.dataset, part.ancestry, part.rare)
                add_prov_edge(graph, largest_stage, part_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            largest_node_id = add_directory_node(
                graph,
                make_dir_node_id("largest_ancestry_specific", phenotype, ancestry),
                label=f"Largest ancestry-specific result for {phenotype} / {ancestry}",
                uri=f"{ANALYSIS_BUCKET}/out/metaanalysis/largest/ancestry-specific/{phenotype}/ancestry={ancestry}/",
                directory_kind="largest_ancestry_specific",
                dapper_class="Dataset",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=True,
            )
            add_prov_edge(graph, largest_node_id, largest_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

        if ancestry != "Mixed" and (phenotype, ancestry) in bottom_ancestry:
            open_stage = add_stage_node(
                graph,
                "OpenDataTransferStage",
                label=f"Open-data publication for {phenotype} / {ancestry}",
                stage_group="bottom-line",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=False,
            )
            ancestry_output_node = make_dir_node_id("bottom_line_ancestry_specific", phenotype, ancestry)
            add_prov_edge(graph, open_stage, ancestry_output_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            add_prov_edge(graph, open_stage, minp_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            if largest_node_id is not None:
                add_prov_edge(graph, open_stage, largest_node_id, edge_class="Used", predicate="prov:used", edge_role="data_input")
            endpoint_node = add_directory_node(
                graph,
                make_dir_node_id("open_data_endpoint", ancestry, phenotype),
                label=f"Open-data bottom-line endpoint for {phenotype} / {ancestry}",
                uri=f"{OPEN_DATA_BUCKET}/bottom-line/{ancestry}/",
                directory_kind="open_data_endpoint",
                dapper_class="DrsObject",
                phenotype=phenotype,
                ancestry=ancestry,
                observed=False,
                inferred_from="openDataTransfer.py output convention",
                published_filename=f"{phenotype}.sumstats.tsv.gz",
            )
            add_prov_edge(graph, endpoint_node, open_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

    # Trans-ethnic branch and open-data Mixed endpoints.
    for phenotype in sorted(bottom_trans | minp_trans | largest_trans):
        ancestry_specific_inputs = [
            combo for combo in sorted(bottom_ancestry)
            if combo[0] == phenotype and combo[1] != "Mixed"
        ]
        if phenotype in bottom_trans:
            trans_stage = add_stage_node(
                graph,
                "TransEthnicStage",
                label=f"Bottom-line trans-ethnic METAL for {phenotype}",
                stage_group="bottom-line",
                phenotype=phenotype,
                observed=True,
            )
            for _, ancestry in ancestry_specific_inputs:
                ancestry_output_node = make_dir_node_id("bottom_line_ancestry_specific", phenotype, ancestry)
                add_prov_edge(graph, trans_stage, ancestry_output_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            trans_staging = add_directory_node(
                graph,
                make_dir_node_id("bottom_line_staging_trans_ethnic", phenotype),
                label=f"Bottom-line trans-ethnic staging for {phenotype}",
                uri=f"{ANALYSIS_BUCKET}/out/metaanalysis/bottom-line/staging/trans-ethnic/{phenotype}/",
                directory_kind="bottom_line_staging_trans_ethnic",
                dapper_class="C2M2File",
                phenotype=phenotype,
                observed=False,
                inferred_from="runTransEthnic.sh output convention",
            )
            add_prov_edge(graph, trans_staging, trans_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

            load_trans_stage = add_stage_node(
                graph,
                "LoadTransEthnicStage",
                label=f"Bottom-line trans-ethnic load for {phenotype}",
                stage_group="bottom-line",
                phenotype=phenotype,
                observed=True,
            )
            add_prov_edge(graph, load_trans_stage, trans_staging, edge_class="Used", predicate="prov:used", edge_role="data_input")
            for dataset in sorted(datasets_by_phenotype.get(phenotype, set())):
                variants_input_node = make_dir_node_id("variants_input", dataset, phenotype)
                add_prov_edge(
                    graph,
                    load_trans_stage,
                    variants_input_node,
                    edge_class="Used",
                    predicate="prov:used",
                    edge_role="data_input",
                    description="Used to recover Mixed ancestry variants during trans-ethnic load",
                )
            trans_output_node = add_directory_node(
                graph,
                make_dir_node_id("bottom_line_trans_ethnic", phenotype),
                label=f"Bottom-line trans-ethnic result for {phenotype}",
                uri=f"{ANALYSIS_BUCKET}/out/metaanalysis/bottom-line/trans-ethnic/{phenotype}/",
                directory_kind="bottom_line_trans_ethnic",
                dapper_class="Dataset",
                phenotype=phenotype,
                observed=True,
            )
            add_prov_edge(graph, trans_output_node, load_trans_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")
        else:
            trans_output_node = make_dir_node_id("bottom_line_trans_ethnic", phenotype)

        minp_trans_node = None
        if phenotype in minp_trans:
            minp_trans_stage = add_stage_node(
                graph,
                "MinPTransEthnicStage",
                label=f"Bottom-line min_p trans-ethnic for {phenotype}",
                stage_group="bottom-line",
                phenotype=phenotype,
                observed=True,
            )
            for _, ancestry in ancestry_specific_inputs:
                minp_ancestry_node = make_dir_node_id("min_p_ancestry_specific", phenotype, ancestry)
                add_prov_edge(graph, minp_trans_stage, minp_ancestry_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            minp_trans_node = add_directory_node(
                graph,
                make_dir_node_id("min_p_trans_ethnic", phenotype),
                label=f"min_p trans-ethnic result for {phenotype}",
                uri=f"{ANALYSIS_BUCKET}/out/metaanalysis/min_p/trans-ethnic/{phenotype}/",
                directory_kind="min_p_trans_ethnic",
                dapper_class="Dataset",
                phenotype=phenotype,
                observed=True,
            )
            add_prov_edge(graph, minp_trans_node, minp_trans_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

        largest_trans_node = None
        if phenotype in largest_trans:
            largest_trans_stage = add_stage_node(
                graph,
                "LargestStage",
                label=f"Bottom-line largest trans-ethnic for {phenotype}",
                stage_group="bottom-line",
                phenotype=phenotype,
                observed=True,
            )
            for dataset in sorted(datasets_by_phenotype.get(phenotype, set())):
                for ancestry in sorted({part.ancestry for part in partitions_by_combo if False}):
                    pass
            for part in [p for plist in partitions_by_combo.values() for p in plist if p.phenotype == phenotype]:
                part_node = make_dir_node_id("partitioned_variants", part.phenotype, part.dataset, part.ancestry, part.rare)
                add_prov_edge(graph, largest_trans_stage, part_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            largest_trans_node = add_directory_node(
                graph,
                make_dir_node_id("largest_trans_ethnic", phenotype),
                label=f"Largest trans-ethnic result for {phenotype}",
                uri=f"{ANALYSIS_BUCKET}/out/metaanalysis/largest/trans-ethnic/{phenotype}/",
                directory_kind="largest_trans_ethnic",
                dapper_class="Dataset",
                phenotype=phenotype,
                observed=True,
            )
            add_prov_edge(graph, largest_trans_node, largest_trans_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

        if phenotype in bottom_trans:
            open_stage = add_stage_node(
                graph,
                "OpenDataTransferStage",
                label=f"Open-data publication for {phenotype} / Mixed",
                stage_group="bottom-line",
                phenotype=phenotype,
                ancestry="Mixed",
                observed=False,
            )
            add_prov_edge(graph, open_stage, trans_output_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            if minp_trans_node is not None:
                add_prov_edge(graph, open_stage, minp_trans_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            if largest_trans_node is not None:
                add_prov_edge(graph, open_stage, largest_trans_node, edge_class="Used", predicate="prov:used", edge_role="data_input")
            endpoint_node = add_directory_node(
                graph,
                make_dir_node_id("open_data_endpoint", "Mixed", phenotype),
                label=f"Open-data bottom-line endpoint for {phenotype} / Mixed",
                uri=f"{OPEN_DATA_BUCKET}/bottom-line/Mixed/",
                directory_kind="open_data_endpoint",
                dapper_class="DrsObject",
                phenotype=phenotype,
                ancestry="Mixed",
                observed=False,
                inferred_from="openDataTransfer.py output convention",
                published_filename=f"{phenotype}.sumstats.tsv.gz",
            )
            add_prov_edge(graph, endpoint_node, open_stage, edge_class="WasGeneratedBy", predicate="prov:wasGeneratedBy")

    return graph.to_dict(), missing_messages


def main() -> int:
    graph, missing_messages = build_graph()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(graph, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"Wrote graph with {len(graph['nodes'])} nodes and {len(graph['edges'])} edges to {OUTPUT_PATH}")
    if missing_messages:
        print("Missing S3 listing snapshots:")
        for message in missing_messages:
            print(f"- {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
