"""Offline Marian full fine-tuning, with a sealed final test and resumable runs.

No glossary, replacement rules, or remote inference are used. The bench update is
discarded; train starts again from the pinned original weights. All writes stay
under .training. See --help for validate / bench / train / evaluate / export.
"""
from __future__ import annotations

import argparse
from collections import Counter
import contextlib
from datetime import datetime, timezone
from difflib import SequenceMatcher
import gc
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import signal
import sys
import time
import unicodedata

APP_ROOT = Path(__file__).resolve().parents[2]
WORK_ROOT = APP_ROOT / ".training"
BASE_ID = "Helsinki-NLP/opus-mt-tc-big-en-ko"
BASE_REVISION = "ae8606b7b29a495f31ce679cee2007f536a3a5ce"
BASE_WEIGHT_HASH = "f7d6ccf642f1672e6b06d46bc406a3f12220b70603f6745dfbce5c097f8511c2"
SCRIPT_VERSION = "marian-finance-train-v1"
STOP_REQUESTED = False

# Frozen before baseline evaluation. A failed gate keeps the experiment and
# checkpoint for inspection, but does not authorize deployment or export.
GATE = {
    "version": 1,
    "minTermAccuracyGain": 0.05,
    "termCeilingException": 0.95,
    "maxChrFRegression": 1.0,
    "maxBleuRegression": 1.0,
    "allowNumericRegression": False,
    "allowEmptyOrCappedOutput": False,
    "requireChangedWeights": True,
    "generalRetention": {"maxChrFRegression": 1.0, "maxBleuRegression": 1.0, "allowNumericRegression": False},
    "selection": "dev gate eligibility, then chrF, then term accuracy, then negative token loss",
    "note": "Training and selection use train/dev only. Final test is consumed once per data hash; no tuning after test. These small authored samples do not establish general translation quality.",
}


class StopRequested(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def emit(event, **values):
    print(json.dumps({"event": event, "time": now(), **values}, ensure_ascii=False, allow_nan=False), flush=True)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", "utf-8")
    os.replace(temporary, path)


def append_json(path, value):
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def relative(path):
    return path.resolve().relative_to(APP_ROOT).as_posix()


def local_path(value, required_root=APP_ROOT):
    path = (APP_ROOT / value).resolve()
    if not path.is_relative_to(required_root.resolve()):
        raise ValueError("Path must stay inside the required project directory")
    return path


@contextlib.contextmanager
def exclusive_run(run_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
        raise ValueError("Invalid run ID")
    directory = WORK_ROOT / "runs" / run_id
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "active.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def normalized_source(value, numbers=False):
    value = unicodedata.normalize("NFKC", value).casefold()
    if numbers:
        value = re.sub(r"\d+(?:[.,]\d+)*", " <number> ", value)
    return " ".join(re.findall(r"[a-z]+|[가-힣]+|<number>|\d+", value))


def normalized_term(value):
    return re.sub(r"[^가-힣a-z0-9]", "", unicodedata.normalize("NFKC", value).casefold())


def term_targets(row):
    values = []
    for term in row.get("terms", []):
        value = term if isinstance(term, str) else term.get("target") if isinstance(term, dict) else None
        if not isinstance(value, str) or not normalized_term(value):
            raise ValueError("Each term must be a Korean target string or a source/target object")
        values.append(value)
    return list(dict.fromkeys(values))


def numeric_tokens(text):
    text = unicodedata.normalize("NFKC", text).replace("−", "-")
    text = re.sub(r"(?<=\d)\s*(?:percent|per cent|퍼센트)", "%", text, flags=re.IGNORECASE)
    matches = re.findall(r"(?<![A-Za-z0-9_])[+-]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][+-]?\d+)?\s*%?", text)
    return Counter(re.sub(r"[,\s]", "", value) for value in matches)


def read_rows(path, split):
    rows = []
    identifiers = set()
    for line_number, line in enumerate(path.read_text("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if (not isinstance(row, dict) or not isinstance(row.get("source"), str)
                or not isinstance(row.get("target"), str) or not row["source"].strip()
                or not row["target"].strip() or len(row["source"]) > 8000 or len(row["target"]) > 8000
                or not isinstance(row.get("terms", []), list)):
            raise ValueError(f"Invalid {split} row at line {line_number}")
        accepted_splits = {"test", "evaluation"} if split == "test" else {split}
        if row.get("split", split) not in accepted_splits:
            raise ValueError(f"Split mismatch at {split} line {line_number}")
        if row.get("domain") not in {"finance", "general"}:
            raise ValueError(f"Expected finance/general domain at {split} line {line_number}")
        row = {**row, "id": str(row.get("id", f"{split}-{line_number}"))}
        if row["id"] in identifiers:
            raise ValueError(f"Duplicate ID in {split}")
        identifiers.add(row["id"])
        term_targets(row)
        if numeric_tokens(row["source"]) != numeric_tokens(row["target"]):
            raise ValueError(f"Reference number mismatch in {split} row {row['id']}")
        rows.append(row)
    if not rows:
        raise ValueError(f"Empty {split} dataset")
    return rows


def validate_data(args, run_dir):
    paths = {split: local_path(getattr(args, split + "_data")) for split in ("train", "dev", "test")}
    rows = {split: read_rows(path, split) for split, path in paths.items()}
    issues = []
    flat = [(split, row, normalized_source(row["source"]), normalized_source(row["source"], True))
            for split, values in rows.items() for row in values]
    for position, (split, row, exact, template) in enumerate(flat):
        for other_split, other, other_exact, other_template in flat[:position]:
            reason = None
            if exact == other_exact:
                reason = "normalized-source-duplicate"
            elif split != other_split and template == other_template:
                reason = "numeric-template-leakage"
            elif split != other_split and min(len(exact), len(other_exact)) >= 35:
                tokens, other_tokens = set(template.split()), set(other_template.split())
                overlap = len(tokens & other_tokens) / max(1, len(tokens | other_tokens))
                ratio = SequenceMatcher(None, template, other_template, autojunk=False).ratio()
                if ratio >= 0.92 or (overlap >= 0.85 and ratio >= 0.82):
                    reason = "near-template-leakage"
            if reason:
                issues.append({"kind": reason, "first": {"split": other_split, "id": other["id"]},
                               "second": {"split": split, "id": row["id"]}})
    report = {"checkedAt": now(), "counts": {split: len(value) for split, value in rows.items()},
              "files": {split: {"path": relative(path), "sha256": sha256(path)} for split, path in paths.items()},
              "checks": "case/punctuation normalized source duplicates; number-masked templates; cross-split character similarity >= .92 or token Jaccard >= .85 with character similarity >= .82",
              "issues": issues, "passed": not issues}
    write_json(run_dir / "data-validation.json", report)
    if issues:
        raise ValueError(f"Dataset leakage check failed: {len(issues)} issue(s); see data-validation.json")
    return rows, report


def network_off():
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        os.environ[key] = "1"
    os.environ["HF_HOME"] = str(WORK_ROOT / "hf-cache")
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    def audit(event, _args):
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.sendto"}:
            raise RuntimeError("Network is disabled for model training")
    sys.addaudithook(audit)


def stack(args):
    network_off()
    import torch
    from transformers import MarianMTModel, MarianTokenizer
    torch.set_num_threads(args.threads)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    device = "cpu"
    if args.device != "cpu":
        try:
            if hasattr(torch, "xpu") and torch.xpu.is_available():
                probe = torch.ones((4, 4), device="xpu", requires_grad=True)
                (probe @ probe).sum().backward()
                torch.xpu.synchronize()
                torch.xpu.manual_seed_all(args.seed)
                device = "xpu"
                del probe
        except Exception as error:
            if args.device == "xpu":
                raise
            emit("device-fallback", selected="cpu", failureType=type(error).__name__)
    if args.device == "xpu" and device != "xpu":
        raise RuntimeError("Requested XPU is not available")
    versions = {name: importlib.metadata.version(name) for name in ("torch", "transformers", "sentencepiece", "safetensors", "sacrebleu")}
    emit("runtime", device=device, precision=args.precision, threads=args.threads, versions=versions)
    return torch, MarianMTModel, MarianTokenizer, device, versions


def synchronize(torch, device):
    if device == "xpu":
        torch.xpu.synchronize()


def load_model(model_class, path, torch, device):
    model = model_class.from_pretrained(str(path), local_files_only=True, use_safetensors=True,
                                        torch_dtype=torch.float32, attn_implementation="eager")
    model.config.use_cache = False
    # The original sinusoidal position table is fixed by Marian's architecture.
    # Every normally trainable model parameter is updated; this is not LoRA.
    model.to(device)
    frozen = [name for name, parameter in model.named_parameters() if not parameter.requires_grad]
    if any(not name.endswith("embed_positions.weight") for name in frozen):
        raise ValueError("Unexpected frozen model weights; this run requires full fine-tuning")
    return model


def tensor_fingerprints(model):
    hashes, samples, count, trainable = {}, {}, 0, 0
    digest = hashlib.sha256()
    for name, parameter in model.named_parameters():
        value = parameter.detach().float().cpu().contiguous()
        hashed = hashlib.sha256(value.numpy().tobytes()).hexdigest()
        hashes[name] = hashed
        samples[name] = value.reshape(-1)[:64].tolist()
        digest.update((name + ":" + hashed + "\n").encode())
        count += parameter.numel()
        trainable += parameter.numel() if parameter.requires_grad else 0
    return {"logicalFP32Sha256": digest.hexdigest(), "tensorHashes": hashes, "first64ValueSamples": samples,
            "parameterCount": count, "trainableParameterCount": trainable,
            "frozenParameterNames": [name for name, parameter in model.named_parameters() if not parameter.requires_grad]}


def compare_fingerprints(before, after):
    if set(before["tensorHashes"]) != set(after["tensorHashes"]):
        raise ValueError("Model parameter structure changed")
    changed = [name for name, value in before["tensorHashes"].items() if value != after["tensorHashes"][name]]
    deltas = [abs(left - right) for name in before["first64ValueSamples"]
              for left, right in zip(before["first64ValueSamples"][name], after["first64ValueSamples"][name])]
    return {"beforeLogicalFP32Sha256": before["logicalFP32Sha256"], "afterLogicalFP32Sha256": after["logicalFP32Sha256"],
            "changedTensorCount": len(changed), "totalTensorCount": len(before["tensorHashes"]),
            "changedTensorNames": changed, "sampledScalarCount": len(deltas),
            "sampledChangedScalarCount": sum(delta > 0 for delta in deltas),
            "sampledMaxAbsDelta": max(deltas, default=0.0), "sampledMeanAbsDelta": sum(deltas) / max(1, len(deltas))}


def encode_rows(tokenizer, rows, max_length):
    encoded = []
    for row in rows:
        source = tokenizer(row["source"], truncation=False)["input_ids"]
        target = tokenizer(text_target=row["target"], truncation=False)["input_ids"]
        if max(len(source), len(target)) > max_length:
            raise ValueError(f"Row {row['id']} exceeds max-length; data is never silently truncated")
        encoded.append({"input_ids": source, "labels": target})
    return encoded


def batch_items(tokenizer, encoded, torch, device):
    result = tokenizer.pad([{"input_ids": item["input_ids"]} for item in encoded], padding=True, return_tensors="pt")
    width = max(len(item["labels"]) for item in encoded)
    result["labels"] = torch.tensor([item["labels"] + [-100] * (width - len(item["labels"])) for item in encoded])
    return {key: value.to(device) for key, value in result.items()}


def autocast(torch, args, device):
    return torch.autocast(device_type=device, dtype=torch.bfloat16) if args.precision == "bf16" and device == "xpu" else contextlib.nullcontext()


def optimizer_update(model, optimizer, encoded, indices, tokenizer, torch, device, args, learning_rate):
    model.train()
    optimizer.zero_grad(set_to_none=True)
    for group in optimizer.param_groups:
        group["lr"] = learning_rate
    total_loss, count = 0.0, 0
    chunks = [indices[i:i + args.batch_size] for i in range(0, len(indices), args.batch_size)]
    synchronize(torch, device)
    started = time.monotonic()
    for chunk in chunks:
        batch = batch_items(tokenizer, [encoded[index] for index in chunk], torch, device)
        with autocast(torch, args, device):
            loss = model(**batch).loss
        if not bool(torch.isfinite(loss)):
            raise FloatingPointError("Nonfinite training loss")
        (loss / len(chunks)).backward()
        total_loss += float(loss.detach())
        count += 1
    norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True, foreach=False)
    optimizer.step()
    synchronize(torch, device)
    return {"loss": total_loss / count, "gradientNorm": float(norm), "seconds": time.monotonic() - started,
            "examples": len(indices), "learningRate": learning_rate}


def token_loss(model, tokenizer, encoded, torch, device, args):
    model.eval()
    total, tokens = 0.0, 0
    with torch.inference_mode():
        for item in encoded:
            batch = batch_items(tokenizer, [item], torch, device)
            with autocast(torch, args, device):
                loss = float(model(**batch).loss)
            if not math.isfinite(loss):
                raise FloatingPointError("Nonfinite evaluation loss")
            total += loss * len(item["labels"])
            tokens += len(item["labels"])
    return total / max(1, tokens)


def generation_protocol(args, device):
    return {"device": device, "effectivePrecision": "bf16" if device == "xpu" and args.precision == "bf16" else "fp32",
            "weightDtype": "fp32", "attentionImplementation": "eager", "doSample": False,
            "beams": args.beams, "maxNewTokens": args.max_new_tokens, "maxInputTokens": args.max_length,
            "runtimeVersions": {name: importlib.metadata.version(name) for name in ("torch", "transformers", "sentencepiece")}}


def predict(model, tokenizer, rows, torch, device, args, cache_path, model_identity):
    protocol = generation_protocol(args, device)
    cached = []
    if cache_path.exists():
        cached = [json.loads(line) for line in cache_path.read_text("utf-8").splitlines() if line.strip()]
    if len(cached) > len(rows):
        raise ValueError("Prediction cache has extra rows")
    for index, item in enumerate(cached):
        expected = hashlib.sha256(rows[index]["source"].encode()).hexdigest()
        if (item["index"] != index or item["sourceHash"] != expected or item["modelIdentity"] != model_identity
                or item.get("generationProtocol") != protocol):
            raise ValueError("Prediction cache identity mismatch")
    model.eval()
    with torch.inference_mode():
        for index in range(len(cached), len(rows)):
            if STOP_REQUESTED:
                raise StopRequested()
            row = rows[index]
            inputs = tokenizer(row["source"], return_tensors="pt", truncation=False)
            if inputs["input_ids"].shape[1] > args.max_length:
                raise ValueError("Evaluation input exceeds the preregistered length limit")
            with autocast(torch, args, device):
                output = model.generate(**{key: value.to(device) for key, value in inputs.items()},
                                        max_new_tokens=args.max_new_tokens, num_beams=args.beams,
                                        do_sample=False, use_cache=True)
            ids = output[0].detach().cpu().tolist()
            item = {"index": index, "id": row["id"], "sourceHash": hashlib.sha256(row["source"].encode()).hexdigest(),
                    "modelIdentity": model_identity, "generationProtocol": protocol, "prediction": tokenizer.decode(ids, skip_special_tokens=True),
                    "generatedTokens": max(0, len(ids) - 1), "atLengthLimit": len(ids) - 1 >= args.max_new_tokens}
            append_json(cache_path, item)
            cached.append(item)
            if (index + 1) % 10 == 0 or index + 1 == len(rows):
                emit("generation-progress", file=relative(cache_path), completed=index + 1, total=len(rows))
    return cached


def metrics(rows, predictions, include_domains=True):
    from sacrebleu.metrics import BLEU, CHRF
    outputs = [item["prediction"] for item in predictions]
    targets = [row["target"] for row in rows]
    chrf, bleu = CHRF(), BLEU(tokenize="none", effective_order=True)
    chrf_score, bleu_score = chrf.corpus_score(outputs, [targets]).score, bleu.corpus_score(outputs, [targets]).score
    term_count = term_hits = numeric_hits = numeric_rows = numeric_row_hits = 0
    details = []
    for row, prediction in zip(rows, predictions):
        output = prediction["prediction"]
        terms = term_targets(row)
        hits = [term for term in terms if normalized_term(term) in normalized_term(output)]
        preserved = numeric_tokens(row["source"]) == numeric_tokens(output)
        term_count += len(terms)
        term_hits += len(hits)
        numeric_hits += preserved
        if numeric_tokens(row["source"]):
            numeric_rows += 1
            numeric_row_hits += preserved
        details.append({"id": row["id"], "expectedTerms": terms, "matchedTerms": hits, "numbersPreserved": preserved})
    protocols = [item.get("generationProtocol") for item in predictions]
    if any(item != protocols[0] for item in protocols):
        raise ValueError("Mixed inference protocols are forbidden in one evaluation")
    result = {"count": len(rows), "generationProtocol": protocols[0], "chrF": chrf_score, "bleu": bleu_score,
            "chrFSignature": str(chrf.get_signature()), "bleuSignature": str(bleu.get_signature()),
            "termAccuracy": term_hits / term_count if term_count else None, "termHits": term_hits, "termCount": term_count,
            "termMatcher": "literal canonical target after Unicode normalization and removal of whitespace/punctuation; this is terminology coverage, not semantic accuracy",
            "numericPreservation": numeric_hits / len(rows), "numericRowCount": numeric_rows,
            "numericRowsPreserved": numeric_row_hits,
            "numericMatcher": "multiset of explicit decimal digits, signs and percentages; numeric words, currency and unit semantics are not scored",
            "emptyOutputs": sum(not text.strip() for text in outputs),
            "cappedOutputs": sum(item["atLengthLimit"] for item in predictions), "details": details}
    if include_domains:
        result["byDomain"] = {}
        for domain in sorted({row["domain"] for row in rows}):
            indices = [index for index, row in enumerate(rows) if row["domain"] == domain]
            result["byDomain"][domain] = metrics([rows[index] for index in indices], [predictions[index] for index in indices], False)
    return result


def gate(base, candidate, domain=None):
    reasons = []
    if base.get("generationProtocol") != candidate.get("generationProtocol"):
        reasons.append("inference-protocol-mismatch")
    base_term, candidate_term = base["termAccuracy"], candidate["termAccuracy"]
    if domain == "general":
        pass
    elif base_term is None or candidate_term is None:
        reasons.append("missing-term-evaluation")
    else:
        required = base_term if base_term >= GATE["termCeilingException"] else min(1.0, base_term + GATE["minTermAccuracyGain"])
        if candidate_term + 1e-12 < required:
            reasons.append("term-gain-below-threshold")
    policy = GATE["generalRetention"] if domain == "general" else GATE
    if candidate["chrF"] + policy["maxChrFRegression"] < base["chrF"]:
        reasons.append("chrf-regression")
    if candidate["bleu"] + policy["maxBleuRegression"] < base["bleu"]:
        reasons.append("bleu-regression")
    if candidate["numericPreservation"] + 1e-12 < base["numericPreservation"]:
        reasons.append("numeric-regression")
    if candidate["emptyOutputs"] or candidate["cappedOutputs"]:
        reasons.append("empty-or-capped-output")
    domains = {}
    if domain is None:
        for name in ("finance", "general"):
            if name not in base.get("byDomain", {}) or name not in candidate.get("byDomain", {}):
                reasons.append(name + "-evaluation-missing")
            else:
                domains[name] = gate(base["byDomain"][name], candidate["byDomain"][name], name)
                if not domains[name]["passed"]:
                    reasons.append(name + "-gate-failed")
    return {"passed": not reasons, "reasons": reasons, **({"byDomain": domains} if domains else {})}


def checkpoint(model, tokenizer, optimizer, torch, device, run_dir, step, cursor, manifest):
    destination = run_dir / "checkpoints" / f"step-{step:06d}"
    if (destination / "checkpoint.json").exists():
        manifest["latestCheckpoint"] = relative(destination)
        write_json(run_dir / "manifest.json", manifest)
        return destination
    destination.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(destination / "model", safe_serialization=True)
    tokenizer.save_pretrained(destination / "model")
    state = {"optimizer": optimizer.state_dict(), "step": step, "cursor": cursor,
             "torch_rng": torch.get_rng_state(), "python_rng": random.getstate()}
    if device == "xpu":
        state["xpu_rng"] = torch.xpu.get_rng_state_all()
    torch.save(state, destination / "state.pt")
    write_json(destination / "checkpoint.json", {"step": step, "cursor": cursor,
               "stateSha256": sha256(destination / "state.pt"), "modelSha256": sha256(destination / "model/model.safetensors"),
               "createdAt": now(), "resume": "locally generated tensor/optimizer state loaded with weights_only=True"})
    manifest["latestCheckpoint"] = relative(destination)
    manifest["completedUpdates"] = step
    write_json(run_dir / "manifest.json", manifest)
    emit("checkpoint", step=step, path=relative(destination))
    return destination


def setup(args):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.run_id):
        raise ValueError("Invalid run ID")
    run_dir = WORK_ROOT / "runs" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    rows, data = validate_data(args, run_dir)
    base_path = local_path(args.base_model, WORK_ROOT)
    base_files = {path.name: sha256(path) for path in sorted(base_path.iterdir()) if path.is_file()}
    if base_files.get("model.safetensors") != BASE_WEIGHT_HASH:
        raise ValueError("Base weights do not match the pinned official release")
    config = {name: getattr(args, name) for name in ("seed", "threads", "precision", "epochs", "batch_size", "accumulation", "learning_rate", "warmup_updates", "max_length", "max_new_tokens", "beams", "checkpoint_every", "max_updates")}
    identity = {"scriptVersion": SCRIPT_VERSION, "scriptSha256": sha256(Path(__file__)), "baseModel": BASE_ID, "baseRevision": BASE_REVISION,
                "basePath": relative(base_path), "baseFiles": base_files, "datasets": data["files"], "config": config, "gate": GATE}
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text("utf-8"))
        if any(manifest.get(key) != value for key, value in identity.items()):
            raise ValueError("Run identity/config/data changed; use a new run ID before final test")
    else:
        manifest = {"schemaVersion": 1, "runId": args.run_id, "createdAt": now(), "status": "prepared", **identity}
        write_json(manifest_path, manifest)
    return run_dir, base_path, rows, manifest


def test_ledger(manifest):
    return WORK_ROOT / "test-evaluations" / (manifest["datasets"]["test"]["sha256"] + ".json")


def benchmark(args, run_dir, base_path, rows, manifest):
    if test_ledger(manifest).exists():
        raise ValueError("Final test has been consumed; further training/bench on this experiment is prohibited")
    if (run_dir / "benchmark.json").exists():
        emit("benchmark-existing", **json.loads((run_dir / "benchmark.json").read_text("utf-8")))
        return
    torch, model_class, tokenizer_class, device, versions = stack(args)
    tokenizer = tokenizer_class.from_pretrained(base_path, local_files_only=True)
    encoded = encode_rows(tokenizer, rows["train"], args.max_length)
    encode_rows(tokenizer, rows["dev"], args.max_length)
    model = load_model(model_class, base_path, torch, device)
    before = tensor_fingerprints(model)
    optimizer = torch.optim.AdamW([parameter for parameter in model.parameters() if parameter.requires_grad], lr=args.learning_rate, weight_decay=0.01, foreach=False)
    indices = list(range(min(len(encoded), args.batch_size * args.accumulation)))
    try:
        measurements = [optimizer_update(model, optimizer, encoded, indices, tokenizer, torch, device, args, args.learning_rate) for _ in range(2)]
    except Exception as error:
        if args.device != "auto" or device != "xpu":
            raise
        emit("benchmark-device-fallback", selected="cpu", failureType=type(error).__name__)
        del optimizer, model
        gc.collect()
        torch.xpu.empty_cache()
        device = "cpu"
        model = load_model(model_class, base_path, torch, device)
        optimizer = torch.optim.AdamW([parameter for parameter in model.parameters() if parameter.requires_grad], lr=args.learning_rate, weight_decay=0.01, foreach=False)
        measurements = [optimizer_update(model, optimizer, encoded, indices, tokenizer, torch, device, args, args.learning_rate) for _ in range(2)]
    after = tensor_fingerprints(model)
    evidence = compare_fingerprints(before, after)
    if not evidence["changedTensorCount"]:
        raise RuntimeError("Benchmark optimizer did not change any model weight")
    updates = math.ceil(len(encoded) / (args.batch_size * args.accumulation)) * args.epochs
    if args.max_updates:
        updates = min(updates, args.max_updates)
    result = {"optimizerUpdates": 2, "steps": measurements, "meanStepSeconds": sum(item["seconds"] for item in measurements) / 2}
    result.update({"device": device, "precision": args.precision, "trainableParameters": before["trainableParameterCount"],
                   "totalParameters": before["parameterCount"], "plannedUpdates": updates,
                   "estimatedTrainingSecondsExcludingEvaluationAndIO": result["meanStepSeconds"] * updates,
                   "estimateCaveat": "Two actual optimizer updates; sentence lengths, warm-up, evaluation and checkpoint IO can change total duration. No dev/test generation in benchmark.",
                   "weightEvidence": evidence, "discardedAfterBenchmark": True})
    write_json(run_dir / "benchmark.json", result)
    manifest.update({"status": "benchmarked", "benchmarkDevice": device, "runtimeVersions": versions})
    write_json(run_dir / "manifest.json", manifest)
    emit("benchmark-complete", **{key: value for key, value in result.items() if key != "weightEvidence"})


def train(args, run_dir, base_path, rows, manifest):
    if test_ledger(manifest).exists():
        raise ValueError("Final test has been consumed; no further training is allowed for this test set")
    if not (run_dir / "benchmark.json").exists():
        raise ValueError("Run the bench stage and inspect its measured duration before training")
    if manifest.get("status") == "trained":
        emit("training-already-complete", summary=relative(run_dir / "training-summary.json"))
        return
    if manifest.get("latestCheckpoint") and not args.resume:
        raise ValueError("An existing checkpoint requires --resume")
    if args.device == "auto":
        args.device = manifest["benchmarkDevice"]
    torch, model_class, tokenizer_class, device, versions = stack(args)
    tokenizer = tokenizer_class.from_pretrained(base_path, local_files_only=True)
    encoded = encode_rows(tokenizer, rows["train"], args.max_length)
    dev_encoded = encode_rows(tokenizer, rows["dev"], args.max_length)
    latest = local_path(manifest["latestCheckpoint"], run_dir) if args.resume and manifest.get("latestCheckpoint") else None
    model = load_model(model_class, latest / "model" if latest else base_path, torch, device)
    optimizer = torch.optim.AdamW([parameter for parameter in model.parameters() if parameter.requires_grad], lr=args.learning_rate, weight_decay=0.01, foreach=False)
    cursor, step = {"epoch": 0, "window": 0}, 0
    if latest:
        metadata = json.loads((latest / "checkpoint.json").read_text("utf-8"))
        if sha256(latest / "state.pt") != metadata["stateSha256"] or sha256(latest / "model/model.safetensors") != metadata["modelSha256"]:
            raise ValueError("Resume checkpoint integrity failed")
        state = torch.load(latest / "state.pt", map_location="cpu", weights_only=True)
        optimizer.load_state_dict(state["optimizer"])
        torch.set_rng_state(state["torch_rng"])
        random.setstate(state["python_rng"])
        if device == "xpu" and "xpu_rng" in state:
            torch.xpu.set_rng_state_all(state["xpu_rng"])
        cursor, step = state["cursor"], state["step"]
        del state
    before_path = run_dir / "weights-before.json"
    if not before_path.exists():
        if latest:
            raise ValueError("Initial weight evidence is missing")
        write_json(before_path, tensor_fingerprints(model))
    before = json.loads(before_path.read_text("utf-8"))
    manifest.update({"status": "training", "trainingDevice": device, "runtimeVersions": versions, "startedAt": manifest.get("startedAt", now())})
    write_json(run_dir / "manifest.json", manifest)
    effective_batch = args.batch_size * args.accumulation
    total_updates = math.ceil(len(encoded) / effective_batch) * args.epochs
    if args.max_updates:
        total_updates = min(total_updates, args.max_updates)
    history = manifest.get("devHistory", [])
    baseline_path = run_dir / "baseline-dev-metrics.json"
    def record_development(current):
        nonlocal history
        identity = json.loads((current / "checkpoint.json").read_text("utf-8"))["modelSha256"]
        predictions = predict(model, tokenizer, rows["dev"], torch, device, args, current / "dev-predictions.jsonl", identity)
        scores = metrics(rows["dev"], predictions)
        scores["tokenLoss"] = token_loss(model, tokenizer, dev_encoded, torch, device, args)
        write_json(current / "dev-metrics.json", scores)
        record = {"step": step, "checkpoint": relative(current), "metrics": scores, "gate": gate(baseline, scores)}
        history = [item for item in history if item["step"] != step] + [record]
        manifest["devHistory"] = history
        write_json(run_dir / "manifest.json", manifest)
        emit("dev-evaluation", step=step, chrF=scores["chrF"], bleu=scores["bleu"], termAccuracy=scores["termAccuracy"], numericPreservation=scores["numericPreservation"], gate=record["gate"])
    try:
        if not baseline_path.exists():
            if latest and step:
                raise ValueError("Original baseline is missing for a resumed run")
            predictions = predict(model, tokenizer, rows["dev"], torch, device, args, run_dir / "baseline-dev.jsonl", BASE_WEIGHT_HASH)
            baseline = metrics(rows["dev"], predictions)
            baseline["tokenLoss"] = token_loss(model, tokenizer, dev_encoded, torch, device, args)
            write_json(baseline_path, baseline)
        baseline = json.loads(baseline_path.read_text("utf-8"))
        if baseline.get("generationProtocol") != generation_protocol(args, device):
            raise ValueError("Use the original evaluation device/precision/runtime for this run; mixed baseline comparisons are forbidden")
        # The last optimizer update can be safely saved before dev generation.
        # On resume, finish that epoch's pending dev candidate before training
        # the next epoch or selecting the final model.
        if latest and step and (cursor["window"] == 0 or step >= total_updates) and not any(item["step"] == step for item in history):
            record_development(latest)
        for epoch in range(cursor["epoch"], args.epochs):
            if step >= total_updates:
                break
            indices = list(range(len(encoded)))
            random.Random(args.seed + epoch).shuffle(indices)
            windows = [indices[i:i + effective_batch] for i in range(0, len(indices), effective_batch)]
            offset = cursor["window"] if cursor["epoch"] == epoch else 0
            for window_index in range(offset, len(windows)):
                if STOP_REQUESTED:
                    raise StopRequested()
                if step >= total_updates:
                    break
                warmup = min(1.0, (step + 1) / max(1, args.warmup_updates))
                decay = max(0.1, 1 - max(0, step - args.warmup_updates) / max(1, total_updates - args.warmup_updates))
                result = optimizer_update(model, optimizer, encoded, windows[window_index], tokenizer, torch, device, args, args.learning_rate * warmup * decay)
                step += 1
                cursor = {"epoch": epoch + 1, "window": 0} if window_index + 1 == len(windows) else {"epoch": epoch, "window": window_index + 1}
                append_json(run_dir / "training-log.jsonl", {"step": step, "epoch": epoch + 1, "time": now(), **result})
                emit("training-step", step=step, total=total_updates, epoch=epoch + 1, **result)
                if step % args.checkpoint_every == 0:
                    checkpoint(model, tokenizer, optimizer, torch, device, run_dir, step, cursor, manifest)
            current = checkpoint(model, tokenizer, optimizer, torch, device, run_dir, step, cursor, manifest)
            record_development(current)
            if step >= total_updates:
                break
        if not history:
            raise RuntimeError("No trained checkpoint was evaluated on development data")
        selected = max(history, key=lambda item: (item["gate"]["passed"], item["metrics"]["chrF"], item["metrics"]["termAccuracy"] or 0, -item["metrics"]["tokenLoss"]))
        selected_path = local_path(selected["checkpoint"], run_dir) / "model"
        del optimizer, model
        gc.collect()
        if device == "xpu":
            torch.xpu.empty_cache()
        selected_model = load_model(model_class, selected_path, torch, "cpu")
        after = tensor_fingerprints(selected_model)
        evidence = compare_fingerprints(before, after)
        write_json(run_dir / "weights-after.json", after)
        if not evidence["changedTensorCount"] or not step:
            raise RuntimeError("No actual model weight update was demonstrated")
        output = run_dir / "model"
        expected_files = {relative_path.relative_to(selected_path).as_posix(): sha256(relative_path)
                          for relative_path in selected_path.rglob("*") if relative_path.is_file()}
        staging = run_dir / "model-staging"
        if not output.exists():
            # A interrupted copy can be completed idempotently; the public final
            # directory only appears after every selected checkpoint file agrees.
            shutil.copytree(selected_path, staging, dirs_exist_ok=True)
            actual_files = {path.relative_to(staging).as_posix(): sha256(path) for path in staging.rglob("*") if path.is_file()}
            if actual_files != expected_files:
                raise ValueError("Staged model inventory differs from the selected checkpoint")
            if not staging.resolve().is_relative_to(run_dir.resolve()) or not output.resolve().is_relative_to(run_dir.resolve()):
                raise ValueError("Final model paths escaped the run directory")
            os.replace(staging, output)
        elif {path.relative_to(output).as_posix(): sha256(path) for path in output.rglob("*") if path.is_file()} != expected_files:
            raise ValueError("Existing final model differs from the selected checkpoint")
        summary = {"runId": args.run_id, "finishedAt": now(), "completedUpdates": step, "selectedStep": selected["step"],
                   "baseWeightFileSha256": BASE_WEIGHT_HASH, "trainedWeightFileSha256": sha256(output / "model.safetensors"),
                   "weightEvidence": evidence, "trainableParameters": before["trainableParameterCount"],
                   "fullFineTuning": True, "glossaryApplied": False, "baselineDev": baseline, "selectedDev": selected["metrics"],
                   "devGate": selected["gate"], "testEvaluated": False, "modelPath": relative(output)}
        write_json(run_dir / "training-summary.json", summary)
        manifest.update({"status": "trained", "completedUpdates": step, "selectedCheckpoint": selected["checkpoint"], "modelPath": relative(output)})
        write_json(run_dir / "manifest.json", manifest)
        emit("training-complete", updates=step, selectedStep=selected["step"], changedTensors=evidence["changedTensorCount"], devGate=selected["gate"], modelPath=relative(output))
    except (StopRequested, KeyboardInterrupt):
        optimizer.zero_grad(set_to_none=True)
        checkpoint(model, tokenizer, optimizer, torch, device, run_dir, step, cursor, manifest)
        manifest["status"] = "paused"
        write_json(run_dir / "manifest.json", manifest)
        emit("training-paused", step=step, resume="train --resume with the same run configuration")
    except Exception:
        # A device error can occur during an optimizer update. Reuse only the
        # previously completed checkpoint; never save a possibly partial update.
        manifest["status"] = "failed"
        write_json(run_dir / "manifest.json", manifest)
        emit("training-failed", lastSafeCheckpoint=manifest.get("latestCheckpoint"), cpuRecovery="Use train --resume --device cpu if a safe checkpoint is present")
        raise


def evaluate(args, run_dir, base_path, rows, manifest):
    summary_path = run_dir / "evaluation-summary.json"
    if summary_path.exists():
        emit("evaluation-existing", summary=relative(summary_path))
        return
    if manifest.get("status") not in {"trained", "evaluating", "evaluation-paused"}:
        raise ValueError("Complete training and dev checkpoint selection before evaluating test")
    training = json.loads((run_dir / "training-summary.json").read_text("utf-8"))
    model_path = local_path(training["modelPath"], run_dir)
    if sha256(model_path / "model.safetensors") != training["trainedWeightFileSha256"]:
        raise ValueError("Selected trained model changed before final evaluation")
    ledger_path = test_ledger(manifest)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    expected = {"runId": args.run_id, "testDataSha256": manifest["datasets"]["test"]["sha256"], "modelSha256": training["trainedWeightFileSha256"]}
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text("utf-8"))
        if any(ledger.get(key) != value for key, value in expected.items()):
            raise ValueError("This test set has already been consumed by another model/run")
    else:
        with ledger_path.open("x", encoding="utf-8") as stream:
            json.dump({**expected, "startedAt": now(), "status": "evaluating"}, stream)
    manifest["status"] = "evaluating"
    write_json(run_dir / "manifest.json", manifest)
    torch, model_class, tokenizer_class, device, _versions = stack(args)
    tokenizer = tokenizer_class.from_pretrained(base_path, local_files_only=True)
    encode_rows(tokenizer, rows["test"], args.max_length)
    evaluated = {}
    try:
        for name, path, identity in [("baseline", base_path, BASE_WEIGHT_HASH), ("finetuned", model_path, training["trainedWeightFileSha256"])]:
            model = load_model(model_class, path, torch, device)
            predictions = predict(model, tokenizer, rows["test"], torch, device, args, run_dir / f"{name}-test.jsonl", identity)
            evaluated[name] = metrics(rows["test"], predictions)
            del model
            gc.collect()
            if device == "xpu":
                torch.xpu.empty_cache()
        test_gate = gate(evaluated["baseline"], evaluated["finetuned"])
        eligible = training["devGate"]["passed"] and test_gate["passed"] and training["weightEvidence"]["changedTensorCount"] > 0
        result = {"runId": args.run_id, "evaluatedAt": now(), "glossaryApplied": False, "modelPath": relative(model_path),
                  "trainedWeightFileSha256": training["trainedWeightFileSha256"], "baseWeightFileSha256": BASE_WEIGHT_HASH,
                  "testDataSha256": manifest["datasets"]["test"]["sha256"], "baseline": evaluated["baseline"],
                  "finetuned": evaluated["finetuned"], "devGate": training["devGate"], "testGate": test_gate,
                  "promotionEligible": eligible, "gatePolicy": GATE,
                  "limitation": "One small authored held-out set; semantic correctness and broad document quality still require review."}
        write_json(summary_path, result)
        write_json(ledger_path, {**expected, "completedAt": now(), "status": "complete", "summaryPath": relative(summary_path)})
        manifest["status"] = "evaluated"
        write_json(run_dir / "manifest.json", manifest)
        emit("evaluation-complete", promotionEligible=eligible, devGate=training["devGate"], testGate=test_gate, summary=relative(summary_path))
    except (StopRequested, KeyboardInterrupt):
        manifest["status"] = "evaluation-paused"
        write_json(run_dir / "manifest.json", manifest)
        emit("evaluation-paused", resume="evaluate resumes cached predictions without regenerating completed test rows")


def export(args, run_dir, _base_path, _rows, _manifest):
    summary = json.loads((run_dir / "evaluation-summary.json").read_text("utf-8"))
    if not summary["promotionEligible"]:
        raise ValueError("Model did not pass the preregistered gate; deployment export is disabled")
    network_off()
    from ctranslate2.converters import TransformersConverter
    target = run_dir / "ctranslate2"
    if target.exists():
        raise ValueError("Export directory already exists")
    model_path = local_path(summary["modelPath"], run_dir)
    if sha256(model_path / "model.safetensors") != summary["trainedWeightFileSha256"]:
        raise ValueError("Model weights changed after the sealed final evaluation")
    converter = TransformersConverter(str(model_path), copy_files=["source.spm", "target.spm", "vocab.json", "tokenizer_config.json", "special_tokens_map.json", "generation_config.json"])
    converter.convert(str(target), quantization="int8")
    files = {path.name: sha256(path) for path in target.iterdir() if path.is_file()}
    write_json(run_dir / "export-manifest.json", {"format": "ctranslate2", "quantization": "int8", "files": files,
               "sourceModelSha256": sha256(model_path / "model.safetensors"), "needsIndependentInferenceParityCheck": True})
    emit("export-complete", path=relative(target), needsInferenceParityCheck=True)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["validate", "bench", "train", "evaluate", "export"])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base-model", default=".training/base-model")
    parser.add_argument("--train-data", default="content/training/train.jsonl")
    parser.add_argument("--dev-data", default="content/training/dev.jsonl")
    parser.add_argument("--test-data", default="content/training/evaluation.jsonl")
    parser.add_argument("--device", choices=["auto", "cpu", "xpu"], default="auto")
    parser.add_argument("--precision", choices=["fp32", "bf16"], default="fp32")
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--accumulation", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--warmup-updates", type=int, default=5)
    parser.add_argument("--max-length", type=int, default=192)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--beams", type=int, default=4)
    parser.add_argument("--checkpoint-every", type=int, default=50)
    parser.add_argument("--max-updates", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not 5e-6 <= args.learning_rate <= 2e-5:
        parser.error("learning-rate must be between 5e-6 and 2e-5")
    if any(getattr(args, key) < 1 for key in ("threads", "epochs", "batch_size", "accumulation", "max_length", "max_new_tokens", "beams", "checkpoint_every")) or args.max_updates < 0 or args.warmup_updates < 0:
        parser.error("invalid positive training limit")
    def stop(_signum, _frame):
        global STOP_REQUESTED
        STOP_REQUESTED = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    with exclusive_run(args.run_id):
        values = setup(args)
        if args.stage == "validate":
            emit("validation-complete", path=relative(values[0] / "data-validation.json"))
        else:
            {"bench": benchmark, "train": train, "evaluate": evaluate, "export": export}[args.stage](args, *values)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        emit("failed", errorType=type(error).__name__, message=str(error)[:500])
        sys.exit(1)
