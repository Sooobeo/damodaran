"""Aggregate completed assistant judgments bound to immutable v5 A/B artifacts.

Only prepared review artifacts and review JSONL files are read. No original
test corpus, prediction cache, model weights, inference engine, or training gate
is opened or executed. Each reviewed row is a copy of the anonymous template,
with reviewerType='assistant', inputSha256, reviewManifestSha256, and completed
choice.review fields. fluency is a boolean; severity is an integer from 0 to 3.
Null judgments or incomplete/duplicate coverage reject the entire aggregation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys

APP_ROOT = Path(__file__).resolve().parents[2]
VERSION = "finance-v5-assistant-review-summary-v1"
PREPARED_VERSION = "finance-v5-anonymous-assistant-review-v1"
RANDOMIZATION_SEED = "finance-v5-two-model-labels-2026-09-10-v1"
BOOLEAN_FIELDS = ("meaningPreserved", "negationAndConditionsPreserved", "contextualWordSensePreserved",
                  "omission", "unsupportedAddition", "quantityOrFormulaError", "fluency")
JUDGMENT_FIELDS = {"severity", *BOOLEAN_FIELDS, "evidence"}
MODELS = ("baseline", "candidate")
LIMITATION = (
    "도우미가 원문에 근거해 작성한 주관적 의미 검토의 집계이며 사람 검수나 전문 번역 정확도 인증이 아닙니다. "
    "참조 번역도 도우미 작성·사람 미검수 자료입니다. 판단 개수는 자동 통과 기준이나 모델 승격 결정이 아닙니다. "
    "소비한 최종 시험의 결과를 재학습·모델 재선택·기준 완화에 사용하지 않습니다."
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def text_hash(text):
    return digest(text.encode("utf-8"))


def canonical_hash(value):
    return text_hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def strict_json(content):
    def object_pairs(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, "Duplicate JSON object field: " + key)
            value[key] = item
        return value

    def invalid_constant(value):
        raise ValueError("Nonfinite JSON constant is not allowed: " + value)

    return json.loads(content, object_pairs_hook=object_pairs, parse_constant=invalid_constant)


def checked_path(root, value, boundary, must_exist=True):
    require(isinstance(value, (str, Path)) and bool(str(value)), "Missing artifact path")
    path = root / value
    resolved = path.resolve()
    require(resolved.is_relative_to(boundary.resolve()), "Artifact path escaped the allowed run directory")
    require(not any(part.is_symlink() for part in (path, *path.parents)), "Linked artifact paths are not allowed")
    if must_exist:
        require(resolved.is_file(), "Artifact must be an existing regular file")
    return resolved


def parse_jsonl(content, name):
    rows = [strict_json(line) for line in content.decode("utf-8-sig").splitlines() if line.strip()]
    require(bool(rows) and all(isinstance(row, dict) for row in rows), "Empty or malformed JSONL: " + name)
    return rows


def checked_judgment(value, identifier, label):
    require(isinstance(value, dict) and set(value) == JUDGMENT_FIELDS, "Judgment schema mismatch: " + identifier + "/" + label)
    require(all(value[field] is not None for field in JUDGMENT_FIELDS), "Unresolved judgment: " + identifier + "/" + label)
    require(type(value["severity"]) is int and 0 <= value["severity"] <= 3, "Severity must be an integer from 0 through 3")
    require(all(type(value[field]) is bool for field in BOOLEAN_FIELDS), "Judgment flags and fluency must be booleans")
    require(isinstance(value["evidence"], str) and bool(value["evidence"].strip()), "Source-grounded review evidence is required")
    return value


def empty_counts():
    return {"judgedChoices": 0, "severity": {str(value): 0 for value in range(4)}, "severityAtLeast2": 0,
            **{field: {"true": 0, "false": 0} for field in BOOLEAN_FIELDS}}


def add_judgment(counts, judgment):
    counts["judgedChoices"] += 1
    counts["severity"][str(judgment["severity"])] += 1
    counts["severityAtLeast2"] += judgment["severity"] >= 2
    for field in BOOLEAN_FIELDS:
        counts[field]["true" if judgment[field] else "false"] += 1


def summarize(run_id, review_files, output=None, app_root=APP_ROOT):
    require(isinstance(run_id, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id)), "Invalid run ID")
    root = Path(app_root).resolve()
    directory = root / ".training/runs" / run_id
    prepared = directory / "assistant-review-v5"
    manifest_path = checked_path(root, prepared / "manifest.json", prepared)
    anonymous_path = checked_path(root, prepared / "anonymous-review.jsonl", prepared)
    key_path = checked_path(root, prepared / "review-key.json", prepared)
    output_path = checked_path(root, output or prepared / "summary.json", prepared, must_exist=False)
    require(output_path.suffix == ".json" and output_path.parent.is_dir(), "Output must be a JSON file in an existing review directory")
    require(output_path not in {manifest_path, anonymous_path, key_path}, "Output may not replace a prepared artifact")
    require(isinstance(review_files, (list, tuple)) and bool(review_files), "At least one review JSONL file is required")
    files = [checked_path(root, path, directory) for path in review_files]
    require(len(set(files)) == len(files), "Duplicate review file path")
    require(all(path.suffix == ".jsonl" and path != anonymous_path and path != output_path for path in files),
            "Use separate review JSONL files, not a template or output artifact")
    snapshots = {}

    def read(path):
        content = path.read_bytes()
        snapshots[path] = digest(content)
        return content

    manifest_bytes = read(manifest_path)
    manifest_hash = digest(manifest_bytes)
    manifest = strict_json(manifest_bytes.decode("utf-8-sig"))
    require(isinstance(manifest, dict) and manifest.get("version") == PREPARED_VERSION
            and manifest.get("status") == "complete" and manifest.get("humanReviewed") is False
            and manifest.get("inferencePerformed") is False and manifest.get("glossaryApplied") is False,
            "Expected a complete, raw, assistant-only prepared manifest")
    identity = manifest.get("identity")
    require(isinstance(identity, dict) and identity.get("version") == PREPARED_VERSION
            and identity.get("randomizationSeed") == RANDOMIZATION_SEED
            and manifest.get("identitySha256") == canonical_hash(identity)
            and isinstance(identity.get("inputs"), dict)
            and identity["inputs"].get("runId") == run_id, "Prepared identity/run mismatch")
    original_hashes = identity.get("inputs", {}).get("files")
    require(isinstance(original_hashes, dict) and bool(original_hashes)
            and all(isinstance(name, str) and valid_hash(value) for name, value in original_hashes.items()),
            "Missing prepared input hash inventory")
    expected_files = manifest.get("files", {})
    require(isinstance(expected_files, dict) and set(expected_files) == {"anonymous-review.jsonl", "review-key.json"}
            and all(valid_hash(value) for value in expected_files.values()), "Prepared output hash inventory mismatch")
    scale = manifest.get("severityScale")
    require(isinstance(scale, dict) and set(scale) == {"0", "1", "2", "3"}
            and all(isinstance(value, str) and bool(value.strip()) for value in scale.values()), "Prepared severity scale mismatch")
    anonymous_bytes = read(anonymous_path)
    anonymous_hash = digest(anonymous_bytes)
    require(anonymous_hash == expected_files[anonymous_path.name], "Anonymous template hash mismatch")
    templates = parse_jsonl(anonymous_bytes, anonymous_path.name)
    require(type(manifest.get("rowCount")) is int and manifest["rowCount"] == len(templates), "Prepared row count mismatch")
    lookup = {}
    for row in templates:
        rid = row.get("id")
        require(isinstance(rid, str) and bool(rid) and rid not in lookup, "Duplicate or invalid anonymous ID")
        require(isinstance(row.get("source"), str) and bool(row["source"].strip())
                and row.get("sourceSha256") == text_hash(row["source"])
                and row.get("domain") in ("finance", "general") and row.get("humanReviewed") is False
                and row.get("referenceHumanReviewed") is False and row.get("reviewerType") is None,
                "Anonymous source/provenance mismatch")
        choices = row.get("choices")
        require(isinstance(choices, list) and len(choices) == 2
                and all(isinstance(choice, dict) for choice in choices)
                and [choice.get("label") for choice in choices] == ["A", "B"]
                and all(set(choice) == {"label", "translation", "review"}
                        and isinstance(choice["translation"], str) for choice in choices), "Anonymous A/B choice schema mismatch")
        for choice in choices:
            require(isinstance(choice["review"], dict) and set(choice["review"]) == JUDGMENT_FIELDS
                    and choice["review"]["evidence"] == ""
                    and all(choice["review"][field] is None for field in JUDGMENT_FIELDS - {"evidence"}),
                    "Anonymous template must contain only blank judgments")
        lookup[rid] = row

    reviewed, file_records = {}, []
    for path in sorted(files):
        content = read(path)
        rows = parse_jsonl(content, path.name)
        file_records.append({"path": path.relative_to(root).as_posix(), "sha256": digest(content), "rowCount": len(rows)})
        for row in rows:
            rid = row.get("id")
            require(isinstance(rid, str) and rid in lookup, "Review contains an unknown ID")
            require(rid not in reviewed, "Duplicate reviewed ID: " + rid)
            require(row.get("inputSha256") == anonymous_hash and row.get("reviewManifestSha256") == manifest_hash,
                    "Review is not bound to the prepared input hashes")
            require(row.get("reviewerType") == "assistant" and row.get("humanReviewed") is False,
                    "Every review must identify an assistant, not human review")
            template = lookup[rid]
            required = set(template) | {"inputSha256", "reviewManifestSha256"}
            require(required <= set(row) <= required | {"reviewerId"}, "Reviewed row fields differ from the template contract")
            if "reviewerId" in row:
                require(isinstance(row["reviewerId"], str) and bool(row["reviewerId"].strip()), "Invalid optional reviewer ID")
            for field in set(template) - {"choices", "reviewerType"}:
                require(row[field] == template[field], "Reviewed source/reference/context changed: " + rid + "/" + field)
            choices = row.get("choices")
            require(isinstance(choices, list) and len(choices) == 2
                    and all(isinstance(choice, dict) for choice in choices)
                    and [choice.get("label") for choice in choices] == ["A", "B"], "Missing, extra, reordered, or duplicate review labels")
            for choice, original in zip(choices, template["choices"]):
                require(set(choice) == {"label", "translation", "review"}
                        and choice.get("translation") == original["translation"], "Reviewed raw translation changed: " + rid)
                checked_judgment(choice["review"], rid, choice["label"])
            reviewed[rid] = row
    require(set(reviewed) == set(lookup), "Review coverage is incomplete; no partial aggregate is written")

    # Unblind only after every judgment has passed coverage and immutability checks.
    key_bytes = read(key_path)
    require(digest(key_bytes) == expected_files[key_path.name], "Review key hash mismatch")
    key = strict_json(key_bytes.decode("utf-8-sig"))
    require(isinstance(key, dict) and key.get("version") == PREPARED_VERSION
            and isinstance(key.get("mappings"), list) and len(key["mappings"]) == len(templates)
            and all(isinstance(item, dict) for item in key["mappings"]), "Review key schema mismatch")
    require([item.get("id") for item in key["mappings"]] == [row["id"] for row in templates], "Review key ID/order/coverage mismatch")
    totals = {name: {**empty_counts(), "byDomain": {domain: empty_counts() for domain in ("finance", "general")}}
              for name in MODELS}
    findings = []
    for template, mapping in zip(templates, key["mappings"]):
        labels = mapping.get("labels")
        require(isinstance(labels, dict) and set(labels) == {"A", "B"}
                and all(isinstance(value, str) for value in labels.values())
                and set(labels.values()) == set(MODELS), "Review key must map each label to a different model")
        row = reviewed[template["id"]]
        for choice in row["choices"]:
            model = labels[choice["label"]]
            judgment = choice["review"]
            add_judgment(totals[model], judgment)
            add_judgment(totals[model]["byDomain"][template["domain"]], judgment)
            if (judgment["severity"] > 0 or any(not judgment[field] for field in
                    ("meaningPreserved", "negationAndConditionsPreserved", "contextualWordSensePreserved", "fluency"))
                    or any(judgment[field] for field in ("omission", "unsupportedAddition", "quantityOrFormulaError"))):
                findings.append({"id": row["id"], "domain": row["domain"], "primaryTermId": row.get("primaryTermId"),
                                 "model": model, "anonymousLabel": choice["label"], "source": row["source"],
                                 "translation": choice["translation"], "review": judgment})
    result = {"version": VERSION, "runId": run_id, "completed": True, "reviewerType": "assistant", "humanReviewed": False,
              "rowCount": len(templates), "judgedChoices": 2 * len(templates), "unresolvedJudgments": 0,
              "domains": dict(Counter(row["domain"] for row in templates)), "models": totals, "findings": findings,
              "preparedManifestSha256": manifest_hash, "preparedIdentitySha256": manifest["identitySha256"],
              "anonymousSha256": anonymous_hash, "keySha256": digest(key_bytes), "reviews": file_records,
              "codeSha256": digest(Path(__file__).read_bytes()), "severityScale": manifest.get("severityScale"),
              "automaticGateCreated": False, "promotionDecision": None, "inferencePerformed": False,
              "originalModelOrTestFilesRead": False, "limitation": LIMITATION}
    content = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    # Detect changed artifacts between validation and publication without opening
    # any of the original model/test paths declared in the prepared manifest.
    for path, expected in snapshots.items():
        checked_path(root, path, directory)
        require(digest(path.read_bytes()) == expected, "Review input changed during aggregation")
    if output_path.exists():
        require(output_path.read_bytes() == content, "Existing immutable summary differs; overwriting is prohibited")
    else:
        # Exclusive creation prevents overwrites even with concurrent aggregators.
        # A partial file after interruption is preserved and rejected on retry.
        with output_path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    return output_path


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--reviews", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    path = summarize(args.run_id, args.reviews, args.output)
    print(json.dumps({"event": "v5-assistant-review-summarized", "path": str(path),
                      "sha256": digest(path.read_bytes()), "humanReviewed": False,
                      "automaticGateCreated": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
