"""NDJSON fixtures only: no deployment, model, native process or application DB."""
import copy
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import bridge


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.identity = "hymt:fixture:model-contract"
        self.terms = [{"id": "fixture-term", "source": "margin", "target": "여백", "definition": "합성 검사용 뜻"}]
        self.bundle = SimpleNamespace(identity=self.identity, terms=self.terms,
                                      manifest={"profile": "contextual", "model": "fixture-model", "modelHash": "fixture-sha",
                                                "runtimeVersion": "fixture-runtime"}, assert_unchanged=Mock())
        self.engine = SimpleNamespace(translate=Mock(return_value={"translatedText": "  실제 모형이 아닌 검사 출력이다.  ",
                                                                  "warnings": ["검사용 경고"]}), close=Mock())
        self.verifier = Mock(return_value=self.bundle)
        self.factory = Mock(return_value=self.engine)
        self.request = {"id": "request-1", "modelIdentity": self.identity, "context": "Background only.",
                        "segments": [{"id": "block-a", "text": "The page margin is narrow."}],
                        "glossary": [{"source": "DO NOT PASS", "target": "HIDDEN REFERENCE TARGET"}]}

    def run_bytes(self, raw):
        output = io.StringIO()
        code = bridge.serve(io.BytesIO(raw), output, verifier=self.verifier, engine_factory=self.factory)
        return code, [json.loads(line) for line in output.getvalue().splitlines()]

    def run_rows(self, *rows):
        return self.run_bytes(b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8") for row in rows))

    def test_ready_identity_source_context_registered_terms_and_raw_output(self):
        code, rows = self.run_rows(self.request)
        self.assertEqual(code, 0)
        self.assertEqual(rows[0], {"ready": True, "provider": "hymt", "model": "fixture-model", "modelHash": "fixture-sha",
                                   "runtimeVersion": "fixture-runtime", "identity": self.identity})
        self.factory.assert_called_once_with("contextual")
        self.engine.translate.assert_called_once_with("The page margin is narrow.", "Background only.", self.terms)
        self.assertEqual(rows[1]["data"]["segments"][0], {"id": "block-a", "translatedText": "  실제 모형이 아닌 검사 출력이다.  ",
                                                           "warnings": ["검사용 경고"]})
        self.assertIsNone(rows[1]["inputTokens"])
        self.assertIsNone(rows[1]["outputTokens"])
        self.assertIsNone(rows[1]["requestId"])
        self.engine.close.assert_called_once()
        self.assertEqual(self.bundle.assert_unchanged.call_count, 3)

    def test_identity_mismatch_is_rejected_before_generation(self):
        self.request["modelIdentity"] = "old-registration"
        code, rows = self.run_rows(self.request)
        self.assertEqual(code, 0)
        self.assertEqual(rows[1]["error"]["code"], "LOCAL_INVALID_REQUEST")
        self.engine.translate.assert_not_called()

    def test_duplicate_ids_unknown_fields_blank_and_oversize_sources_are_rejected(self):
        invalid = []
        value = copy.deepcopy(self.request); value["segments"] *= 2; invalid.append(value)
        value = copy.deepcopy(self.request); value["reference"] = "hidden target"; invalid.append(value)
        value = copy.deepcopy(self.request); value["segments"][0]["reference"] = "hidden target"; invalid.append(value)
        for text in ("", "   ", " " * 12000 + "a"):
            value = copy.deepcopy(self.request); value["segments"][0]["text"] = text; invalid.append(value)
        value = copy.deepcopy(self.request); value["context"] = "x" * 12001; invalid.append(value)
        code, rows = self.run_rows(*invalid)
        self.assertEqual(code, 0)
        self.assertEqual(len(rows), len(invalid) + 1)
        self.assertTrue(all(row["error"]["code"] == "LOCAL_INVALID_REQUEST" for row in rows[1:]))
        self.engine.translate.assert_not_called()

    def test_invalid_json_duplicate_keys_unicode_and_nan_do_not_echo_input(self):
        code, rows = self.run_bytes(b'{"secret":"SENSITIVE_FIXTURE",bad}\n{"id":"x","id":"y"}\n\xff\n{"id":NaN}\n')
        self.assertEqual(code, 0)
        self.assertEqual(len(rows), 5)
        self.assertNotIn("SENSITIVE_FIXTURE", json.dumps(rows))
        self.engine.translate.assert_not_called()

    def test_line_bound_stops_reading_and_releases_engine(self):
        raw = b" " * (bridge.MAX_LINE_BYTES + 1) + b"\n"
        code, rows = self.run_bytes(raw)
        self.assertEqual(code, 1)
        self.assertEqual(rows[-1]["error"]["code"], "LOCAL_INVALID_REQUEST")
        self.engine.translate.assert_not_called()
        self.engine.close.assert_called_once()

    def test_deep_json_is_rejected_and_next_valid_request_is_processed(self):
        malformed = b'{"glossary":' + b'[' * 2048 + b'0' + b']' * 2048 + b'}\n'
        valid = (json.dumps(self.request) + "\n").encode("utf-8")
        code, rows = self.run_bytes(malformed + valid)
        self.assertEqual(code, 0)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["error"]["code"], "LOCAL_INVALID_REQUEST")
        self.assertEqual(rows[2]["id"], self.request["id"])
        self.assertIn("data", rows[2])
        self.engine.translate.assert_called_once()
        self.engine.close.assert_called_once()

    def test_setup_failure_does_not_create_engine_or_echo_exception(self):
        self.verifier.side_effect = ValueError("PRIVATE_SETUP_VALUE")
        code, rows = self.run_rows(self.request)
        self.assertEqual(code, 1)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["ready"])
        self.assertNotIn("PRIVATE_SETUP_VALUE", json.dumps(rows))
        self.factory.assert_not_called()

    def test_startup_integrity_failure_closes_created_engine(self):
        self.bundle.assert_unchanged.side_effect = ValueError("changed")
        code, rows = self.run_rows(self.request)
        self.assertEqual(code, 1)
        self.assertFalse(rows[0]["ready"])
        self.engine.translate.assert_not_called()
        self.engine.close.assert_called_once()

    def test_pre_request_change_stops_without_generating_or_processing_next_request(self):
        self.bundle.assert_unchanged.side_effect = [None, ValueError("changed")]
        code, rows = self.run_rows(self.request, self.request)
        self.assertEqual(code, 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["error"]["code"], "LOCAL_PROVIDER_CHANGED")
        self.engine.translate.assert_not_called()

    def test_inflight_change_does_not_publish_generated_text_and_stops(self):
        self.bundle.assert_unchanged.side_effect = [None, None, ValueError("changed")]
        code, rows = self.run_rows(self.request, self.request)
        self.assertEqual(code, 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["error"]["code"], "LOCAL_PROVIDER_CHANGED")
        self.assertNotIn("data", rows[-1])
        self.assertEqual(self.engine.translate.call_count, 1)

    def test_partial_table_failure_never_publishes_partial_success(self):
        self.request["segments"].append({"id": "block-b", "text": "Another cell."})
        self.engine.translate.side_effect = [{"translatedText": "첫 셀", "warnings": []}, ValueError("PRIVATE SOURCE")]
        code, rows = self.run_rows(self.request)
        self.assertEqual(code, 0)
        self.assertEqual(rows[-1]["error"]["code"], "LOCAL_TRANSLATION_FAILED")
        self.assertNotIn("data", rows[-1])
        self.assertNotIn("PRIVATE SOURCE", json.dumps(rows))

    def test_interrupt_is_reported_and_releases_engine(self):
        self.engine.translate.side_effect = KeyboardInterrupt()
        code, rows = self.run_rows(self.request, self.request)
        self.assertEqual(code, 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["error"]["code"], "LOCAL_STOPPED")
        self.engine.close.assert_called_once()

    def test_empty_response_and_malformed_warnings_never_count_as_success(self):
        self.engine.translate.side_effect = [{"translatedText": "", "warnings": []},
                                            {"translatedText": "합성", "warnings": [None]}]
        code, rows = self.run_rows(self.request, self.request)
        self.assertEqual(code, 0)
        self.assertTrue(all(row["error"]["code"] == "LOCAL_TRANSLATION_FAILED" for row in rows[1:]))

    def test_cleanup_failure_has_nonzero_exit_and_bounded_diagnostic(self):
        self.engine.close.side_effect = RuntimeError("SECRET_PROCESS_DETAIL")
        code, rows = self.run_rows()
        self.assertEqual(code, 1)
        self.assertEqual(rows[-1]["error"]["code"], "LOCAL_CLEANUP_FAILED")
        self.assertNotIn("SECRET_PROCESS_DETAIL", json.dumps(rows))


if __name__ == "__main__":
    unittest.main()
