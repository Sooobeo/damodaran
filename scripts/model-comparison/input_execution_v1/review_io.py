"""Convert explicitly authored review quotes to codepoint evidence spans.

This module supplies no verdicts, answers, issue correspondence or scores.
Callers must supply the actual reviewer/context provenance separately.
"""
from __future__ import annotations
from copy import deepcopy
from . import evaluation as ev


def quote_spans(text, quotes):
    ev.require(isinstance(quotes, list), 'evidence_quotes_must_be_list')
    result = []
    for quote in quotes:
        if isinstance(quote, dict):
            ev.exact_span(text, quote)
            result.append(deepcopy(quote))
            continue
        ev.require(isinstance(quote, str) and quote, 'nonempty_explicit_quote_required')
        start = text.find(quote)
        ev.require(start >= 0, 'authored_quote_not_in_text')
        ev.require(text.find(quote, start + 1) < 0, 'ambiguous_quote_requires_explicit_span')
        result.append({'start': start, 'end': start + len(quote), 'text': quote})
    return result


def materialize_fields(row, texts):
    result = deepcopy(row)
    for field, text in texts.items():
        if field in result:
            result[field] = quote_spans(text, result[field])
    return result


def source_review(draft, packet, reviewer):
    result = deepcopy(draft)
    result.update({k: packet[k] for k in ('reviewId', 'sourceSha256', 'translationSha256')})
    result.update(humanReviewed=False, reviewer=deepcopy(reviewer))
    texts = {'sourceEvidence': packet['source'], 'translationEvidence': packet['translation']}
    result['fullText'] = materialize_fields(result['fullText'], texts)
    result['fullText']['issues'] = [materialize_fields(row, texts) for row in result['fullText']['issues']]
    for group in ('propositions', 'terms'):
        result[group] = [materialize_fields(row, texts) for row in result[group]]
    result['naturalness'] = materialize_fields(result['naturalness'], texts)
    return ev.validate_source_review(result, packet)


def question_answer(draft, packet, reviewer):
    result = deepcopy(draft)
    result.update(reviewId=packet['reviewId'], packetSha256=ev.sha(ev.packed(packet)),
                  humanReviewed=False, reviewer=deepcopy(reviewer))
    result['answers'] = [materialize_fields(row, {'translationEvidence': packet['translation']})
                         for row in result['answers']]
    return ev.validate_answer(result, packet)


def question_grade(draft, packet, answer, reviewer):
    result = deepcopy(draft)
    result.update({k: packet[k] for k in ('reviewId', 'sourceSha256', 'translationSha256')})
    result.update(answerSha256=ev.sha(ev.packed(answer)), humanReviewed=False, reviewer=deepcopy(reviewer))
    answers = {row['questionId']: row for row in answer['answers']}
    result['questions'] = [materialize_fields(row, {'sourceEvidence': packet['source'],
        'answerEvidence': answers[row['questionId']]['answerKo']}) for row in result['questions']]
    return ev.validate_question_grade(result, answer, packet)
