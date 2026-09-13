"""Prepare, or explicitly run, one Hy-MT candidate/profile on frozen general16.

No model is opened without --run. Each invocation owns a fresh native process;
run raw and contextual sequentially only after a candidate has been selected.
Existing producers, prompts, registration and application data are untouched.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.error

import general_context_input as data
import run_hymt as common
import run_hymt30_v4 as large

VERSION = "general-context-hymt-screen-v1"


def backend(candidate):
    common.require(candidate in ("hy7", "hy30"), "unknown_candidate")
    return common if candidate == "hy7" else large


def make_prompts(rows, profile, terms):
    common.require(profile in ("raw", "contextual"), "unknown_profile")
    prepared = []
    for source_row in rows:
        # Defensive second allowlist, even when a caller bypasses the input reader.
        row = {key: source_row[key] for key in data.FIELDS}
        content, matches = common.build_user_prompt(row, profile, terms)
        prepared.append((row, content, matches, {
            "id": row["id"], "sourceSha256": row["sourceSha256"],
            "sourceContextSha256": row["contextSha256"],
            "effectiveContextSha256": common.sha_text(row["context"] if profile == "contextual" else ""),
            "contextIncluded": profile == "contextual",
            "userContentSha256": common.sha_text(content),
            "conditionalTermMatches": matches,
            "referenceKoIncluded": False, "checksIncluded": False,
        }))
    return prepared


def resource_preflight(candidate, budget):
    if candidate == "hy30":
        return large.preflight(ram_budget_gib=budget)
    common.require(type(budget) is int and budget in (8, 9, 10, 11, 12), "invalid_ram_budget")
    state = large.screen.memory_status()
    maximum, headroom = budget * large.GIB, 3 * large.GIB
    return {"ramBudgetGiB": budget, "modelBytes": common.MODEL["size"],
            "availablePhysicalBytes": state["availablePhysical"],
            "availableCommitBytes": state["availablePageFile"],
            "physicalPassed": state["availablePhysical"] >= maximum + headroom,
            "commitPassed": state["availablePageFile"] >= headroom,
            "requestedHardMaximumWorkingSetBytes": maximum - 64 * 1024 ** 2,
            "policy": "owned native child working set plus 3 GiB physical/commit headroom",
            "guaranteesNoOutOfMemory": False, "kvOrScratchMeasured": False}


def code_hashes():
    common.check_identity_unchanged({str(p): digest for p, digest in large.FROZEN.items()})
    paths = (Path(__file__).resolve(), Path(data.__file__).resolve())
    return large.code_hashes() | {str(p): common.digest(p) for p in paths}


def assert_identity(summary):
    data.assert_unchanged(summary["input"])
    common.check_identity_unchanged(summary["codeHashes"])
    common.require(common.digest(common.CATALOG) == summary["catalog"]["sha256"], "catalog_changed")


def validate_runtime(candidate, api, client, template, log_text, setup):
    if candidate == "hy30":
        return large.validate_runtime(client, template, log_text)
    evidence = {"eog": common.eog_from_log(log_text), "tokens": common.validate_runtime_tokens(client)}
    props = client.request("/props", timeout=15)
    common.require(Path(props.get("model_path", "")).resolve() == (setup.DEST / setup.MODEL["name"]).resolve()
                   and props.get("chat_template") == template
                   and props.get("default_generation_settings", {}).get("n_ctx") == api.CONTEXT_SIZE
                   and props.get("total_slots") == 1, "runtime_contract_mismatch")
    evidence["buildInfo"] = props.get("build_info")
    return evidence


def append_record(destination, records, record):
    destination.write(json.dumps(record, ensure_ascii=False) + "\n")
    destination.flush()
    os.fsync(destination.fileno())
    records.append(record)


def execute(args, summary, prepared, output, started):
    """One fresh candidate process; common source prompt logic is reused unchanged."""
    api = backend(args.candidate)
    setup = common.setup_hymt if args.candidate == "hy7" else large.setup
    process = log = monitor = None
    records, current, response_path = [], None, None
    installation = None
    try:
        common.require(os.name == "nt" and sys.version_info[:2] == (3, 11), "windows_cpython311_required")
        summary["preflightBeforeIntegrity"] = resource_preflight(args.candidate, args.ram_budget_gib)
        common.require(summary["preflightBeforeIntegrity"]["physicalPassed"], "insufficient_physical_memory")
        common.require(summary["preflightBeforeIntegrity"]["commitPassed"], "insufficient_commit_memory")
        installation_path = setup.DEST / "installation-manifest.json"
        summary["installationManifestSha256"] = common.digest(installation_path)
        installation = setup.verify_installation()
        summary["installation"] = installation
        template, summary["ggufContract"] = api.gguf_contract(setup.DEST / setup.MODEL["name"])
        assert_identity(summary)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        key = secrets.token_urlsafe(32)
        command = api.server_command(port, key)
        redacted = command.copy()
        redacted[redacted.index("--api-key") + 1] = "[EPHEMERAL_REDACTED]"
        summary["runtimeCommand"] = redacted
        summary["preflightBeforeSpawn"] = resource_preflight(args.candidate, args.ram_budget_gib)
        common.require(summary["preflightBeforeSpawn"]["physicalPassed"], "insufficient_physical_memory")
        common.require(summary["preflightBeforeSpawn"]["commitPassed"], "insufficient_commit_memory")
        sys.path.insert(0, str(large.OWNER.parent))
        from process_owner import claim_process_owner
        original_owner = claim_process_owner()
        limiter = large.WorkingSetLimit(original_owner,
            maximum_bytes=summary["preflightBeforeSpawn"]["requestedHardMaximumWorkingSetBytes"])
        owner = large.SuspendedProcessOwner(original_owner, limiter)
        log_path = output / "runtime.log"
        log = log_path.open("xb")
        load_started = time.monotonic()
        process = owner.spawn(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            cwd=Path(command[0]).parent, env=common.runtime_environment())
        summary.update(childPid=process.pid, suspendedCreation=owner.creation_receipt,
                       jobWorkingSetLimit=limiter.job_record, childWorkingSetLimit=limiter.apply_child(process))
        common.write_once(output / "spawn.json", summary)
        monitor = large.MemoryMonitor(process, limiter, output / "memory-samples.jsonl", started, load_started,
                                      maximum_working_set=args.ram_budget_gib * large.GIB)
        monitor.start()
        client = common.LocalClient(f"http://127.0.0.1:{port}", key)
        startup_seconds = 240 if args.candidate == "hy7" else large.STARTUP_SECONDS
        while time.monotonic() - load_started < startup_seconds:
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
        log.flush()
        summary["actualRuntime"] = validate_runtime(args.candidate, api, client, template,
            log_path.read_text("utf-8", errors="replace"), setup)
        summary.update(modelLoaded=True, loadSeconds=round(time.monotonic() - load_started, 6))
        print(json.dumps({"event": "general-context-ready", "candidate": args.candidate,
                          "profile": args.profile, "count": len(prepared)}), flush=True)
        with (output / "predictions.jsonl").open("x", encoding="utf-8", newline="\n") as destination:
            for row, content, matches, receipt in prepared:
                current, response_path = row, None
                assert_identity(summary)
                original_owner.assert_owned()
                monitor.check()
                prompt = api.render_prompt(template, content)
                tokens = client.request("/tokenize", {"content": prompt, "add_special": False,
                    "parse_special": True}, timeout=15)["tokens"]
                api.validate_prompt_tokens(tokens)
                before_memory = monitor.sample_and_check()
                before = time.monotonic()
                monitor.begin_request()
                summary["completionRequestsSent"] += 1
                try:
                    response = client.request("/completion", api.SAMPLING | {"prompt": tokens},
                                              timeout=large.REQUEST_SECONDS)
                finally:
                    monitor.end_request()
                elapsed = time.monotonic() - before
                response_path = output / (row["id"] + ".raw-response.json")
                common.write_once(response_path, response)
                monitor.check()
                record = api.prediction(row, prompt, matches, tokens, response, elapsed)
                if "outputIntegrityPassed" not in record:
                    record["outputIntegrityPassed"] = all(record["checks"][key] for key in
                        ("nonEmpty", "notTruncated", "belowOutputLimit", "noLeakedControlTokens"))
                record.update(profile=args.profile, candidate=args.candidate, promptInput=receipt,
                    rawResponseFile=response_path.name, rawResponseSha256=common.digest(response_path),
                    memoryBefore=before_memory, memoryAfter=monitor.sample_and_check())
                append_record(destination, records, record)
                current = None
                print(json.dumps({"event": "general-context-row", "id": row["id"],
                    "completed": len(records), "seconds": record["generationSeconds"]}), flush=True)
        common.require(len(records) == 16, "incomplete_generation")
        monitor.sample_and_check()
        summary["status"] = "completed"
    except BaseException as error:
        summary["failure"] = {"type": type(error).__name__, "rowId": current["id"] if current else None,
                              "code": str(error) if isinstance(error, common.RunError) else type(error).__name__}
        if current:
            common.write_once(output / "failed-item.json", {"id": current["id"], "status": "failed",
                "failure": summary["failure"], "rawResponseFile": response_path.name if response_path else None,
                "rawResponseSha256": common.digest(response_path) if response_path else None})
    finally:
        if monitor:
            try:
                monitor.close()
                summary["memoryMonitoring"] = monitor.receipt()
                if monitor.abort_reason or monitor.monitor_error or monitor.kill_error:
                    summary.update(status="failed", memoryOrTimeGuardAborted=True)
            except BaseException as error:
                summary.update(status="failed", monitorCleanupError=type(error).__name__)
        try:
            summary["childProcessStopped"] = common.stop_process(process)
            if summary["childProcessStopped"] is not True:
                summary["status"] = "failed"
        except BaseException as error:
            summary.update(status="failed", cleanupError=type(error).__name__, childProcessStopped=False)
        if log:
            try:
                log.close()
            except BaseException as error:
                summary.update(status="failed", logCleanupError=type(error).__name__)
        try:
            assert_identity(summary)
            if installation is not None:
                common.require(common.digest(installation_path) == summary["installationManifestSha256"]
                    and setup.verify_installation() == installation, "installation_changed_during_run")
            summary["integrityVerified"] = True
        except BaseException as error:
            summary.update(status="failed", integrityVerified=False, finalIntegrityError=type(error).__name__)
        # Missing and failed attempts remain distinct from generated translations.
        covered = {r["id"] for r in records}
        mode = "a" if (output / "predictions.jsonl").exists() else "x"
        with (output / "predictions.jsonl").open(mode, encoding="utf-8", newline="\n") as destination:
            for row, _, _, _ in prepared:
                if row["id"] not in covered:
                    append_record(destination, records, {"id": row["id"],
                        "status": "failed" if current and current["id"] == row["id"] else "not_run",
                        "sourceSha256": row["sourceSha256"], "contextSha256": row["contextSha256"],
                        "translation": None, "humanReviewed": False})
    return records


def run(args):
    api = backend(args.candidate)
    rows, identity = data.read_input(args.input)
    terms, catalog = common.read_catalog()
    prepared = make_prompts(rows, args.profile, terms)
    hashes = code_hashes()
    output = args.output.resolve()
    boundary = common.COMPARISONS.resolve()
    common.require(output.is_relative_to(boundary) and output != boundary
                   and not any(p.is_symlink() for p in (args.output, *args.output.parents)), "output_path_not_allowed")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    summary = {"version": VERSION, "status": "prepared" if not args.run else "failed",
        "candidate": args.candidate, "profile": args.profile, "input": identity, "catalog": catalog,
        "codeHashes": hashes, "sampling": api.SAMPLING, "contextSize": api.CONTEXT_SIZE,
        "modelSha256": api.MODEL["sha256"] if args.candidate == "hy7" else large.setup.MODEL["sha256"],
        "runtimeOverrides": common.OVERRIDES if args.candidate == "hy7" else {},
        "samplingNormalization": large.SAMPLING_NORMALIZATION if args.candidate == "hy30" else None,
        "ramBudgetGiB": args.ram_budget_gib, "modelLoaded": False, "completionRequestsSent": 0,
        "childProcessStopped": True, "inferenceRequested": args.run, "integrityVerified": False,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(), "trainingPerformed": False,
        "postProcessingApplied": False, "translationMemoryApplied": False, "appDeploymentPerformed": False,
        "humanReviewed": False, "coldProcessPerRun": True, "automaticRetryEnabled": False,
        "inputFieldsUsed": ["source"] + (["context"] if args.profile == "contextual" else []),
        "promptReceipts": [item[3] for item in prepared], "failure": None,
        "timeLimitsSeconds": {"startup": 240 if args.candidate == "hy7" else large.STARTUP_SECONDS,
                              "request": large.REQUEST_SECONDS, "total": large.TOTAL_SECONDS}}
    common.write_once(output / "plan.json", summary)
    records = execute(args, summary, prepared, output, started) if args.run else []
    if not args.run:
        assert_identity(summary)
        summary["integrityVerified"] = True
    completed = [r for r in records if r["status"] == "completed"]
    summary.update(expectedCount=16, recordedCount=len(records), completed=len(completed),
        elapsedSeconds=round(time.monotonic() - started, 6), finishedAtUtc=datetime.now(timezone.utc).isoformat(),
        outputIntegrityPassed=len(completed) == 16 and all(r["outputIntegrityPassed"] for r in completed),
        automaticCheckFailureIds=[r["id"] for r in completed if not r["automaticChecksPassed"]],
        completionStatusMeaning="request completion; not semantic approval or app promotion",
        automaticChecksAreSemanticCertification=False)
    summary["artifactHashes"] = {p.name: common.digest(p) for p in output.iterdir() if p.is_file()}
    common.write_once(output / "summary.json", summary)
    print(json.dumps({"event": "general-context-finished", "status": summary["status"],
                      "completed": len(completed), "modelLoaded": summary["modelLoaded"]}), flush=True)
    return 0 if summary["status"] in ("prepared", "completed") else 1


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", type=Path, default=data.INPUT)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--candidate", choices=("hy7", "hy30"), required=True)
    cli.add_argument("--profile", choices=("raw", "contextual"), required=True)
    cli.add_argument("--ram-budget-gib", type=int, choices=(8, 9, 10, 11, 12), default=12)
    cli.add_argument("--run", action="store_true", help="Explicitly load the selected local model and run all 16 rows")
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args)
    except Exception as error:
        print(json.dumps({"event": "general-context-refused", "errorType": type(error).__name__}), flush=True)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    raise SystemExit(main())
