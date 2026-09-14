"""Explicit technical recovery of S4's 26 incomplete outputs; no quality retries.

The original frozen producer is unchanged. This new lifecycle reuses its runtime
contract and guard, but verifies a failed predecessor and records only the tail.
All 64 inputs are rechecked at the runtime before the first recovery completion.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import time
import urllib.error
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts/model-comparison'))
from input_execution_v1 import producer as original
from input_execution_v1 import resource_guard as guard
from input_execution_v1 import runtime_contract as runtime
from input_execution_v1.run_io import (RunError,LocalClient,require,utc,packed,sha,file_hash,
    write_new,write_json)
common=original.common
claim_process_owner=original.claim_process_owner
check_files=original.check_files
safe_error=original.safe_error
PREPARED=original.PREPARED
PREPARED_MANIFEST_SHA=original.PREPARED_MANIFEST_SHA
STARTUP_SECONDS=original.STARTUP_SECONDS
REQUEST_SECONDS=original.REQUEST_SECONDS
TOTAL_SECONDS=original.TOTAL_SECONDS
VERSION='input-preparation-v1-hy7-c0-c3-recovery-producer-v1'
BASE=ROOT/'.training/comparisons/input-preparation-v1/s4-recovery'
PRIOR=ROOT/'.training/comparisons/input-preparation-v1/s4-generation/attempt-001'
CONTRACT=ROOT/'content/model-comparison/input-execution-v1/RECOVERY_V1.md'
PRIOR_IDENTITY=CONTRACT.with_name('RECOVERY_PRIOR.json')

def append_event(path,value):
    path=Path(path)
    require(path.resolve().is_relative_to(BASE) and not any(p.is_symlink() for p in (path,*path.parents)),
        'recovery_event_redirected')
    require(path.name in ('attempt-events.jsonl','predictions.jsonl'),'recovery_event_name_not_allowed')
    with path.open('ab') as stream:
        stream.write(packed(value));stream.flush();os.fsync(stream.fileno())

def select_pending(rows,plan):
    expected=[(r['id'],r['configuration']) for r in rows]
    require(len(rows)==64 and len(set(expected))==64,'recovery_full64_input_inventory')
    pending=[tuple(k) for k in plan['pendingOutputOrder']]
    require(pending==expected[38:] and len(pending)==26,'recovery_tail26_required')
    require([list(k) for k in expected]==plan['outputOrder'],'recovery_full_order_changed')
    require(pending[0]==('IP1-G02','C2'),'recovery_interrupted_identity')
    return rows[38:]

def existing_recovery_generations():
    conflicts=list(BASE.glob('*/http/*completion.request.json')) if BASE.exists() else []
    require(not conflicts,'prior_recovery_completion_requires_new_contract')
    unknown=[p for p in original.BASE.glob('*/http/*completion.request.json') if p.parent.parent!=PRIOR]
    require(not unknown,'unapproved_other_original_generation')

def validate_prior_binding(plan):
    from input_execution_v1 import recovery_cohort
    pinned=json.loads(PRIOR_IDENTITY.read_bytes())
    require(file_hash(PRIOR/'summary.json')==pinned['summarySha256'],'original_failure_summary_changed')
    prior=recovery_cohort.validate_failed_run(PRIOR,root=ROOT)
    recovery=plan['recovery']
    require(prior['evidence']['summary']['sha256']==recovery['priorSummarySha256'],'recovery_prior_summary_changed')
    require([list(k) for k in prior['missingKeys']]==plan['pendingOutputOrder'],'recovery_missing_inventory_changed')
    require(recovery['priorRunPath']==PRIOR.relative_to(ROOT).as_posix(),'recovery_prior_path_changed')
    return prior

def new_run():
    require(not any(p.is_symlink() for p in (BASE,*BASE.parents)),'redirected_recovery_output')
    BASE.mkdir(parents=True,exist_ok=True)
    for number in range(1,1000):
        output=BASE/f'recovery-attempt-{number:03d}'
        try:output.mkdir()
        except FileExistsError:continue
        return output
    raise RunError('recovery_attempt_slots_exhausted')

def execute(rows,plan,output):
    pending=select_pending(rows,plan)
    owner=process=monitor=log=power=None;client=None;current=None;records=[]
    summary={'version':VERSION,'startedAt':utc(),'status':'failed','expectedOutputs':26,'cohortExpectedOutputs':64,'retainedOutputs':38,'completionRequestsSent':0,
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
            existing_recovery_generations()
            validate_prior_binding(plan)
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
            print(json.dumps({'event':'s4-ready','outputs':26,'retainedOutputs':38,'inputParity':64,'run':output.relative_to(ROOT).as_posix()}),flush=True)
            for index,row in enumerate(pending,1):
                current=row
                check_files(plan['inputFiles']);owner.assert_owned();monitor.check()
                require(time.monotonic()-started<TOTAL_SECONDS,'total_time_limit')
                attempt={'id':row['id'],'configuration':row['configuration'],'sequence':index,
                    'preparedSequence':index+38,'technicalRecoveryRetry':index==1,
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
                    sourceOutputReused=False,preparedSequence=index+38,technicalRecoveryRetry=index==1)
                write_json(output/'outputs'/f'{row["id"]}-{row["configuration"]}.json',record)
                append_event(output/'predictions.jsonl',record);records.append(record)
                append_event(output/'attempt-events.jsonl',{**attempt,'at':utc(),'status':'completed',
                    'translationSha256':record['translationSha256'],'rawResponseSha256':record['rawResponseSha256']})
                current=None
                print(json.dumps({'event':'s4-output','completed':len(records),'total':26,'id':row['id'],
                    'configuration':row['configuration'],'seconds':round(elapsed,3),'outputTokens':record['outputTokens'],
                    'automaticChecks':record['automaticChecks']},ensure_ascii=False),flush=True)
            require(len(records)==26,'incomplete_generation')
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
        for row in pending:
            if (row['id'],row['configuration']) not in covered:
                missing.append({'id':row['id'],'configuration':row['configuration'],'sourceSha256':row['sourceSha256'],
                    'status':'failed' if current and row['id']==current['id'] and row['configuration']==current['configuration'] else 'not_run'})
        write_json(output/'missing-outputs.json',missing)
        summary.update(completedOutputs=len(records),missingOutputs=len(missing),finishedAt=utc(),
            elapsedSeconds=round(time.monotonic()-started,6),completionStatusMeaning='technical completion only; no semantic approval',
            outputIntegrityPassed=len(records)==26 and summary['status']=='completed',
            automaticCheckFailureRows=[r['id']+'-'+r['configuration'] for r in records if not all(r['automaticChecks'].values())])
        summary['artifactHashes']={p.relative_to(output).as_posix():file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}
        write_json(output/'summary.json',summary)
    print(json.dumps({'event':'s4-finished','status':summary['status'],'completed':len(records),'total':26,
        'childStopped':summary['childProcessStopped'],'run':output.relative_to(ROOT).as_posix(),'failure':summary['failure']}),flush=True)
    return 0 if summary['status']=='completed' else 1

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',action='store_true')
    args=parser.parse_args()
    from input_execution_v1 import recovery_cohort
    rows,replay=original.load_fixed_inputs()
    pinned=json.loads(PRIOR_IDENTITY.read_bytes())
    require(file_hash(PRIOR/'summary.json')==pinned['summarySha256'],'original_failure_summary_changed')
    prior=recovery_cohort.validate_failed_run(PRIOR,root=ROOT)
    require(len(prior['rows'])==38 and len(prior['missingKeys'])==26,'fixed_recovery_inventory')
    files,registered=original.build_identity()
    paths={ROOT/f['path'] for f in files}
    paths.update(ROOT/f['path'] for f in prior['evidence']['files'])
    paths.update([PRIOR/'summary.json',CONTRACT,PRIOR_IDENTITY,Path(__file__),
        Path(__file__).with_name('test_recovery_producer.py'),Path(recovery_cohort.__file__),
        Path(__file__).with_name('test_recovery_cohort.py')])
    files=[original.identity(p) for p in sorted(paths)]
    original.check_files(files,full=True)
    # Recheck the bound failed evidence after snapshot to reject a TOCTOU change.
    recovery={'priorRunPath':PRIOR.relative_to(ROOT).as_posix(),
        'priorSummarySha256':pinned['summarySha256'],'priorCompletedOutputs':38,
        'priorCompletionRequestsSent':39,'interruptedKey':['IP1-G02','C2'],
        'reuseReason':'Technical AC interruption; preserve all completed outputs byte-for-byte; generate only incomplete tail.',
        'originalFailurePreserved':True,'sourceOutputQualitySelection':False}
    plan={'version':VERSION,'createdAt':utc(),'status':'planned','inferenceRequested':args.run,
        'expectedOutputs':26,'cohortExpectedOutputs':64,'recovery':recovery,
        'preparedManifestPath':(PREPARED/'manifest.json').relative_to(ROOT).as_posix(),
        'preparedManifestSha256':PREPARED_MANIFEST_SHA,'inputFiles':files,
        'registeredIdentity':registered,'sampling':common.SAMPLING,'contextSize':8192,
        'resourceProfile':guard.PROFILE,
        'timeLimitsSeconds':{'startup':STARTUP_SECONDS,'request':REQUEST_SECONDS,'total':TOTAL_SECONDS},
        'outputOrder':[[r['id'],r['configuration']] for r in rows],
        'pendingOutputOrder':[list(k) for k in prior['missingKeys']],
        'automaticRetry':False,'crossConfigurationReuse':False,'annotationFilesRead':False,
        'historicalOutputsUsedForPrompts':False,
        'operatorAuthorization':'User requested S4 through remaining stages; S1 section 8 permits explicit technical recovery',
        'nextStages':'S5 recovered cohort evaluation; S6 only eligible candidate'}
    select_pending(rows,plan);validate_prior_binding(plan)
    if args.run:existing_recovery_generations()
    output=new_run()
    write_json(output/'plan.json',plan)
    write_new(output/'inputs.jsonl',b''.join(packed(row) for row in rows))
    write_json(output/'retained-evidence.json',prior['evidence'])
    write_json(output/'s2-replay-receipt.json',{'status':replay['status'],
        'preparedManifestSha256':PREPARED_MANIFEST_SHA,'nativeCallsThisReplay':0,'units':16,'prompts':64})
    if not args.run:
        print(json.dumps({'status':'planned','run':output.relative_to(ROOT).as_posix(),
            'retainedOutputs':38,'pendingOutputs':26,'generationCalls':0}),flush=True)
        return 0
    return execute(rows,plan,output)

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',line_buffering=True)
    raise SystemExit(main())
