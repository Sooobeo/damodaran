"""Run an isolated, offline TranslateGemma GGUF exploratory comparison.

An owned llama.cpp child binds only to loopback and is stopped in finally. No
application DB, API, provider setting, reviewed memory, or training is involved.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from setup_candidate import ASSETS, DEST, ROOT, digest


def gguf_metadata(path):
    formats = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
    with path.open("rb") as stream:
        def number(fmt):
            return struct.unpack("<" + fmt, stream.read(struct.calcsize("<" + fmt)))[0]

        def string():
            return stream.read(number("Q")).decode("utf-8")

        def value(kind, keep):
            if kind == 8:
                size = number("Q")
                if keep:
                    return stream.read(size).decode("utf-8")
                stream.seek(size, 1)
                return None
            if kind == 9:
                element, count = number("I"), number("Q")
                for _ in range(count):
                    value(element, False)
                return None
            return number(formats[kind])

        if stream.read(4) != b"GGUF" or number("I") != 3:
            raise ValueError("Unexpected GGUF format")
        number("Q")
        count = number("Q")
        metadata = {}
        for _ in range(count):
            key, kind = string(), number("I")
            keep = key in {"tokenizer.chat_template", "tokenizer.ggml.bos_token_id", "general.architecture", "general.name"}
            item = value(kind, keep)
            if keep:
                metadata[key] = item
        return metadata


def numbers(text):
    text = unicodedata.normalize("NFKC", text).replace("−", "-")
    text = re.sub(r"(?<=\d)\s*(?:percent|per cent|퍼센트)", "%", text, flags=re.IGNORECASE)
    return Counter(re.sub(r"[,\s]", "", item) for item in re.findall(r"(?<![A-Za-z0-9_])[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][+-]?\d+)?\s*%?", text))


def request(base, endpoint, payload=None, timeout=300):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base + endpoint, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def fail_template(message):
    raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "content/model-comparison/finance-probe-20260909.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / ".training/comparisons/finance-probe-20260909")
    parser.add_argument("--device", choices=("cpu", "vulkan"), default="cpu")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / ".training/comparisons").resolve()):
        raise ValueError("Comparison output must stay in .training/comparisons")
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / f"translategemma-q4-{args.device}-predictions.jsonl"
    if result_path.exists():
        raise ValueError("Output already exists; preserve the previous experiment")
    rows = [json.loads(line) for line in args.input.read_text("utf-8").splitlines() if line.strip()]
    if not rows or len({row["id"] for row in rows}) != len(rows) or any(row.get("split") != "exploratory_probe" for row in rows):
        raise ValueError("Only unique, explicitly exploratory cases may be used")
    integrity_started = time.monotonic()
    model = DEST / ASSETS[1]["name"]
    if model.stat().st_size != ASSETS[1]["size"] or digest(model) != ASSETS[1]["sha256"]:
        raise ValueError("Pinned model integrity check failed")
    metadata = gguf_metadata(model)
    integrity_seconds = time.monotonic() - integrity_started
    template_text = metadata["tokenizer.chat_template"]
    environment = SandboxedEnvironment(undefined=StrictUndefined)
    environment.globals["raise_exception"] = fail_template
    template = environment.from_string(template_text)
    (output / "translategemma-template.jinja").write_text(template_text, "utf-8")
    prompts = []
    for row in rows:
        prompt = template.render(messages=[{"role": "user", "content": [{"type": "text", "source_lang_code": "en", "target_lang_code": "ko", "text": row["source"]}]}], bos_token="<bos>", add_generation_prompt=True)
        if not prompt.startswith("<bos><start_of_turn>user\n") or not prompt.endswith("<start_of_turn>model\n"):
            raise ValueError("Unexpected rendered translation template")
        prompts.append(prompt)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    exe = DEST / "runtime/llama-server.exe"
    # The server's chat parser cannot infer TranslateGemma's structured language
    # fields. /completion receives exact tokens from the original GGUF template
    # rendered above; the server's unused chat endpoint gets a simple template.
    command = [str(exe), "-m", str(model), "--host", "127.0.0.1", "--port", str(port), "--cors-origins", base, "--no-agent", "--ctx-size", "2048", "--parallel", "1", "--gpu-layers", "99" if args.device == "vulkan" else "0", "--threads", "4", "--batch-size", "256", "--ubatch-size", "128", "--offline", "--no-webui", "--no-warmup", "--no-jinja", "--chat-template", "gemma"]
    if args.device == "cpu":
        command += ["--device", "none", "--no-op-offload"]
    log_path = output / f"translategemma-runtime-{args.device}.log"
    started = time.monotonic()
    results = []
    with log_path.open("xb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Runtime exited with {process.returncode}; see local runtime log")
                try:
                    if request(base, "/health", timeout=2).get("status") == "ok":
                        break
                except (urllib.error.URLError, TimeoutError):
                    pass
                if time.monotonic() - started > 180:
                    raise TimeoutError("Model startup exceeded 180 seconds")
                time.sleep(0.25)
            load_seconds = time.monotonic() - started
            print(json.dumps({"candidateReady": True, "loadSeconds": round(load_seconds, 3), "pid": process.pid}), flush=True)
            with result_path.open("x", encoding="utf-8") as destination:
                for row, prompt in zip(rows, prompts):
                    before = time.monotonic()
                    tokens = request(base, "/tokenize", {"content": prompt, "add_special": False, "parse_special": True})["tokens"]
                    if tokens[0] != metadata["tokenizer.ggml.bos_token_id"] or len(tokens) + 512 > 2048:
                        raise ValueError("BOS or context budget check failed")
                    settings = {"prompt": tokens, "n_predict": 512, "temperature": 0, "seed": 20260909, "repeat_penalty": 1.0, "cache_prompt": False, "stream": False}
                    response = request(base, "/completion", settings)
                    elapsed = time.monotonic() - before
                    text = response["content"].strip()
                    item = {"id": row["id"], "domain": row["domain"], "translation": text, "generationSeconds": round(elapsed, 3), "inputTokens": len(tokens), "generatedTokens": response.get("tokens_predicted"), "timings": response.get("timings"), "stopType": response.get("stop_type"), "truncated": response.get("truncated"), "outputLimitReached": response.get("stop_type") == "limit" or response.get("tokens_predicted", 0) >= 512, "numbersPreserved": numbers(row["source"]) == numbers(text), "inputSha256": hashlib.sha256(row["source"].encode()).hexdigest(), "promptSha256": hashlib.sha256(prompt.encode()).hexdigest()}
                    destination.write(json.dumps(item, ensure_ascii=False) + "\n")
                    destination.flush()
                    results.append(item)
                    print(json.dumps({"completed": row["id"], "seconds": round(elapsed, 2), "tokens": item["generatedTokens"], "numbersPreserved": item["numbersPreserved"]}), flush=True)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
    summary = {"purpose": "assistant-authored unreviewed exploratory probe; not held-out final test or app certification", "model": "TranslateGemma 4B third-party Q4_K_M", "modelSha256": ASSETS[1]["sha256"], "inputFileSha256": digest(args.input), "runnerSha256": digest(Path(__file__)), "runtimeExeSha256": digest(exe), "installationManifestSha256": digest(DEST / "installation-manifest.json"), "templateSha256": hashlib.sha256(template_text.encode()).hexdigest(), "integritySeconds": round(integrity_seconds, 3), "loadSeconds": round(load_seconds, 3), "generationSeconds": round(sum(item["generationSeconds"] for item in results), 3), "count": len(results), "device": args.device, "settings": {"context": 2048, "maxNewTokens": 512, "temperature": 0, "seed": 20260909, "repeatPenalty": 1.0, "promptCaching": False}, "runtimeCommand": command, "glossaryApplied": False, "translationMemoryApplied": False, "appDeploymentPerformed": False, "childProcessStopped": process.poll() is not None}
    (output / f"translategemma-q4-{args.device}-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    main()
