
.headers on
.mode column

select * from prov_artifact where pipeline_type = 'geneset' limit 20;

select document_id, pipeline_type, name, description from prov_document where pipeline_type = 'geneset' limit 20;

select count(*) from prov_artifact;

select count(*) from prov_document;

