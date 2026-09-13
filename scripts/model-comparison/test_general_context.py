"""Synthetic input/protocol tests. No weights, native process, network or DB."""
from __future__ import annotations

from argparse import Namespace
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import general_context_input as data
import run_general_context as runner
import verify_general_context_pair as pairing


def fixture():
    rows = []
    for index, identifier in enumerate(data.IDS):
        source = "The gardener left a basket beside the shed."
        context = "The basket belongs to the gardener." if index >= 12 else ""
        rows.append({"id": identifier, "source": source, "context": context,
            "domain": "general" if index < 12 else "general_context_polysemy",
            "sourceSha256": data.sha(source.encode()), "contextSha256": data.sha(context.encode()),
            "referenceKo": "REFERENCE_MUST_NOT_REACH_MODEL", "checks": ["CHECK_MUST_NOT_REACH_MODEL"],
            "humanReviewed": False, "trainingUseAllowed": False, "finalHoldout": False,
            "split": "development", "provenance": "assistant-authored-development"})
    return rows


def packed(rows):
    raw = ("\n".join(json.dumps(r) for r in rows) + "\n").encode()
    manifest = {"version": "general-context-dev-20260911-v1", "datasetFile": data.INPUT.name,
        "datasetSha256": data.sha(raw), "sourceCount": 16, "selectedIds": data.IDS,
        "trainingUseAllowed": False, "humanReviewed": False, "finalHoldout": False, "split": "development",
        "sourceAudit": {"completed": True, "newCandidateOutputsViewed": False, "humanReviewed": False,
            "findings": [{"id": r["id"], "sourceSha256": r["sourceSha256"],
                "contextSha256": r["contextSha256"], "wholeRowExclusionRecommended": False} for r in rows]}}
    return raw, manifest


def validate(rows, change_manifest=None):
    raw, manifest = packed(rows)
    if change_manifest:
        change_manifest(manifest)
    manifest_raw = json.dumps(manifest).encode()
    return data.validate_bytes(raw, manifest_raw, expected_input_sha=data.sha(raw),
                               expected_manifest_sha=data.sha(manifest_raw))


class InputTests(unittest.TestCase):
    def test_annotation_allowlist(self):
        result = validate(fixture())
        self.assertEqual(len(result), 16)
        self.assertTrue(all(tuple(row) == data.FIELDS for row in result))
        self.assertNotIn("REFERENCE_MUST_NOT_REACH_MODEL", json.dumps(result))
        self.assertNotIn("CHECK_MUST_NOT_REACH_MODEL", json.dumps(result))

    def test_original_bytes_not_silently_reaccepted(self):
        raw, manifest = packed(fixture())
        mr = json.dumps(manifest).encode()
        with self.assertRaisesRegex(ValueError, "frozen_input_changed"):
            data.validate_bytes(raw + b" ", mr, expected_input_sha=data.sha(raw), expected_manifest_sha=data.sha(mr))

    def test_manifest_change_refused(self):
        raw, manifest = packed(fixture())
        mr = json.dumps(manifest).encode()
        with self.assertRaisesRegex(ValueError, "frozen_manifest_changed"):
            data.validate_bytes(raw, mr + b" ", expected_input_sha=data.sha(raw), expected_manifest_sha=data.sha(mr))

    def test_missing_or_reordered_row_refused(self):
        for rows in (fixture()[:-1], list(reversed(fixture()))):
            with self.subTest(count=len(rows)), self.assertRaisesRegex(ValueError, "input_coverage"):
                validate(rows)

    def test_source_and_context_hashes_checked(self):
        for field in ("source", "context"):
            rows = fixture()
            rows[0][field] += " Changed."
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "row_hash"):
                validate(rows)

    def test_train_or_human_claim_refused(self):
        for field in ("trainingUseAllowed", "humanReviewed", "finalHoldout"):
            rows = fixture()
            rows[0][field] = True
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "row_scope"):
                validate(rows)

    def test_audit_after_candidate_output_refused(self):
        with self.assertRaisesRegex(ValueError, "source_audit_missing"):
            validate(fixture(), lambda m: m["sourceAudit"].update(newCandidateOutputsViewed=True))

    def test_audit_row_mismatch_refused(self):
        with self.assertRaisesRegex(ValueError, "row_hash"):
            validate(fixture(), lambda m: m["sourceAudit"]["findings"][0].update(sourceSha256="0" * 64))

    def test_both_model_control_token_families_refused(self):
        for token in ("<|eos|>", "<｜hy_User｜>", "<think>", "<eos:abc>"):
            rows = fixture()
            rows[0]["source"] = token
            rows[0]["sourceSha256"] = data.sha(token.encode())
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, "input_control_token"):
                validate(rows)

    def test_duplicate_json_keys_refused(self):
        with self.assertRaisesRegex(ValueError, "duplicate_json_key"):
            data.parse('{"source": "a", "source": "b"}')


class PromptTests(unittest.TestCase):
    def test_raw_has_no_context_or_annotations(self):
        prepared = runner.make_prompts(fixture(), "raw", [])
        for row, content, matches, receipt in prepared:
            self.assertEqual(content, runner.common.RAW_INSTRUCTION + "\n" + row["source"])
            self.assertNotIn("basket belongs", content)
            self.assertNotIn("MUST_NOT_REACH_MODEL", content)
            self.assertFalse(receipt["contextIncluded"])
            self.assertEqual(receipt["effectiveContextSha256"], runner.common.sha_text(""))
            self.assertEqual(matches, [])

    def test_contextual_reuses_exact_existing_builder(self):
        rows = fixture()
        prepared = runner.make_prompts(rows, "contextual", [])
        for original, (row, content, matches, receipt) in zip(rows, prepared):
            expected = runner.common.build_user_prompt(row, "contextual", [])
            self.assertEqual((content, matches), expected)
            self.assertNotIn("MUST_NOT_REACH_MODEL", content)
            self.assertTrue(receipt["contextIncluded"])
            self.assertEqual(receipt["effectiveContextSha256"], original["contextSha256"])

    def test_context_changes_only_contextual_content(self):
        rows, changed = fixture(), fixture()
        changed[12]["context"] = "A different neighboring sentence."
        changed[12]["contextSha256"] = runner.common.sha_text(changed[12]["context"])
        for profile, should_equal in (("raw", True), ("contextual", False)):
            before = runner.make_prompts(rows, profile, [])[12][1]
            after = runner.make_prompts(changed, profile, [])[12][1]
            self.assertEqual(before == after, should_equal)

    def test_profiles_and_candidates_are_explicit(self):
        with self.assertRaises(ValueError):
            runner.make_prompts(fixture(), "auto", [])
        with self.assertRaises(ValueError):
            runner.backend("auto")
        self.assertIs(runner.backend("hy7"), runner.common)
        self.assertIs(runner.backend("hy30"), runner.large)

    def test_cli_requires_run_to_load_model(self):
        args = runner.parser().parse_args(["--output", "unused", "--candidate", "hy7", "--profile", "raw"])
        self.assertFalse(args.run)


class ExecutionTests(unittest.TestCase):
    def execute_fake(self, fail_prediction=False, candidate="hy7"):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            output = Path(directory) / "out"
            output.mkdir()
            install = Path(directory) / "installation-manifest.json"
            install.write_text("{}", encoding="utf-8")
            setup = SimpleNamespace(DEST=Path(directory), MODEL={"name": "unused.gguf"},
                                    verify_installation=Mock(return_value={"synthetic": True}))
            process = Mock()
            process.pid = 123
            process.poll.return_value = None
            owner = Mock()
            owner.spawn.return_value = process
            owner.creation_receipt = {"synthetic": True}
            original_owner = Mock()
            original_owner.assert_owned = Mock()
            limiter = Mock()
            limiter.job_record = {"synthetic": True}
            limiter.apply_child.return_value = {"synthetic": True}
            monitor = Mock()
            monitor.abort_reason = monitor.monitor_error = monitor.kill_error = None
            monitor.sample_and_check.return_value = {"synthetic": True}
            monitor.receipt.return_value = {"synthetic": True}
            client = Mock()
            client.request.side_effect = lambda endpoint, *a, **k: {
                "/health": {"status": "ok"}, "/tokenize": {"tokens": [1]},
                "/completion": {"content": "합성 응답", "tokens": [2]}}[endpoint]
            def prediction(row, prompt, matches, tokens, response, elapsed):
                if fail_prediction:
                    raise runner.common.RunError("synthetic_invalid_response")
                return {"id": row["id"], "status": "completed", "translation": response["content"],
                    "generationSeconds": 0, "automaticChecksPassed": True, "outputIntegrityPassed": True}
            api = SimpleNamespace(gguf_contract=Mock(return_value=("TEMPLATE", {})),
                server_command=Mock(return_value=[str(Path(directory) / "server.exe"), "--api-key", "SECRET"]),
                render_prompt=lambda template, content: content, validate_prompt_tokens=Mock(),
                prediction=prediction, SAMPLING={"synthetic": True})
            stack.enter_context(patch.object(runner, "backend", return_value=api))
            stack.enter_context(patch.object(runner.common, "setup_hymt", setup))
            stack.enter_context(patch.object(runner.large, "setup", setup))
            stack.enter_context(patch.object(runner, "assert_identity"))
            stack.enter_context(patch.object(runner, "validate_runtime", return_value={"synthetic": True}))
            stack.enter_context(patch.object(runner, "resource_preflight", return_value={"physicalPassed": True,
                "commitPassed": True, "requestedHardMaximumWorkingSetBytes": 123}))
            stack.enter_context(patch.dict(sys.modules, {"process_owner": SimpleNamespace(claim_process_owner=lambda: original_owner)}))
            stack.enter_context(patch.object(runner.large, "WorkingSetLimit", return_value=limiter))
            stack.enter_context(patch.object(runner.large, "SuspendedProcessOwner", return_value=owner))
            stack.enter_context(patch.object(runner.large, "MemoryMonitor", return_value=monitor))
            stack.enter_context(patch.object(runner.common, "LocalClient", return_value=client))
            stop = stack.enter_context(patch.object(runner.common, "stop_process", return_value=True))
            summary = {"status": "failed", "completionRequestsSent": 0}
            args = Namespace(candidate=candidate, profile="raw", ram_budget_gib=12)
            records = runner.execute(args, summary, runner.make_prompts(fixture(), "raw", []), output, 0)
            stop.assert_called_once_with(process)
            monitor.close.assert_called_once()
            self.assertTrue(summary["childProcessStopped"])
            self.assertEqual(len(records), 16)
            self.assertEqual(len({r["id"] for r in records}), 16)
            self.assertTrue((output / "GCTX26-001.raw-response.json").is_file(), summary.get("failure"))
            self.assertNotIn("SECRET", json.dumps(summary))
            return copy.deepcopy(summary), records

    def test_success_stops_owned_process_and_records_all_rows(self):
        summary, records = self.execute_fake()
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["completionRequestsSent"], 16)
        self.assertTrue(all(r["status"] == "completed" for r in records))

    def test_invalid_response_is_preserved_without_retry_or_fallback(self):
        summary, records = self.execute_fake(fail_prediction=True)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["completionRequestsSent"], 1)
        self.assertEqual(records[0]["status"], "failed")
        self.assertTrue(all(r["status"] == "not_run" for r in records[1:]))

    def test_hy30_adapter_uses_same_owned_sequential_loop(self):
        summary, records = self.execute_fake(candidate="hy30")
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["completionRequestsSent"], 16)
        self.assertTrue(all(r["candidate"] == "hy30" for r in records))

    def test_prepare_does_not_enter_execution_or_read_model(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            boundary = Path(directory)
            output = boundary / "new-plan"
            stack.enter_context(patch.object(runner.common, "COMPARISONS", boundary))
            stack.enter_context(patch.object(runner.data, "read_input", return_value=(validate(fixture()), {})))
            stack.enter_context(patch.object(runner.common, "read_catalog", return_value=([], {})))
            stack.enter_context(patch.object(runner, "code_hashes", return_value={}))
            stack.enter_context(patch.object(runner, "assert_identity"))
            execute = stack.enter_context(patch.object(runner, "execute", side_effect=AssertionError("must not execute")))
            installation = stack.enter_context(patch.object(runner.common.setup_hymt, "verify_installation",
                side_effect=AssertionError("must not read model")))
            args = Namespace(candidate="hy7", profile="raw", ram_budget_gib=12, input=Path("unused"), output=output, run=False)
            self.assertEqual(runner.run(args), 0)
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "prepared")
            self.assertFalse(summary["modelLoaded"])
            self.assertEqual(summary["completionRequestsSent"], 0)
            execute.assert_not_called()
            installation.assert_not_called()
            with self.assertRaises(FileExistsError):
                runner.run(args)


class PairTests(unittest.TestCase):
    def pair(self):
        raw = {"version": runner.VERSION, "status": "completed", "profile": "raw", "candidate": "hy7",
            "completed": 16, "recordedCount": 16, "expectedCount": 16, "completionRequestsSent": 16,
            "modelLoaded": True, "childProcessStopped": True, "integrityVerified": True,
            "trainingPerformed": False, "postProcessingApplied": False, "translationMemoryApplied": False,
            "humanReviewed": False, "modelSha256": "a" * 64, "input": {}, "catalog": {}, "codeHashes": {},
            "sampling": {}, "contextSize": 8192, "runtimeOverrides": {}, "samplingNormalization": None,
            "ramBudgetGiB": 12, "installation": {}, "installationManifestSha256": "b" * 64, "timeLimitsSeconds": {}}
        return raw, copy.deepcopy(raw) | {"profile": "contextual"}

    def test_same_candidate_two_profiles_pass_without_quality_gate(self):
        raw, contextual = self.pair()
        raw["outputIntegrityPassed"] = False
        contextual["outputIntegrityPassed"] = True
        pairing.check_pair_identity(raw, contextual)

    def test_model_runtime_or_dataset_change_refused(self):
        for field in ("candidate", "modelSha256", "input", "catalog", "codeHashes", "sampling", "installation"):
            raw, contextual = self.pair()
            contextual[field] = "changed"
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "pair_identity_mismatch"):
                pairing.check_pair_identity(raw, contextual)

    def test_incomplete_or_running_child_refused(self):
        for field, value in (("completed", 15), ("childProcessStopped", False), ("status", "prepared")):
            raw, contextual = self.pair()
            contextual[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                pairing.check_pair_identity(raw, contextual)


class ArtifactTests(unittest.TestCase):
    def exercise(self, change=None):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            boundary = Path(directory)
            output = boundary / "raw"
            output.mkdir()
            rows = validate(fixture())
            planned = runner.make_prompts(rows, "raw", [])
            artifacts, predictions = {}, []
            def save(path, value):
                raw = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
                path.write_bytes(raw)
                return data.sha(raw)
            for row, content, matches, receipt in planned:
                name = row["id"] + ".raw-response.json"
                response = {"content": "합성 검사", "tokens": [127960], "tokens_predicted": 1,
                    "stop_type": "eos", "truncated": False, "generation_settings": copy.deepcopy(runner.common.SAMPLING)}
                artifacts[name] = save(output / name, response)
                predictions.append({"id": row["id"], "status": "completed", "profile": "raw", "candidate": "hy7",
                    "sourceSha256": row["sourceSha256"], "contextSha256": row["contextSha256"], "promptInput": receipt,
                    "translation": response["content"], "targetSha256": runner.common.sha_text(response["content"]),
                    "rawResponseFile": name, "rawResponseSha256": artifacts[name], "outputTokenIds": response["tokens"],
                    "outputTokenIdsSha256": runner.common.sha_json(response["tokens"]), "generatedTokens": 1,
                    "stopType": "eos", "truncated": False, "actualGenerationSettings": copy.deepcopy(runner.common.SAMPLING),
                    "promptSha256": runner.common.sha_text("<|startoftext|>" + content + "<|extra_0|>")})
            if change:
                change(predictions)
            raw = ("\n".join(json.dumps(p, ensure_ascii=False) for p in predictions) + "\n").encode("utf-8")
            (output / "predictions.jsonl").write_bytes(raw)
            artifacts["predictions.jsonl"] = data.sha(raw)
            summary = {"version": runner.VERSION, "profile": "raw", "candidate": "hy7", "input": {}, "catalog": {},
                "codeHashes": {"synthetic-code": "c" * 64}, "artifactHashes": artifacts,
                "sampling": copy.deepcopy(runner.common.SAMPLING)}
            save(output / "summary.json", summary)
            stack.enter_context(patch.object(runner.common, "COMPARISONS", boundary))
            stack.enter_context(patch.object(runner.data, "read_input", return_value=(rows, {})))
            stack.enter_context(patch.object(runner.common, "read_catalog", return_value=([], {})))
            stack.enter_context(patch.object(runner, "code_hashes", return_value={"synthetic-code": "c" * 64}))
            return pairing.load_run(output)

    def test_bound_synthetic_artifacts_pass(self):
        summary, evidence = self.exercise()
        self.assertEqual(summary["candidate"], "hy7")
        self.assertEqual(len(evidence["artifactHashes"]), 17)

    def test_prediction_source_tampering_refused(self):
        with self.assertRaisesRegex(ValueError, "prediction_source_identity"):
            self.exercise(lambda p: p[0].update(sourceSha256="0" * 64))

    def test_claimed_reference_prompt_refused(self):
        with self.assertRaisesRegex(ValueError, "prediction_identity"):
            self.exercise(lambda p: p[0]["promptInput"].update(referenceKoIncluded=True))

    def test_missing_prediction_refused(self):
        with self.assertRaisesRegex(ValueError, "prediction_coverage"):
            self.exercise(lambda p: p.pop())

    def test_rendered_prompt_tampering_refused(self):
        with self.assertRaisesRegex(ValueError, "rendered_prompt_hash"):
            self.exercise(lambda p: p[0].update(promptSha256="0" * 64))

if __name__ == "__main__":
    unittest.main()
