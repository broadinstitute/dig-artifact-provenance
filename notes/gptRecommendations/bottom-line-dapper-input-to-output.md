# Bottom-Line DAPPER Input-To-Output Provenance Documents

## Scope

This note describes how to implement DAPPER-based provenance documents for `bottom-line` pipeline results using:

- [bottom-line-dapper.md](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/gptRecommendations/bottom-line-dapper.md:1)
- [dapper.yaml](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1)

The focus here is a simplified provenance pattern:

- treat the `bottom-line` pipeline execution as one logical processing block
- represent the input files used by that block
- represent the output result produced by that block
- connect them in one node/edge provenance graph inside a provenance document

This is a better first implementation target than modeling every internal stage in full detail.

## Recommended Provenance Document Shape

For each retained `bottom-line` result, create one provenance document that contains:

1. Input file nodes
2. One pipeline block `Activity` node
3. One output `Dataset` node
4. One output `DrsObject` node
5. Provenance edges connecting the inputs, block, and outputs

At the first implementation pass, the whole `bottom-line` run for one logical output can be modeled as one `Activity` even though the real pipeline has many internal stages.

That gives a graph shaped like:

```text
input file(s) -> pipeline block activity -> output dataset -> output drs object
```

More explicitly:

```text
C2M2File --Used--> Activity --WasGeneratedBy--> Dataset --HasDrsObject--> DrsObject
```

And optionally:

```text
Dataset --WasDerivedFrom--> C2M2File
```

if you want a direct high-level derivation link in addition to the activity-centered graph.

## Why Use One Block First

The actual `bottom-line` pipeline has many stages, but the user requirement here is to focus on:

- all stages acting as one block
- input files being consumed by that block
- one output result being produced

That maps cleanly to DAPPER:

- `Activity` represents the whole pipeline block ([Activity](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1253))
- `C2M2File` represents the input files ([C2M2File](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1650))
- `Dataset` represents the logical result ([Dataset](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:721))
- `DrsObject` represents the retrievable output payload ([DrsObject](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1587))
- `Used` and `WasGeneratedBy` represent provenance edges ([Used](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1951), [WasGeneratedBy](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1942))

## Recommended Node Types

### 1. Input nodes: `C2M2File`

Represent each input file or input manifest used by the `bottom-line` block as a `C2M2File`.

Use this for:

- raw input variant files
- input manifests listing S3 objects
- prepared input tables handed into the pipeline block

Recommended fields:

- `id`
- `name`
- `description`
- `filename`
- `persistent_id`
- `local_id`
- `dcc_url` if available
- `drc_url` if available

For `bottom-line`, `local_id` will usually be the most useful locator because it can hold an `s3://` URI or other bucket path.

### 2. Pipeline block node: `Activity`

Represent the entire `bottom-line` processing block for one result as one `Activity`.

This single activity stands in for:

- partitioning
- ancestry-specific processing
- trans-ethnic processing
- result loading
- derived result creation

depending on what output you are documenting.

Recommended fields:

- `id`
- `name`
- `description`
- `activity_type`
- `command`
- `observed_command`
- `entrypoint`
- `repo_url`
- `code_version`
- `container_image`
- `software_name`
- `software_version`
- `generated_at_time`

Recommended naming convention:

- one `Activity` per logical result output
- include phenotype and analysis family in the ID

Examples:

- `dapper:activity.bottom_line.block.transethnic.T2D.v1`
- `dapper:activity.bottom_line.block.ancestry_specific.T2D.EU.v1`
- `dapper:activity.bottom_line.block.opendata.T2D.Mixed.v1`

### 3. Result node: `Dataset`

Represent the logical `bottom-line` result as a `Dataset`.

Examples:

- trans-ethnic result for one phenotype
- ancestry-specific result for one phenotype and ancestry
- open-data published result for one phenotype and ancestry

Recommended fields:

- `id`
- `name`
- `version`
- `resource_type`
- `description`
- `access_level`
- `was_generated_by`
- `was_derived_from`
- `has_drs_object`

Recommended resource types:

- `Dataset` for standard results

### 4. Payload node: `DrsObject`

Represent the actual retrievable result payload as a `DrsObject`.

Use this for:

- a gzipped sumstats file
- a manifest representing a Spark output directory
- a JSON or TSV result file

Recommended fields:

- `id`
- `drs_id`
- `self_uri`
- `size`
- `checksum_sha256`
- `mime_type`
- `access_methods`

## Recommended Edge Types

### 1. Inputs used by the pipeline block: `Used`

Create one `Used` edge from the pipeline block `Activity` to each input `C2M2File`.

Relevant schema:

- [Used](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1951)
- [ProvEdgeRoleEnum](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:306)

Use:

- `edge_role: data_input` for true input data files
- `edge_role: metadata_input` for reference manifests, annotations, or helper files

Graph pattern:

```text
Activity --prov:used--> C2M2File
```

### 2. Output generated by the pipeline block: `WasGeneratedBy`

Create one `WasGeneratedBy` edge from the output `Dataset` to the `Activity`.

Relevant schema:

- [WasGeneratedBy](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1942)

Graph pattern:

```text
Dataset --prov:wasGeneratedBy--> Activity
```

If you also model a materialized file node for the output manifest, that file can also have a `WasGeneratedBy` edge to the same activity.

### 3. Logical derivation of the output from inputs: `WasDerivedFrom`

Optionally create `WasDerivedFrom` edges from the `Dataset` to the input file nodes or to higher-level input datasets.

Relevant schema:

- [WasDerivedFrom](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:1935)

Use this when you want a short direct lineage path without traversing the activity edges.

Graph pattern:

```text
Dataset --prov:wasDerivedFrom--> C2M2File
```

### 4. Output dataset to output payload: `has_drs_object`

Use the `Dataset.has_drs_object` relationship to attach the retrievable object.

Relevant schema:

- [Dataset.has_drs_object](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/provenance/dapper.yaml:824)

Graph pattern:

```text
Dataset --has_drs_object--> DrsObject
```

## What Counts As The Input Files

For this simplified block-level document, input files should mean the files the `bottom-line` process consumes to produce a specific retained output.

Recommended rule:

- only include the files that are semantically required to create the output you are documenting
- do not include every temporary file generated inside the block

Examples by output type:

### Trans-ethnic result

Input files may include:

- ancestry-specific result files or manifests
- mixed-ancestry variant inputs used during final load
- staging METAL outputs if those are retained and considered true inputs to the final load

### Ancestry-specific result

Input files may include:

- raw variant partitions for the phenotype and ancestry
- ancestry-specific staging files
- rare variant files merged into the final output

### Open-data export

Input files may include:

- final trans-ethnic or ancestry-specific result file/manifest
- `min_p` file/manifest
- `largest` file/manifest when present

## Recommended Provenance Document Boundaries

Create one provenance document per retained output artifact family.

Good document boundaries:

- one document per phenotype trans-ethnic result
- one document per phenotype + ancestry ancestry-specific result
- one document per phenotype + ancestry published open-data result

This is simpler than trying to place all phenotypes and all result types into one giant graph document.

## Implementation Pattern

### Pattern A: Result-centric provenance document

For each result:

1. Resolve the exact input files used to produce it.
2. Build `C2M2File` nodes for those inputs.
3. Build one `Activity` node representing the full `bottom-line` block for that result.
4. Build one `Dataset` node representing the logical result.
5. Build one `DrsObject` node representing the stored payload.
6. Add `Used`, `WasGeneratedBy`, and optional `WasDerivedFrom` edges.

This should be the default approach.

### Pattern B: Manifest-backed result document

For Spark directory outputs:

1. Create a manifest of the directory contents.
2. Treat the manifest as the concrete file artifact.
3. Point the `DrsObject` at that manifest-backed representation.
4. Keep the `Dataset` as the logical result product.

This avoids modeling an S3 prefix as if it were one file.

## Recommended Provenance Document Sections

Each markdown or JSON/YAML provenance document should be able to emit these conceptual sections:

### Nodes

- input `C2M2File` nodes
- one `Activity` node
- one output `Dataset` node
- one output `DrsObject` node

### Edges

- `Used` edges from the activity to each input file
- one `WasGeneratedBy` edge from the dataset to the activity
- optional `WasDerivedFrom` edges from the dataset to the inputs
- `has_drs_object` from the dataset to the payload object

## Concrete Graph Template

Below is the recommended graph template for one `bottom-line` output.

```text
[C2M2File input 1] ----Used(data_input)----\
[C2M2File input 2] ----Used(data_input)----- > [Activity bottom_line_block]
[C2M2File input 3] --Used(metadata_input)--/

[Dataset output] ----WasGeneratedBy----> [Activity bottom_line_block]
[Dataset output] ----has_drs_object----> [DrsObject output payload]

Optional:
[Dataset output] ----WasDerivedFrom----> [C2M2File input 1]
[Dataset output] ----WasDerivedFrom----> [C2M2File input 2]
```

## Minimal YAML Example

This example shows one trans-ethnic result produced by one logical `bottom-line` block.

```yaml
nodes:
  - id: dapper:file.bottom_line.input.T2D.eu_manifest.v1
    type: C2M2File
    name: T2D EU ancestry-specific input manifest
    filename: eu_manifest.json
    local_id: s3://example-bucket/out/metaanalysis/bottom-line/ancestry-specific/T2D/ancestry=EU/manifest.json

  - id: dapper:file.bottom_line.input.T2D.hs_manifest.v1
    type: C2M2File
    name: T2D HS ancestry-specific input manifest
    filename: hs_manifest.json
    local_id: s3://example-bucket/out/metaanalysis/bottom-line/ancestry-specific/T2D/ancestry=HS/manifest.json

  - id: dapper:activity.bottom_line.block.transethnic.T2D.v1
    type: Activity
    name: Bottom-line trans-ethnic block for T2D
    activity_type: bottom-line-block
    description: Full bottom-line processing block producing the trans-ethnic T2D result
    command: python loadAnalysis.py --trans-ethnic --phenotype T2D
    entrypoint: loadAnalysis.py
    repo_url: https://github.com/.../MethodsBioindex
    code_version: <git-sha>
    software_name: bottom-line
    generated_at_time: 2026-08-17T12:00:00Z

  - id: dapper:dataset.bottom_line.transethnic.T2D.v1
    type: Dataset
    name: Bottom-line trans-ethnic result for T2D
    version: "1"
    resource_type: Dataset
    description: Final trans-ethnic meta-analysis result for T2D
    access_level: controlled
    was_generated_by: dapper:activity.bottom_line.block.transethnic.T2D.v1
    has_drs_object:
      - dapper:drs.bottom_line.transethnic.T2D.v1

  - id: dapper:drs.bottom_line.transethnic.T2D.v1
    type: DrsObject
    drs_id: bottom-line-t2d-transethnic-v1
    self_uri: drs://bottom-line/bottom-line-t2d-transethnic-v1
    size: 123456789
    checksum_sha256: <manifest-sha256>
    mime_type: application/x-ndjson
    access_methods:
      - s3

edges:
  - type: Used
    subject: dapper:activity.bottom_line.block.transethnic.T2D.v1
    predicate: prov:used
    object: dapper:file.bottom_line.input.T2D.eu_manifest.v1
    edge_role: data_input

  - type: Used
    subject: dapper:activity.bottom_line.block.transethnic.T2D.v1
    predicate: prov:used
    object: dapper:file.bottom_line.input.T2D.hs_manifest.v1
    edge_role: data_input

  - type: WasGeneratedBy
    subject: dapper:dataset.bottom_line.transethnic.T2D.v1
    predicate: prov:wasGeneratedBy
    object: dapper:activity.bottom_line.block.transethnic.T2D.v1

  - type: WasDerivedFrom
    subject: dapper:dataset.bottom_line.transethnic.T2D.v1
    predicate: prov:wasDerivedFrom
    object: dapper:file.bottom_line.input.T2D.eu_manifest.v1

  - type: WasDerivedFrom
    subject: dapper:dataset.bottom_line.transethnic.T2D.v1
    predicate: prov:wasDerivedFrom
    object: dapper:file.bottom_line.input.T2D.hs_manifest.v1
```

## Recommended ID Conventions

Use deterministic IDs so the same logical artifact maps to the same node identity.

Recommended shapes:

- input file node: `dapper:file.bottom_line.input.<result-family>.<phenotype>.<input-name>.v<version>`
- activity node: `dapper:activity.bottom_line.block.<result-family>.<phenotype>[.<ancestry>].v<version>`
- dataset node: `dapper:dataset.bottom_line.<result-family>.<phenotype>[.<ancestry>].v<version>`
- drs node: `dapper:drs.bottom_line.<result-family>.<phenotype>[.<ancestry>].v<version>`

## Recommended First Implementation

Start with three result document types:

1. ancestry-specific result document
2. trans-ethnic result document
3. open-data published result document

For each of those:

- identify the exact inputs
- produce one activity node
- produce one dataset node
- produce one drs object node
- emit the edges

That is enough to establish a clean provenance graph from input files to published `bottom-line` outputs.

## Bottom-Line Recommendation

The first DAPPER provenance document implementation for `bottom-line` should be block-centric and result-centric:

- inputs are `C2M2File` nodes
- the whole `bottom-line` processing path for one result is one `Activity`
- the logical result is one `Dataset`
- the stored payload is one `DrsObject`
- the provenance graph is built from `Used`, `WasGeneratedBy`, optional `WasDerivedFrom`, and `has_drs_object`

This gives a simple, understandable input-to-output provenance document now, while leaving room to later expand the single block into full per-stage provenance if needed.
