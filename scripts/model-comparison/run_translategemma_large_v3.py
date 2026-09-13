"""TranslateGemma 27B v3: bounded-working-set paging experiment, not a safety guarantee."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error

import jinja2
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

import linguistic_screen as screen
import setup_translategemma27 as setup
import run_hymt as transport
import working_set_limit
from working_set_limit import WorkingSetLimit
import suspended_process_owner
from suspended_process_owner import SuspendedProcessOwner

ROOT = setup.ROOT
sys.path.insert(0, str(ROOT / "scripts/local-hymt"))
from process_owner import claim_process_owner
from engine import StartupDiagnostics
import process_owner
import engine

CONTEXT_SIZE = 2048
MAX_NEW_TOKENS = 768
CONTROL = re.compile(r"<\|[^\n>]*\|>|</s>|<(?:bos|eos|pad|unk|mask|start_of_[^\n>]*|end_of_[^\n>]*|image|audio|video|unused[0-9]+)>")
RUNTIME_TOKEN_STRINGS = {**setup.TOKEN_STRINGS, 212: "</s>"}
ORIGINAL_EOG_IDS = {1, 106}
RUNTIME_EOG = {1: "<eos>", 106: "<end_of_turn>", 212: "</s>"}
# Confirmed fixed GGUF metadata; all-layer KV estimate deliberately ignores SWA savings.
MEMORY_SHAPES = {"12b": (48, 8, 256, 256), "27b": (62, 16, 128, 128)}
GIB = 1024 ** 3
MAXIMUM_WORKING_SET = 12 * GIB
REQUESTED_MAXIMUM_WORKING_SET = MAXIMUM_WORKING_SET - 64 * 1024 ** 2
MINIMUM_WORKING_SET = 1024 ** 2
MINIMUM_FREE_MEMORY = 512 * 1024 ** 2
MEMORY_LOW_SECONDS = 3
MONITOR_INTERVAL = 0.5
SAMPLE_RECORD_INTERVAL = 5
STARTUP_SECONDS = 1800
REQUEST_SECONDS = 1800
TOTAL_SECONDS = 12 * 60 * 60
SETTINGS = {"n_predict": MAX_NEW_TOKENS, "temperature": 0.0, "seed": 20260910,
            "repeat_penalty": 1.0, "repeat_last_n": 0, "top_k": 0, "top_p": 1.0, "min_p": 0.0,
            "samplers": ["temperature"], "stop": [], "ignore_eos": False, "cache_prompt": False,
            "stream": False, "return_tokens": True, "n_keep": 0, "id_slot": 0,
            "presence_penalty": 0.0, "frequency_penalty": 0.0, "dry_multiplier": 0.0,
            "mirostat": 0, "dynatemp_range": 0.0, "typical_p": 1.0, "xtc_probability": 0.0,
            "top_n_sigma": -1.0, "logit_bias": []}


class RunError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise RunError(code)


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_json(value):
    return sha_text(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def reject_control(text):
    require(isinstance(text, str) and "\x00" not in text and "\ufffd" not in text and not CONTROL.search(text),
            "source_control_tokens")


def render_prompt(template, row):
    source = row["source"]
    reject_control(source)
    reject_control(row.get("context", ""))
    require(0 < len(source.strip()) <= 12000, "source_size")
    environment = SandboxedEnvironment(undefined=StrictUndefined)
    environment.globals["raise_exception"] = lambda message: (_ for _ in ()).throw(RunError("template_rejected_input"))
    # No reference, annotations, glossary, context or review fields cross this boundary.
    content = [{"type": "text", "source_lang_code": "en", "target_lang_code": "ko", "text": source}]
    prompt = environment.from_string(template).render(messages=[{"role": "user", "content": content}],
                                                       bos_token="<bos>", add_generation_prompt=True)
    require(prompt.startswith("<bos><start_of_turn>user\n") and prompt.endswith("<start_of_turn>model\n")
            and source in prompt and len(prompt) <= 64000, "rendered_template_contract")
    return prompt


def validate_prompt_tokens(tokens, vocab_size):
    require(isinstance(tokens, list) and all(type(t) is int and 0 <= t < vocab_size for t in tokens), "invalid_input_tokens")
    require(tokens and tokens[0] == 2 and tokens.count(2) == 1 and tokens.count(105) == 2
            and tokens.count(106) == 1 and 1 not in tokens and 0 not in tokens and 212 not in tokens,
            "prompt_token_structure")
    require(len(tokens) + MAX_NEW_TOKENS <= CONTEXT_SIZE, "context_budget_exceeded")


def eog_from_log(text):
    # Parse only final model metadata, never the earlier load list or template examples.
    observed = {"EOS": {}, "EOT": {}, "EOG": {}}
    pattern = r"^.*?\bprint_info:\s+(EOS|EOT|EOG) token\s+=\s+(\d+) '([^'\r\n]*)'\s*$"
    for kind, token_id, token in re.findall(pattern, text, re.MULTILINE):
        token_id = int(token_id)
        require(token_id not in observed[kind], "duplicate_runtime_token_evidence")
        observed[kind][token_id] = token
    require(observed["EOS"] == {1: "<eos>"} and observed["EOT"] == {106: "<end_of_turn>"},
            "runtime_eos_eot_mismatch")
    require(observed["EOG"] == RUNTIME_EOG, "runtime_eog_mismatch")
    # Only validated constant token strings are retained; raw startup logs are discarded.
    return {"ids": sorted(RUNTIME_EOG), "tokens": {str(k): v for k, v in RUNTIME_EOG.items()},
            "eosId": 1, "eotId": 106, "basis": "final print_info EOS/EOT/EOG metadata",
            "originalEndTokenIds": sorted(ORIGINAL_EOG_IDS), "additionalRuntimeEndTokenIds": [212],
            "rawLogsRetained": False}


def validate_runtime_tokens(client):
    for token_id, text in RUNTIME_TOKEN_STRINGS.items():
        observed = client.request("/tokenize", {"content": text, "add_special": False, "parse_special": True})
        require(observed.get("tokens") == [token_id], "runtime_special_token_mismatch")


def validate_active_bias(settings):
    require(settings.get("ignore_eos") is False and settings.get("logit_bias") == [],
            "active_eog_or_other_logit_bias")


def memory_requirements(model_size, model, ram_budget_gib=12):
    require(model_size == "27b", "paging_experiment_requires_27b")
    require(type(ram_budget_gib) is int and ram_budget_gib in (8, 9, 10, 11, 12), "invalid_ram_budget")
    maximum_working_set = ram_budget_gib * GIB
    reservation = 64 * 1024 ** 2
    layers, heads, key_size, value_size = MEMORY_SHAPES[model_size]
    kv_bytes = layers * heads * (key_size + value_size) * CONTEXT_SIZE * 2
    return {"weightRepackingEnabled": False, "estimatedKvCacheUpperBoundBytes": kv_bytes,
            "commitOverheadMarginBytes": 3 * GIB,
            "ramBudgetGiB": ram_budget_gib,
            "requiredAvailablePhysicalBytes": maximum_working_set + 3 * GIB,
            "requiredAvailableCommitBytes": kv_bytes + 3 * GIB,
            "maximumWorkingSetBytes": maximum_working_set,
            "requestedHardMaximumWorkingSetBytes": maximum_working_set - reservation,
            "workingSetReservationBytes": reservation,
            "minimumWorkingSetBytes": MINIMUM_WORKING_SET,
            "physicalGateBasis": f"{ram_budget_gib}GiB observed child working-set budget plus 3GiB free physical headroom; not full-resident weights",
            "estimateScope": "f16 KV for all layers at 2K plus 3GiB private runtime margin; file-backed mmap weights; no-repack",
            "allocationGuarantee": False, "absoluteMemorySafetyClaimed": False,
            "continuousMemoryMonitor": True,
            "workingSetScope": "native child's resident working set only; excludes system file cache, prefetch pages outside that set, and other processes",
            "pageFaultScope": "process page-fault counter includes soft faults; not a measurement of disk paging bytes",
            "outputByteIdentityToV2Assumed": False}


def check_memory(memory, policy):
    require(type(memory.get("availablePhysical")) is int and
            memory["availablePhysical"] >= policy["requiredAvailablePhysicalBytes"],
            "insufficient_available_physical_memory")
    require(type(memory.get("availablePageFile")) is int and
            memory["availablePageFile"] >= policy["requiredAvailableCommitBytes"],
            "insufficient_available_commit")


def validate_memory_shape(model_size, metadata):
    names = ("gemma3.block_count", "gemma3.attention.head_count_kv",
             "gemma3.attention.key_length", "gemma3.attention.value_length")
    values = tuple(metadata.get(name) for name in names)
    require(all(type(v) is int for v in values) and values == MEMORY_SHAPES[model_size],
            "memory_estimate_model_shape_mismatch")


class MemoryGuard:
    def __init__(self):
        self.low_since = {"physical": None, "commit": None}

    def observe(self, state, now):
        for kind, field in (("physical", "availablePhysical"), ("commit", "availablePageFile")):
            value = state.get(field)
            require(type(value) is int and value >= 0, "invalid_memory_guard_sample")
            if value < MINIMUM_FREE_MEMORY:
                if self.low_since[kind] is None:
                    self.low_since[kind] = now
                if now - self.low_since[kind] >= MEMORY_LOW_SECONDS:
                    return "memory_" + kind + "_low"
            else:
                self.low_since[kind] = None
        return None


class MemoryMonitor:
    """Observe owned child; stop only it on sustained pressure or fixed deadlines."""
    def __init__(self, process, limiter, path, run_started, startup_started,
                 maximum_working_set=MAXIMUM_WORKING_SET):
        require(type(maximum_working_set) is int and maximum_working_set in (8 * GIB, 9 * GIB, 10 * GIB, 11 * GIB, 12 * GIB),
                "invalid_monitor_working_set_budget")
        self.process, self.limiter = process, limiter
        self.maximum_working_set = maximum_working_set
        self.run_started, self.startup_deadline = run_started, startup_started + STARTUP_SECONDS
        self.request_deadline = None
        self.stop_event, self.lock = threading.Event(), threading.Lock()
        self.observation_lock = threading.Lock()
        self.guard, self.abort_reason, self.monitor_error = MemoryGuard(), None, None
        self.kill_error = None
        self.observations = self.records = 0
        self.last_recorded = None
        self.extrema = {"minimumAvailablePhysical": None, "minimumAvailableCommit": None,
                        "maximumChildWorkingSet": None, "maximumChildPeakWorkingSet": None,
                        "maximumChildPrivateBytes": None}
        self.first_page_fault_count = self.last_page_fault_count = None
        self.path = path
        self.stream = path.open("x", encoding="utf-8", newline="\n")
        self.thread = threading.Thread(target=self._run, name="tg27-private-memory-guard", daemon=True)

    def sample(self):
        return {**screen.memory_status(), "child": self.limiter.sample_child(self.process)}

    def checked_sample(self):
        """Validate synchronous observations without racing the periodic observer."""
        with self.observation_lock:
            state = self.sample()
            self.observe(state, time.monotonic())
            self.check()
            return state

    def _record(self, state, now, force=False):
        self.observations += 1
        child = state["child"]
        if self.first_page_fault_count is None:
            self.first_page_fault_count = child["pageFaultCount"]
        self.last_page_fault_count = child["pageFaultCount"]
        for key, value, choose in (
            ("minimumAvailablePhysical", state["availablePhysical"], min),
            ("minimumAvailableCommit", state["availablePageFile"], min),
            ("maximumChildWorkingSet", child["workingSetBytes"], max),
            ("maximumChildPeakWorkingSet", child["peakWorkingSetBytes"], max),
            ("maximumChildPrivateBytes", child["privateBytes"], max)):
            current = self.extrema[key]
            self.extrema[key] = value if current is None else choose(current, value)
        if force or self.last_recorded is None or now - self.last_recorded >= SAMPLE_RECORD_INTERVAL:
            self.stream.write(json.dumps({"seconds": round(now - self.run_started, 3), **state}) + "\n")
            self.stream.flush()
            self.records += 1
            self.last_recorded = now

    def _abort(self, reason):
        with self.lock:
            self.abort_reason = self.abort_reason or reason
        if self.process.poll() is None:
            try:
                self.process.kill()
            except OSError as error:
                self.kill_error = type(error).__name__

    def observe(self, state, now):
        child = state.get("child", {})
        require(all(type(child.get(key)) is int and child[key] >= 0 for key in
                    ("workingSetBytes", "peakWorkingSetBytes", "privateBytes", "pageFaultCount")), "invalid_child_memory_sample")
        with self.lock:
            startup_deadline, request_deadline = self.startup_deadline, self.request_deadline
        reason = ("total_time_limit" if now - self.run_started >= TOTAL_SECONDS else
                  "startup_time_limit" if startup_deadline is not None and now >= startup_deadline else
                  "request_time_limit" if request_deadline is not None and now >= request_deadline else
                  "working_set_budget_exceeded" if child["workingSetBytes"] > self.maximum_working_set else
                  "peak_working_set_budget_exceeded" if child["peakWorkingSetBytes"] > self.maximum_working_set else
                  self.guard.observe(state, now))
        self._record(state, now, force=reason is not None)
        if reason:
            self._abort(reason)

    def _run(self):
        while not self.stop_event.wait(MONITOR_INTERVAL):
            if self.process.poll() is not None:
                return
            try:
                with self.observation_lock:
                    self.observe(self.sample(), time.monotonic())
                    if self.abort_reason:
                        return
            except Exception as error:
                self.monitor_error = type(error).__name__
                self._abort("memory_monitor_failed")
                return

    def start(self):
        self.thread.start()

    def ready(self):
        with self.lock:
            self.startup_deadline = None

    def begin_request(self):
        self.check()
        with self.lock:
            self.request_deadline = time.monotonic() + REQUEST_SECONDS

    def end_request(self):
        with self.lock:
            self.request_deadline = None

    def check(self):
        require(self.abort_reason is None, self.abort_reason or "memory_monitor_failed")
        require(self.process.poll() is None, "runtime_exited")

    def close(self):
        self.stop_event.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=3)
        require(not self.thread.is_alive(), "memory_monitor_did_not_stop")
        if not self.stream.closed:
            try:
                self.stream.flush()
                os.fsync(self.stream.fileno())
            finally:
                self.stream.close()

    def receipt(self):
        return {"abortReason": self.abort_reason, "monitorError": self.monitor_error,
                "ownedChildKillError": self.kill_error,
                "firstPageFaultCount": self.first_page_fault_count,
                "lastPageFaultCount": self.last_page_fault_count,
                "pageFaultCounterScope": "raw Windows DWORD counter, may wrap; soft and hard faults combined",
                "observations": self.observations, "recordedSamples": self.records, **self.extrema,
                "observeIntervalSeconds": MONITOR_INTERVAL, "recordIntervalSeconds": SAMPLE_RECORD_INTERVAL,
                "minimumAvailablePhysicalBytes": MINIMUM_FREE_MEMORY,
                "minimumAvailableCommitBytes": MINIMUM_FREE_MEMORY, "lowDurationSeconds": MEMORY_LOW_SECONDS,
                "maximumObservedWorkingSetBytes": self.maximum_working_set,
                "independentLowResourceTimers": True, "sampleFile": self.path.name}


def wait_ready(client, process, monitor, started):
    while time.monotonic() - started < STARTUP_SECONDS:
        monitor.check()
        try:
            if client.request("/health", timeout=2).get("status") == "ok":
                monitor.ready()
                return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.25)
    raise RunError("runtime_startup_timeout")


def prediction(row, prompt, tokens, response, elapsed, vocab_size):
    text = response.get("content")
    require(isinstance(text, str), "missing_raw_translation")
    generated, output_tokens = response.get("tokens_predicted"), response.get("tokens")
    require(type(generated) is int and 0 <= generated <= MAX_NEW_TOKENS, "invalid_generated_count")
    require(isinstance(output_tokens, list) and len(output_tokens) == generated
            and all(type(t) is int and 0 <= t < vocab_size for t in output_tokens), "output_token_evidence")
    require(response.get("stop_type") in {"eos", "limit"} and type(response.get("truncated")) is bool,
            "missing_stop_evidence")
    if response["stop_type"] == "eos":
        require(output_tokens and output_tokens[-1] in RUNTIME_EOG, "output_eog_mismatch")
    else:
        require(not any(t in RUNTIME_EOG for t in output_tokens), "limit_with_eog_token")
    require(not any(t in RUNTIME_EOG for t in output_tokens[:-1]), "interior_eog_token")
    actual = response.get("generation_settings")
    require(isinstance(actual, dict), "missing_generation_settings")
    validate_active_bias(actual)
    for key in ("n_predict", "temperature", "seed", "repeat_penalty", "top_k", "top_p", "min_p", "samplers", "ignore_eos", "stop"):
        require(actual.get(key) == SETTINGS[key], "generation_settings_changed")
    limited = response["stop_type"] == "limit" or generated >= MAX_NEW_TOKENS
    terminal_token = output_tokens[-1] if response["stop_type"] == "eos" else None
    original_end = terminal_token in ORIGINAL_EOG_IDS
    checks = {"nonEmpty": bool(text.strip()), "noLeakedControlTokens": not CONTROL.search(text) and "\ufffd" not in text,
              "numbersPreserved": transport.numeric_tokens(row["source"]) == transport.numeric_tokens(text),
              "currencySymbolsPreserved": Counter(re.findall(r"[$€£¥₩]", row["source"])) == Counter(re.findall(r"[$€£¥₩]", text)),
              "notTruncated": not response["truncated"], "belowOutputLimit": not limited,
              "originalEndToken": original_end}
    return {"id": row["id"], "domain": row["domain"], "status": "completed" if original_end else "failed", "translation": text,
            "sourceSha256": row["sourceSha256"], "contextSha256": row["contextSha256"],
            "targetSha256": sha_text(text), "promptSha256": sha_text(prompt), "inputTokens": len(tokens),
            "inputTokenIdsSha256": sha_json(tokens), "generatedTokens": generated, "outputTokenIds": output_tokens,
            "outputTokenIdsSha256": sha_json(output_tokens), "generationSeconds": round(elapsed, 6),
            "checks": checks, "numbersPreserved": checks["numbersPreserved"], "automaticChecksPassed": all(checks.values()),
            "stopType": response["stop_type"], "truncated": response["truncated"], "outputLimitReached": limited,
            "terminalTokenId": terminal_token,
            "terminationClass": "original_model_eog" if original_end else "runtime_added_eog" if terminal_token == 212 else "output_limit",
            "actualGenerationSettings": actual, "timings": response.get("timings"), "humanReviewed": False,
            "postProcessingApplied": False, "contextUsed": False}


def code_hashes():
    files = [Path(__file__), Path(setup.__file__), Path(setup.archive_tools.__file__), Path(transport.__file__),
             Path(screen.__file__), Path(engine.__file__), Path(process_owner.__file__), Path(sys.executable),
             Path(working_set_limit.__file__), Path(suspended_process_owner.__file__)]
    files += sorted(Path(jinja2.__file__).parent.rglob("*.py"))
    return {str(p.resolve()): setup.digest(p) for p in files}


def file_stamps(paths):
    result = {}
    for path in paths:
        setup.plain(path)
        st = path.stat()
        result[str(path)] = [st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino]
    return result


def server_command(dest, model, port, key, threads):
    return [str(dest / "runtime/llama-server.exe"), "--model", str(dest / model["name"]),
            "--host", "127.0.0.1", "--port", str(port), "--cors-origins", f"http://127.0.0.1:{port}",
            "--api-key", key, "--offline", "--no-agent", "--no-webui", "--no-warmup", "--no-jinja",
            "--chat-template", "gemma", "--ctx-size", str(CONTEXT_SIZE), "--parallel", "1", "--no-context-shift",
            "--gpu-layers", "0", "--device", "none", "--no-op-offload", "--no-repack",
            "--load-mode", "mmap", "--fit", "off", "--threads", str(threads),
            "--threads-batch", str(threads), "--batch-size", "256", "--ubatch-size", "128", "--cache-ram", "0",
            "--cache-type-k", "f16", "--cache-type-v", "f16", "--log-verbosity", "4",
            "--n-predict", str(MAX_NEW_TOKENS), "--temp", "0", "--seed", "20260910", "--repeat-penalty", "1",
            "--repeat-last-n", "0", "--top-k", "0", "--top-p", "1", "--min-p", "0", "--samplers", "temperature"]


def smoke_rows():
    sources = ["If the amount rises from 10 to 12, the increase is 2. The condition does not imply that the amount will rise again.",
               "The report distinguishes the value of the existing assets from the value of future projects. It does not treat the two values as identical."]
    return [{"id": f"TG-SMOKE-{i:03d}", "source": text, "context": "", "domain": "general",
             "sourceSha256": sha_text(text), "contextSha256": sha_text("")} for i, text in enumerate(sources, 1)]


def run(args):
    run_started = time.monotonic()
    output = args.output.absolute()
    setup.plain(output)
    require(output.resolve().is_relative_to((ROOT / ".training/comparisons").resolve()), "output_outside_comparisons")
    require(args.model_size == "27b" and args.threads == 4, "paging_experiment_requires_27b_cpu4")
    require(type(args.ram_budget_gib) is int and args.ram_budget_gib in (8, 9, 10, 11, 12), "invalid_ram_budget")
    output.mkdir(parents=True, exist_ok=False)
    rows, results, process, diagnostics, monitor = [], [], None, None, None
    summary = {"version": "translategemma-large-screen-v3", "status": "running", "startedAt": datetime.now(timezone.utc).isoformat(),
               "modelSize": args.model_size, "profile": "source-only", "humanReviewed": False, "paidApiCalls": 0,
               "appDeploymentPerformed": False, "postProcessingApplied": False, "glossaryApplied": False,
               "translationMemoryApplied": False, "contextUsed": False, "settings": SETTINGS, "threads": args.threads,
               "purpose": "bounded-working-set paging feasibility smoke" if args.smoke else "development paging experiment; not independent final certification",
               "timeLimitsSeconds": {"startup": STARTUP_SECONDS, "request": REQUEST_SECONDS, "total": TOTAL_SECONDS},
               "pagingExperiment": True, "fullResidentPhysicalGateApplied": False,
               "ramBudgetGiB": args.ram_budget_gib,
               "outputByteIdentityToV2Assumed": False, "absoluteMemorySafetyClaimed": False}
    phase, failed_id, codes = "input", None, None
    try:
        if args.smoke:
            require(args.input is None and not args.ids, "smoke_cannot_use_development_input")
            rows = smoke_rows()
            info = {"smokeInputSha256": sha_json(rows), "selectedIds": [r["id"] for r in rows]}
        else:
            require(args.input is not None, "input_required")
            rows, info = screen.read_screen(args.input, args.ids)
        for row in rows:
            reject_control(row["source"])
            reject_control(row["context"])
        summary["input"] = info
        summary["expectedCount"] = len(rows)
        codes = code_hashes()
        summary["codeHashes"] = codes
        profile = setup.PROFILES[args.model_size]
        dest, model = profile["dest"], profile["model"]
        phase = "memory-preflight"
        memory = screen.memory_status()
        summary["memoryBefore"] = memory
        policy = memory_requirements(args.model_size, model, args.ram_budget_gib)
        summary["memoryPolicy"] = policy
        summary["requiredAvailablePhysicalBytes"] = policy["requiredAvailablePhysicalBytes"]
        summary["requiredAvailableCommitBytes"] = policy["requiredAvailableCommitBytes"]
        check_memory(memory, policy)
        phase = "installation"
        installation = setup.verify_installation(model_size=args.model_size)
        template, contract = setup.gguf_contract(dest / model["name"])
        validate_memory_shape(args.model_size, contract["metadata"])
        summary.update(modelSha256=model["sha256"], installationManifestSha256=setup.digest(dest / "installation-manifest.json"),
                       runtimeFiles=installation["runtimeFiles"], templateSha256=contract["templateSha256"], ggufContract=contract)
        vocab_size = contract["metadata"]["tokenizer.ggml.tokens"]["count"]
        assets = [dest / model["name"], dest / "installation-manifest.json", dest / "gguf-contract.json", dest / "chat-template.jinja"]
        assets += [dest / "runtime" / item["path"] for item in installation["runtimeFiles"]]
        stamps = file_stamps(assets)
        prompts = [render_prompt(template, row) for row in rows]
        screen.write_once(output / "start.json", summary)

        def unchanged():
            require(code_hashes() == codes and file_stamps(assets) == stamps, "runtime_or_code_changed")
            if not args.smoke:
                screen.assert_input_unchanged(info)

        unchanged()
        phase = "startup"
        memory = screen.memory_status()
        summary["memoryImmediatelyBeforeStartup"] = memory
        check_memory(memory, policy)
        owner = claim_process_owner()
        limiter = WorkingSetLimit(owner, maximum_bytes=policy["requestedHardMaximumWorkingSetBytes"],
                                  minimum_bytes=MINIMUM_WORKING_SET)
        summary["jobWorkingSetLimit"] = limiter.job_record
        owner = SuspendedProcessOwner(owner, limiter)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        key = secrets.token_urlsafe(32)
        command = server_command(dest, model, port, key, args.threads)
        startup = time.monotonic()
        process = owner.spawn(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                              bufsize=0, env=transport.runtime_environment(), cwd=str(dest / "runtime"))
        # The local adapter applies/reads hard flags while suspended, before ResumeThread.
        summary["suspendedCreation"] = owner.creation_receipt
        summary["childWorkingSetLimit"] = limiter.apply_child(process)
        monitor = MemoryMonitor(process, limiter, output / "memory-samples.jsonl", run_started, startup,
                                maximum_working_set=policy["maximumWorkingSetBytes"])
        monitor.start()
        diagnostics = StartupDiagnostics(process.stdout)
        client = transport.LocalClient(f"http://127.0.0.1:{port}", key)
        wait_ready(client, process, monitor, startup)
        deadline = time.monotonic() + 5
        while True:
            monitor.check()
            try:
                summary["actualEog"] = eog_from_log(diagnostics.startup_text())
                break
            except RunError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        validate_runtime_tokens(client)
        props = client.request("/props")
        require(Path(props.get("model_path", "")).resolve() == (dest / model["name"]).resolve()
                and props.get("total_slots") == 1 and props.get("default_generation_settings", {}).get("n_ctx") == CONTEXT_SIZE,
                "server_model_or_context_mismatch")
        # /props uses to_json(only_metrics=true): ignore_eos is nested under params,
        # while logit_bias is intentionally absent. /completion serializes full params.
        default_sampling = props["default_generation_settings"].get("params")
        require(isinstance(default_sampling, dict) and default_sampling.get("ignore_eos") is False,
                "default_ignore_eos_not_false")
        if "logit_bias" in default_sampling:
            validate_active_bias(default_sampling)
        screen.write_once(output / "startup-token-evidence.json", {
            "version": "translategemma-final-runtime-token-evidence-v1", "actualEog": summary["actualEog"],
            "tokenizationChecked": {str(k): v for k, v in RUNTIME_TOKEN_STRINGS.items()},
            "defaultIgnoreEos": False,
            "defaultActiveLogitBias": default_sampling.get("logit_bias"),
            "biasVerification": "props reduced metrics may omit bias; request explicitly sends []; each full completion response must report []",
            "modelSha256": model["sha256"], "installationManifestSha256": summary["installationManifestSha256"],
            "runnerSha256": codes[str(Path(__file__).resolve())],
            "rawStartupLogsRetained": False, "sourceSubmittedAtEvidenceCapture": False})
        summary["startupTokenEvidenceSha256"] = setup.digest(output / "startup-token-evidence.json")
        summary["loadSeconds"] = round(time.monotonic() - startup, 6)
        summary["memoryAfterLoad"] = screen.memory_status()
        summary["childMemoryAfterLoad"] = monitor.checked_sample()["child"]
        summary["runtimeTokenizationAndEogValidated"] = True
        diagnostics.discard()
        phase = "prompt-preflight"
        tokenized = []
        for prompt in prompts:
            monitor.check()
            tokens = client.request("/tokenize", {"content": prompt, "add_special": False, "parse_special": True}, timeout=30).get("tokens")
            validate_prompt_tokens(tokens, vocab_size)
            tokenized.append(tokens)
        phase = "generation"
        with (output / "predictions.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
            for row, prompt, tokens in zip(rows, prompts, tokenized):
                failed_id = row["id"]
                unchanged()
                owner.assert_owned()
                monitor.check()
                before_memory = monitor.checked_sample()
                monitor.begin_request()
                started = time.monotonic()
                try:
                    response = client.request("/completion", SETTINGS | {"prompt": tokens}, timeout=REQUEST_SECONDS)
                finally:
                    monitor.end_request()
                elapsed = time.monotonic() - started
                # Preserve the returned object even if validation rejects its protocol.
                screen.write_once(output / (row["id"] + "-response.json"), response)
                monitor.check()
                item = prediction(row, prompt, tokens, response, elapsed, vocab_size)
                after_memory = monitor.checked_sample()
                item["memoryAfter"] = {k: v for k, v in after_memory.items() if k != "child"}
                item["childMemoryAfter"] = after_memory["child"]
                item["memoryBeforeRequest"] = before_memory
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
                stream.flush()
                results.append(item)
                print(json.dumps({"id": row["id"], "seconds": item["generationSeconds"], "tokens": item["generatedTokens"],
                                  "truncated": item["truncated"], "outputLimitReached": item["outputLimitReached"]}), flush=True)
                require(item["terminalTokenId"] != 212, "runtime_added_eog_stop")
                require(item["checks"]["nonEmpty"] and item["checks"]["noLeakedControlTokens"]
                        and item["checks"]["notTruncated"] and item["checks"]["belowOutputLimit"]
                        and item["checks"]["originalEndToken"], "invalid_or_incomplete_output")
        phase = "final-integrity"
        unchanged()
        monitor.checked_sample()
        summary["integrityVerified"] = True
        summary["status"] = "completed"
    except BaseException as exc:
        summary.update(status="failed", phase=phase, failedRowId=failed_id,
                       error= str(exc) if isinstance(exc, RunError) else type(exc).__name__)
    finally:
        if diagnostics:
            diagnostics.discard()
        if monitor:
            try:
                monitor.close()
            except BaseException as error:
                summary.update(status="failed", monitorCleanupError=type(error).__name__)
            summary["memoryMonitoring"] = monitor.receipt()
            if monitor.abort_reason:
                summary.update(status="failed", memoryOrTimeGuardAborted=True)
        try:
            summary["childProcessStopped"] = transport.stop_process(process)
            if not summary["childProcessStopped"]:
                summary.update(status="failed", cleanupError="owned_process_cleanup_failed")
        except BaseException:
            summary.update(status="failed", childProcessStopped=False, cleanupError="owned_process_cleanup_failed")
        finally:
            if process and process.stdout:
                process.stdout.close()
            if diagnostics:
                diagnostics.join()
        try:
            summary["memoryAfterStop"] = screen.memory_status()
        except BaseException as exc:
            summary["memoryAfterStopError"] = type(exc).__name__
        known = {r["id"] for r in results}
        path = output / "predictions.jsonl"
        with path.open("a" if path.exists() else "x", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                if row["id"] not in known:
                    stream.write(json.dumps({"id": row["id"], "status": "failed" if row["id"] == failed_id else "not_run",
                                             "sourceSha256": row["sourceSha256"], "contextSha256": row["contextSha256"]}) + "\n")
        summary.update(count=len(results), recordedCount=len(rows), finishedAt=datetime.now(timezone.utc).isoformat(),
                       predictionsSha256=setup.digest(path), generationSeconds=sum(r["generationSeconds"] for r in results))
        if monitor and monitor.path.exists():
            summary["memorySamplesSha256"] = setup.digest(monitor.path)
        screen.write_once(output / "summary.json", summary)
    return summary


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--model-size", choices=("27b",), required=True)
    result.add_argument("--input", type=Path)
    result.add_argument("--ids", nargs="+")
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--threads", type=int, choices=(4,), default=4)
    result.add_argument("--ram-budget-gib", type=int, choices=(8, 9, 10, 11, 12), default=12,
                        help="Observed child working-set budget, minimum 8GiB; API cap is 64MiB lower; startup also requires 3GiB free headroom")
    result.add_argument("--smoke", action="store_true")
    return result


def main():
    result = run(parser().parse_args())
    print(json.dumps({"status": result["status"], "phase": result.get("phase"), "count": result["count"]}), flush=True)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
