"""Apply the frozen S3 v2 detector to a complete, hash-verified S4 run.

This is a separate observation artifact, never a generation or quality verdict.
No model, network, app DB, registry, ledger write, or output repair is performed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_execution_v1 import evaluation as e
from input_preparation_v1 import relation_checks_v2 as checker

VERSION = "input-execution-v1-s4-relations-v1"
CHECKER_PATH = "scripts/model-comparison/input_preparation_v1/relation_checks_v2.py"
CHECKER_SHA = "ddacdecd1196b0e58c473a4ad4c471f3dc60ac6b8629a12b69779b21a331b9ff"
BASE = ".training/quality-evaluation/input-preparation-v1"
SUPPORTED = ("supported_match", "supported_conflict")
JOIN_FIELDS = ("id", "configuration", "sourceSha256", "translationSha256", "promptSha256", "rawResponseSha256")


def verify_checker():
    path = ROOT / CHECKER_PATH
    e.require(e.sha(path.read_bytes()) == CHECKER_SHA, "frozen_s3_checker_changed")
    e.require(checker.VERSION == "input-preparation-relations-v2", "frozen_s3_checker_version_changed")
    return {"path": CHECKER_PATH, "sha256": CHECKER_SHA, "version": checker.VERSION}


def exact_utf16_span(text, span):
    e.require(isinstance(span, dict) and set(span) == {"start", "end", "text"}, "relation_evidence_schema")
    a, b = span["start"], span["end"]
    e.require(type(a) is int and type(b) is int and 0 <= a < b, "relation_evidence_offsets")
    encoded = text.encode("utf-16-le")
    e.require(b * 2 <= len(encoded), "relation_evidence_out_of_bounds")
    try:
        quote = encoded[a * 2:b * 2].decode("utf-16-le")
    except UnicodeError:
        raise ValueError("relation_evidence_splits_surrogate") from None
    e.require(quote == span["text"], "relation_evidence_quote_changed")


def validate_diagnostic(result, source, translation):
    e.require(result.get("version") == checker.VERSION and result.get("completeSemanticAssessment") is False
              and result.get("modelCalls") == 0 and result.get("humanReviewed") is False,
              "relation_diagnostic_contract_changed")
    rows = result.get("relations")
    e.require(isinstance(rows, list) and [row.get("type") for row in rows] == list(checker.TYPES),
              "relation_type_inventory_changed")
    for row in rows:
        e.require(row.get("status") in checker.STATUSES and type(row.get("warning")) is bool
                  and row["warning"] == (row["status"] == "supported_conflict"), "relation_status_contract")
        for key, text in (("sourceEvidence", source), ("translationEvidence", translation)):
            e.require(isinstance(row.get(key), list), "relation_evidence_required")
            for span in row[key]:
                exact_utf16_span(text, span)
            if row["status"] in SUPPORTED:
                e.require(bool(row[key]), "supported_relation_requires_both_quotes")
    e.require(type(result.get("warning")) is bool and result["warning"] == any(row["warning"] for row in rows),
              "relation_warning_count_mismatch")


def load_outputs(outputs_path, root=ROOT):
    """Use the existing S5 validators; expose no annotations to the detector.

    evaluation.py has separate loading/validation APIs, so this adapter composes
    validate_run_completion, load_development, load_prepared, validate_outputs.
    Development annotations are read for its validation contract and discarded.
    """
    root = Path(root).resolve()
    path = e.rooted(root, outputs_path)
    completion = e.validate_run_completion(path, root)
    summary_raw = (path.parent / "summary.json").read_bytes()
    e.require(e.sha(summary_raw) == completion["sha256"], "s4_summary_changed_while_loading")
    raw = path.read_bytes()
    e.require(e.sha(raw) == json.loads(summary_raw)["artifactHashes"]["predictions.jsonl"],
              "s4_predictions_changed_while_loading")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    units, _annotations, evidence = e.load_development(root)
    prepared = e.load_prepared(root)
    e.validate_outputs(rows, units, prepared, root)
    e.require(e.validate_run_completion(path, root) == completion, "s4_completion_changed_while_loading")
    references = evidence + [completion,
        {"path": path.relative_to(root).as_posix(), "sha256": e.sha(raw)},
        {"path": e.S1 + "/freeze-manifest.json", "sha256": e.FREEZE_SHA},
        {"path": e.S2 + "/manifest.json", "sha256": e.S2_SHA}]
    return rows, references


def diagnose_rows(rows):
    """Only two strings cross the detector boundary; IDs are attached after it."""
    checker_receipt = verify_checker()
    e.require(isinstance(rows, list) and len(rows) == 64, "complete_64_outputs_required")
    keys = [(row["id"], row["configuration"]) for row in rows]
    ids = {key[0] for key in keys}
    e.require(len(keys) == len(set(keys)) == 64 and len(ids) == 16 and
              set(keys) == {(uid, cfg) for uid in ids for cfg in e.CONFIGURATIONS}, "relation_output_inventory")
    e.require(len({row.get("runId") for row in rows}) == 1 and bool(rows[0].get("runId")), "relation_mixed_runs")
    observations = []
    for row in rows:
        e.require(row.get("status") == "completed" and isinstance(row.get("source"), str)
                  and isinstance(row.get("translation"), str) and row["translation"].strip(), "relation_incomplete_output")
        e.require(e.sha(row["source"]) == row["sourceSha256"] and
                  e.sha(row["translation"]) == row["translationSha256"], "relation_output_text_hash")
        # No configuration, ID, source context, baseline judgment or answer is
        # supplied, including to the registered detector's function signature.
        diagnostic = checker.inspect_relations(row["source"], row["translation"])
        validate_diagnostic(diagnostic, row["source"], row["translation"])
        join = {key: row[key] for key in JOIN_FIELDS}
        content_id = e.sha(e.packed(join))
        observation_id = e.sha(e.packed({"outputIdentity": join, "checkerSha256": CHECKER_SHA,
                                       "diagnostic": diagnostic}))
        observations.append({
            "observationId": observation_id, "outputIdentitySha256": content_id, **join,
            "runId": row["runId"], "rawResponsePath": row["rawResponsePath"],
            "outputRowSha256": e.sha(e.packed(row)), "diagnostic": diagnostic,
            "diagnosticSha256": e.sha(e.packed(diagnostic)),
            "evidenceOffsetEncoding": "UTF-16 code units, half-open; original text",
            "originalAutomaticChecks": deepcopy(row.get("automaticChecks", {})),
            "originalTechnicalChecks": deepcopy(row.get("technicalChecks", {})),
            "originalOutputStatus": row["status"], "originalJudgmentsChanged": False,
            "translationErrorAdded": False, "trainingUseAllowed": False, "humanReviewed": False,
        })
    by_configuration = {}
    for configuration in e.CONFIGURATIONS:
        group = [row for row in observations if row["configuration"] == configuration]
        relations = [relation for row in group for relation in row["diagnostic"]["relations"]]
        statuses = Counter(relation["status"] for relation in relations)
        by_configuration[configuration] = {
            "outputs": len(group), "relationSlots": len(relations),
            "warningOutputs": sum(row["diagnostic"]["warning"] for row in group),
            "warningRelations": sum(relation["warning"] for relation in relations),
            "outputsWithAnySupportedRelation": sum(any(relation["status"] in SUPPORTED for relation in row["diagnostic"]["relations"]) for row in group),
            "supportedRelationSlots": sum(statuses[status] for status in SUPPORTED),
            "statusCounts": {status: statuses[status] for status in checker.STATUSES},
            "byType": {kind: {status: sum(relation["type"] == kind and relation["status"] == status for relation in relations)
                               for status in checker.STATUSES} for kind in checker.TYPES},
        }
    verify_checker()
    return {
        "version": VERSION, "status": "diagnosed", "checker": checker_receipt,
        "outputs": 64, "sourceUnits": 16, "relationSlots": 64 * len(checker.TYPES),
        "configurations": by_configuration, "observations": observations,
        "detectorInputFields": ["source", "translation"], "evaluationAnnotationsUsedByDetector": False,
        "generationQualityErrors": {"status": "not_evaluated", "warningCountIsNotTranslationErrorCount": True},
        "precisionRecall": {"status": "not_evaluated", "precision": None, "recall": None,
            "reasonKo": "별도 원문 검토나 관계별 정답 판정과 아직 대조하지 않았다.",
            "futureReviewJoinFields": list(JOIN_FIELDS),
            "paragraphBaselineIsNotRelationSpecificGold": True},
        "semanticApproval": False, "fullSemanticCoverage": False,
        "unsupportedAndUndeterminedAreNotPass": True, "independentHoldout": False,
        "productQE95GatePassed": False, "modelCalls": 0, "translationGenerations": 0,
        "ledgerWrites": 0, "appRegistrationPerformed": False, "humanReviewed": False,
    }


def write_report(outputs_path, destination, root=ROOT):
    root = Path(root).resolve()
    destination = e.rooted(root, destination)
    base = e.rooted(root, BASE)
    e.require(destination != base and destination.is_relative_to(base), "relation_destination_scope")
    e.require(not destination.exists(), "relation_destination_must_be_new")
    code_path = Path(__file__).resolve()
    code_hash = e.sha(code_path.read_bytes())
    validator_path = Path(e.__file__).resolve()
    validator_hash = e.sha(validator_path.read_bytes())
    rows, evidence = load_outputs(outputs_path, root)
    result = diagnose_rows(rows)
    # Recheck completed artifacts after diagnosis; no incomplete run may be
    # promoted just because individual prediction records already exist.
    completion = e.validate_run_completion(outputs_path, root)
    e.require(completion in evidence and e.sha(code_path.read_bytes()) == code_hash
              and e.sha(validator_path.read_bytes()) == validator_hash, "relation_evidence_changed_during_diagnosis")
    result.update(createdAtUtc=datetime.now(timezone.utc).isoformat(), evidenceFiles=evidence,
                  wrapperCodeSha256=code_hash, completionValidatorCodeSha256=validator_hash,
                  validationReadScope="S1 source/evaluation hashes, S2 prepared evidence, completed S4 artifacts; no holdout")
    destination.mkdir(parents=True, exist_ok=False)
    path = destination / "relation-report.json"
    raw = e.packed(result)
    with path.open("xb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    manifest = {"version": VERSION, "status": "diagnosed", "outputs": 64,
                "reportFile": path.name, "reportSha256": e.sha(raw), "checker": result["checker"],
                "modelCalls": 0, "translationGenerations": 0, "ledgerWrites": 0}
    with (destination / "manifest.json").open("xb") as stream:
        stream.write(e.packed(manifest)); stream.flush(); os.fsync(stream.fileno())
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write_report(args.outputs, args.destination), ensure_ascii=False))


if __name__ == "__main__":
    main()
