"""Guard policy/lifetime failure tests; mocks only, no native model starts."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import resource_guard as g


def system(physical=12 * g.GIB, commit=4 * g.GIB, ac=1):
    return {"availablePhysicalBytes": physical, "availableCommitBytes": commit, "ACLineStatus": ac}


def processes(conflicts=None):
    return {"nativeConflicts": conflicts or [], "classifiedConflicts": [], "querySucceeded": True}


def state(**kwargs):
    return {**system(**kwargs), "child": {"workingSetBytes": 1024, "peakWorkingSetBytes": 2048,
             "privateBytes": 512, "pageFaultCount": 4}}


def owned():
    return Mock(assert_owned=Mock(), _api=Mock(assert_child=Mock()), _handle=123)


class PolicyTests(unittest.TestCase):
    def test_hy7_profile_is_fixed(self):
        self.assertEqual(g.PROFILE["minimumAvailablePhysicalBytes"], 11811160064)
        self.assertEqual(g.PROFILE["minimumAvailableCommitBytes"], 3221225472)
        self.assertEqual(g.PROFILE["requestedNativeMaximumWorkingSetBytes"], 8522825728)
        self.assertEqual(g.PROFILE["maximumObservedNativeWorkingSetBytes"], 8589934592)
        self.assertEqual((g.PROFILE["startupSeconds"], g.PROFILE["requestSeconds"],
                          g.PROFILE["totalSeconds"]), (240, 1800, 28800))
        self.assertFalse(g.PROFILE["aggregateRamCap"])

    def test_start_boundaries_pass_and_one_byte_below_fails(self):
        with patch.object(g, "process_state", return_value=processes()):
            for physical, commit, passed in ((11*g.GIB, 3*g.GIB, True),
                    (11*g.GIB-1, 3*g.GIB, False), (11*g.GIB, 3*g.GIB-1, False)):
                with self.subTest(physical=physical, commit=commit), patch.object(
                        g, "system_state", return_value=system(physical, commit)):
                    self.assertEqual(g.preflight()["passed"], passed)

    def test_ac_unknown_battery_and_native_conflicts_refused(self):
        for ac in (0, 255):
            with patch.object(g, "process_state", return_value=processes()), patch.object(
                    g, "system_state", return_value=system(ac=ac)):
                self.assertEqual(g.preflight()["failures"], ["ac_power_required"])
        with patch.object(g, "process_state", return_value=processes([{ "pid": 4 }])), patch.object(
                g, "system_state", return_value=system()):
            self.assertEqual(g.preflight()["failures"], ["model_or_worker_conflict"])

    def test_failed_query_is_not_empty_success(self):
        with patch.object(g, "process_state", side_effect=g.ResourceGuardError("cim_process_query_failed")), \
                patch.object(g, "system_state", return_value=system()):
            with self.assertRaisesRegex(g.ResourceGuardError, "cim_process_query_failed"):
                g.preflight()

    def test_pair_does_not_reuse_old_success(self):
        with patch.object(g, "preflight", side_effect=[{"passed": True}, {"passed": False}]) as probe, \
                patch.object(g.time, "sleep") as sleep:
            result = g.preflight_pair()
            self.assertFalse(result["passed"])
            self.assertEqual(probe.call_count, 2)
            sleep.assert_called_once_with(3)

    def test_classifier_excludes_only_explicit_self_owned_pids(self):
        command = r'python C:\Users\Insun\damodaran\scripts\local-hymt\bridge.py'
        rows = [{"pid": 101, "name": "python.exe", "command": command},
                {"pid": 102, "name": "llama-server.exe", "command": None}]
        result = g.classify_process_rows(rows, [101])
        self.assertEqual([r["pid"] for r in result["classifiedConflicts"]], [102])
        self.assertNotIn(command, json.dumps(result))
        self.assertNotIn("command", result["classifiedConflicts"][0])

    def test_known_worker_relative_path_and_models_detected(self):
        commands = [r'node.exe node_modules\tsx\dist\cli.mjs worker/index.ts',
                    r'python.exe scripts/model-training/train_v5.py --run',
                    r'python.exe scripts/local-qe/llm-qwen35/runtime_v5.py',
                    r'python.exe scripts/model-comparison/run_general_context.py --run',
                    r'python.exe unrelated.py --model D:\models\weights.gguf',
                    r'python.exe scripts/model-comparison/input_preparation_v1/vocab_worker.py --owned-worker']
        for command in commands:
            with self.subTest(command=command):
                result = g.classify_process_rows([{"pid": 10, "name": "python.exe", "command": command}])
                self.assertEqual(len(result["classifiedConflicts"]), 1)

    def test_unrelated_node_audit_and_test_helpers_not_false_conflicts(self):
        commands = [r'node.exe C:\tools\codex\helper.mjs',
                    r'python.exe scripts/local-hymt/test_engine.py',
                    r'python.exe scripts/model-training/test_training.py',
                    r'python.exe scripts/local-qe/material_warning.py',
                    r'python.exe scripts/model-comparison/input_execution_v1/test_resource_guard.py', None]
        rows = [{"pid": 100+i, "name": "node.exe" if i in (0, 5) else "python.exe", "command": c}
                for i, c in enumerate(commands)]
        result = g.classify_process_rows(rows)
        self.assertEqual(result["classifiedConflicts"], [])
        self.assertEqual(result["unclassifiedCandidateCount"], 1)
        self.assertFalse(result["completeAllModelAbsenceProved"])

    def test_pressure_timers_independent_and_reset_at_threshold(self):
        low, enough = 512*1024**2-1, 512*1024**2
        timers = g.LowResourceTimers()
        self.assertIsNone(timers.observe(system(low, enough), 0))
        self.assertIsNone(timers.observe(system(enough, low), 2))
        self.assertIsNone(timers.observe(system(low, enough), 4))
        self.assertIsNone(timers.observe(system(low, enough), 6.99))
        self.assertEqual(timers.observe(system(low, enough), 7), "sustained_low_physical")

    def test_commit_independently_aborts_after_three_seconds(self):
        timers = g.LowResourceTimers()
        self.assertIsNone(timers.observe(system(commit=1), 20))
        self.assertEqual(timers.observe(system(commit=1), 23), "sustained_low_commit")

    def test_invalid_samples_and_backwards_time_refused(self):
        for value in (True, -1, None, "42"):
            with self.subTest(value=value), self.assertRaises(g.ResourceGuardError):
                g.LowResourceTimers().observe(system(physical=value), 1)
        timers = g.LowResourceTimers()
        timers.observe(system(), 2)
        with self.assertRaisesRegex(g.ResourceGuardError, "time_invalid"):
            timers.observe(system(), 1)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.process = Mock(pid=300, _handle=400)
        self.process.poll.return_value = None
        self.limiter = Mock(owner=owned())
        self.monitor = g.ResourceMonitor(self.process, self.limiter, Path(self.tmp.name)/"memory.jsonl", run_started=0)
        self.monitor.startup_deadline = None

    def tearDown(self):
        self.monitor.stop()
        self.tmp.cleanup()

    def test_ac_loss_stops_owned_handle_and_records_source_free_sample(self):
        self.monitor._observe(state(ac=0), 1)
        self.assertEqual(self.monitor.abort_reason, "ac_power_lost_or_unknown")
        self.limiter.owner._api.assert_child.assert_called_once_with(self.limiter.owner._handle, 400)
        self.process.kill.assert_called_once_with()
        self.assertEqual(self.monitor.summary()["recordedSamples"], 1)
        with self.assertRaises(g.ResourceGuardError):
            self.monitor.check()

    def test_current_or_peak_working_set_over_cap_aborts(self):
        value = state()
        value["child"]["peakWorkingSetBytes"] = 8*g.GIB+1
        self.monitor._observe(value, 1)
        self.assertEqual(self.monitor.abort_reason, "working_set_budget_exceeded")

    def test_startup_request_total_deadlines(self):
        self.monitor.startup_deadline = 10
        self.monitor._observe(state(), 10)
        self.assertEqual(self.monitor.abort_reason, "startup_time_limit")
        self.monitor.abort_reason = None
        self.monitor.startup_deadline = None
        self.monitor.request_deadline = 20
        self.monitor._observe(state(), 20)
        self.assertEqual(self.monitor.abort_reason, "request_time_limit")
        self.monitor.abort_reason = None
        self.monitor._observe(state(), 28800)
        self.assertEqual(self.monitor.abort_reason, "total_time_limit")

    def test_records_five_second_cadence_but_observes_every_call(self):
        for i in range(21):
            self.monitor._observe(state(), i/2)
        receipt = self.monitor.summary()
        self.assertEqual((receipt["observations"], receipt["recordedSamples"]), (21, 3))
        self.assertTrue(receipt["mutexDoesNotBlockUnmodifiedWorker"])

    def test_process_query_failure_aborts_without_killing_foreign_pid(self):
        self.monitor.stop_event.wait = Mock(side_effect=[False, True])
        with patch.object(g, "process_state", side_effect=g.ResourceGuardError("query_failed")):
            self.monitor._scan_processes()
        self.assertEqual(self.monitor.abort_reason, "process_monitor_failed")
        self.process.kill.assert_called_once_with()

    def test_worker_race_stops_our_model_only(self):
        self.monitor.stop_event.wait = Mock(side_effect=[False, True])
        with patch.object(g, "process_state", return_value=processes([{ "pid": 999 }])):
            self.monitor._scan_processes()
        self.assertEqual(self.monitor.abort_reason, "model_or_worker_started_during_run")
        self.process.kill.assert_called_once_with()

    def test_monitor_exception_stops_owned_process(self):
        self.monitor.stop_event.wait = Mock(side_effect=[False, True])
        with patch.object(self.monitor, "sample_and_check", side_effect=OSError("private details")):
            self.monitor._run()
        self.assertEqual(self.monitor.abort_reason, "resource_monitor_failed")
        self.assertEqual(self.monitor.monitor_error, "OSError")
        self.assertNotIn("private details", json.dumps(self.monitor.summary()))


class LifetimeTests(unittest.TestCase):
    def tearDown(self):
        g.ExperimentLock._active = False

    def test_mutex_busy_and_crash_abandonment(self):
        kernel = Mock()
        kernel.CreateMutexW.return_value = 100
        kernel.WaitForSingleObject.return_value = 0x102
        with patch.object(g, "_kernel", return_value=kernel), self.assertRaisesRegex(
                g.ResourceGuardError, "experiment_slot_busy"):
            with g.ExperimentLock():
                self.fail("busy mutex must not enter")
        kernel.CloseHandle.assert_called_once_with(100)
        kernel.WaitForSingleObject.return_value = 0x80
        with patch.object(g, "_kernel", return_value=kernel):
            with g.ExperimentLock() as lock:
                self.assertTrue(lock.receipt["abandonedPreviousOwner"])
                with self.assertRaises(g.ResourceGuardError):
                    with g.ExperimentLock():
                        pass
        kernel.ReleaseMutex.assert_called_once_with(100)
        self.assertFalse(g.ExperimentLock._active)

    def test_power_scope_releases_after_body_exception(self):
        kernel = Mock()
        kernel.SetThreadExecutionState.return_value = 0x80000000
        with patch.object(g, "_kernel", return_value=kernel), patch.object(g, "system_state", return_value=system()):
            request = g.PowerRequest()
            with self.assertRaisesRegex(ValueError, "failure"):
                with request:
                    raise ValueError("failure")
        self.assertEqual([c.args[0] for c in kernel.SetThreadExecutionState.call_args_list],
                         [0x80000003, 0x80000000])
        self.assertTrue(request.receipt["released"])

    def test_priority_failure_prevents_resume_receipt_success(self):
        limiter = object.__new__(g._PriorityCheckedLimiter)
        limiter._native = Mock()
        limiter._native.kernel.GetPriorityClass.return_value = 0x20
        with patch.object(g.WorkingSetLimit, "apply_before_resume", return_value={}):
            with self.assertRaisesRegex(g.ResourceGuardError, "priority_not_below_normal"):
                limiter.apply_before_resume(400, 300)

    def test_spawn_failure_after_handle_creation_uses_owned_cleanup(self):
        g.ExperimentLock._active = True
        child = Mock(pid=300)
        suspended = Mock(creation_receipt={"resumePreviousCount": 2, "limitAppliedBeforeResume": True})
        suspended.spawn.return_value = child
        owner = owned()
        with patch.object(g, "_PriorityCheckedLimiter"), patch.object(g, "SuspendedProcessOwner", return_value=suspended), \
                patch.object(g, "stop_owned") as cleanup:
            with self.assertRaisesRegex(g.ResourceGuardError, "receipt_invalid"):
                g.spawn_guarded(owner, ["allowed.exe"], Mock(), {}, Path.cwd())
            cleanup.assert_called_once_with(child, owner)

    def test_stop_owned_uses_retained_handle_and_waits(self):
        child, owner = Mock(pid=300, _handle=400), owned()
        child.poll.side_effect = [None, 1]
        child.wait.return_value = 1
        result = g.stop_owned(child, owner)
        owner._api.assert_child.assert_called_once_with(owner._handle, 400)
        child.kill.assert_called_once_with()
        child.wait.assert_called_once_with(timeout=15)
        self.assertTrue(result["stopped"])


if __name__ == "__main__":
    unittest.main()
