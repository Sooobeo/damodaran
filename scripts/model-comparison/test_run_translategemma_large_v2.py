"""Synthetic protocol/ownership checks, with no native server or model load."""
from contextlib import ExitStack
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import run_translategemma_large_v2 as run

TEMPLATE = "<bos><start_of_turn>user\n{{ messages[0].content[0].text }}<end_of_turn>\n{{ '<start_of_turn>model\\n' }}"
ROW = {"id": "fixture-01", "source": "The value is 10.", "context": "Reference context.", "domain": "finance",
       "sourceSha256": run.sha_text("The value is 10."), "contextSha256": run.sha_text("Reference context.")}
TOKENS = [2, 105, 123, 106, 105, 124]
EOG_LOG = "\n".join([
    "0.01 I load: printing all EOG tokens:", "0.01 I load: - 1 ('<eos>')",
    "0.01 I load: - 106 ('<end_of_turn>')", "0.01 I load: - 212 ('</s>')",
    "0.02 I print_info: EOS token = 1 '<eos>'", "0.02 I print_info: EOT token = 106 '<end_of_turn>'",
    "0.02 I print_info: EOG token = 1 '<eos>'", "0.02 I print_info: EOG token = 106 '<end_of_turn>'",
    "0.02 I print_info: EOG token = 212 '</s>'", ""])


def response(text="값은 10이다."):
    return {"content": text, "tokens_predicted": 2, "tokens": [123, 1], "stop_type": "eos", "truncated": False,
            "generation_settings": dict(run.SETTINGS), "timings": {"predicted_per_second": 1.0}}


class PureTests(unittest.TestCase):
    def test_only_source_enters_original_template(self):
        row = ROW | {"reference": "SECRET REFERENCE", "criticalPropositions": "SECRET LOGIC"}
        prompt = run.render_prompt(TEMPLATE, row)
        self.assertIn(ROW["source"], prompt)
        self.assertNotIn("Reference context", prompt)
        self.assertNotIn("SECRET", prompt)

    def test_control_tokens_rejected(self):
        for token in ("<bos>", "<end_of_turn>", "<start_of_image>", "<pad>", "<|im_start|>", "</s>", "\x00"):
            with self.subTest(token=token), self.assertRaises(run.RunError):
                run.render_prompt(TEMPLATE, ROW | {"source": "Input " + token})

    def test_context_budget_has_768_token_reserve(self):
        run.validate_prompt_tokens(TOKENS + [1000] * (1280 - len(TOKENS)), 262208)
        with self.assertRaises(run.RunError):
            run.validate_prompt_tokens(TOKENS + [1000] * (1281 - len(TOKENS)), 262208)
        with self.assertRaises(run.RunError):
            run.validate_prompt_tokens(TOKENS + [2], 262208)

    def test_eog_evidence_is_exact(self):
        self.assertEqual(run.eog_from_log(EOG_LOG)["ids"], [1, 106, 212])
        for text in ("", EOG_LOG.replace("106", "107"),
                     EOG_LOG + "print_info: EOG token = 0 '<pad>'\n",
                     EOG_LOG + "print_info: EOG token = 1 '<eos>'\n",
                     EOG_LOG.replace("print_info: EOG token = 212 '</s>'", "other: EOG token = 212 '</s>'")):
            with self.assertRaises(run.RunError):
                run.eog_from_log(text)

    def test_template_examples_do_not_contaminate_final_metadata(self):
        noise = "template: - 987 ('private example')\nprint_info_example: EOG token = 876 'private'\n"
        evidence = run.eog_from_log(EOG_LOG + noise)
        self.assertEqual(evidence["ids"], [1, 106, 212])
        self.assertNotIn("private", json.dumps(evidence))

    def test_active_bias_and_ignore_eos_are_required(self):
        for bias in (None, [{"token": 1, "bias": -100}], [{"token": 212, "bias": 0}]):
            bad = response()
            bad["generation_settings"]["logit_bias"] = bias
            with self.assertRaises(run.RunError):
                run.prediction(ROW, "prompt", TOKENS, bad, 1, 262208)
        bad = response()
        bad["generation_settings"]["ignore_eos"] = 0
        with self.assertRaises(run.RunError):
            run.prediction(ROW, "prompt", TOKENS, bad, 1, 262208)

    def test_added_runtime_eog_is_not_successful_translation(self):
        bad = response("unchanged raw") | {"tokens": [123, 212]}
        item = run.prediction(ROW, "prompt", TOKENS, bad, 1, 262208)
        self.assertEqual(item["translation"], "unchanged raw")
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["terminationClass"], "runtime_added_eog")
        self.assertFalse(item["checks"]["originalEndToken"])

    def test_context_reserves_extra_eog_and_memory_estimate_is_bounded(self):
        with self.assertRaises(run.RunError):
            run.validate_prompt_tokens(TOKENS + [212], 262208)
        policy = run.memory_requirements("12b", {"size": 100})
        self.assertEqual(policy["estimatedKvCacheUpperBoundBytes"], 805306368)
        self.assertEqual(run.memory_requirements("27b", {"size": 100})["estimatedKvCacheUpperBoundBytes"], 1040187392)
        with self.assertRaisesRegex(run.RunError, "insufficient_available_commit"):
            run.check_memory({"availablePhysical": 50 * run.GIB, "availablePageFile": 0}, policy)

    def test_raw_whitespace_preserved_and_numbers_recomputed(self):
        text = "  값은 11이다.\n"
        item = run.prediction(ROW, "prompt", TOKENS, response(text), 1.25, 262208)
        self.assertEqual(item["translation"], text)
        self.assertEqual(item["targetSha256"], run.sha_text(text))
        self.assertFalse(item["checks"]["numbersPreserved"])
        self.assertFalse(item["postProcessingApplied"])

    def test_empty_cap_and_controls_are_flagged_without_repair(self):
        for text in ("", "<end_of_turn>"):
            item = run.prediction(ROW, "prompt", TOKENS, response(text), 1, 262208)
            self.assertFalse(item["automaticChecksPassed"])
            self.assertEqual(item["translation"], text)
        value = response()
        value.update(stop_type="limit", tokens_predicted=768, tokens=[123] * 768)
        self.assertTrue(run.prediction(ROW, "prompt", TOKENS, value, 1, 262208)["outputLimitReached"])

    def test_generation_protocol_mutation_rejected(self):
        for key, value in (("tokens", [123]), ("stop_type", "word"), ("truncated", None)):
            bad = response() | {key: value}
            with self.assertRaises(run.RunError):
                run.prediction(ROW, "prompt", TOKENS, bad, 1, 262208)
        bad = response()
        bad["generation_settings"]["temperature"] = 0.7
        with self.assertRaises(run.RunError):
            run.prediction(ROW, "prompt", TOKENS, bad, 1, 262208)

    def test_server_is_cpu_owned_authenticated_and_bounded(self):
        command = run.server_command(Path("fixture"), {"name": "model.gguf"}, 34567, "test-only-key", 4)
        self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
        self.assertEqual(command[command.index("--api-key") + 1], "test-only-key")
        self.assertEqual(command[command.index("--ctx-size") + 1], "2048")
        self.assertIn("--no-context-shift", command)
        self.assertIn("--no-repack", command)
        self.assertNotIn("--override-kv", command)


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / ".training/comparisons/output"
        self.dest = self.root / "model"
        self.dest.mkdir()
        self.args = SimpleNamespace(output=self.output, threads=4, smoke=False, input=self.root / "input.jsonl", ids=None, model_size="12b")
        self.owner = MagicMock(spec=["spawn", "assert_owned"])
        self.process = MagicMock()
        self.process.stdout = io.BytesIO()
        self.process.poll.return_value = None
        self.owner.spawn.return_value = self.process
        self.diagnostics = MagicMock()
        self.diagnostics.startup_text.return_value = EOG_LOG
        self.client = MagicMock()

        def request(endpoint, payload=None, **kwargs):
            if endpoint == "/tokenize":
                for token_id, text in run.RUNTIME_TOKEN_STRINGS.items():
                    if payload["content"] == text:
                        return {"tokens": [token_id]}
                return {"tokens": TOKENS}
            if endpoint == "/props":
                return {"model_path": str(self.dest / "model.gguf"), "total_slots": 1,
                        "default_generation_settings": {"n_ctx": 2048, "params": {"ignore_eos": False}}}
            if endpoint == "/completion":
                self.diagnostics.discard.assert_called()
                return response()
            raise AssertionError("unexpected endpoint")
        self.client.request.side_effect = request

    def mocked(self):
        stack = ExitStack()
        stack.enter_context(patch.object(run, "ROOT", self.root))
        stack.enter_context(patch.object(run.screen, "read_screen", return_value=([dict(ROW)], {"selectedIds": [ROW["id"]]})))
        stack.enter_context(patch.object(run.screen, "assert_input_unchanged"))
        stack.enter_context(patch.object(run.screen, "memory_status", return_value={"availablePhysical": 12 * 1024 ** 3, "availablePageFile": 12 * 1024 ** 3}))
        stack.enter_context(patch.object(run, "code_hashes", return_value={str(Path(run.__file__).resolve()): "digest"}))
        stack.enter_context(patch.object(run, "file_stamps", return_value={}))
        stack.enter_context(patch.dict(run.setup.PROFILES, {"12b": {"dest": self.dest, "model": {"name": "model.gguf", "size": 100, "sha256": "fixed"}}}))
        stack.enter_context(patch.object(run.setup, "verify_installation", return_value={"runtimeFiles": []}))
        stack.enter_context(patch.object(run.setup, "gguf_contract", return_value=(TEMPLATE, {"templateSha256": "template", "metadata": {
            "tokenizer.ggml.tokens": {"count": 262208}, "gemma3.block_count": 48,
            "gemma3.attention.head_count_kv": 8, "gemma3.attention.key_length": 256,
            "gemma3.attention.value_length": 256}})))
        stack.enter_context(patch.object(run.setup, "digest", return_value="fixturehash"))
        stack.enter_context(patch.object(run, "claim_process_owner", return_value=self.owner))
        stack.enter_context(patch.object(run, "StartupDiagnostics", return_value=self.diagnostics))
        stack.enter_context(patch.object(run.transport, "LocalClient", return_value=self.client))
        stack.enter_context(patch.object(run.transport, "wait_ready"))
        stack.enter_context(patch.object(run.transport, "stop_process", return_value=True))
        return stack

    def test_complete_mock_run_and_exclusive_output(self):
        with self.mocked():
            summary = run.run(self.args)
            self.assertEqual(summary["status"], "completed", summary)
            self.assertEqual(summary["count"], 1)
            self.owner.spawn.assert_called_once()
            self.owner.assert_owned.assert_called_once()
            self.assertTrue(summary["childProcessStopped"])
            self.assertEqual(summary["version"], "translategemma-large-screen-v2")
            self.assertIn("memoryAfterStop", summary)
            with self.assertRaises(FileExistsError):
                run.run(self.args)
        self.assertTrue((self.output / "fixture-01-response.json").exists())
        evidence = json.loads((self.output / "startup-token-evidence.json").read_text())
        self.assertEqual(evidence["actualEog"]["ids"], [1, 106, 212])
        self.assertIsNone(evidence["defaultActiveLogitBias"])
        self.assertFalse(evidence["sourceSubmittedAtEvidenceCapture"])

    def test_low_memory_refuses_spawn_and_preserves_failure(self):
        with self.mocked(), patch.object(run.screen, "memory_status", return_value={"availablePhysical": 0}):
            summary = run.run(self.args)
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["phase"], "memory-preflight")
            self.owner.spawn.assert_not_called()
        row = json.loads((self.output / "predictions.jsonl").read_text())
        self.assertEqual(row["status"], "not_run")

    def test_low_commit_refuses_spawn_even_with_free_physical_ram(self):
        with self.mocked(), patch.object(run.screen, "memory_status", return_value={
                "availablePhysical": 20 * run.GIB, "availablePageFile": 1 * run.GIB}):
            summary = run.run(self.args)
            self.assertEqual(summary["error"], "insufficient_available_commit")
            self.owner.spawn.assert_not_called()

    def test_runtime_added_eog_preserves_raw_failure_and_stops(self):
        original = self.client.request.side_effect

        def request(endpoint, payload=None, **kwargs):
            if endpoint == "/completion":
                return response("raw output is unchanged") | {"tokens": [123, 212]}
            return original(endpoint, payload, **kwargs)

        self.client.request.side_effect = request
        with self.mocked():
            summary = run.run(self.args)
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["error"], "runtime_added_eog_stop")
            self.assertTrue(summary["childProcessStopped"])
        saved = json.loads((self.output / "predictions.jsonl").read_text())
        raw = json.loads((self.output / "fixture-01-response.json").read_text())
        self.assertEqual(saved["translation"], raw["content"])
        self.assertEqual(saved["status"], "failed")

    def test_false_owned_cleanup_is_not_marked_completed(self):
        with self.mocked(), patch.object(run.transport, "stop_process", return_value=False):
            summary = run.run(self.args)
            self.assertEqual(summary["status"], "failed")
            self.assertEqual(summary["cleanupError"], "owned_process_cleanup_failed")

    def test_startup_failure_cleans_only_owned_child_without_prompt(self):
        with self.mocked(), patch.object(run.transport, "wait_ready", side_effect=RuntimeError("fixed startup failure")), patch.object(run.transport, "stop_process", return_value=True) as stopped:
            summary = run.run(self.args)
            self.assertEqual(summary["status"], "failed")
            stopped.assert_called_once_with(self.process)
            self.client.request.assert_not_called()
            self.diagnostics.discard.assert_called()
        self.assertEqual(summary["error"], "RuntimeError")

    def test_code_mutation_blocks_generation(self):
        with self.mocked(), patch.object(run, "code_hashes", side_effect=[{"a": "1"}, {"a": "2"}]):
            summary = run.run(self.args)
            self.assertEqual(summary["status"], "failed")
            self.owner.spawn.assert_not_called()

    def test_eight_threads_forbidden_on_development_data(self):
        self.args.threads = 8
        with self.mocked(), self.assertRaises(run.RunError):
            run.run(self.args)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
