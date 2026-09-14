"""Synthetic tool-contract tests. These fixtures are not model translations."""
from copy import deepcopy
from collections import Counter
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import evaluation as e


def span(text):
    return {"start": 0, "end": len(text), "text": text}


def fixture():
    units, annotations, outputs, prompts = {}, {}, [], []
    for i in range(16):
        uid = "unit" + str(i)
        source = "The record includes cash. It remains available. The amount is unchanged."
        unit = {"id": uid, "domain": "finance" if i < 8 else "general", "targetBlockId": uid + "b",
                "provenance": {"kind": "stored-public-source" if i < 4 else "assistant-authored-structured-fixture"},
                "document": {"resourceId": "document" + str(i // 2), "blocks": [{"id": uid + "b", "text": source, "textSha256": e.sha(source)}]}}
        units[uid] = unit
        props = [{"id": uid + "p" + str(j), "core": True, "sourceQuote": source, "expectedMeaningKo": "시험 명제"} for j in range(3)]
        questions = [{"id": uid + "q" + str(j), "core": j == 0, "questionKo": "기록의 상태는 무엇인가요?", "sourceQuote": source,
                      "expectedAnswerKo": "시험 정답", "allowedKoreanContext": ""} for j in range(2)]
        terms = [{"id": uid + "t" + str(j), "sourceQuote": source, "sourceTerm": "record", "sourceStartCodepoint": 4,
                  "sourceEndCodepoint": 10, "meaningKo": "기록", "allowedKorean": ["기록"]} for j in range(2 if i < 15 else 9)]
        annotations[uid] = {"id": uid, "targetTextSha256": e.sha(source), "propositions": props, "questions": questions, "terms": terms}
        for index, cfg in enumerate(e.CONFIGURATIONS):
            prompt = "fixture " + uid + cfg
            text = "이 기록에는 현금이 포함된다. 계속 이용할 수 있다. 금액은 변하지 않는다."
            outputs.append({"id": uid, "configuration": cfg, "status": "completed", "source": source,
                            "sourceSha256": e.sha(source), "promptSha256": e.sha(prompt), "translation": text,
                            "translationSha256": e.sha(text), "normalization": "none", "postProcessingApplied": False,
                            "outputTokens": 2, "outputTokenIds": [1, 2], "technicalChecks": {k: True for k in e.TECHNICAL_CHECKS},
                            "runId": "test-fixture-run", "producerVersion": "test-fixture", "runtimeContractVersion": "test-fixture",
                            "preparedManifestSha256": e.S2_SHA,
                            "rawResponsePath": "raw/" + uid + cfg + ".json", "rawResponseSha256": "a" * 64})
            prompts.append({"id": uid, "configuration": cfg, "promptSha256": e.sha(prompt), "promptTokens": 100 - index,
                            "context": {"text": "fixture context"}})
    return e.build_review_packets(outputs, units, annotations, prompts), outputs, units, annotations, prompts


def reviews_for(bundle):
    reviews, answers, grades = [], [], []
    qs = {p["reviewId"]: p for p in bundle["questionPackets"]}
    for p in bundle["sourcePackets"]:
        rid = p["reviewId"]
        common = {"reviewId": rid, "sourceSha256": p["sourceSha256"], "translationSha256": p["translationSha256"],
                  "humanReviewed": False, "reviewer": {"actorId": "source-grader", "model": "synthetic-fixture"}}
        evidence = {"sourceEvidence": [span(p["source"])], "translationEvidence": [span(p["translation"])], "reasonKo": "시험 근거"}
        reviews.append({**common, "fullText": {"reviewedAllText": True, "severity": "neutral", "issues": [], **evidence},
                        "propositions": [{"id": x["id"], "verdict": "preserved", **evidence} for x in p["evaluation"]["propositions"]],
                        "terms": [{"id": x["id"], "meaningCorrect": True, "renderingAcceptable": True, **evidence} for x in p["evaluation"]["terms"]],
                        "naturalness": {"score": 3, "reasonKo": "시험 근거", "translationEvidence": [span(p["translation"])]}})
        answer = {"reviewId": rid, "packetSha256": e.sha(e.packed(qs[rid])), "humanReviewed": False,
                  "reviewer": {"actorId": "answer-" + rid, "model": "synthetic-fixture", "freshContext": True,
                               "priorTaskExposure": False, "allowedExposures": ["packet_translation", "packet_questions"]},
                  "answers": [{"questionId": q["questionId"], "answerKo": "현금이 포함된다.", "reasonKo": "시험 근거",
                               "translationEvidence": [span(p["translation"])]} for q in qs[rid]["questions"]]}
        answers.append(answer)
        grades.append({**common, "answerSha256": e.sha(e.packed(answer)),
                       "questions": [{"questionId": q["questionId"], "verdict": "correct", "reasonKo": "시험 근거",
                                      "sourceEvidence": [span(p["source"])], "answerEvidence": [span(q["answerKo"])]} for q in answer["answers"]]})
    return reviews, answers, grades


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.bundle, self.outputs, self.units, self.annotations, self.prompts = fixture()
        self.reviews, self.answers, self.grades = reviews_for(self.bundle)

    def result(self):
        return e.aggregate(self.bundle, self.reviews, self.answers, self.grades)

    def test_packet_allowlist_and_fixed_shuffle(self):
        self.assertEqual(self.bundle, fixture()[0])
        self.assertEqual(len(self.bundle["questionPackets"]), 64)
        for packet in self.bundle["questionPackets"]:
            self.assertEqual(set(packet), {"reviewId", "translation", "questions"})
            self.assertEqual([q["questionId"] for q in packet["questions"]], ["q1", "q2"])
            self.assertTrue(all(set(q) == {"questionId", "questionKo"} for q in packet["questions"]))
        self.assertEqual(Counter(m["stratum"] for m in self.bundle["mapping"]),
                         {"finance_public": 16, "finance_synthetic": 16, "general_synthetic": 32})

    def test_incomplete_duplicate_failed_outputs_rejected(self):
        for rows in (self.outputs[:-1], self.outputs[:-1] + [self.outputs[0]]):
            with self.assertRaises(ValueError): e.validate_outputs(rows, self.units, self.prompts)
        self.outputs[0]["status"] = "failed"
        with self.assertRaisesRegex(ValueError, "incomplete"): e.validate_outputs(self.outputs, self.units, self.prompts)

    def test_output_hash_processing_and_checks_cannot_be_bypassed(self):
        for key, value in (("sourceSha256", "0" * 64), ("promptSha256", "0" * 64), ("translationSha256", "0" * 64),
                           ("postProcessingApplied", True), ("technicalChecks", {}), ("technicalChecks", {"x": "true"}),
                           ("outputTokens", 4096), ("outputTokenIds", [1])):
            rows = deepcopy(self.outputs); rows[0][key] = value
            with self.assertRaises(ValueError): e.validate_outputs(rows, self.units, self.prompts)

    def test_raw_response_content_is_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            for row in self.outputs:
                path = Path(tmp) / row["rawResponsePath"]
                e.write_new(path, {"content": row["translation"]})
                row["rawResponseSha256"] = e.sha(path.read_bytes())
            e.validate_outputs(self.outputs, self.units, self.prompts, tmp)
            row = self.outputs[0]; path = Path(tmp) / row["rawResponsePath"]
            path.write_bytes(e.packed({"content": "changed"})); row["rawResponseSha256"] = e.sha(path.read_bytes())
            with self.assertRaisesRegex(ValueError, "raw_response_translation"):
                e.validate_outputs(self.outputs, self.units, self.prompts, tmp)

    def test_run_shutdown_and_final_integrity_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); folder = root / "run"; folder.mkdir()
            path = folder / "predictions.jsonl"; path.write_bytes(b"{}\n")
            summary = {"status": "completed", "completedOutputs": 64, "missingOutputs": 0,
                       "childProcessStopped": True, "integrityVerified": True, "outputIntegrityPassed": True,
                       "artifactHashes": {"predictions.jsonl": e.sha(path.read_bytes())}}
            (folder / "summary.json").write_bytes(e.packed(summary))
            e.validate_run_completion(path, root)
            summary["childProcessStopped"] = False; (folder / "summary.json").write_bytes(e.packed(summary))
            with self.assertRaisesRegex(ValueError, "shutdown"): e.validate_run_completion(path, root)

    def test_outputs_from_multiple_runs_are_not_merged(self):
        self.outputs[0]["runId"] = "other"
        with self.assertRaisesRegex(ValueError, "mixed"): e.validate_outputs(self.outputs, self.units, self.prompts)

    def test_codepoint_spans_and_changed_quotes(self):
        e.exact_span("😀기록", {"start": 1, "end": 3, "text": "기록"})
        with self.assertRaises(ValueError): e.exact_span("😀기록", {"start": 2, "end": 4, "text": "기록"})
        review = deepcopy(self.reviews[0]); review["fullText"]["sourceEvidence"][0]["text"] = "changed"
        with self.assertRaises(ValueError): e.validate_source_review(review, self.bundle["sourcePackets"][0])

    def test_full_text_issue_severity_consistency_and_missing_proposition(self):
        review = deepcopy(self.reviews[0]); review["fullText"]["severity"] = "major"
        with self.assertRaisesRegex(ValueError, "inconsistency"): e.validate_source_review(review, self.bundle["sourcePackets"][0])
        review = deepcopy(self.reviews[0]); review["propositions"].pop()
        with self.assertRaisesRegex(ValueError, "inventory"): e.validate_source_review(review, self.bundle["sourcePackets"][0])

    def test_string_match_does_not_invent_term_semantics(self):
        self.reviews[0]["terms"][0]["meaningCorrect"] = False
        result = self.result()
        row = next(r for r in result["rows"] if r["reviewId"] == self.reviews[0]["reviewId"])
        self.assertEqual(row["terms"]["passed"], row["terms"]["total"] - 1)

    def test_missing_evaluation_is_unresolved_never_pass(self):
        report = e.aggregate(self.bundle)
        self.assertEqual(report["decision"]["status"], "selection_deferred")
        for s in report["decision"]["configurations"].values():
            self.assertEqual(s["questions"], {"passed": 0, "total": 32, "evaluated": 0, "rate": 0.0, "status": "not_evaluated"})
        self.assertIsNone(e.metric(0, 0, 0)["rate"])

    def test_answer_context_must_not_be_reused_or_exposed(self):
        self.answers[1]["reviewer"]["actorId"] = self.answers[0]["reviewer"]["actorId"]
        with self.assertRaisesRegex(ValueError, "reused"): e.aggregate(self.bundle, self.reviews, self.answers)
        self.answers[1]["reviewer"]["actorId"] = "unique"
        self.answers[1]["reviewer"]["priorTaskExposure"] = True
        with self.assertRaisesRegex(ValueError, "exposure"): e.aggregate(self.bundle, self.reviews, self.answers)

    def test_answer_packet_and_separate_grader_identity(self):
        self.answers[0]["packetSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "packet_identity"): self.result()
        self.answers[0]["packetSha256"] = e.sha(e.packed(self.bundle["questionPackets"][0]))
        self.grades[0]["reviewer"]["actorId"] = self.answers[0]["reviewer"]["actorId"]
        with self.assertRaisesRegex(ValueError, "separate_source_grader"): self.result()

    def test_all_manual_results_fixed_denominators(self):
        report = self.result()
        self.assertEqual(report["counts"]["questionGrades"], 128)
        for s in report["decision"]["configurations"].values():
            self.assertEqual((s["corePropositions"]["total"], s["terms"]["total"], s["questions"]["total"], s["coreQuestions"]["total"]), (48, 39, 32, 16))
        self.assertEqual(report["decision"]["status"], "retain_registered_configuration")
        self.assertFalse(report["decision"]["registrationApproved"])

    def selection_rows(self):
        return deepcopy(self.result()["rows"])

    def damage(self, row):
        pid = next(iter(row["propositionVerdicts"]))
        row["propositionVerdicts"][pid] = "damaged"; row["corePropositions"]["passed"] -= 1

    def test_core_regression_rejects_despite_other_gains(self):
        rows = self.selection_rows()
        baseline = next(r for r in rows if r["id"] == "unit0" and r["configuration"] == "C0"); self.damage(baseline)
        candidate = next(r for r in rows if r["id"] == "unit1" and r["configuration"] == "C1"); self.damage(candidate)
        decision = e.select_candidate(rows)
        self.assertFalse(decision["candidates"]["C1"]["eligible"])
        self.assertIn("new_core_proposition_damage", decision["candidates"]["C1"]["rejectionReasons"])

    def test_general_question_regression_rejects_financial_gain(self):
        rows = self.selection_rows()
        self.damage(next(r for r in rows if r["id"] == "unit0" and r["configuration"] == "C0"))
        candidate = next(r for r in rows if r["id"] == "unit8" and r["configuration"] == "C1")
        candidate["questionVerdicts"][next(iter(candidate["questionVerdicts"]))] = "incorrect"
        candidate["questions"]["passed"] -= 1
        self.assertIn("general_questionVerdicts_regression", e.select_candidate(rows)["candidates"]["C1"]["rejectionReasons"])

    def test_new_material_error_rejected_even_equal_error_count(self):
        rows = self.selection_rows()
        for cfg, category in (("C0", "old"), ("C1", "new")):
            row = next(r for r in rows if r["id"] == "unit0" and r["configuration"] == cfg)
            row["severity"] = "major"; row["issues"] = [{"severity": "major", "category": category, "sourceEvidence": [{"start": 0, "end": 5}]}]
        self.assertIn("new_material_error", e.select_candidate(rows)["candidates"]["C1"]["rejectionReasons"])

    def test_candidate_tie_uses_prompt_tokens_then_config_order(self):
        rows = self.selection_rows()
        self.damage(next(r for r in rows if r["id"] == "unit0" and r["configuration"] == "C0"))
        decision = e.select_candidate(rows)
        self.assertEqual(decision["selectedConfiguration"], "C3")
        for row in rows: row["promptTokens"] = 100
        self.assertEqual(e.select_candidate(rows)["selectedConfiguration"], "C1")

    def test_any_unresolved_candidate_defers_expansion(self):
        rows = self.selection_rows()
        self.damage(next(r for r in rows if r["id"] == "unit0" and r["configuration"] == "C0"))
        rows[0]["complete"] = False
        decision = e.select_candidate(rows)
        self.assertEqual(decision["status"], "selection_deferred")
        self.assertIsNone(decision["selectedConfiguration"])

    def test_frozen_answers_and_grade_packet_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); e.write_new(folder / "private/bundle.json", self.bundle)
            e.write_new(folder / "manifest.json", {"bundleSha256": e.sha(e.packed(self.bundle))})
            with self.assertRaisesRegex(ValueError, "64_fresh"): e.freeze_answers(folder, self.answers[:-1])
            self.assertFalse((folder / "grade-packets").exists())
            receipt = e.freeze_answers(folder, self.answers)
            self.assertEqual(receipt["questions"], 128)
            self.assertEqual(e.sha((folder / "answers-frozen.json").read_bytes()), receipt["answersSha256"])
            self.assertEqual(len(list((folder / "grade-packets").glob("*.json"))), 64)
            with self.assertRaises(FileExistsError): e.freeze_answers(folder, self.answers)

    def test_ledger_only_prepares_distinct_idempotent_observations(self):
        report = self.result(); refs = [{"path": "fixture.json", "sha256": "a" * 64}]
        events = list(e.ledger_observations(self.bundle, report, refs))
        self.assertEqual(events, list(e.ledger_observations(self.bundle, report, refs)))
        self.assertTrue(all(event["kind"] == "input_preparation_observation" and not event["trainingUseAllowed"] for event in events))
        self.assertEqual(report["ledgerEventsAppended"], 0)

    def issue(self, error_id, severity="minor"):
        return {"issueId": error_id, "sourceErrorId": error_id, "severity": severity,
                "category": "semantic_roles", "sourceEvidence": [{"start": 0, "end": 20}]}

    def test_general_new_minor_is_rejected_at_same_highest_severity(self):
        rows = self.selection_rows()
        base = next(r for r in rows if r["id"] == "unit8" and r["configuration"] == "C0")
        candidate = next(r for r in rows if r["id"] == "unit8" and r["configuration"] == "C1")
        base["severity"] = candidate["severity"] = "minor"
        base["issues"] = [self.issue("original")]
        candidate["issues"] = [self.issue("original"), self.issue("new")]
        comparison = e.compare_unit(base, candidate)
        self.assertEqual(comparison["status"], "regressed")
        self.assertIn("new_minor_meaning_error", comparison["rejectionReasons"])

    def test_same_critical_grade_and_quote_do_not_hide_a_new_error(self):
        rows = self.selection_rows()
        base = next(r for r in rows if r["id"] == "unit0" and r["configuration"] == "C0")
        candidate = next(r for r in rows if r["id"] == "unit0" and r["configuration"] == "C1")
        base["severity"] = candidate["severity"] = "critical"
        base["issues"] = [self.issue("old_relation", "critical")]
        candidate["issues"] = [self.issue("different_relation", "critical")]
        self.assertIn("new_material_error", e.compare_unit(base, candidate)["rejectionReasons"])
        candidate["issues"] = [self.issue("old_relation", "critical")]
        self.assertEqual(e.compare_unit(base, candidate)["status"], "maintained")

    def test_same_error_identity_with_worse_severity_is_regression(self):
        rows = self.selection_rows()
        base = next(r for r in rows if r["id"] == "unit0" and r["configuration"] == "C0")
        candidate = next(r for r in rows if r["id"] == "unit0" and r["configuration"] == "C1")
        base["severity"], candidate["severity"] = "major", "critical"
        base["issues"] = [self.issue("same_relation", "major")]
        candidate["issues"] = [self.issue("same_relation", "critical")]
        self.assertIn("new_material_error", e.compare_unit(base, candidate)["rejectionReasons"])

    def test_source_error_identity_is_required_and_not_duplicated(self):
        packet, review = self.bundle["sourcePackets"][0], deepcopy(self.reviews[0])
        issue = {**self.issue("same_relation"), "reasonKo": "원문 의미 관계 변경", "sourceEvidence": [span(packet["source"])],
                 "translationEvidence": [span(packet["translation"])]}
        review["fullText"]["severity"] = "minor"; review["fullText"]["issues"] = [issue]
        e.validate_source_review(review, packet)
        review["fullText"]["issues"].append({**issue, "issueId": "second"})
        with self.assertRaisesRegex(ValueError, "source_error_duplicate"): e.validate_source_review(review, packet)
        review["fullText"]["issues"] = [{**issue, "sourceErrorId": ""}]
        with self.assertRaisesRegex(ValueError, "explicit_source_error"): e.validate_source_review(review, packet)

    def test_missing_core_question_or_source_review_prevents_selection(self):
        for kind in ("grade", "review"):
            with self.subTest(kind=kind):
                report = e.aggregate(self.bundle, self.reviews[:-1] if kind == "review" else self.reviews,
                                     self.answers, self.grades[:-1] if kind == "grade" else self.grades)
                self.assertEqual(report["decision"]["status"], "selection_deferred")
                self.assertIsNone(report["decision"]["selectedConfiguration"])
        grade = deepcopy(self.grades[0]); grade["questions"].pop(0)
        with self.assertRaisesRegex(ValueError, "grade_question_inventory"):
            e.validate_question_grade(grade, self.answers[0], self.bundle["sourcePackets"][0])

    def test_unresolved_core_proposition_or_question_never_counts_as_pass(self):
        review = deepcopy(self.reviews[0]); review["propositions"][0]["verdict"] = "unresolved"
        report = e.aggregate(self.bundle, [review] + self.reviews[1:], self.answers, self.grades)
        row = next(r for r in report["rows"] if r["reviewId"] == review["reviewId"])
        self.assertEqual(row["corePropositions"]["evaluated"], 2)
        self.assertFalse(row["complete"])
        grade = deepcopy(self.grades[0]); grade["questions"][0]["verdict"] = "unresolved"
        report = e.aggregate(self.bundle, self.reviews, self.answers, [grade] + self.grades[1:])
        row = next(r for r in report["rows"] if r["reviewId"] == grade["reviewId"])
        self.assertEqual((row["coreQuestions"]["passed"], row["coreQuestions"]["evaluated"], row["coreQuestions"]["total"]), (0, 0, 1))

    def reward_fixture(self):
        rows = self.selection_rows()
        self.damage(next(r for r in rows if r["id"] == "unit0" and r["configuration"] == "C0"))
        return rows

    def wrong_question(self, rows, cfg, uid, index):
        row = next(r for r in rows if r["id"] == uid and r["configuration"] == cfg)
        qid = list(row["questionVerdicts"])[index]
        row["questionVerdicts"][qid] = "incorrect"; row["questions"]["passed"] -= 1
        if index == 0: row["coreQuestions"]["passed"] -= 1

    def test_core_questions_precede_total_questions_and_tokens(self):
        rows = self.reward_fixture()
        self.wrong_question(rows, "C1", "unit1", 1)
        self.wrong_question(rows, "C1", "unit2", 1)
        for cfg in ("C2", "C3"): self.wrong_question(rows, cfg, "unit1", 0)
        decision = e.select_candidate(rows)
        self.assertEqual(decision["selectedConfiguration"], "C1")
        self.assertEqual(decision["configurations"]["C1"]["questions"]["passed"], 30)
        self.assertEqual(decision["configurations"]["C2"]["questions"]["passed"], 31)

    def test_total_questions_precede_terms_naturalness_and_tokens(self):
        rows = self.reward_fixture()
        for cfg in ("C2", "C3"): self.wrong_question(rows, cfg, "unit1", 1)
        candidate = next(r for r in rows if r["configuration"] == "C1" and r["id"] == "unit1")
        candidate["terms"]["passed"] -= 1
        candidate["termVerdicts"][next(iter(candidate["termVerdicts"]))] = False
        candidate["naturalness"] = 1
        self.assertEqual(e.select_candidate(rows)["selectedConfiguration"], "C1")

    def test_terms_precede_naturalness_and_tokens(self):
        rows = self.reward_fixture()
        for cfg in ("C2", "C3"):
            candidate = next(r for r in rows if r["configuration"] == cfg and r["id"] == "unit1")
            candidate["terms"]["passed"] -= 1
            candidate["termVerdicts"][next(iter(candidate["termVerdicts"]))] = False
        next(r for r in rows if r["configuration"] == "C1" and r["id"] == "unit1")["naturalness"] = 1
        self.assertEqual(e.select_candidate(rows)["selectedConfiguration"], "C1")

    def test_naturalness_precedes_tokens_and_speed(self):
        rows = self.reward_fixture()
        for row in rows:
            row["generationSeconds"] = 1 if row["configuration"] == "C3" else 100
            if row["configuration"] in ("C2", "C3"): row["naturalness"] = 2
        self.assertEqual(e.select_candidate(rows)["selectedConfiguration"], "C1")

    def test_propositions_precede_questions_and_naturalness(self):
        rows = self.reward_fixture()
        for cfg in ("C2", "C3"):
            self.damage(next(r for r in rows if r["id"] == "unit0" and r["configuration"] == cfg))
        self.wrong_question(rows, "C1", "unit1", 0)
        next(r for r in rows if r["configuration"] == "C1" and r["id"] == "unit1")["naturalness"] = 1
        self.assertEqual(e.select_candidate(rows)["selectedConfiguration"], "C1")

    def test_material_error_units_precede_proposition_count(self):
        rows = self.reward_fixture()
        base = next(r for r in rows if r["configuration"] == "C0" and r["id"] == "unit1")
        c1 = next(r for r in rows if r["configuration"] == "C1" and r["id"] == "unit1")
        for row in (base, c1):
            row["severity"] = "major"; row["issues"] = [self.issue("same_error", "major")]
        for cfg in ("C2", "C3"):
            self.damage(next(r for r in rows if r["id"] == "unit0" and r["configuration"] == cfg))
        decision = e.select_candidate(rows)
        self.assertTrue(decision["candidates"]["C1"]["eligible"])
        self.assertEqual(decision["selectedConfiguration"], "C3")

    def test_only_minor_fluency_or_token_gains_do_not_qualify(self):
        rows = self.selection_rows()
        base = next(r for r in rows if r["configuration"] == "C0" and r["id"] == "unit1")
        base["severity"] = "minor"; base["issues"] = [self.issue("minor_only")]; base["naturalness"] = 1
        decision = e.select_candidate(rows)
        self.assertEqual(decision["status"], "retain_registered_configuration")
        self.assertTrue(all("no_required_development_improvement" in c["rejectionReasons"] for c in decision["candidates"].values()))
        self.assertFalse(decision["independentHoldoutAuthorizedByThisResult"])


if __name__ == "__main__":
    unittest.main()
