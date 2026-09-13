"""Isolated TranslateGemma 12B/27B development screen. Raw source-only translation."""
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
import time

import jinja2
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

import linguistic_screen as screen
import setup_translategemma27 as setup
import run_hymt as transport

ROOT = setup.ROOT
sys.path.insert(0, str(ROOT / "scripts/local-hymt"))
from process_owner import claim_process_owner
from engine import StartupDiagnostics
import process_owner
import engine

CONTEXT_SIZE = 2048
MAX_NEW_TOKENS = 768
CONTROL = re.compile(r"<\|[^\n>]*\|>|<(?:bos|eos|pad|unk|mask|start_of_[^\n>]*|end_of_[^\n>]*|image|audio|video|unused[0-9]+)>")
SETTINGS = {"n_predict": MAX_NEW_TOKENS, "temperature": 0.0, "seed": 20260910,
            "repeat_penalty": 1.0, "repeat_last_n": 0, "top_k": 0, "top_p": 1.0, "min_p": 0.0,
            "samplers": ["temperature"], "stop": [], "ignore_eos": False, "cache_prompt": False,
            "stream": False, "return_tokens": True, "n_keep": 0, "id_slot": 0,
            "presence_penalty": 0.0, "frequency_penalty": 0.0, "dry_multiplier": 0.0,
            "mirostat": 0, "dynatemp_range": 0.0, "typical_p": 1.0, "xtc_probability": 0.0,
            "top_n_sigma": -1.0}


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
            and tokens.count(106) == 1 and 1 not in tokens and 0 not in tokens, "prompt_token_structure")
    require(len(tokens) + MAX_NEW_TOKENS <= CONTEXT_SIZE, "context_budget_exceeded")


def eog_from_log(text):
    marker = "printing all EOG tokens:"
    require(marker in text, "missing_eog_evidence")
    eog = {int(i): token for i, token in re.findall(r"^.*?:\s+-\s+(\d+)\s+\('([^']*)'\)\s*$",
                                                  text.split(marker)[-1], re.MULTILINE)}
    require(eog == {1: "<eos>", 106: "<end_of_turn>"}, "runtime_eog_mismatch")
    return {"ids": sorted(eog), "tokens": {str(k): v for k, v in eog.items()}}


def validate_runtime_tokens(client):
    for token_id, text in setup.TOKEN_STRINGS.items():
        observed = client.request("/tokenize", {"content": text, "add_special": False, "parse_special": True})
        require(observed.get("tokens") == [token_id], "runtime_special_token_mismatch")


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
        require(output_tokens and output_tokens[-1] in {1, 106}, "output_eog_mismatch")
    actual = response.get("generation_settings")
    require(isinstance(actual, dict), "missing_generation_settings")
    for key in ("n_predict", "temperature", "seed", "repeat_penalty", "top_k", "top_p", "min_p", "samplers", "ignore_eos", "stop"):
        require(actual.get(key) == SETTINGS[key], "generation_settings_changed")
    limited = response["stop_type"] == "limit" or generated >= MAX_NEW_TOKENS
    checks = {"nonEmpty": bool(text.strip()), "noLeakedControlTokens": not CONTROL.search(text) and "\ufffd" not in text,
              "numbersPreserved": transport.numeric_tokens(row["source"]) == transport.numeric_tokens(text),
              "currencySymbolsPreserved": Counter(re.findall(r"[$€£¥₩]", row["source"])) == Counter(re.findall(r"[$€£¥₩]", text)),
              "notTruncated": not response["truncated"], "belowOutputLimit": not limited}
    return {"id": row["id"], "domain": row["domain"], "status": "completed", "translation": text,
            "sourceSha256": row["sourceSha256"], "contextSha256": row["contextSha256"],
            "targetSha256": sha_text(text), "promptSha256": sha_text(prompt), "inputTokens": len(tokens),
            "inputTokenIdsSha256": sha_json(tokens), "generatedTokens": generated, "outputTokenIds": output_tokens,
            "outputTokenIdsSha256": sha_json(output_tokens), "generationSeconds": round(elapsed, 6),
            "checks": checks, "numbersPreserved": checks["numbersPreserved"], "automaticChecksPassed": all(checks.values()),
            "stopType": response["stop_type"], "truncated": response["truncated"], "outputLimitReached": limited,
            "actualGenerationSettings": actual, "timings": response.get("timings"), "humanReviewed": False,
            "postProcessingApplied": False, "contextUsed": False}


def code_hashes():
    files = [Path(__file__), Path(setup.__file__), Path(setup.archive_tools.__file__), Path(transport.__file__),
             Path(screen.__file__), Path(engine.__file__), Path(process_owner.__file__), Path(sys.executable)]
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
            "--gpu-layers", "0", "--device", "none", "--no-op-offload", "--threads", str(threads),
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
    output = args.output.absolute()
    setup.plain(output)
    require(output.resolve().is_relative_to((ROOT / ".training/comparisons").resolve()), "output_outside_comparisons")
    require(args.threads == 4 or args.smoke, "thread_tuning_requires_separate_smoke")
    output.mkdir(parents=True, exist_ok=False)
    rows, results, process, diagnostics = [], [], None, None
    summary = {"version": "translategemma-large-screen-v1", "status": "running", "startedAt": datetime.now(timezone.utc).isoformat(),
               "modelSize": args.model_size, "profile": "source-only", "humanReviewed": False, "paidApiCalls": 0,
               "appDeploymentPerformed": False, "postProcessingApplied": False, "glossaryApplied": False,
               "translationMemoryApplied": False, "contextUsed": False, "settings": SETTINGS, "threads": args.threads,
               "purpose": "smoke performance probe" if args.smoke else "development screen; not independent final certification"}
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
        summary["requiredAvailablePhysicalBytes"] = model["size"] + 2 * 1024 ** 3
        require(memory["availablePhysical"] >= summary["requiredAvailablePhysicalBytes"], "insufficient_available_physical_memory")
        phase = "installation"
        installation = setup.verify_installation(model_size=args.model_size)
        template, contract = setup.gguf_contract(dest / model["name"])
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
        require(memory["availablePhysical"] >= summary["requiredAvailablePhysicalBytes"], "memory_changed_before_startup")
        owner = claim_process_owner()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        key = secrets.token_urlsafe(32)
        command = server_command(dest, model, port, key, args.threads)
        startup = time.monotonic()
        process = owner.spawn(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                              bufsize=0, env=transport.runtime_environment(), cwd=str(dest / "runtime"))
        diagnostics = StartupDiagnostics(process.stdout)
        client = transport.LocalClient(f"http://127.0.0.1:{port}", key)
        transport.wait_ready(client, process, startup)
        deadline = time.monotonic() + 5
        while True:
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
        summary["loadSeconds"] = round(time.monotonic() - startup, 6)
        summary["memoryAfterLoad"] = screen.memory_status()
        summary["runtimeTokenizationAndEogValidated"] = True
        diagnostics.discard()
        phase = "prompt-preflight"
        tokenized = []
        for prompt in prompts:
            tokens = client.request("/tokenize", {"content": prompt, "add_special": False, "parse_special": True}).get("tokens")
            validate_prompt_tokens(tokens, vocab_size)
            tokenized.append(tokens)
        phase = "generation"
        with (output / "predictions.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
            for row, prompt, tokens in zip(rows, prompts, tokenized):
                failed_id = row["id"]
                unchanged()
                owner.assert_owned()
                started = time.monotonic()
                response = client.request("/completion", SETTINGS | {"prompt": tokens})
                elapsed = time.monotonic() - started
                # Preserve the returned object even if validation rejects its protocol.
                screen.write_once(output / (row["id"] + "-response.json"), response)
                item = prediction(row, prompt, tokens, response, elapsed, vocab_size)
                item["memoryAfter"] = screen.memory_status()
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
                stream.flush()
                results.append(item)
                print(json.dumps({"id": row["id"], "seconds": item["generationSeconds"], "tokens": item["generatedTokens"],
                                  "truncated": item["truncated"], "outputLimitReached": item["outputLimitReached"]}), flush=True)
                require(item["checks"]["nonEmpty"] and item["checks"]["noLeakedControlTokens"]
                        and item["checks"]["notTruncated"] and item["checks"]["belowOutputLimit"], "invalid_or_incomplete_output")
        phase = "final-integrity"
        unchanged()
        summary["integrityVerified"] = True
        summary["status"] = "completed"
    except BaseException as exc:
        summary.update(status="failed", phase=phase, failedRowId=failed_id,
                       error= str(exc) if isinstance(exc, RunError) else type(exc).__name__)
    finally:
        if diagnostics:
            diagnostics.discard()
        try:
            summary["childProcessStopped"] = transport.stop_process(process)
        except BaseException:
            summary.update(status="failed", childProcessStopped=False, cleanupError="owned_process_cleanup_failed")
        finally:
            if process and process.stdout:
                process.stdout.close()
            if diagnostics:
                diagnostics.join()
        known = {r["id"] for r in results}
        path = output / "predictions.jsonl"
        with path.open("a" if path.exists() else "x", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                if row["id"] not in known:
                    stream.write(json.dumps({"id": row["id"], "status": "failed" if row["id"] == failed_id else "not_run",
                                             "sourceSha256": row["sourceSha256"], "contextSha256": row["contextSha256"]}) + "\n")
        summary.update(count=len(results), recordedCount=len(rows), finishedAt=datetime.now(timezone.utc).isoformat(),
                       predictionsSha256=setup.digest(path), generationSeconds=sum(r["generationSeconds"] for r in results))
        screen.write_once(output / "summary.json", summary)
    return summary


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--model-size", choices=tuple(setup.PROFILES), required=True)
    result.add_argument("--input", type=Path)
    result.add_argument("--ids", nargs="+")
    result.add_argument("--output", type=Path, required=True)
    result.add_argument("--threads", type=int, choices=(4, 8), default=4)
    result.add_argument("--smoke", action="store_true")
    return result


def main():
    result = run(parser().parse_args())
    print(json.dumps({"status": result["status"], "phase": result.get("phase"), "count": result["count"]}), flush=True)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
