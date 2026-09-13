"""Synthetic tests only; no native model, network, package installation or DB."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import prepare
import prepare_inputs
import runtime_v4 as runtime


class Tests(unittest.TestCase):
    def test_loaded_python_image_is_hashable(self):
        actual = runtime.actual_python_executable()
        self.assertTrue(actual.is_absolute())
        self.assertEqual(len(prepare.sha_file(actual)), 64)
        self.assertIn(str(actual.resolve()), runtime.code_hashes())

    @classmethod
    def setUpClass(cls):
        cls.output = prepare.DEST / "synthetic-tests" / str(time.time_ns())
        cls.output.mkdir(parents=True, exist_ok=False)
        assert cls.output.resolve().is_relative_to(prepare.DEST.resolve())

    def native(self, content='{"semantic_issues":[],"uncertainties":[],"language_notes":[]}'):
        return {"content": content, "tokens": [10, 20], "prompt": "p", "truncated": False,
                "stop_type": "eos", "stopping_word": "", "generation_settings": copy.deepcopy(runtime.NATIVE_SAMPLING)}

    def test_memory_profile_boundary(self):
        minimum = 11 * runtime.GIB
        self.assertTrue(runtime.memory_plan({"availablePhysical": minimum, "availableCommit": minimum})["physicalPassed"])
        self.assertFalse(runtime.memory_plan({"availablePhysical": minimum - 1, "availableCommit": minimum})["physicalPassed"])
        self.assertEqual(runtime.memory_plan({"availablePhysical": minimum, "availableCommit": minimum})["resourceProfile"], "qwen35-cpu-8g-4threads-4k-prefix-v3")

    def test_guard_aborts_budget_headroom_and_deadline(self):
        state = {"availablePhysical": 4 * runtime.GIB, "availableCommit": 4 * runtime.GIB}
        child = {"workingSetBytes": runtime.BUDGET, "peakWorkingSetBytes": runtime.BUDGET, "privateBytes": 100}
        self.assertIsNone(runtime.Guard.reason(state, child, 2, 0, 3))
        self.assertEqual(runtime.Guard.reason(state, child | {"privateBytes": runtime.BUDGET + 1}, 2, 0, 3), "child_memory_budget_exceeded")
        self.assertEqual(runtime.Guard.reason(state | {"availableCommit": 0}, child, 2, 0, 3), "system_memory_headroom_low")
        self.assertEqual(runtime.Guard.reason(state, child, 3, 0, 3), "request_or_startup_deadline")

    def test_context_budget_refuses_instead_of_truncating(self):
        with self.assertRaisesRegex(ValueError, "context_budget"):
            runtime.completion_payload({}, [1] * (runtime.CONTEXT - runtime.OUTPUT_TOKENS))
        with self.assertRaisesRegex(ValueError, "invalid_prompt_tokens"):
            runtime.completion_payload({}, [True])
        self.assertEqual(runtime.completion_payload({}, [1])["n_predict"], 2048)

    def test_native_eos_and_effective_parameters(self):
        self.assertTrue(runtime.validate_native(self.native(), "p"))
        for field, value in (("stop_type", "limit"), ("truncated", True), ("prompt", "changed")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                runtime.validate_native(self.native() | {field: value}, "p")
        for key, value in (("seed", 0), ("samplers", []), ("presence_penalty", 0)):
            changed = self.native()
            changed["generation_settings"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "sampling_differs"):
                runtime.validate_native(changed, "p")

    def test_native_control_and_limit_rejection(self):
        with self.assertRaisesRegex(ValueError, "control_or_thinking"):
            runtime.validate_native(self.native("<think>hidden</think>{}"), "p")
        with self.assertRaisesRegex(ValueError, "output_limit"):
            runtime.validate_native(self.native() | {"tokens": [1] * 2048}, "p")

    def test_projection_excludes_reference_and_labels(self):
        row = {"id": "i", "source": "s", "translation": "t", "context": "c",
               "referenceKo": "SECRET", "checks": ["SECRET"], "namedError": "SECRET", "translationSystem": "SECRET"}
        self.assertEqual(prepare_inputs.project([row]), [{"id": "i", "source": "s", "translation": "t", "context": "c"}])

    def test_no_redirect_handler(self):
        client = runtime.Client(1234, "secret")
        handlers = [h for h in client.opener.handlers if hasattr(h, "redirect_request")]
        self.assertEqual(len(handlers), 1)
        self.assertIsNone(handlers[0].redirect_request(None, None, 302, "", {}, "https://example.com/"))

    def test_raw_is_preserved_before_json_parse(self):
        raw_path = self.output / "invalid-native.raw"
        response = io.BytesIO(b"not-json")
        response.status = 200
        client = runtime.Client(1234, "secret")
        client.opener.open = mock.Mock(return_value=response)
        with self.assertRaises(json.JSONDecodeError):
            client.call("/completion", {}, raw_path=raw_path)
        self.assertEqual(raw_path.read_bytes(), b"not-json")

    def test_stop_owned_waits_and_escalates_only_given_handle(self):
        child = mock.Mock()
        child.poll.side_effect = [None, 1]
        child.wait.side_effect = [subprocess.TimeoutExpired("owned", 10), 1]
        self.assertTrue(runtime.stop_owned(child))
        child.terminate.assert_called_once()
        child.kill.assert_called_once()

    def test_server_is_single_cpu_model_and_no_thinking(self):
        command = runtime.server_command(1234, "secret")
        self.assertEqual(command[command.index("--parallel") + 1], "1")
        self.assertEqual(command[command.index("--gpu-layers") + 1], "0")
        self.assertEqual(command[command.index("--chat-template-kwargs") + 1], '{"enable_thinking":false}')
        self.assertNotIn("--hf-repo", command)
        for required in ("--offline", "--no-agent", "--no-warmup", "--no-repack"):
            self.assertIn(required, command)
        self.assertEqual(command[command.index("--log-verbosity") + 1], "4")
        for option, expected in (("--threads", "4"), ("--threads-batch", "4"),
                                 ("--prio", "-1"), ("--poll", "0"), ("--poll-batch", "0"),
                                 ("--ctx-checkpoints", "3"), ("--ctx-size", "4096"),
                                 ("--cache-ram", "0")):
            self.assertEqual(command[command.index(option) + 1], expected)

    def test_cache_accounting_uses_prompt_timings_not_slot_cache_size(self):
        result = runtime.cache_observation({"tokens_cached": 1287,
            "timings": {"cache_n": 927, "prompt_n": 226, "prompt_ms": 100, "predicted_ms": 20}}, 1153)
        self.assertEqual(result["reusedPromptTokens"], 927)
        self.assertEqual(result["slotTokensAfterResponse"], 1287)
        self.assertTrue(result["prefixReuseObserved"])
        with self.assertRaisesRegex(ValueError, "cache_accounting"):
            runtime.cache_observation({"timings": {"cache_n": 927, "prompt_n": 227}}, 1153)
        with self.assertRaisesRegex(ValueError, "cache_accounting"):
            runtime.cache_observation({"timings": {"cache_n": True, "prompt_n": 1152}}, 1153)

    def test_audited_prompt_bytes_tokens_and_boundaries_must_match(self):
        prompt = "<|im_start|>system\nS<|im_end|>\n<|im_start|>user\nU<|im_end|>\n<|im_start|>assistant\n"
        tokens = [100, 200, 300]
        row = {"id": "x"}
        audit = {"rows": {"x": {"promptSha256": runtime.sha_text(prompt), "inputTokens": 3,
            "tokenIdsSha256": runtime.sha_text(json.dumps(tokens, separators=(",", ":")))}}}
        runtime.validate_audited_prompt(audit, row, prompt, tokens)
        for changed_prompt, changed_tokens in ((prompt + " ", tokens), (prompt, [100, 201, 300])):
            with self.assertRaisesRegex(ValueError, "differ_from_preflight"):
                runtime.validate_audited_prompt(audit, row, changed_prompt, changed_tokens)
        duplicate = prompt + "<|im_start|>user\n"
        audit["rows"]["x"]["promptSha256"] = runtime.sha_text(duplicate)
        with self.assertRaisesRegex(ValueError, "boundary_not_unique"):
            runtime.validate_audited_prompt(audit, row, duplicate, tokens)

    def test_delimiters_are_metadata_and_labels_never_enter_native_prompt(self):
        request = runtime.contract.build_request({"id": "id-not-model-data", "source": "A.",
                                                 "translation": "가.", "context": ""})
        native = runtime.completion_payload(request, [10, 20])
        self.assertTrue(native["cache_prompt"])
        self.assertEqual(native["message_delimiters"], runtime.MESSAGE_DELIMITERS)
        self.assertEqual(native["prompt"], [10, 20])
        self.assertNotIn("id-not-model-data", json.dumps(request))
        for disallowed in ("referenceKo", "checks", "judgments", "labels", "namedError", "materialWarningV2"):
            self.assertNotIn(disallowed, native)

    def test_budget_failure_stops_before_any_spawn_or_generation(self):
        output = self.output / "budget-refusal"
        output.mkdir()
        plan = {"codeHashes": {}, "installation": {}, "inputSha256": "synthetic"}
        args = SimpleNamespace(slot_approval="synthetic-only", input=Path("unused"))
        with mock.patch.object(runtime, "token_budget_preflight", side_effect=ValueError("token_budget_audit_not_passed")), \
                mock.patch.object(runtime, "memory_plan") as memory, \
                mock.patch.object(runtime, "verify_unchanged"), \
                mock.patch.object(runtime, "verify_installation", return_value={}):
            runtime.execute(args, plan, [{"id": "x"}], output)
        memory.assert_not_called()
        self.assertEqual(plan["generationRequests"], 0)
        self.assertFalse(plan["modelLoaded"])
        self.assertTrue(plan["childProcessStopped"])
        self.assertEqual(plan["failure"]["code"], "token_budget_audit_not_passed")

    def test_stale_audited_contract_is_rejected_in_preflight(self):
        with mock.patch.object(runtime.contract, "contract_identity", return_value={"version": "changed"}):
            with self.assertRaisesRegex(ValueError, "audited_contract_identity_changed"):
                runtime.token_budget_preflight([])

    def test_spawn_cleanup_uncertainty_is_never_reported_as_stopped(self):
        class CleanupUnconfirmed(RuntimeError):
            code = "owned_child_creation_cleanup_failed"
        output = self.output / "spawn-cleanup-refusal"
        output.mkdir()
        launcher = mock.Mock(creation_receipt={"pid": 7654})
        launcher.spawn.side_effect = CleanupUnconfirmed()
        modules = {"process_owner": SimpleNamespace(claim_process_owner=mock.Mock()),
                   "working_set_limit": SimpleNamespace(WorkingSetLimit=mock.Mock()),
                   "suspended_process_owner": SimpleNamespace(SuspendedProcessOwner=lambda *a: launcher)}
        args = SimpleNamespace(slot_approval="synthetic-only", input=Path("unused"))
        plan = {"codeHashes": {}, "installation": {}, "inputSha256": "synthetic"}
        with mock.patch.dict(sys.modules, modules), \
                mock.patch.object(runtime, "token_budget_preflight", return_value={"templateSha256": "template"}), \
                mock.patch.object(runtime, "memory_plan", return_value={"physicalPassed": True, "commitPassed": True}), \
                mock.patch.object(runtime, "gguf_metadata", return_value=("template", {"templateSha256": "template"})), \
                mock.patch.object(runtime, "verify_unchanged"), \
                mock.patch.object(runtime, "verify_installation", return_value={}), \
                mock.patch.object(runtime, "stop_owned") as stop:
            runtime.execute(args, plan, [{"id": "x"}], output)
        stop.assert_not_called()
        self.assertFalse(plan["childProcessStopped"])
        self.assertTrue(plan["spawnCleanupUnconfirmed"])
        self.assertEqual(plan["generationRequests"], 0)

    def test_invalid_schema_is_failed_and_owned_child_is_stopped(self):
        output = self.output / "mock-run"
        output.mkdir()
        suffix = "<think>\n\n</think>\n\n"
        process = mock.Mock(pid=7654)
        process.poll.return_value = None
        def spawn(command, **kwargs):
            kwargs["stdout"].write(b"printing all EOG tokens:\nllama: - 1 ('<|im_end|>')\n")
            kwargs["stdout"].flush()
            return process
        owner = mock.Mock()
        launcher = mock.Mock(spawn=spawn, creation_receipt={"synthetic": True})
        limiter = mock.Mock()
        limiter.apply_child.return_value = {}
        guard = mock.Mock(error=None, kill_error=None)
        class FakeClient:
            def __init__(self, *args):
                pass
            def call(self, endpoint, body=None, **kwargs):
                if endpoint == "/health":
                    return {"status": "ok"}
                if endpoint == "/props":
                    return {"model_path": str(prepare.DEST / prepare.MODEL_NAME), "chat_template": "t",
                            "total_slots": 1, "default_generation_settings": {"n_ctx": runtime.CONTEXT}}
                if endpoint == "/apply-template":
                    return {"prompt": suffix}
                if endpoint == "/tokenize":
                    return {"tokens": [1, 2]}
                value = Tests.native(Tests(), '{"wrong":[]}') | {"prompt": suffix}
                prepare.json_once(kwargs["raw_path"], value)
                return value
        modules = {"process_owner": SimpleNamespace(claim_process_owner=lambda: owner),
                   "working_set_limit": SimpleNamespace(WorkingSetLimit=lambda *a, **k: limiter),
                   "suspended_process_owner": SimpleNamespace(SuspendedProcessOwner=lambda *a: launcher)}
        args = SimpleNamespace(slot_approval="synthetic-only", input=Path("unused"))
        plan = {"codeHashes": {}, "installation": {}, "inputSha256": "synthetic"}
        metadata = {"tokenizerEosId": 1, "eosTokenText": "<|im_end|>", "boundaryTokenIds": {}, "templateSha256": "template"}
        with mock.patch.dict(sys.modules, modules), mock.patch.object(sys, "version_info", (3, 11, 9)), \
                mock.patch.object(runtime, "token_budget_preflight", return_value={"templateSha256": "template"}), \
                mock.patch.object(runtime, "validate_audited_prompt"), \
                mock.patch.object(runtime, "child_telemetry", return_value={"priorityName": "BelowNormal"}), \
                mock.patch.object(runtime, "cache_observation", return_value={"prefixReuseObserved": False}), \
                mock.patch.object(runtime, "memory_plan", return_value={"physicalPassed": True, "commitPassed": True}), \
                mock.patch.object(runtime, "verify_unchanged"), mock.patch.object(runtime, "verify_installation", return_value={}), \
                mock.patch.object(runtime, "gguf_metadata", return_value=("t", metadata)), \
                mock.patch.object(runtime, "Guard", return_value=guard), mock.patch.object(runtime, "Client", FakeClient), \
                mock.patch.object(runtime, "stop_owned", return_value=True) as stop:
            runtime.execute(args, plan, [{"id": "mock", "source": "A.", "translation": "가.", "context": ""}], output)
        self.assertEqual(plan["status"], "failed")
        self.assertEqual(plan["itemStatuses"], [{"id": "mock", "status": "failed"}])
        self.assertTrue(plan["childProcessStopped"])
        self.assertEqual(json.loads((output / "0001-assessment.json").read_text(encoding="utf-8"))["status"], "invalid_structure")
        stop.assert_called_once_with(process)


if __name__ == "__main__":
    unittest.main()
