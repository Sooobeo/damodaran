import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import childProcess, {type ChildProcess} from 'node:child_process';
import {once} from 'node:events';
import {syncBuiltinESMExports} from 'node:module';

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'damodaran-runtime-'));
Object.assign(process.env, {APP_ROOT:temporary, DATA_DIR:path.join(temporary,'data'), NODE_ENV:'test', TRANSLATION_PROVIDER:'argos', OPENAI_API_KEY:''});
const {config} = await import('../lib/config');
const {readLocalRuntime, translationRuntime, assertTranslationAvailable, isLocalTranslationProvider} = await import('../lib/translation/runtime');
const {assertLocalReady, translateLocally, closeLocalTranslator} = await import('../lib/translation/local');
after(() => {closeLocalTranslator();fs.rmSync(temporary, {recursive:true,force:true});});
const digest = (value: string | Buffer) => createHash('sha256').update(value).digest('hex');
function installation() {
  const root = fs.mkdtempSync(path.join(temporary,'fixture-'));
  const write = (relative: string, value: string) => {const target=path.join(root,relative);fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(target,value);};
  write('python/python.exe','test fixture; never executed');
  write('.training/deployed/model/model.bin','test weights one');
  write('.training/deployed/model/tokenizer.json','test tokenizer');
  for (const file of ['scripts/model-training/bridge.py','scripts/model-training/runtime.py','scripts/local-translation/runtime.py','scripts/local-translation/bridge.py']) write(file,'test runtime; never executed');
  const files = ['model.bin','tokenizer.json'].map(name => {const relative=`.training/deployed/model/${name}`,data=fs.readFileSync(path.join(root,relative));return {path:relative,sha256:digest(data),size:data.length};});
  const manifest = {schemaVersion:1,provider:'finetuned',model:'TEST-FIXTURE-finance',modelHash:digest(JSON.stringify(files)),runtimeVersion:'test-runtime-v1',installedAt:'2026-09-09T00:00:00Z',pythonPath:'python/python.exe',modelPath:'.training/deployed/model',modelFiles:files};
  const save = () => write('.training/deployed/manifest.json',JSON.stringify(manifest)); save();
  return {root,manifest,write,save};
}
function argosInstallation(root = fs.mkdtempSync(path.join(temporary,'argos-'))) {
  const write = (relative: string, value: string) => {const target=path.join(root,relative);fs.mkdirSync(path.dirname(target),{recursive:true});fs.writeFileSync(target,value);};
  write('python/python.exe','test fixture; never executed');
  fs.mkdirSync(path.join(root,'.translation/model'),{recursive:true});
  write('scripts/local-translation/bridge.py','test bridge version one');
  write('scripts/local-translation/runtime.py','test runtime version one');
  const manifest = {schemaVersion:1,provider:'argos',model:'TEST-FIXTURE-argos',modelHash:digest('test model identity'),runtimeVersion:'test-runtime-v1',installedAt:'2026-09-09T00:00:00Z',pythonPath:'python/python.exe',modelPath:'.translation/model'};
  const manifestPath=path.join(root,'.translation/manifest.json');
  write('.translation/manifest.json',JSON.stringify(manifest));
  return {root,manifest,manifestPath,write};
}

test('Argos stays the default and finetuned requires registration without an API key', async () => {
  assert.equal(config.TRANSLATION_PROVIDER,'argos');
  assert.equal(isLocalTranslationProvider('argos'),true);assert.equal(isLocalTranslationProvider('finetuned'),true);assert.equal(isLocalTranslationProvider('openai'),false);
  config.TRANSLATION_PROVIDER='finetuned';
  try {
    const status=translationRuntime();assert.equal(status.provider,'finetuned');assert.equal(status.providerLabel,'금융 학습 모델');
    assert.equal(status.local,true);assert.equal(status.apiKeyRequired,false);assert.equal(status.configured,false);
    assert.equal(status.statusMessage,'학습·평가를 마친 모델을 등록해 주세요.');
    assert.throws(()=>assertTranslationAvailable(),error=>error instanceof Error&&'code' in error&&error.code==='SETUP_REQUIRED');
    await assert.rejects(translateLocally({model:status.identity,context:'',glossary:[],segments:[{id:'test',text:'Example.'}]}),/학습·평가를 마친 모델/);
  } finally {config.TRANSLATION_PROVIDER='argos';}
});

test('registered manifest resolves the finetuned bridge and separates local model identities', () => {
  const f=installation(),runtime=readLocalRuntime(f.root,'finetuned');assert.ok(runtime);
  assert.equal(runtime.provider,'finetuned');assert.equal(runtime.bridgePath,path.join(f.root,'scripts/model-training/bridge.py'));
  const original=runtime.identity;f.manifest.runtimeVersion='test-runtime-v2';f.save();assert.notEqual(readLocalRuntime(f.root,'finetuned')?.identity,original);
  const next=readLocalRuntime(f.root,'finetuned')!.identity;f.write('scripts/model-training/runtime.py','changed runtime source');assert.notEqual(readLocalRuntime(f.root,'finetuned')?.identity,next);
  const third=readLocalRuntime(f.root,'finetuned')!.identity;f.write('scripts/local-translation/runtime.py','changed shared processing');assert.notEqual(readLocalRuntime(f.root,'finetuned')?.identity,third);
});

test('tampered model bytes, added inventory and missing tokenizers prevent availability', () => {
  const f=installation();assert.ok(readLocalRuntime(f.root,'finetuned'));
  f.write('.training/deployed/model/model.bin','test weights two');assert.equal(readLocalRuntime(f.root,'finetuned'),null);
  f.write('.training/deployed/model/model.bin','test weights one');assert.ok(readLocalRuntime(f.root,'finetuned'));
  f.write('.training/deployed/model/unregistered.json','unexpected');assert.equal(readLocalRuntime(f.root,'finetuned'),null);
  fs.unlinkSync(path.join(f.root,'.training/deployed/model/unregistered.json'));assert.ok(readLocalRuntime(f.root,'finetuned'));
  fs.unlinkSync(path.join(f.root,'.training/deployed/model/tokenizer.json'));assert.equal(readLocalRuntime(f.root,'finetuned'),null);
});

test('manifest hash, provider, duplicate entries and paths outside the model are rejected', () => {
  const invalidHash=installation();invalidHash.manifest.modelHash='0'.repeat(64);invalidHash.save();assert.equal(readLocalRuntime(invalidHash.root,'finetuned'),null);
  const wrongProvider=installation();wrongProvider.manifest.provider='argos';wrongProvider.save();assert.equal(readLocalRuntime(wrongProvider.root,'finetuned'),null);
  const duplicate=installation();duplicate.manifest.modelFiles.push(duplicate.manifest.modelFiles[0]);duplicate.save();assert.equal(readLocalRuntime(duplicate.root,'finetuned'),null);
  const outside=installation();outside.manifest.modelFiles[0].path='python/python.exe';outside.save();assert.equal(readLocalRuntime(outside.root,'finetuned'),null);
  const escaped=installation();escaped.manifest.pythonPath=path.join(temporary,'outside.exe');escaped.save();assert.equal(readLocalRuntime(escaped.root,'finetuned'),null);
});

test('Argos identity tracks both execution files without changing the installation manifest', () => {
  const f=argosInstallation(),manifestBytes=fs.readFileSync(f.manifestPath);
  const runtime=readLocalRuntime(f.root,'argos');assert.ok(runtime);
  assert.notEqual(runtime.identity,`${f.manifest.model}:${f.manifest.modelHash}:${f.manifest.runtimeVersion}`);
  assert.equal(runtime.bridgePath,path.join(f.root,'scripts/local-translation/bridge.py'));
  assert.equal(readLocalRuntime(f.root,'argos')?.identity,runtime.identity);
  let previous=runtime.identity;
  for (const relative of ['scripts/local-translation/runtime.py','scripts/local-translation/bridge.py']) {
    const file=path.join(f.root,relative),stat=fs.statSync(file),original=fs.readFileSync(file,'utf8');
    // Equal-length edits with restored mtime still change the content identity.
    f.write(relative,original.replace('one','two'));fs.utimesSync(file,stat.atime,stat.mtime);
    const current=readLocalRuntime(f.root,'argos');assert.ok(current);
    assert.notEqual(current.identity,previous);
    assert.equal(current.modelHash,runtime.modelHash);assert.equal(current.runtimeVersion,runtime.runtimeVersion);
    assert.equal(readLocalRuntime(f.root,'argos')?.identity,current.identity);
    assert.deepEqual(fs.readFileSync(f.manifestPath),manifestBytes);
    previous=current.identity;
  }
  f.write('scripts/local-translation/bridge.py','test bridge version one');
  f.write('scripts/local-translation/runtime.py','test runtime version one');
  assert.equal(readLocalRuntime(f.root,'argos')?.identity,runtime.identity);
});

test('Argos rejects stale requests and replaces its child after each execution-code change', {timeout:20000}, async t => {
  const f=argosInstallation(temporary),manifestBytes=fs.readFileSync(f.manifestPath);
  const spawned:Array<{child:ChildProcess;closed:Promise<unknown[]>}> = [];
  const spawn=childProcess.spawn;
  // A real Node NDJSON child stands in for Python; no model or Python installation is opened.
  const fixtureBridge=String.raw`
    const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
    const root=process.env.APP_ROOT,manifest=JSON.parse(fs.readFileSync(path.join(root,'.translation/manifest.json'),'utf8'));
    const code=['scripts/local-translation/bridge.py','scripts/local-translation/runtime.py'].map(relative=>[relative,crypto.createHash('sha256').update(fs.readFileSync(path.join(root,relative))).digest('hex')]);
    const codeHash=crypto.createHash('sha256').update(JSON.stringify(code)).digest('hex');
    const send=value=>process.stdout.write(JSON.stringify(value)+'\n');
    send({ready:true,provider:manifest.provider,model:manifest.model,modelHash:manifest.modelHash,runtimeVersion:manifest.runtimeVersion});
    require('node:readline').createInterface({input:process.stdin}).on('line',line=>{
      const request=JSON.parse(line);send({id:request.id,data:{pid:process.pid,codeHash,segments:request.segments}});
    });
  `;
  t.mock.method(childProcess,'spawn',((command:string,args:string[],options:childProcess.SpawnOptions) => {
    assert.equal(command,path.join(temporary,'python/python.exe'));
    assert.deepEqual(args,['-u',path.join(temporary,'scripts/local-translation/bridge.py')]);
    const child=spawn(process.execPath,['-e',fixtureBridge],options);
    spawned.push({child,closed:once(child,'close')});return child;
  }) as typeof childProcess.spawn);
  syncBuiltinESMExports();
  const request=(model:string)=>({model,context:'',glossary:[],segments:[{id:'fixture-segment',text:'Fixture only.'}]});
  const changed=(error:unknown)=>error instanceof Error&&'code' in error&&error.code==='PROVIDER_CHANGED';
  const translate=async (identity:string) => {
    const input=request(identity),result=await translateLocally(input);
    const data=result.data as {pid:number;codeHash:string;segments:typeof input.segments};
    assert.equal(data.pid,spawned.at(-1)!.child.pid);assert.deepEqual(data.segments,input.segments);
    assert.equal(identity.endsWith(`:${data.codeHash}`),true);
    return data;
  };
  try {
    let identity=translationRuntime().identity;
    const legacy=`${f.manifest.model}:${f.manifest.modelHash}:${f.manifest.runtimeVersion}`;
    assert.throws(()=>assertTranslationAvailable({provider:'argos',model:legacy}),changed);
    await assert.rejects(translateLocally(request(legacy)),changed);assert.equal(spawned.length,0);
    let current=await translate(identity);
    assert.deepEqual(await translate(identity),current);assert.equal(spawned.length,1);
    for (const relative of ['scripts/local-translation/runtime.py','scripts/local-translation/bridge.py']) {
      const oldChild=spawned.at(-1)!;
      f.write(relative,fs.readFileSync(path.join(temporary,relative),'utf8').replace('one','two'));
      const next=translationRuntime().identity;assert.notEqual(next,identity);
      assert.throws(()=>assertTranslationAvailable({provider:'argos',model:identity}),changed);
      await assert.rejects(translateLocally(request(identity)),changed);
      const count:number=spawned.length;
      const nextData=await translate(next);assert.equal(spawned.length,count+1);
      assert.notEqual(nextData.pid,current.pid);assert.notEqual(nextData.codeHash,current.codeHash);
      assert.deepEqual(await oldChild.closed,[0,null]);
      assert.deepEqual(await translate(next),nextData);assert.equal(spawned.length,count+1);
      assert.deepEqual(fs.readFileSync(f.manifestPath),manifestBytes);
      identity=next;current=nextData;
    }
    assert.equal(spawned.length,3);
  } finally {
    closeLocalTranslator();
    t.mock.restoreAll();syncBuiltinESMExports();
    for (const {child} of spawned) if (child.exitCode===null&&!child.killed) child.kill();
    await Promise.all(spawned.map(({closed})=>closed));
  }
});

test('ready handshake rejects the wrong provider, weights or execution version', () => {
  const f=installation(),runtime=readLocalRuntime(f.root,'finetuned')!;
  const ready={ready:true,provider:'finetuned',model:runtime.model,modelHash:runtime.modelHash,runtimeVersion:runtime.runtimeVersion};
  assert.doesNotThrow(()=>assertLocalReady(ready,runtime));
  for (const value of [{...ready,provider:'argos'},{...ready,provider:undefined},{...ready,modelHash:'0'.repeat(64)},{...ready,runtimeVersion:'changed'},{...ready,ready:false}]) assert.throws(()=>assertLocalReady(value,runtime),error=>error instanceof Error&&'code' in error&&error.code==='PROVIDER_CHANGED');
  assert.doesNotThrow(()=>assertLocalReady({...ready,provider:undefined},{...runtime,provider:'argos'}));
});
