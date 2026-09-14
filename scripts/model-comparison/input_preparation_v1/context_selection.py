"""S2 source-only validation and deterministic context preparation.

No files, app state, evaluation annotations, model, or network are read here.
The caller supplies the frozen source snapshot and the *full rendered prompt*
token counter. Unit tests deliberately use fake counters, not token estimates.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import unicodedata
from typing import Callable

C0_POLICY = "existing-title-neighbors-v1"
CONTEXT_POLICY = "source-structure-context-v1"
MAX_PROMPT_TOKENS = 4095
UNIT_KEYS = {"id", "domain", "provenance", "document", "targetBlockId"}
DOCUMENT_KEYS = {"resourceId", "sourceVersionId", "title", "titleKo", "format",
                 "extractorVersion", "extractionConfigHash", "structureProvenance", "blocks"}
BLOCK_KEYS = {"id", "order", "kind", "text", "textSha256", "sourceHash", "metadata",
              "pageIndex", "bbox", "structureSha256", "sourceVersionId", "resourceId"}
METADATA_KEYS = {"schemaVersion", "level", "links", "rows", "pdfParagraphVersion",
                 "lineCount", "lineBoxes", "parentBlockId", "listIntroductionBlockId"}
PROVENANCE_KEYS = {"kind", "humanReviewed", "createdAt", "createdOn", "purpose",
                   "notInvestmentAdvice", "synthetic", "split", "independentEvaluation",
                   "externalSource", "note", "originalBytesVerified", "originalFileSha256",
                   "originalPath", "selection", "sourceUrl"}
FORBIDDEN_KEYS = {"evaluations", "evaluationUnits", "pairId", "propositions", "terms",
                  "questions", "expectedAnswerKo", "expectedMeaningKo", "allowedKorean",
                  "sourceEvidence", "termOccurrences", "targetTranslation", "answer",
                  "translation", "review", "notes", "apiKey", "OPENAI_API_KEY"}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def structure_sha256(metadata: dict | None) -> str:
    return text_sha256(json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False))


def utf16_slice(text: str, start: int = 0, end: int | None = None) -> str:
    """Positive-offset JS String.slice, including a boundary lone surrogate."""
    raw = text.encode("utf-16-le", errors="surrogatepass")
    return raw[2 * start:None if end is None else 2 * end].decode("utf-16-le", errors="surrogatepass")


def _utf16_len(text):
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _string(value, reason, empty=False):
    _require(isinstance(value, str) and (empty or bool(value)), reason)
    _require("\ufffd" not in value and not re.search(r"<\|[^\n>]*\|>", value), "source_control_or_damaged_text")
    _require(all(unicodedata.category(c) != "Cs" and
                 (unicodedata.category(c) != "Cc" or c in "\n\r\t")
                 and c not in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
                 for c in value), "source_control_or_damaged_text")


def _keys(value, allowed, label):
    _require(isinstance(value, dict), "invalid_" + label)
    _require(set(value).issubset(allowed), "unknown_" + label + "_field")


def _no_annotations(value):
    if isinstance(value, dict):
        _require(not FORBIDDEN_KEYS.intersection(value), "evaluation_or_private_field_in_source")
        for child in value.values():
            _no_annotations(child)
    elif isinstance(value, list):
        for child in value:
            _no_annotations(child)


def _coordinate_box(value):
    return isinstance(value, list) and len(value) == 4 and all(
        isinstance(n, (int, float)) and not isinstance(n, bool) and math.isfinite(n) for n in value)


def _metadata(block):
    _require(block.get("metadata") is None or isinstance(block["metadata"], dict), "invalid_metadata")
    metadata = block.get("metadata") or {}
    _keys(metadata, METADATA_KEYS, "metadata")
    if "level" in metadata:
        _require(type(metadata["level"]) is int and 1 <= metadata["level"] <= 6, "invalid_heading_level")
    if "links" in metadata:
        _require(isinstance(metadata["links"], list), "invalid_links")
        for link in metadata["links"]:
            _keys(link, {"url", "text"}, "link")
            _string(link.get("url"), "invalid_link_url")
            _string(link.get("text"), "invalid_link_text", empty=True)
    if "schemaVersion" in metadata:
        _require(type(metadata["schemaVersion"]) is int and metadata["schemaVersion"] >= 1, "invalid_structure_schema")
    if "lineBoxes" in metadata:
        _require(isinstance(metadata["lineBoxes"], list) and all(_coordinate_box(x) for x in metadata["lineBoxes"]), "invalid_line_boxes")
        _require(type(metadata.get("lineCount")) is int and metadata["lineCount"] == len(metadata["lineBoxes"]), "invalid_line_count")
    if "pdfParagraphVersion" in metadata:
        _require(type(metadata["pdfParagraphVersion"]) is int and metadata["pdfParagraphVersion"] in (1, 2), "invalid_pdf_paragraph_version")
    for field in ("parentBlockId", "listIntroductionBlockId"):
        if field in metadata:
            _string(metadata[field], "invalid_parent_reference")
    if "rows" in metadata:
        _require(isinstance(metadata["rows"], list), "invalid_table_rows")
        for row in metadata["rows"]:
            _keys(row, {"cells"}, "table_row")
            _require(isinstance(row.get("cells"), list), "invalid_table_cells")
            for cell in row["cells"]:
                _keys(cell, {"text", "header", "rowSpan", "colSpan"}, "table_cell")
                _string(cell.get("text"), "invalid_cell_text", empty=True)
                _require(type(cell.get("header")) is bool, "invalid_header_flag")
                _require(all(type(cell.get(k)) is int and cell[k] >= 1 for k in ("rowSpan", "colSpan")), "invalid_cell_span")
    if "structureSha256" in block:
        _require(block["structureSha256"] == structure_sha256(metadata), "block_structure_hash_mismatch")
    return metadata


def validate_units(units: list[dict]) -> None:
    """Validate all snapshots together; consistent shared source blocks are legal.

    S1 keeps subsets of a document for each target. A version may recur with a
    different subset, but its metadata, block ownership, and order cannot change.
    Raw S1 provenance is accepted only as inert audit metadata and never selected.
    """
    _require(isinstance(units, list) and bool(units), "invalid_units")
    unit_ids, version_owner, version_snapshot = set(), {}, {}
    block_owner, block_snapshot, version_orders = {}, {}, {}
    for unit in units:
        _no_annotations(unit)
        _keys(unit, UNIT_KEYS, "unit")
        if "id" in unit:
            _string(unit["id"], "invalid_unit_id")
            _require(unit["id"] not in unit_ids, "duplicate_unit_id")
            unit_ids.add(unit["id"])
        if "domain" in unit:
            _require(unit["domain"] in ("finance", "general"), "invalid_inert_domain")
        if "provenance" in unit:
            _keys(unit["provenance"], PROVENANCE_KEYS, "provenance")
        document = unit.get("document")
        _keys(document, DOCUMENT_KEYS, "document")
        for name in ("resourceId", "sourceVersionId", "title", "format", "extractorVersion"):
            _string(document.get(name), "invalid_document_" + name, empty=name == "title")
        _string(document.get("titleKo", ""), "invalid_document_titleKo", empty=True)
        _require(document["format"] in ("html", "pdf"), "unsupported_document_format")
        resource, version = document["resourceId"], document["sourceVersionId"]
        _require(version not in version_owner or version_owner[version] == resource, "version_resource_mismatch")
        version_owner[version] = resource
        # structureProvenance is inert prose, not structural evidence.
        identity = {k: document.get(k, "" if k == "titleKo" else None) for k in DOCUMENT_KEYS - {"blocks", "structureProvenance"}}
        _require(version not in version_snapshot or version_snapshot[version] == identity, "inconsistent_version_snapshot")
        version_snapshot[version] = identity
        blocks = document.get("blocks")
        _require(isinstance(blocks, list) and bool(blocks), "invalid_blocks")
        local_ids, local_orders = set(), []
        for block in blocks:
            _keys(block, BLOCK_KEYS, "block")
            _string(block.get("id"), "invalid_block_id")
            _string(block.get("text"), "invalid_source_text")
            _require(block.get("kind") in ("heading", "paragraph", "list_item", "table", "formula", "image"), "invalid_block_kind")
            order = block.get("order")
            _require(type(order) is int and order >= 0, "invalid_block_order")
            _require(block["id"] not in local_ids and order not in local_orders, "duplicate_block_or_order")
            local_ids.add(block["id"]); local_orders.append(order)
            _require(block.get("sourceVersionId", version) == version and block.get("resourceId", resource) == resource, "block_membership_mismatch")
            _require(block["id"] not in block_owner or block_owner[block["id"]] == version, "block_version_mismatch")
            block_owner[block["id"]] = version
            _require(block.get("textSha256") == text_sha256(block["text"]), "block_text_hash_mismatch")
            if "sourceHash" in block:
                _require(isinstance(block["sourceHash"], str) and re.fullmatch("[0-9a-f]{64}", block["sourceHash"]), "invalid_source_hash")
            if block.get("pageIndex") is not None:
                _require(type(block["pageIndex"]) is int and block["pageIndex"] >= 0, "invalid_page_index")
            if "bbox" in block:
                _require(_coordinate_box(block["bbox"]), "invalid_bbox")
            metadata = _metadata(block)
            snapshot = (resource, order, block["kind"], block["textSha256"], structure_sha256(metadata),
                        block.get("pageIndex"), block.get("bbox"), block.get("sourceHash"))
            key = (version, block["id"])
            _require(key not in block_snapshot or block_snapshot[key] == snapshot, "inconsistent_shared_block")
            block_snapshot[key] = snapshot
            key = (version, order)
            _require(key not in version_orders or version_orders[key] == block["id"], "global_version_order_collision")
            version_orders[key] = block["id"]
        _require(local_orders == sorted(local_orders), "blocks_not_sorted")
        _require(unit.get("targetBlockId") in local_ids, "target_membership_mismatch")
        by_id = {b["id"]: b for b in blocks}
        for block in blocks:
            for name in ("parentBlockId", "listIntroductionBlockId"):
                parent_id = (block.get("metadata") or {}).get(name)
                if parent_id is not None:
                    _require(parent_id in by_id and by_id[parent_id]["order"] < block["order"], "invalid_parent_membership_or_order")


def sanitize_unit(unit: dict) -> dict:
    """Drop unit IDs, domain labels and provenance before any input selection."""
    validate_units([unit])
    document = deepcopy(unit["document"])
    document.pop("structureProvenance", None)
    return {"document": document, "targetBlockId": unit["targetBlockId"]}


def _parts(unit):
    clean = sanitize_unit(unit)
    document = clean["document"]
    return document, next(b for b in document["blocks"] if b["id"] == clean["targetBlockId"])


def _fragment(document, block, reason, evidence=None):
    return {"sourceVersionId": document["sourceVersionId"], "blockId": block["id"],
            "order": block["order"], "kind": block["kind"], "text": block["text"],
            "textSha256": block["textSha256"], "structureSha256": structure_sha256(block.get("metadata")),
            "reason": reason, "evidence": {"quote": block["text"], **(evidence or {})}}


def c0_context(unit: dict) -> dict:
    document, target = _parts(unit)
    neighbors = [b for b in document["blocks"] if target["order"] - 1 <= b["order"] <= target["order"] + 1]
    pieces = []
    for field, kind in (("title", "resource_title_en"), ("titleKo", "resource_title_ko")):
        value = document.get(field, "")
        pieces.append({"sourceVersionId": document["sourceVersionId"], "blockId": None, "order": None,
                       "kind": kind, "text": value, "textSha256": text_sha256(value),
                       "structureSha256": structure_sha256({}), "reason": "existing_resource_title",
                       "evidence": {"field": field, "quote": value}})
    pieces.extend(_fragment(document, b, "existing_target_duplicate" if b["id"] == target["id"] else "existing_order_neighbor") for b in neighbors)
    full = "\n".join(p["text"] for p in pieces)
    text = utf16_slice(full, 0, 10000)
    fragments, excluded, cursor = [], [], 0
    for piece in pieces:
        size = _utf16_len(piece["text"])
        available = max(0, min(size, 10000 - cursor))
        if size and available:
            fragment = deepcopy(piece)
            fragment["text"] = utf16_slice(piece["text"], 0, available)
            fragment["evidence"]["quote"] = fragment["text"]
            fragment["contextStartUtf16"] = cursor
            fragment["contextEndUtf16"] = cursor + available
            if available < size:
                fragment["substring"] = {"startUtf16": 0, "endUtf16": available, "text": fragment["text"]}
            fragments.append(fragment)
        if available < size:
            excluded.append({**piece, "reason": "existing_utf16_limit", "excludedStartUtf16": available,
                             "excludedEndUtf16": size})
        cursor += size + 1
    return {"text": text, "fragments": fragments, "excluded": excluded, "policyVersion": C0_POLICY}


def table_header_evidence(block: dict) -> dict:
    """Only explicit th flags in a rectangular unspanned grid; no semantics."""
    rows = (block.get("metadata") or {}).get("rows", [])
    if block.get("kind") != "table" or not rows:
        return {"status": "unsupported_structure", "reason": "no_explicit_grid"}
    cells = [row.get("cells", []) for row in rows]
    if not cells[0] or any(len(row) != len(cells[0]) for row in cells) or any(
            cell.get("rowSpan") != 1 or cell.get("colSpan") != 1 for row in cells for cell in row):
        return {"status": "unsupported_structure", "reason": "non_simple_grid"}
    headers = [{"rowIndex": r, "cellIndex": c, "quote": cell["text"], "header": True}
               for r, row in enumerate(cells) for c, cell in enumerate(row) if cell.get("header") is True]
    if not headers:
        return {"status": "unsupported_structure", "reason": "no_explicit_header"}
    return {"status": "supported_coordinates", "headers": headers,
            "semanticRowColumnMapping": "unsupported", "unitInference": "unsupported"}


def context_candidates(unit: dict) -> dict:
    document, target = _parts(unit)
    blocks, fragments, excluded = document["blocks"], [], []
    by_id = {b["id"]: b for b in blocks}
    seen = set()
    stack = []
    for block in blocks:
        if block["order"] >= target["order"]:
            break
        level = (block.get("metadata") or {}).get("level")
        if block["kind"] == "heading" and level is not None and document["format"] == "html":
            while stack and stack[-1]["metadata"]["level"] >= level:
                stack.pop()
            stack.append(block)
    # A heading target itself starts a new section, so a former peer/subheading
    # cannot be retained as an ancestor or as preceding body context.
    target_level = (target.get("metadata") or {}).get("level")
    if target["kind"] == "heading" and target_level is not None:
        while stack and stack[-1]["metadata"]["level"] >= target_level:
            stack.pop()

    def exclude(block, reason, evidence=None):
        excluded.append(_fragment(document, block, reason, evidence))

    def add(block, reason, evidence=None):
        if block["id"] == target["id"] or block["text"] == target["text"]:
            if block["id"] not in seen:
                exclude(block, "target_duplicate", evidence)
                seen.add(block["id"])
            return
        if block["id"] in seen:
            return
        seen.add(block["id"])
        fragments.append(_fragment(document, block, reason, evidence))

    exclude(target, "target_duplicate")
    seen.add(target["id"])
    for heading in stack:
        add(heading, "preceding_heading_stack", {"headingLevel": heading["metadata"]["level"],
            "membershipBasis": "preceding_stored_heading_order_and_level_not_stored_section_id"})
    parents = []
    for field in ("parentBlockId", "listIntroductionBlockId"):
        if field in (target.get("metadata") or {}):
            parents.append((by_id[target["metadata"][field]], field))
    for parent, field in sorted(parents, key=lambda pair: pair[0]["order"]):
        add(parent, "explicit_parent" if field == "parentBlockId" else "explicit_list_introduction",
            {"relationshipField": field, "childBlockId": target["id"], "childQuote": target["text"]})
    for order, reason in ((target["order"] - 1, "previous_block"), (target["order"] + 1, "next_block")):
        neighbor = next((b for b in blocks if b["order"] == order), None)
        if neighbor is None or neighbor["id"] in seen:
            continue
        if target["kind"] == "heading" and order < target["order"]:
            exclude(neighbor, "outside_section", {"detail": "target_heading_boundary"})
        elif neighbor["kind"] == "heading":
            exclude(neighbor, "outside_section" if order > target["order"] else "unsupported_structure",
                    {"detail": "next_heading_boundary" if order > target["order"] else "heading_without_supported_level"})
        elif neighbor["kind"] in ("paragraph", "list_item", "formula"):
            add(neighbor, reason, {"targetOrder": target["order"], "relationship": "adjacent_order_only"})
        elif neighbor["kind"] == "table":
            table = table_header_evidence(neighbor)
            if table["status"] == "supported_coordinates":
                add(neighbor, reason, {"tableCoordinates": table})
            else:
                exclude(neighbor, "unsupported_structure", table)
        else:
            exclude(neighbor, "unsupported_structure")
    for block in [target] + [by_id[f["blockId"]] for f in fragments]:
        metadata = block.get("metadata") or {}
        if document["format"] == "pdf":
            exclude(block, "unsupported_structure", {"detail": "pdf_parent_heading_list_semantics_not_stored"})
        elif block["kind"] == "list_item" and not any(k in metadata for k in ("parentBlockId", "listIntroductionBlockId")):
            exclude(block, "unsupported_structure", {"detail": "parent_list_relation_not_stored"})
    return {"fragments": fragments, "excluded": excluded, "policyVersion": CONTEXT_POLICY}


def select_context(unit: dict, token_count: Callable[[str], int], render: Callable[[str], str],
                   max_prompt_tokens: int = MAX_PROMPT_TOKENS) -> dict:
    """Reserve target/instructions/all hints first, then try whole blocks in rank.

    render(context) must return the entire final prompt including target, hints,
    and template. token_count must count that exact prompt with the locked native
    tokenizer. The function does not silently truncate either source or context.
    """
    _require(type(max_prompt_tokens) is int and 0 < max_prompt_tokens <= MAX_PROMPT_TOKENS, "invalid_prompt_budget")
    candidates = context_candidates(unit)
    fragments, excluded, attempts = [], deepcopy(candidates["excluded"]), []

    def measure(context):
        prompt = render(context)
        _require(isinstance(prompt, str), "invalid_rendered_prompt")
        count = token_count(prompt)
        _require(type(count) is int and count >= 0, "invalid_prompt_token_count")
        return count

    count = measure("")
    _require(count <= max_prompt_tokens, "input_over_budget")
    attempts.append({"blockId": None, "promptTokens": count, "status": "target_and_hints_reserved"})
    for fragment in candidates["fragments"]:
        trial = "\n".join(f["text"] for f in fragments + [fragment])
        trial_count = measure(trial)
        selected = trial_count <= max_prompt_tokens
        attempts.append({"blockId": fragment["blockId"], "promptTokens": trial_count,
                         "status": "selected" if selected else "budget_excluded"})
        if selected:
            fragments.append({**fragment, "status": "selected"})
            count = trial_count
        else:
            excluded.append({**fragment, "selectionReason": fragment["reason"], "reason": "budget_excluded",
                             "trialPromptTokens": trial_count})
    return {"text": "\n".join(f["text"] for f in fragments), "fragments": fragments, "excluded": excluded,
            "policyVersion": CONTEXT_POLICY, "promptTokens": count, "maxPromptTokens": max_prompt_tokens,
            "tokenizationAttempts": attempts}
