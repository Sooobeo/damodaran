"""Validate every real-reading judgment before opening the model key; count only."""
from __future__ import annotations

import argparse
from pathlib import Path

import reading_review_common_v4 as common

ENVELOPE = {"id", "sourceSha256", "contextSha256", "preparedManifestSha256", "packetSha256",
            "reviewerType", "humanReviewed", "judgments"}


def summarize(prepared_path, review_paths, output_path, root=common.ROOT):
    root = Path(root).resolve()
    codes = common.code_inventory()
    evidence = common.Evidence(root)
    prepared, output = common.local(prepared_path, root), common.local(output_path, root)
    common.require(prepared.is_relative_to(root / ".training/comparisons") and prepared.is_dir(), "prepared_outside_comparisons")
    common.require(output.suffix == ".json" and output.is_relative_to(prepared.parent)
                   and not output.is_relative_to(prepared) and not output.exists(), "new_output_outside_prepared_required")
    manifest = evidence.read(prepared / "manifest.json")
    manifest_sha = evidence.files[prepared / "manifest.json"]
    common.require(isinstance(manifest, dict) and manifest.get("version") == common.PREPARED_VERSION
        and manifest.get("status") == "prepared" and manifest.get("humanReviewed") is False
        and manifest.get("reviewerType") == "assistant" and manifest.get("referencesHumanReviewed") is False
        and manifest.get("inferencePerformed") is False and manifest.get("modelWeightsRead") is False
        and manifest.get("canonicalTermGateCreated") is False, "prepared_manifest_contract_differs")
    common.require(type(manifest.get("sourceCount")) is int and manifest["sourceCount"] == 6
        and type(manifest.get("systemCount")) is int and 2 <= manifest["systemCount"] <= 4
        and type(manifest.get("judgmentCount")) is int and manifest["judgmentCount"] == 6 * manifest["systemCount"],
        "prepared_counts_differ")
    common.require(manifest.get("codeFiles") == codes, "prepared_review_code_differs")
    identity = manifest.get("inputIdentity")
    common.require(isinstance(identity, dict) and isinstance(identity.get("inputPath"), str), "prepared_input_missing")
    clean, actual_identity, sources, references, advisory = common.read_input(identity["inputPath"], evidence)
    common.require(common.canonical(identity) == common.canonical(actual_identity), "prepared_input_identity_differs")
    files = manifest.get("files")
    common.require(isinstance(files, dict) and set(files) == {"packet.jsonl", "review-advisory.json"}, "prepared_file_list_differs")
    common.require(evidence.raw(prepared / "review-advisory.json", files["review-advisory.json"]) == advisory,
                   "prepared_advisory_differs")
    packet = evidence.read(prepared / "packet.jsonl", files["packet.jsonl"], jsonl=True)
    labels = [chr(ord("A") + index) for index in range(manifest["systemCount"])]
    ids = [row["id"] for row in clean]
    common.require(len(packet) == 6 and all(isinstance(row, dict) for row in packet), "packet_coverage_differs")
    for index, row in enumerate(packet):
        common.require(set(row) == {"input", "sourceProvenance", "reference", "candidates", "reviewInstruction"}
            and common.canonical(row["input"]) == common.canonical(clean[index])
            and common.canonical(row["sourceProvenance"]) == common.canonical(sources[index]["provenance"])
            and common.canonical(row["reference"]) == common.canonical(references[index])
            and row["reviewInstruction"] == common.INSTRUCTION, "packet_source_or_annotation_differs")
        candidates = row["candidates"]
        common.require(isinstance(candidates, list) and len(candidates) == len(labels)
            and all(isinstance(item, dict) for item in candidates)
            and [item.get("label") for item in candidates] == labels, "packet_candidate_coverage_differs")
        for item in candidates:
            common.require(set(item) == {"label", "translation", "targetSha256"} and isinstance(item["translation"], str)
                and item["targetSha256"] == common.reader.text_hash(item["translation"]), "packet_target_hash_differs")
    paths = [common.local(path, root) for path in review_paths]
    common.require(bool(paths) and len(paths) == len(set(paths)) and all(not p.is_relative_to(prepared) for p in paths),
                   "distinct_review_files_outside_prepared_required")
    reviewed, review_files = {}, []
    # Neither the key nor any producer directory is read before this entire phase passes.
    for path in paths:
        rows = evidence.read(path, jsonl=True)
        common.require(bool(rows), "empty_review_file")
        review_files.append({"path": str(path), "sha256": evidence.files[path], "rowCount": len(rows)})
        for row in rows:
            common.require(isinstance(row, dict) and ENVELOPE <= set(row) <= ENVELOPE | {"reviewerId"}, "review_fields_differ")
            rid = row["id"]
            common.require(isinstance(rid, str) and rid in ids and rid not in reviewed, "duplicate_or_unknown_review_id")
            source, template = clean[ids.index(rid)], packet[ids.index(rid)]
            common.require(row["sourceSha256"] == source["sourceSha256"] and row["contextSha256"] == source["contextSha256"]
                and row["preparedManifestSha256"] == manifest_sha and row["packetSha256"] == files["packet.jsonl"],
                "review_input_identity_differs")
            common.require(row["reviewerType"] == "assistant" and row["humanReviewed"] is False
                and ("reviewerId" not in row or isinstance(row["reviewerId"], str) and bool(row["reviewerId"].strip())),
                "review_provenance_differs")
            judgments = row["judgments"]
            common.require(isinstance(judgments, list) and len(judgments) == len(labels)
                and all(isinstance(j, dict) for j in judgments)
                and [j.get("label") for j in judgments] == labels, "judgment_label_coverage_differs")
            for judgment, candidate in zip(judgments, template["candidates"]):
                common.check_judgment(judgment, source, candidate)
            reviewed[rid] = row
    common.require(set(reviewed) == set(ids), "missing_review_rows_before_key")
    evidence.unchanged()
    common.assert_code_unchanged(codes)
    # First key access: every one of the 6 x N judgments has now been validated.
    key = evidence.read(prepared / "review-key.json", manifest.get("keySha256"))
    common.require(isinstance(key, dict) and set(key) == {"systems", "mapping"}
        and isinstance(key["systems"], list) and len(key["systems"]) == len(labels), "key_system_count_differs")
    records, directories = [], set()
    for declaration in key["systems"]:
        common.require(isinstance(declaration, dict) and isinstance(declaration.get("directory"), str), "key_system_invalid")
        directory = common.local(declaration["directory"], root)
        common.require(directory not in directories and not directory.is_relative_to(prepared)
                       and not prepared.is_relative_to(directory), "duplicate_or_nested_producer")
        directories.add(directory)
        records.append(common.producer(directory, clean, identity, evidence, declaration))
    common.require(manifest.get("v4ArtifactFilesSha256") == common.v4_review_evidence.artifact_inventory_sha(evidence),
                   "prepared_v4_artifact_inventory_changed")
    mapping = key["mapping"]
    common.require(isinstance(mapping, list) and all(isinstance(m, dict) for m in mapping)
        and [(m.get("id"), m.get("label")) for m in mapping] == [(rid, label) for rid in ids for label in labels],
        "key_mapping_coverage_differs")
    lookup = {}
    for item in mapping:
        common.require(set(item) == {"id", "label", "systemIndex", "targetSha256"}
            and type(item["systemIndex"]) is int and item["systemIndex"] in range(len(records)), "key_mapping_fields_differ")
        rid, label, system = item["id"], item["label"], item["systemIndex"]
        candidate = packet[ids.index(rid)]["candidates"][labels.index(label)]
        prediction = records[system]["predictions"][ids.index(rid)]
        common.require(item["targetSha256"] == candidate["targetSha256"] == prediction["targetSha256"]
                       and candidate["translation"] == prediction["translation"], "key_candidate_producer_differs")
        lookup[rid, label] = system
    common.require(all({lookup[rid, label] for label in labels} == set(range(len(records))) for rid in ids),
                   "key_must_cover_each_system_once_per_source")
    results, judgments = [], []
    for index, record in enumerate(records):
        results.append({k: v for k, v in record.items() if k != "predictions"} | {
            "systemIndex": index, "counts": {"rows": 0, "severity": {str(n): 0 for n in range(4)},
                "materialErrorRows": 0, "fluentRows": 0}, "byErrorCategory": {}})
    for source in clean:
        for judgment in reviewed[source["id"]]["judgments"]:
            system = lookup[source["id"], judgment["label"]]
            result, severity = results[system], judgment["severity"]
            counts = result["counts"]
            counts["rows"] += 1
            counts["severity"][str(severity)] += 1
            counts["materialErrorRows"] += int(severity >= 2)
            counts["fluentRows"] += int(judgment["fluent"])
            for category in {error["category"] for error in judgment["errors"]}:
                observed = max(error["severity"] for error in judgment["errors"] if error["category"] == category)
                value = result["byErrorCategory"].setdefault(category, {"rows": 0, "materialRows": 0, "maxSeverity": 0})
                value["rows"] += 1
                value["materialRows"] += int(observed >= 2)
                value["maxSeverity"] = max(value["maxSeverity"], observed)
            judgments.append({"id": source["id"], "sourceSha256": source["sourceSha256"],
                "contextSha256": source["contextSha256"], "domain": source["domain"], "systemIndex": system,
                "reviewerId": reviewed[source["id"]].get("reviewerId"), "judgment": judgment})
    result = {"version": common.SUMMARY_VERSION, "completed": True, "sourceCount": 6,
        "systemCount": len(records), "judgmentCount": manifest["judgmentCount"], "humanReviewed": False,
        "reviewerType": "assistant", "inputIdentity": identity, "preparedManifestSha256": manifest_sha,
        "packetSha256": files["packet.jsonl"], "advisorySha256": files["review-advisory.json"],
        "keySha256": manifest["keySha256"], "reviewFiles": review_files, "systems": results, "evidence": judgments,
        "codeFiles": codes, "validatedFiles": {str(p): h for p, h in evidence.files.items()},
        "automaticGateCreated": False, "canonicalTermScoreCreated": False, "promotionDecision": None,
        "inferencePerformed": False, "modelWeightsRead": False, "independentFinalCertification": False,
        "limitations": ["Six finance paragraphs from one public document; no general-domain or population-level certification.",
            "References and judgments are assistant-authored and not human-reviewed; source meaning takes priority.",
            "Counts describe individual judgments, not a canonical term score, 90% gate, or guaranteed quality floor.",
            "Producer completion is not semantic approval. Raw run metadata and output hashes remain unchanged.",
            "The Hy7 producer has no completion-time predictions digest: its output is bound from preparation onward only.",
            "Recorded model and runtime identity metadata is retained; model weights and historical runtime bytes are not reread."]}
    evidence.unchanged()
    common.assert_code_unchanged(codes)
    output.parent.mkdir(parents=True, exist_ok=True)
    common.write_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.prepared, args.reviews, args.output)
    print(common.canonical({"completed": result["completed"], "sources": 6, "judgments": result["judgmentCount"],
                            "humanReviewed": False, "automaticGateCreated": False}).decode("utf-8"))


if __name__ == "__main__":
    main()
