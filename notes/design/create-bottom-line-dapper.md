# `create_bottom_line_dapper.py`

## Purpose

[`src/python/create_bottom_line_dapper.py`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/src/python/create_bottom_line_dapper.py) exports DAPPER-oriented provenance JSON documents for published `bottom-line` outputs.

The script takes a previously generated bottom-line provenance graph and produces one provenance document per open-data endpoint under:

- `s3://dig-open-bottom-line-analysis-stg/`

Each output JSON captures:

- the published endpoint artifact
- the upstream pipeline stages that generated it
- the intermediate files and datasets in its provenance subgraph
- the provenance edges linking those objects together

The script is intended as a graph-to-document export layer. It does not infer pipeline structure directly from Scala code or from S3 listings at runtime. Instead, it relies on the graph produced earlier by [`create_bottom_line_graph.py`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/src/python/create_bottom_line_graph.py).

## Inputs And Outputs

### Input

The script requires:

- `--in-graph-file`

This file must be a JSON graph with top-level `nodes` and `edges` arrays, consistent with [`data/graph/provenance_graph.json`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/data/graph/provenance_graph.json).

### Output

The script writes one JSON file per published open-data endpoint into:

- `data/bottom-line-provenance`

The output filename is derived from the endpoint node `location_path`:

1. remove the `s3://` prefix
2. replace `/` with `_`
3. append `.json`

Example:

- `s3://dig-open-bottom-line-analysis-stg/bottom-line/AA/2hrGadjBMI.sumstats.tsv.gz`
- `dig-open-bottom-line-analysis-stg_bottom-line_AA_2hrGadjBMI.sumstats.tsv.gz.json`

## Command-Line Interface

The script supports:

```bash
python3 src/python/create_bottom_line_dapper.py \
  --in-graph-file data/graph/provenance_graph.json
```

Optional argument:

- `--out-dir`

Default output directory:

- [`data/bottom-line-provenance`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/data/bottom-line-provenance)

## Selection Logic

The script selects root artifacts from graph nodes that satisfy both conditions:

- `directory_kind == "open_data_endpoint"`
- `location_path` starts with `s3://dig-open-bottom-line-analysis-stg/`

Each matching node becomes the root of a separate provenance document.

This design keeps the export focused on the open-data publication boundary rather than on every intermediate file in the full pipeline graph.

## Provenance Traversal

For each selected root node, the script walks the graph backward through outgoing edges whose relationship is one of:

- `WasGeneratedBy`
- `WasDerivedFrom`
- `Used`

This matches the graph orientation currently used in the bottom-line graph:

- an output artifact points to the stage that generated it through `WasGeneratedBy`
- a stage points to its consumed inputs through `Used`
- a derived artifact can point to an upstream artifact or stage through `WasDerivedFrom`

The traversal collects:

- all reachable upstream node ids
- all traversed edge ids

Those nodes and edges form the provenance subgraph for a single published endpoint.

## DAPPER Mapping

The export uses the graph node field `dapper_class` to place graph nodes into DAPPER-style sections.

### `Dataset`

Nodes marked `dapper_class == "Dataset"` are exported into the `datasets` array.

Mapped fields include:

- `id`
- `name`
- `resource_type`
- `description`
- `access_level`
- `location_path`
- `phenotype`
- `ancestry`
- `annotation_source`
- `was_generated_by`

Access level is inferred as:

- `public` for `open_data_endpoint`
- `controlled` otherwise

### `DrsObject`

Nodes marked `dapper_class == "DrsObject"` are exported into the `drs_objects` array.

Mapped fields include:

- `id`
- `drs_id`
- `self_uri`
- `mime_type`
- `access_methods`
- `location_path`
- `description`
- `published_filename`
- `annotation_source`
- `was_generated_by`

`drs_id` is derived from the S3 location path by removing `s3://` and replacing `/` with `_`.

Current MIME type logic is simple:

- `.tsv.gz` -> `text/tab-separated-values`
- otherwise -> `application/octet-stream`

### `Activity`

Nodes marked `dapper_class == "Activity"` are exported into the `activities` array.

Mapped fields include:

- `id`
- `name`
- `activity_type`
- `description`
- `repo_url`
- `stage_group`
- `phenotype`
- `ancestry`
- `dataset`
- `method`
- `annotation_source`

For stage nodes, `location_path` is currently used as the code or repository reference.

### `C2M2File`

Nodes marked `dapper_class == "C2M2File"` are exported into the `c2m2_files` array.

Mapped fields include:

- `id`
- `name`
- `description`
- `filename`
- `local_id`
- `location_path`
- `directory_kind`
- `phenotype`
- `ancestry`
- `dataset`
- `method`
- `rare`
- `annotation_source`
- `was_generated_by`

This section is useful for intermediate files, input directories, and non-published file-like artifacts that still need explicit provenance representation.

### Edges

The subgraph edges are exported into an `edges` array with:

- `id`
- `source`
- `target`
- `relationship`
- `predicate`
- `annotation_source`
- optional `edge_role`
- optional `description`

This preserves the lineage structure separately from the denormalized DAPPER sections.

## Output Document Structure

Each JSON document contains:

- `reference_graph_file`
- `recommendation_reference`
- `root_node_id`
- `root_location_path`
- `datasets`
- `drs_objects`
- `activities`
- `c2m2_files`
- `edges`
- `graph`

The `graph` object includes the raw subgraph:

- `graph.nodes`
- `graph.edges`

This dual structure is intentional:

- the DAPPER-like arrays make downstream consumption simpler
- the embedded raw subgraph preserves complete local provenance context

## Design Rationale

### Why export one document per published endpoint

The publication unit for bottom-line open data is the individual endpoint file in the open-data bucket. Exporting one document per endpoint makes it easier to:

- register or store provenance records independently
- map a published artifact to a single provenance payload
- load provenance into SQLite or a service layer by artifact id
- evolve provenance without reprocessing the entire graph

### Why derive from the graph instead of rebuilding lineage here

The graph script already centralizes knowledge about:

- bottom-line stage structure
- intake-to-bottom-line dependencies
- S3 directory annotations
- node and edge semantics

Keeping `create_bottom_line_dapper.py` as a pure exporter avoids duplicating graph-construction logic and keeps the DAPPER transformation isolated.

### Why keep raw graph data in the output

The recommendation file in [`notes/gptRecommendations/bottom-line-dapper.md`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/gptRecommendations/bottom-line-dapper.md) describes DAPPER as an exchange and publication representation rather than the full operational persistence model.

Including the raw subgraph allows downstream tools to:

- reconstruct lineage paths
- render provenance graphs
- validate stage-to-file relationships
- evolve DAPPER field mappings later without losing graph detail

## Current Limitations

The current implementation is intentionally thin and conservative.

Known limitations:

- it only exports artifacts rooted in `s3://dig-open-bottom-line-analysis-stg/`
- it depends on graph node annotations such as `dapper_class` already being present
- it does not validate against the full YAML DAPPER schema
- it does not compute checksums, object size, timestamps, or content-based versions
- it uses simplified DRS identifiers derived from location paths
- it does not yet emit schema-specific fields such as richer access methods, checksum structures, or version history objects

## Extension Points

The script can be extended in several useful ways.

Potential next steps:

- add JSON Schema or YAML-backed validation for exported documents
- enrich `DrsObject` entries with checksum, size, and version metadata
- add explicit `was_derived_from` references into the DAPPER object sections
- include execution timestamps and code revision metadata on `Activity`
- support exporting intake-rooted or intermediate-only provenance sets
- insert generated documents directly into the SQLite provenance store

## Relationship To The Provenance Service

This script is a document-generation component, not the persistence layer.

A typical flow is:

1. build the bottom-line provenance graph
2. export one DAPPER-oriented document per published endpoint
3. load each JSON document into SQLite, for example into a table like `prov_artifact`
4. expose retrieval through the planned Flask provenance service

That separation aligns with the broader design goal of keeping pipeline changes minimal while still supporting structured artifact provenance capture.
