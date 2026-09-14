"""Read-only S1 artifact audit. No app/model imports or inference/selector code."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'content/model-comparison/input-preparation-v1'
LOCAL = ROOT / '.training/comparisons/input-preparation-v1/source-bundles'
INPUTS = [LOCAL / 'finance-public-inputs.json', BASE / 'finance-synthetic-inputs.json', BASE / 'general-inputs.json']
EVALUATIONS = [LOCAL / 'finance-public-evaluation.json', BASE / 'evaluation/finance-synthetic-evaluation.json', BASE / 'evaluation/general-evaluation.json']
HISTORY = [ROOT / 'content/training' / n for n in ('train.jsonl', 'dev.jsonl', 'evaluation.jsonl')]
HISTORY += [ROOT / '.training/datasets' / v / n for v in ('finance-v4', 'finance-v5') for n in ('train.jsonl','dev.jsonl','test.jsonl')]
HISTORY += [ROOT / 'content/model-comparison' / n for n in ('finance-probe-20260909.jsonl','linguistic-dev-20260910.jsonl','general-context-dev-20260911.jsonl','real-reading-check-20260910/sources.jsonl')]
HISTORY += [ROOT / '.training/comparisons/finance-quality-20260910/dataset.jsonl']
EXPECTED_IDS = [f'IP1-{d}{i:02d}' for d in ('F','G') for i in range(1,9)]
PUBLIC_RECEIPT = ROOT / '.training/verifications/input-preparation-v1-public-snapshot-20260913.json'
REQUIRED_ARTIFACTS = INPUTS + EVALUATIONS + HISTORY + [Path(__file__), PUBLIC_RECEIPT, ROOT/'.gitattributes']
REQUIRED_ARTIFACTS += [BASE / name for name in ('README.md','CONTRACT.md','CURRENT_INPUT_AUDIT.md',
    'audit-identity.json','sense-dictionary.json','DICTIONARY_SOURCES.md','author-review.json')]

def require(ok, message):
    if not ok:
        raise ValueError(message)

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()

def text_sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def relative(path):
    return path.resolve().relative_to(ROOT).as_posix()

def read(path):
    return json.loads(path.read_text('utf-8-sig'))

def artifact(path):
    return {'path':relative(path),'bytes':path.stat().st_size,'sha256':sha(path)}

def normalized(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    text = re.sub(r'\d+(?:[.,]\d+)*',' <number> ',text)
    return ' '.join(re.findall(r'[a-z]+|[가-힣]+|<number>|\d+',text))

def overlap(a, b):
    if a == b: return 'exact-or-number-template'
    if min(len(a),len(b)) < 35: return None
    if a in b or b in a: return 'contained-source'
    # Same frozen v5 near-template thresholds; containment is an extra S1 audit.
    if 2*min(len(a),len(b))/(len(a)+len(b)) < .82: return None
    matcher = SequenceMatcher(None,a,b,autojunk=False)
    if matcher.quick_ratio() < .82: return None
    tokens, other = set(a.split()), set(b.split())
    jaccard = len(tokens & other) / max(1,len(tokens | other))
    ratio = matcher.ratio()
    return 'near-template' if ratio >= .92 or (jaccard >= .85 and ratio >= .82) else None

def validate_freeze(frozen):
    require(frozen['status']=='frozen','not_frozen')
    paths = [entry['path'] for entry in frozen['artifacts']]
    require(len(paths)==len(set(paths)) and set(paths)=={relative(p) for p in REQUIRED_ARTIFACTS},'frozen_artifact_set_missing_extra_or_duplicate')
    for entry in frozen['artifacts']:
        p = (ROOT / entry['path']).resolve()
        require(p.is_relative_to(ROOT),'frozen_path_outside_workspace')
        require(p.is_file() and sha(p)==entry['sha256'] and p.stat().st_size==entry['bytes'], 'frozen_artifact_changed:'+entry['path'])

def audit(check_frozen=True):
    frozen = read(BASE / 'freeze-manifest.json') if check_frozen else None
    if frozen: validate_freeze(frozen)
    units = [u for p in INPUTS for u in read(p)['inputUnits']]
    evaluations = [e for p in EVALUATIONS for e in read(p)['evaluations']]
    require([u['id'] for u in units] == EXPECTED_IDS,'input_ids')
    require([e['id'] for e in evaluations] == EXPECTED_IDS,'evaluation_ids')
    require(Counter(u['domain'] for u in units)=={'finance':8,'general':8},'domain_count')
    forbidden = {'evaluations','evaluationUnits','pairId','propositions','terms','questions','expectedAnswerKo','expectedMeaningKo','allowedKorean','sourceEvidence','termOccurrences','targetTranslation'}
    def check_encoding(value):
        if isinstance(value,str): require('\ufffd' not in value and not re.search(r'\?{3,}',value),'damaged_utf8_text')
        elif isinstance(value,dict):
            for v in value.values(): check_encoding(v)
        elif isinstance(value,list):
            for v in value: check_encoding(v)
    for p in INPUTS + EVALUATIONS + [BASE/'author-review.json',BASE/'sense-dictionary.json']: check_encoding(read(p))
    def reject_annotations(value):
        if isinstance(value,dict):
            require(not forbidden.intersection(value),'annotation_in_source_bundle')
            for v in value.values(): reject_annotations(v)
        elif isinstance(value,list):
            for v in value: reject_annotations(v)
    source_rows, counters, pairs = [], Counter(), Counter()
    block_identity, version_owner, block_owner = {}, {}, {}
    for u, e in zip(units,evaluations):
        reject_annotations(u)
        require(u['provenance']['humanReviewed'] is False and e['humanReviewed'] is False,'human_review_mislabel')
        d = u['document']; blocks = d['blocks']; byid = {b['id']:b for b in blocks}
        require(d['sourceVersionId'] not in version_owner or version_owner[d['sourceVersionId']]==d['resourceId'],'version_resource_mismatch')
        version_owner[d['sourceVersionId']]=d['resourceId']
        require(len(byid)==len(blocks) and len(set(b['order'] for b in blocks))==len(blocks),'duplicate_block_or_order')
        require([b['order'] for b in blocks]==sorted(b['order'] for b in blocks),'block_order')
        require(u['targetBlockId'] in byid,'target_membership')
        target = byid[u['targetBlockId']]; source = target['text']
        require(target['kind']=='paragraph','target_type')
        require({target['order']-1,target['order']+1}.issubset({b['order'] for b in blocks}),'c0_neighbors_missing')
        for b in blocks:
            require(b['id'] not in block_owner or block_owner[b['id']]==d['sourceVersionId'],'block_version_mismatch')
            block_owner[b['id']]=d['sourceVersionId']
            require(isinstance(b['text'],str) and b['text'] and '\x00' not in b['text'],'invalid_source_text')
            require(b['textSha256']==text_sha(b['text']),'block_text_hash')
            key=(d['sourceVersionId'],b['id'])
            identity=(d['resourceId'],b['order'],b['kind'],b['textSha256'],json.dumps(b.get('metadata'),sort_keys=True))
            require(key not in block_identity or block_identity[key]==identity,'inconsistent_shared_block')
            block_identity[key]=identity
        require(e['targetTextSha256']==text_sha(source),'evaluation_target_hash')
        require(len(e['propositions'])==3 and all(p['core'] for p in e['propositions']),'core_propositions')
        require(len(e['questions'])==2 and sum(q['core'] for q in e['questions'])==1,'questions')
        for row in e['propositions']+e['questions']+e['terms']:
            require(row['sourceQuote'] in source,'target_quote_missing:'+u['id'])
            for key in ('expectedMeaningKo','questionKo','expectedAnswerKo','meaningKo'):
                if key in row: require(re.search(r'[가-힣]',row[key]),'korean_annotation_missing:'+u['id'])
            for ev in row.get('sourceEvidence',[]):
                require(ev['blockId'] in byid and ev['quote'] in byid[ev['blockId']]['text'],'evidence_membership_or_quote')
        for q in e['questions']:
            require(q.get('allowedKoreanContext','')=='','unexpected_answer_context')
            require(bool(q['questionKo']) and bool(q['expectedAnswerKo']),'empty_question')
        for t in e['terms']:
            require(source[t['sourceStartCodepoint']:t['sourceEndCodepoint']]==t['sourceTerm'],'term_span')
            require(bool(t['allowedKorean']) and bool(t['meaningKo']),'term_annotation')
        if e.get('pairId'): pairs[e['pairId']] += 1
        counters.update({'units':1,'corePropositions':3,'questions':2,'coreQuestions':1,'termOccurrences':len(e['terms'])})
        public = u['provenance']['kind']=='stored-public-source'
        if public:
            original=(ROOT / u['provenance']['originalPath']).resolve()
            require(original.is_relative_to(ROOT/'data/originals'),'public_original_location')
            require(sha(original)==u['provenance']['originalFileSha256'],'public_original_hash')
        source_rows.append({'id':u['id'],'domain':u['domain'],'provenance':u['provenance']['kind'],
            'resourceId':d['resourceId'],'sourceVersionId':d['sourceVersionId'],'targetBlockId':target['id'],
            'targetOrder':target['order'],'targetTextSha256':text_sha(source),'words':len(source.split()),
            'blockCount':len(blocks),'terms':len(e['terms']), 'publicSourceUrl':u['provenance'].get('sourceUrl'),
            'sourceGroup':'public-'+d['resourceId'] if public else e['pairId']})
    require(dict(counters)=={'units':16,'corePropositions':48,'questions':32,'coreQuestions':16,'termOccurrences':39},'denominators')
    if frozen:
        require(frozen['counts']==dict(counters) and frozen['inputInventory']==source_rows,'frozen_inventory_or_counts_changed')
    require(len(pairs)==6 and set(pairs.values())=={2},'minimal_pair_groups')
    receipt=read(PUBLIC_RECEIPT)
    require(receipt['status']=='passed' and receipt['sourceBundleSha256']==sha(INPUTS[0]) and receipt['unitCount']==4
        and receipt['blockReferences']==17 and receipt['databaseWrites']==0 and not receipt['personalTablesRead'],'public_snapshot_receipt')
    require([r['id'] for r in receipt['units']]==EXPECTED_IDS[:4] and all(r['databaseRowsEqualSnapshot'] for r in receipt['units']),'public_snapshot_receipt_units')
    dictionary=read(BASE/'sense-dictionary.json')
    require(len(dictionary['entries'])==8 and sum(len(e['senses']) for e in dictionary['entries'])==24,'dictionary_count')
    source_ids={s['id'] for s in dictionary['sources']}
    require(len(source_ids)==11,'dictionary_sources')
    for entry in dictionary['entries']:
        for sense in entry['senses']:
            require(set(sense['source_ids']).issubset(source_ids),'dictionary_source_reference')
            require(sense.get('prompt_action') in ('hint_financial','suppress_nonfinancial_hint'),'dictionary_prompt_action')
    # Machine-only source comparison. Never emit historical source/target text or test answers.
    history=[]
    for p in HISTORY:
        require(p.is_file(),'historical_file_missing:'+relative(p))
        is_test = p.name in ('evaluation.jsonl','test.jsonl')
        for line in p.read_text('utf-8-sig').splitlines():
            if not line.strip(): continue
            row=json.loads(line)
            require(isinstance(row.get('source'),str),'historical_source_schema:'+relative(p))
            history.append((relative(p),str(row.get('id')),normalized(row['source']),is_test))
            if isinstance(row.get('context'),str) and row['context'].strip():
                history.append((relative(p),str(row.get('id'))+':context',normalized(row['context']),is_test))
    historical_matches, test_matches = [], []
    for u in units:
        for b in u['document']['blocks']:
            a=normalized(b['text'])
            for p, oldid, old, is_test in history:
                reason=overlap(a,old)
                if reason:
                    m={'unitId':u['id'],'blockId':b['id'],'isTarget':b['id']==u['targetBlockId'],
                       'historicalFile':p,'reason':reason}
                    # test ID is intentionally not rendered or used for editing.
                    if is_test: test_matches.append(m)
                    else: m['historicalId']=oldid; historical_matches.append(m)
    require(not test_matches,'consumed_test_source_overlap:'+json.dumps(test_matches,ensure_ascii=False))
    ungrouped=[]
    for i,u in enumerate(units):
        source=next(b['text'] for b in u['document']['blocks'] if b['id']==u['targetBlockId'])
        for j in range(i):
            other=units[j];other_source=next(b['text'] for b in other['document']['blocks'] if b['id']==other['targetBlockId'])
            reason=overlap(normalized(source),normalized(other_source))
            if reason and source_rows[i]['sourceGroup']!=source_rows[j]['sourceGroup']:
                ungrouped.append([u['id'],other['id'],reason])
    require(not ungrouped,'ungrouped_internal_duplicates')
    identity=read(BASE/'audit-identity.json')
    for f in identity['files']:
        p=Path(f['path']);p=p if p.is_absolute() else ROOT/p
        require(p.is_file() and p.stat().st_size==f['size'] and sha(p)==f['sha256'],'registered_or_audited_file_changed:'+f['path'])
    return {'status':'passed','scope':'S1 data, references, local registered-file hashes; not selector, tokenizer, inference, app or quality test',
        'checkedAt':datetime.now(timezone.utc).isoformat(),'counts':dict(counters),'minimalPairGroups':dict(pairs),
        'dictionary':{'entries':8,'senses':24,'sources':11},'inputInventory':source_rows,
        'sourceAndEvaluationFiles':[artifact(p) for p in INPUTS+EVALUATIONS],
        'leakageAudit':{'historicalFiles':[artifact(p) for p in HISTORY],'historicalSourceOrContextRows':len(history),
            'consumedTestOverlap':0,'historicalDevelopmentMatches':historical_matches,'ungroupedInternalDuplicates':0,
            'policy':'numeric-masked exact + containment (minimum35 chars) + v5 near-template thresholds; full target and retained context blocks; machine-only consumed-test source reads, no answers/output to model',
            'limitation':'Source fingerprint audit cannot establish semantic novelty. R01/R05 and all known error families remain exposed development groups; independent holdout excludes whole groups.'},
        'registeredFilesReverified':len(identity['files']),'modelInferenceCalls':0,'nativeStarts':0,'tokenizerCalls':0,
        'productionDatabaseWrites':0,'personalRecordReads':0,'frozenManifestChecked':check_frozen}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pre-freeze',action='store_true',help='Validate prepared artifacts before a freeze manifest exists; does not create a freeze.')
    parser.add_argument('--output',type=Path,help='Write a NEW audit JSON under .training/verifications; existing files are refused.')
    args=parser.parse_args()
    output=args.output.resolve() if args.output else None
    if output:
        require(output.is_relative_to(ROOT/'.training/verifications'),'report_path_outside_verifications')
        require(not output.exists(),'report_already_exists')
    report=audit(check_frozen=not args.pre_freeze)
    if output:
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf-8',newline='\n') as stream:
            stream.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('status','counts','dictionary','registeredFilesReverified','modelInferenceCalls','frozenManifestChecked')},ensure_ascii=False))

if __name__=='__main__':
    main()
