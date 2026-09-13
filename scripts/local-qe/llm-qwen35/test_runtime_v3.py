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
import runtime_v3 as runtime


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
        self.assertEqual(runtime.memory_plan({"availablePhysical": minimum, "availableCommit": minimum})["resourceProfile"], "qwen35-cpu-8g-v2")

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
                            "total_slots": 1, "default_generation_settings": {"n_ctx": 8192}}
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
        metadata = {"tokenizerEosId": 1, "eosTokenText": "<|im_end|>", "boundaryTokenIds": {}}
        with mock.patch.dict(sys.modules, modules), mock.patch.object(sys, "version_info", (3, 11, 9)), \
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
