"""Synthetic v5 contracts only: no real child, model, vocabulary run or network."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch, Mock

import runtime_v5 as runtime


class FakeGuard:
    limiter = None
    last_deadline_exceeded_at_end = False
    error = None
    def begin_request(self, _row_id):
        self.check()
    def end_phase(self):
        pass
    def check(self):
        if self.error:
            raise ValueError(self.error)


class FakeClient:
    def __init__(self, content='{"semantic_issues":[],"uncertainties":[]}'):
        self.content = content
        self.calls = []
        self.prompt = "<|im_start|>system\ns<|im_end|>\n<|im_start|>user\nt<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    def call(self, endpoint, body=None, *, raw_path=None, **_kwargs):
        self.calls.append((endpoint, body))
        if endpoint == "/apply-template":
            result = {"prompt": self.prompt}
        elif endpoint == "/tokenize":
            result = {"tokens": [1, 2, 3]}
        else:
            result = {"content": self.content, "tokens": [9], "prompt": self.prompt,
                "truncated": False, "stop_type": "eos", "stopping_word": "",
                "generation_settings": copy.deepcopy(runtime.NATIVE_SAMPLING),
                "timings": {"cache_n": 0, "prompt_n": 3, "prompt_ms": 10, "predicted_ms": 10}}
        if raw_path:
            runtime.prepare.json_once(raw_path, result)
        return result


class RuntimeV5Tests(unittest.TestCase):
    def setUp(self):
        self.output = runtime.DEST / "synthetic-tests" / ("runtime-v5-" + uuid.uuid4().hex)
        self.output.mkdir(parents=True, exist_ok=False)

    def test_resource_exact_boundaries_and_unchanged_sampling(self):
        nine = 9 * runtime.GIB
        self.assertTrue(runtime.memory_plan({"availablePhysical": nine, "availableCommit": nine})["physicalPassed"])
        self.assertFalse(runtime.memory_plan({"availablePhysical": nine - 1, "availableCommit": nine})["physicalPassed"])
        self.assertFalse(runtime.memory_plan({"availablePhysical": nine, "availableCommit": nine - 1})["commitPassed"])
        self.assertEqual(runtime.BUDGET, 6 * runtime.GIB)
        state = {"availablePhysical": runtime.GIB, "availableCommit": runtime.GIB}
        child = {"workingSetBytes": runtime.BUDGET, "peakWorkingSetBytes": runtime.BUDGET, "privateBytes": runtime.BUDGET}
        self.assertIsNone(runtime.Guard.reason(state, child, 1, 0, 2))
        for field in child:
            self.assertEqual(runtime.Guard.reason(state, child | {field: runtime.BUDGET + 1}, 1, 0, 2),
                             "child_memory_budget_exceeded")
        self.assertEqual(runtime.Guard.reason(state | {"availablePhysical": runtime.GIB - 1}, child, 1, 0, 2),
                         "system_memory_headroom_low")
        self.assertEqual(runtime.Guard.reason(state, child, 2, 0, 2), "request_or_startup_deadline")
        import runtime_v4
        self.assertEqual(runtime.NATIVE_SAMPLING, runtime_v4.NATIVE_SAMPLING)
        self.assertEqual((runtime.STARTUP_SECONDS, runtime.REQUEST_SECONDS, runtime.TOTAL_SECONDS), (600, 600, 14400))

    def test_server_flags_and_no_key_logging_configuration(self):
        command = runtime.server_command(1234, "synthetic-secret")
        for flag, expected in (("--threads", "4"), ("--threads-batch", "4"), ("--poll", "0"),
                               ("--poll-batch", "0"), ("--prio", "-1"), ("--ctx-size", "4096"),
                               ("--ctx-checkpoints", "3"), ("--load-mode", "mmap"), ("--cache-ram", "0"),
                               ("--log-verbosity", "4")):
            self.assertEqual(command[command.index(flag) + 1], expected)
        self.assertIn("--offline", command)
        self.assertIn("--no-context-shift", command)

    def test_pending_audit_and_missing_binding_refuse_without_native(self):
        with patch.object(runtime, "load_profile", return_value={"tokenBudgetAudit": None}):
            with self.assertRaisesRegex(ValueError, "audit_not_prepared"):
                runtime.token_budget_preflight([])
        with patch.object(runtime, "verify_installation", side_effect=AssertionError("model file must not be read")):
            args = SimpleNamespace(input=runtime.FIXED_INPUT, run=True, execution_binding=None)
            with self.assertRaisesRegex(ValueError, "receipt_required"):
                runtime.run(args)

    def test_old_v1_token_audit_is_not_reused_as_v2_parity(self):
        record = {"path": str((runtime.DEST / "token-budget-v1/attempt-001/summary.json").relative_to(runtime.ROOT)),
                  "sha256": "old-audit"}
        with patch.object(runtime, "load_profile", return_value={"tokenBudgetAudit": record}), \
             patch.object(runtime, "sha_file", side_effect=AssertionError("old evidence must not be accepted/read")):
            with self.assertRaisesRegex(ValueError, "identity_differs"):
                runtime.token_budget_preflight([])

    def test_projection_never_contains_external_screen_or_labels(self):
        row = {"id": "do-not-send-id", "source": "A owns B.", "translation": "A는 B를 소유한다.", "context": ""}
        request = runtime.contract.build_request(row)
        self.assertEqual(json.loads(request["messages"][1]["content"]), {k: row[k] for k in ("source", "translation", "context")})
        self.assertNotIn("do-not-send-id", json.dumps(request))
        for field in ("materialError", "expectedMaterialError", "screenGate", "referenceKo"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                runtime.contract.build_request(row | {field: "DO NOT LEAK"})

    def test_native_eos_effective_sampling_and_strict_context(self):
        client = FakeClient()
        result = client.call("/completion")
        self.assertTrue(runtime.validate_native(result, client.prompt))
        for key, value in (("stop_type", "limit"), ("truncated", True), ("tokens", [True]), ("prompt", "x")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                runtime.validate_native(result | {key: value}, client.prompt)
        bad = copy.deepcopy(result)
        bad["generation_settings"]["n_predict"] = 2047
        with self.assertRaisesRegex(ValueError, "sampling_differs"):
            runtime.validate_native(bad, client.prompt)
        with self.assertRaisesRegex(ValueError, "context_budget"):
            runtime.completion_payload({}, [1] * 2048)

    def make_rows(self):
        return [{"id": "synthetic-" + str(i), "source": "A owns B.", "translation": "A는 B를 소유한다.", "context": ""}
                for i in range(8)]

    def run_rows(self, decision, *, client=None, guard=None):
        rows, client, guard = self.make_rows(), client or FakeClient(), guard or FakeGuard()
        audit = {"rows": {row["id"]: {"promptSha256": runtime.sha_text(client.prompt), "inputTokens": 3,
                  "tokenIdsSha256": runtime.sha_text("[1,2,3]")} for row in rows}}
        plan, finished = {"generationRequests": 0, "status": "failed"}, set()
        def checked(row_id, mapping):
            index = len(finished) + 1
            # The frozen assessment exists before ground-truth evaluation.
            self.assertTrue((self.output / f"{index:04d}-assessment.json").is_file())
            return {"rowId": row_id, "continueRun": decision,
                    "reason": "matched_fixed_development_label" if decision else "false_positive"}
        with patch.object(runtime, "verify_run_unchanged"), patch.object(runtime, "screen_gate", return_value=SimpleNamespace(check=checked)), \
             patch.object(runtime, "child_telemetry", return_value={"creationTicks": 100, "priorityClass": 16384}):
            runtime.evaluate_rows(SimpleNamespace(), plan, rows, self.output, None, guard, client, audit, finished)
        return plan, finished, client

    def test_first_mismatch_persists_raw_assessment_gate_and_no_second_generation(self):
        plan, finished, client = self.run_rows(False)
        self.assertEqual(plan["status"], "stopped_futility")
        self.assertEqual(plan["generationRequests"], 1)
        self.assertEqual(len(finished), 1)
        self.assertEqual(sum(endpoint == "/completion" for endpoint, _ in client.calls), 1)
        self.assertEqual(len(client.calls), 3)
        self.assertTrue((self.output / "0001-completion-receipt.json").is_file())
        gate = json.loads((self.output / "0001-gate.json").read_bytes())
        self.assertEqual(gate["assessmentSha256"], runtime.sha_file(self.output / gate["assessmentFile"]))
        self.assertEqual(gate["rawResponseSha256"], runtime.sha_file(self.output / gate["rawResponseFile"]))
        self.assertEqual(plan["nativeTemplateParity"]["rowsVerified"], 1)

    def test_all_eight_matches_only_complete_diagnostic(self):
        plan, finished, client = self.run_rows(True)
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(plan["generationRequests"], len(finished))
        self.assertEqual(len(finished), 8)
        self.assertEqual(len(plan["screenDecisions"]), 8)
        self.assertNotIn("fullBaselineAccepted", plan)  # No hidden acceptance is manufactured by the row loop.

    def test_invalid_contract_saves_raw_and_assessment_before_failure_without_gate(self):
        client = FakeClient(content="not json")
        with self.assertRaisesRegex(ValueError, "structured_response"):
            self.run_rows(True, client=client)
        self.assertEqual(sum(ep == "/completion" for ep, _ in client.calls), 1)
        self.assertTrue((self.output / "0001-assessment.json").is_file())
        self.assertFalse((self.output / "0001-gate.json").exists())

    def test_first_actual_template_mismatch_refuses_before_generation(self):
        with patch.object(runtime, "validate_audited_prompt", side_effect=ValueError("actual_prompt_or_tokens_differ")):
            client = FakeClient()
            with self.assertRaisesRegex(ValueError, "actual_prompt_or_tokens"):
                self.run_rows(True, client=client)
        self.assertEqual(sum(ep == "/completion" for ep, _ in client.calls), 0)

    def test_late_deadline_is_latched_without_monitor_and_raw_survives(self):
        guard = object.__new__(runtime.Guard)
        guard.state_lock = threading.Lock()
        guard.deadline, guard.error, guard.last_deadline_exceeded_at_end = 10, None, False
        guard.phase, guard.row_id = "request", "synthetic-0"
        guard.request_started, guard.request_started_utc = 0, "synthetic"
        with patch.object(runtime.time, "monotonic", return_value=10):
            guard.end_phase()
        self.assertEqual(guard.error, "request_or_startup_deadline")
        self.assertTrue(guard.last_deadline_exceeded_at_end)
        class LateGuard(FakeGuard):
            def end_phase(self):
                self.error = "request_or_startup_deadline"
                self.last_deadline_exceeded_at_end = True
        client = FakeClient()
        with self.assertRaisesRegex(ValueError, "deadline"):
            self.run_rows(True, guard=LateGuard(), client=client)
        receipt = json.loads((self.output / "0001-completion-receipt.json").read_bytes())
        self.assertTrue(receipt["lastDeadlineExceededAtEnd"])
        self.assertTrue((self.output / "0001-completion.raw.json").is_file())
        self.assertFalse((self.output / "0001-assessment.json").exists())
        self.assertEqual(sum(ep == "/completion" for ep, _ in client.calls), 1)

    def test_no_returned_process_does_not_overwrite_spawn_cleanup_failure(self):
        # Reach a failing owned launcher with every expensive operation mocked.
        args = SimpleNamespace(slot_approval="synthetic", input=runtime.FIXED_INPUT)
        rows = self.make_rows()
        plan = {"codeHashes": {}, "installation": {}, "inputSha256": "synthetic", "executionBinding": {"sha256": "s"}}
        class SpawnError(RuntimeError):
            code = "owned_child_creation_cleanup_failed"
        launcher = SimpleNamespace(creation_receipt={"failure": "synthetic"}, spawn=Mock(side_effect=SpawnError()))
        fake_owner = SimpleNamespace(claim_process_owner=lambda: None)
        fake_limit = SimpleNamespace(WorkingSetLimit=lambda *_a, **_kw: None)
        fake_launcher = SimpleNamespace(SuspendedProcessOwner=lambda *_a: launcher)
        with patch.dict(sys.modules, process_owner=fake_owner, working_set_limit=fake_limit, suspended_process_owner=fake_launcher), \
             patch.object(runtime, "require_execution_binding"), patch.object(runtime, "read_fixed_input", return_value=(rows, "s")), \
             patch.object(runtime, "token_budget_preflight", return_value={"templateSha256": "t"}), \
             patch.object(runtime, "memory_plan", return_value={"physicalPassed": True, "commitPassed": True}), \
             patch.object(runtime, "verify_run_unchanged"), patch.object(runtime, "verify_installation", return_value={}), \
             patch.object(runtime, "gguf_metadata", return_value=("t", {"templateSha256": "t"})), \
             patch.object(runtime.prepare, "json_once"), patch.object(runtime, "sha_file", return_value="s"), \
             patch.object(runtime, "stop_owned") as stop:
            runtime.execute(args, plan, rows, self.output)
        self.assertEqual(plan["status"], "failed")
        self.assertFalse(plan["childProcessStopped"])
        self.assertTrue(plan["spawnCleanupUnconfirmed"])
        stop.assert_not_called()

    def test_binding_requires_review_and_fixed_location_without_importing_evaluator(self):
        path = self.output / "untrusted-binding.json"
        path.write_text('{"version":"qwen-v5-dev8-execution-binding-v1","rootReviewed":true}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "path_invalid"):
            runtime.require_execution_binding(SimpleNamespace(execution_binding=path))

    def test_exact_external_binding_validates_only_data_and_rejects_tamper(self):
        root = self.output / "binding-fixture"
        dest = root / ".translation/qe/llm-candidates/qwen35-9b"
        receipt = root / ".training/verifications/binding.json"
        evaluator = root / "scripts/local-qe/evaluate_llm_review_v3.py"
        profile = root / "profile.json"
        for parent in (dest, receipt.parent, evaluator.parent):
            parent.mkdir(parents=True, exist_ok=True)
        # If this file were imported, the test would fail. It is hash-only data.
        evaluator.write_text('raise AssertionError("MUST NOT EXECUTE EVALUATOR")\n', encoding="utf-8")
        profile.write_text("{}", encoding="utf-8")
        freeze = {"version": "qwen-v5-dev8-freeze-v1", "runtimeVersion": runtime.VERSION,
            "inputSha256": runtime.FIXED_INPUT_SHA, "codeHashes": {"fixed": "code"},
            "resourceProfileSha256": runtime.sha_file(profile), "tokenBudgetAuditSha256": "audit",
            "materialWarningMapping": {"m": 1}, "screenGate": {"g": 1},
            "evaluationAdapter": {"path": "scripts/local-qe/evaluate_llm_review_v3.py", "sha256": runtime.sha_file(evaluator)}}
        frozen = dest / "freeze-v5-dev8.json"
        frozen.write_text(json.dumps(freeze), encoding="utf-8")
        binding = {"version": runtime.EXECUTION_BINDING_VERSION, "rootReviewed": True,
                   "freezePath": str(frozen.relative_to(root)), "freezeSha256": runtime.sha_file(frozen)}
        receipt.write_text(json.dumps(binding), encoding="utf-8")
        with patch.object(runtime, "ROOT", root), patch.object(runtime, "DEST", dest), \
             patch.object(runtime, "PROFILE_PATH", profile), patch.object(runtime, "code_hashes", return_value={"fixed": "code"}), \
             patch.object(runtime, "load_profile", return_value={"tokenBudgetAudit": {"sha256": "audit"}}), \
             patch.object(runtime, "material_mapping", return_value=SimpleNamespace(identity=lambda: {"m": 1})), \
             patch.object(runtime, "screen_gate", return_value=SimpleNamespace(identity=lambda: {"g": 1})):
            checked = runtime.require_execution_binding(SimpleNamespace(execution_binding=receipt))
            self.assertEqual(checked["freezeSha256"], runtime.sha_file(frozen))
            self.assertEqual(len(checked["files"]), 3)
            binding["rootReviewed"] = 1  # A truthy number is not review evidence.
            receipt.write_text(json.dumps(binding), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not_reviewed"):
                runtime.require_execution_binding(SimpleNamespace(execution_binding=receipt))
            binding["rootReviewed"] = True
            receipt.write_text(json.dumps(binding), encoding="utf-8")
            evaluator.write_text("# changed\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "freeze_contract_differs"):
                runtime.require_execution_binding(SimpleNamespace(execution_binding=receipt))

    def test_guard_telemetry_retains_gap_phase_identity_without_prose_or_key(self):
        process = SimpleNamespace(poll=lambda: None)
        limiter = SimpleNamespace(sample_child=lambda _p: {"workingSetBytes": 1, "peakWorkingSetBytes": 1, "privateBytes": 1})
        guard = runtime.Guard(process, limiter, self.output)
        guard.stop = SimpleNamespace(wait=Mock(side_effect=[False, True]))
        guard.phase, guard.row_id = "request", "synthetic-row"
        guard.request_started, guard.request_started_utc = 0, "synthetic-time"
        guard.started, guard.last_sample, guard.deadline = 0, 1, 100
        with patch.object(runtime, "memory_status", return_value={"availablePhysical": runtime.GIB, "availableCommit": runtime.GIB}), \
             patch.object(runtime.time, "monotonic", return_value=4), \
             patch.object(runtime, "child_telemetry", return_value={"pid": 123, "creationTicks": 99, "priorityClass": 16384,
                "kernelCpuSeconds": 1, "userCpuSeconds": 2, "totalCpuSeconds": 3}):
            guard.loop()
        guard.stream.close()
        event = json.loads((self.output / "memory.jsonl").read_bytes())
        self.assertEqual(event["heartbeatGapSeconds"], 3)
        self.assertEqual(event["phase"], "request")
        self.assertEqual(event["child"]["creationTicks"], 99)
        self.assertEqual(event["actualGeneratedTokenProgress"], "unknown_nonstreaming")
        for forbidden in ("source", "translation", "prompt", "apiKey", "command"):
            self.assertNotIn(forbidden, event)

    def test_termination_boundary_integrity_failure_does_not_skip_cleanup(self):
        args = SimpleNamespace(input=runtime.FIXED_INPUT, slot_approval="synthetic")
        plan, rows, events = {"installation": {}}, self.make_rows(), []
        def changed(*_args):
            events.append("integrity-check")
            raise ValueError("execution_binding_evidence_changed")
        def stop(process):
            self.assertIsNone(process)
            events.append("owned-cleanup")
            return True
        with patch.object(runtime, "require_execution_binding"), patch.object(runtime, "read_fixed_input", return_value=(rows, "s")), \
             patch.object(runtime, "token_budget_preflight", return_value={}), \
             patch.object(runtime, "memory_plan", return_value={"physicalPassed": False, "commitPassed": True}), \
             patch.object(runtime, "verify_run_unchanged", side_effect=changed), patch.object(runtime, "stop_owned", side_effect=stop):
            runtime.execute(args, plan, rows, self.output)
        self.assertEqual(events, ["integrity-check", "owned-cleanup", "integrity-check"])
        self.assertEqual(plan["status"], "failed")
        self.assertFalse(plan["finalIntegrityVerified"])
        self.assertTrue(plan["childProcessStopped"])
        self.assertEqual(plan["generationRequests"], 0)


if __name__ == "__main__":
    unittest.main()
