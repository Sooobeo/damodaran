"""Inspect a selected trained checkpoint locally, without glossary or memory.

Example: python infer.py --run-id finance-v3 --text "The book value is positive."
Without --text, read plain UTF-8 text from stdin. This tool never evaluates the
held-out test set, changes model files, or promotes a model into the application.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import time

from train import APP_ROOT, BASE_ID, BASE_REVISION, BASE_WEIGHT_HASH, WORK_ROOT, local_path, network_off, numeric_tokens, relative, sha256, validate_tokenizer_files


class InferenceError(ValueError):
    pass


def read_json(path):
    return json.loads(path.read_text("utf-8"))


def verified_model(run_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
        raise InferenceError("Invalid run ID")
    run_dir = WORK_ROOT / "runs" / run_id
    manifest = read_json(run_dir / "manifest.json")
    training = read_json(run_dir / "training-summary.json")
    if (manifest.get("runId") != run_id or manifest.get("baseModel") != BASE_ID
            or manifest.get("baseRevision") != BASE_REVISION
            or training.get("runId") != run_id or training.get("baseWeightFileSha256") != BASE_WEIGHT_HASH
            or training.get("weightEvidence", {}).get("changedTensorCount", 0) < 1
            or training.get("completedUpdates", 0) < 1):
        raise InferenceError("The run does not identify a completed, genuinely updated model")
    model_path = local_path(training["modelPath"], run_dir)
    selected = local_path(manifest["selectedCheckpoint"], run_dir)
    checkpoint = read_json(selected / "checkpoint.json")
    weight_hash = sha256(model_path / "model.safetensors")
    if (weight_hash != training.get("trainedWeightFileSha256")
            or weight_hash != checkpoint.get("modelSha256")
            or weight_hash != sha256(selected / "model/model.safetensors")):
        raise InferenceError("Selected model weight integrity check failed")

    # Verify the final copy against the selected training checkpoint. The
    # tokenizer's learned vocabulary must remain the pinned original vocabulary;
    # no user-controlled remote tokenizer code or additional token files load.
    files = ("source.spm", "target.spm", "vocab.json", "target_vocab.json", "tokenizer_config.json", "special_tokens_map.json", "config.json", "generation_config.json")
    hashes = {}
    for name in files:
        current, original = model_path / name, selected / "model" / name
        if not current.is_file() or not original.is_file() or sha256(current) != sha256(original):
            raise InferenceError("Selected model/tokenizer file integrity check failed")
        hashes[name] = sha256(current)
    base_path = local_path(manifest["basePath"], WORK_ROOT)
    for name in ("source.spm", "target.spm", "vocab.json", "target_vocab.json", "tokenizer_config.json", "prepared-manifest.json"):
        if sha256(base_path / name) != manifest["baseFiles"].get(name):
            raise InferenceError("Pinned base tokenizer integrity check failed")
    for name in ("source.spm", "target.spm"):
        if hashes[name] != manifest["baseFiles"][name]:
            raise InferenceError("The trained model uses an unexpected SentencePiece tokenizer")
    for name in ("vocab.json", "target_vocab.json"):
        if read_json(model_path / name) != read_json(base_path / name):
            raise InferenceError("The trained model vocabulary differs from the prepared original")
    base_tokenizer = read_json(base_path / "tokenizer_config.json")
    expected_settings = {"source_lang": "en", "target_lang": "ko", "unk_token": "<unk>", "eos_token": "</s>", "pad_token": "<pad>", "separate_vocabs": True}
    if any(base_tokenizer.get(key) != value for key, value in expected_settings.items()):
        raise InferenceError("Unexpected base tokenizer contract")
    validate_tokenizer_files(base_path, manifest["baseFiles"])
    validate_tokenizer_files(model_path)

    evaluation_path = run_dir / "evaluation-summary.json"
    evaluation = read_json(evaluation_path) if evaluation_path.exists() else None
    if evaluation and (evaluation.get("runId") != run_id or evaluation.get("trainedWeightFileSha256") != weight_hash
                       or local_path(evaluation["modelPath"], run_dir) != model_path):
        raise InferenceError("Evaluation identity differs from the selected model")
    eligible = bool(evaluation and evaluation.get("promotionEligible") and evaluation.get("devGate", {}).get("passed")
                    and evaluation.get("testGate", {}).get("passed"))
    return {"manifest": manifest, "training": training, "path": model_path, "weightHash": weight_hash,
            "fileHashes": hashes, "promotionEligible": eligible,
            "evaluationStatus": "passed-local-evaluation" if eligible else "experimental",
            "testWasEvaluated": evaluation is not None}


def translate(text, verified, device="cpu", threads=4):
    network_off()
    import torch
    from transformers import MarianMTModel, MarianTokenizer
    torch.set_num_threads(threads)
    if device == "xpu" and not (hasattr(torch, "xpu") and torch.xpu.is_available()):
        raise InferenceError("Requested XPU is unavailable; the default CPU remains supported")
    path = verified["path"]
    # Construct the documented Marian tokenizer from the checked files, instead
    # of interpreting optional remote/custom tokenizer configuration fields.
    tokenizer = MarianTokenizer(source_spm=str(path / "source.spm"), target_spm=str(path / "target.spm"),
                                vocab=str(path / "vocab.json"), source_lang="en", target_lang="ko",
                                target_vocab_file=str(path / "target_vocab.json"),
                                unk_token="<unk>", eos_token="</s>", pad_token="<pad>",
                                separate_vocabs=True, model_max_length=512)
    config = read_json(path / "config.json")
    if config.get("model_type") != "marian" or tokenizer.pad_token_id != config.get("pad_token_id") or tokenizer.eos_token_id != config.get("eos_token_id"):
        raise InferenceError("Model and tokenizer special tokens do not agree")
    settings = verified["manifest"]["config"]
    inputs = tokenizer(text, return_tensors="pt", truncation=False)
    input_tokens = int(inputs["input_ids"].shape[1])
    if input_tokens > settings["max_length"]:
        raise InferenceError("Input exceeds this run's token limit; supply a shorter passage")
    started = time.monotonic()
    model = MarianMTModel.from_pretrained(str(path), local_files_only=True, use_safetensors=True,
                                          torch_dtype=torch.float32, attn_implementation="eager")
    model.to(device).eval()
    with torch.inference_mode():
        ids = model.generate(**{key: value.to(device) for key, value in inputs.items()}, num_beams=settings["beams"],
                             max_new_tokens=settings["max_new_tokens"], do_sample=False, use_cache=True)[0].cpu().tolist()
    translated = tokenizer.decode(ids, skip_special_tokens=True)
    warnings = []
    if not translated.strip():
        warnings.append("빈 번역이 생성되었습니다.")
    if len(ids) - 1 >= settings["max_new_tokens"]:
        warnings.append("생성 길이 상한에 도달했습니다. 원문과 함께 확인하세요.")
    numbers_preserved = numeric_tokens(text) == numeric_tokens(translated)
    if not numbers_preserved:
        warnings.append("원문의 숫자·부호·백분율과 번역을 확인하세요.")
    return {"translation": translated, "inputTokens": input_tokens, "generatedTokens": max(0, len(ids) - 1),
            "device": device, "precision": "fp32", "elapsedSeconds": round(time.monotonic() - started, 3),
            "numbersPreserved": numbers_preserved, "warnings": warnings}


def main():
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--text")
    parser.add_argument("--device", choices=("cpu", "xpu"), default="cpu")
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.threads <= 32:
        parser.error("threads must be between 1 and 32")
    text = args.text if args.text is not None else sys.stdin.read(12001)
    if not text.strip() or len(text) > 12000:
        raise InferenceError("Supply between 1 and 12000 text characters")
    verified = verified_model(args.run_id)
    # stdout is a single machine-readable JSON object. Third-party progress and
    # diagnostics stay out of it and cannot inadvertently echo the input.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = translate(text, verified, args.device, args.threads)
    result.update({"runId": args.run_id, "baseModel": BASE_ID, "modelPath": relative(verified["path"]),
                   "modelSha256": verified["weightHash"], "tokenizerAndConfigHashes": verified["fileHashes"],
                   "inputSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "glossaryApplied": False,
                   "translationMemoryApplied": False, "promotionEligible": verified["promotionEligible"],
                   "status": verified["evaluationStatus"], "testWasEvaluated": verified["testWasEvaluated"],
                   "appDeploymentPerformed": False})
    print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # No original text, reference translations, or arbitrary library error
        # payloads are included in diagnostic output.
        message = str(error) if isinstance(error, InferenceError) else "선택된 학습 모델의 로컬 번역에 실패했습니다. 실행 기록과 설치 상태를 확인하세요."
        print(json.dumps({"error": {"code": "LOCAL_TRAINED_INFERENCE_FAILED", "type": type(error).__name__, "message": message[:300]}}, ensure_ascii=False), flush=True)
        sys.exit(1)
