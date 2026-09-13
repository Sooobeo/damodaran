"""Prepare an immutable, shuffled review of six public passages; no inference."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import random

import reading_review_common_v5 as common


def prepare(input_path, directories, output_path, root=common.ROOT):
    root = Path(root).resolve()
    codes = common.code_inventory()
    evidence = common.Evidence(root)
    clean, identity, sources, references, advisory = common.read_input(input_path, evidence)
    output = common.local(output_path, root)
    common.require(output.is_relative_to(root / ".training/comparisons") and not output.exists(),
                   "new_prepared_directory_required")
    paths = [common.local(path, root) for path in directories]
    common.require(2 <= len(paths) <= 4 and len(set(paths)) == len(paths), "two_to_four_distinct_systems_required")
    common.require(not any(output.is_relative_to(path) or path.is_relative_to(output) for path in paths),
                   "prepared_and_producer_directories_must_be_separate")
    systems = [common.producer(path, clean, identity, evidence) for path in paths]
    packet, mapping = [], []
    rng = random.SystemRandom()
    for index, source in enumerate(clean):
        order = list(range(len(systems)))
        rng.shuffle(order)
        candidates = []
        for label_index, system_index in enumerate(order):
            label = chr(ord("A") + label_index)
            result = systems[system_index]["predictions"][index]
            candidates.append({"label": label, "translation": result["translation"], "targetSha256": result["targetSha256"]})
            mapping.append({"id": source["id"], "label": label, "systemIndex": system_index,
                            "targetSha256": result["targetSha256"]})
        packet.append({"input": source, "sourceProvenance": sources[index]["provenance"],
            "reference": references[index], "candidates": candidates, "reviewInstruction": common.INSTRUCTION})
    evidence.unchanged()
    common.assert_code_unchanged(codes)
    output.mkdir(parents=True, exist_ok=False)
    common.write_bytes(output / "review-advisory.json", advisory)
    common.write_jsonl(output / "packet.jsonl", packet)
    common.write_json(output / "review-key.json", {
        "systems": [{field: record[field] for field in ("directory", "summarySha256", "predictionsSha256")} for record in systems],
        "mapping": mapping})
    manifest = {"version": common.PREPARED_VERSION, "status": "prepared", "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "inputIdentity": identity, "sourceCount": 6, "systemCount": len(systems), "judgmentCount": 6 * len(systems),
        "humanReviewed": False, "reviewerType": "assistant", "referencesHumanReviewed": False,
        "purpose": "six public paragraphs from one document; not independent final quality certification",
        "files": {name: common.reader.digest(output / name) for name in ("packet.jsonl", "review-advisory.json")},
        "keySha256": common.reader.digest(output / "review-key.json"), "codeFiles": codes,
        "v5ArtifactFilesSha256": common.v5_review_evidence.artifact_inventory_sha(evidence),
        "inferencePerformed": False, "modelWeightsRead": False, "canonicalTermGateCreated": False}
    # Compute the exact future manifest bytes so the template can bind them
    # without publishing a complete manifest before all artifacts exist.
    manifest_raw = common.json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    manifest_sha, packet_sha = common.sha(manifest_raw), manifest["files"]["packet.jsonl"]
    template = []
    for row in packet:
        source = row["input"]
        template.append({"id": source["id"], "sourceSha256": source["sourceSha256"], "contextSha256": source["contextSha256"],
            "preparedManifestSha256": manifest_sha, "packetSha256": packet_sha, "reviewerType": "assistant", "humanReviewed": False,
            "judgments": [{"id": source["id"], "label": item["label"], "targetSha256": item["targetSha256"],
                "severity": None, "fluent": None, "errors": [], "reasonKo": "", "humanReviewed": False}
                for item in row["candidates"]]})
    common.write_jsonl(output / "review-template.jsonl", template)
    evidence.unchanged()
    common.assert_code_unchanged(codes)
    common.write_bytes(output / "manifest.json", manifest_raw)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.input, args.results, args.output)
    print(common.canonical({"status": result["status"], "sources": 6, "judgments": result["judgmentCount"],
                            "humanReviewed": False, "inferencePerformed": False}).decode("utf-8"))


if __name__ == "__main__":
    main()
