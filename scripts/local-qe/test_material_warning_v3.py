"""Real pinned validator plus synthetic assertions; no model or fixed GT read."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import material_warning_v3 as policy

contract = policy._verified_contract()
ROW = {"id": "mapping-v3-synthetic", "source": "Rita hands a key to Omar.",
       "translation": "오마르가 리타에게 열쇠를 건넨다.", "context": ""}


def issue(**updates):
    return {"kind": "mistranslation", "severity": "major", "source_quote": "Rita hands",
            "translation_quote": "오마르가", "reason": "보내는 주체가 바뀌었다는 합성 모델 주장이다."} | updates


def validate(issues=None, uncertainties=None, row=None):
    return contract.validate_response(row or ROW, json.dumps({"semantic_issues": issues or [],
        "uncertainties": uncertainties or []}, ensure_ascii=False))


class MaterialWarningV3Tests(unittest.TestCase):
    def test_empty_arrays_are_assessed_assertions_not_quality_acceptance(self):
        result = policy.classify(validate())
        self.assertFalse(result["primaryWarning"])
        self.assertTrue(result["noMaterialRiskAsserted"])
        self.assertEqual(result["version"], "qwen-material-warning-policy-v3")
        self.assertEqual(result["semanticSpanPrecision"], "not_evaluated")
        self.assertNotIn("accepted", result)
        self.assertNotIn("languageNoteCount", result)

    def test_located_major_and_critical_only_are_red_candidates(self):
        value = validate([issue(severity="minor"), issue(severity="major"), issue(severity="critical")])
        before = copy.deepcopy(value)
        result = policy.classify(value)
        self.assertTrue(result["primaryWarning"])
        self.assertTrue(result["supplementaryMajorWarning"])
        self.assertEqual(result["redCandidateIndices"], [1, 2])
        self.assertEqual(result["majorCriticalAssertionCount"], 2)
        self.assertEqual(result["minorAssertionCount"], 1)
        self.assertEqual(value, before)

    def test_missing_or_ambiguous_major_target_keeps_paragraph_warning(self):
        for quote, row in (("찾을 수 없는 인용", ROW), ("오마르가", ROW | {"translation":"오마르가 오마르가"})):
            with self.subTest(quote=quote):
                result = policy.classify(validate([issue(translation_quote=quote)], row=row))
                self.assertTrue(result["primaryWarning"])
                self.assertEqual(result["redCandidateIndices"], [])
                self.assertEqual(result["unlocalizedMajorCriticalCount"], 1)
                self.assertGreater(result["allUnverifiedQuoteCount"], 0)

    def test_major_omission_has_no_target_red_candidate(self):
        result = policy.classify(validate([issue(kind="omission", translation_quote="")]))
        self.assertTrue(result["primaryWarning"])
        self.assertEqual(result["redCandidateIndices"], [])
        self.assertEqual(result["allQuoteDiagnosticsCount"], 1)

    def test_addition_with_unique_target_is_candidate_without_fabricated_source(self):
        result = policy.classify(validate([issue(kind="addition", source_quote="")]))
        self.assertTrue(result["primaryWarning"])
        self.assertEqual(result["redCandidateIndices"], [0])
        self.assertEqual(result["allQuoteDiagnosticsCount"], 1)

    def test_uncertainty_warns_but_never_becomes_a_red_candidate(self):
        uncertainty = {"source_quote":"Rita", "translation_quote":"오마르가", "reason":"지시 대상이 모호하다는 합성 주장이다."}
        result = policy.classify(validate(uncertainties=[uncertainty]))
        self.assertTrue(result["primaryWarning"])
        self.assertFalse(result["supplementaryMajorWarning"])
        self.assertEqual(result["redCandidateIndices"], [])

    def test_minor_keywords_and_unverified_quotes_never_promote_severity(self):
        result = policy.classify(validate([issue(severity="minor", translation_quote="없는 구절",
            reason="major critical 심각한 의미 왜곡이라는 단어가 들어 있어도 분류는 minor다.")]))
        self.assertFalse(result["primaryWarning"])
        self.assertFalse(result["supplementaryMajorWarning"])
        self.assertEqual(result["minorAssertionCount"], 1)
        self.assertTrue(result["contractDiagnosticWholeParagraphReview"])
        self.assertEqual(result["redCandidateIndices"], [])

    def test_real_invalid_json_and_schema_envelopes_are_unassessed(self):
        for raw in ("{broken", json.dumps({"semantic_issues":[],"uncertainties":[],"language_notes":[]})):
            result = policy.classify(contract.validate_response(ROW, raw))
            self.assertEqual(result["status"], "unassessed")
            self.assertIsNone(result["primaryWarning"])
            self.assertIsNone(result["supplementaryMajorWarning"])
            self.assertFalse(result["noMaterialRiskAsserted"])
            self.assertEqual(result["redCandidateIndices"], [])

    def test_v1_valid_invalid_and_claimed_valid_legacy_fields_are_rejected(self):
        for raw in ('{"semantic_issues":[],"uncertainties":[],"language_notes":[]}', '{broken'):
            old = contract._shared.validate_response(ROW, raw)
            with self.assertRaises(ValueError):
                policy.classify(old)
        for mutate in (lambda x:x["assessment"].update(language_notes=[]),
                       lambda x:x["assessment"]["semantic_issues"][0].update(dimension="actor")):
            value = validate([issue()]); mutate(value)
            with self.assertRaises(ValueError):
                policy.classify(value)

    def test_validator_metadata_and_derived_counts_are_strictly_typed(self):
        mutations = {"contract_version":"qwen35-meaning-v1", "offset_unit":"codepoints", "end_exclusive":1,
            "semantic_quality_certified":True, "semantic_span_precision":"verified", "count_basis":"unique_errors",
            "error_count":True, "uncertainty_count":False, "unverified_span_count":0.0,
            "whole_paragraph_review":1, "semantic":"no_findings", "span_status":"none", "validation_errors":["fabricated"]}
        for field, changed in mutations.items():
            with self.subTest(field=field), self.assertRaises(ValueError):
                policy.classify(validate([issue()]) | {field:changed})

    def test_duplicate_missing_extra_or_boolean_diagnostic_indices_are_rejected(self):
        for mutation in ("duplicate", "missing", "extra_index", "boolean_index"):
            value = validate([issue()]); diagnostics = value["span_diagnostics"]
            if mutation == "duplicate": diagnostics.append(copy.deepcopy(diagnostics[0]))
            elif mutation == "missing": diagnostics.pop()
            elif mutation == "extra_index": diagnostics[0]["index"] = 7
            else: diagnostics[0]["index"] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                policy.classify(value)

    def test_forged_quotes_statuses_and_unverified_offsets_are_rejected(self):
        for changes in ({"quote":"not the submitted quote"}, {"status":"verified"}, {"side":"context"},
                        {"start":True}, {"end":99}, {"extra":1}):
            value = validate([issue()]); value["span_diagnostics"][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                policy.classify(value)
        value = validate([issue(source_quote="absent")]); value["span_diagnostics"][0]["start"] = 0
        with self.assertRaises(ValueError): policy.classify(value)

    def test_unique_spans_inventory_and_utf16_extents_must_match(self):
        row = ROW | {"translation":"💡오마르가 건넨다."}
        value = validate([issue(translation_quote="💡오마르가")], row=row)
        self.assertEqual(policy.classify(value)["redCandidateIndices"], [0])
        for mutation in ("duplicate", "missing", "wrong_length"):
            changed = copy.deepcopy(value)
            if mutation == "duplicate": changed["unique_spans"].append(copy.deepcopy(changed["unique_spans"][0]))
            elif mutation == "missing": changed["unique_spans"].pop()
            else:
                changed["span_diagnostics"][1]["end"] -= 1
                changed["unique_spans"][1]["end"] -= 1
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): policy.classify(changed)

    def test_invalid_envelope_cannot_claim_valid_semantics_or_have_forged_errors(self):
        value = contract.validate_response(ROW, "{broken")
        for changes in ({"semantic":"no_findings"}, {"error_count":0}, {"whole_paragraph_review":False},
                        {"status":"failed"}, {"validation_errors":[]}, {"assessment":{}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): policy.classify(value | changes)
        forged = contract.validate_response(ROW, '{}'); forged['validation_errors'] = ['invented error']
        with self.assertRaises(ValueError): policy.classify(forged)

    def test_identity_binds_frozen_policy_and_contract_dependencies(self):
        identity = policy.identity()
        self.assertEqual(identity["version"], policy.VERSION)
        self.assertEqual(identity["policySha256"], policy.POLICY_SHA256)
        self.assertEqual(identity["validationContract"]["validatorSha256"], policy.CONTRACT_FILES["contract_v2.py"])
        identity["validationContract"].clear()
        self.assertEqual(policy.identity()["validationContract"]["version"], "qwen35-meaning-v2")
        real = policy._sha
        for target in (policy.POLICY_PATH, *[policy.CONTRACT_DIR/name for name in policy.CONTRACT_FILES]):
            with self.subTest(target=target.name), patch.object(policy, "_sha", lambda p,t=target:'0'*64 if p==t else real(p)):
                with self.assertRaises(ValueError): policy.identity()
                with self.assertRaises(ValueError): policy.classify(validate())


if __name__ == "__main__":
    unittest.main()
