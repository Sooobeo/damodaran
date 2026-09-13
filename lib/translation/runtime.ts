import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { z } from 'zod';
import { APP_ROOT, config } from '../config';
import { PipelineError } from '../sources';
import { HYMT_MODEL, readHymtRuntime } from './hymt-runtime';

export type TranslationProvider = 'argos' | 'finetuned' | 'hymt' | 'openai';
export type LocalTranslationProvider = Exclude<TranslationProvider, 'openai'>;
export function isLocalTranslationProvider(provider: string) { return provider === 'argos' || provider === 'finetuned' || provider === 'hymt'; }
export const OPENAI_PROMPT_VERSION = 'finance-ko-v3';
export const ARGOS_PROMPT_VERSION = 'argos-finance-ko-v3';
export const FINETUNED_PROMPT_VERSION = 'finetuned-finance-ko-v1';
export const HYMT_PROMPT_VERSION = 'hymt-finance-ko-v1';
const fileSchema = z.object({path:z.string().min(1),size:z.number().int().nonnegative(),sha256:z.string().regex(/^[a-f0-9]{64}$/)}).strict();
const manifestSchema = z.object({
  schemaVersion: z.literal(1), provider: z.enum(['argos','finetuned']), model: z.string().min(1),
  modelHash: z.string().regex(/^[a-f0-9]{64}$/), runtimeVersion: z.string().min(1),
  installedAt: z.string(), pythonPath: z.string().min(1), modelPath: z.string().min(1),
  modelFiles: z.array(fileSchema).optional(),
});
const fileHashes = new Map<string, {stat: string; hash: string}>();
function fileHash(file: string) {
  const stat = fs.statSync(file, {bigint:true});
  if (!stat.isFile()) throw new Error('Expected a regular runtime file');
  const stamp = [stat.dev,stat.ino,stat.size,stat.mtimeNs,stat.ctimeNs].join(':');
  const cached = fileHashes.get(file); if (cached?.stat === stamp) return cached.hash;
  const digest = createHash('sha256'), buffer = Buffer.allocUnsafe(1024 * 1024), descriptor = fs.openSync(file, 'r');
  try { let bytes: number; while ((bytes = fs.readSync(descriptor, buffer, 0, buffer.length, null)) > 0) digest.update(buffer.subarray(0, bytes)); }
  finally { fs.closeSync(descriptor); }
  const after = fs.statSync(file, {bigint:true});
  if ([after.dev,after.ino,after.size,after.mtimeNs,after.ctimeNs].join(':') !== stamp) throw new Error('Runtime file changed during verification');
  const hash = digest.digest('hex'); fileHashes.set(file, {stat:stamp,hash}); return hash;
}
export function readLocalRuntime(appRoot: string, provider: LocalTranslationProvider) {
  try {
    if (provider === 'hymt') return readHymtRuntime(appRoot, fileHash);
    const root = fs.realpathSync(appRoot);
    const manifest = manifestSchema.parse(JSON.parse(fs.readFileSync(path.join(root, provider === 'finetuned' ? '.training/deployed/manifest.json' : '.translation/manifest.json'), 'utf8')));
    if (manifest.provider !== provider) throw new Error('Wrong local provider');
    const localPath = (relative: string) => {
      if (path.isAbsolute(relative)) throw new Error('Invalid installation path');
      const resolved = fs.realpathSync(path.resolve(root, relative));
      if (!resolved.startsWith(root + path.sep)) throw new Error('Invalid installation path');
      return resolved;
    };
    const pythonPath = localPath(manifest.pythonPath), modelPath = localPath(manifest.modelPath);
    if (!fs.statSync(pythonPath).isFile() || !fs.statSync(modelPath).isDirectory()) throw new Error('Local runtime missing');
    const bridgePath = localPath(provider === 'finetuned' ? 'scripts/model-training/bridge.py' : 'scripts/local-translation/bridge.py');
    let identity = `${manifest.model}:${manifest.modelHash}:${manifest.runtimeVersion}`;
    if (provider === 'finetuned') {
      const files = manifest.modelFiles; if (!files?.length) throw new Error('Model inventory missing');
      const seen = new Set<string>();
      for (const entry of files) {
        const file = localPath(entry.path);
        if (!file.startsWith(modelPath + path.sep) || seen.has(file)) throw new Error('Invalid model inventory');
        seen.add(file);
        if (fs.statSync(file).size !== entry.size || fileHash(file) !== entry.sha256) throw new Error('Model integrity mismatch');
      }
      const walk = (directory: string): string[] => fs.readdirSync(directory, {withFileTypes:true}).flatMap(entry => {
        if (entry.isSymbolicLink()) throw new Error('Linked model inventory is unsupported');
        const file = path.join(directory, entry.name); return entry.isDirectory() ? walk(file) : [fs.realpathSync(file)];
      });
      const actual = walk(modelPath); if (actual.length !== seen.size || actual.some(file => !seen.has(file))) throw new Error('Model inventory changed');
      // Match Python json.dumps(sort_keys=True, ensure_ascii=True, separators=(',', ':')).
      const canonical = JSON.stringify(files.map(entry => ({path:entry.path,sha256:entry.sha256,size:entry.size}))).replace(/[\u007f-\uffff]/g, character => `\\u${character.charCodeAt(0).toString(16).padStart(4,'0')}`);
      if (createHash('sha256').update(canonical).digest('hex') !== manifest.modelHash) throw new Error('Model identity mismatch');
      identity = `finetuned:${identity}`;
    }
    // Processing-code updates must invalidate both cached results and a running local engine.
    const codeFiles = provider === 'finetuned'
      ? ['scripts/model-training/bridge.py','scripts/model-training/runtime.py','scripts/local-translation/runtime.py']
      : ['scripts/local-translation/bridge.py','scripts/local-translation/runtime.py'];
    const code = codeFiles.map(relative => [relative,fileHash(localPath(relative))]);
    const runtimeHash = createHash('sha256').update(JSON.stringify(code)).digest('hex');
    identity = `${identity}:${runtimeHash}`;
    return { ...manifest, pythonPath, modelPath, bridgePath, identity };
  } catch {
    // Recheck restored files instead of retaining hashes read during failed verification.
    fileHashes.clear(); return null;
  }
}
export function localRuntime() { return config.TRANSLATION_PROVIDER === 'openai' ? null : readLocalRuntime(APP_ROOT, config.TRANSLATION_PROVIDER); }
export function translationRuntime() {
  if (config.TRANSLATION_PROVIDER === 'openai') return {
    provider: 'openai' as const, providerLabel: 'OpenAI', local: false, apiKeyRequired: true,
    model: config.TRANSLATION_MODEL, identity: config.TRANSLATION_MODEL, promptVersion: OPENAI_PROMPT_VERSION,
    configured: Boolean(config.OPENAI_API_KEY && config.TRANSLATION_MODEL), modelConfigured: Boolean(config.TRANSLATION_MODEL),
    statusMessage: config.OPENAI_API_KEY && config.TRANSLATION_MODEL ? '선택한 범위는 외부 유료 API에서 번역합니다.' : 'OpenAI를 선택했습니다. API 키와 모델을 설정하세요.',
  };
  const runtime = localRuntime();
  if (config.TRANSLATION_PROVIDER === 'hymt') return {
    provider:'hymt' as const,providerLabel:'Hy-MT2',local:true,apiKeyRequired:false,
    model:runtime?.model || HYMT_MODEL,identity:runtime?.identity || 'hymt:not-registered',promptVersion:HYMT_PROMPT_VERSION,
    configured:Boolean(runtime),modelConfigured:Boolean(runtime),
    statusMessage:runtime ? '무료 로컬 번역이 준비되었습니다. 원문은 이 PC에서 처리합니다.' : '비교와 검토를 마친 Hy-MT2 구성을 등록하고 등록 파일의 무결성을 확인해 주세요.',
  };
  const finetuned = config.TRANSLATION_PROVIDER === 'finetuned';
  return {
    provider: config.TRANSLATION_PROVIDER, providerLabel: finetuned ? '금융 학습 모델' : 'Argos Translate', local: true, apiKeyRequired: false,
    model: runtime?.model || (finetuned ? '금융 학습 모델' : 'argos-en_ko-1.1'), identity: runtime?.identity || (finetuned ? 'finetuned:not-installed' : 'argos-en_ko:not-installed'), promptVersion: finetuned ? FINETUNED_PROMPT_VERSION : ARGOS_PROMPT_VERSION,
    configured: Boolean(runtime), modelConfigured: Boolean(runtime),
    statusMessage: runtime ? '무료 로컬 번역이 준비되었습니다. 원문은 이 PC에서 처리합니다.' : finetuned ? '학습·평가를 마친 모델을 등록해 주세요.' : '무료 번역 모델을 설치하세요: npm run setup:translation',
  };
}
export function assertTranslationAvailable(snapshot?: { provider: TranslationProvider; model: string; promptVersion?: string }) {
  const runtime = translationRuntime();
  // Changing to a free provider must never resume an old paid request.
  if (snapshot && (snapshot.provider !== runtime.provider || snapshot.model !== runtime.identity || (snapshot.promptVersion && snapshot.promptVersion !== runtime.promptVersion))) {
    throw new PipelineError('PROVIDER_CHANGED', '번역 제공자·모델 또는 처리 규칙이 바뀌었습니다. 현재 설정으로 다시 번역을 요청하세요.');
  }
  if (!runtime.configured) throw new PipelineError('SETUP_REQUIRED', runtime.statusMessage);
  return runtime;
}
