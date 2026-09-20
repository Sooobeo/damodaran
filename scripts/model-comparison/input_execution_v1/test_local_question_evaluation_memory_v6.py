"""Focused v6 freeze/provenance/report tests; synthetic completed validator data."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_evaluation_memory_v6 as local
from input_execution_v1 import local_question_transport_memory_v6 as transport
from input_execution_v1 import local_question_runner_memory_v6 as runner
from input_execution_v1 import test_local_question_evaluation_battery_v4 as support
from input_execution_v1.test_local_question_transport_memory_v6 import synthetic_partial, logical_summary, source_run
ev, tr = local.ev, local.tr


class PartialEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.old = support.BatteryEvaluationTests("runTest")
        self.old.setUp(); self.addCleanup(self.old.doCleanups)
        self.f = self.old.f
        self.run = self.f.root / tr.BASE / "synthetic-v5-native"
        self.partial, self.runs = synthetic_partial(self.old.battery, self.f.root, self.run.relative_to(self.f.root).as_posix())
        self.summary = logical_summary(self.partial, self.runs, self.f.identity)
        self.bindings = [{"reviewId": rid, "contextId": "synthetic-partial-" + rid,
            "contextProducerVersion": transport.PRIOR_CONTEXT_VERSION if i < 9 else transport.CONTEXT_VERSION,
            "sourceRun": source_run(i, self.runs)} for i, rid in enumerate(tr.IDS)]
        self.provenance = transport.question_provenance(self.summary, self.bindings, self.partial)
        self.f.stack.enter_context(patch.object(runner, "load_partial_recovery", side_effect=lambda root: deepcopy(self.partial)))
        self.f.collection_receipt.update(version=transport.VERSION, runnerValidatorVersion=transport.RUNNER_VERSION,
            contextProducerVersion=transport.CONTEXT_VERSION, partialRecovery=deepcopy(self.partial),
            nativeSummary=deepcopy(self.summary), bindings=deepcopy(self.bindings),
            questionProvenance=deepcopy(self.provenance), singleQuestionRunCompletionClaimed=False)
        self.f.receipt_path.write_bytes(ev.packed(self.f.collection_receipt))
        self.validator = self.f.stack.enter_context(patch.object(local, "validate_collection", side_effect=self.f.validated))

    def freeze(self):
        return local.freeze_answers(self.f.folder, self.f.collection, self.f.root)

    def test_freeze_triple_read_and_compatible_pair_preserve_actual_run_provenance(self):
        receipt = self.freeze(); formal = local.load_formal(self.f.folder, self.f.root)
        rows, refs, provenance = local.read_frozen_answers_with_provenance(formal, self.f.root)
        pair = local.read_frozen_answers(formal, self.f.root)
        self.assertEqual(pair, (rows, refs))
        self.assertEqual(rows, self.f.answers)
        self.assertEqual(receipt["questionProvenance"], self.provenance)
        self.assertEqual(provenance, self.provenance)
        self.assertEqual([r["status"] for r in provenance["sourceRuns"]], ["failed", "completed"])
        self.assertEqual((self.f.folder / "answers-frozen.json").read_bytes(), ev.packed({"version": ev.VERSION, "rows": self.f.answers}))

    def test_changed_partial_identity_or_origin_is_rejected_before_freeze(self):
        original = deepcopy(self.f.collection_receipt)
        for kind in ("identity", "origin", "single-run", "memory-profile", "previous-v5", "memory-policy"):
            self.f.collection_receipt.clear(); self.f.collection_receipt.update(deepcopy(original))
            if kind == "identity": self.f.collection_receipt["partialRecovery"]["priorClaim"]["sha256"] = "0" * 64
            elif kind == "origin": self.f.collection_receipt["questionProvenance"]["contexts"][0]["sourceRun"]["status"] = "completed"
            elif kind == "memory-profile": self.f.collection_receipt["partialRecovery"]["profile"]["minimumStartPhysicalBytes"] = 0
            elif kind == "previous-v5": self.f.collection_receipt["partialRecovery"]["previousPartialRecovery"]["partialRecoveryFreeze"]["sha256"] = "0" * 64
            elif kind == "memory-policy": self.f.collection_receipt["partialRecovery"]["memoryPolicy"]["automaticModelRetry"] = True
            else: self.f.collection_receipt["singleQuestionRunCompletionClaimed"] = True
            self.f.receipt_path.write_bytes(ev.packed(self.f.collection_receipt))
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.freeze()
            self.assertFalse((self.f.folder / "answers-freeze.json").exists())

    def test_incomplete_answers_or_prior_failure_cannot_be_frozen(self):
        self.f.validation["rows"].pop()
        with self.assertRaisesRegex(ValueError, "all_64_ordered_answers_required"): self.freeze()
        with patch.object(runner, "load_partial_recovery", side_effect=ValueError("prior_output_changed")):
            with self.assertRaisesRegex(ValueError, "prior_output_changed"): self.freeze()
        self.assertFalse((self.f.folder / "grade-packets").exists())

    def test_changed_frozen_source_runs_are_rejected(self):
        self.freeze(); path = self.f.folder / "answers-freeze.json"; value = ev.read_json(path)
        value["questionProvenance"]["sourceRuns"][0]["status"] = "completed"
        path.write_bytes(ev.packed(value))
        with self.assertRaisesRegex(ValueError, "partial_frozen_provenance_mismatch"):
            local.read_frozen_answers(local.load_formal(self.f.folder, self.f.root), self.f.root)

    def test_report_preserves_frozen_aggregate_and_requires_real_provenance(self):
        f = self.f
        incomplete = local.report(f.folder, f.root / tr.BASE / "partial-incomplete.json", root=f.root)
        self.assertIsNone(incomplete["decision"]["selectedConfiguration"])
        self.assertIsNone(incomplete["recoveryEvidence"]["questionProvenance"])
        formal = local.load_formal(f.folder, f.root)
        with self.assertRaisesRegex(ValueError, "partial_report_question_provenance_required"):
            local.report_recovery_evidence(formal, [], f.answers)
        self.freeze()
        source, grades, output = [f.root / tr.BASE / name for name in ("partial-source.json", "partial-grades.json", "partial-report.json")]
        ev.write_new(source, {"rows": f.source}); ev.write_new(grades, {"rows": f.grades})
        report = local.report(f.folder, output, source, grades, f.root)
        self.assertEqual({k: v for k, v in report.items() if k != "recoveryEvidence"}, ev.aggregate(f.bundle, f.source, f.answers, f.grades))
        self.assertEqual(report["recoveryEvidence"]["questionProvenance"], self.provenance)
        self.assertEqual(report["recoveryEvidence"]["contextProducerVersion"], transport.CONTEXT_VERSION)
        self.assertEqual(report["recoveryEvidence"]["coordinatorVersion"], transport.RUNNER_VERSION)

    def test_actual_v6_transport_to_freeze_keeps_answer_content_and_v4_v6_join(self):
        f = self.f; contexts = []
        for i, answer in enumerate(f.answers):
            rid = answer["reviewId"]
            contexts.append({**self.bindings[i], "packetSha256": ev.sha(ev.packed(f.formal["packets"][rid])),
                "draft": {"reviewId": rid, "answers": [{**r, "translationEvidence": [q["text"] for q in r["translationEvidence"]]} for r in answer["answers"]]},
                "process": {"pid": i + 1, "creationTicks": i + 100}})
        native = {"contexts": contexts, "summary": self.summary, "evidence": [tr.reference(f.root, f.raw_path)]}
        destination = f.root / tr.BASE / "actual-partial-collection"
        with patch.object(runner, "validate_run", side_effect=lambda *a, **kw: deepcopy(native)), \
             patch.object(local, "validate_collection", side_effect=lambda directory, formalPath, root:
                 transport.validate_collection(directory, formalPath, root=root)):
            receipt = transport.collect(f.folder, self.old.prior.export, self.run, destination, f.root)
            frozen = local.freeze_answers(f.folder, destination, f.root)
            rows, _, provenance = local.read_frozen_answers_with_provenance(local.load_formal(f.folder, f.root), f.root)
        self.assertEqual((destination / "answers.json").read_bytes(), (f.folder / "answers-frozen.json").read_bytes())
        self.assertEqual([r["answers"] for r in rows], [r["answers"] for r in f.answers])
        self.assertEqual(frozen["collectionMetadata"], receipt)
        self.assertEqual(sum(c["sourceRun"]["reused"] for c in provenance["contexts"]), 9)


if __name__ == "__main__":
    unittest.main()

