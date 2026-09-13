"""Verify a registered, optional Hy-MT2 deployment without starting inference."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ".training/comparisons/hy-mt2-7b-q8"
PYTHON_PATH = ".venv-training/Scripts/python.exe"
JINJA_PATH = ".venv-training/Lib/site-packages/jinja2"
ACTIVE_PATH = ".translation/hymt/manifest.json"
CATALOG_SOURCE = ".training/datasets/finance-v5/term-catalog.json"
CATALOG_SHA256 = "d4af6a9fed8c6dba4c528abc52c64c19621c5fac98fd2bdb09e035769963de67"
MODEL_NAME = "tencent/Hy-MT2-7B-GGUF"
REVISION = "ab8472660ac61fac25f1af43fac2599d52a8a775"
MODEL_FILE = "HY-MT2-7B-Q8_0.gguf"
MODEL_SIZE = 7981928896
MODEL_SHA256 = "58b3ad55dd6f6fa08c695cddc34fb5f8f708a844f78ae10508071914b0ed67c0"
RUNTIME_ZIP = "llama-b10874-bin-win-vulkan-x64.zip"
RUNTIME_SIZE = 35789222
RUNTIME_SHA256 = "0113e9b49a5d979b32805092740ce97b9497494cdb308cf9568577da3e78e3c0"
TEMPLATE_SHA256 = "788ac16c5d7bfefc28655928ad524c8f378a44cb24d24fb125d6a5859b167677"
CODE_PATHS = (
    "scripts/local-hymt/bridge.py", "scripts/local-hymt/deployment.py",
    "scripts/local-hymt/engine.py", "scripts/local-hymt/process_owner.py",
    "scripts/model-comparison/run_hymt.py", "scripts/model-comparison/setup_hymt.py",
)
OVERRIDES = {"tokenizer.ggml.eos_token_id": "int:127960", "tokenizer.ggml.eot_token_id": "int:127967",
             "tokenizer.ggml.add_bos_token": "bool:false", "tokenizer.ggml.add_eos_token": "bool:false"}
SAMPLING = {"temperature": 0.7, "top_p": 0.6, "top_k": 20, "repeat_penalty": 1.05,
            "repeat_last_n": 8192, "min_p": 0.0, "seed": 42,
            "samplers": ["penalties", "temperature", "top_k", "top_p"], "n_predict": 4096,
            "stop": ["<|eos|>", "<|extra_5|>"], "ignore_eos": False, "cache_prompt": False,
            "stream": False, "return_tokens": True, "n_keep": 0, "id_slot": 0,
            "presence_penalty": 0.0, "frequency_penalty": 0.0, "dry_multiplier": 0.0,
            "mirostat": 0, "dynatemp_range": 0.0, "typical_p": 1.0,
            "xtc_probability": 0.0, "top_n_sigma": -1.0}
SYSTEMS = {"argos-app", "marian-v5", "hymt-raw", "hymt-contextual"}
PREPARED_VERSION = "finance-quality-four-system-review-v1"
REVIEW_VERSION = "finance-quality-four-system-assistant-review-summary-v1"
MAX_METADATA = 32 * 1024 * 1024


class DeploymentError(ValueError):
    """Only bounded diagnostic codes, never file contents or source text."""


def require(condition, code):
    if not condition:
        raise DeploymentError(code)


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def prepared_identity_sha(value):
    # summarize_quality.canonical_hash preserves Unicode. The execution contract
    # uses its separate ASCII-escaped canonical format above.
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def parse(raw):
    return json.loads(raw, object_pairs_hook=_unique,
                      parse_constant=lambda _value: require(False, "non_finite_json"))


def relative_name(value):
    require(isinstance(value, str) and value and "\\" not in value and not value.startswith("/"), "invalid_relative_path")
    parts = value.split("/")
    require(all(part not in ("", ".", "..") and part == part.rstrip(" .")
                and not re.search(r'[<>:"|?*\x00-\x1f]', part)
                and not re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                for part in parts) and PurePosixPath(value).as_posix() == value, "invalid_relative_path")
    return value


def safe_path(root, relative, *, exists=True):
    root = Path(root).absolute()
    relative_name(relative)
    current = root
    # A junction is a reparse point too, including on Python before is_junction.
    for item in [root, *root.parents]:
        if item.exists():
            require(not item.is_symlink() and not getattr(item.lstat(), "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT, "linked_root")
    for part in relative.split("/"):
        current = current / part
        if current.exists() or current.is_symlink():
            require(not current.is_symlink() and not getattr(current.lstat(), "st_file_attributes", 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT, "linked_deployment_path")
    require(current.resolve().is_relative_to(root.resolve()), "deployment_path_escape")
    require(not exists or current.exists(), "deployment_file_missing")
    return current


def fingerprint(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            getattr(info, "st_file_attributes", 0))


class FileTracker:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.stamps, self.hashes, self.inventories = {}, {}, {}

    def file(self, relative, *, expected=None, size=None, content=False):
        path = safe_path(self.root, relative)
        require(path.is_file(), "deployment_regular_file_required")
        before = fingerprint(path)
        require(size is None or type(size) is int and size == before[2], "deployment_file_size_changed")
        require(not content or before[2] <= MAX_METADATA, "metadata_byte_limit")
        hasher, chunks = hashlib.sha256(), []
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                hasher.update(chunk)
                if content:
                    chunks.append(chunk)
        require(fingerprint(path) == before, "deployment_file_changed_while_reading")
        observed = hasher.hexdigest()
        require(expected is None or valid_sha(expected) and observed == expected, "deployment_file_hash_changed")
        require(relative not in self.stamps or self.stamps[relative] == before and self.hashes[relative] == observed,
                "deployment_file_changed_during_verification")
        self.stamps[relative], self.hashes[relative] = before, observed
        entry = {"path": relative, "size": before[2], "sha256": observed}
        return b"".join(chunks) if content else entry

    def entry(self, entry):
        require(isinstance(entry, dict) and set(entry) == {"path", "size", "sha256"}
                and type(entry["size"]) is int and entry["size"] >= 0 and valid_sha(entry["sha256"]), "invalid_file_entry")
        return self.file(entry["path"], expected=entry["sha256"], size=entry["size"])

    def inventory(self, relative, names, *, suffix=None):
        directory = safe_path(self.root, relative)
        require(directory.is_dir(), "runtime_directory_missing")
        actual = set()
        for parent, directories, files in os.walk(directory, followlinks=False):
            for name in [*directories, *files]:
                path = Path(parent) / name
                key = path.relative_to(self.root).as_posix()
                safe_path(self.root, key)
                if path.is_file() and (suffix is None or path.suffix.lower() == suffix):
                    actual.add(key)
        require(actual == set(names), "runtime_inventory_changed")
        self.inventories[relative] = (set(names), suffix)

    def assert_unchanged(self):
        for relative, expected in self.stamps.items():
            path = safe_path(self.root, relative)
            require(path.is_file() and fingerprint(path) == expected, "deployment_fingerprint_changed")
        for relative, (names, suffix) in list(self.inventories.items()):
            self.inventory(relative, names, suffix=suffix)


def file_entry(root, relative):
    return FileTracker(root).file(relative)


def review_input_hashes(root, prepared):
    """Validate recorded absolute paths lexically before reading any input."""
    identity = prepared.get("identity") if isinstance(prepared, dict) else None
    inputs = identity.get("inputFiles") if isinstance(identity, dict) else None
    require(isinstance(inputs, dict) and 1 <= len(inputs) <= 10000, "prepared_input_inventory_missing")
    root = Path(root).absolute()
    result, seen = {}, set()
    for name, expected in inputs.items():
        require(isinstance(name, str) and 0 < len(name) <= 4096 and valid_sha(expected), "invalid_prepared_input")
        path = Path(name)
        require(path.is_absolute(), "prepared_input_not_absolute")
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            raise DeploymentError("prepared_input_outside_project") from None
        relative_name(relative)
        require(relative.casefold() not in seen, "duplicate_prepared_input")
        seen.add(relative.casefold())
        result[relative] = expected
    return result


def track_python_runtime(tracker, prepared):
    """Bind only the fixed interpreter and Jinja source tree to review hashes.

    Other paths from prepared metadata are not opened by fresh verification.
    Registration separately tracks all fully revalidated review inputs.
    """
    inputs = review_input_hashes(tracker.root, prepared)
    sources = {name: value for name, value in inputs.items()
               if name.startswith(JINJA_PATH + "/") and name.lower().endswith(".py")}
    require(PYTHON_PATH in inputs and JINJA_PATH + "/__init__.py" in sources, "python_runtime_inventory_missing")
    tracker.file(PYTHON_PATH, expected=inputs[PYTHON_PATH])
    for name, expected in sources.items():
        tracker.file(name, expected=expected)
    tracker.inventory(JINJA_PATH, sources, suffix=".py")
    return inputs


def _helpers():
    comparison = str(ROOT / "scripts/model-comparison")
    if comparison not in sys.path:
        sys.path.insert(0, comparison)
    import run_hymt as hy
    import setup_hymt as setup
    return hy, setup


def execution_contract():
    hy, _setup = _helpers()
    require(hy.REVISION == REVISION and hy.MODEL["name"] == MODEL_FILE and hy.MODEL["sha256"] == MODEL_SHA256
            and hy.MODEL["size"] == MODEL_SIZE and hy.TEMPLATE_SHA == TEMPLATE_SHA256
            and canonical(hy.OVERRIDES) == canonical(OVERRIDES) and canonical(hy.SAMPLING) == canonical(SAMPLING)
            and hy.CONTEXT_SIZE == 8192 and hy.MAX_NEW_TOKENS == 4096, "comparison_execution_contract_changed")
    return {"revision": REVISION, "templateSha256": TEMPLATE_SHA256, "runtimeOverrides": OVERRIDES,
            "sampling": SAMPLING, "contextSize": 8192, "threads": 4, "cpuOnly": True,
            "contextPolicyVersion": "existing-title-neighbors-v1", "pythonVersion": platform.python_version(),
            "jinjaVersion": importlib.metadata.version("jinja2")}


def runtime_inventory(root, tracker):
    _hy, setup = _helpers()
    candidates = [f".training/comparisons/translategemma-4b-q4/{RUNTIME_ZIP}", f"{MODEL_DIR}/{RUNTIME_ZIP}"]
    archive_name = next((name for name in candidates if safe_path(root, name, exists=False).exists()), None)
    require(archive_name is not None, "pinned_runtime_archive_missing")
    tracker.file(archive_name, expected=RUNTIME_SHA256, size=RUNTIME_SIZE)
    entries = setup.archive_inventory(safe_path(root, archive_name))
    tracker.assert_unchanged()
    require(len(entries) == 52, "runtime_file_count_changed")
    return [{**entry, "path": f"{MODEL_DIR}/runtime/{relative_name(entry['path'])}"} for entry in entries]


def read_terms(raw):
    require(sha(raw) == CATALOG_SHA256, "catalog_not_frozen")
    catalog = parse(raw)
    terms = catalog.get("terms") if isinstance(catalog, dict) else None
    require(isinstance(terms, list) and len(terms) == 54, "catalog_count_changed")
    seen, clean = set(), []
    for term in terms:
        require(isinstance(term, dict), "invalid_catalog_term")
        selected = {key: term.get(key) for key in ("id", "source", "target", "definition")}
        require(all(isinstance(value, str) and 0 < len(value.strip()) <= 2000 for value in selected.values())
                and selected["id"] not in seen, "invalid_catalog_term")
        aliases = term.get("aliases", [])
        require(isinstance(aliases, list) and len(aliases) <= 30
                and all(isinstance(value, str) and 0 < len(value.strip()) <= 500 for value in aliases), "invalid_catalog_aliases")
        require(all("<|" not in value and "|>" not in value and not re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value)
                    for value in [*selected.values(), *aliases]), "catalog_control_token")
        seen.add(selected["id"])
        clean.append(selected | {"aliases": list(aliases)})
    return clean


def check_selection(selection, profile, prepared_sha, reviews_sha):
    require(isinstance(selection, dict) and set(selection) == {"selectedSystem", "preparedManifestSha256",
            "assistantReviewSummarySha256", "rationale", "humanReviewed"}, "selection_schema_changed")
    require(profile in ("raw", "contextual") and selection["selectedSystem"] == "hymt-" + profile
            and selection["preparedManifestSha256"] == prepared_sha
            and selection["assistantReviewSummarySha256"] == reviews_sha
            and isinstance(selection["rationale"], str) and 0 < len(selection["rationale"].strip()) <= 12000
            and selection["humanReviewed"] is False, "selection_evidence_changed")


def check_review_metadata(prepared, metrics, reviews, prepared_sha, metrics_sha):
    require(isinstance(prepared, dict) and prepared.get("version") == PREPARED_VERSION
            and prepared.get("status") == "complete" and type(prepared.get("rowCount")) is int
            and prepared["rowCount"] == 24 and prepared.get("humanReviewed") is False
            and prepared.get("qualityGateApplied") is False and prepared.get("inferencePerformed") is False,
            "prepared_review_incomplete")
    identity = prepared.get("identity")
    require(isinstance(identity, dict) and prepared_identity_sha(identity) == prepared.get("identitySha256")
            and set(identity.get("systems", {})) == SYSTEMS and valid_sha(identity.get("datasetSha256"))
            and isinstance(prepared.get("files"), dict)
            and set(prepared["files"]) == {"comparison-metrics.json", "anonymous-review.jsonl", "review-key.json"}
            and all(valid_sha(value) for value in prepared["files"].values())
            and prepared.get("files", {}).get("comparison-metrics.json") == metrics_sha, "prepared_identity_changed")
    require(isinstance(metrics, dict) and metrics.get("version") == PREPARED_VERSION
            and type(metrics.get("rowCount")) is int and metrics["rowCount"] == 24 and metrics.get("humanReviewed") is False
            and metrics.get("qualityGateApplied") is False and metrics.get("inputIdentitySha256") == prepared["identitySha256"]
            and metrics.get("datasetSha256") == identity["datasetSha256"] and set(metrics.get("systems", {})) == SYSTEMS
            and all(isinstance(value, dict) and type(value.get("count")) is int and value["count"] == 24
                    and isinstance(value.get("rows"), list) and len(value["rows"]) == 24
                    and all(isinstance(row, dict) for row in value["rows"]) for value in metrics["systems"].values()),
            "comparison_metrics_incomplete")
    require(isinstance(reviews, dict) and reviews.get("version") == REVIEW_VERSION and reviews.get("completed") is True
            and reviews.get("reviewerType") == "assistant" and reviews.get("humanReviewed") is False
            and all(type(reviews.get(field)) is int for field in ("rowCount", "judgedChoices", "unresolvedJudgments"))
            and reviews.get("rowCount") == 24 and reviews.get("judgedChoices") == 96
            and reviews.get("unresolvedJudgments") == 0 and reviews.get("automaticGateCreated") is False
            and reviews.get("promotionDecision") is None and reviews.get("inferencePerformed") is False
            and reviews.get("humanGoldReference") is False and reviews.get("preparedManifestSha256") == prepared_sha
            and reviews.get("preparedIdentitySha256") == prepared["identitySha256"]
            and reviews.get("anonymousSha256") == prepared["files"].get("anonymous-review.jsonl")
            and reviews.get("keySha256") == prepared["files"].get("review-key.json")
            and set(reviews.get("systems", {})) == SYSTEMS
            and all(isinstance(value, dict) and type(value.get("judgedChoices")) is int and value["judgedChoices"] == 24
                    for value in reviews["systems"].values())
            and isinstance(reviews.get("evidence"), list) and len(reviews["evidence"]) == 96, "assistant_review_incomplete")
    ids = [f"Q26-{index:03d}" for index in range(1, 25)]
    anchors, source_hashes = {}, {}
    for system, values in metrics["systems"].items():
        require([row.get("id") for row in values["rows"]] == ids, "metric_row_coverage_changed")
        for row in values["rows"]:
            require(valid_sha(row.get("sourceSha256")) and valid_sha(row.get("translationSha256")), "metric_text_identity_missing")
            require(row["id"] not in source_hashes or source_hashes[row["id"]] == row["sourceSha256"], "metric_source_identity_changed")
            source_hashes[row["id"]] = row["sourceSha256"]
            anchors[(row["id"], system)] = (row["sourceSha256"], row["translationSha256"])
    flags = {"meaningPreserved", "negationAndConditionsPreserved", "contextualWordSensePreserved",
             "omission", "unsupportedAddition", "quantityOrFormulaError", "fluency"}
    seen = set()
    for item in reviews["evidence"]:
        require(isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("system"), str), "invalid_review_evidence")
        pair = (item["id"], item["system"])
        require(pair in anchors and pair not in seen and anchors[pair] == (item.get("sourceSha256"), item.get("translationSha256")),
                "review_choice_coverage_changed")
        seen.add(pair)
        judgment = item.get("review")
        require(isinstance(judgment, dict) and set(judgment) == flags | {"severity", "evidence"}
                and all(type(judgment[flag]) is bool for flag in flags)
                and type(judgment["severity"]) is int and 0 <= judgment["severity"] <= 3
                and isinstance(judgment["evidence"], str) and 0 < len(judgment["evidence"].strip()) <= 12000,
                "unresolved_assistant_judgment")
    require(seen == set(anchors), "review_choice_coverage_changed")


@dataclass
class VerifiedBundle:
    manifest: dict
    identity: str
    terms: list
    _tracker: FileTracker
    _active_sha: str

    def assert_unchanged(self):
        self._tracker.assert_unchanged()
        path = safe_path(self._tracker.root, ACTIVE_PATH)
        require(path.stat().st_size <= MAX_METADATA and sha(path.read_bytes()) == self._active_sha, "active_manifest_changed")
        self._tracker.assert_unchanged()


def verify_manifest(root=ROOT) -> VerifiedBundle:
    tracker = FileTracker(root)
    raw = tracker.file(ACTIVE_PATH, content=True)
    manifest = parse(raw)
    fields = {"schemaVersion", "provider", "model", "modelHash", "runtimeVersion", "installedAt", "registrationId",
              "pythonPath", "modelPath", "profile", "modelFiles", "runtimeFiles", "codeFiles", "catalog", "evidenceFiles", "execution"}
    require(isinstance(manifest, dict) and set(manifest) == fields and type(manifest["schemaVersion"]) is int
            and manifest["schemaVersion"] == 1 and manifest["provider"] == "hymt" and manifest["model"] == MODEL_NAME
            and manifest["modelHash"] == MODEL_SHA256 and manifest["pythonPath"] == PYTHON_PATH
            and manifest["modelPath"] == MODEL_DIR and manifest["profile"] in ("raw", "contextual"), "invalid_deployment_manifest")
    registration_id = manifest["registrationId"]
    require(isinstance(registration_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", registration_id), "invalid_registration_id")
    require(isinstance(manifest["installedAt"], str) and datetime.fromisoformat(manifest["installedAt"]).utcoffset() is not None,
            "registration_time_missing_timezone")
    registration = f".translation/hymt/registrations/{registration_id}"
    require(tracker.file(f"{registration}/manifest.json", content=True) == raw, "immutable_registration_changed")
    tracker.file(PYTHON_PATH)
    require(isinstance(manifest["codeFiles"], list) and len(manifest["codeFiles"]) == 6
            and all(isinstance(entry, dict) for entry in manifest["codeFiles"])
            and {entry.get("path") for entry in manifest["codeFiles"]} == set(CODE_PATHS), "deployment_code_inventory_changed")
    for entry in manifest["codeFiles"]:
        tracker.entry(entry)
    execution = execution_contract()
    require(canonical(manifest["execution"]) == canonical(execution)
            and manifest["runtimeVersion"] == "hymt-local-v1:" + sha(canonical(execution)), "execution_contract_changed")
    expected_model = [{"path": f"{MODEL_DIR}/{MODEL_FILE}", "size": MODEL_SIZE, "sha256": MODEL_SHA256}]
    require(manifest["modelFiles"] == expected_model, "model_inventory_changed")
    pinned_runtime = runtime_inventory(root, tracker)
    require(manifest["runtimeFiles"] == pinned_runtime, "pinned_runtime_inventory_changed")
    seen = set()
    for field, count in (("modelFiles", 1), ("runtimeFiles", 52), ("codeFiles", 6), ("evidenceFiles", 4)):
        entries = manifest[field]
        require(isinstance(entries, list) and len(entries) == count, "deployment_inventory_count")
        for entry in entries:
            require(isinstance(entry, dict) and isinstance(entry.get("path"), str)
                    and entry["path"].casefold() not in seen, "duplicate_deployment_file")
            seen.add(entry["path"].casefold())
            tracker.entry(entry)
    require({entry["path"] for entry in manifest["codeFiles"]} == set(CODE_PATHS), "deployment_code_inventory_changed")
    tracker.inventory(f"{MODEL_DIR}/runtime", [entry["path"] for entry in pinned_runtime])
    catalog = manifest["catalog"]
    require(isinstance(catalog, dict) and catalog.get("path") == f"{registration}/catalog.json"
            and catalog.get("sha256") == CATALOG_SHA256 and catalog["path"].casefold() not in seen, "registered_catalog_changed")
    tracker.entry(catalog)
    terms = read_terms(tracker.file(catalog["path"], content=True))
    evidence = {entry["path"]: parse(tracker.file(entry["path"], content=True)) for entry in manifest["evidenceFiles"]}
    require(all(isinstance(value, dict) for value in evidence.values()), "invalid_deployment_evidence")
    require(all(name.startswith(".training/comparisons/") for name in evidence), "evidence_path_outside_comparison")
    prepared_names = [name for name, value in evidence.items() if value.get("version") == PREPARED_VERSION and value.get("status") == "complete"]
    review_names = [name for name, value in evidence.items() if value.get("version") == REVIEW_VERSION]
    selection_names = [name for name, value in evidence.items() if "selectedSystem" in value]
    require(len(prepared_names) == len(review_names) == len(selection_names) == 1, "deployment_evidence_roles_changed")
    prepared_name, review_name, selection_name = prepared_names[0], review_names[0], selection_names[0]
    metrics_name = str(PurePosixPath(prepared_name).with_name("comparison-metrics.json"))
    require(metrics_name in evidence and len({prepared_name, review_name, selection_name, metrics_name}) == 4, "deployment_metrics_missing")
    check_review_metadata(evidence[prepared_name], evidence[metrics_name], evidence[review_name],
                          tracker.hashes[prepared_name], tracker.hashes[metrics_name])
    check_selection(evidence[selection_name], manifest["profile"], tracker.hashes[prepared_name], tracker.hashes[review_name])
    track_python_runtime(tracker, evidence[prepared_name])
    tracker.assert_unchanged()
    return VerifiedBundle(manifest, f"hymt:{MODEL_SHA256}:{sha(raw)}", terms, tracker, sha(raw))
