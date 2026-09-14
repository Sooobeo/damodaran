"""Read-only replay of frozen S2 inputs using recorded native counts, not inference.

The optional report is written exclusively outside the frozen attempt directory.
No native tokenizer, DLL, server, database or evaluation annotation is opened.
This checks evidence consistency; it does not re-execute native round trips.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_preparation_v1 import prepare_inputs as prepare


VERSION = "input-preparation-v1-s2-prepared-verifier-v1"
ATTEMPT = ROOT / ".training/comparisons/input-preparation-v1/s2-prepared/attempt-002"
MANIFEST_SHA = "b007e567881c1a6dfa556c775eda65916aa49d76559dedc14420c0e127866653"
REPORT = ROOT / ".training/verifications/input-preparation-v1-s2-prepared-verification-20260913.json"
CONFIGURATIONS = ("C0", "C1", "C2", "C3")
HEX = re.compile(r"[0-9a-f]{64}")
EXPECTED_VOCAB = {"bos": 127958, "eos": 127960, "eot": 127967, "n_tokens": 128167,
                  "addBos": False, "addEos": False, "eogIds": [127957, 127960, 127967]}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def text_hash(text):
    return sha(text.encode("utf-8"))


def json_hash(value):
    return text_hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))


def read_json(path):
    return json.loads(Path(path).read_text("utf-8"))


def local_path(base, relative):
    require(isinstance(relative, str) and relative and "\\" not in relative, "invalid_evidence_path")
    candidate = base / relative
    require(not Path(relative).is_absolute() and ".." not in Path(relative).parts
            and candidate.resolve().is_relative_to(base.resolve()), "evidence_path_escape")
    require(not any(path.is_symlink() for path in (candidate, *candidate.parents)), "evidence_symlink")
    return candidate


def verify_hash_entries(base, entries, *, with_size=False):
    require(isinstance(entries, list) and entries, "missing_hash_entries")
    paths = [entry.get("path") for entry in entries]
    require(len(paths) == len(set(paths)), "duplicate_hash_entry")
    verified = []
    for entry in entries:
        path = local_path(base, entry["path"])
        expected = entry.get("sha256")
        require(isinstance(expected, str) and HEX.fullmatch(expected), "invalid_evidence_sha256")
        require(path.is_file(), "missing_evidence_file")
        actual = prepare.file_hash(path)
        require(actual == expected, "evidence_hash_mismatch:" + entry["path"])
        size = path.stat().st_size
        if with_size:
            require(size == entry["bytes"], "evidence_size_mismatch")
        verified.append({"path": path.relative_to(ROOT).as_posix(), "sha256": actual, "bytes": size})
    return verified


def is_forbidden_path(relative):
    lower = relative.lower().replace("\\", "/")
    return (any(part in lower for part in ("/evaluation/", "/evaluations/", "-evaluation.json",
            "quality-evaluation/", "/holdout", "library.sqlite"))
            or lower.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm")))


class VerificationReadAudit:
    """A verifier-level guard, including refusal of process and native loading."""
    def __init__(self):
        self.reads = set()
        self.blocked = []

    def __call__(self, event, args):
        if event in {"subprocess.Popen", "os.system", "ctypes.dlopen"}:
            self.blocked.append(event)
            raise ValueError("verifier_native_or_process_action_refused")
        if event != "open" or not isinstance(args[0], (str, bytes)):
            return
        path = Path(os.fsdecode(args[0])).resolve()
        mode = args[1]
        if not isinstance(mode, str):
            return
        writing = any(character in mode for character in "wax+")
        if writing:
            require(path == REPORT.resolve() and "x" in mode, "verifier_write_outside_exclusive_report")
            return
        if "r" not in mode or not path.is_relative_to(ROOT):
            return
        relative = path.relative_to(ROOT).as_posix()
        require(not is_forbidden_path(relative), "verifier_forbidden_evaluation_or_database_read")
        if relative.startswith(".training/") and relative.endswith((".json", ".jsonl")):
            require(relative in prepare.SOURCE_PATHS or path.is_relative_to(ATTEMPT),
                    "verifier_historical_or_unapproved_data_read")
        self.reads.add(relative)


def verify_receipt(receipt):
    require(receipt.get("failed") is False and receipt.get("exitCode") == 0
            and receipt.get("ownedProcessExited") is True, "native_termination_not_verified")
    require(receipt.get("vocabularyOnly") is True and receipt.get("contextCreated") is False
            and receipt.get("generationCalls") == 0, "native_generation_scope_mismatch")
    require(receipt.get("addSpecial") is False and receipt.get("parseSpecial") is True
            and receipt.get("outputTokenReserve") == 4096 and receipt.get("contextSize") == 8192
            and receipt.get("maxPromptTokens") == 4095, "native_tokenization_contract_mismatch")
    ownership = receipt.get("ownership", {})
    require(ownership == {"atomicJobAssignment": True, "verifiedChildMembership": True,
                          "killOnOwnerExit": True, "stopByOwnedHandle": True}, "native_ownership_missing")
    ready = receipt.get("ready", {})
    require(ready.get("event") == "ready" and ready.get("vocab") == EXPECTED_VOCAB
            and ready.get("vocabularyOnly") is True and ready.get("contextCreated") is False
            and ready.get("generationCalls") == 0, "native_ready_contract_mismatch")
    probes = [receipt.get(key, {}) for key in ("processAtSpawn", "processAtReady", "processAtClose")]
    require(all(probe.get("pid") == ready.get("pid") and probe.get("priorityClass") == 16384 for probe in probes)
            and len({probe.get("creationTime100ns") for probe in probes}) == 1
            and type(probes[0].get("creationTime100ns")) is int,
            "native_owned_identity_changed")
    cap = receipt["preflight"].get("processCommitCapBytes")
    require(cap == 1024 ** 3 and all(type(probe.get("peakCommitBytes")) is int
            and 0 <= probe["peakCommitBytes"] <= cap for probe in probes), "native_memory_cap_evidence")
    calls = receipt.get("calls")
    require(isinstance(calls, list) and len(calls) == 88 and receipt.get("tokenizationCalls") == len(calls),
            "native_call_count_mismatch")
    require(receipt.get("shutdown") == {"event": "closed", "calls": len(calls),
                                        "generationCalls": 0, "contextCreated": False}, "native_shutdown_mismatch")
    require([call.get("id") for call in calls] == list(range(1, len(calls) + 1)), "native_call_order_mismatch")
    require(len({call.get("textSha256") for call in calls}) == len(calls), "duplicate_native_text_hash")
    for call in calls:
        require(call.get("roundtrip") is True and type(call.get("count")) is int
                and 0 < call["count"] <= 4095 and type(call.get("utf8Bytes")) is int
                and 0 < call["utf8Bytes"] <= 300000, "native_call_value_mismatch")
        require(all(isinstance(call.get(key), str) and HEX.fullmatch(call[key])
                    for key in ("textSha256", "tokensSha256")), "native_call_hash_missing")
    return {call["textSha256"]: call for call in calls}


class RecordedCounter:
    """Use only already-recorded native counts. Never estimate unknown prompts."""
    def __init__(self, calls):
        self.calls = calls
        self.seen = {}
        self.first_use_order = []
        self.lookup_count = 0

    def __call__(self, prompt):
        raw = prompt.encode("utf-8")
        key = sha(raw)
        require(key in self.calls, "replay_prompt_not_in_recorded_native_calls")
        call = self.calls[key]
        require(call["utf8Bytes"] == len(raw), "replay_prompt_byte_count_mismatch")
        if key in self.seen:
            require(self.seen[key] == prompt, "replay_prompt_hash_collision")
        else:
            self.seen[key] = prompt
            self.first_use_order.append(call["id"])
        self.lookup_count += 1
        return call["count"]


def independent_c0_context(unit):
    document = unit["document"]
    target = next(block for block in document["blocks"] if block["id"] == unit["targetBlockId"])
    neighbors = sorted((block for block in document["blocks"] if target["order"] - 1 <= block["order"] <= target["order"] + 1),
                       key=lambda block: block["order"])
    text = document["title"] + "\n" + document.get("titleKo", "") + "\n" + "\n".join(block["text"] for block in neighbors)
    return text.encode("utf-16-le", errors="surrogatepass")[:20000].decode("utf-16-le", errors="surrogatepass")


def verify_parent_read_audit(audit, preflight, manifest, source_identity):
    paths = audit.get("files")
    require(isinstance(paths, list) and paths == sorted(set(paths)), "source_read_audit_duplicate_or_order")
    require(audit.get("scope") == "parent_python_open_audit" and audit.get("annotationFilesOpened") == 0
            and audit.get("historicalOutputsOpened") == 0 and audit.get("databaseOpened") is False,
            "source_read_audit_scope_mismatch")
    require(not any(is_forbidden_path(path) for path in paths), "source_read_audit_forbidden_file")
    identity = read_json(prepare.S1 / "audit-identity.json")
    allowed = {entry["path"] for entry in identity["files"]}
    allowed.update(entry["path"] for entry in source_identity["sourceAndDictionaryReads"])
    allowed.update(entry["path"] for entry in manifest["code"])
    allowed.add("content/model-comparison/input-preparation-v1/freeze-manifest.json")
    require(set(paths).issubset(allowed), "source_read_audit_unapproved_file")
    require(set(prepare.SOURCE_PATHS).issubset(paths), "source_read_audit_missing_source")
    # GGUF tokenStrings has integer keys in memory and JSON string keys on disk.
    require(preflight["identity"] == json.loads(json.dumps(source_identity, ensure_ascii=False)),
            "source_preflight_identity_mismatch")
    return {"scope": audit["scope"], "recordedReads": len(paths), "forbiddenFiles": [],
            "annotationFilesOpened": 0, "historicalOutputsOpened": 0, "databaseOpened": False,
            "childScope": "pinned_vocab_worker_code_and_receipt_not_parent_open_audit"}


def verify():
    require(prepare.file_hash(ATTEMPT / "manifest.json") == MANIFEST_SHA, "prepared_manifest_changed")
    manifest = read_json(ATTEMPT / "manifest.json")
    require(manifest.get("status") == "prepared" and manifest.get("stage") == "S2"
            and manifest.get("units") == 16 and manifest.get("prompts") == 64, "prepared_manifest_scope")
    require(manifest.get("translationGenerations") == 0 and manifest.get("modelWeightTensorLoads") == 0
            and manifest.get("nativeVocabularyLoads") == 1 and manifest.get("appDeploymentChanged") is False
            and manifest.get("annotationFilesOpened") == 0 and manifest.get("maxPromptTokens") == 4095,
            "prepared_manifest_execution_scope")
    require(len(manifest["code"]) == 9 and len(manifest["artifacts"]) == 5, "prepared_inventory_changed")
    code = verify_hash_entries(ROOT, manifest["code"])
    artifacts = verify_hash_entries(ATTEMPT, manifest["artifacts"], with_size=True)
    require({path.name for path in ATTEMPT.iterdir()} == {entry["path"] for entry in manifest["artifacts"]} | {"manifest.json"},
            "unexpected_prepared_artifact")
    preflight = read_json(ATTEMPT / "preflight.json")
    require(preflight.get("code") == manifest["code"] and preflight.get("plannedPrompts") == 64
            and preflight.get("plannedGenerationCalls") == 0 and preflight.get("sourceOnly") is True,
            "prepared_preflight_mismatch")
    receipt = read_json(ATTEMPT / "tokenizer-receipt.json")
    calls = verify_receipt(receipt)
    log = (ATTEMPT / "tokenizer-native.stderr.log").read_text("utf-8")
    require(re.search(r"vocab_only\s*=\s*1", log) is not None and "vocab only - skipping tensors" in log,
            "native_log_vocab_only_evidence_missing")
    require("llama_init_from_model" not in log and "llama_decode" not in log, "unexpected_native_generation_log")
    units, dictionary, terms, template, identity = prepare.read_inputs()
    require(receipt["ready"]["modelSha256"] in identity["registeredIdentity"]["identity"], "native_model_identity_mismatch")
    require(identity["registeredIdentity"]["execution"]["templateSha256"] == text_hash(template), "shared_template_changed")
    parent_audit = verify_parent_read_audit(read_json(ATTEMPT / "source-read-audit.json"), preflight, manifest, identity)
    rows = [json.loads(line) for line in (ATTEMPT / "prompts.jsonl").read_text("utf-8").splitlines() if line.strip()]
    expected_keys = [(unit["id"], configuration) for unit in units for configuration in CONFIGURATIONS]
    require(len(rows) == 64 and [(row.get("id"), row.get("configuration")) for row in rows] == expected_keys,
            "prepared_unit_configuration_coverage")
    counter = RecordedCounter(calls)
    unit_index = {unit["id"]: unit for unit in units}
    comparisons = []
    for row in rows:
        unit = unit_index[row["id"]]
        document = unit["document"]
        target = next(block for block in document["blocks"] if block["id"] == unit["targetBlockId"])
        require(row.get("resourceId") == document["resourceId"] and row.get("sourceVersionId") == document["sourceVersionId"]
                and row.get("targetBlockId") == target["id"] and row.get("source") == target["text"]
                and row.get("sourceSha256") == text_hash(target["text"]), "prepared_source_identity_or_bytes_changed")
        tokens = row.get("tokenIds")
        require(isinstance(tokens, list) and all(type(token) is int and 0 <= token < 128167 for token in tokens)
                and len(tokens) == row.get("promptTokens"), "prepared_token_ids_invalid")
        token_sha = text_hash(json.dumps(tokens, separators=(",", ":")))
        prompt_sha = text_hash(row["prompt"])
        require(prompt_sha == row.get("promptSha256") and prompt_sha in calls
                and token_sha == row.get("tokenIdsSha256") == calls[prompt_sha]["tokensSha256"]
                and len(tokens) == calls[prompt_sha]["count"], "prepared_native_token_evidence_mismatch")
        require(row.get("withinBudget") is True and type(row.get("promptTokens")) is int
                and row["promptTokens"] + 4096 < 8192, "prepared_prompt_over_budget")
        rebuilt = prepare.prepare_one(prepare.sanitize_unit(unit), row["configuration"], terms, dictionary, template, counter)
        rebuilt.update(id=unit["id"], domain=unit["domain"], provenance=unit["provenance"],
                       tokenIds=tokens, tokenIdsSha256=token_sha)
        require(rebuilt == row, "prepared_source_context_hints_or_prompt_replay_mismatch")
        if row["configuration"] in {"C0", "C2"}:
            require(row["context"]["text"] == independent_c0_context(unit), "independent_c0_context_mismatch")
        if row["configuration"] == "C0":
            content, matches = prepare.baseline.build_user_prompt(
                {"source": target["text"], "context": independent_c0_context(unit)}, "contextual", terms)
            require(row["userPrompt"] == content and row["prompt"] == prepare.baseline.render_prompt(template, content)
                    and row["terminology"]["decisions"] == matches, "independent_c0_builder_renderer_mismatch")
        comparisons.append({"id": row["id"], "configuration": row["configuration"],
            "sourceSha256": row["sourceSha256"], "promptSha256": prompt_sha, "tokenIdsSha256": token_sha,
            "promptTokens": row["promptTokens"], "contextFragments": len(row["context"]["fragments"]),
            "hintLines": len(row["terminology"]["hints"]), "exactReplay": True})
    require(counter.first_use_order == list(range(1, 89)) and set(counter.seen) == set(calls),
            "recorded_native_call_coverage_or_order_mismatch")
    configuration_stats = {}
    decision_counts = {}
    by_key = {(row["id"], row["configuration"]): row for row in rows}
    for configuration in CONFIGURATIONS:
        subset = [row for row in rows if row["configuration"] == configuration]
        configuration_stats[configuration] = {"prompts": len(subset),
            "minTokens": min(row["promptTokens"] for row in subset),
            "maxTokens": max(row["promptTokens"] for row in subset),
            "totalTokens": sum(row["promptTokens"] for row in subset),
            "hintLines": sum(len(row["terminology"]["hints"]) for row in subset),
            "budgetExclusions": sum(sum(fragment["reason"] == "budget_excluded" for fragment in row["context"]["excluded"]) for row in subset)}
        if configuration in {"C2", "C3"}:
            decision_counts[configuration] = dict(sorted(Counter(decision["promptAction"] for row in subset
                                               for decision in row["terminology"]["decisions"]).items()))
    require(configuration_stats == manifest["configurationStats"], "prepared_stats_mismatch")
    dictionary_changes = []
    for unit in units:
        current = by_key[(unit["id"], "C0")]
        new = by_key[(unit["id"], "C2")]
        if current["promptSha256"] != new["promptSha256"]:
            dictionary_changes.append({"id": unit["id"], "previousHintLines": len(current["terminology"]["hints"]),
                                       "newHintLines": len(new["terminology"]["hints"]),
                                       "sourceAndContextUnchanged": current["source"] == new["source"]
                                           and current["context"]["text"] == new["context"]["text"]})
    require(all(configuration_stats[config]["hintLines"] == 0 for config in ("C2", "C3")),
            "unexpected_new_financial_hints_in_frozen_attempt")
    # Detect mutation during the replay too, without changing the frozen files.
    verify_hash_entries(ROOT, manifest["code"])
    verify_hash_entries(ATTEMPT, manifest["artifacts"], with_size=True)
    require(prepare.file_hash(ATTEMPT / "manifest.json") == MANIFEST_SHA, "manifest_changed_during_replay")
    return {"version": VERSION, "status": "verified", "verifiedAt": datetime.now(timezone.utc).isoformat(),
        "preparedAttempt": ATTEMPT.relative_to(ROOT).as_posix(), "preparedManifestSha256": MANIFEST_SHA,
        "verificationMode": "recorded_native_count_lookup_and_source_only_deterministic_replay",
        "nativeRetokenizationCalls": 0, "nativeRoundtripReexecuted": False,
        "translationGenerations": 0, "nativeDllLoads": 0, "appOrDatabaseActions": 0,
        "sourceExposure": "verifier_read_frozen_source16_after_selector_code_freeze_no_selector_edits",
        "annotationFilesOpened": 0, "evaluationTermsRead": 0,
        "unitsVerified": len(units), "promptsVerified": len(rows), "codeFilesVerified": len(code),
        "artifactFilesVerified": len(artifacts), "recordedNativeCallsVerified": len(calls),
        "recordedCounterLookups": counter.lookup_count, "reconstructedNativeCallOrderExact": True,
        "uniqueFinalPromptHashes": len({row["promptSha256"] for row in rows}),
        "nativeRecordedRoundtrips": len(calls), "ownedProcessExitRecorded": True,
        "originalNativeElapsedSeconds": receipt["elapsedSeconds"], "maxPromptTokensObserved": max(row["promptTokens"] for row in rows),
        "maxPromptTokensAllowed": 4095, "configurationStats": configuration_stats,
        "newDictionaryDecisionCounts": decision_counts, "dictionaryOnlyPromptChanges": dictionary_changes,
        "c0ExactIndependentBuilderRendererParity": True, "allRawSourceBytesPreserved": True,
        "allContextHintsPromptMetadataReplayExact": True, "sourceReadAudit": parent_audit,
        "registeredSourceIdentity": identity, "code": code, "artifacts": artifacts, "rows": comparisons,
        "limits": ["No fresh native tokenization or model generation was run by this verifier.",
                   "Roundtrip and ownership execution are checks of the original frozen receipt, not new process observations.",
                   "Native server endpoint token parity remains pending S4.",
                   "C2/C3 emit zero financial hints here; this does not demonstrate successful new financial sense selection.",
                   "No translation quality, evaluation questions, 39-term inventory, or application deployment was assessed."]}


def self_test():
    """Small negative diagnostics for the replay guard; no files or source data."""
    text = "synthetic whole prompt"
    key = text_hash(text)
    calls = {key: {"id": 1, "utf8Bytes": len(text.encode()), "count": 7}}
    counter = RecordedCounter(calls)
    require(counter(text) == 7 and counter(text) == 7 and counter.first_use_order == [1], "selftest_counter")
    refused = 0
    for callback in (lambda: counter("different prompt"),
                     lambda: local_path(ROOT, "../escape.json"),
                     lambda: verify_receipt({"failed": True})):
        try:
            callback()
        except ValueError:
            refused += 1
    require(refused == 3, "selftest_failed_to_reject_bad_evidence")
    require(is_forbidden_path(".training/quality-evaluation/hidden.json")
            and is_forbidden_path("data/library.sqlite")
            and not is_forbidden_path("content/model-comparison/input-preparation-v1/general-inputs.json"), "selftest_read_boundary")
    return {"syntheticDiagnostics": 5, "status": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()))
        return
    require(not args.write_report or not REPORT.exists(), "verification_report_already_exists")
    guard = VerificationReadAudit()
    sys.addaudithook(guard)
    diagnostics = self_test()
    evidence = verify()
    evidence["verifier"] = {"path": Path(__file__).relative_to(ROOT).as_posix(), "sha256": prepare.file_hash(Path(__file__))}
    evidence["verifierReadAudit"] = {"scope": "verifier_python_open_audit", "files": sorted(guard.reads),
                                     "blockedActions": guard.blocked, "nativeOrProcessActions": 0}
    evidence["syntheticDiagnostics"] = diagnostics
    if args.write_report:
        require(REPORT.parent.resolve().is_relative_to(ROOT.resolve()) and REPORT.parent.is_dir(), "report_parent_missing")
        with REPORT.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(evidence, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps({"status": evidence["status"], "units": evidence["unitsVerified"],
        "prompts": evidence["promptsVerified"], "recordedNativeCalls": evidence["recordedNativeCallsVerified"],
        "nativeRetokenizationCalls": 0, "configurationStats": evidence["configurationStats"],
        "dictionaryOnlyPromptChanges": evidence["dictionaryOnlyPromptChanges"],
        "report": REPORT.relative_to(ROOT).as_posix() if args.write_report else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
