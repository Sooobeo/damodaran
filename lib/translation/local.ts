import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import { APP_ROOT } from '../config';
import { PipelineError } from '../sources';
import { localRuntime } from './runtime';
import type { ProviderRequest, ProviderResult } from './index';

type Waiting = { resolve: (result: ProviderResult) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> };
let engine: LocalEngine | undefined;
class LocalEngine {
  child: ChildProcessWithoutNullStreams;
  identity: string;
  ready: Promise<void>;
  pending = new Map<string, Waiting>();
  private buffer = '';
  private ended = false;
  constructor(runtime: NonNullable<ReturnType<typeof localRuntime>>) {
    this.identity = runtime.identity;
    this.child = spawn(runtime.pythonPath, ['-u', path.join(APP_ROOT, 'scripts/local-translation/bridge.py')], {
      cwd: APP_ROOT, windowsHide: true, env: { ...process.env, OPENAI_API_KEY: '', PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', APP_ROOT },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let readyResolve: () => void, readyReject: (error: Error) => void;
    this.ready = new Promise((resolve, reject) => { readyResolve = resolve; readyReject = reject; });
    const startupTimer = setTimeout(() => this.fail(new PipelineError('LOCAL_START_TIMEOUT', '로컬 번역 모델을 여는 시간이 초과되었습니다. 설치 상태를 확인하세요.')), 300000);
    this.ready.then(() => clearTimeout(startupTimer), () => clearTimeout(startupTimer));
    const onFailure = (error: Error) => { readyReject(error); for (const waiting of this.pending.values()) { clearTimeout(waiting.timer); waiting.reject(error); } this.pending.clear(); };
    this.failCallbacks = onFailure;
    this.child.stdout.setEncoding('utf8');
    this.child.stdout.on('data', (chunk: string) => {
      this.buffer += chunk;
      if (this.buffer.length > 4000000) return this.fail(new PipelineError('LOCAL_RESPONSE_LIMIT', '로컬 번역 응답이 허용 크기를 넘었습니다.'));
      let end: number;
      while ((end = this.buffer.indexOf('\n')) >= 0) {
        const line = this.buffer.slice(0, end).trim(); this.buffer = this.buffer.slice(end + 1); if (!line) continue;
        let message: Record<string, any>;
        try { message = JSON.parse(line); } catch { this.fail(new PipelineError('LOCAL_PROTOCOL', '로컬 번역 응답 형식이 올바르지 않습니다.')); return; }
        if (message.ready === true) {
          if (message.model !== runtime.model || message.modelHash !== runtime.modelHash || message.runtimeVersion !== runtime.runtimeVersion) {
            this.fail(new PipelineError('PROVIDER_CHANGED', '실행 중 번역 모델이 변경되었습니다. 다시 요청하세요.')); return;
          }
          readyResolve(); continue;
        }
        if (message.error && !message.id) { this.fail(new PipelineError('LOCAL_SETUP_ERROR', '로컬 번역 모델을 열 수 없습니다. npm run setup:translation을 다시 실행하세요.')); return; }
        const waiting = this.pending.get(message.id); if (!waiting) continue;
        this.pending.delete(message.id); clearTimeout(waiting.timer);
        if (message.error) waiting.reject(new PipelineError('LOCAL_TRANSLATION_ERROR', '로컬 번역을 완료하지 못했습니다. 더 짧은 문단으로 다시 시도하세요.'));
        else waiting.resolve({ data: message.data, inputTokens: null, outputTokens: null, requestId: null });
      }
    });
    // Third-party diagnostics can contain text; consume without logging it.
    this.child.stderr.resume();
    this.child.stdin.on('error', () => this.fail(new PipelineError('LOCAL_STOPPED', '로컬 번역 실행기와 연결이 끊어졌습니다.')));
    this.child.on('error', () => this.fail(new PipelineError('LOCAL_START_ERROR', '로컬 번역 실행기를 시작할 수 없습니다. 설치 상태를 확인하세요.')));
    this.child.on('exit', () => this.fail(new PipelineError('LOCAL_STOPPED', '로컬 번역 실행기가 종료되었습니다. 다시 요청하면 재시작합니다.')));
  }
  private failCallbacks: (error: Error) => void;
  private fail(error: Error) {
    if (this.ended) return; this.ended = true; this.failCallbacks(error);
    this.child.kill(); if (engine === this) engine = undefined;
  }
  async translate(input: ProviderRequest) {
    await this.ready;
    const id = randomUUID();
    return new Promise<ProviderResult>((resolve, reject) => {
      const timer = setTimeout(() => this.fail(new PipelineError('LOCAL_TIMEOUT', '로컬 번역 시간이 초과되었습니다. 더 짧은 범위를 선택하세요.')), 300000);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(JSON.stringify({ id, segments: input.segments, glossary: input.glossary }) + '\n', error => {
        if (error) this.fail(new PipelineError('LOCAL_STOPPED', '로컬 번역 실행기와 연결이 끊어졌습니다.'));
      });
    });
  }
  close() { this.child.stdin.end(); const child = this.child; setTimeout(() => { if (child.exitCode === null) child.kill(); }, 2000).unref(); }
}
export async function translateLocally(input: ProviderRequest): Promise<ProviderResult> {
  const runtime = localRuntime();
  if (!runtime) throw new PipelineError('SETUP_REQUIRED', '무료 번역 모델을 설치하세요: npm run setup:translation');
  if (runtime.identity !== input.model) throw new PipelineError('PROVIDER_CHANGED', '번역 모델이 변경되었습니다. 현재 설정으로 다시 요청하세요.');
  if (engine && engine.identity !== runtime.identity) { engine.close(); engine = undefined; }
  engine ||= new LocalEngine(runtime);
  return engine.translate(input);
}
export function closeLocalTranslator() { engine?.close(); engine = undefined; }
