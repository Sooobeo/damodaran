"""Frozen, reference-free review contract; standard library only, no inference.

Only id/source/translation/context are accepted as input. The id never reaches
the model. A valid JSON response is a model assertion, not verified semantics.
Quote matching establishes text locations only, not the truth of an assertion.
Freeze prompt/schema before inference; changing either after seeing results
requires a new version rather than rewriting v1 or its recorded hashes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any


CONTRACT_VERSION = "qwen35-meaning-v1"
PROMPT_PATH = Path(__file__).with_name("prompt-v1.txt")
SCHEMA_PATH = Path(__file__).with_name("response-schema-v1.json")
PROMPT_SHA256 = "bda24929520798c7cc95643f153d92071b6bb8dc0dc733f202d319068a9fba1f"
SCHEMA_SHA256 = "449b9853115e100558e0d2e8727de7e7997cb72254880be1a095cbe55252e214"
SAMPLING_PARAMETERS = MappingProxyType({
    "max_tokens": 2048,
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
    "repeat_penalty": 1.0,
    "seed": 20260911,
})
INPUT_KEYS = frozenset({"id", "source", "translation", "context"})
REQUIRED_INPUT_KEYS = frozenset({"id", "source", "translation"})
INPUT_CHARACTER_LIMITS = MappingProxyType({
    "id": 512, "source": 24000, "translation": 24000, "context": 12000,
})
CHAT_CONTROL_TOKENS = ("<|im_start|>", "<|im_end|>", "<|endoftext|>", "<think>", "</think>")


class ContractError(ValueError):
    """A contract violation; messages intentionally omit input text."""


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ContractError("non_finite_json_constant")


def _parse_json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_object_pairs,
                      parse_constant=_reject_constant)


def _valid_unicode(text: str) -> bool:
    try:
        text.encode("utf-8", errors="strict")
        return True
    except UnicodeEncodeError:
        return False


def _input_row(value: Any, location: str = "input row") -> dict[str, str]:
    if type(value) is not dict:
        raise ContractError(f"{location}: expected_object")
    if set(value) - INPUT_KEYS:
        raise ContractError(f"{location}: unsupported_keys")
    if not REQUIRED_INPUT_KEYS.issubset(value):
        raise ContractError(f"{location}: missing_required_keys")
    for key, text in value.items():
        if type(text) is not str or not _valid_unicode(text):
            raise ContractError(f"{location}: {key}: expected_unicode_string")
        if len(text) > INPUT_CHARACTER_LIMITS[key]:
            raise ContractError(f"{location}: {key}: character_limit_exceeded")
        if key != "id" and any(token in text for token in CHAT_CONTROL_TOKENS):
            raise ContractError(f"{location}: {key}: chat_control_token")
    if not value["id"].strip() or not value["source"].strip():
        raise ContractError(f"{location}: blank_id_or_source")
    # An empty translation is valid input: its meaning may be entirely omitted.
    return {"id": value["id"], "source": value["source"],
            "translation": value["translation"], "context": value.get("context", "")}


def read_input(path: str | Path) -> tuple[list[dict[str, str]], str]:
    """Read UTF-8 JSONL or a JSON array and hash the exact original file bytes.

    Empty JSONL lines and a UTF-8 BOM are accepted; text is never normalized or
    truncated. Limits count Python Unicode characters, not tokens: source and
    translation 24,000 each, context 12,000, id 512. The runner must also tokenize
    the real chat template and reserve output space before model execution.
    Top-level wrapper objects and extra row metadata are rejected. Known chat
    boundary literals are rejected rather than interpreted or silently escaped.
    """
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ContractError("input: invalid_utf8") from exc
    try:
        if text.lstrip().startswith("["):
            values = _parse_json(text)
            if type(values) is not list:
                raise ContractError("input: expected_array")
        else:
            # JSONL uses LF, not every Unicode line separator. U+2028/U+2029 can
            # legally occur inside a JSON string and must remain original text.
            values = [_parse_json(line) for line in text.split("\n") if line.strip()]
    except (json.JSONDecodeError, ContractError, RecursionError) as exc:
        raise ContractError("input: invalid_json") from exc
    if not values:
        raise ContractError("input: no_rows")
    rows = [_input_row(value, f"input row {index + 1}")
            for index, value in enumerate(values)]
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ContractError("input: duplicate_ids")
    return rows, hashlib.sha256(raw).hexdigest()


def _frozen_bytes(path: Path, expected_sha256: str) -> bytes:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ContractError(f"frozen_contract_changed: {path.name}; create_v2")
    return raw


def load_schema() -> dict[str, Any]:
    """Return a fresh schema after verifying the frozen bytes."""
    return _parse_json(_frozen_bytes(SCHEMA_PATH, SCHEMA_SHA256).decode("utf-8"))


def load_prompt() -> str:
    return _frozen_bytes(PROMPT_PATH, PROMPT_SHA256).decode("utf-8")


def contract_identity() -> dict[str, str]:
    load_prompt()
    load_schema()
    return {"version": CONTRACT_VERSION, "prompt_sha256": PROMPT_SHA256,
            "schema_sha256": SCHEMA_SHA256}


def build_request(row: dict[str, str]) -> dict[str, Any]:
    """Build a llama chat-compatible request body; caller supplies model alias.

    This function does not call HTTP, tokenize, launch a model, or read any
    reference. The runner must separately validate context length/termination.
    """
    checked = _input_row(row)
    model_input = {key: checked[key] for key in ("source", "translation", "context")}
    return {
        "messages": [
            {"role": "system", "content": load_prompt()},
            {"role": "user", "content": json.dumps(model_input, ensure_ascii=False,
                                                     separators=(",", ":"))},
        ],
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "qwen35_meaning_v1", "strict": True, "schema": load_schema(),
        }},
        "chat_template_kwargs": {"enable_thinking": False},
        "stream": False,
        **SAMPLING_PARAMETERS,
    }


def _shape_errors(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate the small, frozen schema vocabulary without dependencies."""
    errors: list[str] = []
    expected = {"object": dict, "array": list, "string": str}[schema["type"]]
    if type(value) is not expected:
        return [f"{path}: invalid_type"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: invalid_enum")
    if type(value) is str:
        if not _valid_unicode(value):
            errors.append(f"{path}: invalid_unicode")
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: too_short")
    elif type(value) is list:
        for index, item in enumerate(value):
            errors.extend(_shape_errors(item, schema["items"], f"{path}[{index}]"))
    else:
        properties = schema["properties"]
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            errors.append(f"{path}: unsupported_keys")
        for key in schema["required"]:
            if key not in value:
                errors.append(f"{path}.{key}: missing_required_key")
        for key in properties:
            if key in value:
                errors.extend(_shape_errors(value[key], properties[key], f"{path}.{key}"))
    return errors


def _anchor_contract_errors(assessment: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for collection, entries in assessment.items():
        for index, entry in enumerate(entries):
            path = f"$.{collection}[{index}]"
            if not entry["reason"].strip():
                errors.append(f"{path}.reason: blank_reason")
            source = entry.get("source_quote", "")
            translation = entry["translation_quote"]
            # Whitespace is not a meaningful anchor; preserve exact text otherwise.
            if (source and not source.strip()) or (translation and not translation.strip()):
                errors.append(f"{path}: whitespace_only_quote")
            if collection == "semantic_issues":
                expected = {"mistranslation": (True, True), "omission": (True, False),
                            "addition": (False, True)}[entry["kind"]]
                if (bool(source), bool(translation)) != expected:
                    errors.append(f"{path}: invalid_empty_anchor_contract")
            elif collection == "uncertainties" and not (source or translation):
                errors.append(f"{path}: at_least_one_quote_required")
    return errors


def _locate(text: str, quote: str) -> tuple[str, int | None, int | None]:
    first = text.find(quote)
    if first < 0:
        return "not_found", None, None
    # Include overlapping occurrences: 'aa' occurs twice in 'aaa'.
    if text.find(quote, first + 1) >= 0:
        return "ambiguous", None, None
    start = len(text[:first].encode("utf-16-le")) // 2
    end = start + len(quote.encode("utf-16-le")) // 2
    return "unique", start, end


def _invalid_result(status: str, errors: list[str], assessment: Any = None) -> dict[str, Any]:
    return {
        "status": status, "semantic": "unknown", "error_count": None,
        "uncertainty_count": None, "language_note_count": None,
        "span_status": "none", "unverified_span_count": 0,
        "whole_paragraph_review": True, "unique_spans": [], "span_diagnostics": [],
        "assessment": assessment, "validation_errors": errors,
    }


def validate_response(row: dict[str, str], raw_text: str) -> dict[str, Any]:
    """Validate format, retain assertions, and locate only exact unique quotes.

    status=valid means format/anchor structure passed, NOT semantic correctness.
    semantic is derived from model assertions; it is not an independent verdict.
    Missing/repeated quotes preserve assertions and force paragraph review; they
    never become no_findings. UTF-16 end offsets are exclusive.
    """
    checked = _input_row(row)
    if type(raw_text) is not str:
        return _invalid_result("invalid_json", ["response: expected_string"])
    try:
        assessment = _parse_json(raw_text)
    except (json.JSONDecodeError, ContractError, RecursionError):
        return _invalid_result("invalid_json", ["response: invalid_json"])
    errors = _shape_errors(assessment, load_schema())
    if not errors:
        errors = _anchor_contract_errors(assessment)
    if errors:
        return _invalid_result("invalid_schema", errors, assessment)

    unique_spans: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for collection, entries in assessment.items():
        for index, entry in enumerate(entries):
            for side in ("source", "translation"):
                quote = entry.get(f"{side}_quote", "")
                if not quote:
                    continue  # Intentional absent-side anchor, not a failed match.
                status, start, end = _locate(checked[side], quote)
                span = {"collection": collection, "index": index, "side": side,
                        "quote": quote, "start": start, "end": end}
                diagnostics.append({**span, "status": status})
                if status == "unique":
                    unique_spans.append(span)
    unverified = sum(item["status"] != "unique" for item in diagnostics)
    error_count = len(assessment["semantic_issues"])
    uncertainty_count = len(assessment["uncertainties"])
    semantic = ("issues_found" if error_count else
                "unsure" if uncertainty_count else "no_findings")
    return {
        "status": "valid", "semantic": semantic, "error_count": error_count,
        "uncertainty_count": uncertainty_count,
        "language_note_count": len(assessment["language_notes"]),
        "span_status": "unverified" if unverified else "verified" if diagnostics else "none",
        "unverified_span_count": unverified,
        # Even a located omission/uncertainty needs paragraph context. The runner
        # must not turn these independent observations into application underlines.
        "whole_paragraph_review": bool(error_count or uncertainty_count or unverified),
        "unique_spans": unique_spans, "span_diagnostics": diagnostics,
        "assessment": assessment, "validation_errors": [],
    }
