"""Explicit S5 path for a validated 38+26 recovery cohort; never rewrite failed runs.

The recovery cohort validator owns native/output provenance validation. Frozen
S1/S2 loading, packet semantics and judgment/selection rules remain unchanged.
No inference, implicit source review, answer authoring or ledger append occurs.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_execution_v1 import evaluation as ev
from input_execution_v1 import answer_transport as transport
from input_execution_v1 import provisional_reviews as provisional

VERSION = "input-execution-v1-recovery-evaluation-v1"
COHORT_VERSION = "input-execution-v1-recovery-cohort-v1"
CODE_FILES = tuple("scripts/model-comparison/input_execution_v1/" + name for name in (
    "evaluation.py", "review_io.py", "provisional_reviews.py", "answer_transport.py",
    "recovery_cohort.py", "recovery_evaluation.py", "recovery_answer_transport.py"))


def validate_cohort(path, root=ROOT):
    # Explicit dependency, not a replacement for any frozen completion function.
    from input_execution_v1.recovery_cohort import validate_cohort as validator
    return validator(path, root=root)


def ref(root, path, raw=None):
    return transport.reference(root, path, raw)


def verify_refs(root, records):
    transport.verify_refs(root, records)


def cohort_manifest_path(path, root):
    path = ev.rooted(root, path)
    return path / "manifest.json" if path.is_dir() else path


def recovery_identity(manifest, manifest_ref):
    return {"cohortManifest": deepcopy(manifest_ref), "cohortStatus": manifest["status"],
            "originalAttemptStatus": "failed", "originalFailureReclassified": False,
            "retainedOutputs": 38, "recoveredOutputs": 26, "completedOutputs": 64,
            "completionRequestsSent": 65, "interruptedRequests": 1,
            "sourceRuns": deepcopy(manifest["sourceRuns"])}


def load_cohort(path, root=ROOT):
    root = Path(root).resolve()
    manifest_path = cohort_manifest_path(path, root)
    ev.require(manifest_path.name == "manifest.json", "recovery_cohort_manifest_required")
    raw_before = manifest_path.read_bytes()
    validated = validate_cohort(manifest_path, root=root)
    manifest = validated["manifest"]
    actual, raw_after = transport.read(manifest_path)
    ev.require(raw_before == raw_after and actual == manifest, "cohort_changed_during_validation")
    expected = {"version": COHORT_VERSION, "status": "completed_logical_cohort", "retainedOutputs": 38,
                "recoveredOutputs": 26, "completionRequestsSent": 65, "interruptedRequests": 1, "expectedOutputs": 64}
    ev.require(all(manifest.get(k) == v for k, v in expected.items()), "recovery_cohort_contract")
    runs = manifest.get("sourceRuns")
    ev.require(isinstance(runs, list) and len(runs) == 2 and
               sorted((r["status"], r["outputs"], r["completionRequestsSent"]) for r in runs) ==
               [("completed", 26, 26), ("failed", 38, 39)], "original_failure_must_remain_failed")
    ev.require(isinstance(validated["evidence"], dict) and isinstance(validated["evidence"].get("files"), list),
               "cohort_file_evidence_required")
    evidence = validated["evidence"]["files"]
    ev.require(evidence and all(isinstance(r, dict) and set(r) >= {"path", "sha256"} for r in evidence),
               "cohort_file_reference_schema")
    verify_refs(root, evidence)
    units, annotations, source_evidence = ev.load_development(root)
    prepared = ev.load_prepared(root)
    rows = validated["rows"]
    ev.require(isinstance(rows, list) and [ev.output_key(r) for r in rows] == [ev.output_key(r) for r in prepared],
               "recovery_original_prepared_order")
    outputs, prompts = {ev.output_key(r): r for r in rows}, {ev.output_key(r): r for r in prepared}
    expected_keys = {(uid, cfg) for uid in units for cfg in ev.CONFIGURATIONS}
    ev.require(len(rows) == len(outputs) == len(prompts) == 64 and len(units) == 16 and
               set(outputs) == set(prompts) == expected_keys, "recovery_complete_64_inventory")
    file_refs = {r["path"]: r["sha256"] for r in evidence}
    provenance = []
    for row in rows:
        key = ev.output_key(row)
        unit = units[row["id"]]
        target = next(b for b in unit["document"]["blocks"] if b["id"] == unit["targetBlockId"])
        ev.require(row["status"] == "completed" and row["source"] == target["text"] and
                   row["sourceSha256"] == target["textSha256"] == ev.sha(row["source"]) and
                   row["promptSha256"] == prompts[key]["promptSha256"] and
                   row["translationSha256"] == ev.sha(row["translation"]), "recovery_source_prompt_output_identity")
        ev.require(row["preparedManifestSha256"] == ev.S2_SHA and row["normalization"] == "none" and
                   row["postProcessingApplied"] is False, "recovery_original_processing_required")
        matching = [run for run in runs if Path(run["runPath"]).name == row["runId"] and
                    ev.rooted(root, row["rawResponsePath"]).is_relative_to(ev.rooted(root, run["runPath"]) / "http")]
        ev.require(len(matching) == 1, "recovery_original_run_provenance")
        run = matching[0]
        output_path = ev.rooted(root, run["runPath"]) / "outputs" / (row["id"] + "-" + row["configuration"] + ".json")
        output, raw = transport.read(output_path)
        output_ref = ref(root, output_path, raw)
        ev.require(output == row and file_refs.get(output_ref["path"]) == output_ref["sha256"] and
                   file_refs.get(row["rawResponsePath"]) == row["rawResponseSha256"], "recovery_original_row_evidence")
        provenance.append({"id": row["id"], "configuration": row["configuration"], "runId": row["runId"],
                           "producerVersion": row["producerVersion"], "sourceRunStatus": run["status"],
                           "outputFile": output_ref, "rawResponse": {"path": row["rawResponsePath"], "sha256": row["rawResponseSha256"]}})
    ev.require(sum(p["sourceRunStatus"] == "failed" for p in provenance) == 38, "recovery_retained_38_required")
    manifest_ref = ref(root, manifest_path, raw_after)
    refs = [*source_evidence, *evidence, manifest_ref,
            ref(root, root / ev.S1 / "freeze-manifest.json"), ref(root, root / ev.S2 / "manifest.json")]
    code_refs = [ref(root, root / path) for path in CODE_FILES]
    refs.extend(code_refs)
    verify_refs(root, refs)
    return {"rows": rows, "units": units, "annotations": annotations, "prepared": prepared,
            "manifest": manifest, "manifestRef": manifest_ref, "provenance": provenance,
            "evidence": refs, "code": code_refs}


def build_review_packets(cohort):
    """Construct unchanged packet schemas from validated original mixed-run rows.

    No single-run validator is patched, skipped through a fabricated run ID, or
    invoked on rewritten rows. provisional.packet_for supplies identical source
    packets; the fixed question allowlist/mapping mirrors frozen S5 explicitly.
    """
    rows, units, annotations, prepared = (cohort[k] for k in ("rows", "units", "annotations", "prepared"))
    outputs = {ev.output_key(r): r for r in rows}
    prompts = {ev.output_key(r): r for r in prepared}
    order = sorted(outputs, key=lambda key: ev.sha(ev.SEED + "\n" + key[0] + "\n" + key[1]))
    questions, sources, mapping = [], [], []
    for index, key in enumerate(order, 1):
        output, unit, annotation = outputs[key], units[key[0]], annotations[key[0]]
        rid = f"R{index:03d}"
        packet = {"reviewId": rid, "translation": output["translation"],
                  "questions": [{"questionId": f"q{i}", "questionKo": q["questionKo"]}
                                for i, q in enumerate(annotation["questions"], 1)]}
        source_packet = provisional.packet_for(key[0], key[1], output, units, annotations, prepared)
        ev.require(source_packet["reviewId"] == rid, "recovery_fixed_packet_order")
        questions.append(packet); sources.append(source_packet)
        mapping.append({"reviewId": rid, "id": key[0], "configuration": key[1], "domain": unit["domain"],
                        "stratum": "general_synthetic" if unit["domain"] == "general" else
                            ("finance_public" if unit["provenance"].get("kind") == "stored-public-source" else "finance_synthetic"),
                        "documentGroup": annotation.get("pairId", unit["document"]["resourceId"]),
                        "sourceSha256": output["sourceSha256"], "translationSha256": output["translationSha256"],
                        "promptSha256": output["promptSha256"], "promptTokens": prompts[key]["promptTokens"],
                        "contextSha256": ev.sha(prompts[key]["context"]["text"]),
                        "questionPacketSha256": ev.sha(ev.packed(packet)),
                        "questionMap": [{"questionId": q["questionId"], "originalQuestionId": a["id"], "core": a["core"]}
                                        for q, a in zip(packet["questions"], annotation["questions"])],
                        "rawResponsePath": output["rawResponsePath"], "rawResponseSha256": output["rawResponseSha256"]})
    return {"version": VERSION, "evaluationVersion": ev.VERSION, "seed": ev.SEED, "mapping": mapping,
            "questionPackets": questions, "sourcePackets": sources, "s4OutputRowsSha256": ev.sha(ev.packed(rows)),
            "s1FreezeSha256": ev.FREEZE_SHA, "s2ManifestSha256": ev.S2_SHA,
            "recovery": recovery_identity(cohort["manifest"], cohort["manifestRef"]),
            "outputProvenance": deepcopy(cohort["provenance"]), "evidenceFiles": deepcopy(cohort["evidence"]),
            "codeFiles": deepcopy(cohort["code"])}


def expected_manifest(bundle):
    return {"version": VERSION, "evaluationVersion": ev.VERSION, "status": "recovery_packets_prepared",
            "questionPackets": 64, "sourcePackets": 64, "bundleSha256": ev.sha(ev.packed(bundle)),
            "cohortManifest": deepcopy(bundle["recovery"]["cohortManifest"]),
            "originalAttemptStatus": "failed", "completionRequestsSent": 65, "interruptedRequests": 1,
            "retainedOutputs": 38, "recoveredOutputs": 26, "sourceReviews": 0, "questionAnswers": 0,
            "grades": 0, "translationGenerations": 0, "humanReviewed": False}


def prepare_files(cohort_path, destination, root=ROOT):
    root = Path(root).resolve()
    destination = transport.private_path(root, destination)
    ev.require(not destination.exists(), "recovery_review_destination_must_be_new")
    cohort = load_cohort(cohort_path, root)
    bundle = build_review_packets(cohort)
    manifest = expected_manifest(bundle)
    verify_refs(root, cohort["evidence"])
    destination.mkdir(parents=True, exist_ok=False)
    for group, directory in (("questionPackets", "question-packets"), ("sourcePackets", "source-packets")):
        for packet in bundle[group]:
            ev.write_new(destination / directory / (packet["reviewId"] + ".json"), packet)
    ev.write_new(destination / "private/bundle.json", bundle)
    ev.write_new(destination / "manifest.json", manifest)
    return manifest


def load_formal(folder, root=ROOT):
    root = Path(root).resolve()
    folder = transport.private_path(root, folder)
    manifest, manifest_raw = transport.read(folder / "manifest.json")
    bundle, bundle_raw = transport.read(folder / "private/bundle.json")
    ev.require(manifest == expected_manifest(bundle) and bundle_raw == ev.packed(bundle), "recovery_formal_manifest")
    verify_refs(root, [manifest["cohortManifest"], *bundle["codeFiles"]])
    cohort = load_cohort(manifest["cohortManifest"]["path"], root)
    ev.require(bundle == build_review_packets(cohort), "recovery_formal_bundle_replay")
    refs = [*cohort["evidence"], ref(root, folder / "manifest.json", manifest_raw),
            ref(root, folder / "private/bundle.json", bundle_raw)]
    packets = {}
    for group, directory in (("questionPackets", "question-packets"), ("sourcePackets", "source-packets")):
        transport.exact_files(folder / directory, [rid + ".json" for rid in transport.IDS])
        ev.require([p["reviewId"] for p in bundle[group]] == list(transport.IDS), "recovery_64_packet_order")
        for packet in bundle[group]:
            path = folder / directory / (packet["reviewId"] + ".json")
            raw = ev.rooted(root, path).read_bytes()
            ev.require(raw == ev.packed(packet), "recovery_formal_packet_changed")
            refs.append(ref(root, path, raw))
            if group == "questionPackets":
                packets[packet["reviewId"]] = packet
    protocol = ref(root, root / transport.PROTOCOL)
    refs.append(protocol)
    verify_refs(root, refs)
    return {"folder": folder, "bundle": bundle, "packets": packets, "manifestSha256": ev.sha(manifest_raw),
            "bundleSha256": manifest["bundleSha256"], "protocol": protocol, "evidence": refs,
            "cohort": cohort, "recovery": deepcopy(bundle["recovery"])}


def freeze_answers(folder, collection, root=ROOT):
    from input_execution_v1 import recovery_answer_transport as rt
    root = Path(root).resolve()
    formal = load_formal(folder, root)
    answer_rows, collection_refs = rt.validate_collection(formal, collection, root)
    folder = formal["folder"]
    ev.require(not any((folder / name).exists() for name in ("answers-frozen.json", "answers-freeze.json", "grade-packets")),
               "recovery_answers_freeze_must_be_new")
    answers = ev.unique(answer_rows, "reviewId", "answer")
    ev.require(set(answers) == set(transport.IDS), "recovery_all_64_answers_required")
    actors = []
    for rid in transport.IDS:
        ev.validate_answer(answers[rid], formal["packets"][rid])
        actors.append(answers[rid]["reviewer"]["actorId"])
    ev.require(len(set(actors)) == 64, "recovery_answer_context_reused")
    payload = {"version": ev.VERSION, "rows": [answers[rid] for rid in transport.IDS]}
    receipt = {"version": VERSION, "status": "recovery_answers_frozen", "answersSha256": ev.sha(ev.packed(payload)),
               "formalManifestSha256": formal["manifestSha256"], "recovery": formal["recovery"],
               "answerContexts": 64, "questions": 128, "freshContextActorIds": actors,
               "collection": ref(root, transport.private_path(root, collection) / "receipt.json"),
               "collectionEvidence": collection_refs, "sourceGradingCompleted": False, "humanReviewed": False}
    verify_refs(root, formal["evidence"] + collection_refs)
    ev.write_new(folder / "answers-frozen.json", payload)
    ev.write_new(folder / "answers-freeze.json", receipt)
    for packet in formal["bundle"]["sourcePackets"]:
        answer = answers[packet["reviewId"]]
        ev.write_new(folder / "grade-packets" / (packet["reviewId"] + ".json"),
                     {**packet, "answer": answer, "answerSha256": ev.sha(ev.packed(answer))})
    return receipt


def read_frozen_answers(formal, root):
    from input_execution_v1 import recovery_answer_transport as rt
    folder = formal["folder"]
    receipt, raw = transport.read(folder / "answers-freeze.json")
    ev.require(receipt["version"] == VERSION and receipt["status"] == "recovery_answers_frozen" and
               receipt["formalManifestSha256"] == formal["manifestSha256"] and receipt["recovery"] == formal["recovery"] and
               receipt["answerContexts"] == 64 and receipt["questions"] == 128 and receipt["humanReviewed"] is False and
               receipt["sourceGradingCompleted"] is False, "recovery_answer_freeze_contract")
    verify_refs(root, [receipt["collection"], *receipt["collectionEvidence"]])
    collection = ev.rooted(root, receipt["collection"]["path"]).parent
    rows, collection_refs = rt.validate_collection(formal, collection, root)
    ev.require(receipt["collectionEvidence"] == collection_refs, "recovery_answer_collection_evidence")
    answer_raw = (folder / "answers-frozen.json").read_bytes()
    ev.require(answer_raw == ev.packed({"version": ev.VERSION, "rows": rows}) and ev.sha(answer_raw) == receipt["answersSha256"],
               "recovery_frozen_answers_changed")
    actors = [r["reviewer"]["actorId"] for r in rows]
    ev.require(len(set(actors)) == 64 and receipt["freshContextActorIds"] == actors, "recovery_frozen_actor_identity")
    answers = {r["reviewId"]: r for r in rows}
    transport.exact_files(folder / "grade-packets", [rid + ".json" for rid in transport.IDS])
    refs = [ref(root, folder / "answers-freeze.json", raw), ref(root, folder / "answers-frozen.json", answer_raw), *collection_refs]
    for packet in formal["bundle"]["sourcePackets"]:
        answer = answers[packet["reviewId"]]
        ev.validate_answer(answer, formal["packets"][packet["reviewId"]])
        expected = {**packet, "answer": answer, "answerSha256": ev.sha(ev.packed(answer))}
        path = folder / "grade-packets" / (packet["reviewId"] + ".json")
        grade_raw = path.read_bytes()
        ev.require(grade_raw == ev.packed(expected), "recovery_grade_packet_changed")
        refs.append(ref(root, path, grade_raw))
    return rows, refs


def report(folder, output, source_reviews=None, grades=None, root=ROOT):
    root = Path(root).resolve()
    output = transport.private_path(root, output)
    ev.require(not output.exists(), "recovery_report_must_be_new")
    formal = load_formal(folder, root)
    refs = list(formal["evidence"])
    def authored_rows(path):
        if path is None:
            return []
        path = transport.private_path(root, path)
        value, raw = transport.read(path)
        refs.append(ref(root, path, raw))
        return value if isinstance(value, list) else value["rows"]
    source_rows, grade_rows = authored_rows(source_reviews), authored_rows(grades)
    answers = []
    if grades is not None or (formal["folder"] / "answers-freeze.json").exists():
        answers, answer_refs = read_frozen_answers(formal, root)
        refs.extend(answer_refs)
    result = ev.aggregate(formal["bundle"], source_rows, answers, grade_rows)
    result["recoveryEvidence"] = {"wrapperVersion": VERSION, "formalManifestSha256": formal["manifestSha256"],
                                  **formal["recovery"], "evidenceFiles": refs,
                                  "singleRunCompletionClaimed": False, "ledgerAppendPerformed": False}
    verify_refs(root, refs)
    ev.write_new(output, result)
    return result


def verify_promotion(provisional_folder, final_folder, receipt_path, root=ROOT):
    """Packet/hash eligibility only; no automatic approval of source judgments."""
    root = Path(root).resolve()
    provisional_folder = transport.private_path(root, provisional_folder)
    receipt_path = transport.private_path(root, receipt_path)
    ev.require(not receipt_path.exists(), "recovery_promotion_receipt_must_be_new")
    formal = load_formal(final_folder, root)
    manifest_path = provisional_folder / "private/manifest.json"
    manifest, manifest_raw = transport.read(manifest_path)
    ev.require(manifest.get("version") == provisional.VERSION and manifest.get("provisional") is True and
               manifest.get("seed") == ev.SEED and manifest.get("sourcePackets") == 4 and
               manifest.get("questionPackets") == 0 and len(manifest.get("mappings", [])) == 4,
               "recovery_provisional_manifest_contract")
    mappings = ev.unique(manifest["mappings"], "reviewId", "provisional_mapping")
    ev.require({m["configuration"] for m in mappings.values()} == set(ev.CONFIGURATIONS) and
               {m["id"] for m in mappings.values()} == {manifest["unit"]}, "recovery_provisional_unit_inventory")
    verify_refs(root, manifest["evidence"])
    packet_map = {p["reviewId"]: p for p in formal["bundle"]["sourcePackets"]}
    final_map = {m["reviewId"]: m for m in formal["bundle"]["mapping"]}
    provenance = {(p["id"], p["configuration"]): p for p in formal["bundle"]["outputProvenance"]}
    rows, refs = [], [ref(root, manifest_path, manifest_raw), *manifest["evidence"], *formal["evidence"]]
    for rid, mapping in mappings.items():
        final, original = final_map[rid], provenance[(mapping["id"], mapping["configuration"])]
        ev.require(all(final[k] == mapping[k] for k in ("id", "configuration", "sourceSha256", "promptSha256", "translationSha256")),
                   "recovery_provisional_mapping_changed")
        ev.require(all(mapping["outputFile"][k] == original["outputFile"][k] and
                       mapping["rawResponse"][k] == original["rawResponse"][k] for k in ("path", "sha256")) and
                   original["sourceRunStatus"] == "failed" and
                   Path(original["outputFile"]["path"]).parent.parent.as_posix() == manifest["run"],
                   "recovery_provisional_original_output_changed")
        verify_refs(root, [mapping["outputFile"], mapping["rawResponse"]])
        path = provisional_folder / "source-packets" / (rid + ".json")
        raw = ev.rooted(root, path).read_bytes()
        final_raw = (formal["folder"] / "source-packets" / (rid + ".json")).read_bytes()
        ev.require(raw == final_raw == ev.packed(packet_map[rid]) and ev.sha(raw) == mapping["packetSha256"],
                   "recovery_provisional_packet_not_byte_identical")
        refs.append(ref(root, path, raw))
        rows.append({"reviewId": rid, "packetSha256": mapping["packetSha256"], "byteIdentical": True,
                     "originalOutput": original["outputFile"], "originalRawResponse": original["rawResponse"]})
    verify_refs(root, refs)
    receipt = {"version": VERSION, "status": "recovery_provisional_packet_identity_verified", "rows": rows,
               "provisionalManifestSha256": ev.sha(manifest_raw), "formalManifestSha256": formal["manifestSha256"],
               "recovery": formal["recovery"], "cohort64ValidationPerformed": True,
               "originalFailedRunMarkedCompleted": False, "packetPromotionEligible": True,
               "formalSourceReviewsApproved": False, "actualReviewerConfirmationStillRequired": True,
               "questionsEvaluated": 0, "scoresProduced": 0, "candidateSelections": 0, "humanReviewed": False,
               "evidenceFiles": refs}
    ev.write_new(receipt_path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--cohort", type=Path, required=True); prepare.add_argument("--destination", type=Path, required=True)
    freeze = sub.add_parser("freeze-answers")
    freeze.add_argument("--folder", type=Path, required=True); freeze.add_argument("--collection", type=Path, required=True)
    summary = sub.add_parser("report")
    summary.add_argument("--folder", type=Path, required=True); summary.add_argument("--output", type=Path, required=True)
    summary.add_argument("--source-reviews", type=Path); summary.add_argument("--grades", type=Path)
    promote = sub.add_parser("verify-promotion")
    promote.add_argument("--provisional", type=Path, required=True); promote.add_argument("--folder", type=Path, required=True)
    promote.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_files(args.cohort, args.destination)
    elif args.command == "freeze-answers":
        result = freeze_answers(args.folder, args.collection)
    elif args.command == "report":
        result = report(args.folder, args.output, args.source_reviews, args.grades)
    else:
        result = verify_promotion(args.provisional, args.folder, args.receipt)
    print(__import__("json").dumps({k: result[k] for k in result if k in
          ("version", "status", "counts", "answerContexts", "questions", "packetPromotionEligible")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
