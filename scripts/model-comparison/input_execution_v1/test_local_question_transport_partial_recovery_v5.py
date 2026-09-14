"""Focused logical-9+55 transport checks over synthetic native/prior boundaries."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import local_question_transport_partial_recovery_v5 as t
from input_execution_v1 import local_question_runner_partial_recovery_v5 as runner
from input_execution_v1 import test_local_question_transport_battery_v4 as support


def synthetic_partial(battery, root=None, new_run=None):
    def artifact(path):
        if root is None: return {"path": path, "sha256": "b" * 64}
        t.ev.write_new(Path(root) / path, {"syntheticOnly": True})
        return t.tr.reference(root, Path(root) / path)
    base = t.tr.BASE + "/synthetic-partial-recovery"
    previous = base + "/prior-v4"
    new_run = new_run or base + "/new-v5"
    ids = list(t.contract.IDS)
    partial = {"version": "input-execution-v1-local-question-partial-recovery-freeze-v5",
        "batteryExecution": deepcopy(battery),
        "priorRun": {"path": previous, "status": "failed", "expectedContexts": 64,
            "completedContexts": 9, "freshNativeProcesses": 9, "completionRequestsSent": 9,
            "completedReviewIds": ids[:9], "summary": artifact(previous + "/summary.json"),
            "plan": artifact(previous + "/plan.json"), "artifactInventorySha256": "a" * 64,
            "failedContext": {"reviewId": "R010", "completionRequestsSent": 0, "freshNativeProcesses": 0,
                "files": [artifact(previous + "/R010/evidence-" + str(i) + ".json") for i in range(4)],
                "nativeStoppedMeaning": "no_process_was_created"}},
        "priorClaim": artifact(base + "/prior-claim.json"), "priorSupersession": artifact(base + "/prior-supersession.json"),
        "priorReview": artifact(base + "/prior-review.json"),
        "newRunPath": new_run, "newClaimPath": base + "/new-claim.json",
        "expectedReviewIds": ids, "retainedReviewIds": ids[:9], "pendingReviewIds": ids[9:],
        "waitPolicy": deepcopy(runner.WAIT_POLICY), "partialRecoveryFreeze": artifact(base + "/freeze.json"),
        "files": [artifact(base + "/code-" + str(i) + ".json") for i in range(9)]}
    source_runs = []
    for path, version, status, expected, completed in ((previous, t.CONTEXT_VERSION, "failed", 64, 9),
            (new_run, t.RUNNER_VERSION, "completed", 55, 55)):
        summary_ref = partial["priorRun"]["summary"] if status == "failed" else artifact(path + "/summary.json")
        source_runs.append({"runPath": path, "version": version, "status": status, "expectedContexts": expected,
            "completedContexts": completed, "freshNativeProcesses": completed, "completionRequestsSent": completed,
            "summary": summary_ref, "powerRequest": {"requested": True, "released": True, "syntheticOnly": True}})
    return partial, source_runs


def logical_summary(partial, runs, inventory):
    return {"version": t.SUMMARY_VERSION, "status": "completed_logical_cohort", "contextProducerVersion": t.CONTEXT_VERSION,
        "partialRecovery": deepcopy(partial), "batteryExecution": deepcopy(partial["batteryExecution"]),
        "zeroCallRecovery": deepcopy(partial["batteryExecution"]["zeroCallRecovery"]), "inventoryCorrection": deepcopy(inventory),
        "expectedContexts": 64, "completedContexts": 64, "freshNativeProcesses": 64, "completionRequestsSent": 64,
        "retainedContexts": 9, "freshContexts": 55, "sourceRuns": deepcopy(runs)}


def source_run(index, runs):
    run = runs[0 if index < 9 else 1]
    return {"runPath": run["runPath"], "coordinatorVersion": run["version"], "status": run["status"], "reused": index < 9}


class PartialTransportTests(unittest.TestCase):
    def setUp(self):
        self.old = support.BatteryTransportTests("runTest")
        self.old.setUp(); self.addCleanup(self.old.doCleanups)
        self.f = self.old.f
        self.partial, self.runs = synthetic_partial(self.old.battery, self.f.root, self.f.run.relative_to(self.f.root).as_posix())
        self.native = deepcopy(self.old.native)
        self.native["summary"] = logical_summary(self.partial, self.runs, self.f.identity)
        for index, context in enumerate(self.native["contexts"]):
            context["sourceRun"] = source_run(index, self.runs)
        self.f.stack.enter_context(patch.object(runner, "load_partial_recovery", side_effect=lambda root: deepcopy(self.partial)))
        self.validator = self.f.stack.enter_context(patch.object(runner, "validate_run", side_effect=lambda *a, **kw: deepcopy(self.native)))

    def collect(self):
        return t.collect(self.f.formal_path, self.f.export, self.f.run, self.f.dest, self.f.root)

    def test_logical_roundtrip_preserves_failed9_completed55_and_v4_contexts(self):
        paths = [*self.f.export.iterdir(), *[self.f.root / ref["path"] for ref in t.partial_recovery_refs(self.partial)]]
        before = {path: path.read_bytes() for path in paths}
        receipt = self.collect(); result = t.validate_collection(self.f.dest, self.f.formal_path, self.f.root)
        self.assertEqual(set(result), {"rows", "evidence", "receipt"})
        self.assertEqual(len(result["rows"]), 64)
        self.assertEqual(receipt["runnerValidatorVersion"], t.RUNNER_VERSION)
        self.assertEqual(receipt["contextProducerVersion"], t.CONTEXT_VERSION)
        self.assertFalse(receipt["singleQuestionRunCompletionClaimed"])
        provenance = receipt["questionProvenance"]
        self.assertEqual([r["status"] for r in provenance["sourceRuns"]], ["failed", "completed"])
        self.assertEqual([r["completionRequestsSent"] for r in provenance["sourceRuns"]], [9, 55])
        self.assertEqual(sum(row["sourceRun"]["reused"] for row in provenance["contexts"]), 9)
        self.assertTrue(all(row["contextProducerVersion"] == t.CONTEXT_VERSION for row in provenance["contexts"]))
        self.assertEqual(before, {path: path.read_bytes() for path in paths})
        self.old.validator.assert_not_called()

    def test_partial_prior_failure_or_battery_rebinding_stops_before_native(self):
        with patch.object(runner, "load_partial_recovery", side_effect=ValueError("prior_nine_not_valid")):
            with self.assertRaisesRegex(ValueError, "prior_nine_not_valid"): self.collect()
        self.partial["batteryExecution"]["userAuthorization"]["acOverride"] = False
        with self.assertRaisesRegex(ValueError, "partial_battery_lineage_mismatch"): self.collect()
        self.validator.assert_not_called(); self.assertFalse(self.f.dest.exists())

    def test_actual_run_summary_cannot_masquerade_as_logical64(self):
        self.native["summary"]["version"] = t.RUNNER_VERSION
        with self.assertRaisesRegex(ValueError, "inventory_runner_lineage_mismatch"): self.collect()
        self.native["summary"]["version"] = t.SUMMARY_VERSION
        self.native["summary"]["freshContexts"] = 64
        with self.assertRaisesRegex(ValueError, "partial_logical_cohort_counts"): self.collect()

    def test_prior_status_counts_or_context_origin_cannot_change(self):
        original = deepcopy(self.native)
        for kind in ("prior-status", "new-count", "context-version", "context-origin"):
            self.native = deepcopy(original)
            if kind == "prior-status": self.native["summary"]["sourceRuns"][0]["status"] = "completed"
            elif kind == "new-count": self.native["summary"]["sourceRuns"][1]["completionRequestsSent"] = 64
            elif kind == "context-version": self.native["contexts"][9]["contextProducerVersion"] = t.RUNNER_VERSION
            else: self.native["contexts"][9]["sourceRun"] = source_run(0, self.runs)
            with self.subTest(kind=kind), self.assertRaises(ValueError): self.collect()
            self.assertFalse(self.f.dest.exists())

    def test_missing_or_duplicated_context_cannot_form_full_collection(self):
        original = deepcopy(self.native["contexts"])
        self.native["contexts"].pop()
        with self.assertRaisesRegex(ValueError, "all_64_fresh_native_contexts_required"): self.collect()
        self.native["contexts"] = deepcopy(original)
        self.native["contexts"][9]["contextId"] = original[0]["contextId"]
        with self.assertRaisesRegex(ValueError, "all_64_fresh_native_contexts_required"): self.collect()

    def test_changed_source_summary_or_collected_provenance_is_rejected(self):
        path = self.f.root / self.runs[1]["summary"]["path"]; raw = path.read_bytes()
        path.write_bytes(raw + b"changed")
        with self.assertRaisesRegex(ValueError, "evidence_changed"): self.collect()
        path.write_bytes(raw); self.collect()
        path = self.f.dest / "receipt.json"; value = t.ev.read_json(path)
        value["questionProvenance"]["contexts"][0]["sourceRun"]["reused"] = False
        path.write_bytes(t.ev.packed(value))
        with self.assertRaisesRegex(ValueError, "collection_replay_changed"):
            t.validate_collection(self.f.dest, self.f.formal_path, self.f.root)


class ActualPartialLoaderBoundaryTests(unittest.TestCase):
    def test_actual_partial_freeze_loader_blocks_code_change_before_native(self):
        with tempfile.TemporaryDirectory(prefix="partial-loader-boundary-") as temporary:
            root = Path(temporary).resolve()
            with support.actual_battery_fixture(root) as battery:
                partial, _ = synthetic_partial(battery, root)
                expected = {key: value for key, value in partial.items() if key not in ("version", "partialRecoveryFreeze", "files")}
                files = []
                for name in sorted(runner.REQUIRED_PARTIAL_FILES):
                    path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"synthetic partial recovery code")
                    files.append(t.tr.reference(root, path))
                t.ev.write_new(root / runner.PARTIAL_FREEZE, {"version": runner.PARTIAL_VERSION, **expected,
                    "actualNewQuestionCallsAtFreeze": 0, "files": files})
                # The actual closed prior-9/native replay is the mocked boundary;
                # exact v5 freeze schema, file inventory and hashes remain real.
                with patch.object(runner, "partial_freeze_inputs", return_value=deepcopy(expected)):
                    actual = t.load_partial_recovery(root)
                    self.assertEqual(actual["batteryExecution"], battery)
                    self.assertEqual(len(actual["files"]), 9)
                    self.assertEqual(actual["priorRun"]["completedContexts"], 9)
                    export_ref = battery["zeroCallRecovery"]["priorRun"]["exportManifest"]
                    self.assertFalse((root / export_ref["path"]).exists())
                    (root / files[0]["path"]).write_bytes(b"changed code")
                    with patch.object(t, "verify_export", return_value=({}, [export_ref])), \
                         patch.object(t, "load_correction", return_value={}), \
                         patch.object(runner, "validate_run") as native:
                        with self.assertRaisesRegex(ValueError, "evidence_hash_changed"):
                            t.contents({}, root / "unused-export", root / expected["newRunPath"], root)
                    native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
