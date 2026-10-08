

alter table prov_artifact
add column document_id TEXT NOT NULL default 'NaN';

alter table prov_trait
add column pipeline_type TEXT NOT NULL default 'bottom-line';
