"""Focused answer freeze/report recovery tests over synthetic formal data."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_evaluation_recovery_v3 as local
from input_execution_v1 import local_question_evaluation_inventory_v2 as inventory_eval
from input_execution_v1 import local_question_transport_recovery_v3 as transport
from input_execution_v1 import local_question_runner_recovery_v3 as runner
from input_execution_v1 import test_local_question_evaluation_inventory_v2 as support
from input_execution_v1.test_local_question_transport_recovery_v3 import synthetic_recovery
from input_execution_v1.test_local_question_runner_recovery_v3 import recovery_fixture

ev, tr = local.ev, local.tr


class ZeroCallEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.f = support.LocalQuestionEvaluationTests("runTest")
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        f = self.f
        self.export = f.root / tr.BASE / "synthetic-export"
        transport.export_questions(f.folder, self.export, f.root)
        self.zero = synthetic_recovery(f.root, f.identity, tr.reference(f.root, self.export / "manifest.json"))
        f.stack.enter_context(patch.object(runner, "load_recovery", side_effect=lambda root: deepcopy(self.zero)))
        f.collection_receipt.update(zeroCallRecovery=deepcopy(self.zero), runnerValidatorVersion=runner.VERSION)
        f.receipt_path.write_bytes(ev.packed(f.collection_receipt))
        self.validator = f.stack.enter_context(patch.object(local, "validate_collection", side_effect=f.validated))

    def freeze(self):
        return local.freeze_answers(self.f.folder, self.f.collection, self.f.root)

    def read(self):
        return local.read_frozen_answers(local.load_formal(self.f.folder, self.f.root), self.f.root)

    def test_freeze_and_read_keep_answer_bytes_and_both_lineages(self):
        original = (self.f.folder / "manifest.json").read_bytes()
        receipt = self.freeze(); answers, refs = self.read()
        self.assertEqual(answers, self.f.answers)
        self.assertEqual(receipt["zeroCallRecovery"], self.zero)
        self.assertEqual(receipt["inventoryCorrection"], self.f.identity)
        self.assertEqual(receipt["version"], local.VERSION)
        self.assertEqual((self.f.folder / "answers-frozen.json").read_bytes(), ev.packed({"version": ev.VERSION, "rows": self.f.answers}))
        self.assertEqual(original, (self.f.folder / "manifest.json").read_bytes())
        self.assertTrue(all(ref in refs or ref in local.load_formal(self.f.folder, self.f.root)["evidence"]
            for ref in transport.recovery_refs(self.zero)))

    def test_collection_result_requires_exact_schema(self):
        self.f.validation["unexpected"] = "not allowed"
        with self.assertRaisesRegex(ValueError, "local_collection_validator_contract"):
            self.freeze()
        self.assertFalse((self.f.folder / "answers-frozen.json").exists())

    def test_forged_zero_call_receipt_cannot_be_frozen(self):
        self.f.collection_receipt["zeroCallRecovery"]["priorRun"]["completionRequestsSent"] = 1
        self.f.receipt_path.write_bytes(ev.packed(self.f.collection_receipt))
        with self.assertRaisesRegex(ValueError, "zero_call_collection_lineage_mismatch"):
            self.freeze()
        self.assertFalse((self.f.folder / "grade-packets").exists())

    def test_prior_proof_failure_prevents_native_collection_and_writes(self):
        with patch.object(runner, "load_recovery", side_effect=ValueError("prior_claim_changed")):
            with self.assertRaisesRegex(ValueError, "prior_claim_changed"):
                self.freeze()
        self.validator.assert_not_called()
        self.assertFalse((self.f.folder / "answers-freeze.json").exists())

    def test_old_version_and_edited_recovery_freeze_are_rejected(self):
        self.freeze(); path = self.f.folder / "answers-freeze.json"; raw = path.read_bytes()
        for alter in (lambda v: v.update(version=inventory_eval.VERSION),
                      lambda v: v["zeroCallRecovery"]["priorClaim"].update(sha256="0" * 64)):
            value = ev.read_json(path); alter(value); path.write_bytes(ev.packed(value))
            with self.assertRaisesRegex(ValueError, "freeze_contract"):
                self.read()
            path.write_bytes(raw)

    def test_prior_evidence_changed_after_freeze_is_rejected(self):
        self.freeze()
        path = self.f.root / self.zero["priorReview"]["path"]
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            self.read()

    def test_report_reuses_unchanged_aggregate_and_records_zero_call_proof(self):
        self.freeze(); f = self.f
        source, grades, output = [f.root / tr.BASE / name for name in ("source.json", "grades.json", "report.json")]
        ev.write_new(source, {"rows": f.source}); ev.write_new(grades, {"rows": f.grades})
        result = local.report(f.folder, output, source, grades, f.root)
        self.assertEqual({k: v for k, v in result.items() if k != "recoveryEvidence"},
            ev.aggregate(f.bundle, f.source, f.answers, f.grades))
        self.assertEqual(result["recoveryEvidence"]["zeroCallRecovery"], self.zero)
        self.assertEqual(result["recoveryEvidence"]["inventoryCorrection"], f.identity)

    def test_missing_answers_remain_incomplete_without_claiming_success(self):
        result = local.report(self.f.folder, self.f.root / tr.BASE / "incomplete.json", root=self.f.root)
        self.assertEqual(result["recoveryEvidence"]["questionAnswering"]["validatedContexts"], 0)
        self.assertIsNone(result["decision"]["selectedConfiguration"])
        self.assertEqual(result["recoveryEvidence"]["zeroCallRecovery"]["priorRun"]["status"], "failed")

    def test_actual_transport_collection_freeze_read_keeps_verbatim_answers(self):
        f = self.f; contexts = []
        for index, answer in enumerate(f.answers):
            rid = answer["reviewId"]
            draft_rows = []
            for row in answer["answers"]:
                draft_rows.append({**row, "translationEvidence": [r["text"] for r in row["translationEvidence"]]})
            contexts.append({"reviewId": rid, "contextId": "synthetic-v3-context-" + rid,
                "packetSha256": ev.sha(ev.packed(f.formal["packets"][rid])),
                "draft": {"reviewId": rid, "answers": draft_rows}, "process": {"pid": index + 1, "creationTicks": index + 100}})
        native = {"contexts": contexts, "summary": {"syntheticOnly": True, "version": runner.VERSION,
            "inventoryCorrection": f.identity, "zeroCallRecovery": self.zero}, "evidence": [tr.reference(f.root, f.raw_path)]}
        collected = f.root / tr.BASE / "actual-v3-synthetic-collection"
        with patch.object(runner, "validate_run", side_effect=lambda *a, **kw: deepcopy(native)), \
             patch.object(local, "validate_collection", side_effect=lambda directory, formalPath, root:
                transport.validate_collection(directory, formalPath, root=root)):
            receipt = transport.collect(f.folder, self.export, f.root / tr.BASE / "synthetic-native", collected, f.root)
            frozen = local.freeze_answers(f.folder, collected, f.root)
            answers, _ = local.read_frozen_answers(local.load_formal(f.folder, f.root), f.root)
        self.assertEqual((collected / "answers.json").read_bytes(), (f.folder / "answers-frozen.json").read_bytes())
        self.assertEqual([r["answers"] for r in answers], [r["answers"] for r in f.answers])
        self.assertEqual(frozen["collectionMetadata"], receipt)
        self.assertEqual(frozen["zeroCallRecovery"], self.zero)


class ActualRecoveryLoaderBoundaryTests(unittest.TestCase):
    def test_actual_prior_loader_binds_formal_and_blocks_changed_claim(self):
        with tempfile.TemporaryDirectory(prefix="evaluation-v3-prior-") as temporary:
            root = Path(temporary).resolve()
            with recovery_fixture(root) as identity:
                folder = root / tr.BASE / "synthetic-formal"
                original = {"folder": folder, "evidence": []}
                baseline = SimpleNamespace(load_formal=lambda *args: deepcopy(original))
                with patch.object(local, "baseline", baseline):
                    formal = local.load_formal(folder, root)
                    self.assertEqual(formal["zeroCallRecovery"], identity)
                    self.assertEqual(formal["evidence"], runner.recovery_refs(identity))
                    self.assertNotIn("zeroCallRecovery", original)
                    self.assertFalse((root / identity["priorRun"]["exportManifest"]["path"]).exists())
                    (root / identity["priorClaim"]["path"]).write_bytes(b"changed synthetic claim")
                    with patch.object(local, "validate_collection") as collection:
                        with self.assertRaisesRegex(ValueError, "evidence_hash_changed"):
                            local.freeze_answers(folder, root / tr.BASE / "unused-collection", root)
                    collection.assert_not_called()
                    self.assertFalse(folder.exists())


if __name__ == "__main__":
    unittest.main()
