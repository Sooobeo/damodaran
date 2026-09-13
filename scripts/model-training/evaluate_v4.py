"""Sealed three-model comparison using only a new run's dev/test data.

After training selects a checkpoint, run ``freeze`` BEFORE train.py evaluate.
Run ``compare --split dev`` or ``compare --split test`` to reuse the new run's
base/candidate predictions and generate the previous model exactly once. This
script neither trains nor exports/registers a model. Unit/symbol diagnostics and
anonymous review forms supplement lexical metrics; they are not semantic scores.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import gc
import hashlib
import json
from pathlib import Path
import platform
import random
import re
import signal
import sys
from types import SimpleNamespace
import unicodedata

import train as training

VERSION = "finance-v4-three-model-comparison-v1"
POLICY = {
    "version": 1,
    "numeric": "Existing explicit-digit/sign/percent multiset, separate from units and meaning.",
    "units": "Conservative explicit currency/scale/percent marker counts; legitimate conversion or omitted repeated units can flag. Not quantity equivalence or semantic accuracy.",
    "annotatedUnits": "Source-anchored targetAny presence and forbidden literal presence; diagnostic only.",
    "symbols": "Case-sensitive exact literal and occurrence count, with ASCII identifier boundaries where applicable. Whitespace is not normalized.",
    "semantics": "Unscored anonymous review of meaning, negation, conditions, omissions, additions, quantities and formulas. Assistant reference/review is not human review.",
    "contextualWordSense": "Judge ambiguous words from the source sentence and its financial or ordinary context, not a fixed glossary match: interest may mean 이자 or 관심; return 수익률 or 반환; capital 자본 or 수도; bond 채권 or 유대; equity 자기자본 or 공정성; period 기간 or 마침표. These examples are not exhaustive or replacement rules. A fluent sentence or matching financial term can still have the wrong sense; record source-grounded evidence and leave uncertain cases unresolved.",
    "semanticSeverity": {
        "0": "Source meaning preserved with usable Korean; stylistic alternatives allowed.",
        "1": "Awkward or mildly imprecise wording, with core claims, conditions and quantities preserved.",
        "2": "Material mistranslation, omission or unsupported addition affecting a source claim, condition, term or quantity.",
        "3": "Predominantly unusable output or reversal of the central source proposition.",
    },
    "semanticDecision": "Report anonymous assistant-reviewed severity counts and source-grounded examples. They are subjective on a small synthetic-reference sample, not certified accuracy. No automatic promotion from lexical gate alone.",
    "promotion": "This supplemental comparison never authorizes model registration or changes the frozen training gate.",
}
MODEL_NAMES = ("baseline", "previous", "candidate")
TOKENIZER_FILES = ("source.spm", "target.spm", "vocab.json", "target_vocab.json", "tokenizer_config.json", "special_tokens_map.json")
MODEL_FILES = ("model.safetensors", "config.json", "generation_config.json", *TOKENIZER_FILES)
UNIT_PATTERNS = {
    "currency:dollar": r"US\s*\$|(?<![A-Za-z])USD(?![A-Za-z])|\$|\bdollars?\b|달러",
    "currency:won": r"(?<![A-Za-z])KRW(?![A-Za-z])|₩|\bwon\b|(?<=[\d천만억조])\s*원|(?<![가-힣])원(?![가-힣])",
    "currency:euro": r"(?<![A-Za-z])EUR(?![A-Za-z])|€|\beuros?\b|유로",
    "currency:yen": r"(?<![A-Za-z])JPY(?![A-Za-z])|¥|\byen\b|(?<=[\d천만억조])\s*엔|(?<![가-힣])엔(?![가-힣])",
    "currency:pound": r"(?<![A-Za-z])GBP(?![A-Za-z])|£|\bpounds?\b|파운드",
    "scale:1e3": r"\bthousands?\b|(?<=\d)\s*천(?!만)",
    "scale:1e4": r"(?<=\d)\s*만|(?<![가-힣])만(?=\s*(?:원|달러|유로|엔))",
    "scale:1e6": r"\bmillions?\b|(?<=\d)\s*백만|(?<![가-힣])백만",
    "scale:1e8": r"(?<=\d)\s*억|(?<![가-힣])억(?=\s*(?:원|달러|유로|엔))",
    "scale:1e9": r"\bbillions?\b|(?<=\d)\s*십억|(?<![가-힣])십억",
    "scale:1e12": r"\btrillions?\b|(?<=\d)\s*조|(?<![가-힣])조(?=\s*(?:원|달러|유로|엔))",
    "percent": r"%|\bpercent(?:age)?\b|\bper\s+cent\b|퍼센트",
    "basis-point": r"\bbasis\s+points?\b|\bbps\b|베이시스\s*포인트",
}


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def read_json(path):
    return json.loads(path.read_text("utf-8-sig"))


def literal_count(text, literal):
    left = r"(?<![A-Za-z0-9_])" if re.match(r"[A-Za-z0-9_]", literal) else ""
    right = r"(?![A-Za-z0-9_])" if re.search(r"[A-Za-z0-9_]$", literal) else ""
    return len(re.findall(left + re.escape(literal) + right, text))


def explicit_units(text):
    normalized = unicodedata.normalize("NFKC", text)
    return Counter({name: count for name, pattern in UNIT_PATTERNS.items()
                    if (count := len(re.findall(pattern, normalized, re.IGNORECASE)))})


def validate_annotations(row):
    for field in ("protectedSymbols", "unitChecks", "criticalChecks"):
        if not isinstance(row.get(field, []), list):
            raise ValueError(f"{row['id']}: {field} must be a list")
    seen = set()
    for symbol in row.get("protectedSymbols", []):
        if not isinstance(symbol, str) or not symbol or symbol in seen:
            raise ValueError(f"{row['id']}: invalid/duplicate protected symbol")
        seen.add(symbol)
        count = literal_count(row["source"], symbol)
        if not count or literal_count(row["target"], symbol) != count:
            raise ValueError(f"{row['id']}: protected reference symbol does not exactly match source")
    for check in row.get("unitChecks", []):
        if (not isinstance(check, dict) or not isinstance(check.get("source"), str) or not check["source"]
                or not isinstance(check.get("targetAny"), list) or not check["targetAny"]
                or not isinstance(check.get("forbidden", []), list)
                or any(not isinstance(value, str) or not value for value in check["targetAny"] + check.get("forbidden", []))):
            raise ValueError(f"{row['id']}: invalid unit annotation")
        if not literal_count(row["source"], check["source"]):
            raise ValueError(f"{row['id']}: unit source anchor is absent")
        if not any(literal_count(row["target"], value) for value in check["targetAny"]):
            raise ValueError(f"{row['id']}: unit target reference is absent")
        if any(literal_count(row["target"], value) for value in check.get("forbidden", [])):
            raise ValueError(f"{row['id']}: reference includes a forbidden unit form")


def diagnostic_row(row, output):
    source_units, output_units = explicit_units(row["source"]), explicit_units(output)
    symbols = [{"literal": value, "sourceCount": literal_count(row["source"], value),
                "outputCount": literal_count(output, value)} for value in row.get("protectedSymbols", [])]
    annotated = [{"source": check["source"], "targetAny": check["targetAny"],
                  "matchedTargets": [value for value in check["targetAny"] if literal_count(output, value)],
                  "foundForbidden": [value for value in check.get("forbidden", []) if literal_count(output, value)]}
                 for check in row.get("unitChecks", [])]
    return {"id": row["id"], "numericTokensPreserved": training.numeric_tokens(row["source"]) == training.numeric_tokens(output),
            "sourceUnits": dict(source_units), "outputUnits": dict(output_units),
            "addedUnitMarkers": dict(output_units - source_units), "missingUnitMarkers": dict(source_units - output_units),
            "protectedSymbols": symbols, "protectedSymbolsPreserved": all(item["sourceCount"] == item["outputCount"] for item in symbols),
            "annotatedUnits": annotated, "annotatedUnitWarnings": sum(not item["matchedTargets"] or bool(item["foundForbidden"]) for item in annotated),
            "semanticReviewRequired": True, "semanticAccuracy": None}


def diagnostics(rows, predictions):
    details = [diagnostic_row(row, prediction["prediction"]) for row, prediction in zip(rows, predictions)]
    return {"count": len(details), "rowsWithAddedUnitMarkers": sum(bool(row["addedUnitMarkers"]) for row in details),
            "rowsWithMissingUnitMarkers": sum(bool(row["missingUnitMarkers"]) for row in details),
            "rowsWithAnnotatedUnitWarnings": sum(bool(row["annotatedUnitWarnings"]) for row in details),
            "rowsWithProtectedSymbols": sum(bool(row["protectedSymbols"]) for row in details),
            "rowsWithProtectedSymbolMismatch": sum(not row["protectedSymbolsPreserved"] for row in details),
            "semanticAccuracy": None, "policy": POLICY, "details": details}


def validate_predictions(rows, predictions, identity, protocol, complete=True):
    if len(predictions) > len(rows) or (complete and len(predictions) != len(rows)):
        raise ValueError("Prediction cache row count mismatch")
    for index, prediction in enumerate(predictions):
        row = rows[index]
        expected = {"index": index, "id": row["id"], "sourceHash": hashlib.sha256(row["source"].encode("utf-8")).hexdigest(),
                    "modelIdentity": identity, "generationProtocol": protocol}
        if any(prediction.get(key) != value for key, value in expected.items()):
            raise ValueError("Prediction cache identity/order/protocol mismatch")
        if (not isinstance(prediction.get("prediction"), str) or not isinstance(prediction.get("atLengthLimit"), bool)
                or type(prediction.get("generatedTokens")) is not int or prediction["generatedTokens"] < 0):
            raise ValueError("Prediction cache has invalid output metadata")


def source_group_metrics(rows, predictions_by_model):
    """Describe already-validated predictions by exact row provenance.

    Preserve original row indexes while subsetting every model. Group reports
    use the existing lexical metrics and diagnostics only, without new gates,
    generation or a claim that assistant reference translations establish truth.
    """
    if not rows or set(predictions_by_model) != set(MODEL_NAMES):
        raise ValueError("Source grouping requires nonempty rows and all comparison models")
    for predictions in predictions_by_model.values():
        if len(predictions) != len(rows):
            raise ValueError("Source grouping requires complete prediction rows")
        for index, (row, prediction) in enumerate(zip(rows, predictions)):
            if prediction.get("index") != index or prediction.get("id") != row["id"]:
                raise ValueError("Source grouping requires predictions aligned to original row indexes")
    groups = {}
    for index, row in enumerate(rows):
        provenance = row.get("provenance")
        if provenance is None or provenance == "":
            provenance = "unspecified"
        if not isinstance(provenance, str):
            raise ValueError("Source grouping requires a string provenance or an unspecified value")
        groups.setdefault(provenance, []).append(index)
    result = {}
    for provenance, indexes in groups.items():
        subset = [rows[index] for index in indexes]
        predictions = {name: [predictions_by_model[name][index] for index in indexes] for name in MODEL_NAMES}
        result[provenance] = {
            "count": len(indexes),
            "lexicalMetrics": {name: training.metrics(subset, predictions[name]) for name in MODEL_NAMES},
            "diagnostics": {name: diagnostics(subset, predictions[name]) for name in MODEL_NAMES},
            "semanticAccuracy": None,
            "note": "Grouped by dataset provenance; lexical similarity and explicit-marker diagnostics are not semantic correctness or human certification.",
        }
    return result


def read_predictions(path):
    # A torn/invalid JSONL tail is rejected rather than silently removed.
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def immutable_json(path, value):
    if path.exists():
        if read_json(path) != value:
            raise ValueError(f"Existing immutable output differs: {path.name}")
    else:
        training.write_json(path, value)


def immutable_jsonl(path, rows):
    text = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows)
    if path.exists():
        if path.read_text("utf-8") != text:
            raise ValueError(f"Existing immutable output differs: {path.name}")
    else:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text, "utf-8")
        temporary.replace(path)


def anonymous_review(rows, predictions_by_model, salt):
    reviews, keys = [], []
    for index, row in enumerate(rows):
        order = list(MODEL_NAMES)
        random.Random(canonical_hash([salt, row["id"]])).shuffle(order)
        choices = []
        for letter, name in zip("ABC", order):
            choices.append({"label": letter, "translation": predictions_by_model[name][index]["prediction"],
                            "review": {"meaningPreserved": None, "negationAndConditionsPreserved": None,
                                       "contextualWordSensePreserved": None,
                                       "omission": None, "unsupportedAddition": None, "quantityOrFormulaError": None,
                                       "fluency": None, "evidence": ""}})
        reviews.append({"id": row["id"], "source": row["source"], "assistantReference": row["target"],
                        "referenceProvenance": row.get("provenance"), "referenceReviewStatus": row.get("reviewStatus"),
                        "criticalChecks": row.get("criticalChecks", []), "protectedSymbols": row.get("protectedSymbols", []),
                        "unitChecks": row.get("unitChecks", []), "choices": choices,
                        "reviewerType": None, "humanReviewed": False,
                        "note": "Reference and automatic diagnostics are not ground-truth semantic judgments. Record source-grounded evidence; identify assistant vs human reviewer."})
        keys.append({"id": row["id"], "labels": dict(zip("ABC", order))})
    return reviews, keys


def run_directory(identifier):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", identifier):
        raise ValueError("Invalid run ID")
    return training.local_path(f".training/runs/{identifier}", training.WORK_ROOT / "runs")


def model_inventory(path, weight_hash):
    if training.sha256(path / "model.safetensors") != weight_hash:
        raise ValueError("Selected model weights changed")
    training.validate_tokenizer_files(path)
    return {"path": training.relative(path), "weightSha256": weight_hash,
            "files": {name: training.sha256(path / name) for name in MODEL_FILES}}


def generation_defaults(path):
    return {key: value for key, value in read_json(path / "generation_config.json").items()
            if key not in {"transformers_version", "_from_model_config"}}


def runtime_identity(torch, device, versions, args):
    device_name = torch.xpu.get_device_name(0) if device == "xpu" else platform.processor()
    return {"python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine(),
            "executable": str(Path(sys.executable).resolve()), "versions": versions, "device": device,
            "deviceName": device_name, "threads": args.threads, "seed": args.seed,
            "torchBuildSha256": hashlib.sha256(torch.__config__.show().encode("utf-8")).hexdigest(),
            "deterministicAlgorithms": "enabled-warn-only; no bitwise determinism guarantee"}


def contract_inputs(run_id, previous_run_id):
    directory, previous_directory = run_directory(run_id), run_directory(previous_run_id)
    if directory == previous_directory:
        raise ValueError("Previous and new run must differ")
    manifest = read_json(directory / "manifest.json")
    if manifest.get("status") not in {"trained", "evaluating", "evaluation-paused", "evaluated"}:
        raise ValueError("Complete training and freeze checkpoint selection first")
    summary = read_json(directory / "training-summary.json")
    previous_summary = read_json(previous_directory / "training-summary.json")
    if summary.get("runId") != run_id or previous_summary.get("runId") != previous_run_id:
        raise ValueError("Run identity mismatch")
    if summary.get("completedUpdates", 0) < 1 or summary.get("weightEvidence", {}).get("changedTensorCount", 0) < 1:
        raise ValueError("New model has no verified training evidence")
    if manifest["scriptSha256"] != training.sha256(Path(training.__file__)):
        raise ValueError("Original training script changed")
    for item in manifest["datasets"].values():
        if training.sha256(training.local_path(item["path"])) != item["sha256"]:
            raise ValueError("Dataset changed after training")
    protocol = summary["selectedDev"]["generationProtocol"]
    if protocol != summary["baselineDev"]["generationProtocol"]:
        raise ValueError("Training dev comparison used mixed inference protocols")
    args = SimpleNamespace(**manifest["config"], device=protocol["device"])
    paths = {"baseline": training.local_path(manifest["basePath"], training.WORK_ROOT),
             "previous": training.local_path(previous_summary["modelPath"], previous_directory),
             "candidate": training.local_path(summary["modelPath"], directory)}
    weights = {"baseline": training.BASE_WEIGHT_HASH, "previous": previous_summary["trainedWeightFileSha256"],
               "candidate": summary["trainedWeightFileSha256"]}
    models = {name: model_inventory(paths[name], weights[name]) for name in MODEL_NAMES}
    defaults = generation_defaults(paths["baseline"])
    for name in MODEL_NAMES:
        if generation_defaults(paths[name]) != defaults:
            raise ValueError("Model generation defaults differ; matched inference is required")
        # save_pretrained changes JSON formatting and adds serialization
        # metadata. Compare the actual maps/SPM, then use one shared tokenizer.
        if (any(models[name]["files"][key] != models["baseline"]["files"][key] for key in ("source.spm", "target.spm"))
                or any(read_json(paths[name] / key) != read_json(paths["baseline"] / key) for key in ("vocab.json", "target_vocab.json"))):
            raise ValueError("Comparison tokenizer vocabularies differ")
    return directory, manifest, summary, args, paths, models, protocol, defaults


def assemble_identity(run_id, previous_run_id, values, runtime):
    directory, manifest, summary, args, _paths, models, protocol, defaults = values
    return {"version": VERSION, "runId": run_id, "previousRunId": previous_run_id,
            "datasets": manifest["datasets"], "config": manifest["config"], "models": models,
            "selectedStep": summary["selectedStep"], "trainingSummarySha256": training.sha256(directory / "training-summary.json"),
            "previousTrainingSummarySha256": training.sha256(run_directory(previous_run_id) / "training-summary.json"),
            "trainingScriptSha256": training.sha256(Path(training.__file__)), "comparisonScriptSha256": training.sha256(Path(__file__)),
            "generationProtocol": protocol, "generationDefaults": defaults,
            "runtime": runtime, "runtimeSha256": canonical_hash(runtime), "diagnosticPolicy": POLICY, "frozenTrainingGate": manifest["gate"]}


def prepare(run_id, previous_run_id):
    values = contract_inputs(run_id, previous_run_id)
    torch, model_class, tokenizer_class, device, versions = training.stack(values[3])
    if training.generation_protocol(values[3], device) != values[6]:
        raise ValueError("Current runtime/protocol differs from the selected dev comparison")
    runtime = runtime_identity(torch, device, versions, values[3])
    identity = assemble_identity(run_id, previous_run_id, values, runtime)
    return values, (torch, model_class, tokenizer_class, device), identity


def freeze(run_id, previous_run_id):
    values, _stack, identity = prepare(run_id, previous_run_id)
    directory, manifest = values[:2]
    output = directory / "comparison-v4"
    frozen_path = output / "manifest.json"
    if frozen_path.exists():
        frozen = read_json(frozen_path)
        if frozen.get("identity") != identity or frozen.get("identitySha256") != canonical_hash(identity):
            raise ValueError("Existing frozen comparison identity changed")
        training.emit("comparison-freeze-existing", path=training.relative(frozen_path))
        return
    if training.test_ledger(manifest).exists() or (directory / "evaluation-summary.json").exists():
        raise ValueError("Freeze the three-model protocol before final test consumption")
    immutable_json(frozen_path, {"frozenAt": training.now(), "identitySha256": canonical_hash(identity), "identity": identity})
    training.emit("comparison-frozen", path=training.relative(frozen_path), identitySha256=canonical_hash(identity))


def check_test_ledger(manifest, summary, frozen, ledger):
    expected = {"runId": manifest["runId"], "testDataSha256": manifest["datasets"]["test"]["sha256"],
                "modelSha256": summary["trainedWeightFileSha256"]}
    if any(ledger.get(key) != value for key, value in expected.items()) or ledger.get("status") != "complete":
        raise ValueError("Complete the matching train.py final evaluation before adding the previous model")
    # Completion time survives train.py's replacement of its initial ledger.
    if datetime.fromisoformat(ledger["completedAt"]) < datetime.fromisoformat(frozen["frozenAt"]):
        raise ValueError("Comparison was not frozen before test completion")


def compare(run_id, split):
    directory = run_directory(run_id)
    frozen_path = directory / "comparison-v4" / "manifest.json"
    frozen = read_json(frozen_path)
    values, stack, identity = prepare(run_id, frozen["identity"]["previousRunId"])
    if frozen.get("identity") != identity or frozen.get("identitySha256") != canonical_hash(identity):
        raise ValueError("Frozen data/model/code/runtime identity changed; comparison cannot resume")
    directory, manifest, summary, args, paths, models, protocol, _defaults = values
    output = frozen_path.parent
    if split == "test":
        check_test_ledger(manifest, summary, frozen, read_json(training.test_ledger(manifest)))
        evaluation = read_json(directory / "evaluation-summary.json")
        if (evaluation.get("testDataSha256") != manifest["datasets"]["test"]["sha256"]
                or evaluation.get("trainedWeightFileSha256") != models["candidate"]["weightSha256"]):
            raise ValueError("Final evaluation summary identity changed")
    rows = training.read_rows(training.local_path(manifest["datasets"][split]["path"]), split)
    for row in rows:
        validate_annotations(row)
    selected = training.local_path(manifest["selectedCheckpoint"], directory)
    cache_paths = ({"baseline": directory / "baseline-dev.jsonl", "candidate": selected / "dev-predictions.jsonl"}
                   if split == "dev" else {"baseline": directory / "baseline-test.jsonl", "candidate": directory / "finetuned-test.jsonl"})
    predictions = {}
    for name, path in cache_paths.items():
        predictions[name] = read_predictions(path)
        validate_predictions(rows, predictions[name], models[name]["weightSha256"], protocol)
    consumption_path = output / f"{split}-consumption.json"
    expected = {"split": split, "identitySha256": canonical_hash(identity), "dataSha256": manifest["datasets"][split]["sha256"],
                "reusedPredictionFiles": {name: {"path": training.relative(path), "sha256": training.sha256(path)} for name, path in cache_paths.items()}}
    if consumption_path.exists():
        consumption = read_json(consumption_path)
        if any(consumption.get(key) != value for key, value in expected.items()):
            raise ValueError("Comparison consumption record conflicts with frozen inputs")
    else:
        consumption = {**expected, "startedAt": training.now(), "status": "generating-previous"}
        immutable_json(consumption_path, consumption)
    summary_path = output / f"{split}-summary.json"
    if consumption.get("status") == "complete":
        for item in consumption["outputs"].values():
            if training.sha256(training.local_path(item["path"], output)) != item["sha256"]:
                raise ValueError("Completed comparison output changed")
        training.emit("comparison-existing", split=split, summary=training.relative(summary_path))
        return
    torch, model_class, tokenizer_class, device = stack
    tokenizer = tokenizer_class.from_pretrained(paths["baseline"], local_files_only=True)
    training.encode_rows(tokenizer, rows, args.max_length)
    previous_path = output / f"previous-{split}.jsonl"
    if previous_path.exists():
        validate_predictions(rows, read_predictions(previous_path), models["previous"]["weightSha256"], protocol, complete=False)
    model = None
    try:
        # Completed rows are strictly verified and never regenerated on resume.
        cached = read_predictions(previous_path) if previous_path.exists() else []
        if len(cached) == len(rows):
            predictions["previous"] = cached
        else:
            model = training.load_model(model_class, paths["previous"], torch, device)
            predictions["previous"] = training.predict(model, tokenizer, rows, torch, device, args, previous_path, models["previous"]["weightSha256"])
        validate_predictions(rows, predictions["previous"], models["previous"]["weightSha256"], protocol)
    except (training.StopRequested, KeyboardInterrupt):
        training.emit("comparison-paused", split=split, resume="Use the same compare command; completed predictions remain cached")
        return
    finally:
        del model
        gc.collect()
        if device == "xpu":
            torch.xpu.empty_cache()
    reviews, keys = anonymous_review(rows, predictions, canonical_hash(identity))
    review_path, key_path = output / f"{split}-anonymous-review.jsonl", output / f"{split}-review-key.json"
    immutable_jsonl(review_path, reviews)
    immutable_json(key_path, {"identitySha256": canonical_hash(identity), "mapping": keys})
    scores = {name: training.metrics(rows, predictions[name]) for name in MODEL_NAMES}
    result = {"runId": run_id, "previousRunId": identity["previousRunId"], "split": split,
              "identitySha256": canonical_hash(identity), "dataSha256": manifest["datasets"][split]["sha256"],
              "models": models, "runtimeSha256": identity["runtimeSha256"], "generationProtocol": protocol,
              "lexicalMetrics": scores, "diagnostics": {name: diagnostics(rows, predictions[name]) for name in MODEL_NAMES},
              "sourceGroupMetrics": source_group_metrics(rows, predictions),
              "originalGateCandidateVsBase": training.gate(scores["baseline"], scores["candidate"]),
              "originalGateCandidateVsPrevious": training.gate(scores["previous"], scores["candidate"]),
              "semanticAccuracy": None, "humanReviewed": False, "promotionAuthorized": False,
              "anonymousReviewPath": training.relative(review_path), "limitation": POLICY["semantics"]}
    immutable_json(summary_path, result)
    outputs = {"summary": summary_path, "previousPredictions": previous_path, "anonymousReview": review_path, "reviewKey": key_path}
    training.write_json(consumption_path, {**consumption, "status": "complete", "completedAt": training.now(),
                        "outputs": {name: {"path": training.relative(path), "sha256": training.sha256(path)} for name, path in outputs.items()}})
    training.emit("comparison-complete", split=split, summary=training.relative(summary_path), anonymousReview=training.relative(review_path))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("freeze", "compare"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--previous-run-id", default="finance-v3")
    parser.add_argument("--split", choices=("dev", "test"), default="test")
    args = parser.parse_args()
    def stop(_signum, _frame):
        training.STOP_REQUESTED = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    with training.exclusive_run(args.run_id):
        if args.stage == "freeze":
            freeze(args.run_id, args.previous_run_id)
        else:
            compare(args.run_id, args.split)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        training.emit("comparison-failed", errorType=type(error).__name__, message=str(error)[:500])
        sys.exit(1)
