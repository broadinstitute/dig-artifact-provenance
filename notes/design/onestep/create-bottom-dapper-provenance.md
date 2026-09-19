# `create_bottom_line_dapper_provenance.py` Design

## Purpose

[`create_bottom_line_dapper_provenance.py`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/src/python/onestep/create_bottom_line_dapper_provenance.py) is a one-step provenance export script for the `intake` and `bottom-line` pipeline outputs.

It combines the behavior of:

- [`create_bottom_line_graph.py`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/src/python/individualsteps/create_bottom_line_graph.py)
- [`create_bottom_line_dapper.py`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/src/python/individualsteps/create_bottom_line_dapper.py)
- [`load_bottom_line_to_db.py`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/src/python/individualsteps/load_bottom_line_to_db.py)

The script builds the bottom-line graph in memory, creates one DAPPER provenance document per published open-data endpoint, mints DAPPER-ID-1 identifiers, validates each document with the DAPPER linter, and loads those documents into the SQLite `prov_artifact` table.

Each generated document uses the DAPPER 0.1.0 group-keyed document shape expected by the upstream linter. The document root contains DAPPER node and edge groups such as `datasets`, `activities`, `c2m2_files`, `drs_objects`, `used_edges`, `was_generated_by_edges`, and `has_drs_object_edges`.

Application-only envelope fields such as `graph`, `root_location_path`, and flattened `edges` are intentionally omitted from the emitted provenance document because the DAPPER linter runs in closed mode and treats unknown top-level keys as errors.

Writing the individual provenance JSON files is optional.

## How To Run

Run from the repository root:

```bash
python3 src/python/onestep/create_bottom_line_dapper_provenance.py
```

There are no mandatory command-line arguments.

Optional arguments:

- `--s3-listing-dir`: directory containing S3 listing snapshot text files. Default: `data/s3`
- `--in-database`: SQLite database file to load. Default: `data/database/provenance_db.sqlite`
- `--in-log-file`: log file for loader activity. Default: `logs/bottom-line-provenance.log`
- `--save-provenance-files`: write individual provenance JSON files. Default: disabled
- `--out-dir`: output directory for DAPPER provenance JSON files when `--save-provenance-files` is set. Default: `data/bottom-line-provenance`
- `--out-graph-file`: optional path for writing the intermediate computed-id graph JSON. Default: not written
- `--dapper-identity`: DAPPER identity script used to mint DAPPER-ID-1 node identifiers. Default: `/Users/mduby/Code/DccWorkspace/DapperSchema/schema/identity/dapper_identity.py`
- `--dapper-linter`: DAPPER linter script used to validate generated provenance. Default: `/Users/mduby/Code/DccWorkspace/DapperSchema/schema/lint/lint_provenance.py`
- `--skip-dapper-lint`: skip DAPPER lint validation. Default: disabled
- `--max-endpoints`: only export the first N sorted open-data endpoints. Default: all endpoints

Example with explicit arguments:

```bash
python3 src/python/onestep/create_bottom_line_dapper_provenance.py \
  --s3-listing-dir data/s3 \
  --in-database data/database/provenance_db.sqlite \
  --in-log-file logs/bottom-line-provenance.log \
  --save-provenance-files \
  --out-dir data/bottom-line-provenance \
  --out-graph-file data/graph/provenance_graph.json
```

One-endpoint validation test:

```bash
python3 src/python/onestep/create_bottom_line_dapper_provenance.py \
  --max-endpoints 1 \
  --in-database /tmp/onestep_dapper_one.sqlite \
  --in-log-file /tmp/onestep_dapper_one.log \
  --save-provenance-files \
  --out-dir /tmp/onestep_bottom_line_dapper_one \
  --out-graph-file /tmp/onestep_graph_one.json
```

Validate the generated file directly with the upstream DAPPER linter:

```bash
uv run /Users/mduby/Code/DccWorkspace/DapperSchema/schema/lint/lint_provenance.py \
  /tmp/onestep_bottom_line_dapper_one/dig-open-bottom-line-analysis-stg_bottom-line_AA_2hrGadjBMI.sumstats.tsv.gz.json
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

The primary output is a refreshed set of SQLite records in the `prov_artifact` table. Each stored provenance value is the DAPPER linter-facing provenance document, not the earlier application envelope.

Default database:

- [`data/database/provenance_db.sqlite`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/data/database/provenance_db.sqlite)

Before loading, existing rows with `pipeline_type = 'bottom-line'` are deleted. Each generated provenance document is inserted with:

- `id`: the output provenance filename stem
- `pipeline_type`: `bottom-line`
- `provenance`: the generated provenance document as compact JSON text
- `name`: the end-result description, usually from the first DRS object
- `description`: `NULL`

The database file must already exist and contain the `prov_artifact` table.

## Optional File Outputs

When `--save-provenance-files` is set, the script also writes one JSON file per open-data bottom-line endpoint.

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

## Logging

The script logs database-load activity to the path passed through `--in-log-file`.

Default log file:

- [`logs/bottom-line-provenance.log`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/logs/bottom-line-provenance.log)

The log includes:

- each generated provenance record prepared for database insertion
- total generated documents
- total database records created

## Processing Flow

The script performs seven main operations.

1. Build the bottom-line graph from S3 listing snapshots by calling `build_graph()` from `create_bottom_line_graph.py`.
2. Select root nodes where `directory_kind == "open_data_endpoint"` and `location_path` starts with `s3://dig-open-bottom-line-analysis-stg/`.
3. Optionally limit the selected roots with `--max-endpoints`, primarily for test runs.
4. Walk each root node's upstream provenance subgraph through `WasGeneratedBy`, `WasDerivedFrom`, and `Used` edges.
5. Build a DAPPER group-keyed document for that root, including a terminal published `Dataset` linked to its `DrsObject`.
6. Mint DAPPER-ID-1 computed node identifiers by running the DAPPER identity script.
7. Validate the generated document by running the DAPPER linter, then load it into SQLite and optionally write it as an individual JSON file.

## Identifier Behavior

The generated provenance references the DAPPER 0.1.0 model and identity behavior through the local DAPPER schema repository:

- identity minting: `/Users/mduby/Code/DccWorkspace/DapperSchema/schema/identity/dapper_identity.py`
- provenance linting: `/Users/mduby/Code/DccWorkspace/DapperSchema/schema/lint/lint_provenance.py`
- upstream release: `https://github.com/broadinstitute/dapper/releases/tag/0.1.0`

The final document uses the DAPPER-ID-1 computed identifier pattern:

```text
dapper:{ClassName}.{sha512t24u digest}
```

Temporary graph-construction ids, such as `node:*` and `stage:*`, are used only before minting. The final provenance document stores DAPPER-computed identifiers and edge endpoints rewritten to those identifiers.

## DAPPER Schema Shape

The full emitted provenance document is the schema/linter-facing DAPPER document.

It contains:

- `datasets`: DAPPER `Dataset` nodes, including the terminal published open-data result
- `drs_objects`: DAPPER `DrsObject` nodes for the published artifact bytes
- `activities`: DAPPER `Activity` nodes for intake and bottom-line pipeline stages
- `c2m2_files`: DAPPER `C2M2File` nodes for file inputs and intermediate artifacts
- `used_edges`: DAPPER `Used` relationships from activities to consumed files/datasets
- `was_generated_by_edges`: DAPPER `WasGeneratedBy` relationships from datasets/files to producing activities
- `was_derived_from_edges`: DAPPER `WasDerivedFrom` relationships when present in the graph
- `has_drs_object_edges`: DAPPER `HasDrsObject` relationship from the terminal dataset to the published DRS object

The records use DAPPER slot names such as:

- `Dataset`: `id`, `name`, `resource_type`, `description`, `access_level`, `was_generated_by`
- `DrsObject`: `id`, `name`, `drs_id`, `self_uri`, `mime_type`, `access_methods`
- `Activity`: `id`, `name`, `description`, `activity_type`, `repo_url`
- `C2M2File`: `id`, `name`, `description`, `filename`, `local_id`
- `Edge`: `subject`, `predicate`, `object`, and optional `edge_role`

Operational fields such as `phenotype`, `ancestry`, `location_path`, and raw `graph` are excluded from the linter-facing provenance document unless they are represented through DAPPER-defined fields.

## Relationship To Existing Scripts

This script is intended for operational convenience when the intermediate graph file and individual provenance files are not required as separate artifacts.

Use `create_bottom_line_graph.py` and `create_bottom_line_dapper.py` separately when:

- you want to inspect or version the full graph before export
- you want to reuse the graph for multiple downstream exporters
- you want to debug graph construction independently from document export
- you want file generation without database loading

Use this one-step script when:

- you want SQLite loading as the primary output
- the graph is only an intermediate representation
- a single command is easier for pipeline automation
- writing individual provenance files should be optional instead of mandatory

## Current Limitations

The script intentionally delegates graph construction and DAPPER section mapping to the existing scripts.

Known limitations:

- it does not create or migrate the SQLite schema
- it does not compute file checksums, sizes, or object version metadata
- it only exports provenance for open-data endpoints under `s3://dig-open-bottom-line-analysis-stg/`
- it overwrites matching output filenames if `--save-provenance-files` is used against an existing output directory
- running identity minting and linting through `uv run` for every endpoint is correct but can be slow for the full endpoint set
