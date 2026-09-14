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
from input_execution_v1 import recovery_evaluation as v1
from input_execution_v1 import recovery_v2_lineage as lineage

VERSION = "input-execution-v1-recovery-evaluation-v2"
COHORT_VERSION = "input-execution-v1-recovery-cohort-v1"
RECOVERY_PROVISIONAL_SHA = "ef62ae93467924548e3966754bfc7a53d3a620fb89bd6b516cfa7f37a8cfebb0"
CODE_FILES = tuple("scripts/model-comparison/input_execution_v1/" + name for name in (
    "evaluation.py", "review_io.py", "provisional_reviews.py", "answer_transport.py",
    "recovery_cohort.py", "recovery_cohort_v2.py", "recovery_evaluation.py", "recovery_answer_transport.py",
    "recovery_evaluation_v2.py", "recovery_answer_transport_v2.py", "recovery_v2_lineage.py"))


def validate_cohort(path, root=ROOT):
    # Explicit dependency, not a replacement for any frozen completion function.
    from input_execution_v1.recovery_cohort_v2 import validate_cohort as validator
    return validator(path, root=root)


def ref(root, path, raw=None):
    return transport.reference(root, path, raw)


def verify_refs(root, records):
    transport.verify_refs(root, records)


def cohort_manifest_path(path, root):
    path = ev.rooted(root, path)
    return path / "manifest.json" if path.is_dir() else path


def recovery_identity(manifest, manifest_ref, evaluation_lineage):
    identity = v1.recovery_identity(manifest, manifest_ref)
    identity.update(validationVersion=manifest["validationVersion"],
                    validationLineage=deepcopy(manifest["validationLineage"]),
                    evaluationLineage=deepcopy(evaluation_lineage))
    return identity


def load_cohort(path, root=ROOT):
    root = Path(root).resolve()
    evaluation_lineage = lineage.verify(root)
    manifest_path = cohort_manifest_path(path, root)
    ev.require(manifest_path.name == "manifest.json", "recovery_cohort_manifest_required")
    raw_before = manifest_path.read_bytes()
    validated = validate_cohort(manifest_path, root=root)
    manifest = validated["manifest"]
    ev.require(manifest.get("validationVersion") == lineage.VALIDATION_VERSION and
               isinstance(manifest.get("validationLineage"), dict) and manifest["validationLineage"],
               "explicit_cohort_validator_v2_required")
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
    refs = [*evaluation_lineage["files"], *source_evidence, *evidence, manifest_ref,
            ref(root, root / ev.S1 / "freeze-manifest.json"), ref(root, root / ev.S2 / "manifest.json")]
    code_refs = [ref(root, root / path) for path in CODE_FILES]
    refs.extend(code_refs)
    verify_refs(root, refs)
    return {"rows": rows, "units": units, "annotations": annotations, "prepared": prepared,
            "manifest": manifest, "manifestRef": manifest_ref, "provenance": provenance,
            "evidence": refs, "code": code_refs, "evaluationLineage": evaluation_lineage}


def build_review_packets(cohort):
    """Reuse frozen packet construction; change only the explicit v2 envelope."""
    bundle = v1.build_review_packets(cohort)
    bundle.update(version=VERSION, recovery=recovery_identity(
        cohort["manifest"], cohort["manifestRef"], cohort["evaluationLineage"]))
    return bundle


def expected_manifest(bundle):
    manifest = v1.expected_manifest(bundle)
    manifest.update(version=VERSION, validationVersion=lineage.VALIDATION_VERSION)
    return manifest


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
    from input_execution_v1 import recovery_answer_transport_v2 as rt
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
    from input_execution_v1 import recovery_answer_transport_v2 as rt
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


def verify_recovery_provisional(provisional_folder, final_folder, receipt_path, root=ROOT):
    """Bind a mixed/in-progress source draft to final evidence, without approving it."""
    root = Path(root).resolve()
    provisional_folder = transport.private_path(root, provisional_folder)
    receipt_path = transport.private_path(root, receipt_path)
    ev.require(not receipt_path.exists(), "recovery_promotion_receipt_must_be_new")
    formal = load_formal(final_folder, root)
    path = provisional_folder / "private/manifest.json"
    manifest, manifest_raw = transport.read(path)
    required = {"version": "input-execution-v1-recovery-provisional-source-v1",
                "status": "provisional_source_packets_only", "sourcePackets": 4,
                "questionPackets": 0, "seed": ev.SEED, "whole64CohortValidated": False,
                "formalSourceReviewsApproved": False, "sourceRunInProgress": True,
                "candidateDecisions": 0, "generationCalls": 0, "humanReviewed": False}
    ev.require(all(type(manifest.get(k)) is type(v) and manifest[k] == v for k, v in required.items()) and
               manifest.get("unit") in {f"IP1-G{i:02d}" for i in range(2, 9)},
               "recovery_source_provisional_contract")
    mappings = ev.unique(manifest["mappings"], "reviewId", "recovery_provisional_mapping")
    ev.require(len(mappings) == 4 and {m["configuration"] for m in mappings.values()} == set(ev.CONFIGURATIONS) and
               {m["id"] for m in mappings.values()} == {manifest["unit"]}, "recovery_provisional_unit_inventory")
    transport.exact_files(provisional_folder / "source-packets", [rid + ".json" for rid in mappings])
    ev.require(not (provisional_folder / "question-packets").exists(), "recovery_provisional_question_packets_forbidden")
    code = ref(root, root / "scripts/model-comparison/input_execution_v1/recovery_provisional_reviews.py")
    ev.require(code["sha256"] == manifest["preparationCodeSha256"] == RECOVERY_PROVISIONAL_SHA,
               "recovery_provisional_preparation_code_changed")
    evidence = {r["path"]: r for r in formal["cohort"]["evidence"]}
    refs = [ref(root, path, manifest_raw), code, *formal["evidence"]]

    def checked_ref(record):
        ev.require(isinstance(record, dict) and set(record) == {"path", "sha256", "bytes"} and
                   type(record["bytes"]) is int, "recovery_provisional_reference_schema")
        raw = ev.rooted(root, record["path"]).read_bytes()
        actual = ref(root, root / record["path"], raw)
        ev.require(record["bytes"] == len(raw) and all(record[k] == actual[k] for k in ("path", "sha256")) and
                   record["path"] in evidence and evidence[record["path"]]["sha256"] == record["sha256"],
                   "recovery_provisional_evidence_not_in_cohort")
        refs.append(actual)
        return actual

    plan = checked_ref(manifest["plan"])
    final_runs = formal["cohort"]["manifest"]["sourceRuns"]
    recovered = next(r for r in final_runs if r["status"] == "completed")
    ev.require(plan["path"] == recovered["runPath"] + "/plan.json" and
               plan["sha256"] == recovered["planSha256"], "recovery_provisional_plan_changed")
    packet_map = {p["reviewId"]: p for p in formal["bundle"]["sourcePackets"]}
    final_map = {m["reviewId"]: m for m in formal["bundle"]["mapping"]}
    provenance = {(p["id"], p["configuration"]): p for p in formal["bundle"]["outputProvenance"]}
    rows = []
    for rid, mapping in mappings.items():
        ev.require(rid in final_map and all(mapping[k] == final_map[rid][k] for k in ("id", "configuration")),
                   "recovery_provisional_mapping_changed")
        original = provenance[(mapping["id"], mapping["configuration"])]
        ev.require(mapping["sourceRunId"] == original["runId"] and mapping["sourceRunStatus"] ==
                   ("failed" if original["sourceRunStatus"] == "failed" else "not_finalized"),
                   "recovery_provisional_original_status_changed")
        checked = {key: checked_ref(mapping[key]) for key in ("output", "rawResponse", "request", "receipt")}
        ev.require(all(checked["output"][k] == original["outputFile"][k] and
                       checked["rawResponse"][k] == original["rawResponse"][k] for k in ("path", "sha256")),
                   "recovery_provisional_original_output_changed")
        raw_path = Path(original["rawResponse"]["path"])
        prefix = raw_path.name.removesuffix(".response.bin")
        ev.require(checked["request"]["path"] == raw_path.with_name(prefix + ".request.json").as_posix() and
                   checked["receipt"]["path"] == raw_path.with_name(prefix + ".receipt.json").as_posix(),
                   "recovery_provisional_http_join_changed")
        packet_path = provisional_folder / "source-packets" / (rid + ".json")
        raw = ev.rooted(root, packet_path).read_bytes()
        ev.require(raw == (formal["folder"] / "source-packets" / (rid + ".json")).read_bytes() == ev.packed(packet_map[rid]) and
                   ev.sha(raw) == mapping["packetSha256"], "recovery_provisional_packet_not_byte_identical")
        refs.append(ref(root, packet_path, raw))
        rows.append({"reviewId": rid, "packetSha256": mapping["packetSha256"], "byteIdentical": True,
                     "originalRunId": original["runId"], "originalRunStatus": original["sourceRunStatus"],
                     "provisionalRunStatus": mapping["sourceRunStatus"], "originalEvidence": checked})
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
    recovery_promote = sub.add_parser("verify-recovery-provisional")
    recovery_promote.add_argument("--provisional", type=Path, required=True)
    recovery_promote.add_argument("--folder", type=Path, required=True)
    recovery_promote.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_files(args.cohort, args.destination)
    elif args.command == "freeze-answers":
        result = freeze_answers(args.folder, args.collection)
    elif args.command == "report":
        result = report(args.folder, args.output, args.source_reviews, args.grades)
    elif args.command == "verify-recovery-provisional":
        result = verify_recovery_provisional(args.provisional, args.folder, args.receipt)
    else:
        result = verify_promotion(args.provisional, args.folder, args.receipt)
    print(__import__("json").dumps({k: result[k] for k in result if k in
          ("version", "status", "counts", "answerContexts", "questions", "packetPromotionEligible")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
