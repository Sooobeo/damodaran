"""Lightweight 30B boundary tests: no model execution or network calls."""
from __future__ import annotations

import copy
from pathlib import Path
import unittest

import run_hymt30 as runner


class Hy30Tests(unittest.TestCase):
    def response(self, text="고정 기능 예문이다."):
        return {"content": text, "tokens": [45, 120025], "tokens_predicted": 2,
                "stop_type": "eos", "truncated": False, "generation_settings": copy.deepcopy(runner.SAMPLING)}

    def row(self):
        return {"id": "FIXTURE", "source": "A fixed functional example.", "context": "",
                "sourceSha256": runner.common.sha_text("A fixed functional example."),
                "contextSha256": runner.common.sha_text("")}

    def test_30b_independent_publisher_sampling(self):
        self.assertEqual((runner.SAMPLING["top_p"], runner.SAMPLING["top_k"], runner.SAMPLING["repeat_penalty"]), (1, -1, 1))
        self.assertIsNot(runner.SAMPLING, runner.common.SAMPLING)
        self.assertEqual(runner.SAMPLING["stop"], [])
        with self.assertRaisesRegex(ValueError, "sampling_mismatch"):
            runner.validate_settings(runner.common.SAMPLING)

    def test_command_owned_cpu_only_no_eos_override(self):
        command = runner.server_command(43219, "fixture-key")
        for key, value in {"--host": "127.0.0.1", "--threads": "4", "--gpu-layers": "0",
                           "--device": "none", "--load-mode": "mmap", "--ctx-size": "8192", "--top-k": "-1"}.items():
            self.assertEqual(command[command.index(key) + 1], value)
        for value in ("--no-agent", "--offline", "--no-webui", "--no-op-offload"):
            self.assertIn(value, command)
        self.assertNotIn("--override-kv", command)
        self.assertIn(runner.setup.MODEL["name"], " ".join(command))
        source = Path(runner.__file__).read_text("utf-8")
        self.assertIn("owner.spawn(command", source)
        self.assertNotIn("subprocess.Popen(", source)

    def test_architecture_budget_boundary(self):
        gate = runner.setup.MODEL["size"] + 3 * 1024**3
        self.assertEqual(runner.preflight(gate)["f16KvBudgetBytes"], 805306368)
        self.assertFalse(runner.preflight(gate - 1)["passed"])
        self.assertTrue(runner.preflight(gate)["passed"])
        self.assertFalse(runner.preflight(gate)["sharedGpuMemoryAddsRam"])

    def test_prompt_token_boundaries_reject_7b_double_bos_and_eog(self):
        tokens = [120000, 120044, 8, 9, 120006, 44, 120007, 120029, 120030]
        runner.validate_prompt_tokens(tokens)
        for bad in ([127958, 20, 127962], [120000] + tokens, tokens[:-3] + [120025] + tokens[-3:],
                    tokens[:-1], tokens + [1] * 8192):
            with self.assertRaises(ValueError):
                runner.validate_prompt_tokens(bad)

    def test_eog_exact_native_eos_only(self):
        log = "load: printing all EOG tokens:\nload: - 120025 ('<eos:6124c78e>')\n"
        self.assertEqual(runner.runtime_eog(log)["ids"], [120025])
        for bad in ("", log + "load: - 120026 ('unexpected')\n", log.replace("120025", "3")):
            with self.assertRaises(ValueError):
                runner.runtime_eog(bad)

    def test_prediction_raw_whitespace_and_semantic_warning_preserved(self):
        text = "  숫자 12가 추가되었다.\n"
        record = runner.prediction(self.row(), "prompt", [], [1], self.response(text), 1.5)
        self.assertEqual(record["translation"], text)
        self.assertEqual(record["targetSha256"], runner.common.sha_text(text))
        self.assertEqual(record["status"], "completed")
        self.assertFalse(record["checks"]["numbersPreserved"])
        self.assertTrue(record["outputIntegrityPassed"])
        self.assertFalse(record["postProcessingApplied"])

    def test_limit_preserved_and_bad_settings_eog_rejected(self):
        response = self.response()
        response.update(stop_type="limit", tokens=[3], tokens_predicted=1)
        record = runner.prediction(self.row(), "p", [], [1], response, 1)
        self.assertTrue(record["outputLimitReached"])
        self.assertFalse(record["automaticChecksPassed"])
        self.assertFalse(record["outputIntegrityPassed"])
        for change in ({"tokens": [3, 120026]}, {"stop_type": "word"}, {"tokens_predicted": 3},
                       {"generation_settings": runner.common.SAMPLING}):
            with self.assertRaises(ValueError):
                runner.prediction(self.row(), "p", [], [1], self.response() | change, 1)

    def test_frozen_inputs_are_allowlisted_without_reference(self):
        rows, identity = runner.screen.read_screen(runner.common.ROOT / "content/model-comparison/linguistic-dev-20260910.jsonl")
        self.assertEqual(len(rows), 18)
        self.assertEqual(set(rows[0]), {"id", "source", "context", "domain", "sourceSha256", "contextSha256"})
        runner.screen.assert_input_unchanged(identity)
        terms, _ = runner.common.read_catalog()
        content, _ = runner.common.build_user_prompt(rows[0], "contextual", terms)
        self.assertIn(rows[0]["source"], content)
        self.assertNotIn("criticalPropositions", content)

    def test_partial_original_metadata_and_template_without_model_load(self):
        model = runner.setup.DEST / runner.setup.MODEL["name"]
        partial = model.with_name(model.name + ".part")
        if not model.exists() and not partial.exists():
            self.skipTest("publisher header is not installed")
        template, contract = runner.gguf_contract(model if model.exists() else partial)
        self.assertEqual(contract["runtimeOverrides"], {})
        self.assertEqual(contract["tensorTypes"], {14: 72, 0: 287, 12: 407})
        result = runner.render_prompt(template, "A new functional sample.")
        self.assertTrue(result.startswith(runner.TOKEN_STRINGS[120000]))
        self.assertTrue(result.endswith("<think></think>"))
        self.assertNotIn("\n", result)
        for special in ("<think>", runner.TOKEN_STRINGS[120006], "<eos:6124c78e>"):
            with self.assertRaisesRegex(ValueError, "control_token"):
                runner.render_prompt(template, "Source " + special)

    def test_frozen_dependencies_unchanged(self):
        runner.common.check_identity_unchanged({str(p): sha for p, sha in runner.FROZEN.items()})


if __name__ == "__main__":
    unittest.main()
