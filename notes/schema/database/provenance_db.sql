

.headers on
.mode column

CREATE TABLE prov_artifact (
    id TEXT PRIMARY KEY,
    pipeline_type TEXT NOT NULL,
    provenance TEXT NOT NULL,
    name TEXT NOT NULL,
    document_id TEXT NOT NULL default '',      -- foreign key to prov_document.document_id
    trait_legacy_id TEXT NOT NULL,  -- foreign key to prov_trait.legacy_id
    ancestry_id TEXT NOT NULL,      -- foreign key to prov_ancestry.ancestry_id
    description TEXT
);

CREATE TABLE prov_document (
    document_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    document_text TEXT NOT NULL,
    description TEXT
);


CREATE TABLE drs_artifact (
    id TEXT PRIMARY KEY,
    dcc_location TEXT NOT NULL,
    artifact_type TEXT,
    description TEXT
);

CREATE TABLE prov_trait (
    legacy_id TEXT PRIMARY KEY,
    kpn_id TEXT NOT NULL,
    pipeline_type_id TEXT NOT NULL default 'bottom-line',
    name TEXT NOT NULL,
    description TEXT
);

CREATE TABLE prov_ancestry (
    ancestry_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT
);

CREATE TABLE prov_pipeline (
    pipeline_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT
);


-- insert pipeline data
insert into prov_pipeline (pipeline_id, name) values ('bottom-line', 'aggregator GWAS bottom line analysis');
insert into prov_pipeline (pipeline_id, name) values ('geneset', 'gene set extractor');


-- insert ancestry data
insert into prov_ancestry (ancestry_id, name) values('AA', 'African American');
insert into prov_ancestry (ancestry_id, name) values('AF', 'African');
insert into prov_ancestry (ancestry_id, name) values('EA', 'East Asian');
insert into prov_ancestry (ancestry_id, name) values('SA', 'South Asian');
insert into prov_ancestry (ancestry_id, name) values('EU', 'European');
insert into prov_ancestry (ancestry_id, name) values('HS', 'Hispanic');
insert into prov_ancestry (ancestry_id, name) values('GME', 'GME');
insert into prov_ancestry (ancestry_id, name) values('SSAF', 'SSAF');
insert into prov_ancestry (ancestry_id, name) values('Mixed', 'Mixed');

