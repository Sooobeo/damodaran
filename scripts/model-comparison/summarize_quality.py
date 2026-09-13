"""Publish lexical diagnostics and blinded review for four completed Q26 systems.

No inference, weight loading, training, deployment, or quality gate. The 24
assistant-authored passages are an exploratory system comparison, not v5 test.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/model-training"))
import train
import code_snapshot
import jinja2
import run_hymt as hy
import sacrebleu
from sacrebleu.metrics import BLEU, CHRF

VERSION = "finance-quality-four-system-review-v1"
SEED = "finance-quality-20260910-four-labels-v1"
SYSTEMS = ("argos-app", "marian-v5", "hymt-raw", "hymt-contextual")
OUTPUT_FILES = ("comparison-metrics.json", "anonymous-review.jsonl", "review-key.json")
ARGOS_CODE = ("scripts/model-comparison/run_app_baseline.ts", "lib/translation/index.ts",
              "lib/translation/local.ts", "lib/translation/runtime.ts", "lib/translation/glossary.ts",
              "scripts/local-translation/bridge.py", "scripts/local-translation/runtime.py", "content/index.ts",
              "content/translation-glossary.json", "package-lock.json", "lib/translation/memory.ts", "lib/config.ts",
              "lib/db/index.ts", "lib/sources/index.ts")
MARIAN_CODE = ("scripts/model-comparison/run_quality_marian.py", "scripts/model-comparison/verify_quality_v5.py", "scripts/model-training/infer_v5.py",
               "scripts/model-training/infer.py", "scripts/model-training/train_v5.py", "scripts/model-training/train.py",
               "scripts/model-training/dataset_v5.py", "scripts/model-training/dataset_io.py", "scripts/model-training/assemble_v5_dataset.py")
MARIAN_RUNTIME_FILES = {"config.json", "generation_config.json", "source.spm", "target.spm", "vocab.json",
                        "target_vocab.json", "tokenizer_config.json", "special_tokens_map.json"}
LIMITATION = (
    "새 24개 장문을 이용한 탐색적 시스템 비교입니다. FINANCE_V5 최종 시험 점수나 54개 용어의 90% 기준과 다릅니다. "
    "참조 번역과 검토 항목은 도우미 작성·사람 미검수 자료이며 인간 정답이 아닙니다. "
    "termTargets는 프롬프트 사전 밖 표현도 포함하는 참고 문자열 적중입니다. 의미·부정·조건·역할·다의어·누락·추가·수량·수식은 "
    "원문과 문맥을 대조해 별도로 검토해야 합니다. 어떤 지표도 자동 통과·앱 적용을 승인하지 않습니다."
)
GROUPS = {
    "argos-app": {"sourceInput": True, "contextUsedForTermSelection": True, "contextPassedToModel": False,
                  "conditionalGlossaryHintsApplied": False, "glossaryPostProcessingApplied": True,
                  "numberFormulaProtectionApplied": True, "translationMemoryApplied": False},
    "marian-v5": {"sourceInput": True, "contextPassedToModel": False, "conditionalGlossaryHintsApplied": False,
                  "glossaryPostProcessingApplied": False, "numberFormulaProtectionApplied": False, "translationMemoryApplied": False},
    "hymt-raw": {"sourceInput": True, "contextPassedToModel": False, "conditionalGlossaryHintsApplied": False,
                 "glossaryPostProcessingApplied": False, "numberFormulaProtectionApplied": False, "translationMemoryApplied": False},
    "hymt-contextual": {"sourceInput": True, "contextPassedToModel": True, "conditionalGlossaryHintsApplied": True,
                        "glossaryPostProcessingApplied": False, "numberFormulaProtectionApplied": False, "translationMemoryApplied": False},
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def canonical_hash(value):
    return sha_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def parse(raw):
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda value: require(False, "Non-finite JSON value"))


def local(path, root):
    path = Path(root) / path
    resolved = path.resolve()
    boundary = (Path(root) / ".training/comparisons").resolve()
    require(resolved.is_relative_to(boundary) and resolved != boundary and
            not any(item.is_symlink() for item in (path, *path.parents)), "Comparison path escaped its boundary")
    return resolved


_UNSPECIFIED_HASH = object()


class Inputs:
    def __init__(self):
        self.files = {}

    def record(self, path, expected=_UNSPECIFIED_HASH):
        path = Path(path)
        require(not any(item.is_symlink() for item in (path, *path.parents)), "Linked input file is prohibited")
        path = path.resolve()
        require(path.is_file(), "Missing comparison input file")
        observed = digest(path)
        require(expected is _UNSPECIFIED_HASH or valid_sha(expected) and observed == expected, "Input file SHA256 changed")
        key = str(path)
        require(key not in self.files or self.files[key] == observed, "Input changed while collecting evidence")
        self.files[key] = observed
        return observed

    def read(self, path, *, jsonl=False):
        path = Path(path)
        require(not any(item.is_symlink() for item in (path, *path.parents)), "Linked input file is prohibited")
        path = path.resolve()
        require(path.stat().st_size <= 32 * 1024 * 1024, "Comparison metadata exceeds byte limit")
        raw = path.read_bytes()
        observed = hashlib.sha256(raw).hexdigest()
        self.record(path, observed)
        if jsonl:
            return [parse(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        return parse(raw)

    def unchanged(self):
        for path, expected in self.files.items():
            require(Path(path).is_file() and digest(path) == expected, "Input changed during publication")


def read_dataset(path, inputs):
    publication = inputs.read(path.with_name("dataset-manifest.json"))
    rows = inputs.read(path, jsonl=True)
    require(isinstance(publication, dict) and publication.get("version") == hy.DATA_VERSION and
            publication.get("status") == "frozen" and publication.get("humanReviewed") is False and
            publication.get("sourceType") == "assistant_authored_unreviewed", "Dataset is not the frozen quality probe")
    ids = [f"Q26-{i:03d}" for i in range(1, 25)]
    require(isinstance(rows, list) and len(rows) == 24 and all(isinstance(row, dict) for row in rows) and
            [row.get("id") for row in rows] == ids and publication.get("dataset") ==
            {"file": path.name, "sha256": inputs.files[str(path)], "count": 24, "ids": ids}, "Dataset coverage or SHA differs")
    for row in rows:
        require(row.get("domain") in ("finance", "general") and row.get("split") == "exploratory_probe" and
                row.get("humanReviewed") is False and row.get("provenance") == "assistant_authored_unreviewed", "Dataset provenance differs")
        for field in ("source", "target", "context"):
            value = row.get(field)
            require(isinstance(value, str) and len(value) <= 12000 and (bool(value.strip()) or field == "context") and
                    row.get(field + "Sha256") == sha_text(value), "Dataset text hash differs")
        for field in ("termTargets", "forbiddenTerms", "protectedSymbols", "criticalChecks"):
            require(isinstance(row.get(field), list) and all(isinstance(s, str) and s.strip() for s in row[field]) and
                    len(set(row[field])) == len(row[field]), "Malformed quality annotations")
        require(bool(row["criticalChecks"]) and isinstance(row.get("unitChecks"), list), "Missing semantic review checks")
        require(all(symbol in row["source"] and symbol in row["target"] for symbol in row["protectedSymbols"]), "Protected reference symbol differs")
        for check in row["unitChecks"]:
            require(isinstance(check, dict) and all(isinstance(check.get(k), str) and check[k].strip()
                    for k in ("source", "target", "meaning")) and check["source"] in row["source"] and
                    check["target"] in row["target"], "Unit reference anchors differ")
    require(sum(row["domain"] == "finance" for row in rows) == 16 and sum(bool(row["context"]) for row in rows) == 8,
            "Quality domain/context coverage differs")
    reviews = publication.get("reviewFiles")
    require(isinstance(reviews, list) and reviews, "Missing dataset review evidence")
    paths = []
    for item in reviews:
        name = item.get("file") if isinstance(item, dict) else None
        require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name) and
                name not in paths and not (path.parent / name).is_symlink(), "Invalid dataset review evidence path")
        paths.append(name)
        inputs.record(path.parent / name, item.get("sha256"))
    # historicalFiles attest the earlier publication. Never open historical
    # train/dev/test files or model weights to summarize this new comparison.
    return rows, publication


def verify_code(mapping, expected_paths, inputs):
    require(isinstance(mapping, dict) and set(mapping) == set(expected_paths)
            and all(valid_sha(value) for value in mapping.values()), "Producer code inventory differs")
    for key, path in expected_paths.items():
        inputs.record(path, mapping[key])


def hymt_code_paths(root):
    paths = [Path(root) / "scripts/model-comparison/run_hymt.py", Path(root) / "scripts/model-comparison/setup_hymt.py",
             Path(sys.executable).resolve(), *sorted(Path(jinja2.__file__).resolve().parent.rglob("*.py"))]
    return {str(path.resolve()): path for path in paths}


def scalar_code_paths():
    return [Path(__file__), Path(code_snapshot.__file__), Path(train.__file__), Path(sys.executable),
            *sorted(Path(sacrebleu.__file__).resolve().parent.rglob("*.py"))]


def check_common(rows, predictions, input_sha):
    require(isinstance(predictions, list) and len(predictions) == 24 and all(isinstance(p, dict) for p in predictions) and
            [p.get("id") for p in predictions] == [r["id"] for r in rows], "Prediction ID/order/coverage differs")
    for row, item in zip(rows, predictions):
        require(item.get("sourceSha256") == row["sourceSha256"] and item.get("contextSha256") == row["contextSha256"] and
                ("inputSha256" not in item or item["inputSha256"] == input_sha), "Prediction source/context/input identity differs")
        require(isinstance(item.get("translation"), str) and len(item["translation"]) <= 64000 and
                finite(item.get("generationSeconds")) and type(item.get("outputLimitReached")) is bool,
                "Malformed prediction text, time or output limit")


def read_standard(name, directory, rows, input_sha, manifest_sha, run_id, root, inputs, review_files):
    start, summary = inputs.read(directory / "start.json"), inputs.read(directory / "summary.json")
    predictions = inputs.read(directory / "predictions.jsonl", jsonl=True)
    require(type(start.get("version")) is int and start["version"] == 1 and start.get("inputSha256") == input_sha and
            start.get("datasetManifestSha256") == manifest_sha and summary.get("status") == "complete" and
            summary.get("inputSha256") == input_sha and summary.get("resultSha256") == inputs.files[str(directory / "predictions.jsonl")] and
            integer(summary.get("count"), 24, 24) and summary.get("errorType") is None and summary.get("integrityVerified") is True and
            finite(summary.get("totalSeconds")) and start.get("reviewFiles") == review_files and
            start.get("sourceType") == "assistant_authored_unreviewed",
            "Producer is incomplete or input/result SHA differs")
    for metadata in (start, summary):
        require(metadata.get("humanReviewed") is False and metadata.get("appDeploymentPerformed") is False and
                type(metadata.get("paidCalls")) is int and metadata["paidCalls"] == 0, "Producer scope differs")
    code = ARGOS_CODE if name == "argos-app" else MARIAN_CODE
    mapping = start.get("codeHashes")
    require(isinstance(mapping, dict) and set(mapping) == set(code)
            and all(valid_sha(value) for value in mapping.values()), "Producer code inventory differs")
    historical = code_snapshot.snapshot_paths(directory, mapping, inputs.files[str(directory / "start.json")],
                                               inputs.files[str(directory / "summary.json")], inputs)
    verify_code(mapping, historical or {key: Path(root) / key for key in code}, inputs)
    check_common(rows, predictions, input_sha)
    if name == "argos-app":
        expected = {"profile": "argos-app-pipeline", "inputCount": 24, "glossaryApplied": True,
                    "numberFormulaProtectionApplied": True, "translationMemoryApplied": False,
                    "contextCollected": True, "contextPassedToModel": False, "sourceType": "assistant_authored_unreviewed"}
        require(all(type(start.get(k)) is type(v) and start[k] == v for k, v in expected.items()) and
                summary.get("generatedCount") == 24 and summary.get("databaseOpened") is False and
                summary.get("translationMemoryApplied") is False, "Argos application profile differs")
        runtime = start.get("runtime", {})
        require(runtime.get("provider") == "argos" and runtime.get("configured") is True and runtime.get("local") is True and
                isinstance(runtime.get("identity"), str) and runtime["identity"] and isinstance(runtime.get("model"), str),
                "Argos runtime identity differs")
        for item in predictions:
            require(item.get("status") == "generated" and item.get("inputSha256") == input_sha and
                    item.get("modelIdentity") == runtime["identity"] and item.get("inputTokens") is None and
                    item.get("generatedTokens") is None and item["outputLimitReached"] is False and
                    item.get("emptyOutput") is (not item["translation"].strip()), "Argos prediction identity differs")
        identity = {"model": runtime["model"], "runtimeIdentity": runtime["identity"], "profile": start["profile"]}
    else:
        settings = start.get("settings", {})
        require(start.get("runId") == run_id and valid_sha(start.get("modelSha256")) and
                summary.get("modelSha256") == start["modelSha256"] and integer(summary.get("expectedCount"), 24, 24) and
                integer(summary.get("recordedCount"), 24, 24) and summary.get("failedRowId") is None and
                integer(start.get("selectedStep"), 1, 10000000) and isinstance(start.get("modelFiles"), dict) and
                set(start["modelFiles"]) == MARIAN_RUNTIME_FILES and all(valid_sha(value) for value in start["modelFiles"].values()), "Marian model identity differs")
        expected = {"device": "cpu", "precision": "fp32", "threads": 4, "seed": 20260910, "doSample": False,
                    "useCache": True, "attentionImplementation": "eager", "glossaryApplied": False,
                    "contextPassedToModel": False, "translationMemoryApplied": False, "numberFormulaProtectionApplied": False}
        require(all(type(settings.get(k)) is type(v) and settings[k] == v for k, v in expected.items()) and
                integer(settings.get("maxInputTokens"), 1, 12000) and integer(settings.get("maxNewTokens"), 1, 64000) and
                integer(settings.get("beams"), 1, 100), "Marian generation settings differ")
        for item in predictions:
            require(item.get("status") == "generated" and item.get("inputSha256") == input_sha and
                    item.get("modelSha256") == start["modelSha256"] and integer(item.get("inputTokens"), 1, settings["maxInputTokens"]) and
                    integer(item.get("generatedTokens"), 0, settings["maxNewTokens"]) and
                    item["outputLimitReached"] == (item["generatedTokens"] >= settings["maxNewTokens"]) and
                    item.get("emptyOutput") is (not item["translation"].strip()), "Marian prediction limit or model identity differs")
        identity = {"runId": run_id, "modelSha256": start["modelSha256"], "modelFiles": start["modelFiles"],
                    "selectedStep": start["selectedStep"], "settings": settings}
    return predictions, identity, summary


def read_hymt(name, directory, rows, input_path, publication, root, inputs):
    prepared, summary = inputs.read(directory / "run.json"), inputs.read(directory / "summary.json")
    predictions = inputs.read(directory / "predictions.jsonl", jsonl=True)
    profile = name.removeprefix("hymt-")
    require(prepared.get("status") == "prepared" and summary.get("status") == "completed" and
            all(summary.get(k) == v for k, v in prepared.items() if k not in ("status", "completionRequestsSent")) and
            integer(prepared.get("completionRequestsSent"), 0, 0) and all(integer(summary.get(field), 24, 24)
            for field in ("completionRequestsSent", "expectedCount", "recordCount", "completedCount")) and
            summary.get("failure") is None and summary.get("childProcessStopped") is True and
            summary.get("inferenceRequested") is True and finite(summary.get("elapsedSeconds")), "Hy-MT execution is incomplete or changed")
    expected = {"version": 1, "profile": profile, "model": "tencent/Hy-MT2-7B-GGUF", "revision": hy.REVISION,
                "modelSha256": hy.MODEL["sha256"], "precision": "publisher Q8_0", "checkpoint": None,
                "trainingPerformed": False, "translationMemoryApplied": False, "postProcessingApplied": False,
                "appDeploymentPerformed": False, "humanReviewed": False, "sampling": hy.SAMPLING,
                "contextSize": hy.CONTEXT_SIZE, "conditionalGlossaryHintsApplied": profile == "contextual",
                "inputFieldsUsed": ["source"] + (["context"] if profile == "contextual" else []), "cpuOnly": True}
    require(all(type(summary.get(k)) is type(v) and summary[k] == v for k, v in expected.items()), "Hy-MT profile/model identity differs")
    require(integer(summary.get("actualContextSize"), hy.CONTEXT_SIZE, hy.CONTEXT_SIZE), "Hy-MT actual context limit differs")
    require(summary.get("predictionsSha256") == inputs.files[str(directory / "predictions.jsonl")], "Hy-MT prediction file SHA differs")
    info, catalog = summary.get("input", {}), summary.get("catalog", {})
    manifest_path = input_path.with_name("dataset-manifest.json")
    review_evidence = [{"path": str(input_path.parent / item["file"]), "sha256": item["sha256"]} for item in publication["reviewFiles"]]
    require(info == {"path": str(input_path), "sha256": inputs.files[str(input_path)], "manifestPath": str(manifest_path),
            "manifestSha256": inputs.files[str(manifest_path)], "version": hy.DATA_VERSION, "count": 24,
            "ids": [row["id"] for row in rows], "reviewEvidence": review_evidence}, "Hy-MT input identity differs")
    catalog_path = Path(root) / ".training/datasets/finance-v5/term-catalog.json"
    require(catalog.get("path") == str(catalog_path) and catalog.get("count") == 54, "Hy-MT catalog identity differs")
    inputs.record(catalog_path, catalog.get("sha256"))
    code_paths = hymt_code_paths(root)
    verify_code(summary.get("initialCodeHashes"), code_paths, inputs)
    install_path = Path(root) / ".training/comparisons/hy-mt2-7b-q8/installation-manifest.json"
    all_paths = code_paths | {str(p): p for p in (input_path, manifest_path, catalog_path, install_path)}
    all_paths.update({item["path"]: Path(item["path"]) for item in review_evidence})
    verify_code(summary.get("codeAndInputHashes"), all_paths, inputs)
    installation = inputs.read(install_path)
    require(summary.get("installation") == installation and installation.get("model") == hy.MODEL,
            "Hy-MT installation identity differs")
    inputs.record(Path(root) / ".training/comparisons/hy-mt2-7b-q8/runtime/llama-server.exe", summary.get("runtimeExeSha256"))
    inputs.record(directory / "runtime.log", summary.get("runtimeLogSha256"))
    check_common(rows, predictions, info["sha256"])
    prompt_hashes = summary.get("promptHashes")
    require(isinstance(prompt_hashes, list) and [p.get("id") for p in prompt_hashes] == [row["id"] for row in rows], "Hy-MT prompt coverage differs")
    for item, prompt in zip(predictions, prompt_hashes):
        require(item.get("status") == "completed" and item.get("humanReviewed") is False and item.get("postProcessingApplied") is False and
                item.get("targetSha256") == sha_text(item["translation"]) and valid_sha(item.get("promptSha256")) and
                item["promptSha256"] == prompt.get("sha256") and valid_sha(item.get("inputTokenIdsSha256")) and
                integer(item.get("inputTokens"), 1, hy.CONTEXT_SIZE - hy.MAX_NEW_TOKENS - 1) and
                integer(item.get("generatedTokens"), 0, hy.MAX_NEW_TOKENS) and type(item.get("truncated")) is bool,
                "Hy-MT prediction identity or input limit differs")
        tokens = item.get("outputTokenIds")
        require(isinstance(tokens, list) and all(integer(t, 0, 128166) for t in tokens) and len(tokens) == item["generatedTokens"] and
                hy.sha_json(tokens) == item.get("outputTokenIdsSha256"), "Hy-MT output token evidence differs")
        stop = item.get("stopType")
        require(stop in ("eos", "word", "limit") and (stop != "eos" or tokens and tokens[-1] in (127957, 127960, 127967)) and
                (stop != "word" or item.get("stoppingWord") in hy.SAMPLING["stop"]) and
                item["outputLimitReached"] == (stop == "limit" or item["generatedTokens"] >= hy.MAX_NEW_TOKENS), "Hy-MT stop/output limit differs")
        hy.validate_generation_settings(item.get("actualGenerationSettings"))
        require(isinstance(item.get("conditionalTermMatches"), list) and
                (profile != "raw" or item["conditionalTermMatches"] == []), "Hy-MT glossary profile differs")
    identity = {key: summary[key] for key in ("model", "revision", "modelSha256", "profile", "sampling", "contextSize", "runtimeExeSha256")}
    return predictions, identity, summary


def metrics(rows, predictions, identities):
    output = {"version": VERSION, "rowCount": 24, "humanReviewed": False, "qualityGateApplied": False,
              "purpose": "new 24-passage exploratory system comparison", "limitation": LIMITATION,
              "numericMatcher": "frozen train.numeric_tokens; independently recalculated for every system",
              "systems": {}}
    chrf, bleu = CHRF(), BLEU(tokenize="none", effective_order=True)
    for name in SYSTEMS:
        values = predictions[name]
        details = []
        for row, item in zip(rows, values):
            text = item["translation"]
            details.append({"id": row["id"], "sourceSha256": row["sourceSha256"], "translationSha256": sha_text(text),
                            "numericTokensMatch": train.numeric_tokens(row["source"]) == train.numeric_tokens(text),
                            "explicitNumericSource": bool(train.numeric_tokens(row["source"])),
                            "termHits": sum(term in text for term in row["termTargets"]), "termTargets": len(row["termTargets"]),
                            "forbiddenStringHits": [term for term in row["forbiddenTerms"] if term in text],
                            "empty": not text.strip(), "outputLimitReached": item["outputLimitReached"],
                            "inputTruncated": item.get("truncated", False), "generationSeconds": item["generationSeconds"]})
        times = [row["generationSeconds"] for row in details]
        entry = {"identity": identities[name], "grouping": GROUPS[name], "count": 24,
                 "generationTotalSeconds": sum(times), "generationMedianSeconds": statistics.median(times),
                 "generationMinSeconds": min(times), "generationMaxSeconds": max(times),
                 "outputLimitUnknownCount": 24 if name == "argos-app" else 0, "rows": details, "domains": {}}
        for domain in ("all", "finance", "general"):
            indexes = [i for i, row in enumerate(rows) if domain == "all" or row["domain"] == domain]
            selected = [details[i] for i in indexes]
            hypotheses, references = [values[i]["translation"] for i in indexes], [rows[i]["target"] for i in indexes]
            entry["domains"][domain] = {"count": len(indexes), "chrF": chrf.corpus_score(hypotheses, [references]).score,
                "BLEU": bleu.corpus_score(hypotheses, [references]).score,
                "termHits": sum(d["termHits"] for d in selected), "termTargets": sum(d["termTargets"] for d in selected),
                "numericTokensMatchCount": sum(d["numericTokensMatch"] for d in selected),
                "explicitNumericSourceCount": sum(d["explicitNumericSource"] for d in selected),
                "explicitNumericMatchCount": sum(d["explicitNumericSource"] and d["numericTokensMatch"] for d in selected),
                "emptyCount": sum(d["empty"] for d in selected), "outputLimitReachedCount": sum(d["outputLimitReached"] for d in selected),
                "inputTruncatedCount": sum(d["inputTruncated"] for d in selected),
                "forbiddenStringHitRows": sum(bool(d["forbiddenStringHits"]) for d in selected)}
        output["systems"][name] = entry
    output["metricSignatures"] = {"chrF": str(chrf.get_signature()), "BLEU": str(bleu.get_signature()), "sacrebleuVersion": sacrebleu.__version__}
    return output


def blinded(rows, predictions, identity_sha, input_sha):
    reviews, keys = [], []
    for row in rows:
        names = list(SYSTEMS)
        random.Random(sha_text(SEED + "\n" + identity_sha + "\n" + row["id"])).shuffle(names)
        choices = []
        for label, name in zip("ABCD", names):
            text = predictions[name][len(reviews)]["translation"]
            choices.append({"label": label, "translation": text, "translationSha256": sha_text(text), "review": {
                "severity": None, "meaningPreserved": None, "negationAndConditionsPreserved": None,
                "contextualWordSensePreserved": None, "omission": None, "unsupportedAddition": None,
                "quantityOrFormulaError": None, "fluency": None, "evidence": ""}})
        reviews.append({"id": row["id"], "source": row["source"], "context": row["context"],
                        "sourceSha256": row["sourceSha256"], "contextSha256": row["contextSha256"],
                        "inputIdentitySha256": identity_sha, "inputSha256": input_sha, "assistantReference": row["target"],
                        "referenceSha256": row["targetSha256"], "referenceHumanReviewed": False,
                        "referenceProvenance": row["provenance"], "domain": row["domain"],
                        "criticalChecks": row["criticalChecks"], "unitChecks": row["unitChecks"],
                        "protectedSymbols": row["protectedSymbols"], "forbiddenTerms": row["forbiddenTerms"],
                        "choices": choices, "humanReviewed": False, "reviewerType": None, "note": LIMITATION})
        keys.append({"id": row["id"], "labels": dict(zip("ABCD", names))})
    return reviews, {"version": VERSION, "inputIdentitySha256": identity_sha, "mappings": keys}


def summarize(input_path, results_dir, output_dir, marian_run_id, root=ROOT):
    require(isinstance(marian_run_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", marian_run_id), "Invalid Marian run ID")
    input_path, results_dir, output = (local(path, root) for path in (input_path, results_dir, output_dir))
    require(output != input_path.parent and output != results_dir and output not in [results_dir / name for name in SYSTEMS], "Output collides with inputs")
    inputs = Inputs()
    # Capture scalar implementation bytes before consuming any producer files.
    for path in scalar_code_paths():
        inputs.record(path)
    context_path = results_dir / "execution-context.json"
    context_present = context_path.exists()
    execution_context = inputs.read(context_path) if context_present else None
    require(execution_context is None or isinstance(execution_context, dict), "Malformed execution context record")
    rows, publication = read_dataset(input_path, inputs)
    predictions, identities = {}, {}
    execution_times = {}
    review_files = [{"file": item["file"], "path": str(input_path.parent / item["file"]), "sha256": item["sha256"]}
                    for item in publication["reviewFiles"]]
    for name in SYSTEMS:
        directory = local(results_dir / name, root)
        if name.startswith("hymt-"):
            values, identity, _summary = read_hymt(name, directory, rows, input_path, publication, root, inputs)
        else:
            values, identity, _summary = read_standard(name, directory, rows, inputs.files[str(input_path)],
                inputs.files[str(input_path.with_name("dataset-manifest.json"))], marian_run_id, root, inputs, review_files)
        predictions[name], identities[name] = values, identity
        execution_times[name] = {"producerElapsedSeconds": _summary.get("elapsedSeconds", _summary.get("totalSeconds")),
                                 "loadSeconds": _summary.get("loadSeconds"), "integritySeconds": _summary.get("integritySeconds")}
        require(all(value is None or finite(value) for value in execution_times[name].values()), "Invalid producer timing evidence")
    # Only scalar metrics/helpers are used. Never open model weights or the v5 test.
    identity = {"version": VERSION, "randomizationSeed": SEED, "inputFiles": dict(sorted(inputs.files.items())),
                "systems": identities, "datasetSha256": inputs.files[str(input_path)], "marianRunId": marian_run_id,
                "inputPath": str(input_path), "resultsDirectory": str(results_dir),
                "executionContext": {"path": str(context_path), "sha256": inputs.files[str(context_path)]} if context_present else None}
    identity_sha = canonical_hash(identity)
    metric_values = metrics(rows, predictions, identities)
    metric_values.update(inputIdentitySha256=identity_sha, datasetSha256=identity["datasetSha256"])
    metric_values["recordedExecutionContext"] = execution_context
    metric_values["executionContextInterpretation"] = "기록 시점의 실행 환경이며 다른 모델의 최종 실행 상태를 나타내지 않습니다."
    metric_values["latencyComparisonLimitation"] = (
        "실행 환경 동일성 검증 없음. 시간은 각 실행의 서술 통계이며 시스템 간 속도 순위로 비교할 수 없습니다."
        if not context_present else
        "기록된 실행 환경이 다를 수 있으므로 시간은 서술 통계로만 해석하세요. 학습과 병행한 Argos 시간을 다른 모델의 단독 실행 시간과 속도 순위로 비교하지 마세요.")
    for name in SYSTEMS:
        metric_values["systems"][name]["executionTiming"] = execution_times[name]
    reviews, key = blinded(rows, predictions, identity_sha, identity["datasetSha256"])
    contents = {"comparison-metrics.json": json_bytes(metric_values), "review-key.json": json_bytes(key),
                "anonymous-review.jsonl": "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in reviews).encode("utf-8")}
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()}
    inputs.unchanged()
    require(context_path.exists() == context_present, "Execution context appeared or disappeared during publication")
    if output.exists():
        require(not (output / "manifest.json").is_symlink(), "Linked output manifest is prohibited")
        manifest = parse((output / "manifest.json").read_bytes())
        require(manifest.get("status") == "complete" and manifest.get("identity") == identity and manifest.get("identitySha256") == identity_sha and
                manifest.get("files") == hashes and manifest.get("rowCount") == 24 and manifest.get("humanReviewed") is False and
                manifest.get("qualityGateApplied") is False, "Existing immutable publication identity differs")
        require({p.name for p in output.iterdir()} == {*OUTPUT_FILES, "manifest.json"} and all(not (output / name).is_symlink() and
                digest(output / name) == value for name, value in hashes.items()), "Existing immutable publication changed")
        inputs.unchanged()
        return output / "manifest.json"
    staging = output.with_name(output.name + ".pending")
    staging.mkdir(parents=True, exist_ok=False)
    manifest = {"version": VERSION, "status": "complete", "createdAt": datetime.now(timezone.utc).isoformat(),
                "identity": identity, "identitySha256": identity_sha, "files": hashes, "rowCount": 24,
                "humanReviewed": False, "qualityGateApplied": False, "inferencePerformed": False,
                "referenceType": "assistant_authored_unreviewed", "limitation": LIMITATION,
                "severityScale": {"0": "의미 오류 없음", "1": "핵심 의미를 바꾸지 않는 경미한 문제",
                                  "2": "실질적인 의미·조건·역할·수량 오류", "3": "핵심 결론 반전 또는 광범위한 누락·추가"},
                "reviewInstructions": "익명 검토 시 key와 metrics를 열지 마세요. 원문·문맥을 기준으로 판단하고 null은 미해결로 남기세요. 원본 템플릿과 inputSha256를 보존하고 reviewerType을 assistant로 설정하세요. 각 행에 anonymousInputSha256(익명 JSONL 파일 해시), reviewManifestSha256(manifest 파일 해시)를 추가하세요. fluency는 자연스러우면 true입니다. 검토 JSONL은 이 불변 폴더 밖의 별도 경로에 저장하세요."}
    for name, raw in {**contents, "manifest.json": json_bytes(manifest)}.items():
        with (staging / name).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    inputs.unchanged()
    require(not output.exists(), "Publication appeared concurrently; preserving pending files")
    staging.rename(output)
    return output / "manifest.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--marian-run-id", required=True)
    args = parser.parse_args()
    path = summarize(args.input, args.results, args.output, args.marian_run_id)
    print(json.dumps({"event": "quality-comparison-ready", "manifest": str(path), "manifestSha256": digest(path),
                      "humanReviewed": False, "inferencePerformed": False, "qualityGateApplied": False}))


if __name__ == "__main__":
    raise SystemExit(main())
