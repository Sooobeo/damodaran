"""Offline fixture tests: no weights, training data, or real inference are used."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import run_hymt as runner


TEMPLATE = "{% set ns = namespace(has_head=true) %}{% set loop_messages = messages %}{% for message in loop_messages %}{% set content = message['content'] %}{% if loop.index0 == 0 %}{% if content == '' %}{% set ns.has_head = false %}{% elif message['role'] == 'system' %}{% set content = '<|startoftext|>' + content + '<|extra_4|>' %}{% endif %}{% endif %}{% if message['role'] == 'user' %}{% if loop.index0 == 1 and ns.has_head %}{% set content = content + '<|extra_0|>' %}{% else %}{% set content = '<|startoftext|>' + content + '<|extra_0|>' %}{% endif %}{% elif message['role'] == 'assistant' %}{% set content = content + '<|eos|>' %}{% endif %}{{ content }}{% endfor %}"
EOG_LOG = "load: printing all EOG tokens:\nload:   - 127957 ('<|endoftext|>')\nload:   - 127960 ('<|eos|>')\nload:   - 127967 ('<|extra_5|>')\n"


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def fixture_dataset(directory):
    directory.mkdir(parents=True)
    rows = [{"id": f"FIXTURE-{i:02}", "split": "exploratory_probe", "source": f"The example contains {i} units.",
             "context": "Read the source carefully.", "target": "ANSWER_SENTINEL", "domain": "DOMAIN_SENTINEL",
             "termTargets": ["TERMS_SENTINEL"], "forbiddenTerms": ["FORBIDDEN_SENTINEL"],
             "criticalChecks": "CHECKS_SENTINEL"} for i in range(24)]
    for row in rows:
        row["sourceSha256"] = runner.sha_text(row["source"])
        row["contextSha256"] = runner.sha_text(row["context"])
    path = directory / "dataset.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    review = directory / "review.json"
    dump(review, {"assistantReviewed": True, "humanReviewed": False})
    manifest = {"version": runner.DATA_VERSION, "status": "frozen", "humanReviewed": False,
                "sourceType": "assistant_authored_unreviewed", "dataset": {
                    "file": path.name, "sha256": runner.digest(path), "count": 24, "ids": [r["id"] for r in rows]},
                "reviewFiles": [{"file": review.name, "sha256": runner.digest(review)}]}
    dump(directory / "dataset-manifest.json", manifest)
    return path, rows, manifest


def term(identity, source, target="예시 용어", aliases=()):
    return {"id": identity, "source": source, "target": target, "aliases": list(aliases),
            "definition": "원문의 금융 뜻에 해당할 때만 사용하는 정의다."}


def good_response(number=0):
    return {"content": f"예시에는 {number}개 단위가 있다.", "tokens_predicted": 3,
            "tokens": [10, 11, 127960], "stop_type": "eos", "stopping_word": "", "truncated": False,
            "generation_settings": copy.deepcopy(runner.SAMPLING),
            "timings": {"predicted_per_second": 1.0}}


class PromptTests(unittest.TestCase):
    def test_raw_ignores_context_and_all_answer_fields(self):
        row = {"source": "A bond is due.", "context": "CONTEXT_SENTINEL", "target": "ANSWER_SENTINEL",
               "domain": "finance", "termTargets": ["TERMS_SENTINEL"], "criticalChecks": "CHECKS_SENTINEL"}
        prompt, matches = runner.build_user_prompt(row, "raw", [term("B", "Bond")])
        self.assertEqual(prompt, runner.RAW_INSTRUCTION + "\nA bond is due.")
        self.assertEqual(matches, [])
        for sentinel in ("CONTEXT_SENTINEL", "ANSWER_SENTINEL", "TERMS_SENTINEL", "CHECKS_SENTINEL"):
            self.assertNotIn(sentinel, prompt)

    def test_longest_source_overlap_acronym_case_and_word_boundaries(self):
        terms = [term("PV", "Present Value", aliases=["PV"]), term("NPV", "Net Present Value"),
                 term("ROE", "Return on Equity", aliases=["ROE"]), term("B", "Bond")]
        matches = runner.term_matches("Net present value differs from present value. ROE is not roe or bonded.", terms)
        self.assertEqual([m["id"] for m in matches], ["NPV", "PV", "ROE"])
        self.assertEqual(matches[0]["source"], "Net present value")

    def test_conditional_hints_only_use_source_not_context(self):
        row = {"source": "Her interest in art increased.", "context": "A bond appears elsewhere.",
               "target": "ANSWER_SENTINEL", "domain": "DOMAIN_SENTINEL", "forbiddenTerms": ["FORBIDDEN_SENTINEL"]}
        prompt, matches = runner.build_user_prompt(row, "contextual", [term("I", "Interest"), term("B", "Bond")])
        self.assertEqual([m["id"] for m in matches], ["I"])
        self.assertIn("ordinary meaning or a different financial meaning", prompt)
        self.assertIn("금융 뜻", prompt)
        self.assertNotIn("ANSWER_SENTINEL", prompt)
        self.assertNotIn("DOMAIN_SENTINEL", prompt)
        self.assertNotIn("FORBIDDEN_SENTINEL", prompt)

    def test_original_template_bos_and_no_implicit_eos(self):
        self.assertEqual(runner.sha_text(TEMPLATE), runner.TEMPLATE_SHA)
        self.assertEqual(runner.render_prompt(TEMPLATE, "Translate $5."), "<|startoftext|>Translate $5.<|extra_0|>")
        with self.assertRaises(runner.RunError):
            runner.render_prompt("{{ content }}", "x")

    def test_numeric_multiset_negation_does_not_change_numbers(self):
        self.assertEqual(runner.numeric_tokens("Not -1.5%, +2% or 1,000."), runner.numeric_tokens("-1.5%, +2%, 1000은 아니다."))
        self.assertNotEqual(runner.numeric_tokens("-1.5% and -1.5%"), runner.numeric_tokens("1.5%"))


class FrozenInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.scope = patch.object(runner, "COMPARISONS", self.root)
        self.scope.start()
        self.path, self.rows, self.manifest = fixture_dataset(self.root / "input")

    def tearDown(self):
        self.scope.stop()
        self.temp.cleanup()

    def test_answer_fields_are_removed_before_prompt_stage(self):
        clean, info = runner.read_frozen_input(self.path)
        self.assertEqual(len(clean), 24)
        self.assertEqual(set(clean[0]), {"id", "source", "context", "sourceSha256", "contextSha256"})
        self.assertEqual(info["ids"], [r["id"] for r in self.rows])

    def test_refuses_changed_file_without_new_freeze(self):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(" ")
        with self.assertRaisesRegex(runner.RunError, "input_manifest_mismatch"):
            runner.read_frozen_input(self.path)

    def test_refuses_changed_source_even_when_dataset_hash_replaced(self):
        self.rows[0]["source"] = "Changed source."
        self.path.write_text("\n".join(json.dumps(r) for r in self.rows), encoding="utf-8")
        self.manifest["dataset"]["sha256"] = runner.digest(self.path)
        dump(self.path.with_name("dataset-manifest.json"), self.manifest)
        with self.assertRaisesRegex(runner.RunError, "source_hash_mismatch"):
            runner.read_frozen_input(self.path)

    def test_refuses_changed_review_evidence(self):
        self.path.with_name("review.json").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(runner.RunError, "review_hash_mismatch"):
            runner.read_frozen_input(self.path)

    def test_refuses_control_token_in_source(self):
        with self.assertRaises(runner.RunError):
            runner.reject_special_text("amount <|eos|> $5")


class RuntimeContractTests(unittest.TestCase):
    def test_eog_override_requires_both_ids_and_no_dollar(self):
        good = runner.eog_from_log(EOG_LOG)
        self.assertEqual(good["additionalIds"], [127957])
        for broken in (EOG_LOG + "load:   - 3 ('$')\n", EOG_LOG.replace("127967", "127966"), ""):
            with self.assertRaises(runner.RunError):
                runner.eog_from_log(broken)

    def test_prompt_token_boundaries_and_context_budget(self):
        runner.validate_prompt_tokens([127958, 3, 1, 127962])
        for tokens in ([127958, 127958, 127962], [127958, 127960, 127962], [127958, 1],
                       [127958] + [1] * 4094 + [127962]):
            with self.assertRaises(runner.RunError):
                runner.validate_prompt_tokens(tokens)

    def test_actual_sampling_drift_is_rejected(self):
        settings = good_response()["generation_settings"]
        runner.validate_generation_settings(settings)
        settings["top_p"] = 0.8
        with self.assertRaisesRegex(runner.RunError, "actual_sampling_mismatch_top_p"):
            runner.validate_generation_settings(settings)

    def test_missing_output_tokens_and_dollar_eos_are_rejected(self):
        row = {"id": "F", "source": "There are 0 units.", "sourceSha256": "s", "contextSha256": "c"}
        for tokens in ([], [10, 11, 3], [10, 11, 12, 127960]):
            response = good_response()
            response["tokens"] = tokens
            with self.assertRaises(runner.RunError):
                runner.prediction(row, "prompt", [], [127958, 1, 127962], response, 1.0)

    def test_identity_uses_bytes_previously_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, manifest, catalog = root / "source", root / "manifest", root / "catalog"
            for file in (source, manifest, catalog):
                file.write_text("original", encoding="utf-8")
            input_info = {"path": str(source), "sha256": runner.digest(source), "manifestPath": str(manifest),
                          "manifestSha256": runner.digest(manifest), "reviewEvidence": []}
            catalog_info = {"path": str(catalog), "sha256": runner.digest(catalog)}
            source.write_text("changed during model integrity check", encoding="utf-8")
            with self.assertRaisesRegex(runner.RunError, "execution_identity_changed"):
                runner.identity_hashes(input_info, catalog_info)

    def test_numbers_limit_empty_and_exact_raw_output_are_recorded(self):
        row = {"id": "F", "source": "There are 5 units.", "sourceSha256": "s", "contextSha256": "c"}
        response = good_response(5)
        response["content"] = "  5개 단위가 있다.\n"
        item = runner.prediction(row, "prompt", [], [127958, 1, 127962], response, 1.0)
        self.assertEqual(item["translation"], response["content"])
        self.assertTrue(item["automaticChecksPassed"])
        response.update(content="", stop_type="limit", truncated=True)
        item = runner.prediction(row, "prompt", [], [127958, 1, 127962], response, 1.0)
        self.assertFalse(item["automaticChecksPassed"])
        self.assertTrue(item["outputLimitReached"])

    def test_fixed_command_cpu_auth_overrides_and_no_sampling_cli(self):
        command = runner.server_command(9999, "SECRET")
        self.assertIn("--no-agent", command)
        self.assertIn("--offline", command)
        self.assertEqual(command[command.index("--gpu-layers") + 1], "0")
        self.assertIn("tokenizer.ggml.eos_token_id=int:127960", command[command.index("--override-kv") + 1])
        with redirect_stdout(io.StringIO()), patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                runner.parser().parse_args(["--input", "a", "--output", "b", "--profile", "raw", "--temperature", "0"])

    def test_inherited_vertex_and_llama_overrides_are_removed(self):
        with patch.dict("os.environ", {"AIP_MODE": "PREDICTION", "AIP_HTTP_PORT": "80", "LLAMA_ARG_PORT": "81",
                                       "GGML_CUDA_DISABLE_GRAPHS": "1", "PATH": "kept"}, clear=True):
            env = runner.runtime_environment()
        self.assertEqual(env, {"PATH": "kept", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})

    def test_own_process_is_killed_after_terminate_timeout(self):
        class Process:
            def __init__(self):
                self.dead, self.killed = False, False
            def poll(self):
                return 1 if self.dead else None
            def terminate(self):
                pass
            def kill(self):
                self.dead, self.killed = True, True
            def wait(self, timeout):
                if not self.dead:
                    raise runner.subprocess.TimeoutExpired("fixture", timeout)
        process = Process()
        self.assertTrue(runner.stop_process(process))
        self.assertTrue(process.killed)

    def test_loopback_auth_proxy_bypass_and_redirect_block(self):
        class Handler(BaseHTTPRequestHandler):
            auth = None
            redirect = False
            def log_message(self, *args):
                pass
            def do_GET(self):
                Handler.auth = self.headers.get("Authorization")
                if Handler.redirect:
                    self.send_response(302)
                    self.send_header("Location", "http://example.invalid/")
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict("os.environ", {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
                client = runner.LocalClient(f"http://127.0.0.1:{server.server_port}", "ephemeral")
                self.assertEqual(client.request("/health", timeout=2), {"status": "ok"})
                self.assertEqual(Handler.auth, "Bearer ephemeral")
                Handler.redirect = True
                with self.assertRaisesRegex(runner.RunError, "loopback_redirect_blocked"):
                    client.request("/health", timeout=2)
            with self.assertRaises(runner.RunError):
                runner.LocalClient("http://example.invalid:123", "key")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class FullFixtureTests(unittest.TestCase):
    def test_partial_failure_preserves_all_ids_stops_child_and_refuses_overwrite(self):
        self.exercise_fixture("timeout")

    def test_response_validation_failure_preserves_translation(self):
        self.exercise_fixture("settings")

    def test_complete_run_preserves_coverage_and_passes_checks(self):
        self.exercise_fixture(None)

    def exercise_fixture(self, failure):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            source, rows, _ = fixture_dataset(root / "input")
            dest = root / "installation"
            (dest / "runtime").mkdir(parents=True)
            (dest / runner.MODEL["name"]).write_bytes(b"fixture, not a model")
            (dest / "runtime/llama-server.exe").write_bytes(b"fixture, never executed")
            dump(dest / "installation-manifest.json", {"fixture": True})
            catalog = root / "catalog.json"
            dump(catalog, {"terms": [term(f"T{i}", f"Special phrase {i}") for i in range(54)]})
            output = root / "output"

            class Process:
                pid = 111
                stopped = False
                def poll(self):
                    return 0 if self.stopped else None
                def terminate(self):
                    self.stopped = True
                def wait(self, timeout):
                    return 0

            process = Process()

            def spawn(command, **kwargs):
                kwargs["stdout"].write(EOG_LOG.encode())
                kwargs["stdout"].flush()
                return process

            class Client:
                completed = 0
                def __init__(self, *args):
                    pass
                def request(self, endpoint, payload=None, timeout=1800):
                    if endpoint == "/health":
                        return {"status": "ok"}
                    if endpoint == "/props":
                        return {"model_path": str(dest / runner.MODEL["name"]), "chat_template": TEMPLATE, "build_info": "fixture",
                                "default_generation_settings": {"n_ctx": 8192}, "total_slots": 1}
                    if endpoint == "/detokenize":
                        return {"content": "$"}
                    if endpoint == "/tokenize":
                        for token_id, text in runner.TOKEN_STRINGS.items():
                            if payload["content"] == text:
                                return {"tokens": [token_id]}
                        return {"tokens": [127958, 1, 127962]}
                    if endpoint == "/completion":
                        if Client.completed == 1 and failure == "timeout":
                            raise TimeoutError("SECRET_PROMPT_MUST_NOT_PRINT")
                        response = good_response(Client.completed)
                        if Client.completed == 1 and failure == "settings":
                            response["generation_settings"]["top_p"] = 0.8
                        Client.completed += 1
                        return response
                    raise AssertionError(endpoint)

            for name, value in (("COMPARISONS", root), ("DEST", dest), ("CATALOG", catalog), ("LocalClient", Client)):
                stack.enter_context(patch.object(runner, name, value))
            stack.enter_context(patch.object(runner, "verify_installation", return_value={"fixture": True}))
            stack.enter_context(patch.object(runner, "gguf_contract", return_value=(TEMPLATE, {"fixture": True})))
            stack.enter_context(patch.object(runner.platform, "platform", return_value="fixture-platform"))
            stack.enter_context(patch.object(runner.platform, "machine", return_value="fixture-machine"))
            stack.enter_context(patch.object(runner.subprocess, "Popen", side_effect=spawn))
            console = stack.enter_context(redirect_stdout(io.StringIO()))
            self.assertEqual(runner.run(source, output, "raw"), 1 if failure else 0)
            self.assertTrue(process.stopped)
            result = [json.loads(line) for line in (output / "predictions.jsonl").read_text("utf-8").splitlines()]
            summary = json.loads((output / "summary.json").read_text("utf-8"))
            self.assertEqual([r["id"] for r in result], [r["id"] for r in rows])
            if failure:
                self.assertEqual([r["status"] for r in result[:3]], ["completed", "failed", "not_run"])
                self.assertEqual(summary["status"], "failed")
                self.assertEqual(summary["completedCount"], 1)
                if failure == "settings":
                    self.assertEqual(result[1]["translation"], good_response(1)["content"])
            else:
                self.assertEqual(summary["status"], "completed")
                self.assertEqual(summary["completedCount"], 24)
                self.assertTrue(summary["automaticChecksPassed"])
            self.assertTrue(summary["childProcessStopped"])
            self.assertNotIn("SECRET_PROMPT_MUST_NOT_PRINT", console.getvalue())
            self.assertEqual(summary["runtimeCommand"][summary["runtimeCommand"].index("--api-key") + 1], "[EPHEMERAL_REDACTED]")
            before = runner.digest(output / "predictions.jsonl")
            with self.assertRaises(FileExistsError):
                runner.run(source, output, "raw")
            self.assertEqual(before, runner.digest(output / "predictions.jsonl"))


if __name__ == "__main__":
    unittest.main()
