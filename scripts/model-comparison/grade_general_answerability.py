"""Freeze Korean-only answers, then aggregate explicit source-grounded judgments.

No inference, semantic exact-match scoring, registration, or existing-file writes.
Use `freeze-answers --packet-directory DIR --answers FILE --output NEW_RECEIPT`
before writing judgments. Then `grade --packet-directory DIR --answers FILE
--freeze-receipt RECEIPT --judgments FILE --output NEW_REPORT`.
All paths are within .training/quality-evaluation; packet directories use the
prepare_general_answerability boundary. --self-test uses synthetic fixtures only.
Freeze receipts and grading reports are coordinator-only artifacts; do not send
them back to an answer session as additional reading material.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import secrets
import sys

import prepare_general_answerability as packets

VERSION = 'general16-answerability-grade-v1'
ANSWER_VERSION = 'general16-korean-answers-v1'
JUDGMENT_VERSION = 'general16-answerability-judgments-v1'
FREEZE_VERSION = 'general16-answer-freeze-v1'
ARTIFACT_ROOT = packets.ROOT / '.training/quality-evaluation'
ANSWER_STATUSES = {'answered', 'insufficient_information', 'ambiguous', 'unresolved'}
ANSWER_FIELDS = {'questionId', 'answerKo', 'targetEvidenceQuotes', 'contextEvidenceQuotes', 'status', 'uncertaintyKo'}
require, parse, packed, sha, text_sha = packets.require, packets.parse, packets.packed, packets.sha, packets.text_sha


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def utc(value):
    require(type(value) is str, 'utc_timestamp_required')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('invalid_utc_timestamp') from None
    require(result.tzinfo is not None and result.utcoffset().total_seconds() == 0, 'utc_timestamp_required')
    return result


def read_artifact(path):
    path = packets.safe_path(path, ARTIFACT_ROOT)
    require(path.is_file() and path.stat().st_size <= 16 * 1024 ** 2, 'artifact_file_required_or_too_large')
    raw = path.read_bytes()
    return parse(raw), {'path': str(path), 'sha256': sha(raw), 'sizeBytes': len(raw),
                        'mtimeNs': path.stat().st_mtime_ns, 'observedAtUtc': utc_now()}


def index_rows(rows, allowed, code, *, complete=False):
    require(type(rows) is list and all(type(row) is dict for row in rows), code + '_rows_required')
    result = {}
    for row in rows:
        identifier = row.get('questionId')
        require(type(identifier) is str and identifier in allowed, code + '_unknown_id')
        require(identifier not in result, code + '_duplicate_id')
        result[identifier] = row
    if complete:
        require(set(result) == set(allowed), code + '_missing_id')
    return result


def check_quotes(quotes, text, code, *, required=False):
    require(type(quotes) is list and (not required or bool(quotes)), code + '_list_required')
    require(all(type(quote) is str and bool(quote.strip()) and quote in text for quote in quotes), code + '_not_exact_substring')


def validate_bundle(packet, key, manifest, questions, sources):
    require(manifest.get('version') == key.get('version') == packets.VERSION
            and manifest.get('status') == 'prepared_not_answered_not_scored'
            and manifest.get('expectedItems') == 16 and manifest.get('expectedQuestions') == 32, 'unsupported_packet_manifest')
    require(key.get('questionnaireSha256') == packets.QUESTION_SHA and key.get('datasetSha256') == packets.DATASET_SHA,
            'private_key_frozen_identity')
    require(key.get('candidate') == manifest.get('candidate') and key.get('profile') == manifest.get('profile')
            and key['profile'] in ('raw', 'contextual'), 'packet_configuration_differs')
    require(packed(key.get('scoring')) == packed(questions['scoring']), 'private_key_scoring_changed')
    require(manifest.get('packet', {}).get('path') == 'answerer/packet.jsonl'
            and manifest.get('privateKey', {}).get('path') == 'private/private-key.json', 'packet_paths_changed')
    require(all(manifest.get(field) is True for field in ('freshAnswerSessionPerPacketRequired',
            'answererMustNotHaveSeenSourceOrKey', 'preparerOrQuestionAuthorMustNotAnswer', 'answersMustBeFrozenBeforeOpeningKey')),
            'packet_administration_missing')
    require(len(packet) == len(key.get('rows', [])) == 32, 'packet_32_required')
    identifiers = [row.get('questionId') for row in packet]
    require(all(type(identifier) is str and re.fullmatch(r'q_[0-9a-f]{32}', identifier)
                for identifier in identifiers), 'packet_anonymous_ids_required')
    require(len(set(identifiers)) == 32, 'packet_duplicate_id')
    packet_map = index_rows(packet, identifiers, 'packet', complete=True)
    key_map = index_rows(key['rows'], identifiers, 'key', complete=True)
    frozen = {question['id']: (item, question) for item in questions['items'] for question in item['questions']}
    require(len(frozen) == 32 and {row.get('originalQuestionId') for row in key['rows']} == set(frozen), 'frozen_question_coverage')
    require(Counter(row.get('sourceId') for row in key['rows']) == Counter({identifier: 2 for identifier in packets.IDS}),
            'sixteen_source_coverage')
    source_map, targets = {row['id']: row for row in sources}, {}
    for identifier, row in packet_map.items():
        link = key_map[identifier]
        item, question = frozen[link['originalQuestionId']]
        require(link.get('sourceId') == item['id'] and link.get('domain') == item['domain']
                and packed(link.get('question')) == packed(question), 'private_question_changed')
        require(set(row) == packets.VISIBLE_FIELDS and all(type(value) is str for value in row.values()), 'packet_field_allowlist')
        require(row['questionKo'] == question['questionKo'] and row['learningContextKo'] == item['learningContextKo']
                and link.get('learningContextKoSha256') == text_sha(row['learningContextKo'])
                and link.get('targetSha256') == text_sha(row['candidateTranslationKo']), 'packet_text_hash_or_question_differs')
        for field in ('sourceSha256', 'contextSha256'):
            require(link.get(field) == item[field] == source_map[item['id']][field], 'packet_source_hash_differs')
        if item['id'] in targets:
            require(targets[item['id']] == row['candidateTranslationKo'], 'two_questions_have_different_translation')
        targets[item['id']] = row['candidateTranslationKo']
    require(sum(row['question']['criticalQuestion'] is True for row in key['rows']) == 16, 'critical_16_required')
    return {'packet': packet_map, 'key': key_map, 'sources': source_map, 'manifest': manifest, 'questions': questions}


def load_bundle(directory):
    directory = packets.safe_path(directory, packets.OUTPUT_ROOT)
    paths = {'packet': directory / 'answerer/packet.jsonl', 'key': directory / 'private/private-key.json',
             'manifest': directory / 'private/manifest.json'}
    for path in paths.values():
        packets.safe_path(path, ARTIFACT_ROOT)
    raw = {name: path.read_bytes() for name, path in paths.items()}
    key, manifest = parse(raw['key']), parse(raw['manifest'])
    require(sha(raw['packet']) == key.get('packetSha256') == manifest.get('packet', {}).get('sha256')
            and sha(raw['key']) == manifest.get('privateKey', {}).get('sha256'), 'packet_or_private_key_hash_changed')
    dataset_raw, questions_raw = packets.DATASET.read_bytes(), packets.QUESTIONS.read_bytes()
    require(sha(dataset_raw) == packets.DATASET_SHA and sha(questions_raw) == packets.QUESTION_SHA, 'frozen_source_or_questions_changed')
    sources = [{field: row[field] for field in ('id', 'source', 'context', 'domain', 'sourceSha256', 'contextSha256')}
               for row in (parse(line) for line in dataset_raw.split(b'\n') if line.strip())]
    questions = packets.validate_questions(questions_raw, sources, expected_sha=packets.QUESTION_SHA)
    packet = [parse(line) for line in raw['packet'].split(b'\n') if line.strip()]
    bundle = validate_bundle(packet, key, manifest, questions, sources)
    bundle['hashes'] = {'packetSha256': sha(raw['packet']), 'privateKeySha256': sha(raw['key']),
                        'packetManifestSha256': sha(raw['manifest']), 'questionnaireSha256': sha(questions_raw),
                        'datasetSha256': sha(dataset_raw)}
    bundle['files'] = {str(paths[name]): sha(value) for name, value in raw.items()}
    bundle['files'].update({str(packets.DATASET): sha(dataset_raw), str(packets.QUESTIONS): sha(questions_raw)})
    return bundle


def validate_answers(document, bundle):
    require(document.get('version') == ANSWER_VERSION and document.get('packetSha256') == bundle['hashes']['packetSha256'],
            'answer_packet_identity')
    provenance = document.get('provenance', {})
    for field in ('answererId', 'sessionId'):
        require(type(provenance.get(field)) is str and bool(provenance[field].strip()), 'answerer_identity_missing')
    require(provenance.get('newSession') is True and provenance.get('forkTurns') == 'none'
            and provenance.get('inputScope') == 'packet_only', 'new_packet_only_session_required')
    for field in ('sourceOrKeyPreviouslyViewed', 'otherCandidateOutputsViewed', 'priorJudgmentsViewed', 'isQuestionAuthorOrPacketPreparer'):
        require(provenance.get(field) is False, 'answerer_exposure_or_missing_provenance_' + field)
    rows = index_rows(document.get('answers'), bundle['packet'], 'answer')
    for identifier, row in rows.items():
        require(set(row) == ANSWER_FIELDS and type(row.get('answerKo')) is str and type(row.get('uncertaintyKo')) is str,
                'answer_field_contract')
        require(row.get('status') in ANSWER_STATUSES, 'unknown_answer_status')
        answered = row['status'] == 'answered'
        require(bool(row['answerKo'].strip()) if answered else bool(row['uncertaintyKo'].strip()), 'answer_or_uncertainty_required')
        visible = bundle['packet'][identifier]
        check_quotes(row['targetEvidenceQuotes'], visible['candidateTranslationKo'], 'target_evidence', required=answered)
        check_quotes(row['contextEvidenceQuotes'], visible['learningContextKo'], 'korean_context_evidence')
    return rows


def seal(files):
    packets.assert_sealed({'verificationFiles': files})


def save_new(output, result, files):
    output = packets.safe_path(output, ARTIFACT_ROOT)
    require(not output.exists(), 'new_output_file_required')
    seal(files)
    raw = packed(result) + b'\n'
    output.parent.mkdir(parents=True, exist_ok=True)
    packets.write_exclusive(output, raw)
    require(output.read_bytes() == raw, 'output_write_verification_failed')
    seal(files)
    return result


def freeze_answers(directory, answer_path, output):
    bundle = load_bundle(directory)
    answers, evidence = read_artifact(answer_path)
    rows = validate_answers(answers, bundle)
    files = bundle['files'] | {evidence['path']: evidence['sha256']}
    frozen = {'version': FREEZE_VERSION, 'status': 'answers_frozen_before_judgment', 'frozenAtUtc': utc_now(),
        'freezeNonce': secrets.token_hex(32), 'hashes': bundle['hashes'] | {'answersSha256': evidence['sha256']},
        'answersEvidence': evidence, 'answererProvenance': answers['provenance'], 'expectedQuestions': 32,
        'providedAnswerCount': len(rows), 'missingQuestionIds': sorted(set(bundle['packet']) - set(rows)),
        'answerStatusCounts': dict(Counter(row['status'] for row in rows.values())),
        'quoteSubstringsVerified': True, 'semanticAnswersJudged': False, 'judgmentsRead': False,
        'provenanceScope': 'coordinator attestation; not independent proof of session history',
        'files': files, 'freezerCodeSha256': sha(Path(__file__).read_bytes())}
    return save_new(output, frozen, files)


def validate_freeze(receipt, evidence, bundle, answers, answer_evidence):
    require(receipt.get('version') == FREEZE_VERSION and receipt.get('status') == 'answers_frozen_before_judgment', 'freeze_receipt_required')
    require(receipt.get('hashes') == bundle['hashes'] | {'answersSha256': answer_evidence['sha256']}, 'frozen_answer_or_bundle_hash_changed')
    require(receipt.get('answersEvidence', {}).get('path') == answer_evidence['path'], 'frozen_answer_path_differs')
    require(receipt.get('answererProvenance') == answers['provenance'] and receipt.get('judgmentsRead') is False
            and receipt.get('semanticAnswersJudged') is False and receipt.get('quoteSubstringsVerified') is True, 'freeze_stage_contract')
    nonce = receipt.get('freezeNonce')
    require(type(nonce) is str and len(nonce) == 64 and all(char in '0123456789abcdef' for char in nonce), 'freeze_nonce_missing')
    frozen_at = utc(receipt.get('frozenAtUtc'))
    require(frozen_at <= utc(evidence['observedAtUtc']) and receipt.get('freezerCodeSha256') == sha(Path(__file__).read_bytes()),
            'freeze_time_or_supported_code_differs')
    require(receipt.get('files') == bundle['files'] | {answer_evidence['path']: answer_evidence['sha256']}, 'freeze_file_inventory_differs')
    seal(receipt['files'])


def validate_judgments(document, bundle, answer_sha, receipt_sha):
    require(document.get('version') == JUDGMENT_VERSION, 'judgment_version')
    for field, expected in {'packetSha256': bundle['hashes']['packetSha256'],
        'privateKeySha256': bundle['hashes']['privateKeySha256'], 'answersSha256': answer_sha, 'freezeReceiptSha256': receipt_sha}.items():
        require(document.get(field) == expected, 'judgment_hash_binding_' + field)
    judge = document.get('judge', {})
    require(type(judge.get('id')) is str and bool(judge['id'].strip()) and judge.get('kind') in ('assistant', 'human')
            and judge.get('sourceGroundedReview') is True and judge.get('automaticExactMatch') is False, 'source_grounded_judge_required')
    rows = index_rows(document.get('judgments'), bundle['packet'], 'judgment', complete=True)
    for identifier, row in rows.items():
        require(row.get('verdict') in ('correct', 'incorrect', 'unresolved')
                and type(row.get('reasonKo')) is str and bool(row['reasonKo'].strip()), 'explicit_judgment_and_reason_required')
        source = bundle['sources'][bundle['key'][identifier]['sourceId']]
        check_quotes(row.get('sourceEvidenceQuotes'), source['source'], 'source_evidence', required=True)
        check_quotes(row.get('contextEvidenceQuotes', []), source['context'], 'english_context_evidence')
    return rows


def statistics(rows):
    total = len(rows)
    count = Counter(row['effectiveVerdict'] for row in rows)
    return {'count': total, 'correct': count['correct'], 'incorrect': count['incorrect'], 'unresolved': count['unresolved'],
            'missingAnswers': sum(row['missingAnswer'] for row in rows),
            'correctRate': count['correct'] / total if total else None,
            'coverageRate': sum(not row['missingAnswer'] for row in rows) / total if total else None,
            'status': 'evaluated' if total else 'not_evaluated'}


def aggregate(bundle, answers, judgments):
    rows = []
    for identifier in bundle['packet']:
        link, judgment, answer = bundle['key'][identifier], judgments[identifier], answers.get(identifier)
        missing = answer is None
        conflict = (missing or answer['status'] != 'answered') and judgment['verdict'] == 'correct'
        effective = ('incorrect' if missing else 'unresolved' if conflict else judgment['verdict'])
        rows.append({'questionId': identifier, 'sourceId': link['sourceId'], 'domain': link['domain'],
            'criticalQuestion': link['question']['criticalQuestion'], 'annotationVerdict': judgment['verdict'],
            'effectiveVerdict': effective, 'missingAnswer': missing, 'answerStatus': answer['status'] if answer else 'missing',
            'annotationConflict': conflict, 'technicalOverride': 'missing_answer_counts_incorrect' if missing else
                'cannot_credit_nonanswered_status' if conflict else None,
            'reasonKo': judgment['reasonKo'], 'sourceEvidenceQuotes': judgment['sourceEvidenceQuotes'],
            'contextEvidenceQuotes': judgment.get('contextEvidenceQuotes', [])})
    overall = statistics(rows)
    critical = statistics([row for row in rows if row['criticalQuestion']])
    require(overall['count'] == 32 and critical['count'] == 16, 'fixed_denominators_required')
    checks = {'overall95Passed': overall['correct'] >= 31, 'critical100Passed': critical['correct'] == 16,
              'noUnresolvedPassed': overall['unresolved'] == 0, 'coveragePassed': overall['missingAnswers'] == 0,
              'noAnnotationConflictPassed': not any(row['annotationConflict'] for row in rows)}
    return {'overall': overall, 'critical': critical,
        'secondary': statistics([row for row in rows if not row['criticalQuestion']]),
        'byDomain': {domain: statistics([row for row in rows if row['domain'] == domain])
                     for domain in ('general', 'general_context_polysemy')},
        'answerabilityGate': {'passed': all(checks.values()), 'checks': checks,
            'minimumCorrectOf32': 31, 'minimumCriticalCorrectOf16': 16, 'maximumUnresolved': 0,
            'missingAnswersRemainInDenominator': True}, 'rows': rows}


def grade(directory, answer_path, receipt_path, judgment_path, output):
    bundle = load_bundle(directory)
    answer_doc, answer_evidence = read_artifact(answer_path)
    answers = validate_answers(answer_doc, bundle)
    receipt, receipt_evidence = read_artifact(receipt_path)
    validate_freeze(receipt, receipt_evidence, bundle, answer_doc, answer_evidence)
    judgment_doc, judgment_evidence = read_artifact(judgment_path)
    judgments = validate_judgments(judgment_doc, bundle, answer_evidence['sha256'], receipt_evidence['sha256'])
    # The annotations must bind a nonce-bearing receipt that did not exist before
    # freeze. Also check actual local file write order, not just authored timestamps.
    require(utc(receipt['frozenAtUtc']) <= utc(judgment_doc.get('createdAtUtc')) <= utc(judgment_evidence['observedAtUtc']),
            'claimed_judgment_time_out_of_order')
    require(judgment_evidence['mtimeNs'] >= receipt_evidence['mtimeNs'], 'judgment_file_predates_freeze_receipt')
    result = {'version': VERSION, 'createdAtUtc': utc_now(),
        'candidate': bundle['manifest']['candidate'], 'profile': bundle['manifest']['profile'],
        'policyVersion': bundle['questions']['policyId'], 'scope': 'one_model_configuration_and_generation_profile',
        'configuration': bundle['manifest']['configuration'],
        'hashes': bundle['hashes'] | {'answersSha256': answer_evidence['sha256'],
            'freezeReceiptSha256': receipt_evidence['sha256'], 'judgmentsSha256': judgment_evidence['sha256']},
        'answererProvenance': answer_doc['provenance'], 'judge': judgment_doc['judge'],
        'orderingEvidence': {'frozenAtUtc': receipt['frozenAtUtc'], 'freezeReceiptMtimeNs': receipt_evidence['mtimeNs'],
            'annotationsCreatedAtUtc': judgment_doc['createdAtUtc'], 'annotationsMtimeNs': judgment_evidence['mtimeNs'],
            'annotationsObservedAtUtc': judgment_evidence['observedAtUtc'], 'nonceBearingReceiptHashBound': True,
            'localFileWriteOrderVerified': True, 'independentTimestampAttestation': False},
        'evidence': {'answers': answer_evidence, 'freezeReceipt': receipt_evidence, 'judgments': judgment_evidence},
        **aggregate(bundle, answers, judgments),
        'semanticExactMatchUsed': False, 'reasonTruthAutomaticallyVerified': False,
        'quoteValidationScope': 'literal evidence occurrence only; source-grounded semantic judgment supplied by the named judge',
        'meaningGate': {'status': 'not_evaluated', 'passed': False},
        'terminologyGate': {'status': 'not_evaluated', 'passed': False},
        'fullLearningReadiness': {'status': 'not_evaluated', 'passed': False},
        'independentTest': False, 'humanLearningEffectMeasured': False,
        'modelInferencePerformed': False, 'registrationPerformed': False,
        'graderCodeSha256': sha(Path(__file__).read_bytes()), 'packetContractCodeSha256': sha(Path(packets.__file__).read_bytes())}
    files = bundle['files'] | {evidence['path']: evidence['sha256']
                              for evidence in (answer_evidence, receipt_evidence, judgment_evidence)}
    return save_new(output, result, files)


def self_test():
    import copy
    import tempfile
    import unittest
    from unittest.mock import patch

    class SyntheticTests(unittest.TestCase):
        def setUp(self):
            self.temp = tempfile.TemporaryDirectory(prefix='synthetic-answerability-grade-')
            self.addCleanup(self.temp.cleanup)
            self.root = Path(self.temp.name)
            self.packet = self.root / 'packets/one'
            (self.packet / 'answerer').mkdir(parents=True)
            (self.packet / 'private').mkdir()
            self.dataset, self.question_file = self.root / 'source.jsonl', self.root / 'questions.json'
            sources, items, visible, links = [], [], [], []
            for index, identifier in enumerate(packets.IDS):
                context = 'The lamp belongs to Jo.' if index >= 12 else ''
                source = {'id': identifier, 'source': 'The lamp was blue.', 'context': context,
                    'sourceSha256': text_sha('The lamp was blue.'), 'contextSha256': text_sha(context),
                    'domain': 'general' if index < 12 else 'general_context_polysemy'}
                sources.append(source)
                learning = '등은 조의 것이다.' if context else ''
                item = {key: source[key] for key in ('id', 'sourceSha256', 'contextSha256', 'domain')}
                item.update(learningContextKo=learning, learningContextKoSha256=text_sha(learning), questions=[])
                for number in (1, 2):
                    question = {'id': identifier + f'-Q{number}', 'questionKo': '등의 색은 무엇인가?',
                        'criticalQuestion': number == 1, 'contextAloneCompleteAnswer': False,
                        'sampleAnswerKo': '파랗다.', 'requiredFactsKo': ['파란색이라는 의미를 보존한다.']}
                    item['questions'].append(question)
                    anonymous = 'q_' + f'{index * 2 + number:032x}'
                    visible.append({'questionId': anonymous, 'questionKo': question['questionKo'],
                                    'candidateTranslationKo': '등은 파란색이었다.', 'learningContextKo': learning})
                    links.append({'questionId': anonymous, 'sourceId': identifier, 'originalQuestionId': question['id'],
                        'question': question, 'domain': source['domain'], 'sourceSha256': source['sourceSha256'],
                        'contextSha256': source['contextSha256'], 'targetSha256': text_sha('등은 파란색이었다.'),
                        'learningContextKoSha256': text_sha(learning)})
                items.append(item)
            self.dataset.write_bytes(b''.join(packed(row) + b'\n' for row in sources))
            self.questions = {'version': 'general-context-questions-20260911-v1', 'datasetSha256': sha(self.dataset.read_bytes()),
                'policyId': 'synthetic-policy', 'provenance': {'candidateOutputTextViewedDuringAuthoring': False},
                'sourceAudit': {'completed': True}, 'contextPolicy': {'allowedForIds': packets.IDS[12:],
                    'sameContextForProfiles': ['raw', 'contextual']}, 'items': items,
                'scoring': {'minimumCorrectOf32': 31, 'minimumCorrectCriticalOf16': 16}}
            self.question_file.write_bytes(packed(self.questions))
            for obj, name, value in ((packets, 'OUTPUT_ROOT', self.root / 'packets'), (packets, 'QUESTIONS', self.question_file),
                (packets, 'DATASET', self.dataset), (packets, 'QUESTION_SHA', sha(self.question_file.read_bytes())),
                (packets, 'DATASET_SHA', sha(self.dataset.read_bytes())), (sys.modules[__name__], 'ARTIFACT_ROOT', self.root)):
                active = patch.object(obj, name, value)
                active.start()
                self.addCleanup(active.stop)
            packet_raw = b''.join(packed(row) + b'\n' for row in visible)
            key = {'version': packets.VERSION, 'candidate': 'synthetic', 'profile': 'raw', 'rows': links,
                'questionnaireSha256': packets.QUESTION_SHA, 'datasetSha256': packets.DATASET_SHA,
                'packetSha256': sha(packet_raw), 'scoring': self.questions['scoring']}
            key_raw = packed(key)
            manifest = {'version': packets.VERSION, 'status': 'prepared_not_answered_not_scored',
                'expectedItems': 16, 'expectedQuestions': 32, 'candidate': 'synthetic', 'profile': 'raw', 'configuration': {},
                'packet': {'path': 'answerer/packet.jsonl', 'sha256': sha(packet_raw)},
                'privateKey': {'path': 'private/private-key.json', 'sha256': sha(key_raw)},
                'freshAnswerSessionPerPacketRequired': True, 'answererMustNotHaveSeenSourceOrKey': True,
                'preparerOrQuestionAuthorMustNotAnswer': True, 'answersMustBeFrozenBeforeOpeningKey': True}
            (self.packet / 'answerer/packet.jsonl').write_bytes(packet_raw)
            (self.packet / 'private/private-key.json').write_bytes(key_raw)
            (self.packet / 'private/manifest.json').write_bytes(packed(manifest))
            self.answers = {'version': ANSWER_VERSION, 'packetSha256': sha(packet_raw), 'provenance': {
                'answererId': 'synthetic-answerer', 'sessionId': 'synthetic-new-session', 'newSession': True, 'forkTurns': 'none',
                'inputScope': 'packet_only', 'sourceOrKeyPreviouslyViewed': False, 'otherCandidateOutputsViewed': False,
                'priorJudgmentsViewed': False, 'isQuestionAuthorOrPacketPreparer': False},
                'answers': [{'questionId': row['questionId'], 'answerKo': '푸른색이다.', 'targetEvidenceQuotes': ['파란색이었다'],
                             'contextEvidenceQuotes': [], 'status': 'answered', 'uncertaintyKo': ''} for row in visible]}
            self.answer_path, self.receipt_path, self.judgment_path = self.root / 'answers.json', self.root / 'freeze.json', self.root / 'judgments.json'

        def freeze_and_annotate(self):
            self.answer_path.write_bytes(packed(self.answers))
            freeze_answers(self.packet, self.answer_path, self.receipt_path)
            bundle = load_bundle(self.packet)
            self.judgments = {'version': JUDGMENT_VERSION, 'packetSha256': bundle['hashes']['packetSha256'],
                'privateKeySha256': bundle['hashes']['privateKeySha256'], 'answersSha256': sha(self.answer_path.read_bytes()),
                'freezeReceiptSha256': sha(self.receipt_path.read_bytes()), 'createdAtUtc': utc_now(),
                'judge': {'id': 'synthetic-judge', 'kind': 'assistant', 'sourceGroundedReview': True, 'automaticExactMatch': False},
                'judgments': [{'questionId': identifier, 'verdict': 'correct', 'reasonKo': '동일한 색 의미를 보존한다는 합성 주석이다.',
                               'sourceEvidenceQuotes': ['The lamp was blue.'], 'contextEvidenceQuotes': []} for identifier in bundle['packet']]}

        def evaluate(self, output='result.json'):
            self.judgment_path.write_bytes(packed(self.judgments))
            return grade(self.packet, self.answer_path, self.receipt_path, self.judgment_path, self.root / output)

        def test_synonym_annotation_pass_does_not_certify_meaning_or_learning(self):
            self.freeze_and_annotate()
            result = self.evaluate()
            self.assertTrue(result['answerabilityGate']['passed'])
            self.assertEqual(result['overall']['correct'], 32)
            self.assertFalse(result['semanticExactMatchUsed'])
            for field in ('meaningGate', 'terminologyGate', 'fullLearningReadiness'):
                self.assertEqual(result[field], {'status': 'not_evaluated', 'passed': False})

        def test_one_secondary_error_passes_but_one_critical_error_fails(self):
            self.freeze_and_annotate()
            self.judgments['judgments'][1]['verdict'] = 'incorrect'
            result = self.evaluate('secondary.json')
            self.assertTrue(result['answerabilityGate']['passed'])
            self.assertEqual(result['overall']['correctRate'], 31 / 32)
            self.judgments['judgments'][1]['verdict'] = 'correct'
            self.judgments['judgments'][0]['verdict'] = 'incorrect'
            result = self.evaluate('critical.json')
            self.assertFalse(result['answerabilityGate']['passed'])
            self.assertTrue(result['answerabilityGate']['checks']['overall95Passed'])
            self.judgments['judgments'][0]['verdict'] = 'correct'
            self.judgments['judgments'][1]['verdict'] = 'incorrect'
            self.judgments['judgments'][3]['verdict'] = 'incorrect'
            result = self.evaluate('overall30.json')
            self.assertEqual(result['overall']['correct'], 30)
            self.assertFalse(result['answerabilityGate']['checks']['overall95Passed'])
            self.assertTrue(result['answerabilityGate']['checks']['critical100Passed'])

        def test_missing_answer_stays_in_denominator_and_fails_coverage(self):
            self.answers['answers'].pop(1)
            self.freeze_and_annotate()
            self.judgments['judgments'][1]['verdict'] = 'incorrect'
            result = self.evaluate()
            self.assertEqual((result['overall']['count'], result['overall']['incorrect'], result['overall']['missingAnswers']), (32, 1, 1))
            self.assertTrue(result['answerabilityGate']['checks']['overall95Passed'])
            self.assertFalse(result['answerabilityGate']['checks']['coveragePassed'])
            self.assertFalse(result['answerabilityGate']['passed'])

        def test_unresolved_answer_and_annotation_cannot_receive_credit(self):
            self.answers['answers'][1].update(status='unresolved', uncertaintyKo='추론 개입 여부가 미확정이다.')
            self.freeze_and_annotate()
            result = self.evaluate()
            self.assertEqual(result['overall']['unresolved'], 1)
            self.assertFalse(result['answerabilityGate']['passed'])
            self.judgments['judgments'][1]['verdict'] = 'unresolved'
            result = self.evaluate('explicit-unresolved.json')
            self.assertFalse(result['answerabilityGate']['checks']['noUnresolvedPassed'])

        def test_quote_substrings_and_fresh_answerer_provenance_are_required(self):
            bundle = load_bundle(self.packet)
            for field in ('targetEvidenceQuotes', 'contextEvidenceQuotes'):
                document = copy.deepcopy(self.answers)
                document['answers'][0][field] = ['존재하지 않는 구절']
                with self.assertRaisesRegex(ValueError, 'not_exact_substring'):
                    validate_answers(document, bundle)
            for field, value in (('forkTurns', 'all'), ('sourceOrKeyPreviouslyViewed', True), ('priorJudgmentsViewed', True),
                                 ('otherCandidateOutputsViewed', True), ('isQuestionAuthorOrPacketPreparer', True)):
                document = copy.deepcopy(self.answers)
                document['provenance'][field] = value
                with self.assertRaises(ValueError):
                    validate_answers(document, bundle)

        def test_duplicate_unknown_and_missing_judgments_are_refused(self):
            bundle = load_bundle(self.packet)
            for value in ([self.answers['answers'][0]] * 2, [self.answers['answers'][0] | {'questionId': 'unknown'}]):
                with self.assertRaises(ValueError):
                    validate_answers(self.answers | {'answers': value}, bundle)
            self.freeze_and_annotate()
            original = copy.deepcopy(self.judgments['judgments'])
            for value in (original[:-1], [original[0]] * 32, [original[0] | {'questionId': 'unknown'}, *original[1:]]):
                self.judgments['judgments'] = value
                with self.assertRaises(ValueError):
                    self.evaluate()

        def test_changed_answer_packet_key_and_fake_source_evidence_are_refused(self):
            self.freeze_and_annotate()
            before = self.answer_path.read_bytes()
            self.answer_path.write_bytes(before + b' ')
            with self.assertRaisesRegex(ValueError, 'frozen_answer_or_bundle_hash_changed'):
                self.evaluate()
            self.answer_path.write_bytes(before)
            self.judgments['judgments'][0]['sourceEvidenceQuotes'] = ['This is not in the source.']
            with self.assertRaisesRegex(ValueError, 'source_evidence_not_exact_substring'):
                self.evaluate()
            path = self.packet / 'private/private-key.json'
            path.write_bytes(path.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'packet_or_private_key_hash_changed'):
                load_bundle(self.packet)

        def test_actual_receipt_binding_order_and_no_overwrite(self):
            self.freeze_and_annotate()
            original_sha = self.judgments['freezeReceiptSha256']
            self.judgments['freezeReceiptSha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'judgment_hash_binding'):
                self.evaluate()
            self.judgments['freezeReceiptSha256'] = original_sha
            self.judgment_path.write_bytes(packed(self.judgments))
            old = self.receipt_path.stat().st_mtime_ns - 10_000_000_000
            os.utime(self.judgment_path, ns=(old, old))
            with self.assertRaisesRegex(ValueError, 'judgment_file_predates_freeze_receipt'):
                grade(self.packet, self.answer_path, self.receipt_path, self.judgment_path, self.root / 'result.json')
            self.evaluate()
            with self.assertRaisesRegex(ValueError, 'new_output_file_required'):
                self.evaluate()
            with self.assertRaisesRegex(ValueError, 'new_output_file_required'):
                freeze_answers(self.packet, self.answer_path, self.receipt_path)

        def test_zero_denominator_is_unassessed_not_perfect(self):
            value = statistics([])
            self.assertIsNone(value['correctRate'])
            self.assertIsNone(value['coverageRate'])
            self.assertEqual(value['status'], 'not_evaluated')

        def test_no_answers_still_yields_32_incorrect_and_zero_coverage(self):
            self.answers['answers'] = []
            self.freeze_and_annotate()
            result = self.evaluate()
            self.assertEqual((result['overall']['incorrect'], result['overall']['missingAnswers']), (32, 32))
            self.assertEqual(result['overall']['coverageRate'], 0)
            self.assertEqual(result['critical']['incorrect'], 16)
            self.assertFalse(result['answerabilityGate']['passed'])

        def test_coherently_rehashed_key_cannot_change_frozen_critical_flags(self):
            path = self.packet / 'private/private-key.json'
            key = parse(path.read_bytes())
            key['rows'][0]['question']['criticalQuestion'] = False
            path.write_bytes(packed(key))
            manifest_path = self.packet / 'private/manifest.json'
            manifest = parse(manifest_path.read_bytes())
            manifest['privateKey']['sha256'] = sha(path.read_bytes())
            manifest_path.write_bytes(packed(manifest))
            with self.assertRaisesRegex(ValueError, 'private_question_changed'):
                load_bundle(self.packet)

    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SyntheticTests))
    return 0 if result.wasSuccessful() else 1


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--self-test', action='store_true')
    sub = cli.add_subparsers(dest='command')
    for command in ('freeze-answers', 'grade'):
        parser = sub.add_parser(command)
        parser.add_argument('--packet-directory', required=True, type=Path)
        parser.add_argument('--answers', required=True, type=Path)
        parser.add_argument('--output', required=True, type=Path)
        if command == 'grade':
            parser.add_argument('--freeze-receipt', required=True, type=Path)
            parser.add_argument('--judgments', required=True, type=Path)
    args = cli.parse_args(argv)
    if args.self_test:
        require(args.command is None, 'self_test_must_be_separate')
        return self_test()
    if args.command is None:
        cli.error('choose freeze-answers or grade, or use --self-test')
    try:
        if args.command == 'freeze-answers':
            result = freeze_answers(args.packet_directory, args.answers, args.output)
            report = {'status': result['status'], 'providedAnswerCount': result['providedAnswerCount'],
                      'missingAnswerCount': len(result['missingQuestionIds']), 'frozenAtUtc': result['frozenAtUtc'],
                      'receiptSha256': sha(args.output.read_bytes())}
        else:
            result = grade(args.packet_directory, args.answers, args.freeze_receipt, args.judgments, args.output)
            report = {key: result[key] for key in ('overall', 'critical', 'answerabilityGate', 'fullLearningReadiness')}
        print(packed(report).decode('utf-8'))
        return 0
    except Exception as error:
        print(packed({'status': 'refused', 'errorType': type(error).__name__,
                      'code': str(error) if type(error) is ValueError else 'verification_failed'}).decode('utf-8'))
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
    raise SystemExit(main())
