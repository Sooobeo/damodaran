"""Offline v5 terminology training, warm-started from the sealed v4 FP32 model.

The original trainer is loaded into a private module instance. Its checkpoint,
optimizer, generation-cache and final-test consumption machinery is reused;
the original file and all v4 artifacts remain unchanged. This entry point does
not export, register, or switch an application provider.
"""
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import signal
import sys

import dataset_v5

APP_ROOT = Path(__file__).resolve().parents[2]
WORK_ROOT = APP_ROOT / ".training"
DEPENDENCY_PATH = Path(__file__).with_name("train.py")
DEPENDENCY_SHA256 = "556d1d9845efea3fc34c3fd4013cb025bc2cba6796b0a1ce2366ab56c7f2ec3d"
PARENT_RUN_ID = "finance-v4-teacher"
PARENT_SELECTED_STEP = 218
PARENT_WEIGHT_SHA256 = "feff4d684e52e5d7cae7eccc0bf19316687159169e5c766aa03de73e5dc40a1e"
ORIGINAL_MODEL_ID = "Helsinki-NLP/opus-mt-tc-big-en-ko"
ORIGINAL_REVISION = "ae8606b7b29a495f31ce679cee2007f536a3a5ce"
ORIGINAL_WEIGHT_SHA256 = "f7d6ccf642f1672e6b06d46bc406a3f12220b70603f6745dfbce5c097f8511c2"
VERSION = "marian-finance-v5-warmstart-absolute-terminology-v1"
DATA_ROOT = WORK_ROOT / "datasets" / "finance-v5"
MODEL_FILES = ("model.safetensors", "config.json", "generation_config.json", "source.spm", "target.spm",
               "vocab.json", "target_vocab.json", "tokenizer_config.json", "special_tokens_map.json")
IDENTITY_KEYS = ('scriptVersion', 'scriptSha256', 'dependencyFiles', 'baseModel', 'baseRevision',
                 'basePath', 'baseFiles', 'originalBase', 'baselineRole', 'lineage', 'datasets',
                 'datasetPublication', 'config', 'gate')
RUNTIME_DEPENDENCIES = ('train.py', 'dataset_v5.py', 'dataset_io.py', 'assemble_v5_dataset.py', 'infer.py', 'infer_v5.py')
POLICY = {
    "version": 5,
    "minimumFinancialTermAccuracy": 0.90,
    "allowFinancialTermRegression": False,
    "maxChrFRegression": 1.0,
    "maxBleuRegression": 1.0,
    "allowNumericRegression": False,
    "allowEmptyOrCappedOutput": False,
    "maximumForbiddenTermHits": 0,
    "requireGeneralForbiddenTermCoverage": True,
    "generalRetention": {"maxChrFRegression": 1.0, "maxBleuRegression": 1.0, "allowNumericRegression": False},
    "requireChangedWeights": True,
    "selection": "dev gate eligibility, then chrF, then term accuracy, then negative token loss",
    "note": "Absolute finance terminology coverage >=90% on fresh dev/test, with no regression from the v4 warm-start baseline. Coverage is lexical, not semantic correctness. Source-grounded forbidden sense hits must be zero. Final test is consumed once; no tuning after its results.",
}

if hashlib.sha256(DEPENDENCY_PATH.read_bytes()).hexdigest() != DEPENDENCY_SHA256:
    raise RuntimeError("The frozen trainer dependency changed")
_spec = importlib.util.spec_from_file_location("_v5_private_frozen_training_engine", DEPENDENCY_PATH)
engine = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(engine)
LEGACY_METRICS = engine.metrics
LEGACY_VALIDATE_DATA = engine.validate_data


def forbidden_targets(row):
    values = row.get("forbiddenTerms", [])
    if not isinstance(values, list):
        raise ValueError(f"{row['id']}: forbiddenTerms must be a list")
    targets = []
    for value in values:
        target = value.get("target") if isinstance(value, dict) else None
        reason = value.get("reason") if isinstance(value, dict) else None
        if (not isinstance(target, str) or not engine.normalized_term(target)
                or not isinstance(reason, str) or not reason.strip()):
            raise ValueError(f"{row['id']}: each forbidden term needs a target and source-grounded reason")
        if isinstance(value, dict) and "source" in value:
            anchor = value["source"]
            if not isinstance(anchor, str) or not anchor.strip() or anchor.casefold() not in row["source"].casefold():
                raise ValueError(f"{row['id']}: forbidden-term source anchor is absent")
        if engine.normalized_term(target) in engine.normalized_term(row["target"]):
            raise ValueError(f"{row['id']}: assistant reference contains a forbidden term")
        targets.append(target)
    return list(dict.fromkeys(targets))


def leakage_issues(rows, statistics=None):
    """Exactly preserve the frozen duplicate decisions, pruning only upper bounds.

    SequenceMatcher.ratio = 2*M/(len(a)+len(b)); M cannot exceed the shorter
    sequence or the multiset intersection used by quick_ratio. A Jaccard set
    score cannot exceed min(set sizes)/max(set sizes). Bounds only skip pairs
    that cannot reach either existing acceptance threshold; no threshold changes.
    """
    statistics = statistics if statistics is not None else {}
    statistics.update({"pairs": 0, "lengthBoundPruned": 0, "quickRatioPruned": 0, "fullRatioCalls": 0})
    flat = []
    for split, values in rows.items():
        for row in values:
            exact, template = engine.normalized_source(row["source"]), engine.normalized_source(row["source"], True)
            flat.append((split, row, exact, template, set(template.split())))
    issues = []
    for position, (split, row, exact, template, tokens) in enumerate(flat):
        for other_split, other, other_exact, other_template, other_tokens in flat[:position]:
            statistics["pairs"] += 1
            reason = None
            if exact == other_exact:
                reason = "normalized-source-duplicate"
            elif split != other_split and template == other_template:
                reason = "numeric-template-leakage"
            elif split != other_split and min(len(exact), len(other_exact)) >= 35:
                maximum_jaccard = min(len(tokens), len(other_tokens)) / max(1, max(len(tokens), len(other_tokens)))
                overlap = (len(tokens & other_tokens) / max(1, len(tokens | other_tokens))) if maximum_jaccard >= 0.85 else 0.0
                threshold = 0.82 if overlap >= 0.85 else 0.92
                maximum_ratio = 2.0 * min(len(template), len(other_template)) / (len(template) + len(other_template))
                if maximum_ratio < threshold:
                    statistics["lengthBoundPruned"] += 1
                    continue
                matcher = SequenceMatcher(None, template, other_template, autojunk=False)
                if matcher.quick_ratio() < threshold:
                    statistics["quickRatioPruned"] += 1
                    continue
                statistics["fullRatioCalls"] += 1
                ratio = matcher.ratio()
                if ratio >= 0.92 or (overlap >= 0.85 and ratio >= 0.82):
                    reason = "near-template-leakage"
            if reason:
                issues.append({"kind": reason, "first": {"split": other_split, "id": other["id"]},
                               "second": {"split": split, "id": row["id"]}})
    return issues


def metrics(rows, predictions, include_domains=True):
    if len(rows) != len(predictions):
        raise ValueError("V5 metrics require complete prediction rows")
    # The frozen implementation recursively resolves its module-global metrics;
    # after hook installation the same extension is included in each domain.
    result = LEGACY_METRICS(rows, predictions, include_domains)
    checked_rows = checked_terms = hits = 0
    details = []
    for row, prediction in zip(rows, predictions):
        terms = forbidden_targets(row)
        matched = [term for term in terms if engine.normalized_term(term) in engine.normalized_term(prediction["prediction"])]
        checked_rows += bool(terms)
        checked_terms += len(terms)
        hits += len(matched)
        details.append({"id": row["id"], "forbiddenTerms": terms, "matchedForbiddenTerms": matched})
    result.update({"forbiddenTermRows": checked_rows, "forbiddenTermCount": checked_terms, "forbiddenTermHits": hits,
                   "forbiddenTermDetails": details,
                   "forbiddenTermMatcher": "Literal normalized target occurrence on source-grounded annotated rows only; not general semantic accuracy."})
    return result


def gate(base, candidate, domain=None):
    reasons = []
    if base.get("generationProtocol") != candidate.get("generationProtocol"):
        reasons.append("inference-protocol-mismatch")
    for name in ("chrF", "bleu", "numericPreservation"):
        if any(type(scores.get(name)) not in (int, float) or not math.isfinite(scores[name]) for scores in (base, candidate)):
            return {"passed": False, "reasons": [*reasons, "invalid-" + name]}
    if domain == "finance":
        before, after = base.get("termAccuracy"), candidate.get("termAccuracy")
        if (type(before) not in (int, float) or type(after) not in (int, float)
                or not math.isfinite(before) or not math.isfinite(after) or not 0 <= before <= 1 or not 0 <= after <= 1
                or not candidate.get("termCount")):
            reasons.append("missing-finance-term-evaluation")
        else:
            if after + 1e-12 < POLICY["minimumFinancialTermAccuracy"]:
                reasons.append("finance-term-accuracy-below-90-percent")
            if after + 1e-12 < before:
                reasons.append("finance-term-accuracy-regression")
    if domain == "general" and (not candidate.get("forbiddenTermRows") or not candidate.get("forbiddenTermCount")):
        reasons.append("general-contextual-sense-evaluation-missing")
    for name, limit in (("chrF", POLICY["maxChrFRegression"]), ("bleu", POLICY["maxBleuRegression"])):
        if candidate[name] + limit < base[name]:
            reasons.append(name.lower() + "-regression")
    if candidate["numericPreservation"] + 1e-12 < base["numericPreservation"]:
        reasons.append("numeric-regression")
    if candidate["emptyOutputs"] or candidate["cappedOutputs"]:
        reasons.append("empty-or-capped-output")
    if candidate.get("forbiddenTermHits", 0):
        reasons.append("contextually-forbidden-term-produced")
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


def validate_data(args, run_dir, write_report=True):
    paths = {split: engine.local_path(getattr(args, split + "_data"), DATA_ROOT) for split in ("train", "dev", "test")}
    publication = dataset_v5.verify_published_dataset(paths, DATA_ROOT)
    rows = {split: engine.read_rows(path, split) for split, path in paths.items()}
    for split, values in rows.items():
        for row in values:
            forbidden_targets(row)
            for target in engine.term_targets(row):
                if engine.normalized_term(target) not in engine.normalized_term(row["target"]):
                    raise ValueError(f"{split}/{row['id']}: reference does not contain its terminology annotation")
        if split in {"dev", "test"}:
            if not any(row["domain"] == "finance" and engine.term_targets(row) for row in values):
                raise ValueError(f"{split} needs finance terminology evaluation")
            if not any(row["domain"] == "general" and forbidden_targets(row) for row in values):
                raise ValueError(f"{split} needs source-grounded general forbidden-sense examples")
    statistics = {}
    issues = leakage_issues(rows, statistics)
    report = {"checkedAt": engine.now(), "counts": {split: len(value) for split, value in rows.items()},
              "files": {split: {"path": engine.relative(path), "sha256": engine.sha256(path)} for split, path in paths.items()},
              "checks": "Frozen v2 exact/number-template/near-template decisions, with proven ratio/Jaccard upper-bound pruning; reference term and forbidden-sense metadata validated.",
              "publication": publication,
              "optimizedComparisonStatistics": statistics, "issues": issues, "passed": not issues}
    if write_report:
        engine.write_json(run_dir / "data-validation.json", report)
    if issues:
        raise ValueError(f"Dataset leakage check failed: {len(issues)} issue(s); see data-validation.json")
    return rows, report


def read_json(path):
    return json.loads(path.read_text("utf-8-sig"))


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def dependency_hashes():
    return {engine.relative(Path(__file__).with_name(name)): engine.sha256(Path(__file__).with_name(name))
            for name in RUNTIME_DEPENDENCIES}


def model_inventory(directory, manifest, summary):
    model = engine.local_path(summary['modelPath'], directory)
    selected = engine.local_path(manifest['selectedCheckpoint'], directory)
    checkpoint = read_json(selected / 'checkpoint.json')
    files = {}
    for name in MODEL_FILES:
        current, original = model / name, selected / 'model' / name
        if current.is_symlink() or original.is_symlink() or engine.sha256(current) != engine.sha256(original):
            raise ValueError('V5 final model differs from the selected checkpoint')
        files[name] = engine.sha256(current)
    if (checkpoint.get('step') != summary.get('selectedStep')
            or checkpoint.get('modelSha256') != summary.get('trainedWeightFileSha256')
            or files['model.safetensors'] != summary.get('trainedWeightFileSha256')):
        raise ValueError('V5 final model checkpoint identity differs')
    return {'version': 1, 'selectedStep': summary['selectedStep'], 'modelPath': engine.relative(model),
            'selectedCheckpoint': engine.relative(selected), 'files': files}


def verify_model_inventory(directory, manifest, summary):
    inventory = manifest.get('modelInventory')
    if (not isinstance(inventory, dict) or summary.get('modelInventory') != inventory
            or manifest.get('modelInventorySha256') != canonical_hash(inventory)
            or summary.get('modelInventorySha256') != canonical_hash(inventory)
            or model_inventory(directory, manifest, summary) != inventory):
        raise ValueError('V5 final runtime inventory is missing or changed; do not reseal altered files')
    return inventory


def train(args, directory, model, rows, manifest):
    summary_path = directory / 'training-summary.json'
    existed = summary_path.exists()
    if existed:
        summary = read_json(summary_path)
        # A complete summary is committed before the final manifest. Resume an
        # interrupted second write from its already sealed inventory only.
        saved = summary.get('modelInventory')
        if manifest.get('modelInventory') is None and isinstance(saved, dict):
            proposed = {**manifest, 'selectedCheckpoint': saved.get('selectedCheckpoint')}
            if (summary.get('modelInventorySha256') != canonical_hash(saved)
                    or model_inventory(directory, proposed, summary) != saved):
                raise ValueError('Interrupted final inventory changed; do not reseal')
            manifest.update({'modelInventory': saved, 'modelInventorySha256': summary['modelInventorySha256'],
                             'selectedCheckpoint': saved['selectedCheckpoint'], 'modelPath': summary['modelPath'],
                             'status': 'trained', 'completedUpdates': summary['completedUpdates']})
            engine.write_json(directory / 'manifest.json', manifest)
        verify_model_inventory(directory, manifest, summary)
    elif manifest.get('modelInventory') is not None:
        raise ValueError('V5 final inventory exists without its training summary')
    original_writer = engine.write_json
    def write_with_final_inventory(path, value):
        if path == summary_path:
            if existed:
                raise ValueError('An existing final summary must not be resealed')
            candidates = [entry for entry in manifest.get('devHistory', []) if entry['step'] == value['selectedStep']]
            if len(candidates) != 1:
                raise ValueError('The final selection does not identify one recorded dev checkpoint')
            selected_manifest = {**manifest, 'selectedCheckpoint': candidates[0]['checkpoint']}
            inventory = model_inventory(directory, selected_manifest, value)
            stamp = {'modelInventory': inventory, 'modelInventorySha256': canonical_hash(inventory)}
            value.update(stamp)
            manifest.update(stamp)
        original_writer(path, value)
    # This temporary private hook seals the inventory in the very first summary
    # write. The frozen engine file and independently imported modules stay intact.
    engine.write_json = write_with_final_inventory
    try:
        engine.train(args, directory, model, rows, manifest)
    finally:
        engine.write_json = original_writer
    if manifest.get('status') == 'trained':
        verify_model_inventory(directory, manifest, read_json(summary_path))


def verify_parent():
    """Read v4 lineage and selected model files only, never v4 final-test data."""
    directory = WORK_ROOT / "runs" / PARENT_RUN_ID
    manifest_path, summary_path = directory / "manifest.json", directory / "training-summary.json"
    manifest, summary = read_json(manifest_path), read_json(summary_path)
    if (manifest.get("runId") != PARENT_RUN_ID or summary.get("runId") != PARENT_RUN_ID
            or manifest.get("baseModel") != ORIGINAL_MODEL_ID or manifest.get("baseRevision") != ORIGINAL_REVISION
            or manifest.get("baseFiles", {}).get("model.safetensors") != ORIGINAL_WEIGHT_SHA256
            or summary.get("baseWeightFileSha256") != ORIGINAL_WEIGHT_SHA256
            or summary.get("trainedWeightFileSha256") != PARENT_WEIGHT_SHA256
            or summary.get("selectedStep") != PARENT_SELECTED_STEP
            or summary.get("completedUpdates", 0) < 1
            or summary.get("weightEvidence", {}).get("changedTensorCount", 0) < 1):
        raise ValueError("Parent lineage differs from the sealed v4 selected FP32 model")
    model = engine.local_path(summary["modelPath"], directory)
    selected = engine.local_path(manifest["selectedCheckpoint"], directory)
    checkpoint = read_json(selected / "checkpoint.json")
    if checkpoint.get("step") != PARENT_SELECTED_STEP or checkpoint.get("modelSha256") != PARENT_WEIGHT_SHA256:
        raise ValueError("Parent selected checkpoint differs")
    for name in MODEL_FILES:
        current, source = model / name, selected / "model" / name
        if current.is_symlink() or source.is_symlink() or engine.sha256(current) != engine.sha256(source):
            raise ValueError("Parent selected model/tokenizer copy changed")
    inventory = {name: engine.sha256(model / name) for name in MODEL_FILES}
    if inventory["model.safetensors"] != PARENT_WEIGHT_SHA256:
        raise ValueError("Parent warm-start weight hash changed")
    tokenizer = engine.validate_tokenizer_files(model)
    parent_protocol = summary.get("selectedDev", {}).get("generationProtocol", {})
    if parent_protocol.get("effectivePrecision") != "fp32":
        raise ValueError("Warm-start parent must be the evaluated FP32 checkpoint")
    lineage = {"parentRunId": PARENT_RUN_ID, "parentSelectedStep": PARENT_SELECTED_STEP,
               "parentModelPath": engine.relative(model), "parentWeightSha256": PARENT_WEIGHT_SHA256,
               "parentModelFiles": inventory, "parentManifestSha256": engine.sha256(manifest_path),
               "parentTrainingSummarySha256": engine.sha256(summary_path), "parentCheckpointMetadataSha256": engine.sha256(selected / "checkpoint.json"),
               "originalBase": {"model": ORIGINAL_MODEL_ID, "revision": ORIGINAL_REVISION, "weightSha256": ORIGINAL_WEIGHT_SHA256},
               "baselineRole": "v4 warm-start checkpoint before v5 optimizer updates; not original public weights",
               "tokenizer": tokenizer, "oldFinalTestRead": False}
    return model, inventory, lineage


def setup(args):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.run_id) or args.run_id == PARENT_RUN_ID:
        raise ValueError("Use a new valid v5 run ID")
    if engine.sha256(DEPENDENCY_PATH) != DEPENDENCY_SHA256:
        raise ValueError("Frozen trainer dependency changed after module loading")
    directory = WORK_ROOT / "runs" / args.run_id
    directory.mkdir(parents=True, exist_ok=True)
    rows, data = validate_data(args, directory, write_report=False)
    model, files, lineage = verify_parent()
    config = {name: getattr(args, name) for name in ("device", "seed", "threads", "precision", "epochs", "batch_size", "accumulation",
              "learning_rate", "warmup_updates", "max_length", "max_new_tokens", "beams", "checkpoint_every", "max_updates")}
    identity = {"scriptVersion": VERSION, "scriptSha256": engine.sha256(Path(__file__)),
                "dependencyFiles": dependency_hashes(),
                "baseModel": ORIGINAL_MODEL_ID, "baseRevision": ORIGINAL_REVISION,
                "basePath": engine.relative(model), "baseFiles": files, "originalBase": lineage["originalBase"],
                "baselineRole": lineage["baselineRole"], "lineage": lineage, "datasets": data["files"],
                "datasetPublication": data['publication'],
                "config": config, "gate": POLICY}
    path = directory / "manifest.json"
    if path.exists():
        manifest = read_json(path)
        if (manifest.get("runId") != args.run_id or manifest.get("identitySha256") != canonical_hash(identity)
                or any(manifest.get(key) != value for key, value in identity.items())):
            raise ValueError("V5 run identity/config/data/parent changed; do not resume this run")
    else:
        manifest = {"schemaVersion": 2, "runId": args.run_id, "createdAt": engine.now(), "status": "prepared",
                    "identitySha256": canonical_hash(identity), **identity}
        engine.write_json(path, manifest)
    engine.write_json(directory / 'data-validation.json', data)
    return directory, model, rows, manifest


def evaluate(args, directory, model, rows, manifest):
    summary = read_json(directory / "training-summary.json")
    # The root/user explicitly invokes this stage only after dev passes. Never
    # consume a new held-out test to diagnose an already-failing dev candidate.
    if not summary.get("devGate", {}).get("passed"):
        raise ValueError("V5 final test requires a passing selected dev gate first")
    verify_model_inventory(directory, manifest, summary)
    return engine.evaluate(args, directory, model, rows, manifest)


def install_engine_hooks():
    # These globals belong solely to the private module loaded above. The
    # original module/file, v4 manifests and deployed runtime are never changed.
    engine.BASE_WEIGHT_HASH = PARENT_WEIGHT_SHA256
    engine.GATE = POLICY
    engine.SCRIPT_VERSION = VERSION
    engine.metrics = metrics
    engine.gate = gate


install_engine_hooks()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("validate", "bench", "train", "evaluate"))
    parser.add_argument("--run-id", default="finance-v5-terminology")
    parser.add_argument("--train-data", default=".training/datasets/finance-v5/train.jsonl")
    parser.add_argument("--dev-data", default=".training/datasets/finance-v5/dev.jsonl")
    parser.add_argument("--test-data", default=".training/datasets/finance-v5/test.jsonl")
    parser.add_argument("--device", choices=("xpu", "cpu"), default="xpu")
    parser.add_argument("--precision", choices=("fp32",), default="fp32")
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--accumulation", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=1e-5)
    parser.add_argument("--warmup-updates", type=int, default=5)
    parser.add_argument("--max-length", type=int, default=384)
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--beams", type=int, default=4)
    parser.add_argument("--checkpoint-every", type=int, default=50)
    parser.add_argument("--max-updates", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not 5e-6 <= args.learning_rate <= 2e-5:
        parser.error("learning-rate must be between 5e-6 and 2e-5")
    if (any(getattr(args, name) < 1 for name in ("threads", "epochs", "batch_size", "accumulation", "max_length", "max_new_tokens", "checkpoint_every"))
            or args.max_length > 512 or args.max_new_tokens > 512 or args.beams != 4 or args.max_updates < 0 or args.warmup_updates < 0):
        parser.error("Use positive limits <=512 tokens, beam4, and nonnegative update/warmup limits")
    def stop(_signum, _frame):
        engine.STOP_REQUESTED = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    with engine.exclusive_run(args.run_id):
        values = setup(args)
        if args.stage == "validate":
            engine.emit("v5-validation-complete", path=engine.relative(values[0] / "data-validation.json"))
        else:
            {"bench": engine.benchmark, "train": train, "evaluate": evaluate}[args.stage](args, *values)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        engine.emit("v5-failed", errorType=type(error).__name__, message=str(error)[:500])
        sys.exit(1)
