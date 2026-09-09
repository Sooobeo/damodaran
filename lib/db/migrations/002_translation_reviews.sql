CREATE TABLE translation_reviews (
 id TEXT PRIMARY KEY,
 translation_id TEXT NOT NULL REFERENCES translations(id),
 parent_review_id TEXT REFERENCES translation_reviews(id),
 source_text TEXT NOT NULL,
 source_hash TEXT NOT NULL,
 context_hash TEXT NOT NULL,
 glossary_version TEXT NOT NULL,
 block_type TEXT NOT NULL CHECK(block_type NOT IN ('table','image')),
 text_ko TEXT NOT NULL CHECK(length(trim(text_ko))>0),
 created_at TEXT NOT NULL
);
CREATE INDEX translation_review_history_idx ON translation_reviews(translation_id,created_at);
CREATE INDEX translation_review_memory_idx ON translation_reviews(source_hash,context_hash,glossary_version,block_type);
CREATE TABLE translation_review_links (
 translation_id TEXT PRIMARY KEY REFERENCES translations(id),
 review_id TEXT NOT NULL REFERENCES translation_reviews(id)
);
