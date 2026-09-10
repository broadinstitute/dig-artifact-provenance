# `load_bottom_line_to_db.py` Design

## Purpose

[`src/python/load_bottom_line_to_db.py`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/src/python/load_bottom_line_to_db.py) loads generated bottom-line provenance JSON files into the SQLite provenance database.

It is the database-loading step that takes per-artifact provenance JSON documents and stores them in the `prov_artifact` table for retrieval through the Flask provenance service.

## How To Run

Run from the repository root:

```bash
python3 src/python/load_bottom_line_to_db.py
```

There are no mandatory command-line arguments.

Optional arguments:

- `--in-data-dir`: directory containing provenance JSON files. Default: `data/bottom-line-provenance`
- `--in-database`: SQLite database file to load. Default: `data/database/provenance_db.sqlite`
- `--in-log-file`: log file for loader activity. Default: `logs/bottom-line-provenance.log`

Example with explicit arguments:

```bash
python3 src/python/load_bottom_line_to_db.py \
  --in-data-dir data/bottom-line-provenance \
  --in-database data/database/provenance_db.sqlite \
  --in-log-file logs/bottom-line-provenance.log
```

## Inputs

The script expects an input directory containing generated DAPPER-oriented provenance JSON files.

Default input directory:

- [`data/bottom-line-provenance`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/data/bottom-line-provenance)

Only files matching `*.json` are loaded.

Malformed JSON files are skipped and logged.

## Database Target

The script writes to the SQLite database passed through `--in-database`.

Default database:

- [`data/database/provenance_db.sqlite`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/data/database/provenance_db.sqlite)

The target database must already exist and must contain the `prov_artifact` table defined in:

- [`notes/schema/database/provenance_db.sql`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/notes/schema/database/provenance_db.sql)

## Load Behavior

Before loading new records, the script deletes existing rows where:

```sql
pipeline_type = 'bottom-line'
```

Each provenance JSON file is inserted into `prov_artifact` with:

- `id`: the JSON filename stem
- `pipeline_type`: `bottom-line`
- `provenance`: the full JSON document serialized as compact JSON text
- `name`: the end-result description, usually from `drs_objects[0].description`
- `description`: `NULL`

## Logging

The script logs to the path passed through `--in-log-file`.

Default log file:

- [`logs/bottom-line-provenance.log`](/Users/mduby/Code/DccWorkspace/ArtifactProvenance/logs/bottom-line-provenance.log)

The log includes:

- malformed JSON files that were skipped
- each provenance file prepared for database insertion
- total files read
- total database records created

## Expected Workflow

A typical workflow is:

```bash
python3 src/python/onestep/create_bottom_line_dapper_provenance.py
python3 src/python/load_bottom_line_to_db.py
```

The first command generates provenance JSON files. The second command replaces the current `bottom-line` records in SQLite with the generated files.

## Current Limitations

The script does not create or migrate the SQLite schema. The database file and `prov_artifact` table must exist before the load runs.

The load is scoped to `pipeline_type = 'bottom-line'`; it intentionally leaves other pipeline rows untouched.

The script does not remove or validate stale JSON files in the input directory before loading. If stale files remain in `data/bottom-line-provenance`, they will be loaded.
