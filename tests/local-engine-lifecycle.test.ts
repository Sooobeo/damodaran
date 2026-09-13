import test, {after, type TestContext} from 'node:test';
import assert from 'node:assert/strict';
import childProcess, {type ChildProcess} from 'node:child_process';
import {EventEmitter, once} from 'node:events';
import {syncBuiltinESMExports} from 'node:module';
import {PassThrough} from 'node:stream';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import type {ProviderRequest} from '../lib/translation';

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'damodaran-lifecycle-'));
Object.assign(process.env, {APP_ROOT:temporary, DATA_DIR:path.join(temporary,'data'), NODE_ENV:'test', TRANSLATION_PROVIDER:'argos', OPENAI_API_KEY:''});
const {LocalEngine, LOCAL_ENGINE_IDLE_MS, translateLocally, closeLocalTranslator} = await import('../lib/translation/local');
const {translationRuntime} = await import('../lib/translation/runtime');
type Runtime = ConstructorParameters<typeof LocalEngine>[0];
const runtime: Runtime = {schemaVersion:1,installedAt:'2026-09-11',provider:'argos',model:'fixture',modelHash:'0'.repeat(64),runtimeVersion:'fixture-v1',identity:'fixture-identity',pythonPath:'fixture-python',bridgePath:'fixture-bridge',modelPath:'fixture-model'};
const request = (model = runtime.identity): ProviderRequest => ({model,context:'',glossary:[],segments:[{id:'s',text:'Fixture source.'}]});
const tick = () => new Promise<void>(resolve => setImmediate(resolve));
const hasCode = (code: string) => (error: unknown) => error instanceof Error && 'code' in error && error.code === code;

after(async () => {
  await closeLocalTranslator();
  assert.ok(temporary.startsWith(os.tmpdir()+path.sep));
  fs.rmSync(temporary,{recursive:true,force:true});
});

function fakeBridge(t: TestContext, options: {delayedExit?:boolean;pid?:number;expectedCloseError?:string} = {}) {
  let ends = 0, kills = 0;
  const child = Object.assign(new EventEmitter(), {
    stdin:new PassThrough(),stdout:new PassThrough(),stderr:new PassThrough(),
    pid:options.pid,exitCode:null as number|null,signalCode:null as string|null,
    kill() { kills++; if (!options.delayedExit) exit(1); return true; },
  });
  function exit(code=0) { if (child.exitCode !== null) return; child.exitCode=code;child.emit('exit',code,null); }
  const requests: Array<{id:string}> = [];
  child.stdin.on('data', chunk => requests.push(JSON.parse(chunk.toString())));
  child.stdin.on('finish', () => { ends++; if (!options.delayedExit) exit(); });
  t.mock.method(childProcess, 'spawn', (()=>child) as unknown as typeof childProcess.spawn);
  syncBuiltinESMExports();
  const engine = new LocalEngine(runtime);
  const send = (message:unknown) => child.stdout.write(JSON.stringify(message)+'\n');
  const ready = () => send({ready:true,provider:runtime.provider,model:runtime.model,modelHash:runtime.modelHash,runtimeVersion:runtime.runtimeVersion});
  t.after(async () => {
    exit();
    if (options.expectedCloseError) await assert.rejects(engine.close(),hasCode(options.expectedCloseError)); else await engine.close();
    t.mock.restoreAll();syncBuiltinESMExports();
  });
  return {engine,child,requests,send,ready,exit,get ends(){return ends;},get kills(){return kills;}};
}

test('startup wait counts as activity and the last completed request starts the idle period', async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const f=fakeBridge(t),first=f.engine.translate(request());
  assert.equal(f.engine.pending.size,0);
  t.mock.timers.tick(LOCAL_ENGINE_IDLE_MS+1);assert.equal(f.ends,0);assert.equal(f.kills,0);
  f.ready();await tick();assert.equal(f.requests.length,1);
  t.mock.timers.tick(LOCAL_ENGINE_IDLE_MS+1);assert.equal(f.ends,0);
  f.send({id:f.requests[0].id,data:{value:1}});assert.deepEqual((await first).data,{value:1});
  t.mock.timers.tick(LOCAL_ENGINE_IDLE_MS-1);assert.equal(f.ends,0);
  t.mock.timers.tick(1);await f.engine.close();assert.equal(f.ends,1);
});

test('an additional active request prevents idle release after another finishes', async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const f=fakeBridge(t);f.ready();
  const first=f.engine.translate(request()),second=f.engine.translate(request());await tick();
  f.send({id:f.requests[0].id,data:{}});await first;
  t.mock.timers.tick(LOCAL_ENGINE_IDLE_MS+1);assert.equal(f.ends,0);assert.equal(f.engine.pending.size,1);
  f.send({id:f.requests[1].id,data:{}});await second;
  t.mock.timers.tick(LOCAL_ENGINE_IDLE_MS);await f.engine.close();assert.equal(f.ends,1);
});

test('a new request resets an existing idle deadline', async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const f=fakeBridge(t);f.ready();await f.engine.ready;
  t.mock.timers.tick(LOCAL_ENGINE_IDLE_MS-1);
  const pending=f.engine.translate(request());await tick();
  t.mock.timers.tick(2);assert.equal(f.ends,0);
  f.send({id:f.requests[0].id,data:{}});await pending;
  t.mock.timers.tick(LOCAL_ENGINE_IDLE_MS-1);assert.equal(f.ends,0);
  t.mock.timers.tick(1);await f.engine.close();assert.equal(f.ends,1);
});

test('close rejects ready waiters, is idempotent, and awaits the actual exit', async t => {
  const f=fakeBridge(t,{delayedExit:true});
  const waiting=assert.rejects(f.engine.translate(request()),hasCode('LOCAL_STOPPED'));
  const ready=assert.rejects(f.engine.ready,hasCode('LOCAL_STOPPED'));
  const close=f.engine.close();assert.equal(close,f.engine.close());
  let finished=false;void close.then(()=>{finished=true;});await tick();
  await waiting;await ready;assert.equal(finished,false);assert.equal(f.requests.length,0);assert.equal(f.ends,1);
  f.ready();await assert.rejects(f.engine.translate(request()),hasCode('LOCAL_STOPPED'));assert.equal(f.requests.length,0);
  f.exit();await close;assert.equal(finished,true);
});

test('close rejects pending requests and ignores late protocol output', async t => {
  const f=fakeBridge(t,{delayedExit:true});f.ready();
  const pending=assert.rejects(f.engine.translate(request()),hasCode('LOCAL_STOPPED'));await tick();
  const close=f.engine.close();await pending;assert.equal(f.engine.pending.size,0);
  f.send({id:f.requests[0].id,data:{late:true}});f.exit();await close;
});

test('cancellation during startup releases the child and sends no source', async t => {
  const f=fakeBridge(t),controller=new AbortController();
  const pending=assert.rejects(f.engine.translate(request(),controller.signal),hasCode('CANCELLED'));
  controller.abort();await pending;await f.engine.close();assert.equal(f.kills,1);assert.equal(f.requests.length,0);
});

test('cancellation of a sent request terminates its shared engine and settles all waiters', async t => {
  const f=fakeBridge(t),controller=new AbortController();f.ready();
  const first=assert.rejects(f.engine.translate(request(),controller.signal),hasCode('CANCELLED'));
  const second=assert.rejects(f.engine.translate(request()),hasCode('CANCELLED'));await tick();
  controller.abort();await Promise.all([first,second]);await f.engine.close();assert.equal(f.engine.pending.size,0);assert.equal(f.kills,1);
});

test('an already cancelled request never sends data or closes a healthy engine', async t => {
  const f=fakeBridge(t);f.ready();
  await assert.rejects(f.engine.translate(request(),AbortSignal.abort()),hasCode('CANCELLED'));
  assert.equal(f.requests.length,0);assert.equal(f.kills,0);assert.equal(f.engine.closing,false);
});

test('force release waits for exit, not merely successful kill submission', async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const f=fakeBridge(t,{delayedExit:true});f.ready();
  const close=f.engine.close();let finished=false;void close.then(()=>{finished=true;});
  t.mock.timers.tick(2000);await tick();assert.equal(f.kills,1);assert.equal(finished,false);
  f.exit(1);await close;assert.equal(finished,true);
});

test('a process that does not exit causes a bounded release failure', async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const f=fakeBridge(t,{delayedExit:true,expectedCloseError:'LOCAL_CLOSE_TIMEOUT'});f.ready();
  const close=f.engine.close(),rejected=assert.rejects(close,hasCode('LOCAL_CLOSE_TIMEOUT'));
  t.mock.timers.tick(15000);await rejected;assert.equal(f.kills,1);assert.equal(f.engine.close(),close);
  // The same failed close remains a barrier even if an exit is observed later.
  f.exit();
});

test('Windows release awaits taskkill completion even if child exit arrives first', {skip:process.platform!=='win32'}, async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  let complete!: (error:null)=>void;
  t.mock.method(childProcess,'execFile',((command:string,args:string[],options:childProcess.ExecFileOptions,callback:(error:null)=>void)=>{
    assert.equal(command,'taskkill');assert.deepEqual(args,['/PID','43123','/T','/F']);assert.equal(options.windowsHide,true);complete=callback;
    return new EventEmitter();
  }) as unknown as typeof childProcess.execFile);syncBuiltinESMExports();
  const f=fakeBridge(t,{delayedExit:true,pid:43123});f.ready();
  const close=f.engine.close();let finished=false;void close.then(()=>{finished=true;});
  t.mock.timers.tick(2000);f.exit(1);await tick();assert.equal(finished,false);
  complete(null);await close;assert.equal(finished,true);
});

function installation() {
  const write=(relative:string,value:string)=>{const target=path.join(temporary,relative);fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(target,value);};
  write('python/python.exe','not executable; replaced only by the test spawn mock');
  fs.mkdirSync(path.join(temporary,'.translation/model'),{recursive:true});
  write('scripts/local-translation/bridge.py','fixture bridge one');write('scripts/local-translation/runtime.py','fixture runtime one');
  write('.translation/manifest.json',JSON.stringify({schemaVersion:1,provider:'argos',model:'fixture',modelHash:createHash('sha256').update('fixture').digest('hex'),runtimeVersion:'fixture',installedAt:'2026-09-11',pythonPath:'python/python.exe',modelPath:'.translation/model'}));
  return {write};
}

test('identity replacement and requests arriving during close wait for the preceding real child exit', {timeout:10000}, async t => {
  const f=installation(),spawn=childProcess.spawn,spawned:Array<{child:ChildProcess;closed:Promise<unknown[]>}>=[];
  const bridge=String.raw`
    const fs=require('node:fs'),path=require('node:path');
    const manifest=JSON.parse(fs.readFileSync(path.join(process.env.APP_ROOT,'.translation/manifest.json'),'utf8'));
    const send=value=>process.stdout.write(JSON.stringify(value)+'\n');
    const hold=setInterval(()=>{},1000);
    send({ready:true,provider:manifest.provider,model:manifest.model,modelHash:manifest.modelHash,runtimeVersion:manifest.runtimeVersion});
    require('node:readline').createInterface({input:process.stdin}).on('line',line=>send({id:JSON.parse(line).id,data:{pid:process.pid}})).on('close',()=>setTimeout(()=>{clearInterval(hold);process.exit(0);},100));
  `;
  t.mock.method(childProcess,'spawn',((command:string,args:string[],options:childProcess.SpawnOptions)=>{
    assert.equal(command,path.join(temporary,'python/python.exe'));
    for(const previous of spawned) assert.ok(previous.child.exitCode!==null||previous.child.signalCode!==null,'previous child must have exited before another starts');
    const child=spawn(process.execPath,['-e',bridge],options);spawned.push({child,closed:once(child,'close')});return child;
  }) as typeof childProcess.spawn);syncBuiltinESMExports();
  try {
    await assert.rejects(translateLocally(request(translationRuntime().identity),AbortSignal.abort()),hasCode('CANCELLED'));assert.equal(spawned.length,0);
    const first=await translateLocally(request(translationRuntime().identity));assert.equal(spawned.length,1);
    f.write('scripts/local-translation/runtime.py','fixture runtime two');
    const second=await translateLocally(request(translationRuntime().identity));assert.notDeepEqual(first.data,second.data);assert.equal(spawned.length,2);
    const closed=closeLocalTranslator(),third=translateLocally(request(translationRuntime().identity));
    await closed;await third;assert.equal(spawned.length,3);
    await Promise.all([closeLocalTranslator(),closeLocalTranslator()]);
    assert.equal(spawned.every(({child})=>child.exitCode===0),true);
  } finally {
    await closeLocalTranslator();await Promise.all(spawned.map(({closed})=>closed));t.mock.restoreAll();syncBuiltinESMExports();
  }
});

test('Windows forced release really terminates an owned child and its descendant before resolving', {skip:process.platform!=='win32',timeout:15000}, async t => {
  const spawn=childProcess.spawn;
  const bridge=String.raw`
    const child=require('node:child_process').spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{windowsHide:true,stdio:'ignore'});
    const send=value=>process.stdout.write(JSON.stringify(value)+'\n');
    setInterval(()=>{},1000);
    child.on('spawn',()=>send({ready:true,provider:'argos',model:'fixture',modelHash:'0'.repeat(64),runtimeVersion:'fixture-v1'}));
    require('node:readline').createInterface({input:process.stdin}).on('line',line=>send({id:JSON.parse(line).id,data:{descendantPid:child.pid}}));
  `;
  t.mock.method(childProcess,'spawn',((command:string,args:string[],options:childProcess.SpawnOptions)=>{
    assert.equal(command,runtime.pythonPath);return spawn(process.execPath,['-e',bridge],options);
  }) as typeof childProcess.spawn);syncBuiltinESMExports();
  const engine=new LocalEngine(runtime);
  try {
    const result=await engine.translate(request()),descendantPid=(result.data as {descendantPid:number}).descendantPid;
    assert.doesNotThrow(()=>process.kill(descendantPid,0));
    await engine.close();
    assert.ok(engine.child.exitCode!==null||engine.child.signalCode!==null);
    assert.throws(()=>process.kill(descendantPid,0),(error:unknown)=>error instanceof Error&&'code' in error&&error.code==='ESRCH');
  } finally { await engine.close();t.mock.restoreAll();syncBuiltinESMExports(); }
});
