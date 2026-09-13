"""Verify the sealed v5 model for the independent quality comparison.

The training dependency infer_v5.py is immutable. Its full completed-model,
lineage, inventory, evaluation and ledger checks are preserved here; only the
parent-to-checkpoint special-token serialization comparison is made semantic.
MarianTokenizer.save_pretrained converts legacy strings to AddedToken objects.
No weights, manifests, evaluation records or frozen training code are changed.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

TRAINING_ROOT = Path(__file__).resolve().parents[1] / "model-training"
sys.path.insert(0, str(TRAINING_ROOT))
import infer
import train_v5 as v5

IDENTITY_KEYS = v5.IDENTITY_KEYS
FROZEN_INFERENCE_SHA256 = "4cb61e7a4785f8dd69b3fda3b803d1f8dc1fa4c4ff9e1863895d9359f8e24712"
TOKENIZER_POLICY = "v5-identical-tokenizer-legacy-special-token-serialization-v1"
TOKENS = {"eos_token": "</s>", "pad_token": "<pad>", "unk_token": "<unk>"}
TOKEN_OPTIONS = {"lstrip", "normalized", "rstrip", "single_word"}


def special_token_map(path):
    """Accept only the observed legacy strings or exact default token objects."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise infer.InferenceError("Duplicate special-token metadata key")
            result[key] = value
        return result

    def nonfinite(_value):
        raise infer.InferenceError("Nonfinite special-token metadata value")

    try:
        values = json.loads(path.read_text("utf-8"), object_pairs_hook=unique,
                            parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise infer.InferenceError("Malformed special-token metadata") from error
    if not isinstance(values, dict) or set(values) != set(TOKENS):
        raise infer.InferenceError("Unexpected special-token metadata keys")
    result = {}
    for name, expected in TOKENS.items():
        value = values[name]
        if isinstance(value, str):
            content = value
        elif isinstance(value, dict):
            if (set(value) != {"content", *TOKEN_OPTIONS}
                    or any(value[key] is not False for key in TOKEN_OPTIONS)):
                raise infer.InferenceError("Unexpected special-token options")
            content = value["content"]
        else:
            raise infer.InferenceError("Unexpected special-token representation")
        if not isinstance(content, str) or content != expected:
            raise infer.InferenceError("Special-token content differs from the frozen contract")
        result[name] = content
    return result


def verify_tokenizer_equivalence(model, parent, hashes, parent_hashes):
    # All learned token pieces, IDs and runtime options remain byte-identical.
    for name in ("source.spm", "target.spm", "vocab.json", "target_vocab.json", "tokenizer_config.json"):
        if hashes[name] != parent_hashes[name]:
            raise infer.InferenceError("V5 tokenizer differs from the frozen parent")
    name = "special_tokens_map.json"
    if special_token_map(model / name) != special_token_map(parent / name):
        raise infer.InferenceError("V5 special-token semantics differ from the frozen parent")
    return {"policy": TOKENIZER_POLICY, "parentSha256": parent_hashes[name],
            "selectedSha256": hashes[name], "bytesDiffer": hashes[name] != parent_hashes[name],
            "tokenSemanticsEqual": True, "otherTokenizerFilesByteIdentical": True}


def verified_model(run_id):
    if infer.sha256(TRAINING_ROOT / "infer_v5.py") != FROZEN_INFERENCE_SHA256:
        raise infer.InferenceError("Frozen v5 inference dependency changed")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
        raise infer.InferenceError("Invalid run ID")
    run_dir = v5.WORK_ROOT / "runs" / run_id
    manifest = infer.read_json(run_dir / "manifest.json")
    training = infer.read_json(run_dir / "training-summary.json")
    if (manifest.get("runId") != run_id or manifest.get("scriptVersion") != v5.VERSION
            or manifest.get("scriptSha256") != infer.sha256(Path(v5.__file__))
            or manifest.get("gate") != v5.POLICY
            or manifest.get("identitySha256") != v5.canonical_hash({key: manifest.get(key) for key in IDENTITY_KEYS})
            or training.get("runId") != run_id or training.get("baseWeightFileSha256") != v5.PARENT_WEIGHT_SHA256
            or training.get("completedUpdates", 0) < 1 or training.get("weightEvidence", {}).get("changedTensorCount", 0) < 1):
        raise infer.InferenceError("The run does not identify a completed v5 model and frozen policy")
    parent, files, lineage = v5.verify_parent()
    if (manifest.get("lineage") != lineage or manifest.get("baseFiles") != files
            or manifest.get("originalBase") != lineage["originalBase"]
            or infer.local_path(manifest["basePath"], v5.WORK_ROOT) != parent
            or manifest.get("dependencyFiles") != v5.dependency_hashes()):
        raise infer.InferenceError("V5 parent lineage or training dependency integrity changed")
    model = infer.local_path(training["modelPath"], run_dir)
    selected = infer.local_path(manifest["selectedCheckpoint"], run_dir)
    checkpoint = infer.read_json(selected / "checkpoint.json")
    hashes = {}
    for name in v5.MODEL_FILES:
        current, original = model / name, selected / "model" / name
        if current.is_symlink() or original.is_symlink() or infer.sha256(current) != infer.sha256(original):
            raise infer.InferenceError("V5 selected model/tokenizer integrity changed")
        hashes[name] = infer.sha256(current)
    weight = hashes["model.safetensors"]
    if (weight != training.get("trainedWeightFileSha256") or weight != checkpoint.get("modelSha256")
            or checkpoint.get("step") != training.get("selectedStep")):
        raise infer.InferenceError("V5 selected checkpoint identity changed")
    try:
        v5.verify_model_inventory(run_dir, manifest, training)
    except ValueError as error:
        raise infer.InferenceError(str(error)) from error
    tokenizer_serialization = verify_tokenizer_equivalence(model, parent, hashes, files)
    infer.validate_tokenizer_files(model)
    evaluation_path = run_dir / "evaluation-summary.json"
    evaluation = infer.read_json(evaluation_path) if evaluation_path.exists() else None
    if evaluation and (evaluation.get("runId") != run_id or evaluation.get("trainedWeightFileSha256") != weight
            or evaluation.get("baseWeightFileSha256") != v5.PARENT_WEIGHT_SHA256
            or evaluation.get("gatePolicy") != v5.POLICY or infer.local_path(evaluation["modelPath"], run_dir) != model
            or evaluation.get("testDataSha256") != manifest.get("datasets", {}).get("test", {}).get("sha256")
            or not evaluation.get("testDataSha256") or evaluation.get("devGate") != training.get("devGate")):
        raise infer.InferenceError("V5 evaluation identity differs from the model")
    if evaluation:
        ledger_path = v5.engine.test_ledger(manifest)
        ledger = infer.read_json(ledger_path) if ledger_path.exists() else {}
        if (ledger.get("status") != "complete" or ledger.get("runId") != run_id
                or ledger.get("modelSha256") != weight or ledger.get("testDataSha256") != evaluation["testDataSha256"]
                or ledger.get("summaryPath") != infer.relative(evaluation_path)):
            raise infer.InferenceError("V5 final evaluation completion ledger differs")
    eligible = bool(evaluation and evaluation.get("promotionEligible")
                    and evaluation.get("devGate", {}).get("passed") and evaluation.get("testGate", {}).get("passed"))
    return {"manifest": manifest, "training": training, "path": model, "weightHash": weight,
            "fileHashes": {key: value for key, value in hashes.items() if key != "model.safetensors"},
            "promotionEligible": eligible, "evaluationStatus": "passed-local-evaluation" if eligible else "experimental",
            "testWasEvaluated": evaluation is not None, "evaluation": evaluation,
            "tokenizerSerialization": tokenizer_serialization}
