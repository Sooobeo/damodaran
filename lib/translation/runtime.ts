import fs from 'node:fs';
import path from 'node:path';
import { z } from 'zod';
import { APP_ROOT, config } from '../config';
import { PipelineError } from '../sources';

export type TranslationProvider = 'argos' | 'openai';
export const OPENAI_PROMPT_VERSION = 'finance-ko-v3';
export const ARGOS_PROMPT_VERSION = 'argos-finance-ko-v3';
const manifestSchema = z.object({
  schemaVersion: z.literal(1), provider: z.literal('argos'), model: z.string().min(1),
  modelHash: z.string().regex(/^[a-f0-9]{64}$/), runtimeVersion: z.string().min(1),
  installedAt: z.string(), pythonPath: z.string().min(1), modelPath: z.string().min(1),
});
export function localRuntime() {
  try {
    const manifest = manifestSchema.parse(JSON.parse(fs.readFileSync(path.join(APP_ROOT, '.translation/manifest.json'), 'utf8')));
    const localPath = (relative: string) => {
      if (path.isAbsolute(relative)) throw new Error('Invalid installation path');
      const resolved = fs.realpathSync(path.resolve(APP_ROOT, relative));
      if (!resolved.startsWith(fs.realpathSync(APP_ROOT) + path.sep)) throw new Error('Invalid installation path');
      return resolved;
    };
    const pythonPath = localPath(manifest.pythonPath), modelPath = localPath(manifest.modelPath);
    return { ...manifest, pythonPath, modelPath, identity: `${manifest.model}:${manifest.modelHash}:${manifest.runtimeVersion}` };
  } catch { return null; }
}
export function translationRuntime() {
  if (config.TRANSLATION_PROVIDER === 'openai') return {
    provider: 'openai' as const, providerLabel: 'OpenAI', local: false, apiKeyRequired: true,
    model: config.TRANSLATION_MODEL, identity: config.TRANSLATION_MODEL, promptVersion: OPENAI_PROMPT_VERSION,
    configured: Boolean(config.OPENAI_API_KEY && config.TRANSLATION_MODEL), modelConfigured: Boolean(config.TRANSLATION_MODEL),
    statusMessage: config.OPENAI_API_KEY && config.TRANSLATION_MODEL ? '선택한 범위는 외부 유료 API에서 번역합니다.' : 'OpenAI를 선택했습니다. API 키와 모델을 설정하세요.',
  };
  const runtime = localRuntime();
  return {
    provider: 'argos' as const, providerLabel: 'Argos Translate', local: true, apiKeyRequired: false,
    model: runtime?.model || 'argos-en_ko-1.1', identity: runtime?.identity || 'argos-en_ko:not-installed', promptVersion: ARGOS_PROMPT_VERSION,
    configured: Boolean(runtime), modelConfigured: Boolean(runtime),
    statusMessage: runtime ? '무료 로컬 번역이 준비되었습니다. 원문은 이 PC에서 처리합니다.' : '무료 번역 모델을 설치하세요: npm run setup:translation',
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
