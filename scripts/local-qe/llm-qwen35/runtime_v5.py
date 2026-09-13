"""One fixed eight-row meaning-only diagnostic; preparation does not load a model.

No inference is performed on import, help, or preparation. Existing ownership
helpers are imported only in an explicitly requested Windows CPython 3.11 run.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import http.client
import importlib.util
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

import contract_v2 as contract
import prepare

VERSION = "qwen35-semantic-review-run-v5"
ROOT, DEST = prepare.ROOT, prepare.DEST
GIB = 1024 ** 3
CONTEXT = 4096
OUTPUT_TOKENS = 2048
BUDGET = 6 * GIB
HEADROOM = 3 * GIB
RESOURCE_PROFILE = "qwen35-cpu-6g-4threads-4k-dev8-v4"
STARTUP_SECONDS, REQUEST_SECONDS, TOTAL_SECONDS = 600, 600, 4 * 3600
RESPONSE_BYTES = 2 * 1024 * 1024
ALIAS = "qwen35-9b-semantic-review-v2-dev8"
PROFILE_PATH = Path(__file__).with_name("resource-profile-v4.json")
FIXED_INPUT = DEST / "inputs/semantic-v2-dev8-v1.jsonl"
FIXED_INPUT_SHA = "55e52db41f16e35998eab1f3835f60aafc7e1a875a38b1d32a00dd5ec49e68ce"
EXPECTED_COUNT = 8
EXECUTION_BINDING_VERSION = "qwen-v5-dev8-execution-binding-v1"
CONTRACT_FILES = {
    "scripts/local-qe/llm-qwen35/contract_v2.py": "463561009a9eb966b97893d8447315730cf66b454332f7811c48a96b974b8378",
    "scripts/local-qe/llm-qwen35/contract.py": "d4e6e8fd6e5d09729c1adf745468d383b217cba8da202b98bd66b26a76697a0b",
    "scripts/local-qe/llm-qwen35/screen_gate_v1.py": "a622f193b34403b87199dda0ea2017697f9249464c290e64ec7dbc23c32e283a",
}
MAPPING_FILES = {
    "scripts/local-qe/qwen-material-warning-policy-v3.json": "964593f6d8af8c5b819871922eee82227e6a75ec22042f5f869e263c647929ba",
    "scripts/local-qe/material_warning_v3.py": "c1e8209c8ecc5e4c6ffdb3563c27bc2a6d1eb27de08b652fb6362a3e1bfc9cd7",
}
MESSAGE_DELIMITERS = [{"role": role, "delimiter": "<|im_start|>" + role + "\n"}
                      for role in ("system", "user", "assistant")]
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
    "cache_prompt": True, "stream": False, "return_tokens": True, "id_slot": 0,
    "dry_multiplier": 0.0, "mirostat": 0, "dynatemp_range": 0.0,
    "typical_p": 1.0, "xtc_probability": 0.0, "top_n_sigma": -1.0,
}
require = prepare.require
sha_file = prepare.sha_file


def sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def material_mapping():
    for relative, expected in MAPPING_FILES.items():
        require(expected is not None, "material_warning_v3_final_pin_pending")
        require(sha_file(ROOT / relative) == expected, "material_warning_mapping_changed")
    path = ROOT / "scripts/local-qe/material_warning_v3.py"
    spec = importlib.util.spec_from_file_location("qwen35_material_warning_v3", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(module.VERSION == "qwen-material-warning-policy-v3", "material_warning_version_differs")
    return module


def screen_gate():
    path = Path(__file__).with_name("screen_gate_v1.py")
    expected = CONTRACT_FILES["scripts/local-qe/llm-qwen35/screen_gate_v1.py"]
    require(sha_file(path) == expected, "screen_gate_code_changed")
    spec = importlib.util.spec_from_file_location("qwen35_fixed_screen_gate_v1", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_fixed_input(path=FIXED_INPUT):
    require(Path(path).resolve() == FIXED_INPUT.resolve(), "only_fixed_dev8_input_supported")
    rows, digest = contract.read_input(path)
    require(digest == FIXED_INPUT_SHA and len(rows) == EXPECTED_COUNT, "fixed_dev8_input_changed")
    gate_identity = screen_gate().identity()
    require([row["id"] for row in rows] == gate_identity["inputIdsInOrder"], "fixed_dev8_order_changed")
    return rows, digest


def require_execution_binding(args):
    require(getattr(args, "execution_binding", None) is not None, "execution_binding_receipt_required")
    path = Path(args.execution_binding)
    require(path.resolve().is_relative_to((ROOT / ".training/verifications").resolve())
            and path.suffix == ".json" and path.is_file()
            and not any(p.is_symlink() for p in (path, *path.parents)), "execution_binding_path_invalid")
    binding = json.loads(path.read_bytes())
    require(binding.get("version") == EXECUTION_BINDING_VERSION and binding.get("rootReviewed") is True,
            "execution_binding_not_reviewed")
    freeze_path = ROOT / binding["freezePath"]
    require(freeze_path.resolve() == (DEST / "freeze-v5-dev8.json").resolve()
            and not any(p.is_symlink() for p in (freeze_path, *freeze_path.parents))
            and sha_file(freeze_path) == binding["freezeSha256"], "execution_freeze_identity_differs")
    freeze = json.loads(freeze_path.read_bytes())
    profile = load_profile()
    require(type(profile.get("tokenBudgetAudit")) is dict, "v2_token_budget_audit_not_prepared")
    evaluator_path = ROOT / "scripts/local-qe/evaluate_llm_review_v3.py"
    require(evaluator_path.is_file() and not evaluator_path.is_symlink(), "evaluation_adapter_not_prepared")
    expected = {"version": "qwen-v5-dev8-freeze-v1", "runtimeVersion": VERSION,
                "inputSha256": FIXED_INPUT_SHA, "codeHashes": code_hashes(),
                "resourceProfileSha256": sha_file(PROFILE_PATH),
                "tokenBudgetAuditSha256": profile["tokenBudgetAudit"]["sha256"],
                "materialWarningMapping": material_mapping().identity(), "screenGate": screen_gate().identity(),
                "evaluationAdapter": {"path": "scripts/local-qe/evaluate_llm_review_v3.py",
                                      "sha256": sha_file(evaluator_path)}}
    require(all(freeze.get(key) == value for key, value in expected.items()), "execution_freeze_contract_differs")
    # Evidence is data-only: no Python is imported from a receipt/output path.
    return {"version": EXECUTION_BINDING_VERSION, "path": str(path.resolve()),
            "sha256": sha_file(path), "freezePath": str(freeze_path.resolve()),
            "freezeSha256": binding["freezeSha256"], "rootReviewed": True,
            "files": {str(path.resolve()): sha_file(path), str(freeze_path.resolve()): sha_file(freeze_path),
                      str(evaluator_path.resolve()): sha_file(evaluator_path)}}


def load_profile():
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    fixed = {"version": RESOURCE_PROFILE, "contextTokens": CONTEXT,
             "outputReservedTokens": OUTPUT_TOKENS, "threads": 4, "batchThreads": 4,
             "batchSize": 128, "physicalBatchSize": 128, "parallelSlots": 1,
             "contextCheckpoints": 3, "cachePrompt": True, "cacheRamMiB": 0,
             "messageDelimiters": MESSAGE_DELIMITERS, "priorityClass": "BelowNormal",
             "nativePriority": -1, "poll": 0, "pollBatch": 0,
             "maximumChildWorkingSetBytes": BUDGET, "maximumChildPrivateBytes": BUDGET,
             "minimumStartAvailablePhysicalBytes": BUDGET + HEADROOM,
             "minimumStartAvailableCommitBytes": BUDGET + HEADROOM,
             "minimumRunningSystemHeadroomBytes": GIB,
             "contractVersion": contract.CONTRACT_VERSION, "expectedCount": EXPECTED_COUNT,
             "promptSha256": contract.contract_identity()["prompt_sha256"],
             "schemaSha256": contract.contract_identity()["schema_sha256"],
             "outputMappingVersion": "qwen-material-warning-policy-v3",
             "inputSha256": FIXED_INPUT_SHA, "executionBinding": "required_external_receipt"}
    require(all(profile.get(key) == value for key, value in fixed.items()), "resource_profile_differs")
    return profile


def audit_artifact(path, audit, relative):
    require(isinstance(relative, str) and not Path(relative).is_absolute(), "audit_artifact_path_invalid")
    target = path.parent / relative
    require(target.resolve().is_relative_to(path.parent.resolve())
            and not any(p.is_symlink() for p in (target, *target.parents)), "audit_artifact_redirected")
    require(sha_file(target) == audit["artifacts"][relative], "token_budget_artifact_changed")
    return target


def token_budget_preflight(rows):
    profile = load_profile()
    record = profile.get("tokenBudgetAudit")
    require(type(record) is dict, "v2_token_budget_audit_not_prepared")
    path = (ROOT / record["path"]).resolve()
    require(path.is_relative_to((DEST / "token-budget-v2").resolve())
            and not any(p.is_symlink() for p in (ROOT / record["path"], *(ROOT / record["path"]).parents))
            and sha_file(path) == record["sha256"], "token_budget_audit_identity_differs")
    audit = json.loads(path.read_text(encoding="utf-8"))
    require(audit["version"] == "qwen35-vocab-only-token-budget-v2"
            and audit["contractIdentity"] == contract.contract_identity(), "audited_contract_identity_changed")
    require(audit["status"] == "completed" and audit["inputCount"] == EXPECTED_COUNT
            and audit["generationCalls"] == 0 and audit["weightTensorsLoaded"] is False
            and audit["contextTokens"] == CONTEXT and audit["reservedOutputTokens"] == OUTPUT_TOKENS
            and audit["allRowsFitStrict"] is True and audit["fitCount"] == EXPECTED_COUNT
            and audit["finalIntegrityVerified"] is True and audit["allChildrenStopped"] is True
            and audit["nativeProcessCount"] == EXPECTED_COUNT + 2
            and audit["priorPromptParityReused"] is False and audit["networkRequests"] == 0
            and audit["nativeTemplateParity"] == "pending_actual_v5_apply_template"
            and "allFirst3TokenIdsEqual" not in audit and "templateParity" not in audit,
            "v2_token_budget_audit_not_passed")
    # Every recorded code/interpreter identity is still the actual one; no old
    # prompt parity or arbitrary output-directory Python is imported.
    for recorded, digest in audit["codeAndInterpreter"]["files"].items():
        require(sha_file(Path(recorded)) == digest, "audited_code_or_interpreter_changed")
    for current in (Path(__file__), Path(__file__).with_name("token_budget_v2.py"), Path(prepare.__file__),
                    Path(__file__).with_name("contract.py"), Path(contract.__file__),
                    ROOT / "scripts/local-hymt/process_owner.py", contract.PROMPT_PATH, contract.SCHEMA_PATH):
        require(sha_file(current) == audit["codeAndInterpreter"]["files"][str(current.resolve())],
                "audited_contract_code_or_bytes_changed")
    inputs, input_sha = read_fixed_input(Path(audit["inputPath"]))
    require(input_sha == audit["inputSha256"] == FIXED_INPUT_SHA and rows == inputs,
            "audited_input_changed")
    require([item["id"] for item in audit["rows"]] == [row["id"] for row in rows],
            "token_budget_row_inventory_differs")
    budgets = {item["id"]: item for item in audit["rows"]}
    for relative in audit["artifacts"]:
        audit_artifact(path, audit, relative)
    require(sha_file(audit_artifact(path, audit, "evidence/tokenize.cpp")) ==
            "db0cd035294b91009250029a1435a1a688d72eb489c406b630d2293b30d6fb34"
            and audit["officialSource"]["vocabOnlyAssignmentVerified"] is True
            and audit["officialSource"]["decodeOrSamplingCallPresent"] is False
            and audit["officialSource"]["networkRequestPerformed"] is False,
            "vocabulary_only_source_evidence_invalid")
    receipt_paths = [relative for relative in audit["artifacts"] if relative.endswith(".process.json")]
    require(len(receipt_paths) == EXPECTED_COUNT + 2, "vocabulary_child_inventory_invalid")
    for relative in receipt_paths:
        receipt = json.loads(audit_artifact(path, audit, relative).read_bytes())
        require(receipt["childStopped"] is True and receipt["exitCode"] == 0 and receipt["timedOut"] is False
                and "failure" not in receipt and "cleanupError" not in receipt
                and receipt["ownershipVerifiedBySpawn"] is True and receipt["atStart"]["priorityClass"] == 0x4000
                and receipt["atStart"]["pid"] == receipt["pid"] == receipt["atExit"]["pid"]
                and receipt["atStart"]["createdFileTime100ns"] > 0
                and receipt["atStart"]["createdFileTime100ns"] == receipt["atExit"]["createdFileTime100ns"]
                and receipt["atExit"]["exitedFileTime100ns"] > 0, "vocabulary_child_evidence_invalid")
    require(sha_file(audit_artifact(path, audit, "actual-chat-template.jinja")) == audit["templateSha256"],
            "audited_template_identity_differs")
    for row in rows:
        item = budgets[row["id"]]
        require(item["fitsStrict"] is True and type(item["inputTokens"]) is int
                and 0 < item["inputTokens"] + OUTPUT_TOKENS < CONTEXT, "audited_row_exceeds_context")
        token_file = audit_artifact(path, audit, item["rawTokenIdsFile"])
        prompt_file = audit_artifact(path, audit, item["rawPromptStdinFile"])
        ids = json.loads(token_file.read_bytes())
        require(type(ids) is list and all(type(t) is int and t >= 0 for t in ids)
                and len(ids) == item["inputTokens"]
                and sha_text(json.dumps(ids, separators=(",", ":"))) == item["tokenIdsSha256"]
                and sha_file(prompt_file) == item["promptSha256"], "audited_row_content_differs")
        receipt = json.loads(audit_artifact(path, audit, item["processEvidenceFile"]).read_bytes())
        require(receipt["childStopped"] is True and receipt["exitCode"] == 0
                and receipt["timedOut"] is False and "failure" not in receipt
                and receipt["pid"] == item["childPid"] and receipt["ownershipVerifiedBySpawn"] is True
                and receipt["atStart"]["priorityClass"] == 0x4000
                and receipt["atStart"]["createdFileTime100ns"] > 0
                and receipt["atStart"]["createdFileTime100ns"] == receipt["atExit"]["createdFileTime100ns"]
                and receipt["atExit"]["exitedFileTime100ns"] > 0,
                "vocabulary_child_evidence_invalid")
    return {"path": str(path), "sha256": record["sha256"], "inputSha256": input_sha,
            "templateSha256": audit["templateSha256"], "rows": budgets,
            "sharedPrefixAudit": audit["sharedPrefixAudit"], "contextTokens": CONTEXT,
            "nativeTemplateParity": "pending_actual_v5_apply_template",
            "reservedOutputTokens": OUTPUT_TOKENS, "mapping": material_mapping().identity()}


def validate_audited_prompt(audit, row, prompt, tokens):
    item = audit["rows"][row["id"]]
    require(sha_text(prompt) == item["promptSha256"] and len(tokens) == item["inputTokens"]
            and sha_text(json.dumps(tokens, separators=(",", ":"))) == item["tokenIdsSha256"],
            "actual_prompt_or_tokens_differ_from_preflight")
    for delimiter in MESSAGE_DELIMITERS:
        require(prompt.count(delimiter["delimiter"]) == 1, "chat_message_boundary_not_unique")


def child_telemetry(process, limiter):
    # The existing limiter validates live owned handle, PID and creation time.
    handle, identity = limiter._identity(process)
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetPriorityClass.argtypes = [wintypes.HANDLE]
    kernel.GetPriorityClass.restype = wintypes.DWORD
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    priority = kernel.GetPriorityClass(handle)
    require(priority == subprocess.BELOW_NORMAL_PRIORITY_CLASS, "owned_priority_not_below_normal")
    times = [wintypes.FILETIME() for _ in range(4)]
    require(kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)), "owned_cpu_times_failed")
    ticks = [value.dwHighDateTime << 32 | value.dwLowDateTime for value in times]
    require(ticks[0] == identity["creationTicks"], "owned_telemetry_identity_differs")
    return identity | {"priorityClass": priority, "priorityName": "BelowNormal",
                       "kernelCpuSeconds": ticks[2] / 10**7, "userCpuSeconds": ticks[3] / 10**7,
                       "totalCpuSeconds": (ticks[2] + ticks[3]) / 10**7}


def cache_observation(response, input_tokens):
    timings = response.get("timings", {})
    cached, processed = timings.get("cache_n"), timings.get("prompt_n")
    require(type(cached) is int and type(processed) is int and 0 <= cached < input_tokens
            and processed > 0 and cached + processed == input_tokens, "native_prompt_cache_accounting_differs")
    return {"reusedPromptTokens": cached, "processedPromptTokens": processed,
            "promptMilliseconds": timings.get("prompt_ms"),
            "predictedMilliseconds": timings.get("predicted_ms"),
            "nativeTimings": timings, "slotTokensAfterResponse": response.get("tokens_cached"),
            "prefixReuseObserved": cached > 0, "sameSeedGuaranteesSameOutput": False}


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
            "contextCheckpointCount": 3, "checkpointCoreEstimateBytes": 3 * 50.25 * 1024**2,
            "checkpointEstimateBasedOnV3ObservedRecurrentBuffer": True,
            "checkpointSerializationOverheadMeasured": False,
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
    for relative, expected in (HELPER_HASHES | CONTRACT_FILES | MAPPING_FILES).items():
        require(sha_file(ROOT / relative) == expected, "readonly_ownership_helper_changed")
    paths = [Path(__file__), Path(prepare.__file__), Path(contract.__file__),
             contract.PROMPT_PATH, contract.SCHEMA_PATH, PROFILE_PATH,
             *[ROOT / key for key in (HELPER_HASHES | CONTRACT_FILES | MAPPING_FILES)],
             *[ROOT / record["path"] for record in screen_gate().identity()["files"].values()],
             Path(sys.executable), actual_python_executable()]
    return {str(path.resolve()): sha_file(path) for path in paths}


def verify_unchanged(hashes, installation, input_path, input_sha):
    require(sha_file(input_path) == input_sha, "input_changed")
    require(all(sha_file(path) == expected for path, expected in hashes.items()), "code_or_python_changed")
    require(all(stat_id(path) == expected for path, expected in installation["statIdentities"].items()),
            "installation_stat_changed")


def server_command(port, key):
    return [str(prepare.RUNTIME_DIR / "llama-server.exe"), "--model", str(DEST / prepare.MODEL_NAME),
            "--alias", ALIAS, "--host", "127.0.0.1", "--port", str(port), "--api-key", key,
            "--ctx-size", str(CONTEXT), "--parallel", "1", "--threads", "4", "--threads-batch", "4",
            "--prio", "-1", "--poll", "0", "--poll-batch", "0",
            "--offline", "--no-agent", "--no-warmup", "--no-repack", "--load-mode", "mmap",
            "--cache-ram", "0", "--no-op-offload", "--log-verbosity", "4",
            "--cors-origins", f"http://127.0.0.1:{port}",
            "--batch-size", "128", "--ubatch-size", "128", "--gpu-layers", "0", "--device", "none",
            "--fit", "off", "--cache-type-k", "f16", "--cache-type-v", "f16", "--ctx-checkpoints", "3",
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
        self.phase = "startup"
        self.row_id = None
        self.request_started = None
        self.request_started_utc = None
        self.last_sample = self.started
        self.last_deadline_exceeded_at_end = False
        self.state_lock = threading.Lock()
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
                child.update(child_telemetry(self.process, self.limiter))
                with self.state_lock:
                    self.error = self.error or self.reason(state, child, now, self.started, self.deadline)
                    event = {"at": prepare.utc(), "system": state, "child": child,
                             "abortReason": self.error, "phase": self.phase, "rowId": self.row_id,
                             "requestStartUTC": self.request_started_utc,
                             "requestElapsedSeconds": None if self.request_started is None else now - self.request_started,
                             "heartbeatGapSeconds": now - self.last_sample,
                             "actualGeneratedTokenProgress": "unknown_nonstreaming",
                             "pageFaultCountIncludesSoftAndHard": True}
                    self.last_sample = now
                self.stream.write(json.dumps(event) + "\n")
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
    def begin_request(self, row_id):
        self.check()
        with self.state_lock:
            self.phase, self.row_id = "request", row_id
            self.request_started = time.monotonic()
            self.request_started_utc = prepare.utc()
            self.deadline = self.request_started + REQUEST_SECONDS
    def end_phase(self):
        # Latch on the caller even if Windows suspended the monitor. This never
        # raises: Client.call has already saved returned raw bytes before check.
        with self.state_lock:
            exceeded = self.deadline is not None and time.monotonic() >= self.deadline
            self.last_deadline_exceeded_at_end = self.last_deadline_exceeded_at_end or exceeded
            if exceeded and self.error is None:
                self.error = "request_or_startup_deadline"
            self.deadline = None
            self.phase = "between_rows"
            self.row_id = None
            self.request_started = self.request_started_utc = None
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
    return NATIVE_SAMPLING | {"prompt": tokens, "json_schema": contract.load_schema(),
                              "message_delimiters": MESSAGE_DELIMITERS}


def validate_native(response, prompt):
    require(response.get("stop_type") == "eos" and response.get("truncated") is False,
            "response_not_complete_eos")
    require(response.get("stopping_word", "") == "", "unexpected_stop_word")
    require(response.get("prompt") == prompt, "actual_prompt_differs")
    tokens, content = response.get("tokens"), response.get("content")
    require(isinstance(content, str) and content.strip() and isinstance(tokens, list)
            and 0 < len(tokens) < OUTPUT_TOKENS and all(type(t) is int and t >= 0 for t in tokens),
            "response_empty_or_output_limit")
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


def verify_run_unchanged(args, plan):
    verify_unchanged(plan["codeHashes"], plan["installation"], args.input, plan["inputSha256"])
    require(all(sha_file(path) == expected for path, expected in plan["executionBinding"]["files"].items()),
            "execution_binding_evidence_changed")


def evaluate_rows(args, plan, rows, output, process, guard, client, audited, finished):
    """Strictly sequential; a persisted valid mismatch ends this loop immediately."""
    mapper, gate = material_mapping(), screen_gate()
    plan["screenDecisions"] = []
    plan["nativeTemplateParity"] = {"status": "pending", "rowsVerified": 0,
                                   "firstRequestVerifiedBeforeGeneration": False}
    for index, row in enumerate(rows):
        plan["activeRowId"] = row["id"]
        prefix = f"{index + 1:04d}"
        guard.begin_request(row["id"])
        verify_run_unchanged(args, plan)
        request = contract.build_request(row) | {"model": ALIAS}
        applied = client.call("/apply-template", request, raw_path=output / (prefix + "-template.raw.json"))
        prompt = applied.get("prompt")
        require(isinstance(prompt, str) and prompt.endswith("<think>\n\n</think>\n\n"),
                "nonthinking_template_suffix_unvalidated")
        tokens = client.call("/tokenize", {"content": prompt, "add_special": False, "parse_special": True},
                             raw_path=output / (prefix + "-tokenize.raw.json"))["tokens"]
        validate_audited_prompt(audited, row, prompt, tokens)
        plan["nativeTemplateParity"] = {"status": "verified", "rowsVerified": index + 1,
                                       "firstRequestVerifiedBeforeGeneration": True}
        native = completion_payload(request, tokens)
        prepare.json_once(output / (prefix + "-native-request.json"), native)
        guard.check()
        before = time.monotonic()
        plan["generationRequests"] += 1
        raw_path = output / (prefix + "-completion.raw.json")
        try:
            response = client.call("/completion", native, timeout=REQUEST_SECONDS, raw_path=raw_path)
        finally:
            # Client.call writes all received bytes (including a partial body)
            # before returning/raising; latch the exact deadline without raising.
            guard.end_phase()
            if raw_path.is_file():
                prepare.json_once(output / (prefix + "-completion-receipt.json"),
                    {"id": row["id"], "rawResponseFile": raw_path.name, "rawResponseSha256": sha_file(raw_path),
                     "returnedAt": prepare.utc(), "lastDeadlineExceededAtEnd": guard.last_deadline_exceeded_at_end})
        guard.check()
        content = validate_native(response, prompt)
        caching = cache_observation(response, len(tokens))
        assessment = contract.validate_response(row, content)
        mapping = mapper.classify(assessment)
        record = {"id": row["id"], "status": "completed" if assessment["status"] == "valid" else "invalid_structure",
                  "seconds": time.monotonic() - before,
                  "rawResponseFile": raw_path.name, "rawResponseSha256": sha_file(raw_path),
                  "promptSha256": sha_text(prompt), "inputTokens": len(tokens), "outputTokens": len(response["tokens"]),
                  "assessment": assessment, "materialWarningV3": mapping,
                  "cache": caching, "childTelemetry": child_telemetry(process, guard.limiter),
                  "humanReviewed": False, "semanticQualityCertified": False}
        assessment_path = output / (prefix + "-assessment.json")
        prepare.json_once(assessment_path, record)
        require(assessment["status"] == "valid", "structured_response_validation_failed")
        guard.check()
        decision = gate.check(row["id"], mapping)
        decision = decision | {"index": index + 1, "assessmentFile": assessment_path.name,
                               "assessmentSha256": sha_file(assessment_path), "rawResponseFile": raw_path.name,
                               "rawResponseSha256": sha_file(raw_path)}
        prepare.json_once(output / (prefix + "-gate.json"), decision)
        plan["screenDecisions"].append(decision)
        finished.add(row["id"])
        plan["activeRowId"] = None
        print(json.dumps({"event": "review-row", "completed": len(finished), "total": len(rows),
                          "continueRun": decision["continueRun"]}), flush=True)
        if decision["continueRun"] is False:
            plan.update(status="stopped_futility", futilityStop=decision, stopReason=decision["reason"])
            return
        require(decision["continueRun"] is True, "invalid_screen_decision")
    plan["status"] = "completed"


def execute(args, plan, rows, output):
    process = guard = log = None
    finished = set()
    plan["activeRowId"] = None
    plan.update(status="failed", childProcessStopped=True, generationRequests=0, modelLoaded=False,
                runtimeContractValidated=False)
    try:
        require_execution_binding(args)
        require(read_fixed_input(args.input)[0] == rows, "execution_rows_changed")
        require(os.name == "nt" and sys.version_info[:2] == (3, 11) and sys.dont_write_bytecode,
                "native_run_requires_windows_cpython311_B")
        require(bool(args.slot_approval), "coordinator_slot_approval_receipt_required")
        audited = token_budget_preflight(rows)
        plan["tokenBudgetPreflight"] = audited
        check = memory_plan()
        plan["memoryPreflight"] = check
        require(check["physicalPassed"] and check["commitPassed"], "insufficient_single_model_headroom")
        verify_run_unchanged(args, plan)
        claim_path = DEST / ".dev8-v5-generation.claim.json"
        prepare.json_once(claim_path, {"version": VERSION, "createdAt": prepare.utc(),
            "output": str(output), "inputSha256": FIXED_INPUT_SHA,
            "executionBindingSha256": plan["executionBinding"]["sha256"],
            "singleDiagnosticAttemptOnly": True, "automaticRetries": False})
        plan["diagnosticClaim"] = {"path": str(claim_path), "sha256": sha_file(claim_path)}
        template, metadata = gguf_metadata(DEST / prepare.MODEL_NAME)
        require(metadata["templateSha256"] == audited["templateSha256"], "audited_template_changed")
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
        try:
            process = launcher.spawn(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                     cwd=prepare.RUNTIME_DIR, env=environment,
                                     creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        except BaseException as error:
            if getattr(error, "code", None) in ("owned_child_creation_cleanup_failed", "unowned_child_cleanup_failed"):
                plan.update(spawnCleanupUnconfirmed=True, childProcessStopped=False,
                            spawn=launcher.creation_receipt, spawnCleanupCode=error.code)
            raise
        plan.update(childProcessStopped=False, ownedPid=process.pid, spawn=launcher.creation_receipt,
                    workingSet=limiter.apply_child(process))
        plan["initialChildTelemetry"] = child_telemetry(process, limiter)
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
        guard.end_phase()
        guard.check()
        props = client.call("/props", raw_path=output / "runtime-props.raw.json")
        plan["modelLoaded"] = True  # /health ready and /props response: loading observed, not yet contract accepted
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
        plan["runtimeContractValidated"] = True
        evaluate_rows(args, plan, rows, output, process, guard, client, audited, finished)
    except BaseException as error:
        plan["failure"] = {"type": type(error).__name__, "code": str(error) if isinstance(error, ValueError)
                           else type(error).__name__, "rowId": plan["activeRowId"]}
    finally:
        if guard:
            try:
                guard.close()
                plan["guard"] = {"abortReason": guard.error, "killError": guard.kill_error,
                                 "lastDeadlineExceededAtEnd": guard.last_deadline_exceeded_at_end}
                if guard.error or guard.kill_error:
                    plan["status"] = "failed"
            except BaseException as error:
                plan.update(status="failed", guardCleanupError=type(error).__name__)
        try:
            # Receipt/code integrity is checked at the termination boundary too;
            # any failure is recorded without skipping owned-child cleanup.
            verify_run_unchanged(args, plan)
        except BaseException as error:
            plan.update(status="failed", preStopIntegrityError=type(error).__name__)
        try:
            require(not plan.get("spawnCleanupUnconfirmed"), "spawned_child_stop_unconfirmed")
            plan["childProcessStopped"] = stop_owned(process)
            require(plan["childProcessStopped"], "owned_child_still_alive")
        except BaseException as error:
            plan.update(status="failed", childProcessStopped=False, cleanupError=type(error).__name__)
        if log:
            log.close()
        try:
            verify_run_unchanged(args, plan)
            require(verify_installation() == plan["installation"], "installation_final_verification_differs")
            plan["finalIntegrityVerified"] = True
        except BaseException as error:
            plan.update(status="failed", finalIntegrityVerified=False, integrityError=type(error).__name__)
        plan["itemStatuses"] = [{"id": row["id"], "status": "completed" if row["id"] in finished
                                 else "failed" if row["id"] == plan["activeRowId"] else "not_run"} for row in rows]
        plan["completedCount"] = len(finished)
        plan["unexecutedIds"] = [item["id"] for item in plan["itemStatuses"] if item["status"] == "not_run"]
    return plan


def run(args):
    rows, input_sha = read_fixed_input(args.input)
    profile = load_profile()
    if not args.run:
        print(json.dumps({"version": VERSION, "status": "prepared_not_executed", "expectedCount": len(rows),
            "inputSha256": input_sha, "modelLoaded": False, "generationRequests": 0,
            "tokenBudgetAudit": profile.get("tokenBudgetAudit"),
            "executionBinding": "required_external_receipt", "launchAuthorized": False,
            "minimumStartAvailablePhysicalBytes": BUDGET + HEADROOM,
            "minimumStartAvailableCommitBytes": BUDGET + HEADROOM,
            "semanticQualityCertified": False, "appRegistrationPerformed": False}), flush=True)
        return 0
    # Validate missing activation evidence before any model/hash/RAM access or
    # output creation. Parent finalizes profile, then freeze, then receipt.
    binding = require_execution_binding(args)
    audited = token_budget_preflight(rows)
    require(args.output is not None, "new_output_required")
    output = args.output.resolve()
    require(output.is_relative_to((DEST / "runs").resolve()) and output != (DEST / "runs").resolve()
            and not any(p.is_symlink() for p in (args.output, *args.output.parents)), "output_outside_owned_runs")
    installation = verify_installation()
    hashes = code_hashes()
    output.mkdir(parents=True, exist_ok=False)
    plan = {"version": VERSION, "createdAt": prepare.utc(), "status": "prepared", "inputSha256": input_sha,
            "inputFields": ["source", "translation", "context"], "referencesOrJudgmentsIncluded": False,
            "codeHashes": hashes, "installation": installation, "expectedCount": len(rows),
            "executionBinding": binding, "screenGate": screen_gate().identity(),
            "sampling": NATIVE_SAMPLING, "thinking": {"enable_thinking": False, "suffixMustBeVerifiedAtRuntime": True},
            "resourceProfile": RESOURCE_PROFILE, "memoryPlan": memory_plan(),
            "tokenBudgetPreflight": audited, "materialWarningMapping": audited["mapping"],
            "nativeStartupConfiguration": {"offline": True, "agentEnabled": False, "warmup": False,
                "repack": False, "loadMode": "mmap", "cacheRamMiB": 0, "logVerbosity": 4,
                "threads": 4, "batchThreads": 4, "processPriority": "BelowNormal", "nativePriority": -1,
                "poll": 0, "pollBatch": 0, "contextCheckpoints": 3, "messageDelimiters": MESSAGE_DELIMITERS},
            "slotApprovalReceipt": args.slot_approval, "contextTokens": CONTEXT,
            "modelLoaded": False, "generationRequests": 0, "humanReviewed": False, "trainingPerformed": False,
            "semanticQualityCertified": False, "automaticRetries": False, "appRegistrationPerformed": False,
            "developmentDiagnosisOnly": True, "independentHoldout": False, "allEightCorrectIsAcceptance": False,
            "fullBaselineAccepted": False, "semanticSpanPrecision": "not_evaluated",
            "completedCount": 0, "screenDecisions": [], "singleDiagnosticAttemptOnly": True,
            "newPromptAndCapEffectsCannotBeSeparated": True,
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
    return 0 if plan["status"] in ("prepared", "completed", "stopped_futility") else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=FIXED_INPUT, help="Only the exact pinned dev8 file is supported")
    parser.add_argument("--output", type=Path, help="New owned run folder, required with --run")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--slot-approval", default=None, help="coordinator's actual approval receipt, required with --run")
    parser.add_argument("--execution-binding", type=Path, help="Parent-reviewed external freeze/evaluator receipt")
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
