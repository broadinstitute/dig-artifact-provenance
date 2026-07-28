# Bottom-Line Provenance Recommendations

## Scope

This document summarizes recommendations for a Python provenance application in `/Users/mduby/Code/DccWorkspace/ArtifactProvenance` that can track artifacts produced by the `bottom-line` pipeline in `/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line` and remain reusable for other MethodsBioindex pipelines.

The existing `bottom-line` method is a Scala orchestrator over EMR jobs with Python and shell execution at stage boundaries. The least disruptive design is to add provenance capture around those stage/job boundaries and artifact write points rather than rewriting stage logic.

The preferred architecture is now:

- a thin pip-installable provenance client that pipeline jobs can install at runtime on EMR
- a REST-based provenance server implemented in this repository with Flask and SQLite
- minimal pipeline code changes, ideally wrapper calls or a few explicit observation calls at output boundaries

## Observations From `bottom-line`

The pipeline is stage-oriented in Scala and already has clean execution seams:

- [BottomLine.scala](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/scala/BottomLine.scala:20) defines a serial stage chain.
- [PartitionStage.scala](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/scala/PartitionStage.scala:23) emits a PySpark job from a simple `make()` method.
- [AncestrySpecificStage.scala](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/scala/AncestrySpecificStage.scala:41) and [TransEthnicStage.scala](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/scala/TransEthnicStage.scala:22) submit shell jobs for METAL-oriented analysis.
- [LoadAncestrySpecificStage.scala](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/scala/LoadAncestrySpecificStage.scala:59) and [LoadTransEthnicStage.scala](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/scala/LoadTransEthnicStage.scala:36) call a shared `loadAnalysis.py`.
- [partitionVariants.py](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/resources/partitionVariants.py:21), [loadAnalysis.py](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/resources/loadAnalysis.py:156), [runNaive.py](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/resources/runNaive.py:54), and [openDataTransfer.py](/Users/mduby/Code/ScalaWorkspace/MethodsBioindex/bottom-line/src/main/resources/openDataTransfer.py:37) each have explicit source and output S3 path construction.

This makes provenance collection feasible with:

- stage-level annotations in Scala
- a thin Python client or CLI wrapper around existing Python entrypoints
- optional shell wrappers for script-based stages
- artifact registration after writes to S3
- REST submission to an external provenance service

## Bottom-Line Recommendation

Build the provenance system as two parts:

1. A thin client package, distributable by `pip`, that the pipeline can install at runtime and call with minimal code changes.
2. A Flask REST server in this repository backed by SQLite for persistence and retrieval.
3. Stage metadata expressed through annotations, wrappers, or sidecar manifests.
4. Artifact registration sent over REST from the thin client to the server.
5. GA4GH DRS-compatible IDs minted and resolved by the server.
6. Canonical artifact metadata mirrored to S3 as manifests.

This avoids deep changes to the Scala stage engine, keeps runtime dependencies light for EMR jobs, and makes the provenance layer portable across `bottom-line`, `bioindex`, `pigean`, `ldsc`, and similar modules.

## Proposed Integration Model

### 1. Stage annotations, not stage rewrites

Add minimal metadata to each Scala stage definition, either with:

- a lightweight trait such as `ObservableStage`
- a `Map[String, String]` metadata field
- or a sidecar config file keyed by stage class name

Recommended stage metadata:

- `method_name`
- `stage_name`
- `stage_kind` such as `spark`, `pyspark`, `shell`, `metal`, `plink`
- `logical_inputs`
- `logical_outputs`
- `artifact_family`
- `version_strategy`

If changing the Scala core is undesirable, the lowest-risk option is a sidecar YAML or JSON manifest in this repository that maps:

- `org.broadinstitute.dig.aggregator.methods.bottomline.PartitionStage`
- `...AncestrySpecificStage`
- `...LoadAncestrySpecificStage`
- etc.

to provenance behavior and artifact patterns.

### 2. Thin client plus entry-point wrapping

Wrap existing jobs instead of modifying their internals heavily, and route events through a small client library.

Examples:

- `Job.PySpark(resourceUri("partitionVariants.py"), ...)` becomes a call to a generic provenance launcher that then invokes `partitionVariants.py`.
- Shell stages such as `runAncestrySpecific.sh` and `runTransEthnic.sh` are invoked through a thin wrapper that records run start, run end, exit code, inputs, and outputs.
- Existing Python scripts can import a lightweight client helper and call one or two functions around output writes.

Recommended wrapper behavior:

- create `run_id`
- resolve declared input S3 paths
- collect environment and CLI args
- invoke original command
- enumerate produced artifacts
- compute checksums or manifest hashes
- POST run and artifact events to the REST server
- fall back to buffered local JSON if the server is temporarily unavailable

### 3. Output registration at write boundaries

The most reliable low-touch provenance signal is the existing output path creation in Python scripts.

Examples already present:

- `partitionVariants.py` writes partitioned CSV to `out/metaanalysis/variants/...`
- `loadAnalysis.py` writes final JSON to `out/metaanalysis/bottom-line/...`
- `runNaive.py` writes naive outputs to `out/metaanalysis/naive/...`
- `openDataTransfer.py` writes public sumstats to open-data S3

Instrument these write boundaries with a shared helper like:

```python
from provenance_client.observer_utils import observe_artifact_write

observe_artifact_write(
    logical_name="bottom-line.trans-ethnic",
    s3_uri=outdir,
    artifact_type="spark-json-dataset",
    produced_by_stage="LoadTransEthnicStage",
    run_context=ctx,
)
```

For shell-only stages, capture outputs from declared path patterns in the stage manifest.

The key point is that the pipeline code should not need to know about SQLite or Flask directly. It only talks to the thin client.

## Recommended Python Application Structure

Generate the code under subdirectories of `src/` and split it into two packages:

- `provenance_client` for the thin pip-installable runtime layer
- `provenance_server` for the Flask and SQLite backend

Use function-oriented utility modules as requested.

```text
ArtifactProvenance/
  src/
    provenance_client/
      __init__.py
      config_utils.py
      logging_utils.py
      time_utils.py
      id_utils.py
      annotations_utils.py
      observer_utils.py
      context_utils.py
      file_utils.py
      s3_utils.py
      hash_utils.py
      rest_utils.py
      retry_utils.py
      queue_utils.py
      manifest_utils.py
      json_utils.py
      pipeline_utils.py
      stage_utils.py
      run_utils.py
      artifact_utils.py
      cli_utils.py
      emr_utils.py
    provenance_server/
      __init__.py
      config_utils.py
      logging_utils.py
      time_utils.py
      id_utils.py
      sqlite_utils.py
      schema_utils.py
      migration_utils.py
      drs_utils.py
      manifest_utils.py
      json_utils.py
      sql_utils.py
      web_utils.py
      flask_utils.py
      pipeline_utils.py
      stage_utils.py
      run_utils.py
      artifact_utils.py
      s3_utils.py
      auth_utils.py
      models.py
      app.py
  notes/
    gptRecommendations/
```

Suggested responsibilities:

- `provenance_client/annotations_utils.py`: decorators and annotation parsing
- `provenance_client/observer_utils.py`: run/artifact observation API used by pipeline code
- `provenance_client/rest_utils.py`: REST submission to the provenance server
- `provenance_client/queue_utils.py`: local buffering for retryable event delivery
- `provenance_client/file_utils.py`: local file discovery and metadata
- `provenance_client/s3_utils.py`: S3 stat and manifest capture from runtime jobs
- `provenance_client/pipeline_utils.py`: generic pipeline registration model
- `provenance_client/stage_utils.py`: stage metadata normalization
- `provenance_client/run_utils.py`: client-side run lifecycle helpers
- `provenance_client/artifact_utils.py`: client-side artifact event creation
- `provenance_client/emr_utils.py`: EMR metadata capture from environment and Spark context
- `provenance_server/sqlite_utils.py`: SQLite connection and transaction helpers
- `provenance_server/schema_utils.py`: DDL and schema lifecycle
- `provenance_server/migration_utils.py`: schema evolution
- `provenance_server/drs_utils.py`: DRS object ID generation and response serialization
- `provenance_server/web_utils.py` and `provenance_server/flask_utils.py`: Flask routes and HTTP helpers
- `provenance_server/run_utils.py`: persisted run lifecycle state transitions
- `provenance_server/artifact_utils.py`: artifact version creation and lineage
- `provenance_server/s3_utils.py`: S3 manifest publication and optional access URL generation

## Data Model Recommendation

Use SQLite as the system-of-record on the server side. Keep binary or large content in S3.

Core tables:

- `pipeline`
- `stage`
- `run`
- `run_event`
- `artifact`
- `artifact_version`
- `artifact_location`
- `artifact_lineage`
- `drs_object`
- `tag`

Recommended fields:

### `run`

- `run_id`
- `pipeline_name`
- `method_name`
- `stage_name`
- `parent_run_id`
- `status`
- `started_at`
- `ended_at`
- `exit_code`
- `emr_cluster_id`
- `emr_step_id`
- `spark_app_id`
- `command`
- `args_json`
- `env_json`

### `artifact`

- `artifact_id`
- `logical_name`
- `artifact_type`
- `method_name`
- `stage_name`
- `drs_object_id`
- `current_version_id`

### `artifact_version`

- `artifact_version_id`
- `artifact_id`
- `version_label`
- `content_hash`
- `manifest_hash`
- `size_bytes`
- `format`
- `created_at`
- `created_by_run_id`
- `is_materialized`

### `artifact_location`

- `artifact_location_id`
- `artifact_version_id`
- `uri`
- `uri_type`
- `region`
- `bucket`
- `key`
- `is_primary`

### `artifact_lineage`

- `parent_artifact_version_id`
- `child_artifact_version_id`
- `relationship_type`

Useful relationship types:

- `derived_from`
- `partition_of`
- `merged_from`
- `published_from`
- `replaces`

## DRS Recommendation

Use DRS as the stable external identifier model, not as the first persistence layer.

Recommended approach:

- generate a stable `drs://` style identifier for each logical artifact
- create a new DRS version record for each material artifact version
- store enough metadata in SQLite to serve DRS-compatible responses
- optionally persist DRS JSON documents in S3 for portability

Recommended DRS mapping:

- DRS object = logical artifact
- DRS version = artifact version
- DRS access methods = S3 URIs or pre-signed URL generation strategy

Do not start by implementing the full GA4GH DRS API surface. Start with:

- internal DRS ID minting
- `GetObject`-like JSON representation
- version resolution
- access URL generation for S3

## S3 Recommendation

Use S3 both for artifact payloads and for exported provenance manifests.

Recommended S3 content:

- original pipeline outputs
- artifact manifest JSON files
- optional copied SQLite snapshots for audit/export
- optional per-run event logs

Recommended pattern:

- keep SQLite local to the provenance service or mounted volume
- publish immutable artifact manifests to S3 under a canonical prefix
- use manifest content hash as a version integrity check

Example prefix shape:

```text
s3://<bucket>/provenance/
  runs/<run_id>.json
  artifacts/<artifact_id>/versions/<version_id>.json
  drs/<drs_object_id>.json
```

## Flask Recommendation

Use Flask as the REST server and retrieval surface.

Recommended endpoints:

- `GET /health`
- `GET /runs`
- `GET /runs/<run_id>`
- `GET /artifacts`
- `GET /artifacts/<artifact_id>`
- `GET /artifacts/<artifact_id>/versions`
- `GET /drs/objects/<object_id>`
- `GET /lineage/<artifact_version_id>`
- `POST /api/v1/runs`
- `POST /api/v1/run-events`
- `POST /api/v1/artifacts`
- `POST /api/v1/artifact-versions`

Optional later endpoints:

- `POST /api/v1/batch`
- `POST /api/v1/manifests`

Initially, keep the client API very small. The thin layer should mostly need:

- `start_run(...)`
- `complete_run(...)`
- `fail_run(...)`
- `observe_artifact_write(...)`

## Least-Disruptive Rollout Path

### Phase 1

Implement the server app in this repository only:

- Flask app
- SQLite schema
- DRS ID model
- S3 metadata helpers
- REST write and read API
- thin client package scaffolding under `src/provenance_client`

No MethodsBioindex changes yet beyond analysis.

### Phase 2

Integrate by wrapping only a few `bottom-line` entrypoints with the pip-installable client:

- `partitionVariants.py`
- `loadAnalysis.py`
- `openDataTransfer.py`

This covers partitioned outputs, final analysis outputs, and public publication outputs.

### Phase 3

Add stage manifests for all `bottom-line` stages:

- `PartitionStage`
- `AncestrySpecificStage`
- `LoadAncestrySpecificStage`
- `TransEthnicStage`
- `LoadTransEthnicStage`
- `MinPStage`
- `NaiveStage`
- `LargestStage`
- clumping stages
- `OpenDataTransferStage`

### Phase 4

Generalize the same wrapper and manifest pattern to other MethodsBioindex method directories.

## Generic Reuse Across MethodsBioindex

To keep the app reusable for other pipelines, design around a generic contract:

- a pipeline has stages
- a stage consumes logical inputs and produces logical outputs
- a run executes a command in an environment
- an artifact version is created from a run

Avoid encoding `bottom-line` assumptions into the core package. Put method-specific knowledge in:

- stage manifest files
- per-method adapter modules
- configuration files for path patterns and artifact types

Recommended layout for method-specific adapters:

```text
src/provenance/methods/
  __init__.py
  bottom_line_utils.py
  bioindex_utils.py
  pigean_utils.py
```

## Concrete Hook Points For `bottom-line`

Best initial hook points:

1. Wrap all Python entrypoints under `bottom-line/src/main/resources/`.
2. Add sidecar manifests for shell-only stages such as METAL and PLINK steps.
3. Capture output S3 prefixes at the point of `.write(...)`, `aws s3 cp`, `aws s3 mv`, or generated staging directory creation.

Particularly high-value artifacts:

- `out/metaanalysis/variants/...`
- `out/metaanalysis/bottom-line/ancestry-specific/...`
- `out/metaanalysis/bottom-line/trans-ethnic/...`
- `out/metaanalysis/min_p/...`
- `out/metaanalysis/naive/...`
- `out/metaanalysis/largest/...`
- clumped outputs
- open-data published sumstats

## Recommendation On Observables And Annotations

Use both:

- annotations for static metadata
- observables for runtime events

Recommended pattern:

- decorators for Python entrypoints and helper functions
- manifest-driven metadata for Scala and shell stages
- event hooks for `run_started`, `artifact_written`, `artifact_promoted`, `run_failed`, `run_completed`

Example decorator:

```python
@observable_stage(
    method_name="bottom-line",
    stage_name="LoadTransEthnicStage",
    artifact_type="spark-json-dataset",
)
def load_trans_ethnic_analysis(...):
    ...
```

This is appropriate for new Python code in the provenance client. For existing MethodsBioindex scripts, prefer a wrapper or explicit helper calls over broad decorator retrofits.

## Thin Client Packaging Recommendation

The runtime tracking component should be distributed as a small pip package, for example:

- package name: `dig-provenance-client`
- install target: EMR bootstrap or job runtime install
- dependency profile: minimal, avoiding heavy server-only dependencies

Recommended client characteristics:

- no SQLite dependency
- no Flask dependency
- small dependency surface, ideally `requests` plus AWS SDK support only if needed
- stateless operation except for optional local retry queue
- compatible with Python scripts and shell wrappers

Recommended install options:

- include in EMR bootstrap for broad reuse
- or install at job runtime for minimal platform coupling

## Server Recommendation

The persistence and retrieval server should remain in this repository and expose a REST API backed by Flask and SQLite.

Recommended server characteristics:

- authoritative run and artifact persistence
- DRS object and version resolution
- lineage traversal
- optional S3 manifest publication
- read-heavy API plus small authenticated write surface for the thin client

## Risks And Constraints

- SQLite is strong for metadata and low operational overhead, but not a write-heavy distributed coordination store. Use it for provenance records on the server, not as a cross-cluster lock service.
- S3 directory-like outputs from Spark are multi-file datasets, so versioning should be manifest-based rather than single-object based.
- Some shell stages may produce outputs indirectly, so manifest-driven output declaration will be more reliable than runtime filesystem inference alone.
- EMR retries can create duplicate write attempts; run registration must be idempotent.
- Client-to-server communication can fail transiently on EMR, so the thin client should support buffered retry.
- Checksumming whole Spark output trees can be expensive. Prefer manifest hashes from object listings, ETags where appropriate, and optionally sampled validation.

## Implementation Recommendation

Build the system in this repository as a generic provenance server plus thin client package with:

- manifest-driven stage definitions
- wrapper-based runtime observation
- REST-based client/server communication
- Flask retrieval and write API
- SQLite metadata on the server
- S3-backed artifact manifests
- DRS-compatible object/version IDs
- pip-distributed runtime client under `src/provenance_client`
- server implementation under `src/provenance_server`

For `bottom-line`, start with three entrypoints and a sidecar stage manifest rather than changing the Scala framework deeply. That gives useful lineage quickly and provides the template for reuse across the rest of MethodsBioindex.
