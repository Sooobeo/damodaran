"""Prepare immutable, anonymous assistant review from a completed v5 test cache.

This command never loads a translation model or generates translations. Run it
only after train_v5.py evaluate finishes. References were written by assistants,
not human gold references. Save completed judgments separately from the template.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys

import train_v5 as v5

APP_ROOT = Path(__file__).resolve().parents[2]
VERSION = "finance-v5-anonymous-assistant-review-v1"
RANDOMIZATION_SEED = "finance-v5-two-model-labels-2026-09-10-v1"
OUTPUT_FILES = ("anonymous-review.jsonl", "review-key.json")
LIMITATION = (
    "참조 번역은 도우미 작성·사람 미검수 자료이며 인간 정답이 아닙니다. "
    "원문을 기준으로 의미·조건·부정·역할·다의어·누락·추가·수량·수식을 판단하세요. "
    "용어 표기 적중이나 자연스러움만으로 의미 보존을 판단하지 마세요. "
    "확신할 수 없는 항목은 미해결로 남기고 실제 검토자 유형을 기록하세요."
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path):
    return json.loads(path.read_text("utf-8-sig"))


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text("utf-8-sig").splitlines() if line.strip()]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local_path(root, value, boundary):
    require(isinstance(value, str) and bool(value), "Missing local input path")
    path = root / value
    resolved = path.resolve()
    require(resolved.is_relative_to(boundary.resolve()), "Input path escaped its allowed directory")
    # Reject links at every level, including a linked parent directory.
    require(not any(part.is_symlink() for part in (path, *path.parents)), "Linked inputs are not allowed")
    return resolved


def valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_predictions(rows, predictions, model_hash, protocol):
    require(len(predictions) == len(rows), "Incomplete or extra cached predictions")
    for index, (row, prediction) in enumerate(zip(rows, predictions)):
        expected = {"index": index, "id": row["id"], "sourceHash": text_hash(row["source"]),
                    "modelIdentity": model_hash, "generationProtocol": protocol}
        require(type(prediction.get("index")) is int
                and all(prediction.get(key) == value for key, value in expected.items()),
                "Prediction ID/order/source/model/protocol mismatch")
        require(isinstance(prediction.get("prediction"), str)
                and type(prediction.get("generatedTokens")) is int and prediction["generatedTokens"] >= 0
                and type(prediction.get("atLengthLimit")) is bool,
                "Malformed raw prediction")
        require(prediction["atLengthLimit"] == (prediction["generatedTokens"] >= protocol["maxNewTokens"]),
                "Prediction length metadata differs from the generation protocol")


def collect_inputs(run_id, app_root=APP_ROOT):
    """Validate completion before opening held-out text, then bind every input."""
    require(bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id)), "Invalid run ID")
    root = Path(app_root).resolve()
    work = root / ".training"
    directory = local_path(root, f".training/runs/{run_id}", work / "runs")
    inputs = {}

    def record(path):
        path = local_path(root, str(path), work)
        inputs[path.relative_to(root).as_posix()] = sha256(path)
        return path

    def record_hash(path):
        recorded = record(path)
        return inputs[recorded.relative_to(root).as_posix()]

    manifest = read_json(record(directory / "manifest.json"))
    training = read_json(record(directory / "training-summary.json"))
    evaluation_path = directory / "evaluation-summary.json"
    evaluation = read_json(record(evaluation_path))
    require(all(item.get("runId") == run_id for item in (manifest, training, evaluation)), "Run identity mismatch")
    require(manifest.get("status") == "evaluated", "Final test evaluation is not complete")
    identity = {key: manifest.get(key) for key in v5.IDENTITY_KEYS}
    require(manifest.get("identitySha256") == v5.canonical_hash(identity), "Frozen run identity changed")
    require(manifest.get("scriptVersion") == v5.VERSION
            and manifest.get("scriptSha256") == sha256(Path(v5.__file__))
            and manifest.get("dependencyFiles") == v5.dependency_hashes(), "Training code identity changed")
    require(manifest.get("gate") == v5.POLICY and evaluation.get("gatePolicy") == v5.POLICY,
            "Frozen gate policy changed")
    require(manifest.get("baseModel") == v5.ORIGINAL_MODEL_ID
            and manifest.get("baseRevision") == v5.ORIGINAL_REVISION,
            "Original model identity changed")
    lineage = manifest.get("lineage", {})
    require(lineage.get("parentRunId") == v5.PARENT_RUN_ID
            and lineage.get("parentSelectedStep") == v5.PARENT_SELECTED_STEP
            and lineage.get("parentWeightSha256") == v5.PARENT_WEIGHT_SHA256,
            "Warm-start lineage changed")
    base_hash = v5.PARENT_WEIGHT_SHA256
    candidate_hash = training.get("trainedWeightFileSha256")
    require(valid_hash(candidate_hash) and candidate_hash != base_hash, "Missing changed trained weights")
    for summary in (training, evaluation):
        require(summary.get("baseWeightFileSha256") == base_hash
                and summary.get("trainedWeightFileSha256") == candidate_hash
                and summary.get("glossaryApplied") is False, "Summary model or raw-output identity changed")
    step = training.get("selectedStep")
    updates = training.get("completedUpdates")
    require(type(step) is int and type(updates) is int and 0 < step <= updates
            and manifest.get("completedUpdates") == updates
            and training.get("weightEvidence", {}).get("changedTensorCount", 0) > 0,
            "Missing completed optimizer/checkpoint evidence")
    require(training.get("devGate", {}).get("passed") is True
            and training["devGate"] == evaluation.get("devGate"), "Selected dev gate does not pass or changed")
    require(v5.gate(training["baselineDev"], training["selectedDev"]) == training["devGate"], "Stored dev gate differs from fixed policy")
    require(v5.gate(evaluation["baseline"], evaluation["finetuned"]) == evaluation.get("testGate"), "Stored test gate differs from fixed policy")
    require(evaluation.get("promotionEligible") is evaluation["testGate"]["passed"], "Test eligibility differs from completed gates")
    protocol = training["selectedDev"].get("generationProtocol")
    require(isinstance(protocol, dict) and protocol.get("effectivePrecision") == "fp32"
            and protocol.get("weightDtype") == "fp32" and protocol.get("doSample") is False
            and protocol.get("attentionImplementation") == "eager", "Unrecognized raw FP32 generation protocol")
    require(all(scores.get("generationProtocol") == protocol for scores in
                (training["baselineDev"], evaluation["baseline"], evaluation["finetuned"])), "Generation protocol changed between stages")
    config = manifest["config"]
    require(all(protocol.get(key) == config.get(config_key) for key, config_key in
                (("beams", "beams"), ("maxNewTokens", "max_new_tokens"), ("maxInputTokens", "max_length"))),
            "Generation settings differ from the frozen run")
    require(all(type(protocol.get(key)) is int and protocol[key] > 0 for key in ("beams", "maxNewTokens", "maxInputTokens"))
            and protocol.get("device") in ("cpu", "xpu")
            and isinstance(protocol.get("runtimeVersions"), dict)
            and all(isinstance(protocol["runtimeVersions"].get(name), str) and protocol["runtimeVersions"][name]
                    for name in ("torch", "transformers", "sentencepiece")), "Incomplete generation protocol")

    dataset = manifest.get("datasets", {}).get("test", {})
    test_hash = dataset.get("sha256")
    require(valid_hash(test_hash) and evaluation.get("testDataSha256") == test_hash, "Test dataset identity mismatch")
    ledger = read_json(record(work / "test-evaluations" / (test_hash + ".json")))
    require(all(ledger.get(key) == value for key, value in
                {"runId": run_id, "testDataSha256": test_hash, "modelSha256": candidate_hash, "status": "complete"}.items()),
            "Complete matching final-test ledger is required")
    require(local_path(root, ledger.get("summaryPath"), directory) == evaluation_path, "Ledger summary path mismatch")
    try:
        finished = datetime.fromisoformat(training["finishedAt"])
        evaluated = datetime.fromisoformat(evaluation["evaluatedAt"])
        completed = datetime.fromisoformat(ledger["completedAt"])
        require(all(date.tzinfo is not None for date in (finished, evaluated, completed))
                and finished <= evaluated <= completed, "Invalid completion chronology")
    except (KeyError, TypeError) as error:
        raise ValueError("Missing completion timestamps") from error

    inventory = manifest.get("modelInventory")
    require(isinstance(inventory, dict) and training.get("modelInventory") == inventory
            and manifest.get("modelInventorySha256") == v5.canonical_hash(inventory)
            and training.get("modelInventorySha256") == v5.canonical_hash(inventory), "Runtime inventory changed")
    model = local_path(root, training.get("modelPath"), directory)
    selected = local_path(root, manifest.get("selectedCheckpoint"), directory)
    require(inventory.get("version") == 1 and inventory.get("selectedStep") == step
            and inventory.get("modelPath") == training["modelPath"] == manifest.get("modelPath") == evaluation.get("modelPath")
            and inventory.get("selectedCheckpoint") == manifest["selectedCheckpoint"], "Selected runtime identity changed")
    checkpoint = read_json(record(selected / "checkpoint.json"))
    require(checkpoint.get("step") == step and checkpoint.get("modelSha256") == candidate_hash, "Selected checkpoint metadata changed")
    base = local_path(root, manifest.get("basePath"), work / "runs" / v5.PARENT_RUN_ID)
    require(set(inventory.get("files", {})) == set(v5.MODEL_FILES)
            and set(manifest.get("baseFiles", {})) == set(v5.MODEL_FILES), "Incomplete runtime file inventory")
    for name in v5.MODEL_FILES:
        require(record_hash(model / name) == record_hash(selected / "model" / name) == inventory["files"][name],
                "Selected runtime file changed")
        require(record_hash(base / name) == manifest["baseFiles"][name], "Warm-start runtime file changed")
    require(inventory["files"]["model.safetensors"] == candidate_hash
            and manifest["baseFiles"]["model.safetensors"] == base_hash, "Runtime weight identity mismatch")

    # No held-out text is opened until all completion/model checks above pass.
    data_root = work / "datasets" / "finance-v5"
    publication = manifest.get("datasetPublication", {})
    publication_path = local_path(root, publication.get("manifestPath"), data_root)
    require(publication_path == data_root / "dataset-manifest.json"
            and record_hash(publication_path) == publication.get("manifestSha256"), "Published dataset manifest changed")
    published = read_json(publication_path)
    require(published.get("files", {}).get("test", {}).get("sha256") == test_hash,
            "Published test identity differs from run")
    for name, key in (("term-catalog.json", "catalogSha256"), ("annotation-policy.json", "annotationPolicySha256")):
        require(record_hash(data_root / name) == publication.get(key), "Published annotation policy changed")
    test_path = local_path(root, dataset.get("path"), data_root)
    require(test_path == data_root / "test.jsonl", "Use the final published test file only")
    require(record_hash(test_path) == test_hash, "Frozen test bytes changed")
    rows = read_jsonl(test_path)
    require(bool(rows) and len({row.get("id") for row in rows}) == len(rows), "Missing or duplicate test IDs")
    for row in rows:
        require(isinstance(row.get("id"), str) and row["id"] and row.get("split") == "test"
                and isinstance(row.get("source"), str) and row["source"].strip()
                and isinstance(row.get("target"), str) and row["target"].strip()
                and row.get("sourceSha256") == text_hash(row["source"])
                and row.get("targetSha256") == text_hash(row["target"])
                and row.get("humanReviewed") is False and row.get("domain") in ("finance", "general"),
                "Malformed or changed assistant test reference")
    predictions = {}
    for name, filename, weight in (("baseline", "baseline-test.jsonl", base_hash), ("candidate", "finetuned-test.jsonl", candidate_hash)):
        predictions[name] = read_jsonl(record(directory / filename))
        validate_predictions(rows, predictions[name], weight, protocol)
        scores = evaluation["baseline" if name == "baseline" else "finetuned"]
        require(scores.get("count") == len(rows), "Test summary row count mismatch")
    for name in ("prepare_v5_review.py", "train_v5.py"):
        inputs["scripts/model-training/" + name] = sha256(Path(__file__).with_name(name))
    inputs.update(manifest["dependencyFiles"])
    return directory, rows, predictions, {"runId": run_id, "runIdentitySha256": manifest["identitySha256"],
                                         "selectedStep": step, "generationProtocol": protocol, "files": inputs}


def anonymous_review(rows, predictions, salt):
    reviews, keys = [], []
    for index, row in enumerate(rows):
        order = ["baseline", "candidate"]
        random.Random(v5.canonical_hash([RANDOMIZATION_SEED, salt, row["id"], row["sourceSha256"]])).shuffle(order)
        choices = [{"label": label, "translation": predictions[name][index]["prediction"],
                    "review": {"severity": None, "meaningPreserved": None, "negationAndConditionsPreserved": None,
                               "contextualWordSensePreserved": None, "omission": None, "unsupportedAddition": None,
                               "quantityOrFormulaError": None, "fluency": None, "evidence": ""}}
                   for label, name in zip("AB", order)]
        reviews.append({"id": row["id"], "source": row["source"], "sourceSha256": row["sourceSha256"],
                        "assistantReference": row["target"], "referenceProvenance": row.get("provenance"),
                        "referenceReviewStatus": row.get("reviewStatus"), "referenceHumanReviewed": False,
                        "domain": row["domain"], "primaryTermId": row.get("primaryTermId"),
                        "forbiddenTerms": row.get("forbiddenTerms", []), "criticalChecks": row.get("criticalChecks", []),
                        "protectedSymbols": row.get("protectedSymbols", []), "unitChecks": row.get("unitChecks", []),
                        "choices": choices, "reviewerType": None, "humanReviewed": False, "note": LIMITATION})
        keys.append({"id": row["id"], "labels": dict(zip("AB", order))})
    return reviews, keys


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def prepare(run_id, app_root=APP_ROOT):
    directory, rows, predictions, inputs = collect_inputs(run_id, app_root)
    output = directory / "assistant-review-v5"
    identity = {"version": VERSION, "randomizationSeed": RANDOMIZATION_SEED, "inputs": inputs}
    identity_hash = v5.canonical_hash(identity)
    reviews, keys = anonymous_review(rows, predictions, identity_hash)
    contents = {"anonymous-review.jsonl": "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reviews).encode("utf-8"),
                "review-key.json": json_bytes({"version": VERSION, "mappings": keys})}
    expected_hashes = {name: hashlib.sha256(content).hexdigest() for name, content in contents.items()}
    # Bind bytes read earlier even if a file was replaced during preparation.
    for relative, expected in inputs["files"].items():
        require(sha256(Path(app_root) / relative) == expected, "Review input changed during preparation")
    if output.exists():
        require(not output.is_symlink(), "Linked output directory is not allowed")
        manifest = read_json(output / "manifest.json")
        require(manifest.get("status") == "complete" and manifest.get("identity") == identity
                and manifest.get("identitySha256") == identity_hash and manifest.get("rowCount") == len(rows),
                "Existing review input identity changed; overwriting is prohibited")
        require(manifest.get("files") == expected_hashes, "Existing review manifest outputs changed")
        for name in OUTPUT_FILES:
            require(not (output / name).is_symlink() and sha256(output / name) == manifest["files"][name],
                    "Existing immutable review output changed")
        return output / "manifest.json"
    # A completed directory appears atomically. A leftover pending directory is
    # preserved for inspection; an interrupted preparation never overwrites it.
    staging = directory / "assistant-review-v5.pending"
    staging.mkdir(exist_ok=False)
    for name, content in contents.items():
        with (staging / name).open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    manifest = {"version": VERSION, "status": "complete", "createdAt": datetime.now(timezone.utc).isoformat(),
                "identity": identity, "identitySha256": identity_hash, "rowCount": len(rows),
                "files": expected_hashes,
                "inferencePerformed": False, "glossaryApplied": False, "humanReviewed": False,
                "severityScale": {"0": "의미 오류 없음", "1": "핵심 의미를 바꾸지 않는 경미한 문제",
                                  "2": "실질적인 의미·역할·조건·수량 오류", "3": "핵심 결론 반전 또는 광범위한 누락·추가"},
                "limitation": LIMITATION,
                "reviewInstructions": "익명 검토 시 review-key.json을 열지 마세요. 템플릿을 덮어쓰지 않고 별도 파일에 검토 결과를 저장하세요."}
    with (staging / "manifest.json").open("xb") as stream:
        stream.write(json_bytes(manifest))
        stream.flush()
        os.fsync(stream.fileno())
    require(not output.exists(), "Another review preparation completed concurrently; preserved pending files")
    staging.rename(output)
    return output / "manifest.json"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    path = prepare(args.run_id)
    print(json.dumps({"event": "v5-assistant-review-ready", "manifest": str(path), "manifestSha256": sha256(path),
                      "inferencePerformed": False, "humanReviewed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
