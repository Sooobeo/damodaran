"""Audit only the fixed meaning-v2 dev8 vocabulary tokens; explicit --run only.

No generation, weight tensor loading, installation, network restore or reuse of
old prompt parity. Actual v5 native apply-template parity remains unmeasured
until the first generation request has independently matched this Jinja audit.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import contract_v2 as contract
import prepare
import runtime_v5

VERSION = "qwen35-vocab-only-token-budget-v2"
ROOT = prepare.ROOT
OUTPUT = prepare.DEST / "token-budget-v2"
INPUT = prepare.DEST / "inputs/semantic-v2-dev8-v1.jsonl"
INPUT_SHA = "55e52db41f16e35998eab1f3835f60aafc7e1a875a38b1d32a00dd5ec49e68ce"
SOURCE_CACHE = prepare.DEST / "token-budget-v1/attempt-001/evidence/tokenize.cpp"
SOURCE_SHA = "db0cd035294b91009250029a1435a1a688d72eb489c406b630d2293b30d6fb34"
TOKENIZER = prepare.RUNTIME_DIR / "llama-tokenize.exe"
MODEL = prepare.DEST / prepare.MODEL_NAME
EXPECTED_ROWS = 8
CONTEXT_TOKENS, OUTPUT_RESERVE = 4096, 2048
SOURCE_URL = ("https://raw.githubusercontent.com/ggml-org/llama.cpp/"
              "72797e89198ab564fd0e6baa54ab196e8dd1d884/tools/tokenize/tokenize.cpp")
BELOW_NORMAL = 0x00004000
CREATE_NO_WINDOW = 0x08000000
CHILD_SECONDS = 120
MAX_NATIVE_BYTES = 4 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def sha_file(path):
    return prepare.sha_file(path)


class Artifacts:
    """Exclusive writes within one newly created attempt; no overwrite/delete."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.hashes = {}
        self.native_receipts = []

    def write(self, relative, raw):
        path = self.root / relative
        require(path.resolve().is_relative_to(self.root), "artifact_outside_attempt")
        require(not any(p.is_symlink() for p in (path, *path.parents)), "artifact_symlink")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
        self.hashes[relative] = sha_bytes(raw)

    def json(self, relative, value):
        self.write(relative, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def new_attempt():
    require(not any(p.is_symlink() for p in (OUTPUT, *OUTPUT.parents)), "output_symlink")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1000):
        target = OUTPUT / f"attempt-{number:03d}"
        try:
            target.mkdir()
        except FileExistsError:
            continue
        return Artifacts(target)
    raise ValueError("attempt_limit")


def read_plan_inputs():
    rows, digest = contract.read_input(INPUT)
    require(len(rows) == EXPECTED_ROWS and digest == INPUT_SHA, "fixed_dev8_input_changed")
    return rows, digest


def render_prompts(rows, template):
    import jinja2
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    require(jinja2.__version__ == "3.1.6", "jinja_version_changed")
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True,
                                       keep_trailing_newline=True, undefined=jinja2.StrictUndefined)
    def reject(_message):
        raise ValueError("template_rejected_input")
    env.globals["raise_exception"] = reject
    compiled = env.from_string(template)
    prompts = []
    for row in rows:
        request = contract.build_request(row)
        prompt = compiled.render(messages=request["messages"], tools=[],
                                 add_generation_prompt=True, enable_thinking=False, add_vision_id=False)
        require(prompt.endswith("<think>\n\n</think>\n\n"), "nonthinking_suffix_changed")
        prompts.append(prompt)
    return prompts


def parse_tokenizer_ids(raw):
    try:
        values = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid_tokenizer_json") from error
    require(type(values) is list and values and
            all(type(value) is int and value >= 0 for value in values), "invalid_token_ids")
    return values


def check_budget(ids, context=CONTEXT_TOKENS, reserve=OUTPUT_RESERVE):
    require(type(ids) is list and ids and all(type(t) is int and t >= 0 for t in ids), "invalid_token_ids")
    require(type(context) is int and type(reserve) is int and context > 0 and reserve > 0,
            "invalid_token_budget")
    total = len(ids) + reserve
    return {"inputTokens": len(ids), "reservedOutputTokens": reserve, "contextTokens": context,
            "totalReservedTokens": total, "remainingTokens": context - total,
            "fitsStrict": total < context}


def longest_common_prefix(sequences):
    require(type(sequences) is list and sequences and all(type(s) is list for s in sequences),
            "invalid_prefix_sequences")
    length = 0
    for values in zip(*sequences):
        if any(value != values[0] for value in values[1:]):
            break
        length += 1
    return length


def boundary_positions(prompt, ids, boundary_ids):
    raw = prompt.encode("utf-8")
    user_boundary = b"<|im_start|>user\n"
    require(raw.startswith(b"<|im_start|>system\n") and raw.count(user_boundary) == 1,
            "unexpected_text_message_boundaries")
    starts = [index for index, value in enumerate(ids) if value == boundary_ids["<|im_start|>"]]
    ends = [index for index, value in enumerate(ids) if value == boundary_ids["<|im_end|>"]]
    require(len(starts) == 3 and len(ends) == 2 and starts[0] == 0
            and starts[0] < ends[0] < starts[1] < ends[1] < starts[2],
            "unexpected_token_message_boundaries")
    return {"systemEndBoundaryTokenIndex": ends[0], "firstUserBoundaryTokenIndex": starts[1],
            "systemEndBoundaryByteOffset": raw.index(b"<|im_end|>"),
            "firstUserBoundaryByteOffset": raw.index(user_boundary),
            "systemEndBoundaryTokenId": boundary_ids["<|im_end|>"],
            "firstUserBoundaryTokenId": boundary_ids["<|im_start|>"],
            "tokenIndicesAreZeroBased": True, "byteOffsetsAreUtf8": True}


def shared_prefix_audit(token_arrays, positions):
    require(len(token_arrays) >= 2 and len(token_arrays) == len(positions), "prefix_audit_row_count")
    adjacent = [{"fromIndex": index, "toIndex": index + 1,
                 "commonPrefixTokens": longest_common_prefix([token_arrays[index - 1], token_arrays[index]])}
                for index in range(1, len(token_arrays))]
    return {"allRowsLongestCommonPrefixTokens": longest_common_prefix(token_arrays),
            "adjacentMinimumCommonPrefixTokens": min(item["commonPrefixTokens"] for item in adjacent),
            "adjacentMaximumCommonPrefixTokens": max(item["commonPrefixTokens"] for item in adjacent),
            "adjacentPairs": adjacent, "messageBoundaries": positions,
            "cacheReuseDemonstrated": False, "recurrentCheckpointStateObserved": False,
            "sharedInputTokensOnly": True}


def tokenizer_command():
    # --no-parse-special is deliberately absent: b10888 defaults parse_special=true.
    # The tool's fixed vocab_only load performs no decode, prefill, or sampling.
    return [str(TOKENIZER), "--model", str(MODEL), "--ids", "--no-bos", "--no-escape",
            "--stdin", "--offline", "--threads", "1", "--threads-batch", "1",
            "--poll", "0", "--poll-batch", "0", "--prio", "-1"]


def process_probe(process):
    """Point-in-time evidence; no polling thread and no unrelated process access."""
    require(os.name == "nt", "windows_process_evidence_required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = ctypes.c_void_p(int(process._handle))
    kernel.GetPriorityClass.argtypes = [ctypes.c_void_p]
    kernel.GetPriorityClass.restype = ctypes.c_ulong
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel.GetExitCodeProcess.restype = ctypes.c_int
    kernel.GetProcessId.argtypes = [ctypes.c_void_p]
    kernel.GetProcessId.restype = ctypes.c_ulong
    require(kernel.GetProcessId(handle) == process.pid, "owned_tokenizer_pid_mismatch")
    exit_code = ctypes.c_ulong()
    require(kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)), "child_exit_code_query_failed")
    result = {"priorityClass": int(kernel.GetPriorityClass(handle)),
              "exitCode": int(exit_code.value), "processHandleQueried": True}
    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]
        def value(self):
            return (int(self.high) << 32) | int(self.low)
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(FileTime)] * 4
    kernel.GetProcessTimes.restype = ctypes.c_int
    created, exited, kernel_time, user_time = [FileTime() for _ in range(4)]
    require(kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                   ctypes.byref(kernel_time), ctypes.byref(user_time)), "owned_tokenizer_times_failed")
    require(created.value() > 0, "owned_tokenizer_creation_missing")
    result.update(pid=process.pid, createdFileTime100ns=created.value(), exitedFileTime100ns=exited.value(),
                  kernelCpuSeconds=kernel_time.value() / 10000000,
                  userCpuSeconds=user_time.value() / 10000000)
    class MemoryCounters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("pageFaultCount", ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ("peakWorkingSetBytes", "workingSetBytes",
                "quotaPeakPagedPoolUsage", "quotaPagedPoolUsage", "quotaPeakNonPagedPoolUsage",
                "quotaNonPagedPoolUsage", "pagefileUsage", "peakPagefileUsage", "privateBytes")]
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(MemoryCounters), ctypes.c_ulong]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
        result["memory"] = {name: int(getattr(counters, name)) for name in
                            ("peakWorkingSetBytes", "workingSetBytes", "privateBytes", "peakPagefileUsage")}
    else:
        result["memoryQueryWinerror"] = ctypes.get_last_error()
    return result


def run_owned_tokenizer(owner, command, stdin_bytes, *, probe=process_probe):
    require(type(stdin_bytes) is bytes, "binary_stdin_required")
    child_env = {k: v for k, v in os.environ.items() if not k.startswith(("LLAMA_", "HF_"))}
    child_env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    started = time.monotonic()
    child = None
    receipt = {"pid": None, "ownerProcessId": owner.process_id, "createdAt": utc(),
               "ownershipVerifiedBySpawn": False, "creationFlags": BELOW_NORMAL | CREATE_NO_WINDOW,
               "command": command, "stdinBytes": len(stdin_bytes), "stdinSha256": sha_bytes(stdin_bytes),
               "sequentialChildOnly": True, "pollingLoopUsed": False, "globalEnvironmentChanged": False,
               "timeoutSeconds": CHILD_SECONDS, "timedOut": False, "childStopped": False}
    stdout = stderr = b""
    try:
        child = owner.spawn(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=BELOW_NORMAL | CREATE_NO_WINDOW, cwd=prepare.RUNTIME_DIR, env=child_env)
        receipt.update(pid=child.pid, ownershipVerifiedBySpawn=True)
        receipt["atStart"] = probe(child)
        require(receipt["atStart"]["priorityClass"] == BELOW_NORMAL, "child_priority_not_below_normal")
        require(receipt["atStart"]["pid"] == child.pid and receipt["atStart"]["createdFileTime100ns"] > 0,
                "child_creation_identity_missing")
        stdout, stderr = child.communicate(input=stdin_bytes, timeout=CHILD_SECONDS)
    except subprocess.TimeoutExpired:
        receipt["timedOut"] = True
    except BaseException as error:
        receipt["failure"] = {"type": type(error).__name__,
                              "code": getattr(error, "code", str(error) if isinstance(error, ValueError)
                                              else type(error).__name__)}
        if child is None:
            # There is no returned verified handle. Never infer successful stop
            # from None, even if the spawn helper attempted its own cleanup.
            receipt["spawnCleanupUnconfirmed"] = True
    finally:
        if child is not None:
            try:
                if child.poll() is None:
                    child.kill()
                    stdout, stderr = child.communicate(timeout=10)
                receipt["childStopped"] = child.poll() is not None
                require(receipt["childStopped"], "owned_tokenizer_stop_unconfirmed")
                receipt["atExit"] = probe(child)
                initial = receipt.get("atStart", {})
                require(receipt["atExit"]["pid"] == child.pid
                        and receipt["atExit"]["createdFileTime100ns"] == initial.get("createdFileTime100ns")
                        and receipt["atExit"]["exitedFileTime100ns"] > 0,
                        "owned_tokenizer_exit_identity_changed")
            except BaseException as error:
                receipt["cleanupError"] = {"type": type(error).__name__,
                                         "code": str(error) if isinstance(error, ValueError) else type(error).__name__}
                # Closure proof must include the same creation time, not just a
                # reused numeric PID or a failed OS query.
                receipt["childStopped"] = False
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()
        receipt.update(seconds=time.monotonic() - started, exitCode=None if child is None else child.returncode,
                       finishedAt=utc())
    return stdout, stderr, receipt


def record_native(artifacts, name, owner, command, stdin_bytes):
    artifacts.write(name + ".stdin.bin", stdin_bytes)
    stdout, stderr, receipt = run_owned_tokenizer(owner, command, stdin_bytes)
    artifacts.native_receipts.append(receipt)
    artifacts.write(name + ".stdout.bin", stdout)
    artifacts.write(name + ".stderr.bin", stderr)
    artifacts.json(name + ".process.json", receipt)
    require(receipt["childStopped"] and not receipt["timedOut"] and receipt["exitCode"] == 0
            and "failure" not in receipt and "cleanupError" not in receipt,
            "owned_tokenizer_failed_or_not_stopped")
    require(len(stdout) <= MAX_NATIVE_BYTES and len(stderr) <= MAX_NATIVE_BYTES, "native_output_too_large")
    return stdout, stderr, receipt


def attest_runtime_files():
    require(sha_file(prepare.RUNTIME_ARCHIVE) == prepare.RUNTIME_SHA, "pinned_runtime_archive_differs")
    files = {}
    with zipfile.ZipFile(prepare.RUNTIME_ARCHIVE) as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            relative = Path(entry.filename)
            require(not relative.is_absolute() and ".." not in relative.parts, "archive_path_invalid")
            target = prepare.RUNTIME_DIR / relative
            require(target.resolve().is_relative_to(prepare.RUNTIME_DIR.resolve()) and target.is_file()
                    and not any(p.is_symlink() for p in (target, *target.parents)), "runtime_file_redirected")
            expected = sha_bytes(archive.read(entry))
            require(sha_file(target) == expected, "runtime_file_differs_from_pinned_archive")
            files[str(target.resolve())] = expected
    require(str(TOKENIZER.resolve()) in files, "tokenizer_missing_from_archive")
    actual = {str(path.resolve()) for path in prepare.RUNTIME_DIR.rglob("*") if path.is_file()}
    require(actual == set(files), "runtime_file_inventory_differs")
    return files


def python_code_identity():
    import jinja2
    import markupsafe
    files = [Path(__file__), Path(__file__).with_name("test_token_budget_v2.py"),
             Path(contract.__file__), Path(__file__).with_name("contract.py"),
             Path(prepare.__file__), Path(runtime_v5.__file__),
             contract.PROMPT_PATH, contract.SCHEMA_PATH, ROOT / "scripts/local-hymt/process_owner.py",
             Path(sys.executable), runtime_v5.actual_python_executable()]
    dll = Path(sys.base_prefix) / "python311.dll"
    require(dll.is_file(), "loaded_python_dll_path_unavailable")
    files.append(dll)
    for module in (jinja2, markupsafe):
        files.extend(path for path in Path(module.__file__).parent.rglob("*")
                     if path.is_file() and path.suffix in (".py", ".pyd"))
    return {"pythonVersion": sys.version, "jinjaVersion": jinja2.__version__,
            "interpreter": str(runtime_v5.actual_python_executable()),
            "files": {str(path.resolve()): sha_file(path) for path in files}}


def read_cached_official_source():
    require(SOURCE_CACHE.is_file() and not SOURCE_CACHE.is_symlink(), "official_tokenizer_source_cache_missing")
    raw = SOURCE_CACHE.read_bytes()
    require(sha_bytes(raw) == SOURCE_SHA, "official_tokenizer_source_cache_changed")
    code = raw.decode("utf-8")
    require("model_params.vocab_only = true;" in code and
            "tokens = common_tokenize(vocab, prompt, add_bos, parse_special);" in code and
            not any(name in code for name in ("llama_decode(", "llama_encode(", "llama_sampler_sample(")),
            "official_vocab_only_source_contract_changed")
    return raw


def run():
    require(os.name == "nt" and sys.version_info[:2] == (3, 11) and sys.dont_write_bytecode,
            "windows_cpython311_B_mode_required")
    require(Path(sys.executable).resolve() == (ROOT / ".venv-training/Scripts/python.exe").resolve(),
            "existing_training_interpreter_required")
    # Reject missing fixed code/source evidence before creating a native child.
    require(sha_file(Path(contract.__file__)) == runtime_v5.CONTRACT_FILES["scripts/local-qe/llm-qwen35/contract_v2.py"],
            "contract_v2_code_changed")
    require(sha_file(ROOT / "scripts/local-hymt/process_owner.py") == runtime_v5.HELPER_HASHES["scripts/local-hymt/process_owner.py"],
            "ownership_helper_changed")
    read_cached_official_source()
    artifacts = new_attempt()
    summary = {"version": VERSION, "createdAt": utc(), "status": "failed",
               "generationCalls": 0, "weightTensorsLoaded": False, "vocabularyLoadedOnly": True,
               "nativeExecutable": str(TOKENIZER), "modelExpectedSha256": prepare.MODEL_SHA,
               "fullModelHashRecomputed": False, "semanticOutputsRead": False,
               "contextTokens": CONTEXT_TOKENS, "reservedOutputTokens": OUTPUT_RESERVE,
               "strictCondition": "inputTokens + 2048 < 4096", "rows": [],
               "networkRequests": 0, "priorPromptParityReused": False,
               "nativeTemplateParity": "pending_actual_v5_apply_template",
               "modelInputFields": ["source", "translation", "context"],
               "referencesOrJudgmentsIncluded": False,
               "maximumConcurrentNativeChildren": 1, "nativeProcessCount": 0}
    try:
        rows, input_sha = read_plan_inputs()
        summary.update(inputPath=str(INPUT), inputSha256=input_sha, inputCount=len(rows),
                       contractIdentity=contract.contract_identity())
        template, metadata = runtime_v5.gguf_metadata(MODEL)
        summary["templateSha256"] = metadata["templateSha256"]
        model_stat = runtime_v5.stat_id(MODEL)
        artifacts.write("actual-chat-template.jinja", template.encode("utf-8"))
        artifacts.json("gguf-metadata.json", metadata)
        prompts = render_prompts(rows, template)
        identities = python_code_identity()
        runtime_hashes = attest_runtime_files()
        summary.update(codeAndInterpreter=identities, runtimeFiles=runtime_hashes,
                       runtimeArchiveSha256=prepare.RUNTIME_SHA)
        source = read_cached_official_source()
        artifacts.write("evidence/tokenize.cpp", source)
        summary["officialSource"] = {"url": SOURCE_URL, "sha256": sha_bytes(source),
                                      "localCachePath": str(SOURCE_CACHE), "networkRequestPerformed": False,
                                      "vocabOnlyAssignmentVerified": True, "decodeOrSamplingCallPresent": False}
        sys.path.insert(0, str(ROOT / "scripts/local-hymt"))
        from process_owner import claim_process_owner
        owner = claim_process_owner()
        out, err, _receipt = record_native(artifacts, "evidence/help", owner, [str(TOKENIZER), "--help"], b"")
        summary["nativeProcessCount"] += 1
        help_text = (out + err).decode("utf-8")
        required_flags = ("--ids", "--no-bos", "--no-escape", "--stdin", "--no-parse-special",
                          "--offline", "--threads", "--threads-batch", "--poll", "--poll-batch", "--prio")
        require(all(flag in help_text for flag in required_flags), "tokenizer_required_flags_missing")
        out, err, _receipt = record_native(artifacts, "evidence/version", owner, [str(TOKENIZER), "--version"], b"")
        summary["nativeProcessCount"] += 1
        version_text = (out + err).decode("utf-8")
        require("build 10888" in version_text and "72797e891" in version_text, "tokenizer_version_differs")
        command = tokenizer_command()
        token_arrays, positions, native_receipts = [], [], []
        for index, (row, prompt) in enumerate(zip(rows, prompts), 1):
            require(sha_file(INPUT) == input_sha and runtime_v5.stat_id(MODEL) == model_stat, "input_or_model_changed")
            raw = prompt.encode("utf-8")
            out, err, receipt = record_native(artifacts, f"rows/{index:04d}", owner, command, raw)
            summary["nativeProcessCount"] += 1
            native_receipts.append(receipt)
            ids = parse_tokenizer_ids(out)
            token_arrays.append(ids)
            positions.append({"index": index, **boundary_positions(prompt, ids, metadata["boundaryTokenIds"])})
            result = {"index": index, "id": row["id"], "promptSha256": sha_bytes(raw),
                      "rawTokenIdsFile": f"rows/{index:04d}.stdout.bin",
                      "rawPromptStdinFile": f"rows/{index:04d}.stdin.bin",
                      "processEvidenceFile": f"rows/{index:04d}.process.json",
                      "tokenIdsSha256": sha_bytes(json.dumps(ids, separators=(",", ":")).encode("utf-8")),
                      "seconds": receipt["seconds"], "childPid": receipt["pid"],
                      "childStopped": receipt["childStopped"], **check_budget(ids)}
            artifacts.json(f"rows/{index:04d}.budget.json", result)
            summary["rows"].append(result)
            print(json.dumps({"event": "vocab-only-row", "completed": index, "total": len(rows),
                              "inputTokens": len(ids), "fitsStrict": result["fitsStrict"]}), flush=True)
        prefix_audit = shared_prefix_audit(token_arrays, positions)
        artifacts.json("shared-prefix-audit.json", prefix_audit)
        summary["sharedPrefixAudit"] = prefix_audit
        measurements = [receipt[phase]["memory"] for receipt in native_receipts
                        for phase in ("atStart", "atExit") if "memory" in receipt[phase]]
        summary["vocabularyProcessResources"] = {
            "nativePeakWorkingSetBytes": max((m["peakWorkingSetBytes"] for m in measurements), default=None),
            "nativePeakCommitBytes": max((m["peakPagefileUsage"] for m in measurements), default=None),
            "nativeObservedPrivateBytesMaximum": max((m["privateBytes"] for m in measurements), default=None),
            "privateObservationsArePointInTimeNotPeak": True,
            "peakCommitCounter": "PROCESS_MEMORY_COUNTERS_EX.PeakPagefileUsage",
            "allStartPriorityClassesBelowNormal": all(r["atStart"]["priorityClass"] == BELOW_NORMAL
                                                       for r in native_receipts),
            "totalChildCpuSeconds": sum(r["atExit"].get("kernelCpuSeconds", 0) +
                                        r["atExit"].get("userCpuSeconds", 0) for r in native_receipts),
            "totalChildWallSeconds": sum(r["seconds"] for r in native_receipts)}
        require(all(sha_file(path) == digest for path, digest in
                    (identities["files"] | runtime_hashes | {str(INPUT): input_sha, str(SOURCE_CACHE): SOURCE_SHA}).items()),
                "final_code_runtime_input_or_technical_hash_changed")
        require(runtime_v5.stat_id(MODEL) == model_stat, "final_model_stat_changed")
        summary.update(status="completed", finalIntegrityVerified=True,
                       maximumInputTokens=max(item["inputTokens"] for item in summary["rows"]),
                       minimumRemainingTokens=min(item["remainingTokens"] for item in summary["rows"]),
                       allRowsFitStrict=all(item["fitsStrict"] for item in summary["rows"]),
                       fitCount=sum(item["fitsStrict"] for item in summary["rows"]),
                       allChildrenStopped=all(item["childStopped"] for item in summary["rows"]))
    except BaseException as error:
        summary["failure"] = {"type": type(error).__name__,
                              "code": str(error) if isinstance(error, ValueError) else type(error).__name__}
    summary["nativeProcessCount"] = len(artifacts.native_receipts)
    summary["allChildrenStopped"] = bool(artifacts.native_receipts) and all(
        r.get("childStopped") is True and "cleanupError" not in r for r in artifacts.native_receipts)
    summary["finishedAt"] = utc()
    summary["artifacts"] = dict(artifacts.hashes)
    artifacts.json("summary.json", summary)
    print(json.dumps({"event": "token-budget-finished", "status": summary["status"],
                      "completedRows": len(summary["rows"]), "output": str(artifacts.root),
                      "maximumInputTokens": summary.get("maximumInputTokens"),
                      "allRowsFitStrict": summary.get("allRowsFitStrict")}), flush=True)
    return 0 if summary["status"] == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run only vocabulary tokenization; never generation.")
    args = parser.parse_args()
    if not args.run:
        print(json.dumps({"version": VERSION, "nativeCalls": 0, "generationCalls": 0,
                          "next": "--run (coordinator approval required)", "output": str(OUTPUT),
                          "expectedCount": EXPECTED_ROWS, "nativeTemplateParity": "pending_actual_v5_apply_template",
                          "networkRequests": 0, "priorPromptParityReused": False}))
        return 0
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
