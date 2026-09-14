"""Protocol corruption and diagnostic-vs-technical boundaries; no native loads."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import runtime_contract as c


def row():
    source = "The fee is $20."
    user = (c.baseline.CONTEXT_INSTRUCTION + "\n\n[Background Information]\nNone provided." +
            "\n\n[Conditional terminology references]\nNone matched.\n\n[Source Text]\n" + source)
    prompt = "<|startoftext|>" + user + "<|extra_0|>"
    # Synthetic token fixture, never a real tokenization claim.
    tokens = [127958, 11, 12, 13, 127962]
    return {"id": "X01", "configuration": "C0", "resourceId": "resource-fixture",
            "sourceVersionId": "version-fixture", "targetBlockId": "block-fixture",
            "source": source, "sourceSha256": c.sha_text(source), "context": {"text": ""},
            "terminology": {"hints": []}, "userPrompt": user, "prompt": prompt,
            "promptSha256": c.sha_text(prompt), "promptTokens": len(tokens),
            "tokenIds": tokens, "tokenIdsSha256": c.sha_json(tokens),
            "contextSize": 8192, "outputTokenReserve": 4096, "withinBudget": True}


def response(r=None):
    r = r or row()
    settings = deepcopy(c.baseline.SAMPLING)
    settings.update(grammar="", generation_prompt="", logit_bias=[], lora=[],
                    preserved_tokens=[], grammar_triggers=[])
    for key in ("cache_prompt", "return_tokens", "id_slot"):
        settings.pop(key)
    return {"content": "  수수료는 $20이다.\n", "tokens_predicted": 3,
            "tokens": [21, 22, 127960], "stop": True, "stop_type": "eos",
            "stopping_word": "", "truncated": False, "id_slot": 0,
            "tokens_evaluated": len(r["tokenIds"]), "generation_settings": settings,
            "timings": {"cache_n": 0, "prompt_n": len(r["tokenIds"]), "predicted_n": 3,
                        "prompt_ms": 45.0, "predicted_ms": 101.0}, "tokens_cached": 7}


class RuntimeContractTests(unittest.TestCase):
    def test_all_frozen_s2_rows_and_source_only_projection(self):
        path = c.ROOT / ".training/comparisons/input-preparation-v1/s2-prepared/attempt-002/prompts.jsonl"
        rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
        self.assertEqual(len(rows), 64)
        for value in rows:
            clean = c.source_only_row(value)
            self.assertNotIn("domain", clean)
            self.assertNotIn("provenance", clean)
            c.validate_prompt_row(clean)

    def test_raw_whitespace_and_input_immutable(self):
        r, value = row(), response()
        original = deepcopy(value)
        result = c.validate_completion(r, value, 0.5)
        self.assertEqual(result["translation"], "  수수료는 $20이다.\n")
        self.assertEqual(result["normalization"], "none")
        self.assertEqual(value, original)
        self.assertTrue(all(result["technicalChecks"].values()))

    def test_bad_numbers_remain_completed_quality_failure(self):
        value = response()
        value["content"] = "수수료는 25유로이다."
        result = c.validate_completion(row(), value, 0.5)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["automaticChecksPassed"])
        self.assertFalse(result["automaticChecks"]["numbersPreserved"])
        self.assertFalse(result["automaticChecks"]["currencySymbolsPreserved"])

    def test_incomplete_or_corrupt_responses_stop(self):
        cases = [{"content": " "}, {"content": "번역\ufffd"}, {"content": "번역\ud800"},
                 {"content": "번역<|eos|>"}, {"truncated": True}, {"truncated": 0},
                 {"stop": False}, {"stop": 1}, {"stop_type": "limit"},
                 {"stop_type": "none"}, {"tokens_predicted": 4096},
                 {"tokens_predicted": True}, {"tokens": [21, True, 127960]},
                 {"tokens": [21, 22, 3]}, {"tokens": [127960, 22, 127960]},
                 {"tokens": [21, 127960]}, {"id_slot": 1}, {"tokens_evaluated": 4},
                 {"stopping_word": "bad-stop"}, {"error": {"code": 500}}]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(c.ProtocolError):
                c.validate_completion(row(), response() | change, 0.5)

    def test_sampling_changes_stop(self):
        cases = {"temperature": 0.8, "seed": 43, "samplers": ["temperature", "top_p"],
                 "n_predict": 2048, "stream": True, "n_keep": 1, "grammar": "root ::= 'x'",
                 "logit_bias": [{"token": 3, "bias": -5}], "lora": [{"id": 1, "scale": 1}],
                 "stop": ["other"], "ignore_eos": 0}
        for key, value in cases.items():
            bad = response()
            bad["generation_settings"][key] = value
            with self.subTest(key=key), self.assertRaises(c.ProtocolError):
                c.validate_completion(row(), bad, 0.5)

    def test_float32_native_settings_tolerance(self):
        value = response()
        value["generation_settings"]["temperature"] = 0.699999988079071
        self.assertEqual(c.validate_completion(row(), value, 0.5)["status"], "completed")

    def test_native_token_or_template_mismatch(self):
        r = row()
        c.validate_template_response({"prompt": r["prompt"]}, r)
        c.validate_tokenize_response({"tokens": r["tokenIds"]}, r)
        c.validate_detokenize_response({"content": "$"}, "$")
        for call in (
            lambda: c.validate_template_response({"prompt": r["prompt"] + " "}, r),
            lambda: c.validate_tokenize_response({"tokens": [127958, 11, 14, 13, 127962]}, r),
            lambda: c.validate_detokenize_response({"content": ""}, "$"),
        ):
            with self.assertRaises(c.ProtocolError):
                call()

    def test_payloads_isolated_and_sampling_unchanged(self):
        r = row()
        payload = c.completion_payload(r)
        self.assertEqual(payload, c.baseline.SAMPLING | {"prompt": r["tokenIds"]})
        payload["prompt"].append(100)
        payload["stop"].append("wrong")
        self.assertEqual(len(r["tokenIds"]), 5)
        self.assertEqual(len(c.baseline.SAMPLING["stop"]), 2)
        self.assertEqual(c.template_payload(r)["messages"], [{"role": "user", "content": r["userPrompt"]}])

    def test_prompt_field_hash_or_source_mutation_rejected(self):
        for key, value in {"source": "Other source.", "promptSha256": "0" * 64,
                           "tokenIdsSha256": "0" * 64, "withinBudget": False,
                           "promptTokens": 6, "outputTokenReserve": 2048}.items():
            with self.subTest(key=key), self.assertRaises(c.ProtocolError):
                c.validate_prompt_row(row() | {key: value})

    def test_context_budget_strict_bound(self):
        r = row()
        r["tokenIds"] = [127958] + [11] * 4094 + [127962]
        r["promptTokens"] = len(r["tokenIds"])
        r["tokenIdsSha256"] = c.sha_json(r["tokenIds"])
        with self.assertRaises(c.ProtocolError):
            c.validate_prompt_row(r)

    def test_cached_native_input_or_count_mismatch_rejected(self):
        for field, value in (("prompt_n", 4), ("predicted_n", 2), ("prompt_ms", float("nan")), ("cache_n", 1)):
            bad = response()
            bad["timings"][field] = value
            with self.subTest(field=field), self.assertRaises(c.ProtocolError):
                c.validate_completion(row(), bad, 0.5)

    def test_word_stop_and_eog_preserved(self):
        value = response()
        value.update(stop_type="word", stopping_word="<|extra_5|>", tokens=[21, 22, 127967])
        self.assertEqual(c.validate_completion(row(), value, 0.5)["stopType"], "word")


if __name__ == "__main__":
    unittest.main()
