"""Synthetic battery-v4 answer freeze/report tests; no actual question reads."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_evaluation_battery_v4 as local
from input_execution_v1 import local_question_transport_battery_v4 as transport
from input_execution_v1 import local_question_runner_battery_v4 as runner
from input_execution_v1 import test_local_question_evaluation_recovery_v3 as support
from input_execution_v1.test_local_question_transport_battery_v4 import synthetic_battery, actual_battery_fixture
ev, tr = local.ev, local.tr


class BatteryEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.prior = support.ZeroCallEvaluationTests("runTest")
        self.prior.setUp(); self.addCleanup(self.prior.doCleanups)
        self.f = self.prior.f
        self.battery = synthetic_battery(self.prior.zero, self.f.root)
        self.f.stack.enter_context(patch.object(runner, "load_battery", side_effect=lambda root: deepcopy(self.battery)))
        self.f.collection_receipt.update(version=transport.VERSION, runnerValidatorVersion=runner.VERSION,
            contextProducerVersion=runner.VERSION, batteryExecution=deepcopy(self.battery))
        self.f.receipt_path.write_bytes(ev.packed(self.f.collection_receipt))
        self.validator = self.f.stack.enter_context(patch.object(local, "validate_collection", side_effect=self.f.validated))

    def freeze(self):
        return local.freeze_answers(self.f.folder, self.f.collection, self.f.root)

    def test_freeze_read_preserves_verbatim_answers_and_three_lineages(self):
        manifest = (self.f.folder / "manifest.json").read_bytes()
        frozen = self.freeze()
        formal = local.load_formal(self.f.folder, self.f.root)
        answers, refs = local.read_frozen_answers(formal, self.f.root)
        self.assertEqual(answers, self.f.answers)
        self.assertEqual((self.f.folder / "answers-frozen.json").read_bytes(), ev.packed({"version": ev.VERSION, "rows": self.f.answers}))
        self.assertEqual(frozen["batteryExecution"], self.battery)
        self.assertEqual(frozen["zeroCallRecovery"], self.prior.zero)
        self.assertEqual(frozen["inventoryCorrection"], self.f.identity)
        self.assertEqual(frozen["contextProducerVersion"], runner.VERSION)
        self.assertTrue(all(r in formal["evidence"] or r in refs for r in transport.battery_refs(self.battery)))
        self.assertEqual(manifest, (self.f.folder / "manifest.json").read_bytes())

    def test_foreign_battery_collection_or_context_version_cannot_be_frozen(self):
        original = deepcopy(self.f.collection_receipt)
        for kind in ("policy", "context"):
            self.f.collection_receipt.clear(); self.f.collection_receipt.update(deepcopy(original))
            if kind == "policy": self.f.collection_receipt["batteryExecution"]["powerPolicy"]["minimumBatteryPercentExclusive"] = 0
            else: self.f.collection_receipt["contextProducerVersion"] = "old-context"
            self.f.receipt_path.write_bytes(ev.packed(self.f.collection_receipt))
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "battery_collection_lineage_mismatch"):
                self.freeze()
            self.assertFalse((self.f.folder / "answers-frozen.json").exists())

    def test_recovery_rebinding_or_battery_failure_stops_before_collection(self):
        self.battery["zeroCallRecovery"]["priorClaim"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "battery_recovery_lineage_mismatch"):
            self.freeze()
        with patch.object(runner, "load_battery", side_effect=ValueError("battery_freeze_changed")):
            with self.assertRaisesRegex(ValueError, "battery_freeze_changed"):
                self.freeze()
        self.validator.assert_not_called()

    def test_incomplete_or_extra_schema_collection_is_rejected(self):
        original = deepcopy(self.f.validation)
        for kind in ("missing", "schema"):
            self.f.validation.clear(); self.f.validation.update(deepcopy(original))
            if kind == "missing": self.f.validation["rows"].pop()
            else: self.f.validation["extra"] = True
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.freeze()
            self.assertFalse((self.f.folder / "grade-packets").exists())

    def test_frozen_context_version_or_battery_evidence_change_is_rejected(self):
        self.freeze(); path = self.f.folder / "answers-freeze.json"
        original = path.read_bytes(); value = ev.read_json(path)
        value["contextProducerVersion"] = "old-version"; path.write_bytes(ev.packed(value))
        with self.assertRaisesRegex(ValueError, "freeze_contract"):
            local.read_frozen_answers(local.load_formal(self.f.folder, self.f.root), self.f.root)
        path.write_bytes(original)
        artifact = self.f.root / self.battery["batteryFreeze"]["path"]
        artifact.write_bytes(artifact.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            local.load_formal(self.f.folder, self.f.root)

    def test_report_reuses_frozen_aggregate_and_incomplete_stays_unselected(self):
        f = self.f
        incomplete = local.report(f.folder, f.root / tr.BASE / "incomplete-battery.json", root=f.root)
        self.assertIsNone(incomplete["decision"]["selectedConfiguration"])
        self.assertEqual(incomplete["recoveryEvidence"]["questionAnswering"]["validatedContexts"], 0)
        self.freeze()
        source, grades, output = [f.root / tr.BASE / name for name in ("battery-source.json", "battery-grades.json", "battery-report.json")]
        ev.write_new(source, {"rows": f.source}); ev.write_new(grades, {"rows": f.grades})
        result = local.report(f.folder, output, source, grades, f.root)
        self.assertEqual({k: v for k, v in result.items() if k != "recoveryEvidence"}, ev.aggregate(f.bundle, f.source, f.answers, f.grades))
        self.assertEqual(result["recoveryEvidence"]["batteryExecution"], self.battery)
        self.assertEqual(result["recoveryEvidence"]["contextProducerVersion"], runner.VERSION)

    def test_actual_v4_transport_freeze_read_keeps_answer_bytes(self):
        f = self.f; contexts = []
        for i, answer in enumerate(f.answers):
            rid = answer["reviewId"]
            draft = {"reviewId": rid, "answers": [{**row, "translationEvidence": [r["text"] for r in row["translationEvidence"]]} for row in answer["answers"]]}
            contexts.append({"reviewId": rid, "contextId": "synthetic-battery-" + rid,
                "contextProducerVersion": runner.VERSION, "packetSha256": ev.sha(ev.packed(f.formal["packets"][rid])),
                "draft": draft, "process": {"pid": i + 1, "creationTicks": i + 100}})
        native = {"contexts": contexts, "summary": {"version": runner.VERSION, "contextProducerVersion": runner.VERSION,
            "inventoryCorrection": f.identity, "zeroCallRecovery": self.prior.zero, "batteryExecution": self.battery},
            "evidence": [tr.reference(f.root, f.raw_path)]}
        destination = f.root / tr.BASE / "actual-battery-collection"
        with patch.object(runner, "validate_run", side_effect=lambda *a, **kw: deepcopy(native)), \
             patch.object(local, "validate_collection", side_effect=lambda directory, formalPath, root:
                transport.validate_collection(directory, formalPath, root=root)):
            receipt = transport.collect(f.folder, self.prior.export, f.root / tr.BASE / "synthetic-run", destination, f.root)
            frozen = local.freeze_answers(f.folder, destination, f.root)
            rows, _ = local.read_frozen_answers(local.load_formal(f.folder, f.root), f.root)
        self.assertEqual((destination / "answers.json").read_bytes(), (f.folder / "answers-frozen.json").read_bytes())
        self.assertEqual([row["answers"] for row in rows], [row["answers"] for row in f.answers])
        self.assertEqual(frozen["collectionMetadata"], receipt)


class ActualBatteryLoaderBoundaryTests(unittest.TestCase):
    def test_actual_battery_loader_binds_formal_then_blocks_changed_code(self):
        with tempfile.TemporaryDirectory(prefix="evaluation-battery-loader-") as temporary:
            root = Path(temporary).resolve()
            with actual_battery_fixture(root) as identity:
                folder = root / tr.BASE / "synthetic-formal"
                value = {"folder": folder, "evidence": [], "zeroCallRecovery": identity["zeroCallRecovery"]}
                baseline = SimpleNamespace(load_formal=lambda *args: deepcopy(value))
                with patch.object(local, "baseline", baseline):
                    formal = local.load_formal(folder, root)
                    self.assertEqual(formal["batteryExecution"], identity)
                    self.assertEqual(formal["evidence"], transport.battery_refs(identity))
                    self.assertNotIn("batteryExecution", value)
                    self.assertFalse((root / identity["zeroCallRecovery"]["priorRun"]["exportManifest"]["path"]).exists())
                    (root / identity["files"][0]["path"]).write_bytes(b"changed synthetic code")
                    with patch.object(local, "validate_collection") as collection:
                        with self.assertRaisesRegex(ValueError, "evidence_hash_changed"):
                            local.freeze_answers(folder, root / tr.BASE / "unused-collection", root)
                    collection.assert_not_called()
                    self.assertFalse(folder.exists())


if __name__ == "__main__":
    unittest.main()
