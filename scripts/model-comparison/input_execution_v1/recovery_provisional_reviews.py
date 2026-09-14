"""Provisional source packets for four completed rows while recovery continues.

No formal S5 approval, QA packet, score, candidate, model or output rewrite.
Per-output evidence is validated; complete cohort validation remains pending.
"""
from pathlib import Path
import argparse,json,sys
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/model-comparison'))
from input_execution_v1 import evaluation as e,provisional_reviews as provisional
from input_execution_v1 import recovery_cohort as cohort
from input_execution_v1 import producer as original

VERSION='input-execution-v1-recovery-provisional-source-v1'
RECOVERY=ROOT/'.training/comparisons/input-preparation-v1/s4-recovery/recovery-attempt-001'
BASE=ROOT/'.training/quality-evaluation/input-preparation-v1/s5-recovery-source-drafts-20260914'
EXECUTION_FREEZE=ROOT/'content/model-comparison/input-execution-v1/recovery-execution-freeze-v1.json'
EXECUTION_SHA='de5ae401d39bee9a263eb6d4bfe08bd3e3ac97db554ca9f3c6eac6c6066a83ec'

def prepare(unit):
    e.require(unit in ['IP1-G'+str(i).zfill(2) for i in range(2,9)],'recovery_provisional_unit_scope')
    destination=BASE/unit
    e.require(not destination.exists(),'provisional_destination_must_be_new')
    e.require(e.sha(EXECUTION_FREEZE.read_bytes())==EXECUTION_SHA,'execution_freeze_changed')
    freeze=e.read_json(EXECUTION_FREEZE)
    for record in freeze['files']:
        e.require(e.sha((ROOT/record['path']).read_bytes())==record['sha256'],'execution_code_changed')
    plan=e.read_json(RECOVERY/'plan.json');plan_ref=cohort.ref(ROOT,RECOVERY/'plan.json')
    e.require(plan['version']==cohort.RECOVERY_VERSION and plan['recovery']['priorSummarySha256']==cohort.PRIOR_SUMMARY_SHA256,
              'recovery_plan_identity')
    original.check_files(plan['inputFiles'])
    parity=e.read_json(RECOVERY/'all-input-parity.json')
    e.require(parity['completed']==64 and parity['completionRequestsSent']==0,'all64_input_parity_required')
    units,annotations,_=e.load_development(ROOT);prepared=e.load_prepared(ROOT)
    expected=[r for r in prepared if r['id']==unit]
    e.require(len(expected)==4,'four_configuration_unit_required')
    packets=[];mappings=[]
    for prompt in expected:
        index=next(i for i,row in enumerate(prepared) if e.output_key(row)==e.output_key(prompt))
        old=index<38;run=ROOT/cohort.PRIOR_PATH if old else RECOVERY
        output_path=run/'outputs'/(unit+'-'+prompt['configuration']+'.json')
        row=e.read_json(output_path)
        response_path=e.rooted(ROOT,row['rawResponsePath'])
        prefix=response_path.name.removesuffix('.response.bin')
        request_path=response_path.with_name(prefix+'.request.json')
        receipt_path=response_path.with_name(prefix+'.receipt.json')
        request=e.read_json(request_path);receipt=e.read_json(receipt_path)
        payload=cohort.runtime.completion_payload(prompt)
        wire=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
        e.require(request['payload']==payload and request['metadata']['requestSha256']==e.sha(wire),'actual_request_binding')
        e.require(all(receipt.get(k)==v for k,v in request['metadata'].items()) and receipt['automaticRetry'] is False,
                  'receipt_request_binding')
        item={'path':request_path,'request':request,'receiptPath':receipt_path,'receipt':receipt}
        checked=cohort._record(run,prompt,item,index+1 if old else index-37,
                               cohort.PRIOR_VERSION if old else cohort.RECOVERY_VERSION,ROOT,0 if old else 38)
        packet=provisional.packet_for(unit,prompt['configuration'],checked,units,annotations,prepared)
        packets.append(packet)
        mappings.append({'reviewId':packet['reviewId'],'id':unit,'configuration':prompt['configuration'],
            'output':cohort.ref(ROOT,output_path),'request':cohort.ref(ROOT,request_path),
            'receipt':cohort.ref(ROOT,receipt_path),'rawResponse':cohort.ref(ROOT,response_path),
            'packetSha256':e.sha(e.packed(packet)),'sourceRunId':row['runId'],
            'sourceRunStatus':'failed' if old else 'not_finalized'})
    e.require(cohort.ref(ROOT,RECOVERY/'plan.json')==plan_ref,'plan_changed_during_packet_preparation')
    for mapping in mappings:
        for key in ('output','request','receipt','rawResponse'):
            r=mapping[key];e.require(cohort.ref(ROOT,r['path'])==r,'output_evidence_changed')
    for packet in packets:e.write_new(destination/'source-packets'/(packet['reviewId']+'.json'),packet)
    manifest={'version':VERSION,'unit':unit,'status':'provisional_source_packets_only','mappings':mappings,
        'sourcePackets':4,'questionPackets':0,'seed':e.SEED,'plan':plan_ref,
        'whole64CohortValidated':False,'formalSourceReviewsApproved':False,'sourceRunInProgress':True,
        'candidateDecisions':0,'generationCalls':0,'humanReviewed':False,
        'preparationCodeSha256':e.sha(Path(__file__).read_bytes())}
    e.write_new(destination/'private/manifest.json',manifest)
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--unit',required=True)
    args=parser.parse_args();result=prepare(args.unit)
    print(json.dumps({k:result[k] for k in ('unit','status','sourcePackets','questionPackets')},ensure_ascii=False))
