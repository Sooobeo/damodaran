"""Owned, offline Hy-MT2 engine prepared for a separately approved app provider.

This module neither registers nor selects an application provider. A caller must
verify its deployment manifest before construction. It reuses the fixed model,
template, sampling and protocol checks of the independent comparison runner.
"""
from __future__ import annotations

import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
import run_hymt as hy
from process_owner import claim_process_owner


class EngineError(ValueError):
    """Only fixed diagnostic codes; never source text, HTTP bodies or API keys."""


class StartupDiagnostics:
    """Drain native logs without printing/saving source or retaining request logs."""

    LIMIT = 2 * 1024 * 1024

    def __init__(self, stream):
        self.stream = stream
        self._lock = threading.Lock()
        self._buffer = bytearray()
        self._collect = True
        self._overflow = False
        self._failed = False
        self._thread = threading.Thread(target=self._drain, name="hymt-private-diagnostics", daemon=True)
        self._thread.start()

    def _drain(self):
        try:
            # Popen uses bufsize=0, so one read returns available pipe bytes.
            while chunk := self.stream.read(65536):
                with self._lock:
                    if self._collect:
                        if len(self._buffer) + len(chunk) > self.LIMIT:
                            self._overflow = True
                        elif not self._overflow:
                            self._buffer.extend(chunk)
        except (OSError, ValueError):
            with self._lock:
                self._failed = True

    def startup_text(self):
        with self._lock:
            if self._overflow or self._failed:
                raise EngineError("startup_diagnostics_unavailable")
            return self._buffer.decode("utf-8", errors="replace")

    def discard(self):
        # Must happen before the first source text is sent to native inference.
        with self._lock:
            self._collect = False
            self._buffer.clear()

    def join(self):
        self._thread.join(timeout=2)


class OwnedHymtEngine:
    def __init__(self, profile):
        if profile not in ("raw", "contextual"):
            raise EngineError("unsupported_profile")
        self.profile = profile
        self.process = None
        self.diagnostics = None
        self.client = None
        self.closed = False
        self._cleanup_complete = False
        self._request_lock = threading.Lock()
        self.installation = hy.verify_installation()
        self.template, self.contract = hy.gguf_contract(hy.DEST / hy.MODEL["name"])
        # A failed ownership claim must never create an orphan native process.
        self.owner = claim_process_owner()
        try:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            key = secrets.token_urlsafe(32)
            self.process = self.owner.spawn(
                hy.server_command(port, key), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
                env=hy.runtime_environment(), cwd=str(hy.DEST / "runtime"))
            self.diagnostics = StartupDiagnostics(self.process.stdout)
            self.client = hy.LocalClient(f"http://127.0.0.1:{port}", key)
            hy.wait_ready(self.client, self.process, time.monotonic())
            # Health can become ready just before the pipe reader consumes the
            # final metadata bytes. Wait briefly for the fixed token evidence.
            deadline = time.monotonic() + 5
            while True:
                try:
                    self.actual_eog = hy.eog_from_log(self.diagnostics.startup_text())
                    break
                except hy.RunError:
                    if time.monotonic() >= deadline:
                        raise EngineError("startup_eog_contract_unavailable")
                    time.sleep(0.02)
            self.actual_tokens = hy.validate_runtime_tokens(self.client)
            props = self.client.request("/props")
            if (Path(props.get("model_path", "")).resolve() != (hy.DEST / hy.MODEL["name"]).resolve()
                    or props.get("chat_template") != self.template
                    or props.get("default_generation_settings", {}).get("n_ctx") != hy.CONTEXT_SIZE
                    or props.get("total_slots") != 1):
                raise EngineError("runtime_contract_mismatch")
            self.diagnostics.discard()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _terms(terms):
        if not isinstance(terms, list) or len(terms) > 100:
            raise EngineError("invalid_terminology")
        clean, identifiers = [], set()
        for term in terms:
            if not isinstance(term, dict):
                raise EngineError("invalid_terminology")
            item = {key: term.get(key) for key in ("id", "source", "target", "definition")}
            if (any(not isinstance(value, str) or not value.strip() or len(value) > 2000 for value in item.values())
                    or item["id"] in identifiers):
                raise EngineError("invalid_terminology")
            aliases = term.get("aliases", [])
            if (not isinstance(aliases, list) or len(aliases) > 30
                    or any(not isinstance(alias, str) or not alias.strip() or len(alias) > 500 for alias in aliases)):
                raise EngineError("invalid_terminology")
            item["aliases"] = list(aliases)
            for value in [item["source"], item["target"], item["definition"], *aliases]:
                hy.reject_special_text(value)
            identifiers.add(item["id"])
            clean.append(item)
        return clean

    def translate(self, source, context, terms):
        if (not isinstance(source, str) or not source.strip() or len(source) > 12000
                or not isinstance(context, str) or len(context) > 12000):
            raise EngineError("invalid_source_or_context")
        hy.reject_special_text(source)
        hy.reject_special_text(context)
        clean_terms = self._terms(terms)
        # The only text/annotation fields accepted by the fixed prompt builder.
        row = {"id": "segment", "source": source, "context": context,
               "sourceSha256": hy.sha_text(source), "contextSha256": hy.sha_text(context)}
        content, matches = hy.build_user_prompt(row, self.profile, clean_terms)
        prompt = hy.render_prompt(self.template, content)
        with self._request_lock:
            if self.closed or self.process.poll() is not None:
                raise EngineError("runtime_stopped")
            self.owner.assert_owned()
            tokens = self.client.request("/tokenize", {"content": prompt, "add_special": False, "parse_special": True})["tokens"]
            hy.validate_prompt_tokens(tokens)
            started = time.monotonic()
            response = self.client.request("/completion", hy.SAMPLING | {"prompt": tokens})
            result = hy.prediction(row, prompt, matches, tokens, response, time.monotonic() - started)
        if (result["emptyOutput"] if "emptyOutput" in result else not result["checks"]["nonEmpty"]):
            raise EngineError("empty_translation")
        if result["truncated"] or result["outputLimitReached"] or not result["checks"]["noLeakedControlTokens"]:
            raise EngineError("incomplete_translation")
        warnings = []
        if not result["checks"]["numbersPreserved"]:
            warnings.append("숫자·부호·백분율의 표기가 원문과 다릅니다. 수량과 단위를 대조하세요.")
        if not result["checks"]["currencySymbolsPreserved"]:
            warnings.append("통화 기호가 원문과 다릅니다. 통화와 금액을 대조하세요.")
        # Raw output is retained, including whitespace; no replacement or repair.
        return {"translatedText": result["translation"], "warnings": warnings}

    def close(self):
        if self._cleanup_complete:
            return
        # Refuse new work immediately, but allow another cleanup attempt if
        # terminating/waiting for the owned native process fails.
        self.closed = True
        if self.diagnostics:
            self.diagnostics.discard()
        try:
            if not hy.stop_process(self.process):
                raise EngineError("native_process_cleanup_incomplete")
        finally:
            if self.process and self.process.stdout:
                self.process.stdout.close()
            if self.diagnostics:
                self.diagnostics.join()
        self._cleanup_complete = True
        # Do not close the Job handle: it also contains this Python process.
        # The module-owned handle is released by the OS after normal return.
