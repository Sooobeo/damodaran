"""Synthetic formal/export/collection boundaries; no model calls."""
import copy
import sys
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
from input_execution_v1 import local_question_transport_v1 as t
from input_execution_v1 import evaluation as e


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / t.tr.BASE
        self.formal_path = self.base / "formal"
        self.export = self.base / "question-export"
        self.run = self.base / "native-run"
        self.dest = self.base / "collection"
        for path, value in ((t.PROTOCOL, "Synthetic procedure"), ("small-code.py", "pass\n")):
            target = self.root / path; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value, encoding="utf-8")
        self.code_ref = t.tr.reference(self.root, self.root / "small-code.py")
        frozen_refs = []
        for name in sorted(t.contract.REQUIRED_FREEZE_FILES):
            target = self.root / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True); target.write_text("Synthetic\n", encoding="utf-8")
            frozen_refs.append(t.tr.reference(self.root, target))
        freeze = {"version": "input-execution-v1-local-question-execution-freeze-v1",
                  "actualQuestionCallsAtFreeze": 0, "files": frozen_refs}
        e.write_new(self.root / t.FREEZE, freeze)
        packets = {rid: {"reviewId": rid, "translation": "문은 닫혔다. 창은 열렸다.",
                   "questions": [{"questionId": "q1", "questionKo": "문 상태?"},
                                 {"questionId": "q2", "questionKo": "창 상태?"}]} for rid in t.contract.IDS}
        self.formal = {"folder": self.formal_path, "packets": packets, "manifestSha256": "a" * 64,
                       "bundleSha256": "b" * 64, "evidence": [self.code_ref]}
        self.formal_patch = patch.object(t.reval, "load_formal", return_value=self.formal)
        self.formal_patch.start(); self.addCleanup(self.formal_patch.stop)
        contexts = []
        for idx, rid in enumerate(t.contract.IDS):
            draft = {"reviewId": rid, "answers": [{"questionId": q, "answerKo": "닫혔다.", "reasonKo": "본문 서술이다.",
                    "cannotDetermine": False, "translationEvidence": ["문은 닫혔다."]} for q in ("q1", "q2")]}
            contexts.append({"reviewId": rid, "contextId": "fixture-context-" + rid,
                             "packetSha256": e.sha(e.packed(packets[rid])), "draft": draft,
                             "process": {"pid": idx + 1, "creationTime": str(idx)}})
        self.native = {"contexts": contexts, "evidence": [self.code_ref], "summary": {"status": "synthetic"}}
        module = types.ModuleType("input_execution_v1.local_question_runner_v1")
        module.validate_run = lambda *a, **kw: self.native
        self.runner_patch = patch.dict(sys.modules, {module.__name__: module})
        self.runner_patch.start(); self.addCleanup(self.runner_patch.stop)

    def prepare(self):
        return t.export_questions(self.formal_path, self.export, self.root)

    def collect(self):
        self.prepare()
        return t.collect(self.formal_path, self.export, self.run, self.dest, self.root)

    def test_export_exact_question_only_and_new_destination(self):
        self.prepare()
        self.assertEqual(len(list(self.export.iterdir())), 65)
        packet, _ = t.tr.read(self.export / "R001.json")
        self.assertEqual(set(packet), {"reviewId", "translation", "questions"})
        self.assertNotIn("formalPath", t.tr.read(self.export / "manifest.json")[0])
        with self.assertRaises(ValueError): self.prepare()

    def test_source_field_rejected_before_write(self):
        self.formal["packets"]["R001"]["source"] = "hidden"
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.export.exists())

    def test_freeze_change_rejected_before_write(self):
        (self.root / t.PROTOCOL).write_text("changed")
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.export.exists())

    def test_export_tamper_and_extra_file_rejected(self):
        self.prepare()
        (self.export / "extra.json").write_text("{}")
        with self.assertRaises(ValueError): t.verify_export(self.formal, self.export, self.root)
        (self.export / "extra.json").unlink()
        path = self.export / "R001.json"; path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaises(ValueError): t.verify_export(self.formal, self.export, self.root)

    def test_complete_native_collection_roundtrip_and_no_fabricated_spawn(self):
        receipt = self.collect()
        validated = t.validate_collection(self.dest, self.formal_path, self.root)
        self.assertEqual(len(validated["rows"]), 64)
        self.assertEqual(receipt["actualCollaborationSpawns"], 0)
        self.assertTrue(all(a["reviewer"]["actorId"].startswith("local-native:") for a in validated["rows"]))
        self.assertEqual(validated["rows"][0]["answers"][0]["answerKo"], "닫혔다.")

    def test_context_reuse_or_missing_output_rejected(self):
        self.prepare()
        self.native["contexts"][1]["contextId"] = self.native["contexts"][0]["contextId"]
        with self.assertRaises(ValueError): t.collect(self.formal_path, self.export, self.run, self.dest, self.root)
        self.native["contexts"].pop()
        with self.assertRaises(ValueError): t.collect(self.formal_path, self.export, self.run, self.dest, self.root)
        self.assertFalse(self.dest.exists())

    def test_wrong_draft_id_is_not_overwritten(self):
        self.prepare(); self.native["contexts"][0]["draft"]["reviewId"] = "R002"
        with self.assertRaises(ValueError): t.collect(self.formal_path, self.export, self.run, self.dest, self.root)

    def test_wrong_actual_packet_rejected(self):
        self.prepare(); self.native["contexts"][0]["packetSha256"] = "c" * 64
        with self.assertRaises(ValueError): t.collect(self.formal_path, self.export, self.run, self.dest, self.root)

    def test_answer_change_after_collect_rejected(self):
        self.collect()
        path = self.dest / "answers.json"
        value, _ = t.tr.read(path); value["rows"][0]["answers"][0]["answerKo"] = "열렸다."
        path.write_bytes(e.packed(value))
        with self.assertRaises(ValueError): t.validate_collection(self.dest, self.formal_path, self.root)

    def test_native_provenance_change_after_collect_rejected(self):
        self.collect(); self.native["contexts"][0]["process"]["pid"] += 10
        with self.assertRaises(ValueError): t.validate_collection(self.dest, self.formal_path, self.root)

    def test_canonical_private_scope_before_writing(self):
        for bad in (self.base / "../../../escaped-output", self.root / "outside", self.base / "holdout-v1/out"):
            with self.assertRaises(ValueError): t.export_questions(self.formal_path, bad, self.root)
            self.assertFalse(bad.resolve().exists())

    def test_missing_or_duplicate_freeze_contract_rejected(self):
        path = self.root / t.FREEZE
        original, _ = t.tr.read(path)
        extra = self.root / "content/extra-source.json"
        extra.write_text("{}", encoding="utf-8")
        for files in ([], original["files"][:-1], original["files"] + [original["files"][0]],
                      original["files"] + [t.tr.reference(self.root, extra)]):
            path.write_bytes(e.packed(original | {"files": files}))
            with self.assertRaises(ValueError): self.prepare()
            self.assertFalse(self.export.exists())

    def test_formal_path_checked_before_legacy_loader(self):
        bad = self.base / "../../../formal"
        with patch.object(t.reval, "load_formal") as loader:
            for call in (lambda: t.export_questions(bad, self.export, self.root),
                         lambda: t.collect(bad, self.export, self.run, self.dest, self.root),
                         lambda: t.validate_collection(self.dest, bad, self.root)):
                with self.assertRaises(ValueError): call()
            loader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
