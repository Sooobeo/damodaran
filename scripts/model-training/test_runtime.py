"""Offline runtime tests with fake tokenizers and a fake native translator.

No real model, training corpus, held-out corpus, export or registration is read
or executed. Run with .venv-training/Scripts/python.exe on this file.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


DIRECTORY = Path(__file__).resolve().parent


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, DIRECTORY / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = load_module("finetuned_runtime_under_test", "runtime.py")
train = load_module("finetuned_train_for_runtime_tests", "train.py")
with patch.dict(sys.modules, {"runtime": runtime, "train": train}):
    bridge = load_module("finetuned_bridge_under_test", "bridge.py")
    infer = load_module("finetuned_infer_for_runtime_tests", "infer.py")
    with patch.dict(sys.modules, {"infer": infer}):
        deploy = load_module("finetuned_deploy_under_test", "deploy.py")
        exporter = load_module("finetuned_export_under_test", "export_model.py")


class FakeSourceTokenizer:
    def __init__(self, pieces=None):
        self.pieces = pieces
        self.inputs = []

    def encode(self, text, out_type):
        assert out_type is str
        self.inputs.append(text)
        return list(self.pieces) if self.pieces is not None else text.split()

    def piece_to_id(self, piece):
        return 0 if piece == "unknown-piece" else 7


class FakeTargetTokenizer:
    def decode_pieces(self, pieces):
        return " ".join(pieces)


class FakeNativeTranslator:
    def __init__(self, hypothesis=None):
        self.hypothesis = ["번역", "결과", "</s>"] if hypothesis is None else hypothesis
        self.calls = []

    def translate_batch(self, batches, **options):
        self.calls.append({"batches": copy.deepcopy(batches), "options": options})
        return [SimpleNamespace(hypotheses=[list(self.hypothesis)])]


def translator(pieces=None, hypothesis=None, max_input=192, max_output=256):
    value = runtime.LocalTranslator.__new__(runtime.LocalTranslator)
    value.settings = {"beams": 4, "maxInputTokens": max_input, "maxNewTokens": max_output}
    value.source = FakeSourceTokenizer(pieces)
    value.target = FakeTargetTokenizer()
    value.native = FakeNativeTranslator(hypothesis)
    value.glossary = runtime.glossary_module()
    return value


class NativeRuntimeTests(unittest.TestCase):
    def test_source_eos_is_appended_and_native_truncation_is_disabled(self):
        value = translator(pieces=["ordinary-piece", "unknown-piece"])
        result = value.raw_result("An independent example.")
        call = value.native.calls[0]
        self.assertEqual(call["batches"], [["ordinary-piece", "<unk>", "</s>"]])
        self.assertEqual(call["options"]["max_input_length"], 0)
        self.assertTrue(call["options"]["return_end_token"])
        self.assertEqual(call["options"]["beam_size"], 4)
        self.assertEqual(call["options"]["suppress_sequences"], [["<pad>"]])
        self.assertEqual(result["translatedText"], "번역 결과")
        self.assertFalse(result["atLengthLimit"])

    def test_runtime_identity_declares_learned_decoder_start_embedding(self):
        self.assertEqual(runtime.PROCESSING["decoderStartToken"], "<pad>")
        self.assertTrue(runtime.PROCESSING["preserveLearnedPadEmbedding"])
        self.assertEqual(runtime.MODEL_DIRECTORY, "ctranslate2-pad-v2")

    def test_export_keeps_the_learned_pad_row_and_uses_it_as_decoder_start(self):
        class FakeMarianLoader:
            def set_decoder(self, spec, decoder):
                spec.embeddings = decoder
                spec.start_from_zero_embedding = True
            def set_config(self, config, model, tokenizer):
                config.decoder_start_token = "</s>"
        loader = exporter.build_loader(FakeMarianLoader)
        embedding = [[0.0], [0.012]]
        spec = SimpleNamespace()
        loader.set_decoder(spec, embedding)
        loader._remove_pad_weights(spec)
        self.assertIs(spec.embeddings, embedding)
        self.assertEqual(spec.embeddings[-1], [0.012])
        self.assertFalse(spec.start_from_zero_embedding)
        config = SimpleNamespace()
        model = SimpleNamespace(config=SimpleNamespace(decoder_start_token_id=32000))
        tokenizer = SimpleNamespace(pad_token_id=32000, pad_token="<pad>")
        loader.set_config(config, model, tokenizer)
        self.assertEqual(config.decoder_start_token, "<pad>")

    def test_export_vocabulary_keeps_all_32001_ids_including_pad(self):
        mapping = {"piece-" + str(index): index for index in range(32000)}
        mapping["<pad>"] = 32000
        tokenizer = SimpleNamespace(separate_vocabs=True, encoder=mapping, target_encoder=dict(mapping))
        loader = exporter.build_loader(object)
        vocabularies = loader.get_vocabulary(None, tokenizer)
        self.assertEqual([len(value) for value in vocabularies], [32001, 32001])
        self.assertEqual([value[-1] for value in vocabularies], ["<pad>", "<pad>"])

    def test_input_limit_includes_eos_and_rejects_before_native_generation(self):
        value = translator(pieces=["word"] * 16, max_input=16)
        with self.assertRaisesRegex(ValueError, "no source truncation"):
            value.raw_result("This sentence must not be shortened silently.")
        self.assertEqual(value.native.calls, [])

    def test_empty_or_unterminated_or_length_capped_outputs_are_rejected(self):
        cases = (["<unk>", "</s>"], [], ["아직", "끝나지"], ["단어"] * 15 + ["</s>"])
        for hypothesis in cases:
            with self.subTest(hypothesis=hypothesis):
                value = translator(hypothesis=hypothesis, max_output=16)
                with self.assertRaisesRegex(ValueError, "Empty or capped"):
                    value.translate_plain("Translate this complete sentence.")

    def test_reserved_token_literals_cannot_be_injected_into_raw_generation(self):
        for token in ("<unk>", "<pad>", "<s>", "</s>"):
            with self.subTest(token=token):
                value = translator()
                with self.assertRaisesRegex(ValueError, "Reserved tokenizer literal"):
                    value.raw_result("Do not interpret " + token + " as a control token.")
                self.assertEqual(value.native.calls, [])

    def test_raw_input_character_cap_is_enforced_before_tokenization(self):
        value = translator()
        with self.assertRaisesRegex(ValueError, "Invalid translation input"):
            value.raw_result("x" * 150001)
        self.assertEqual(value.source.inputs, [])
        self.assertEqual(value.native.calls, [])

    def test_valid_plain_translation_preserves_surrounding_whitespace(self):
        value = translator()
        self.assertEqual(value.translate_plain(" \tAn example.\n"), " \t번역 결과\n")
        self.assertEqual(value.source.inputs, ["An example."])

    def test_numeric_protection_markers_never_reach_native_inference(self):
        # The application replaces numeric values with these markers first.
        # This tests the Python boundary, not a claim that raw_result shields digits.
        marker = "__PV_deadbeef_12__"
        value = translator()
        result = value.translate_segment_result("Value " + marker + " grows by " + marker, [])
        self.assertEqual(result["translatedText"].count(marker), 2)
        self.assertTrue(value.native.calls)
        self.assertTrue(all(marker not in text and "__PV_" not in text for text in value.source.inputs))
        self.assertTrue(all(marker not in piece for call in value.native.calls for batch in call["batches"] for piece in batch))

    def test_numeric_formula_is_preserved_without_native_generation(self):
        value = translator()
        formula = "3.5 × 2 = 7 ≤ 10"
        self.assertEqual(value.translate_segment(formula, []), formula)
        self.assertEqual(value.source.inputs, [])
        self.assertEqual(value.native.calls, [])

    def test_comparison_symbols_keep_their_order_between_translated_fragments(self):
        value = translator()
        output = value.translate_segment("Assets ≤ Claims ± Buffer × Weight ÷ Scale", [])
        self.assertEqual([character for character in output if character in "≤±×÷"], list("≤±×÷"))
        self.assertTrue(all(not any(character in text for character in "≤±×÷") for text in value.source.inputs))

    def test_abbreviations_and_decimals_do_not_split_their_sentence(self):
        value = translator()
        source = "Dr. Gray reviewed 2.5 pages. Then he left."
        pieces = value.source_sentences(source)
        self.assertEqual(pieces, ["Dr. Gray reviewed 2.5 pages. ", "Then he left."])
        self.assertEqual("".join(pieces), source)


def request():
    return {"id": "request-fixture", "segments": [{"id": "block-a", "text": "An independent fixture."}],
            "glossary": [{"source": "book value", "target": "장부가치", "mode": "phrase",
                          "aliases": ["book values"], "replacements": ["책 값"]}]}


class BridgeRequestTests(unittest.TestCase):
    def test_distinct_segments_with_a_bounded_glossary_are_valid(self):
        value = request()
        value["segments"].append({"id": "block-b", "text": "Another independent fixture."})
        self.assertIsNone(bridge.validate_request(value))

    def test_duplicate_segment_ids_are_rejected_even_with_different_text(self):
        value = request()
        value["segments"].append({"id": "block-a", "text": "Different content with the same identity."})
        with self.assertRaisesRegex(ValueError, "Invalid segment"):
            bridge.validate_request(value)

    def test_total_character_limit_cannot_be_bypassed_with_multiple_segments(self):
        value = request()
        value["segments"] = [{"id": "first", "text": "x" * 75000}, {"id": "second", "text": "y" * 75001}]
        with self.assertRaisesRegex(ValueError, "Invalid input size"):
            bridge.validate_request(value)

    def test_segment_and_glossary_count_limits_are_enforced(self):
        value = request()
        value["segments"] = [{"id": str(index), "text": "x"} for index in range(1001)]
        with self.assertRaisesRegex(ValueError, "Invalid segments"):
            bridge.validate_request(value)
        value = request()
        value["glossary"] *= 101
        with self.assertRaisesRegex(ValueError, "Invalid input size"):
            bridge.validate_request(value)

    def test_invalid_rule_variants_cannot_reach_translation(self):
        for field, invalid in (("mode", "regex"), ("aliases", "not-a-list"), ("replacements", [None])):
            with self.subTest(field=field):
                value = request()
                value["glossary"][0][field] = invalid
                with self.assertRaises(ValueError):
                    bridge.validate_request(value)


def domain_scores(count, terms):
    return {"count": count, "chrF": 60.0, "bleu": 35.0, "numericPreservation": 1.0,
            "termAccuracy": terms, "emptyOutputs": 0, "cappedOutputs": 0}


def parity_pair():
    base = {"count": 40, "byDomain": {"finance": domain_scores(30, 0.8), "general": domain_scores(10, None)}}
    candidate = copy.deepcopy(base)
    candidate["byDomain"]["finance"].update({"chrF": 70.0, "bleu": 45.0, "termAccuracy": 0.9})
    return base, candidate


class ConversionParityTests(unittest.TestCase):
    def test_finance_improvement_with_general_retention_is_eligible(self):
        base, candidate = parity_pair()
        self.assertTrue(deploy.parity_gate(base, candidate)["passed"])

    def test_finance_improvement_cannot_hide_general_quality_regression(self):
        for metric in ("chrF", "bleu"):
            with self.subTest(metric=metric):
                base, candidate = parity_pair()
                candidate["byDomain"]["general"][metric] -= 1.5
                result = deploy.parity_gate(base, candidate)
                self.assertFalse(result["passed"])
                self.assertIn(metric + "-regression", result["byDomain"]["general"]["reasons"])

    def test_better_text_metrics_cannot_hide_numeric_regression_in_either_domain(self):
        for domain in ("finance", "general"):
            with self.subTest(domain=domain):
                base, candidate = parity_pair()
                candidate["byDomain"][domain]["numericPreservation"] = 0.9
                result = deploy.parity_gate(base, candidate)
                self.assertFalse(result["passed"])
                self.assertIn("numeric-regression", result["byDomain"][domain]["reasons"])

    def test_better_fluency_scores_cannot_hide_financial_term_coverage_loss(self):
        base, candidate = parity_pair()
        candidate["byDomain"]["finance"]["termAccuracy"] = 0.77
        result = deploy.parity_gate(base, candidate)
        self.assertFalse(result["passed"])
        self.assertIn("term-coverage-regression", result["byDomain"]["finance"]["reasons"])

    def test_missing_domain_or_different_sample_counts_are_rejected(self):
        for change in ("missing", "domain-count", "total-count"):
            with self.subTest(change=change):
                base, candidate = parity_pair()
                if change == "missing":
                    del candidate["byDomain"]["general"]
                elif change == "domain-count":
                    candidate["byDomain"]["general"]["count"] = 9
                else:
                    candidate["count"] = 39
                self.assertFalse(deploy.parity_gate(base, candidate)["passed"])

    def test_nonfinite_quality_metrics_and_capped_outputs_are_rejected(self):
        for field, invalid in (("chrF", float("nan")), ("bleu", float("inf")), ("emptyOutputs", 1), ("cappedOutputs", 1)):
            with self.subTest(field=field):
                base, candidate = parity_pair()
                candidate["byDomain"]["general"][field] = invalid
                self.assertFalse(deploy.parity_gate(base, candidate)["passed"])


class DependencyBoundaryTests(unittest.TestCase):
    def test_importing_runtime_and_bridge_does_not_load_training_frameworks(self):
        # A fresh interpreter catches attempted imports even if they would have
        # been cached by another test. No native model constructor is called.
        program = "\n".join([
            "import importlib.abc, sys",
            f"sys.path.insert(0, {str(DIRECTORY)!r})",
            "class BlockTrainingFrameworks(importlib.abc.MetaPathFinder):",
            "    def find_spec(self, fullname, path=None, target=None):",
            "        if fullname.split('.')[0] in {'torch', 'transformers'}:",
            "            raise AssertionError('Training dependency imported: ' + fullname)",
            "        return None",
            "sys.meta_path.insert(0, BlockTrainingFrameworks())",
            "import runtime, bridge, deploy",
            "runtime.glossary_module()",
            "assert not any(name.split('.')[0] in {'torch', 'transformers'} for name in sys.modules)",
            "print('imports-without-training-frameworks')",
        ])
        result = subprocess.run([sys.executable, "-X", "utf8", "-c", program], cwd=DIRECTORY,
                                capture_output=True, text=True, timeout=15, check=True)
        self.assertEqual(result.stdout.strip(), "imports-without-training-frameworks")


if __name__ == "__main__":
    unittest.main(verbosity=2)
