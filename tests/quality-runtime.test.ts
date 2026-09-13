/** Runtime protocol and lifecycle fixtures only; never loads a QE or translation model. */
import test,{after,type TestContext} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import childProcess,{type ChildProcess} from 'node:child_process';
import {EventEmitter,once} from 'node:events';
import {syncBuiltinESMExports} from 'node:module';
import {PassThrough} from 'node:stream';

const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'damodaran-qe-runtime-'));
// Production contract parsing is intentional; APP_ROOT contains only this test's
// synthetic registration. Its fake Python path must always be intercepted.
Object.assign(process.env,{APP_ROOT:temporary,DATA_DIR:path.join(temporary,'data'),NODE_ENV:'production',OPENAI_API_KEY:''});
const {QualityEngine,qualityRuntime,evaluateQuality,closeQualityEvaluator}=await import('../lib/quality/runtime');
const hash=(value:string|Buffer)=>createHash('sha256').update(value).digest('hex');
const tick=()=>new Promise<void>(resolve=>setImmediate(resolve));
const hasCode=(code:string)=>(error:unknown)=>error instanceof Error&&'code' in error&&error.code===code;
const input={source:'Fixture source.',translation:'테스트 번역.',context:'Fixture context.'};
after(async()=>{await closeQualityEvaluator();assert.ok(temporary.startsWith(os.tmpdir()+path.sep));fs.rmSync(temporary,{recursive:true,force:true});});

function installation(){
  const write=(relative:string,text:string)=>{const full=path.join(temporary,relative);fs.mkdirSync(path.dirname(full),{recursive:true});fs.writeFileSync(full,text);return {path:relative,size:Buffer.byteLength(text),sha256:hash(text)};};
  const python=write('.venv-qe/Scripts/python.exe','fixture only; never executed'),bridge=write('scripts/local-qe/bridge.py','fixture bridge');
  const evidence=write('.training/quality-evaluation/calibration.json','fixture evidence'),weights=write('.translation/qe/candidates/model.bin','fixture weights');
  const manifest={schemaVersion:1,modelId:'FIXTURE-QE',modelRevision:'fixture-revision',modelHash:weights.sha256,modelIdentity:hash('fixture-model-runtime'),pythonPath:python.path,bridgePath:bridge.path,contextUsed:false,spanSupport:false,files:[python,bridge,evidence,weights],calibration:{version:'product-qe-95-v2',threshold:-0.5,accepted:true,scoreDirection:'lower-is-risk',comparison:'score<=threshold',evidencePath:evidence.path,evidenceSha256:evidence.sha256}};
  const save=()=>write('.translation/qe/manifest.json',JSON.stringify(manifest));save();
  const read=()=>{const runtime=qualityRuntime();assert.equal(runtime.configured,true);assert.ok(runtime.manifest);return runtime;};
  return {manifest,write,save,read};
}
type Fixture=ReturnType<typeof installation>;
function fake(t:TestContext,f:Fixture,options:{delayed?:boolean;pid?:number;closeError?:string}={}){
  const child=Object.assign(new EventEmitter(),{stdin:new PassThrough(),stdout:new PassThrough(),stderr:new PassThrough(),exitCode:null as number|null,signalCode:null,pid:options.pid,kills:0,kill(){child.kills++;if(!options.delayed)exit();return true;}});
  function exit(){if(child.exitCode!==null)return;child.exitCode=0;child.emit('close',0,null);}
  const requests:Array<typeof input&{id:string}>=[];child.stdin.on('data',data=>requests.push(JSON.parse(data.toString())));child.stdin.on('finish',()=>{if(!options.delayed)exit();});
  t.mock.method(childProcess,'spawn',((command:string,args:string[],options:childProcess.SpawnOptions)=>{assert.equal(command,path.join(temporary,f.manifest.pythonPath));assert.deepEqual(args,['-u',path.join(temporary,f.manifest.bridgePath)]);assert.equal(options.windowsHide,true);assert.equal(options.env?.OPENAI_API_KEY,'');assert.equal(options.env?.HF_HUB_OFFLINE,'1');return child;}) as unknown as typeof childProcess.spawn);syncBuiltinESMExports();
  const engine=new QualityEngine(f.read());
  const send=(message:unknown)=>child.stdout.write(JSON.stringify(message)+'\n');
  const response=(overrides:Record<string,unknown>={})=>{const request=requests.at(-1)!;return {id:request.id,status:'completed',score:-1.75,risk:'review',modelId:f.manifest.modelId,modelRevision:f.manifest.modelRevision,modelHash:f.manifest.modelHash,modelIdentity:f.manifest.modelIdentity,calibrationVersion:f.manifest.calibration.version,scoreDirection:'higher-is-better',contextUsed:false,spans:[],truncated:false,sourceSha256:hash(request.source),translationSha256:hash(request.translation),contextSha256:hash(request.context),...overrides};};
  t.after(async()=>{exit();if(options.closeError)await assert.rejects(engine.close(),hasCode(options.closeError));else await engine.close();t.mock.restoreAll();syncBuiltinESMExports();});
  return {engine,child,requests,exit,send,response};
}

test('calibration direction/comparison, model runtime identity and no-span contract are mandatory',()=>{
  const f=installation();assert.equal(f.read().threshold,-0.5);
  const original=JSON.stringify(f.manifest);
  for(const [key,values] of [['version',[undefined,'assistant-dev48-screen-v1','product-qe-95-v1','unknown-future-policy']],['scoreDirection',[undefined,'higher-is-risk']],['comparison',[undefined,'score>=threshold']],['accepted',[undefined,false]]] as const){
    for(const value of values){const changed=JSON.parse(original);changed.calibration[key]=value;f.write('.translation/qe/manifest.json',JSON.stringify(changed));assert.equal(qualityRuntime().configured,false);}
  }
  for(const key of ['modelIdentity','contextUsed','spanSupport']){const changed=JSON.parse(original);delete changed[key];f.write('.translation/qe/manifest.json',JSON.stringify(changed));assert.equal(qualityRuntime().configured,false);}
});
test('allowed prefixes cannot be escaped by traversal, alternate streams or sibling names',()=>{
  const f=installation();
  for(const relative of ['.translation/qe/../../data/private.txt','.venv-qe/../outside/python.exe','scripts/local-qe/../unrelated.py','.training/quality-evaluation/../training.bin','.translation/qe-sibling/model','.translation\\qe\\..\\private.txt','.translation/qe/model.bin:alternate','.translation/qe//model.bin']){
    f.manifest.files[0].path=relative;f.save();assert.equal(qualityRuntime().configured,false,relative);
  }
});
test('junctions and symlinks cannot redirect a registered file to another allowed prefix',async t=>{
  const f=installation(),target=path.join(temporary,'.training/quality-evaluation'),link=path.join(temporary,'.translation/qe/linked');
  fs.symlinkSync(target,link,process.platform==='win32'?'junction':'dir');
  try{f.manifest.files[2].path='.translation/qe/linked/calibration.json';f.manifest.calibration.evidencePath=f.manifest.files[2].path;f.save();let spawns=0;t.mock.method(childProcess,'spawn',(()=>{spawns++;throw new Error('must not spawn');}) as unknown as typeof childProcess.spawn);syncBuiltinESMExports();await assert.rejects(evaluateQuality(input,f.read().identity),hasCode('QE_CHANGED'));assert.equal(spawns,0);}
  finally{fs.unlinkSync(link);t.mock.restoreAll();syncBuiltinESMExports();}
});
test('duplicate files, unregistered Python and changed weight bytes fail before spawn',async t=>{
  let spawns=0;t.mock.method(childProcess,'spawn',(()=>{spawns++;throw new Error('must not spawn');}) as unknown as typeof childProcess.spawn);syncBuiltinESMExports();
  try{
    const duplicate=installation();duplicate.manifest.files.push({...duplicate.manifest.files[0]});duplicate.save();await assert.rejects(evaluateQuality(input,duplicate.read().identity),hasCode('QE_CHANGED'));
    const missing=installation();missing.manifest.files.shift();missing.save();await assert.rejects(evaluateQuality(input,missing.read().identity),hasCode('QE_CHANGED'));
    const changed=installation();changed.write('.translation/qe/candidates/model.bin','fixture WEIGHTS');await assert.rejects(evaluateQuality(input,changed.read().identity),hasCode('QE_CHANGED'));assert.equal(spawns,0);
  }finally{t.mock.restoreAll();syncBuiltinESMExports();}
});
test('finite negative and above-one raw scores are preserved without probability conversion',async t=>{
  const f=installation(),child=fake(t,f);
  for(const score of [-1.75,0,2.4]){const result=child.engine.evaluate(input);child.send(child.response({score,risk:score<=f.manifest.calibration.threshold?'review':'no_findings'}));assert.deepEqual(await result,{score,findings:[]});}
});
test('only completed responses with a finite numeric raw score are accepted',async t=>{
  for(const overrides of [{status:undefined},{status:'running'},{status:'cancelled'},{score:null},{score:'0.5'},{score:true}]){
    await t.test(JSON.stringify(overrides),async sub=>{const f=installation(),child=fake(sub,f),result=assert.rejects(child.engine.evaluate(input),hasCode('QE_PROTOCOL'));child.send(child.response(overrides));await result;assert.equal(child.engine.closing,true);});
  }
});
test('reported failure states do not accept a plausible-looking score or expose diagnostics',async t=>{
  for(const status of ['failed','unavailable'])await t.test(status,async sub=>{const f=installation(),child=fake(sub,f),result=assert.rejects(child.engine.evaluate(input),error=>hasCode('QE_FAILED')(error)&&!String(error).includes('private diagnostic'));child.send(child.response({status,errorCode:'private diagnostic'}));await result;});
});
test('every model and input identity field is checked before accepting the result',async t=>{
  for(const key of ['modelId','modelRevision','modelHash','modelIdentity','calibrationVersion','sourceSha256','translationSha256','contextSha256','truncated','scoreDirection','contextUsed','risk']){
    await t.test(key,async sub=>{const f=installation(),child=fake(sub,f),result=assert.rejects(child.engine.evaluate(input),hasCode(['modelId','modelRevision','modelHash','modelIdentity','calibrationVersion'].includes(key)?'QE_CHANGED':'QE_PROTOCOL'));child.send(child.response({[key]:'wrong'}));await result;});
  }
});
test('a no-span evaluator may not return fabricated or unvalidated error regions',async t=>{
  for(const value of [{spans:undefined},{spans:[{start:0,end:1}]},{findings:[{category:'word_sense',severity:'warning',reason:'invented',detector:'qe',target:null,source:null}]}]){
    await t.test(JSON.stringify(value),async sub=>{const f=installation(),child=fake(sub,f),result=assert.rejects(child.engine.evaluate(input),hasCode('QE_OFFSETS'));child.send(child.response(value));await result;});
  }
});
test('malformed, wrong-ID and duplicate NDJSON responses close the engine',async t=>{
  for(const kind of ['null','array','wrong-id','duplicate'])await t.test(kind,async sub=>{
    const f=installation(),child=fake(sub,f),result=assert.rejects(child.engine.evaluate(input),hasCode('QE_PROTOCOL'));
    if(kind==='duplicate'){const response=JSON.stringify(child.response());child.child.stdout.write(response+'\n'+response+'\n');}else child.send(kind==='null'?null:kind==='array'?[]:child.response({id:'wrong'}));
    await result;assert.equal(child.engine.closing,true);
  });
});
test('stdin failures and request timeout settle pending work and close only the owned child',async t=>{
  await t.test('stdin',async sub=>{const f=installation(),child=fake(sub,f),result=assert.rejects(child.engine.evaluate(input),hasCode('QE_STOPPED'));child.child.stdin.emit('error',new Error('private input diagnostic'));await result;await child.engine.close();assert.equal(child.child.exitCode,0);});
  await t.test('timeout',async sub=>{sub.mock.timers.enable({apis:['setTimeout']});const f=installation(),child=fake(sub,f),result=assert.rejects(child.engine.evaluate(input),hasCode('QE_TIMEOUT'));sub.mock.timers.tick(300000);await result;await child.engine.close();});
});
test('close is idempotent and does not resolve when a kill has only been submitted',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});const f=installation(),child=fake(t,f,{delayed:true});
  const pending=assert.rejects(child.engine.evaluate(input),hasCode('QE_STOPPED')),close=child.engine.close();assert.equal(close,child.engine.close());let closed=false;void close.then(()=>{closed=true;});
  await pending;await tick();t.mock.timers.tick(2000);await tick();assert.equal(child.child.kills,1);assert.equal(closed,false);
  child.exit();await close;assert.equal(closed,true);
});
test('Windows taskkill has a bounded deadline and its failure leaves the engine closed to new work',{skip:process.platform!=='win32'},async t=>{
  t.mock.timers.enable({apis:['setTimeout']});let callback!:(error:Error)=>void;
  t.mock.method(childProcess,'execFile',((command:string,args:string[],options:childProcess.ExecFileOptions,done:(error:Error)=>void)=>{assert.equal(command,'taskkill');assert.deepEqual(args,['/PID','54321','/T','/F']);assert.equal(options.timeout,10000);assert.equal(options.windowsHide,true);assert.equal(options.killSignal,'SIGKILL');callback=done;return new EventEmitter();}) as unknown as typeof childProcess.execFile);syncBuiltinESMExports();
  const f=installation(),child=fake(t,f,{delayed:true,pid:54321,closeError:'QE_CLOSE_FAILED'}),close=child.engine.close(),rejected=assert.rejects(close,hasCode('QE_CLOSE_FAILED'));
  await tick();t.mock.timers.tick(2000);await tick();callback(new Error('fixture taskkill timeout'));await rejected;
  await assert.rejects(child.engine.evaluate(input),hasCode('QE_BUSY'));assert.equal(child.engine.close(),close);
});

const nodeBridge=String.raw`
  const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
  const m=JSON.parse(fs.readFileSync(path.join(process.env.APP_ROOT,'.translation/qe/manifest.json'),'utf8'));
  const hash=s=>crypto.createHash('sha256').update(s).digest('hex'),hold=setInterval(()=>{},1000);
  require('node:readline').createInterface({input:process.stdin}).on('line',line=>{
    const r=JSON.parse(line);if(r.source==='hold')return;
    setTimeout(()=>process.stdout.write(JSON.stringify({id:r.id,status:'completed',score:2.4,risk:'no_findings',modelId:m.modelId,modelRevision:m.modelRevision,modelHash:m.modelHash,modelIdentity:m.modelIdentity,calibrationVersion:m.calibration.version,sourceSha256:hash(r.source),translationSha256:hash(r.translation),contextSha256:hash(r.context),scoreDirection:'higher-is-better',contextUsed:false,truncated:false,spans:[]})+'\n'),100);
  }).on('close',()=>setTimeout(()=>{clearInterval(hold);process.exit(0);},100));
`;
test('real Node fixture: concurrent acquisition, identity switch and restart during close never overlap owned children',{timeout:15000},async t=>{
  const f=installation(),spawn=childProcess.spawn,spawned:Array<{child:ChildProcess;closed:Promise<unknown[]>}>=[];
  t.mock.method(childProcess,'spawn',((command:string,args:string[],options:childProcess.SpawnOptions)=>{
    assert.equal(command,path.join(temporary,f.manifest.pythonPath));assert.ok(spawned.every(({child})=>child.exitCode!==null||child.signalCode!==null));
    const child=spawn(process.execPath,['-e',nodeBridge],options);spawned.push({child,closed:once(child,'close')});return child;
  }) as typeof childProcess.spawn);syncBuiltinESMExports();
  try{
    const identity=f.read().identity,results=await Promise.allSettled([evaluateQuality(input,identity),evaluateQuality(input,identity)]);
    assert.equal(spawned.length,1);assert.equal(results[0].status,'fulfilled');assert.equal(results[1].status,'rejected');if(results[1].status==='rejected')assert.ok(hasCode('QE_BUSY')(results[1].reason));
    f.manifest.modelRevision='fixture-revision-v2';f.save();await evaluateQuality(input,f.read().identity);assert.equal(spawned.length,2);
    const pending=assert.rejects(evaluateQuality({...input,source:'hold'},f.read().identity),hasCode('QE_STOPPED'));await tick();
    const close=closeQualityEvaluator(),replacement=evaluateQuality(input,f.read().identity);await close;await pending;await replacement;assert.equal(spawned.length,3);
    await closeQualityEvaluator();assert.ok(spawned.every(({child})=>child.exitCode===0));
  }finally{await closeQualityEvaluator();await Promise.all(spawned.map(({closed})=>closed));t.mock.restoreAll();syncBuiltinESMExports();}
});
test('a manifest changed during asynchronous file verification cannot spawn an old identity',async t=>{
  const f=installation(),identity=f.read().identity,read=fs.createReadStream;let spawns=0,changed=false;
  t.mock.method(fs,'createReadStream',((...args:Parameters<typeof fs.createReadStream>)=>{if(!changed){changed=true;f.manifest.calibration.version='changed-during-verify';f.save();}return read(...args);}) as typeof fs.createReadStream);
  t.mock.method(childProcess,'spawn',(()=>{spawns++;throw new Error('must not spawn');}) as unknown as typeof childProcess.spawn);syncBuiltinESMExports();
  try{await assert.rejects(evaluateQuality(input,identity),hasCode('QE_CHANGED'));assert.equal(spawns,0);}finally{t.mock.restoreAll();syncBuiltinESMExports();}
});
