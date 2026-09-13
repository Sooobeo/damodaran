"""Exercise only CLI routing with mocked verification and inference."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout, redirect_stderr
import io
import json
import sys
import unittest
from unittest.mock import Mock, patch

import infer_quality_v5 as api


class QualityV5CliTests(unittest.TestCase):
    def setUp(self):
        self.previous_verifier = api.infer.verified_model
        self.previous_description = api.infer.__doc__
        self.verified = {"path": api.verifier.v5.APP_ROOT / ".training/runs/synthetic-v5/model",
                         "weightHash": "7" * 64, "fileHashes": {"special_tokens_map.json": "8" * 64},
                         "promotionEligible": False, "evaluationStatus": "experimental", "testWasEvaluated": True}

    def invoke(self, args, *, stdin="", verification_error=None, inference_error=None):
        verify = Mock(return_value=self.verified, side_effect=verification_error)
        translate = Mock(return_value={"translation": "합성 번역", "precision": "fp32"}, side_effect=inference_error)
        output, errors = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(api.verifier, "verified_model", verify))
            stack.enter_context(patch.object(api.infer, "translate", translate))
            stack.enter_context(patch.object(sys, "argv", ["infer_quality_v5.py", *args]))
            stack.enter_context(patch.object(sys, "stdin", io.StringIO(stdin)))
            stack.enter_context(redirect_stdout(output))
            stack.enter_context(redirect_stderr(errors))
            result = api.cli()
        return result, output.getvalue(), errors.getvalue(), verify, translate

    def tearDown(self):
        self.assertIs(api.infer.verified_model, self.previous_verifier)
        self.assertEqual(api.infer.__doc__, self.previous_description)

    def test_default_cpu_fp32_routes_through_new_verifier(self):
        result, output, errors, verify, translate = self.invoke(
            ["--run-id", "synthetic-v5", "--text", "A synthetic CLI input."])
        self.assertEqual(result, 0)
        self.assertEqual(errors, "")
        verify.assert_called_once_with("synthetic-v5")
        translate.assert_called_once_with("A synthetic CLI input.", self.verified, "cpu", 4)
        payload = json.loads(output)
        self.assertEqual(payload["precision"], "fp32")
        self.assertEqual(payload["modelSha256"], self.verified["weightHash"])
        self.assertEqual(payload["tokenizerAndConfigHashes"], self.verified["fileHashes"])
        self.assertFalse(payload["glossaryApplied"])
        self.assertFalse(payload["translationMemoryApplied"])
        self.assertFalse(payload["appDeploymentPerformed"])
        self.assertNotIn("A synthetic CLI input.", output)

    def test_explicit_xpu_threads_and_stdin_are_forwarded(self):
        result, output, _errors, verify, translate = self.invoke(
            ["--run-id", "synthetic-v5", "--device", "xpu", "--threads", "2"],
            stdin="Synthetic standard input.")
        self.assertEqual(result, 0)
        verify.assert_called_once_with("synthetic-v5")
        translate.assert_called_once_with("Synthetic standard input.", self.verified, "xpu", 2)
        self.assertEqual(json.loads(output)["status"], "experimental")

    def test_verification_failure_stops_before_inference(self):
        result, output, _errors, _verify, translate = self.invoke(
            ["--run-id", "synthetic-v5", "--text", "Synthetic input."],
            verification_error=api.infer.InferenceError("Synthetic integrity failure"))
        self.assertEqual(result, 1)
        translate.assert_not_called()
        self.assertEqual(json.loads(output)["error"]["message"], "Synthetic integrity failure")

    def test_unexpected_inference_error_does_not_expose_its_payload(self):
        result, output, _errors, _verify, _translate = self.invoke(
            ["--run-id", "synthetic-v5", "--text", "Synthetic input."],
            inference_error=RuntimeError("private source and runtime exception payload"))
        self.assertEqual(result, 1)
        self.assertNotIn("private source", output)
        self.assertEqual(json.loads(output)["error"]["code"], "LOCAL_V5_QUALITY_INFERENCE_FAILED")

    def test_empty_text_is_rejected_before_model_verification(self):
        result, output, _errors, verify, translate = self.invoke(
            ["--run-id", "synthetic-v5", "--text", " "])
        self.assertEqual(result, 1)
        verify.assert_not_called()
        translate.assert_not_called()
        self.assertIn("12000", json.loads(output)["error"]["message"])

    def test_invalid_device_or_threads_keep_original_cli_validation(self):
        for args in (["--device", "cuda"], ["--threads", "0"], ["--threads", "33"]):
            with self.subTest(args=args):
                with self.assertRaises(SystemExit) as raised:
                    self.invoke(["--run-id", "synthetic-v5", "--text", "Synthetic input.", *args])
                self.assertEqual(raised.exception.code, 2)
                self.assertIs(api.infer.verified_model, self.previous_verifier)

    def test_help_describes_new_v5_route_and_requires_no_verification(self):
        with patch.object(api.verifier, "verified_model") as verify:
            with patch.object(sys, "argv", ["infer_quality_v5.py", "--help"]):
                with redirect_stdout(io.StringIO()) as output:
                    with self.assertRaises(SystemExit) as raised:
                        api.main()
        self.assertEqual(raised.exception.code, 0)
        verify.assert_not_called()
        self.assertIn("serialization-compatible", output.getvalue())
        self.assertIn("{cpu,xpu}", output.getvalue())
        self.assertNotIn("finance-v3", output.getvalue())


if __name__ == "__main__":
    unittest.main()
