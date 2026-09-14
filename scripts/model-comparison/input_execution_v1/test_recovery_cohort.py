"""Small synthetic protocol and evidence-corruption tests; no native calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from input_execution_v1 import recovery_cohort as c
from test_runtime_contract import row as prompt_fixture, response as response_fixture


class RecoveryCohortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="recovery-cohort-test-")
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / c.PRIOR_PATH
        self.run.mkdir(parents=True)
        self.prompt = prompt_fixture()
        self.prompt.update(id="IP1-F01", configuration="C0")
        self.prompt["context"]["policyVersion"] = "fixture-context"
        self.prompt["terminology"]["selectorVersion"] = "fixture-selector"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(c.e.packed(value))

    def http(self, payload=None, response=None, endpoint="/completion", sequence=1):
        payload = c.runtime.completion_payload(self.prompt) if payload is None else payload
        response = response_fixture(self.prompt) if response is None else response
        path = self.run / "http" / f"{sequence:05d}-{endpoint[1:]}.request.json"
        metadata = {"endpoint": endpoint, "method": "POST", "sequence": sequence, "requestSha256": c.e.sha(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))), "authorizationRecorded": False, "startedAt": "2026-09-14T00:00:00+00:00", "timeoutSeconds": 1800}
        self.write(path, {"metadata": metadata, "payload": payload})
        raw_path = path.with_name(path.name.replace(".request.json", ".response.bin"))
        self.write(raw_path, response)
        receipt = {**metadata, "automaticRetry": False, "httpStatus": 200, "responseCompleteWithinLimit": True, "responseEofObserved": True, "transportOrProtocolErrorType": None, "rawResponsePath": raw_path.relative_to(self.root).as_posix(), "rawResponseSha256": c.file_sha(raw_path), "responseBytes": raw_path.stat().st_size, "expectedResponseBytes": raw_path.stat().st_size, "completedAt": "2026-09-14T00:00:01+00:00"}
        receipt_path = path.with_name(path.name.replace(".request.json", ".receipt.json"))
        self.write(receipt_path, receipt)
        return {"path": path, "request": c.read_json(path), "receiptPath": receipt_path, "receipt": receipt}

    def record(self, item, *, recovered=False):
        value = c.runtime.validate_completion(self.prompt, c.read_json(self.root / item["receipt"]["rawResponsePath"]), 1.0)
        value.update(runId=self.run.name, producerVersion=c.RECOVERY_VERSION if recovered else c.PRIOR_VERSION, requestSequence=1, preparedManifestSha256=c.e.S2_SHA, sourceOutputReused=False, processingIdentity={"configuration": "C0", "preparedPromptSha256": self.prompt["promptSha256"], "contextPolicy": "fixture-context", "selectorVersion": "fixture-selector"}, rawResponsePath=item["receipt"]["rawResponsePath"], rawResponseSha256=item["receipt"]["rawResponseSha256"])
        if recovered:
            value.update(preparedSequence=39, technicalRecoveryRetry=True)
        self.write(self.run / "outputs/IP1-F01-C0.json", value)
        return value

    def test_valid_record_preserves_raw_outer_whitespace(self):
        item = self.http()
        output = self.record(item)
        actual = c._record(self.run, self.prompt, item, 1, c.PRIOR_VERSION, self.root)
        self.assertEqual(actual, output)
        self.assertEqual(actual["translation"], "  수수료는 $20이다.\n")

    def test_saved_sorted_payload_reconstructs_actual_wire_hash(self):
        item = self.http()
        requests = c._http_inventory(self.run, self.root)
        self.assertEqual(requests[0]["request"], item["request"])
        c._http_response(requests[0], self.run, self.root, c.runtime.completion_payload(self.prompt))
        item["request"]["metadata"]["requestSha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "actual_request_hash"):
            c._http_response(item, self.run, self.root, c.runtime.completion_payload(self.prompt))

    def test_quality_warning_is_not_regeneration_permission(self):
        response = response_fixture(self.prompt)
        response["content"] = "수수료는 25유로이다."
        item = self.http(response=response)
        self.record(item)
        output = c._record(self.run, self.prompt, item, 1, c.PRIOR_VERSION, self.root)
        self.assertEqual(output["status"], "completed")
        self.assertFalse(output["automaticChecksPassed"])

    def test_output_record_changes_are_rejected(self):
        item = self.http()
        original = self.record(item)
        for field, bad in (("translation", original["translation"].strip()), ("runId", "fake-run"), ("producerVersion", "fake-version"), ("requestSequence", 2), ("sourceOutputReused", True), ("processingIdentity", {})):
            with self.subTest(field=field):
                self.write(self.run / "outputs/IP1-F01-C0.json", original | {field: bad})
                with self.assertRaises(ValueError):
                    c._record(self.run, self.prompt, item, 1, c.PRIOR_VERSION, self.root)

    def test_raw_response_and_receipt_corruption_are_rejected(self):
        item = self.http()
        for field, bad in (("httpStatus", 206), ("responseCompleteWithinLimit", False), ("responseEofObserved", False), ("transportOrProtocolErrorType", "TimeoutError"), ("rawResponseSha256", "0" * 64), ("responseBytes", 2)):
            changed = deepcopy(item)
            changed["receipt"][field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                c._http_response(changed, self.run, self.root, c.runtime.completion_payload(self.prompt))

    def test_new_record_must_mark_only_interrupted_retry(self):
        item = self.http()
        original = self.record(item, recovered=True)
        c._record(self.run, self.prompt, item, 1, c.RECOVERY_VERSION, self.root, 38)
        for field, bad in (("preparedSequence", 38), ("technicalRecoveryRetry", False)):
            self.write(self.run / "outputs/IP1-F01-C0.json", original | {field: bad})
            with self.assertRaisesRegex(ValueError, "tail_sequence"):
                c._record(self.run, self.prompt, item, 1, c.RECOVERY_VERSION, self.root, 38)

    def test_missing_or_extra_http_request_cannot_hide_in_counters(self):
        self.http(sequence=2)
        with self.assertRaisesRegex(ValueError, "sequence_gap"):
            c._http_inventory(self.run, self.root)

    def test_request_timeout_and_other_recovery_calls_are_rejected(self):
        item = self.http()
        request = c.read_json(item["path"])
        request["metadata"]["timeoutSeconds"] = 1801
        self.write(item["path"], request)
        with self.assertRaisesRegex(ValueError, "timeout_changed"):
            c._http_inventory(self.run, self.root)
        other = self.root / c.RECOVERY_BASE / "recovery-attempt-002/http/00001-completion.request.json"
        self.write(other, {})
        selected = self.root / c.RECOVERY_BASE / "recovery-attempt-001"
        selected.mkdir()
        with self.assertRaisesRegex(ValueError, "other_recovery_calls"):
            c.validate_recovery_run(selected, {}, self.root)

    def test_artifact_inventory_is_closed_and_bound(self):
        path = self.run / "one.json"
        self.write(path, {"value": 1})
        self.write(self.run / "summary.json", {"artifactHashes": {"one.json": c.file_sha(path)}})
        c._artifact_snapshot(self.run, self.root)
        self.write(self.run / "extra.json", {})
        with self.assertRaisesRegex(ValueError, "unlisted_or_missing"):
            c._artifact_snapshot(self.run, self.root)
        (self.run / "extra.json").unlink()
        self.write(path, {"value": 2})
        with self.assertRaisesRegex(ValueError, "artifact_hash_changed"):
            c._artifact_snapshot(self.run, self.root)

    def test_pinned_failed_summary_cannot_be_promoted_in_place(self):
        self.write(self.run / "summary.json", {"status": "completed", "completedOutputs": 64})
        with self.assertRaisesRegex(ValueError, "pinned_prior_changed"):
            c.validate_failed_run(self.run, self.root)

    def test_completion_before_all64_parity_is_rejected(self):
        self.write(self.run / "all-input-parity.json", {"completed": 64, "completionRequestsSent": 0, "rows": []})
        requests = [{"request": {"metadata": {"endpoint": "/completion", "sequence": 1, "startedAt": "0"}}}]
        requests += [{"request": {"metadata": {"endpoint": "/apply-template", "sequence": 2 * i + 2}}} for i in range(64)]
        with self.assertRaisesRegex(ValueError, "before_all64_parity"):
            c._parity_and_runtime(self.run, {}, [self.prompt] * 64, requests, self.root)

    def test_input_file_hash_is_streamed_and_checked(self):
        path = self.root / "model.gguf"
        path.write_bytes(b"x" * (2 * 1024 * 1024 + 3))
        expected = c.ref(self.root, path)
        with patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded read")):
            c._input_bindings({"inputFiles": [expected]}, self.root)
        path.write_bytes(b"y" * expected["bytes"])
        with self.assertRaisesRegex(ValueError, "input_identity_changed"):
            c._input_bindings({"inputFiles": [expected]}, self.root)

    def test_duplicate_json_keys_and_nonfinite_numbers_rejected(self):
        for raw in (b'{"value":1,"value":2}', b'{"value":NaN}'):
            with self.assertRaises(ValueError):
                c._json(raw)

    def recovered_fixture(self):
        """Stored startup replay plus synthetic completions, never inference."""
        prepared = c.e.load_prepared(c.ROOT)
        original = c.ROOT / c.PRIOR_PATH
        self.run = self.root / c.RECOVERY_BASE / "recovery-attempt-001"
        self.run.mkdir(parents=True)
        for name in ("all-input-parity.json", "runtime-identity.json", "runtime.log"):
            (self.run / name).write_bytes((original / name).read_bytes())
        model = self.root / "fixture.gguf"
        model.write_bytes(b"synthetic identity fixture; no weights")
        first_completion = min(int(p.name.split("-")[0]) for p in (original / "http").glob("*-completion.request.json"))
        for source in (original / "http").iterdir():
            if int(source.name.split("-")[0]) >= first_completion:
                continue
            target = self.run / "http" / source.name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(source.read_bytes())
        for receipt_path in (self.run / "http").glob("*.receipt.json"):
            receipt = c.read_json(receipt_path)
            raw_path = self.run / "http" / Path(receipt["rawResponsePath"]).name
            if receipt["endpoint"] == "/props":
                props = c.read_json(raw_path)
                props["model_path"] = str(model)
                self.write(raw_path, props)
                receipt["responseBytes"] = receipt["expectedResponseBytes"] = raw_path.stat().st_size
            receipt["rawResponsePath"] = raw_path.relative_to(self.root).as_posix()
            receipt["rawResponseSha256"] = c.file_sha(raw_path)
            self.write(receipt_path, receipt)
        rows, events = [], []
        for index, prompt in enumerate(prepared[38:], 1):
            self.prompt = prompt
            response = response_fixture(prompt)
            response["tokens_cached"] = len(prompt["tokenIds"]) + 2
            item = self.http(response=response, sequence=first_completion + index - 1)
            output = c.runtime.validate_completion(prompt, response, 1.0)
            output.update(runId=self.run.name, producerVersion=c.RECOVERY_VERSION, requestSequence=index, preparedSequence=index + 38, technicalRecoveryRetry=index == 1, preparedManifestSha256=c.e.S2_SHA, sourceOutputReused=False, processingIdentity={"configuration": prompt["configuration"], "preparedPromptSha256": prompt["promptSha256"], "contextPolicy": prompt["context"]["policyVersion"], "selectorVersion": prompt["terminology"]["selectorVersion"]}, rawResponsePath=item["receipt"]["rawResponsePath"], rawResponseSha256=item["receipt"]["rawResponseSha256"])
            self.write(self.run / "outputs" / f'{prompt["id"]}-{prompt["configuration"]}.json', output)
            rows.append(output)
            pending = {"id": prompt["id"], "configuration": prompt["configuration"], "sequence": index, "preparedSequence": index + 38, "technicalRecoveryRetry": index == 1, "sourceSha256": prompt["sourceSha256"], "promptSha256": prompt["promptSha256"], "tokenIdsSha256": prompt["tokenIdsSha256"], "contextPolicy": prompt["context"]["policyVersion"], "selectorVersion": prompt["terminology"]["selectorVersion"], "at": "2026-09-13T23:59:59+00:00", "status": "request_pending", "automaticRetry": False, "sourceOutputReuse": False}
            events.extend([pending, pending | {"status": "completed", "translationSha256": output["translationSha256"], "rawResponseSha256": output["rawResponseSha256"]}])
        plan = {"version": c.RECOVERY_VERSION, "expectedOutputs": 26, "inferenceRequested": True, "preparedManifestSha256": c.e.S2_SHA, "sampling": c.runtime.baseline.SAMPLING, "contextSize": 8192, "annotationFilesRead": False, "automaticRetry": False, "crossConfigurationReuse": False, "historicalOutputsUsedForPrompts": False, "outputOrder": [[p["id"], p["configuration"]] for p in prepared], "inputFiles": [c.ref(self.root, model)]}
        self.write(self.run / "plan.json", plan)
        for name, values in (("inputs.jsonl", prepared), ("predictions.jsonl", rows), ("attempt-events.jsonl", events)):
            (self.run / name).write_bytes(b"".join(c.e.packed(value) for value in values))
        self.write(self.run / "missing-outputs.json", [])
        summary = {"version": c.RECOVERY_VERSION, "status": "completed", "completedOutputs": 26, "expectedOutputs": 26, "completionRequestsSent": 26, "missingOutputs": 0, "childProcessStopped": True, "integrityVerified": True, "outputIntegrityPassed": True, "failure": None, "powerRequestAfterRelease": {"released": True}, "shutdownReceipt": {"stopped": True}}
        summary["artifactHashes"] = {p.relative_to(self.run).as_posix(): c.file_sha(p) for p in self.run.rglob("*") if p.is_file()}
        self.write(self.run / "summary.json", summary)
        return prepared, rows

    def test_complete_recovery_run_validates_all64_parity_and26_real_contract_responses(self):
        prepared, expected = self.recovered_fixture()
        checked = c._validate_run(self.run, self.root, prepared, recovered=True)
        self.assertEqual(checked["rows"], expected)
        self.assertEqual(len(checked["requests"]), 26)

    def test_run_mutation_during_output_validation_is_rejected(self):
        prepared, _ = self.recovered_fixture()
        original_record = c._record
        def change_after_read(*args, **kwargs):
            result = original_record(*args, **kwargs)
            if args[3] == 26:
                path = self.run / "runtime.log"
                path.write_bytes(path.read_bytes() + b"\nchanged after initial snapshot\n")
            return result
        with patch.object(c, "_record", side_effect=change_after_read), self.assertRaisesRegex(ValueError, "artifact_hash_changed"):
            c._validate_run(self.run, self.root, prepared, recovered=True)

    def cohort_sources(self):
        order = [(f"IP1-{domain}{number:02d}", config) for domain in ("F", "G") for number in range(1, 9) for config in c.e.CONFIGURATIONS]
        sources = []
        for keys, folder, version in ((order[:38], self.run, c.PRIOR_VERSION), (order[38:], self.root / c.RECOVERY_BASE / "recovery-attempt-001", c.RECOVERY_VERSION)):
            rows = [{"id": key[0], "configuration": key[1], "runId": folder.name, "producerVersion": version, "translation": "  원문 보존\n"} for key in keys]
            folder.mkdir(parents=True, exist_ok=True)
            raw = b"".join((json.dumps(row, ensure_ascii=False, indent=None, separators=(", ", ": ")) + "\n").encode("utf-8") for row in rows)
            (folder / "predictions.jsonl").write_bytes(raw)
            self.write(folder / "summary.json", {"fixture": True})
            self.write(folder / "plan.json", {"fixture": True})
            evidence = {name: c.ref(self.root, folder / (name + (".jsonl" if name == "predictions" else ".json"))) for name in ("summary", "plan", "predictions")}
            evidence["files"] = list(evidence.values())
            sources.append({"rows": rows, "evidence": evidence})
        return sources

    def test_cohort_preserves_original_ndjson_and_failed_provenance(self):
        prior, recovery = self.cohort_sources()
        destination = self.root / c.COHORT_BASE / "cohort-fixture"
        with patch.object(c, "validate_failed_run", return_value=prior), patch.object(c, "validate_recovery_run", return_value=recovery):
            built = c.build(self.run, "unused", destination, self.root)
            checked = c.validate_cohort(destination, self.root)
            self.assertEqual(checked["rows"], prior["rows"] + recovery["rows"])
            self.assertEqual(built["manifest"]["sourceRuns"][0]["status"], "failed")
            self.assertFalse(built["manifest"]["singleRunCompletion"])
            self.assertEqual(built["manifest"]["completionRequestsSent"], 65)
            self.assertEqual(c.validate_cohort(destination / "manifest.json", self.root)["manifest"], built["manifest"])
            manifest = c.read_json(destination / "manifest.json")
            manifest["interruptedRequests"] = 0
            self.write(destination / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "metadata_changed"):
                c.validate_cohort(destination, self.root)

    def test_prediction_change_after_validation_blocks_build(self):
        prior, recovery = self.cohort_sources()
        prediction = self.root / recovery["evidence"]["predictions"]["path"]
        prediction.write_bytes(prediction.read_bytes() + b"{}\n")
        destination = self.root / c.COHORT_BASE / "cohort-race"
        with patch.object(c, "validate_failed_run", return_value=prior), patch.object(c, "validate_recovery_run", return_value=recovery), self.assertRaisesRegex(ValueError, "predictions_changed_before_build"):
            c.build(self.run, "unused", destination, self.root)
        self.assertFalse(destination.exists())

    def test_original_record_rewrite_with_updated_manifest_is_rejected(self):
        prior, recovery = self.cohort_sources()
        destination = self.root / c.COHORT_BASE / "cohort-rewrite"
        with patch.object(c, "validate_failed_run", return_value=prior), patch.object(c, "validate_recovery_run", return_value=recovery):
            c.build(self.run, "unused", destination, self.root)
            predictions = destination / "predictions.jsonl"
            predictions.write_bytes(b"".join(c.e.packed(row) for row in prior["rows"] + recovery["rows"]))
            manifest = c.read_json(destination / "manifest.json")
            manifest["predictions"] = c.ref(self.root, predictions)
            self.write(destination / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "records_rewritten"):
                c.validate_cohort(destination, self.root)


if __name__ == "__main__":
    unittest.main()
