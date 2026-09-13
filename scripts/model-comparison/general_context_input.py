"""Read the frozen general16 development input; annotations never leave this reader."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "content/model-comparison/general-context-dev-20260911.jsonl"
MANIFEST = INPUT.with_name("general-context-dev-20260911-manifest.json")
INPUT_SHA = "4d63cbbd7299835849cdf750813e7c18a051ae98149a8ec51b73d5e8057cdfd4"
MANIFEST_SHA = "ee8191331de4c169cd0a077c112bbb9773fbae82f5f403141f91b918e56bb6e2"
IDS = [f"GCTX26-{i:03d}" for i in range(1, 17)]
FIELDS = ("id", "source", "context", "domain", "sourceSha256", "contextSha256")


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: require(False, "nonfinite_json"))


def validate_bytes(raw, manifest_raw, *, expected_input_sha=INPUT_SHA,
                   expected_manifest_sha=MANIFEST_SHA):
    """Explicit digest arguments exist for synthetic tests, never as CLI overrides."""
    require(sha(raw) == expected_input_sha, "frozen_input_changed")
    require(sha(manifest_raw) == expected_manifest_sha, "frozen_manifest_changed")
    manifest = parse(manifest_raw)
    require(manifest.get("version") == "general-context-dev-20260911-v1"
            and manifest.get("datasetFile") == INPUT.name
            and manifest.get("datasetSha256") == sha(raw), "manifest_identity")
    require(manifest.get("sourceCount") == 16 and manifest.get("selectedIds") == IDS,
            "manifest_coverage")
    require(manifest.get("trainingUseAllowed") is False
            and manifest.get("humanReviewed") is False
            and manifest.get("finalHoldout") is False
            and manifest.get("split") == "development", "manifest_scope")
    audit = manifest.get("sourceAudit", {})
    require(audit.get("completed") is True and audit.get("newCandidateOutputsViewed") is False
            and audit.get("humanReviewed") is False, "source_audit_missing")
    rows = [parse(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    require(len(rows) == 16 and [r.get("id") for r in rows] == IDS, "input_coverage")
    require(Counter(r.get("domain") for r in rows) == {"general": 12, "general_context_polysemy": 4},
            "input_domains")
    findings = audit.get("findings", [])
    require([f.get("id") for f in findings] == IDS, "audit_coverage")
    clean = []
    for row, finding in zip(rows, findings, strict=True):
        require(row.get("humanReviewed") is False and row.get("trainingUseAllowed") is False
                and row.get("finalHoldout") is False and row.get("split") == "development"
                and row.get("provenance") == "assistant-authored-development", "row_scope")
        source, context = row.get("source"), row.get("context")
        require(isinstance(source, str) and 1 <= len(source.strip()) <= 12000
                and isinstance(context, str) and len(context) <= 12000, "input_text_range")
        for text in (source, context):
            require("\x00" not in text and not re.search(
                r"<\|[^\n>]*\|>|<｜[^\n>]*｜>|</?(?:think|answer|tool[^>]*)>|<eos:[^>]*>", text),
                "input_control_token")
        for field, value in (("sourceSha256", source), ("contextSha256", context)):
            require(row.get(field) == sha(value.encode("utf-8")) == finding.get(field), "row_hash")
        require(finding.get("wholeRowExclusionRecommended") is False, "audit_exclusion")
        # No referenceKo, checks, audit, or other annotation can reach a producer.
        clean.append({key: row[key] for key in FIELDS})
    return clean


def read_input(path=INPUT):
    path = Path(path)
    require(path.resolve() == INPUT.resolve() and not path.is_symlink(), "fixed_input_path_required")
    require(not MANIFEST.is_symlink(), "linked_manifest")
    raw, manifest_raw = path.read_bytes(), MANIFEST.read_bytes()
    require(len(raw) <= 2 * 1024 ** 2 and len(manifest_raw) <= 2 * 1024 ** 2, "input_size_limit")
    rows = validate_bytes(raw, manifest_raw)
    return rows, {"inputPath": str(path.resolve()), "inputSha256": sha(raw),
                  "manifestPath": str(MANIFEST.resolve()), "manifestSha256": sha(manifest_raw),
                  "selectedIds": IDS, "sourceCount": 16, "inputFieldsAllowed": list(FIELDS),
                  "referencesUsedForInference": False, "checksUsedForInference": False,
                  "trainingUseAllowed": False, "humanReviewed": False}


def assert_unchanged(identity):
    for path_key, hash_key in (("inputPath", "inputSha256"), ("manifestPath", "manifestSha256")):
        require(sha(Path(identity[path_key]).read_bytes()) == identity[hash_key], "input_changed_during_run")
