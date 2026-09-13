"""Use a verified v5 FP32 checkpoint locally; no glossary, memory or deployment."""
from pathlib import Path
import re
import sys
import json

import infer
import train_v5 as v5

IDENTITY_KEYS = v5.IDENTITY_KEYS


def verified_model(run_id):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', run_id):
        raise infer.InferenceError('Invalid run ID')
    run_dir=v5.WORK_ROOT/'runs'/run_id
    manifest=infer.read_json(run_dir/'manifest.json')
    training=infer.read_json(run_dir/'training-summary.json')
    if (manifest.get('runId')!=run_id or manifest.get('scriptVersion')!=v5.VERSION
            or manifest.get('scriptSha256')!=infer.sha256(Path(v5.__file__))
            or manifest.get('gate')!=v5.POLICY
            or manifest.get('identitySha256')!=v5.canonical_hash({key:manifest.get(key) for key in IDENTITY_KEYS})
            or training.get('runId')!=run_id or training.get('baseWeightFileSha256')!=v5.PARENT_WEIGHT_SHA256
            or training.get('completedUpdates',0)<1 or training.get('weightEvidence',{}).get('changedTensorCount',0)<1):
        raise infer.InferenceError('The run does not identify a completed v5 model and frozen policy')
    parent,files,lineage=v5.verify_parent()
    if (manifest.get('lineage')!=lineage or manifest.get('baseFiles')!=files
            or manifest.get('originalBase')!=lineage['originalBase']
            or infer.local_path(manifest['basePath'],v5.WORK_ROOT)!=parent
            or manifest.get('dependencyFiles')!=v5.dependency_hashes()):
        raise infer.InferenceError('V5 parent lineage or training dependency integrity changed')
    model=infer.local_path(training['modelPath'],run_dir)
    selected=infer.local_path(manifest['selectedCheckpoint'],run_dir)
    checkpoint=infer.read_json(selected/'checkpoint.json')
    hashes={}
    for name in v5.MODEL_FILES:
        current,original=model/name,selected/'model'/name
        if current.is_symlink() or original.is_symlink() or infer.sha256(current)!=infer.sha256(original):
            raise infer.InferenceError('V5 selected model/tokenizer integrity changed')
        hashes[name]=infer.sha256(current)
    weight=hashes['model.safetensors']
    if (weight!=training.get('trainedWeightFileSha256') or weight!=checkpoint.get('modelSha256')
            or checkpoint.get('step')!=training.get('selectedStep')):
        raise infer.InferenceError('V5 selected checkpoint identity changed')
    try:
        v5.verify_model_inventory(run_dir,manifest,training)
    except ValueError as error:
        raise infer.InferenceError(str(error)) from error
    for name in ('source.spm','target.spm','vocab.json','target_vocab.json','tokenizer_config.json','special_tokens_map.json'):
        if hashes[name]!=files[name]:
            raise infer.InferenceError('V5 tokenizer differs from the frozen parent')
    infer.validate_tokenizer_files(model)
    evaluation_path=run_dir/'evaluation-summary.json'
    evaluation=infer.read_json(evaluation_path) if evaluation_path.exists() else None
    if evaluation and (evaluation.get('runId')!=run_id or evaluation.get('trainedWeightFileSha256')!=weight
            or evaluation.get('baseWeightFileSha256')!=v5.PARENT_WEIGHT_SHA256
            or evaluation.get('gatePolicy')!=v5.POLICY or infer.local_path(evaluation['modelPath'],run_dir)!=model
            or evaluation.get('testDataSha256')!=manifest.get('datasets',{}).get('test',{}).get('sha256')
            or not evaluation.get('testDataSha256') or evaluation.get('devGate')!=training.get('devGate')):
        raise infer.InferenceError('V5 evaluation identity differs from the model')
    if evaluation:
        ledger_path=v5.engine.test_ledger(manifest)
        ledger=infer.read_json(ledger_path) if ledger_path.exists() else {}
        if (ledger.get('status')!='complete' or ledger.get('runId')!=run_id
                or ledger.get('modelSha256')!=weight or ledger.get('testDataSha256')!=evaluation['testDataSha256']
                or ledger.get('summaryPath')!=infer.relative(evaluation_path)):
            raise infer.InferenceError('V5 final evaluation completion ledger differs')
    eligible=bool(evaluation and evaluation.get('promotionEligible')
                  and evaluation.get('devGate',{}).get('passed') and evaluation.get('testGate',{}).get('passed'))
    return {'manifest':manifest,'training':training,'path':model,'weightHash':weight,
            'fileHashes':{k:v for k,v in hashes.items() if k!='model.safetensors'},
            'promotionEligible':eligible,'evaluationStatus':'passed-local-evaluation' if eligible else 'experimental',
            'testWasEvaluated':evaluation is not None,'evaluation':evaluation}


if __name__=='__main__':
    # Reuse the fixed FP32 tokenizer/generation/CLI implementation. This process
    # only substitutes v5 lineage verification; legacy infer.py stays unchanged.
    infer.verified_model=verified_model
    try:
        infer.main()
    except Exception as error:
        message=str(error) if isinstance(error,infer.InferenceError) else 'V5 local inference failed; inspect the run and installation.'
        print(json.dumps({'error':{'code':'LOCAL_V5_INFERENCE_FAILED','type':type(error).__name__,'message':message[:300]}}))
        sys.exit(1)
