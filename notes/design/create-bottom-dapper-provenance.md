# `create_bottom_line_dapper_provenance.py` Design

## Purpose

[`create_bottom_line_dapper_provenance.py`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/src/python/onestep/create_bottom_line_dapper_provenance.py) is a one-step provenance export script for the `intake` and `bottom-line` pipeline outputs.

It combines the behavior of:

- [`create_bottom_line_graph.py`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/src/python/create_bottom_line_graph.py)
- [`create_bottom_line_dapper.py`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/src/python/create_bottom_line_dapper.py)

The script builds the computed-id bottom-line graph in memory, finds each published open-data endpoint, walks its upstream provenance subgraph, and writes one DAPPER-oriented JSON provenance document per endpoint.

## How To Run

Run from the repository root:

```bash
python3 src/python/onestep/create_bottom_line_dapper_provenance.py
```

There are no mandatory command-line arguments.

Optional arguments:

- `--s3-listing-dir`: directory containing S3 listing snapshot text files. Default: `data/s3`
- `--out-dir`: output directory for DAPPER provenance JSON files. Default: `data/bottom-line-provenance`
- `--out-graph-file`: optional path for writing the intermediate computed-id graph JSON. Default: not written

Example with explicit arguments:

```bash
python3 src/python/onestep/create_bottom_line_dapper_provenance.py \
  --s3-listing-dir data/s3 \
  --out-dir data/bottom-line-provenance \
  --out-graph-file data/graph/provenance_graph.json
```

## Inputs

The script consumes the same S3 listing snapshots as `create_bottom_line_graph.py`.

By default, those files are read from:

- [`data/s3`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/data/s3)

Expected listing files include:

- `dig-anal-variants_raw.txt`
- `dig-anal-variants_processed.txt`
- `dig-anal-variants.txt`
- `dig-anal-out-meta-variants.txt`
- `dig-anal-out-meta-bottom-line.txt`
- `dig-anal-out-meta-minp.txt`
- `dig-anal-out-meta-largest.txt`
- `dig-open-bottom-line-analysis-stg.txt`

If any expected listing file is missing, the script prints a warning that includes the represented S3 prefix and expected local path.

## Outputs

The primary output is one JSON file per open-data bottom-line endpoint.

Default output directory:

- [`data/bottom-line-provenance`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/data/bottom-line-provenance)

Output filenames are derived from the endpoint `location_path` by:

1. removing the `s3://` prefix
2. replacing `/` with `_`
3. appending `.json`

Example:

```text
s3://dig-open-bottom-line-analysis-stg/bottom-line/AA/2hrGadjBMI.sumstats.tsv.gz
```

becomes:

```text
dig-open-bottom-line-analysis-stg_bottom-line_AA_2hrGadjBMI.sumstats.tsv.gz.json
```

If `--out-graph-file` is provided, the script also writes the intermediate graph JSON to that path. This is useful for debugging or for keeping the one-step output comparable to the original two-step process.

## Processing Flow

The script performs four main operations.

1. Build the bottom-line graph from S3 listing snapshots by calling `build_graph()` from `create_bottom_line_graph.py`.
2. Use the graph generator's DAPPER 0.1.0 computed identifier pass so graph nodes use `dapper:{ClassName}.{digest}` ids.
3. Select root nodes where `directory_kind == "open_data_endpoint"` and `location_path` starts with `s3://dig-open-bottom-line-analysis-stg/`.
4. Walk each root node's upstream provenance subgraph through `WasGeneratedBy`, `WasDerivedFrom`, and `Used` edges, then write a DAPPER-style document for that root.

## Identifier Behavior

The graph generation step references:

- `https://github.com/broadinstitute/dapper/releases/tag/0.1.0`

The graph uses the DAPPER-ID-1 computed identifier pattern:

```text
dapper:{ClassName}.{sha512t24u digest}
```

The original graph-construction ids, such as `node:*` and `stage:*`, are retained as `original_id` on nodes and edges. Edge `source` and `target` values are rewritten to the computed DAPPER ids.

Each generated provenance document includes:

- `dapper_release`
- `dapper_id_profile`
- `root_node_id`
- `root_location_path`
- DAPPER-style arrays for datasets, DRS objects, activities, C2M2 files, and edges
- the raw root-specific provenance subgraph under `graph`

## Relationship To Existing Scripts

This script is intended for operational convenience when the intermediate graph file is not needed as a separate artifact.

Use `create_bottom_line_graph.py` and `create_bottom_line_dapper.py` separately when:

- you want to inspect or version the full graph before export
- you want to reuse the graph for multiple downstream exporters
- you want to debug graph construction independently from document export

Use this one-step script when:

- you want the final per-endpoint DAPPER provenance files directly
- the graph is only an intermediate representation
- a single command is easier for pipeline automation

## Current Limitations

The script intentionally delegates graph construction and DAPPER section mapping to the existing scripts.

Known limitations:

- it does not validate the generated JSON against a formal DAPPER schema implementation
- it does not compute file checksums, sizes, or object version metadata
- it only exports provenance for open-data endpoints under `s3://dig-open-bottom-line-analysis-stg/`
- it writes existing output filenames again if rerun against the same output directory
