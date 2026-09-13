"""CPU-only engine fixtures: no installation hashes, model, server or inference.

The installation/header readers, socket, owner/spawn and HTTP client are all
replaced before constructing the engine. Native child creation is prohibited.
"""
from __future__ import annotations

import copy
import io
import queue
import subprocess
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import engine
from process_owner import ProcessOwnershipError
from test_run_hymt import TEMPLATE, EOG_LOG


class FakePipe:
    def __init__(self, initial=EOG_LOG.encode()):
        self._queue = queue.Queue()
        self._condition = threading.Condition()
        self.closed = False
        self.reads = 0
        self.read_calls = 0
        if initial is not None:
            self.feed(initial)

    def feed(self, data):
        self._queue.put(data)

    def read(self, _size):
        with self._condition:
            self.read_calls += 1
            self._condition.notify_all()
        value = self._queue.get()
        with self._condition:
            self.reads += 1
            self._condition.notify_all()
        if isinstance(value, Exception):
            raise value
        return b"" if value is None else value

    def await_reads(self, count):
        with self._condition:
            if not self._condition.wait_for(lambda: self.reads >= count, timeout=2):
                raise AssertionError("Fixture diagnostic reader did not consume its bytes")

    def await_read_calls(self, count):
        with self._condition:
            if not self._condition.wait_for(lambda: self.read_calls >= count, timeout=2):
                raise AssertionError("Fixture diagnostic reader did not finish its previous chunk")

    def close(self):
        if not self.closed:
            self.closed = True
            self.feed(None)


class FakeProcess:
    def __init__(self, pipe):
        self.stdout = pipe
        self.returncode = None
        self.terminate_calls = self.kill_calls = self.wait_calls = 0
        self.ignore_terminate = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminate_calls += 1
        if not self.ignore_terminate:
            self.returncode = 0

    def kill(self):
        self.kill_calls += 1
        self.returncode = -9

    def wait(self, timeout):
        self.wait_calls += 1
        if self.returncode is None:
            raise subprocess.TimeoutExpired("synthetic child", timeout)
        return self.returncode


class FakeOwner:
    process_id = 12345

    def __init__(self, process):
        self.process = process
        self.spawned = []
        self.owned_checks = 0
        self.owned = True

    def assert_owned(self):
        self.owned_checks += 1
        if not self.owned:
            raise ProcessOwnershipError("fixture_ownership_lost")

    def spawn(self, argv, **kwargs):
        self.assert_owned()
        self.spawned.append((argv, kwargs))
        return self.process

    def close(self):
        raise AssertionError("The owner job handle must not be closed by the engine")


class FakeReservation:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def bind(self, address):
        if address != ("127.0.0.1", 0):
            raise AssertionError("Only a loopback port reservation is expected")

    def getsockname(self):
        return "127.0.0.1", 43210


class FakeClient:
    def __init__(self):
        self.calls = []
        self.on_prompt = None
        self.prompt_tokens = [127958, 20, 127962]
        self.props = {"model_path": str(engine.hy.DEST / engine.hy.MODEL["name"]), "chat_template": TEMPLATE,
                      "default_generation_settings": {"n_ctx": engine.hy.CONTEXT_SIZE}, "total_slots": 1}
        self.response = {"content": "5개 단위를 보존한다.", "tokens_predicted": 2, "tokens": [20, 127960],
                         "stop_type": "eos", "stopping_word": None, "truncated": False,
                         "generation_settings": copy.deepcopy(engine.hy.SAMPLING), "timings": {}}

    def request(self, endpoint, payload=None, timeout=None):
        self.calls.append((endpoint, copy.deepcopy(payload)))
        if endpoint == "/health":
            return {"status": "ok"}
        if endpoint == "/props":
            return copy.deepcopy(self.props)
        if endpoint == "/detokenize":
            if payload != {"tokens": [3]}:
                raise AssertionError("Unexpected fixture token request")
            return {"content": "$"}
        if endpoint == "/tokenize":
            special = {value: key for key, value in engine.hy.TOKEN_STRINGS.items()}
            if payload["content"] in special:
                return {"tokens": [special[payload["content"]]]}
            if self.on_prompt:
                self.on_prompt(payload["content"])
            return {"tokens": list(self.prompt_tokens)}
        if endpoint == "/completion":
            return copy.deepcopy(self.response)
        raise AssertionError("Unexpected fixture HTTP endpoint")


class EngineFixtureTests(unittest.TestCase):
    def setUp(self):
        self.pipe = FakePipe()
        self.process = FakeProcess(self.pipe)
        self.owner = FakeOwner(self.process)
        self.client = FakeClient()
        self.instances = []
        fixtures = ((engine.hy, "verify_installation", {"return_value": {"fixture": True}}),
                    (engine.hy, "gguf_contract", {"return_value": (TEMPLATE, {"fixture": True})}),
                    (engine, "claim_process_owner", {"return_value": self.owner}),
                    (engine.hy, "LocalClient", {"return_value": self.client}),
                    (engine.socket, "socket", {"return_value": FakeReservation()}),
                    (engine.secrets, "token_urlsafe", {"return_value": "FIXTURE_EPHEMERAL_KEY"}),
                    (engine.hy, "digest", {"side_effect": AssertionError("No real asset hashing")}),
                    (engine.subprocess, "Popen", {"side_effect": AssertionError("No real native process")}),
                    (engine.hy.urllib.request, "urlopen", {"side_effect": AssertionError("No real HTTP")}) )
        self.mocks = {}
        for obj, name, settings in fixtures:
            patcher = patch.object(obj, name, **settings)
            self.mocks[name] = patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for instance in self.instances:
            instance.close()
        self.pipe.close()

    def construct(self, profile="contextual"):
        value = engine.OwnedHymtEngine(profile)
        self.instances.append(value)
        return value

    def completion_calls(self):
        return [payload for endpoint, payload in self.client.calls if endpoint == "/completion"]

    def test_startup_uses_owned_spawn_and_validates_fixed_runtime_before_discard(self):
        value = self.construct()
        self.assertEqual(value.actual_eog["ids"], [127957, 127960, 127967])
        self.assertEqual(value.actual_tokens["3"], "$")
        self.assertFalse(value.diagnostics._collect)
        self.assertEqual(value.diagnostics.startup_text(), "")
        self.assertEqual(len(self.owner.spawned), 1)
        argv, options = self.owner.spawned[0]
        self.assertIn("--offline", argv)
        self.assertEqual(argv[argv.index("--host") + 1], "127.0.0.1")
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertEqual(options["stdout"], subprocess.PIPE)
        self.assertEqual(options["stderr"], subprocess.STDOUT)
        self.assertEqual(options["bufsize"], 0)
        self.mocks["verify_installation"].assert_called_once_with()
        self.mocks["gguf_contract"].assert_called_once()
        self.mocks["Popen"].assert_not_called()
        self.mocks["digest"].assert_not_called()
        self.mocks["urlopen"].assert_not_called()
        self.assertFalse(self.completion_calls())

    def test_claim_failure_never_spawns_a_native_process(self):
        self.mocks["claim_process_owner"].side_effect = ProcessOwnershipError("fixture_claim_failed")
        with self.assertRaisesRegex(ProcessOwnershipError, "fixture_claim_failed"):
            self.construct()
        self.assertEqual(self.owner.spawned, [])
        self.assertEqual(self.process.terminate_calls, 0)

    def test_unsupported_profile_is_rejected_before_installation_or_ownership(self):
        with self.assertRaisesRegex(engine.EngineError, "unsupported_profile"):
            self.construct("unregistered-profile")
        self.mocks["verify_installation"].assert_not_called()
        self.mocks["claim_process_owner"].assert_not_called()
        self.assertEqual(self.owner.spawned, [])

    def test_spawn_failure_has_no_native_process_or_http_request(self):
        with patch.object(self.owner, "spawn", side_effect=OSError("synthetic spawn failure")):
            with self.assertRaises(OSError):
                self.construct()
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.process.terminate_calls, 0)

    def test_startup_health_failure_terminates_owned_process_and_discards_diagnostics(self):
        observed, original = [], engine.StartupDiagnostics
        def diagnostics(stream):
            value = original(stream)
            observed.append(value)
            return value
        with patch.object(engine, "StartupDiagnostics", side_effect=diagnostics), \
             patch.object(engine.hy, "wait_ready", side_effect=engine.hy.RunError("runtime_startup_timeout")):
            with self.assertRaisesRegex(engine.hy.RunError, "runtime_startup_timeout"):
                self.construct()
        self.assertEqual(self.process.terminate_calls, 1)
        self.assertTrue(self.pipe.closed)
        self.assertIsNotNone(self.process.poll())
        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0]._collect)
        self.assertEqual(observed[0]._buffer, bytearray())
        self.assertFalse(observed[0]._thread.is_alive())

    def test_startup_interrupt_also_cleans_the_owned_process(self):
        with patch.object(engine.hy, "wait_ready", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.construct()
        self.assertEqual(self.process.terminate_calls, 1)
        self.assertTrue(self.pipe.closed)

    def test_missing_eog_evidence_fails_closed_without_waiting_five_real_seconds(self):
        with patch.object(engine.hy, "wait_ready"), \
             patch.object(engine.hy, "eog_from_log", side_effect=engine.hy.RunError("missing_runtime_eog_evidence")), \
             patch.object(engine.time, "monotonic", side_effect=[0, 1, 7]):
            with self.assertRaisesRegex(engine.EngineError, "startup_eog_contract_unavailable"):
                self.construct()
        self.assertEqual(self.process.terminate_calls, 1)
        self.assertTrue(self.pipe.closed)

    def test_runtime_properties_mismatch_is_rejected_and_cleaned(self):
        self.client.props["default_generation_settings"]["n_ctx"] = 1024
        with self.assertRaisesRegex(engine.EngineError, "runtime_contract_mismatch"):
            self.construct()
        self.assertEqual(self.process.terminate_calls, 1)
        self.assertTrue(self.pipe.closed)

    def test_full_source_and_context_reach_prompt_but_extra_term_annotations_do_not(self):
        value = self.construct()
        source = "Do not change the discount rate. Preserve 5 units in the second sentence."
        context = "The finance committee is considering the same scenario."
        terms = [{"id": "fixture-term", "source": "discount rate", "target": "할인율", "definition": "미래 금액의 현재가치를 구할 때 쓰는 비율",
                  "aliases": [], "assistantReference": "REFERENCE_SENTINEL", "criticalChecks": "CHECKS_SENTINEL",
                  "termTargets": ["TARGETS_SENTINEL"]}]
        def inspect(prompt):
            self.assertFalse(value.diagnostics._collect)
            self.assertEqual(value.diagnostics.startup_text(), "")
            self.assertIn(source, prompt)
            self.assertIn(context, prompt)
            self.assertIn("discount rate → 할인율", prompt)
            self.assertFalse(any(sentinel in prompt for sentinel in ("REFERENCE_SENTINEL", "CHECKS_SENTINEL", "TARGETS_SENTINEL")))
        self.client.on_prompt = inspect
        value.translate(source, context, terms)
        calls = self.completion_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], engine.hy.SAMPLING | {"prompt": self.client.prompt_tokens})

    def test_raw_profile_passes_the_whole_source_without_context_or_glossary(self):
        value = self.construct("raw")
        source = "Keep 5 units. Do not translate the other document."
        def inspect(prompt):
            self.assertIn(source, prompt)
            self.assertNotIn("CONTEXT_SENTINEL", prompt)
            self.assertNotIn("TERM_SENTINEL", prompt)
        self.client.on_prompt = inspect
        value.translate(source, "CONTEXT_SENTINEL", [{"id": "fixture", "source": "units", "target": "TERM_SENTINEL", "definition": "fixture"}])
        self.assertEqual(len(self.completion_calls()), 1)

    def test_raw_translation_keeps_whitespace_and_does_not_repair_numbers_or_currency(self):
        value = self.construct()
        text = "  6개 단위가 있다.\n"
        self.client.response["content"] = text
        result = value.translate("Keep $5 units.", "", [])
        self.assertEqual(result["translatedText"], text)
        self.assertEqual(len(result["warnings"]), 2)
        self.assertIn("숫자", result["warnings"][0])
        self.assertIn("통화", result["warnings"][1])
        self.assertEqual(len(self.completion_calls()), 1)

    def test_preserved_numbers_and_currency_have_no_warnings(self):
        value = self.construct()
        self.client.response["content"] = "$5와 10%를 보존한다."
        result = value.translate("Keep $5 and 10 percent.", "", [])
        self.assertEqual(result["warnings"], [])

    def test_empty_truncated_capped_and_control_token_outputs_are_rejected_without_retry(self):
        value = self.construct()
        original = copy.deepcopy(self.client.response)
        for changes, message in (({"content": " \n "}, "empty_translation"), ({"truncated": True}, "incomplete_translation"),
                                 ({"stop_type": "limit"}, "incomplete_translation"),
                                 ({"content": "누출 <|eos|>"}, "incomplete_translation")):
            with self.subTest(changes=changes):
                self.client.response = original | changes
                before = len(self.completion_calls())
                with self.assertRaisesRegex(engine.EngineError, message):
                    value.translate("Keep 5 units.", "", [])
                self.assertEqual(len(self.completion_calls()), before + 1)

    def test_wrong_response_token_count_sampling_or_unicode_is_rejected(self):
        value = self.construct()
        original = copy.deepcopy(self.client.response)
        variants = [{"tokens_predicted": 3}, {"generation_settings": {**engine.hy.SAMPLING, "seed": 999}},
                    {"content": "bad \ufffd text"}, {"tokens": [20, 3]}]
        for changes in variants:
            with self.subTest(changes=changes):
                self.client.response = original | changes
                with self.assertRaises(engine.hy.RunError):
                    value.translate("Keep 5 units.", "", [])

    def test_source_context_and_terminology_validation_precedes_any_prompt_request(self):
        value = self.construct()
        baseline = len(self.client.calls)
        for source, context, terms in (("", "", []), (" " * 12000 + "x", "", []), ("x", "x" * 12001, []),
                                       (None, "", []), ("x", None, []), ("x", "", None), ("x", "", [{}]),
                                       ("x", "", [{"id": "a", "source": "a", "target": "b", "definition": "c", "aliases": [None]}])):
            with self.subTest(source_type=type(source).__name__, context_type=type(context).__name__):
                with self.assertRaises(engine.EngineError):
                    value.translate(source, context, terms)
        self.assertEqual(len(self.client.calls), baseline)

    def test_special_tokens_in_source_context_or_terms_are_rejected_before_requests(self):
        value = self.construct()
        baseline = len(self.client.calls)
        for source, context, terms in (("x <|eos|>", "", []), ("x", "<|extra_0|>", []),
                                      ("x", "", [{"id": "a", "source": "x", "target": "<|eos|>", "definition": "fixture"}])):
            with self.assertRaises(engine.hy.RunError):
                value.translate(source, context, terms)
        self.assertEqual(len(self.client.calls), baseline)

    def test_invalid_or_over_budget_prompt_tokens_never_reach_completion(self):
        value = self.construct()
        for tokens in ([127958, 127960, 127962], [127958] + [20] * 4094 + [127962]):
            self.client.prompt_tokens = tokens
            with self.assertRaises(engine.hy.RunError):
                value.translate("Keep 5 units.", "", [])
        self.assertEqual(self.completion_calls(), [])

    def test_ownership_is_checked_for_each_translation_and_loss_blocks_requests(self):
        value = self.construct()
        initial = self.owner.owned_checks
        value.translate("Keep 5 units.", "", [])
        self.assertEqual(self.owner.owned_checks, initial + 1)
        baseline = len(self.client.calls)
        self.owner.owned = False
        with self.assertRaises(ProcessOwnershipError):
            value.translate("Keep 5 units.", "", [])
        self.assertEqual(len(self.client.calls), baseline)

    def test_closed_or_dead_engine_refuses_requests(self):
        value = self.construct()
        baseline = len(self.client.calls)
        self.process.returncode = 1
        with self.assertRaisesRegex(engine.EngineError, "runtime_stopped"):
            value.translate("Keep 5 units.", "", [])
        value.close()
        with self.assertRaisesRegex(engine.EngineError, "runtime_stopped"):
            value.translate("Keep 5 units.", "", [])
        self.assertEqual(len(self.client.calls), baseline)

    def test_native_request_logs_are_drained_but_not_retained_or_printed(self):
        output, error = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            value = self.construct()
            consumed = self.pipe.reads
            self.pipe.feed(b"PRIVATE_SOURCE_SENTINEL PRIVATE_RESPONSE_SENTINEL FIXTURE_EPHEMERAL_KEY\n")
            self.pipe.await_reads(consumed + 1)
            self.assertEqual(value.diagnostics.startup_text(), "")
            self.assertEqual(value.diagnostics._buffer, bytearray())
            value.close()
            self.assertFalse(value.diagnostics._thread.is_alive())
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(error.getvalue(), "")

    def test_close_terminates_waits_closes_pipe_and_is_idempotent_after_success(self):
        value = self.construct()
        value.close()
        self.assertTrue(value.closed)
        self.assertTrue(value._cleanup_complete)
        self.assertTrue(self.pipe.closed)
        self.assertFalse(value.diagnostics._thread.is_alive())
        self.assertEqual((self.process.terminate_calls, self.process.wait_calls), (1, 1))
        value.close()
        self.assertEqual((self.process.terminate_calls, self.process.wait_calls), (1, 1))

    def test_failed_native_cleanup_can_be_retried_while_new_translation_stays_blocked(self):
        value = self.construct()
        with patch.object(engine.hy, "stop_process", return_value=False):
            with self.assertRaisesRegex(engine.EngineError, "native_process_cleanup_incomplete"):
                value.close()
        self.assertTrue(value.closed)
        self.assertFalse(value._cleanup_complete)
        with self.assertRaisesRegex(engine.EngineError, "runtime_stopped"):
            value.translate("Keep 5 units.", "", [])
        value.close()
        self.assertTrue(value._cleanup_complete)
        self.assertEqual(self.process.terminate_calls, 1)

    def test_owned_process_that_ignores_termination_is_killed_and_waited(self):
        value = self.construct()
        self.process.ignore_terminate = True
        value.close()
        self.assertEqual((self.process.terminate_calls, self.process.kill_calls, self.process.wait_calls), (1, 1, 2))
        self.assertTrue(value._cleanup_complete)


class StartupDiagnosticsTests(unittest.TestCase):
    def make_diagnostics(self, initial):
        pipe = FakePipe(initial)
        value = engine.StartupDiagnostics(pipe)
        self.addCleanup(value.join)
        self.addCleanup(pipe.close)
        self.addCleanup(value.discard)
        return pipe, value

    def test_startup_bytes_are_available_then_erased_before_later_stream_bytes(self):
        pipe, value = self.make_diagnostics(b"fixed startup metadata")
        # Starting the next read proves that processing of the prior chunk,
        # including the diagnostic buffer lock, has finished.
        pipe.await_read_calls(2)
        self.assertEqual(value.startup_text(), "fixed startup metadata")
        value.discard()
        pipe.feed(b"private later request")
        pipe.await_read_calls(3)
        self.assertEqual(value.startup_text(), "")

    def test_overflow_is_bounded_and_cannot_be_used_as_startup_evidence(self):
        pipe, value = self.make_diagnostics(b"x" * (engine.StartupDiagnostics.LIMIT + 1))
        pipe.await_reads(1)
        # Ensure the drain has applied the overflow flag before inspection.
        pipe.close()
        value.join()
        self.assertLessEqual(len(value._buffer), engine.StartupDiagnostics.LIMIT)
        with self.assertRaisesRegex(engine.EngineError, "startup_diagnostics_unavailable"):
            value.startup_text()

    def test_reader_failure_prevents_startup_evidence(self):
        pipe, value = self.make_diagnostics(OSError("synthetic pipe failure"))
        value.join()
        with self.assertRaisesRegex(engine.EngineError, "startup_diagnostics_unavailable"):
            value.startup_text()


if __name__ == "__main__":
    unittest.main()
