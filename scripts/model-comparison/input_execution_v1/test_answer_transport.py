"""Synthetic transport tests only: no actual assignments, answers or model calls."""
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import answer_transport as tr
from input_execution_v1 import evaluation as ev
from input_execution_v1.test_evaluation import fixture


class AnswerTransportTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        _, outputs, units, annotations, prepared = fixture()
        for row in outputs:
            raw_path = self.root / row["rawResponsePath"]
            ev.write_new(raw_path, {"content": row["translation"]})
            row["rawResponseSha256"] = ev.sha(raw_path.read_bytes())
        run = self.root / "synthetic-run"
        run.mkdir()
        self.predictions = run / "predictions.jsonl"
        self.predictions.write_bytes(b"".join(ev.packed(row) for row in outputs))
        self.summary = run / "summary.json"
        ev.write_new(self.summary, {"status": "completed", "completedOutputs": 64, "missingOutputs": 0,
                     "childProcessStopped": True, "integrityVerified": True, "outputIntegrityPassed": True,
                     "artifactHashes": {"predictions.jsonl": ev.sha(self.predictions.read_bytes())}})
        self.source = self.root / "synthetic-source-inventory.json"
        ev.write_new(self.source, {"syntheticFixtureOnly": True})
        evidence = [tr.reference(self.root, self.source)]
        self.load_source = self.stack.enter_context(patch.object(ev, "load_development", return_value=(units, annotations, evidence)))
        self.load_prepared = self.stack.enter_context(patch.object(ev, "load_prepared", return_value=prepared))
        self.folder = self.root / tr.BASE / "synthetic-formal"
        self.drafts = self.root / tr.BASE / "synthetic-drafts"
        self.destination = self.root / tr.BASE / "synthetic-collection"
        protocol = self.root / tr.PROTOCOL
        protocol.parent.mkdir(parents=True)
        protocol.write_text("Synthetic protocol fixture, not a real review.\n", encoding="utf-8")
        ev.prepare_files(self.predictions, self.folder, self.root)
        self.bundle = ev.read_json(self.folder / "private/bundle.json")
        self.packets = {p["reviewId"]: p for p in self.bundle["questionPackets"]}

    def record(self, rid="R001", actor="/root/synthetic_r001"):
        return tr.record(self.folder, rid, actor, self.root)

    def update_json(self, path, change):
        value = ev.read_json(path)
        change(value)
        path.write_bytes(ev.packed(value))

    def populate(self):
        first = self.record()
        for rid in tr.IDS:
            if rid != "R001":
                item = deepcopy(first)
                item["reviewId"] = rid
                item["packetSha256"] = ev.sha(ev.packed(self.packets[rid]))
                item["reviewer"]["actorId"] = item["spawn"]["actorId"] = "/root/synthetic_" + rid.lower()
                ev.write_new(self.folder / "assignments" / (rid + ".json"), item)
            packet = self.packets[rid]
            draft = {"reviewId": rid, "answers": [{"questionId": q["questionId"], "answerKo": "합성 답변",
                     "reasonKo": "도구 형식 검사만을 위한 합성 이유", "translationEvidence": [packet["translation"]]}
                     for q in packet["questions"]]}
            ev.write_new(self.drafts / (rid + ".json"), draft)

    def collect(self):
        return tr.collect(self.folder, self.drafts, self.destination, self.root)

    def assert_collect_fails(self, error):
        with self.assertRaisesRegex(ValueError, error):
            self.collect()
        self.assertFalse(self.destination.exists())

    def test_record_links_supplied_actor_exact_packet_and_unknown_model(self):
        value = self.record()
        self.assertEqual(value["reviewer"], tr.reviewer("/root/synthetic_r001"))
        self.assertEqual(value["spawn"]["forkTurns"], "none")
        self.assertEqual(value["packetSha256"], ev.sha(ev.packed(self.packets["R001"])))
        self.assertFalse(value["isolationIndependentlyVerified"])
        self.assertEqual(value["reviewer"]["model"], tr.MODEL)
        self.assertFalse((self.folder / ".assignment-record.lock").exists())
        self.assertTrue(self.load_source.called and self.load_prepared.called)

    def test_no_actor_or_review_id_is_automatically_created(self):
        for rid, actor in (("R000", "/root/test"), ("R065", "/root/test"), ("R001", ""),
                           ("R001", "/root"), ("R001", "invented-short-id")):
            with self.subTest(rid=rid, actor=actor), self.assertRaises(ValueError):
                self.record(rid, actor)
        self.assertFalse((self.folder / "assignments").exists())

    def test_existing_assignment_and_duplicate_actor_rejected_without_stale_lock(self):
        self.record()
        before = (self.folder / "assignments/R001.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "must_be_new"):
            self.record()
        with self.assertRaisesRegex(ValueError, "actor_reused"):
            self.record("R002")
        self.assertEqual(before, (self.folder / "assignments/R001.json").read_bytes())
        self.assertFalse((self.folder / ".assignment-record.lock").exists())
        self.record("R002", "/root/synthetic_r002")

    def test_concurrent_record_lock_is_not_stolen_or_removed(self):
        lock = self.folder / ".assignment-record.lock"
        lock.write_bytes(b"other process")
        with self.assertRaises(FileExistsError):
            self.record()
        self.assertEqual(lock.read_bytes(), b"other process")

    def test_unfinished_s4_rejected_before_assignment(self):
        self.update_json(self.summary, lambda v: v.update(childProcessStopped=False))
        with self.assertRaisesRegex(ValueError, "shutdown"):
            self.record()
        self.assertFalse((self.folder / "assignments").exists())

    def test_manifest_packet_inventory_and_file_changes_rejected(self):
        path = self.folder / "question-packets/R064.json"
        path.unlink()
        with self.assertRaisesRegex(ValueError, "file_inventory"):
            self.record()
        self.assertFalse((self.folder / "assignments").exists())

    def test_extra_source_field_even_with_rehashed_bundle_is_rejected(self):
        self.bundle["questionPackets"][0]["source"] = "forbidden source fixture"
        (self.folder / "private/bundle.json").write_bytes(ev.packed(self.bundle))
        self.update_json(self.folder / "manifest.json", lambda v: v.update(bundleSha256=ev.sha(ev.packed(self.bundle))))
        with self.assertRaisesRegex(ValueError, "bundle_replay"):
            self.record()

    def test_formal_manifest_count_cannot_be_relabelled(self):
        self.update_json(self.folder / "manifest.json", lambda v: v.update(questionPackets=63))
        with self.assertRaisesRegex(ValueError, "formal_s5_manifest"):
            self.record()

    def test_packet_whitespace_mutation_rejected_as_byte_change(self):
        path = self.folder / "question-packets/R001.json"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "packet_changed"):
            self.record()

    def test_collect_preserves_authored_text_and_original_bytes_without_freezing(self):
        self.populate()
        draft = self.drafts / "R001.json"
        draft.write_text(json.dumps(ev.read_json(draft), ensure_ascii=False, indent=2), encoding="utf-8")
        before = draft.read_bytes()
        receipt = self.collect()
        answers = ev.read_json(self.destination / "answers.json")["rows"]
        self.assertEqual(len(answers), 64)
        self.assertEqual(answers[0]["answers"][0]["answerKo"], "합성 답변")
        self.assertEqual(answers[0]["answers"][0]["reasonKo"], "도구 형식 검사만을 위한 합성 이유")
        self.assertEqual(answers[0]["answers"][0]["translationEvidence"][0]["text"], self.packets["R001"]["translation"])
        self.assertEqual(before, (self.destination / "original-drafts/R001.json").read_bytes())
        self.assertEqual(receipt["bindings"][0]["draftSha256"], ev.sha(before))
        self.assertEqual(receipt["answersSha256"], ev.sha((self.destination / "answers.json").read_bytes()))
        self.assertFalse(receipt["answersFrozen"] or receipt["sourceGradingCompleted"] or receipt["candidateSelectionPerformed"])
        self.assertFalse((self.folder / "answers-frozen.json").exists())
        self.assertFalse((self.folder / "grade-packets").exists())
        self.assertEqual(receipt["modelCalls"], 0)

    def test_destination_must_be_new(self):
        self.destination.mkdir()
        self.assert_collect_fails_existing_destination()

    def assert_collect_fails_existing_destination(self):
        with self.assertRaisesRegex(ValueError, "destination_must_be_new"):
            self.collect()
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_all_64_assignments_required(self):
        self.populate()
        (self.folder / "assignments/R064.json").unlink()
        self.assert_collect_fails("file_inventory")

    def test_all_64_drafts_required(self):
        self.populate()
        (self.drafts / "R064.json").unlink()
        self.assert_collect_fails("file_inventory")

    def test_draft_review_id_checked_before_helper_can_overwrite(self):
        self.populate()
        self.update_json(self.drafts / "R001.json", lambda v: v.update(reviewId="R002"))
        with patch.object(tr.review_io, "question_answer", side_effect=AssertionError("helper must not be called")) as convert:
            self.assert_collect_fails("draft_review_id_mismatch")
            convert.assert_not_called()

    def test_duplicate_json_review_id_cannot_be_silently_chosen(self):
        self.populate()
        path = self.drafts / "R001.json"
        path.write_bytes(path.read_bytes().replace(b'{', b'{"reviewId":"R064",', 1))
        self.assert_collect_fails("duplicate_json_key")

    def test_forged_reused_actor_in_two_records_rejected(self):
        self.populate()
        def change(v):
            v["reviewer"]["actorId"] = v["spawn"]["actorId"] = "/root/synthetic_r001"
        self.update_json(self.folder / "assignments/R002.json", change)
        self.assert_collect_fails("actor_reused")

    def test_assignment_packet_hash_and_exposure_are_rechecked(self):
        self.populate()
        self.update_json(self.folder / "assignments/R001.json", lambda v: v.update(packetSha256="0" * 64))
        self.assert_collect_fails("assignment_packet_identity")

    def test_prior_exposure_cannot_be_labelled_as_fresh(self):
        self.populate()
        self.update_json(self.folder / "assignments/R001.json", lambda v: v["reviewer"].update(priorTaskExposure=True))
        self.assert_collect_fails("exposure_provenance")

    def test_missing_question_wrong_quote_and_nonboolean_cannot_determine_rejected(self):
        self.populate()
        path = self.drafts / "R001.json"
        original = path.read_bytes()
        for mutate, error in ((lambda v: v["answers"].pop(), "question_inventory"),
                              (lambda v: v["answers"][0].update(translationEvidence=["없는 합성 인용"]), "not_in_text"),
                              (lambda v: v["answers"][0].update(cannotDetermine=1), "must_be_boolean"),
                              (lambda v: v["answers"][0].update(translationEvidence=[]), "missing_evidence")):
            with self.subTest(error=error):
                path.write_bytes(original)
                self.update_json(path, mutate)
                self.assert_collect_fails(error)

    def test_explicit_cannot_determine_allows_empty_evidence(self):
        self.populate()
        self.update_json(self.drafts / "R001.json", lambda v: v["answers"][0].update(cannotDetermine=True,
                         answerKo="번역만으로 판단할 수 없다.", translationEvidence=[]))
        self.collect()
        answer = ev.read_json(self.destination / "answers.json")["rows"][0]["answers"][0]
        self.assertTrue(answer["cannotDetermine"])
        self.assertEqual(answer["translationEvidence"], [])

    def test_changed_draft_during_conversion_rejected_before_any_output(self):
        self.populate()
        original = tr.review_io.question_answer
        def mutate(draft, packet, reviewer):
            result = original(draft, packet, reviewer)
            if packet["reviewId"] == "R002":
                self.update_json(self.drafts / "R001.json", lambda v: v["answers"][0].update(answerKo="changed"))
            return result
        with patch.object(tr.review_io, "question_answer", side_effect=mutate):
            self.assert_collect_fails("transport_evidence_changed")

    def test_draft_cannot_inject_reviewer_or_grading_fields(self):
        self.populate()
        self.update_json(self.drafts / "R001.json", lambda v: v.update(reviewer={"actorId": "other"}))
        self.assert_collect_fails("draft_schema")

    def test_transport_refuses_paths_outside_private_development(self):
        with self.assertRaisesRegex(ValueError, "private_development_path"):
            tr.collect(self.folder, self.drafts, self.root / "public/result", self.root)


if __name__ == "__main__":
    unittest.main()
