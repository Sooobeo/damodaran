"""Aggregate fully completed assistant reviews of the four-system quality probe.

Copy the immutable anonymous rows to separate JSONL files outside the prepared
folder. Keep source, context, reference, and each raw choice unchanged. Set
reviewerType='assistant', retain humanReviewed=false, and add per row the exact
anonymousInputSha256 and reviewManifestSha256. Fill all choice.review fields:
severity is an integer 0..3, seven flags (including fluency) are booleans, and
evidence is nonempty source-grounded text. Null judgments fail closed.

This command does not create a gate, infer translations, read weights, or read
any historical v5 test. Output is a separate immutable JSON file.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path

import summarize_quality as prepared

VERSION = "finance-quality-four-system-assistant-review-summary-v1"
FLAGS = ("meaningPreserved", "negationAndConditionsPreserved", "contextualWordSensePreserved",
         "omission", "unsupportedAddition", "quantityOrFormulaError", "fluency")
JUDGMENT_FIELDS = {"severity", "evidence", *FLAGS}
require = prepared.require


def check_judgment(value):
    require(isinstance(value, dict) and set(value) == JUDGMENT_FIELDS, "Judgment schema differs")
    require(all(item is not None for item in value.values()), "Unresolved judgment; partial reviews are not published")
    require(type(value["severity"]) is int and 0 <= value["severity"] <= 3, "Severity must be an integer from 0 through 3")
    require(all(type(value[name]) is bool for name in FLAGS), "Review flags and fluency must be booleans")
    require(isinstance(value["evidence"], str) and 1 <= len(value["evidence"].strip()) <= 12000,
            "Nonempty source-grounded evidence is required")
    return value


def empty_counts():
    return {"judgedChoices": 0, "severity": {str(i): 0 for i in range(4)}, "severityAtLeast2": 0,
            **{name: {"true": 0, "false": 0} for name in FLAGS}}


def add(counts, judgment):
    counts["judgedChoices"] += 1
    counts["severity"][str(judgment["severity"])] += 1
    counts["severityAtLeast2"] += judgment["severity"] >= 2
    for name in FLAGS:
        counts[name]["true" if judgment[name] else "false"] += 1


def summarize(review_directory, review_files, output_path, root=prepared.ROOT):
    directory = prepared.local(review_directory, root)
    manifest_path = directory / "manifest.json"
    inputs = prepared.Inputs()
    code_sha = inputs.record(Path(__file__))
    manifest = inputs.read(manifest_path)
    require(isinstance(manifest, dict) and manifest.get("version") == prepared.VERSION and
            manifest.get("status") == "complete" and manifest.get("rowCount") == 24 and
            manifest.get("humanReviewed") is False and manifest.get("qualityGateApplied") is False and
            manifest.get("inferencePerformed") is False, "Prepared review manifest differs")
    identity = manifest.get("identity")
    require(isinstance(identity, dict) and all(isinstance(identity.get(name), str) for name in
            ("inputPath", "resultsDirectory", "marianRunId")), "Missing prepared input identity")
    # Reuse the complete producer, source/context, raw translation, key and file
    # hash checks. summarize returns the same already-published artifact only.
    prepared.summarize(Path(identity["inputPath"]), Path(identity["resultsDirectory"]), directory,
                       identity["marianRunId"], root)
    inputs.record(manifest_path, inputs.files[str(manifest_path)])
    templates = inputs.read(directory / "anonymous-review.jsonl", jsonl=True)
    inputs.record(directory / "anonymous-review.jsonl", manifest["files"]["anonymous-review.jsonl"])
    require(len(templates) == 24 and all(isinstance(row, dict) for row in templates), "Anonymous coverage differs")
    anonymous_sha = inputs.files[str(directory / "anonymous-review.jsonl")]
    manifest_sha = inputs.files[str(manifest_path)]
    lookup = {row["id"]: row for row in templates}
    require(len(lookup) == 24, "Duplicate anonymous IDs")
    require(isinstance(review_files, (list, tuple)) and bool(review_files), "Review JSONL files are required")
    paths = [prepared.local(path, root) for path in review_files]
    require(len(set(paths)) == len(paths), "Duplicate review file path")
    output = prepared.local(output_path, root)
    require(output.suffix == ".json" and not output.is_relative_to(directory) and output not in paths,
            "Review summary may not replace prepared artifacts or input reviews")
    results_directory = prepared.local(Path(identity["resultsDirectory"]), root)
    require(output.is_relative_to(results_directory) and all(path.is_relative_to(results_directory) and
            not path.is_relative_to(directory) and path.suffix == ".jsonl" for path in paths),
            "Review files and summary must stay in this comparison outside the prepared folder")
    require(not any(output.is_relative_to(results_directory / name) or any(path.is_relative_to(results_directory / name)
            for path in paths) for name in prepared.SYSTEMS), "Review files may not replace producer evidence")
    reviewed, records = {}, []
    for path in sorted(paths):
        rows = inputs.read(path, jsonl=True)
        require(bool(rows) and all(isinstance(row, dict) for row in rows), "Empty or malformed review file")
        records.append({"path": str(path), "sha256": inputs.files[str(path)], "rowCount": len(rows)})
        for row in rows:
            rid = row.get("id")
            require(isinstance(rid, str) and rid in lookup, "Unknown reviewed ID")
            require(rid not in reviewed, "Duplicate reviewed ID")
            template = lookup[rid]
            required = set(template) | {"anonymousInputSha256", "reviewManifestSha256"}
            require(required <= set(row) <= required | {"reviewerId"}, "Reviewed row fields differ")
            require(row["reviewerType"] == "assistant" and row["humanReviewed"] is False and
                    row["anonymousInputSha256"] == anonymous_sha and row["reviewManifestSha256"] == manifest_sha,
                    "Assistant provenance or prepared input hash differs")
            if "reviewerId" in row:
                require(isinstance(row["reviewerId"], str) and 1 <= len(row["reviewerId"].strip()) <= 120, "Invalid reviewer ID")
            for field in set(template) - {"choices", "reviewerType"}:
                require(type(row[field]) is type(template[field]) and row[field] == template[field],
                        "Reviewed source/context/reference/annotation changed")
            choices = row.get("choices")
            require(isinstance(choices, list) and len(choices) == 4 and all(isinstance(choice, dict) for choice in choices) and
                    [choice.get("label") for choice in choices] == list("ABCD"), "Missing, extra, duplicate or reordered choice labels")
            for choice, original in zip(choices, template["choices"]):
                require(set(choice) == set(original) and all(choice.get(field) == original[field] for field in
                        ("label", "translation", "translationSha256")) and
                        prepared.sha_text(choice["translation"]) == choice["translationSha256"], "Raw choice translation changed")
                check_judgment(choice["review"])
            reviewed[rid] = row
    require(set(reviewed) == set(lookup), "Review coverage is incomplete; all 24 rows and 96 choices are required")

    # Unblind for aggregation only after every assistant judgment is complete.
    key = inputs.read(directory / "review-key.json")
    inputs.record(directory / "review-key.json", manifest["files"]["review-key.json"])
    require(key.get("version") == prepared.VERSION and key.get("inputIdentitySha256") == manifest["identitySha256"] and
            isinstance(key.get("mappings"), list) and [item.get("id") for item in key["mappings"]] == [row["id"] for row in templates],
            "Review key identity/order differs")
    totals = {name: {**empty_counts(), "byDomain": {domain: empty_counts() for domain in ("finance", "general")}}
              for name in prepared.SYSTEMS}
    evidence = []
    for template, mapping in zip(templates, key["mappings"]):
        labels = mapping.get("labels")
        require(isinstance(labels, dict) and set(labels) == set("ABCD") and set(labels.values()) == set(prepared.SYSTEMS),
                "Review key must map each label to one different system")
        for choice in reviewed[template["id"]]["choices"]:
            name, judgment = labels[choice["label"]], choice["review"]
            add(totals[name], judgment)
            add(totals[name]["byDomain"][template["domain"]], judgment)
            evidence.append({"id": template["id"], "domain": template["domain"], "system": name,
                             "anonymousLabel": choice["label"], "sourceSha256": template["sourceSha256"],
                             "translationSha256": choice["translationSha256"], "reviewerId": reviewed[template["id"]].get("reviewerId"),
                             "review": judgment})
    result = {"version": VERSION, "completed": True, "reviewerType": "assistant", "humanReviewed": False,
              "rowCount": 24, "judgedChoices": 96, "unresolvedJudgments": 0,
              "domains": dict(Counter(row["domain"] for row in templates)), "systems": totals, "evidence": evidence,
              "preparedManifestSha256": manifest_sha, "preparedIdentitySha256": manifest["identitySha256"],
              "anonymousSha256": anonymous_sha, "keySha256": inputs.files[str(directory / "review-key.json")],
              "reviews": records, "codeSha256": code_sha, "severityScale": manifest["severityScale"],
              "automaticGateCreated": False, "promotionDecision": None, "inferencePerformed": False,
              "humanGoldReference": False, "limitation": prepared.LIMITATION}
    content = prepared.json_bytes(result)
    inputs.unchanged()
    # Recheck the producer/input bytes captured by the validated preparation,
    # without following any unvalidated paths from a caller-supplied manifest.
    for path, expected in identity["inputFiles"].items():
        require(prepared.digest(Path(path)) == expected, "Producer input changed during review aggregation")
    if output.exists():
        require(output.read_bytes() == content, "Existing immutable review summary differs")
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    path = summarize(args.prepared, args.reviews, args.output)
    print(json.dumps({"event": "quality-assistant-review-summary-ready", "path": str(path),
                      "sha256": prepared.digest(path), "humanReviewed": False, "automaticGateCreated": False}))


if __name__ == "__main__":
    raise SystemExit(main())
