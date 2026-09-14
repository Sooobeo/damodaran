"""Conservative, occurrence-level S2 dictionary selection without model or I/O.

Rules are editorial patterns for the frozen eight concepts, not a general parser.
Only source text, surviving source context, and the focused dictionary are inputs.
Offsets are Python Unicode code points; UTF-16 offsets are recorded separately.
The integration layer must validate source membership and supply only context that
survives rendering. This module also rejects cross-version context when IDs exist.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass


SELECTOR_VERSION = "source-sense-patterns-v1-20260913"
_FINANCIAL = "hint_financial"
_SUPPRESS = "suppress_nonfinancial_hint"
_CONCEPTS = {"equity", "interest", "security", "principal", "overhead", "margin",
             "percentage-change", "increase-relation"}
_BARRIER = re.compile(r"[;!?\n]|(?<!\d)\.(?!\d)|\b(?:but|whereas|while)\b", re.I)
_SCOPE = re.compile(
    r"\b(?:not|no|never|neither|nor|without|unless|if|whether|only|except|"
    r"instead|rather|den(?:y|ies|ied)|unclear|ambiguous)\b|n['’]t\b", re.I)
_WORDS = r"(?:\w+(?:['’]\w+)?\s+){0,4}"
_MONEY = r"(?:[$£€]\s*\d[\d,.]*|\d[\d,.]*\s+(?:dollars?|euros?|pounds?))"
_QUANTITY = r"(?:[$£€]\s*\d[\d,.]*|\d[\d,.]*\s*(?:%|percent\b|per cent\b|dollars?\b|euros?\b|pounds?\b|units?\b|points?\b))"


@dataclass(frozen=True)
class Rule:
    concept: str
    sense: str
    key: str
    patterns: tuple[str, ...]
    # First allowed rendering whose condition the pattern establishes.
    rendering: int = 0


# {t} is replaced by an exact, named capture of the specific source occurrence.
# Each pattern must demonstrate a relation to that capture, never just a domain
# keyword elsewhere in the document. Every regex match becomes an exact quote.
_RULES = (
    Rule("equity", "ownership", "individual-company-ownership", (
        r"\b(?:own|owns|owned|hold|holds|held|acquire|acquires|acquired|sell|sells|sold)\s+" + _WORDS + r"{t}\s+in\s+(?:the\s+)?(?:company|firm|business)\b",
        r"{t}\s+(?:means|represents|is)\s+(?:an?\s+)?(?:ownership\s+)?(?:stake|share|interest)\s+in\s+(?:the\s+|a\s+)?(?:company|firm|business)\b",
    )),
    Rule("equity", "ownership", "traded-equities", (
        r"\b(?:buy|buys|bought|sell|sells|sold|trade|trades|traded)\s+{t}\s+(?:on|in)\s+(?:the\s+)?(?:stock\s+)?(?:market|exchange)\b",
    ), 1),
    Rule("equity", "residual-capital", "company-assets-minus-liabilities", (
        r"{t}\s+(?:equals|is|means|represents)\s+(?:the\s+)?(?:company['’]s\s+|firm['’]s\s+|corporate\s+)?(?:total\s+)?assets\s+(?:minus|less)\s+(?:total\s+)?liabilities\b",
        r"\b(?:company|firm)['’]s\s+{t}\s+(?:equals|is)\s+assets\s+(?:minus|less)\s+liabilities\b",
    )),
    Rule("equity", "residual-capital", "property-value-minus-mortgage", (
        r"{t}\s+(?:equals|is|means|represents)\s+(?:the\s+)?(?:home|house|property)['’]s\s+value\s+(?:minus|less)\s+(?:the\s+)?(?:remaining\s+|outstanding\s+)?mortgage\b",
        r"{t}\s+(?:equals|is)\s+(?:the\s+)?(?:home|house|property)\s+value\s+(?:minus|less)\s+(?:the\s+)?mortgage\b",
    ), 2),
    Rule("equity", "fair-treatment", "fair-treatment-or-allocation", (
        r"{t}\s+(?:means|requires|promotes|is)\s+(?:the\s+)?(?:fair|equitable)\s+(?:treatment|allocation|distribution|access)\b",
        r"\b(?:fair|equitable)\s+(?:treatment|allocation|distribution)\s+" + _WORDS + r"(?:is called|defines|constitutes)\s+{t}",
        r"\b(?:promote|promotes|promoting|ensure|ensures|ensuring)\s+{t}\s+in\s+(?:education|educational access|access to education|resource allocation|treatment)\b",
    )),
    Rule("interest", "money-payment", "loan-deposit-payment", (
        r"\b(?:pay|pays|paid|earn|earns|earned|receive|receives|received|charge|charges|charged)\s+(?:" + _MONEY + r"\s+(?:in\s+)?)?{t}\s+on\s+(?:the\s+|a\s+|their\s+|its\s+)?(?:loan|debt|deposit|borrowed money|savings)\b",
        r"{t}\s+(?:is|means|represents)\s+(?:the\s+|a\s+)?(?:payment|charge|cost)\s+(?:for|on)\s+(?:a\s+|the\s+)?(?:loan|borrowed money|borrowing)\b",
        r"{t}\s+(?:on|from)\s+(?:the\s+|a\s+)?(?:loan|deposit|debt)\s+(?:is|was|amounts to)\s+" + _MONEY,
    )),
    Rule("interest", "attention", "attention-learning", (
        r"\b(?:show|shows|showed|develop|develops|developed|express|expresses|expressed|take|takes|took)\s+(?:an?\s+)?(?:keen\s+|strong\s+)?{t}\s+in\s+(?:learning|studying|reading|music|art|history|science|the lesson)\b",
        r"\b(?:am|is|are|was|were|became)\s+{t}\s+in\s+(?:learning|studying|reading|music|art|history|science)\b",
        r"{t}\s+(?:means|is)\s+(?:curiosity|attention|a desire to learn)\b",
    )),
    Rule("interest", "legal-stake", "company-ownership-right", (
        r"\b(?:own|owns|owned|hold|holds|held|acquire|acquires|acquired)\s+(?:an?\s+)?(?:\d+(?:\.\d+)?%\s+)?{t}\s+in\s+(?:the\s+|a\s+)?(?:company|firm|business)\b",
        r"{t}\s+(?:means|is|represents)\s+(?:an?\s+)?ownership\s+(?:stake|share|right)\s+in\s+(?:the\s+|a\s+)?(?:company|firm|business)\b",
    )),
    Rule("interest", "legal-stake", "explicit-property-legal-right", (
        r"{t}\s+(?:means|is|represents)\s+(?:a\s+)?legal\s+right\s+(?:in|to)\s+(?:the\s+|a\s+)?property\b",
    ), 1),
    Rule("interest", "advantage", "benefit-protect", (
        r"\b(?:act|acts|acted|acting|work|works|worked)\s+{t}\s+(?:to\s+)?(?:protect|benefit|serve)\b",
        r"{t}\s+(?:means|is|represents)\s+(?:the\s+)?(?:benefit|advantage|welfare)\s+(?:of|to|for)\b",
        r"\b(?:protect|protects|protected|protecting|serve|serves|serving)\s+(?:the\s+)?(?:public['’]s\s+|citizens['’]\s+|people['’]s\s+)?{t}\s+(?:of|by|through)\b",
    )),
    Rule("security", "tradable-investment", "traded-financial-instrument", (
        r"\b(?:buy|buys|bought|sell|sells|sold|issue|issues|issued|hold|holds|held|trade|trades|traded)\s+(?:the\s+|a\s+)?{t}\s+(?:such as|including)\s+(?:stocks?|shares?|bonds?)\b",
        r"{t}\s+(?:is|are|means)\s+(?:a\s+)?(?:tradable|traded)\s+(?:financial\s+)?(?:investment|instrument|bond|share|stock)\b",
        r"\b(?:buy|buys|bought|sell|sells|sold|trade|trades|traded)\s+(?:the\s+)?{t}\s+on\s+(?:the\s+)?(?:stock|bond|financial)\s+(?:market|exchange)\b",
    )),
    Rule("security", "protection", "protection-from-risk", (
        r"{t}\s+(?:means|is|requires|provides|ensures)\s+(?:the\s+)?(?:protection|safety|stability|freedom from)\b",
        r"\b(?:improve|improves|improved|ensure|ensures|ensured|maintain|maintains|maintained)\s+{t}\s+(?:against|from|by preventing)\s+(?:theft|attacks?|intrusion|crime|loss|unemployment)\b",
        r"{t}\s+of\s+(?:the\s+)?(?:data|network|building|job|investments)\s+(?:against|from)\s+" + _WORDS + r"(?:theft|attacks?|intrusion|loss)\b",
    )),
    Rule("security", "collateral", "asset-pledged-for-debt", (
        r"\b(?:pledge|pledges|pledged|offer|offers|offered|provide|provides|provided)\s+(?:the\s+|a\s+)?(?:house|property|asset|assets|land|building)\s+(?:as\s+)?{t}\s+for\s+(?:the\s+|a\s+)?(?:loan|debt|borrowing)\b",
        r"{t}\s+(?:means|is)\s+(?:an?\s+)?asset\s+pledged\s+for\s+(?:the\s+|a\s+)?(?:loan|debt)\b",
    )),
    Rule("principal", "money-base", "loan-investment-base", (
        r"{t}\s+(?:is|means|equals|represents)\s+(?:the\s+)?(?:original|initial)\s+(?:amount\s+)?(?:borrowed|lent|invested|loan|investment)\b",
        r"\b(?:repay|repays|repaid|lend|lends|lent|borrow|borrows|borrowed)\s+(?:the\s+)?{t}\s+(?:of|on)\s+(?:the\s+|a\s+)?loan\b",
        r"{t}\s+(?:of|on)\s+(?:the\s+|a\s+)?loan\s+(?:is|was|equals)\s+" + _MONEY,
    )),
    Rule("principal", "head-person", "school-head", (
        r"{t}\s+(?:of|at)\s+(?:the\s+|a\s+)?school\b",
        r"{t}\s+(?:welcomed|addressed|met|taught|spoke to)\s+(?:the\s+)?(?:students|pupils|teachers)\b",
        r"{t}\s+(?:is|means)\s+(?:the\s+)?head\s+of\s+(?:the\s+|a\s+)?school\b",
    )),
    Rule("principal", "main-adjective", "explicit-modified-noun", (
        r"{t}\s+(?:reason|cause|aim|objective|purpose|factor|concern|author|investigator)\b",
        r"{t}\s+(?:is|means)\s+(?:main|chief|most important)\b",
    )),
    Rule("principal", "main-adjective", "compound-main-reason", (
        r"{t}\s+(?:is|was|for)\b",
    )),
    Rule("overhead", "indirect-business-cost", "indirect-product-cost", (
        r"{t}\s+(?:means|is|are|includes|comprises)\s+(?:the\s+)?indirect\s+(?:production\s+|manufacturing\s+|business\s+|operating\s+)?costs\b",
        r"\bindirect\s+(?:production\s+|manufacturing\s+|business\s+)?costs\s+(?:are called|constitute|are classified as)\s+{t}",
    )),
    Rule("overhead", "above-head", "physical-above-head", (
        r"\b(?:birds|planes|aircraft|helicopters)\s+(?:flew|fly|flies|passed|circled)\s+{t}",
        r"{t}\s+(?:is|was)\s+(?:above|over)\s+(?:our|their|the|my|your)\s+heads?\b",
        r"\b(?:installed|hung|suspended)\s+{t}\s+(?:above|over)\s+(?:the\s+)?(?:desk|table|stage|room)\b",
    )),
    Rule("margin", "profit-amount", "money-sales-minus-cost", (
        r"{t}\s+(?:is|was|equals|amounts to)\s+" + _MONEY + r"\s*[,—-]?\s*(?:the\s+)?(?:selling price|sales|revenue)\s+(?:minus|less)\s+(?:the\s+)?(?:cost|costs)\b",
        r"{t}\s+(?:is|equals)\s+(?:the\s+)?(?:selling price|sales|revenue)\s+(?:minus|less)\s+(?:the\s+)?costs?\s*[,—-]?\s*(?:an amount of|amounting to)\s+" + _MONEY,
    )),
    Rule("margin", "operating-profit-ratio", "operating-profit-net-revenue", (
        r"{t}\s+(?:is|equals|means)\s+(?:the\s+)?(?:operating (?:income|profit)|income from operations)\s+(?:divided by|/)\s+(?:the\s+)?net\s+(?:revenues?|sales)\b",
        r"{t}\s+(?:is|was|equals|rose to|fell to)\s+\d+(?:\.\d+)?\s*(?:%|percent\b)",
    )),
    Rule("margin", "page-space", "page-space-or-width", (
        r"{t}\s+(?:is|means)\s+(?:the\s+)?(?:blank|empty)\s+space\s+(?:around|beside)\s+(?:the\s+)?(?:text|printed text|page text)\b",
        r"\b(?:write|writes|wrote|writing|notes?)\s+(?:in|on)\s+(?:the\s+)?{t}\s+of\s+(?:the\s+|a\s+)?(?:page|document|paper)\b",
        r"{t}\s+(?:is|was|measures)\s+\d+(?:\.\d+)?\s*(?:cm|mm|inches|inch)\s+(?:wide|on the page)\b",
    )),
    Rule("margin", "difference", "score-or-result-gap", (
        r"\b(?:won|win|wins|lost|lose|loses|defeated)\s+" + _WORDS + r"by\s+(?:a\s+|the\s+)?{t}\s+of\s+\d+\s+(?:votes|points|goals)\b",
        r"{t}\s+(?:is|means|equals)\s+(?:the\s+)?difference\s+between\s+(?:the\s+)?(?:two\s+)?(?:scores|results|vote totals)\b",
    )),
    Rule("margin", "trading-collateral", "broker-trade-loss-collateral", (
        r"{t}\s+(?:is|means)\s+(?:the\s+)?(?:cash|money|collateral|securities)\s+(?:posted|deposited|pledged)\s+(?:with|to)\s+(?:the\s+|a\s+)?broker\s+(?:to cover|against|for)\s+(?:trading\s+|trade\s+)?losses\b",
        r"\b(?:post|posts|posted|deposit|deposits|deposited)\s+{t}\s+with\s+(?:the\s+|a\s+)?broker\s+to cover\s+(?:trading|trade)\s+losses\b",
    )),
    Rule("percentage-change", "relative-change", "explicit-relative-positive-base", (
        r"{t}\s+(?:is|means)\s+(?:the\s+)?(?:relative change|change relative)\s+(?:in|to|from)\s+(?:the\s+|a\s+)?(?:positive\s+)(?:initial|original|base|previous)\s+value\b",
    )),
    Rule("percentage-change", "percentage-point-difference", "explicit-percentage-point-unit", (
        r"{t}",
    )),
    Rule("increase-relation", "by-change", "by-numeric-change", (
        r"{t}\s+" + _QUANTITY,
    )),
    Rule("increase-relation", "to-level", "to-numeric-level", (
        r"{t}\s+" + _QUANTITY,
    )),
)


def _pattern(surface: str) -> str:
    # Flexible whitespace/apostrophe matching changes no source bytes or offsets.
    return r"(?<!\w)" + r"\s+".join(re.escape(p).replace("'", "['’]") for p in surface.split()) + r"(?!\w)"


def _u16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _quote(text: str, start: int, end: int, ref: dict, kind: str) -> dict:
    return {"sourceVersionId": ref.get("sourceVersionId"),
            "blockId": ref.get("blockId"), "documentLocator": ref.get("documentLocator"),
            "order": ref.get("order"),
            "origin": kind, "start": start, "end": end,
            "startUtf16": _u16(text[:start]), "endUtf16": _u16(text[:end]),
            "text": text[start:end]}


def _clause(text: str, start: int, end: int) -> tuple[int, int]:
    left, right = 0, len(text)
    for boundary in _BARRIER.finditer(text):
        if boundary.end() <= start:
            left = boundary.end()
        elif boundary.start() >= end:
            right = boundary.start()
            break
    return left, right


def _eligible_rule(rule: Rule, surface: str) -> bool:
    normalized = " ".join(surface.casefold().replace("’", "'").split())
    if rule.key == "traded-equities":
        return normalized == "equities"
    if rule.key == "compound-main-reason":
        return normalized == "principal reason"
    if rule.sense == "operating-profit-ratio":
        return normalized == "operating margin"
    if rule.sense == "percentage-point-difference":
        return normalized in {"percentage point", "percentage points", "percentage point change"}
    if rule.sense == "relative-change":
        return normalized in {"percentage change", "percent change", "per cent change"}
    if rule.sense == "by-change":
        return normalized.endswith(" by")
    if rule.sense == "to-level":
        return normalized.endswith(" to")
    if rule.concept == "interest" and rule.sense != "attention":
        return normalized not in {"interested", "interesting"}
    return True


def _evidence(text: str, occurrence: dict, ref: dict, kind: str) -> tuple[list[dict], list[dict]]:
    start, end = occurrence["start"], occurrence["end"]
    lo, hi = _clause(text, start, end)
    clause = text[lo:hi]
    scope = list(_SCOPE.finditer(clause))
    if scope:
        return [], [{"reason": "unsupported_negation_condition_or_ambiguity_scope",
                     "quote": _quote(text, lo, hi, ref, kind)}]
    hits = []
    for rule in _RULES:
        if rule.concept != occurrence["conceptId"] or not _eligible_rule(rule, text[start:end]):
            continue
        term = "(?P<term>" + _pattern(text[start:end]) + ")"
        for pattern in rule.patterns:
            compiled = re.compile(pattern.replace("{t}", term), re.I)
            for match in compiled.finditer(clause):
                if match.span("term") != (start - lo, end - lo):
                    continue
                if rule.key == "company-assets-minus-liabilities" and not (
                    "shareholders" in occurrence["surface"].casefold()
                    or re.search(r"\b(?:company|firm|corporate)\b", match.group(), re.I)
                    or re.search(r"\b(?:company|firm)['’]s\s*$", text[lo:start], re.I)
                ):
                    continue
                # An additional predicate about the same mention may contradict
                # the supported relation. It is not parsed or silently discarded.
                tail = clause[match.end():]
                if re.match(r"\s*,?\s*and\s+(?:means|is|equals|represents|requires|includes)\b", tail, re.I):
                    return [], [{"reason": "unsupported_coordinated_predicate",
                                 "quote": _quote(text, lo, hi, ref, kind)}]
                rendering = rule.rendering
                if rule.sense == "protection":
                    normalized = match.group().casefold()
                    rendering = (0 if re.search(r"\b(?:data|network|building)\b", normalized)
                                 else 3 if "national security" in normalized
                                 else 2 if re.search(r"\b(?:job|financial)\b", normalized)
                                 else 1)
                hits.append({"senseId": rule.sense, "ruleId": rule.key,
                             "renderingIndex": rendering,
                             "quote": _quote(text, lo + match.start(), lo + match.end(), ref, kind)})
    return hits, []


def _occurrences(source: str, dictionary: dict) -> tuple[list[dict], list[dict]]:
    candidates = []
    for entry in dictionary["entries"]:
        for form in entry["forms"]:
            for match in re.finditer(_pattern(form), source, re.I):
                candidates.append({"conceptId": entry["id"], "start": match.start(), "end": match.end(),
                                   "surface": match.group(), "compound": None})
        for compound in entry.get("compounds", []):
            for match in re.finditer(_pattern(compound["surface"]), source, re.I):
                candidates.append({"conceptId": entry["id"], "start": match.start(), "end": match.end(),
                                   "surface": match.group(), "compound": compound})
    candidates.sort(key=lambda item: (-len(item["surface"].split()),
                                     -(item["end"] - item["start"]),
                                     item["compound"] is None, item["start"], item["conceptId"]))
    kept, suppressed = [], []
    for candidate in candidates:
        overlap = next((item for item in kept if candidate["start"] < item["end"]
                        and candidate["end"] > item["start"]), None)
        if overlap:
            if (candidate["start"], candidate["end"]) == (overlap["start"], overlap["end"]):
                candidate_key = (candidate["conceptId"],
                    (candidate["compound"] or {}).get("action"),
                    (candidate["compound"] or {}).get("sense_id"))
                overlap_key = (overlap["conceptId"],
                    (overlap["compound"] or {}).get("action"),
                    (overlap["compound"] or {}).get("sense_id"))
                if candidate["conceptId"] != overlap["conceptId"] or (
                    candidate["compound"] and overlap["compound"] and candidate_key != overlap_key
                ):
                    overlap["conflictingEqualSpan"] = True
            # A form and its own exact compound are one occurrence, not an error.
            if (candidate["start"], candidate["end"], candidate["conceptId"]) == (
                    overlap["start"], overlap["end"], overlap["conceptId"]):
                continue
            suppressed.append({**candidate, "suppressedBy": {"start": overlap["start"],
                "end": overlap["end"], "surface": overlap["surface"]}})
        else:
            kept.append(candidate)
    return sorted(kept, key=lambda item: item["start"]), suppressed


def _validate_dictionary(dictionary: dict) -> None:
    entries = dictionary.get("entries")
    if not isinstance(entries, list) or {entry.get("id") for entry in entries} != _CONCEPTS or len(entries) != 8:
        raise ValueError("focused_dictionary_required_no_registered_dictionary_merge")
    expected = {(rule.concept, rule.sense) for rule in _RULES}
    actual = {(entry["id"], sense["id"]) for entry in entries for sense in entry["senses"]}
    if actual != expected or sum(len(entry["senses"]) for entry in entries) != 24:
        raise ValueError("unsupported_dictionary_sense_inventory")
    for entry in entries:
        for sense in entry["senses"]:
            if sense.get("prompt_action") not in {_FINANCIAL, _SUPPRESS}:
                raise ValueError("unsupported_dictionary_prompt_action")
            if not sense.get("allowed_renderings") or not sense.get("definition_ko"):
                raise ValueError("dictionary_rendering_or_definition_missing")
            financial = sense["id"] in {
                "ownership", "residual-capital", "money-payment", "legal-stake",
                "tradable-investment", "collateral", "money-base", "indirect-business-cost",
                "profit-amount", "operating-profit-ratio", "trading-collateral"}
            if sense["prompt_action"] != (_FINANCIAL if financial else _SUPPRESS):
                raise ValueError("dictionary_sense_prompt_action_mismatch")
    actions = [sense["prompt_action"] for entry in entries for sense in entry["senses"]]
    if actions.count(_FINANCIAL) != 11 or actions.count(_SUPPRESS) != 13:
        raise ValueError("dictionary_prompt_action_inventory_mismatch")


def select_senses(source: str, context_fragments: list[dict], dictionary: dict, *,
                  source_ref: dict | None = None) -> dict:
    """Return financial prompt hints plus all occurrence decisions and evidence.

    Unrecognized metadata is ignored, never used as a semantic label. Surviving
    context may disambiguate only an explicit target reference to a definition
    ("equity has the meaning defined above"). Arbitrary neighboring keywords
    and same-term mentions do not establish reference identity.
    """
    if not isinstance(source, str) or not isinstance(context_fragments, list):
        raise ValueError("source_text_and_context_list_required")
    _validate_dictionary(dictionary)
    source_ref = {key: (source_ref or {}).get(key) for key in
                  ("sourceVersionId", "blockId", "documentLocator", "order")}
    clean_context = []
    for fragment in context_fragments:
        if not isinstance(fragment, dict) or not isinstance(fragment.get("text"), str):
            raise ValueError("context_text_required")
        fragment_ref = {key: fragment.get(key) for key in
                        ("sourceVersionId", "blockId", "documentLocator", "order")}
        if source_ref["sourceVersionId"] is not None and fragment_ref["sourceVersionId"] != source_ref["sourceVersionId"]:
            raise ValueError("context_source_version_mismatch")
        clean_context.append({"text": fragment["text"], **fragment_ref})
    dictionary_hash = hashlib.sha256(json.dumps(dictionary, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")).encode("utf-8")).hexdigest()
    entries = {entry["id"]: entry for entry in dictionary["entries"]}
    sources = {item["id"]: item["url"] for item in dictionary.get("sources", [])}
    occurrences, overlaps = _occurrences(source, dictionary)
    hints, decisions, rendered = [], [], set()
    for occurrence in occurrences:
        entry = entries[occurrence["conceptId"]]
        compound = occurrence["compound"]
        decision = {key: occurrence[key] for key in ("conceptId", "surface", "start", "end")}
        decision.update({"occurrence": _quote(source, occurrence["start"], occurrence["end"], source_ref, "target"),
                         "senseId": None, "promptAction": "ambiguous_no_hint", "renderedHint": None,
                         "applicationEvidence": [], "exclusionCheck": [], "reason": None,
                         "dictionarySha256": dictionary_hash, "dictionaryHashKind": "canonical-json-utf8",
                         "selectorVersion": SELECTOR_VERSION})
        if occurrence.get("conflictingEqualSpan"):
            decision["reason"] = "conflicting_equal_length_dictionary_candidates"
            decisions.append(decision)
            continue
        if compound and compound["action"] == "defer":
            decision.update({"promptAction": "unsupported_no_hint", "reason": "deferred_compound_blocks_inner_forms",
                             "exclusionCheck": [{"condition": compound["condition_ko"], "status": "deferred"}]})
            decisions.append(decision)
            continue
        evidence, blocked = _evidence(source, occurrence, source_ref, "target")
        # Only an explicit metalinguistic reference permits cross-block evidence.
        lo, hi = _clause(source, occurrence["start"], occurrence["end"])
        reference_pattern = re.compile("(?P<term>" + _pattern(occurrence["surface"]) + ")" +
            r"\s+has\s+the\s+meaning\s+defined\s+(?P<direction>above|below)\b", re.I)
        referential = next((match for match in reference_pattern.finditer(source[lo:hi])
            if match.span("term") == (occurrence["start"] - lo, occurrence["end"] - lo)), None)
        if not evidence and not blocked and referential:
            for fragment in clean_context:
                target_order, context_order = source_ref.get("order"), fragment.get("order")
                if not isinstance(target_order, int) or not isinstance(context_order, int):
                    continue
                if not ((referential.group("direction").casefold() == "above" and context_order < target_order)
                        or (referential.group("direction").casefold() == "below" and context_order > target_order)):
                    continue
                matching, _ = _occurrences(fragment["text"], dictionary)
                for ctx in matching:
                    if ctx["conceptId"] != occurrence["conceptId"] or (ctx["compound"] and ctx["compound"]["action"] == "defer"):
                        continue
                    normalize = lambda value: " ".join(value.casefold().replace("’", "'").split())
                    if normalize(ctx["surface"]) != normalize(occurrence["surface"]):
                        continue
                    # Definition must state this term's sense explicitly.
                    after = fragment["text"][ctx["end"]:]
                    if not re.match(r"\s+(?:means|is|equals|represents)\b", after, re.I):
                        continue
                    found, exclusions = _evidence(fragment["text"], ctx, fragment, "context")
                    evidence.extend(found)
                    blocked.extend(exclusions)
            if evidence:
                decision["contextReference"] = _quote(source, lo + referential.start(),
                    lo + referential.end(), source_ref, "target")
                decision["contextReference"]["targetOrder"] = source_ref["order"]
                decision["contextReference"]["direction"] = referential.group("direction").casefold()
        decision["applicationEvidence"] = evidence
        decision["exclusionCheck"] = blocked
        selected = {item["senseId"] for item in evidence}
        if blocked:
            decision.update({"promptAction": "unsupported_no_hint", "reason": "negation_or_condition_scope_not_resolved"})
        elif len(selected) > 1:
            decision["reason"] = "multiple_supported_senses"
        elif not selected:
            decision["reason"] = "no_explicit_supported_relation"
        else:
            sense_id = next(iter(selected))
            sense = next(item for item in entry["senses"] if item["id"] == sense_id)
            indexes = {item["renderingIndex"] for item in evidence}
            # Contradictory rendering conditions are withheld, not resolved by order.
            if len(indexes) > 1:
                decision["reason"] = "conflicting_rendering_conditions"
                decisions.append(decision)
                continue
            if compound and compound.get("sense_id") != sense_id:
                decision["reason"] = "compound_condition_conflict"
                decisions.append(decision)
                continue
            index = min(indexes)
            rendering = sense["allowed_renderings"][index]
            decision.update({"senseId": sense_id, "promptAction": sense["prompt_action"],
                             "reason": "single_explicit_supported_sense", "selectedRendering": rendering,
                             "sourceUrls": [sources[item] for item in sense["source_ids"]],
                             "exclusionCheck": [{"condition": item,
                                 "status": "not_observed_within_supported_pattern",
                                 "scope": "quoted_relation_only_not_general_semantic_proof"}
                                 for item in sense["exclusion_conditions_ko"]]})
            if sense["prompt_action"] == _FINANCIAL:
                hint = {"source": occurrence["surface"], "target": rendering["text"],
                        "definition": sense["definition_ko"], "senseId": sense_id,
                        "start": occurrence["start"], "end": occurrence["end"]}
                key = (hint["source"], hint["senseId"], hint["target"])
                if key not in rendered:
                    hints.append(hint)
                    rendered.add(key)
                decision["renderedHint"] = hint
        decisions.append(decision)
    for occurrence in overlaps:
        decisions.append({"conceptId": occurrence["conceptId"], "surface": occurrence["surface"],
                          "start": occurrence["start"], "end": occurrence["end"],
                          "occurrence": _quote(source, occurrence["start"], occurrence["end"], source_ref, "target"),
                          "senseId": None, "promptAction": "overlap_suppressed", "renderedHint": None,
                          "reason": "longer_compound_owns_occurrence", "suppressedBy": occurrence["suppressedBy"],
                          "applicationEvidence": [], "exclusionCheck": [],
                          "dictionarySha256": dictionary_hash, "dictionaryHashKind": "canonical-json-utf8",
                          "selectorVersion": SELECTOR_VERSION})
    return {"hints": hints, "decisions": sorted(decisions, key=lambda item: (item["start"], -item["end"])),
            "selectorVersion": SELECTOR_VERSION, "dictionarySha256": dictionary_hash,
            "dictionaryHashKind": "canonical-json-utf8", "support": {
                "concepts": 8, "sensesWithLimitedPatterns": 24, "financialHintSenses": 11,
                "nonfinancialSuppressionSenses": 13,
                "scope": "explicit_local_relations_and_explicit_definition_references_only",
                "unsupported": ["general_word_sense_disambiguation", "implicit_coreference",
                                "negation_and_condition_scope", "unlisted_compounds_and_senses",
                                "definition_reference_without_explicit_order"]}}
