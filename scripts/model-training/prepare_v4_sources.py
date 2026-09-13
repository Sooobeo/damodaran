"""Read immutable stored source blocks into a separate teacher-translation corpus.

No original content or DB row is modified. Splits are assigned by whole resource
before translation: R01/R05 train, R02 dev, B01 test. Targets are authored later.
"""
from __future__ import annotations

from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from dataset_io import write_outputs_once

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / ".training/datasets/finance-v4"
VERSIONS = {
    "R01": ("train", "7d350f79-0b36-4ac3-a89f-73b965b463ed"),
    "R05": ("train", "6c868b56-d0f7-45f5-8863-50b6de627ec3"),
    "R02": ("dev", "077ae2a1-7b10-433e-abce-a5528b0c96b6"),
    "B01": ("test", "48bcd948-bffd-4d3f-a4b8-271d5e5c6bf8"),
}


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path,
                        help="Absolute DATA_DIR resolved by the app's common environment loader")
    args = parser.parse_args()
    if not args.data_dir.is_absolute():
        raise ValueError("Pass the application's resolved absolute DATA_DIR")
    import spacy
    nlp = spacy.blank("en")
    nlp.add_pipe("sentencizer")
    DEST.mkdir(parents=True, exist_ok=True)
    output_names = ("train-sources.jsonl", "dev-sources.jsonl", "test-sources.jsonl", "source-manifest.json")
    if any((DEST / name).exists() for name in output_names):
        raise ValueError("Preserve the existing corpus; use a new version for new sources")
    connection = sqlite3.connect((args.data_dir / "library.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    splits = {name: [] for name in ("train", "dev", "test")}
    skipped, documents = [], []
    try:
        for resource, (split, version) in VERSIONS.items():
            document = connection.execute("SELECT v.*,r.title_en FROM source_versions v JOIN resources r ON r.id=v.resource_id WHERE v.id=? AND v.resource_id=?", (version, resource)).fetchone()
            if not document or document["extraction_status"] != "ready":
                raise ValueError("Expected immutable readable source version missing")
            documents.append({"resourceId": resource, "sourceVersionId": version, "split": split, "url": document["final_url"], "title": document["title_en"], "originalFileHash": document["file_hash"], "extractor": document["extractor_version"]})
            for block in connection.execute("SELECT * FROM source_blocks WHERE source_version_id=? AND type='paragraph' ORDER BY sort_order", (version,)):
                original = block["text"]
                text = " ".join(original.split())
                if "\ufffd" in text or len(re.findall(r"[A-Za-z]+", text)) < 15:
                    skipped.append({"blockId": block["id"], "reason": "replacement-character-or-too-little-prose"})
                    continue
                sentences = [sentence.text.strip() for sentence in nlp(text).sents if sentence.text.strip()]
                chunks, current = [], []
                for sentence in sentences:
                    if len(sentence.split()) > 110:
                        if current:
                            chunks.append(" ".join(current)); current = []
                        skipped.append({"blockId": block["id"], "reason": "single-sentence-over-110-words", "sentenceSha256": sha(sentence)})
                        continue
                    if current and len((" ".join(current) + " " + sentence).split()) > 80:
                        chunks.append(" ".join(current)); current = []
                    current.append(sentence)
                if current:
                    chunks.append(" ".join(current))
                for index, source in enumerate(chunks):
                    if len(source.split()) < 15:
                        skipped.append({"blockId": block["id"], "chunkIndex": index, "reason": "short-chunk"})
                        continue
                    # Do not train detached figure/equation captions that require
                    # an omitted image to supply the actual source meaning.
                    if re.search(r"^(?:Figure|Table|Illustration)\s+\d", source, flags=re.I):
                        skipped.append({"blockId": block["id"], "chunkIndex": index, "reason": "caption"})
                        continue
                    splits[split].append({"id": f"V4-{resource}-{block['sort_order']:03d}-{index:02d}", "source": source, "split": split, "domain": "finance", "provenance": "stored_source_assistant_translation", "reviewStatus": "awaiting_assistant_translation_not_human_reviewed", "documentId": resource, "sourceVersionId": version, "sourceBlockId": block["id"], "sourceUrl": document["final_url"], "sourceBlockSha256": sha(original), "sourceSha256": sha(source), "sourceNormalization": "whitespace-only; contiguous sentence grouping within immutable block", "sourceWordCount": len(source.split())})
    finally:
        connection.close()
    # Keep the complete eligible train corpus. Development is a fixed, uniformly
    # spaced sample within its separate document; no translation/model output is
    # available during this selection.
    if len(splits["dev"]) > 36:
        values = splits["dev"]
        splits["dev"] = [values[round(i * (len(values) - 1) / 35)] for i in range(36)]
    manifest = {"version": 1, "source": "local immutable source blocks; read-only SQLite connection", "targetAuthor": "assistant; no human-reviewed targets available", "normalization": "whitespace and source sentence grouping only", "documents": documents, "counts": {key: len(values) for key, values in splits.items()}, "skipped": skipped, "files": {}}
    payloads = {}
    for split, values in splits.items():
        file = DEST / f"{split}-sources.jsonl"
        payloads[file.name] = "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values)
        manifest["files"][split] = {"path": file.relative_to(ROOT).as_posix(), "sha256": sha(payloads[file.name]), "words": sum(value["sourceWordCount"] for value in values)}
    payloads["source-manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    write_outputs_once(DEST, payloads)
    print(json.dumps({"counts": manifest["counts"], "words": {key: value["words"] for key, value in manifest["files"].items()}, "documents": [{"resource": d["resourceId"], "split": d["split"]} for d in documents], "skipped": len(skipped)}), flush=True)


if __name__ == "__main__":
    main()
