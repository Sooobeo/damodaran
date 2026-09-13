"""Meaning-only v2 format/quote contract; no model, HTTP, DB, or registration.

The preserved v1 module supplies only unchanged generic validators and input
projection rules. Its request builder, frozen prompt/schema and response
validator are never called. Material-warning mapping belongs to a later module.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


CONTRACT_VERSION = "qwen35-meaning-v2"
PROMPT_PATH = Path(__file__).with_name("prompt-semantic-v2.txt")
SCHEMA_PATH = Path(__file__).with_name("response-schema-v2.json")
PROMPT_SHA256 = "3227a46ec7f9f5d4669a783dc5938df3c3f57720be16dc993eb8dce2c97b5ae7"
SCHEMA_SHA256 = "a6785ded870ca55af1408c97db558ecfb23bb074d81e81b2546b5b17b0419cd7"
SHARED_HELPERS_PATH = Path(__file__).with_name("contract.py")
SHARED_HELPERS_SHA256 = "d4e6e8fd6e5d09729c1adf745468d383b217cba8da202b98bd66b26a76697a0b"


def _verify_shared() -> None:
    if hashlib.sha256(SHARED_HELPERS_PATH.read_bytes()).hexdigest() != SHARED_HELPERS_SHA256:
        raise ValueError("frozen_generic_helpers_changed")


_verify_shared()
_spec = importlib.util.spec_from_file_location("qwen35_preserved_generic_validators_v1", SHARED_HELPERS_PATH)
_shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared)
ContractError = _shared.ContractError
SAMPLING_PARAMETERS = _shared.SAMPLING_PARAMETERS
INPUT_KEYS = _shared.INPUT_KEYS
INPUT_CHARACTER_LIMITS = _shared.INPUT_CHARACTER_LIMITS
CHAT_CONTROL_TOKENS = _shared.CHAT_CONTROL_TOKENS


def read_input(path: str | Path) -> tuple[list[dict[str, str]], str]:
    """Original-byte hash and strict id/source/translation/context input only."""
    _verify_shared()
    return _shared.read_input(path)


def _frozen_bytes(path: Path, expected: str) -> bytes:
    _verify_shared()
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ContractError(f"frozen_v2_contract_changed: {path.name}; create_new_version")
    return raw


def load_prompt() -> str:
    return _frozen_bytes(PROMPT_PATH, PROMPT_SHA256).decode("utf-8")


def load_schema() -> dict[str, Any]:
    """Return a fresh schema; caller mutation cannot change later requests."""
    return _shared._parse_json(_frozen_bytes(SCHEMA_PATH, SCHEMA_SHA256).decode("utf-8"))


def contract_identity() -> dict[str, str]:
    load_prompt()
    load_schema()
    return {"version": CONTRACT_VERSION, "prompt_sha256": PROMPT_SHA256,
            "schema_sha256": SCHEMA_SHA256, "shared_helpers_sha256": SHARED_HELPERS_SHA256}


def build_request(row: dict[str, str]) -> dict[str, Any]:
    """Build only a request object; real template, grammar and tokens need runtime checks."""
    _verify_shared()
    checked = _shared._input_row(row)
    model_input = {key: checked[key] for key in ("source", "translation", "context")}
    return {
        "messages": [
            {"role": "system", "content": load_prompt()},
            {"role": "user", "content": json.dumps(model_input, ensure_ascii=False, separators=(",", ":"))},
        ],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "qwen35_meaning_v2", "strict": True, "schema": load_schema(),
        }},
        "chat_template_kwargs": {"enable_thinking": False},
        "stream": False,
        **SAMPLING_PARAMETERS,
    }


def _validation_metadata() -> dict[str, Any]:
    return {"contract_version": CONTRACT_VERSION, "offset_unit": "utf16_code_units",
            "end_exclusive": True, "semantic_quality_certified": False,
            "semantic_span_precision": "not_evaluated",
            "count_basis": "reported_assertions_not_unique_adjudicated_errors"}


def _invalid_result(status: str, errors: list[str], assessment: Any = None) -> dict[str, Any]:
    return {**_validation_metadata(), "status": status, "semantic": "unknown", "error_count": None,
            "uncertainty_count": None, "span_status": "none", "unverified_span_count": 0,
            "whole_paragraph_review": True, "unique_spans": [], "span_diagnostics": [],
            "assessment": assessment, "validation_errors": errors}


def validate_response(row: dict[str, str], raw_text: str) -> dict[str, Any]:
    """Keep exact assertions; locate quotes without judging their meaning.

    Whole-paragraph review is the preserved diagnostic signal, NOT a material
    warning or an acceptance decision. Missing/ambiguous quotes do not erase
    severity. The caller must retain raw bytes and check EOS/truncation/sampling.
    """
    _verify_shared()
    checked = _shared._input_row(row)
    if type(raw_text) is not str:
        return _invalid_result("invalid_json", ["response: expected_string"])
    try:
        assessment = _shared._parse_json(raw_text)
    except (json.JSONDecodeError, ContractError, RecursionError):
        return _invalid_result("invalid_json", ["response: invalid_json"])
    errors = _shared._shape_errors(assessment, load_schema())
    if not errors:
        errors = _shared._anchor_contract_errors(assessment)
    if errors:
        return _invalid_result("invalid_schema", errors, assessment)

    unique_spans, diagnostics = [], []
    for collection, entries in assessment.items():
        for index, entry in enumerate(entries):
            for side in ("source", "translation"):
                quote = entry.get(f"{side}_quote", "")
                if not quote:
                    continue
                status, start, end = _shared._locate(checked[side], quote)
                span = {"collection": collection, "index": index, "side": side,
                        "quote": quote, "start": start, "end": end}
                diagnostics.append({**span, "status": status})
                if status == "unique":
                    unique_spans.append(span)
    unverified = sum(item["status"] != "unique" for item in diagnostics)
    error_count, uncertainty_count = len(assessment["semantic_issues"]), len(assessment["uncertainties"])
    semantic = "issues_found" if error_count else "unsure" if uncertainty_count else "no_findings"
    return {**_validation_metadata(), "status": "valid", "semantic": semantic,
            "error_count": error_count, "uncertainty_count": uncertainty_count,
            "span_status": "unverified" if unverified else "verified" if diagnostics else "none",
            "unverified_span_count": unverified,
            "whole_paragraph_review": bool(error_count or uncertainty_count or unverified),
            "unique_spans": unique_spans, "span_diagnostics": diagnostics,
            "assessment": assessment, "validation_errors": []}
