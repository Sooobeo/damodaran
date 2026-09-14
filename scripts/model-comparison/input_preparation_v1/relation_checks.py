"""Conservative, reference-free EN/KO surface relation diagnostics (not app QE)."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
import re

VERSION = "input-preparation-relations-v1"
TYPES = ("value_unit", "percent_change", "start_delta_result", "division_direction",
         "inclusion_negation", "time_condition")
STATUSES = ("supported_match", "supported_conflict", "undetermined", "unsupported", "source_ambiguous")
NUMBER = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
UNIT = r"(?:percentage\s+points?|percent(?:age)?|%\s*[pP]|퍼센트\s*포인트|퍼센트|%|dollars?|달러|euros?|유로|centimeters?|centimetres?|센티미터|cm|meters?|metres?|미터|m)"
QUANTITY = re.compile(rf"(?<![\w.])(?:(?P<prefix>[$€])\s*(?P<pn>{NUMBER})|(?P<n>{NUMBER})\s*(?P<unit>{UNIT}))", re.I)
CHANGE = re.compile(rf"\b(?P<verb>increas(?:e|es|ed|ing)|decreas(?:e|es|ed|ing)|ris(?:e|es|ing)|rose|fall(?:s|ing)?|fell)\s+(?:it\s+)?(?P<role>by|to)\s+(?P<num>{NUMBER})\s*(?P<unit>percentage\s+points?|percent(?:age)?|%)", re.I)
KO_CHANGE = re.compile(r"증가|높이|높인|높였|올리|올린|올렸|오른|올랐|상승|증가분|감소|낮추|낮춘|내리|내린|떨어|줄어")
NEGATION_EN = re.compile(r"\b(?:not|never|cannot|can't|may not|might not)\b", re.I)
NEGATION_KO = re.compile(r"않|아니|못하|못한|없")


def evidence(text: str, start: int, end: int) -> dict:
    """Offsets are half-open UTF-16 code units, always exact original text."""
    return {"start": len(text[:start].encode("utf-16-le")) // 2,
            "end": len(text[:end].encode("utf-16-le")) // 2, "text": text[start:end]}


def exact(text: str, quote: str) -> dict | None:
    if not quote or quote not in text:
        return None
    start = text.index(quote)
    return evidence(text, start, start + len(quote))


def sentence(text: str, start: int, end: int) -> tuple[int, int]:
    # Decimal points are not sentence boundaries. No text is normalized for spans.
    boundaries = list(re.finditer(r"[!?;\n]|\.(?=\s|$)", text))
    left = max((m.end() for m in boundaries if m.end() <= start), default=0)
    right = min((m.end() for m in boundaries if m.start() >= end), default=len(text))
    while left < right and text[left].isspace():
        left += 1
    return left, right


def canonical_number(value: str) -> str:
    return str(Decimal(value.replace(",", "")).normalize())


def unit_name(value: str) -> str:
    value = re.sub(r"\s+", "", value.lower())
    if value in ("$", "dollar", "dollars", "달러"):
        return "USD"
    if value in ("€", "euro", "euros", "유로"):
        return "EUR"
    if value in ("percentagepoint", "percentagepoints", "%p", "퍼센트포인트"):
        return "percentage_point"
    if value in ("%", "percent", "percentage", "퍼센트"):
        return "percent"
    return "cm" if value in ("cm", "centimeter", "centimeters", "centimetre", "centimetres", "센티미터") else "m"


def quantities(text: str) -> list[dict]:
    return [{"value": canonical_number(m.group("pn") or m.group("n")),
             "unit": unit_name(m.group("prefix") or m.group("unit")),
             "start": m.start(), "end": m.end()} for m in QUANTITY.finditer(text)]


def result(kind, status, reason, source, target, source_spans=(), target_spans=(), **details):
    return {"type": kind, "status": status, "warning": status == "supported_conflict",
            "reasonKo": reason, "sourceEvidence": [evidence(source, *s) for s in source_spans],
            "translationEvidence": [evidence(target, *s) for s in target_spans],
            "scope": "명시된 제한 표면 관계만 검사; 문단 의미 수용 판정 아님", **details}


def value_unit(source, target):
    kind = "value_unit"
    a, b = quantities(source), quantities(target)
    spans_a = [(q["start"], q["end"]) for q in a]
    spans_b = [(q["start"], q["end"]) for q in b]
    if not a:
        return result(kind, "unsupported", "지원 단위를 붙인 아라비아 숫자가 원문에 없다.", source, target)
    if re.search(r"\b(?:unclear|ambiguous)\b", source, re.I):
        return result(kind, "source_ambiguous", "원문이 수량 또는 단위의 불명확성을 명시한다.", source, target, spans_a)
    ca = Counter((q["value"], q["unit"]) for q in a)
    cb = Counter((q["value"], q["unit"]) for q in b)
    if ca == cb:
        status, reason = "supported_match", "값·단위 출현 다중집합이 같다. 수량의 대상·역할은 별도다."
    elif not b or len(a) != len(b):
        status, reason = "undetermined", "명시 수량 출현 수가 달라 안전하게 짝지을 수 없다. 한글 수사·단위 환산은 미지원이다."
    elif len(a) == 1 or Counter(q["value"] for q in a) == Counter(q["value"] for q in b):
        status, reason = "supported_conflict", "동일 출현 수의 명시 값·단위 대응이 다르다. 대상 역할의 정렬은 주장하지 않는다."
    else:
        status, reason = "undetermined", "여러 값의 변경·합치기·단위 환산 가능성을 이 규칙으로 확정하지 않는다."
    return result(kind, status, reason, source, target, spans_a, spans_b,
                  sourceQuantities=[{k:q[k] for k in ("value", "unit")} for q in a],
                  translationQuantities=[{k:q[k] for k in ("value", "unit")} for q in b])


def change_relation(source, target, kind):
    changes = list(CHANGE.finditer(source))
    if not changes:
        return result(kind, "unsupported", "명시 by/to 백분율 변화 구문이 없다.", source, target)
    if len(changes) != 1:
        return result(kind, "undetermined", "복수 변화의 한국어 대응 정렬은 지원하지 않는다.", source, target)
    a = changes[0]
    ss = sentence(source, a.start(), a.end())
    if NEGATION_EN.search(source[ss[0]:ss[1]]):
        return result(kind, "source_ambiguous", "변화 구문에 부정 범위가 있어 단순 긍정 관계로 확정하지 않는다.", source, target, [ss])
    qs = [q for q in quantities(target) if q["value"] == canonical_number(a["num"]) and q["unit"] in ("percent", "percentage_point")]
    candidates = []
    for q in qs:
        ts = sentence(target, q["start"], q["end"])
        tail = target[q["end"]:min(ts[1], q["end"]+30)]
        verb = KO_CHANGE.search(tail)
        if verb:
            candidates.append((q, ts, tail, verb))
    if len(candidates) != 1:
        return result(kind, "undetermined", "같은 값의 한국어 변화 구문을 하나로 대응시키지 못했다.", source, target, [ss])
    q, ts, tail, verb = candidates[0]
    if NEGATION_KO.search(target[ts[0]:ts[1]]):
        return result(kind, "undetermined", "한국어 변화 문장의 부정 범위를 확정하지 않는다.", source, target, [ss], [ts])
    direction_a = "down" if a["verb"].lower().startswith(("decreas", "fall", "fell")) else "up"
    direction_b = "down" if re.search(r"감소|낮추|낮춘|내리|내린|떨어|줄어", verb.group()) else "up"
    role_a = "delta" if a["role"].lower() == "by" else "result"
    role_b = "result" if re.match(r"\s*(?:으로|로)", tail) else "delta"
    if kind == "percent_change":
        agree = unit_name(a["unit"]) == q["unit"] and direction_a == direction_b
        reason = "명시 변화량의 %/%p 단위와 상승/하락을 대조했다."
    else:
        agree = role_a == role_b
        reason = "by 변화량과 to 결과값을 한국어 수치 뒤 조사·증가분 구문에 대응했다. 계산은 하지 않는다."
        # A starting value is checked only when both sides state it explicitly.
        start_a = re.search(rf"\bstarts?\s+at\s+({NUMBER})\s*%", source, re.I)
        if start_a:
            sq = [x for x in quantities(target) if x["value"] == canonical_number(start_a[1]) and x["unit"] == "percent"]
            if len(sq) != 1 or not re.search(r"시작|초기|처음", target[sentence(target, sq[0]["start"], sq[0]["end"])[0]:sentence(target, sq[0]["start"], sq[0]["end"])[1]]):
                return result(kind, "undetermined", "원문 시작값의 한국어 역할이 명시적으로 대응되지 않는다.", source, target, [ss], [ts])
    return result(kind, "supported_match" if agree else "supported_conflict", reason, source, target, [ss], [ts],
                  sourceRelation={"value":canonical_number(a["num"]),"unit":unit_name(a["unit"]),"direction":direction_a,"role":role_a},
                  translationRelation={"value":q["value"],"unit":q["unit"],"direction":direction_b,"role":role_b})


def division_direction(source, target):
    kind = "division_direction"
    op = rf"[$€]?\s*{NUMBER}(?:\s*(?:dollars?|euros?|달러|유로))?"
    patterns = [rf"\bdivid(?:e|es|ed|ing)\s+(?P<a>{op})\s+by\s+(?P<b>{op})", rf"(?P<a>{op})\s+divided\s+by\s+(?P<b>{op})"]
    matches = [m for p in patterns for m in re.finditer(p, source, re.I)]
    if not matches:
        return result(kind, "unsupported", "명시 숫자 피연산자의 나눗셈만 지원한다. 일반 명사·수식 변수 정렬은 미지원이다.", source, target)
    if len(matches) != 1:
        return result(kind, "undetermined", "복수 나눗셈 정렬을 확정하지 않는다.", source, target)
    m = matches[0]; ss = sentence(source, m.start(), m.end())
    if NEGATION_EN.search(source[ss[0]:ss[1]]):
        return result(kind, "source_ambiguous", "나눗셈 부정·지시 범위를 단순 계산 관계로 바꾸지 않는다.", source, target, [ss])
    forms = [(rf"(?P<a>{op})\s*(?:을|를)\s*(?P<b>{op})\s*(?:으로|로)\s*나[누눈눌]", False),
             (rf"(?P<b>{op})\s*(?:으로|로)\s*(?P<a>{op})\s*(?:을|를)\s*나[누눈눌]", False),
             (rf"(?P<a>{op})\s*(?:÷|/)\s*(?P<b>{op})", False)]
    tm = [x for p, _ in forms for x in re.finditer(p, target)]
    if len(tm) != 1:
        return result(kind, "undetermined", "한국어 명시 나눗셈의 두 피연산자를 유일하게 찾지 못했다.", source, target, [ss])
    n = tm[0]; ts = sentence(target, n.start(), n.end())
    if NEGATION_KO.search(target[ts[0]:ts[1]]):
        return result(kind, "undetermined", "한국어 나눗셈 부정 범위는 보류한다.", source, target, [ss], [ts])
    values = lambda x: [canonical_number(re.search(NUMBER, x[k])[0]) for k in ("a", "b")]
    va, vb = values(m), values(n)
    return result(kind, "supported_match" if va == vb else "supported_conflict", "명시 피제수·제수의 순서를 조사 또는 ÷/로 대조했다.", source, target, [ss], [ts], sourceOperands=va, translationOperands=vb)


def inclusion_negation(source, target):
    kind = "inclusion_negation"
    quantifier = re.search(r"\b(?P<q>not every|no)\s+(?:subsidiary|company)\b", source, re.I)
    if quantifier:
        ss = sentence(source, quantifier.start(), quantifier.end())
        # Scope is anchored to the explicitly stated dividend payment predicate.
        if not re.search(r"(?:paid|distributed)\s+a dividend", source[ss[0]:ss[1]], re.I):
            return result(kind, "unsupported", "부분/전면 부정은 배당 지급 명시 구문으로 범위를 한정한다.", source, target, [ss])
        ts = sentence(target, 0, 0); clause = target[ts[0]:ts[1]]
        if not re.search(r"(?:자회사|회사).*배당(?:금)?.*(?:지급|분배)", clause):
            return result(kind, "undetermined", "한국어의 회사·배당 지급 주체/술어를 대응시키지 못했다.", source, target, [ss], [ts])
        if re.search(r"모든.*(?:것은|것이|건)\s*아니", clause):
            q = "not every"
        elif re.search(r"(?:어느|어떤).*회사도.*(?:않|못)", clause):
            q = "no"
        else:
            return result(kind, "undetermined", "모두의 단순 부정은 한국어 부정 범위를 확정하지 않는다.", source, target, [ss], [ts])
        return result(kind, "supported_match" if q == quantifier["q"].lower() else "supported_conflict", "전칭 부정과 전면 부정을 별도 명시 패턴으로 대조했다.", source, target, [ss], [ts])
    matches = list(re.finditer(r"\b(?:(?P<neg>does? not)\s+)?(?P<v>includ(?:e|es)|exclud(?:e|es))\s+(?P<object>cash|debt|revenue)\b", source, re.I))
    if not matches:
        return result(kind, "unsupported", "명시 include/exclude와 지원 대상 명사 패턴이 없다.", source, target)
    if len(matches) != 1:
        return result(kind, "undetermined", "복수 포함/제외의 주체·대상 정렬은 지원하지 않는다.", source, target)
    m = matches[0]; ss = sentence(source, m.start(), m.end())
    if re.search(r"\b(?:may|might|could|not only|not necessarily)\b", source[ss[0]:ss[1]], re.I):
        return result(kind, "source_ambiguous", "양태·중첩 부정이 있어 포함 여부를 이진값으로 확정하지 않는다.", source, target, [ss])
    word = {"cash":"현금", "debt":"부채", "revenue":"(?:매출액|매출|수익)"}[m["object"].lower()]
    objs = list(re.finditer(word, target))
    if len(objs) != 1:
        return result(kind, "undetermined", "한국어 대상의 유일한 명시 인용을 찾지 못했다.", source, target, [ss])
    ts = sentence(target, objs[0].start(), objs[0].end()); clause = target[ts[0]:ts[1]]
    verb = re.search(r"(?P<v>포함|제외)(?P<tail>[^.!?;\n]*)", clause)
    if not verb or re.search(r"수\s*있|수\s*없|것은\s*아니|않.*않", clause):
        return result(kind, "undetermined", "한국어 포함 술어·양태·중첩 부정을 확정하지 않는다.", source, target, [ss], [ts])
    a = m["v"].lower().startswith("include") != bool(m["neg"])
    b = (verb["v"] == "포함") != bool(re.search(r"않|아니", verb["tail"]))
    return result(kind, "supported_match" if a == b else "supported_conflict", "하나의 명시 대상에 연결된 포함/제외와 단일 부정을 대조했다.", source, target, [ss], [ts], sourceIncluded=a, translationIncluded=b)


def time_condition(source, target):
    kind = "time_condition"
    matches = list(re.finditer(r"\b(?:only after|even before|only if|only when|after|during)\b", source, re.I))
    if not matches:
        return result(kind, "unsupported", "지원하는 명시 시간·필요조건 접속 표현이 없다.", source, target)
    if len(matches) != 1:
        return result(kind, "undetermined", "복수 시간/조건의 절 정렬을 확정하지 않는다.", source, target)
    m = matches[0]; ss = sentence(source, m.start(), m.end()); clause = source[ss[0]:ss[1]]
    if NEGATION_EN.search(clause):
        return result(kind, "source_ambiguous", "원문의 부정이 시간/조건 범위에 걸릴 수 있어 보류한다.", source, target, [ss])
    # Lexical anchors restrict the supported scope; they are a generic lexicon,
    # never item IDs, reference translations, judgments, or hidden propositions.
    anchors = [(r"\bintegration\b", r"통합"), (r"\bsigns?\b", r"서명"),
               (r"\bcode is valid\b", r"코드.*유효")]
    selected = [(en, ko) for en, ko in anchors if re.search(en, clause, re.I)]
    if len(selected) != 1:
        return result(kind, "unsupported", "시간/조건은 통합·서명·코드 유효성의 명시 사건에 한정한다.", source, target, [ss])
    anchors_ko = list(re.finditer(selected[0][1], target))
    if len(anchors_ko) != 1:
        return result(kind, "undetermined", "한국어 사건 인용을 유일하게 찾지 못했다.", source, target, [ss])
    tm = anchors_ko[0]; ts = sentence(target, tm.start(), tm.end()); ko = target[ts[0]:ts[1]]
    if NEGATION_KO.search(ko):
        return result(kind, "undetermined", "한국어 부정 조건의 범위를 확정하지 않는다.", source, target, [ss], [ts])
    if re.search(r"(?:후|뒤)(?:에)?만|후에야|뒤에야", ko):
        rel = "only after"
    elif re.search(r"전(?:에)?도", ko):
        rel = "even before"
    elif re.search(r"(?:경우|때)(?:에)?만", ko):
        rel = "only if"
    elif re.search(r"동안|도중|중에|진행\s*중", ko):
        rel = "during"
    elif re.search(r"후|뒤|이후", ko):
        rel = "after"
    elif re.search(r"경우|때", ko):
        rel = "if"
    else:
        return result(kind, "undetermined", "한국어의 명시 시간/필요조건 표현을 찾지 못했다.", source, target, [ss], [ts])
    original = m[0].lower().replace("only when", "only if")
    return result(kind, "supported_match" if rel == original else "supported_conflict", "같은 명시 사건의 이후/도중/이전 허용·필요조건 표지를 대조했다. 전체 당사자 역할은 미검사다.", source, target, [ss], [ts], sourceRelation=original, translationRelation=rel)


def inspect_relations(source: str, translation: str) -> dict:
    """Strict two-string boundary: evaluation metadata cannot be supplied."""
    if not isinstance(source, str) or not isinstance(translation, str):
        raise TypeError("source_and_translation_must_be_strings")
    rows = [value_unit(source, translation),
            change_relation(source, translation, "percent_change"),
            change_relation(source, translation, "start_delta_result"),
            division_direction(source, translation), inclusion_negation(source, translation),
            time_condition(source, translation)]
    for row in rows:
        if row["status"] in ("supported_match", "supported_conflict"):
            assert row["sourceEvidence"] and row["translationEvidence"]
    return {"version": VERSION, "warning": any(r["warning"] for r in rows), "relations": rows,
            "completeSemanticAssessment": False, "modelCalls": 0, "humanReviewed": False}
