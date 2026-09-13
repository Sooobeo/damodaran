"""Publish reviewed v5 datasets once; keep source text and review evidence local."""
from collections import Counter
import json
from pathlib import Path

import dataset_v5 as d
from dataset_io import write_outputs_once
import train
from train_v5 import leakage_issues


def rows(path):
    return [json.loads(line) for line in path.read_text('utf-8-sig').splitlines() if line.strip()]


def reviewed(name, split, catalog):
    return d.reviewed_input(name, split, catalog)


def main():
    outputs = ('train.jsonl', 'dev.jsonl', 'test.jsonl', 'dataset-manifest.json')
    if any((d.DEST/name).exists() for name in outputs):
        raise ValueError('Final v5 data already exists; do not overwrite the experiment')
    catalog = d.load_catalog()
    a, ai = reviewed('train-a', 'train', catalog)
    b, bi = reviewed('train-b', 'train', catalog)
    dev, di = reviewed('dev', 'dev', catalog)
    test, ti = reviewed('test', 'test', catalog)
    new = a+b
    d.validate_coverage({'train-a':a, 'train-b':b, 'dev':dev, 'test':test}, catalog)
    replay_path=d.ROOT/'.training/datasets/finance-v4/train.jsonl'
    if train.sha256(replay_path)!='4cb6a18b165af96e746b7e000404e0cd7aa9ec9dd97872dd5911863475c0fbd7':
        raise ValueError('Frozen v4 training replay changed')
    replay, excluded = [], []
    for row in rows(replay_path):
        missing=[t['id'] for t in d.catalog_matches(row['source'],catalog)
                 if train.normalized_term(t['target']) not in train.normalized_term(row['target'])] if row['domain']=='finance' else []
        if missing:
            excluded.append({'id':row['id'],'reason':'replay-reference-uses-other-wording-than-v5-canonical','termIds':missing})
        else:
            replay.append({**row,'split':'train','forbiddenTerms':row.get('forbiddenTerms',[]),
                           'replayOrigin':'.training/datasets/finance-v4/train.jsonl'})
    report=d.validate_rows(replay,catalog,'train',require_canonical=False)
    if not report['passed']:
        raise ValueError('Replay integrity failed')
    data={'train': [d.enrich(r) for r in replay+new], 'dev':[d.enrich(r) for r in dev], 'test':[d.enrich(r) for r in test]}
    identifiers=[r['id'] for values in data.values() for r in values]
    if len(identifiers)!=len(set(identifiers)):
        raise ValueError('Duplicate ID across split files')
    statistics={}
    issues=leakage_issues(data,statistics)
    # Check old consumed holdouts mechanically, without exposing their text to
    # authors or selection. No old holdout is ever included in new training.
    old_files=[d.ROOT/'content/training/dev.jsonl',d.ROOT/'content/training/evaluation.jsonl',
               d.ROOT/'.training/datasets/finance-v4/dev.jsonl', d.ROOT/'.training/datasets/finance-v4/test.jsonl',
               d.ROOT/'.training/datasets/finance-v4/polysemy-probe.jsonl']
    # Replayed rows already coexisted with old development splits; test new
    # authored training only against old holdouts, preserving their source IDs.
    old=[]
    for file in old_files:
        old.extend({**r,'id':'old:'+file.name+':'+r['id']} for r in rows(file))
    historic=[item for item in leakage_issues({'new-train':new,'old-heldout':old})
              if item['first']['split']!=item['second']['split']]
    validation={'passed':not issues and not historic,'issues':issues,'historicHoldoutIssues':historic,'statistics':statistics}
    if not validation['passed']:
        # Diagnostic is metadata only, never held-out source or reference text.
        train.write_json(d.DEST/'prepublication-leakage.json',validation)
        raise ValueError('Leakage preflight failed; inspect IDs only in prepublication-leakage.json')
    payloads={name+'.jsonl': ''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in values) for name,values in data.items()}
    manifest={'version':d.PUBLICATION_VERSION,'createdAt':train.now(),'humanReviewed':False,
              'catalogSha256':d.CATALOG_SHA256,'annotationPolicySha256':train.sha256(d.DEST/'annotation-policy.json'),
              'codeFiles':{name:train.sha256(Path(__file__).with_name(name)) for name in d.PUBLICATION_CODE_FILES},
              'reviewedInputs':{'train-a':ai,'train-b':bi,'dev':di,'test':ti},
              'replay':{'path':train.relative(replay_path),'sha256':train.sha256(replay_path),'retained':len(replay),'excluded':excluded},
              'historicHoldoutFiles':[{'path':train.relative(f),'sha256':train.sha256(f)} for f in old_files],
              'validation':validation,'files':{name:{'count':len(values),'domains':dict(Counter(r['domain'] for r in values)),
                 'sourceWords':sum(len(r['source'].split()) for r in values),'sha256':d.digest(payloads[name+'.jsonl'])}
                 for name,values in data.items()}}
    payloads['dataset-manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2)+'\n'
    write_outputs_once(d.DEST,payloads)
    print(json.dumps({'files':manifest['files'],'replayRetained':len(replay),'replayExcluded':len(excluded)},indent=2))


if __name__=='__main__':
    main()
