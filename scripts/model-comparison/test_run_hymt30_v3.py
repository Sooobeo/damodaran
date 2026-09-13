"""Paging policy and protocol tests; no network or model process is started."""
from pathlib import Path
import copy
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import Mock, patch

import run_hymt30_v2 as previous
import run_hymt30_v3 as runner


class Hy30V3Tests(unittest.TestCase):
    def test_preserved_v2_and_frozen_helpers(self):
        self.assertEqual(runner.common.digest(previous.__file__),
                         "8c86655908f402358a6d28aa90c4848864d2a73a94cecff796d779653ed41474")
        runner.common.check_identity_unchanged({str(p): sha for p, sha in runner.FROZEN.items()})

    def test_generation_contract_unchanged(self):
        self.assertEqual(runner.server_command(43123, "fixture"), previous.server_command(43123, "fixture"))
        for name in ("SAMPLING", "TEMPLATE_SHA", "TOKEN_STRINGS", "CONTEXT_SIZE", "MAX_NEW_TOKENS", "EOG_IDS"):
            self.assertEqual(getattr(runner, name), getattr(previous, name))
        self.assertEqual(runner.VERSION, "hymt30-development-screen-v3")

    def test_each_explicit_memory_budget_boundary(self):
        for budget in (8, 9, 10, 11, 12):
            physical, commit = (budget + 3) * runner.GIB, 3 * runner.GIB
            policy = runner.preflight(physical, commit, budget)
            self.assertTrue(policy["passed"])
            self.assertEqual(policy["f16KvBudgetBytes"], 805306368)
            self.assertEqual(policy["maximumWorkingSetBytes"], budget * runner.GIB)
            self.assertEqual(policy["requestedHardMaximumWorkingSetBytes"], budget * runner.GIB - 64 * 1024**2)
            self.assertFalse(runner.preflight(physical - 1, commit, budget)["passed"])
            self.assertFalse(runner.preflight(physical, commit - 1, budget)["passed"])
            self.assertFalse(policy["guaranteesNoOutOfMemory"])
        for budget in (True, 0, 7, 13, 10.0):
            with self.assertRaisesRegex(ValueError, "invalid_ram_budget"):
                runner.preflight(100 * runner.GIB, 100 * runner.GIB, budget)

    def test_pressure_timers_are_independent(self):
        guard = runner.MemoryGuard()
        high = runner.GIB
        for state, now in (({"availablePhysical": 1, "availablePageFile": high}, 0),
                           ({"availablePhysical": high, "availablePageFile": 1}, 2),
                           ({"availablePhysical": 1, "availablePageFile": high}, 4)):
            self.assertIsNone(guard.observe(state, now))
        self.assertEqual(guard.observe({"availablePhysical": 1, "availablePageFile": high}, 7), "physical")

    def test_smoke_is_source_only_and_not_development_rows(self):
        rows = runner.smoke_rows()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(set(row), {"id", "source", "context", "domain", "sourceSha256", "contextSha256"})
            self.assertTrue(row["id"].startswith("HY30-SMOKE-"))
            self.assertEqual(row["sourceSha256"], runner.common.sha_text(row["source"]))
            self.assertEqual(row["context"], "")

    def test_raw_output_and_integrity_rules_unchanged(self):
        row = runner.smoke_rows()[0]
        response = {"content": "  원문에 없는 숫자 900.\n", "tokens": [45, 120025], "tokens_predicted": 2,
                    "stop_type": "eos", "truncated": False, "generation_settings": copy.deepcopy(runner.SAMPLING)}
        actual = runner.prediction(row, "fixture", [], [1], response, 1)
        self.assertEqual(actual, previous.prediction(row, "fixture", [], [1], response, 1))
        self.assertEqual(actual["translation"], response["content"])
        self.assertFalse(actual["automaticChecksPassed"])
        self.assertTrue(actual["outputIntegrityPassed"])

    def test_cleanup_false_changes_successful_run_to_failure(self):
        import json
        root = runner.common.COMPARISONS.resolve()
        temporary = tempfile.TemporaryDirectory(prefix="hy30-cleanup-fixture-", dir=root)
        directory = Path(temporary.name).resolve()
        self.assertTrue(directory.is_relative_to(root) and directory != root)
        try:
            output = directory / "prepared"
            args = Namespace(output=output, input=None, run=False, smoke=False, ram_budget_gib=8)
            with patch.object(runner.common, "stop_process", return_value=False) as stopped:
                code = runner.run(args)
            stopped.assert_called_once_with(None)
            summary = json.loads((output / "summary.json").read_text("utf-8"))
            self.assertEqual(code, 1)
            self.assertEqual(summary["status"], "failed")
            self.assertFalse(summary["childProcessStopped"])
            self.assertEqual(summary["cleanupError"], "owned_child_did_not_stop")
        finally:
            self.assertTrue(directory.is_relative_to(root) and directory != root)
            temporary.cleanup()


class Hy30MonitorTests(unittest.TestCase):
    def setUp(self):
        self.root = (runner.common.ROOT / ".training/verifications").resolve()
        self.temp = tempfile.TemporaryDirectory(prefix="hy30-monitor-fixture-", dir=self.root)
        self.directory = Path(self.temp.name).resolve()
        self.assertTrue(self.directory.is_relative_to(self.root) and self.directory != self.root)
        self.process = Mock()
        self.process.poll.return_value = None
        self.monitor = runner.MemoryMonitor(self.process, Mock(), self.directory / "samples.jsonl", 0, 0,
                                            maximum_working_set=10 * runner.GIB)

    def tearDown(self):
        self.monitor.close()
        self.assertTrue(self.directory.is_relative_to(self.root) and self.directory != self.root)
        self.temp.cleanup()

    def state(self, **changes):
        child = {"workingSetBytes": 10 * runner.GIB, "peakWorkingSetBytes": 10 * runner.GIB,
                 "privateBytes": runner.GIB, "pageFaultCount": 100}
        child.update(changes)
        return {"availablePhysical": runner.GIB, "availablePageFile": runner.GIB, "child": child}

    def test_exact_budget_allowed_and_current_overage_kills_own_child(self):
        self.monitor.observe(self.state(), 1)
        self.process.kill.assert_not_called()
        self.monitor.observe(self.state(workingSetBytes=10 * runner.GIB + 4096), 2)
        self.assertEqual(self.monitor.abort_reason, "working_set_budget_exceeded")
        self.process.kill.assert_called_once_with()

    def test_historical_peak_overage_fails_even_current_below(self):
        self.monitor.observe(self.state(workingSetBytes=runner.GIB, peakWorkingSetBytes=10 * runner.GIB + 4096), 1)
        self.assertEqual(self.monitor.abort_reason, "peak_working_set_budget_exceeded")
        self.assertEqual(self.monitor.receipt()["maximumChildPeakWorkingSet"], 10 * runner.GIB + 4096)
        self.process.kill.assert_called_once_with()

    def test_startup_deadline_enforced(self):
        self.monitor.observe(self.state(), runner.STARTUP_SECONDS)
        self.assertEqual(self.monitor.abort_reason, "startup_time_limit")
        self.process.kill.assert_called_once_with()

    def test_request_deadline_and_total_limit_are_separate(self):
        self.monitor.ready()
        with patch.object(runner.time, "monotonic", return_value=20):
            self.monitor.begin_request()
        self.monitor.observe(self.state(), 20 + runner.REQUEST_SECONDS)
        self.assertEqual(self.monitor.abort_reason, "request_time_limit")
        self.monitor.end_request()
        self.assertIsNone(self.monitor.request_deadline)

    def test_total_deadline_remains_after_ready(self):
        self.monitor.ready()
        self.monitor.observe(self.state(), runner.TOTAL_SECONDS)
        self.assertEqual(self.monitor.abort_reason, "total_time_limit")

    def test_pressure_kills_only_after_three_seconds(self):
        self.monitor.ready()
        low = self.state() | {"availablePageFile": 1}
        self.monitor.observe(low, 1)
        self.monitor.observe(low, 3.99)
        self.process.kill.assert_not_called()
        self.monitor.observe(low, 4)
        self.assertEqual(self.monitor.abort_reason, "commit")
        self.process.kill.assert_called_once_with()

    def test_monitor_error_stops_child(self):
        self.monitor.stop_event = Mock()
        self.monitor.stop_event.wait.return_value = False
        with patch.object(self.monitor, "sample", side_effect=RuntimeError("synthetic")):
            self.monitor._run()
        self.assertEqual(self.monitor.abort_reason, "memory_monitor_failed")
        self.assertEqual(self.monitor.monitor_error, "RuntimeError")
        self.process.kill.assert_called_once_with()

    def test_background_budget_abort_keeps_original_cause(self):
        self.monitor.stop_event = Mock()
        self.monitor.stop_event.wait.return_value = False
        with patch.object(self.monitor, "sample", return_value=self.state(peakWorkingSetBytes=10 * runner.GIB + 4096)), \
             patch.object(runner.time, "monotonic", return_value=10):
            self.monitor._run()
        self.assertEqual(self.monitor.abort_reason, "peak_working_set_budget_exceeded")
        self.assertIsNone(self.monitor.monitor_error)
        self.process.kill.assert_called_once_with()

    def test_invalid_sample_does_not_become_valid_extrema(self):
        with self.assertRaisesRegex(ValueError, "invalid_child_memory_sample"):
            self.monitor.observe(self.state(peakWorkingSetBytes=-1), 1)

    def test_direct_final_sample_checks_historical_peak_before_completion(self):
        self.monitor.ready()
        with patch.object(self.monitor, "sample", return_value=self.state(peakWorkingSetBytes=10 * runner.GIB + 4096)), \
             patch.object(runner.time, "monotonic", return_value=10):
            with self.assertRaisesRegex(ValueError, "peak_working_set_budget_exceeded"):
                self.monitor.sample_and_check()
        self.process.kill.assert_called_once_with()
        self.assertEqual(self.monitor.receipt()["maximumChildPeakWorkingSet"], 10 * runner.GIB + 4096)

    def test_direct_allowed_sample_is_recorded_and_returned(self):
        self.monitor.ready()
        state = self.state()
        with patch.object(self.monitor, "sample", return_value=state), \
             patch.object(runner.time, "monotonic", return_value=10):
            self.assertEqual(self.monitor.sample_and_check(), state)
        self.assertEqual(self.monitor.observations, 1)
        self.process.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
