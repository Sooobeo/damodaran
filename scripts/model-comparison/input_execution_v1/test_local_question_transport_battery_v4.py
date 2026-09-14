"""Focused battery pipeline tests; temporary synthetic artifacts, no native calls."""
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_transport_battery_v4 as t
from input_execution_v1 import local_question_runner_battery_v4 as runner
from input_execution_v1 import test_local_question_transport_recovery_v3 as support
from input_execution_v1.test_local_question_runner_recovery_v3 import recovery_fixture


def synthetic_battery(zero, root=None):
    """Schema-shaped synthetic lineage; the runner alone validates real approval."""
    def artifact(name):
        path = t.tr.BASE + "/synthetic-battery-lineage/" + name
        if root is None:
            return {"path": path, "sha256": "b" * 64}
        t.ev.write_new(Path(root) / path, {"syntheticOnly": True})
        return t.tr.reference(root, Path(root) / path)
    return {"version": "input-execution-v1-local-question-battery-execution-freeze-v4",
        "zeroCallRecovery": deepcopy(zero),
        "userAuthorization": {"source": "user-current-session",
            "instruction": "충전기 연결은 안 됐는데 배터리 잔량은 충분함. 진행해봐", "acOverride": True},
        "powerPolicy": {"allowedACLineStatus": [0, 1], "minimumBatteryPercentExclusive": 20,
            "unknownBatteryAction": "abort", "batterySource": "GetSystemPowerStatus.BatteryLifePercent"},
        "batteryFreeze": artifact("freeze.json"), "files": [artifact(str(i) + ".json") for i in range(9)]}


class BatteryTransportTests(unittest.TestCase):
    def setUp(self):
        self.prior = support.ZeroCallTransportTests("runTest")
        self.prior.setUp(); self.addCleanup(self.prior.doCleanups)
        self.f = self.prior.f
        self.battery = synthetic_battery(self.prior.zero, self.f.root)
        self.native = deepcopy(self.prior.native)
        self.native["summary"].update(version=runner.VERSION, contextProducerVersion=runner.VERSION,
            batteryExecution=deepcopy(self.battery))
        for row in self.native["contexts"]:
            row["contextProducerVersion"] = runner.VERSION
        self.f.stack.enter_context(patch.object(runner, "load_battery", side_effect=lambda root: deepcopy(self.battery)))
        self.validator = self.f.stack.enter_context(patch.object(runner, "validate_run", side_effect=lambda *a, **kw: deepcopy(self.native)))

    def collect(self):
        return t.collect(self.f.formal_path, self.f.export, self.f.run, self.f.dest, self.f.root)

    def test_roundtrip_preserves_bytes_and_three_lineages_with_actual_context_version(self):
        paths = [*self.f.export.iterdir(), *[self.f.root / r["path"] for r in t.battery_refs(self.battery)]]
        before = {p: p.read_bytes() for p in paths}
        receipt = self.collect()
        result = t.validate_collection(self.f.dest, self.f.formal_path, self.f.root)
        self.assertEqual(set(result), {"rows", "evidence", "receipt"})
        self.assertEqual(len(result["rows"]), 64)
        self.assertEqual(receipt["batteryExecution"], self.battery)
        self.assertEqual(receipt["zeroCallRecovery"], self.prior.zero)
        self.assertEqual(receipt["inventoryCorrection"], self.f.identity)
        self.assertEqual(receipt["contextProducerVersion"], runner.VERSION)
        self.assertTrue(all(r["contextProducerVersion"] == runner.VERSION for r in receipt["bindings"]))
        self.assertTrue(all(r in result["evidence"] for r in t.battery_refs(self.battery)))
        self.assertEqual(before, {p: p.read_bytes() for p in paths})
        self.assertEqual(self.validator.call_count, 2)
        self.prior.native_validator.assert_not_called()

    def test_battery_loader_failure_blocks_native_and_writes(self):
        with patch.object(runner, "load_battery", side_effect=ValueError("battery_authorization_not_bound")):
            with self.assertRaisesRegex(ValueError, "battery_authorization_not_bound"):
                self.collect()
        self.validator.assert_not_called(); self.assertFalse(self.f.dest.exists())

    def test_battery_cannot_rebind_zero_call_history(self):
        self.battery["zeroCallRecovery"]["priorRun"]["completionRequestsSent"] = 1
        with self.assertRaisesRegex(ValueError, "battery_recovery_lineage_mismatch"):
            self.collect()
        self.validator.assert_not_called(); self.assertFalse(self.f.dest.exists())

    def test_summary_policy_authorization_or_freeze_fraud_is_rejected(self):
        original = deepcopy(self.native["summary"])
        for key, field, value in (("powerPolicy", "minimumBatteryPercentExclusive", 0),
                ("userAuthorization", "acOverride", False), ("batteryFreeze", "sha256", "0" * 64)):
            self.native["summary"] = deepcopy(original)
            self.native["summary"]["batteryExecution"][key][field] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "battery_runner_lineage_mismatch"):
                self.collect()
            self.assertFalse(self.f.dest.exists())

    def test_old_summary_or_mixed_context_producer_is_rejected(self):
        self.native["summary"]["contextProducerVersion"] = "old-context-version"
        with self.assertRaisesRegex(ValueError, "battery_runner_lineage_mismatch"):
            self.collect()
        self.native["summary"]["contextProducerVersion"] = runner.VERSION
        self.native["contexts"][32]["contextProducerVersion"] = "old-context-version"
        with self.assertRaisesRegex(ValueError, "battery_context_producer_mismatch"):
            self.collect()
        self.assertFalse(self.f.dest.exists())

    def test_incomplete_duplicate_or_wrong_review_id_cannot_be_collected(self):
        original = deepcopy(self.native["contexts"])
        for kind in ("missing", "duplicate", "wrong-draft"):
            self.native["contexts"] = deepcopy(original)
            if kind == "missing": self.native["contexts"].pop()
            elif kind == "duplicate": self.native["contexts"][1]["contextId"] = self.native["contexts"][0]["contextId"]
            else: self.native["contexts"][0]["draft"]["reviewId"] = "R002"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.collect()
            self.assertFalse(self.f.dest.exists())

    def test_changed_battery_evidence_or_receipt_is_rejected(self):
        path = self.f.root / self.battery["batteryFreeze"]["path"]
        raw = path.read_bytes(); path.write_bytes(raw + b"changed")
        with self.assertRaisesRegex(ValueError, "evidence_changed"):
            self.collect()
        path.write_bytes(raw); self.collect()
        receipt = self.f.dest / "receipt.json"; value = t.ev.read_json(receipt)
        value["bindings"][0]["contextProducerVersion"] = "old-version"
        receipt.write_bytes(t.ev.packed(value))
        with self.assertRaisesRegex(ValueError, "collection_replay_changed"):
            t.validate_collection(self.f.dest, self.f.formal_path, self.f.root)


@contextmanager
def actual_battery_fixture(root):
    """Real v3/v4 lineage loaders over synthetic temp files; no packet exists."""
    with recovery_fixture(root) as prior:
        refs = []
        for name in sorted(runner.REQUIRED_BATTERY_FILES):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic battery execution code")
            refs.append(t.tr.reference(root, path))
        payload = {"version": runner.BATTERY_VERSION, "zeroCallRecovery": prior,
            "userAuthorization": runner.USER_AUTHORIZATION, "powerPolicy": runner.POWER_POLICY,
            "actualQuestionCallsAtFreeze": 0, "files": refs}
        t.ev.write_new(root / runner.BATTERY_FREEZE, payload)
        with patch.object(runner, "PRIOR_RECOVERY_FREEZE_SHA", prior["recoveryFreeze"]["sha256"]):
            yield runner.load_battery(root)


class ActualBatteryLoaderBoundaryTests(unittest.TestCase):
    def test_actual_battery_loader_rejects_altered_approval_before_native(self):
        with tempfile.TemporaryDirectory(prefix="transport-battery-loader-") as temporary:
            root = Path(temporary).resolve()
            with actual_battery_fixture(root) as identity:
                self.assertEqual(t.load_battery(root), identity)
                self.assertEqual(len(t.battery_refs(identity)), 27)
                export_ref = identity["zeroCallRecovery"]["priorRun"]["exportManifest"]
                self.assertFalse((root / export_ref["path"]).exists())
                path = root / runner.BATTERY_FREEZE; value = t.ev.read_json(path)
                value["userAuthorization"]["acOverride"] = False
                path.write_bytes(t.ev.packed(value))
                with patch.object(t, "verify_export", return_value=({}, [export_ref])), \
                     patch.object(t, "load_correction", return_value={}), \
                     patch.object(runner, "validate_run") as native:
                    with self.assertRaisesRegex(ValueError, "battery_freeze_binding"):
                        t.contents({}, root / "unused-export", root / "unused-run", root)
                native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
