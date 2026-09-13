"""V3 isolated Hy-MT2-30B-A3B Q4 CPU paging screen with an owned working-set cap.

Uses the original 30B GGUF template/EOS and the publisher's 30B sampling.
Only the frozen source/context allowlist and existing conditional catalog enter
the prompt. Outputs are raw and exclusive; semantic errors remain for review.
--run is required to load the model. No production module is monkeypatched.
Disables CPU weight repacking to avoid an extra private copy of Q4 tensors.
This changes the kernel path; identical output bytes are not assumed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import ctypes
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error

from jinja2 import StrictUndefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

import linguistic_screen as screen
import run_hymt as common
import setup_hymt30 as setup

import working_set_limit
from working_set_limit import WorkingSetLimit
import suspended_process_owner
from suspended_process_owner import SuspendedProcessOwner

VERSION = "hymt30-development-screen-v3"
OWNER = common.ROOT / "scripts/local-hymt/process_owner.py"
FROZEN = {
    Path(common.__file__).resolve(): "a80c431e51c17d510273457d0991e2febba57bac33a8f89a067e1f571387a410",
    Path(setup.__file__).resolve(): "92f67dde573aa80796c4fcf1d99b3258a9ac0f2d49d849d111db9788c8bdf45c",
    OWNER: "9a00d809466e304359fb47c374c8fc2464ce31cdbc9557fbc9eee089bfe9d8b2",
    Path(working_set_limit.__file__).resolve(): "4f8a45772a0f3d3f63274c7309bc7e673446eebb47a4a3e71dc558b95a4cbcdb",
    Path(suspended_process_owner.__file__).resolve(): "f01ca8a2fd5a75e501c343d2632b2f534b4e5f0397fda502449c69c5d363dfcd",
}
CONTEXT_SIZE = 8192
MAX_NEW_TOKENS = 4096
GIB = 1024 ** 3
MAXIMUM_WORKING_SET = 12 * GIB
MINIMUM_FREE_MEMORY = 512 * 1024 ** 2
MEMORY_LOW_SECONDS = 3
MONITOR_INTERVAL = 0.5
SAMPLE_RECORD_INTERVAL = 5
STARTUP_SECONDS = 1800
REQUEST_SECONDS = 1800
TOTAL_SECONDS = 12 * 60 * 60
HEADER_END = 5080783
HEADER_SHA = "405976041b7b408b6354db60c9e6d2fcabda0758c5289fc20fb1e5296e79b747"
TEMPLATE_SHA = "53e11a67caa40e918de4930d836829024973da39b357cec4f4bf5aa2da96717f"
TOKEN_STRINGS = {
    120000: "<｜hy_begin▁of▁sentence｜>", 120001: "<｜hy_end▁of▁sentence｜>",
    120002: "<｜hy_▁pad▁｜>", 120006: "<｜hy_User｜>", 120007: "<｜hy_Assistant｜>",
    120008: "<｜hy_EOT｜>", 120025: "<eos:6124c78e>",
    120026: "<｜hy_place▁holder▁no▁8｜>", 120029: "<think>", 120030: "</think>",
    120044: "<｜reasoning_mode｜>",
}
EOG_IDS = {120025}
# The publisher specifies -1 to disable top-k, top-p=1 and repetition penalty=1.
# All additional llama.cpp filters/penalties are explicitly disabled. This is
# independent of the frozen 7B SAMPLING object and its incorrect-EOS workaround.
SAMPLING = {
    "temperature": 0.7, "top_p": 1.0, "top_k": -1, "repeat_penalty": 1.0,
    "repeat_last_n": CONTEXT_SIZE, "min_p": 0.0, "seed": 42,
    "samplers": ["penalties", "temperature", "top_k", "top_p"],
    "n_predict": MAX_NEW_TOKENS, "stop": [], "ignore_eos": False,
    "cache_prompt": False, "stream": False, "return_tokens": True,
    "n_keep": 0, "id_slot": 0, "presence_penalty": 0.0, "frequency_penalty": 0.0,
    "dry_multiplier": 0.0, "mirostat": 0, "dynatemp_range": 0.0,
    "typical_p": 1.0, "xtc_probability": 0.0, "top_n_sigma": -1.0,
}
require = common.require


def gguf_contract(path):
    """Read bounded metadata/descriptors, not weights; full SHA is checked by setup."""
    with Path(path).open("rb") as source:
        data = source.read(16 * 1024 * 1024)
    stream = io.BytesIO(data)
    formats = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}

    def take(size):
        require(0 <= size <= len(data), "invalid_gguf_size")
        value = stream.read(size)
        require(len(value) == size, "incomplete_gguf_header")
        return value

    def number(fmt):
        return struct.unpack("<" + fmt, take(struct.calcsize("<" + fmt)))[0]

    def string():
        return take(number("Q")).decode("utf-8")

    def value(kind):
        if kind == 8:
            return string()
        if kind == 9:
            element, count = number("I"), number("Q")
            require(count <= 1000000 and element != 9, "invalid_gguf_array")
            return [value(element) for _ in range(count)]
        require(kind in formats, "invalid_gguf_type")
        return number(formats[kind])

    require(take(4) == b"GGUF" and number("I") == 3, "gguf_version")
    tensors, count = number("Q"), number("Q")
    require(tensors == 766 and count == 41, "gguf_header_identity")
    metadata = {}
    for _ in range(count):
        key, kind = string(), number("I")
        require(key not in metadata, "duplicate_gguf_key")
        metadata[key] = value(kind)
    require(stream.tell() == HEADER_END and hashlib.sha256(data[:HEADER_END]).hexdigest() == HEADER_SHA,
            "gguf_metadata_hash")
    expected = {
        "general.architecture": "hy_v3", "general.file_type": 15,
        "hy_v3.block_count": 48, "hy_v3.embedding_length": 2048,
        "hy_v3.attention.head_count": 32, "hy_v3.attention.head_count_kv": 4,
        "hy_v3.attention.key_length": 128, "hy_v3.attention.value_length": 128,
        "hy_v3.expert_count": 128, "hy_v3.expert_used_count": 8,
        "tokenizer.ggml.model": "gpt2", "tokenizer.ggml.pre": "hunyuan-dense",
        "tokenizer.ggml.bos_token_id": 120000, "tokenizer.ggml.eos_token_id": 120025,
        "tokenizer.ggml.padding_token_id": 120002, "tokenizer.ggml.seperator_token_id": 120007,
    }
    require(all(metadata.get(k) == v for k, v in expected.items()), "gguf_original_metadata_mismatch")
    require(not any(k in metadata for k in ("tokenizer.ggml.eot_token_id", "tokenizer.ggml.eom_token_id",
                "tokenizer.ggml.add_bos_token", "tokenizer.ggml.add_eos_token")), "unexpected_token_metadata")
    tokens, types = metadata["tokenizer.ggml.tokens"], metadata["tokenizer.ggml.token_type"]
    require(len(tokens) == len(types) == 120832, "gguf_vocabulary_size")
    require(all(tokens[k] == v for k, v in TOKEN_STRINGS.items()), "gguf_token_strings")
    # The published GGUF marks reasoning_mode CONTROL (3), while its original
    # tokenizer_config lists special=false; preserve these actual GGUF bytes.
    require(all(types[k] == (4 if k in (120029, 120030) else 3) for k in TOKEN_STRINGS),
            "gguf_token_types")
    template = metadata["tokenizer.chat_template"]
    require(common.sha_text(template) == TEMPLATE_SHA, "gguf_original_template_mismatch")
    tensor_types = Counter()
    for _ in range(tensors):
        string()
        dimensions = number("I")
        require(1 <= dimensions <= 4, "gguf_tensor_dimensions")
        for _ in range(dimensions):
            require(number("Q") > 0, "invalid_tensor_dimension")
        tensor_types[number("I")] += 1
        number("Q")
    require(dict(tensor_types) == {14: 72, 0: 287, 12: 407} and stream.tell() == 5128863,
            "gguf_tensor_descriptor_identity")
    return template, {
        "metadataSha256": HEADER_SHA, "templateSha256": TEMPLATE_SHA, "originalMetadata": expected,
        "tokenStrings": TOKEN_STRINGS, "tokenTypes": {k: types[k] for k in TOKEN_STRINGS},
        "tensorTypes": dict(tensor_types), "tensorDescriptorEnd": stream.tell(), "tensorCount": tensors,
        "runtimeOverrides": {}, "expectedEogIds": sorted(EOG_IDS),
        "eogSource": "b10888 llama-vocab.cpp text detection plus original GGUF EOS; startup log must agree",
        "originalEod120026IsNotEos": True,
    }


def render_prompt(template, content):
    require(common.sha_text(template) == TEMPLATE_SHA, "template_not_pinned")
    require(isinstance(content, str) and not re.search(
        r"<\|[^\n>]*\|>|<｜[^\n>]*｜>|</?(?:think|answer|tool[^>]*)>|<eos:[^>]*>", content),
        "input_contains_control_token")
    # Transformers' official apply_chat_template uses these whitespace options.
    # The non-trimmed Jinja default introduces two extra newlines before BOS.
    env = ImmutableSandboxedEnvironment(undefined=StrictUndefined, autoescape=False,
                                       trim_blocks=True, lstrip_blocks=True)
    prompt = env.from_string(template).render(messages=[{"role": "user", "content": content}],
        tools=None, add_generation_prompt=True, reasoning_effort="no_think")
    expected = (TOKEN_STRINGS[120000] + TOKEN_STRINGS[120044] + "reasoning_effort:no_think"
                + TOKEN_STRINGS[120006] + content + TOKEN_STRINGS[120007] + "<think></think>")
    require(prompt == expected, "original_template_render_contract")
    return prompt


def validate_prompt_tokens(tokens):
    require(isinstance(tokens, list) and all(type(t) is int and 0 <= t < 120832 for t in tokens),
            "invalid_prompt_tokens")
    require(tokens[:2] == [120000, 120044] and tokens[-3:] == [120007, 120029, 120030]
            and all(tokens.count(k) == 1 for k in (120000, 120044, 120006, 120007, 120029, 120030))
            and not ({120001, 120002, 120008, 120025, 120026} & set(tokens)), "prompt_special_tokens")
    require(len(tokens) + MAX_NEW_TOKENS < CONTEXT_SIZE, "prompt_context_budget")


def runtime_eog(log):
    require("printing all EOG tokens:" in log, "missing_runtime_eog_evidence")
    section = log.split("printing all EOG tokens:")[-1]
    result = {int(i): token for i, token in re.findall(
        r"^.*?:\s+-\s+(\d+)\s+\('([^']*)'\)\s*$", section, re.MULTILINE)}
    require(result == {k: TOKEN_STRINGS[k] for k in EOG_IDS}, "runtime_eog_mismatch")
    return {"ids": sorted(result), "tokens": result, "overridesApplied": False}


def validate_runtime(client, template, log):
    evidence = {"eog": runtime_eog(log), "tokens": {}}
    for token_id, text in TOKEN_STRINGS.items():
        result = client.request("/tokenize", {"content": text, "add_special": False, "parse_special": True}, timeout=15)
        require(result.get("tokens") == [token_id], "runtime_token_id_mismatch")
        evidence["tokens"][str(token_id)] = text
    props = client.request("/props", timeout=15)
    require(Path(props.get("model_path", "")).resolve() == (setup.DEST / setup.MODEL["name"]).resolve(),
            "runtime_model_path_mismatch")
    require(props.get("chat_template") == template and props.get("total_slots") == 1
            and props.get("default_generation_settings", {}).get("n_ctx") == CONTEXT_SIZE,
            "runtime_template_context_slots_mismatch")
    evidence["buildInfo"] = props.get("build_info")
    return evidence


def validate_settings(settings):
    require(isinstance(settings, dict), "missing_generation_settings")
    for k in ("temperature", "top_p", "top_k", "repeat_penalty", "repeat_last_n", "min_p", "seed",
              "samplers", "n_predict", "ignore_eos", "presence_penalty", "frequency_penalty",
              "dry_multiplier", "mirostat", "dynatemp_range", "typical_p", "xtc_probability", "stop", "top_n_sigma"):
        actual, expected = settings.get(k), SAMPLING[k]
        equal = math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-8) if (
            type(actual) in (float, int) and type(expected) in (float, int)) else actual == expected
        require(equal, "actual_sampling_mismatch_" + k)
    return {k: settings[k] for k in SAMPLING if k in settings}


def prediction(row, prompt, matches, tokens, response, elapsed):
    require(isinstance(response.get("content"), str), "missing_translation")
    text, output = response["content"], response.get("tokens")
    generated = response.get("tokens_predicted")
    require(type(generated) is int and 0 <= generated <= MAX_NEW_TOKENS, "invalid_generated_count")
    require(isinstance(output, list) and all(type(t) is int and 0 <= t < 120832 for t in output)
            and len(output) == generated, "invalid_generated_token_evidence")
    require(response.get("stop_type") in ("eos", "limit") and type(response.get("truncated")) is bool,
            "invalid_stop_evidence")
    if response["stop_type"] == "eos":
        require(output and output[-1] in EOG_IDS, "output_eog_token_mismatch")
    settings = validate_settings(response.get("generation_settings"))
    limited = response["stop_type"] == "limit" or generated >= MAX_NEW_TOKENS
    checks = {
        "nonEmpty": bool(text.strip()), "numbersPreserved": common.numeric_tokens(row["source"]) == common.numeric_tokens(text),
        "currencySymbolsPreserved": Counter(re.findall(r"[$€£¥₩]", row["source"])) == Counter(re.findall(r"[$€£¥₩]", text)),
        "notTruncated": not response["truncated"], "belowOutputLimit": not limited,
        "validUnicode": "\ufffd" not in text,
        "noLeakedControlTokens": re.search(r"<\|[^\n>]*\|>|<｜[^\n>]*｜>|</?(?:think|answer|tool[^>]*)>|<eos:[^>]*>", text) is None,
    }
    output_integrity = all(checks[k] for k in
        ("nonEmpty", "notTruncated", "belowOutputLimit", "validUnicode", "noLeakedControlTokens"))
    return {
        "id": row["id"], "status": "completed", "translation": text,
        "sourceSha256": row["sourceSha256"], "contextSha256": row["contextSha256"],
        "promptSha256": common.sha_text(prompt), "targetSha256": common.sha_text(text),
        "inputTokens": len(tokens), "inputTokenIdsSha256": common.sha_json(tokens),
        "generatedTokens": generated, "outputTokenIds": output, "outputTokenIdsSha256": common.sha_json(output),
        "stopType": response["stop_type"], "stoppingWord": response.get("stopping_word"),
        "truncated": response["truncated"], "outputLimitReached": limited,
        "checks": checks, "automaticChecksPassed": all(checks.values()), "outputIntegrityPassed": output_integrity,
        "generationSeconds": round(elapsed, 6), "timings": response.get("timings"),
        "actualGenerationSettings": settings, "conditionalTermMatches": matches,
        "humanReviewed": False, "postProcessingApplied": False,
    }


def preflight(available=None, available_commit=None, ram_budget_gib=12):
    require(type(ram_budget_gib) is int and ram_budget_gib in (8, 9, 10, 11, 12), "invalid_ram_budget")
    maximum = ram_budget_gib * GIB
    if available is None or available_commit is None:
        state = screen.memory_status()
        if available is None:
            available = state["availablePhysical"]
        if available_commit is None:
            available_commit = state["availablePageFile"]
    kv = 48 * CONTEXT_SIZE * 4 * 128 * 2 * 2
    headroom = 3 * 1024 ** 3
    physical_passed = available >= maximum + headroom
    commit_passed = available_commit >= headroom
    return {
        "availablePhysicalBytes": available, "availableCommitBytes": available_commit,
        "availableCommitField": "GlobalMemoryStatusEx.ullAvailPageFile", "modelBytes": setup.MODEL["size"],
        "f16KvBudgetBytes": kv, "scratchAndHeadroomEstimateBytes": headroom - kv,
        "ramBudgetGiB": ram_budget_gib,
        "maximumWorkingSetBytes": maximum,
        "requestedHardMaximumWorkingSetBytes": maximum - 64 * 1024 ** 2,
        "workingSetReservationBytes": 64 * 1024 ** 2,
        "minimumAvailablePhysicalBytes": maximum + headroom,
        "minimumAvailableCommitBytes": headroom,
        "physicalPassed": physical_passed, "commitPassed": commit_passed,
        "passed": physical_passed and commit_passed,
        "scratchMeasured": False, "kvFormula": "48 layers * 8192 positions * 4 KV heads * 128 dimensions * 2 K/V * 2 bytes",
        "policy": "physical: observed child working-set budget + 3 GiB; additional commit: 3 GiB for KV/heap/headroom with read-only mmap and no repack",
        "weightRepacking": False, "commitIsFreePageFileDiskSpace": False,
        "sharedGpuMemoryAddsRam": False, "guaranteesNoOutOfMemory": False,
        "workingSetScope": "native child resident pages only; excludes system cache, prefetch outside that set, and other processes",
        "pageFaultScope": "raw process count includes soft faults; not disk paging bytes",
        "outputByteIdentityToV2Assumed": False,
    }


class MemoryGuard:
    """Independent sustained-low timers; switching low resources cannot combine them."""
    def __init__(self):
        self.low_since = {"physical": None, "commit": None}

    def observe(self, state, now):
        for kind, field in (("physical", "availablePhysical"), ("commit", "availablePageFile")):
            value = state.get(field)
            require(type(value) is int and value >= 0, "invalid_memory_guard_sample")
            if value < 512 * 1024 ** 2:
                if self.low_since[kind] is None:
                    self.low_since[kind] = now
                if now - self.low_since[kind] >= 3:
                    return kind
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
        self.thread = threading.Thread(target=self._run, name="hy30-private-memory-guard", daemon=True)

    def sample(self):
        return {**screen.memory_status(), "child": self.limiter.sample_child(self.process)}

    def sample_and_check(self):
        # Serialize both the sampling timestamp and observation with the worker:
        # an older concurrently acquired sample must not rewind pressure timers.
        with self.observation_lock:
            state = self.sample()
            self._observe(state, time.monotonic())
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
        with self.observation_lock:
            self._observe(state, now)

    def _observe(self, state, now):
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
                    self._observe(self.sample(), time.monotonic())
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


def memory_state(process):
    state = screen.memory_status()
    if process.poll() is None:
        class Memory(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("pageFaultCount", ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in ("peakWorkingSet", "workingSet", "peakPagedPool", "pagedPool",
                    "peakNonPagedPool", "nonPagedPool", "pagefile", "peakPagefile", "private")]
        value = Memory()
        value.cb = ctypes.sizeof(value)
        library = ctypes.WinDLL("psapi", use_last_error=True)
        library.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Memory), ctypes.c_ulong]
        library.GetProcessMemoryInfo.restype = ctypes.c_int
        require(library.GetProcessMemoryInfo(int(process._handle), ctypes.byref(value), value.cb), "child_memory_query_failed")
        state.update(childWorkingSetBytes=value.workingSet, childPrivateBytes=value.private,
                     childPeakWorkingSetBytes=value.peakWorkingSet, childPageFaultCount=value.pageFaultCount)
    return state


def server_command(port, key):
    return [str(setup.DEST / "runtime-cpu/llama-server.exe"), "--model", str(setup.DEST / setup.MODEL["name"]),
        "--host", "127.0.0.1", "--port", str(port), "--cors-origins", f"http://127.0.0.1:{port}",
        "--api-key", key, "--no-agent", "--offline", "--no-webui", "--no-warmup", "--jinja",
        "--ctx-size", str(CONTEXT_SIZE), "--parallel", "1", "--no-context-shift", "--gpu-layers", "0",
        "--device", "none", "--no-op-offload", "--load-mode", "mmap", "--no-repack", "--fit", "off",
        "--threads", "4", "--threads-batch", "4", "--batch-size", "256", "--ubatch-size", "128",
        "--cache-ram", "0", "--cache-type-k", "f16", "--cache-type-v", "f16", "--log-verbosity", "4",
        "--temp", "0.7", "--top-p", "1", "--top-k", "-1", "--min-p", "0", "--repeat-penalty", "1",
        "--repeat-last-n", str(CONTEXT_SIZE), "--seed", "42", "--samplers", ";".join(SAMPLING["samplers"]),
        "--n-predict", str(MAX_NEW_TOKENS)]


def code_hashes():
    paths = [Path(__file__).resolve(), Path(setup.__file__).resolve(), Path(screen.__file__).resolve(), OWNER, Path(working_set_limit.__file__).resolve(), Path(suspended_process_owner.__file__).resolve()]
    optional = common.ROOT / "scripts/model-comparison/reading_check_input.py"
    if optional.exists():
        paths.append(optional)
    return common.code_hashes() | {str(p): common.digest(p) for p in paths}


def smoke_rows():
    sources = ["If the amount rises from 10 to 12, the increase is 2. The condition does not imply that the amount will rise again.",
               "The report distinguishes the value of the existing assets from the value of future projects. It does not treat the two values as identical."]
    return [{"id": f"HY30-SMOKE-{i:03d}", "source": text, "context": "", "domain": "general",
             "sourceSha256": common.sha_text(text), "contextSha256": common.sha_text("")} for i, text in enumerate(sources, 1)]


def run(args):
    output = args.output.resolve()
    require(output.is_relative_to(common.COMPARISONS.resolve()) and output != common.COMPARISONS.resolve(),
            "output_path_not_allowed")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    summary = {
        "version": VERSION, "status": "failed", "completed": 0, "failure": None,
        "model": setup.MODEL, "modelRevision": setup.REVISION, "runtimeTag": setup.RUNTIME_TAG,
        "runtimeRevision": setup.RUNTIME_REVISION, "backend": "cpu", "threads": 4, "loadMode": "mmap",
        "weightRepacking": False, "kernelPathChangedFromV1": True, "outputByteIdentityAssumed": False,
        "outputByteIdentityToV2Assumed": False, "pagingExperiment": True, "ramBudgetGiB": args.ram_budget_gib, "smoke": args.smoke,
        "profile": "contextual", "sampling": SAMPLING, "contextSize": CONTEXT_SIZE, "runtimeOverrides": {},
        "createdAt": datetime.now(timezone.utc).isoformat(), "humanReviewed": False,
        "modelLoaded": False, "warmupRequests": 0, "warmupEnabled": False,
        "firstRequestIncludesColdKernels": True, "applicationChanged": False,
        "independentFinalCertification": False, "stabilityCertified": False,
        "timeLimitsSeconds": {"startup": STARTUP_SECONDS, "request": REQUEST_SECONDS, "total": TOTAL_SECONDS},
        "memoryGuard": {"minimumAvailablePhysicalBytes": 512 * 1024 ** 2,
                        "minimumAvailableCommitBytes": 512 * 1024 ** 2,
                        "durationSeconds": 3, "sampleIntervalSeconds": 0.5, "independentResourceTimers": True},
    }
    process = log = monitor = None
    records = []
    identity = current = None
    try:
        require(os.name == "nt" and sys.version_info[:2] == (3, 11), "pinned_windows_cpython311_required")
        common.check_identity_unchanged({str(p): sha for p, sha in FROZEN.items()})
        summary["codeHashes"] = code_hashes()
        summary["preflightBeforeIntegrity"] = preflight(ram_budget_gib=args.ram_budget_gib)
        if not args.run:
            summary["status"] = "prepared"
            summary["sourceRead"] = False
        else:
            if args.smoke:
                require(args.input is None, "smoke_cannot_use_development_input")
                rows = smoke_rows()
                summary.update(sourceRead=False, smokeInputSha256=common.sha_json(rows), selectedIds=[r["id"] for r in rows])
            else:
                require(args.input is not None, "input_required_for_run")
                rows, identity = screen.read_screen(args.input)
                summary.update(identity)
                summary["sourceRead"] = True
            summary["selectedCount"] = len(rows)
            terms, summary["catalog"] = common.read_catalog()
            require(summary["preflightBeforeIntegrity"]["physicalPassed"], "insufficient_physical_memory")
            require(summary["preflightBeforeIntegrity"]["commitPassed"], "insufficient_commit_memory")
            installation_path = setup.DEST / "installation-manifest.json"
            summary["installationManifestSha256"] = common.digest(installation_path)
            summary["installation"] = setup.verify_installation()
            template, summary["ggufContract"] = gguf_contract(setup.DEST / setup.MODEL["name"])
            if identity:
                screen.assert_input_unchanged(identity)
            common.check_identity_unchanged(summary["codeHashes"])
            require(common.digest(common.CATALOG) == summary["catalog"]["sha256"], "catalog_changed_before_spawn")
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            key = secrets.token_urlsafe(32)
            command = server_command(port, key)
            redacted = command.copy()
            redacted[redacted.index("--api-key") + 1] = "[EPHEMERAL_REDACTED]"
            summary["runtimeCommand"] = redacted
            summary["preflightImmediatelyBeforeSpawn"] = preflight(ram_budget_gib=args.ram_budget_gib)
            require(summary["preflightImmediatelyBeforeSpawn"]["physicalPassed"], "insufficient_physical_memory")
            require(summary["preflightImmediatelyBeforeSpawn"]["commitPassed"], "insufficient_commit_memory")
            sys.path.insert(0, str(OWNER.parent))
            from process_owner import claim_process_owner
            original_owner = claim_process_owner()
            limiter = WorkingSetLimit(original_owner, maximum_bytes=summary["preflightImmediatelyBeforeSpawn"]["requestedHardMaximumWorkingSetBytes"])
            summary["jobWorkingSetLimit"] = limiter.job_record
            owner = SuspendedProcessOwner(original_owner, limiter)
            log_path = output / "runtime.log"
            log = log_path.open("xb")
            load_started = time.monotonic()
            process = owner.spawn(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                cwd=setup.DEST / "runtime-cpu", env=common.runtime_environment())
            summary["childPid"] = process.pid

            summary["suspendedCreation"] = owner.creation_receipt
            summary["childWorkingSetLimit"] = limiter.apply_child(process)
            common.write_once(output / "start.json", summary)
            monitor = MemoryMonitor(process, limiter, output / "memory-samples.jsonl", started, load_started,
                                    maximum_working_set=args.ram_budget_gib * GIB)
            monitor.start()
            client = common.LocalClient(f"http://127.0.0.1:{port}", key)
            while time.monotonic() - load_started < STARTUP_SECONDS:
                monitor.check()
                try:
                    if client.request("/health", timeout=2).get("status") == "ok":
                        break
                except (urllib.error.URLError, TimeoutError):
                    pass
                time.sleep(0.25)
            else:
                raise common.RunError("runtime_startup_timeout")
            monitor.ready()
            monitor.check()
            summary["loadSeconds"] = round(time.monotonic() - load_started, 6)
            summary["modelLoaded"] = True
            log.flush()
            summary["actualRuntime"] = validate_runtime(client, template, log_path.read_text("utf-8", errors="replace"))
            with (output / "predictions.jsonl").open("x", encoding="utf-8", newline="\n") as destination:
                for row in rows:
                    current = row["id"]
                    if identity:
                        screen.assert_input_unchanged(identity)
                    common.check_identity_unchanged(summary["codeHashes"])
                    monitor.check()
                    content, matches = common.build_user_prompt(row, "contextual", terms)
                    prompt = render_prompt(template, content)
                    tokens = client.request("/tokenize", {"content": prompt, "add_special": False, "parse_special": True}, timeout=15)["tokens"]
                    validate_prompt_tokens(tokens)
                    before_memory = monitor.sample_and_check()
                    before = time.monotonic()
                    monitor.begin_request()
                    try:
                        response = client.request("/completion", SAMPLING | {"prompt": tokens}, timeout=REQUEST_SECONDS)
                    finally:
                        monitor.end_request()
                    elapsed = time.monotonic() - before
                    # Persist every raw server response before validation, including a failed one.
                    raw_path = output / (row["id"] + ".raw-response.json")
                    common.write_once(raw_path, response)
                    monitor.check()
                    record = prediction(row, prompt, matches, tokens, response, elapsed)
                    record.update(rawResponseFile=raw_path.name, rawResponseSha256=common.digest(raw_path),
                                  memoryBefore=before_memory, memoryAfter=monitor.sample_and_check())
                    destination.write(json.dumps(record, ensure_ascii=False) + "\n")
                    destination.flush()
                    os.fsync(destination.fileno())
                    records.append(record)
                    summary["completed"] = len(records)
                    print(json.dumps({"event": "hymt30-screen", "id": row["id"], "completed": len(records),
                                      "total": len(rows), "seconds": record["generationSeconds"]}), flush=True)
                    current = None
            require(len(records) == len(rows), "incomplete_generation")
            monitor.sample_and_check()
            summary["status"] = "completed"
    except BaseException as error:
        summary["failure"] = {"type": type(error).__name__, "currentId": current,
            "code": str(error) if isinstance(error, common.RunError) else type(error).__name__}
        if current:
            common.write_once(output / "failed-item.json", {"id": current, "status": "failed", "failure": summary["failure"]})
    finally:
        if monitor:
            try:
                monitor.close()
            except BaseException as error:
                summary.update(status="failed", monitorCleanupError=type(error).__name__)
            summary["memoryMonitoring"] = monitor.receipt()
            if monitor.abort_reason or monitor.monitor_error or monitor.kill_error:
                summary.update(status="failed", memoryOrTimeGuardAborted=True)
        try:
            summary["childProcessStopped"] = common.stop_process(process)
            if summary["childProcessStopped"] is not True:
                summary.update(status="failed", cleanupError="owned_child_did_not_stop")
        except BaseException as error:
            summary.update(status="failed", cleanupError=type(error).__name__, childProcessStopped=False)
        try:
            # Sample after the owned child exits, before the final whole-file hash.
            summary["memoryAfterChildCleanup"] = screen.memory_status()
        except BaseException as error:
            summary.update(status="failed", cleanupMemoryError=type(error).__name__)
        if log:
            try:
                log.close()
            except BaseException as error:
                summary.update(status="failed", logCleanupError=type(error).__name__)
        if any(summary.get(k) for k in ("memoryGuardAborted", "timeGuardAborted", "memoryMonitorError")):
            summary["status"] = "failed"
        try:
            if identity:
                screen.assert_input_unchanged(identity)
            if "installation" in summary:
                require(common.digest(setup.DEST / "installation-manifest.json") == summary["installationManifestSha256"],
                        "installation_manifest_changed_during_run")
                require(setup.verify_installation() == summary["installation"], "installation_changed_during_run")
            if "catalog" in summary:
                require(common.digest(common.CATALOG) == summary["catalog"]["sha256"], "catalog_changed_during_run")
            if "codeHashes" in summary:
                common.check_identity_unchanged(summary["codeHashes"])
        except BaseException as error:
            summary.update(status="failed", finalIntegrityError=type(error).__name__)
        summary.update(elapsedSeconds=round(time.monotonic() - started, 6),
                       finishedAt=datetime.now(timezone.utc).isoformat(),
                       automaticChecksPassed=sum(r["automaticChecksPassed"] for r in records),
                       completionStatusMeaning="requests completed; not semantic or output-integrity approval",
                       outputIntegrityPassed=bool(records) and all(r["outputIntegrityPassed"] for r in records),
                       invalidOutputCount=sum(not r["outputIntegrityPassed"] for r in records),
                       invalidOutputIds=[r["id"] for r in records if not r["outputIntegrityPassed"]],
                       automaticChecksAreSemanticCertification=False)
        # The memory monitor writes bounded streamed JSONL, not an in-memory history.
        summary["artifactHashes"] = {p.name: common.digest(p) for p in output.iterdir() if p.is_file()}
        common.write_once(output / "summary.json", summary)
        print(json.dumps({"status": summary["status"], "output": str(output), "failure": summary["failure"]}), flush=True)
    return 0 if summary["status"] in ("completed", "prepared") else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Required only for --run; source-free preparation does not read this file")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="Start the pinned CPU model and translate the frozen input once")
    parser.add_argument("--smoke", action="store_true", help="With --run, use only two built-in functional inputs")
    parser.add_argument("--ram-budget-gib", type=int, choices=(8, 9, 10, 11, 12), default=12)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
