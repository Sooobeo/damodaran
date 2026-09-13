"""Run existing local translation models on a new, explicitly supplied probe.

Use .venv-translation for --model argos and .venv-training for --model marian.
Only .training/comparisons receives results; existing runs and app data are read
only. Different runtimes make this a practical candidate comparison, not an
isolated estimate of fine-tuning effectiveness. No held-out data is opened.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

sys.dont_write_bytecode = True
APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_ROOT / "scripts" / "model-training"))
from train import network_off, numeric_tokens, sha256  # noqa: E402


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def relative(path):
    return path.resolve().relative_to(APP_ROOT).as_posix()


def versions(names):
    return {"python": platform.python_version(), "platform": platform.platform(),
            **{name: importlib.metadata.version(name) for name in names}}


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    os.replace(temporary, path)


def read_probe(path):
    if not path.is_relative_to(APP_ROOT / "content" / "model-comparison"):
        raise ValueError("Probe must be a new file under content/model-comparison")
    rows, identifiers = [], set()
    for line in path.read_text("utf-8-sig").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not row["id"] or row["id"] in identifiers
                or not isinstance(row.get("source"), str) or not row["source"].strip()
                or len(row["source"]) > 12000):
            raise ValueError("Invalid or duplicate probe row")
        identifiers.add(row["id"])
        # References, if present, are never used by the model or generator.
        rows.append({"id": row["id"], "source": row["source"], "domain": row.get("domain")})
    if not rows:
        raise ValueError("Empty probe")
    return rows


def argos_model(_args):
    sys.path.insert(0, str(APP_ROOT / "scripts" / "local-translation"))
    from runtime import LocalTranslator, verify_manifest
    started = time.monotonic()
    manifest = verify_manifest()
    integrity_seconds = time.monotonic() - started
    started = time.monotonic()
    local = LocalTranslator(manifest)
    load_seconds = time.monotonic() - started
    original = local.translator.translator

    class Capture:
        def __init__(self):
            self.inputs, self.outputs = [], []

        def translate_batch(self, tokenized, **kwargs):
            self.inputs.extend(len(tokens) for tokens in tokenized)
            results = original.translate_batch(tokenized, **kwargs)
            self.outputs.extend(len(result.hypotheses[0]) for result in results)
            return results

    capture = Capture()
    local.translator.translator = capture

    def generate(text):
        capture.inputs.clear()
        capture.outputs.clear()
        translation = local.translate_plain(text)
        return {"translation": translation, "inputTokens": sum(capture.inputs),
                "generatedTokens": sum(capture.outputs),
                "sentenceInputTokens": list(capture.inputs),
                "sentenceGeneratedTokens": list(capture.outputs),
                "outputLimitReached": any(count >= 1024 for count in capture.outputs)}

    identity = {"name": manifest["model"], "modelSha256": manifest["modelHash"],
                "modelHashKind": "sha256-of-canonical-file-inventory",
                "modelPath": manifest["modelPath"], "files": manifest["modelFiles"],
                "manifestSha256": sha256(APP_ROOT / ".translation" / "manifest.json"),
                "appRuntimeIdentity": manifest["runtimeVersion"]}
    settings = {"device": "cpu", "precision": "int8", "beamSize": 4,
                "lengthPenalty": 0.2, "numHypotheses": 1, "replaceUnknowns": True,
                "interThreads": 1, "intraThreads": 4, "maxSentenceInputTokens": 400,
                "maxDecodingTokensPerSentence": 1024, "nativeInputTruncation": False,
                "segmentation": "existing local spaCy sentence splitter; 400-token chunks",
                "glossaryApplied": False, "translationMemoryApplied": False,
                "appNumberFormulaProtectionApplied": False}
    return generate, identity, settings, versions(["argostranslate", "ctranslate2", "sentencepiece", "spacy"]), integrity_seconds, load_seconds


def marian_model(args):
    from infer import verified_model
    started = time.monotonic()
    verified = verified_model(args.run_id)
    integrity_seconds = time.monotonic() - started
    started = time.monotonic()
    import torch
    from transformers import MarianMTModel, MarianTokenizer
    torch.set_num_threads(args.threads)
    torch.manual_seed(20260909)
    path = verified["path"]
    tokenizer = MarianTokenizer(source_spm=str(path / "source.spm"), target_spm=str(path / "target.spm"),
                                vocab=str(path / "vocab.json"), source_lang="en", target_lang="ko",
                                target_vocab_file=str(path / "target_vocab.json"),
                                unk_token="<unk>", eos_token="</s>", pad_token="<pad>",
                                separate_vocabs=True, model_max_length=512)
    config = json.loads((path / "config.json").read_text("utf-8"))
    if (config.get("model_type") != "marian" or tokenizer.pad_token_id != config.get("pad_token_id")
            or tokenizer.eos_token_id != config.get("eos_token_id")):
        raise ValueError("Model and tokenizer special tokens differ")
    run_settings = verified["manifest"]["config"]
    model = MarianMTModel.from_pretrained(str(path), local_files_only=True, use_safetensors=True,
                                          torch_dtype=torch.float32, attn_implementation="eager")
    model.to("cpu").eval()
    load_seconds = time.monotonic() - started

    def generate(text):
        inputs = tokenizer(text, return_tensors="pt", truncation=False)
        input_tokens = int(inputs["input_ids"].shape[1])
        if input_tokens > run_settings["max_length"]:
            raise ValueError("INPUT_EXCEEDS_FIXED_MODEL_LIMIT")
        with torch.inference_mode():
            ids = model.generate(**inputs, num_beams=run_settings["beams"],
                                 max_new_tokens=run_settings["max_new_tokens"],
                                 do_sample=False, use_cache=True)[0].cpu().tolist()
        generated_tokens = max(0, len(ids) - 1)
        return {"translation": tokenizer.decode(ids, skip_special_tokens=True),
                "inputTokens": input_tokens, "generatedTokens": generated_tokens,
                "outputLimitReached": generated_tokens >= run_settings["max_new_tokens"]}

    identity = {"name": "finance-v3" if args.run_id == "finance-v3" else args.run_id,
                "runId": args.run_id, "baseModel": verified["manifest"]["baseModel"],
                "baseRevision": verified["manifest"]["baseRevision"],
                "modelPath": relative(path), "modelSha256": verified["weightHash"],
                "modelHashKind": "sha256-of-model.safetensors",
                "tokenizerAndConfigHashes": verified["fileHashes"],
                "selectedCheckpoint": verified["manifest"]["selectedCheckpoint"],
                "integrityVerifierSha256": sha256(APP_ROOT / "scripts/model-training/infer.py")}
    settings = {"device": "cpu", "precision": "fp32", "threads": args.threads,
                "beamSize": run_settings["beams"], "maxInputTokens": run_settings["max_length"],
                "maxNewTokens": run_settings["max_new_tokens"], "doSample": False,
                "useCache": True, "attentionImplementation": "eager", "inputTruncation": False,
                "segmentation": "whole probe row", "glossaryApplied": False,
                "translationMemoryApplied": False, "appNumberFormulaProtectionApplied": False,
                "generationConfig": model.generation_config.to_dict()}
    return generate, identity, settings, versions(["torch", "transformers", "sentencepiece", "safetensors"]), integrity_seconds, load_seconds


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=("argos", "marian"), required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-id", default="finance-v3")
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.threads <= 32:
        parser.error("threads must be between 1 and 32")
    input_path = (APP_ROOT / args.input).resolve()
    output_dir = (APP_ROOT / args.output_dir).resolve()
    if not output_dir.is_relative_to(APP_ROOT / ".training/comparisons"):
        parser.error("Output must stay under .training/comparisons")
    rows = read_probe(input_path)
    prefix = "argos-raw" if args.model == "argos" else "marian-" + args.run_id + "-fp32"
    # The run ID also becomes a file component; validate before any file creation.
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.run_id):
        parser.error("Invalid run ID")
    result_path = output_dir / (prefix + ".jsonl")
    summary_path = output_dir / (prefix + "-summary.json")
    if result_path.exists() or summary_path.exists() or summary_path.with_suffix(".json.tmp").exists():
        parser.error("Result already exists; choose a new comparison directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    network_off()
    started_at = utc_now()
    started = time.monotonic()
    summary = {"schemaVersion": 1, "status": "loading", "startedAt": started_at,
               "scriptPath": relative(Path(__file__)), "scriptSha256": sha256(Path(__file__)),
               "inputPath": relative(input_path), "inputSha256": sha256(input_path),
               "inputCount": len(rows), "resultPath": relative(result_path),
               "comparisonPurpose": "practical-model-candidate-probe; not isolated fine-tuning effect",
               "caveats": ["Small assistant-authored unreviewed probe; no statistical quality claim.",
                           "Model runtimes, precision, segmentation and output limits differ.",
                           "Numeric lexical preservation is not semantic, formula or unit validation."],
               "appDeploymentPerformed": False, "paidCalls": 0, "networkAllowed": False,
               "priorHeldOutSourceOrPredictionsRead": False}
    write_json(summary_path, summary)
    try:
        generate, identity, settings, runtime, integrity_seconds, load_seconds = (
            argos_model(args) if args.model == "argos" else marian_model(args))
    except Exception as error:
        summary.update(status="load_failed", errorType=type(error).__name__, finishedAt=utc_now())
        write_json(summary_path, summary)
        raise
    summary.update(status="running", model=identity, generationOptions=settings, runtime=runtime,
                   integritySeconds=round(integrity_seconds, 6), loadSeconds=round(load_seconds, 6))
    write_json(summary_path, summary)
    results = []
    with result_path.open("x", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            record = {**row, "model": prefix, "modelSha256": identity["modelSha256"],
                      "inputSha256": summary["inputSha256"],
                      "sourceSha256": hashlib.sha256(row["source"].encode("utf-8")).hexdigest()}
            tick = time.monotonic()
            try:
                output = generate(row["source"])
                numbers_before = numeric_tokens(row["source"])
                numbers_after = numeric_tokens(output["translation"])
                record.update(**output, status="generated", numbersPreserved=numbers_before == numbers_after,
                              sourceNumericTokens=dict(numbers_before), outputNumericTokens=dict(numbers_after),
                              emptyOutput=not output["translation"].strip())
            except Exception as error:
                code = ("INPUT_EXCEEDS_FIXED_MODEL_LIMIT" if str(error) == "INPUT_EXCEEDS_FIXED_MODEL_LIMIT"
                        else "OUTPUT_LIMIT" if str(error) == "Local translation reached decoder limit"
                        else "GENERATION_FAILED")
                record.update(status="failed", errorCode=code, errorType=type(error).__name__,
                              translation=None, numbersPreserved=False, outputLimitReached=code == "OUTPUT_LIMIT")
            record["generationSeconds"] = round(time.monotonic() - tick, 6)
            results.append(record)
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            print(json.dumps({"model": prefix, "id": row["id"], "status": record["status"],
                              "generationSeconds": record["generationSeconds"]}), flush=True)
    successful = [row for row in results if row["status"] == "generated"]
    durations = [row["generationSeconds"] for row in successful]
    summary.update(status="completed" if len(successful) == len(rows) else "partial",
                   finishedAt=utc_now(), totalSeconds=round(time.monotonic() - started, 6),
                   resultSha256=sha256(result_path), generatedCount=len(successful),
                   failureCount=len(rows) - len(successful),
                   numericPreservedCount=sum(row["numbersPreserved"] for row in successful),
                   numericCheckCount=len(successful),
                   emptyCount=sum(row["emptyOutput"] for row in successful),
                   cappedCount=sum(row["outputLimitReached"] for row in results),
                   generationTotalSeconds=round(sum(durations), 6),
                   generationMedianSeconds=round(statistics.median(durations), 6) if durations else None,
                   generationMeanSeconds=round(statistics.mean(durations), 6) if durations else None)
    write_json(summary_path, summary)
    print(json.dumps({"summaryPath": relative(summary_path), "status": summary["status"],
                      "generatedCount": len(successful), "inputCount": len(rows)}), flush=True)
    return 0 if summary["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
