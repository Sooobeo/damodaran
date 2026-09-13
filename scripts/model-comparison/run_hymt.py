"""Frozen, free local Hy-MT2 Q8 comparison; never trains or deploys a model.

Only source/context enter prompts. Evaluation annotations stay outside inference.
The publisher's immutable GGUF has an incorrect EOS ID (3, dollar); documented
runtime overrides restore the two EOS IDs in its original generation_config.
No command line sampling overrides, automatic retries, or output replacement.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import struct
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import jinja2
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

import setup_hymt
from setup_hymt import DEST, MODEL, REVISION, ROOT, digest, verify_installation

CATALOG = ROOT / ".training/datasets/finance-v5/term-catalog.json"
COMPARISONS = ROOT / ".training/comparisons"
DATA_VERSION = "finance-quality-20260910-v1"
EXPECTED_ROWS = 24
CONTEXT_SIZE = 8192
MAX_NEW_TOKENS = 4096
HEADER_END = 7498031
HEADER_SHA = "cc88c338a3aae5e90d69acfa0ed7c47c433d0eee02a58363440563681556baff"
TEMPLATE_SHA = "788ac16c5d7bfefc28655928ad524c8f378a44cb24d24fb125d6a5859b167677"
TOKEN_STRINGS = {3: "$", 127957: "<|endoftext|>", 127958: "<|startoftext|>",
                 127960: "<|eos|>", 127961: "<|pad|>", 127962: "<|extra_0|>",
                 127967: "<|extra_5|>"}
OVERRIDES = {"tokenizer.ggml.eos_token_id": "int:127960",
             "tokenizer.ggml.eot_token_id": "int:127967",
             "tokenizer.ggml.add_bos_token": "bool:false",
             "tokenizer.ggml.add_eos_token": "bool:false"}
SAMPLING = {"temperature": 0.7, "top_p": 0.6, "top_k": 20,
            "repeat_penalty": 1.05, "repeat_last_n": 8192, "min_p": 0.0,
            "seed": 42, "samplers": ["penalties", "temperature", "top_k", "top_p"],
            "n_predict": MAX_NEW_TOKENS, "stop": ["<|eos|>", "<|extra_5|>"],
            "ignore_eos": False, "cache_prompt": False, "stream": False,
            "return_tokens": True, "n_keep": 0, "id_slot": 0,
            "presence_penalty": 0.0, "frequency_penalty": 0.0,
            "dry_multiplier": 0.0, "mirostat": 0, "dynatemp_range": 0.0,
            "typical_p": 1.0, "xtc_probability": 0.0, "top_n_sigma": -1.0}
RAW_INSTRUCTION = ("Translate the following text into Korean. Note that you should only "
                   "output the translated result without any additional explanation:")
CONTEXT_INSTRUCTION = """Translate only the Source Text into natural Korean using declarative ~다 endings.
Use Background Information only to resolve meaning; do not translate or add it to the result.
Preserve the source's subjects and roles, negation, conditions, exceptions, comparisons,
numbers, signs, percentages, currency symbols, units, and formulas. Do not add facts or advice.
The terminology entries are conditional references, not mandatory replacements. Use an entry
only when the source expression has the financial meaning described by its Korean definition.
For an ordinary meaning or a different financial meaning, translate that meaning naturally;
do not force the listed financial term. Context takes priority over an isolated word match.
Return only the Korean translation of Source Text, without headings or explanation."""


class RunError(ValueError):
    """A fixed, non-sensitive diagnostic code, safe to put in console/summary."""


def require(condition, code):
    if not condition:
        raise RunError(code)


def sha_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha_json(value):
    return sha_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def write_once(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def limited_bytes(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    require(len(data) <= limit, "file_size_limit")
    return data


def reject_special_text(value):
    require("\x00" not in value and re.search(r"<\|[^\n>]*\|>", value) is None,
            "input_contains_model_control_token")


def read_frozen_input(path):
    path = path.resolve()
    allowed = (COMPARISONS.resolve(), (ROOT / "content/model-comparison").resolve())
    require(path.name == "dataset.jsonl" and any(path.is_relative_to(p) for p in allowed),
            "input_path_not_allowed")
    raw = limited_bytes(path, 2 * 1024 * 1024)
    manifest_path = path.with_name("dataset-manifest.json")
    manifest_bytes = limited_bytes(manifest_path, 2 * 1024 * 1024)
    manifest = json.loads(manifest_bytes)
    require(manifest.get("version") == DATA_VERSION and manifest.get("status") == "frozen"
            and manifest.get("humanReviewed") is False
            and manifest.get("sourceType") == "assistant_authored_unreviewed", "input_not_frozen")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    require(len(rows) == EXPECTED_ROWS and all(isinstance(r, dict) for r in rows), "input_row_count")
    dataset = manifest.get("dataset", {})
    ids = [r.get("id") for r in rows]
    require(dataset.get("file") == path.name and dataset.get("sha256") == hashlib.sha256(raw).hexdigest()
            and dataset.get("count") == EXPECTED_ROWS and dataset.get("ids") == ids, "input_manifest_mismatch")
    require(all(isinstance(i, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", i) for i in ids)
            and len(set(ids)) == len(ids), "invalid_or_duplicate_ids")
    clean = []
    for row in rows:
        source, context = row.get("source"), row.get("context", "")
        require(row.get("split") == "exploratory_probe", "input_split_not_exploratory")
        require(isinstance(source, str) and 1 <= len(source.strip()) <= 12000
                and isinstance(context, str) and len(context) <= 12000, "input_text_range")
        reject_special_text(source)
        reject_special_text(context)
        require(row.get("sourceSha256") == sha_text(source), "source_hash_mismatch")
        if "contextSha256" in row:
            require(row["contextSha256"] == sha_text(context), "context_hash_mismatch")
        # This allowlist is the only row representation passed to prompt generation.
        clean.append({"id": row["id"], "source": source, "context": context,
                      "sourceSha256": sha_text(source), "contextSha256": sha_text(context)})
    reviews = manifest.get("reviewFiles")
    require(isinstance(reviews, list) and bool(reviews), "missing_review_evidence")
    evidence = []
    for item in reviews:
        name = item.get("file", "")
        require(isinstance(name, str) and Path(name).name == name and name not in ("", ".", ".."),
                "invalid_review_path")
        review_path = path.parent / name
        require(review_path.resolve().parent == path.parent and not review_path.is_symlink(), "invalid_review_path")
        require(digest(review_path) == item.get("sha256"), "review_hash_mismatch")
        evidence.append({"path": str(review_path), "sha256": item["sha256"]})
    return clean, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                   "manifestPath": str(manifest_path), "manifestSha256": hashlib.sha256(manifest_bytes).hexdigest(),
                   "version": DATA_VERSION, "count": len(clean), "ids": ids, "reviewEvidence": evidence}


def read_catalog():
    raw = limited_bytes(CATALOG, 512 * 1024)
    catalog = json.loads(raw)
    terms = catalog.get("terms", [])
    require(len(terms) == 54 and len({t.get("id") for t in terms}) == 54, "catalog_count")
    for term in terms:
        require(all(isinstance(term.get(k), str) and bool(term[k].strip())
                    for k in ("id", "source", "target", "definition")), "catalog_fields")
        require(isinstance(term.get("aliases", []), list)
                and all(isinstance(a, str) and a for a in term.get("aliases", [])), "catalog_aliases")
        for text in [term["source"], term["target"], term["definition"], *term.get("aliases", [])]:
            reject_special_text(text)
    return terms, {"path": str(CATALOG), "sha256": hashlib.sha256(raw).hexdigest(),
                   "version": catalog.get("version"), "count": len(terms)}


def term_matches(source, terms):
    possible = []
    for term in terms:
        for phrase in dict.fromkeys([term["source"], *term.get("aliases", [])]):
            acronym = (not re.search(r"\s", phrase) and any(c.isalpha() for c in phrase)
                       and phrase.upper() == phrase)
            pattern = r"(?<![\w])" + re.escape(phrase) + r"(?![\w])"
            for match in re.finditer(pattern, source, 0 if acronym else re.IGNORECASE):
                possible.append({"id": term["id"], "start": match.start(), "end": match.end(),
                                 "source": match.group(), "target": term["target"],
                                 "definition": term["definition"]})
    chosen = []
    for match in sorted(possible, key=lambda m: (-(m["end"] - m["start"]), m["start"], m["id"])):
        if not any(match["start"] < p["end"] and p["start"] < match["end"] for p in chosen):
            chosen.append(match)
    return sorted(chosen, key=lambda m: (m["start"], m["end"]))


def build_user_prompt(row, profile, terms):
    require(profile in ("raw", "contextual"), "unknown_profile")
    source = row["source"]
    if profile == "raw":
        return RAW_INSTRUCTION + "\n" + source, []
    matches = term_matches(source, terms)
    hints, seen = [], set()
    for match in matches:
        if match["id"] not in seen:
            hints.append(f'{match["source"]} → {match["target"]} (뜻: {match["definition"]})')
            seen.add(match["id"])
    prompt = CONTEXT_INSTRUCTION + "\n\n[Background Information]\n" + (row.get("context", "") or "None provided.")
    prompt += "\n\n[Conditional terminology references]\n" + ("\n".join(hints) or "None matched.")
    prompt += "\n\n[Source Text]\n" + source
    return prompt, matches


def gguf_contract(path):
    # Only the first 8 MiB: metadata and tensor descriptors, never tensor loading.
    with path.open("rb") as stream:
        data = stream.read(8 * 1024 * 1024)
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
    tensor_count, count = number("Q"), number("Q")
    require(tensor_count == 354 and count == 33, "gguf_header_identity")
    metadata = {}
    for _ in range(count):
        key, kind = string(), number("I")
        require(key not in metadata, "duplicate_gguf_key")
        metadata[key] = value(kind)
    require(stream.tell() == HEADER_END and hashlib.sha256(data[:HEADER_END]).hexdigest() == HEADER_SHA,
            "gguf_metadata_hash")
    require(metadata.get("general.architecture") == "hunyuan-dense"
            and metadata.get("tokenizer.ggml.pre") == "hunyuan", "gguf_architecture")
    tokens, types = metadata["tokenizer.ggml.tokens"], metadata["tokenizer.ggml.token_type"]
    require(len(tokens) == 128167 and len(types) == len(tokens), "gguf_vocabulary_size")
    require(all(tokens[i] == text for i, text in TOKEN_STRINGS.items()) and types[3] == 1
            and all(types[i] == 3 for i in TOKEN_STRINGS if i != 3), "gguf_special_tokens")
    expected = {"tokenizer.ggml.bos_token_id": 127958, "tokenizer.ggml.eos_token_id": 3,
                "tokenizer.ggml.eot_token_id": 127960, "tokenizer.ggml.padding_token_id": 127961,
                "tokenizer.ggml.seperator_token_id": 127962}
    require(all(metadata.get(k) == v for k, v in expected.items())
            and "tokenizer.ggml.add_bos_token" not in metadata and "tokenizer.ggml.add_eos_token" not in metadata,
            "gguf_original_token_metadata")
    template = metadata["tokenizer.chat_template"]
    require(sha_text(template) == TEMPLATE_SHA, "gguf_template_hash")
    tensor_types = Counter()
    for _ in range(tensor_count):
        string()
        dimensions = number("I")
        require(1 <= dimensions <= 4, "gguf_tensor_dimensions")
        for _ in range(dimensions):
            number("Q")
        tensor_types[number("I")] += 1
        number("Q")
    require(tensor_types == {0: 129, 8: 225}, "gguf_tensor_types")
    return template, {"architecture": "hunyuan-dense", "metadataSha256": HEADER_SHA,
                      "templateSha256": TEMPLATE_SHA, "tokenStrings": TOKEN_STRINGS,
                      "originalMetadata": expected, "originalAddBos": None, "originalAddEos": None,
                      "runtimeOverrides": OVERRIDES, "tensorTypes": {"F32": 129, "Q8_0": 225},
                      "expectedEogIds": [127957, 127960, 127967],
                      "additionalRuntimeEog": {"127957": "<|endoftext|>"}}


def render_prompt(template_text, content):
    require(sha_text(template_text) == TEMPLATE_SHA, "template_not_pinned")
    environment = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    template = environment.from_string(template_text)
    rendered = template.render(messages=[{"role": "user", "content": content}], add_generation_prompt=True,
                               bos_token="<|startoftext|>", eos_token="<|eos|>")
    require(rendered == "<|startoftext|>" + content + "<|extra_0|>", "rendered_prompt_contract")
    return rendered


def numeric_tokens(text):
    text = unicodedata.normalize("NFKC", text).replace("−", "-")
    text = re.sub(r"(?<=\d)\s*(?:percent|per cent|퍼센트)", "%", text, flags=re.IGNORECASE)
    return Counter(re.sub(r"[,\s]", "", item) for item in re.findall(
        r"(?<![A-Za-z0-9_])[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][+-]?\d+)?\s*%?", text))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RunError("loopback_redirect_blocked")


class LocalClient:
    def __init__(self, base, key):
        parsed = urllib.parse.urlsplit(base)
        require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port
                and parsed.path == "" and not parsed.query and not parsed.username, "non_loopback_server")
        self.base, self.key = base, key
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, endpoint, payload=None, timeout=1800):
        require(endpoint in ("/health", "/tokenize", "/detokenize", "/completion", "/props"), "endpoint_not_allowed")
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + endpoint, data=data, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.key, "Origin": self.base})
        with self.opener.open(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        require(len(raw) <= 2 * 1024 * 1024, "response_size_limit")
        value = json.loads(raw)
        require(isinstance(value, dict) and "error" not in value, "invalid_server_response")
        return value


def eog_from_log(text):
    # b10874 llama-vocab.cpp emits this section after applying runtime overrides.
    marker = "printing all EOG tokens:"
    require(marker in text, "missing_runtime_eog_evidence")
    section = text.split(marker)[-1]
    eog = {int(i): token for i, token in re.findall(r"^.*?:\s+-\s+(\d+)\s+\('([^']*)'\)\s*$", section, re.MULTILINE)}
    require(3 not in eog and eog.get(127960) == "<|eos|>" and eog.get(127967) == "<|extra_5|>",
            "runtime_eog_mismatch")
    require(all(i in {127957, 127960, 127967} and TOKEN_STRINGS[i] == t for i, t in eog.items()), "unexpected_runtime_eog")
    return {"ids": sorted(eog), "tokens": eog, "additionalIds": sorted(set(eog) - {127960, 127967}),
            "dollarIsEog": False}


def validate_runtime_tokens(client):
    checked = {}
    for token_id, content in TOKEN_STRINGS.items():
        tokens = client.request("/tokenize", {"content": content, "add_special": False, "parse_special": True})["tokens"]
        require(tokens == [token_id], "runtime_token_id_mismatch")
        checked[str(token_id)] = content
    require(client.request("/detokenize", {"tokens": [3]}).get("content") == "$", "dollar_detokenization")
    return checked


def validate_prompt_tokens(tokens):
    require(isinstance(tokens, list) and all(type(t) is int and 0 <= t < 128167 for t in tokens),
            "invalid_prompt_tokens")
    require(tokens and tokens[0] == 127958 and tokens[-1] == 127962 and tokens.count(127958) == 1
            and tokens.count(127962) == 1 and not ({127957, 127960, 127967} & set(tokens)), "prompt_special_tokens")
    require(len(tokens) + MAX_NEW_TOKENS < CONTEXT_SIZE, "prompt_context_budget")


def server_command(port, key):
    base = f"http://127.0.0.1:{port}"
    return [str(DEST / "runtime/llama-server.exe"), "--model", str(DEST / MODEL["name"]),
            "--host", "127.0.0.1", "--port", str(port), "--cors-origins", base,
            "--api-key", key, "--no-agent", "--offline", "--no-webui", "--no-warmup",
            "--jinja", "--ctx-size", str(CONTEXT_SIZE), "--parallel", "1", "--no-context-shift",
            "--gpu-layers", "0", "--device", "none", "--no-op-offload", "--threads", "4",
            "--threads-batch", "4", "--batch-size", "256", "--ubatch-size", "128", "--cache-ram", "0",
            # b10874 maps library INFO (including the EOG inventory) to TRACE=4.
            "--cache-type-k", "f16", "--cache-type-v", "f16", "--log-verbosity", "4",
            "--override-kv", ",".join(f"{k}={v}" for k, v in OVERRIDES.items()),
            "--temp", "0.7", "--top-p", "0.6", "--top-k", "20", "--min-p", "0",
            "--repeat-penalty", "1.05", "--repeat-last-n", "8192", "--seed", "42",
            "--samplers", ";".join(SAMPLING["samplers"]), "--n-predict", str(MAX_NEW_TOKENS)]


def stop_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
    return process is None or process.poll() is not None


def runtime_environment():
    # b10874's Vertex compatibility also overrides ports/routes via AIP_*.
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("LLAMA_", "GGML_", "AIP_"))}
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    return env


def wait_ready(client, process, started):
    while time.monotonic() - started < 240:
        require(process.poll() is None, "runtime_exited_before_ready")
        try:
            if client.request("/health", timeout=2).get("status") == "ok":
                return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.25)
    raise RunError("runtime_startup_timeout")


def validate_generation_settings(settings):
    require(isinstance(settings, dict), "missing_generation_settings")
    for key in ("temperature", "top_p", "top_k", "repeat_penalty", "repeat_last_n", "min_p", "seed",
                "samplers", "n_predict", "ignore_eos", "presence_penalty", "frequency_penalty",
                "dry_multiplier", "mirostat", "dynatemp_range", "typical_p", "xtc_probability", "stop", "top_n_sigma"):
        actual, expected = settings.get(key), SAMPLING[key]
        equal = math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-8) if (
            type(expected) in (float, int) and type(actual) in (float, int)) else actual == expected
        require(equal, "actual_sampling_mismatch_" + key)
    # b10874 task_params::to_json does not include n_ctx. Verify it in /props.
    return {k: settings[k] for k in SAMPLING if k in settings}


def prediction(row, prompt, matches, tokens, response, elapsed):
    require(isinstance(response.get("content"), str), "missing_translation")
    text = response["content"]  # No correction, trimming, replacement or hidden regeneration.
    generated = response.get("tokens_predicted")
    output_tokens = response.get("tokens")
    require(type(generated) is int and 0 <= generated <= MAX_NEW_TOKENS, "invalid_generated_count")
    require(isinstance(output_tokens, list) and all(type(t) is int and 0 <= t < 128167 for t in output_tokens),
            "missing_generated_token_evidence")
    require(len(output_tokens) == generated, "generated_token_count_mismatch")
    require(response.get("stop_type") in ("eos", "word", "limit") and type(response.get("truncated")) is bool,
            "missing_stop_evidence")
    if response["stop_type"] == "eos":
        require(output_tokens and output_tokens[-1] in {127957, 127960, 127967}, "output_eog_token_mismatch")
    if response["stop_type"] == "word":
        require(response.get("stopping_word") in SAMPLING["stop"], "unexpected_stopping_word")
    settings = validate_generation_settings(response.get("generation_settings"))
    require("\ufffd" not in text, "invalid_unicode_output")
    limited = response["stop_type"] == "limit" or generated >= MAX_NEW_TOKENS
    checks = {"nonEmpty": bool(text.strip()), "numbersPreserved": numeric_tokens(row["source"]) == numeric_tokens(text),
              "currencySymbolsPreserved": Counter(re.findall(r"[$€£¥₩]", row["source"])) == Counter(re.findall(r"[$€£¥₩]", text)),
              "notTruncated": not response["truncated"], "belowOutputLimit": not limited,
              "noLeakedControlTokens": re.search(r"<\|[^\n>]*\|>", text) is None}
    return {"id": row["id"], "status": "completed", "translation": text, "sourceSha256": row["sourceSha256"],
            "contextSha256": row["contextSha256"], "promptSha256": sha_text(prompt), "targetSha256": sha_text(text),
            "inputTokens": len(tokens), "inputTokenIdsSha256": sha_json(tokens), "generatedTokens": generated,
            "outputTokenIds": output_tokens, "outputTokenIdsSha256": sha_json(output_tokens),
            "stopType": response["stop_type"], "stoppingWord": response.get("stopping_word"),
            "truncated": response["truncated"], "outputLimitReached": limited, "checks": checks,
            "numbersPreserved": checks["numbersPreserved"], "automaticChecksPassed": all(checks.values()),
            "generationSeconds": round(elapsed, 6), "timings": response.get("timings"),
            "actualGenerationSettings": settings, "conditionalTermMatches": matches,
            "humanReviewed": False, "postProcessingApplied": False}


def code_hashes():
    paths = [Path(__file__).resolve(), Path(setup_hymt.__file__).resolve(), Path(sys.executable).resolve()]
    # Include implementation files, not just Jinja's __init__/version string.
    paths += sorted(Path(jinja2.__file__).resolve().parent.rglob("*.py"))
    return {str(p): digest(p) for p in paths}


def identity_hashes(input_info, catalog_info):
    # Bind to the exact bytes already parsed, not a later replacement observed
    # after the long model hash check. Review evidence is also checked again.
    expected = {input_info["path"]: input_info["sha256"], input_info["manifestPath"]: input_info["manifestSha256"],
                catalog_info["path"]: catalog_info["sha256"]}
    expected.update({item["path"]: item["sha256"] for item in input_info["reviewEvidence"]})
    check_identity_unchanged(expected)
    return code_hashes() | expected | {str(DEST / "installation-manifest.json"): digest(DEST / "installation-manifest.json")}


def check_identity_unchanged(before):
    require(all(Path(path).is_file() and digest(Path(path)) == value for path, value in before.items()),
            "execution_identity_changed")


def run(input_path, output, profile):
    require(profile in ("raw", "contextual"), "unknown_profile")
    output = output.resolve()
    require(output.is_relative_to(COMPARISONS.resolve()) and output != COMPARISONS.resolve(), "output_path_not_allowed")
    # A directory itself is the exclusive experiment reservation; never resume/overwrite.
    output.mkdir(parents=True, exist_ok=False)
    result_path, summary_path = output / "predictions.jsonl", output / "summary.json"
    started = time.monotonic()
    rows, results, process, log = [], [], None, None
    summary = {"version": 1, "profile": profile, "status": "failed", "startedAt": datetime.now(timezone.utc).isoformat(),
               "model": "tencent/Hy-MT2-7B-GGUF", "revision": REVISION, "modelSha256": MODEL["sha256"],
               "precision": "publisher Q8_0", "checkpoint": None, "trainingPerformed": False,
               "translationMemoryApplied": False, "postProcessingApplied": False, "appDeploymentPerformed": False,
               "humanReviewed": False, "sampling": SAMPLING, "contextSize": CONTEXT_SIZE,
               "conditionalGlossaryHintsApplied": profile == "contextual", "inputFieldsUsed": ["source"] + (["context"] if profile == "contextual" else []),
               "cpuOnly": True, "threads": 4, "batchSize": 256, "ubatchSize": 128, "kvCacheType": "f16",
               "failure": None, "childProcessStopped": True, "completionRequestsSent": 0}
    current, response, prompt = None, None, None
    with result_path.open("x", encoding="utf-8", newline="\n") as destination:
        def append(item):
            destination.write(json.dumps(item, ensure_ascii=False) + "\n")
            destination.flush()
            os.fsync(destination.fileno())
            results.append(item)

        try:
            summary["initialCodeHashes"] = code_hashes()
            rows, input_info = read_frozen_input(input_path)
            terms, catalog_info = read_catalog()
            summary.update(input=input_info, catalog=catalog_info)
            verified = verify_installation()
            summary["installation"] = verified
            summary["codeAndInputHashes"] = identity_hashes(input_info, catalog_info)
            check_identity_unchanged(summary["initialCodeHashes"])
            template, contract = gguf_contract(DEST / MODEL["name"])
            summary["ggufContract"] = contract
            summary["runtimeOverrides"] = OVERRIDES
            summary["samplingSource"] = "Official model card top_p=0.6; source generation_config and GGUF default=0.8 overridden explicitly"
            summary["jinjaVersion"] = jinja2.__version__
            summary["python"] = {"version": sys.version, "executable": sys.executable,
                                 "platform": platform.platform(), "machine": platform.machine()}
            prompts = []
            for row in rows:
                content, matches = build_user_prompt(row, profile, terms)
                prompts.append((render_prompt(template, content), matches))
            summary["promptHashes"] = [{"id": row["id"], "sha256": sha_text(prompt)} for row, (prompt, _) in zip(rows, prompts)]
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            key = secrets.token_urlsafe(32)
            command = server_command(port, key)
            redacted = command.copy()
            redacted[redacted.index("--api-key") + 1] = "[EPHEMERAL_REDACTED]"
            summary["runtimeCommand"] = redacted
            summary["runtimeExeSha256"] = digest(DEST / "runtime/llama-server.exe")
            summary["integritySeconds"] = round(time.monotonic() - started, 6)
            write_once(output / "run.json", summary | {"status": "prepared"})
            # Inherited llama settings must not silently change this frozen profile.
            env = runtime_environment()
            log_path = output / "runtime.log"
            log = log_path.open("xb")
            load_started = time.monotonic()
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env,
                                       cwd=str(DEST / "runtime"), creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            summary["childPid"] = process.pid
            client = LocalClient(f"http://127.0.0.1:{port}", key)
            wait_ready(client, process, load_started)
            summary["loadSeconds"] = round(time.monotonic() - load_started, 6)
            log.flush()
            summary["actualEog"] = eog_from_log(log_path.read_text("utf-8", errors="replace"))
            summary["actualTokenizerTokens"] = validate_runtime_tokens(client)
            props = client.request("/props")
            require(Path(props.get("model_path", "")).resolve() == (DEST / MODEL["name"]).resolve(), "runtime_model_path")
            require(props.get("chat_template") == template, "runtime_chat_template")
            default_settings = props.get("default_generation_settings", {})
            require(default_settings.get("n_ctx") == CONTEXT_SIZE and props.get("total_slots") == 1,
                    "actual_context_or_slots_mismatch")
            summary["actualContextSize"] = default_settings["n_ctx"]
            summary["runtimeBuildInfo"] = props.get("build_info")
            print(json.dumps({"event": "hymt-ready", "profile": profile, "count": len(rows)}), flush=True)
            for row, (prompt, matches) in zip(rows, prompts):
                current = row
                response = None
                require(process.poll() is None, "runtime_exited")
                check_identity_unchanged(summary["codeAndInputHashes"])
                tokens = client.request("/tokenize", {"content": prompt, "add_special": False, "parse_special": True})["tokens"]
                validate_prompt_tokens(tokens)
                before = time.monotonic()
                summary["completionRequestsSent"] += 1
                response = client.request("/completion", SAMPLING | {"prompt": tokens})
                item = prediction(row, prompt, matches, tokens, response, time.monotonic() - before)
                append(item)
                current = None
                print(json.dumps({"event": "hymt-completed", "profile": profile, "id": row["id"],
                                  "seconds": item["generationSeconds"], "automaticChecksPassed": item["automaticChecksPassed"]}), flush=True)
            check_identity_unchanged(summary["codeAndInputHashes"])
            # Re-hash all pinned weights and runtime files at completion, not only exe.
            require(verify_installation() == verified, "installation_changed_during_run")
            require([r["id"] for r in results] == input_info["ids"], "prediction_coverage_mismatch")
            summary["status"] = "completed"
        except BaseException as exc:
            code = str(exc) if isinstance(exc, RunError) else type(exc).__name__
            summary["failure"] = {"code": code, "type": type(exc).__name__, "rowId": current["id"] if current else None}
            if current is not None:
                # Preserve a completed response even when its contract validation
                # fails. Never copy the response's echoed prompt into artifacts.
                evidence = {k: response[k] for k in ("content", "tokens", "tokens_predicted", "stop_type", "truncated", "timings")
                            if isinstance(response, dict) and k in response}
                append({"id": current["id"], "status": "failed", "sourceSha256": current["sourceSha256"],
                        "contextSha256": current["contextSha256"], "errorCode": code,
                        "promptSha256": sha_text(prompt) if prompt is not None else None,
                        "translation": evidence.get("content"), "responseEvidence": evidence})
        finally:
            try:
                summary["childProcessStopped"] = stop_process(process)
            except BaseException as exc:
                summary["childProcessStopped"] = process is None or process.poll() is not None
                summary["status"] = "failed"
                summary["cleanupFailure"] = type(exc).__name__
            if log is not None:
                log.close()
            completed_ids = {r["id"] for r in results}
            for row in rows:
                if row["id"] not in completed_ids:
                    append({"id": row["id"], "status": "not_run", "sourceSha256": row["sourceSha256"],
                            "contextSha256": row["contextSha256"], "translation": None, "errorCode": "run_did_not_complete"})
    summary["expectedCount"] = len(rows)
    summary["recordCount"] = len(results)
    summary["completedCount"] = sum(r["status"] == "completed" for r in results)
    summary["inferenceRequested"] = summary["completionRequestsSent"] > 0
    summary["automaticCheckFailures"] = [r["id"] for r in results if r.get("automaticChecksPassed") is False]
    summary["automaticChecksPassed"] = summary["status"] == "completed" and not summary["automaticCheckFailures"]
    summary["elapsedSeconds"] = round(time.monotonic() - started, 6)
    summary["finishedAt"] = datetime.now(timezone.utc).isoformat()
    summary["predictionsSha256"] = digest(result_path)
    if (output / "runtime.log").exists():
        summary["runtimeLogSha256"] = digest(output / "runtime.log")
    write_once(summary_path, summary)
    print(json.dumps({"event": "hymt-finished", "status": summary["status"], "count": summary["completedCount"],
                      "automaticChecksPassed": summary["automaticChecksPassed"], "failure": summary["failure"]}), flush=True)
    return 0 if summary["status"] == "completed" else 1


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--input", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--profile", choices=("raw", "contextual"), required=True)
    return cli


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args.input, args.output, args.profile)
    except Exception as exc:
        # Do not print arbitrary exception text: HTTP errors can contain input data.
        print(json.dumps({"event": "hymt-refused", "errorType": type(exc).__name__,
                          "errorCode": str(exc) if isinstance(exc, RunError) else "output_or_initialization_failure"}), flush=True)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    raise SystemExit(main())
