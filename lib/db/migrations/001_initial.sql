CREATE TABLE resources (
 id TEXT PRIMARY KEY, source_type TEXT NOT NULL CHECK(source_type IN ('remote','upload')), canonical_url TEXT,
 original_filename TEXT, author TEXT, title_en TEXT NOT NULL, title_ko TEXT NOT NULL, summary_ko TEXT NOT NULL DEFAULT '',
 kind TEXT NOT NULL, format TEXT NOT NULL, level TEXT NOT NULL DEFAULT '입문', priority TEXT NOT NULL DEFAULT '보충',
 tags_json TEXT NOT NULL DEFAULT '[]', objectives_json TEXT NOT NULL DEFAULT '[]', question TEXT NOT NULL DEFAULT '',
 source_status TEXT NOT NULL DEFAULT 'not_imported', created_at TEXT NOT NULL,
 CHECK(source_type='upload' OR canonical_url IS NOT NULL)
);
CREATE TABLE resource_preferences(resource_id TEXT PRIMARY KEY REFERENCES resources(id),priority_override TEXT,updated_at TEXT NOT NULL);
CREATE TABLE resource_relations(parent_id TEXT REFERENCES resources(id),child_id TEXT REFERENCES resources(id),relation TEXT NOT NULL,sort_order INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(parent_id,child_id,relation),CHECK(parent_id<>child_id));
CREATE TABLE modules(id TEXT PRIMARY KEY,slug TEXT NOT NULL UNIQUE,sort_order INTEGER NOT NULL,title_ko TEXT NOT NULL,question TEXT NOT NULL,objectives_json TEXT NOT NULL,questions_json TEXT NOT NULL);
CREATE TABLE module_resources(module_id TEXT REFERENCES modules(id),resource_id TEXT REFERENCES resources(id),role TEXT NOT NULL DEFAULT 'reading',sort_order INTEGER NOT NULL,optional_range_json TEXT,PRIMARY KEY(module_id,resource_id));
CREATE TABLE glossary_terms(id TEXT PRIMARY KEY,term_en TEXT NOT NULL,term_ko TEXT NOT NULL,acronym TEXT,aliases_json TEXT NOT NULL,definition_ko TEXT NOT NULL,formula TEXT,example_ko TEXT,notes TEXT,sources_json TEXT NOT NULL,revision INTEGER NOT NULL);
CREATE TABLE module_terms(module_id TEXT REFERENCES modules(id),term_id TEXT REFERENCES glossary_terms(id),role TEXT NOT NULL DEFAULT 'core',sort_order INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(module_id,term_id));
CREATE TABLE source_versions(
 id TEXT PRIMARY KEY,resource_id TEXT NOT NULL REFERENCES resources(id),file_hash TEXT NOT NULL,original_path TEXT NOT NULL,
 final_url TEXT,mime TEXT NOT NULL,format TEXT NOT NULL,byte_size INTEGER NOT NULL,page_count INTEGER,imported_at TEXT NOT NULL,
 fetched_at TEXT,published_at TEXT,http_last_modified TEXT,etag TEXT,declared_version TEXT,extractor_version TEXT NOT NULL,
 extraction_config_hash TEXT NOT NULL,extraction_status TEXT NOT NULL,warnings_json TEXT NOT NULL DEFAULT '[]',
 UNIQUE(resource_id,file_hash,extractor_version,extraction_config_hash),UNIQUE(resource_id,id),CHECK(page_count IS NULL OR page_count>=0)
);
CREATE TABLE source_blocks(
 id TEXT PRIMARY KEY,source_version_id TEXT NOT NULL REFERENCES source_versions(id),sort_order INTEGER NOT NULL,
 type TEXT NOT NULL,text TEXT NOT NULL,source_hash TEXT NOT NULL,page_index INTEGER,bbox_json TEXT,structure_json TEXT,
 warnings_json TEXT NOT NULL DEFAULT '[]',UNIQUE(source_version_id,sort_order),UNIQUE(source_version_id,id),CHECK(page_index IS NULL OR page_index>=0)
);
CREATE TABLE source_assets(id TEXT PRIMARY KEY,source_version_id TEXT NOT NULL REFERENCES source_versions(id),source_url TEXT,local_path TEXT,file_hash TEXT,mime TEXT,status TEXT NOT NULL,UNIQUE(source_version_id,source_url));
CREATE TABLE translations(
 id TEXT PRIMARY KEY,block_id TEXT NOT NULL REFERENCES source_blocks(id),cache_key TEXT NOT NULL UNIQUE,text_ko TEXT NOT NULL,
 provider TEXT NOT NULL,model TEXT NOT NULL,prompt_version TEXT NOT NULL,glossary_version TEXT NOT NULL,context_hash TEXT NOT NULL,
 generation_status TEXT NOT NULL DEFAULT 'ready',validation_status TEXT NOT NULL,review_status TEXT NOT NULL DEFAULT 'unreviewed',
 usage_json TEXT,structure_json TEXT,created_at TEXT NOT NULL
);
CREATE TABLE editorial_contents(id TEXT PRIMARY KEY,resource_id TEXT NOT NULL REFERENCES resources(id),source_version_id TEXT,block_id TEXT,type TEXT NOT NULL,text_ko TEXT NOT NULL,provenance TEXT NOT NULL,references_json TEXT NOT NULL,
 FOREIGN KEY(resource_id,source_version_id) REFERENCES source_versions(resource_id,id),FOREIGN KEY(source_version_id,block_id) REFERENCES source_blocks(source_version_id,id),CHECK(block_id IS NULL OR source_version_id IS NOT NULL));
CREATE TABLE notes(id TEXT PRIMARY KEY,resource_id TEXT NOT NULL REFERENCES resources(id),source_version_id TEXT,block_id TEXT,page_index INTEGER,quote TEXT,text TEXT NOT NULL,updated_at TEXT NOT NULL,
 FOREIGN KEY(resource_id,source_version_id) REFERENCES source_versions(resource_id,id),FOREIGN KEY(source_version_id,block_id) REFERENCES source_blocks(source_version_id,id),CHECK((block_id IS NULL AND page_index IS NULL) OR source_version_id IS NOT NULL),CHECK(page_index IS NULL OR page_index>=0));
CREATE TABLE bookmarks(id TEXT PRIMARY KEY,resource_id TEXT NOT NULL REFERENCES resources(id),source_version_id TEXT,block_id TEXT,page_index INTEGER,location_key TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,
 FOREIGN KEY(resource_id,source_version_id) REFERENCES source_versions(resource_id,id),FOREIGN KEY(source_version_id,block_id) REFERENCES source_blocks(source_version_id,id),CHECK((block_id IS NULL AND page_index IS NULL) OR source_version_id IS NOT NULL),CHECK(page_index IS NULL OR page_index>=0));
CREATE TABLE reading_positions(resource_id TEXT NOT NULL,source_version_id TEXT NOT NULL,block_id TEXT,page_index INTEGER,offset REAL NOT NULL DEFAULT 0,language_mode TEXT NOT NULL CHECK(language_mode IN ('ko','en','parallel')),updated_at TEXT NOT NULL,
 PRIMARY KEY(resource_id,source_version_id),FOREIGN KEY(resource_id,source_version_id) REFERENCES source_versions(resource_id,id),FOREIGN KEY(source_version_id,block_id) REFERENCES source_blocks(source_version_id,id),CHECK(page_index IS NULL OR page_index>=0));
CREATE TABLE learning_progress(module_id TEXT PRIMARY KEY REFERENCES modules(id),status TEXT NOT NULL CHECK(status IN ('not_started','in_progress','completed')),completed_at TEXT,updated_at TEXT NOT NULL,CHECK((status='completed')=(completed_at IS NOT NULL)));
CREATE TABLE jobs(id TEXT PRIMARY KEY,type TEXT NOT NULL,dedupe_key TEXT NOT NULL,status TEXT NOT NULL,scope_json TEXT NOT NULL,progress_json TEXT NOT NULL DEFAULT '{}',attempts INTEGER NOT NULL DEFAULT 0,next_attempt_at TEXT,lease_owner TEXT,lease_until TEXT,cancel_requested_at TEXT,error_code TEXT,error_message TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX active_jobs ON jobs(dedupe_key) WHERE status IN ('queued','running');
CREATE TABLE job_items(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id),unit_key TEXT NOT NULL,work_key TEXT NOT NULL,block_id TEXT REFERENCES source_blocks(id),status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_attempt_at TEXT,result_id TEXT,error_code TEXT,error_message TEXT,scope_json TEXT NOT NULL DEFAULT '{}',UNIQUE(job_id,unit_key));
CREATE UNIQUE INDEX active_work ON job_items(work_key) WHERE status IN ('queued','running');
CREATE TABLE usage_records(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id),job_item_id TEXT REFERENCES job_items(id),attempt_id TEXT NOT NULL UNIQUE,provider_request_id TEXT,model TEXT NOT NULL,source_chars INTEGER NOT NULL,reservation_status TEXT NOT NULL,input_tokens INTEGER,output_tokens INTEGER,outcome TEXT,estimated_cost REAL,created_at TEXT NOT NULL);
CREATE TABLE settings(key TEXT PRIMARY KEY,non_secret_value_json TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE INDEX modules_resource_idx ON module_resources(resource_id);
CREATE INDEX relations_child_idx ON resource_relations(child_id);
CREATE INDEX versions_resource_idx ON source_versions(resource_id,imported_at);
CREATE INDEX blocks_page_idx ON source_blocks(source_version_id,page_index,sort_order);
CREATE INDEX translations_block_idx ON translations(block_id,created_at);
CREATE INDEX notes_resource_idx ON notes(resource_id,source_version_id);
CREATE INDEX bookmarks_resource_idx ON bookmarks(resource_id,source_version_id);
CREATE INDEX position_date_idx ON reading_positions(updated_at);
CREATE INDEX jobs_ready_idx ON jobs(status,next_attempt_at,lease_until);
CREATE INDEX items_status_idx ON job_items(job_id,status);
CREATE INDEX usage_date_idx ON usage_records(created_at);
