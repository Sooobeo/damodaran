"""Prepare and verify manual term occurrence judgments on frozen development outputs."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import ledger as l

VERSION='finance-term-occurrence-review-v1'
INVENTORY='.training/quality-evaluation/finance-terms/source-inventory-v1/source-inventory.json'
SOURCE='.training/quality-evaluation/finance-answerability/source-only-v1/source-only.jsonl'
ROOT_REVIEW='.training/quality-evaluation/finance-terms/source-inventory-review-20260911/root-review.json'
SYSTEMS=('hy7-baseline','hymt30-paging-v4')

def prepare(ev):
    inventory=ev.json(INVENTORY,'61d9b4a1535b2bcb0e67e593e975bc6945da987ec277cf4a8bd5ee9e2eff7930')
    sources=ev.lines(SOURCE,'2ed90d5a9f6250020ba0e73c1fae5a77ecd9f63032badf9ddee7b9a1668432bb')
    source_map={r['id']:r for r in sources}; review=ev.json(ROOT_REVIEW)
    l.require(review['sourceInventorySha256']==ev.files[INVENTORY] and review['sourceFileSha256']==ev.files[SOURCE],'source_review_identity')
    l.require(review['reviewedTermOccurrences']==review['revisedDenominator']==149 and review['changedSelections']==0,'source_review_scope')
    previous=list(l.base48(ev))+list(l.round2(ev)); rows=[]
    for cohort,folder in (('dev18','linguistic-dev-20260910'),('reading6','real-reading-check-20260910')):
        for system in SYSTEMS:
            run=f'.training/comparisons/{folder}/{system}'; path=run+'/predictions.jsonl'
            predictions=ev.lines(path)
            selected=[r for r in inventory['sources'] if r['cohort']==cohort]
            l.require(len(predictions)==len(selected) and {r['id'] for r in predictions}=={r['id'] for r in selected},'candidate_inventory')
            by_id={r['id']:r for r in predictions}
            for source_inventory in selected:
                sid=source_inventory['id']; source=source_map[sid]; pred=by_id[sid]
                l.require(pred['status']=='completed' and not pred['truncated'],'candidate_incomplete')
                l.require(l.sha(pred['translation'].encode('utf-8'))==pred['targetSha256'],'candidate_target_hash')
                linked=l.link_output(previous,sid,system,pred['targetSha256'])
                for field in ('source','context'):
                    l.require(l.sha(source[field].encode('utf-8'))==source_inventory[field+'Sha256']==linked[field+'Sha256'],'candidate_source_identity')
                rows.append(dict(**linked,source=source['source'],context=source['context'],translation=pred['translation'],
                    terms=source_inventory['occurrences'],automaticChecksPassed=pred['automaticChecksPassed'],
                    evidence=[ev.ref(path,sid),ev.ref(INVENTORY,sid),ev.ref(SOURCE,sid)]))
    l.require(len(rows)==48 and sum(len(r['terms']) for r in rows)==298,'packet_coverage')
    ev.verify_unchanged()
    return dict(version=VERSION,kind='manual_review_packet',systems=list(SYSTEMS),rows=rows,
        rootSourceReview=review,rootSourceReviewEvidence=ev.ref(ROOT_REVIEW),evidenceFiles=ev.files,
        humanReviewed=False,independentBlindReview=False,rootHadSeenPriorCandidateResults=True,
        trainingUseAllowed=False,finalHoldout=False,codeSha256=l.sha(Path(__file__).read_bytes()))

def validate(packet,judgments,packet_hash):
    l.require(packet['version']==judgments['version']==VERSION,'term_version')
    l.require(judgments['packetSha256']==packet_hash,'term_packet_hash')
    expected={(r['system'],t['occurrenceId']):(r,t) for r in packet['rows'] for t in r['terms']}
    l.require(len(judgments['rows'])==len(expected),'term_coverage')
    seen=set(); events=[]
    for j in judgments['rows']:
        key=(j['system'],j['occurrenceId']); l.require(key not in seen and key in expected,'term_duplicate_or_extra');seen.add(key)
        row,term=expected[key]
        l.require(j['sourceSha256']==row['sourceSha256'] and j['translationSha256']==row['translationSha256'],'term_text_identity')
        l.require(j['verdict'] in ('correct','incorrect','unresolved'),'term_verdict')
        l.require(isinstance(j['reasonKo'],str) and j['reasonKo'].strip(),'term_reason_required')
        l.require(isinstance(j['targetQuotes'],list) and (j['targetQuotes'] or j['verdict']!='correct'),'correct_term_evidence_required')
        l.require(all(isinstance(q,str) and q and q in row['translation'] for q in j['targetQuotes']),'term_quote_not_exact')
        span=term['span'];l.require(row['source'][span['start']:span['end']]==span['quote'],'term_source_quote')
        events.append(dict(version=l.VERSION,kind='term_judgment',reviewVersion=VERSION,
            **{k:row[k] for k in ('system','run','sourceId','outputId','sourceSha256','translationSha256','contextSha256','cohort','linkedReviewEventIds')},
            term=term,judgment=j,verdict=j['verdict'],sourceSpan=span,
            packetSha256=packet_hash,humanReviewed=False,trainingUseAllowed=False,independentHoldout=False,
            evidence=row['evidence']+[packet['rootSourceReviewEvidence']]))
    l.require(seen==set(expected),'term_missing')
    summaries=[]
    for system in packet['systems']:
        for cohort in ('dev18','reading6','all24'):
            rows=[e for e in events if e['system']==system and (cohort=='all24' or e['cohort']==cohort)]
            counts=Counter(e['verdict'] for e in rows);n=len(rows)
            summaries.append(dict(system=system,cohort=cohort,denominator=n,counts=dict(counts),
                correctRate=counts['correct']/n if n else None,
                termOnly95GatePassed=bool(n) and counts['correct']/n>=0.95 and counts['unresolved']==0,
                fullLearningReadinessAccepted=False))
    return events,dict(version=VERSION,kind='term_only_summary',rows=summaries,
        independentOccurrencesAssumed=False,humanReviewed=False,independentHoldout=False,
        stringMatchingUsedToJudgeMeaning=False,fullLearningReadinessAccepted=False)

def main():
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('--prepare',action='store_true')
    cli.add_argument('--packet',type=Path);cli.add_argument('--judgments',type=Path);cli.add_argument('--output',type=Path,required=True)
    args=cli.parse_args();out=args.output.resolve()
    l.require(out.is_relative_to(l.ROOT/'.training/quality-evaluation/finance-terms'),'owned_term_output_required')
    l.require(not out.exists(),'new_term_output_required')
    if args.prepare:
        l.require(args.packet is None and args.judgments is None,'prepare_inputs_not_allowed')
        packet=prepare(l.Evidence());out.mkdir(parents=True)
        with (out/'packet.json').open('xb') as f:f.write(l.packed(packet))
        print(json.dumps(dict(status='prepared',rows=len(packet['rows']),terms=sum(len(r['terms']) for r in packet['rows']))))
    else:
        l.require(args.packet and args.judgments,'packet_and_judgments_required')
        ev=l.Evidence();raw=ev.read(args.packet);packet=json.loads(raw);judgments=ev.json(args.judgments)
        l.require(l.sha(Path(__file__).read_bytes())==packet['codeSha256'],'frozen_term_code_changed')
        for path,digest in packet['evidenceFiles'].items():ev.read(path,digest)
        events,report=validate(packet,judgments,l.sha(raw));ev.verify_unchanged()
        for event in events:event['evidence'] += [ev.ref(args.packet),ev.ref(args.judgments,event['system']+'/'+event['term']['occurrenceId'])]
        out.mkdir(parents=True)
        with (out/'events.jsonl').open('xb') as f:
            for event in events:f.write(l.packed(event))
        report.update(evidenceFiles=ev.files,judgmentsSha256=ev.files[args.judgments.resolve().relative_to(l.ROOT).as_posix()])
        with (out/'summary.json').open('xb') as f:f.write(l.packed(report))
        print(json.dumps(dict(status='verified',events=len(events),rows=report['rows'])))

if __name__=='__main__':main()
