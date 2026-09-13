"""Strict material-risk mapping for the pinned two-array meaning-v2 contract.

No model, runtime, evaluation dataset, DB or registration is accessed. The
caller must revalidate raw output against the original row: an internally
consistent envelope alone cannot authenticate where its quotes came from.
"""
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path


VERSION = "qwen-material-warning-policy-v3"
POLICY_PATH = Path(__file__).with_name("qwen-material-warning-policy-v3.json")
POLICY_SHA256 = "964593f6d8af8c5b819871922eee82227e6a75ec22042f5f869e263c647929ba"
CONTRACT_DIR = Path(__file__).with_name("llm-qwen35")
CONTRACT_FILES = {
    "contract_v2.py": "463561009a9eb966b97893d8447315730cf66b454332f7811c48a96b974b8378",
    "prompt-semantic-v2.txt": "3227a46ec7f9f5d4669a783dc5938df3c3f57720be16dc993eb8dce2c97b5ae7",
    "response-schema-v2.json": "a6785ded870ca55af1408c97db558ecfb23bb074d81e81b2546b5b17b0419cd7",
    "contract.py": "d4e6e8fd6e5d09729c1adf745468d383b217cba8da202b98bd66b26a76697a0b",
}
VALIDATION_CONTRACT = {
    "version": "qwen35-meaning-v2", "validatorSha256": CONTRACT_FILES["contract_v2.py"],
    "promptSha256": CONTRACT_FILES["prompt-semantic-v2.txt"],
    "schemaSha256": CONTRACT_FILES["response-schema-v2.json"],
    "genericHelperSha256": CONTRACT_FILES["contract.py"],
}


def _require(value, code):
    if not value:
        raise ValueError(code)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _same(left, right):
    # Ordinary Python equality conflates bool/int; reject nonfinite values too.
    try:
        return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return False


@lru_cache(maxsize=1)
def _load_contract():
    path = CONTRACT_DIR / "contract_v2.py"
    _require(_sha(path) == CONTRACT_FILES[path.name], "material_v3_contract_changed")
    spec = importlib.util.spec_from_file_location("material_v3_pinned_semantic_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verified_contract():
    _require(_sha(POLICY_PATH) == POLICY_SHA256, "material_v3_policy_changed")
    for name, expected in CONTRACT_FILES.items():
        _require(_sha(CONTRACT_DIR / name) == expected, "material_v3_contract_dependency_changed")
    contract = _load_contract()
    _require(_same(contract.contract_identity(), {
        "version": VALIDATION_CONTRACT["version"], "prompt_sha256": VALIDATION_CONTRACT["promptSha256"],
        "schema_sha256": VALIDATION_CONTRACT["schemaSha256"],
        "shared_helpers_sha256": VALIDATION_CONTRACT["genericHelperSha256"],
    }), "material_v3_contract_identity_differs")
    return contract


def identity():
    _verified_contract()
    return {"version": VERSION, "policySha256": POLICY_SHA256, "codeSha256": _sha(Path(__file__)),
            "validationContract": dict(VALIDATION_CONTRACT)}


def _content_errors(contract, content):
    errors = contract._shared._shape_errors(content, contract.load_schema())
    return errors or contract._shared._anchor_contract_errors(content)


def _check_envelope(contract, value):
    _require(type(value) is dict, "material_v3_expected_validated_object")
    expected_keys = set(contract._invalid_result("invalid_json", []))
    _require(set(value) == expected_keys, "material_v3_validated_keys_differ")
    for key, expected in contract._validation_metadata().items():
        _require(_same(value[key], expected), "material_v3_validator_metadata_differs")
    status = value["status"]
    _require(type(status) is str and status in ("valid", "invalid_json", "invalid_schema"),
             "material_v3_unknown_validation_status")
    errors = value["validation_errors"]
    _require(type(errors) is list and all(type(item) is str and item.strip() for item in errors),
             "material_v3_validation_errors_invalid")
    if status != "valid":
        if status == "invalid_json":
            _require(value["assessment"] is None and errors in
                     (["response: invalid_json"], ["response: expected_string"]), "material_v3_invalid_json_envelope_differs")
        else:
            expected_errors = _content_errors(contract, value["assessment"])
            _require(bool(expected_errors) and _same(errors, expected_errors), "material_v3_invalid_schema_envelope_differs")
        _require(_same(value, contract._invalid_result(status, errors, value["assessment"])),
                 "material_v3_invalid_envelope_differs")
        return False
    _require(not _content_errors(contract, value["assessment"]), "material_v3_content_not_two_array_contract")
    _require(errors == [], "material_v3_valid_with_errors")
    return True


def _check_diagnostics(value):
    content = value["assessment"]
    expected = {(collection, index, side): entry[side + "_quote"]
                for collection, entries in content.items() for index, entry in enumerate(entries)
                for side in ("source", "translation") if entry[side + "_quote"]}
    diagnostics, spans = value["span_diagnostics"], value["unique_spans"]
    _require(type(diagnostics) is list and type(spans) is list, "material_v3_diagnostics_not_lists")
    seen, expected_spans, located_targets = set(), [], set()
    keys = {"collection", "index", "side", "quote", "start", "end", "status"}
    for item in diagnostics:
        _require(type(item) is dict and set(item) == keys, "material_v3_diagnostic_shape_invalid")
        _require(type(item["collection"]) is str and item["collection"] in content
                 and type(item["index"]) is int and item["index"] >= 0
                 and type(item["side"]) is str and item["side"] in ("source", "translation"),
                 "material_v3_diagnostic_identity_invalid")
        key = item["collection"], item["index"], item["side"]
        _require(key in expected and key not in seen, "material_v3_duplicate_or_extra_diagnostic")
        seen.add(key)
        _require(type(item["quote"]) is str and item["quote"] == expected[key], "material_v3_diagnostic_quote_differs")
        _require(type(item["status"]) is str and item["status"] in ("unique", "ambiguous", "not_found"),
                 "material_v3_diagnostic_status_invalid")
        if item["status"] == "unique":
            start, end = item["start"], item["end"]
            _require(type(start) is int and type(end) is int and 0 <= start < end
                     and end - start == len(item["quote"].encode("utf-16-le")) // 2,
                     "material_v3_unique_utf16_extent_invalid")
            expected_spans.append({k: item[k] for k in item if k != "status"})
            if item["collection"] == "semantic_issues" and item["side"] == "translation":
                located_targets.add(item["index"])
        else:
            _require(item["start"] is None and item["end"] is None, "material_v3_unverified_has_offsets")
    _require(seen == set(expected), "material_v3_missing_quote_diagnostic")
    _require(_same(spans, expected_spans), "material_v3_unique_spans_differ")
    issues, uncertainties = content["semantic_issues"], content["uncertainties"]
    unverified = sum(item["status"] != "unique" for item in diagnostics)
    expected_fields = {
        "error_count": len(issues), "uncertainty_count": len(uncertainties),
        "semantic": "issues_found" if issues else "unsure" if uncertainties else "no_findings",
        "unverified_span_count": unverified,
        "span_status": "unverified" if unverified else "verified" if diagnostics else "none",
        "whole_paragraph_review": bool(issues or uncertainties or unverified),
    }
    _require(all(_same(value[key], wanted) for key, wanted in expected_fields.items()),
             "material_v3_derived_validation_fields_differ")
    return located_targets


def classify(validated):
    contract = _verified_contract()
    base = {"version": VERSION, "validationContract": dict(VALIDATION_CONTRACT),
            "semanticSpanPrecision": "not_evaluated"}
    if not _check_envelope(contract, validated):
        return base | {"status": "unassessed", "primaryWarning": None, "supplementaryMajorWarning": None,
                       "noMaterialRiskAsserted": False, "redCandidateIndices": []}
    located_targets = _check_diagnostics(validated)
    issues, uncertainties = (validated["assessment"][key] for key in ("semantic_issues", "uncertainties"))
    major_indices = [index for index, item in enumerate(issues) if item["severity"] in ("major", "critical")]
    warning = bool(major_indices or uncertainties)
    return base | {
        "status": "assessed_model_assertions", "primaryWarning": warning,
        "supplementaryMajorWarning": bool(major_indices), "noMaterialRiskAsserted": not warning,
        "majorCriticalAssertionCount": len(major_indices), "minorAssertionCount": len(issues) - len(major_indices),
        "uncertaintyCount": len(uncertainties),
        "unlocalizedMajorCriticalCount": sum(index not in located_targets for index in major_indices),
        "redCandidateIndices": [index for index in major_indices if index in located_targets],
        "contractDiagnosticWholeParagraphReview": validated["whole_paragraph_review"],
        "allQuoteDiagnosticsCount": len(validated["span_diagnostics"]),
        "allUnverifiedQuoteCount": validated["unverified_span_count"],
    }
