"""Prepare/freeze C0-C3 prompts from S1 source-only files; never generate text."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/model-comparison'))
import run_hymt as baseline
from input_preparation_v1.context_selection import validate_units, sanitize_unit, c0_context, select_context, text_sha256
from input_preparation_v1.sense_selection import select_senses
from input_preparation_v1.tokenizer_client import NativeTokenizer, write_json, utc

VERSION='input-preparation-v1-source-adapter-v1'
S1=ROOT/'content/model-comparison/input-preparation-v1'
FREEZE_SHA='b70304bbd8a3961096f6d07116d51196ece3c4416977f0c0899cf712b7ada4cf'
SOURCE_PATHS=(
    '.training/comparisons/input-preparation-v1/source-bundles/finance-public-inputs.json',
    'content/model-comparison/input-preparation-v1/finance-synthetic-inputs.json',
    'content/model-comparison/input-preparation-v1/general-inputs.json')
MAX_TOKENS=4095

def require(value,code):
    if not value:raise ValueError(code)

def file_hash(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):result.update(chunk)
    return result.hexdigest()

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

class SourceReadAudit:
    """Record file reads and refuse annotations, old outputs, or personal DBs."""
    def __init__(self):self.paths=set()
    def __call__(self,event,args):
        if event!='open' or not isinstance(args[0],(str,bytes)):return
        mode=args[1]
        if not isinstance(mode,str) or not ('r' in mode or '+' in mode):return
        path=Path(args[0]).resolve()
        if not path.is_relative_to(ROOT):return
        relative=path.relative_to(ROOT).as_posix();lower=relative.lower()
        require(not any(x in lower for x in ('/evaluation/','-evaluation.json','quality-evaluation/','/holdout','library.sqlite')),
            'forbidden_annotation_or_personal_data_read')
        if lower.startswith('.training/') and lower.endswith(('.json','.jsonl')):
            require(relative in SOURCE_PATHS or lower.startswith('.training/comparisons/input-preparation-v1/s2-prepared/'),
                'historical_or_unapproved_data_read')
        self.paths.add(relative)

def read_inputs():
    """No evaluation loader is imported or opened. Paths are fixed, hash-bound."""
    require(file_hash(S1/'freeze-manifest.json')==FREEZE_SHA,'s1_freeze_changed')
    manifest=json.loads((S1/'freeze-manifest.json').read_text('utf-8'))
    artifacts={a['path']:a for a in manifest['artifacts']}
    reads=[]
    def frozen_read(path):
        path=Path(path);relative=path.relative_to(ROOT).as_posix()
        require(relative in artifacts,'unfrozen_input')
        raw=path.read_bytes();require(hashlib.sha256(raw).hexdigest()==artifacts[relative]['sha256'],'frozen_input_changed')
        reads.append({'path':relative,'sha256':artifacts[relative]['sha256']})
        return json.loads(raw)
    units=[]
    for relative in SOURCE_PATHS:
        bundle=frozen_read(ROOT/relative)
        require(set(bundle).issubset({'schemaVersion','inputUnits','language'})
            and bundle['schemaVersion']=='input-preparation-source-v1','source_bundle_schema')
        units.extend(bundle['inputUnits'])
    validate_units(units);require(len(units)==16,'source_unit_count')
    dictionary=frozen_read(S1/'sense-dictionary.json')
    audit=frozen_read(S1/'audit-identity.json')
    # Source/runtime contracts can be inspected; historical translations and
    # their judgments remain outside this process. Native child checks weights.
    for f in audit['files']:
        if f['category'] in ('input-path-code','codeFiles','python-jinja','active-manifest','registration-manifest','catalog'):
            require(file_hash(ROOT/f['path'])==f['sha256'],'registered_input_identity_changed')
    catalog_file=next(f for f in audit['files'] if f['category']=='catalog')
    catalog=json.loads((ROOT/catalog_file['path']).read_text('utf-8'))
    require(len(catalog['terms'])==54,'registered_catalog_count')
    execution=audit['registeredIdentity']['execution']
    require(execution['sampling']==baseline.SAMPLING and execution['runtimeOverrides']==baseline.OVERRIDES
            and execution['threads']==4 and execution['contextSize']==8192,'shared_execution_changed')
    template,metadata=baseline.gguf_contract(ROOT/next(f['path'] for f in audit['files'] if f['category']=='modelFiles'))
    return units,dictionary,catalog['terms'],template,{'sourceAndDictionaryReads':reads,'s1FreezeSha256':FREEZE_SHA,
        'registeredIdentity':audit['registeredIdentity'],'catalogFile':catalog_file,'ggufContract':metadata,
        'annotationFilesOpened':0,'historicalOutputsOpened':0,'databaseOpened':False}

def user_prompt(source,context,hints):
    lines=[f'{h["source"]} → {h["target"]} (뜻: {h["definition"]})' for h in hints]
    return baseline.CONTEXT_INSTRUCTION+'\n\n[Background Information]\n'+(context or 'None provided.')+\
        '\n\n[Conditional terminology references]\n'+('\n'.join(lines) or 'None matched.')+'\n\n[Source Text]\n'+source

def legacy_hints(source,terms):
    matches=baseline.term_matches(source,terms);seen=set();hints=[]
    for match in matches:
        if match['id'] not in seen:hints.append(match);seen.add(match['id'])
    return {'hints':hints,'decisions':matches,'selectorVersion':'registered-term-matcher-v1'}

def retain_prior_hints(previous,updated):
    """Budget trimming may remove hints, never resolve ambiguity into new ones."""
    def key(h):return tuple(h.get(k) for k in ('source','senseId','target','definition','start','end'))
    allowed={key(h) for h in previous['hints']}
    suppressed=[h for h in updated['hints'] if key(h) not in allowed]
    updated['hints']=[h for h in updated['hints'] if key(h) in allowed]
    suppressed_keys={key(h) for h in suppressed}
    for decision in updated['decisions']:
        hint=decision.get('renderedHint')
        if hint and key(hint) in suppressed_keys:
            decision['budgetSuppressedHint']=hint;decision['renderedHint']=None
            decision['originalPromptAction']=decision.get('promptAction')
            decision['promptAction']='budget_suppressed_new_hint'
    updated['budgetSuppressedHints']=previous.get('budgetSuppressedHints',[])+[
        {**h,'reason':'no_new_hints_after_context_budget_removal'} for h in suppressed]
    return updated

def prepare_one(clean,configuration,terms,dictionary,template,token_count):
    """Pure source-only boundary. IDs/domain/provenance cannot pick a rule."""
    require(configuration in ('C0','C1','C2','C3'),'unknown_configuration')
    clean=sanitize_unit(clean);document=clean['document']
    target=next(b for b in document['blocks'] if b['id']==clean['targetBlockId']);source=target['text']
    ref={'sourceVersionId':document['sourceVersionId'],'blockId':target['id'],'order':target['order']}
    def senses(fragments):return select_senses(source,fragments,dictionary,source_ref=ref)
    choice=legacy_hints(source,terms) if configuration in ('C0','C1') else senses([])
    def render(context,current=None):return baseline.render_prompt(template,user_prompt(source,context,(current or choice)['hints']))
    # Reject every configuration for this unit if target+instructions+hints
    # alone do not fit. C0/C2 retain their legacy context without new trimming.
    require(token_count(render(''))<=MAX_TOKENS,'input_over_budget')
    if configuration in ('C0','C2'):
        context=c0_context(clean)
        if configuration=='C2':choice=senses(context['fragments'])
        context['promptTokens']=token_count(render(context['text']))
        require(context['promptTokens']<=MAX_TOKENS,'input_over_budget')
    else:
        context=select_context(clean,token_count,render)
        if configuration=='C3':
            choice=senses(context['fragments']);removals=[]
            while token_count(render(context['text']))>MAX_TOKENS:
                require(bool(context['fragments']),'input_over_budget')
                removed=context['fragments'].pop()
                context['excluded'].append({**removed,'reason':'budget_excluded','selectionReason':removed['reason'],
                    'budgetStage':'after_final_context_senses'})
                removals.append(removed['blockId'])
                context['text']='\n'.join(f['text'] for f in context['fragments'])
                # Recompute only from the remaining citations; removed context
                # and hints are never added back in an optimization loop.
                choice=retain_prior_hints(choice,senses(context['fragments']))
            context['postSenseRemovals']=removals
        context['promptTokens']=token_count(render(context['text']))
    content=user_prompt(source,context['text'],choice['hints']);prompt=baseline.render_prompt(template,content)
    if configuration in ('C0','C1'):
        expected,matches=baseline.build_user_prompt({'source':source,'context':context['text']},'contextual',terms)
        require(content==expected and choice['decisions']==matches,'c0_registered_prompt_mismatch')
    require(prompt.endswith('\n\n[Source Text]\n'+source+'<|extra_0|>'),'target_changed')
    tokens=token_count(prompt);require(tokens==context['promptTokens'] and tokens<=MAX_TOKENS,'prompt_budget_mismatch')
    return {'configuration':configuration,'resourceId':document['resourceId'],'sourceVersionId':document['sourceVersionId'],
        'targetBlockId':target['id'],'source':source,'sourceSha256':target['textSha256'],
        'context':context,'terminology':choice,'userPrompt':content,'prompt':prompt,'promptSha256':text_sha256(prompt),
        'promptTokens':tokens,'contextSize':8192,'outputTokenReserve':4096,'withinBudget':True}

def new_attempt():
    base=ROOT/'.training/comparisons/input-preparation-v1/s2-prepared'
    require(not any(p.is_symlink() for p in (base,*base.parents)),'output_symlink')
    base.mkdir(parents=True,exist_ok=True)
    for i in range(1,1000):
        output=base/f'attempt-{i:03d}'
        try:output.mkdir();return output
        except FileExistsError:continue
    raise ValueError('attempt_limit')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',action='store_true');args=parser.parse_args()
    require(args.run,'explicit_run_required')
    read_audit=SourceReadAudit();sys.addaudithook(read_audit)
    units,dictionary,terms,template,identity=read_inputs();output=new_attempt()
    code=[p for p in sorted(Path(__file__).parent.glob('*.py')) if not p.name.startswith(('relation','run_relation','test_relation','verify_relation'))]
    code_snapshot=[{'path':p.relative_to(ROOT).as_posix(),'sha256':file_hash(p)} for p in code]
    write_json(output/'preflight.json',{'version':VERSION,'createdAt':utc(),'code':code_snapshot,'identity':identity,
        'plannedPrompts':64,'plannedGenerationCalls':0,'sourceOnly':True})
    rows=[]
    try:
        with NativeTokenizer(output) as tokenizer:
            for unit in units:
                clean=sanitize_unit(unit);group=[]
                for configuration in ('C0','C1','C2','C3'):
                    row=prepare_one(clean,configuration,terms,dictionary,template,tokenizer.count)
                    # Reporting metadata is attached only after selection/render.
                    row.update(id=unit['id'],domain=unit['domain'],provenance=unit['provenance'])
                    token_ids=tokenizer.tokens(row['prompt']);row['tokenIds']=token_ids
                    row['tokenIdsSha256']=text_sha256(json.dumps(token_ids,separators=(',',':')))
                    group.append(row)
                rows.extend(group)
        require(len(rows)==64,'incomplete_prompt_set')
        require(all(file_hash(ROOT/f['path'])==f['sha256'] for f in code_snapshot),'adapter_changed_during_preparation')
        with (output/'prompts.jsonl').open('x',encoding='utf-8',newline='\n') as stream:
            for row in rows:stream.write(canonical(row)+'\n')
        write_json(output/'source-read-audit.json',{'version':VERSION,'scope':'parent_python_open_audit',
            'files':sorted(read_audit.paths),'annotationFilesOpened':0,'historicalOutputsOpened':0,'databaseOpened':False,
            'childReadScope':'vocab_worker independently reads pinned audit/header, model and runtime; no data/evaluation loader'})
        artifacts=[{'path':p.name,'sha256':file_hash(p),'bytes':p.stat().st_size} for p in sorted(output.iterdir()) if p.is_file()]
        report={'version':VERSION,'completedAt':utc(),'status':'prepared','stage':'S2','units':16,'prompts':64,
            'translationGenerations':0,'modelWeightTensorLoads':0,'nativeVocabularyLoads':1,'appDeploymentChanged':False,
            'annotationFilesOpened':0,'sourceUnchanged':True,'c0ExactPromptParity':True,'nativeServerEndpointParity':'pending_S4',
            'maxPromptTokens':MAX_TOKENS,'configurationStats':{},'code':code_snapshot,'artifacts':artifacts}
        for config in ('C0','C1','C2','C3'):
            subset=[r for r in rows if r['configuration']==config]
            report['configurationStats'][config]={'prompts':len(subset),'minTokens':min(r['promptTokens'] for r in subset),
                'maxTokens':max(r['promptTokens'] for r in subset),'totalTokens':sum(r['promptTokens'] for r in subset),
                'hintLines':sum(len(r['terminology']['hints']) for r in subset),
                'budgetExclusions':sum(sum(f['reason']=='budget_excluded' for f in r['context']['excluded']) for r in subset)}
        write_json(output/'manifest.json',report)
        print(json.dumps({'status':'prepared','path':output.relative_to(ROOT).as_posix(),'manifestSha256':file_hash(output/'manifest.json'),
            'configurationStats':report['configurationStats']},ensure_ascii=False))
    except BaseException as error:
        write_json(output/'failure.json',{'status':'failed','code':type(error).__name__,'reason':str(error),'at':utc(),'generationCalls':0})
        raise

if __name__=='__main__':main()
