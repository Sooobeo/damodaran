"""Validate all blind judgments before unblinding this 18-row development screen."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re

import v3_review_evidence

ROOT = Path(__file__).resolve().parents[2]
VERSION = "linguistic-development-assistant-review-summary-v1"
CATEGORIES = {"semantic_role", "negation_condition", "quantity_formula", "word_sense",
              "discourse_reference", "omission", "unsupported_addition", "terminology",
              "modality", "fluency", "other"}
ENVELOPE = {"id", "sourceSha256", "reviewerType", "humanReviewed",
            "preparedManifestSha256", "packetSha256", "judgments"}
JUDGMENT = {"id", "label", "targetSha256", "severity", "fluent", "contrastPreserved",
            "errors", "termChecks", "humanReviewed", "reasonKo"}


def require(value, message):
    if not value:
        raise ValueError(message)


def hash_bytes(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha_value(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def strict_json(value):
    def invalid_constant(_):
        raise ValueError("nonfinite_json_number")
    return json.loads(value, object_pairs_hook=strict_pairs, parse_constant=invalid_constant)


def local(path, root):
    path = Path(path)
    require(".." not in path.parts, "parent_path_component")
    path = path if path.is_absolute() else root / path
    require(not any(p.is_symlink() for p in (path, *path.parents)), "symlink_input_path")
    result = path.resolve()
    require(result.is_relative_to(root.resolve()), "path_outside_workspace")
    return result


class Inputs:
    def __init__(self, root):
        self.root = root
        self.files = {}

    def read(self, path, expected=None, jsonl=False):
        path = local(path, self.root)
        require(path.is_file() and path.stat().st_size <= 16 * 1024 * 1024, "invalid_or_large_evidence_file")
        raw = path.read_bytes()
        digest = hash_bytes(raw)
        if expected is not None:
            require(sha_value(expected) and digest == expected, "evidence_hash_mismatch")
        if path in self.files:
            require(self.files[path] == digest, "evidence_changed_during_read")
        self.files[path] = digest
        text = raw.decode("utf-8")
        return ([strict_json(line) for line in text.splitlines() if line.strip()]
                if jsonl else strict_json(text))

    def unchanged(self):
        for path, expected in self.files.items():
            require(local(path, self.root).is_file() and hash_bytes(path.read_bytes()) == expected,
                    "evidence_changed_during_aggregation")


def korean_reason(value):
    return (isinstance(value, str) and 1 <= len(value.strip()) <= 6000
            and re.search(r"[가-힣]", value) is not None)


def check_judgment(judgment, source, candidate):
    require(isinstance(judgment, dict) and set(judgment) == JUDGMENT, "judgment_fields_differ")
    require(judgment["id"] == source["id"] and judgment["label"] == candidate["label"]
            and judgment["targetSha256"] == candidate["targetSha256"], "judgment_target_identity_mismatch")
    require(judgment["humanReviewed"] is False and korean_reason(judgment["reasonKo"]),
            "judgment_provenance_or_reason_missing")
    require(type(judgment["severity"]) is int and 0 <= judgment["severity"] <= 3
            and type(judgment["fluent"]) is bool, "invalid_severity_or_fluency")
    contrast = judgment["contrastPreserved"]
    require(type(contrast) is bool if source.get("pair") else contrast is None,
            "contrast_requires_boolean_for_pair_and_null_otherwise")
    errors = judgment["errors"]
    require(isinstance(errors, list) and len(errors) <= 100, "invalid_error_list")
    for error in errors:
        require(isinstance(error, dict) and set(error) == {
            "severity", "category", "sourceSpan", "targetSpan", "reasonKo"}, "error_fields_differ")
        require(type(error["severity"]) is int and error["severity"] in (1, 2, 3)
                and error["category"] in CATEGORIES and korean_reason(error["reasonKo"]), "invalid_error")
        anchor = error["sourceSpan"]
        require(isinstance(anchor, str) and anchor.strip() and
                (anchor in source["source"] or anchor in source.get("context", "")), "source_error_span_absent")
        target = error["targetSpan"]
        require(isinstance(target, str) and ((target.strip() and target in candidate["translation"])
                or (target == "" and error["category"] == "omission")), "target_error_span_absent")
    require(judgment["severity"] == max((e["severity"] for e in errors), default=0), "severity_not_max_errors")
    terms = judgment["termChecks"]
    expected = [term["id"] for term in source.get("canonicalTermChecks", [])]
    require(len(set(expected)) == len(expected), "duplicate_source_term_id")
    require(isinstance(terms, list) and all(isinstance(t, dict) for t in terms)
            and [term.get("id") for term in terms] == expected, "term_check_coverage_or_order_differs")
    for term in terms:
        require(set(term) == {"id", "correctSense", "canonicalForm", "reasonKo"}
                and type(term["correctSense"]) is bool and type(term["canonicalForm"]) is bool
                and korean_reason(term["reasonKo"]), "invalid_term_check")
    return judgment


def counts():
    return {"rows": 0, "severity": {str(i): 0 for i in range(4)}, "materialRows": 0,
            "fluentRows": 0, "termChecks": 0, "correctSense": 0, "canonicalForm": 0, "jointCorrect": 0}


def add_count(result, judgment):
    result["rows"] += 1
    result["severity"][str(judgment["severity"])] += 1
    result["materialRows"] += int(judgment["severity"] >= 2)
    result["fluentRows"] += int(judgment["fluent"])
    for term in judgment["termChecks"]:
        result["termChecks"] += 1
        result["correctSense"] += int(term["correctSense"])
        result["canonicalForm"] += int(term["canonicalForm"])
        result["jointCorrect"] += int(term["correctSense"] and term["canonicalForm"])


def normalize_producer_summary(summary, predictions_sha):
    """Normalize only documented producer schemas; preserve the full raw metadata."""
    require(isinstance(summary, dict), "invalid_producer_summary")
    if summary.get("version") in v3_review_evidence.VERSIONS:
        v3_review_evidence.check_v3_evidence(summary, predictions_sha)
    if summary.get("version") in ("translategemma-large-screen-v1", "translategemma-large-screen-v2", "translategemma-large-screen-v3"):
        require(all(type(summary.get(field)) is int and summary[field] == 18
                    for field in ("count", "recordedCount", "expectedCount")),
                "translategemma_completion_counts_differ")
        require(not any(field in summary for field in
                        ("completed", "totalRows", "inputSha256", "manifestSha256", "selectedIds")),
                "ambiguous_translategemma_summary_schema")
        require(isinstance(summary.get("input"), dict)
                and summary.get("integrityVerified") is True and summary.get("childProcessStopped") is True
                and summary.get("predictionsSha256") == predictions_sha
                and summary.get("modelSize") in ("12b", "27b") and summary.get("profile") == "source-only",
                "translategemma_completion_identity_differs")
        return {"schema": summary["version"], "status": summary.get("status"),
                "completed": summary["count"], "input": summary["input"]}
    require(summary.get("version") in (None, "hymt30-development-screen-v1", "hymt30-development-screen-v2", "hymt30-development-screen-v3"),
            "unsupported_producer_summary_version")
    return {"schema": summary.get("version") or "unversioned-flat-linguistic-screen", "status": summary.get("status"),
            "completed": summary.get("completed"), "input": summary}


def check_producer_input(summary, predictions_sha, identity):
    normalized = normalize_producer_summary(summary, predictions_sha)
    record = normalized["input"]
    require(type(normalized["completed"]) is int and type(record.get("totalRows")) is int
            and normalized["status"] == "completed" and normalized["completed"] == 18
            and record.get("inputSha256") == identity["inputSha256"]
            and record.get("manifestSha256") == identity["manifestSha256"]
            and record.get("selectedIds") == identity["selectedIds"] and record.get("totalRows") == 18,
            "producer_summary_not_complete_for_same_input")
    return normalized


def review_code_inventory():
    paths = (Path(__file__).resolve(), Path(__file__).with_name("prepare_linguistic_reviews.py").resolve(),
             Path(v3_review_evidence.__file__).resolve())
    return {str(path): hash_bytes(path.read_bytes()) for path in paths}


def summarize(prepared_directory, review_paths, output_path, root=ROOT):
    code_sha = hash_bytes(Path(__file__).read_bytes())
    review_codes = review_code_inventory()
    root = Path(root).resolve()
    prepared = local(prepared_directory, root)
    require(prepared.is_relative_to(root / ".training/comparisons"), "prepared_outside_comparisons")
    output = local(output_path, root)
    require(output.suffix == ".json" and output.is_relative_to(prepared.parent)
            and not output.is_relative_to(prepared), "output_must_be_outside_prepared_in_same_comparison")
    require(not output.exists(), "immutable_output_already_exists")
    evidence = Inputs(root)
    manifest_path = prepared / "manifest.json"
    manifest = evidence.read(manifest_path)
    manifest_sha = evidence.files[manifest_path]
    if "reviewCodeFiles" in manifest:
        require(manifest["reviewCodeFiles"] == review_codes, "prepared_review_code_changed")
    require(isinstance(manifest, dict) and all(type(manifest.get(field)) is int for field in
            ("sourceCount", "judgmentCount", "totalRows")) and manifest.get("sourceCount") == 18
            and manifest.get("judgmentCount") in (36, 54, 72) and manifest.get("totalRows") == 18
            and manifest.get("humanReview") is False and manifest.get("humanReviewed") is False,
            "prepared_manifest_contract_differs")
    require(sha_value(manifest.get("inputSha256")) and sha_value(manifest.get("manifestSha256")),
            "prepared_input_hash_missing")
    system_count = manifest["judgmentCount"] // 18
    labels = list("ABCD"[:system_count])
    dataset_path = local(manifest["inputPath"], root)
    require(dataset_path.is_relative_to(root / "content/model-comparison"), "dataset_outside_content_comparison")
    dataset = evidence.read(dataset_path, manifest.get("inputSha256"), jsonl=True)
    dataset_manifest_path = local(manifest["manifestPath"], root)
    require(dataset_manifest_path == dataset_path.with_name("dataset-manifest.json"), "dataset_manifest_path_differs")
    dataset_manifest = evidence.read(dataset_manifest_path, manifest.get("manifestSha256"))
    source_audit_sha = None
    if "sourceAudit" in manifest:
        audit_identity = manifest["sourceAudit"]
        require(isinstance(audit_identity, dict) and set(audit_identity) == {"file", "sha256"}
                and audit_identity["file"] == "source-audit.json" and sha_value(audit_identity["sha256"]),
                "source_audit_file_identity_differs")
        audit = evidence.read(prepared / "source-audit.json", audit_identity["sha256"])
        require(isinstance(audit, dict) and audit.get("version") == "linguistic-dev-independent-source-audit-v1"
                and audit.get("modelOutputsViewed") is False and audit.get("humanReviewed") is False
                and isinstance(audit.get("inputs"), dict)
                and audit["inputs"].get(dataset_path.relative_to(root).as_posix()) == manifest["inputSha256"],
                "source_audit_provenance_or_dataset_differs")
        source_audit_sha = audit_identity["sha256"]
    ids = [row.get("id") for row in dataset]
    require(len(dataset) == 18 and len(set(ids)) == 18 and all(isinstance(rid, str) for rid in ids)
            and manifest.get("selectedIds") == ids, "source_coverage_or_order_differs")
    require(dataset_manifest.get("version") == "linguistic-dev-20260910-v1"
            and dataset_manifest.get("status") == "frozen" and dataset_manifest.get("humanReviewed") is False
            and dataset_manifest.get("sourceType") == "assistant_authored_unreviewed"
            and dataset_manifest.get("dataset") == {"file": dataset_path.name, "sha256": manifest["inputSha256"],
                                                   "count": 18, "ids": ids}, "frozen_dataset_identity_differs")
    for row in dataset:
        require(row.get("split") == "development_screen" and row.get("humanReviewed") is False
                and row.get("domain") in ("finance", "general") and isinstance(row.get("category"), str),
                "source_annotation_contract_differs")
        for field in ("source", "context", "reference"):
            require(isinstance(row.get(field), str) and row.get(field + "Sha256") ==
                    hash_bytes(row[field].encode("utf-8")), "source_or_reference_hash_differs")
    files = manifest.get("files")
    require(isinstance(files, dict) and set(files) == {f"packet-{i}.jsonl" for i in range(1, 4)}
            and all(sha_value(v) for v in files.values()) and sha_value(manifest.get("keySha256")),
            "packet_file_inventory_differs")
    templates, packet_hashes = {}, {}
    packet_order = []
    for name in sorted(files):
        packet_rows = evidence.read(prepared / name, files[name], jsonl=True)
        require(len(packet_rows) == 6, "packet_must_have_six_sources")
        for packet in packet_rows:
            require(isinstance(packet, dict) and set(packet) == {"input", "candidates", "reviewInstruction"},
                    "packet_fields_differ")
            source = packet["input"]
            rid = source.get("id")
            require(rid in ids and rid not in templates and canonical(source) == canonical(dataset[ids.index(rid)]),
                    "packet_source_or_annotations_changed")
            candidates = packet["candidates"]
            require(isinstance(candidates, list) and len(candidates) == system_count
                    and [c.get("label") for c in candidates] == labels, "candidate_coverage_differs")
            for candidate in candidates:
                require(set(candidate) == {"label", "translation", "targetSha256"}
                        and isinstance(candidate["translation"], str) and candidate["translation"].strip()
                        and candidate["targetSha256"] == hash_bytes(candidate["translation"].encode("utf-8")),
                        "candidate_translation_identity_differs")
            templates[rid] = packet
            packet_hashes[rid] = files[name]
            packet_order.append(rid)
    require(packet_order == ids, "packet_order_differs")
    paths = [local(path, root) for path in review_paths]
    require(bool(paths) and len(set(paths)) == len(paths), "empty_or_duplicate_review_files")
    require(all(p.suffix == ".jsonl" and p.is_relative_to(prepared.parent) and not p.is_relative_to(prepared)
                and p != dataset_path for p in paths), "invalid_review_file_location")
    reviewed, review_records = {}, []
    for path in paths:
        review_rows = evidence.read(path, jsonl=True)
        require(bool(review_rows), "empty_review_file")
        review_records.append({"path": str(path), "sha256": evidence.files[path], "rowCount": len(review_rows)})
        for row in review_rows:
            require(isinstance(row, dict) and ENVELOPE <= set(row) <= ENVELOPE | {"reviewerId"},
                    "review_envelope_fields_differ")
            rid = row["id"]
            require(rid in templates and rid not in reviewed, "unknown_or_duplicate_review_id")
            source = templates[rid]["input"]
            require(row["sourceSha256"] == source["sourceSha256"] and row["preparedManifestSha256"] == manifest_sha
                    and row["packetSha256"] == packet_hashes[rid] and row["reviewerType"] == "assistant"
                    and row["humanReviewed"] is False, "review_identity_or_provenance_differs")
            if "reviewerId" in row:
                require(isinstance(row["reviewerId"], str) and 1 <= len(row["reviewerId"].strip()) <= 120,
                        "invalid_reviewer_id")
            judgments = row["judgments"]
            require(isinstance(judgments, list) and len(judgments) == system_count
                    and [j.get("label") for j in judgments] == labels, "judgment_label_coverage_differs")
            for judgment, candidate in zip(judgments, templates[rid]["candidates"]):
                check_judgment(judgment, source, candidate)
            reviewed[rid] = row
    require(set(reviewed) == set(ids), "all_18_sources_and_all_model_judgments_required")

    # This is the first access to the key or any producer directory. A missing,
    # malformed or incomplete blind judgment fails before this line.
    key = evidence.read(prepared / "review-key.json", manifest["keySha256"])
    require(isinstance(key, dict) and set(key) == {"systems", "mapping"}
            and isinstance(key["systems"], list) and len(key["systems"]) == system_count, "key_system_inventory_differs")
    system_records, directories = [], []
    for system in key["systems"]:
        require(set(system) == {"directory", "predictionsSha256", "summarySha256"}, "system_identity_fields_differ")
        directory = local(system["directory"], root)
        require(directory.is_relative_to(root / ".training/comparisons") and directory not in directories
                and not output.is_relative_to(directory) and not any(p.is_relative_to(directory) for p in paths),
                "invalid_or_duplicate_producer_directory")
        directories.append(directory)
        summary = evidence.read(directory / "summary.json", system["summarySha256"])
        predictions = evidence.read(directory / "predictions.jsonl", system["predictionsSha256"], jsonl=True)
        normalized_summary = check_producer_input(summary, system["predictionsSha256"], manifest)
        require([row.get("id") for row in predictions] == ids, "producer_prediction_coverage_differs")
        for source, prediction in zip(dataset, predictions):
            require(prediction.get("status") == "completed" and prediction.get("sourceSha256") == source["sourceSha256"]
                    and prediction.get("contextSha256") == source["contextSha256"]
                    and isinstance(prediction.get("translation"), str)
                    and prediction.get("targetSha256") == hash_bytes(prediction["translation"].encode("utf-8")),
                    "producer_prediction_identity_differs")
        system_records.append({"systemIndex": len(system_records), "directory": str(directory),
                               "predictionsSha256": system["predictionsSha256"], "summarySha256": system["summarySha256"],
                               "runMetadataSha256": hash_bytes(canonical(summary)), "runMetadata": summary,
                               "summarySchema": normalized_summary["schema"],
                               "predictions": predictions})
    mapping = key["mapping"]
    require(isinstance(mapping, list) and len(mapping) == manifest["judgmentCount"], "key_mapping_count_differs")
    mapping_lookup = {}
    expected_order = [(rid, label) for rid in ids for label in labels]
    require([(m.get("id"), m.get("label")) for m in mapping] == expected_order, "key_mapping_order_or_coverage_differs")
    for item in mapping:
        require(set(item) == {"id", "label", "systemIndex", "targetSha256"}
                and type(item["systemIndex"]) is int and item["systemIndex"] in range(system_count), "invalid_key_mapping")
        rid, label, index = item["id"], item["label"], item["systemIndex"]
        candidate = templates[rid]["candidates"][ord(label) - ord("A")]
        prediction = system_records[index]["predictions"][ids.index(rid)]
        require(item["targetSha256"] == candidate["targetSha256"] == prediction["targetSha256"]
                and candidate["translation"] == prediction["translation"], "key_candidate_producer_mismatch")
        mapping_lookup[(rid, label)] = index
    require(all({mapping_lookup[(rid, label)] for label in labels} == set(range(system_count)) for rid in ids),
            "key_row_does_not_cover_each_system_once")
    pairs = defaultdict(list)
    for source in dataset:
        if source.get("pair"):
            pairs[source["pair"]["id"]].append(source["id"])
    require(len(pairs) == 6 and all(len(members) == 2 for members in pairs.values()), "six_complete_contrast_pairs_required")
    source_lookup = {source["id"]: source for source in dataset}
    for members in pairs.values():
        a, b = members
        require(source_lookup[a]["pair"]["contrastWith"] == b and source_lookup[b]["pair"]["contrastWith"] == a,
                "nonreciprocal_pair_annotation")
    results, judgments_by_system = [], [{} for _ in range(system_count)]
    for record in system_records:
        result = {k: v for k, v in record.items() if k != "predictions"}
        result.update(counts=counts(), byDomain={d: counts() for d in ("finance", "general")},
                      bySourceCategory={}, byErrorCategory={})
        results.append(result)
    evidence_rows = []
    for rid in ids:
        source = source_lookup[rid]
        for judgment in reviewed[rid]["judgments"]:
            index = mapping_lookup[(rid, judgment["label"])]
            result = results[index]
            judgments_by_system[index][rid] = judgment
            add_count(result["counts"], judgment)
            add_count(result["byDomain"][source["domain"]], judgment)
            add_count(result["bySourceCategory"].setdefault(source["category"], counts()), judgment)
            categories = {error["category"] for error in judgment["errors"]}
            for category in categories:
                value = result["byErrorCategory"].setdefault(category, {"rows": 0, "materialRows": 0, "maxSeverity": 0})
                severity = max(e["severity"] for e in judgment["errors"] if e["category"] == category)
                value["rows"] += 1
                value["materialRows"] += int(severity >= 2)
                value["maxSeverity"] = max(value["maxSeverity"], severity)
            evidence_rows.append({"id": rid, "domain": source["domain"], "category": source["category"],
                                  "systemIndex": index, "sourceSha256": source["sourceSha256"],
                                  "reviewerId": reviewed[rid].get("reviewerId"), "judgment": judgment})
    for index, result in enumerate(results):
        result["contrastPairs"] = [{"id": pair_id, "sourceIds": members,
            "contrastPreservedByRow": {rid: judgments_by_system[index][rid]["contrastPreserved"] for rid in members},
            "bothPreserved": all(judgments_by_system[index][rid]["contrastPreserved"] for rid in members)}
            for pair_id, members in pairs.items()]
        result["completeContrastPairsPreserved"] = sum(p["bothPreserved"] for p in result["contrastPairs"])
        categories = result["byErrorCategory"]
        worst = max(((c["materialRows"], c["maxSeverity"], c["rows"]) for c in categories.values()), default=None)
        result["worstErrorCategories"] = sorted(k for k, c in categories.items()
            if (c["materialRows"], c["maxSeverity"], c["rows"]) == worst)
    output_data = {"version": VERSION, "completed": True, "humanReviewed": False,
        "reviewerType": "assistant", "sourceCount": 18, "systemCount": system_count,
        "judgmentCount": manifest["judgmentCount"],
        "preparedManifestSha256": manifest_sha, "inputSha256": manifest["inputSha256"],
        "datasetManifestSha256": manifest["manifestSha256"], "packetFiles": files,
        "sourceAuditSha256": source_audit_sha,
        "keySha256": manifest["keySha256"], "reviewFiles": review_records,
        "systems": results, "evidence": evidence_rows,
        "codeSha256": code_sha, "reviewCodeFiles": review_codes,
        "validatedFiles": {str(p): h for p, h in evidence.files.items()},
        "automaticGateCreated": False,
        "promotionDecision": None, "inferencePerformed": False, "modelWeightsRead": False,
        "limitations": ["Assistant judgments of 18 development sources, not human gold or a final holdout.",
            "The 6 contrast pairs are dependent observations; do not treat the 18 sources as independent statistical samples.",
            "Term sense, canonical spelling and their joint count are separate; no 90% gate or promotion threshold is created.",
            "General-domain coverage is small. Counts cannot certify a translation-quality lower bound.",
            "Source/packet/review/key/producer bytes are checked. Stored run identity is preserved without rereading model weights."]}
    evidence.unchanged()
    require(hash_bytes(Path(__file__).read_bytes()) == code_sha, "scorer_code_changed_during_aggregation")
    require(review_code_inventory() == review_codes, "review_code_changed_during_aggregation")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(json.dumps(output_data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    return output_data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.prepared, args.reviews, args.output)
    print(json.dumps({"completed": result["completed"], "sources": 18, "judgments": result["judgmentCount"],
                      "output": str(args.output), "automaticGateCreated": False, "humanReviewed": False}))


if __name__ == "__main__":
    main()
