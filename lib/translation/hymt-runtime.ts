import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { z } from 'zod';

// These identify the registered installation; execution-contract validation also
// runs in Python before the bridge can emit its matching ready handshake.
export const HYMT_MODEL = 'tencent/Hy-MT2-7B-GGUF';
export const HYMT_MODEL_HASH = '58b3ad55dd6f6fa08c695cddc34fb5f8f708a844f78ae10508071914b0ed67c0';
const MODEL_DIR = '.training/comparisons/hy-mt2-7b-q8';
const MODEL_FILE = `${MODEL_DIR}/HY-MT2-7B-Q8_0.gguf`;
const MODEL_SIZE = 7981928896;
const PYTHON_PATH = '.venv-training/Scripts/python.exe';
const JINJA_PATH = '.venv-training/Lib/site-packages/jinja2';
const CATALOG_HASH = 'd4af6a9fed8c6dba4c528abc52c64c19621c5fac98fd2bdb09e035769963de67';
const RUNTIME_ZIP = 'llama-b10874-bin-win-vulkan-x64.zip';
const RUNTIME_ZIP_HASH = '0113e9b49a5d979b32805092740ce97b9497494cdb308cf9568577da3e78e3c0';
const CODE_PATHS = [
  'scripts/local-hymt/bridge.py', 'scripts/local-hymt/deployment.py',
  'scripts/local-hymt/engine.py', 'scripts/local-hymt/process_owner.py',
  'scripts/model-comparison/run_hymt.py', 'scripts/model-comparison/setup_hymt.py',
];
const sha = z.string().regex(/^[a-f0-9]{64}$/);
const fileSchema = z.object({path:z.string().min(1),size:z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),sha256:sha}).strict();
const manifestSchema = z.object({
  schemaVersion:z.literal(1), provider:z.literal('hymt'), model:z.literal(HYMT_MODEL), modelHash:z.literal(HYMT_MODEL_HASH),
  runtimeVersion:z.string().regex(/^hymt-local-v1:[a-f0-9]{64}$/),
  installedAt:z.iso.datetime({offset:true}), registrationId:z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/),
  pythonPath:z.literal('.venv-training/Scripts/python.exe'), modelPath:z.literal(MODEL_DIR), profile:z.enum(['raw','contextual']),
  modelFiles:z.array(fileSchema).length(1), runtimeFiles:z.array(fileSchema).length(52), codeFiles:z.array(fileSchema).length(6),
  catalog:fileSchema, evidenceFiles:z.array(fileSchema).length(4),
  execution:z.object({
    revision:z.literal('ab8472660ac61fac25f1af43fac2599d52a8a775'),
    templateSha256:z.literal('788ac16c5d7bfefc28655928ad524c8f378a44cb24d24fb125d6a5859b167677'),
    runtimeOverrides:z.object({
      'tokenizer.ggml.eos_token_id':z.literal('int:127960'),'tokenizer.ggml.eot_token_id':z.literal('int:127967'),
      'tokenizer.ggml.add_bos_token':z.literal('bool:false'),'tokenizer.ggml.add_eos_token':z.literal('bool:false'),
    }).strict(),
    sampling:z.object({
      temperature:z.literal(0.7),top_p:z.literal(0.6),top_k:z.literal(20),repeat_penalty:z.literal(1.05),repeat_last_n:z.literal(8192),min_p:z.literal(0),seed:z.literal(42),
      samplers:z.tuple([z.literal('penalties'),z.literal('temperature'),z.literal('top_k'),z.literal('top_p')]),n_predict:z.literal(4096),
      stop:z.tuple([z.literal('<|eos|>'),z.literal('<|extra_5|>')]),ignore_eos:z.literal(false),cache_prompt:z.literal(false),stream:z.literal(false),return_tokens:z.literal(true),
      n_keep:z.literal(0),id_slot:z.literal(0),presence_penalty:z.literal(0),frequency_penalty:z.literal(0),dry_multiplier:z.literal(0),mirostat:z.literal(0),dynatemp_range:z.literal(0),typical_p:z.literal(1),xtc_probability:z.literal(0),top_n_sigma:z.literal(-1),
    }).strict(),
    contextSize:z.literal(8192), threads:z.literal(4), cpuOnly:z.literal(true),
    contextPolicyVersion:z.literal('existing-title-neighbors-v1'), pythonVersion:z.string().min(1), jinjaVersion:z.string().min(1),
  }).strict(),
}).strict();

/** No model loading: use the caller's stat-based SHA cache for inventory files. */
export function readHymtRuntime(appRoot:string, fileHash:(file:string)=>string) {
  const root=path.resolve(appRoot);
  for(let parent=root;;parent=path.dirname(parent)){
    if(fs.lstatSync(parent).isSymbolicLink())throw new Error('Linked installation root');
    if(path.dirname(parent)===parent)break;
  }
  const localPath=(relative:string)=>{
    const parts=relative.split('/');
    if(!relative||parts.some(part=>!part||part==='.'||part==='..'||/[<>:"\\|?*\x00-\x1f]/.test(part)||/[ .]$/.test(part)||/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$/i.test(part)))throw new Error('Invalid installation path');
    let current=root;
    for(const part of parts){current=path.join(current,part);if(fs.lstatSync(current).isSymbolicLink())throw new Error('Linked installation path');}
    const resolved=fs.realpathSync(current);
    if(!resolved.startsWith(fs.realpathSync(root)+path.sep))throw new Error('Installation path escaped');
    return resolved;
  };
  const stamps=new Map<string,string>();
  const stamp=(file:string)=>{const s=fs.statSync(file,{bigint:true});return [s.dev,s.ino,s.size,s.mtimeNs,s.ctimeNs].join(':');};
  const metadata=(relative:string)=>{
    const file=localPath(relative),stat=fs.statSync(file);
    if(!stat.isFile()||stat.size>32*1024*1024)throw new Error('Invalid registration metadata');
    const before=stamp(file),raw=fs.readFileSync(file);
    if(stamp(file)!==before)throw new Error('Registration changed while reading');
    stamps.set(relative,before);return raw;
  };
  const raw=metadata('.translation/hymt/manifest.json');
  const manifest=manifestSchema.parse(JSON.parse(raw.toString('utf8')));
  const registration=`.translation/hymt/registrations/${manifest.registrationId}`;
  if(!metadata(`${registration}/manifest.json`).equals(raw))throw new Error('Immutable registration changed');
  if(manifest.modelFiles[0].path!==MODEL_FILE||manifest.modelFiles[0].size!==MODEL_SIZE||manifest.modelFiles[0].sha256!==HYMT_MODEL_HASH)throw new Error('Model inventory changed');
  if(manifest.catalog.path!==`${registration}/catalog.json`||manifest.catalog.sha256!==CATALOG_HASH)throw new Error('Registered catalog changed');
  if(CODE_PATHS.some(name=>!manifest.codeFiles.some(entry=>entry.path===name)))throw new Error('Code inventory changed');
  if(manifest.runtimeFiles.some(entry=>!entry.path.startsWith(`${MODEL_DIR}/runtime/`))||manifest.evidenceFiles.some(entry=>!entry.path.startsWith('.training/comparisons/')))throw new Error('Inventory scope changed');
  const seen=new Set<string>();
  const archiveName=[`.training/comparisons/translategemma-4b-q4/${RUNTIME_ZIP}`,`${MODEL_DIR}/${RUNTIME_ZIP}`].find(name=>fs.existsSync(path.join(root,name)));
  if(!archiveName)throw new Error('Pinned runtime archive missing');
  const archive={path:archiveName,size:35789222,sha256:RUNTIME_ZIP_HASH};
  for(const entry of [...manifest.modelFiles,...manifest.runtimeFiles,...manifest.codeFiles,manifest.catalog,...manifest.evidenceFiles,archive]){
    if(seen.has(entry.path.toLowerCase()))throw new Error('Duplicate inventory path');
    seen.add(entry.path.toLowerCase());
    const file=localPath(entry.path),before=stamp(file),stat=fs.statSync(file);
    if(!stat.isFile()||stat.size!==entry.size||fileHash(file)!==entry.sha256||stamp(file)!==before)throw new Error('Deployment file integrity mismatch');
    stamps.set(entry.path,before);
  }
  const runtimeNames=new Set(manifest.runtimeFiles.map(entry=>entry.path));
  const walk=(relative:string):string[]=>fs.readdirSync(localPath(relative),{withFileTypes:true}).flatMap(entry=>{
    const name=`${relative}/${entry.name}`,file=localPath(name);
    return fs.statSync(file).isDirectory()?walk(name):[name];
  });
  const actual=walk(`${MODEL_DIR}/runtime`);
  if(actual.length!==runtimeNames.size||actual.some(name=>!runtimeNames.has(name)))throw new Error('Runtime inventory changed');
  const evidence=manifest.evidenceFiles.map(entry=>{
    const bytes=metadata(entry.path);
    if(bytes.length!==entry.size||createHash('sha256').update(bytes).digest('hex')!==entry.sha256)throw new Error('Deployment evidence changed');
    return JSON.parse(bytes.toString('utf8')) as unknown;
  });
  const isObject=(value:unknown):value is Record<string,unknown>=>Boolean(value)&&typeof value==='object'&&!Array.isArray(value);
  const prepared=evidence.filter(value=>isObject(value)&&value.version==='finance-quality-four-system-review-v1'&&value.status==='complete');
  if(prepared.length!==1||!isObject(prepared[0])||!isObject(prepared[0].identity)||!isObject(prepared[0].identity.inputFiles))throw new Error('Prepared dependency inventory missing');
  const inputFiles=Object.entries(prepared[0].identity.inputFiles),inputs=new Map<string,string>(),inputNames=new Set<string>();
  if(!inputFiles.length||inputFiles.length>10000)throw new Error('Prepared dependency inventory invalid');
  // Validate the entire map lexically. Fresh readiness opens only the exact
  // interpreter and Jinja source paths below, never other caller-listed files.
  for(const [name,expected] of inputFiles){
    const relative=path.relative(root,name).split(path.sep).join('/');
    if(name.length>4096||!path.isAbsolute(name)||name.split(/[\\/]/).some(part=>part==='.'||part==='..')||!relative||relative==='..'||relative.startsWith('../')||path.isAbsolute(relative)||!sha.safeParse(expected).success||inputNames.has(relative.toLowerCase()))throw new Error('Invalid prepared dependency path or hash');
    inputNames.add(relative.toLowerCase());inputs.set(relative,expected as string);
  }
  const jinjaNames=new Set([...inputs.keys()].filter(name=>name.startsWith(`${JINJA_PATH}/`)&&/\.py$/i.test(name)));
  if(!inputs.has(PYTHON_PATH)||!jinjaNames.has(`${JINJA_PATH}/__init__.py`))throw new Error('Python dependency inventory missing');
  for(const relative of [PYTHON_PATH,...jinjaNames]){
    const file=localPath(relative),before=stamp(file);
    if(!fs.statSync(file).isFile()||fileHash(file)!==inputs.get(relative)||stamp(file)!==before)throw new Error('Python dependency integrity mismatch');
    stamps.set(relative,before);
  }
  const assertJinjaInventory=()=>{
    const names=walk(JINJA_PATH).filter(name=>/\.py$/i.test(name));
    if(names.length!==jinjaNames.size||names.some(name=>!jinjaNames.has(name)))throw new Error('Python dependency inventory changed');
  };
  assertJinjaInventory();
  const pythonPath=localPath(manifest.pythonPath),modelPath=localPath(manifest.modelPath),bridgePath=localPath(CODE_PATHS[0]);
  if(!fs.statSync(pythonPath).isFile()||!fs.statSync(modelPath).isDirectory())throw new Error('Local runtime missing');
  for(const [relative,before] of stamps)if(stamp(localPath(relative))!==before)throw new Error('Deployment changed during verification');
  assertJinjaInventory();
  // Do not reconstruct Python's canonical execution JSON in JavaScript.
  const identity=`hymt:${manifest.modelHash}:${createHash('sha256').update(raw).digest('hex')}`;
  return {...manifest,pythonPath,modelPath,bridgePath,identity};
}
