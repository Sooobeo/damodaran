import test,{after,type TestContext} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import childProcess from 'node:child_process';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {syncBuiltinESMExports} from 'node:module';

const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'damodaran-hymt-runtime-'));
Object.assign(process.env,{APP_ROOT:temporary,DATA_DIR:path.join(temporary,'data'),NODE_ENV:'test',TRANSLATION_PROVIDER:'argos',OPENAI_API_KEY:''});
const {config}=await import('../lib/config');
const {readLocalRuntime,translationRuntime,assertTranslationAvailable,isLocalTranslationProvider}=await import('../lib/translation/runtime');
const {readHymtRuntime,HYMT_MODEL,HYMT_MODEL_HASH}=await import('../lib/translation/hymt-runtime');
const {assertLocalReady,LocalEngine,translateLocally,closeLocalTranslator}=await import('../lib/translation/local');
const {translateSnapshot,setTestTranslationProvider,translationSnapshot}=await import('../lib/translation');
const {db,setupDatabase,closeDb,hash,id,now}=await import('../lib/db');
import type {TranslationSnapshot,ProviderRequest} from '../lib/translation';
for(const name of fs.readdirSync(path.join(process.cwd(),'lib/db/migrations')).filter(name=>name.endsWith('.sql'))){
  const destination=path.join(temporary,'lib/db/migrations',name);fs.mkdirSync(path.dirname(destination),{recursive:true});
  fs.copyFileSync(new URL(`../lib/db/migrations/${name}`,import.meta.url),destination);
}
setupDatabase();
after(()=>{setTestTranslationProvider(undefined);closeLocalTranslator();closeDb();assert.ok(temporary.startsWith(os.tmpdir()+path.sep));fs.rmSync(temporary,{recursive:true,force:true});});
const digest=(value:string|Buffer)=>createHash('sha256').update(value).digest('hex');
const MODEL_DIR='.training/comparisons/hy-mt2-7b-q8',MODEL_FILE=`${MODEL_DIR}/HY-MT2-7B-Q8_0.gguf`;
const CATALOG_HASH='d4af6a9fed8c6dba4c528abc52c64c19621c5fac98fd2bdb09e035769963de67';
const CODE_PATHS=['scripts/local-hymt/bridge.py','scripts/local-hymt/deployment.py','scripts/local-hymt/engine.py','scripts/local-hymt/process_owner.py','scripts/model-comparison/run_hymt.py','scripts/model-comparison/setup_hymt.py'];

function installation(t:TestContext){
  const root=fs.mkdtempSync(path.join(temporary,'bundle-'));
  const write=(relative:string,value:string)=>{const file=path.join(root,relative);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,value);return {path:relative,size:Buffer.byteLength(value),sha256:digest(value)};};
  const model=write(MODEL_FILE,'synthetic weight; never loaded');
  const archive=write(`${MODEL_DIR}/llama-b10874-bin-win-vulkan-x64.zip`,'synthetic ZIP; never extracted');
  const catalog=write('.translation/hymt/registrations/fixture/catalog.json','synthetic catalog; not real data');
  const python=write('.venv-training/Scripts/python.exe','synthetic executable; never spawned');
  const jinja=['__init__.py','environment.py'].map(name=>write(`.venv-training/Lib/site-packages/jinja2/${name}`,`synthetic Jinja source ${name}`));
  const prepared={version:'finance-quality-four-system-review-v1',status:'complete',identity:{inputFiles:Object.fromEntries([python,...jinja].map(entry=>[path.join(root,entry.path),entry.sha256]))}};
  const manifest={schemaVersion:1,provider:'hymt',model:HYMT_MODEL,modelHash:HYMT_MODEL_HASH,runtimeVersion:`hymt-local-v1:${digest('fixture execution')}`,
    installedAt:'2026-09-10T00:00:00+00:00',registrationId:'fixture',pythonPath:'.venv-training/Scripts/python.exe',modelPath:MODEL_DIR,profile:'raw',
    modelFiles:[{...model,size:7981928896,sha256:HYMT_MODEL_HASH}],runtimeFiles:Array.from({length:52},(_,i)=>write(`${MODEL_DIR}/runtime/file-${i}.dll`,`synthetic runtime ${i}`)),
    codeFiles:CODE_PATHS.map(name=>write(name,`synthetic code ${name}`)),catalog:{...catalog,sha256:CATALOG_HASH},
    evidenceFiles:['manifest.json','comparison-metrics.json','assistant-review-summary.json','selection.json'].map(name=>write(`.training/comparisons/fixture/${name}`,JSON.stringify(name==='manifest.json'?prepared:{fixture:name}))),
    execution:{revision:'ab8472660ac61fac25f1af43fac2599d52a8a775',templateSha256:'788ac16c5d7bfefc28655928ad524c8f378a44cb24d24fb125d6a5859b167677',
      runtimeOverrides:{'tokenizer.ggml.eos_token_id':'int:127960','tokenizer.ggml.eot_token_id':'int:127967','tokenizer.ggml.add_bos_token':'bool:false','tokenizer.ggml.add_eos_token':'bool:false'},
      sampling:{temperature:0.7,top_p:0.6,top_k:20,repeat_penalty:1.05,repeat_last_n:8192,min_p:0,seed:42,samplers:['penalties','temperature','top_k','top_p'],n_predict:4096,stop:['<|eos|>','<|extra_5|>'],ignore_eos:false,cache_prompt:false,stream:false,return_tokens:true,n_keep:0,id_slot:0,presence_penalty:0,frequency_penalty:0,dry_multiplier:0,mirostat:0,dynatemp_range:0,typical_p:1,xtc_probability:0,top_n_sigma:-1},
      contextSize:8192,threads:4,cpuOnly:true,contextPolicyVersion:'existing-title-neighbors-v1',pythonVersion:'3.12.0',jinjaVersion:'3.1.6'}};
  const save=(spacing?:number)=>{const raw=JSON.stringify(manifest,null,spacing);write('.translation/hymt/manifest.json',raw);write('.translation/hymt/registrations/fixture/manifest.json',raw);return raw;};
  save();
  // Only this tiny fixture's stat and digest stand in for pinned weight/catalog
  // bytes. No real model, installation directory or 8 GB file is accessed.
  const stat=fs.statSync;
  t.mock.method(fs,'statSync',((file:fs.PathLike,options?:unknown)=>{
    const result=(stat as Function)(file,options);
    const size=path.resolve(String(file))===path.join(root,MODEL_FILE)?7981928896:path.resolve(String(file))===path.join(root,archive.path)?35789222:null;
    return size!==null?new Proxy(result,{get:(target,key)=>key==='size'?(typeof target.size==='bigint'?BigInt(size):size):Reflect.get(target,key)}):result;
  }) as typeof fs.statSync);
  const fileHash=(file:string)=>{
    const raw=fs.readFileSync(file),text=raw.toString('utf8');
    if(file===path.join(root,MODEL_FILE)&&text==='synthetic weight; never loaded')return HYMT_MODEL_HASH;
    if(file===path.join(root,catalog.path)&&text==='synthetic catalog; not real data')return CATALOG_HASH;
    if(file===path.join(root,archive.path)&&text==='synthetic ZIP; never extracted')return '0113e9b49a5d979b32805092740ce97b9497494cdb308cf9568577da3e78e3c0';
    return digest(raw);
  };
  return {root,manifest,prepared,save,write,fileHash,read:()=>readHymtRuntime(root,fileHash)};
}
const hasCode=(code:string)=>(error:unknown)=>error instanceof Error&&'code' in error&&error.code===code;

test('Argos remains default; opted-in Hy-MT2 is free and missing registration fails without fallback',async()=>{
  assert.equal(config.TRANSLATION_PROVIDER,'argos');assert.equal(isLocalTranslationProvider('hymt'),true);
  config.TRANSLATION_PROVIDER='hymt';
  try{
    const status=translationRuntime();assert.equal(status.provider,'hymt');assert.equal(status.local,true);assert.equal(status.apiKeyRequired,false);assert.equal(status.configured,false);assert.equal(status.identity,'hymt:not-registered');
    assert.throws(()=>assertTranslationAvailable(),hasCode('SETUP_REQUIRED'));
    await assert.rejects(translateLocally({model:status.identity,context:'',segments:[{id:'a',text:'Fixture.'}],glossary:[]}),hasCode('SETUP_REQUIRED'));
    assert.equal(readLocalRuntime(temporary,'hymt'),null);
  }finally{config.TRANSLATION_PROVIDER='argos';}
});

test('registered runtime uses the exact raw manifest identity and fixed bridge',t=>{
  const f=installation(t),runtime=f.read(),raw=fs.readFileSync(path.join(f.root,'.translation/hymt/manifest.json'));
  assert.equal(runtime.identity,`hymt:${HYMT_MODEL_HASH}:${digest(raw)}`);assert.equal(runtime.bridgePath,path.join(f.root,CODE_PATHS[0]));assert.equal(runtime.pythonPath,path.join(f.root,'.venv-training/Scripts/python.exe'));
  assert.equal(f.read().identity,runtime.identity);
  f.save(2);const reserialized=f.read();assert.notEqual(reserialized.identity,runtime.identity);
  f.manifest.profile='contextual';f.save(2);assert.notEqual(f.read().identity,reserialized.identity);
});

test('all inventory categories reject changed bytes and allow original bytes to be restored',t=>{
  const f=installation(t);f.read();
  for(const relative of [MODEL_FILE,`${MODEL_DIR}/llama-b10874-bin-win-vulkan-x64.zip`,f.manifest.runtimeFiles[0].path,...CODE_PATHS,f.manifest.catalog.path,...f.manifest.evidenceFiles.map(e=>e.path)]){
    const original=fs.readFileSync(path.join(f.root,relative),'utf8');f.write(relative,original.replace(/.$/,'!'));
    assert.throws(f.read,/integrity/);f.write(relative,original);assert.ok(f.read());
  }
});

test('registration copy mismatch and unexpected or missing runtime files are rejected',t=>{
  const f=installation(t);f.write('.translation/hymt/registrations/fixture/manifest.json','{}');assert.throws(f.read,/registration/);f.save();
  const extra=`${MODEL_DIR}/runtime/unregistered.dll`;f.write(extra,'unexpected');assert.throws(f.read,/inventory/);fs.unlinkSync(path.join(f.root,extra));assert.ok(f.read());
  fs.unlinkSync(path.join(f.root,f.manifest.runtimeFiles[0].path));assert.throws(f.read);
});

test('the pinned runtime ZIP is required before the installation can be ready',t=>{
  const f=installation(t),archive=`${MODEL_DIR}/llama-b10874-bin-win-vulkan-x64.zip`;
  fs.unlinkSync(path.join(f.root,archive));assert.throws(f.read,/archive missing/);
});

test('manifest boundaries reject missing fields, wrong pinned identities, duplicate paths and traversal',t=>{
  const f=installation(t),original=structuredClone(f.manifest);
  const mutate:Array<(value:typeof f.manifest)=>void>=[
    m=>{m.modelHash='0'.repeat(64);},m=>{m.modelFiles[0].size=1;},m=>{m.runtimeVersion='wrong';},m=>{m.profile='other';},
    m=>{m.codeFiles[0]=m.codeFiles[1];},m=>{m.runtimeFiles[1]=m.runtimeFiles[0];},m=>{m.catalog.sha256='0'.repeat(64);},
    m=>{m.evidenceFiles[0].sha256=null as unknown as string;},m=>{m.execution.contextPolicyVersion='changed';},m=>{m.installedAt='2026-09-10';},
    m=>{m.execution.sampling.n_predict=4000;},m=>{m.execution.runtimeOverrides['tokenizer.ggml.eos_token_id']='int:2';},
    m=>{m.evidenceFiles[0].path='.training/comparisons/../../escape';},m=>{m.evidenceFiles[0].path='.training/comparisons/NUL.txt';},m=>{m.evidenceFiles[0].path='.training/comparisons/folder\\file';},
    m=>{delete (m as Partial<typeof m>).evidenceFiles;},
  ];
  for(const change of mutate){Object.assign(f.manifest,structuredClone(original));change(f.manifest);f.save();assert.throws(f.read);}
});

test('a junction inside the runtime tree cannot substitute inventory files',t=>{
  const f=installation(t),outside=fs.mkdtempSync(path.join(temporary,'outside-'));
  fs.symlinkSync(outside,path.join(f.root,MODEL_DIR,'runtime','linked'),'junction');assert.throws(f.read,/Linked/);
});

test('a file changing after its hash was read aborts verification',t=>{
  const f=installation(t),early=f.manifest.codeFiles[0].path,last=f.manifest.evidenceFiles.at(-1)!.path;
  assert.throws(()=>readHymtRuntime(f.root,file=>{const hash=f.fileHash(file);if(file===path.join(f.root,last))f.write(early,'changed after hash');return hash;}),/changed during/);
});

test('fresh readiness rejects changed Python/Jinja bytes and added Python source without changing version',t=>{
  const f=installation(t);f.read();
  for(const relative of ['.venv-training/Scripts/python.exe','.venv-training/Lib/site-packages/jinja2/environment.py']){
    const original=fs.readFileSync(path.join(f.root,relative),'utf8');f.write(relative,original.replace(/.$/,'!'));
    assert.throws(f.read,/Python dependency integrity/);f.write(relative,original);
  }
  const added='.venv-training/Lib/site-packages/jinja2/nested/late_source.PY';f.write(added,'new source');
  assert.throws(f.read,/Python dependency inventory/);fs.unlinkSync(path.join(f.root,added));assert.ok(f.read());
});

test('prepared Python dependency paths reject outside, duplicate, missing and invalid hash records',t=>{
  const f=installation(t),original=structuredClone(f.prepared.identity.inputFiles),python=path.join(f.root,'.venv-training/Scripts/python.exe');
  const mutations:Array<(inputs:Record<string,string>)=>void>=[
    values=>{delete values[python];},
    values=>{delete values[path.join(f.root,'.venv-training/Lib/site-packages/jinja2/environment.py')];},
    values=>{values[python]=null as unknown as string;},
    values=>{values[path.join(temporary,'outside.py')]='0'.repeat(64);},
    values=>{values[python.toUpperCase()]=values[python];},
  ];
  for(const change of mutations){
    f.prepared.identity.inputFiles=structuredClone(original);change(f.prepared.identity.inputFiles);
    f.manifest.evidenceFiles[0]=f.write(f.manifest.evidenceFiles[0].path,JSON.stringify(f.prepared));f.save();assert.throws(f.read);
  }
  f.prepared.identity.inputFiles={...original,[path.join(f.root,'unrelated-unopened.bin')]:'0'.repeat(64)};
  f.manifest.evidenceFiles[0]=f.write(f.manifest.evidenceFiles[0].path,JSON.stringify(f.prepared));f.save();assert.ok(f.read());
});

test('Hy-MT2 ready handshake requires every identity field including the full manifest hash',t=>{
  const runtime=installation(t).read(),ready={ready:true,provider:'hymt',model:runtime.model,modelHash:runtime.modelHash,runtimeVersion:runtime.runtimeVersion,identity:runtime.identity};
  assert.doesNotThrow(()=>assertLocalReady(ready,runtime));
  for(const key of ['ready','provider','model','modelHash','runtimeVersion','identity']){
    assert.throws(()=>assertLocalReady({...ready,[key]:undefined},runtime),hasCode('PROVIDER_CHANGED'));
    assert.throws(()=>assertLocalReady({...ready,[key]:'changed'},runtime),hasCode('PROVIDER_CHANGED'));
  }
});

function fakeBridge(t:TestContext,runtime:ConstructorParameters<typeof LocalEngine>[0]){
  const child=Object.assign(new EventEmitter(),{stdin:new PassThrough(),stdout:new PassThrough(),stderr:new PassThrough(),exitCode:null as number|null,killed:false,kill(){child.killed=true;child.exitCode=1;child.emit('exit',1);return true;}});
  const requests:ProviderRequest[]=[];
  child.stdin.on('data',chunk=>requests.push(JSON.parse(chunk.toString())));
  child.stdin.on('finish',()=>{child.exitCode=0;child.emit('exit',0);});
  t.mock.method(childProcess,'spawn',((command:string,args:string[],options:childProcess.SpawnOptions)=>{
    assert.equal(command,runtime.pythonPath);assert.deepEqual(args,['-u',runtime.bridgePath]);assert.equal(options.windowsHide,true);assert.equal(options.env?.OPENAI_API_KEY,'');return child;
  }) as unknown as typeof childProcess.spawn);syncBuiltinESMExports();
  t.after(()=>{child.kill();t.mock.restoreAll();syncBuiltinESMExports();});
  const engine=new LocalEngine(runtime),send=(message:unknown)=>child.stdout.write(JSON.stringify(message)+'\n');
  return {child,requests,engine,send,ready:()=>send({ready:true,provider:runtime.provider,model:runtime.model,modelHash:runtime.modelHash,runtimeVersion:runtime.runtimeVersion,identity:runtime.identity})};
}

test('mock bridge gets full source/context and modelIdentity; stale requests never reach stdin',async t=>{
  const runtime=installation(t).read(),f=fakeBridge(t,runtime);f.ready();await f.engine.ready;
  const request={model:runtime.identity,context:'Whole prior context.',glossary:[{source:'term',target:'앱 사전'}],segments:[{id:'s',text:'A whole source with USD 100 and r = 2.'}]};
  await assert.rejects(f.engine.translate({...request,model:'stale'}),hasCode('PROVIDER_CHANGED'));assert.equal(f.requests.length,0);
  const promise=f.engine.translate(request);await new Promise(resolve=>setImmediate(resolve));
  const wire=f.requests[0] as unknown as Record<string,unknown>;
  assert.equal(wire.modelIdentity,runtime.identity);assert.equal(wire.context,request.context);assert.deepEqual(wire.segments,request.segments);assert.equal(wire.model,undefined);
  const data={segments:[{id:'s',translatedText:' 원시 출력  ',warnings:[]}]};f.send({id:wire.id,data});const result=await promise;
  assert.deepEqual(result,{data,inputTokens:null,outputTokens:null,requestId:null});f.engine.close();
});

test('mock nonobject NDJSON terminates the child',async t=>{
  const runtime=installation(t).read(),f=fakeBridge(t,runtime);
  const rejection=assert.rejects(f.engine.ready,hasCode('LOCAL_PROTOCOL'));f.send(null);await rejection;assert.equal(f.child.killed,true);
});

test('long Hy-MT2 request survives five minutes, accepts a response, then still enforces its deadline',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const runtime=installation(t).read(),f=fakeBridge(t,runtime);f.ready();await f.engine.ready;
  const request={model:runtime.identity,context:'',glossary:[],segments:[{id:'long',text:'A long source paragraph.'}]};
  const first=f.engine.translate(request);await new Promise(resolve=>setImmediate(resolve));
  t.mock.timers.tick(300001);assert.equal(f.child.killed,false);assert.equal(f.engine.pending.size,1);
  const wire=f.requests[0] as unknown as {id:string};
  const data={segments:[{id:'long',translatedText:'긴 원문 문단이다.',warnings:[]}]};
  f.send({id:wire.id,data});assert.deepEqual((await first).data,data);assert.equal(f.engine.pending.size,0);
  const second=f.engine.translate(request);const rejected=assert.rejects(second,hasCode('LOCAL_TIMEOUT'));
  await new Promise(resolve=>setImmediate(resolve));t.mock.timers.tick(1800000);await rejected;
  assert.equal(f.child.killed,true);assert.equal(f.engine.pending.size,0);
});

test('Argos request retains its five-minute deadline',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const runtime={...installation(t).read(),provider:'argos' as const},f=fakeBridge(t,runtime);f.ready();await f.engine.ready;
  const pending=f.engine.translate({model:runtime.identity,context:'',glossary:[],segments:[{id:'s',text:'Source.'}]});
  const rejected=assert.rejects(pending,hasCode('LOCAL_TIMEOUT'));await new Promise(resolve=>setImmediate(resolve));
  t.mock.timers.tick(300000);await rejected;assert.equal(f.child.killed,true);assert.equal(f.engine.pending.size,0);
});

test('mock mismatching ready identity terminates the child before a request',async t=>{
  const runtime=installation(t).read(),f=fakeBridge(t,runtime),rejection=assert.rejects(f.engine.ready,hasCode('PROVIDER_CHANGED'));
  f.send({ready:true,provider:runtime.provider,model:runtime.model,modelHash:runtime.modelHash,runtimeVersion:runtime.runtimeVersion,identity:'stale identity'});
  await rejection;assert.equal(f.child.killed,true);assert.equal(f.requests.length,0);
});

test('mock startup error yields registration guidance and discards diagnostics',async t=>{
  const runtime=installation(t).read(),f=fakeBridge(t,runtime);
  const rejection=assert.rejects(f.engine.ready,error=>hasCode('LOCAL_SETUP_ERROR')(error)&&error instanceof Error&&error.message.includes('등록')&&!error.message.includes('sensitive'));
  f.child.stderr.write('sensitive source diagnostic');f.send({ready:false,error:{message:'sensitive source diagnostic'}});await rejection;assert.equal(f.child.killed,true);
});

function snapshot(provider:'hymt'|'argos'='hymt'):TranslationSnapshot{return {blockId:'fixture',sourceVersionId:'version',resourceId:'resource',text:'If r = 2, do not change USD 100 or -5%.',type:'paragraph',structure:{},context:'Full title and neighboring paragraph.',contextHash:'context',glossary:[{source:'r',target:'도우미 사전'}],glossaryVersion:'fixture',cacheKey:'fixture',model:'fixture-full-identity',provider,promptVersion:'fixture'};}

test('Hy-MT2 whole source and context pass unchanged; raw translation and numeric warnings are retained',async()=>{
  const s=snapshot(),raw='  r = 2일 때 USD 100 또는 -5%를 바꾸지 않는다.\n';
  setTestTranslationProvider(async input=>{assert.equal(input.segments[0].text,s.text);assert.equal(input.context,s.context);assert.equal(input.model,s.model);return {data:{segments:[{id:s.blockId,translatedText:raw,warnings:['도우미 검사 경고']}]},inputTokens:null,outputTokens:null,requestId:null};});
  const result=await translateSnapshot(s);assert.equal(result.textKo,raw);assert.deepEqual(result.warnings,['도우미 검사 경고']);
  setTestTranslationProvider(async()=>({data:{segments:[{id:s.blockId,translatedText:'r = 2일 때 USD 101을 바꾼다.',warnings:[]}]},inputTokens:null,outputTokens:null,requestId:null}));
  const damaged=await translateSnapshot(s);assert.equal(damaged.textKo,'r = 2일 때 USD 101을 바꾼다.');assert.ok(damaged.warnings.some(w=>w.includes('숫자')));
});

test('Hy-MT2 empty, control-token, over-limit and duplicate responses are rejected',async()=>{
  const s=snapshot();
  for(const text of ['',' \n','<|eos|>','bad\u0000text','x'.repeat(80001)]){
    setTestTranslationProvider(async()=>({data:{segments:[{id:s.blockId,translatedText:text,warnings:[]}]},inputTokens:null,outputTokens:null,requestId:null}));
    await assert.rejects(translateSnapshot(s),hasCode('INVALID_RESPONSE'));
  }
  setTestTranslationProvider(async()=>({data:{segments:[{id:s.blockId,translatedText:'결과',warnings:[]},{id:s.blockId,translatedText:'결과',warnings:[]}]},inputTokens:null,outputTokens:null,requestId:null}));
  await assert.rejects(translateSnapshot(s),hasCode('INVALID_RESPONSE'));
});

test('Argos retains source protection while Hy-MT2 table cells retain their own raw identities',async()=>{
  const argos=snapshot('argos');setTestTranslationProvider(async input=>{assert.notEqual(input.segments[0].text,argos.text);assert.match(input.segments[0].text,/__PV_/);return {data:{segments:input.segments.map(s=>({id:s.id,translatedText:s.text,warnings:[]}))},inputTokens:null,outputTokens:null,requestId:null};});
  assert.equal((await translateSnapshot(argos)).textKo,argos.text);
  const table={...snapshot(),type:'table',structure:{rows:[{cells:[{text:'USD 100'},{text:'2'}]}]}};
  setTestTranslationProvider(async input=>{assert.deepEqual(input.segments,[{id:'fixture:0:0',text:'USD 100'}]);return {data:{segments:[{id:'fixture:0:0',translatedText:' USD 100 ',warnings:[]}]},inputTokens:null,outputTokens:null,requestId:null};});
  assert.equal((await translateSnapshot(table)).textKo,' USD 100 \t2');
});

test('provider and full context participate in existing cache identity without changing saved source',()=>{
  const resource=id(),version=id(),block=id(),text='Synthetic unmodified source.';
  db().prepare("INSERT INTO resources(id,source_type,title_en,title_ko,kind,format,created_at) VALUES(?,'upload','Synthetic title','합성 제목','본문','html',?)").run(resource,now());
  db().prepare("INSERT INTO source_versions(id,resource_id,file_hash,original_path,mime,format,byte_size,imported_at,extractor_version,extraction_config_hash,extraction_status) VALUES(?,?,?,'originals/unused','text/html','html',1,?,'fixture','fixture','ready')").run(version,resource,hash(text),now());
  db().prepare("INSERT INTO source_blocks(id,source_version_id,sort_order,type,text,source_hash) VALUES(?,?,0,'paragraph',?,?)").run(block,version,text,hash(text));
  const argos=translationSnapshot(block);config.TRANSLATION_PROVIDER='hymt';
  try{const hymt=translationSnapshot(block);assert.notEqual(hymt.cacheKey,argos.cacheKey);assert.equal(hymt.context,argos.context);assert.equal(hymt.text,text);assert.equal(hymt.provider,'hymt');
    db().prepare('UPDATE resources SET title_en=? WHERE id=?').run('Another synthetic title',resource);assert.notEqual(translationSnapshot(block).cacheKey,hymt.cacheKey);
  }finally{config.TRANSLATION_PROVIDER='argos';}
});
