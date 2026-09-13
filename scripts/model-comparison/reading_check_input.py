"""Read six attributed public passages without exposing reference annotations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
VERSION = "real-reading-check-20260910-v1"
ALLOWLIST = ("id", "source", "context", "domain", "sourceSha256", "contextSha256")
SOURCE_FIELDS = set(ALLOWLIST) | {"split", "sourceWordCount", "humanReviewed", "provenance"}
PROVENANCE_FIELDS = {"sourceType", "resourceId", "author", "titleInDatabase", "sourceUrl",
    "sourceVersionId", "sourceBlockId", "sourceBlockSha256", "sortOrderZeroBased", "blockOrdinalOneBased",
    "paragraphOrdinalOneBased", "paragraphOrdinalDefinition", "originalPath", "originalFileSha256",
    "extractorVersion", "extractionConfigHash", "fetchedAt", "importedAt", "sourceTextAltered"}
REFERENCE_FIELDS = {"id", "sourceSha256", "referenceKo", "referenceSha256", "criticalPropositionsKo",
    "senseChecks", "referenceType", "humanReviewed", "onlyAcceptableTranslation", "referenceIsInferenceInput"}
_UNSET = object()


def require(value, message):
    if not value:
        raise ValueError(message)


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def is_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def plain(path):
    path = Path(path)
    require(".." not in path.parts, "parent_path_not_allowed")
    path = path if path.is_absolute() else ROOT / path
    require(not any(p.is_symlink() for p in (path, *path.parents)), "symlink_path_not_allowed")
    result = path.resolve()
    require(result.is_relative_to(ROOT.resolve()), "reading_input_outside_workspace")
    return result


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("nonfinite_json")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


class Evidence:
    def __init__(self):
        self.files = {}

    def read(self, path, expected=_UNSET, *, jsonl=False):
        path = plain(path)
        require(path.is_file() and path.stat().st_size <= 2 * 1024 * 1024, "invalid_or_large_reading_file")
        raw = path.read_bytes()
        value = hashlib.sha256(raw).hexdigest()
        if expected is not _UNSET:
            require(is_sha(expected) and expected == value, "reading_file_hash_mismatch")
        require(path not in self.files or self.files[path] == value, "reading_file_changed")
        self.files[path] = value
        text = raw.decode("utf-8")
        return ([strict_json(line) for line in text.splitlines() if line.strip()]
                if jsonl else strict_json(text))

    def original(self, relative, expected):
        require(isinstance(relative, str) and is_sha(expected), "original_provenance_missing")
        relative_path = Path(relative)
        require(not relative_path.is_absolute(), "original_path_must_be_relative")
        path = plain(relative_path)
        require(path.is_relative_to((ROOT / "data/originals").resolve())
                and path.name == expected + ".html" and path.is_file()
                and path.stat().st_size <= 16 * 1024 * 1024, "invalid_public_original_path")
        if path not in self.files:
            self.files[path] = digest(path)
        require(self.files[path] == expected, "public_original_hash_mismatch")

    def unchanged(self):
        for path, expected in self.files.items():
            require(plain(path).is_file() and digest(path) == expected, "reading_evidence_changed_during_validation")


def read_reading_check(path, ids=None):
    """Return the standard clean row allowlist and an honestly named identity."""
    code_path = Path(__file__).resolve()
    code_sha = digest(code_path)
    path = plain(path)
    require(path.name == "sources.jsonl" and path.is_relative_to((ROOT / "content/model-comparison").resolve()),
            "reading_source_file_required")
    evidence = Evidence()
    manifest_path = path.with_name("dataset-manifest.json")
    manifest = evidence.read(manifest_path)
    require(isinstance(manifest, dict) and manifest.get("version") == VERSION
            and manifest.get("status") == "frozen" and manifest.get("humanReviewed") is False
            and manifest.get("referencesHumanReviewed") is False and manifest.get("selectionSawModelOutputs") is False
            and manifest.get("inferenceInputFields") == list(ALLOWLIST)
            and manifest.get("evaluationFileExcludedFromInference") == "assistant-reference.jsonl",
            "real_reading_manifest_contract_differs")
    declaration = manifest.get("dataset")
    require(isinstance(declaration, dict) and set(declaration) == {"file", "sha256", "count", "ids"}
            and declaration["file"] == "sources.jsonl" and type(declaration["count"]) is int
            and declaration["count"] == 6 and is_sha(declaration["sha256"]), "six_reading_sources_required")
    rows = evidence.read(path, declaration["sha256"], jsonl=True)
    expected_ids = [f"REAL26-{i:03d}" for i in range(1, 7)]
    require(len(rows) == 6 and all(isinstance(row, dict) for row in rows)
            and [row.get("id") for row in rows] == declaration["ids"] == expected_ids,
            "reading_source_ids_or_order_differ")
    require(manifest.get("wordTokenPattern") == "[a-z0-9]+(?:'[a-z]+)?", "reading_word_count_rule_differs")
    clean, block_ids, resources = [], set(), set()
    for row in rows:
        require(set(row) == SOURCE_FIELDS and row.get("split") == "real_reading_followup"
                and row.get("domain") == "finance" and row.get("humanReviewed") is False,
                "source_file_must_exclude_evaluation_annotations")
        source, context = row["source"], row["context"]
        require(isinstance(source, str) and 1 <= len(source.strip()) <= 12000
                and isinstance(context, str) and len(context) <= 12000
                and row["sourceSha256"] == text_hash(source) and row["contextSha256"] == text_hash(context),
                "reading_source_or_context_hash_differs")
        require(not any("\x00" in value or "\ufffd" in value or
            re.search(r"<\|[^\n>]*\|>|<(?:bos|eos|start_of_turn|end_of_turn|start_of_image|pad)>", value)
            for value in (source, context)), "reading_source_contains_control_or_encoding_damage")
        # The frozen sourceWordCount was recorded with this literal regex on
        # lower-cased, otherwise unchanged source (including curly apostrophes).
        count = len(re.findall(manifest["wordTokenPattern"], source.lower()))
        require(type(row["sourceWordCount"]) is int and row["sourceWordCount"] == count and 60 <= count <= 140,
                "reading_source_word_count_differs")
        provenance = row["provenance"]
        require(isinstance(provenance, dict) and set(provenance) == PROVENANCE_FIELDS
                and provenance["sourceType"] == "stored_public_original" and provenance["sourceTextAltered"] is False
                and provenance["sourceBlockSha256"] == row["sourceSha256"]
                and provenance["extractorVersion"] == "structured-v2" and is_sha(provenance["extractionConfigHash"]),
                "public_original_provenance_differs")
        for field in ("resourceId", "author", "titleInDatabase", "sourceUrl", "paragraphOrdinalDefinition",
                      "fetchedAt", "importedAt"):
            require(isinstance(provenance[field], str) and provenance[field].strip(), "public_source_attribution_missing")
        url = urlsplit(provenance["sourceUrl"])
        require(url.scheme == "https" and url.hostname == "pages.stern.nyu.edu" and url.port in (None, 443)
                and url.username is None and url.password is None and url.path.startswith("/~adamodar/"),
                "public_source_url_outside_attributed_collection")
        for field in ("sourceVersionId", "sourceBlockId"):
            require(isinstance(provenance[field], str) and
                    re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", provenance[field]),
                    "public_source_version_or_block_missing")
        require(type(provenance["sortOrderZeroBased"]) is int and provenance["sortOrderZeroBased"] >= 0
                and type(provenance["blockOrdinalOneBased"]) is int
                and provenance["blockOrdinalOneBased"] == provenance["sortOrderZeroBased"] + 1
                and type(provenance["paragraphOrdinalOneBased"]) is int and provenance["paragraphOrdinalOneBased"] > 0,
                "public_source_position_invalid")
        require(provenance["sourceBlockId"] not in block_ids, "duplicate_public_source_block")
        block_ids.add(provenance["sourceBlockId"])
        resources.add(provenance["resourceId"])
        evidence.original(provenance["originalPath"], provenance["originalFileSha256"])
        clean.append({key: row[key] for key in ALLOWLIST})
    order = manifest.get("selectionOrder")
    require(isinstance(order, dict) and isinstance(order.get("selectedResourceIds"), list)
            and set(order["selectedResourceIds"]) == resources, "selected_public_resources_differ")
    reference_info = manifest.get("references")
    require(isinstance(reference_info, dict) and set(reference_info) == {"file", "sha256", "count"}
            and reference_info["file"] == "assistant-reference.jsonl" and type(reference_info["count"]) is int
            and reference_info["count"] == 6 and is_sha(reference_info["sha256"]), "reading_reference_manifest_differs")
    references = evidence.read(path.with_name("assistant-reference.jsonl"), reference_info["sha256"], jsonl=True)
    require(len(references) == 6 and all(isinstance(row, dict) for row in references)
            and [row.get("id") for row in references] == expected_ids, "reading_reference_coverage_differs")
    for source, reference in zip(rows, references):
        require(set(reference) == REFERENCE_FIELDS and reference["sourceSha256"] == source["sourceSha256"]
                and isinstance(reference["referenceKo"], str) and reference["referenceKo"].strip()
                and reference["referenceSha256"] == text_hash(reference["referenceKo"])
                and reference["referenceType"] == "assistant_authored_unreviewed"
                and reference["humanReviewed"] is False and reference["onlyAcceptableTranslation"] is False
                and reference["referenceIsInferenceInput"] is False,
                "reading_reference_identity_or_provenance_differs")
    advisory_info = manifest.get("reviewAdvisory")
    require(isinstance(advisory_info, dict) and set(advisory_info) == {"file", "sha256", "evaluationOnly", "inferenceInput"}
            and advisory_info["file"] == "review-advisory.json" and is_sha(advisory_info["sha256"])
            and advisory_info["evaluationOnly"] is True and advisory_info["inferenceInput"] is False,
            "reading_advisory_manifest_differs")
    advisory = evidence.read(path.with_name("review-advisory.json"), advisory_info["sha256"])
    require(isinstance(advisory, dict) and advisory.get("version") == "real-reading-advisory-v1"
            and advisory.get("humanReviewed") is False and advisory.get("reviewerType") == "assistant"
            and advisory.get("preparedBeforeModelOutputs") is True and advisory.get("evaluationOnly") is True
            and advisory.get("inferenceInput") is False and isinstance(advisory.get("items"), list),
            "reading_advisory_provenance_differs")
    advisory_ids = [item.get("id") for item in advisory["items"] if isinstance(item, dict)]
    require(len(advisory_ids) == len(advisory["items"]) and len(set(advisory_ids)) == len(advisory_ids)
            and set(advisory_ids) <= set(expected_ids), "reading_advisory_ids_differ")
    if ids is not None:
        require(isinstance(ids, (list, tuple)) and bool(ids) and all(isinstance(rid, str) for rid in ids)
                and len(set(ids)) == len(ids) and set(ids) <= set(expected_ids), "invalid_reading_subset")
        clean = [row for row in clean if row["id"] in ids]
    evidence.unchanged()
    require(digest(code_path) == code_sha, "reading_reader_code_changed")
    return clean, {"inputPath": str(path), "inputSha256": evidence.files[path],
        "manifestPath": str(manifest_path), "manifestSha256": evidence.files[manifest_path],
        "totalRows": 6, "selectedIds": [row["id"] for row in clean], "humanReviewed": False,
        "purpose": "actual public reading follow-up; not independent final quality certification",
        "datasetVersion": VERSION, "sourceType": "stored_public_original", "referencesHumanReviewed": False,
        "wordCountMethod": "manifest.wordTokenPattern on source.lower(); source punctuation unchanged",
        "inputFiles": {str(p): sha for p, sha in evidence.files.items()}, "readerCodeFiles": {str(code_path): code_sha},
        "dbQueried": False, "originalReextracted": False, "modelOutputsRead": False}
