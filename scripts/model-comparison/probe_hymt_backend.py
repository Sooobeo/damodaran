"""Isolated 7B Q8 backend timing smoke; does not evaluate translation quality.

The two new functional sentences are unrelated to development/test datasets.
One explicit warmup request is separate from two measured requests. Q26's
model/template/context/sampling/catalog remain fixed; only backend/threads vary.
No inference occurs without --run. Never deploys or changes application settings.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error

import run_hymt as fixed
import setup_hymt30 as runtime_setup

RUNNER_SHA = "a80c431e51c17d510273457d0991e2febba57bac33a8f89a067e1f571387a410"
OWNER = fixed.ROOT / "scripts/local-hymt/process_owner.py"
OWNER_SHA = "9a00d809466e304359fb47c374c8fc2464ce31cdbc9557fbc9eee089bfe9d8b2"
SOURCES = ("The small bridge crosses a quiet river.",
           "If the package arrives before noon, place it on the wooden table.")
STARTUP_SECONDS = 240
REQUEST_SECONDS = 240


class BoundedClient(fixed.LocalClient):
    def request(self, endpoint, payload=None, timeout=10):
        return super().request(endpoint, payload, timeout=min(timeout, REQUEST_SECONDS))


def memory_state(process=None):
    result = {"availablePhysicalBytes": runtime_setup.memory_preflight()["availablePhysicalBytes"]}
    if process is not None and process.poll() is None:
        class ProcessMemory(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("pageFaultCount", ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in ("peakWorkingSet", "workingSet", "peakPagedPool",
                    "pagedPool", "peakNonPagedPool", "nonPagedPool", "pagefile", "peakPagefile", "private")]
        value = ProcessMemory()
        value.cb = ctypes.sizeof(value)
        library = ctypes.WinDLL("psapi", use_last_error=True)
        library.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessMemory), ctypes.c_ulong]
        library.GetProcessMemoryInfo.restype = ctypes.c_int
        if library.GetProcessMemoryInfo(int(process._handle), ctypes.byref(value), value.cb):
            result.update(childWorkingSetBytes=value.workingSet, childPrivateBytes=value.private,
                          childPeakWorkingSetBytes=value.peakWorkingSet, childPageFaultCount=value.pageFaultCount)
    return result


def preflight(available=None):
    if available is None:
        available = memory_state()["availablePhysicalBytes"]
    # GGUF: 32 layers * 8192 positions * 8 KV heads * 128 dims * 2(K,V) * 2 bytes = 1 GiB.
    # Another 1 GiB is the minimum scratch/graph/allocation headroom for this short smoke.
    headroom = 2 * 1024 ** 3
    return {"availablePhysicalBytes": available, "modelBytes": fixed.MODEL["size"],
            "f16KvBudgetBytes": 1024 ** 3, "scratchHeadroomBytes": 1024 ** 3,
            "minimumAvailablePhysicalBytes": fixed.MODEL["size"] + headroom,
            "passed": available >= fixed.MODEL["size"] + headroom,
            "sharedGpuMemoryAddsRam": False, "guaranteesNoOutOfMemory": False}


def server_command(backend, threads, port, key):
    fixed.require(backend in ("cpu", "sycl") and threads in (4, 8), "invalid_backend_profile")
    fixed.require(backend == "cpu" or threads == 4, "sycl_profile_requires_four_threads")
    directory = runtime_setup.DEST / f"runtime-{backend}"
    argv = [str(directory / "llama-server.exe"), "--model", str(fixed.DEST / fixed.MODEL["name"]),
            "--host", "127.0.0.1", "--port", str(port), "--cors-origins", f"http://127.0.0.1:{port}",
            "--api-key", key, "--no-agent", "--offline", "--no-webui", "--no-warmup", "--jinja",
            "--ctx-size", str(fixed.CONTEXT_SIZE), "--parallel", "1", "--no-context-shift",
            "--gpu-layers", "0" if backend == "cpu" else "99",
            "--device", "none" if backend == "cpu" else "SYCL0", "--fit", "off",
            "--threads", str(threads), "--threads-batch", str(threads),
            "--batch-size", "256", "--ubatch-size", "128", "--cache-ram", "0",
            "--cache-type-k", "f16", "--cache-type-v", "f16", "--log-verbosity", "4",
            "--override-kv", ",".join(f"{k}={v}" for k, v in fixed.OVERRIDES.items()),
            "--temp", "0.7", "--top-p", "0.6", "--top-k", "20", "--min-p", "0",
            "--repeat-penalty", "1.05", "--repeat-last-n", "8192", "--seed", "42",
            "--samplers", ";".join(fixed.SAMPLING["samplers"]), "--n-predict", str(fixed.MAX_NEW_TOKENS)]
    if backend == "cpu":
        argv.append("--no-op-offload")
    return argv


def run(args):
    output = args.output.resolve()
    fixed.require(output.is_relative_to(fixed.COMPARISONS.resolve()) and output != fixed.COMPARISONS.resolve(),
                  "output_path_not_allowed")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    summary = {"version": "hymt-backend-smoke-v1", "status": "failed", "backend": args.backend,
               "threads": args.threads, "runtimeTag": runtime_setup.RUNTIME_TAG,
               "runtimeRevision": runtime_setup.RUNTIME_REVISION, "warmupRequests": 0,
               "measuredRequests": 0, "modelLoaded": False, "humanReviewed": False,
               "qualityEvaluation": False, "stabilityCertified": False, "applicationChanged": False,
               "contextSize": fixed.CONTEXT_SIZE, "sampling": fixed.SAMPLING, "profile": "contextual",
               "createdAt": datetime.now(timezone.utc).isoformat(), "failure": None}
    process = None
    log = None
    monitor = None
    stop_monitor = threading.Event()
    memory_samples = []
    records = []
    current = None
    response = None
    try:
        fixed.require(fixed.digest(fixed.__file__) == RUNNER_SHA and fixed.digest(OWNER) == OWNER_SHA,
                      "frozen_dependency_changed")
        summary["codeHashes"] = fixed.code_hashes() | {
            str(Path(__file__).resolve()): fixed.digest(__file__),
            str(Path(runtime_setup.__file__).resolve()): fixed.digest(runtime_setup.__file__),
            str(OWNER): OWNER_SHA}
        summary["preflightBeforeIntegrity"] = preflight()
        if not args.run:
            summary["status"] = "prepared"
            return 0
        fixed.require(summary["preflightBeforeIntegrity"]["passed"], "insufficient_physical_memory")
        summary["originalInstallation"] = fixed.verify_installation()
        inventory = runtime_setup.install_runtime(args.backend, offline=True)
        summary["runtimeFiles"] = inventory
        summary["runtimeArchive"] = runtime_setup.RUNTIMES[args.backend]
        template, contract = fixed.gguf_contract(fixed.DEST / fixed.MODEL["name"])
        summary["ggufContract"] = contract
        terms, catalog = fixed.read_catalog()
        summary["catalog"] = catalog
        rows = [{"id": f"RUNTIME-SMOKE-{i+1}", "source": source, "context": "",
                 "sourceSha256": fixed.sha_text(source), "contextSha256": fixed.sha_text("")}
                for i, source in enumerate(SOURCES)]
        summary["sources"] = rows
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        key = secrets.token_urlsafe(32)
        command = server_command(args.backend, args.threads, port, key)
        redacted = command.copy()
        redacted[redacted.index("--api-key") + 1] = "[EPHEMERAL_REDACTED]"
        summary["runtimeCommand"] = redacted
        summary["preflightImmediatelyBeforeSpawn"] = preflight()
        fixed.require(summary["preflightImmediatelyBeforeSpawn"]["passed"], "insufficient_physical_memory")
        sys.path.insert(0, str(OWNER.parent))
        from process_owner import claim_process_owner
        owner = claim_process_owner()
        log_path = output / "runtime.log"
        log = log_path.open("xb")
        load_started = time.monotonic()
        process = owner.spawn(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                              cwd=runtime_setup.DEST / f"runtime-{args.backend}", env=fixed.runtime_environment())
        summary["childPid"] = process.pid

        def observe_memory():
            low_since = None
            while not stop_monitor.wait(0.5) and process.poll() is None:
                try:
                    state = memory_state(process)
                    state["seconds"] = round(time.monotonic() - load_started, 3)
                    memory_samples.append(state)
                    if state["availablePhysicalBytes"] < 512 * 1024 ** 2:
                        low_since = low_since or time.monotonic()
                        if time.monotonic() - low_since >= 3:
                            summary["memoryGuardAborted"] = True
                            process.kill()
                            return
                    else:
                        low_since = None
                except Exception as error:
                    summary["memoryMonitorError"] = type(error).__name__
                    process.kill()
                    return

        monitor = threading.Thread(target=observe_memory, daemon=True)
        monitor.start()
        client = BoundedClient(f"http://127.0.0.1:{port}", key)
        fixed.wait_ready(client, process, load_started)
        summary["loadSeconds"] = round(time.monotonic() - load_started, 6)
        summary["modelLoaded"] = True
        log.flush()
        summary["actualEog"] = fixed.eog_from_log(log_path.read_text("utf-8", errors="replace"))
        summary["actualTokenizerTokens"] = fixed.validate_runtime_tokens(client)
        props = client.request("/props", timeout=10)
        fixed.require(Path(props.get("model_path", "")).resolve() == (fixed.DEST / fixed.MODEL["name"]).resolve(),
                      "runtime_model_path")
        fixed.require(props.get("chat_template") == template and props.get("total_slots") == 1 and
                      props.get("default_generation_settings", {}).get("n_ctx") == fixed.CONTEXT_SIZE,
                      "runtime_template_context_slots_mismatch")
        summary["runtimeBuildInfo"] = props.get("build_info")
        with (output / "predictions.jsonl").open("x", encoding="utf-8", newline="\n") as destination:
            # The identical first source warms kernels/cache; it is not a measured result.
            for phase, row in [("warmup", rows[0]), *[("measured", row) for row in rows]]:
                current = {"phase": phase, "id": row["id"]}
                response = None
                fixed.require(process.poll() is None, "runtime_exited")
                content, matches = fixed.build_user_prompt(row, "contextual", terms)
                prompt = fixed.render_prompt(template, content)
                tokens = client.request("/tokenize", {"content": prompt, "add_special": False,
                                                       "parse_special": True}, timeout=10)["tokens"]
                fixed.validate_prompt_tokens(tokens)
                before_memory = memory_state(process)
                before = time.monotonic()
                summary["warmupRequests" if phase == "warmup" else "measuredRequests"] += 1
                response = client.request("/completion", fixed.SAMPLING | {"prompt": tokens}, timeout=REQUEST_SECONDS)
                record = fixed.prediction(row, prompt, matches, tokens, response, time.monotonic() - before)
                record.update(phase=phase, memoryBefore=before_memory, memoryAfter=memory_state(process),
                              source=row["source"], qualityEvaluation=False)
                destination.write(json.dumps(record, ensure_ascii=False) + "\n")
                destination.flush()
                os.fsync(destination.fileno())
                records.append(record)
                print(json.dumps({"event": "backend-smoke", "backend": args.backend, "phase": phase,
                                  "id": row["id"], "seconds": record["generationSeconds"]}), flush=True)
                current = None
        fixed.require(all(row["automaticChecksPassed"] for row in records), "automatic_function_checks_failed")
        summary["status"] = "completed"
    except BaseException as error:
        summary["failure"] = {"type": type(error).__name__,
                              "code": str(error) if isinstance(error, fixed.RunError) else type(error).__name__,
                              "current": current}
        if response is not None and current is not None:
            summary["failedResponseEvidence"] = {k: response[k] for k in (
                "content", "tokens", "tokens_predicted", "stop_type", "truncated", "timings", "generation_settings")
                if k in response}
    finally:
        try:
            summary["childProcessStopped"] = fixed.stop_process(process)
        except BaseException as error:
            summary.update(status="failed", cleanupError=type(error).__name__, childProcessStopped=False)
        stop_monitor.set()
        if monitor:
            monitor.join(timeout=3)
        if log:
            log.close()
        try:
            if summary.get("originalInstallation") is not None:
                fixed.require(fixed.verify_installation() == summary["originalInstallation"], "model_changed_during_probe")
                fixed.require(runtime_setup.install_runtime(args.backend, offline=True) == summary["runtimeFiles"],
                              "runtime_changed_during_probe")
                fixed.require(fixed.digest(fixed.CATALOG) == summary["catalog"]["sha256"], "catalog_changed_during_probe")
            if "codeHashes" in summary:
                fixed.check_identity_unchanged(summary["codeHashes"])
        except BaseException as error:
            summary.update(status="failed", finalIntegrityError=type(error).__name__)
        summary.update(records=records, elapsedSeconds=round(time.monotonic() - started, 6),
                       finishedAt=datetime.now(timezone.utc).isoformat())
        fixed.write_once(output / "memory-samples.json", memory_samples)
        summary["artifactHashes"] = {p.name: fixed.digest(p) for p in output.iterdir() if p.is_file()}
        fixed.write_once(output / "summary.json", summary)
        print(json.dumps({"status": summary["status"], "output": str(output), "failure": summary["failure"]}), flush=True)
    return 0 if summary["status"] in ("completed", "prepared") else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("cpu", "sycl"), required=True)
    parser.add_argument("--threads", choices=(4, 8), type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run", action="store_true", help="Actually start the pinned 7B model and three bounded requests")
    args = parser.parse_args()
    fixed.require(args.backend == "cpu" or args.threads == 4, "sycl_profile_requires_four_threads")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
