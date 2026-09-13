"""Assemble the explicitly authorized local teacher corpus before any v4 training.

Original source/teacher text stays under ignored .training, never in this script.
The frozen trainer performs its existing numeric/template leakage checks as well.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import evaluate_v4
import train
from dataset_io import write_outputs_once

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / ".training/datasets/finance-v4"


def rows(path):
    return [json.loads(line) for line in path.read_text("utf-8-sig").splitlines() if line.strip()]


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def enrich(row, document_id=None):
    result = dict(row)
    result.setdefault("terms", [])
    result.setdefault("criticalChecks", [])
    result.setdefault("protectedSymbols", [])
    result.setdefault("unitChecks", [])
    result.setdefault("reviewStatus", "assistant_authored_not_human_reviewed")
    result.setdefault("humanReviewed", False)
    result.setdefault("sourceSha256", digest(row["source"]))
    result.setdefault("sourceWordCount", len(row["source"].split()))
    result["targetSha256"] = digest(row["target"])
    if document_id is not None:
        result.setdefault("documentId", document_id)
    return result


def verify_row(row):
    if row["sourceSha256"] != digest(row["source"]):
        raise ValueError(f"Source integrity mismatch: {row['id']}")
    if row.get("humanReviewed") is not False:
        raise ValueError(f"No human-reviewed corpus was supplied: {row['id']}")
    if "\ufffd" in row["source"] + row["target"] or "??" in row["target"]:
        raise ValueError(f"Suspected encoding corruption: {row['id']}")
    if train.numeric_tokens(row["source"]) != train.numeric_tokens(row["target"]):
        raise ValueError(f"Reference numeral mismatch: {row['id']}")
    evaluate_v4.validate_annotations(row)
    for term in row["terms"]:
        if train.normalized_term(term["target"] if isinstance(term, dict) else term) not in train.normalized_term(row["target"]):
            raise ValueError(f"Term absent from reference: {row['id']}")


def main():
    if any((DEST / name).exists() for name in ("train.jsonl", "dev.jsonl", "test.jsonl", "dataset-manifest.json")):
        raise ValueError("Dataset is already frozen; do not overwrite an experiment")
    source_manifest = json.loads((DEST / "source-manifest.json").read_text("utf-8"))
    for split, item in source_manifest["files"].items():
        expected = DEST / f"{split}-sources.jsonl"
        if item["path"] != expected.relative_to(ROOT).as_posix() or train.sha256(expected) != item["sha256"]:
            raise ValueError("Extracted source differs from the original source manifest")
    sources = {split: {r["id"]: r for r in rows(DEST / f"{split}-sources.jsonl")}
               for split in ("train", "dev", "test")}
    second_review = json.loads((DEST / "train-second-review.json").read_text("utf-8"))
    if (second_review.get("passed") is not True or second_review.get("humanReviewed") is not False
            or second_review.get("teacherTrainFileSha256") != train.sha256(DEST / "teacher-train.jsonl")):
        raise ValueError("Independent train translation review is missing or stale")
    dev_review = json.loads((DEST / "dev-general-second-review.json").read_text("utf-8"))
    if (dev_review.get("humanReviewed") is not False
            or dev_review["teacherDev"]["sha256"] != train.sha256(DEST / "teacher-dev.jsonl")
            or any(dev_review["inputFiles"][name] != train.sha256(DEST / name)
                   for name in ("dev-sources.jsonl", "dev-targets.json", "general-authored.json"))):
        raise ValueError("Independent dev/general translation review is missing or stale")
    exclusions = json.loads((DEST / "translation-exclusions.json").read_text("utf-8"))["excluded"]
    excluded_train = {row["id"] for row in exclusions}
    for row in exclusions:
        if sources["train"][row["id"]]["sourceSha256"] != row["sourceSha256"]:
            raise ValueError("Source exclusion identity changed")
    assembled = {}
    for split in ("train", "dev", "test"):
        assembled[split] = [enrich(r) for r in rows(DEST / f"teacher-{split}.jsonl")]
        expected_ids = set(sources[split]) - (excluded_train if split == "train" else set())
        if {row["id"] for row in assembled[split]} != expected_ids:
            raise ValueError(f"Unexplained missing/extra source translations in {split}")
        for row in assembled[split]:
            original = sources[split][row["id"]]
            for field, value in original.items():
                if field != "reviewStatus" and row.get(field) != value:
                    raise ValueError(f"Immutable source metadata changed: {row['id']} / {field}")
            if split == "train":
                row["reviewStatus"] = "assistant_translated_second_assistant_checked_not_human_reviewed"
    # Retain the previous training-only short sentence corpus. Never read/replay
    # its development or already-consumed final evaluation data.
    old_train = [enrich(r, "v3-authored-train") for r in rows(ROOT / "content/training/train.jsonl")]
    for row in old_train:
        row["replayOrigin"] = "content/training/train.jsonl"
    assembled["train"].extend(old_train)
    authored = json.loads((DEST / "general-authored.json").read_text("utf-8"))
    for row in authored:
        row = enrich({**row, "domain": "general", "provenance": "assistant_authored"}, row["id"])
        assembled[row["split"]].append(row)
    assembled["test"].extend(enrich(r, r["id"]) for r in rows(DEST / "authored-test.jsonl"))
    identifiers, document_splits = set(), {}
    for split, values in assembled.items():
        for row in values:
            if row["id"] in identifiers or row["split"] != split:
                raise ValueError(f"Duplicate ID or wrong split: {row['id']}")
            identifiers.add(row["id"])
            verify_row(row)
            document = row["documentId"]
            if document in document_splits and document_splits[document] != split:
                raise ValueError(f"Whole-document split overlap: {document}")
            document_splits[document] = split
    # Exact duplicates are never silently deleted; a source-backed exclusion
    # must be documented before rerunning this pre-training step.
    payloads = {f"{split}.jsonl": "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in values)
                for split, values in assembled.items()}
    manifest = {
        "version": 1,
        "authorization": "User requested assistant translation of available source material followed by actual model training; no separate human-reviewed corpus.",
        "humanReviewedCount": 0,
        "sourcePolicy": "Read-only stored immutable blocks; whole resources R01/R05 train, R02 dev, B01 test. No old dev/test replay.",
        "referenceLimitations": "Assistant translations/authored references, not official translations or human gold. Historical statements are translations of the stored original, not current legal/financial claims.",
        "dataProtection": "Source, translations and weights are local ignored artifacts. SQLite backup does not cover .training results.",
        "files": {},
        "inputs": {},
        "documents": document_splits,
    }
    for split, values in assembled.items():
        file = DEST / f"{split}.jsonl"
        manifest["files"][split] = {"path": file.relative_to(ROOT).as_posix(), "sha256": digest(payloads[file.name]),
                                   "count": len(values), "sourceWords": sum(r["sourceWordCount"] for r in values),
                                   "domains": dict(Counter(r["domain"] for r in values)),
                                   "provenance": dict(Counter(r["provenance"] for r in values)),
                                   "reviewStatus": dict(Counter(r["reviewStatus"] for r in values))}
    for name in ("source-manifest.json", "teacher-train.jsonl", "teacher-dev.jsonl", "teacher-test.jsonl", "authored-test.jsonl", "general-authored.json", "train-second-review.json", "translation-exclusions.json", "dev-general-second-review.json", "test-validation.json", "test-annotation-validation.json"):
        manifest["inputs"][name] = train.sha256(DEST / name)
    manifest["inputs"]["content/training/train.jsonl"] = train.sha256(ROOT / "content/training/train.jsonl")
    payloads["dataset-manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    write_outputs_once(DEST, payloads)
    print(json.dumps({"files": manifest["files"], "humanReviewedCount": 0}, ensure_ascii=True))


if __name__ == "__main__":
    main()
