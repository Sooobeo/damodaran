"""S5 evidence packets, explicit review validation and frozen S6 development gate.

No model, network, app DB, registry or ledger mutation is performed here. Scores
are computed only from separately supplied, source-grounded reviewer judgments.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VERSION = "input-execution-v1-s5-evaluation-v1"
SEED = "input-preparation-v1-review-20260913"
CONFIGURATIONS = ("C0", "C1", "C2", "C3")
TECHNICAL_CHECKS = ("nonEmpty", "notTruncated", "belowOutputLimit", "noLeakedControlTokens",
                    "stopEvidenceVerified", "generationSettingsVerified", "promptAndOutputCountsVerified", "noPromptCacheReuse")
SEVERITY = {"neutral": 0, "minor": 1, "major": 2, "critical": 3}
S1 = "content/model-comparison/input-preparation-v1"
FREEZE_SHA = "b70304bbd8a3961096f6d07116d51196ece3c4416977f0c0899cf712b7ada4cf"
S2 = ".training/comparisons/input-preparation-v1/s2-prepared/attempt-002"
S2_SHA = "b007e567881c1a6dfa556c775eda65916aa49d76559dedc14420c0e127866653"
SOURCES = (
    ".training/comparisons/input-preparation-v1/source-bundles/finance-public-inputs.json",
    S1 + "/finance-synthetic-inputs.json", S1 + "/general-inputs.json")
EVALUATIONS = (
    ".training/comparisons/input-preparation-v1/source-bundles/finance-public-evaluation.json",
    S1 + "/evaluation/finance-synthetic-evaluation.json", S1 + "/evaluation/general-evaluation.json")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def packed(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def sha(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_bytes())


def read_rows(path):
    path = Path(path)
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]
    data = read_json(path)
    return data if isinstance(data, list) else data["rows"]


def write_new(path, data):
    path = Path(path)
    require(not any(p.is_symlink() for p in (path, *path.parents)), "redirected_output_path")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(packed(data))


def rooted(root, path):
    root = Path(root).resolve()
    path = Path(path)
    path = path if path.is_absolute() else root / path
    require(path.resolve().is_relative_to(root) and not any(p.is_symlink() for p in (path, *path.parents)),
            "evidence_path_outside_root_or_redirected")
    return path


def unique(rows, field, name):
    require(isinstance(rows, list), name + "_must_be_list")
    result = {r[field]: r for r in rows}
    require(len(result) == len(rows), name + "_duplicate")
    return result


def load_development(root=ROOT):
    """Only development annotations are opened; no holdout/test content is read."""
    root = Path(root)
    manifest = rooted(root, S1 + "/freeze-manifest.json")
    require(sha(manifest.read_bytes()) == FREEZE_SHA, "s1_freeze_changed")
    artifacts = {a["path"]: a for a in read_json(manifest)["artifacts"]}
    evidence, units, annotations = [], [], []
    for name in SOURCES + EVALUATIONS:
        raw = rooted(root, name).read_bytes()
        require(sha(raw) == artifacts[name]["sha256"], "development_file_changed:" + name)
        evidence.append({"path": name, "sha256": sha(raw)})
        data = json.loads(raw)
        if name in SOURCES:
            units.extend(data["inputUnits"])
        else:
            annotations.extend(data["evaluations"])
    by_id = unique(units, "id", "source")
    evaluations = unique(annotations, "id", "evaluation")
    require(len(by_id) == 16 and set(by_id) == set(evaluations), "development_inventory")
    for unit in by_id.values():
        blocks = unique(unit["document"]["blocks"], "id", "block")
        target = blocks[unit["targetBlockId"]]
        annotation = evaluations[unit["id"]]
        require(sha(target["text"]) == target["textSha256"] == annotation["targetTextSha256"],
                "target_annotation_identity")
        require(len(annotation["propositions"]) == 3 and len(annotation["questions"]) == 2,
                "annotation_cardinality")
        require(all(p["core"] is True for p in annotation["propositions"]), "proposition_core_contract")
        require(sum(q["core"] is True for q in annotation["questions"]) == 1, "question_core_contract")
        require(all(q["allowedKoreanContext"] == "" for q in annotation["questions"]), "question_context_contract")
        for item in annotation["propositions"] + annotation["questions"] + annotation["terms"]:
            require(item["sourceQuote"] and item["sourceQuote"] in target["text"], "annotation_quote_mismatch")
    require(sum(len(e["terms"]) for e in annotations) == 39, "term_inventory")
    return by_id, evaluations, evidence


def load_prepared(root=ROOT):
    manifest_path = rooted(root, S2 + "/manifest.json")
    require(sha(manifest_path.read_bytes()) == S2_SHA, "s2_manifest_changed")
    manifest = read_json(manifest_path)
    for entry in manifest["code"]:
        require(sha(rooted(root, entry["path"]).read_bytes()) == entry["sha256"], "s2_code_changed")
    for entry in manifest["artifacts"]:
        require(sha(rooted(root, S2 + "/" + entry["path"]).read_bytes()) == entry["sha256"], "s2_artifact_changed")
    return read_rows(rooted(root, S2 + "/prompts.jsonl"))


def output_key(row):
    return row["id"], row["configuration"]


def validate_run_completion(outputs_path, root=ROOT):
    path = rooted(root, outputs_path)
    require(path.name == "predictions.jsonl", "s4_prediction_artifact_required")
    summary_path = path.parent / "summary.json"
    summary = read_json(summary_path)
    require(summary.get("status") == "completed" and summary.get("completedOutputs") == 64 and summary.get("missingOutputs") == 0,
            "s4_run_not_complete")
    require(all(summary.get(k) is True for k in ("childProcessStopped", "integrityVerified", "outputIntegrityPassed")),
            "s4_shutdown_or_integrity_not_verified")
    require(sha(path.read_bytes()) == summary["artifactHashes"]["predictions.jsonl"], "s4_predictions_changed_after_completion")
    for name, digest in summary["artifactHashes"].items():
        require(sha(rooted(root, path.parent / name).read_bytes()) == digest, "s4_completed_artifact_changed")
    return {"path": summary_path.relative_to(root).as_posix(), "sha256": sha(summary_path.read_bytes())}


def validate_outputs(rows, units, prepared, root=None):
    """Reject incomplete generation inventories before exposing any review packet."""
    expected = {(uid, cfg) for uid in units for cfg in CONFIGURATIONS}
    mapped = {output_key(row): row for row in rows}
    prompts = {output_key(row): row for row in prepared}
    require(len(rows) == len(mapped) == 64 and set(mapped) == expected == set(prompts), "s4_complete_64_required")
    for name in ("runId", "producerVersion", "runtimeContractVersion"):
        require(len({r.get(name) for r in rows}) == 1 and bool(rows[0].get(name)), "s4_mixed_or_missing_" + name)
    for key, row in mapped.items():
        unit = units[key[0]]
        target = next(b for b in unit["document"]["blocks"] if b["id"] == unit["targetBlockId"])
        require(row["status"] == "completed", "s4_incomplete_output")
        require(row["source"] == target["text"] and row["sourceSha256"] == sha(row["source"]), "s4_source_identity")
        require(row["promptSha256"] == prompts[key]["promptSha256"], "s4_prompt_identity")
        require(isinstance(row["translation"], str) and row["translation"].strip(), "s4_empty_translation")
        require(row["translationSha256"] == sha(row["translation"]), "s4_translation_identity")
        require(row.get("normalization") == "none" and row.get("postProcessingApplied") is False,
                "s4_translation_must_be_unmodified")
        checks = row["technicalChecks"]
        require(isinstance(checks, dict) and set(checks) == set(TECHNICAL_CHECKS) and all(type(v) is bool and v for v in checks.values()),
                "s4_technical_checks_incomplete")
        require(type(row["outputTokens"]) is int and 0 < row["outputTokens"] < 4096, "s4_output_token_count")
        require(isinstance(row["outputTokenIds"], list) and len(row["outputTokenIds"]) == row["outputTokens"]
                and all(type(t) is int and 0 <= t < 128167 for t in row["outputTokenIds"]), "s4_output_token_ids")
        if root is not None:
            raw = rooted(root, row["rawResponsePath"]).read_bytes()
            require(sha(raw) == row["rawResponseSha256"], "s4_raw_response_identity")
            require(json.loads(raw)["content"] == row["translation"], "s4_raw_response_translation")
        require(row.get("preparedManifestSha256") == S2_SHA, "s4_prepared_manifest_identity")
    return mapped, prompts


def build_review_packets(rows, units, evaluations, prepared, root=None):
    outputs, prompts = validate_outputs(rows, units, prepared, root)
    order = sorted(outputs, key=lambda key: sha(SEED + "\n" + key[0] + "\n" + key[1]))
    question_packets, source_packets, mapping = [], [], []
    for index, key in enumerate(order, 1):
        output, unit, annotation = outputs[key], units[key[0]], evaluations[key[0]]
        review_id = "R" + format(index, "03d")
        questions = [{"questionId": "q" + str(i), "questionKo": q["questionKo"]}
                     for i, q in enumerate(annotation["questions"], 1)]
        # Keep this explicit allowlist. No source, answers, cohort, titles,
        # original question IDs, configuration, provenance or hash-key metadata.
        question_packet = {"reviewId": review_id, "translation": output["translation"], "questions": questions}
        question_packets.append(question_packet)
        source_packets.append({"reviewId": review_id, "source": output["source"],
            "sourceSha256": output["sourceSha256"], "translation": output["translation"],
            "translationSha256": output["translationSha256"], "evaluation": deepcopy(annotation),
            "sourceDocument": deepcopy(unit["document"]), "humanReviewed": False})
        mapping.append({"reviewId": review_id, "id": key[0], "configuration": key[1],
            "domain": unit["domain"], "stratum": "general_synthetic" if unit["domain"] == "general" else
                ("finance_public" if unit["provenance"].get("kind") == "stored-public-source" else "finance_synthetic"),
            "documentGroup": annotation.get("pairId", unit["document"]["resourceId"]),
            "sourceSha256": output["sourceSha256"], "translationSha256": output["translationSha256"],
            "promptSha256": output["promptSha256"], "promptTokens": prompts[key]["promptTokens"],
            "contextSha256": sha(prompts[key]["context"]["text"]),
            "questionPacketSha256": sha(packed(question_packet)),
            "questionMap": [{"questionId": q["questionId"], "originalQuestionId": e["id"], "core": e["core"]}
                            for q, e in zip(questions, annotation["questions"])],
            "rawResponsePath": output["rawResponsePath"], "rawResponseSha256": output["rawResponseSha256"]})
    return {"version": VERSION, "seed": SEED, "mapping": mapping,
            "questionPackets": question_packets, "sourcePackets": source_packets,
            "s4OutputRowsSha256": sha(packed(rows)), "s1FreezeSha256": FREEZE_SHA, "s2ManifestSha256": S2_SHA}


def exact_span(text, span):
    """Review offsets are Python/Unicode codepoints, not browser UTF-16 offsets."""
    require(isinstance(span, dict) and set(span) == {"start", "end", "text"}, "span_schema")
    a, b, quote = span["start"], span["end"], span["text"]
    require(type(a) is int and type(b) is int and 0 <= a < b <= len(text) and text[a:b] == quote,
            "span_not_exact_codepoint_quote")


def spans(text, rows, allow_empty=False):
    require(isinstance(rows, list) and (rows or allow_empty), "missing_evidence")
    for row in rows:
        exact_span(text, row)


def reason(row):
    require(isinstance(row.get("reasonKo"), str) and row["reasonKo"].strip(), "reason_required")


def validate_source_review(review, packet):
    for field in ("reviewId", "sourceSha256", "translationSha256"):
        require(review[field] == packet[field], "source_review_identity")
    require(review.get("humanReviewed") is False, "assistant_review_not_human_review")
    reviewer = review["reviewer"]
    require(reviewer.get("actorId") and reviewer.get("model"), "source_reviewer_provenance_required")
    source, translation = packet["source"], packet["translation"]
    full = review["fullText"]
    require(full.get("reviewedAllText") is True, "whole_text_review_required")
    require(full["severity"] in (*SEVERITY, "unresolved"), "full_text_severity")
    reason(full); spans(source, full["sourceEvidence"]); spans(translation, full["translationEvidence"])
    issues = unique(full["issues"], "issueId", "issue")
    unique(full["issues"], "sourceErrorId", "source_error")
    for issue in issues.values():
        require(issue["severity"] in ("minor", "major", "critical") and issue.get("category"), "issue_classification")
        require(isinstance(issue["sourceErrorId"], str) and issue["sourceErrorId"].strip(), "explicit_source_error_identity_required")
        reason(issue); spans(source, issue["sourceEvidence"])
        spans(translation, issue["translationEvidence"], allow_empty=issue.get("omission") is True)
    highest = max((SEVERITY[i["severity"]] for i in issues.values()), default=0)
    require(full["severity"] == "unresolved" or SEVERITY[full["severity"]] == highest, "severity_issue_inconsistency")
    expected = packet["evaluation"]
    for group in ("propositions", "terms"):
        rows = unique(review[group], "id", group)
        require(set(rows) == {p["id"] for p in expected[group]}, group + "_inventory")
        expected_rows = {p["id"]: p for p in expected[group]}
        for row in rows.values():
            reason(row); spans(source, row["sourceEvidence"])
            spans(translation, row["translationEvidence"], allow_empty=row.get("omission") is True)
            expected_item = expected_rows[row["id"]]
            source_start = source.index(expected_item["sourceQuote"])
            source_end = source_start + len(expected_item["sourceQuote"])
            require(any(s["start"] < source_end and source_start < s["end"] for s in row["sourceEvidence"]),
                    "review_evidence_not_linked_to_annotation")
            if group == "propositions":
                require(row["verdict"] in ("preserved", "damaged", "unresolved"), "proposition_verdict")
            else:
                require(all(row[k] is None or type(row[k]) is bool for k in ("meaningCorrect", "renderingAcceptable")),
                        "term_judgment_requires_semantics_and_rendering")
                a, b = expected_item["sourceStartCodepoint"], expected_item["sourceEndCodepoint"]
                require(any(s["start"] <= a and b <= s["end"] for s in row["sourceEvidence"]), "term_occurrence_evidence")
    natural = review["naturalness"]
    require(natural["score"] is None or type(natural["score"]) is int and natural["score"] in (1, 2, 3), "naturalness_scale")
    reason(natural); spans(translation, natural["translationEvidence"])
    return review


def validate_answer(answer, packet):
    require(answer["reviewId"] == packet["reviewId"] and answer["packetSha256"] == sha(packed(packet)), "answer_packet_identity")
    reviewer = answer["reviewer"]
    require(reviewer.get("actorId") and reviewer.get("model") and reviewer.get("freshContext") is True,
            "answer_fresh_context_provenance_required")
    require(reviewer.get("priorTaskExposure") is False and reviewer.get("allowedExposures") == ["packet_translation", "packet_questions"],
            "answerer_exposure_contract")
    require(answer.get("humanReviewed") is False, "assistant_answer_not_human_study")
    answers = unique(answer["answers"], "questionId", "answer")
    require(set(answers) == {q["questionId"] for q in packet["questions"]}, "answer_question_inventory")
    for row in answers.values():
        require(isinstance(row.get("answerKo"), str) and row["answerKo"].strip(), "answer_text_required")
        reason(row)
        spans(packet["translation"], row["translationEvidence"], allow_empty=row.get("cannotDetermine") is True)
    return answer


def validate_question_grade(grade, answer, packet):
    require(grade["reviewId"] == packet["reviewId"] and grade["answerSha256"] == sha(packed(answer)), "grade_answer_identity")
    require(grade["sourceSha256"] == packet["sourceSha256"] and grade["translationSha256"] == packet["translationSha256"],
            "grade_source_identity")
    require(grade["reviewer"].get("actorId") and grade["reviewer"].get("model") and
            grade["reviewer"]["actorId"] != answer["reviewer"]["actorId"], "separate_source_grader_required")
    require(grade.get("humanReviewed") is False, "assistant_grade_not_human_study")
    rows = unique(grade["questions"], "questionId", "grade")
    require(set(rows) == {q["questionId"] for q in answer["answers"]}, "grade_question_inventory")
    answer_map = {q["questionId"]: q for q in answer["answers"]}
    for row in rows.values():
        require(row["verdict"] in ("correct", "incorrect", "unresolved"), "question_grade_verdict")
        reason(row); spans(packet["source"], row["sourceEvidence"])
        spans(answer_map[row["questionId"]]["answerKo"], row["answerEvidence"])
        annotation = packet["evaluation"]["questions"][int(row["questionId"][1:]) - 1]
        a = packet["source"].index(annotation["sourceQuote"])
        b = a + len(annotation["sourceQuote"])
        require(any(s["start"] < b and a < s["end"] for s in row["sourceEvidence"]), "grade_evidence_not_linked_to_question")
    return grade


def metric(passed, total, evaluated):
    return {"passed": passed, "total": total, "evaluated": evaluated,
            "rate": passed / total if total else None,
            "status": "not_applicable" if not total else ("complete" if evaluated == total else "not_evaluated")}


def unit_result(mapping, packet, review=None, answer=None, grade=None):
    annotation = packet["evaluation"]
    result = {k: mapping[k] for k in ("reviewId", "id", "configuration", "domain", "stratum", "documentGroup", "promptTokens")}
    result.update(technicalComplete=True, sourceReviewComplete=False, questionsComplete=False, unresolved=True,
                  severity="unresolved", issues=[], naturalness=None, propositionVerdicts={}, questionVerdicts={}, termVerdicts={})
    if review is not None:
        validate_source_review(review, packet)
        result["severity"] = review["fullText"]["severity"]
        result["issues"] = review["fullText"]["issues"]
        result["naturalness"] = review["naturalness"]["score"]
        result["propositionVerdicts"] = {p["id"]: p["verdict"] for p in review["propositions"]}
        result["termVerdicts"] = {t["id"]: (None if t["meaningCorrect"] is None or t["renderingAcceptable"] is None
                                             else t["meaningCorrect"] and t["renderingAcceptable"]) for t in review["terms"]}
        result["sourceReviewComplete"] = (result["severity"] != "unresolved" and result["naturalness"] is not None
            and "unresolved" not in result["propositionVerdicts"].values() and None not in result["termVerdicts"].values())
    if grade is not None:
        require(answer is not None, "question_grade_without_answer")
        validate_question_grade(grade, answer, packet)
        qmap = {q["questionId"]: q["originalQuestionId"] for q in mapping["questionMap"]}
        result["questionVerdicts"] = {qmap[q["questionId"]]: q["verdict"] for q in grade["questions"]}
        result["questionsComplete"] = "unresolved" not in result["questionVerdicts"].values()
    result["complete"] = result["sourceReviewComplete"] and result["questionsComplete"]
    result["unresolved"] = not result["complete"]
    props, terms, questions = result["propositionVerdicts"], result["termVerdicts"], result["questionVerdicts"]
    result["corePropositions"] = metric(sum(v == "preserved" for v in props.values()), len(annotation["propositions"]),
                                         sum(v != "unresolved" for v in props.values()))
    result["terms"] = metric(sum(v is True for v in terms.values()), len(annotation["terms"]), sum(v is not None for v in terms.values()))
    result["questions"] = metric(sum(v == "correct" for v in questions.values()), 2, sum(v != "unresolved" for v in questions.values()))
    core = [q["id"] for q in annotation["questions"] if q["core"]]
    result["coreQuestions"] = metric(sum(questions.get(q) == "correct" for q in core), len(core),
                                     sum(questions.get(q) in ("correct", "incorrect") for q in core))
    return result


def compare_unit(baseline, candidate):
    require(baseline["id"] == candidate["id"], "comparison_source_mismatch")
    if not baseline["complete"] or not candidate["complete"]:
        return {"status": "unresolved", "reasons": ["unit_evaluation_incomplete"],
                "rejectionReasons": ["unit_evaluation_incomplete"]}
    regressions, improvements, rejection = [], [], []
    if SEVERITY[candidate["severity"]] > SEVERITY[baseline["severity"]]:
        regressions.append("new_or_worse_meaning_error")
        if candidate["severity"] in ("major", "critical") or baseline["domain"] == "general":
            rejection.append("new_or_worse_meaning_error")
    for issue in candidate["issues"]:
        def same_prior(prior):
            # This ID is a source reviewer's explicit cross-candidate semantic
            # judgment. Matching strings or overlapping quotes alone cannot
            # establish that two translations contain the same meaning error.
            return (bool(issue.get("sourceErrorId")) and prior.get("sourceErrorId") == issue["sourceErrorId"]
                    and prior["category"] == issue["category"] and SEVERITY[prior["severity"]] >= SEVERITY[issue["severity"]]
                    and any(a["start"] < b["end"] and b["start"] < a["end"]
                            for a in prior["sourceEvidence"] for b in issue["sourceEvidence"]))
        if not any(same_prior(prior) for prior in baseline["issues"]):
            material = issue["severity"] in ("major", "critical")
            label = "new_material_error" if material else "new_minor_meaning_error"
            regressions.append(label)
            if material or baseline["domain"] == "general":
                rejection.append(label)
    for pid, value in baseline["propositionVerdicts"].items():
        if value == "preserved" and candidate["propositionVerdicts"][pid] != "preserved":
            regressions.append("new_core_proposition_damage")
            rejection.append("new_core_proposition_damage")
    for group, correct in (("questionVerdicts", "correct"), ("termVerdicts", True)):
        if any(value == correct and candidate[group][key] != correct for key, value in baseline[group].items()):
            label = baseline["domain"] + "_" + group + "_regression"
            regressions.append(label)
            if baseline["domain"] == "general":
                rejection.append(label)
    if SEVERITY[candidate["severity"]] < SEVERITY[baseline["severity"]]:
        improvements.append("meaning_error_reduced")
    for group in ("corePropositions", "coreQuestions", "questions", "terms"):
        if candidate[group]["passed"] > baseline[group]["passed"]:
            improvements.append(group + "_improved")
    return {"status": "regressed" if regressions else ("improved" if improvements else "maintained"),
            "reasons": sorted(set(regressions or improvements)), "rejectionReasons": sorted(set(rejection)),
            "regressions": sorted(set(regressions)), "improvements": sorted(set(improvements))}


def summarize_configuration(rows):
    summary = {"units": len(rows), "completeUnits": sum(r["complete"] for r in rows),
        "materialErrorUnits": sum(r["severity"] in ("major", "critical") for r in rows),
        "severityCounts": dict(Counter(r["severity"] for r in rows)),
        "naturalnessTotal": sum(r["naturalness"] or 0 for r in rows),
        "naturalnessEvaluated": sum(r["naturalness"] is not None for r in rows),
        "totalPromptTokens": sum(r["promptTokens"] for r in rows)}
    for group in ("corePropositions", "coreQuestions", "questions", "terms"):
        summary[group] = metric(*(sum(r[group][key] for r in rows) for key in ("passed", "total", "evaluated")))
    return summary


def select_candidate(unit_rows):
    require(len(unit_rows) == 64 and len({(r["id"], r["configuration"]) for r in unit_rows}) == 64,
            "selection_complete_inventory_required")
    groups = {cfg: {r["id"]: r for r in unit_rows if r["configuration"] == cfg} for cfg in CONFIGURATIONS}
    require(all(len(g) == 16 and set(g) == set(groups["C0"]) for g in groups.values()), "selection_unit_inventory")
    summaries = {cfg: summarize_configuration(list(rows.values())) for cfg, rows in groups.items()}
    baseline = summaries["C0"]
    candidates, eligible = {}, []
    for cfg in CONFIGURATIONS[1:]:
        comparisons = [{"id": uid, "stratum": row["stratum"], "documentGroup": row["documentGroup"],
                        **compare_unit(groups["C0"][uid], row)} for uid, row in groups[cfg].items()]
        failures = sorted({reason for row in comparisons for reason in row["rejectionReasons"]})
        summary = summaries[cfg]
        improves = (summary["materialErrorUnits"] < baseline["materialErrorUnits"] or
                    summary["materialErrorUnits"] <= baseline["materialErrorUnits"] and
                    summary["corePropositions"]["passed"] > baseline["corePropositions"]["passed"])
        if not improves:
            failures.append("no_required_development_improvement")
        candidates[cfg] = {"eligible": not failures, "rejectionReasons": failures, "comparisons": comparisons}
        candidates[cfg]["strata"] = {stratum: {"units": sum(row["stratum"] == stratum for row in comparisons),
            "comparisonCounts": dict(Counter(row["status"] for row in comparisons if row["stratum"] == stratum))}
            for stratum in ("finance_public", "finance_synthetic", "general_synthetic")}
        if not failures:
            eligible.append(cfg)
    def rank(cfg):
        s = summaries[cfg]
        return (s["materialErrorUnits"], -s["corePropositions"]["passed"], -s["coreQuestions"]["passed"],
                -s["questions"]["passed"], -s["terms"]["passed"], -s["naturalnessTotal"],
                s["totalPromptTokens"], CONFIGURATIONS.index(cfg))
    winner = min(eligible, key=rank) if eligible else None
    unresolved = any(not row["complete"] for row in unit_rows)
    return {"status": "selection_deferred" if unresolved else ("candidate_selected" if winner else "retain_registered_configuration"),
            "selectedConfiguration": None if unresolved else winner, "configurations": summaries, "candidates": candidates,
            "independentHoldoutAuthorizedByThisResult": bool(winner) and not unresolved,
            "registrationApproved": False, "appDeploymentChanged": False, "humanReviewed": False,
            "generalizationClaim": False, "selectionPolicy": "S1 CONTRACT section 5; criteria unchanged"}


def aggregate(bundle, source_reviews=(), answers=(), grades=()):
    maps = {k: unique(list(v), "reviewId", k) for k, v in
            (("reviews", source_reviews), ("answers", answers), ("grades", grades))}
    question_packets = {p["reviewId"]: p for p in bundle["questionPackets"]}
    source_packets = {p["reviewId"]: p for p in bundle["sourcePackets"]}
    ids = set(question_packets)
    require(all(set(m).issubset(ids) for m in maps.values()), "unexpected_review_id")
    actors = []
    for rid, answer in maps["answers"].items():
        validate_answer(answer, question_packets[rid]); actors.append(answer["reviewer"]["actorId"])
    require(len(actors) == len(set(actors)), "answer_context_reused_across_units_or_candidates")
    rows = [unit_result(m, source_packets[m["reviewId"]], *(maps[k].get(m["reviewId"]) for k in ("reviews", "answers", "grades")))
            for m in bundle["mapping"]]
    return {"version": VERSION, "rows": rows, "decision": select_candidate(rows),
            "counts": {"expectedOutputs": 64, "sourceReviews": len(maps["reviews"]), "answerContexts": len(actors),
                       "expectedPropositions": 192, "expectedTerms": 156, "expectedQuestions": 128, "expectedCoreQuestions": 64,
                       "questionGrades": sum(len(g["questions"]) for g in grades)},
            "modelCalls": 0, "ledgerEventsAppended": 0, "humanLearningEffectMeasured": False}


def ledger_observations(bundle, report, evidence_refs):
    """Prepare a distinct observation kind for append_events; never append here.

    Keeping this separate from historical translation_review events avoids
    counting a review, an incorrect answer and a diagnostic as three errors.
    """
    require(report["version"] == VERSION and isinstance(evidence_refs, list) and evidence_refs,
            "ledger_report_evidence_required")
    require(all(set(ref) >= {"path", "sha256"} and len(ref["sha256"]) == 64 for ref in evidence_refs), "ledger_evidence_schema")
    mapping = {m["reviewId"]: m for m in bundle["mapping"]}
    for row in report["rows"]:
        m = mapping[row["reviewId"]]
        text_hashes = {k: m[k] for k in ("sourceSha256", "translationSha256", "contextSha256")}
        yield {"version": "translation-error-ledger-v1", "kind": "input_preparation_observation",
               "cohort": "input-preparation-dev16", "reviewVersion": VERSION, "sourceId": m["id"],
               "system": m["configuration"], "outputId": sha(packed(text_hashes)), **text_hashes,
               "reviewId": m["reviewId"], "observation": row, "evidence": deepcopy(evidence_refs),
               "translationErrorInferredFromWrongAnswer": False, "independentHoldout": False,
               "humanReviewed": False, "trainingUseAllowed": False, "modelInternalCauseConfirmed": False}


def prepare_files(outputs_path, destination, root=ROOT):
    completion_evidence = validate_run_completion(outputs_path, root)
    units, evaluations, evidence = load_development(root)
    prepared = load_prepared(root)
    rows = read_rows(outputs_path)
    bundle = build_review_packets(rows, units, evaluations, prepared, root)
    destination = rooted(root, destination)
    require(not destination.exists(), "review_destination_must_be_new")
    # All validations finish before the first packet is written.
    for packet in bundle["questionPackets"]:
        write_new(destination / "question-packets" / (packet["reviewId"] + ".json"), packet)
    for packet in bundle["sourcePackets"]:
        write_new(destination / "source-packets" / (packet["reviewId"] + ".json"), packet)
    bundle["evidenceFiles"] = evidence + [completion_evidence,
        {"path": rooted(root, outputs_path).relative_to(root).as_posix(), "sha256": sha(rooted(root, outputs_path).read_bytes())}]
    write_new(destination / "private" / "bundle.json", bundle)
    manifest = {"version": VERSION, "status": "packets_prepared", "questionPackets": 64, "sourcePackets": 64,
                "bundleSha256": sha(packed(bundle)), "sourceReviews": 0, "questionAnswers": 0, "grades": 0,
                "translationGenerations": 0, "humanReviewed": False}
    write_new(destination / "manifest.json", manifest)
    return manifest


def freeze_answers(folder, answer_rows):
    folder = Path(folder)
    bundle = read_json(folder / "private/bundle.json")
    manifest = read_json(folder / "manifest.json")
    require(sha(packed(bundle)) == manifest["bundleSha256"], "review_bundle_changed")
    packets = {p["reviewId"]: p for p in bundle["questionPackets"]}
    answers = unique(answer_rows, "reviewId", "answer")
    require(set(answers) == set(packets) and len(answers) == 64, "all_64_fresh_answer_contexts_required")
    actors = []
    for rid, answer in answers.items():
        validate_answer(answer, packets[rid]); actors.append(answer["reviewer"]["actorId"])
    require(len(set(actors)) == 64, "answer_context_reused_across_units_or_candidates")
    ordered = [answers[rid] for rid in sorted(answers)]
    write_new(folder / "answers-frozen.json", {"version": VERSION, "rows": ordered})
    receipt = {"version": VERSION, "answersSha256": sha(packed({"version": VERSION, "rows": ordered})),
               "answerContexts": 64, "questions": 128, "freshContextActorIds": actors,
               "sourceGradingCompleted": False, "humanReviewed": False}
    write_new(folder / "answers-freeze.json", receipt)
    for packet in bundle["sourcePackets"]:
        answer = answers[packet["reviewId"]]
        write_new(folder / "grade-packets" / (packet["reviewId"] + ".json"),
                  {**packet, "answer": answer, "answerSha256": sha(packed(answer))})
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare"); prepare.add_argument("--outputs", type=Path, required=True); prepare.add_argument("--destination", type=Path, required=True)
    freeze = sub.add_parser("freeze-answers"); freeze.add_argument("--folder", type=Path, required=True); freeze.add_argument("--answers", type=Path, required=True)
    report = sub.add_parser("report"); report.add_argument("--folder", type=Path, required=True); report.add_argument("--source-reviews", type=Path)
    report.add_argument("--grades", type=Path); report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare_files(args.outputs, args.destination)
    elif args.command == "freeze-answers":
        result = freeze_answers(args.folder, read_rows(args.answers))
    else:
        bundle = read_json(args.folder / "private/bundle.json")
        manifest = read_json(args.folder / "manifest.json")
        require(sha(packed(bundle)) == manifest["bundleSha256"], "review_bundle_changed")
        answers = []
        if args.grades or (args.folder / "answers-freeze.json").exists():
            receipt = read_json(args.folder / "answers-freeze.json")
            answer_path = args.folder / "answers-frozen.json"
            require(sha(answer_path.read_bytes()) == receipt["answersSha256"], "frozen_answers_changed")
            answers = read_rows(answer_path)
        result = aggregate(bundle, read_rows(args.source_reviews) if args.source_reviews else [], answers,
                           read_rows(args.grades) if args.grades else [])
        write_new(args.output, result)
    print(json.dumps({k: result[k] for k in result if k in ("version", "status", "counts", "answerContexts", "questions")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
