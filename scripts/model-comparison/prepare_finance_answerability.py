"""Prepare one finance24 cohort's blinded Korean-only packet, without inference.

Only explicit TG27 v5 / TG27 v4 / Hy30 v4 recorded producers are supported.
No model weights, native inference runtime or database are opened. Producer code
and the small Python executable are hashed as evidence, never executed/imported.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys

import linguistic_screen as screen
import reading_review_common_v5 as reading
import summarize_linguistic_reviews_v5 as development
import v4_review_evidence as v4
import v5_review_evidence as v5

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = ROOT / '.training/quality-evaluation/finance-answerability'
DATASET = OUTPUT_ROOT / 'source-only-v1/source-only.jsonl'
QUESTIONS = DATASET.with_name('assistant-questionnaire-v2.json')
DATASET_SHA = '2ed90d5a9f6250020ba0e73c1fae5a77ecd9f63032badf9ddee7b9a1668432bb'
QUESTION_SHA = 'a946103f3068b64e54107f4b96a01834857b146d2190e7b5f5e009b9cef1d75b'
POLICY = ROOT / 'content/model-comparison/LEARNING_READINESS_BASELINE_20260911.json'
POLICY_SHA = '18aca6dd384b02a0c83e81b1d644961422ed7ec034abdb1b8c92394e0e2c8928'
VERSION = 'finance24-korean-answer-packet-v1'
COHORTS = {'dev18': [f'LDEV26-{n:03d}' for n in range(1, 19)],
           'reading6': [f'REAL26-{n:03d}' for n in range(1, 7)]}
INPUTS = {'dev18': ROOT / 'content/model-comparison/linguistic-dev-20260910.jsonl',
          'reading6': ROOT / 'content/model-comparison/real-reading-check-20260910/sources.jsonl'}
INPUT_SHAS = {'dev18': 'b8a2b91802e36d96654631a9965566f774e050ea4139ca7270d9f84d2a916848',
              'reading6': 'becd55204f1533adf68c0512638f17db676d4848a175367c841ed71fca34f8d0'}
SUPPORTED = {v5.VERSION, 'translategemma-large-screen-v4', 'hymt30-development-screen-v4'}
VISIBLE_FIELDS = {'questionId', 'questionKo', 'candidateTranslationKo', 'learningContextKo'}
MODELS = {'tg27': ('translategemma-27b-q4', '7f1e67c4ecfec676b38c1ea2ef85c46fafe2f02d3c050fb9540e51787405d8a3'),
          'hy30': ('hy-mt2-30b-a3b-q4', 'bb44b11bb0f7cd3d1321645b41e911cd3de2e473731227fc6bf37aa18b543f88')}


def require(value, code):
    if not value:
        raise ValueError(code)


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def text_sha(value):
    return sha(value.encode('utf-8'))


def parse(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: require(False, 'nonfinite_json'))


def utc(value):
    require(type(value) is str, 'utc_timestamp_required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None and result.utcoffset().total_seconds() == 0, 'utc_timestamp_required')
    return result


def now():
    return datetime.now(timezone.utc).isoformat()


def safe_path(path, boundary=None):
    boundary = ROOT if boundary is None else boundary
    require('..' not in Path(path).parts, 'parent_path_refused')
    absolute = Path(os.path.abspath(path))
    for item in (absolute, *absolute.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            require(not item.is_symlink() and not (getattr(info, 'st_file_attributes', 0)
                    & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)), 'linked_path_refused')
    result = absolute.resolve()
    require(result.is_relative_to(Path(boundary).resolve()) and result != Path(boundary).resolve(), 'path_outside_boundary')
    return result


class Evidence:
    def __init__(self):
        self.root, self.files = ROOT, {}

    def raw(self, path, expected=None):
        path = safe_path(path)
        require(path.is_file() and path.stat().st_size <= 16 * 1024 ** 2, 'missing_or_large_evidence_file')
        raw = path.read_bytes()
        digest = sha(raw)
        require(expected is None or digest == expected, 'evidence_hash_mismatch')
        require(path not in self.files or self.files[path] == digest, 'evidence_changed_during_read')
        self.files[path] = digest
        return raw

    def read(self, path, expected=None, jsonl=False):
        raw = self.raw(path, expected)
        return [parse(line) for line in raw.splitlines() if line.strip()] if jsonl else parse(raw)

    def unchanged(self):
        seal({str(path): value for path, value in self.files.items()})


def seal(files):
    require(type(files) is dict and bool(files), 'evidence_inventory_required')
    for path, digest in files.items():
        require(v5.is_sha(digest) and v4.stream_hash(safe_path(path)) == digest, 'sealed_evidence_changed')


def code_files():
    names = ('prepare_finance_answerability.py', 'grade_finance_answerability.py',
             'linguistic_screen.py', 'reading_check_input.py', 'reading_review_common_v5.py',
             'summarize_linguistic_reviews_v5.py', 'v3_review_evidence.py', 'v4_review_evidence.py', 'v5_review_evidence.py')
    return {str(Path(__file__).with_name(name).resolve()): sha(Path(__file__).with_name(name).read_bytes()) for name in names}


def load_frozen(evidence):
    sources = evidence.read(DATASET, DATASET_SHA, jsonl=True)
    questions = evidence.read(QUESTIONS, QUESTION_SHA)
    policy = evidence.read(POLICY, POLICY_SHA)
    ids = COHORTS['dev18'] + COHORTS['reading6']
    require([s.get('id') for s in sources] == ids and [q.get('id') for q in questions.get('rows', [])] == ids,
            'frozen_24_coverage_required')
    require(questions.get('version') == 'finance24-source-questionnaire-v2'
            and questions.get('sourcePacketSha256') == DATASET_SHA, 'questionnaire_identity_differs')
    author = questions.get('author', {})
    require(author.get('newSession') is True and author.get('forkTurns') == 'none' and author.get('sourceOnly') is True
            and author.get('candidateOutputsViewed') is False and author.get('referencesOrPriorJudgmentsViewed') is False
            and questions.get('humanReviewed') is False and questions.get('finalHoldout') is False, 'question_author_provenance')
    utc(questions['createdAtUtc'])
    require(policy.get('policyId') == 'learning-readiness-20260911-v1'
            and policy['translationGates']['questionAnswerability']['minimumRate'] == 0.95
            and policy['translationGates']['coreQuestionAnswerability']['minimumRate'] == 1, 'policy_contract_differs')
    for source, item in zip(sources, questions['rows'], strict=True):
        require(source['id'] in COHORTS[source['cohort']] and item.get('cohort') == source['cohort'], 'source_cohort_differs')
        for field in ('source', 'context'):
            require(type(source.get(field)) is str and source.get(field + 'Sha256') == item.get(field + 'Sha256')
                    == text_sha(source[field]), 'source_question_hash_differs')
        require(item.get('learningContextKo') == '' and item.get('learningContextEvidenceQuotes') == [],
                'v2_has_no_allowed_learning_context')
        require([q.get('id') for q in item.get('questions', [])] == [source['id'] + '-Q1', source['id'] + '-Q2'],
                'fixed_question_coverage')
        for index, question in enumerate(item['questions']):
            require(question.get('criticalQuestion') is (index == 0) and question.get('contextAloneCompleteAnswer') is False
                    and type(question.get('questionKo')) is str and re.search('[가-힣]', question['questionKo'])
                    and question.get('requiredFactsKo') and question.get('sampleAnswerKo'), 'question_contract_differs')
            for field, text in (('evidenceSourceQuotes', source['source']), ('evidenceContextQuotes', source['context'])):
                quotes = question.get(field)
                require(type(quotes) is list and all(type(q) is str and q.strip() and q in text for q in quotes),
                        'question_source_evidence_differs')
    return sources, questions


def validate_prediction_rows(predictions, sources, summary):
    require([row.get('id') for row in predictions] == [row['id'] for row in sources], 'full_cohort_prediction_coverage')
    hy = summary['version'] == 'hymt30-development-screen-v4'
    limit, terminals = (4096, {120025}) if hy else (768, {1, 106})
    for row, source in zip(predictions, sources, strict=True):
        require(row.get('status') == 'completed' and row.get('stopType') == 'eos'
                and row.get('truncated') is False and row.get('outputLimitReached') is False, 'incomplete_prediction')
        for field in ('sourceSha256', 'contextSha256'):
            require(row.get(field) == source[field], 'prediction_input_hash_differs')
        target = row.get('translation')
        require(type(target) is str and target.strip() and row.get('targetSha256') == text_sha(target), 'prediction_target_hash_differs')
        require('\ufffd' not in target and not re.search(r'<\|[^\n>]*\|>|<｜[^\n>]*｜>|</?(?:think|answer)>', target),
                'invalid_target_control_or_unicode')
        tokens = row.get('outputTokenIds')
        require(type(tokens) is list and 0 < len(tokens) < limit and all(type(n) is int and n >= 0 for n in tokens)
                and tokens[-1] in terminals and row.get('generatedTokens') == len(tokens)
                and row.get('outputTokenIdsSha256') == sha(packed(tokens)), 'prediction_token_evidence_differs')
        require(v5.is_sha(row.get('promptSha256')), 'prompt_hash_missing')


def load_verified(run, cohort, producer_version):
    require(cohort in COHORTS and producer_version in SUPPORTED, 'unsupported_cohort_or_explicit_producer_version')
    run = safe_path(run, ROOT / '.training/comparisons')
    evidence = Evidence()
    sources, questions = load_frozen(evidence)
    selected = [s for s in sources if s['cohort'] == cohort]
    summary = evidence.read(run / 'summary.json')
    require(summary.get('version') == producer_version and summary.get('status') == 'completed', 'explicit_completed_producer_required')
    clean, identity = screen.read_screen(INPUTS[cohort])
    evidence.raw(INPUTS[cohort], INPUT_SHAS[cohort])
    evidence.raw(identity['manifestPath'], identity['manifestSha256'])
    for name, expected in (identity.get('inputFiles', {}) | identity.get('readerCodeFiles', {})).items():
        evidence.raw(name, expected)
    require([{k: r[k] for k in ('id', 'sourceSha256', 'contextSha256')} for r in clean]
            == [{k: r[k] for k in ('id', 'sourceSha256', 'contextSha256')} for r in selected], 'projection_producer_input_differs')
    if cohort == 'reading6':
        evidence.raw(run / 'predictions.jsonl')
        producer = reading.producer(run, clean, identity, evidence, expected={
            'directory': str(run), 'summarySha256': evidence.files[run / 'summary.json'],
            'predictionsSha256': evidence.files[run / 'predictions.jsonl']})
        predictions = producer['predictions']
    else:
        predictions = evidence.read(run / 'predictions.jsonl', jsonl=True)
        development.check_producer_input(summary, evidence.files[run / 'predictions.jsonl'], identity)
        v5.check_artifacts(run, summary, predictions, evidence)
    record = summary if producer_version == 'hymt30-development-screen-v4' else summary['input']
    require(all(packed(record.get(k)) == packed(value) for k, value in identity.items()), 'complete_input_identity_differs')
    validate_prediction_rows(predictions, selected, summary)
    candidate = 'hy30' if producer_version == 'hymt30-development-screen-v4' else 'tg27'
    directory, model_sha = MODELS[candidate]
    installation = evidence.read(ROOT / '.training/comparisons' / directory / 'installation-manifest.json',
                                 summary['installationManifestSha256'])
    declared_model = summary.get('model', {}).get('sha256') if candidate == 'hy30' else summary.get('modelSha256')
    require(declared_model == installation.get('model', {}).get('sha256') == model_sha, 'model_installation_hash_differs')
    if candidate == 'hy30':
        require(summary.get('modelRevision') == installation.get('modelRevision')
                and summary.get('runtimeRevision') == installation.get('runtimeRevision'), 'model_runtime_revision_differs')
    else:
        require(summary.get('modelSize') == installation.get('modelSize') == '27b'
                and packed(summary.get('runtimeFiles')) == packed(installation.get('runtimeFiles'))
                and summary.get('templateSha256') == installation.get('templateSha256'), 'model_runtime_template_differs')
        require(packed(summary.get('settings')) == packed(v5.TG_SETTINGS), 'tg_settings_differs')
    if candidate == 'hy30':
        # The documented Hy30-v4 summary predates TG's processing flags.
        require(summary.get('humanReviewed') is False and summary.get('applicationChanged') is False
                and all(r.get('postProcessingApplied') is False and r.get('humanReviewed') is False
                        and r.get('outputIntegrityPassed') is True for r in predictions), 'hy30_processing_contract')
    else:
        for field in ('postProcessingApplied', 'translationMemoryApplied', 'appDeploymentPerformed', 'humanReviewed'):
            require(summary.get(field) is False, 'unapproved_processing_' + field)
    codes = summary.get('codeHashes')
    require(type(codes) is dict and bool(codes), 'producer_code_inventory_missing')
    runner_name, runner_sha = {
        v5.VERSION: ('run_translategemma_large_v5.py', v5.SUPPORTED_CODE['scripts/model-comparison/run_translategemma_large_v5.py']),
        'translategemma-large-screen-v4': ('run_translategemma_large_v4.py', v5.BASE_V4_SHA256),
        'hymt30-development-screen-v4': ('run_hymt30_v4.py', 'b9cfd5dadaa98283b087488e41c57c46664e7f2298e5f835d313af0c39986edf'),
    }[producer_version]
    require(codes.get(str((ROOT / 'scripts/model-comparison' / runner_name).resolve())) == runner_sha,
            'reviewed_producer_code_required')
    for name, expected in codes.items():
        evidence.raw(name, expected)
    for row in predictions:
        name = row['id'] + ('.raw-response.json' if candidate == 'hy30' else '-response.json')
        raw = evidence.read(run / name)
        require(type(raw.get('prompt')) is str and text_sha(raw['prompt']) == row['promptSha256'], 'raw_prompt_hash_differs')
    for name, expected in code_files().items():
        evidence.raw(name, expected)
    evidence.unchanged()
    started = summary.get('createdAt') if candidate == 'hy30' else summary.get('startedAt')
    chronology = ('questions_frozen_before_run' if started and
                  utc(started) >= utc(questions['createdAtUtc']) else 'retrospective_questions_after_output_or_unproven')
    return {'sources': selected, 'questions': questions, 'summary': summary, 'predictions': predictions,
            'cohort': cohort, 'candidate': candidate, 'producerVersion': producer_version, 'run': str(run),
            'chronology': chronology, 'installation': installation,
            'files': {str(path): value for path, value in evidence.files.items()}}


def build_packet(verified):
    cohort = verified['cohort']
    require(cohort in COHORTS, 'one_cohort_required')
    predictions = verified['predictions']
    require([p.get('id') for p in predictions] == COHORTS[cohort], 'packet_cohort_coverage')
    questions = {r['id']: r for r in verified['questions']['rows']}
    packet, links = [], []
    for source, prediction in zip(verified['sources'], predictions, strict=True):
        item = questions[source['id']]
        for question in item['questions']:
            identifier = 'q_' + secrets.token_hex(16)
            packet.append({'questionId': identifier, 'questionKo': question['questionKo'],
                           'candidateTranslationKo': prediction['translation'], 'learningContextKo': item['learningContextKo']})
            links.append({'questionId': identifier, 'sourceId': source['id'], 'originalQuestionId': question['id'],
                          'question': question, 'targetSha256': prediction['targetSha256']})
    secrets.SystemRandom().shuffle(packet)
    key = {'version': VERSION, 'cohort': cohort, 'sources': verified['sources'], 'rows': links,
           'questionnaireSha256': QUESTION_SHA, 'sourcePacketSha256': DATASET_SHA, 'policySha256': POLICY_SHA}
    validate_packet(packet, key, verified)
    return packet, key


def validate_packet(packet, key, verified):
    cohort, ids = verified['cohort'], COHORTS[verified['cohort']]
    require(key.get('version') == VERSION and key.get('cohort') == cohort
            and key.get('questionnaireSha256') == QUESTION_SHA and key.get('sourcePacketSha256') == DATASET_SHA
            and key.get('policySha256') == POLICY_SHA, 'private_key_identity')
    require(packed(key.get('sources')) == packed(verified['sources']), 'private_source_changed')
    require(len(packet) == len(key.get('rows', [])) == 2 * len(ids), 'fixed_question_count')
    identifiers = [r.get('questionId') for r in packet]
    require(len(set(identifiers)) == len(identifiers) and all(type(q) is str and re.fullmatch(r'q_[0-9a-f]{32}', q)
            for q in identifiers), 'anonymous_unique_ids_required')
    links = {r['questionId']: r for r in key['rows']}
    require(len(links) == len(packet) and set(links) == set(identifiers), 'key_question_coverage')
    frozen = {q['id']: (item, q) for item in verified['questions']['rows'] if item['cohort'] == cohort for q in item['questions']}
    require(Counter(r.get('originalQuestionId') for r in key['rows']) == Counter(frozen.keys()), 'frozen_question_coverage')
    targets = {r['id']: r for r in verified['predictions']}
    for row in packet:
        link = links[row['questionId']]
        item, question = frozen[link['originalQuestionId']]
        require(link.get('sourceId') == item['id'] and packed(link.get('question')) == packed(question), 'private_question_changed')
        require(set(row) == VISIBLE_FIELDS and all(type(v) is str for v in row.values()), 'visible_field_allowlist')
        require(row['questionKo'] == question['questionKo'] and row['learningContextKo'] == item['learningContextKo']
                and row['candidateTranslationKo'] == targets[item['id']]['translation']
                and link.get('targetSha256') == text_sha(row['candidateTranslationKo']), 'visible_text_or_target_changed')


def write_exclusive(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    require(path.read_bytes() == raw, 'output_write_verification_failed')


def prepare(run, cohort, producer_version, output):
    output = safe_path(output, OUTPUT_ROOT)
    require(not output.exists(), 'new_output_directory_required')
    verified = load_verified(run, cohort, producer_version)
    packet, key = build_packet(verified)
    packet_raw = b''.join(packed(row) + b'\n' for row in packet)
    key['packetSha256'] = sha(packet_raw)
    key_raw = packed(key) + b'\n'
    manifest = {'version': VERSION, 'status': 'prepared_not_answered_not_scored', 'createdAtUtc': now(),
        'cohort': cohort, 'expectedItems': len(COHORTS[cohort]), 'expectedQuestions': len(packet),
        'producerVersion': producer_version, 'run': verified['run'], 'candidate': verified['candidate'],
        'runMetadata': verified['summary'], 'installationMetadata': verified['installation'],
        'questionChronology': verified['chronology'], 'files': verified['files'], 'codeFiles': code_files(),
        'packetSha256': sha(packet_raw), 'privateKeySha256': sha(key_raw),
        'shareOnly': 'answerer/packet.jsonl', 'freshUnexposedAnswererRequired': True,
        'freezeAnswersBeforeJudgmentsRequired': True, 'automaticCheckFailuresExcluded': False,
        'modelWeightsOrNativeRuntimeRehashed': False, 'modelInferencePerformed': False,
        'humanReviewed': False, 'independentTest': False, 'answerabilityEvaluated': False}
    seal(verified['files'])
    output.mkdir(parents=True, exist_ok=False)
    (output / 'answerer').mkdir()
    (output / 'private').mkdir()
    write_exclusive(output / 'answerer/packet.jsonl', packet_raw)
    write_exclusive(output / 'private/private-key.json', key_raw)
    seal(verified['files'])
    write_exclusive(output / 'private/manifest.json', packed(manifest) + b'\n')
    return manifest


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--run', required=True, type=Path)
    cli.add_argument('--cohort', required=True, choices=COHORTS)
    cli.add_argument('--producer-version', required=True, choices=sorted(SUPPORTED))
    cli.add_argument('--output', required=True, type=Path)
    args = cli.parse_args()
    try:
        result = prepare(args.run, args.cohort, args.producer_version, args.output)
        print(packed({k: result[k] for k in ('status', 'cohort', 'expectedQuestions', 'questionChronology')}).decode())
        return 0
    except Exception as error:
        print(packed({'status': 'refused', 'errorType': type(error).__name__,
                      'code': str(error) if type(error) is ValueError else 'verification_failed'}).decode())
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
