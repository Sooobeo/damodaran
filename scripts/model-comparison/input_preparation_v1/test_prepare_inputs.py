"""S2 synthetic integration tests; counters here are NOT native token evidence.

Source/terms are independent synthetic diagnostics. The Jinja template below
was read from the pinned GGUF metadata and is checked against the frozen hash.
No dev/evaluation source, candidate translation, native DLL or model tensor is
read by this test suite. Run with the existing Jinja-capable training Python.
"""

from copy import deepcopy
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/model-comparison"))
from input_preparation_v1 import prepare_inputs as prepare
from input_preparation_v1 import tokenizer_client
from input_preparation_v1.context_selection import text_sha256


TEMPLATE = "{% set ns = namespace(has_head=true) %}{% set loop_messages = messages %}{% for message in loop_messages %}{% set content = message['content'] %}{% if loop.index0 == 0 %}{% if content == '' %}{% set ns.has_head = false %}{% elif message['role'] == 'system' %}{% set content = '<|startoftext|>' + content + '<|extra_4|>' %}{% endif %}{% endif %}{% if message['role'] == 'user' %}{% if loop.index0 == 1 and ns.has_head %}{% set content = content + '<|extra_0|>' %}{% else %}{% set content = '<|startoftext|>' + content + '<|extra_0|>' %}{% endif %}{% elif message['role'] == 'assistant' %}{% set content = content + '<|eos|>' %}{% endif %}{{ content }}{% endfor %}"
DICTIONARY = json.loads((ROOT / "content/model-comparison/input-preparation-v1/sense-dictionary.json").read_text("utf-8"))
TERMS = [
    {"id": "synthetic-equity", "source": "equity", "aliases": ["equities"],
     "target": "지분", "definition": "기업에 대한 소유 지분."},
    {"id": "synthetic-interest", "source": "interest", "aliases": [],
     "target": "이자", "definition": "빌린 돈의 사용 대가."},
]


def block(identifier, order, text, kind="paragraph", metadata=None):
    return {"id": identifier, "order": order, "kind": kind, "text": text,
            "textSha256": text_sha256(text), "metadata": metadata or {"schemaVersion": 1, "links": []}}


def unit(blocks=None):
    return {"id": "synthetic-unit", "domain": "general",
            "provenance": {"kind": "assistant-authored-structured-fixture", "humanReviewed": False},
            "document": {"resourceId": "synthetic-resource", "sourceVersionId": "synthetic-version",
                         "title": "Synthetic source", "titleKo": "", "format": "html",
                         "extractorVersion": "synthetic-v1",
                         "blocks": blocks or [block("target", 0, "The founders own equity in the company.")]},
            "targetBlockId": "target"}


def prompt_sections(prompt):
    background = prompt.split("[Background Information]\n", 1)[1].split("\n\n[Conditional terminology references]\n", 1)[0]
    references = prompt.split("[Conditional terminology references]\n", 1)[1].split("\n\n[Source Text]\n", 1)[0]
    return background, references


def counter_with_context_and_hint_costs(base, fragment_cost, hint_cost):
    """Synthetic discontinuous oracle to exercise final-hint budget feedback."""
    calls = []
    def count(prompt):
        calls.append(prompt)
        background, references = prompt_sections(prompt)
        fragments = 0 if background == "None provided." else len(background.splitlines())
        hints = 0 if references == "None matched." else len(references.splitlines())
        return base + fragments * fragment_cost + hints * hint_cost
    return count, calls


class InputPreparationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if prepare.baseline.sha_text(TEMPLATE) != prepare.baseline.TEMPLATE_SHA:
            raise AssertionError("synthetic_test_template_is_not_the_frozen_template")

    def run_config(self, value, config, count=None, terms=None):
        return prepare.prepare_one(value, config, TERMS if terms is None else terms,
                                   DICTIONARY, TEMPLATE, count or (lambda _: 100))

    def test_c0_exact_frozen_builder_renderer_and_existing_duplicate(self):
        source = "Equity and equities.\r\nInterest and equity remain distinct: $3 / 2 = 1.5. 😀"
        value = unit([block("far", 0, "Not an order neighbor."),
                      block("target", 4, source), block("after", 5, "Following source.")])
        value["document"].update(title="An English title", titleKo="한국어 제목")
        original = deepcopy(value)
        result = self.run_config(value, "C0")
        expected_context = "An English title\n한국어 제목\n" + source + "\nFollowing source."
        expected_content, expected_matches = prepare.baseline.build_user_prompt(
            {"source": source, "context": expected_context}, "contextual", TERMS)
        self.assertEqual(result["context"]["text"], expected_context)
        self.assertEqual(result["userPrompt"], expected_content)
        self.assertEqual(result["prompt"], prepare.baseline.render_prompt(TEMPLATE, expected_content))
        self.assertEqual(result["terminology"]["decisions"], expected_matches)
        self.assertEqual(len(result["terminology"]["hints"]), 2)
        self.assertIn("existing_target_duplicate", [f["reason"] for f in result["context"]["fragments"]])
        self.assertEqual(value, original)

    def test_c1_c3_keep_target_bytes_and_apply_whole_block_budget(self):
        source = "Output remained $3 / 2 = 1.5.\r\n  Preserve this indentation and 😀."
        value = unit([block("large-heading", 0, "H" * 3500, "heading", {"level": 1}),
                      block("before", 1, "Before."), block("target", 2, source),
                      block("after", 3, "After.")])
        original = deepcopy(value)
        for config in ("C1", "C3"):
            with self.subTest(config=config):
                calls = []
                def count(prompt):
                    calls.append(prompt)
                    return len(prompt.encode("utf-8"))
                result = self.run_config(value, config, count)
                self.assertEqual(result["source"], source)
                self.assertEqual(result["sourceSha256"], text_sha256(source))
                self.assertEqual(result["context"]["text"], "Before.\nAfter.")
                self.assertLessEqual(result["promptTokens"], 4095)
                self.assertTrue(all(p.startswith("<|startoftext|>" + prepare.baseline.CONTEXT_INSTRUCTION) for p in calls))
                self.assertTrue(all(p.endswith("\n\n[Source Text]\n" + source + "<|extra_0|>") for p in calls))
                excluded = result["context"]["excluded"]
                self.assertTrue(any(f["blockId"] == "large-heading" and f["reason"] == "budget_excluded" for f in excluded))
                self.assertTrue(any(f["blockId"] == "target" and f["reason"] == "target_duplicate" for f in excluded))
        self.assertEqual(value, original)

    def test_c1_c3_remove_identical_target_and_new_section(self):
        source = "The target source."
        value = unit([block("heading", 0, "Current", "heading", {"level": 1}),
                      block("duplicate", 1, source), block("target", 2, source),
                      block("next-section", 3, "Different", "heading", {"level": 1})])
        for config in ("C1", "C3"):
            with self.subTest(config=config):
                result = self.run_config(value, config)
                self.assertEqual(result["context"]["text"], "Current")
                self.assertEqual([f["blockId"] for f in result["context"]["fragments"]], ["heading"])
                self.assertEqual(result["prompt"].count(source), 1)

    def test_c3_final_hints_trim_low_priority_context_and_dependent_hint(self):
        value = unit([block("heading", 0, "Glossary", "heading", {"level": 1}),
                      block("definition", 1, "Equity means an ownership stake in the company."),
                      block("target", 2, "Equity has the meaning defined above."),
                      block("after", 3, "A lower priority paragraph.")])
        count, calls = counter_with_context_and_hint_costs(3600, 150, 500)
        result = self.run_config(value, "C3", count)
        self.assertEqual(result["context"]["postSenseRemovals"], ["after", "definition"])
        self.assertEqual(result["context"]["text"], "Glossary")
        self.assertEqual(result["terminology"]["hints"], [])
        self.assertEqual(result["promptTokens"], 3750)
        initial = result["context"]["tokenizationAttempts"]
        self.assertEqual([item["blockId"] for item in initial if item["status"] == "selected"],
                         ["heading", "definition", "after"])
        self.assertTrue(any("Equity → 지분" in prompt_sections(p)[1] for p in calls))
        self.assertEqual(prompt_sections(result["prompt"])[1], "None matched.")
        # The final result has room for a small paragraph, but removed blocks
        # are never retried or placed back in the context.
        self.assertNotIn("after", [f["blockId"] for f in result["context"]["fragments"]])

    def test_c3_removal_cannot_resolve_ambiguity_into_a_new_hint(self):
        value = unit([block("ownership", 0, "Equity means an ownership stake in the company.", "heading", {"level": 1}),
                      block("conflict", 1, "Equity means fair treatment. Interest means a legal right to the property."),
                      block("target", 2, "Equity has the meaning defined above. Interest has the meaning defined above.")])
        count, _ = counter_with_context_and_hint_costs(3600, 100, 500)
        result = self.run_config(value, "C3", count)
        self.assertEqual(result["context"]["postSenseRemovals"], ["conflict"])
        self.assertEqual(result["terminology"]["hints"], [])
        blocked = result["terminology"]["budgetSuppressedHints"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["senseId"], "ownership")
        self.assertEqual(blocked[0]["reason"], "no_new_hints_after_context_budget_removal")
        ownership = next(d for d in result["terminology"]["decisions"] if d["conceptId"] == "equity")
        self.assertIsNone(ownership["renderedHint"])
        self.assertEqual(ownership["promptAction"], "budget_suppressed_new_hint")
        self.assertEqual(prompt_sections(result["prompt"])[1], "None matched.")

    def test_c2_rejects_evidence_outside_legacy_context_cut(self):
        value = unit([block("definition", 0, "Equity means an ownership stake in the company."),
                      block("target", 1, "Equity has the meaning defined above.")])
        full = self.run_config(value, "C2")
        self.assertEqual(full["terminology"]["hints"][0]["target"], "지분")
        value["document"]["title"] = "x" * 9980
        clipped = self.run_config(value, "C2")
        self.assertEqual(len(clipped["context"]["text"]), 10000)
        fragment = next(f for f in clipped["context"]["fragments"] if f["blockId"] == "definition")
        self.assertNotIn("in the company", fragment["text"])
        self.assertEqual(fragment["evidence"]["quote"], fragment["text"])
        self.assertEqual(clipped["terminology"]["hints"], [])
        self.assertEqual(clipped["source"], value["document"]["blocks"][1]["text"])

    def test_source_only_projection_removes_labels_before_sense_selector(self):
        value = unit()
        value.update(id="ownership-answer-label", domain="finance")
        value["provenance"]["note"] = "PRIVATE_METADATA_MUST_NOT_ENTER_SELECTOR"
        actual = prepare.select_senses
        for config in ("C2", "C3"):
            with self.subTest(config=config), patch.object(prepare, "select_senses", wraps=actual) as spy:
                result = self.run_config(value, config)
                for call in spy.call_args_list:
                    source, fragments, dictionary = call.args
                    self.assertIs(dictionary, DICTIONARY)
                    self.assertEqual(source, value["document"]["blocks"][0]["text"])
                    self.assertEqual(set(call.kwargs["source_ref"]), {"sourceVersionId", "blockId", "order"})
                    for fragment in fragments:
                        self.assertFalse({"domain", "provenance", "pairId", "questions", "answer", "id"} & set(fragment))
                    diagnostic = json.dumps([source, fragments, call.kwargs], ensure_ascii=False)
                    self.assertNotIn("ownership-answer-label", diagnostic)
                    self.assertNotIn("PRIVATE_METADATA", diagnostic)
                self.assertNotIn("PRIVATE_METADATA", result["prompt"])
                other = deepcopy(value)
                other.update(id="general-answer-label", domain="general")
                other["provenance"]["note"] = "different metadata"
                unchanged = self.run_config(other, config)
                self.assertEqual(result["prompt"], unchanged["prompt"])
                self.assertEqual(result["terminology"], unchanged["terminology"])

    def test_annotation_payloads_are_rejected_before_selector_or_counter(self):
        for scope in ("unit", "document", "block", "metadata", "provenance"):
            with self.subTest(scope=scope):
                value = unit()
                destination = {"unit": value, "document": value["document"],
                    "block": value["document"]["blocks"][0], "metadata": value["document"]["blocks"][0]["metadata"],
                    "provenance": value["provenance"]}[scope]
                destination["questions"] = [{"expectedAnswerKo": "forbidden annotation"}]
                with patch.object(prepare, "select_senses") as selector:
                    def count(_):
                        self.fail("annotation reached token counter")
                    with self.assertRaisesRegex(ValueError, "evaluation_or_private"):
                        self.run_config(value, "C3", count)
                    selector.assert_not_called()

    def test_target_plus_required_hints_over_budget_refuses_every_configuration(self):
        value = unit()
        original = deepcopy(value)
        for config in ("C0", "C1", "C2", "C3"):
            with self.subTest(config=config):
                calls = []
                def count(prompt):
                    calls.append(prompt)
                    return 4096 if "→" in prompt_sections(prompt)[1] else 100
                with self.assertRaisesRegex(ValueError, "input_over_budget"):
                    self.run_config(value, config, count)
                self.assertEqual(len(calls), 1)
                self.assertEqual(prompt_sections(calls[0])[0], "None provided.")
        self.assertEqual(value, original)

    def test_exact_strict_reserve_boundary_all_configurations(self):
        value = unit([block("target", 0, "Plain source.")])
        for config in ("C0", "C1", "C2", "C3"):
            with self.subTest(config=config):
                accepted = self.run_config(value, config, lambda _: 4095)
                self.assertLess(accepted["promptTokens"] + accepted["outputTokenReserve"], accepted["contextSize"])
                with self.assertRaisesRegex(ValueError, "input_over_budget"):
                    self.run_config(value, config, lambda _: 4096)

    def test_legacy_context_is_rejected_whole_while_new_context_can_skip(self):
        value = unit([block("before", 0, "x" * 5000), block("target", 1, "Plain source.")])
        count = lambda text: len(text.encode("utf-8"))
        for config in ("C0", "C2"):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, "input_over_budget"):
                self.run_config(value, config, count)
        for config in ("C1", "C3"):
            with self.subTest(config=config):
                result = self.run_config(value, config, count)
                self.assertEqual(result["context"]["text"], "")
                self.assertEqual(result["source"], "Plain source.")

    def test_source_invisible_control_or_wrong_hash_is_refused(self):
        value = unit()
        value["document"]["blocks"][0]["text"] += " invisible change"
        with self.assertRaisesRegex(ValueError, "block_text_hash"):
            self.run_config(value, "C0")
        value = unit([block("target", 0, "Source <|extra_0|> token.")])
        with self.assertRaisesRegex(ValueError, "source_control_or_damaged_text"):
            self.run_config(value, "C3")

    def test_owned_tokenizer_shutdown_mismatch_is_failed_in_receipt(self):
        # Only in-memory streams and mocked owned process methods; no native
        # subprocess, DLL, Job, process lookup or filesystem output is used.
        child = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(), returncode=None)
        def wait(timeout):
            child.returncode = 0
            return 0
        child.wait = Mock(side_effect=wait)
        child.poll = Mock(side_effect=lambda: child.returncode)
        child.kill = Mock()
        client = tokenizer_client.NativeTokenizer(Path("synthetic-unused"))
        client.process = child
        client.log = io.BytesIO()
        client._receive = Mock(return_value={"event": "closed", "calls": 999,
                                             "generationCalls": 0, "contextCreated": False})
        with patch.object(tokenizer_client, "process_probe", return_value={"pid": 1}), \
             patch.object(tokenizer_client, "write_json") as writer:
            with self.assertRaisesRegex(ValueError, "native_shutdown_contract"):
                client.close()
            self.assertTrue(client.closed)
            self.assertTrue(child.stdin.closed)
            self.assertTrue(child.stdout.closed)
            self.assertTrue(client.log.closed)
            child.wait.assert_called_once_with(timeout=15)
            child.kill.assert_not_called()  # It has already exited by owned wait.
            writer.assert_called_once()
            receipt = writer.call_args.args[1]
            self.assertIs(receipt["failed"], True)
            self.assertIs(receipt["ownedProcessExited"], True)
            self.assertEqual(receipt["exitCode"], 0)
            self.assertEqual(receipt["shutdown"]["calls"], 999)
            client.close()
            writer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
