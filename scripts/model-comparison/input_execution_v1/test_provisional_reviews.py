"""Synthetic protocol fixtures only; no actual translations are scored."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from input_execution_v1 import provisional_reviews as p


def response(row):
    settings = deepcopy(p.runtime.baseline.SAMPLING)
    settings.update(grammar="", generation_prompt="", logit_bias=[], lora=[], preserved_tokens=[], grammar_triggers=[])
    for key in ("cache_prompt", "return_tokens", "id_slot"): settings.pop(key)
    return {"content": "합성 계약 검사 자료다.", "tokens_predicted": 3, "tokens": [21, 22, 127960],
            "stop": True, "stop_type": "eos", "stopping_word": "", "truncated": False, "id_slot": 0,
            "tokens_evaluated": row["promptTokens"], "generation_settings": settings,
            "timings": {"cache_n": 0, "prompt_n": row["promptTokens"], "predicted_n": 3,
                        "prompt_ms": 45.0, "predicted_ms": 101.0}, "tokens_cached": 7}


def output(row):
    result = p.runtime.validate_completion(row, response(row), 0.5)
    return result | {"runId": "attempt-001", "producerVersion": p.PRODUCER_VERSION,
                     "preparedManifestSha256": p.e.S2_SHA,
                     "rawResponsePath": ".training/comparisons/input-preparation-v1/s4-generation/attempt-001/http/00001-completion.response.bin",
                     "rawResponseSha256": p.e.sha(p.e.packed(response(row)))}


class ProvisionalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units, cls.annotations, _ = p.e.load_development()
        cls.prepared = p.e.load_prepared()

    def setUp(self):
        self.row = self.prepared[0]

    def test_all_provisional_packet_bytes_match_final_source_packet_schema(self):
        outputs = [output(row) for row in self.prepared]
        bundle = p.e.build_review_packets(outputs, self.units, self.annotations, self.prepared)
        final = {packet["reviewId"]: packet for packet in bundle["sourcePackets"]}
        for row, record in zip(self.prepared, outputs):
            packet = p.packet_for(row["id"], row["configuration"], record, self.units, self.annotations, self.prepared)
            self.assertEqual(p.e.packed(packet), p.e.packed(final[packet["reviewId"]]))
            self.assertNotIn("configuration", packet)
            self.assertNotIn("provisional", packet)

    def test_shuffle_does_not_depend_on_completion_order(self):
        original = p.packet_for(self.row["id"], "C0", output(self.row), self.units, self.annotations, self.prepared)
        reversed_order = p.packet_for(self.row["id"], "C0", output(self.row), dict(reversed(list(self.units.items()))),
                                     self.annotations, list(reversed(self.prepared)))
        self.assertEqual(original, reversed_order)
        with self.assertRaisesRegex(ValueError, "64_review_order"):
            p.packet_for(self.row["id"], "C0", output(self.row), {self.row["id"]: self.units[self.row["id"]]}, self.annotations, self.prepared)

    def files(self, root):
        run = root / ".training/comparisons/input-preparation-v1/s4-generation/attempt-001"
        record = output(self.row)
        raw_path = root / record["rawResponsePath"]
        payload = p.runtime.completion_payload(self.row)
        request_sha = p.e.sha(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        metadata = {"endpoint": "/completion", "method": "POST", "authorizationRecorded": False,
                    "requestSha256": request_sha, "sequence": 1}
        p.e.write_new(raw_path, response(self.row))
        p.e.write_new(raw_path.with_name("00001-completion.request.json"), {"metadata": metadata, "payload": payload})
        p.e.write_new(raw_path.with_name("00001-completion.receipt.json"), metadata | {
            "httpStatus": 200, "responseCompleteWithinLimit": True, "responseEofObserved": True,
            "transportOrProtocolErrorType": None, "automaticRetry": False,
            "rawResponseSha256": record["rawResponseSha256"], "rawResponsePath": record["rawResponsePath"],
            "responseBytes": len(raw_path.read_bytes())})
        output_path = run / "outputs" / (self.row["id"] + "-C0.json")
        p.e.write_new(output_path, record)
        return run, raw_path, output_path

    def test_request_response_and_record_revalidated_without_model(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); run, raw_path, output_path = self.files(root)
            verified, refs = p.verify_output(run, self.row["id"], "C0", self.row, root)
            self.assertEqual(verified, output(self.row)); self.assertEqual(len(refs), 4)
            p.verify_refs(root, refs)
            changed = p.e.read_json(raw_path); changed["content"] = "다른 문장"
            raw_path.write_bytes(p.e.packed(changed))
            with self.assertRaisesRegex(ValueError, "raw_hash"): p.verify_output(run, self.row["id"], "C0", self.row, root)

    def test_unmodified_record_fields_are_not_trusted_without_runtime_revalidation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); run, _, output_path = self.files(root)
            changed = p.e.read_json(output_path); changed["actualGenerationSettings"]["seed"] = 43
            output_path.write_bytes(p.e.packed(changed))
            with self.assertRaisesRegex(ValueError, "revalidation_mismatch"): p.verify_output(run, self.row["id"], "C0", self.row, root)

    def test_changed_request_and_incomplete_http_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); run, raw_path, _ = self.files(root)
            request_path = raw_path.with_name("00001-completion.request.json")
            request = p.e.read_json(request_path); request["payload"]["prompt"][0] = 123
            request_path.write_bytes(p.e.packed(request))
            with self.assertRaisesRegex(ValueError, "actual_request_mismatch"): p.verify_output(run, self.row["id"], "C0", self.row, root)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); run, raw_path, _ = self.files(root)
            receipt_path = raw_path.with_name("00001-completion.receipt.json")
            receipt = p.e.read_json(receipt_path); receipt["responseEofObserved"] = False
            receipt_path.write_bytes(p.e.packed(receipt))
            with self.assertRaisesRegex(ValueError, "response_incomplete"): p.verify_output(run, self.row["id"], "C0", self.row, root)

    def test_promotion_cannot_bypass_final64_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); provisional = root / p.REVIEW_BASE / "provisional"
            p.e.write_new(provisional / "private/manifest.json", {"version": p.VERSION, "provisional": True,
                "run": ".training/comparisons/input-preparation-v1/s4-generation/attempt-001",
                "mappings": [{}, {}, {}, {}]})
            with patch.object(p.e, "validate_run_completion", side_effect=ValueError("s4_run_not_complete")) as gate:
                with self.assertRaisesRegex(ValueError, "s4_run_not_complete"):
                    p.verify_promotion(provisional, root / p.REVIEW_BASE / "formal", root / p.REVIEW_BASE / "receipt.json", root)
                gate.assert_called_once()
            self.assertFalse((root / p.REVIEW_BASE / "receipt.json").exists())

    def test_writes_outside_private_training_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, "private_training_subfolder"):
                p.prepare(root / ".training/comparisons/input-preparation-v1/s4-generation/attempt-001", "IP1-F01", root / "public", root)

    def promotion_fixture(self, root):
        outputs = [output(row) for row in self.prepared]
        bundle = p.e.build_review_packets(outputs, self.units, self.annotations, self.prepared)
        run = ".training/comparisons/input-preparation-v1/s4-generation/attempt-001"
        bundle["evidenceFiles"] = [{"path": run + "/predictions.jsonl", "sha256": "a" * 64}]
        provisional = root / p.REVIEW_BASE / "provisional"; final = root / p.REVIEW_BASE / "formal"
        mappings = [m for m in bundle["mapping"] if m["id"] == self.row["id"]]
        packets = {packet["reviewId"]: packet for packet in bundle["sourcePackets"]}
        draft_mappings = []
        for m in mappings:
            packet = packets[m["reviewId"]]
            for folder in (provisional, final): p.e.write_new(folder / "source-packets" / (packet["reviewId"] + ".json"), packet)
            draft_mappings.append({**m, "packetSha256": p.e.sha(p.e.packed(packet)),
                                   "rawResponse": {"path": m["rawResponsePath"], "sha256": m["rawResponseSha256"]}})
        p.e.write_new(provisional / "private/manifest.json", {"version": p.VERSION, "provisional": True,
            "run": run, "mappings": draft_mappings, "evidence": []})
        p.e.write_new(final / "private/bundle.json", bundle)
        p.e.write_new(final / "manifest.json", {"status": "packets_prepared", "questionPackets": 64, "sourcePackets": 64,
                                               "bundleSha256": p.e.sha(p.e.packed(bundle))})
        return provisional, final, mappings[0]["reviewId"]

    def test_promotion_checks_byte_identity_and_does_not_approve_reviews(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); provisional, final, rid = self.promotion_fixture(root)
            receipt = root / p.REVIEW_BASE / "receipt.json"
            with patch.object(p.e, "validate_run_completion", return_value={}):
                result = p.verify_promotion(provisional, final, receipt, root)
            self.assertTrue(result["packetPromotionEligible"])
            self.assertFalse(result["formalSourceReviewsApproved"])
            self.assertTrue(p.e.read_json(receipt)["actualReviewerConfirmationStillRequired"])
            target = final / "source-packets" / (rid + ".json")
            target.write_bytes(target.read_bytes() + b"\n")
            with patch.object(p.e, "validate_run_completion", return_value={}):
                with self.assertRaisesRegex(ValueError, "not_identical"):
                    p.verify_promotion(provisional, final, root / p.REVIEW_BASE / "new-receipt.json", root)

    def test_promotion_rejects_changed_configuration_mapping(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); provisional, final, rid = self.promotion_fixture(root)
            bundle_path = final / "private/bundle.json"; bundle = p.e.read_json(bundle_path)
            next(m for m in bundle["mapping"] if m["reviewId"] == rid)["configuration"] = "C9"
            bundle_path.write_bytes(p.e.packed(bundle))
            manifest_path = final / "manifest.json"; manifest = p.e.read_json(manifest_path)
            manifest["bundleSha256"] = p.e.sha(p.e.packed(bundle)); manifest_path.write_bytes(p.e.packed(manifest))
            with patch.object(p.e, "validate_run_completion", return_value={}):
                with self.assertRaisesRegex(ValueError, "mapping_mismatch"):
                    p.verify_promotion(provisional, final, root / p.REVIEW_BASE / "receipt.json", root)


if __name__ == "__main__":
    unittest.main()
