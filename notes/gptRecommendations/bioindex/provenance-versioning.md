# Versioned pipeline results and provenance

## Scope and decision

Publish final result shards under immutable S3 keys. Keep intermediate outputs in a reusable cache keyed by their inputs and relevant code. A quarterly release is an immutable manifest that selects final artifacts; unchanged artifacts are referenced again without recomputation. Store all lineage manifests under one `provenance-files/` prefix. The search service returns a provenance reference with each result. A dataset registry explicitly marks which stage outputs are public final results; all other stage outputs are intermediate, even if several pipelines consume them.

This is a design for the methods in this repository. The shared execution and state changes belong in `dig-aggregator-core`, which all method projects use. Python, Scala, and shell jobs provide read and write information to the same recorder.

## Names and storage

The exact bucket names are configuration values. Suggested keys:

```text
s3://dig-analysis-provenance/provenance-files/v1/inputs/<input-snapshot-id>.json
s3://dig-analysis-provenance/provenance-files/v1/steps/<step-execution-id>.json
s3://dig-analysis-provenance/provenance-files/v1/artifacts/<artifact-id>.json
s3://dig-analysis-provenance/provenance-files/v1/releases/<release-id>/manifest.json

s3://dig-analysis-work-cache/<step-fingerprint>/...            # intermediate; expirable
s3://<final-results-bucket>/artifacts/<artifact-id>/part-*.json # final; immutable
```

`v1` is the provenance schema version, not the quarterly data release. A release ID such as `2027-Q1` is a tag. Derive an artifact ID from the step fingerprint and successful execution ID before writing shards; record a separate digest of the complete output shard inventory in its manifest. A new execution attempt gets a new execution ID, even when it retries the same fingerprint. Object keys for final shards and manifests are write-once. Enable S3 Versioning on the final artifact and dedicated provenance buckets. Keep the intermediate cache in an unversioned bucket. Do not use S3's generated version IDs as the release naming scheme. Record each object's S3 version ID when available.

Keep a small mutable `latest` pointer in a release registry, separate from the immutable release manifest. Publish the pointer only after every manifest and shard has been validated. Never rewrite a released manifest or artifact.

## Execution graph and cache decisions

Model every runnable stage output as a node in a directed acyclic graph. An edge names the upstream artifact or intermediate result that the node actually consumes. Shared intermediate work is one node referenced by several downstream nodes, including nodes in other methods. Define stable node IDs from method, stage, output partition, and any parameters that affect the result.

For each node compute a `step_fingerprint` from a canonical, sorted record containing:

1. The step ID and effective parameters.
2. Exact input identities: S3 bucket, key, version ID or immutable snapshot ID, and a checksum where available. For a directory, include a sorted inventory of all selected objects, not just the prefix or `_SUCCESS` marker.
3. Fingerprints of upstream nodes and the versions of reference data read by the step.
4. Digests of only the code and resources that affect the step: its Scala definition, Python or shell scripts, shared libraries, packaged core artifact, bootstrap scripts, dependency lock or package artifact, and relevant runtime configuration. Include the full Git commit SHA separately for traceability. An unrelated repository commit must not invalidate every node.

Reuse a successful cached node only if its fingerprint matches and its required output objects still exist. A failed attempt is never a cache hit. When an input identity or relevant code digest changes, the node gets a new fingerprint; downstream fingerprints change transitively. A retry with identical inputs and code gets a new execution ID, while retaining the same fingerprint. A forced rerun may produce a new artifact; record its execution ID and compare output inventories before deciding whether to reuse or replace a candidate artifact.

Intermediate cache entries may be deleted by lifecycle policy once no in-progress execution needs them. A node that depends on a deleted cache entry must rerun, even when its fingerprint has not changed. Keep all start inputs and released final artifacts available for the full release retention period; a pointer to mutable input data cannot support exact reconstruction.

## Capturing exact lineage

Before running a step, freeze or identify each start input object. Prefer an S3 version ID in a versioned bucket. For nonversioned or externally controlled inputs, copy the selected objects into an immutable input snapshot. An input manifest lists every selected key, version ID or snapshot key, size, and checksum. Capture the selection boundary before execution, then make the job read that selection; simply listing a mutable prefix before and after a run does not prove which files were read.

The shared runner records method, stage, output partition, command, arguments, UTC start and finish times, status, EMR application or cluster ID, step fingerprint, Git repository and commit SHA, code/resource digests, input manifest IDs, upstream step IDs, and expected output prefixes. Jobs report actual reads and writes through a small common interface. Spark jobs can report resolved input files and output directories; `boto3`, AWS CLI, and shell jobs need wrappers or explicit declarations. The runner validates these reports against the frozen inputs and lists output shards after successful completion.

Each final artifact manifest contains its producing step execution ID, the transitive input and code manifest references, and an exhaustive sorted list of final shards with bucket, key, version ID if present, size, and checksum. Use a full-object checksum; an S3 ETag is not always a content checksum. A manifest is committed only after the job succeeds and all listed shards are visible. Record failed attempts in step manifests, but never add their outputs to a release.

Example artifact manifest (shortened):

```json
{
  "schema_version": 1,
  "artifact_id": "sha256:...",
  "dataset": "associations",
  "partition": {"phenotype": "T2D", "ancestry": "Mixed"},
  "step_execution_id": "01J...",
  "step_fingerprint": "sha256:...",
  "shard_inventory_digest": "sha256:...",
  "inputs": ["s3://dig-analysis-provenance/provenance-files/v1/inputs/sha256-....json"],
  "upstream_steps": ["01H..."],
  "code": {"repository": "broadinstitute/dig-aggregator-methods", "commit": "<full-commit-sha>", "effective_digest": "sha256:..."},
  "shards": [{"bucket": "dig-bio-index", "key": "artifacts/<artifact-id>/part-00000.json", "version_id": "...", "size": 12345, "checksum_sha256": "..."}]
}
```

## Quarterly release and search lookup

A release manifest maps each public dataset or index partition to an artifact manifest ID. Its `created_at`, `published_at`, release tag, schema version, and parent release are explicit. Copy unchanged artifact references from the prior release; replace only entries whose fingerprints or required files changed. Validate that every referenced artifact and shard exists, then publish the release tag once. This permits a quarterly release with no recomputation.

For search, store `release_id` and `artifact_id` with each indexed result or its enclosing immutable search partition. The search response exposes these IDs and a provenance URL. The lookup service resolves `(release_id, artifact_id)` to the artifact manifest, then follows upstream step and input manifests to return all start files and code commits. If a search result combines multiple artifacts, return all contributing artifact IDs. If exact *row-level* ancestry is required after joins, add row lineage to the job; an artifact-level manifest proves the inputs to the output partition, not which input rows contributed to one hit.

An index rebuild must read the release's artifact manifest, not an unversioned `latest` S3 prefix. Keep old index versions or a searchable release filter so a search against an earlier release resolves to the same artifact IDs later.

## Repository integration

1. Add node fingerprinting, run state, immutable manifest writing, cache validation, and publication to `dig-aggregator-core`. Reuse its existing run-state store if it supports the required keys and conditional updates; otherwise keep a small registry for node fingerprints, attempts, and release pointers. S3 holds the authoritative manifests.
2. Add a common job reporting contract for actual reads and writes. Start with BioIndex final stages and their direct inputs, then cover the shell and Python jobs with undeclared S3 reads and writes. Stage `sources` and `rules` remain useful for scheduling but do not fully describe actual data access.
3. Change final writers from overwrite-in-place to run-specific staging followed by immutable artifact keys. Configure BioIndex indexing to consume release manifests and expose provenance IDs in search responses.
4. Migrate one vertical slice first: a single BioIndex partition such as `associations/phenotype/T2D/Mixed`, its upstream steps, a release manifest, and a search lookup. Verify unchanged reuse, one changed start shard, one changed script, retry after failure, shared upstream invalidation, and lookup of a previous release.

The current SBT `version.properties` records the Git commit and dirty state at build time. Require a clean committed build for a release, capture the core library artifact digest, and pin dependencies so the recorded commit actually corresponds to the executed code. Existing BioIndex scripts often write to fixed S3 paths with Spark overwrite mode, so their output path or publication step must change before they can be safely tagged.
