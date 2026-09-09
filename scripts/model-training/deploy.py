"""Check raw INT8/dev parity, then explicitly register a passing local model.

parity: compare against the selected checkpoint's cached dev predictions only.
register: verify the completed evidence and atomically write the app manifest.
Neither stage changes the app's selected provider or reads held-out test rows.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
from pathlib import Path
import sys

import infer
import runtime
import train

PARITY_POLICY = {"version": 1, "dataset": "selected-checkpoint-dev-only", "glossaryApplied": False,
                 "maxChrFRegression": 1.0, "maxBleuRegression": 1.0, "maxTermCoverageRegression": 0.02,
                 "allowNumericRegression": False, "allowEmptyOrCappedOutput": False,
                 "domainsRequired": ["finance", "general"], "beamSize": 4,
                 "note": "INT8 is compared with cached raw FP32 development predictions. Exact string matches are reported, not required. Held-out test rows are never consumed for quantization tuning."}


def read_json(path):
    return json.loads(path.read_text("utf-8"))


def preflight(run_id):
    verified = infer.verified_model(run_id)
    if not verified["promotionEligible"]:
        raise ValueError("The selected model has not passed both preregistered evaluation gates")
    manifest = verified["manifest"]
    run = runtime.TRAINING_ROOT / "runs" / run_id
    export = read_json(run / runtime.EXPORT_MANIFEST)
    if (export.get("format") != "ctranslate2" or export.get("quantization") != "int8"
            or export.get("sourceModelSha256") != verified["weightHash"]
            or export.get("separateVocabularies") is not True or export.get("tokenizerRepair") != train.TOKENIZER_REPAIR
            or export.get("conversionVersion") != runtime.EXPORT_VARIANT
            or export.get("decoderStartEmbeddingPreserved") is not True):
        raise ValueError("Export provenance differs from the evaluated model")
    directory = run / runtime.MODEL_DIRECTORY
    inventory = runtime.model_inventory(directory)
    files = {Path(item["path"]).name: item["sha256"] for item in inventory}
    if len(files) != len(inventory) or files != export.get("files"):
        raise ValueError("Export model inventory or bytes changed")
    for name in ("source.spm", "target.spm", "vocab.json", "target_vocab.json", "tokenizer_config.json", "special_tokens_map.json", "generation_config.json"):
        if files.get(name) != verified["fileHashes"][name]:
            raise ValueError("Export tokenizer differs from the evaluated model")
    settings = runtime.decoding_settings({"beams": manifest["config"]["beams"], "maxInputTokens": manifest["config"]["max_length"],
                                         "maxNewTokens": manifest["config"]["max_new_tokens"]})
    candidate = {"schemaVersion": 1, "provider": "finetuned", "runId": run_id,
                 "model": "marian-finance-" + run_id + "-int8-pad-v2", "modelHash": runtime.canonical_hash(inventory),
                 "runtimeVersion": runtime.runtime_version(settings), "pythonPath": runtime.relative(Path(sys.executable)),
                 "modelPath": runtime.relative(directory), "modelFiles": inventory, "decoding": settings,
                 "promotionEligible": True, "evaluationSummarySha256": runtime.sha256(run / "evaluation-summary.json")}
    return run, verified, candidate


def dev_inputs(run, verified):
    manifest = verified["manifest"]
    data = manifest["datasets"]["dev"]
    path = train.local_path(data["path"])
    if train.sha256(path) != data["sha256"]:
        raise ValueError("Development data changed after model selection")
    rows = train.read_rows(path, "dev")
    checkpoint = train.local_path(manifest["selectedCheckpoint"], run)
    cache = checkpoint / "dev-predictions.jsonl"
    predictions = [json.loads(line) for line in cache.read_text("utf-8").splitlines() if line.strip()]
    if len(predictions) != len(rows):
        raise ValueError("Selected model development cache is incomplete")
    protocol = predictions[0].get("generationProtocol", {})
    if (protocol.get("effectivePrecision") != "fp32" or protocol.get("beams") != 4
            or protocol.get("maxNewTokens") != manifest["config"]["max_new_tokens"]
            or protocol.get("maxInputTokens") != manifest["config"]["max_length"]):
        raise ValueError("Parity requires the selected raw FP32/beam4 development baseline")
    for index, (row, item) in enumerate(zip(rows, predictions)):
        if (item.get("index") != index or item.get("id") != row["id"] or item.get("generationProtocol") != protocol
                or item.get("modelIdentity") != verified["weightHash"]
                or item.get("sourceHash") != train.hashlib.sha256(row["source"].encode()).hexdigest()):
            raise ValueError("Selected development predictions have a different identity")
    expected_metrics = verified["training"]["selectedDev"]
    observed_metrics = train.metrics(rows, predictions)
    if any(expected_metrics.get(key) != value for key, value in observed_metrics.items()):
        raise ValueError("Cached development predictions differ from the selected model's recorded metrics")
    return rows, predictions, train.sha256(cache)


def parity_gate(base, candidate):
    reasons, domains = [], {}
    for domain in PARITY_POLICY["domainsRequired"]:
        left, right = base.get("byDomain", {}).get(domain), candidate.get("byDomain", {}).get(domain)
        failures = []
        if not left or not right or left.get("count") != right.get("count"):
            failures.append("domain-missing-or-count-mismatch")
        else:
            for metric in ("chrF", "bleu", "numericPreservation"):
                if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (left.get(metric), right.get(metric))):
                    failures.append("invalid-" + metric)
            if failures:
                domains[domain] = {"passed": False, "reasons": failures}
                reasons.append(domain + "-parity-failed")
                continue
            for metric, limit in (("chrF", 1.0), ("bleu", 1.0)):
                if right[metric] + limit + 1e-9 < left[metric]:
                    failures.append(metric + "-regression")
            if right["numericPreservation"] + 1e-9 < left["numericPreservation"]:
                failures.append("numeric-regression")
            if right.get("emptyOutputs") or right.get("cappedOutputs"):
                failures.append("empty-or-capped-output")
            if domain == "finance":
                if left.get("termAccuracy") is None or right.get("termAccuracy") is None:
                    failures.append("term-coverage-missing")
                elif right["termAccuracy"] + 0.02 + 1e-9 < left["termAccuracy"]:
                    failures.append("term-coverage-regression")
        domains[domain] = {"passed": not failures, "reasons": failures}
        if failures:
            reasons.append(domain + "-parity-failed")
    if base.get("count") != candidate.get("count") or not candidate.get("count"):
        reasons.append("prediction-count-mismatch")
    return {"passed": not reasons, "reasons": reasons, "byDomain": domains}


def identity_for(run, verified, candidate, source_cache_hash):
    return {"modelHash": candidate["modelHash"], "runtimeVersion": candidate["runtimeVersion"],
            "sourceModelSha256": verified["weightHash"], "sourcePredictionSha256": source_cache_hash,
            "devDataSha256": verified["manifest"]["datasets"]["dev"]["sha256"],
            "evaluationSummarySha256": candidate["evaluationSummarySha256"],
            "policy": PARITY_POLICY, "deploymentScriptSha256": train.sha256(Path(__file__))}


def parity(run_id):
    run, verified, candidate = preflight(run_id)
    rows, source_predictions, source_hash = dev_inputs(run, verified)
    identity = identity_for(run, verified, candidate, source_hash)
    path = run / runtime.PARITY_PREDICTIONS
    predictions = [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()] if path.exists() else []
    if len(predictions) > len(rows):
        raise ValueError("Extra development parity predictions")
    for index, item in enumerate(predictions):
        if item.get("index") != index or item.get("identity") != identity or item.get("sourceHash") != source_predictions[index]["sourceHash"]:
            raise ValueError("Existing parity cache uses another model or runtime")
    if len(predictions) < len(rows):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            translator = runtime.LocalTranslator(candidate)
        for index in range(len(predictions), len(rows)):
            # No glossary, sentencizer, protected-value substitution or reference
            # text is passed to inference. Only original English source is used.
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                result = translator.raw_result(rows[index]["source"])
            item = {"index": index, "id": rows[index]["id"], "sourceHash": source_predictions[index]["sourceHash"],
                    "identity": identity, "modelIdentity": candidate["modelHash"],
                    "generationProtocol": {"backend": "ctranslate2-cpu-int8", "runtimeVersion": candidate["runtimeVersion"], "decoding": candidate["decoding"]},
                    "prediction": result["translatedText"], "generatedTokens": result["generatedTokens"], "atLengthLimit": result["atLengthLimit"]}
            train.append_json(path, item)
            predictions.append(item)
            if (index + 1) % 10 == 0 or index + 1 == len(rows):
                train.emit("parity-progress", completed=index + 1, total=len(rows))
        del translator
    base, converted = train.metrics(rows, source_predictions), train.metrics(rows, predictions)
    result = parity_gate(base, converted)
    exact = sum(left["prediction"] == right["prediction"] for left, right in zip(source_predictions, predictions))
    summary = {"schemaVersion": 1, "runId": run_id, "createdAt": train.now(), **identity, **result,
               "baseline": base, "converted": converted, "exactStringMatches": exact, "exactStringMatchFraction": exact / len(rows),
               "predictionSha256": train.sha256(path), "heldOutTestRowsRead": False, "glossaryApplied": False,
               "appRegistrationPerformed": False}
    train.write_json(run / runtime.PARITY_SUMMARY, summary)
    train.emit("parity-complete", runId=run_id, passed=result["passed"], reasons=result["reasons"], exactStringMatchFraction=summary["exactStringMatchFraction"])


def register(run_id):
    run, verified, candidate = preflight(run_id)
    rows, source_predictions, source_hash = dev_inputs(run, verified)
    identity = identity_for(run, verified, candidate, source_hash)
    summary = read_json(run / runtime.PARITY_SUMMARY)
    if any(summary.get(key) != value for key, value in identity.items()) or not summary.get("passed"):
        raise ValueError("A passing parity check for these exact model/runtime bytes is required")
    cache = run / runtime.PARITY_PREDICTIONS
    if summary.get("predictionSha256") != train.sha256(cache):
        raise ValueError("Parity predictions changed after validation")
    predictions = [json.loads(line) for line in cache.read_text("utf-8").splitlines() if line.strip()]
    if len(predictions) != len(rows):
        raise ValueError("Parity prediction count changed")
    for index, item in enumerate(predictions):
        if item.get("index") != index or item.get("identity") != identity or item.get("sourceHash") != source_predictions[index]["sourceHash"]:
            raise ValueError("Parity prediction identity changed")
    if not parity_gate(train.metrics(rows, source_predictions), train.metrics(rows, predictions))["passed"]:
        raise ValueError("Recomputed development parity does not pass")
    candidate.update({"installedAt": train.now(), "paritySummarySha256": train.sha256(run / runtime.PARITY_SUMMARY)})
    train.write_json(runtime.TRAINING_ROOT / "deployed/manifest.json", candidate)
    # This file does not switch the app's provider. Root/user handles that
    # separate, concrete final action after reviewing the completed artifacts.
    train.emit("registered-local-model", runId=run_id, model=candidate["model"], modelHash=candidate["modelHash"], providerSwitchPerformed=False)


def main():
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("parity", "register"))
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    runtime.disable_network()
    with train.exclusive_run(args.run_id):
        (parity if args.stage == "parity" else register)(args.run_id)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": {"code": "LOCAL_MODEL_REGISTRATION_FAILED", "type": type(error).__name__,
                                    "message": "학습 모델의 무결성·평가·변환 동등성 검증을 통과하지 못했습니다."}}, ensure_ascii=False), flush=True)
        sys.exit(1)
