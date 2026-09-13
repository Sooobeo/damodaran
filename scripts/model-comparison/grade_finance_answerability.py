"""Freeze Korean-only answers before independent source-grounded manual judgments.

Aggregates supplied judgments, never semantic keywords or sample-answer matching.
All outputs are new files within finance-answerability; no inference or app writes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import re
import secrets
import sys

import prepare_finance_answerability as p

VERSION = 'finance24-answerability-grade-v1'
ANSWER_VERSION = 'finance24-korean-answers-v1'
JUDGMENT_VERSION = 'finance24-answerability-judgments-v1'
FREEZE_VERSION = 'finance24-answer-freeze-v1'
ANSWER_FIELDS = {'questionId', 'answerKo', 'targetEvidenceQuotes', 'contextEvidenceQuotes', 'status', 'uncertaintyKo'}
ANSWER_STATUSES = {'answered', 'insufficient_information', 'ambiguous', 'unresolved'}
require, packed, sha, parse = p.require, p.packed, p.sha, p.parse


def index(rows, allowed, code, complete=False):
    require(type(rows) is list and all(type(row) is dict for row in rows), code + '_rows_required')
    result = {}
    for row in rows:
        identifier = row.get('questionId')
        require(type(identifier) is str and identifier in allowed, code + '_unknown_id')
        require(identifier not in result, code + '_duplicate_id')
        result[identifier] = row
    require(not complete or set(result) == set(allowed), code + '_missing_id')
    return result


def read_artifact(path):
    path = p.safe_path(path, p.OUTPUT_ROOT)
    require(path.is_file() and path.stat().st_size <= 16 * 1024 ** 2, 'missing_or_large_artifact')
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    require(before.st_mtime_ns == after.st_mtime_ns and before.st_size == after.st_size, 'artifact_changed_during_read')
    return parse(raw), {'path': str(path), 'sha256': sha(raw), 'mtimeNs': after.st_mtime_ns, 'observedAtUtc': p.now()}


def load_bundle(directory):
    directory = p.safe_path(directory, p.OUTPUT_ROOT)
    paths = {'packet': directory / 'answerer/packet.jsonl', 'key': directory / 'private/private-key.json',
             'manifest': directory / 'private/manifest.json'}
    evidence = p.Evidence()
    raw = {name: evidence.raw(path) for name, path in paths.items()}
    manifest, key = parse(raw['manifest']), parse(raw['key'])
    require(manifest.get('version') == p.VERSION and manifest.get('status') == 'prepared_not_answered_not_scored',
            'prepared_packet_manifest_required')
    require(manifest.get('packetSha256') == key.get('packetSha256') == sha(raw['packet'])
            and manifest.get('privateKeySha256') == sha(raw['key']), 'packet_or_private_key_hash_changed')
    require(manifest.get('codeFiles') == p.code_files(), 'packet_contract_code_changed')
    verified = p.load_verified(manifest['run'], manifest['cohort'], manifest['producerVersion'])
    require(manifest.get('files') == verified['files'] and packed(manifest.get('runMetadata')) == packed(verified['summary'])
            and packed(manifest.get('installationMetadata')) == packed(verified['installation'])
            and manifest.get('candidate') == verified['candidate']
            and manifest.get('questionChronology') == verified['chronology'], 'producer_evidence_changed')
    count = len(p.COHORTS[verified['cohort']])
    require(manifest.get('expectedItems') == count and manifest.get('expectedQuestions') == 2 * count
            and manifest.get('freshUnexposedAnswererRequired') is True
            and manifest.get('freezeAnswersBeforeJudgmentsRequired') is True, 'packet_administration_differs')
    packet = [parse(line) for line in raw['packet'].splitlines() if line.strip()]
    p.validate_packet(packet, key, verified)
    evidence.unchanged()
    hashes = {'packetSha256': sha(raw['packet']), 'privateKeySha256': sha(raw['key']),
              'packetManifestSha256': sha(raw['manifest']), 'questionnaireSha256': p.QUESTION_SHA,
              'sourcePacketSha256': p.DATASET_SHA, 'policySha256': p.POLICY_SHA}
    return {'packet': {r['questionId']: r for r in packet}, 'key': {r['questionId']: r for r in key['rows']},
            'sources': {r['id']: r for r in verified['sources']}, 'manifest': manifest, 'hashes': hashes,
            'files': verified['files'] | {str(path): evidence.files[path] for path in paths.values()}}


def quotes(value, text, code, required=False):
    require(type(value) is list and (not required or value), code + '_quotes_required')
    require(all(type(q) is str and q.strip() and q in text for q in value), code + '_quote_not_in_text')


def korean(value):
    return type(value) is str and 0 < len(value.strip()) <= 12000 and re.search('[가-힣]', value) is not None


def validate_answers(document, bundle):
    require(document.get('version') == ANSWER_VERSION and document.get('packetSha256') == bundle['hashes']['packetSha256'],
            'answer_packet_identity')
    provenance = document.get('provenance', {})
    require(all(type(provenance.get(k)) is str and provenance[k].strip() for k in ('answererId', 'sessionId')),
            'answerer_identity_required')
    require(provenance.get('newSession') is True and provenance.get('forkTurns') == 'none'
            and provenance.get('inputScope') == 'packet_only', 'fresh_packet_only_answerer_required')
    for field in ('sourceOrKeyPreviouslyViewed', 'otherCandidateOutputsViewed', 'priorJudgmentsViewed',
                  'isQuestionAuthorOrPacketPreparer'):
        require(provenance.get(field) is False, 'answerer_exposure_or_missing_provenance')
    rows = index(document.get('answers'), bundle['packet'], 'answer')
    for identifier, row in rows.items():
        require(set(row) == ANSWER_FIELDS and row['status'] in ANSWER_STATUSES
                and type(row['answerKo']) is str and type(row['uncertaintyKo']) is str, 'answer_contract')
        answered = row['status'] == 'answered'
        require(korean(row['answerKo']) if answered else korean(row['uncertaintyKo']), 'korean_answer_or_uncertainty_required')
        visible = bundle['packet'][identifier]
        quotes(row['targetEvidenceQuotes'], visible['candidateTranslationKo'], 'target', required=answered)
        quotes(row['contextEvidenceQuotes'], visible['learningContextKo'], 'korean_context')
    return rows


def save_new(output, value, files):
    output = p.safe_path(output, p.OUTPUT_ROOT)
    require(not output.exists(), 'new_output_file_required')
    p.seal(files)
    output.parent.mkdir(parents=True, exist_ok=True)
    p.write_exclusive(output, packed(value) + b'\n')
    p.seal(files)
    return value


def freeze_answers(directory, answer_path, output):
    bundle = load_bundle(directory)
    document, evidence = read_artifact(answer_path)
    answers = validate_answers(document, bundle)
    files = bundle['files'] | {evidence['path']: evidence['sha256']}
    receipt = {'version': FREEZE_VERSION, 'status': 'answers_frozen_before_judgment', 'frozenAtUtc': p.now(),
        'freezeNonce': secrets.token_hex(32), 'hashes': bundle['hashes'] | {'answersSha256': evidence['sha256']},
        'answerPath': evidence['path'], 'answererProvenance': document['provenance'], 'files': files,
        'expectedQuestions': len(bundle['packet']), 'providedAnswers': len(answers),
        'missingQuestionIds': sorted(set(bundle['packet']) - set(answers)),
        'answerStatusCounts': dict(Counter(r['status'] for r in answers.values())),
        'judgmentsRead': False, 'semanticAnswersJudged': False, 'freezerCodeSha256': sha(Path(__file__).read_bytes()),
        'provenanceScope': 'coordinator attestation; session history is not independently proven'}
    return save_new(output, receipt, files)


def validate_freeze(receipt, bundle, document, evidence):
    require(receipt.get('version') == FREEZE_VERSION and receipt.get('status') == 'answers_frozen_before_judgment', 'freeze_receipt_required')
    require(receipt.get('hashes') == bundle['hashes'] | {'answersSha256': evidence['sha256']}
            and receipt.get('answerPath') == evidence['path'], 'frozen_answers_or_bundle_changed')
    require(receipt.get('answererProvenance') == document['provenance'] and receipt.get('judgmentsRead') is False
            and receipt.get('semanticAnswersJudged') is False
            and re.fullmatch(r'[0-9a-f]{64}', receipt.get('freezeNonce', ''))
            and receipt.get('freezerCodeSha256') == sha(Path(__file__).read_bytes()), 'freeze_contract_differs')
    require(receipt.get('files') == bundle['files'] | {evidence['path']: evidence['sha256']}, 'freeze_inventory_changed')
    p.seal(receipt['files'])


def validate_judgments(document, bundle, answers_document, answer_sha, receipt_sha):
    require(document.get('version') == JUDGMENT_VERSION, 'judgment_version')
    for key, digest in {'packetSha256': bundle['hashes']['packetSha256'], 'privateKeySha256': bundle['hashes']['privateKeySha256'],
                        'answersSha256': answer_sha, 'freezeReceiptSha256': receipt_sha}.items():
        require(document.get(key) == digest, 'judgment_hash_binding')
    judge = document.get('judge', {})
    require(type(judge.get('id')) is str and judge['id'].strip() and judge.get('kind') in ('assistant', 'human')
            and type(judge.get('sessionId')) is str and judge['sessionId'].strip()
            and judge['id'] != answers_document['provenance']['answererId']
            and judge['sessionId'] != answers_document['provenance']['sessionId']
            and judge.get('sourceGroundedReview') is True and judge.get('automaticExactMatch') is False
            and judge.get('independentOfAnswerer') is True, 'independent_manual_judge_required')
    rows = index(document.get('judgments'), bundle['packet'], 'judgment', complete=True)
    for identifier, row in rows.items():
        require(row.get('verdict') in ('correct', 'incorrect', 'unresolved') and korean(row.get('reasonKo')),
                'explicit_judgment_and_korean_reason_required')
        source = bundle['sources'][bundle['key'][identifier]['sourceId']]
        quotes(row.get('sourceEvidenceQuotes'), source['source'], 'source', required=True)
        quotes(row.get('contextEvidenceQuotes'), source['context'], 'english_context')
    return rows


def statistics(rows):
    n = len(rows)
    counts = Counter(r['effectiveVerdict'] for r in rows)
    return {'count': n, 'correct': counts['correct'], 'incorrect': counts['incorrect'], 'unresolved': counts['unresolved'],
            'missingAnswers': sum(r['missingAnswer'] for r in rows), 'correctRate': counts['correct'] / n if n else None,
            'coverageRate': sum(not r['missingAnswer'] for r in rows) / n if n else None,
            'status': 'evaluated' if n else 'not_evaluated'}


def aggregate(bundle, answers, judgments):
    cohort = bundle['manifest']['cohort']
    rows = []
    for identifier, link in bundle['key'].items():
        answer, judgment = answers.get(identifier), judgments[identifier]
        missing = answer is None
        conflict = judgment['verdict'] == 'correct' and (missing or answer['status'] != 'answered')
        effective = 'incorrect' if missing else 'unresolved' if conflict else judgment['verdict']
        rows.append({'questionId': identifier, 'sourceId': link['sourceId'],
                     'criticalQuestion': link['question']['criticalQuestion'], 'effectiveVerdict': effective,
                     'annotationVerdict': judgment['verdict'], 'missingAnswer': missing, 'annotationConflict': conflict,
                     'answerStatus': answer['status'] if answer else 'missing', 'reasonKo': judgment['reasonKo']})
    overall = statistics(rows)
    core = statistics([r for r in rows if r['criticalQuestion']])
    expected = len(p.COHORTS[cohort])
    require(overall['count'] == expected * 2 and core['count'] == expected, 'fixed_cohort_denominators_required')
    minimum = (expected * 2 * 95 + 99) // 100
    checks = {'overall95Passed': overall['correct'] >= minimum, 'core100Passed': core['correct'] == expected,
              'coveragePassed': overall['missingAnswers'] == 0, 'noUnresolvedPassed': overall['unresolved'] == 0,
              'noAnnotationConflictPassed': not any(r['annotationConflict'] for r in rows)}
    return {'cohort': cohort, 'overall': overall, 'core': core, 'secondary': statistics([r for r in rows if not r['criticalQuestion']]),
            'answerStatusCounts': dict(Counter(r['answerStatus'] for r in rows)),
            'answerabilityGate': {'passed': all(checks.values()), 'checks': checks, 'minimumCorrect': minimum,
                'minimumCoreCorrect': expected, 'maximumUnresolved': 0, 'missingAnswersRemainInDenominator': True}, 'rows': rows}


def grade(directory, answer_path, receipt_path, judgment_path, output):
    bundle = load_bundle(directory)
    answers_document, answer_evidence = read_artifact(answer_path)
    answers = validate_answers(answers_document, bundle)
    receipt, receipt_evidence = read_artifact(receipt_path)
    validate_freeze(receipt, bundle, answers_document, answer_evidence)
    document, judgment_evidence = read_artifact(judgment_path)
    judgments = validate_judgments(document, bundle, answers_document, answer_evidence['sha256'], receipt_evidence['sha256'])
    require(p.utc(receipt['frozenAtUtc']) <= p.utc(document.get('createdAtUtc')) <= p.utc(judgment_evidence['observedAtUtc']),
            'judgment_time_out_of_order')
    require(judgment_evidence['mtimeNs'] >= receipt_evidence['mtimeNs'], 'judgment_file_predates_freeze')
    files = bundle['files'] | {r['path']: r['sha256'] for r in (answer_evidence, receipt_evidence, judgment_evidence)}
    result = {'version': VERSION, 'createdAtUtc': p.now(), 'policyId': 'learning-readiness-20260911-v1',
        'scope': 'one_model_configuration_one_cohort', 'candidate': bundle['manifest']['candidate'],
        'producerVersion': bundle['manifest']['producerVersion'], 'runMetadata': bundle['manifest']['runMetadata'],
        'questionChronology': bundle['manifest']['questionChronology'],
        'hashes': bundle['hashes'] | {'answersSha256': answer_evidence['sha256'], 'freezeReceiptSha256': receipt_evidence['sha256'],
                                    'judgmentsSha256': judgment_evidence['sha256']},
        'answererProvenance': answers_document['provenance'], 'judge': document['judge'],
        'orderingEvidence': {'frozenAtUtc': receipt['frozenAtUtc'], 'judgmentsCreatedAtUtc': document['createdAtUtc'],
            'receiptMtimeNs': receipt_evidence['mtimeNs'], 'judgmentsMtimeNs': judgment_evidence['mtimeNs'],
            'nonceBearingReceiptHashBound': True, 'independentTimestampAttestation': False},
        **aggregate(bundle, answers, judgments), 'files': files, 'graderCodeSha256': sha(Path(__file__).read_bytes()),
        'semanticKeywordMatchingUsed': False, 'semanticTruthAutomaticallyVerified': False,
        'quoteValidationScope': 'literal occurrence only; meanings are manually judged against frozen source criteria',
        'meaningGate': {'status': 'not_evaluated', 'passed': False}, 'terminologyGate': {'status': 'not_evaluated', 'passed': False},
        'fullLearningReadiness': {'status': 'not_evaluated', 'passed': False},
        'humanLearningEffectMeasured': False, 'independentTest': False, 'modelInferencePerformed': False, 'registrationPerformed': False}
    return save_new(output, result, files)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest='command', required=True)
    for name in ('freeze-answers', 'grade'):
        command = commands.add_parser(name)
        command.add_argument('--packet-directory', required=True, type=Path)
        command.add_argument('--answers', required=True, type=Path)
        command.add_argument('--output', required=True, type=Path)
        if name == 'grade':
            command.add_argument('--freeze-receipt', required=True, type=Path)
            command.add_argument('--judgments', required=True, type=Path)
    args = cli.parse_args()
    try:
        if args.command == 'freeze-answers':
            result = freeze_answers(args.packet_directory, args.answers, args.output)
            report = {k: result[k] for k in ('status', 'expectedQuestions', 'providedAnswers')}
        else:
            result = grade(args.packet_directory, args.answers, args.freeze_receipt, args.judgments, args.output)
            report = {k: result[k] for k in ('cohort', 'overall', 'core', 'answerabilityGate', 'fullLearningReadiness')}
        print(packed(report).decode())
        return 0
    except Exception as error:
        print(packed({'status': 'refused', 'errorType': type(error).__name__,
                      'code': str(error) if type(error) is ValueError else 'verification_failed'}).decode())
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
