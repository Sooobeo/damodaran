"""Run a separate, non-tuning word-sense diagnostic after sealed final evaluation."""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path
import signal

import evaluate_v4 as comparison
import train


def verify_final_comparison(directory, frozen, consumed, manifest):
    expected = {"status": "complete", "split": "test", "identitySha256": frozen["identitySha256"],
                "dataSha256": manifest["datasets"]["test"]["sha256"]}
    if any(consumed.get(key) != value for key, value in expected.items()):
        raise ValueError("Final comparison consumption identity does not match this frozen test")
    reused = consumed.get("reusedPredictionFiles", {})
    outputs = consumed.get("outputs", {})
    expected_reused = {"baseline": directory / "baseline-test.jsonl", "candidate": directory / "finetuned-test.jsonl"}
    expected_outputs = {"summary": directory / "comparison-v4/test-summary.json",
                        "previousPredictions": directory / "comparison-v4/previous-test.jsonl",
                        "anonymousReview": directory / "comparison-v4/test-anonymous-review.jsonl",
                        "reviewKey": directory / "comparison-v4/test-review-key.json"}
    for files, expected_paths in ((reused, expected_reused), (outputs, expected_outputs)):
        if not isinstance(files, dict) or set(files) != set(expected_paths):
            raise ValueError("Final comparison file inventory is incomplete")
        for name, expected_path in expected_paths.items():
            item = files[name]
            path = train.local_path(item["path"], directory)
            if path != expected_path.resolve() or train.sha256(path) != item["sha256"]:
                raise ValueError("Completed final comparison output changed")


def run(run_id, data_file):
    directory = comparison.run_directory(run_id)
    frozen = comparison.read_json(directory / "comparison-v4/manifest.json")
    consumed = comparison.read_json(directory / "comparison-v4/test-consumption.json")
    if consumed.get("status") != "complete":
        raise ValueError("Complete the sealed three-model final comparison before a diagnostic probe")
    values, stack, identity = comparison.prepare(run_id, frozen["identity"]["previousRunId"])
    if identity != frozen["identity"] or comparison.canonical_hash(identity) != frozen["identitySha256"]:
        raise ValueError("The sealed comparison identity changed")
    _directory, manifest, summary, args, model_paths, models, protocol, _defaults = values
    comparison.check_test_ledger(manifest, summary, frozen, comparison.read_json(train.test_ledger(manifest)))
    verify_final_comparison(directory, frozen, consumed, manifest)
    path = train.local_path(data_file, train.WORK_ROOT / "datasets")
    rows = train.read_rows(path, "diagnostic")
    for row in rows:
        comparison.validate_annotations(row)
        if (row.get("humanReviewed") is not False
                or any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("focusWord", "expectedSense"))
                or not isinstance(row.get("forbiddenSenseExplanation", ""), str)):
            raise ValueError(f"Missing honest word-sense reference metadata: {row['id']}")
    # This is not a new checkpoint-selection set or a replacement final test.
    output = directory / "word-sense"
    probe_identity = {"version": 1, "runId": run_id, "frozenComparisonSha256": frozen["identitySha256"],
                      "dataPath": train.relative(path), "dataSha256": train.sha256(path),
                      "scriptSha256": train.sha256(Path(__file__)), "models": models, "generationProtocol": protocol,
                      "purpose": "Paired contextual-sense diagnostic after model selection/final evaluation; never used to tune or authorize promotion."}
    comparison.immutable_json(output / "manifest.json", probe_identity)
    complete_path = output / "completion.json"
    if complete_path.exists():
        complete = comparison.read_json(complete_path)
        if complete["identitySha256"] != comparison.canonical_hash(probe_identity):
            raise ValueError("Word-sense completion identity mismatch")
        if set(complete.get("files", {})) != {*(f"predictions:{name}" for name in comparison.MODEL_NAMES), "review", "key", "summary"}:
            raise ValueError("Word-sense completion file inventory is incomplete")
        for item in complete["files"].values():
            if train.sha256(train.local_path(item["path"], output)) != item["sha256"]:
                raise ValueError("Completed word-sense output changed")
        train.emit("word-sense-existing", path=train.relative(complete_path))
        return
    torch, model_class, tokenizer_class, device = stack
    tokenizer = tokenizer_class.from_pretrained(model_paths["baseline"], local_files_only=True)
    train.encode_rows(tokenizer, rows, args.max_length)
    predictions, files = {}, {}
    for name in comparison.MODEL_NAMES:
        cache = output / f"{name}.jsonl"
        if cache.exists():
            comparison.validate_predictions(rows, comparison.read_predictions(cache), models[name]["weightSha256"], protocol, complete=False)
        cached = comparison.read_predictions(cache) if cache.exists() else []
        model = None
        try:
            if len(cached) == len(rows):
                predictions[name] = cached
            else:
                model = train.load_model(model_class, model_paths[name], torch, device)
                predictions[name] = train.predict(model, tokenizer, rows, torch, device, args, cache, models[name]["weightSha256"])
        finally:
            del model
            gc.collect()
            if device == "xpu":
                torch.xpu.empty_cache()
        comparison.validate_predictions(rows, predictions[name], models[name]["weightSha256"], protocol)
        files[f"predictions:{name}"] = cache
    reviews, keys = comparison.anonymous_review(rows, predictions, comparison.canonical_hash(probe_identity))
    for row, review in zip(rows, reviews):
        review.update({"focusWord": row["focusWord"], "expectedSense": row["expectedSense"],
                       "forbiddenSenseExplanation": row.get("forbiddenSenseExplanation", ""), "domain": row["domain"]})
    review_path, key_path, summary_path = output / "anonymous-review.jsonl", output / "review-key.json", output / "summary.json"
    comparison.immutable_jsonl(review_path, reviews)
    comparison.immutable_json(key_path, {"mapping": keys, "identitySha256": comparison.canonical_hash(probe_identity)})
    comparison.immutable_json(summary_path, {"identity": probe_identity, "count": len(rows),
                                           "focusWords": sorted({row["focusWord"] for row in rows}),
                                           "diagnostics": {name: comparison.diagnostics(rows, predictions[name]) for name in comparison.MODEL_NAMES},
                                           "semanticAccuracy": None, "promotionAuthorized": False,
                                           "note": "Assistant-authored references and contextual assistant review are not human certification. No weights or gates are changed."})
    files.update({"review": review_path, "key": key_path, "summary": summary_path})
    comparison.immutable_json(complete_path, {"completedAt": train.now(), "identitySha256": comparison.canonical_hash(probe_identity),
                                             "files": {name: {"path": train.relative(file), "sha256": train.sha256(file)} for name, file in files.items()}})
    train.emit("word-sense-complete", count=len(rows), path=train.relative(summary_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--data-file", required=True)
    args = parser.parse_args()
    def stop(_signum, _frame):
        train.STOP_REQUESTED = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    with train.exclusive_run(args.run_id):
        try:
            run(args.run_id, args.data_file)
        except (train.StopRequested, KeyboardInterrupt):
            train.emit("word-sense-paused", resume="Same command resumes verified completed rows without regeneration")


if __name__ == "__main__":
    main()
