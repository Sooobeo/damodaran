"""S4 producer and loopback evidence tests; never starts a model/native process."""
from contextlib import ExitStack, contextmanager, redirect_stdout
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from input_execution_v1 import producer as p, run_io as r
from test_runtime_contract import row as fixture_row, response as fixture_response


@contextmanager
def local_server(body=b'{ "status": "ok" }\n', *, status=200, declared_length=None,
                 pause_after=None, chunked=False):
    """Tiny test-only HTTP server, bound exclusively to 127.0.0.1."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            request_body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.append({"path": self.path, "body": request_body,
                             "authorization": self.headers.get("Authorization")})
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "close")
            if chunked:
                self.send_header("Transfer-Encoding", "chunked")
            else:
                self.send_header("Content-Length", str(len(body) if declared_length is None else declared_length))
            if status == 302:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
                if pause_after is not None:
                    time.sleep(pause_after)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            self.close_connection = True

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


class IsolatedArtifacts(unittest.TestCase):
    def setUp(self):
        actual_root = p.ROOT.resolve()
        target = actual_root / ".training/verifications"
        target.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="s4-producer-test-", dir=target)
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(target.resolve()))
        self.base = self.root / "s4-generation"
        self.output = self.base / "attempt-001"
        self.output.mkdir(parents=True)
        self.patches = ExitStack()
        self.patches.enter_context(patch.object(r, "ROOT", self.root))
        self.patches.enter_context(patch.object(r, "BASE", self.base))
        self.patches.enter_context(patch.object(p, "ROOT", self.root))
        self.patches.enter_context(patch.object(p, "BASE", self.base))

    def tearDown(self):
        self.patches.close()
        # Target was resolved/verified under .training/verifications above.
        self.temporary.cleanup()

    def bodies(self):
        return list((self.output / "http").glob("*.response.bin"))

    def summary(self):
        return json.loads((self.output / "summary.json").read_text("utf-8"))


class LoopbackEvidenceTests(IsolatedArtifacts):
    def test_exact_http_bytes_and_secret_free_artifacts(self):
        body = b'{ "tokens" : [1, 2] }\r\n'
        secret = "test-ephemeral-secret-never-write"
        with local_server(body) as (base, requests):
            client = r.LocalClient(base, secret, self.output)
            self.assertEqual(client.request("/tokenize", {"content": "x"}), {"tokens": [1, 2]})
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["authorization"], "Bearer " + secret)
        self.assertEqual(self.bodies()[0].read_bytes(), body)
        self.assertTrue(client.last["responseCompleteWithinLimit"])
        for file in self.output.rglob("*"):
            if file.is_file():
                self.assertNotIn(secret.encode(), file.read_bytes())

    def test_invalid_json_is_preserved_without_retry(self):
        body = b'{"tokens": [1,]}'
        with local_server(body) as (base, requests):
            client = r.LocalClient(base, "test", self.output)
            with self.assertRaises(Exception):
                client.request("/tokenize", {"content": "x"})
            self.assertEqual(len(requests), 1)
        self.assertEqual(self.bodies()[0].read_bytes(), body)
        self.assertIsNotNone(client.last["transportOrProtocolErrorType"])

    def test_invalid_utf8_is_preserved(self):
        body = b'{"content":"\xff"}'
        with local_server(body) as (base, _):
            client = r.LocalClient(base, "test", self.output)
            with self.assertRaises(Exception):
                client.request("/detokenize", {"tokens": [1]})
        self.assertEqual(self.bodies()[0].read_bytes(), body)

    def test_completed_json_with_short_content_length_is_rejected(self):
        body = b'{"tokens":[1]}'
        with local_server(body, declared_length=len(body) + 9) as (base, _):
            client = r.LocalClient(base, "test", self.output)
            with self.assertRaises(Exception):
                client.request("/tokenize", {"content": "x"})
        self.assertEqual(self.bodies()[0].read_bytes(), body)
        self.assertFalse(client.last["responseCompleteWithinLimit"])

    def test_timeout_keeps_received_prefix(self):
        body = b'{"content":'
        with local_server(body, declared_length=100, pause_after=0.3) as (base, _):
            client = r.LocalClient(base, "test", self.output)
            with self.assertRaises(Exception):
                client.request("/completion", {"prompt": [1]}, timeout=0.05)
        self.assertEqual(self.bodies()[0].read_bytes(), body)
        self.assertFalse(client.last["responseCompleteWithinLimit"])
        self.assertEqual(client.completions, 1)

    def test_chunked_incomplete_read_preserves_decoded_prefix(self):
        decoded = b'{"content":'
        chunks = f"{len(decoded):x}\r\n".encode() + decoded + b"\r\n"
        with local_server(chunks, chunked=True) as (base, _):
            client = r.LocalClient(base, "test", self.output)
            with self.assertRaises(Exception):
                client.request("/completion", {"prompt": [1]})
        self.assertEqual(self.bodies()[0].read_bytes(), decoded)
        self.assertFalse(client.last["responseCompleteWithinLimit"])

    def test_redirect_body_preserved_and_never_followed(self):
        body = b'{"redirect":"disabled"}'
        with local_server(body, status=302) as (base, requests):
            client = r.LocalClient(base, "test", self.output)
            with self.assertRaises(Exception):
                client.request("/props")
            self.assertEqual([request["path"] for request in requests], ["/props"])
        self.assertEqual(self.bodies()[0].read_bytes(), body)
        self.assertEqual(client.last["httpStatus"], 302)

    def test_oversize_response_is_bounded_and_preserved(self):
        body = b"x" * 100
        with patch.object(r, "MAX_RESPONSE_BYTES", 64), local_server(body) as (base, _):
            client = r.LocalClient(base, "test", self.output)
            with self.assertRaises(Exception):
                client.request("/props")
        self.assertEqual(self.bodies()[0].read_bytes(), body[:65])
        self.assertFalse(client.last["responseCompleteWithinLimit"])

    def test_disallowed_endpoint_or_remote_base_never_sent(self):
        for base in ("https://127.0.0.1:80", "http://localhost:80", "http://example.com:80",
                     "http://127.0.0.1:80/", "http://127.0.0.1:80?query=x"):
            with self.subTest(base=base), self.assertRaises(r.RunError):
                r.LocalClient(base, "test", self.output)
        with local_server() as (base, requests):
            client = r.LocalClient(base, "test", self.output)
            with self.assertRaises(r.RunError):
                client.request("/v1/chat/completions")
            self.assertEqual(requests, [])

    def test_prior_completion_intent_blocks_new_generation(self):
        r.write_json(self.output / "http/00001-completion.request.json", {"fixture": "intent only"})
        with self.assertRaisesRegex(r.RunError, "prior_completion"):
            p.existing_generations()

    def test_prior_read_only_requests_do_not_block_generation(self):
        r.write_json(self.output / "http/00001-tokenize.request.json", {"fixture": "read only"})
        p.existing_generations()


class FrozenInputBindingTests(IsolatedArtifacts):
    def prepared_fixture(self):
        prepared = self.root / "prepared-fixture"
        prepared.mkdir()
        rows = []
        for number in range(1, 17):
            for configuration in ("C0", "C1", "C2", "C3"):
                rows.append(fixture_row() | {"id": f"X{number:02d}", "configuration": configuration})
        raw = b"".join(r.packed(row) for row in rows)
        (prepared / "prompts.jsonl").write_bytes(raw)
        manifest = {"artifacts": [{"path": "prompts.jsonl", "sha256": r.sha(raw)}], "code": []}
        manifest_raw = r.packed(manifest)
        (prepared / "manifest.json").write_bytes(manifest_raw)
        self.patches.enter_context(patch.object(p, "PREPARED", prepared))
        self.patches.enter_context(patch.object(p, "PREPARED_MANIFEST_SHA", r.sha(manifest_raw)))
        return prepared, rows

    def test_self_consistent_prompt_replaced_during_replay_is_rejected(self):
        prepared, rows = self.prepared_fixture()
        with patch.object(p.s2, "verify", return_value={"status": "verified"}):
            self.assertEqual(p.load_fixed_inputs()[0], rows)
        changed = deepcopy(rows)
        for key in ("source", "userPrompt", "prompt"):
            changed[0][key] = changed[0][key].replace("$20", "$21")
        changed[0]["sourceSha256"] = p.runtime.sha_text(changed[0]["source"])
        changed[0]["promptSha256"] = p.runtime.sha_text(changed[0]["prompt"])
        # Internal row hashes alone are self-consistent; the frozen artifact
        # binding must reject replacement between replay and the source read.
        p.runtime.validate_prompt_row(changed[0])

        def replace_after_replay():
            (prepared / "prompts.jsonl").write_bytes(b"".join(r.packed(row) for row in changed))
            return {"status": "verified"}

        with patch.object(p.s2, "verify", side_effect=replace_after_replay):
            with self.assertRaisesRegex(r.RunError, "s2_prompt_artifact_changed"):
                p.load_fixed_inputs()

    def test_identity_snapshot_cannot_approve_changed_frozen_artifact(self):
        prepared, _ = self.prepared_fixture()
        audit = self.root / "audit-fixture.json"
        audit_raw = r.packed({"files": [], "registeredIdentity": {"fixture": True}})
        audit.write_bytes(audit_raw)
        frozen = self.root / "content/model-comparison/input-preparation-v1/freeze-manifest.json"
        frozen.parent.mkdir(parents=True)
        frozen_raw = r.packed({"artifacts": []})
        frozen.write_bytes(frozen_raw)
        self.patches.enter_context(patch.object(p, "AUDIT", audit))
        self.patches.enter_context(patch.object(p, "AUDIT_SHA", r.sha(audit_raw)))
        self.patches.enter_context(patch.object(p, "S1_FREEZE_SHA", r.sha(frozen_raw)))

        def simulated_snapshot(path):
            path = Path(path)
            if path.is_relative_to(self.root):
                relative = path.relative_to(self.root).as_posix()
            else:
                relative = "fixture-code/" + path.name
            digest = r.file_hash(path) if path.is_file() else "a" * 64
            if path == prepared / "prompts.jsonl":
                digest = "0" * 64  # Changed after the immutable manifests were read.
            return {"path": relative, "sha256": digest, "bytes": 1, "mtimeNs": 1, "inode": 1}

        with patch.object(p, "identity", side_effect=simulated_snapshot):
            with self.assertRaisesRegex(r.RunError, "frozen_input_changed_during_snapshot"):
                p.build_identity()


class ProducerLifecycleTests(IsolatedArtifacts):
    def execute_fake(self, *, parity_failure=False, completion_failure=False, corrupt_numeric=False):
        events = []
        rows = []
        for number in range(1, 17):
            for config in ("C0", "C1", "C2", "C3"):
                value = fixture_row()
                value.update(id=f"X{number:02d}", configuration=config)
                value["context"]["policyVersion"] = "fixture-context"
                value["terminology"]["selectorVersion"] = "fixture-terms"
                rows.append(value)

        class Scope:
            def __init__(self, name):
                self.name, self.receipt = name, {"fixture": name}

            def __enter__(self):
                events.append(self.name + "-enter")
                return self

            def __exit__(self, *args):
                events.append(self.name + "-exit")

        class Process:
            pid = 987654
            stopped = False

            def poll(self):
                return 0 if self.stopped else None

        process = Process()

        class Monitor:
            def __init__(self, *args, **kwargs):
                pass

            def start(self):
                events.append("monitor-start")

            def ready(self):
                pass

            def check(self):
                pass

            def begin_request(self):
                events.append("begin-request")

            def end_request(self):
                events.append("end-request")

            def stop(self):
                events.append("monitor-stop")

            def summary(self):
                return {"fixture": True}

        def stop_owned(child, owner):
            if not child.stopped:
                events.append("native-stop")
                child.stopped = True
            return {"stopped": True}

        fake_guard = SimpleNamespace(
            PROFILE={"preflightSeparationSeconds": 0}, ResourceGuardError=p.guard.ResourceGuardError,
            ExperimentLock=lambda: Scope("lock"), PowerRequest=lambda: Scope("power"),
            preflight=lambda **kwargs: {"passed": True}, require_preflight=lambda value: None,
            spawn_guarded=lambda *args: (process, object(), {"fixture": True}),
            ResourceMonitor=Monitor, stop_owned=stop_owned)
        state = {"templates": 0, "tokenizes": 0, "completions": 0}

        class Client:
            def __init__(self, base, key, output):
                self.last, self.completions = None, 0

            def request(self, endpoint, payload=None, timeout=15):
                events.append(endpoint)
                if endpoint == "/health":
                    return {"status": "ok"}
                if endpoint == "/props":
                    return {}
                if endpoint == "/apply-template":
                    state["templates"] += 1
                    if parity_failure and state["templates"] == 64:
                        return {"prompt": "mismatch"}
                    return {"prompt": "<|startoftext|>" + payload["messages"][0]["content"] + "<|extra_0|>"}
                if endpoint == "/tokenize":
                    state["tokenizes"] += 1
                    return {"tokens": fixture_row()["tokenIds"]}
                if endpoint == "/completion":
                    self.assert_parity()
                    state["completions"] += 1
                    self.completions += 1
                    self.last = {"rawResponsePath": "fixture.raw", "rawResponseSha256": "f" * 64}
                    if completion_failure:
                        raise r.RunError("fixture_completion_failed")
                    value = fixture_response()
                    if corrupt_numeric:
                        value["content"] = "수수료는 25유로이다."
                    return value
                raise AssertionError(endpoint)

            @staticmethod
            def assert_parity():
                if state["templates"] != 64 or state["tokenizes"] != 64:
                    raise AssertionError("completion before all 64 input parity checks")

        plan = {"inputFiles": []}
        with ExitStack() as stack:
            stack.enter_context(patch.object(p, "guard", fake_guard))
            stack.enter_context(patch.object(p, "LocalClient", Client))
            stack.enter_context(patch.object(p, "claim_process_owner", return_value=SimpleNamespace(assert_owned=lambda: None)))
            stack.enter_context(patch.object(p, "check_files", side_effect=lambda *args, **kwargs: events.append("check-files")))
            stack.enter_context(patch.object(p.common, "gguf_contract", return_value=("template-fixture", {})))
            stack.enter_context(patch.object(p.common, "server_command", side_effect=lambda port, key: ["fixture.exe", "--api-key", key]))
            stack.enter_context(patch.object(p.common, "validate_runtime_tokens", return_value={"fixture": True}))
            stack.enter_context(patch.object(p.common, "eog_from_log", return_value={"fixture": True}))
            stack.enter_context(patch.object(p.runtime, "validate_props", return_value={"fixture": True}))
            stack.enter_context(redirect_stdout(io.StringIO()))
            result = p.execute(rows, plan, self.output)
        return result, self.summary(), events, state

    def test_all_64_parity_before_first_completion_and_normal_cleanup(self):
        result, summary, events, state = self.execute_fake()
        self.assertEqual(result, 0)
        self.assertEqual(state["completions"], 64)
        self.assertEqual(summary["completedOutputs"], 64)
        self.assertEqual(summary["completionRequestsSent"], 64)
        self.assertEqual(summary["missingOutputs"], 0)
        self.assertLess(events.index("native-stop"), events.index("power-exit"))
        self.assertLess(events.index("power-exit"), events.index("lock-exit"))

    def test_last_parity_failure_sends_zero_completions_and_stops_before_power_release(self):
        result, summary, events, state = self.execute_fake(parity_failure=True)
        self.assertEqual(result, 1)
        self.assertEqual(state["completions"], 0)
        self.assertEqual(summary["missingOutputs"], 64)
        self.assertTrue(summary["childProcessStopped"])
        self.assertLess(events.index("native-stop"), events.index("power-exit"))

    def test_completion_exception_preserves_failure_intent_and_all_missing_rows(self):
        result, summary, events, state = self.execute_fake(completion_failure=True)
        self.assertEqual(result, 1)
        self.assertEqual(state["completions"], 1)
        self.assertEqual(summary["completionRequestsSent"], 1)
        self.assertEqual(summary["completedOutputs"], 0)
        self.assertEqual(summary["missingOutputs"], 64)
        self.assertTrue((self.output / "failed-item.json").is_file())
        self.assertIn('"status":"request_pending"', (self.output / "attempt-events.jsonl").read_text("utf-8"))
        self.assertLess(events.index("native-stop"), events.index("power-exit"))

    def test_quality_failures_do_not_regenerate_or_hide_completed_outputs(self):
        result, summary, _, state = self.execute_fake(corrupt_numeric=True)
        self.assertEqual(result, 0)
        self.assertEqual(state["completions"], 64)
        self.assertEqual(len(summary["automaticCheckFailureRows"]), 64)
        self.assertEqual(summary["completedOutputs"], 64)


if __name__ == "__main__":
    unittest.main()
