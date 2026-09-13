"""Create shuffled, source-grounded assistant review packets without scores."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import random

from linguistic_screen import ROOT, assert_input_unchanged, digest, read_screen, text_hash, write_once
from summarize_linguistic_reviews_v4 import check_producer_input, review_code_inventory, Inputs
import v4_review_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-audit", type=Path, required=True)
    args = parser.parse_args()
    review_codes = review_code_inventory()
    clean, identity = read_screen(args.input)
    raw = [json.loads(line) for line in args.input.read_text("utf-8").splitlines() if line.strip()]
    assert_input_unchanged(identity)
    audit_raw = args.source_audit.read_bytes()
    audit = json.loads(audit_raw.decode("utf-8"))
    if (audit.get("version") != "linguistic-dev-independent-source-audit-v1"
            or audit.get("modelOutputsViewed") is not False or audit.get("humanReviewed") is not False
            or audit.get("inputs", {}).get(str(args.input.resolve().relative_to(ROOT)).replace("\\", "/")) != identity["inputSha256"]):
        raise ValueError("source_audit_identity_mismatch")
    evidence = Inputs(ROOT)
    systems = []
    for directory in args.results:
        summary = evidence.read(directory / "summary.json")
        if summary.get("status") != "completed":
            raise ValueError("incomplete_result_cannot_be_blind_comparison")
        path = directory / "predictions.jsonl"
        predictions_sha = digest(path)
        check_producer_input(summary, predictions_sha, identity)
        rows = evidence.read(path, predictions_sha, jsonl=True)
        v4_review_evidence.check_artifacts(directory, summary, rows, evidence)
        if [r["id"] for r in rows] != [r["id"] for r in clean]:
            raise ValueError("result_ids_do_not_match_full_input")
        for source, result in zip(clean, rows):
            if (result.get("status") != "completed" or result.get("sourceSha256") != source["sourceSha256"]
                    or result.get("contextSha256") != source["contextSha256"]
                    or result.get("targetSha256") != text_hash(result["translation"])):
                raise ValueError("result_text_identity_mismatch")
        systems.append({"directory": str(directory.resolve()), "predictionsSha256": predictions_sha,
                        "summarySha256": digest(directory / "summary.json"), "rows": rows})
    if not 2 <= len(systems) <= 4 or len({s["directory"] for s in systems}) != len(systems):
        raise ValueError("two_to_four_distinct_systems_required")
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / ".training/comparisons").resolve()):
        raise ValueError("output_outside_comparisons")
    assert_input_unchanged(identity)
    if review_code_inventory() != review_codes or any(
            digest(Path(s["directory"]) / "predictions.jsonl") != s["predictionsSha256"]
            or digest(Path(s["directory"]) / "summary.json") != s["summarySha256"] for s in systems):
        raise ValueError("review_code_or_producer_changed_during_preparation")
    evidence.unchanged()
    output.mkdir(parents=True, exist_ok=False)
    with (output / "source-audit.json").open("xb") as stream:
        stream.write(audit_raw)
    packet, key = [], []
    rng = random.SystemRandom()
    for index, row in enumerate(raw):
        order = list(range(len(systems)))
        rng.shuffle(order)
        candidates = []
        for label_index, system_index in enumerate(order):
            label = chr(ord("A") + label_index)
            result = systems[system_index]["rows"][index]
            candidates.append({"label": label, "translation": result["translation"],
                               "targetSha256": result["targetSha256"]})
            key.append({"id": row["id"], "label": label, "systemIndex": system_index,
                        "targetSha256": result["targetSha256"]})
        packet.append({"input": row, "candidates": candidates,
                       "reviewInstruction": "source-audit.json의 사전 원문 감사도 읽고 원문·문맥에서 판단한다. 참조 번역도 도우미 작성이며 정답으로 보장되지 않는다. 중요한 의미 오류, 경미한 문제, 자연스러움과 용어의 뜻·표기를 분리한다. 후보 이름과 점수는 열지 않는다."})
    for offset in range(0, len(packet), 6):
        with (output / f"packet-{offset // 6 + 1}.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
            for row in packet[offset:offset + 6]:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_once(output / "review-key.json", {"systems": [{k: v for k, v in s.items() if k != "rows"} for s in systems], "mapping": key})
    evidence.unchanged()
    if review_code_inventory() != review_codes:
        raise ValueError("review_code_changed_during_preparation")
    write_once(output / "manifest.json", {**identity, "createdAtUtc": datetime.now(timezone.utc).isoformat(),
                                          "sourceCount": len(packet), "judgmentCount": len(key),
                                          "files": {p.name: digest(p) for p in sorted(output.glob("*.jsonl"))},
                                          "keySha256": digest(output / "review-key.json"),
                                          "sourceAudit": {"file": "source-audit.json", "sha256": digest(output / "source-audit.json")},
                                          "humanReview": False, "reviewCodeFiles": review_codes,
                                          "reviewEvidenceVersion": "v4",
                                          "v4ArtifactFilesSha256": v4_review_evidence.artifact_inventory_sha(evidence)})
    print(json.dumps({"prepared": len(packet), "judgments": len(key), "modelLabelsWithheld": True}))


if __name__ == "__main__":
    main()
