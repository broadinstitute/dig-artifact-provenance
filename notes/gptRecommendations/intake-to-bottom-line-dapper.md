# Intake-To-Bottom-Line DAPPER Provenance Documents

## Scope

This note extends the block-level DAPPER approach in [bottom-line-dapper-input-to-output.md](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/gptRecommendations/bottom-line-dapper-input-to-output.md:1) to cover the full path from raw input variants through `intake` and into `bottom-line`.

It uses example input and output locations like:

- input: `s3://dig-analysis-data/variants_raw/GWAS/Agrawal2022_LocalAdiposity_Mixed_females/GFAT/`
- published output root: `s3://dig-open-bottom-line-analysis-stg/bottom-line/AA/`

This note assumes the provenance document may need to represent both:

- the `intake` pipeline block
- the `bottom-line` pipeline block

The goal is to produce a DAPPER provenance graph that traces:

```text
raw input files -> intake block -> intake outputs -> bottom-line block -> final published outputs
```

## Why Intake Belongs In The Graph

`bottom-line` consumes `variants/...`, not `variants_raw/...` directly. In practice, those `variants/...` artifacts are produced by the `intake` pipeline.

So if you want full input-to-output provenance from the raw source files, the graph should include:

1. raw variant input files
2. one `intake` activity block
3. one or more intake-produced files or datasets
4. one `bottom-line` activity block
5. one logical `bottom-line` output dataset
6. one published output `DrsObject`

This gives a two-block graph instead of a one-block graph.

## Recommended Provenance Document Shape

For each retained final `bottom-line` result, create one provenance document containing:

1. Raw input `C2M2File` nodes from `variants_raw/...`
2. One `Activity` node for the `intake` block
3. One or more intake output `C2M2File` nodes or one intake output `Dataset`
4. One `Activity` node for the `bottom-line` block
5. One final `Dataset` node for the logical result
6. One final `DrsObject` node for the retrievable or published output
7. Provenance edges connecting the whole chain

The graph shape becomes:

```text
raw input file(s)
  -> intake activity
  -> intake output artifact(s)
  -> bottom-line activity
  -> bottom-line dataset
  -> drs object
```

More explicitly:

```text
C2M2File(raw) --Used--> Activity(intake)
C2M2File(intake_output) --WasGeneratedBy--> Activity(intake)

Activity(bottom_line) --Used--> C2M2File(intake_output)
Dataset(bottom_line_result) --WasGeneratedBy--> Activity(bottom_line)
Dataset(bottom_line_result) --HasDrsObject--> DrsObject(published_output)
```

## Recommended Node Types

### 1. Raw input files: `C2M2File`

Represent the raw source files under `variants_raw/...` as `C2M2File` nodes.

Example source prefix:

- `s3://dig-analysis-data/variants_raw/GWAS/Agrawal2022_LocalAdiposity_Mixed_females/GFAT/`

Relevant schema:

- [C2M2File](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1650)

Recommended fields:

- `id`
- `name`
- `filename`
- `description`
- `local_id`
- `persistent_id` if available

Recommended `local_id` value:

- the exact `s3://` object URI
- or a manifest URI representing all files under the raw input prefix

For large collections of raw files, use a manifest-backed representation rather than enumerating every object inline in the first implementation.

### 2. Intake block: `Activity`

Represent the full `intake` process for one dataset/phenotype as one `Activity`.

This single activity can stand in for:

- `VariantProcessingStage`
- `VariantQCStage`
- `VariantScalingStage`

Relevant schema:

- [Activity](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1253)

Recommended `activity_type`:

- `intake-block`

Recommended naming:

- `dapper:activity.intake.block.<method>.<dataset>.<phenotype>.v<version>`

Example:

- `dapper:activity.intake.block.GWAS.Agrawal2022_LocalAdiposity_Mixed_females.GFAT.v1`

### 3. Intake outputs: `C2M2File` or `Dataset`

Represent the `intake` output consumed by `bottom-line` as:

- `C2M2File` if you want file-level or manifest-level fidelity
- `Dataset` if you want a higher-level logical representation

For the first implementation, prefer `C2M2File` because `bottom-line` consumes a file artifact space:

- `variants/<method>/<dataset>/<phenotype>/...`

Example:

- `s3://dig-analysis-data/variants/GWAS/Agrawal2022_LocalAdiposity_Mixed_females/GFAT/`

### 4. Bottom-line block: `Activity`

Represent the full `bottom-line` process for one retained result as one `Activity`.

This block stands in for all internal stages needed to produce the documented output.

Relevant schema:

- [Activity](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1253)

Recommended `activity_type` values:

- `bottom-line-block-ancestry-specific`
- `bottom-line-block-transethnic`
- `bottom-line-block-opendata`

### 5. Final result: `Dataset`

Represent the logical `bottom-line` result as a `Dataset`.

Relevant schema:

- [Dataset](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:721)

Examples:

- final ancestry-specific result for `GFAT` and `AA`
- final trans-ethnic result for `GFAT`
- published open-data result for `GFAT` and `AA`

### 6. Published payload: `DrsObject`

Represent the final retrievable output as a `DrsObject`.

Relevant schema:

- [DrsObject](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1587)

Example published prefix:

- `s3://dig-open-bottom-line-analysis-stg/bottom-line/AA/`

Example object:

- `s3://dig-open-bottom-line-analysis-stg/bottom-line/AA/GFAT.sumstats.tsv.gz`

## Recommended Edge Types

### Raw inputs into intake: `Used`

Create `Used` edges from the intake `Activity` to raw input `C2M2File` nodes.

Relevant schema:

- [Used](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1951)

Use:

- `edge_role: data_input`

### Intake outputs generated by intake: `WasGeneratedBy`

Create `WasGeneratedBy` edges from the intake output artifacts to the intake activity.

Relevant schema:

- [WasGeneratedBy](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1942)

### Intake outputs used by bottom-line: `Used`

Create `Used` edges from the `bottom-line` activity to the intake output artifact nodes.

This is the key bridge between the two pipelines.

### Bottom-line result generated by bottom-line: `WasGeneratedBy`

Create a `WasGeneratedBy` edge from the final `Dataset` to the `bottom-line` activity.

### Final result derived from intake outputs: `WasDerivedFrom`

Optionally create `WasDerivedFrom` edges from the final `Dataset` to the intake-produced file artifacts.

This provides a direct top-level lineage path from `bottom-line` results back to `intake`.

### Final result payload attachment: `has_drs_object`

Attach the final retrievable object using `Dataset.has_drs_object`.

## Recommended Artifact Boundaries

### Raw input artifact

For:

- `s3://dig-analysis-data/variants_raw/GWAS/Agrawal2022_LocalAdiposity_Mixed_females/GFAT/`

Represent either:

- one manifest `C2M2File` for the whole prefix
- or one `C2M2File` per actual object if you need object-level provenance

Recommended first pass:

- one manifest `C2M2File` per raw input prefix

### Intake output artifact

For:

- `s3://.../variants/GWAS/Agrawal2022_LocalAdiposity_Mixed_females/GFAT/`

Represent either:

- one manifest `C2M2File` for the scaled output prefix
- or one logical `Dataset` plus manifest

Recommended first pass:

- one manifest-backed `C2M2File` representing the intake output consumed by `bottom-line`

### Bottom-line published output artifact

For:

- `s3://dig-open-bottom-line-analysis-stg/bottom-line/AA/`

Represent:

- one final logical `Dataset`
- one `DrsObject` for the published output file or file family

Recommended first pass:

- one `DrsObject` per published phenotype/ancestry output

## Recommended Graph Patterns

### Pattern A: Full intake-to-bottom-line chain

Use when you want true raw-input provenance.

```text
raw input file(s)
  -> intake block
  -> intake output artifact
  -> bottom-line block
  -> final bottom-line dataset
  -> final drs object
```

### Pattern B: Intake output as the effective bottom-line input

Use when the raw input layer is too detailed or not yet available.

```text
intake output artifact
  -> bottom-line block
  -> final bottom-line dataset
  -> final drs object
```

This is still valid DAPPER provenance, but it starts lineage at the post-intake boundary.

## Minimal Example With Your S3 Paths

The following example shows one raw input prefix, one intake block, one intake output artifact, one bottom-line block, and one published AA output.

```yaml
nodes:
  - id: dapper:file.variants_raw.GWAS.Agrawal2022_LocalAdiposity_Mixed_females.GFAT.v1
    type: C2M2File
    name: Raw GWAS GFAT input manifest
    filename: manifest.json
    description: Manifest for raw input files under the GFAT prefix
    local_id: s3://dig-analysis-data/variants_raw/GWAS/Agrawal2022_LocalAdiposity_Mixed_females/GFAT/

  - id: dapper:activity.intake.block.GWAS.Agrawal2022_LocalAdiposity_Mixed_females.GFAT.v1
    type: Activity
    name: Intake block for GWAS Agrawal2022 LocalAdiposity Mixed females GFAT
    activity_type: intake-block
    description: Variant processing, QC, and scaling for the GFAT raw inputs
    command: sbt intake run GWAS/Agrawal2022_LocalAdiposity_Mixed_females/GFAT
    repo_url: https://github.com/.../MethodsBioindex
    code_version: <git-sha>
    software_name: intake
    generated_at_time: 2026-08-17T12:00:00Z

  - id: dapper:file.variants.GWAS.Agrawal2022_LocalAdiposity_Mixed_females.GFAT.v1
    type: C2M2File
    name: Intake-scaled GFAT variant manifest
    filename: manifest.json
    description: Manifest for intake output consumed by bottom-line
    local_id: s3://dig-analysis-data/variants/GWAS/Agrawal2022_LocalAdiposity_Mixed_females/GFAT/

  - id: dapper:activity.bottom_line.block.AA.GFAT.v1
    type: Activity
    name: Bottom-line AA block for GFAT
    activity_type: bottom-line-block-opendata
    description: Bottom-line processing and publication for AA GFAT output
    command: python openDataTransfer.py --phenotype=GFAT --ancestry=AA
    repo_url: https://github.com/.../MethodsBioindex
    code_version: <git-sha>
    software_name: bottom-line
    generated_at_time: 2026-08-17T13:00:00Z

  - id: dapper:dataset.bottom_line.opendata.AA.GFAT.v1
    type: Dataset
    name: Bottom-line AA published result for GFAT
    version: "1"
    resource_type: Dataset
    description: Published AA bottom-line result for GFAT
    access_level: public
    was_generated_by: dapper:activity.bottom_line.block.AA.GFAT.v1
    was_derived_from:
      - dapper:file.variants.GWAS.Agrawal2022_LocalAdiposity_Mixed_females.GFAT.v1
    has_drs_object:
      - dapper:drs.bottom_line.opendata.AA.GFAT.v1

  - id: dapper:drs.bottom_line.opendata.AA.GFAT.v1
    type: DrsObject
    drs_id: bottom-line-aa-gfat-v1
    self_uri: drs://bottom-line/bottom-line-aa-gfat-v1
    size: 12345678
    checksum_sha256: <sha256>
    mime_type: text/tab-separated-values
    access_methods:
      - s3

edges:
  - type: Used
    subject: dapper:activity.intake.block.GWAS.Agrawal2022_LocalAdiposity_Mixed_females.GFAT.v1
    predicate: prov:used
    object: dapper:file.variants_raw.GWAS.Agrawal2022_LocalAdiposity_Mixed_females.GFAT.v1
    edge_role: data_input

  - type: WasGeneratedBy
    subject: dapper:file.variants.GWAS.Agrawal2022_LocalAdiposity_Mixed_females.GFAT.v1
    predicate: prov:wasGeneratedBy
    object: dapper:activity.intake.block.GWAS.Agrawal2022_LocalAdiposity_Mixed_females.GFAT.v1

  - type: Used
    subject: dapper:activity.bottom_line.block.AA.GFAT.v1
    predicate: prov:used
    object: dapper:file.variants.GWAS.Agrawal2022_LocalAdiposity_Mixed_females.GFAT.v1
    edge_role: data_input

  - type: WasGeneratedBy
    subject: dapper:dataset.bottom_line.opendata.AA.GFAT.v1
    predicate: prov:wasGeneratedBy
    object: dapper:activity.bottom_line.block.AA.GFAT.v1

  - type: WasDerivedFrom
    subject: dapper:dataset.bottom_line.opendata.AA.GFAT.v1
    predicate: prov:wasDerivedFrom
    object: dapper:file.variants.GWAS.Agrawal2022_LocalAdiposity_Mixed_females.GFAT.v1
```

## Recommended First Implementation

Start with one document per published output under:

- `s3://dig-open-bottom-line-analysis-stg/bottom-line/<ancestry>/`

For each such output:

1. Identify the corresponding `bottom-line` logical result.
2. Identify the intake-produced `variants/...` input prefix that fed it.
3. Identify the raw `variants_raw/...` prefix that fed intake.
4. Create:
   - one raw input `C2M2File`
   - one intake `Activity`
   - one intake output `C2M2File`
   - one bottom-line `Activity`
   - one final `Dataset`
   - one final `DrsObject`
5. Emit the connecting edges.

This gives a clean provenance chain from raw input S3 to published bottom-line S3.

## Bottom-Line Recommendation

For full `intake` to `bottom-line` provenance, use a two-block DAPPER graph:

- raw input S3 prefixes as `C2M2File`
- `intake` as one `Activity`
- `variants/...` intake outputs as `C2M2File`
- `bottom-line` as one `Activity`
- final logical result as `Dataset`
- final published output as `DrsObject`

That is the simplest correct representation of:

```text
s3://dig-analysis-data/variants_raw/... -> intake -> variants/... -> bottom-line -> s3://dig-open-bottom-line-analysis-stg/bottom-line/...
```
