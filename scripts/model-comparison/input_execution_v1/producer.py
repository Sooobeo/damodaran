"""S4: execute all frozen C0-C3 inputs once, with owned native runtime and guard.

No deployment, database, paid API, training or evaluation annotation access.
Without --run this creates an immutable plan only. A failed run is never reused
as a completed comparison; all raw responses and missing rows are retained.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.error

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/model-comparison'))
sys.path.insert(0,str(ROOT/'scripts/local-hymt'))
import run_hymt as common
from process_owner import claim_process_owner
from input_preparation_v1 import verify_prepared_inputs as s2
from input_preparation_v1.prepare_inputs import SOURCE_PATHS
from input_execution_v1 import resource_guard as guard
from input_execution_v1 import runtime_contract as runtime
from input_execution_v1.run_io import BASE,ROOT,RunError,LocalClient,require,utc,packed,sha,file_hash,write_new,write_json,append_event,new_run

VERSION='input-preparation-v1-hy7-c0-c3-producer-v1'
PREPARED=ROOT/'.training/comparisons/input-preparation-v1/s2-prepared/attempt-002'
PREPARED_MANIFEST_SHA='b007e567881c1a6dfa556c775eda65916aa49d76559dedc14420c0e127866653'
AUDIT=ROOT/'content/model-comparison/input-preparation-v1/audit-identity.json'
AUDIT_SHA='b1355e1cc9554c953fff8aeba2071b73fdffcacd3fbbb14fc0fdc5a8dbd92739'
S1_FREEZE_SHA='b70304bbd8a3961096f6d07116d51196ece3c4416977f0c0899cf712b7ada4cf'
STARTUP_SECONDS=240
REQUEST_SECONDS=1800
TOTAL_SECONDS=28800

def identity(path):
    p=Path(path);stat=p.stat()
    return {'path':p.relative_to(ROOT).as_posix(),'sha256':file_hash(p),'bytes':stat.st_size,
            'mtimeNs':stat.st_mtime_ns,'inode':stat.st_ino}

def check_files(files,full=False):
    for f in files:
        p=ROOT/f['path'];stat=p.stat()
        require(stat.st_size==f['bytes'] and stat.st_mtime_ns==f['mtimeNs'] and stat.st_ino==f['inode'],'run_input_stat_changed')
        if full or f['bytes']<2*1024*1024:require(file_hash(p)==f['sha256'],'run_input_hash_changed')

def load_fixed_inputs():
    manifest_raw=(PREPARED/'manifest.json').read_bytes()
    require(sha(manifest_raw)==PREPARED_MANIFEST_SHA,'s2_manifest_changed')
    manifest=json.loads(manifest_raw)
    evidence=s2.verify() # Pure replay using recorded counts, no new model/tokenizer.
    require(evidence['status']=='verified','s2_replay_failed')
    raw=(PREPARED/'prompts.jsonl').read_bytes()
    expected=next(f['sha256'] for f in manifest['artifacts'] if f['path']=='prompts.jsonl')
    require(sha(raw)==expected,'s2_prompt_artifact_changed')
    rows=[json.loads(line) for line in raw.decode('utf-8').splitlines()]
    require(len(rows)==64 and len({(r['id'],r['configuration']) for r in rows})==64,'fixed_output_plan_count')
    for row in rows:runtime.validate_prompt_row(row)
    # Existing S2 order is authoritative: source unit, then C0,C1,C2,C3.
    ids=list(dict.fromkeys(r['id'] for r in rows))
    require([(r['id'],r['configuration']) for r in rows]==[(i,c) for i in ids for c in ('C0','C1','C2','C3')],'fixed_input_order_changed')
    return rows,evidence

def build_identity():
    audit_raw=AUDIT.read_bytes();require(sha(audit_raw)==AUDIT_SHA,'s1_audit_changed')
    audit=json.loads(audit_raw)
    selected=[f for f in audit['files'] if f['category'] in ('modelFiles','runtimeFiles','codeFiles','python-jinja',
        'active-manifest','registration-manifest','catalog','input-path-code')]
    paths={ROOT/f['path'] for f in selected}
    for f in selected:require(file_hash(ROOT/f['path'])==f['sha256'],'registered_identity_changed')
    small=[Path(__file__),Path(runtime.__file__),Path(guard.__file__),Path(__file__).with_name('run_io.py'),
        Path(__file__).with_name('__init__.py'),Path(s2.__file__),
        Path(__file__).with_name('PROFILE.md'),Path(__file__).with_name('test_resource_guard.py'),
        Path(__file__).with_name('test_runtime_contract.py'),Path(__file__).with_name('test_producer.py'),
        ROOT/'scripts/model-comparison/working_set_limit.py',ROOT/'scripts/model-comparison/suspended_process_owner.py',
        AUDIT,ROOT/'content/model-comparison/input-preparation-v1/freeze-manifest.json',
        ROOT/'content/model-comparison/input-preparation-v1/sense-dictionary.json',
        PREPARED/'manifest.json',PREPARED/'prompts.jsonl',*(ROOT/p for p in SOURCE_PATHS)]
    prepared_raw=(PREPARED/'manifest.json').read_bytes()
    require(sha(prepared_raw)==PREPARED_MANIFEST_SHA,'s2_manifest_changed')
    prepared_manifest=json.loads(prepared_raw)
    small.extend(ROOT/f['path'] for f in prepared_manifest['code'])
    small.extend(PREPARED/f['path'] for f in prepared_manifest['artifacts'])
    paths.update(small)
    frozen_path=ROOT/'content/model-comparison/input-preparation-v1/freeze-manifest.json'
    frozen_raw=frozen_path.read_bytes();require(sha(frozen_raw)==S1_FREEZE_SHA,'s1_freeze_changed')
    frozen=json.loads(frozen_raw)
    expected={ROOT/f['path']:f['sha256'] for f in selected}
    expected.update({ROOT/f['path']:f['sha256'] for f in prepared_manifest['code']})
    expected.update({PREPARED/f['path']:f['sha256'] for f in prepared_manifest['artifacts']})
    expected.update({ROOT/f['path']:f['sha256'] for f in frozen['artifacts'] if ROOT/f['path'] in paths})
    expected.update({AUDIT:AUDIT_SHA,PREPARED/'manifest.json':PREPARED_MANIFEST_SHA,frozen_path:S1_FREEZE_SHA})
    records=[identity(p) for p in sorted(paths)]
    require(all(f['sha256']==expected[ROOT/f['path']] for f in records if ROOT/f['path'] in expected),'frozen_input_changed_during_snapshot')
    return records,audit['registeredIdentity']

def existing_generations():
    """A no-resume v1 refuses a second call set after even one sent request."""
    conflicts=[]
    if BASE.exists():
        for p in sorted(BASE.glob('attempt-*/http/*completion.request.json')):
            conflicts.append(p.relative_to(ROOT).as_posix())
    require(not conflicts,'prior_completion_attempts_require_explicit_recovery_contract')

def safe_error(error):
    # Never print raw network exceptions, environment values or ephemeral keys.
    known=isinstance(error,(RunError,common.RunError,runtime.ProtocolError,guard.ResourceGuardError))
    return {'type':type(error).__name__,'code':str(error) if known else type(error).__name__}

def execute(rows,plan,output):
    owner=process=monitor=log=power=None;client=None;current=None;records=[]
    summary={'version':VERSION,'startedAt':utc(),'status':'failed','expectedOutputs':64,'completionRequestsSent':0,
        'modelLoaded':False,'childProcessStopped':True,'integrityVerified':False,'failure':None,
        'appDeploymentPerformed':False,'trainingPerformed':False,'paidApiCalls':0,'humanReviewed':False}
    started=time.monotonic()
    def cleanup_native():
        # Registered with the innermost ExitStack, so this always runs before
        # power request release and model-slot unlock, including exceptions.
        try:
            if monitor:
                monitor.stop();summary['resourceMonitoring']=monitor.summary()
        finally:
            if process and not summary['childProcessStopped']:
                receipt=guard.stop_owned(process,owner)
                summary['shutdownReceipt']=receipt
                summary['childProcessStopped']=receipt.get('stopped') is True
                require(summary['childProcessStopped'],'owned_shutdown_unverified')
    try:
        with guard.ExperimentLock() as lock, guard.PowerRequest() as power, ExitStack() as cleanup:
            cleanup.callback(cleanup_native)
            summary['experimentLock']=dict(lock.receipt)
            summary['powerRequestStart']=dict(power.receipt)
            existing_generations()
            first=guard.preflight(exclude_pids=(os.getpid(),))
            first_observed=time.monotonic()
            write_json(output/'preflight-before-integrity.json',first)
            guard.require_preflight(first)
            check_files(plan['inputFiles'],full=True)
            template,gguf=common.gguf_contract(common.DEST/common.MODEL['name'])
            summary['ggufContract']=gguf
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
            key=secrets.token_urlsafe(32);command=common.server_command(port,key)
            redacted=command.copy();redacted[redacted.index('--api-key')+1]='[EPHEMERAL_REDACTED]'
            write_json(output/'runtime-command.json',{'argv':redacted,'sampling':common.SAMPLING,'shell':False})
            gap=guard.PROFILE['preflightSeparationSeconds']-(time.monotonic()-first_observed)
            if gap>0:time.sleep(gap)
            second=guard.preflight(exclude_pids=(os.getpid(),))
            write_json(output/'preflight-before-spawn.json',second)
            guard.require_preflight(second)
            owner=claim_process_owner()
            log=(output/'runtime.log').open('xb')
            process,limiter,spawn_receipt=guard.spawn_guarded(owner,command,log,common.runtime_environment(),Path(command[0]).parent)
            summary['childProcessStopped']=False
            write_json(output/'spawn.json',spawn_receipt)
            monitor=guard.ResourceMonitor(process,limiter,output/'resource-samples.jsonl',exclude_pids=(os.getpid(),),run_started=time.monotonic())
            monitor.start()
            client=LocalClient(f'http://127.0.0.1:{port}',key,output)
            load_started=time.monotonic()
            while time.monotonic()-load_started<STARTUP_SECONDS:
                monitor.check();require(process.poll() is None,'runtime_exited_before_ready')
                try:
                    if client.request('/health',timeout=2).get('status')=='ok':break
                except (urllib.error.URLError,TimeoutError):pass
                time.sleep(0.25)
            else:raise RunError('runtime_startup_timeout')
            monitor.ready();summary['modelLoaded']=True
            summary['loadSeconds']=round(time.monotonic()-load_started,6)
            log.flush()
            eog=common.eog_from_log((output/'runtime.log').read_text('utf-8',errors='replace'))
            specials=common.validate_runtime_tokens(client)
            props=client.request('/props',timeout=15)
            props_receipt=runtime.validate_props(props,model_path=common.DEST/common.MODEL['name'],template=template)
            write_json(output/'runtime-identity.json',{'eog':eog,'specialTokens':specials,'props':props_receipt})
            # Validate all input construction and tokens before the first call.
            parity=[]
            for row in rows:
                monitor.check()
                rendered=client.request('/apply-template',runtime.template_payload(row),timeout=15)
                render_receipt=runtime.validate_template_response(rendered,row)
                tokenized=client.request('/tokenize',runtime.tokenize_payload(row),timeout=15)
                tokens=runtime.validate_tokenize_response(tokenized,row)
                parity.append({'id':row['id'],'configuration':row['configuration'],'template':render_receipt,
                    'promptSha256':row['promptSha256'],'tokenIdsSha256':row['tokenIdsSha256'],'tokenCount':len(tokens)})
            require(len(parity)==64,'runtime_input_parity_incomplete')
            write_json(output/'all-input-parity.json',{'rows':parity,'completed':64,'completionRequestsSent':0})
            print(json.dumps({'event':'s4-ready','outputs':64,'inputParity':64,'run':output.relative_to(ROOT).as_posix()}),flush=True)
            for index,row in enumerate(rows,1):
                current=row
                check_files(plan['inputFiles']);owner.assert_owned();monitor.check()
                require(time.monotonic()-started<TOTAL_SECONDS,'total_time_limit')
                attempt={'id':row['id'],'configuration':row['configuration'],'sequence':index,
                    'sourceSha256':row['sourceSha256'],'promptSha256':row['promptSha256'],
                    'tokenIdsSha256':row['tokenIdsSha256'],
                    'contextPolicy':row['context']['policyVersion'],'selectorVersion':row['terminology']['selectorVersion'],'at':utc(),
                    'status':'request_pending','automaticRetry':False,'sourceOutputReuse':False}
                append_event(output/'attempt-events.jsonl',attempt)
                before=time.monotonic();monitor.begin_request()
                summary['completionRequestsSent']+=1
                try:response=client.request('/completion',runtime.completion_payload(row),timeout=REQUEST_SECONDS)
                finally:monitor.end_request()
                elapsed=time.monotonic()-before
                # Even if guard or parser rejects it, original HTTP body exists.
                monitor.check()
                record=runtime.validate_completion(row,response,elapsed)
                record.update(rawResponsePath=client.last['rawResponsePath'],rawResponseSha256=client.last['rawResponseSha256'],
                    requestSequence=index,runId=output.name,producerVersion=VERSION,
                    preparedManifestSha256=PREPARED_MANIFEST_SHA,
                    processingIdentity={'configuration':row['configuration'],'preparedPromptSha256':row['promptSha256'],
                        'contextPolicy':row['context']['policyVersion'],'selectorVersion':row['terminology']['selectorVersion']},
                    sourceOutputReused=False)
                write_json(output/'outputs'/f'{row["id"]}-{row["configuration"]}.json',record)
                append_event(output/'predictions.jsonl',record);records.append(record)
                append_event(output/'attempt-events.jsonl',{**attempt,'at':utc(),'status':'completed',
                    'translationSha256':record['translationSha256'],'rawResponseSha256':record['rawResponseSha256']})
                current=None
                print(json.dumps({'event':'s4-output','completed':len(records),'total':64,'id':row['id'],
                    'configuration':row['configuration'],'seconds':round(elapsed,3),'outputTokens':record['outputTokens'],
                    'automaticChecks':record['automaticChecks']},ensure_ascii=False),flush=True)
            require(len(records)==64,'incomplete_generation')
            summary['status']='completed'
            # Close while PowerRequest is still held, then release it normally.
            monitor.stop();summary['resourceMonitoring']=monitor.summary();monitor.check()
            summary['shutdownReceipt']=guard.stop_owned(process,owner)
            summary['childProcessStopped']=summary['shutdownReceipt'].get('stopped') is True
            require(summary['childProcessStopped'] is True,'owned_shutdown_unverified')
            summary['powerRequestBeforeRelease']=dict(power.receipt)
        summary['powerRequestAfterRelease']=dict(power.receipt)
    except BaseException as error:
        summary['status']='failed';summary['failure']={**safe_error(error),'id':current['id'] if current else None,
            'configuration':current['configuration'] if current else None}
        if current:
            write_json(output/'failed-item.json',{'id':current['id'],'configuration':current['configuration'],
                'failure':summary['failure'],'lastHttpReceipt':client.last if client else None})
    finally:
        if power:summary['powerRequestAfterRelease']=dict(power.receipt)
        if monitor:
            try:monitor.stop();summary['resourceMonitoring']=monitor.summary()
            except BaseException as error:summary.update(status='failed',monitorCleanupError=type(error).__name__)
        if process and not summary['childProcessStopped']:
            try:
                summary['shutdownReceipt']=guard.stop_owned(process,owner)
                summary['childProcessStopped']=summary['shutdownReceipt'].get('stopped') is True
                if not summary['childProcessStopped']:summary['status']='failed'
            except BaseException as error:summary.update(status='failed',childProcessStopped=False,shutdownError=type(error).__name__)
        if log:log.close()
        try:check_files(plan['inputFiles'],full=True);summary['integrityVerified']=True
        except BaseException as error:summary.update(status='failed',finalIntegrityError=type(error).__name__)
        covered={(r['id'],r['configuration']) for r in records}
        missing=[]
        for row in rows:
            if (row['id'],row['configuration']) not in covered:
                missing.append({'id':row['id'],'configuration':row['configuration'],'sourceSha256':row['sourceSha256'],
                    'status':'failed' if current and row['id']==current['id'] and row['configuration']==current['configuration'] else 'not_run'})
        write_json(output/'missing-outputs.json',missing)
        summary.update(completedOutputs=len(records),missingOutputs=len(missing),finishedAt=utc(),
            elapsedSeconds=round(time.monotonic()-started,6),completionStatusMeaning='technical completion only; no semantic approval',
            outputIntegrityPassed=len(records)==64 and summary['status']=='completed',
            automaticCheckFailureRows=[r['id']+'-'+r['configuration'] for r in records if not all(r['automaticChecks'].values())])
        summary['artifactHashes']={p.relative_to(output).as_posix():file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}
        write_json(output/'summary.json',summary)
    print(json.dumps({'event':'s4-finished','status':summary['status'],'completed':len(records),'total':64,
        'childStopped':summary['childProcessStopped'],'run':output.relative_to(ROOT).as_posix(),'failure':summary['failure']}),flush=True)
    return 0 if summary['status']=='completed' else 1

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',action='store_true')
    args=parser.parse_args();rows,replay=load_fixed_inputs()
    files,registered=build_identity()
    output=new_run()
    plan={'version':VERSION,'createdAt':utc(),'status':'planned','inferenceRequested':args.run,'expectedOutputs':64,
        'preparedManifestPath':(PREPARED/'manifest.json').relative_to(ROOT).as_posix(),'preparedManifestSha256':PREPARED_MANIFEST_SHA,
        'inputFiles':files,'registeredIdentity':registered,'sampling':common.SAMPLING,'contextSize':8192,
        'resourceProfile':guard.PROFILE,'timeLimitsSeconds':{'startup':STARTUP_SECONDS,'request':REQUEST_SECONDS,'total':TOTAL_SECONDS},
        'outputOrder':[[r['id'],r['configuration']] for r in rows],'automaticRetry':False,'crossConfigurationReuse':False,
        'annotationFilesRead':False,'historicalOutputsUsedForPrompts':False,'operatorAuthorization':'user requested S4 through remaining stages',
        'nextStages':'S5 independent questions/source review; S6 expand only eligible candidate'}
    write_json(output/'plan.json',plan)
    write_new(output/'inputs.jsonl',b''.join(packed(row) for row in rows))
    write_json(output/'s2-replay-receipt.json',{'status':replay['status'],'preparedManifestSha256':PREPARED_MANIFEST_SHA,
        'nativeCallsThisReplay':0,'units':16,'prompts':64})
    if not args.run:
        print(json.dumps({'status':'planned','run':output.relative_to(ROOT).as_posix(),'generationCalls':0}),flush=True);return 0
    return execute(rows,plan,output)

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',line_buffering=True)
    raise SystemExit(main())
