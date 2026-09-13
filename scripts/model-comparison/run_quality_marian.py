"""Compare a sealed v5 FP32 model on the new long-passage quality probe.

The whole source is translated on CPU without glossary, context, memory, or
number masking. V5 identity and evaluation-summary/ledger metadata are checked;
training/test text and prior predictions are not read or evaluated again.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
CODE_DEPENDENCIES = (
    "scripts/model-comparison/run_quality_marian.py",
    "scripts/model-comparison/verify_quality_v5.py",
    "scripts/model-training/infer_v5.py", "scripts/model-training/infer.py",
    "scripts/model-training/train_v5.py", "scripts/model-training/train.py",
    "scripts/model-training/dataset_v5.py", "scripts/model-training/dataset_io.py",
    "scripts/model-training/assemble_v5_dataset.py",
)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local(value):
    path = (ROOT / value).resolve()
    require(path.is_relative_to((ROOT / ".training/comparisons").resolve()), "Comparison path escaped its directory")
    return path


def read_frozen_input(source_path):
    require(source_path.name == "dataset.jsonl", "Expected the frozen dataset.jsonl")
    manifest_path = source_path.with_name("dataset-manifest.json")
    source_bytes, manifest_bytes = source_path.read_bytes(), manifest_path.read_bytes()
    input_sha = hashlib.sha256(source_bytes).hexdigest()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    publication = json.loads(manifest_bytes)
    rows = [json.loads(line) for line in source_bytes.decode("utf-8").splitlines() if line.strip()]
    require(isinstance(publication, dict) and isinstance(publication.get("dataset"), dict)
            and len(rows) == 24 and all(isinstance(row, dict) for row in rows),
            "The quality dataset is not frozen")
    dataset = publication["dataset"]
    ids = [row.get("id") for row in rows]
    require(publication.get("version") == "finance-quality-20260910-v1"
            and publication.get("status") == "frozen" and publication.get("humanReviewed") is False
            and publication.get("sourceType") == "assistant_authored_unreviewed"
            and dataset.get("file") == source_path.name and dataset.get("sha256") == input_sha
            and type(dataset.get("count")) is int and dataset["count"] == 24 and dataset.get("ids") == ids
            and all(isinstance(identity, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", identity)
                    for identity in ids) and len(set(ids)) == len(ids), "The quality dataset is not frozen")
    clean = []
    for row in rows:
        source, context = row.get("source"), row.get("context", "")
        require(row.get("split") == "exploratory_probe" and isinstance(source, str) and source.strip()
                and isinstance(context, str), "Quality input source identity differs")
        source_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        context_sha = hashlib.sha256(context.encode("utf-8")).hexdigest()
        require(row.get("sourceSha256") == source_sha
                and ("contextSha256" not in row or row["contextSha256"] == context_sha),
                "Quality input source/context identity differs")
        # Only these fields can reach inference; evaluation annotations stay outside it.
        clean.append({"id": row["id"], "source": source, "context": context,
                      "sourceSha256": source_sha, "contextSha256": context_sha})
    reviews = publication.get("reviewFiles")
    require(isinstance(reviews, list) and bool(reviews), "Frozen review evidence is required")
    evidence, names, hashes = [], set(), set()
    input_hashes = {str(source_path): input_sha, str(manifest_path): manifest_sha}
    for review in reviews:
        require(isinstance(review, dict), "Invalid review evidence")
        name, review_sha = review.get("file"), review.get("sha256")
        require(isinstance(name, str) and name not in ("", ".", "..", source_path.name, manifest_path.name)
                and Path(name).name == name and not re.search(r'[\\/:<>"|?*\x00-\x1f]', name)
                and name == name.rstrip(" .") and name.casefold() not in names
                and isinstance(review_sha, str) and re.fullmatch(r"[0-9a-f]{64}", review_sha)
                and review_sha not in hashes, "Invalid or duplicate review identity")
        review_path = source_path.parent / name
        require(not review_path.is_symlink() and review_path.resolve().parent == source_path.parent
                and review_path.is_file() and digest(review_path) == review_sha, "Frozen review evidence changed")
        names.add(name.casefold())
        hashes.add(review_sha)
        input_hashes[str(review_path)] = review_sha
        evidence.append({"file": name, "path": str(review_path), "sha256": review_sha})
    return clean, {"inputSha256": input_sha, "datasetManifestSha256": manifest_sha,
                   "sourceType": publication["sourceType"], "reviewFiles": evidence}, input_hashes


def check_integrity(input_hashes, code_hashes):
    expected = input_hashes | {str(ROOT / name): value for name, value in code_hashes.items()}
    require(all(Path(path).is_file() and digest(path) == value for path, value in expected.items()),
            "Comparison input, review evidence or code changed during execution")


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    source_path, output = local(args.input), local(args.output)
    require(not output.exists(), "Preserve the previous comparison; choose a new output directory")
    # Capture every local helper before importing inference/training modules.
    code_hashes = {name: digest(ROOT / name) for name in CODE_DEPENDENCIES}
    rows, input_info, input_hashes = read_frozen_input(source_path)
    input_sha = input_info["inputSha256"]
    check_integrity(input_hashes, code_hashes)
    sys.path.insert(0, str(ROOT / "scripts/model-training"))
    import verify_quality_v5
    from train import network_off, numeric_tokens
    network_off()
    integrity_start = time.monotonic()
    verified = verify_quality_v5.verified_model(args.run_id)
    integrity_seconds = time.monotonic() - integrity_start
    run_config = verified["manifest"]["config"]
    options = {"device": "cpu", "precision": "fp32", "threads": 4, "seed": 20260910,
               "beams": run_config["beams"], "maxInputTokens": run_config["max_length"],
               "maxNewTokens": run_config["max_new_tokens"], "doSample": False, "useCache": True,
               "attentionImplementation": "eager", "glossaryApplied": False, "contextPassedToModel": False,
               "translationMemoryApplied": False, "numberFormulaProtectionApplied": False}
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "start.json", {"version": 1, "startedAt": now(), "inputSha256": input_sha,
               "datasetManifestSha256": input_info["datasetManifestSha256"], "codeHashes": code_hashes, "settings": options,
               "reviewFiles": input_info["reviewFiles"], "sourceType": input_info["sourceType"],
               "modelSha256": verified["weightHash"], "modelFiles": verified["fileHashes"],
               "tokenizerSerialization": verified["tokenizerSerialization"],
               "runId": args.run_id, "selectedStep": verified["training"]["selectedStep"],
               "evaluationStatus": verified["evaluationStatus"], "appDeploymentPerformed": False,
               "humanReviewed": False, "paidCalls": 0})
    predictions = output / "predictions.jsonl"
    results, failure, current, current_translation = [], None, None, None
    completed, integrity_verified = False, False
    started = time.monotonic()
    with predictions.open("x", encoding="utf-8", newline="\n") as stream:
        def append(result):
            stream.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            results.append(result)

        try:
            check_integrity(input_hashes, code_hashes)
            import torch
            import transformers
            from transformers import MarianMTModel, MarianTokenizer
            torch.set_num_threads(4)
            torch.manual_seed(20260910)
            model_path = verified["path"]
            tokenizer = MarianTokenizer(source_spm=str(model_path / "source.spm"), target_spm=str(model_path / "target.spm"),
                                        vocab=str(model_path / "vocab.json"), source_lang="en", target_lang="ko",
                                        target_vocab_file=str(model_path / "target_vocab.json"),
                                        unk_token="<unk>", eos_token="</s>", pad_token="<pad>",
                                        separate_vocabs=True, model_max_length=512)
            config = json.loads((model_path / "config.json").read_text("utf-8"))
            require(config.get("model_type") == "marian" and tokenizer.pad_token_id == config.get("pad_token_id")
                    and tokenizer.eos_token_id == config.get("eos_token_id"), "Model and tokenizer special tokens differ")
            model = MarianMTModel.from_pretrained(str(model_path), local_files_only=True, use_safetensors=True,
                                                  torch_dtype=torch.float32, attn_implementation="eager").eval()
            load_seconds = time.monotonic() - started
            for row in rows:
                current, current_translation = row, None
                check_integrity(input_hashes, code_hashes)
                tick = time.monotonic()
                inputs = tokenizer(row["source"], return_tensors="pt", truncation=False)
                count = int(inputs["input_ids"].shape[1])
                require(count <= options["maxInputTokens"], "Source exceeds the fixed model input limit")
                with torch.inference_mode():
                    ids = model.generate(**inputs, num_beams=options["beams"], max_new_tokens=options["maxNewTokens"],
                                         do_sample=False, use_cache=True)[0].cpu().tolist()
                translation = tokenizer.decode(ids, skip_special_tokens=True)
                current_translation = translation
                generated = max(0, len(ids) - 1)
                result = {"id": row["id"], "sourceSha256": row["sourceSha256"],
                          "contextSha256": row["contextSha256"],
                          "inputSha256": input_sha, "modelSha256": verified["weightHash"], "status": "generated",
                          "translation": translation, "inputTokens": count, "generatedTokens": generated,
                          "outputLimitReached": generated >= options["maxNewTokens"], "emptyOutput": not translation.strip(),
                          "numbersPreserved": numeric_tokens(row["source"]) == numeric_tokens(translation),
                          "generationSeconds": time.monotonic() - tick}
                append(result)
                current, current_translation = None, None
                print(json.dumps({"event": "comparison-progress", "id": row["id"], "seconds": result["generationSeconds"]}), flush=True)
            check_integrity(input_hashes, code_hashes)
            integrity_verified = True
            completed = True
        except BaseException as error:
            failure = type(error).__name__
            completed = False
            raise
        finally:
            recorded_ids = {result["id"] for result in results}
            for row in rows:
                if row["id"] not in recorded_ids:
                    failed_row = current is not None and current["id"] == row["id"]
                    append({"id": row["id"], "sourceSha256": row["sourceSha256"],
                            "contextSha256": row["contextSha256"], "inputSha256": input_sha,
                            "modelSha256": verified["weightHash"], "status": "failed" if failed_row else "not_run",
                            "translation": current_translation if failed_row else None,
                            "errorType": failure if failed_row else "run_did_not_complete"})
            generated_results = [result for result in results if result["status"] == "generated"]
            write_json(output / "summary.json", {"status": "complete" if completed else "failed",
                       "finishedAt": now(), "errorType": failure, "count": len(generated_results), "expectedCount": len(rows),
                       "recordedCount": len(results), "integrityVerified": integrity_verified,
                       "failedRowId": current["id"] if current is not None else None,
                       "inputSha256": input_sha, "modelSha256": verified["weightHash"],
                       "resultSha256": digest(predictions),
                       "integritySeconds": integrity_seconds, "loadSeconds": locals().get("load_seconds"),
                       "totalSeconds": time.monotonic() - started, "emptyCount": sum(row["emptyOutput"] for row in generated_results),
                       "cappedCount": sum(row["outputLimitReached"] for row in generated_results),
                       "numericPreservedCount": sum(row["numbersPreserved"] for row in generated_results),
                       "runtimeVersions": {"torch": getattr(locals().get("torch"), "__version__", None),
                                           "transformers": getattr(locals().get("transformers"), "__version__", None)},
                       "humanReviewed": False, "appDeploymentPerformed": False, "paidCalls": 0})


if __name__ == "__main__":
    main()
