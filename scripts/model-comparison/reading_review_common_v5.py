"""Shared evidence checks for the six public-reading follow-up review only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

import reading_check_input as reader
import v3_review_evidence
import v4_review_evidence
import v5_review_evidence

ROOT = Path(__file__).resolve().parents[2]
PREPARED_VERSION = "real-reading-blind-review-packets-v5"
SUMMARY_VERSION = "real-reading-assistant-review-summary-v5"
INSTRUCTION = ("원문과 문맥, review-advisory.json을 먼저 읽고 각 후보의 의미 관계와 자연스러움을 대조한다. "
    "도우미 참조는 사람 검수본이나 유일한 정답이 아니다. 생략·추가·주체·부정·조건·수량·다의어를 원문에 근거해 판단한다. "
    "모델 이름·키·점수는 판단 완료 전에 읽지 않는다. 용어 표기 90% 검사나 최종 품질 인증이 아니다.")
CATEGORIES = {"semantic_role", "negation_condition", "quantity_formula", "word_sense",
    "discourse_reference", "omission", "unsupported_addition", "terminology", "modality", "fluency", "other"}
_UNSET = object()


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def local(path, root):
    path = Path(path)
    require(".." not in path.parts, "parent_path_not_allowed")
    path = path if path.is_absolute() else root / path
    require(not any(p.is_symlink() for p in (path, *path.parents)), "symlink_path_not_allowed")
    result = path.resolve()
    require(result.is_relative_to(root.resolve()), "path_outside_workspace")
    return result


class Evidence:
    def __init__(self, root):
        self.root, self.files = root, {}

    def raw(self, path, expected=_UNSET):
        path = local(path, self.root)
        require(path.is_file() and path.stat().st_size <= 16 * 1024 * 1024, "invalid_or_large_evidence_file")
        raw = path.read_bytes()
        value = sha(raw)
        if expected is not _UNSET:
            require(reader.is_sha(expected) and value == expected, "evidence_hash_mismatch")
        require(path not in self.files or self.files[path] == value, "evidence_changed_during_read")
        self.files[path] = value
        return raw

    def read(self, path, expected=_UNSET, *, jsonl=False):
        text = self.raw(path, expected).decode("utf-8")
        return ([reader.strict_json(line) for line in text.splitlines() if line.strip()]
                if jsonl else reader.strict_json(text))

    def unchanged(self):
        for path, expected in list(self.files.items()):
            require(local(path, self.root).is_file() and v4_review_evidence.stream_hash(path) == expected,
                    "evidence_hash_mismatch")


def code_inventory():
    paths = [Path(__file__).resolve(), Path(reader.__file__).resolve(), Path(v3_review_evidence.__file__).resolve(), Path(v4_review_evidence.__file__).resolve(), Path(v5_review_evidence.__file__).resolve()]
    paths += [Path(__file__).with_name(name) for name in
              ("prepare_reading_reviews_v5.py", "summarize_reading_reviews_v5.py")]
    return {str(path): reader.digest(path) for path in paths}


def assert_code_unchanged(expected):
    require(code_inventory() == expected, "review_code_changed")


def read_input(path, evidence):
    clean, identity = reader.read_reading_check(path)
    require(len(clean) == 6 and identity["totalRows"] == 6, "all_six_reading_rows_required")
    for name, expected in identity["inputFiles"].items():
        evidence.raw(name, expected)
    source = Path(identity["inputPath"])
    rows = evidence.read(source, identity["inputSha256"], jsonl=True)
    manifest = evidence.read(identity["manifestPath"], identity["manifestSha256"])
    references = evidence.read(source.with_name("assistant-reference.jsonl"), manifest["references"]["sha256"], jsonl=True)
    advisory = evidence.raw(source.with_name("review-advisory.json"), manifest["reviewAdvisory"]["sha256"])
    require([row["id"] for row in references] == [row["id"] for row in clean], "reference_coverage_differs")
    return clean, identity, rows, references, advisory


def check_input_identity(record, identity):
    fields = ("inputPath", "inputSha256", "manifestPath", "manifestSha256", "totalRows", "selectedIds",
              "datasetVersion", "sourceType", "humanReviewed", "referencesHumanReviewed", "inputFiles", "readerCodeFiles")
    require(isinstance(record, dict) and all(field in record for field in fields), "producer_input_identity_missing")
    require(type(record["totalRows"]) is int and record["totalRows"] == 6, "producer_input_count_differs")
    for field in fields:
        require(canonical(record[field]) == canonical(identity[field]), "producer_input_identity_differs")


def hash_inventory(value):
    return (isinstance(value, dict) and bool(value) and
            all(isinstance(name, str) and name.strip() and reader.is_sha(digest) for name, digest in value.items()))


def producer(directory, clean, identity, evidence, expected=_UNSET):
    directory = local(directory, evidence.root)
    require(directory.is_relative_to(evidence.root / ".training/comparisons"), "producer_outside_comparisons")
    if expected is not _UNSET:
        require(isinstance(expected, dict) and set(expected) == {"directory", "summarySha256", "predictionsSha256"}
                and expected["directory"] == str(directory), "producer_evidence_fields_differ")
    prediction_expected = _UNSET if expected is _UNSET else expected["predictionsSha256"]
    summary_expected = _UNSET if expected is _UNSET else expected["summarySha256"]
    predictions = evidence.read(directory / "predictions.jsonl", prediction_expected, jsonl=True)
    summary = evidence.read(directory / "summary.json", summary_expected)
    require(isinstance(summary, dict) and summary.get("status") == "completed", "producer_not_completed")
    require(hash_inventory(summary.get("codeHashes")), "producer_code_identity_missing")
    version = summary.get("version")
    prediction_sha = evidence.files[directory / "predictions.jsonl"]
    if version == v5_review_evidence.VERSION:
        v5_review_evidence.check_v5_evidence(summary, prediction_sha)
        v5_review_evidence.check_artifacts(directory, summary, predictions, evidence)
    if version in v4_review_evidence.VERSIONS:
        v4_review_evidence.check_v4_evidence(summary, prediction_sha)
        v5_review_evidence.check_artifacts(directory, summary, predictions, evidence)
    if version in v3_review_evidence.VERSIONS:
        v3_review_evidence.check_v3_evidence(summary, prediction_sha)
    if version in ("translategemma-large-screen-v2", "translategemma-large-screen-v3", "translategemma-large-screen-v4", v5_review_evidence.VERSION):
        require(all(type(summary.get(k)) is int and summary[k] == 6 for k in ("count", "recordedCount", "expectedCount"))
                and summary.get("integrityVerified") is True and summary.get("childProcessStopped") is True
                and summary.get("predictionsSha256") == prediction_sha
                and summary.get("modelSize") in ("12b", "27b") and summary.get("profile") == "source-only"
                and reader.is_sha(summary.get("modelSha256")) and reader.is_sha(summary.get("installationManifestSha256"))
                and not any(k in summary for k in ("completed", "totalRows", "inputSha256", "manifestSha256", "selectedIds")),
                "translategemma_completion_evidence_differs")
        check_input_identity(summary.get("input"), identity)
    else:
        require(version in (None, "hymt30-development-screen-v2", "hymt30-development-screen-v3", "hymt30-development-screen-v4"), "unsupported_producer_version")
        require(type(summary.get("completed")) is int and summary["completed"] == 6,
                "producer_completion_count_differs")
        check_input_identity(summary, identity)
        if version is None:
            deployed = summary.get("deployedIdentity")
            require(summary.get("model") == "Hy-MT2-7B-Q8_0" and summary.get("profile") == "contextual"
                    and reader.is_sha(summary.get("modelSha256")) and isinstance(deployed, str)
                    and re.fullmatch(r"hymt:[0-9a-f]{64}:[0-9a-f]{64}", deployed) is not None
                    and deployed.split(":")[1] == summary["modelSha256"]
                    and summary.get("childStopped") is True, "baseline_completion_identity_differs")
        else:
            model = summary.get("model")
            require(summary.get("childProcessStopped") is True and summary.get("modelLoaded") is True
                    and summary.get("profile") == "contextual"
                    and isinstance(model, dict) and reader.is_sha(model.get("sha256"))
                    and isinstance(model.get("name"), str) and bool(model["name"].strip())
                    and type(model.get("size")) is int and model["size"] > 0
                    and isinstance(summary.get("modelRevision"), str) and bool(summary["modelRevision"].strip())
                    and isinstance(summary.get("runtimeRevision"), str) and bool(summary["runtimeRevision"].strip())
                    and reader.is_sha(summary.get("installationManifestSha256"))
                    and summary.get("artifactHashes", {}).get("predictions.jsonl") == prediction_sha,
                    "hymt30_completion_evidence_differs")
    require(len(predictions) == 6 and all(isinstance(row, dict) for row in predictions)
            and [row.get("id") for row in predictions] == identity["selectedIds"], "producer_prediction_coverage_differs")
    for row, result in zip(clean, predictions):
        require(result.get("status") == "completed" and result.get("sourceSha256") == row["sourceSha256"]
                and result.get("contextSha256") == row["contextSha256"]
                and isinstance(result.get("translation"), str)
                and result.get("targetSha256") == reader.text_hash(result["translation"]), "producer_prediction_identity_differs")
    return {"directory": str(directory), "summarySha256": evidence.files[directory / "summary.json"],
            "predictionsSha256": prediction_sha, "summarySchema": version or "unversioned-hymt7-reading-baseline",
            "runMetadata": summary, "runMetadataSha256": sha(canonical(summary)), "predictions": predictions}


def write_bytes(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def write_json(path, value):
    write_bytes(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n")


def write_jsonl(path, rows):
    write_bytes(path, b"".join(canonical(row) + b"\n" for row in rows))


def korean_reason(value):
    return isinstance(value, str) and 1 <= len(value.strip()) <= 6000 and re.search(r"[가-힣]", value) is not None


def check_judgment(value, source, candidate):
    require(isinstance(value, dict) and set(value) == {"id", "label", "targetSha256", "severity", "fluent",
            "errors", "humanReviewed", "reasonKo"}, "judgment_fields_differ")
    require(value["id"] == source["id"] and value["label"] == candidate["label"]
            and value["targetSha256"] == candidate["targetSha256"], "judgment_target_identity_differs")
    require(value["humanReviewed"] is False and korean_reason(value["reasonKo"]), "judgment_evidence_missing")
    require(type(value["severity"]) is int and value["severity"] in range(4) and type(value["fluent"]) is bool,
            "judgment_severity_or_fluency_invalid")
    errors = value["errors"]
    require(isinstance(errors, list) and len(errors) <= 100, "invalid_error_list")
    for error in errors:
        require(isinstance(error, dict) and set(error) == {"severity", "category", "sourceSpan", "targetSpan", "reasonKo"},
                "error_fields_differ")
        require(type(error["severity"]) is int and error["severity"] in (1, 2, 3)
                and isinstance(error["category"], str) and error["category"] in CATEGORIES
                and korean_reason(error["reasonKo"]), "error_evidence_invalid")
        source_span, target_span = error["sourceSpan"], error["targetSpan"]
        require(isinstance(source_span, str) and source_span.strip()
                and (source_span in source["source"] or source_span in source["context"]), "source_error_span_absent")
        require(isinstance(target_span, str) and ((bool(target_span.strip()) and target_span in candidate["translation"])
                or (target_span == "" and error["category"] == "omission")), "target_error_span_absent")
    require(value["severity"] == max((e["severity"] for e in errors), default=0), "severity_not_max_errors")
