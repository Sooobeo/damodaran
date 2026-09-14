"""Synthetic source-free boundary checks, no model or actual questions."""
import copy
import json
import unittest
from input_execution_v1 import local_question_contract_v1 as c


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.packet = {"reviewId": "R001", "translation": "문은 닫혔다. 창은 열렸다.",
                       "questions": [{"questionId": "q1", "questionKo": "문 상태?"},
                                     {"questionId": "q2", "questionKo": "창 상태?"}]}
        self.raw = {"reviewId": "R001", "answers": {q: {
            "answerKo": "닫혔다" if q == "q1" else "열렸다", "reasonKo": "본문의 명시적인 서술이다.",
            "cannotDetermine": False, "translationEvidence": ["문은 닫혔다." if q == "q1" else "창은 열렸다."]
        } for q in ("q1", "q2")}}

    def test_only_packet_and_fixed_instruction_delivered(self):
        request = c.messages(self.packet)
        self.assertEqual(request[0]["content"], c.INSTRUCTION)
        self.assertEqual(c.parse(request[1]["content"]), self.packet)
        for forbidden in ("source", "context", "configuration", "correctAnswer", "reviewer"):
            packet = self.packet | {forbidden: "secret"}
            with self.assertRaises(ValueError): c.messages(packet)

    def test_question_allowlist(self):
        for key in ("isCore", "sourceEvidence", "originalQuestionId"):
            packet = copy.deepcopy(self.packet)
            packet["questions"][0][key] = "secret"
            with self.assertRaises(ValueError): c.messages(packet)

    def test_control_boundaries_in_translation_and_question_rejected(self):
        for literal in ("<|im_start|>", "<|im_end|>", "<|endoftext|>", "<think>", "</think>"):
            for field in ("translation", "question"):
                packet = copy.deepcopy(self.packet)
                if field == "translation": packet[field] += literal
                else: packet["questions"][0]["questionKo"] += literal
                with self.assertRaises(ValueError): c.messages(packet)

    def test_packet_inventory(self):
        for mutate in (lambda p: p.update(reviewId="R065"), lambda p: p["questions"].pop(),
                       lambda p: p["questions"].reverse(), lambda p: p.update(translation=" ")):
            packet = copy.deepcopy(self.packet); mutate(packet)
            with self.assertRaises(ValueError): c.validate_packet(packet)

    def test_schema_quotes_only_given_translation(self):
        values = c.response_schema(self.packet)["properties"]["answers"]["properties"]["q1"]["properties"]["translationEvidence"]["items"]["enum"]
        self.assertEqual(values, ["문은 닫혔다.", "창은 열렸다.", self.packet["translation"]])
        self.assertTrue(all(q in self.packet["translation"] for q in values))

    def test_repeated_sentence_uses_only_unambiguous_quote(self):
        packet = self.packet | {"translation": "반복이다. 반복이다."}
        self.assertEqual(c.evidence_options(packet), [packet["translation"]])

    def test_structural_conversion_does_not_change_text(self):
        rows = c.decode_draft(json.dumps(self.raw, ensure_ascii=False), self.packet)["answers"]
        for q, row in zip(("q1", "q2"), rows):
            self.assertEqual(row, {"questionId": q, **self.raw["answers"][q]})

    def test_id_checked_before_conversion(self):
        raw = self.raw | {"reviewId": "R002"}
        with self.assertRaises(ValueError): c.decode_draft(json.dumps(raw), self.packet)

    def test_question_inventory_and_fields(self):
        raw = copy.deepcopy(self.raw); raw["answers"]["q3"] = raw["answers"].pop("q2")
        with self.assertRaises(ValueError): c.decode_draft(json.dumps(raw), self.packet)
        raw = copy.deepcopy(self.raw); raw["answers"]["q1"]["source"] = "secret"
        with self.assertRaises(ValueError): c.decode_draft(json.dumps(raw), self.packet)

    def test_quotes_never_repaired(self):
        for quotes in (["문은 열렸다."], ["문은 닫혔다"], [], [self.packet["translation"]] * 4):
            raw = copy.deepcopy(self.raw); raw["answers"]["q1"]["translationEvidence"] = quotes
            with self.assertRaises(ValueError): c.decode_draft(json.dumps(raw), self.packet)

    def test_abstention_can_have_no_quote(self):
        raw = copy.deepcopy(self.raw)
        raw["answers"]["q1"].update(cannotDetermine=True, translationEvidence=[], answerKo="판단할 수 없다.")
        result = c.decode_draft(json.dumps(raw), self.packet)
        self.assertEqual(result["answers"][0]["translationEvidence"], [])

    def test_duplicate_json_and_nonfinite_rejected(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}'):
            with self.assertRaises(ValueError): c.parse(raw)

    def test_budget_strict_and_sampling_fixed(self):
        self.assertEqual(c.completion_payload(self.packet, [1] * 2047)["n_predict"], 2048)
        for tokens in ([1] * 2048, [], [True], [1, -1]):
            with self.assertRaises(ValueError): c.completion_payload(self.packet, tokens)

    def response(self):
        return {"stop_type": "eos", "truncated": False, "stopping_word": "", "prompt": "rendered",
                "tokens": [1, 2], "tokens_evaluated": 3, "tokens_predicted": 2,
                "timings": {"cache_n": 0, "prompt_n": 3}, "generation_settings": dict(c.NATIVE_SAMPLING),
                "content": json.dumps(self.raw, ensure_ascii=False)}

    def test_native_metadata_and_draft(self):
        self.assertEqual(c.validate_native(self.response(), "rendered", [1, 2, 3], self.packet)["reviewId"], "R001")
        for key, value in (("stop_type", "limit"), ("truncated", True), ("prompt", "other"),
                           ("tokens_evaluated", 2), ("tokens_predicted", 3), ("tokens", [])):
            response = self.response(); response[key] = value
            with self.assertRaises(ValueError): c.validate_native(response, "rendered", [1, 2, 3], self.packet)

    def test_cache_reuse_and_sampling_rejected(self):
        for key, value in (("cache_n", 1), ("cache_n", False), ("prompt_n", 2)):
            response = self.response(); response["timings"][key] = value
            with self.assertRaises(ValueError): c.validate_native(response, "rendered", [1, 2, 3], self.packet)
        response = self.response(); response["generation_settings"]["seed"] += 1
        with self.assertRaises(ValueError): c.validate_native(response, "rendered", [1, 2, 3], self.packet)


if __name__ == "__main__":
    unittest.main()
