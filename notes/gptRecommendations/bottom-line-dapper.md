# Applying The DAPPER Schema To The `bottom-line` Pipeline

## Scope

This note describes how to apply the DAPPER schema in [dapper.yaml](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1) to the `bottom-line` pipeline in `/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line`.

The goal is to represent `bottom-line` outputs as citable, retrievable, provenance-linked resources without forcing the pipeline to store all operational state directly in DAPPER-native form.

## Bottom-Line Summary

The `bottom-line` method is a stage-based EMR pipeline that:

1. Partitions variants by ancestry and rarity.
2. Runs ancestry-specific meta-analysis.
3. Runs trans-ethnic meta-analysis.
4. Produces alternate result families such as `min_p`, `naive`, `largest`, and clumped outputs.
5. Publishes selected outputs to open-data S3.

The method order is defined in [BottomLine.scala](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/scala/BottomLine.scala:20), and the high-level analysis behavior is described in [README.md](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/README.md:1).

## Recommended DAPPER Modeling Strategy

Use DAPPER as a publication and exchange model for `bottom-line` artifacts.

Use the provenance app’s internal database for:

- run state
- retries
- local queueing
- operational logs
- fine-grained internal event capture

Use DAPPER objects for:

- final result products
- selected intermediate artifacts worth preserving
- artifact identity and versioning
- stage provenance
- file/object retrieval metadata

## Core Class Mapping

### 1. Final result products -> `Dataset`

Use `Dataset` for logical `bottom-line` result products, especially those intended for retrieval, handoff, publication, or downstream use.

Relevant schema class:

- [Dataset](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:721)

Recommended `bottom-line` `Dataset` examples:

- one ancestry-specific result per `phenotype + ancestry`
- one trans-ethnic result per `phenotype`
- one open-data published result per `phenotype + ancestry`
- optional `min_p`, `naive`, `largest`, and clumped derived result datasets when those outputs are retained as distinct products

Recommended `Dataset` fields:

- `id`
- `name`
- `version`
- `resource_type`
- `description`
- `access_level`
- `was_generated_by`
- `was_derived_from`
- `has_drs_object`

### 2. Stage executions -> `Activity`

Use `Activity` for each meaningful pipeline step that produces or transforms artifacts.

Relevant schema class:

- [Activity](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1253)

Recommended `Activity` examples:

- `PartitionStage`
- `AncestrySpecificStage`
- `LoadAncestrySpecificStage`
- `TransEthnicStage`
- `LoadTransEthnicStage`
- `MinPStage`
- `MinPTransEthnicStage`
- `NaiveStage`
- `NaiveTransEthnicStage`
- `LargestStage`
- `ClumpedPlinkStage`
- `ClumpedMergeStage`
- `ClumpedAssociationsStage`
- `OpenDataTransferStage`

Recommended `Activity` fields:

- `id`
- `name`
- `activity_type`
- `description`
- `command`
- `observed_command`
- `entrypoint`
- `repo_url`
- `code_version`
- `container_image`
- `software_name`
- `software_version`
- `generated_at_time`

### 3. Retrievable files and object payloads -> `DrsObject`

Use `DrsObject` for concrete retrievable payloads.

Relevant schema class:

- [DrsObject](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1587)

Recommended `DrsObject` uses in `bottom-line`:

- manifest-backed Spark JSON output trees
- gzipped open-data sumstats exports
- retained TSV, JSON, or archive outputs

Recommended `DrsObject` fields:

- `drs_id`
- `self_uri`
- `size`
- `checksum_sha256`
- `mime_type`
- `access_methods`

For multi-file Spark outputs, treat the output directory as a logical artifact and attach a manifest object to DRS rather than pretending the S3 prefix is a single file.

### 4. Selected intermediate or manifest-backed files -> `C2M2File`

Use `C2M2File` when you want explicit file-level nodes for:

- stage outputs before publication
- input manifests
- intermediate JSON or CSV outputs
- manifest files describing Spark output directories

Relevant schema class:

- [C2M2File](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1648)

This is especially useful if you want provenance edges from an `Activity` to exact input or output files.

### 5. Gene-set-style `Set` is not the primary model here

Do not use `Set` as the primary representation of standard `bottom-line` result tables.

Relevant schema class:

- [Set](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:855)

`Set` is appropriate for member collections such as gene sets. `bottom-line` outputs are better treated as `Dataset` resources with attached file objects and provenance.

## Provenance Edge Mapping

### `Used`

Use `Used` for inputs consumed by an `Activity`.

Relevant schema edge:

- [Used](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1952)

Examples:

- `LoadTransEthnicStage` used ancestry-specific result datasets
- `PartitionStage` used raw variant inputs
- `OpenDataTransferStage` used trans-ethnic or ancestry-specific outputs plus derived `min_p` or `largest` outputs

Use `edge_role` to distinguish:

- `data_input`
- `metadata_input`

### `WasGeneratedBy`

Use `WasGeneratedBy` for outputs generated by an `Activity`.

Relevant schema edge:

- [WasGeneratedBy](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1943)

Examples:

- trans-ethnic result dataset was generated by `LoadTransEthnicStage`
- ancestry-specific result dataset was generated by `LoadAncestrySpecificStage`
- open-data sumstats object was generated by `OpenDataTransferStage`

### `WasDerivedFrom`

Use `WasDerivedFrom` for higher-level lineage between result products.

Relevant schema edge:

- [WasDerivedFrom](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1938)

Examples:

- trans-ethnic dataset derived from ancestry-specific datasets
- clumped dataset derived from trans-ethnic dataset
- open-data export derived from trans-ethnic and helper result families such as `min_p` and `largest`

## Recommended Bottom-Line Artifact Granularity

Use three layers of granularity.

### Layer 1: logical result product

Represent each important result family as a `Dataset`.

Examples:

- `bottom-line/trans-ethnic/T2D`
- `bottom-line/ancestry-specific/T2D/EU`
- `bottom-line/open-data/T2D/Mixed`

### Layer 2: concrete retrievable payload

Represent the bytes behind each logical result as one or more `DrsObject` records.

Examples:

- manifest for `s3://.../out/metaanalysis/bottom-line/trans-ethnic/T2D/`
- `s3://dig-open-bottom-line-analysis-stg/bottom-line/Mixed/T2D.sumstats.tsv.gz`

### Layer 3: optional file-level detail

Represent important files or manifests as `C2M2File` when file-level provenance matters.

Examples:

- Spark output manifest JSON
- publish manifest TSV
- generated METAL table

## Recommended Resource Boundaries For `bottom-line`

### Final outputs that should always be modeled

- final ancestry-specific results
- final trans-ethnic results
- open-data published sumstats outputs

### Derived outputs that should usually be modeled

- `min_p`
- `naive`
- `largest`
- clumped outputs

### Intermediate outputs that can be optional

- partitioned variant outputs
- staging METAL outputs
- temporary merge files

If storage or complexity is a concern, keep DAPPER focused on final and retained artifacts, not every transient file.

## Proposed Stage-To-DAPPER Mapping

### `PartitionStage`

Source:

- [PartitionStage.scala](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/scala/PartitionStage.scala:8)
- [partitionVariants.py](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/resources/partitionVariants.py:1)

Recommended mapping:

- one `Activity` per `dataset + phenotype`
- optional `C2M2File` or `Dataset` for partitioned output prefix
- `Used` edges to raw variant inputs
- `WasGeneratedBy` for partitioned outputs

### `LoadAncestrySpecificStage`

Source:

- [LoadAncestrySpecificStage.scala](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/scala/LoadAncestrySpecificStage.scala:8)
- [loadAnalysis.py](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/resources/loadAnalysis.py:156)

Recommended mapping:

- one `Activity` per `phenotype + ancestry`
- one ancestry-specific `Dataset`
- one `DrsObject` pointing to the persisted output manifest or file object
- `Used` edges to staging tables and rare variant inputs
- `WasGeneratedBy` from output dataset to the load activity

### `LoadTransEthnicStage`

Source:

- [LoadTransEthnicStage.scala](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/scala/LoadTransEthnicStage.scala:8)
- [loadAnalysis.py](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/resources/loadAnalysis.py:216)

Recommended mapping:

- one `Activity` per `phenotype`
- one trans-ethnic `Dataset`
- one `DrsObject` for the final persisted payload
- `Used` edges to ancestry-specific datasets and mixed-ancestry inputs
- `WasDerivedFrom` edges from the final dataset to ancestry-specific datasets

### `OpenDataTransferStage`

Source:

- [OpenDataTransferStage.scala](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/scala/OpenDataTransferStage.scala:7)
- [openDataTransfer.py](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/resources/openDataTransfer.py:1)

Recommended mapping:

- one `Activity` per `phenotype + ancestry`
- one published `Dataset`
- one `DrsObject` for the `.sumstats.tsv.gz` object in S3
- `Used` edges to source result dataset, `min_p`, and optionally `largest`
- `WasGeneratedBy` from published dataset to the publication activity
- `WasDerivedFrom` from published dataset to the internal source dataset

## Recommended Identifier Strategy

### `Dataset.id`

Use stable logical identifiers scoped by result family and version.

Examples:

- `doi:10.0000/bottom-line.trans-ethnic.T2D.v1`
- `doi:10.0000/bottom-line.ancestry-specific.T2D.EU.v1`
- `doi:10.0000/bottom-line.opendata.T2D.Mixed.v1`

If no DOI exists, use an internal stable URI or CURIE namespace under your provenance system.

### `Activity.id`

Use content-addressed or deterministic IDs based on:

- pipeline name
- stage name
- phenotype
- ancestry if applicable
- code version
- command

### `DrsObject.drs_id`

Use stable IDs per materialized payload version.

Examples:

- `bottom-line-t2d-transethnic-v1`
- `bottom-line-t2d-eu-v1`
- `bottom-line-t2d-mixed-open-v1`

## Recommended File Strategy For Spark Outputs

Many `bottom-line` outputs are directory-like Spark results rather than single files.

Do not model an S3 prefix as if it were one file object. Instead:

1. Generate a manifest describing the objects under the prefix.
2. Compute a manifest hash.
3. Represent the manifest as the `DrsObject` payload or as a `C2M2File`.
4. Attach the logical `Dataset` to that manifest-backed object.

This keeps DRS usage honest and gives stable versioning for multi-file results.

## Minimal Example: Trans-Ethnic Result

Below is a minimal conceptual mapping for a trans-ethnic phenotype result.

```yaml
dataset:
  id: dapper:dataset.bottom_line.transethnic.T2D.v1
  name: Bottom-line trans-ethnic meta-analysis for T2D
  version: "1"
  resource_type: Dataset
  description: Final trans-ethnic bottom-line result for phenotype T2D
  access_level: controlled
  was_generated_by: dapper:activity.bottom_line.load_transethnic.T2D.v1
  was_derived_from:
    - dapper:dataset.bottom_line.ancestry_specific.T2D.EU.v1
    - dapper:dataset.bottom_line.ancestry_specific.T2D.HS.v1
  has_drs_object:
    - dapper:drs.bottom_line.transethnic.T2D.v1

activity:
  id: dapper:activity.bottom_line.load_transethnic.T2D.v1
  name: LoadTransEthnicStage T2D
  activity_type: bottom-line-load-trans-ethnic
  command: python loadAnalysis.py --trans-ethnic --phenotype T2D
  entrypoint: loadAnalysis.py
  repo_url: https://github.com/.../MethodsBioindex
  code_version: <git-sha>
  container_image: <emr-image-or-runtime-id>
  generated_at_time: 2026-08-14T12:00:00Z

drs_object:
  id: dapper:drs.bottom_line.transethnic.T2D.v1
  drs_id: bottom-line-t2d-transethnic-v1
  self_uri: drs://bottom-line/bottom-line-t2d-transethnic-v1
  size: 123456789
  checksum_sha256: <manifest-sha256>
  mime_type: application/x-ndjson
  access_methods:
    - s3

used_edges:
  - subject: dapper:activity.bottom_line.load_transethnic.T2D.v1
    predicate: prov:used
    object: dapper:dataset.bottom_line.ancestry_specific.T2D.EU.v1
    edge_role: data_input
  - subject: dapper:activity.bottom_line.load_transethnic.T2D.v1
    predicate: prov:used
    object: dapper:dataset.bottom_line.ancestry_specific.T2D.HS.v1
    edge_role: data_input
```

## Recommended Implementation Pattern

Inside the provenance application:

1. Keep operational run tracking in SQLite.
2. Build DAPPER-exportable records for retained artifacts.
3. Emit DAPPER `Dataset`, `Activity`, `DrsObject`, and provenance edges after successful artifact registration.
4. Persist those as JSON or YAML documents and optionally expose them through the Flask API.

This gives you:

- operational flexibility internally
- standards-oriented export externally
- a clean mapping from `bottom-line` outputs to citable provenance objects

## Bottom-Line Recommendation

For `bottom-line`, use DAPPER primarily as the external artifact and lineage model.

The best default mapping is:

- `Dataset` for logical result products
- `Activity` for each pipeline stage execution
- `DrsObject` for retrievable payloads
- `C2M2File` for optional file-level detail
- `Used`, `WasGeneratedBy`, and `WasDerivedFrom` for lineage

That model is expressive enough for final `bottom-line` results, open-data exports, and selected derived outputs without forcing row-level or transient-file overmodeling.
