"""Prepare 64 S5 observations plus 64 relation observations; append only explicitly.

No model, HTTP, app database, registration, translation repair or historical
judgment mutation. Final S4/S5 evidence is revalidated before even preparing.
The content-addressed existing ledger API supplies idempotent append semantics.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'scripts/model-comparison'))
sys.path.insert(0, str(ROOT/'scripts/model-comparison/error-ledger'))
from input_execution_v1 import evaluation as e
from input_execution_v1 import relations
import ledger

VERSION = 'input-execution-v1-ledger-evidence-v1'
FREEZE_PATH = 'content/model-comparison/input-execution-v1/evaluation-freeze-v1.json'
FREEZE_SHA = 'e1bc158a004e313f136b36c584f9a1d6a75f8afec43451c8d5e2c3b289e3d64b'
LEDGER_PATH = '.training/quality-evaluation/error-ledger/v1'
LEDGER_CODE = 'scripts/model-comparison/error-ledger/ledger.py'
LEDGER_CODE_SHA = '13090664fa5e2cbb4e0e22d4ab2e1cbe2092e83251961098c1a4665403462d22'
BASELINE = {'events':836, 'translationReviews':146,
            'inventorySha256':'6454dde292fa30469e44d734889923ee3692c0140c8a85452e896fe590d81edb'}
BASE = '.training/quality-evaluation/input-preparation-v1'
FROZEN_NAMES = {'evaluation.py','test_evaluation.py','EVALUATION.md','provisional_reviews.py',
                'test_provisional_reviews.py','review_io.py','test_review_io.py','relations.py','test_relations.py'}
GUIDE = BASE+'/s5-source-drafts-20260913/GUIDE.md'


class EvidenceGraph:
    """Small JSON reads plus bounded streaming hashes for GGUF/runtime files."""
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.files = {}

    def path(self, path):
        return e.rooted(self.root, path)

    def key(self, path):
        return self.path(path).relative_to(self.root).as_posix()

    def remember(self, path, digest):
        key = self.key(path)
        e.require(key not in self.files or self.files[key] == digest, 'evidence_changed_during_import')
        self.files[key] = digest
        return {'path':key,'sha256':digest}

    def verify(self, path, expected=None):
        path = self.path(path)
        before = path.stat()
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                digest.update(chunk)
        after = path.stat()
        e.require((before.st_size,before.st_mtime_ns,before.st_ino) ==
                  (after.st_size,after.st_mtime_ns,after.st_ino), 'file_changed_during_hash')
        value = digest.hexdigest()
        e.require(expected is None or value == expected, 'evidence_hash_mismatch:'+self.key(path))
        return self.remember(path,value)

    def read(self, path, expected=None):
        path = self.path(path)
        e.require(path.stat().st_size <= 32*1024**2, 'json_evidence_size_limit')
        raw = path.read_bytes()
        digest = e.sha(raw)
        e.require(expected is None or digest == expected, 'evidence_hash_mismatch:'+self.key(path))
        self.remember(path,digest)
        return raw

    def json(self, path, expected=None):
        return json.loads(self.read(path,expected))

    def rows(self, path):
        raw = self.read(path)
        if Path(path).suffix == '.jsonl':
            return [json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
        data = json.loads(raw)
        return data if isinstance(data,list) else data['rows']

    def ref(self, path, pointer=''):
        key = self.key(path)
        return {'path':key,'sha256':self.files[key],'pointer':pointer}

    def verify_unchanged(self):
        for path,digest in list(self.files.items()):
            self.verify(path,digest)


def verify_evaluation_freeze(graph):
    freeze = graph.json(FREEZE_PATH,FREEZE_SHA)
    e.require(freeze['version'] == 'input-execution-v1-evaluation-tool-freeze-v1'
              and freeze['s1FreezeSha256'] == e.FREEZE_SHA and freeze['s2ManifestSha256'] == e.S2_SHA,
              'evaluation_freeze_identity')
    expected = {'scripts/model-comparison/input_execution_v1/'+name for name in FROZEN_NAMES}|{GUIDE}
    records = e.unique(freeze['files'],'path','evaluation_freeze_files')
    e.require(set(records) == expected and len(records) == 10, 'evaluation_freeze_ten_files_required')
    for record in records.values():
        graph.verify(record['path'],record['sha256'])
    graph.verify(LEDGER_CODE,LEDGER_CODE_SHA)
    graph.verify(relations.CHECKER_PATH,relations.CHECKER_SHA)
    return freeze


def require_complete_report(report):
    e.require(report.get('version') == e.VERSION and len(report.get('rows',[])) == 64, 'complete_s5_report_required')
    expected = {'expectedOutputs':64,'sourceReviews':64,'answerContexts':64,'expectedPropositions':192,
                'expectedTerms':156,'expectedQuestions':128,'expectedCoreQuestions':64,'questionGrades':128}
    e.require(report.get('counts') == expected, 'complete_s5_denominators_required')
    e.require(all(r.get('complete') is True and r.get('sourceReviewComplete') is True and
                  r.get('questionsComplete') is True and r.get('unresolved') is False for r in report['rows']),
              's5_unresolved_or_incomplete_review')
    e.require(report.get('modelCalls') == 0 and report.get('ledgerEventsAppended') == 0 and
              report.get('humanLearningEffectMeasured') is False, 's5_report_boundary_changed')


def revalidate_report(bundle, source_rows, answers, grade_rows, report):
    require_complete_report(report)
    rederived = e.aggregate(bundle,source_rows,answers,grade_rows)
    e.require(report == rederived, 's5_report_not_reproducible_from_judgments')


def revalidate_relations(report, output_rows):
    recomputed = relations.diagnose_rows(output_rows)
    e.require(all(report.get(key) == value for key,value in recomputed.items()), 'relation_report_not_reproducible')


def validate_final_evidence(*, folder, outputs, source_reviews, grades, report_path, relation_folder, root=ROOT):
    graph = EvidenceGraph(root)
    freeze = verify_evaluation_freeze(graph)
    folder, outputs, relation_folder = (graph.path(p) for p in (folder,outputs,relation_folder))
    # The existing completion validator is mandatory before reading S5 packets.
    completion = e.validate_run_completion(outputs,graph.root)
    graph.verify(completion['path'],completion['sha256'])
    summary = graph.json(outputs.parent/'summary.json')
    plan = graph.json(outputs.parent/'plan.json',freeze['s4PlanSha256'])
    for record in plan['inputFiles']:
        graph.verify(record['path'],record['sha256'])
    for name,digest in summary['artifactHashes'].items():
        graph.verify(outputs.parent/name,digest)
    units, annotations, source_evidence = e.load_development(graph.root)
    prepared = e.load_prepared(graph.root)
    output_rows = graph.rows(outputs)
    generated_bundle = e.build_review_packets(output_rows,units,annotations,prepared,graph.root)
    bundle = graph.json(folder/'private/bundle.json')
    manifest = graph.json(folder/'manifest.json')
    e.require(e.sha(e.packed(bundle)) == manifest['bundleSha256'] and manifest['questionPackets'] ==
              manifest['sourcePackets'] == 64, 's5_bundle_manifest_changed')
    original_bundle = {key:value for key,value in bundle.items() if key != 'evidenceFiles'}
    e.require(original_bundle == generated_bundle, 's5_packet_bundle_not_reproducible')
    expected_bundle_evidence = source_evidence + [completion,
        {'path':outputs.relative_to(graph.root).as_posix(),'sha256':graph.files[graph.key(outputs)]}]
    e.require(bundle['evidenceFiles'] == expected_bundle_evidence, 's5_bundle_evidence_changed')
    for reference in bundle['evidenceFiles']:
        graph.verify(reference['path'],reference['sha256'])
    for group,directory in (('questionPackets','question-packets'),('sourcePackets','source-packets')):
        for packet in bundle[group]:
            raw = graph.read(folder/directory/(packet['reviewId']+'.json'))
            e.require(raw == e.packed(packet), 'formal_packet_bytes_changed')
    answer_freeze = graph.json(folder/'answers-freeze.json')
    answers_file = folder/'answers-frozen.json'
    graph.verify(answers_file,answer_freeze['answersSha256'])
    answers = graph.rows(answers_file)
    e.require(answer_freeze['answerContexts'] == 64 and answer_freeze['questions'] == 128 and
              answer_freeze['humanReviewed'] is False, 'answers_freeze_inventory')
    actors = [answer['reviewer']['actorId'] for answer in answers]
    e.require(len(actors) == len(set(actors)) == len(answer_freeze['freshContextActorIds']) == 64
              and set(actors) == set(answer_freeze['freshContextActorIds']),
              'answers_freeze_actor_identity')
    answer_map = e.unique(answers,'reviewId','answer')
    for packet in bundle['sourcePackets']:
        answer = answer_map[packet['reviewId']]
        expected = {**packet,'answer':answer,'answerSha256':e.sha(e.packed(answer))}
        e.require(graph.read(folder/'grade-packets'/(packet['reviewId']+'.json')) == e.packed(expected),
                  'grade_packet_or_frozen_answer_changed')
    source_rows, grade_rows = graph.rows(source_reviews), graph.rows(grades)
    report = graph.json(report_path)
    revalidate_report(bundle,source_rows,answers,grade_rows,report)

    relation_manifest = graph.json(relation_folder/'manifest.json')
    relation_path = relation_folder/'relation-report.json'
    relation_report = graph.json(relation_path,relation_manifest['reportSha256'])
    e.require(relation_manifest == {'version':relations.VERSION,'status':'diagnosed','outputs':64,
              'reportFile':'relation-report.json','reportSha256':graph.files[graph.key(relation_path)],
              'checker':relation_report['checker'],'modelCalls':0,'translationGenerations':0,'ledgerWrites':0},
              'relation_manifest_inventory')
    revalidate_relations(relation_report,output_rows)
    e.require(relation_report['checker'] == relation_manifest['checker'], 'relation_manifest_checker_changed')
    graph.verify('scripts/model-comparison/input_execution_v1/relations.py',relation_report['wrapperCodeSha256'])
    graph.verify('scripts/model-comparison/input_execution_v1/evaluation.py',relation_report['completionValidatorCodeSha256'])
    for reference in relation_report['evidenceFiles']:
        graph.verify(reference['path'],reference['sha256'])
    e.require(completion in relation_report['evidenceFiles'], 'relation_completion_evidence_missing')
    e.require({'path':graph.key(outputs),'sha256':graph.files[graph.key(outputs)]} in relation_report['evidenceFiles'],
              'relation_output_evidence_missing')
    # The adapter is frozen separately, without changing the evaluation freeze.
    graph.verify(Path(__file__).resolve())
    refs = [graph.ref(path) for path in (report_path,source_reviews,grades,answers_file,folder/'answers-freeze.json',
            folder/'private/bundle.json',folder/'manifest.json',outputs,outputs.parent/'summary.json',
            FREEZE_PATH,LEDGER_CODE,Path(__file__).resolve())]
    relation_refs = [graph.ref(path) for path in (relation_path,relation_folder/'manifest.json',relations.CHECKER_PATH,
                                                  'scripts/model-comparison/input_execution_v1/relations.py')]
    e.require(e.validate_run_completion(outputs,graph.root) == completion,'s4_completion_changed_during_validation')
    graph.verify_unchanged()
    return {'graph':graph,'bundle':bundle,'report':report,'outputs':output_rows,'relations':relation_report,
            'evidenceRefs':refs,'relationEvidenceRefs':relation_refs,'run':outputs.parent.relative_to(graph.root).as_posix()}


def build_events(validated):
    bundle, report = validated['bundle'], validated['report']
    require_complete_report(report)
    inputs = list(e.ledger_observations(bundle,report,validated['evidenceRefs']))
    e.require(len(inputs) == 64 and len({x['reviewId'] for x in inputs}) == 64, 'input_observation_inventory')
    mappings = {(m['id'],m['configuration']):m for m in bundle['mapping']}
    linked = {(r['sourceId'],r['system']):r for r in inputs}
    observations = validated['relations']['observations']
    e.require(len(observations) == 64 and len({(r['id'],r['configuration']) for r in observations}) == 64,
              'relation_observation_inventory')
    events = list(inputs)
    for row in observations:
        key = row['id'],row['configuration']
        e.require(key in mappings and key in linked, 'relation_without_input_observation')
        mapped, original = mappings[key], linked[key]
        e.require(all(row[k] == mapped[k] for k in ('sourceSha256','translationSha256','promptSha256','rawResponseSha256')),
                  'relation_input_join_changed')
        events.append({'version':ledger.VERSION,'kind':'relation_observation','checker':relations.checker.VERSION,
            'checkerCodeSha256':relations.CHECKER_SHA,'reviewVersion':e.VERSION,
            'instanceId':e.sha(e.packed({'run':validated['run'],'id':row['id'],'configuration':row['configuration']})),
            **{k:original[k] for k in ('outputId','sourceId','sourceSha256','translationSha256','contextSha256','cohort','system')},
            'run':validated['run'],'reviewId':mapped['reviewId'],'observationId':row['observationId'],
            'baselineReviewEventIds':[], 'linkedInputPreparationEventIds':[ledger.sha(ledger.packed(original))],
            'originalRunStatus':'completed','originalOutputStatus':row['originalOutputStatus'],
            'originalAutomaticChecks':deepcopy(row['originalAutomaticChecks']),
            'originalTechnicalChecks':deepcopy(row['originalTechnicalChecks']),
            'diagnostic':deepcopy(row['diagnostic']),'evidenceOffsetEncoding':row['evidenceOffsetEncoding'],
            'evidence':deepcopy(validated['evidenceRefs']+validated['relationEvidenceRefs']),
            'translationErrorAdded':False,'originalJudgmentsChanged':False,'trainingUseAllowed':False,
            'independentHoldout':False,'humanReviewed':False,'modelCalls':0,
            'warningCountIsNotTranslationErrorCount':True,'paragraphBaselineIsNotRelationSpecificGold':True})
    e.require(Counter(event['kind'] for event in events) ==
              {'input_preparation_observation':64,'relation_observation':64}, 'event_kind_inventory')
    e.require(len({ledger.sha(ledger.packed(event)) for event in events}) == 128, 'duplicate_prepared_event')
    return events


def ledger_snapshot(folder):
    folder = Path(folder)
    e.require(folder.is_dir() and not any(p.is_symlink() for p in (folder,*folder.parents)), 'existing_ledger_required')
    e.require((folder/'events').is_dir() and not (folder/'events').is_symlink(), 'existing_ledger_events_redirected')
    summary = ledger.summarize(folder)
    files = {}
    for path in sorted((folder/'events').glob('*.json')):
        raw = path.read_bytes()
        e.require(path.stem == ledger.sha(raw) and raw == ledger.packed(json.loads(raw)), 'existing_event_corrupted')
        files[path.stem] = raw
    e.require(len(files) == summary['eventCount'], 'ledger_changed_during_snapshot')
    return {'summary':summary,'files':files,'inventorySha256':ledger.sha(ledger.packed(sorted(files)))}


def require_baseline(snapshot, events, baseline=BASELINE):
    incoming = {ledger.sha(ledger.packed(event)) for event in events}
    untouched = set(snapshot['files'])-incoming
    e.require(len(untouched) == baseline['events'] and ledger.sha(ledger.packed(sorted(untouched))) ==
              baseline['inventorySha256'], 'existing_ledger_baseline_inventory_changed')
    e.require(snapshot['summary']['eventKindCounts'].get('translation_review',0) == baseline['translationReviews'],
              'historical_translation_reviews_changed')
    return {'baselineEvents':len(untouched),'baselineInventorySha256':baseline['inventorySha256'],
            'existingPreparedEvents':len(set(snapshot['files']) & incoming),
            'translationReviews':baseline['translationReviews']}


def append_prevalidated(folder, events, before, baseline=BASELINE):
    require_baseline(before,events,baseline)
    # A fresh read immediately before append catches changes since preparation.
    current = ledger_snapshot(folder)
    e.require(current['files'] == before['files'], 'ledger_changed_before_append')
    imported = ledger.append_events(folder,events)
    after = ledger_snapshot(folder)
    require_baseline(after,events,baseline)
    e.require(all(after['files'].get(key) == raw for key,raw in before['files'].items()), 'historical_event_modified')
    expected = set(before['files'])|{ledger.sha(ledger.packed(event)) for event in events}
    e.require(set(after['files']) == expected, 'unexpected_concurrent_ledger_events')
    e.require(after['summary']['eventKindCounts'].get('translation_review',0) ==
              before['summary']['eventKindCounts'].get('translation_review',0), 'translation_review_count_changed')
    return {'imported':imported,'beforeEventCount':len(before['files']),'afterEventCount':len(after['files']),
            'beforeInventorySha256':before['inventorySha256'],'afterInventorySha256':after['inventorySha256'],
            'allPriorEventBytesUnchanged':True,'translationReviews':baseline['translationReviews']}


def prepare(*, folder, outputs, source_reviews, grades, report_path, relation_folder, destination, append=False, root=ROOT):
    graph_root = Path(root).resolve()
    destination = e.rooted(graph_root,destination)
    boundary = graph_root/BASE
    e.require(destination.is_relative_to(boundary) and destination != boundary and not destination.exists(),
              'fresh_owned_evidence_destination_required')
    validated = validate_final_evidence(folder=folder,outputs=outputs,source_reviews=source_reviews,grades=grades,
        report_path=report_path,relation_folder=relation_folder,root=graph_root)
    events = build_events(validated)
    ledger_folder = graph_root/LEDGER_PATH
    before = ledger_snapshot(ledger_folder)
    baseline = require_baseline(before,events)
    validated['graph'].verify_unchanged()
    # No writer is invoked before every gate and event construction has passed.
    e.write_new(destination/'events.json',{'version':VERSION,'events':events})
    result = {'version':VERSION,'status':'prepared','createdAtUtc':datetime.now(timezone.utc).isoformat(),
        'events':128,'eventKindCounts':dict(Counter(event['kind'] for event in events)),
        'eventsSha256':e.sha((destination/'events.json').read_bytes()),'appendRequested':bool(append),
        'ledgerEventsAppended':0,'baseline':baseline,'existingEventCount':len(before['files']),
        'existingInventorySha256':before['inventorySha256'],'evidenceFiles':dict(validated['graph'].files),
        'humanReviewed':False,'modelCalls':0,'translationReviewEventsAdded':0,
        'translationErrorCountIncreasedByObservations':False,'questionErrorsInferredAsTranslationErrors':False,
        'relationWarningCountIsNotErrorCount':True,'originalJudgmentsChanged':False,
        'trainingUseAllowed':False,'independentHoldout':False,'appRegistrationPerformed':False}
    e.write_new(destination/'prepared-manifest.json',result)
    if append:
        validated['graph'].verify_unchanged()
        receipt = append_prevalidated(ledger_folder,events,before)
        result = {**result,'status':'appended','ledgerEventsAppended':receipt['imported']['added'],**receipt}
        e.write_new(destination/'append-receipt.json',result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('folder','outputs','source-reviews','grades','report','relations','destination'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--append',action='store_true',help='Explicitly append to the existing evidence ledger after revalidation.')
    args = parser.parse_args()
    result = prepare(folder=args.folder,outputs=args.outputs,source_reviews=args.source_reviews,grades=args.grades,
        report_path=args.report,relation_folder=args.relations,destination=args.destination,append=args.append)
    print(json.dumps({key:result[key] for key in ('status','events','eventKindCounts','ledgerEventsAppended')},ensure_ascii=False))


if __name__ == '__main__':
    main()
