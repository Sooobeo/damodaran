CREATE UNIQUE INDEX translations_block_identity ON translations(block_id,id);
CREATE TABLE translation_quality_assessments (
 id TEXT PRIMARY KEY,
 translation_id TEXT NOT NULL,
 block_id TEXT NOT NULL,
 source_version_id TEXT NOT NULL,
 source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
 translation_hash TEXT NOT NULL CHECK(length(translation_hash)=64),
 context_hash TEXT NOT NULL CHECK(length(context_hash)=64),
 cache_key TEXT NOT NULL,
 model_identity TEXT NOT NULL,
 calibration_version TEXT NOT NULL,
 rules_version TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','completed','unavailable','failed','cancelled','stale')),
 risk TEXT NOT NULL CHECK(risk IN ('review','no_findings','unknown')),
 score REAL,
 findings_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(findings_json)),
 message TEXT NOT NULL DEFAULT '',
 job_id TEXT REFERENCES jobs(id),
 created_at TEXT NOT NULL,
 completed_at TEXT,
 FOREIGN KEY(block_id,translation_id) REFERENCES translations(block_id,id),
 FOREIGN KEY(source_version_id,block_id) REFERENCES source_blocks(source_version_id,id)
);
CREATE INDEX quality_translation_idx ON translation_quality_assessments(translation_id,created_at);
CREATE INDEX quality_cache_idx ON translation_quality_assessments(translation_id,cache_key,status);
CREATE UNIQUE INDEX quality_active_key ON translation_quality_assessments(translation_id,cache_key) WHERE status IN ('queued','running');
