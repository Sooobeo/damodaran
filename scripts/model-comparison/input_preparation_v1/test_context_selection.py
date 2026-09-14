"""Synthetic selector tests. Fake counters are not native tokenizer evidence."""
from copy import deepcopy
import json
import subprocess
import unittest

from context_selection import (c0_context, context_candidates, sanitize_unit,
                               select_context, structure_sha256, table_header_evidence,
                               text_sha256, validate_units)


def block(identifier, order, text, kind="paragraph", metadata=None):
    return {"id": identifier, "order": order, "kind": kind, "text": text,
            "textSha256": text_sha256(text), "metadata": metadata or {"schemaVersion": 1, "links": []}}


def unit(blocks=None, target="target"):
    return {"id": "fixture-1", "domain": "general",
            "provenance": {"kind": "assistant-authored-structured-fixture", "humanReviewed": False},
            "document": {"resourceId": "resource-1", "sourceVersionId": "version-1", "title": "Document",
                         "titleKo": "", "format": "html", "extractorVersion": "synthetic-v1",
                         "blocks": blocks or [block("before", 0, "Before."), block("target", 1, "Target."), block("after", 2, "After.")]},
            "targetBlockId": target}


def _count_fake_utf8(prompt):
    return len(prompt.encode("utf-8"))


class SourceValidationTests(unittest.TestCase):
    def test_source_projection_drops_labels_and_provenance_and_does_not_mutate(self):
        value = unit()
        original = deepcopy(value)
        clean = sanitize_unit(value)
        self.assertEqual(set(clean), {"document", "targetBlockId"})
        self.assertEqual(value, original)
        clean["document"]["blocks"][0]["text"] = "changed"
        self.assertEqual(value, original)

    def test_rejects_leakage_at_each_supported_level(self):
        for scope in ("unit", "document", "metadata", "provenance"):
            with self.subTest(scope=scope):
                value = unit()
                destination = {"unit": value, "document": value["document"],
                               "metadata": value["document"]["blocks"][0]["metadata"],
                               "provenance": value["provenance"]}[scope]
                destination["questions"] = [{"expectedAnswerKo": "secret"}]
                with self.assertRaisesRegex(ValueError, "evaluation_or_private"):
                    c0_context(value)
        value = unit(); value["document"]["blocks"][0]["metadata"]["hints"] = "secret"
        with self.assertRaisesRegex(ValueError, "unknown_metadata_field"):
            context_candidates(value)

    def test_rejects_text_and_structure_hash_changes(self):
        value = unit(); value["document"]["blocks"][0]["text"] += " "
        with self.assertRaisesRegex(ValueError, "block_text_hash"):
            validate_units([value])
        value = unit(); source = value["document"]["blocks"][0]
        source["structureSha256"] = structure_sha256(source["metadata"])
        source["metadata"]["level"] = 2
        with self.assertRaisesRegex(ValueError, "block_structure_hash"):
            validate_units([value])

    def test_rejects_controls_damaged_unicode_and_model_tokens(self):
        for text in ("source\x00bad", "source\ud800", "source\ufffd", "<|extra_0|>", "text\u202e"):
            with self.subTest(text=repr(text)):
                value = unit()
                value["document"]["blocks"][0]["text"] = text
                with self.assertRaisesRegex(ValueError, "source_control_or_damaged_text"):
                    validate_units([value])

    def test_rejects_local_duplicate_ids_orders_and_missing_target(self):
        for mutation, reason in (("id", "duplicate_block_or_order"), ("order", "duplicate_block_or_order"), ("target", "target_membership")):
            value = unit()
            if mutation == "target":
                value["targetBlockId"] = "missing"
            else:
                value["document"]["blocks"][1][mutation] = value["document"]["blocks"][0][mutation]
            with self.assertRaisesRegex(ValueError, reason):
                validate_units([value])

    def test_false_empty_metadata_is_malformed(self):
        for invalid in ([], "", False):
            value = unit(); value["document"]["blocks"][0]["metadata"] = invalid
            with self.assertRaisesRegex(ValueError, "invalid_metadata"):
                validate_units([value])

    def test_global_ownership_snapshot_and_order_validation(self):
        value, other = unit(), unit()
        other["id"] = "fixture-2"
        validate_units([value, other])  # identical shared blocks are intentional
        for field, changed, reason in (("resourceId", "other", "version_resource"),
                                       ("sourceVersionId", "other", "block_version"),
                                       ("title", "changed title", "inconsistent_version")):
            with self.subTest(field=field):
                broken = deepcopy(other); broken["document"][field] = changed
                with self.assertRaisesRegex(ValueError, reason):
                    validate_units([value, broken])
        other["document"]["blocks"][0]["id"] = "new-block"
        with self.assertRaisesRegex(ValueError, "global_version_order_collision"):
            validate_units([value, other])

    def test_same_version_accepts_disjoint_subsets_but_checks_shared_metadata(self):
        value = unit()
        other = unit([block("other", 10, "Another target")], "other")
        other["id"] = "fixture-2"
        validate_units([value, other])
        other = unit(); other["id"] = "fixture-2"
        other["document"]["blocks"][0]["metadata"]["level"] = 1
        with self.assertRaisesRegex(ValueError, "inconsistent_shared_block"):
            validate_units([value, other])

    def test_explicit_foreign_membership_and_invalid_parent_are_rejected(self):
        for field in ("resourceId", "sourceVersionId"):
            value = unit(); value["document"]["blocks"][0][field] = "foreign"
            with self.assertRaisesRegex(ValueError, "block_membership"):
                validate_units([value])
        for parent in ("missing", "target", "after"):
            value = unit(); value["document"]["blocks"][1]["metadata"]["parentBlockId"] = parent
            with self.assertRaisesRegex(ValueError, "invalid_parent"):
                validate_units([value])


class C0Tests(unittest.TestCase):
    def test_exact_app_order_range_and_duplicate_not_array_neighbors(self):
        value = unit([block("distant", 0, "Distant."), block("target", 4, "Target.\r\n  formula / 2"), block("after", 5, "After.")])
        result = c0_context(value)
        self.assertEqual(result["text"], "Document\n\nTarget.\r\n  formula / 2\nAfter.")
        self.assertIn("existing_target_duplicate", [f["reason"] for f in result["fragments"]])
        self.assertNotIn("Distant", result["text"])

    def test_js_utf16_slice_matches_node_at_surrogate_boundary(self):
        value = unit([block("target", 0, "T")])
        value["document"]["title"] = "a" * 9999 + "😀"
        result = c0_context(value)
        self.assertEqual(result["text"][-1], "\ud83d")
        self.assertEqual(len(result["text"].encode("utf-16-le", errors="surrogatepass")) // 2, 10000)
        script = "const v=JSON.parse(require('fs').readFileSync(0,'utf8'));process.stdout.write(JSON.stringify((v+'\\n\\nT').slice(0,10000)));"
        node = subprocess.run(["node", "-e", script], input=json.dumps(value["document"]["title"]),
                              text=True, capture_output=True, check=True)
        self.assertEqual(json.loads(node.stdout), result["text"])
        self.assertEqual(result["fragments"][0]["substring"]["endUtf16"], 10000)
        self.assertEqual(result["excluded"][-1]["blockId"], "target")

    def test_actual_retained_fragment_not_full_truncated_quote(self):
        value = unit([block("target", 0, "ABCDE")]); value["document"]["title"] = "x" * 9996
        result = c0_context(value)
        target = next(f for f in result["fragments"] if f["blockId"] == "target")
        self.assertEqual(target["text"], "AB")
        self.assertEqual(target["evidence"]["quote"], "AB")
        self.assertEqual(target["textSha256"], text_sha256("ABCDE"))


class StructureSelectionTests(unittest.TestCase):
    def test_stack_replacement_priority_and_next_section_exclusion(self):
        value = unit([block("root", 0, "Root", "heading", {"level": 1}),
                      block("old", 1, "Old", "heading", {"level": 2}),
                      block("new", 2, "New", "heading", {"level": 2}),
                      block("previous", 3, "Previous."), block("target", 4, "Target."),
                      block("next-section", 5, "Following", "heading", {"level": 2})])
        result = context_candidates(value)
        self.assertEqual([f["blockId"] for f in result["fragments"]], ["root", "new", "previous"])
        self.assertTrue(any(f["blockId"] == "next-section" and f["reason"] == "outside_section" for f in result["excluded"]))
        self.assertTrue(all(f["evidence"]["quote"] == f["text"] for f in result["fragments"]))

    def test_no_title_promotion_and_exact_target_duplicate_removed(self):
        value = unit([block("looks-heading", 0, "IMPORTANT SECTION"), block("previous", 2, "Target."),
                      block("target", 3, "Target."), block("after", 4, "After")])
        result = context_candidates(value)
        self.assertEqual([f["blockId"] for f in result["fragments"]], ["after"])
        self.assertEqual({f["blockId"] for f in result["excluded"] if f["reason"] == "target_duplicate"}, {"target", "previous"})

    def test_heading_target_does_not_take_body_or_peer_from_previous_section(self):
        value = unit([block("ancestor", 0, "Root", "heading", {"level": 1}),
                      block("peer", 1, "Earlier", "heading", {"level": 2}),
                      block("previous", 2, "Earlier section body."),
                      block("target", 3, "New section", "heading", {"level": 2}),
                      block("after", 4, "New section body.")])
        result = context_candidates(value)
        self.assertEqual([f["blockId"] for f in result["fragments"]], ["ancestor", "after"])
        self.assertTrue(any(f["blockId"] == "previous" and f["reason"] == "outside_section" for f in result["excluded"]))

    def test_explicit_parent_is_selected_without_inferred_relation(self):
        value = unit([block("parent", 0, "The list includes:"), block("previous", 3, "Before."),
                      block("target", 4, "First condition.", "list_item", {"parentBlockId": "parent"}),
                      block("after", 5, "After.")])
        result = context_candidates(value)
        self.assertEqual([f["blockId"] for f in result["fragments"]], ["parent", "previous", "after"])
        self.assertEqual(result["fragments"][0]["evidence"]["relationshipField"], "parentBlockId")
        del value["document"]["blocks"][2]["metadata"]["parentBlockId"]
        result = context_candidates(value)
        self.assertNotIn("parent", [f["blockId"] for f in result["fragments"]])
        self.assertTrue(any(f["evidence"].get("detail") == "parent_list_relation_not_stored" for f in result["excluded"]))

    def test_pdf_boxes_do_not_become_headings_or_list_parents(self):
        value = unit()
        value["document"]["format"] = "pdf"
        value["document"]["blocks"][1]["metadata"] = {"pdfParagraphVersion": 2, "lineCount": 1, "lineBoxes": [[0, 0, 30, 10]]}
        result = context_candidates(value)
        self.assertEqual([f["reason"] for f in result["fragments"]], ["previous_block", "next_block"])
        self.assertTrue(any(f["evidence"].get("detail") == "pdf_parent_heading_list_semantics_not_stored" for f in result["excluded"]))

    def test_table_requires_explicit_headers_and_unspanned_simple_grid(self):
        def cell(text, header):
            return {"text": text, "header": header, "rowSpan": 1, "colSpan": 1}
        table = block("table", 0, "Name | Value\nA | 2", "table", {"rows": [
            {"cells": [cell("Name", True), cell("Value", True)]},
            {"cells": [cell("A", False), cell("2", False)]}]})
        value = unit([table, block("target", 1, "Target.")])
        result = context_candidates(value)
        evidence = result["fragments"][0]["evidence"]["tableCoordinates"]
        self.assertEqual(evidence["headers"][1], {"rowIndex": 0, "cellIndex": 1, "quote": "Value", "header": True})
        self.assertEqual(evidence["semanticRowColumnMapping"], "unsupported")
        for c in table["metadata"]["rows"][0]["cells"]:
            c["header"] = False
        self.assertEqual(table_header_evidence(table)["reason"], "no_explicit_header")
        self.assertFalse(context_candidates(value)["fragments"])
        table["metadata"]["rows"][0]["cells"][0]["header"] = True
        table["metadata"]["rows"][0]["cells"][0]["colSpan"] = 2
        self.assertEqual(table_header_evidence(table)["reason"], "non_simple_grid")


class BudgetTests(unittest.TestCase):
    def test_counts_entire_prompt_and_skips_oversize_then_tries_next(self):
        value = unit([block("heading", 0, "H" * 30, "heading", {"level": 1}),
                      block("previous", 1, "Before"), block("target", 2, "Target"), block("after", 3, "After")])
        calls = []
        def render(context):
            return "TEMPLATE:Target;ALL HINTS;" + context + ":END"
        def count(prompt):
            calls.append(prompt)
            return _count_fake_utf8(prompt)
        result = select_context(value, count, render, 42)
        self.assertEqual(result["text"], "Before\nAfter")
        self.assertTrue(all(p.startswith("TEMPLATE:Target;ALL HINTS;") and p.endswith(":END") for p in calls))
        self.assertTrue(any(f["blockId"] == "heading" and f["reason"] == "budget_excluded" for f in result["excluded"]))
        self.assertEqual(result["promptTokens"], count(render(result["text"])))
        self.assertEqual(value["document"]["blocks"][2]["text"], "Target")

    def test_4095_allowed_4096_rejected_without_truncation(self):
        value = unit([block("target", 0, "unchanged")])
        self.assertEqual(select_context(value, lambda _: 4095, lambda ctx: "full target and hints")["promptTokens"], 4095)
        with self.assertRaisesRegex(ValueError, "input_over_budget"):
            select_context(value, lambda _: 4096, lambda ctx: "full target and hints")
        self.assertEqual(value["document"]["blocks"][0]["text"], "unchanged")

    def test_nonadditive_tokenizer_is_called_on_reassembled_context(self):
        value = unit()
        def count(prompt):
            return 1 if prompt == "[Before.\nAfter.]" else len(prompt)
        result = select_context(value, count, lambda ctx: "[" + ctx + "]", 20)
        self.assertEqual(result["text"], "Before.\nAfter.")
        self.assertEqual(result["promptTokens"], 1)

    def test_invalid_counter_and_budget_refused(self):
        for bad in (True, -1, 1.2, "1"):
            with self.assertRaisesRegex(ValueError, "invalid_prompt_token_count"):
                select_context(unit(), lambda _, v=bad: v, lambda ctx: ctx)
        with self.assertRaisesRegex(ValueError, "invalid_prompt_budget"):
            select_context(unit(), len, lambda ctx: ctx, 4096)


if __name__ == "__main__":
    unittest.main()
