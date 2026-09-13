"""Experimental Qwen35 review runner. Default prepares files; --run loads one model.

No inference is performed on import, help, or preparation. Existing ownership
helpers are imported only in an explicitly requested Windows CPython 3.11 run.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import http.client
import io
import json
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
import urllib.request

import contract
import prepare

VERSION = "qwen35-semantic-review-run-v2"
ROOT, DEST = prepare.ROOT, prepare.DEST
GIB = 1024 ** 3
CONTEXT = 8192
OUTPUT_TOKENS = 2048
BUDGET = 8 * GIB
HEADROOM = 3 * GIB
RESOURCE_PROFILE = "qwen35-cpu-8g-v2"
STARTUP_SECONDS, REQUEST_SECONDS, TOTAL_SECONDS = 600, 600, 4 * 3600
RESPONSE_BYTES = 2 * 1024 * 1024
ALIAS = "qwen35-9b-semantic-review-v1"
HELPER_HASHES = {
    "scripts/local-hymt/process_owner.py": "9a00d809466e304359fb47c374c8fc2464ce31cdbc9557fbc9eee089bfe9d8b2",
    "scripts/model-comparison/working_set_limit.py": "4f8a45772a0f3d3f63274c7309bc7e673446eebb47a4a3e71dc558b95a4cbcdb",
    "scripts/model-comparison/suspended_process_owner.py": "f01ca8a2fd5a75e501c343d2632b2f534b4e5f0397fda502449c69c5d363dfcd",
}
NATIVE_SAMPLING = {
    "temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0,
    "presence_penalty": 1.5, "frequency_penalty": 0.0, "repeat_penalty": 1.0,
    "repeat_last_n": CONTEXT, "seed": 20260911,
    "samplers": ["penalties", "top_k", "top_p", "temperature"],
    "n_predict": OUTPUT_TOKENS, "ignore_eos": False, "stop": [],
    "cache_prompt": False, "stream": False, "return_tokens": True, "id_slot": 0,
    "dry_multiplier": 0.0, "mirostat": 0, "dynatemp_range": 0.0,
    "typical_p": 1.0, "xtc_probability": 0.0, "top_n_sigma": -1.0,
}
require = prepare.require
sha_file = prepare.sha_file


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def memory_status():
    require(os.name == "nt", "windows_memory_query_required")
    class Memory(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ("totalPhysical", "availablePhysical", "totalCommit",
                "availableCommit", "totalVirtual", "availableVirtual", "availableExtendedVirtual")]
    value = Memory()
    value.length = ctypes.sizeof(value)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(Memory)]
    kernel.GlobalMemoryStatusEx.restype = ctypes.c_int
    require(kernel.GlobalMemoryStatusEx(ctypes.byref(value)), "memory_query_failed")
    return {name: int(getattr(value, name)) for name, _ in Memory._fields_ if name != "length"}


def memory_plan(state=None):
    state = state if state is not None else memory_status()
    # Qwen official observed config: 8 full-attention layers, 4 KV heads, D=256.
    # Remaining 24 layers use recurrent state. Scratch/allocator overhead is not measured.
    kv = 2 * 8 * 4 * 256 * 2 * CONTEXT
    recurrent_lower_estimate = 24 * 32 * 128 * 128 * 4
    return {"modelBytes": prepare.MODEL_SIZE, "contextTokens": CONTEXT, "parallelSlots": 1,
            "fullAttentionKvEstimateBytes": kv, "recurrentCoreEstimateBytes": recurrent_lower_estimate,
            "runtimeScratchMeasured": False, "maximumChildWorkingSetBytes": BUDGET,
            "maximumChildPrivateBytes": BUDGET, "freeHeadroomBeforeStartBytes": HEADROOM,
            "minimumAvailablePhysicalBytes": BUDGET + HEADROOM,
            "minimumAvailableCommitBytes": BUDGET + HEADROOM,
            "physicalPassed": state["availablePhysical"] >= BUDGET + HEADROOM,
            "commitPassed": state["availableCommit"] >= BUDGET + HEADROOM,
            "observed": state, "guaranteesNoOutOfMemory": False,
            "resourceProfile": RESOURCE_PROFILE, "workingSetLimitIsAggregateOrCommitCap": False,
            "otherModelsMustBeStoppedByCoordinator": True}


def stat_id(path):
    info = Path(path).stat()
    return [info.st_size, info.st_mtime_ns, info.st_ino]


def verify_installation():
    manifest_path = DEST / "install-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model = manifest["model"]
    require(model["sha256"] == prepare.MODEL_SHA and model["revision"] == prepare.QUANT_REV
            and model["sizeBytes"] == prepare.MODEL_SIZE and model["file"] == prepare.MODEL_NAME,
            "installation_model_identity_differs")
    require(manifest["runtime"]["commit"] == prepare.RUNTIME_COMMIT
            and manifest["runtime"]["archiveSha256"] == prepare.RUNTIME_SHA, "runtime_identity_differs")
    identities = {}
    for path, expected in [(DEST / prepare.MODEL_NAME, prepare.MODEL_SHA),
                           *[(ROOT / item["path"], item["sha256"]) for item in manifest["runtime"]["files"]]]:
        require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), "redirected_installation_path")
        require(sha_file(path) == expected, "installed_file_sha256_differs")
        identities[str(path)] = stat_id(path)
    expected_runtime = {str((ROOT / item["path"]).resolve()) for item in manifest["runtime"]["files"]}
    actual_runtime = {str(p.resolve()) for p in prepare.RUNTIME_DIR.rglob("*") if p.is_file()}
    require(expected_runtime == actual_runtime, "runtime_file_inventory_differs")
    identities[str(manifest_path)] = stat_id(manifest_path)
    return {"manifestSha256": sha_file(manifest_path), "statIdentities": identities}


def gguf_metadata(path):
    """Read at most 16 MiB of the header; do not construct tensors or load weights."""
    with Path(path).open("rb") as source:
        data = source.read(16 * 1024 * 1024)
    stream = io.BytesIO(data)
    formats = {0:"B", 1:"b", 2:"H", 3:"h", 4:"I", 5:"i", 6:"f", 7:"?", 10:"Q", 11:"q", 12:"d"}
    def take(size):
        require(0 <= size <= len(data), "gguf_invalid_length")
        value = stream.read(size)
        require(len(value) == size, "gguf_header_exceeds_limit")
        return value
    def number(fmt):
        return struct.unpack("<" + fmt, take(struct.calcsize("<" + fmt)))[0]
    def string():
        return take(number("Q")).decode("utf-8")
    def value(kind):
        if kind == 8:
            return string()
        if kind == 9:
            inner, count = number("I"), number("Q")
            require(inner != 9 and count <= 1000000, "gguf_invalid_array")
            return [value(inner) for _ in range(count)]
        require(kind in formats, "gguf_unknown_type")
        return number(formats[kind])
    require(take(4) == b"GGUF" and number("I") == 3, "gguf_version")
    tensor_count, count = number("Q"), number("Q")
    require(count < 10000, "gguf_metadata_count")
    metadata = {}
    for _ in range(count):
        key, kind = string(), number("I")
        require(key not in metadata, "gguf_duplicate_key")
        metadata[key] = value(kind)
    template = metadata.get("tokenizer.chat_template")
    require(metadata.get("general.architecture") == "qwen35" and isinstance(template, str)
            and "enable_thinking" in template, "qwen35_template_contract_missing")
    return template, {"architecture": metadata["general.architecture"], "tensorCount": tensor_count,
        "metadataCount": count, "metadataBytes": stream.tell(), "metadataSha256": hashlib.sha256(data[:stream.tell()]).hexdigest(),
        "templateSha256": sha_text(template), "tokenizerEosId": metadata.get("tokenizer.ggml.eos_token_id"),
        "boundaryTokenIds": {token: metadata["tokenizer.ggml.tokens"].index(token) for token in
            ("<|im_start|>", "<|im_end|>", "<|endoftext|>", "<think>", "</think>")},
        "eosTokenText": metadata["tokenizer.ggml.tokens"][metadata["tokenizer.ggml.eos_token_id"]],
        "originalProvenance": {k: v for k, v in metadata.items() if k.startswith("general.") and not isinstance(v, list)},
        "weightTensorsLoaded": False}


def actual_python_executable():
    # Windows Store sys._base_executable can be a non-readable App Execution Alias.
    # Attest this process's loaded image, not the alias or a guessed PATH lookup.
    require(os.name == "nt", "windows_python_identity_required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
    kernel.GetModuleFileNameW.restype = ctypes.c_ulong
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetModuleFileNameW(None, buffer, len(buffer))
    require(0 < length < len(buffer), "loaded_python_path_unavailable")
    result = Path(buffer.value)
    require(result.is_file(), "loaded_python_executable_unreadable")
    return result


def code_hashes():
    for relative, expected in HELPER_HASHES.items():
        require(sha_file(ROOT / relative) == expected, "readonly_ownership_helper_changed")
    paths = [Path(__file__), Path(prepare.__file__), Path(contract.__file__),
             Path(__file__).with_name("prompt-v1.txt"), Path(__file__).with_name("response-schema-v1.json"),
             Path(__file__).with_name("resource-profiles.json"),
             *[ROOT / key for key in HELPER_HASHES], Path(sys.executable), actual_python_executable()]
    return {str(path.resolve()): sha_file(path) for path in paths}


def verify_unchanged(hashes, installation, input_path, input_sha):
    require(sha_file(input_path) == input_sha, "input_changed")
    require(all(sha_file(path) == expected for path, expected in hashes.items()), "code_or_python_changed")
    require(all(stat_id(path) == expected for path, expected in installation["statIdentities"].items()),
            "installation_stat_changed")


def server_command(port, key):
    return [str(prepare.RUNTIME_DIR / "llama-server.exe"), "--model", str(DEST / prepare.MODEL_NAME),
            "--alias", ALIAS, "--host", "127.0.0.1", "--port", str(port), "--api-key", key,
            "--ctx-size", str(CONTEXT), "--parallel", "1", "--threads", "8", "--threads-batch", "8",
            "--batch-size", "128", "--ubatch-size", "128", "--gpu-layers", "0", "--device", "none",
            "--fit", "off", "--cache-type-k", "f16", "--cache-type-v", "f16", "--ctx-checkpoints", "0",
            "--no-context-shift", "--no-webui", "--jinja", "--reasoning-format", "none",
            "--chat-template-kwargs", '{"enable_thinking":false}']


def stop_owned(process):
    if process is None:
        return True
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    return process.poll() is not None


class Guard:
    def __init__(self, process, limiter, output):
        self.process, self.limiter = process, limiter
        self.started = time.monotonic()
        self.deadline = self.started + STARTUP_SECONDS
        self.error = None
        self.kill_error = None
        self.stop = threading.Event()
        self.stream = (output / "memory.jsonl").open("x", encoding="utf-8", newline="\n")
        self.thread = threading.Thread(target=self.loop, name="qwen35-owned-memory-guard", daemon=True)
    @staticmethod
    def reason(state, child, now, started, deadline):
        if now - started >= TOTAL_SECONDS:
            return "total_deadline"
        if deadline is not None and now >= deadline:
            return "request_or_startup_deadline"
        if state["availablePhysical"] < GIB or state["availableCommit"] < GIB:
            return "system_memory_headroom_low"
        if child["workingSetBytes"] > BUDGET or child["peakWorkingSetBytes"] > BUDGET or child["privateBytes"] > BUDGET:
            return "child_memory_budget_exceeded"
        return None
    def loop(self):
        while not self.stop.wait(0.5):
            try:
                if self.process.poll() is not None:
                    self.error = "owned_runtime_exited"
                    return
                state, child, now = memory_status(), self.limiter.sample_child(self.process), time.monotonic()
                self.error = self.reason(state, child, now, self.started, self.deadline)
                self.stream.write(json.dumps({"at": prepare.utc(), "system": state, "child": child,
                                              "abortReason": self.error}) + "\n")
                self.stream.flush()
            except BaseException as error:
                self.error = "guard_failed_" + type(error).__name__
            if self.error:
                try:
                    if self.process.poll() is None:
                        self.process.kill()
                except BaseException as error:
                    self.kill_error = type(error).__name__
                return
    def check(self):
        require(self.error is None, self.error or "guard_failed")
        require(self.process.poll() is None, "owned_runtime_exited")
    def close(self):
        self.stop.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=3)
        require(not self.thread.is_alive(), "guard_thread_did_not_stop")
        self.stream.close()


class Client:
    def __init__(self, port, key):
        self.base = f"http://127.0.0.1:{port}"
        self.key = key
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    def call(self, endpoint, body=None, *, timeout=30, raw_path=None):
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + endpoint, data=payload,
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            chunks, length = [], 0
            try:
                while length <= RESPONSE_BYTES:
                    chunk = response.read(min(65536, RESPONSE_BYTES + 1 - length))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    length += len(chunk)
            except http.client.IncompleteRead as error:
                chunks.append(error.partial)
                raise
            finally:
                raw = b"".join(chunks)
                if raw_path is not None:
                    prepare.once(raw_path, raw)
            require(len(raw) <= RESPONSE_BYTES, "response_byte_limit")
            require(response.status == 200, "runtime_http_status_" + str(response.status))
            return json.loads(raw.decode("utf-8"))


def completion_payload(request, tokens):
    require(type(tokens) is list and all(type(t) is int and t >= 0 for t in tokens), "invalid_prompt_tokens")
    require(0 < len(tokens) + OUTPUT_TOKENS < CONTEXT, "prompt_output_context_budget")
    return NATIVE_SAMPLING | {"prompt": tokens, "json_schema": contract.load_schema()}


def validate_native(response, prompt):
    require(response.get("stop_type") == "eos" and response.get("truncated") is False,
            "response_not_complete_eos")
    require(response.get("stopping_word", "") == "", "unexpected_stop_word")
    require(response.get("prompt") == prompt, "actual_prompt_differs")
    tokens, content = response.get("tokens"), response.get("content")
    require(isinstance(content, str) and content.strip() and isinstance(tokens, list)
            and 0 < len(tokens) < OUTPUT_TOKENS, "response_empty_or_output_limit")
    require(not any(tag in content for tag in ("<think>", "</think>", "<|im_start|>", "<|im_end|>")),
            "response_control_or_thinking_leak")
    settings = response.get("generation_settings", {})
    for key in ("temperature", "top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty",
                "repeat_penalty", "repeat_last_n", "seed", "samplers", "n_predict", "ignore_eos", "stop",
                "dry_multiplier", "mirostat", "dynatemp_range", "typical_p", "xtc_probability", "top_n_sigma"):
        actual, wanted = settings.get(key), NATIVE_SAMPLING[key]
        require(abs(actual - wanted) < 1e-5 if isinstance(wanted, float) and type(actual) in (int, float)
                else actual == wanted, "actual_sampling_differs_" + key)
    return content


def execute(args, plan, rows, output):
    process = guard = log = None
    current = None
    finished = set()
    plan.update(status="failed", childProcessStopped=True, generationRequests=0, modelLoaded=False)
    try:
        require(os.name == "nt" and sys.version_info[:2] == (3, 11), "native_run_requires_windows_cpython311")
        require(bool(args.slot_approval), "coordinator_slot_approval_receipt_required")
        check = memory_plan()
        plan["memoryPreflight"] = check
        require(check["physicalPassed"] and check["commitPassed"], "insufficient_single_model_headroom")
        verify_unchanged(plan["codeHashes"], plan["installation"], args.input, plan["inputSha256"])
        template, metadata = gguf_metadata(DEST / prepare.MODEL_NAME)
        plan["ggufMetadata"] = metadata
        sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
        sys.path.insert(0, str(ROOT / "scripts/local-hymt"))
        from process_owner import claim_process_owner
        from working_set_limit import WorkingSetLimit
        from suspended_process_owner import SuspendedProcessOwner
        owner = claim_process_owner()
        limiter = WorkingSetLimit(owner, maximum_bytes=BUDGET - 64 * 1024 ** 2)
        launcher = SuspendedProcessOwner(owner, limiter)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        key = secrets.token_hex(32)
        command = server_command(port, key)
        environment = {k: v for k, v in os.environ.items() if not k.startswith(("LLAMA_", "HF_"))}
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", NO_PROXY="127.0.0.1,localhost")
        log = (output / "runtime.log").open("xb")
        process = launcher.spawn(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 cwd=prepare.RUNTIME_DIR, env=environment)
        plan.update(childProcessStopped=False, ownedPid=process.pid, spawn=launcher.creation_receipt,
                    workingSet=limiter.apply_child(process))
        guard = Guard(process, limiter, output)
        guard.thread.start()
        client = Client(port, key)
        while True:
            guard.check()
            try:
                if client.call("/health", timeout=2).get("status") == "ok":
                    break
            except (urllib.error.URLError, TimeoutError, ValueError):
                pass
            time.sleep(0.25)
        guard.deadline = None
        props = client.call("/props", raw_path=output / "runtime-props.raw.json")
        require(Path(props.get("model_path", "")).resolve() == (DEST / prepare.MODEL_NAME).resolve()
                and props.get("chat_template") == template and props.get("total_slots") == 1
                and props.get("default_generation_settings", {}).get("n_ctx") == CONTEXT, "loaded_runtime_contract_differs")
        log.flush()
        logged = (output / "runtime.log").read_text(encoding="utf-8", errors="replace")
        require("printing all EOG tokens:" in logged, "runtime_eog_evidence_missing")
        eog = {int(i): value for i, value in re.findall(r"^.*?:\s+-\s+(\d+)\s+\('([^']*)'\)\s*$",
            logged.split("printing all EOG tokens:")[-1], re.MULTILINE)}
        require(eog.get(metadata["tokenizerEosId"]) == metadata["eosTokenText"], "runtime_eos_differs_from_gguf")
        for token, token_id in metadata["boundaryTokenIds"].items():
            actual = client.call("/tokenize", {"content": token, "add_special": False, "parse_special": True})
            require(actual.get("tokens") == [token_id], "runtime_boundary_token_differs")
        plan["runtimeEogTokens"] = eog
        plan["modelLoaded"] = True
        for index, row in enumerate(rows):
            current = row["id"]
            prefix = f"{index + 1:04d}"
            guard.check()
            guard.deadline = time.monotonic() + REQUEST_SECONDS
            verify_unchanged(plan["codeHashes"], plan["installation"], args.input, plan["inputSha256"])
            request = contract.build_request(row) | {"model": ALIAS}
            applied = client.call("/apply-template", request, raw_path=output / (prefix + "-template.raw.json"))
            prompt = applied.get("prompt")
            require(isinstance(prompt, str) and prompt.endswith("<think>\n\n</think>\n\n"),
                    "nonthinking_template_suffix_unvalidated")
            tokens = client.call("/tokenize", {"content": prompt, "add_special": False, "parse_special": True},
                                 raw_path=output / (prefix + "-tokenize.raw.json"))["tokens"]
            native = completion_payload(request, tokens)
            prepare.json_once(output / (prefix + "-native-request.json"), native)
            guard.check()
            before = time.monotonic()
            plan["generationRequests"] += 1
            raw_path = output / (prefix + "-completion.raw.json")
            response = client.call("/completion", native, timeout=REQUEST_SECONDS, raw_path=raw_path)
            guard.check()
            content = validate_native(response, prompt)
            assessment = contract.validate_response(row, content)
            record = {"id": row["id"], "status": "completed" if assessment["status"] == "valid" else "invalid_structure",
                      "seconds": time.monotonic() - before,
                      "rawResponseFile": raw_path.name, "rawResponseSha256": sha_file(raw_path),
                      "promptSha256": sha_text(prompt), "inputTokens": len(tokens), "outputTokens": len(response["tokens"]),
                      "assessment": assessment, "humanReviewed": False, "semanticQualityCertified": False}
            prepare.json_once(output / (prefix + "-assessment.json"), record)
            require(assessment["status"] == "valid", "structured_response_validation_failed")
            finished.add(row["id"])
            current = None
            guard.deadline = None
            print(json.dumps({"event": "review-row", "completed": len(finished), "total": len(rows)}), flush=True)
        plan["status"] = "completed"
    except BaseException as error:
        plan["failure"] = {"type": type(error).__name__, "code": str(error) if isinstance(error, ValueError)
                           else type(error).__name__, "rowId": current}
    finally:
        if guard:
            try:
                guard.close()
                plan["guard"] = {"abortReason": guard.error, "killError": guard.kill_error}
                if guard.error or guard.kill_error:
                    plan["status"] = "failed"
            except BaseException as error:
                plan.update(status="failed", guardCleanupError=type(error).__name__)
        try:
            plan["childProcessStopped"] = stop_owned(process)
            require(plan["childProcessStopped"], "owned_child_still_alive")
        except BaseException as error:
            plan.update(status="failed", childProcessStopped=False, cleanupError=type(error).__name__)
        if log:
            log.close()
        try:
            verify_unchanged(plan["codeHashes"], plan["installation"], args.input, plan["inputSha256"])
            require(verify_installation() == plan["installation"], "installation_final_verification_differs")
            plan["finalIntegrityVerified"] = True
        except BaseException as error:
            plan.update(status="failed", finalIntegrityVerified=False, integrityError=type(error).__name__)
        plan["itemStatuses"] = [{"id": row["id"], "status": "completed" if row["id"] in finished
                                 else "failed" if row["id"] == current else "not_run"} for row in rows]
    return plan


def run(args):
    rows, input_sha = contract.read_input(args.input)
    output = args.output.resolve()
    require(output.is_relative_to((DEST / "runs").resolve()) and output != (DEST / "runs").resolve()
            and not any(p.is_symlink() for p in (args.output, *args.output.parents)), "output_outside_owned_runs")
    installation = verify_installation()
    hashes = code_hashes()
    output.mkdir(parents=True, exist_ok=False)
    plan = {"version": VERSION, "createdAt": prepare.utc(), "status": "prepared", "inputSha256": input_sha,
            "inputFields": ["source", "translation", "context"], "referencesOrJudgmentsIncluded": False,
            "codeHashes": hashes, "installation": installation, "expectedCount": len(rows),
            "sampling": NATIVE_SAMPLING, "thinking": {"enable_thinking": False, "suffixMustBeVerifiedAtRuntime": True},
            "resourceProfile": RESOURCE_PROFILE, "memoryPlan": memory_plan(),
            "slotApprovalReceipt": args.slot_approval, "contextTokens": CONTEXT,
            "modelLoaded": False, "generationRequests": 0, "humanReviewed": False, "trainingPerformed": False,
            "semanticQualityCertified": False, "automaticRetries": False, "appRegistrationPerformed": False,
            "timeLimitsSeconds": {"startup": STARTUP_SECONDS, "request": REQUEST_SECONDS, "total": TOTAL_SECONDS},
            "responseByteLimit": RESPONSE_BYTES, "runRequested": args.run}
    prepare.once(output / "requests.jsonl", "".join(json.dumps({"id": row["id"],
        "request": contract.build_request(row)}, ensure_ascii=False) + "\n" for row in rows).encode("utf-8"))
    prepare.json_once(output / "plan.json", plan)
    if args.run:
        lock_path = DEST / ".run.lock"
        try:
            with lock_path.open("x", encoding="utf-8") as lock:
                lock.write(str(os.getpid()))
        except FileExistsError:
            plan.update(status="failed", failure={"code": "existing_owned_run_lock_preserved"})
        else:
            try:
                execute(args, plan, rows, output)
            finally:
                if plan.get("childProcessStopped") is True:
                    lock_path.unlink()
                else:
                    plan["runLockPreservedBecauseChildStopUnconfirmed"] = True
    plan["finishedAt"] = prepare.utc()
    plan["artifacts"] = {p.name: sha_file(p) for p in output.iterdir() if p.is_file()}
    prepare.json_once(output / "summary.json", plan)
    print(json.dumps({"event": "finished", "status": plan["status"], "modelLoaded": plan["modelLoaded"],
                      "generationRequests": plan["generationRequests"]}), flush=True)
    return 0 if plan["status"] in ("prepared", "completed") else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--slot-approval", default=None, help="coordinator's actual approval receipt, required with --run")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        prepare.json_once(DEST / f"run-preparation-failure-{time.time_ns()}.json",
                          {"at": prepare.utc(), "type": type(error).__name__, "message": str(error),
                           "runtimeVersion": VERSION, "note": "Check any per-run summary separately for native state."})
        raise
