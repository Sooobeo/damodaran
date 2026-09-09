import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';

const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'damodaran-runtime-'));
Object.assign(process.env, {APP_ROOT:temporary, DATA_DIR:path.join(temporary,'data'), NODE_ENV:'test', TRANSLATION_PROVIDER:'argos', OPENAI_API_KEY:''});
const {config} = await import('../lib/config');
const {readLocalRuntime, translationRuntime, assertTranslationAvailable, isLocalTranslationProvider} = await import('../lib/translation/runtime');
const {assertLocalReady, translateLocally} = await import('../lib/translation/local');
after(() => fs.rmSync(temporary, {recursive:true,force:true}));
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

test('Argos installation keeps its existing cache identity', () => {
  const f=installation();const {modelFiles:_files,...manifest}=f.manifest;
  f.write('.translation/manifest.json',JSON.stringify({...manifest,provider:'argos'}));
  const runtime=readLocalRuntime(f.root,'argos');assert.ok(runtime);
  assert.equal(runtime.identity,`${manifest.model}:${manifest.modelHash}:${manifest.runtimeVersion}`);
  assert.equal(runtime.bridgePath,path.join(f.root,'scripts/local-translation/bridge.py'));
});

test('ready handshake rejects the wrong provider, weights or execution version', () => {
  const f=installation(),runtime=readLocalRuntime(f.root,'finetuned')!;
  const ready={ready:true,provider:'finetuned',model:runtime.model,modelHash:runtime.modelHash,runtimeVersion:runtime.runtimeVersion};
  assert.doesNotThrow(()=>assertLocalReady(ready,runtime));
  for (const value of [{...ready,provider:'argos'},{...ready,provider:undefined},{...ready,modelHash:'0'.repeat(64)},{...ready,runtimeVersion:'changed'},{...ready,ready:false}]) assert.throws(()=>assertLocalReady(value,runtime),error=>error instanceof Error&&'code' in error&&error.code==='PROVIDER_CHANGED');
  assert.doesNotThrow(()=>assertLocalReady({...ready,provider:undefined},{...runtime,provider:'argos'}));
});
