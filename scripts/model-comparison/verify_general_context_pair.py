"""Verify two completed general16 profiles of one candidate, without quality scores."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import general_context_input as data
import run_general_context as runner


def check_pair_identity(raw, contextual):
    require = data.require
    require(raw.get("profile") == "raw" and contextual.get("profile") == "contextual", "profile_pair_required")
    for result in (raw, contextual):
        require(result.get("version") == runner.VERSION and result.get("status") == "completed"
                and result.get("completed") == result.get("recordedCount") == result.get("expectedCount") == 16,
                "both_complete_general16_required")
        require(result.get("modelLoaded") is True and result.get("childProcessStopped") is True
                and result.get("integrityVerified") is True and result.get("completionRequestsSent") == 16,
                "run_evidence_incomplete")
        require(result.get("trainingPerformed") is False and result.get("postProcessingApplied") is False
                and result.get("translationMemoryApplied") is False and result.get("humanReviewed") is False,
                "unexpected_processing")
    for field in ("candidate", "modelSha256", "input", "catalog", "codeHashes", "sampling", "contextSize",
                  "runtimeOverrides", "samplingNormalization", "ramBudgetGiB", "installation",
                  "installationManifestSha256", "timeLimitsSeconds"):
        require(field in raw and field in contextual and raw[field] == contextual[field], "pair_identity_mismatch_" + field)
    require(raw["candidate"] in ("hy7", "hy30"), "unknown_candidate")


def load_run(directory):
    directory = Path(directory).resolve()
    boundary = runner.common.COMPARISONS.resolve()
    data.require(directory.is_relative_to(boundary) and directory != boundary, "run_path_not_allowed")
    summary_path = directory / "summary.json"
    summary_raw = summary_path.read_bytes()
    summary = data.parse(summary_raw)
    data.require(summary.get("version") == runner.VERSION, "not_general_context_run")
    data.require(summary.get("codeHashes") == runner.code_hashes(), "producer_code_identity_mismatch")
    api = runner.backend(summary.get("candidate"))
    artifacts = summary.get("artifactHashes")
    data.require(isinstance(artifacts, dict) and "predictions.jsonl" in artifacts, "missing_artifacts")
    for name, expected in artifacts.items():
        data.require(Path(name).name == name and name not in ("", ".", "..", "summary.json"), "artifact_path")
        path = directory / name
        data.require(not path.is_symlink() and data.sha(path.read_bytes()) == expected, "artifact_hash_mismatch")
    rows, identity = data.read_input()
    data.require(summary.get("input") == identity, "frozen_input_identity_mismatch")
    terms, catalog = runner.common.read_catalog()
    data.require(summary.get("catalog") == catalog, "catalog_identity_mismatch")
    planned = runner.make_prompts(rows, summary.get("profile"), terms)
    predictions = [data.parse(line) for line in (directory / "predictions.jsonl").read_bytes().splitlines() if line.strip()]
    data.require([p.get("id") for p in predictions] == data.IDS, "prediction_coverage")
    for row, prepared, prediction in zip(rows, planned, predictions, strict=True):
        data.require(prediction.get("status") == "completed" and prediction.get("profile") == summary["profile"]
            and prediction.get("candidate") == summary["candidate"] and prediction.get("promptInput") == prepared[3],
            "prediction_identity")
        for field in ("sourceSha256", "contextSha256"):
            data.require(prediction.get(field) == row[field], "prediction_source_identity")
        text = prediction.get("translation")
        data.require(isinstance(text, str) and prediction.get("targetSha256") == runner.common.sha_text(text),
                     "prediction_target_hash")
        name = prediction.get("rawResponseFile")
        data.require(name == row["id"] + ".raw-response.json" and name in artifacts, "raw_response_reference")
        raw_bytes = (directory / name).read_bytes()
        data.require(prediction.get("rawResponseSha256") == data.sha(raw_bytes), "raw_response_hash")
        response = data.parse(raw_bytes)
        data.require(response.get("content") == text and response.get("tokens") == prediction.get("outputTokenIds")
            and response.get("tokens_predicted") == prediction.get("generatedTokens")
            and response.get("stop_type") == prediction.get("stopType")
            and response.get("truncated") == prediction.get("truncated"), "raw_prediction_link")
        tokens = prediction.get("outputTokenIds")
        data.require(isinstance(tokens, list) and prediction.get("outputTokenIdsSha256") == runner.common.sha_json(tokens)
            and len(tokens) == prediction.get("generatedTokens"), "output_token_hash")
        settings = (runner.common.validate_generation_settings(response.get("generation_settings"))
                    if summary["candidate"] == "hy7" else runner.large.validate_settings(response.get("generation_settings")))
        data.require(settings == prediction.get("actualGenerationSettings") and summary.get("sampling") == api.SAMPLING,
                     "generation_settings_mismatch")
        content = prepared[1]
        # These are the exact rendered contracts asserted by the existing renderers.
        expected_prompt = ("<|startoftext|>" + content + "<|extra_0|>" if summary["candidate"] == "hy7" else
            runner.large.TOKEN_STRINGS[120000] + runner.large.TOKEN_STRINGS[120044] + "reasoning_effort:no_think"
            + runner.large.TOKEN_STRINGS[120006] + content + runner.large.TOKEN_STRINGS[120007] + "<think></think>")
        data.require(prediction.get("promptSha256") == runner.common.sha_text(expected_prompt), "rendered_prompt_hash")
    # Do not reclassify failed automatic checks or score their semantic quality.
    data.require(summary_path.read_bytes() == summary_raw, "summary_changed_during_verification")
    return summary, {"directory": str(directory), "summarySha256": data.sha(summary_raw), "artifactHashes": artifacts}


def verify(raw_path, contextual_path, output):
    raw, raw_evidence = load_run(raw_path)
    contextual, contextual_evidence = load_run(contextual_path)
    check_pair_identity(raw, contextual)
    output = Path(output).resolve()
    boundary = runner.common.COMPARISONS.resolve()
    data.require(output.is_relative_to(boundary) and not output.exists(), "new_comparison_output_required")
    result = {"version": "general-context-profile-pair-verification-v1", "integrityVerified": True,
        "candidate": raw["candidate"], "sourceCount": 16, "judgmentCount": 0,
        "raw": raw_evidence, "contextual": contextual_evidence,
        "outputIntegrityPassed": {"raw": raw["outputIntegrityPassed"], "contextual": contextual["outputIntegrityPassed"]},
        "automaticCheckFailureIds": {"raw": raw["automaticCheckFailureIds"], "contextual": contextual["automaticCheckFailureIds"]},
        "semanticQualityJudged": False, "humanReviewed": False, "modelInferencePerformed": False,
        "verifierSha256": data.sha(Path(__file__).read_bytes()),
        "meaningKo": "같은 후보의 두 프로필 실행·파일 관계 검증이며 번역 품질 점수나 승격 판정이 아니다."}
    # Recheck all bound artifacts after both profiles were read.
    for evidence in (raw_evidence, contextual_evidence):
        directory = Path(evidence["directory"])
        data.require(data.sha((directory / "summary.json").read_bytes()) == evidence["summarySha256"], "summary_changed")
        for name, expected in evidence["artifactHashes"].items():
            data.require(data.sha((directory / name).read_bytes()) == expected, "artifact_changed")
    runner.common.write_once(output, result)
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--raw", type=Path, required=True)
    cli.add_argument("--contextual", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    verify(args.raw, args.contextual, args.output)
    print(json.dumps({"integrityVerified": True, "semanticQualityJudged": False}))
