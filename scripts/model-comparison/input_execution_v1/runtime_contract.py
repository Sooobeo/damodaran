"""Pure validation for the frozen Hy7 C0-C3 S4 protocol; no HTTP or model loads.

The caller must persist each raw HTTP body before JSON decoding/validation and
stop its owned server on ProtocolError. A numeric warning is a quality result,
not permission to replace the output. Outer whitespace is never stripped.

Native contracts were checked against llama.cpp commit
e2d2c0d6aa9b996d5d3a3c1d5e24c8c19728bb3d/tools/server/{README.md,server-task.cpp}.
This module cannot certify semantic quality or decide candidate acceptance.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
import run_hymt as baseline

VERSION = "input-execution-v1-runtime-contract-v1"
CONTEXT_SIZE = 8192
OUTPUT_RESERVE = 4096
VOCAB_SIZE = 128167
CONFIGURATIONS = ("C0", "C1", "C2", "C3")
EOG_IDS = {127957, 127960, 127967}
BUILD_INFO = "b10874-e2d2c0d6a"
CONTROL = re.compile(r"<\|[^\n>]*\|>")
SOURCE_READ_KEYS = (
    "id", "configuration", "resourceId", "sourceVersionId", "targetBlockId",
    "source", "sourceSha256", "context", "terminology", "userPrompt", "prompt",
    "promptSha256", "promptTokens", "tokenIds", "tokenIdsSha256",
    "contextSize", "outputTokenReserve", "withinBudget",
)


class ProtocolError(ValueError):
    """Safe error code; raw source/response text is never in the exception."""


def require(condition, code):
    if not condition:
        raise ProtocolError(code)


def sha_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha_json(value):
    return sha_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False))


def _text(value, code, *, nonempty=True):
    require(isinstance(value, str), code)
    try:
        value.encode("utf-8", "strict")
    except UnicodeError:
        raise ProtocolError(code + "_unicode") from None
    require("\x00" not in value and "\ufffd" not in value, code + "_unicode")
    require(not nonempty or bool(value.strip()), code + "_empty")
    return value


def _object(response):
    require(isinstance(response, dict) and "error" not in response,
            "invalid_server_response")


def _tokens(value, code):
    require(isinstance(value, list) and all(type(token) is int and
            0 <= token < VOCAB_SIZE for token in value), code)
    return value


def validate_prompt_row(row):
    """Check source-only values; domain/provenance and evaluation keys are unused.

    The caller separately binds the entire S2 file to its frozen manifest hash.
    This validator does not accept an arbitrary row as a new frozen experiment.
    """
    require(isinstance(row, dict), "invalid_prompt_row")
    require(row.get("configuration") in CONFIGURATIONS, "unknown_configuration")
    for key in ("id", "resourceId", "sourceVersionId", "targetBlockId"):
        _text(row.get(key), "invalid_" + key)
    source = _text(row.get("source"), "invalid_source")
    user = _text(row.get("userPrompt"), "invalid_user_prompt")
    prompt = _text(row.get("prompt"), "invalid_prompt")
    require(row.get("sourceSha256") == sha_text(source), "source_hash_mismatch")
    require(row.get("promptSha256") == sha_text(prompt), "prompt_hash_mismatch")
    require(prompt == "<|startoftext|>" + user + "<|extra_0|>", "rendered_prompt_mismatch")
    require(user.endswith("\n\n[Source Text]\n" + source), "target_source_mismatch")
    require(CONTROL.search(user) is None, "source_control_token_injection")
    context = row.get("context")
    terms = row.get("terminology")
    require(isinstance(context, dict) and isinstance(context.get("text"), str), "invalid_context")
    require(isinstance(terms, dict) and isinstance(terms.get("hints"), list), "invalid_terminology")
    lines = []
    for hint in terms["hints"]:
        require(isinstance(hint, dict), "invalid_hint")
        for key in ("source", "target", "definition"):
            _text(hint.get(key), "invalid_hint_" + key)
        lines.append(f'{hint["source"]} → {hint["target"]} (뜻: {hint["definition"]})')
    expected_user = (baseline.CONTEXT_INSTRUCTION + "\n\n[Background Information]\n" +
                    (context["text"] or "None provided.") +
                    "\n\n[Conditional terminology references]\n" +
                    ("\n".join(lines) or "None matched.") + "\n\n[Source Text]\n" + source)
    require(user == expected_user, "prompt_fields_mismatch")
    tokens = _tokens(row.get("tokenIds"), "invalid_prompt_tokens")
    try:
        baseline.validate_prompt_tokens(tokens)
    except baseline.RunError as error:
        raise ProtocolError(str(error)) from None
    require(type(row.get("promptTokens")) is int and row["promptTokens"] == len(tokens),
            "prompt_count_mismatch")
    require(row.get("tokenIdsSha256") == sha_json(tokens), "prompt_token_hash_mismatch")
    require(row.get("contextSize") == CONTEXT_SIZE and row.get("outputTokenReserve") == OUTPUT_RESERVE
            and row.get("withinBudget") is True and len(tokens) + OUTPUT_RESERVE < CONTEXT_SIZE,
            "prompt_context_budget")
    return {"id": row["id"], "configuration": row["configuration"],
            "sourceSha256": row["sourceSha256"], "promptSha256": row["promptSha256"],
            "tokenIdsSha256": row["tokenIdsSha256"], "inputTokens": len(tokens)}


def source_only_row(row):
    validate_prompt_row(row)
    return deepcopy({key: row[key] for key in SOURCE_READ_KEYS})


def template_payload(row):
    validate_prompt_row(row)
    return {"messages": [{"role": "user", "content": row["userPrompt"]}],
            "add_generation_prompt": True}


def tokenize_payload(row):
    validate_prompt_row(row)
    return {"content": row["prompt"], "add_special": False, "parse_special": True}


def completion_payload(row):
    validate_prompt_row(row)
    return deepcopy(baseline.SAMPLING) | {"prompt": list(row["tokenIds"])}


def validate_props(props, *, model_path, template):
    _object(props)
    require(isinstance(props.get("model_path"), str) and
            Path(props["model_path"]).resolve() == Path(model_path).resolve(), "runtime_model_path")
    require(sha_text(template) == baseline.TEMPLATE_SHA and props.get("chat_template") == template,
            "runtime_chat_template")
    defaults = props.get("default_generation_settings")
    require(isinstance(defaults, dict) and type(defaults.get("n_ctx")) is int and
            defaults["n_ctx"] == CONTEXT_SIZE and type(props.get("total_slots")) is int and
            props["total_slots"] == 1, "actual_context_or_slots_mismatch")
    # The complete immutable executable hash is checked by the producer. Keep
    # build_info visible; known builds may use a longer prefix of this commit.
    build = props.get("build_info")
    require(isinstance(build, str) and re.fullmatch(r"b10874-e2d2c0d6a[0-9a-f]*", build),
            "runtime_build_info_mismatch")
    return {"contextSize": CONTEXT_SIZE, "totalSlots": 1, "buildInfo": build,
            "modelPathVerified": True, "templateSha256": sha_text(template)}


def validate_template_response(response, row):
    validate_prompt_row(row)
    _object(response)
    require(response.get("prompt") == row["prompt"], "native_template_prompt_mismatch")
    return {"promptSha256": row["promptSha256"], "nativeTemplateParity": True}


def validate_tokenize_response(response, row):
    validate_prompt_row(row)
    _object(response)
    tokens = _tokens(response.get("tokens"), "invalid_native_prompt_tokens")
    require(tokens == row["tokenIds"], "native_prompt_token_mismatch")
    return list(tokens)


def validate_detokenize_response(response, expected):
    _object(response)
    require(response.get("content") == expected, "native_detokenize_mismatch")
    return {"textSha256": sha_text(expected), "roundtrip": True}


def validate_generation_settings(settings):
    """Check all observable requested fields, including stable native defaults.

    b10874 task_params::to_json does not emit cache_prompt/return_tokens/id_slot;
    these are request-bound, with slot and returned-token evidence checked below.
    n_ctx belongs to /props, not this object.
    """
    try:
        baseline.validate_generation_settings(settings)
    except baseline.RunError as error:
        raise ProtocolError(str(error)) from None
    for key in ("ignore_eos", "top_k", "repeat_last_n", "seed", "n_predict", "mirostat"):
        require(type(settings.get(key)) is type(baseline.SAMPLING[key]),
                "actual_sampling_type_mismatch_" + key)
    for key in ("n_keep", "stream"):
        require(type(settings.get(key)) is type(baseline.SAMPLING[key]) and
                settings[key] == baseline.SAMPLING[key], "actual_sampling_mismatch_" + key)
    for key in ("grammar", "generation_prompt"):
        require(settings.get(key) == "", "unexpected_generation_constraint_" + key)
    for key in ("logit_bias", "lora", "preserved_tokens", "grammar_triggers"):
        require(settings.get(key) == [], "unexpected_generation_constraint_" + key)
    return deepcopy(settings)


def validate_completion(row, response, elapsed):
    """Return an unmodified completed output or raise a technical-stop error."""
    receipt = validate_prompt_row(row)
    _object(response)
    text = _text(response.get("content"), "invalid_translation")
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0,
            "invalid_generation_seconds")
    generated = response.get("tokens_predicted")
    require(type(generated) is int and 0 < generated < OUTPUT_RESERVE, "output_limit_or_count")
    tokens = _tokens(response.get("tokens"), "missing_generated_token_evidence")
    require(len(tokens) == generated, "generated_token_count_mismatch")
    require(response.get("stop") is True, "incomplete_stop_response")
    require(response.get("truncated") is False, "output_context_truncated")
    require(response.get("stop_type") in ("eos", "word"), "output_stop_limit_or_unknown")
    if response["stop_type"] == "eos":
        require(tokens[-1] in EOG_IDS and not (set(tokens[:-1]) & EOG_IDS), "output_eog_token_mismatch")
        require(response.get("stopping_word") == "", "unexpected_stopping_word")
    else:
        require(response.get("stopping_word") in baseline.SAMPLING["stop"], "unexpected_stopping_word")
    require(CONTROL.search(text) is None, "output_leaked_control_tokens")
    require(type(response.get("id_slot")) is int and response["id_slot"] == 0, "response_slot_mismatch")
    require(type(response.get("tokens_evaluated")) is int and
            response["tokens_evaluated"] == len(row["tokenIds"]), "evaluated_prompt_count_mismatch")
    settings = validate_generation_settings(response.get("generation_settings"))
    timings = response.get("timings")
    require(isinstance(timings, dict), "missing_native_timings")
    require(type(timings.get("predicted_n")) is int and timings["predicted_n"] == generated,
            "native_generated_timing_count_mismatch")
    require(type(timings.get("prompt_n")) is int and timings["prompt_n"] == len(row["tokenIds"]),
            "unexpected_prompt_cache_reuse")
    require(type(timings.get("cache_n")) is int and timings["cache_n"] == 0,
            "unexpected_prompt_cache_reuse")
    for key in ("prompt_ms", "predicted_ms"):
        require(type(timings.get(key)) in (int, float) and math.isfinite(timings[key]) and timings[key] >= 0,
                "invalid_native_timing_" + key)
    automatic = {
        "numbersPreserved": baseline.numeric_tokens(row["source"]) == baseline.numeric_tokens(text),
        "currencySymbolsPreserved": Counter(re.findall(r"[$€£¥₩]", row["source"])) ==
                                    Counter(re.findall(r"[$€£¥₩]", text)),
    }
    technical = {"nonEmpty": True, "notTruncated": True, "belowOutputLimit": True,
                 "noLeakedControlTokens": True, "stopEvidenceVerified": True,
                 "generationSettingsVerified": True, "promptAndOutputCountsVerified": True,
                 "noPromptCacheReuse": True}
    return receipt | {
        "status": "completed", "source": row["source"], "translation": text,
        "translationSha256": sha_text(text), "outputTokens": generated,
        "outputTokenIds": list(tokens), "outputTokenIdsSha256": sha_json(tokens),
        "technicalChecks": technical, "automaticChecks": automatic,
        "automaticChecksPassed": all(automatic.values()), "outputIntegrityPassed": True,
        "generationSeconds": float(elapsed), "stopType": response["stop_type"],
        "stoppingWord": response["stopping_word"], "truncated": False,
        "outputLimitReached": False, "timings": deepcopy(timings),
        "slotTokensAfterResponse": response.get("tokens_cached"),
        "actualGenerationSettings": settings, "normalization": "none",
        "postProcessingApplied": False, "translationMemoryApplied": False,
        "humanReviewed": False, "semanticQualityCertified": False,
        "runtimeContractVersion": VERSION,
    }
