"""Append-only evidence ledger for explicitly selected development comparisons."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VERSION = 'translation-error-ledger-v1'

def packed(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))+'\n').encode('utf-8')

def sha(value):
    return hashlib.sha256(value).hexdigest()

def require(value, reason):
    if not value: raise ValueError(reason)

class Evidence:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.files = {}

    def path(self, path):
        path = Path(path)
        path = path if path.is_absolute() else self.root/path
        require(path.resolve().is_relative_to(self.root) and not any(p.is_symlink() for p in (path,*path.parents)), 'evidence_path_redirected')
        return path

    def read(self, path, expected=None):
        path = self.path(path)
        raw = path.read_bytes()
        digest = sha(raw)
        require(expected is None or digest == expected, 'evidence_hash_mismatch:'+str(path))
        relative = path.relative_to(self.root).as_posix()
        require(relative not in self.files or self.files[relative] == digest, 'evidence_changed_during_import')
        self.files[relative] = digest
        return raw

    def json(self, path, expected=None):
        return json.loads(self.read(path, expected))

    def lines(self, path, expected=None):
        return [json.loads(line) for line in self.read(path, expected).decode('utf-8-sig').splitlines() if line.strip()]

    def ref(self, path, pointer=''):
        path = self.path(path).relative_to(self.root).as_posix()
        return dict(path=path, sha256=self.files[path], pointer=pointer)

    def verify_unchanged(self):
        for path, digest in list(self.files.items()): self.read(path, digest)

HYPOTHESES = {
    'word_sense': ('문맥에 따른 개념 선택 또는 구체성 손실', '같은 표현의 금융/일반 최소대립과 원문 핵심 명제를 대조한다.'),
    'semantic_roles': ('주체·대상·수식 대상 연결 손실', '누가 누구에게 무엇을 하는지 원문 역할을 따로 대조한다.'),
    'omission': ('필수 조건 또는 명제의 누락', '유창성 및 숫자 보존과 별개로 빠진 조건·범위를 검사한다.'),
    'numeric_roles': ('수치의 의미 역할 또는 연산 관계 변경', '숫자 존재뿐 아니라 피연산자·단위·대상을 대조한다.'),
}

def review_event(source_id, source, translation, context, system, run, cohort, review_version, review, refs):
    require(cohort in ('dev18','reading6','general16'), 'only_development_cohorts_allowed')
    text_hashes = {k+'Sha256':sha(v.encode('utf-8')) for k,v in dict(source=source,translation=translation,context=context).items()}
    require(review.get('sourceSha256',text_hashes['sourceSha256']) == text_hashes['sourceSha256'], 'review_source_mismatch')
    require(review.get('targetSha256',review.get('translationSha256',text_hashes['translationSha256'])) == text_hashes['translationSha256'], 'review_translation_mismatch')
    errors = review.get('errors', [])
    quotes = []
    for error in errors:
        sq,tq = error.get('sourceSpan',error.get('sourceQuote','')),error.get('targetSpan',error.get('targetQuote',''))
        quotes.append(dict(sourceQuote=sq,targetQuote=tq,sourceExact=bool(sq) and sq in source,
            targetExact=bool(tq) and tq in translation, annotation=error))
    for check in review.get('propositionChecks',[]):
        if check.get('preserved') is not True:
            sq,tq = check.get('sourceQuote',''),check.get('targetQuote','')
            quotes.append(dict(sourceQuote=sq,targetQuote=tq,sourceExact=bool(sq) and sq in source,
                targetExact=bool(tq) and tq in translation, annotation=check))
    severity = review.get('highestSeverity',review.get('severity'))
    severity = {0:'none',1:'minor',2:'major',3:'critical'}.get(severity,severity)
    require(severity in ('none','minor','major','critical','unresolved'), 'unknown_severity')
    categories = sorted({x.get('category','unclassified') for x in errors})
    if quotes and not categories: categories = ['proposition_relation']
    hypotheses = []
    for category in categories:
        hypothesis,next_check = HYPOTHESES.get(category,('원문 관계 또는 표현 선택 오류 가능성','원시 인용과 반례를 대조해 유형·원인을 확인한다.'))
        hypotheses.append(dict(category=category,hypothesisKo=hypothesis,confidence='unconfirmed_causal_hypothesis',
            counterEvidence='not_yet_tested',nextVerificationKo=next_check,modelInternalCauseConfirmed=False))
    return dict(version=VERSION,kind='translation_review',cohort=cohort,system=system,run=run,
        sourceId=source_id,outputId=sha(packed(text_hashes)),**text_hashes,
        reviewVersion=review_version,severity=severity,categories=categories,review=review,
        observationQuotes=quotes,causeHypotheses=hypotheses,evidence=refs,
        humanReviewed=False,trainingUseAllowed=False,independentHoldout=False,
        automaticChecks='see_linked_run_evidence; not_equivalent_to_meaning_review')

def base48(ev):
    folder='.training/quality-evaluation/dev48-v1'
    inp=folder+'/inputs.jsonl'; labels=folder+'/judgments.jsonl'
    rows=ev.lines(inp,'054d7f41e7f47b40b7216d047adcc35a0ab741409d954554143d0284cca3d222')
    judgments=ev.lines(labels,'5e466ac8aaec742c5db7b4ed3f163f63273a5a8facbc274b1776d402f7046b19')
    by_id={r['id']:r for r in rows}
    require(len(by_id)==len(rows)==len(judgments)==48,'base48_inventory_mismatch')
    manifest_path=folder+'/manifest.json'
    manifest=ev.json(manifest_path)
    for record in manifest['evidenceFiles']: ev.read(record['path'],record['sha256'])
    for index,j in enumerate(judgments):
        r=by_id[j['id']]
        for name in ('source','translation','context'):
            require(sha(r[name].encode('utf-8'))==r[name+'Sha256'],'input_text_hash_mismatch')
        cohort={'dev':'dev18','reading':'reading6'}[r['split']]
        folder_name='linguistic-dev-20260910' if cohort=='dev18' else 'real-reading-check-20260910'
        run=f'.training/comparisons/{folder_name}/{r["translationSystem"]}'
        yield review_event(r['sourceId'],r['source'],r['translation'],r['context'],r['translationSystem'],run,cohort,
            'adjusted-hy7-tg12-v1',j,[ev.ref(inp,r['id']),ev.ref(labels,str(index)),ev.ref(manifest_path)])

def round2(ev):
    for cohort,folder_name in (('dev18','linguistic-dev-20260910'),('reading6','real-reading-check-20260910')):
        folder=f'.training/comparisons/{folder_name}'
        summary_path=folder+'/assistant-review-round2-v4.json'
        summary=ev.json(summary_path)
        prepared=folder+'/prepared-round2-v4'
        key_path=prepared+'/review-key.json'
        key=ev.json(key_path,summary['keySha256'])
        mapping={(r['id'],r['label']):r for r in key['mapping']}
        packets={}
        packet_files=summary.get('packetFiles') or {'packet.jsonl':summary['packetSha256']}
        for name,digest in packet_files.items():
            path=prepared+'/'+name
            for packet in ev.lines(path,digest): packets[packet['input']['id']]=(packet,path)
        for system in key['systems']:
            ev.read(Path(system['directory'])/'predictions.jsonl',system['predictionsSha256'])
            ev.read(Path(system['directory'])/'summary.json',system['summarySha256'])
        for record in summary['reviewFiles']:
            for reviewed in ev.lines(record['path'],record['sha256']):
                packet,packet_path=packets[reviewed['id']]
                inp=packet['input']; candidates={r['label']:r for r in packet['candidates']}
                require(sha(inp['source'].encode('utf-8'))==reviewed['sourceSha256'],'packet_source_mismatch')
                for j in reviewed['judgments']:
                    target=candidates[j['label']]; mapped=mapping[(j['id'],j['label'])]
                    require(sha(target['translation'].encode('utf-8'))==target['targetSha256']==mapped['targetSha256']==j['targetSha256'],'packet_target_mismatch')
                    directory=Path(key['systems'][mapped['systemIndex']]['directory'])
                    yield review_event(j['id'],inp['source'],target['translation'],inp.get('context',''),directory.name,
                        directory.relative_to(ev.root).as_posix(),cohort,'assistant-round2-v4',j,
                        [ev.ref(summary_path),ev.ref(key_path),ev.ref(packet_path,j['id']),ev.ref(record['path'],j['id']+'/'+j['label'])])

def general(ev):
    source_path='content/model-comparison/general-context-dev-20260911.jsonl'
    sources={r['id']:r for r in ev.lines(source_path)}
    require(len(sources)==16,'general_source_inventory')
    for candidate in ('candidate-a','candidate-b'):
        folder='.training/quality-evaluation/general-meaning/'+candidate
        link_path=folder+'/private-producer-link.json'; review_path=folder+'/assistant-review.json'; packet_path=folder+'/reviewer-packet.json'
        link=ev.json(link_path); review=ev.json(review_path); packet=ev.json(packet_path,link['packetSha256'])
        ev.read(Path(link['runDirectory'])/'summary.json',link['runSummarySha256'])
        require(ev.files[packet_path]==review['packetSha256'],'general_packet_identity')
        targets={r['id']:r for r in packet['rows']}
        require(len(review['rows'])==len(targets)==16,'general_review_inventory')
        for j in review['rows']:
            src=sources[j['id']]; target=targets[j['id']]
            yield review_event(j['id'],src['source'],target['translationKo'],src.get('context',''),
                'hy7-'+link['profile'],Path(link['runDirectory']).relative_to(ev.root).as_posix(),
                'general16',review['version'],j,[ev.ref(source_path,j['id']),ev.ref(packet_path,j['id']),ev.ref(review_path,j['id']),ev.ref(link_path)])

def link_output(reviews, source_id, system, target_hash=None):
    matches=[r for r in reviews if r['sourceId']==source_id and r['system']==system
             and (target_hash is None or r['translationSha256']==target_hash)]
    require(matches and len({r['outputId'] for r in matches})==1,'missing_or_ambiguous_output_link')
    first=matches[0]
    return {**{k:first[k] for k in ('outputId','sourceId','sourceSha256','translationSha256','contextSha256','cohort','system','run')},
            'linkedReviewEventIds':sorted({sha(packed(r)) for r in matches})}

def question_events(ev,reviews):
    cases=[('general-answerability/raw-v1','hy7-raw','answers.json','answers-freeze.json',32),
           ('general-answerability/contextual-v1','hy7-contextual','answers.json','answers-freeze.json',32),
           ('finance-answerability/reading-candidate-a-v1','hymt30-paging-v4','answers-a-v1.json','answers-freeze-v1.json',12)]
    for name,system,answer_name,freeze_name,count in cases:
        folder='.training/quality-evaluation/'+name; report_path=folder+'/grade-report-v1.json'
        report=ev.json(report_path); hashes=report['hashes']
        answer_path=folder+'/'+answer_name; freeze_path=folder+'/'+freeze_name
        key_path=folder+'/private/private-key.json'; packet_path=folder+'/answerer/packet.jsonl'
        judgment_path=folder+'/root-judgments-v1.json'
        answers=ev.json(answer_path,hashes['answersSha256'])
        ev.read(freeze_path,hashes['freezeReceiptSha256']); ev.read(judgment_path,hashes['judgmentsSha256'])
        key=ev.json(key_path,hashes['privateKeySha256']); packets=ev.lines(packet_path,hashes['packetSha256'])
        ev.read(folder+'/private/manifest.json',hashes['packetManifestSha256'])
        keys={r['questionId']:r for r in key['rows']}; targets={r['questionId']:r for r in packets}
        answer_map={r['questionId']:r for r in answers['answers']}
        require(len(keys)==len(key['rows'])==len(targets)==len(packets)==len(answer_map)==len(answers['answers'])==len(report['rows'])==count,'question_inventory_mismatch')
        require({r['questionId'] for r in report['rows']}==set(keys)==set(targets)==set(answer_map),'question_ids_mismatch')
        for row in report['rows']:
            qid=row['questionId']; keyed=keys[qid]; packet=targets[qid]
            require(row['sourceId']==keyed['sourceId'],'question_source_mismatch')
            require(sha(packet['candidateTranslationKo'].encode('utf-8'))==keyed['targetSha256'],'question_packet_target_mismatch')
            linked=link_output(reviews,row['sourceId'],system,keyed['targetSha256'])
            require(keyed.get('sourceSha256',linked['sourceSha256'])==linked['sourceSha256'],'question_source_hash_mismatch')
            yield dict(version=VERSION,kind='question_judgment',**linked,questionId=qid,
                originalQuestionId=keyed['originalQuestionId'],reviewVersion=report['version'],
                effectiveVerdict=row['effectiveVerdict'],criticalQuestion=row['criticalQuestion'],
                judgment=row,question=keyed['question'],answer=answer_map[qid],
                answererProvenance=answers.get('provenance',{}),
                translationErrorInferredFromWrongAnswer=False,humanLearningEffectMeasured=False,
                humanReviewed=False,trainingUseAllowed=False,
                evidence=[ev.ref(p,qid) for p in (report_path,key_path,packet_path,answer_path,judgment_path)]+[ev.ref(freeze_path)])

def qe_event(linked,checker,version,row,refs):
    completed=row['completed']; warning=row['warning']; material=row['materialError']
    require(type(completed) is bool and type(material) is bool,'qe_boolean_required')
    require(not completed or type(warning) is bool,'qe_warning_required_for_completed')
    confusion=('true_positive' if warning else 'false_negative') if material else ('false_positive' if warning else 'true_negative')
    return dict(version=VERSION,kind='qe_observation',**linked,checker=checker,reviewVersion=version,
        baselineJudgmentVersion='adjusted-hy7-tg12-v1',completed=completed,
        observationStatus=confusion if completed else 'not_observed',
        materialError=material,warning=warning if completed else None,rawAssessment=row,
        humanReviewed=False,trainingUseAllowed=False,independentHoldout=False,
        semanticSpanQualityInferred=False,evidence=refs)

def qe_events(ev,reviews):
    baseline={f'{"dev" if r["cohort"]=="dev18" else "reading"}:{r["system"]}:{r["sourceId"]}':r
              for r in reviews if r['reviewVersion']=='adjusted-hy7-tg12-v1'}
    cases=[('qwen35-dev48-warning-v2-failed-run.json','qwen35-v4',
            '.translation/qe/llm-candidates/qwen35-9b/runs/dev48-v4'),
           ('qwen-v5-dev8-evaluation-resume-20260911.json','qwen35-v5-meaning',
            '.translation/qe/llm-candidates/qwen35-9b/runs/semantic-v2-dev8-v5')]
    for name,checker,run in cases:
        path='.training/quality-evaluation/'+name; report=ev.json(path)
        summary_path=run+'/summary.json'; ev.read(summary_path,report['evidence']['runSummarySha256'])
        require(len({r['id'] for r in report['rows']})==len(report['rows']),'duplicate_qe_rows')
        for row in report['rows']:
            base=baseline[row['id']]
            require(row['materialError']==base['review']['materialError'],'qe_baseline_judgment_mismatch')
            linked=link_output(reviews,base['sourceId'],base['system'],base['translationSha256'])
            yield qe_event(linked,checker,report['version'],row,[ev.ref(path,row['id']),ev.ref(summary_path)])
    folder='.training/quality-evaluation/wmt20-dev48-v2'
    path=folder+'/calibration-product-95-v2.json'; report=ev.json(path)
    for record in report['evidenceFiles']:ev.read(record['path'],record['sha256'])
    score_path=folder+'/scores.jsonl'; scores=ev.lines(score_path)
    require(len(scores)==48 and {r['id'] for r in scores}==set(baseline),'comet_inventory_mismatch')
    observed=[]
    for score in scores:
        base=baseline[score['id']]
        require(score['status']=='completed' and not score['truncated'],'comet_incomplete')
        for key in ('sourceSha256','translationSha256','contextSha256'):
            require(score[key]==base[key],'comet_output_identity_mismatch')
        row=dict(id=score['id'],completed=True,warning=score['score']<=report['threshold'],
                 materialError=base['review']['materialError'],score=score['score'],threshold=report['threshold'])
        linked=link_output(reviews,base['sourceId'],base['system'],base['translationSha256'])
        event=qe_event(linked,'wmt20-comet-qe-da',report['version'],row,[ev.ref(path),ev.ref(score_path,score['id'])])
        observed.append(event); yield event
    counts=Counter(r['observationStatus'] for r in observed)
    require(counts['false_positive']==report['observed']['falsePositives'] and counts['false_negative']==report['observed']['falseNegatives']
            and counts['true_positive']==report['observed']['truePositives'],'comet_confusion_mismatch')

def runtime_events(ev):
    cases=[('tg27-v5-user-stop-20260911.json','user_cancellation'),
           ('qwen-v4-dev48-standby-failure-20260911.json','runtime_failure_with_standby_observation'),
           ('tg27-v5-resume-standby-failure-20260911.json','runtime_failure_with_standby_observation'),
           ('general-contextual-standby-observation-20260911.json','standby_observation')]
    for name,category in cases:
        path='.training/verifications/'+name; record=ev.json(path)
        refs=[ev.ref(path)]
        for evidence_path,digest in record.get('files',{}).items():ev.read(evidence_path,digest)
        if record.get('summarySha256'):
            summary_path=record['run']+'/summary.json'; ev.read(summary_path,record['summarySha256']); refs.append(ev.ref(summary_path))
        yield dict(version=VERSION,kind='runtime_observation',runtimeCategory=category,run=record['run'],
            observation=record,evidence=refs,translationQualityFailureInferred=False,
            syntheticTestFailure=False,modelInternalCauseConfirmed=False,trainingUseAllowed=False)

def term_events(ev):
    # Recompute the manual-judgment contract; no model or automatic meaning judge is invoked.
    import term_review
    folder='.training/quality-evaluation/finance-terms/candidates-root-v1'
    packet_path=folder+'/packet.json'; judgment_path=folder+'/root-judgments.json'
    packet=ev.json(packet_path); judgments=ev.json(judgment_path)
    ev.read('scripts/model-comparison/error-ledger/term_review.py',packet['codeSha256'])
    for path,digest in packet['evidenceFiles'].items():ev.read(path,digest)
    events,report=term_review.validate(packet,judgments,ev.files[packet_path])
    result_folder='.training/quality-evaluation/finance-terms/candidates-root-v1-evaluated'
    result_path=result_folder+'/events.jsonl'; saved=ev.lines(result_path)
    summary_path=result_folder+'/summary.json'; summary=ev.json(summary_path)
    require(summary['rows']==report['rows'] and summary['judgmentsSha256']==ev.files[judgment_path],'term_summary_mismatch')
    require(len(saved)==len(events),'term_saved_inventory')
    for event,original in zip(events,saved):
        event['evidence'] += [ev.ref(packet_path),ev.ref(judgment_path,event['system']+'/'+event['term']['occurrenceId'])]
        require(event==original,'term_saved_event_mismatch')
        event['evidence'] += [ev.ref(summary_path),ev.ref(result_path,event['system']+'/'+event['term']['occurrenceId'])]
        yield event

def append_events(folder, events):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    require(not any(p.is_symlink() for p in (folder,*folder.parents)),'ledger_redirected')
    lock=folder/'.writer.lock'
    with lock.open('x') as lock_stream:
        try:
            target=folder/'events'; target.mkdir(exist_ok=True)
            require(not target.is_symlink(),'events_redirected')
            added=reused=0
            for event in events:
                raw=packed(event); key=sha(raw); path=target/(key+'.json')
                if path.exists():
                    require(not path.is_symlink() and path.read_bytes()==raw,'existing_event_corrupted'); reused+=1
                else:
                    with path.open('xb') as stream:
                        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                    added+=1
            return dict(added=added,reused=reused)
        finally:
            lock_stream.close()
            lock.unlink()

def summarize(folder):
    events=[]
    for path in sorted((Path(folder)/'events').glob('*.json')):
        require(not path.is_symlink(),'event_redirected')
        raw=path.read_bytes(); require(path.stem==sha(raw),'event_digest_mismatch')
        event=json.loads(raw); require(event['version']==VERSION,'unsupported_event_version'); events.append(event)
    groups=defaultdict(list); history=defaultdict(list)
    for event in events:
        if event['kind']=='translation_review':
            groups[(event['cohort'],event['system'],event['reviewVersion'])].append(event)
            history[event['outputId']].append(dict(eventId=sha(packed(event)),system=event['system'],reviewVersion=event['reviewVersion'],severity=event['severity']))
    slices=[]
    for (cohort,system,version),values in sorted(groups.items()):
        slices.append(dict(cohort=cohort,system=system,reviewVersion=version,reviewRows=len(values),
            uniqueOutputs=len({v['outputId'] for v in values}),severityCounts=dict(Counter(v['severity'] for v in values)),
            categoryCounts=dict(Counter(c for v in values for c in v['categories']))))
    return dict(version=VERSION,eventCount=len(events),slices=slices,
        eventKindCounts=dict(Counter(r['kind'] for r in events)),
        questionSlices=[dict(cohort=cohort,system=system,count=len(values),
            verdicts=dict(Counter(r['effectiveVerdict'] for r in values)),
            coreVerdicts=dict(Counter(r['effectiveVerdict'] for r in values if r['criticalQuestion'])))
            for (cohort,system),values in grouped(events,'question_judgment',('cohort','system'))],
        qeSlices=[dict(checker=checker,observations=dict(Counter(r['observationStatus'] for r in values)))
            for (checker,),values in grouped(events,'qe_observation',('checker',))],
        runtimeCategories=dict(Counter(r['runtimeCategory'] for r in events if r['kind']=='runtime_observation')),
        termSlices=[dict(system=system,cohort=cohort,count=len(values),verdicts=dict(Counter(r['verdict'] for r in values)))
            for (system,cohort),values in grouped(events,'term_judgment',('system','cohort'))],
        multipleReviewHistories={k:v for k,v in history.items() if len(v)>1},
        reviewRoundsAreNotSummedAsNewModelErrors=True,causalAttributionConfirmed=False,
        coverage=['base48 adjusted reviews','Hy7/Hy30 round2 dev18 reading6','Hy7 general16 raw contextual',
                  'general raw/contextual and Hy30 reading question judgments','Qwen v4/v5 and COMET QE observations',
                  'selected runtime failures, standby and user cancellation','Hy7/Hy30 149 source term occurrences each'],
        pendingCoverage=['new TG27 results','other historical runtime incidents'],
        humanReviewed=False,independentHoldout=False,modelQualityAcceptance=False)

def grouped(events,kind,keys):
    groups=defaultdict(list)
    for event in events:
        if event['kind']==kind:groups[tuple(event[k] for k in keys)].append(event)
    return sorted(groups.items())

def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output',type=Path,required=True)
    args=cli.parse_args(); output=args.output.resolve()
    require(output.is_relative_to(ROOT/'.training/quality-evaluation/error-ledger'),'owned_ledger_output_required')
    ev=Evidence(); reviews=list(base48(ev))+list(round2(ev))+list(general(ev))
    events=reviews+list(question_events(ev,reviews))+list(qe_events(ev,reviews))+list(runtime_events(ev))+list(term_events(ev)); ev.verify_unchanged()
    imported=append_events(output,events); report=summarize(output); ev.verify_unchanged()
    reports=output/'reports'; reports.mkdir(exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    report.update(imported=imported,evidenceFiles=ev.files,codeSha256=sha(Path(__file__).read_bytes()))
    with (reports/(stamp+'.json')).open('xb') as stream: stream.write(packed(report))
    print(json.dumps(dict(status='imported',**imported,eventCount=report['eventCount'],report=str(reports/(stamp+'.json')))))

if __name__=='__main__': main()
