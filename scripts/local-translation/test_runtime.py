"""Integrity tests without downloading a model or touching the learning DB."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import runtime


class RuntimeTests(unittest.TestCase):
    def translator(self):
        value = runtime.LocalTranslator.__new__(runtime.LocalTranslator)
        calls = []

        def fake(text):
            calls.append(text)
            return "한국어" if text else ""

        value.translate_plain = fake
        value.source_sentences = lambda text: [text]
        return value, calls

    def test_protected_values_never_reach_the_translation_model(self):
        translator, calls = self.translator()
        token = "__PV_a12fdead0010_3__"
        output = translator.translate_segment("Value " + token + " at maturity " + token, [])
        self.assertEqual(output.count(token), 2)
        self.assertTrue(all(token not in text for text in calls))

    def test_equation_symbols_remain_in_original_order(self):
        translator, calls = self.translator()
        output = translator.translate_segment("A ≤ B ± C × D ÷ E", [])
        self.assertEqual([symbol for symbol in output if symbol in "≤±×÷"], list("≤±×÷"))
        self.assertTrue(all(not any(symbol in text for symbol in "≤±×÷") for text in calls))

    def test_glossary_does_not_blindly_replace_terms_inside_sentences(self):
        translator, calls = self.translator()
        glossary = [{"source": "Present Value (PV)", "target": "현재가치"}]
        self.assertEqual(translator.translate_segment("Present Value", glossary), "현재가치")
        self.assertEqual(translator.translate_segment("The present value is uncertain.", glossary), "한국어")
        self.assertEqual(calls, ["The present value is uncertain."])

    def test_accounting_phrase_is_corrected_after_whole_sentence_translation(self):
        translator, calls = self.translator()
        token = "__PV_abcdef001122_0__"
        def fake(text):
            calls.append(text)
            return "금융 진술은 정보를 제공합니다. "
        translator.translate_plain = fake
        source = "Financial statements provide information. "
        rule = {"source":"financial statements","target":"재무제표","mode":"phrase","replacements":["금융 진술"]}
        output = translator.translate_segment_result(source + token, [rule])
        self.assertEqual(output["translatedText"], "재무제표는 정보를 제공합니다. " + token)
        self.assertEqual(calls, [source])
        self.assertEqual(output["warnings"], [])
        unchanged = runtime.correct_sentence_terms("myfinancial statements_extra", "금융 진술은", [rule])
        self.assertEqual(unchanged["translatedText"], "금융 진술은")

    def test_primer_title_is_exact_and_ambiguous_words_remain_model_input(self):
        translator, calls = self.translator()
        self.assertEqual(translator.translate_segment("A Primer on Financial Statements", [{"source":"A Primer on Financial Statements","target":"재무제표 입문","mode":"exact"}]), "재무제표 입문")
        self.assertEqual(calls, [])
        translator.translate_segment("In this primer, we study the rate of return.", [])
        self.assertEqual(calls, ["In this primer, we study the rate of return."])

    def test_missing_or_multiple_target_matches_are_never_inserted_or_replaced(self):
        rule = {"source":"financial statements","target":"재무제표","mode":"phrase","replacements":["금융 진술"]}
        for translated in ["정보를 제공합니다.", "금융 진술과 금융 진술을 읽습니다."]:
            result = runtime.correct_sentence_terms("Read the financial statements.", translated, [rule])
            self.assertEqual(result["translatedText"], translated)
            self.assertTrue(result["warnings"])

    def test_terms_in_different_sentences_do_not_trigger_a_correction(self):
        rule = {"source":"financial statements","target":"재무제표","mode":"phrase","replacements":["금융 진술"]}
        result = runtime.correct_sentence_terms("He made a statement.", "그는 금융 진술을 했습니다.", [rule])
        self.assertEqual(result, {"translatedText":"그는 금융 진술을 했습니다.","warnings":[]})

    def test_longest_source_phrase_wins_and_source_conflicts_are_flagged(self):
        rules = [{"source":"present value","target":"현재가치","mode":"phrase","replacements":["선물 값"]}, {"source":"net present value","target":"순현재가치","mode":"phrase","replacements":["순 선물 값"]}]
        result = runtime.correct_sentence_terms("The net present value is positive.", "순 선물 값은 양수입니다.", rules)
        self.assertEqual(result, {"translatedText":"순현재가치는 양수입니다.","warnings":[]})
        rules.append({"source":"net present value","target":"다른 용어","mode":"phrase","replacements":["순 선물 값"]})
        result = runtime.correct_sentence_terms("The net present value is positive.", "순 선물 값은 양수입니다.", rules)
        self.assertEqual(result["translatedText"], "순 선물 값은 양수입니다.")
        self.assertTrue(result["warnings"])

    def test_acronyms_are_case_sensitive_and_ambiguous_single_words_are_ignored(self):
        rules = [{"source":"return on equity","aliases":["ROE"],"target":"자기자본이익률","mode":"phrase","replacements":["주식 반환"]}, {"source":"return","target":"수익률","mode":"phrase","replacements":["반환"]}]
        self.assertEqual(runtime.correct_sentence_terms("ROE improved.", "주식 반환이 개선되었습니다.", rules)["translatedText"], "자기자본이익률이 개선되었습니다.")
        self.assertEqual(runtime.correct_sentence_terms("roe means fish eggs; please return.", "주식 반환과 반환", rules), {"translatedText":"주식 반환과 반환","warnings":[]})

    def test_korean_particles_follow_the_corrected_final_consonant(self):
        self.assertEqual(runtime.adjusted_particle("재무제표", "으로부터"), "로부터")
        self.assertEqual(runtime.adjusted_particle("자본", "를"), "을")
        self.assertEqual(runtime.adjusted_particle("비율", "으로"), "로")
        self.assertEqual(runtime.adjusted_particle("재무제표", "에서"), "에서")

    def test_canonical_korean_spacing_does_not_trigger_a_false_warning(self):
        rule = {"source":"future value","target":"미래가치","mode":"phrase","replacements":[]}
        result = runtime.correct_sentence_terms("The future value changes.", "미래 가치는 변합니다.", [rule])
        self.assertEqual(result, {"translatedText":"미래 가치는 변합니다.","warnings":[]})
        result = runtime.correct_sentence_terms("The future value changes.", "미래 가치평가가 변합니다.", [rule])
        self.assertTrue(result["warnings"])

    def test_copula_endings_allow_complete_terms_without_matching_other_words(self):
        rule = {"source":"present value","target":"현재가치","mode":"phrase","replacements":["선물 값"]}
        for translated in ["현재가치이다.", "현재가치입니다.", "현재 가치였다."]:
            self.assertEqual(runtime.correct_sentence_terms("This is the present value.", translated, [rule]), {"translatedText":translated,"warnings":[]})
        self.assertTrue(runtime.correct_sentence_terms("This is the present value.", "현재가치평가입니다.", [rule])["warnings"])
        for before, after in [("선물 값이었다.", "현재가치였다."), ("선물 값입니다.", "현재가치입니다."), ("선물 값이다.", "현재가치다.")]:
            self.assertEqual(runtime.correct_sentence_terms("This is the present value.", before, [rule])["translatedText"], after)
        working = {"source":"working capital","target":"운전자본","mode":"phrase","replacements":[]}
        self.assertEqual(runtime.correct_sentence_terms("This was the working capital.", "운전자본이었다.", [working])["warnings"], [])

    def test_bridge_rejects_malformed_rule_metadata_and_duplicate_ids(self):
        from bridge import validate_request
        request = {"id":"test","segments":[{"id":"s1","text":"public fixture"}],"glossary":[{"source":"book value","target":"장부가치","mode":"phrase","aliases":["book values"],"replacements":["책 값"]}]}
        validate_request(request)
        for field, value in [("mode", "regex"), ("aliases", "book values"), ("replacements", [None])]:
            invalid = {**request, "glossary":[{**request["glossary"][0], field:value}]}
            with self.assertRaises(ValueError):
                validate_request(invalid)
        with self.assertRaises(ValueError):
            validate_request({**request, "segments":request["segments"] * 2})

    def test_conflicting_target_spans_are_not_silently_replaced(self):
        rules = [{"source":"cost of debt","target":"부채비용","mode":"phrase","replacements":["금융 비용"]}, {"source":"cost of equity","target":"자기자본비용","mode":"phrase","replacements":["금융 비용"]}]
        result = runtime.correct_sentence_terms("Compare cost of debt and cost of equity.", "금융 비용을 비교합니다.", rules)
        self.assertEqual(result["translatedText"], "금융 비용을 비교합니다.")
        self.assertEqual(len(result["warnings"]), 2)

    def test_another_detected_terms_correct_translation_is_never_overwritten(self):
        rules = [{"source":"cost of debt","target":"부채비용","mode":"phrase","replacements":["자기자본비용"]}, {"source":"cost of equity","target":"자기자본비용","mode":"phrase","replacements":[]}]
        result = runtime.correct_sentence_terms("Compare cost of debt and cost of equity.", "자기자본비용을 비교합니다.", rules)
        self.assertEqual(result["translatedText"], "자기자본비용을 비교합니다.")
        self.assertEqual(len(result["warnings"]), 1)

    def test_network_is_blocked_before_any_connection(self):
        script = "import socket; from runtime import disable_network; disable_network();\ntry: socket.getaddrinfo('example.com',443)\nexcept RuntimeError: print('blocked')\nelse: raise AssertionError('network allowed')"
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).parent, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "blocked")

    def test_model_change_is_rejected_before_loading(self):
        with tempfile.TemporaryDirectory(prefix="argos-integrity-") as directory:
            root = Path(directory)
            model = root / ".translation/packages/en-ko"
            model.mkdir(parents=True)
            file = model / "model.bin"
            file.write_bytes(b"original model")
            (root / "python.exe").write_bytes(b"test executable reference")
            files = [{"path": file.relative_to(root).as_posix(), "size": file.stat().st_size, "sha256": runtime.sha256(file)}]
            manifest = {"schemaVersion": 1, "provider": "argos", "model": runtime.MODEL, "modelHash": hashlib.sha256(json.dumps(files, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()).hexdigest(), "runtimeVersion": "test", "pythonPath": "python.exe", "modelPath": model.relative_to(root).as_posix(), "modelFiles": files}
            (root / ".translation/manifest.json").write_text(json.dumps(manifest), "utf-8")
            with patch.object(runtime, "APP_ROOT", root), patch.object(runtime, "RUNTIME_ROOT", root / ".translation"), patch.object(runtime, "runtime_version", return_value="test"):
                runtime.verify_manifest()
                file.write_bytes(b"tampered model")
                with self.assertRaises(ValueError):
                    runtime.verify_manifest()


if __name__ == "__main__":
    unittest.main()
