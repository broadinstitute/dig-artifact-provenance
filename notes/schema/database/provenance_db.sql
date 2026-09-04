CREATE TABLE prov_artifact (
    id TEXT PRIMARY KEY,
    pipeline_type TEXT NOT NULL,
    provenance TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT
);

CREATE TABLE drs_artifact (
    id TEXT PRIMARY KEY,
    dcc_location TEXT NOT NULL,
    artifact_type TEXT,
    description TEXT
);
