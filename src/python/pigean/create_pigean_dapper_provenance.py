#!/usr/bin/env python3
"""Create DAPPER 0.2.0 Pigean provenance from local S3 listing snapshots.

One YAML document is made for each supported JSON result in
data/pigean/s3/dig-pigean-json-results.txt. No S3 calls are made. The Scala
stages and Python resources in broadinstitute/dig-aggregator-methods establish
the inferred lineage; listing snapshots establish only observed locations.

Example:
    python3 src/python/pigean/create_pigean_dapper_provenance.py \\
        --in-database data/database/20261109pigeanStartLoad.sqlite \\
        --out-dir /path/to/PigeanYaml

Use --max-results 3 with a copy of the database for a small integration test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src" / "python" / "onestep"))
from create_bottom_line_dapper_provenance import lint_documents, mint_documents  # noqa: E402

PIPELINE_TYPE = "pigean"
ANCESTRY_ID = "Unspecified"
LISTING_DIR = REPO_ROOT / "data" / "pigean" / "s3"
DATABASE = REPO_ROOT / "data" / "database" / "20261109pigeanStartLoad.sqlite"
OUTPUT_DIR = Path("/Users/mduby/Data/Broad/ProvenanceArtifact/Pigean/PigeanYaml/20261109bSecondDraftChasePigean")
LINTER = Path("/Users/mduby/Code/DccWorkspace/DapperSchema/schema/lint/lint_provenance.py")
IDENTITY = Path("/Users/mduby/Code/DccWorkspace/DapperSchema/schema/identity/dapper_identity.py")
METHODS = "https://github.com/broadinstitute/dig-aggregator-methods"
RESULT_TYPES = {
    "gene_stats": "translatePigean.py",
    "gene_set_stats": "translatePigean.py",
    "gene_gene_set_stats": "translatePigean.py",
    "factor": "translateFactor.py",
    "gene_factor": "translateFactor.py",
    "gene_set_factor": "translateFactor.py",
    "combined_gene_stats": "combinePigeanFactor.py",
    "combined_gene_set_stats": "combinePigeanFactor.py",
}
EXPECTED_LISTING_PREFIXES = (
    "s3://dig-analysis-data/out/pigean/gene_lists/",
    "s3://dig-analysis-data/out/pigean/staging/combined/",
    "s3://dig-analysis-data/variants_raw/",
    "s3://dig-analysis-bin/pigean/methods/",
)


def normalize_listing_uri(uri: str) -> str:
    """Undo the repeated query prefix in these full-path listing snapshots."""
    if not uri.startswith("s3://"):
        raise ValueError("not an S3 URI")
    bucket, _, key = uri[5:].partition("/")
    if not bucket or not key:
        raise ValueError("S3 URI is missing a bucket or key")
    parts = key.strip("/").split("/")
    for size in range(len(parts) // 2, 0, -1):
        if parts[:size] == parts[size:2 * size]:
            parts = parts[size:]
            break
    return f"s3://{bucket}/{'/'.join(parts)}" + ("/" if uri.endswith("/") else "")


def read_locations(listing_dir: Path) -> dict[str, set[str]]:
    required = (
        "dig-pigean-json-results.txt",
        "dig-pigean-results.txt",
        "dig-anal-data-out-pigean-inputs.txt",
        "dig-anal-data-out-pigean-staging.txt",
        "dig-anal-cfde-out-pigean-staging.txt",
        "dig-anal-data-out-pigean-genelists.txt",
        "dig-anal-data-variants-raw.txt",
        "dig-anal-data-out-meta-bl-ethnic.txt",
        "dig-anal-data-gene_assoc.txt",
        "dig-anal-bin-gwas.txt",
        "dig-anal-bin-hpo.txt",
        "dig-anal-bin-orphanet.txt",
        "dig-anal-bin-pigean-methods.txt",
    )
    missing = [name for name in required if not (listing_dir / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing required S3 listing snapshot(s): " + ", ".join(missing))
    locations = {
        name: {normalize_listing_uri(line.strip()) for line in (listing_dir / name).read_text(encoding="utf-8").splitlines() if line.strip()}
        for name in required
    }
    locations["_all"] = set().union(*locations.values())
    return locations


def result_parts(uri: str) -> tuple[str, str, str, str]:
    marker = "/out/pigean/"
    if marker not in uri:
        raise ValueError("result path does not contain out/pigean")
    parts = uri.split(marker, 1)[1].split("/")
    if len(parts) != 5 or parts[0] not in RESULT_TYPES or parts[-1] != parts[0] + ".json":
        raise ValueError("no supported Pigean result producer for this path")
    kind, model, group, phenotype, _ = parts
    if not all((kind, model, group, phenotype)):
        raise ValueError("result path is missing a model, trait group, or phenotype")
    return kind, model, group, phenotype


def node_id(kind: str, value: str) -> str:
    return kind + ":" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def provenance_filename(uri: str, ancestry: str, case_collisions: set[str]) -> str:
    kind, model, group, phenotype = result_parts(uri)
    stem = f"pigean_{kind}_{model}_{group}_{ancestry}_{phenotype}"
    if stem.casefold() in case_collisions:
        # APFS is commonly case insensitive. Distinguish trait codes whose
        # only difference is letter case without adding a generated number.
        phenotype = "".join("^" + ch if ch.isupper() else ch for ch in phenotype)
        stem = f"pigean_{kind}_{model}_{group}_{ancestry}_{phenotype}"
    filename = stem + ".yaml"
    if len(filename.encode("utf-8")) > 255 or "/" in filename or "\\" in filename:
        raise ValueError("trait code cannot be represented safely in a filename")
    return filename


def case_collision_keys(result_paths: list[str], ancestry: str) -> set[str]:
    names: Counter[str] = Counter()
    for uri in result_paths:
        try:
            kind, model, group, phenotype = result_parts(uri)
        except ValueError:
            continue
        names[f"pigean_{kind}_{model}_{group}_{ancestry}_{phenotype}".casefold()] += 1
    return {name for name, count in names.items() if count > 1}


def script_url(script: str) -> str:
    return f"{METHODS}/blob/master/pigean/src/main/resources/{script}"


def build_document(uri: str, locations: dict[str, set[str]]) -> tuple[dict, str, str]:
    kind, model, group, phenotype = result_parts(uri)
    all_locations = locations["_all"]
    doc: dict[str, list[dict]] = {
        "datasets": [], "files": [], "activities": [], "used_edges": [],
        "was_generated_by_edges": [], "has_file_edges": [],
    }
    datasets: dict[str, str] = {}
    activities: set[str] = set()

    def dataset(path: str, name: str) -> str:
        if path not in datasets:
            identifier = node_id("dataset", path)
            observed = path in all_locations or (path.rstrip("/") + "/") in all_locations
            evidence = "Present in a supplied S3 listing snapshot." if observed else "Location inferred from pipeline source code; absent from supplied listing snapshots."
            doc["datasets"].append({
                "id": identifier, "name": name, "resource_type": "dataset",
                "description": evidence, "location": path,
            })
            datasets[path] = identifier
        return datasets[path]

    def stage(output: str, script: str, inputs: list[str]) -> None:
        activity_key = script + "|" + output
        if activity_key in activities:
            return
        activities.add(activity_key)
        activity = node_id("activity", activity_key)
        doc["activities"].append({
            "id": activity, "name": script.removesuffix(".py") + f" for {group}/{phenotype} ({model})",
            "activity_type": script.removesuffix(".py"),
            "description": "Pipeline stage inferred from the DIG Aggregator Methods source; this snapshot does not prove a particular execution.",
            "script_url": script_url(script), "repo_url": METHODS,
        })
        doc["was_generated_by_edges"].append({"subject": output, "predicate": "prov:wasGeneratedBy", "object": activity})
        for source in dict.fromkeys(inputs):
            doc["used_edges"].append({"subject": activity, "predicate": "prov:used", "object": source})

    base = "s3://dig-analysis-cfde/catalog_v2/out/pigean"
    staging_pigean = dataset(f"{base}/staging/pigean/{group}/{phenotype}/{model}/", "Pigean model staging output")
    input_type = {"gcat_trait": "sumstats", "portal": "sumstats", "rare_v2": "gene_lists"}.get(group)
    if input_type is None:
        raise ValueError(f"unsupported trait group {group!r}")
    input_path = f"s3://dig-analysis-data/out/pigean/inputs/{input_type}/{group}/{phenotype}/"
    input_dataset = dataset(input_path, f"Pigean {input_type} input for {group}/{phenotype}")
    stage(staging_pigean, "runPigean.py", [input_dataset])

    if group == "gcat_trait":
        raw = dataset("s3://dig-analysis-bin/gwas_catalog/", "GWAS Catalog input")
        stage(input_dataset, "gcatIntake.py", [raw])
    elif group == "rare_v2":
        raw = dataset("s3://dig-analysis-bin/orphanet/", "Orphanet source")
        stage(input_dataset, "orphanetIntake.py", [raw])
    else:
        raw = dataset(f"s3://dig-analysis-data/out/metaanalysis/bottom-line/trans-ethnic/{phenotype}/", "Bottom-line trans-ethnic result")
        stage(input_dataset, "makeSumstats.py", [raw])

    def translated(source_kind: str) -> str:
        source_path = f"{base}/{source_kind}/{model}/{group}/{phenotype}/"
        output = dataset(source_path, f"Pigean {source_kind} for {group}/{phenotype}")
        if source_kind in {"factor", "gene_factor", "gene_set_factor"}:
            factor_staging = dataset(f"{base}/staging/factor/{group}/{phenotype}/{model}/", "Factor staging output")
            stage(factor_staging, "factorBetas.py", [staging_pigean])
            stage(output, "translateFactor.py", [factor_staging])
        else:
            stage(output, "translatePigean.py", [staging_pigean])
        return output

    final_dir = uri.rsplit("/", 1)[0] + "/"
    final_dataset = dataset(final_dir, f"Pigean {kind} result for {group}/{phenotype} ({model})")
    if kind.startswith("combined_"):
        data_kind = "gene_stats" if kind == "combined_gene_stats" else "gene_set_stats"
        factor_kind = "gene_factor" if kind == "combined_gene_stats" else "gene_set_factor"
        inputs = [translated(data_kind)]
        factor_uri = f"{base}/{factor_kind}/{model}/{group}/{phenotype}/{factor_kind}.json"
        if factor_uri in locations["dig-pigean-json-results.txt"]:
            inputs.append(translated(factor_kind))
        stage(final_dataset, RESULT_TYPES[kind], inputs)
    elif kind in {"factor", "gene_factor", "gene_set_factor"}:
        factor_staging = dataset(f"{base}/staging/factor/{group}/{phenotype}/{model}/", "Factor staging output")
        stage(factor_staging, "factorBetas.py", [staging_pigean])
        stage(final_dataset, RESULT_TYPES[kind], [factor_staging])
    else:
        stage(final_dataset, RESULT_TYPES[kind], [staging_pigean])

    file_id = node_id("file", uri)
    doc["files"].append({"id": file_id, "name": f"{kind}.json", "filename": f"{kind}.json", "location": uri,
                         "description": "JSON result path present in the supplied S3 listing snapshot; object content was not inspected."})
    doc["has_file_edges"].append({"subject": final_dataset, "predicate": "dapper:hasFile", "object": file_id})
    return doc, f"{group}:{phenotype}", f"Pigean {kind} for {group}/{phenotype} ({model})"


def load_batch(connection: sqlite3.Connection, batch: list[tuple[str, dict, str, str]]) -> None:
    traits = sorted({trait for _, _, trait, _ in batch})
    connection.executemany(
        "INSERT INTO prov_trait (legacy_id, kpn_id, name, description) VALUES (?, ?, ?, NULL) ON CONFLICT(legacy_id) DO NOTHING",
        [(trait, trait, trait) for trait in traits],
    )
    connection.executemany(
        "INSERT INTO prov_artifact (id, pipeline_type, provenance, name, trait_legacy_id, ancestry_id, description) VALUES (?, ?, ?, ?, ?, ?, NULL)",
        [(name.removesuffix(".yaml"), PIPELINE_TYPE, json.dumps(doc, separators=(",", ":")), title, trait, "Unspecified")
         for name, doc, trait, title in batch],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--s3-listing-dir", type=Path, default=LISTING_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--in-database", type=Path, default=DATABASE)
    parser.add_argument("--skip-database", action="store_true", help="Write YAML without changing a database.")
    parser.add_argument("--dapper-linter", type=Path, default=LINTER)
    parser.add_argument("--dapper-identity", type=Path, default=IDENTITY)
    parser.add_argument("--max-results", type=int, help="Process only the first N result paths for testing.")
    parser.add_argument("--lint-all", action="store_true", help="Lint every document; default checks three representative files.")
    parser.add_argument("--batch-size", type=int, default=500)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.batch_size < 1 or (args.max_results is not None and args.max_results < 1):
        raise ValueError("batch size and max results must be positive")
    locations = read_locations(args.s3_listing_dir)
    all_result_paths = sorted(locations["dig-pigean-json-results.txt"])
    case_collisions = case_collision_keys(all_result_paths, ANCESTRY_ID)
    result_paths = all_result_paths
    if args.max_results is not None:
        result_paths = result_paths[:args.max_results]
    print(f"Read {len(result_paths)} JSON result paths from local S3 listings", flush=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    skipped: list[tuple[str, str]] = []
    seen_filenames: set[str] = set()
    samples: dict[str, tuple[str, dict]] = {}
    counts: Counter[str] = Counter()
    connection = None if args.skip_database else sqlite3.connect(args.in_database)
    try:
        if connection is not None:
            connection.execute("INSERT INTO prov_ancestry (ancestry_id, name, description) VALUES ('Unspecified', 'Unspecified', 'Pigean result path does not encode ancestry') ON CONFLICT(ancestry_id) DO NOTHING")
            connection.execute("DELETE FROM prov_artifact WHERE pipeline_type = ?", (PIPELINE_TYPE,))
        for start in range(0, len(result_paths), args.batch_size):
            records: list[tuple[str, dict, str, str]] = []
            for uri in result_paths[start:start + args.batch_size]:
                try:
                    if uri not in locations["dig-pigean-results.txt"]:
                        raise ValueError("result is absent from dig-pigean-results.txt")
                    filename = provenance_filename(uri, ANCESTRY_ID, case_collisions)
                    if filename.casefold() in seen_filenames:
                        raise ValueError(f"filename collides with another result: {filename}")
                    document, trait, title = build_document(uri, locations)
                    records.append((filename, document, trait, title))
                    seen_filenames.add(filename.casefold())
                except ValueError as exc:
                    skipped.append((uri, str(exc)))
            if not records:
                continue
            minted = dict(mint_documents([(name, doc) for name, doc, _, _ in records], args.dapper_identity))
            batch = [(name, minted[name], trait, title) for name, _, trait, title in records]
            if args.lint_all:
                lint_documents([(name, doc) for name, doc, _, _ in batch], args.dapper_linter, True)
            for name, doc, trait, title in batch:
                kind = name.split("_", 2)[1]
                if kind in {"gene", "factor", "combined"} and kind not in samples:
                    samples[kind] = (name, doc)
                (args.out_dir / name).write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
                counts[kind] += 1
            if connection is not None:
                load_batch(connection, batch)
            print(f"Created {sum(counts.values())}/{len(result_paths)} provenance YAML files", flush=True)
        if not args.lint_all:
            selected = list(samples.values())[:3]
            selected_names = {name for name, _ in selected}
            for name, doc, _, _ in batch:
                if len(selected) >= min(3, sum(counts.values())):
                    break
                if name not in selected_names:
                    selected.append((name, doc))
                    selected_names.add(name)
            lint_documents(selected, args.dapper_linter, True)
            print(f"Linted {len(selected)} sample provenance files", flush=True)
        if connection is not None:
            connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()
    print(f"Created {sum(counts.values())} files; database pipeline_type={PIPELINE_TYPE}")
    print("Missing directory listings by bucket (upstream paths referenced by code):")
    for prefix in EXPECTED_LISTING_PREFIXES:
        if any(path.startswith(prefix) for path in locations["_all"]):
            continue
        print(f"- {prefix}")
    print(f"Skipped result paths ({len(skipped)}):")
    for uri, reason in skipped:
        print(f"- {uri} — {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
