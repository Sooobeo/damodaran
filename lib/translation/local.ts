import { execFile, spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { APP_ROOT } from '../config';
import { PipelineError } from '../sources';
import { localRuntime, translationRuntime } from './runtime';
import type { ProviderRequest, ProviderResult } from './index';

type Waiting = { resolve: (result: ProviderResult) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> };
let engine: LocalEngine | undefined;
let lifecycle: Promise<void> = Promise.resolve();
export const LOCAL_ENGINE_IDLE_MS = 60000;
const CLOSE_GRACE_MS = 2000;
const CLOSE_TIMEOUT_MS = 15000;
const stopped = () => new PipelineError('LOCAL_STOPPED', '로컬 번역 실행기가 종료되었습니다. 다시 요청하세요.');
const cancelled = () => new PipelineError('CANCELLED', '로컬 번역 요청을 취소했습니다.');

// Serialize acquisition and release, without serializing the actual translations.
// A failed release retains the old engine and refuses another model load.
function withLifecycle<T>(operation: () => Promise<T> | T): Promise<T> {
  const result = lifecycle.then(operation);
  lifecycle = result.then(() => undefined, () => undefined);
  return result;
}
export function assertLocalReady(message: Record<string, unknown>, runtime: Pick<NonNullable<ReturnType<typeof localRuntime>>, 'provider' | 'model' | 'modelHash' | 'runtimeVersion'> & {identity?:string}) {
  if (message.ready !== true || message.model !== runtime.model || message.modelHash !== runtime.modelHash || message.runtimeVersion !== runtime.runtimeVersion
      || (message.provider != null && message.provider !== runtime.provider) || (runtime.provider === 'finetuned' && message.provider !== 'finetuned')
      || (runtime.provider === 'hymt' && (message.provider !== 'hymt' || !runtime.identity || message.identity !== runtime.identity))) {
    throw new PipelineError('PROVIDER_CHANGED', '실행 중 번역 모델이 변경되었습니다. 다시 요청하세요.');
  }
}
export class LocalEngine {
  child: ChildProcessWithoutNullStreams;
  identity: string;
  ready: Promise<void>;
  pending = new Map<string, Waiting>();
  private buffer = '';
  private ended = false;
  private readyReceived = false;
  private active = 0;
  private idleTimer?: ReturnType<typeof setTimeout>;
  private closePromise?: Promise<void>;
  private childExited = false;
  private finishClose = () => {};
  get closing() { return this.ended; }
  constructor(private readonly runtime: NonNullable<ReturnType<typeof localRuntime>>) {
    this.identity = runtime.identity;
    this.child = spawn(runtime.pythonPath, ['-u', runtime.bridgePath], {
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
      if (this.ended) return;
      this.buffer += chunk;
      if (this.buffer.length > 4000000) return this.fail(new PipelineError('LOCAL_RESPONSE_LIMIT', '로컬 번역 응답이 허용 크기를 넘었습니다.'));
      let end: number;
      while ((end = this.buffer.indexOf('\n')) >= 0) {
        const line = this.buffer.slice(0, end).trim(); this.buffer = this.buffer.slice(end + 1); if (!line) continue;
        let message: Record<string, any>;
        try { message = JSON.parse(line); if (!message || typeof message !== 'object' || Array.isArray(message)) throw new Error('Invalid message'); } catch { this.fail(new PipelineError('LOCAL_PROTOCOL', '로컬 번역 응답 형식이 올바르지 않습니다.')); return; }
        if (message.ready === true) {
          try { assertLocalReady(message, runtime); } catch (error) { this.fail(error as Error); return; }
          this.readyReceived = true; readyResolve(); this.scheduleIdle(); continue;
        }
        if (message.error && !message.id) { this.fail(new PipelineError('LOCAL_SETUP_ERROR', runtime.provider === 'hymt' ? 'Hy-MT2를 준비할 수 없습니다. 등록 상태와 무결성을 확인해 주세요.' : runtime.provider === 'finetuned' ? '학습 모델을 열 수 없습니다. 등록 상태를 확인해 주세요.' : '로컬 번역 모델을 열 수 없습니다. npm run setup:translation을 다시 실행하세요.')); return; }
        const waiting = this.pending.get(message.id); if (!waiting) continue;
        this.pending.delete(message.id); clearTimeout(waiting.timer);
        if (message.error) waiting.reject(new PipelineError('LOCAL_TRANSLATION_ERROR', '로컬 번역을 완료하지 못했습니다. 더 짧은 문단으로 다시 시도하세요.'));
        else waiting.resolve({ data: message.data, inputTokens: null, outputTokens: null, requestId: null });
      }
    });
    // Third-party diagnostics can contain text; consume without logging it.
    this.child.stderr.resume();
    this.child.stdin.on('error', () => this.fail(new PipelineError('LOCAL_STOPPED', '로컬 번역 실행기와 연결이 끊어졌습니다.')));
    this.child.on('error', () => {
      // A failed spawn has no process whose exit event could be awaited.
      if (!this.child.pid) this.childExited = true;
      this.fail(new PipelineError('LOCAL_START_ERROR', '로컬 번역 실행기를 시작할 수 없습니다. 설치 상태를 확인하세요.'));
      this.finishClose();
    });
    this.child.on('exit', () => {
      this.childExited = true;
      this.fail(stopped());
      this.finishClose();
    });
  }
  private failCallbacks: (error: Error) => void;
  private fail(error: Error) {
    if (!this.ended) void this.beginClose(error, true);
  }
  private scheduleIdle() {
    clearTimeout(this.idleTimer);
    if (this.ended || !this.readyReceived || this.active !== 0) return;
    this.idleTimer = setTimeout(() => {
      if (this.active === 0 && !this.ended) void this.close();
    }, LOCAL_ENGINE_IDLE_MS);
    this.idleTimer.unref();
  }
  async translate(input: ProviderRequest, signal?: AbortSignal) {
    if (signal?.aborted) throw cancelled();
    if (this.ended) throw stopped();
    if (input.model !== this.identity) throw new PipelineError('PROVIDER_CHANGED', '번역 모델이 변경되었습니다. 현재 설정으로 다시 요청하세요.');
    // Count before ready: a request waiting for model startup is already active.
    this.active++; clearTimeout(this.idleTimer);
    const abort = () => this.fail(cancelled());
    signal?.addEventListener('abort', abort, {once: true});
    try {
      await this.ready;
      if (this.ended) throw stopped();
      const id = randomUUID();
      return await new Promise<ProviderResult>((resolve, reject) => {
        // The 7B CPU engine can need more than five minutes for a long paragraph.
        // Match its bounded HTTP deadline; the worker keeps its lease heartbeat.
        const requestTimeout = this.runtime.provider === 'hymt' ? 1800000 : 300000;
        const timer = setTimeout(() => this.fail(new PipelineError('LOCAL_TIMEOUT', '로컬 번역 시간이 초과되었습니다. 더 짧은 범위를 선택하세요.')), requestTimeout);
        this.pending.set(id, { resolve, reject, timer });
        const request = {id,segments:input.segments,glossary:input.glossary,...(this.runtime.provider === 'hymt' ? {modelIdentity:this.identity,context:input.context} : {})};
        try {
          this.child.stdin.write(JSON.stringify(request) + '\n', error => {
            if (error) this.fail(new PipelineError('LOCAL_STOPPED', '로컬 번역 실행기와 연결이 끊어졌습니다.'));
          });
        } catch { this.fail(stopped()); }
      });
    } finally {
      signal?.removeEventListener('abort', abort);
      this.active--; this.scheduleIdle();
    }
  }
  private beginClose(error: Error, immediate = false): Promise<void> {
    if (this.closePromise) return this.closePromise;
    this.ended = true; clearTimeout(this.idleTimer); this.buffer = '';
    this.failCallbacks(error);
    let resolveClose!: () => void, rejectClose!: (error: Error) => void;
    this.closePromise = new Promise((resolve, reject) => { resolveClose = resolve; rejectClose = reject; });
    // Existing callers may release without awaiting. Keep failures observable on
    // this same promise without creating unhandled rejections for those callers.
    void this.closePromise.catch(() => {});
    let graceTimer: ReturnType<typeof setTimeout> | undefined;
    let forceStarted = false, forceSettled = false, finished = false;
    let forceError: Error | undefined;
    const cleanup = () => { clearTimeout(graceTimer); clearTimeout(deadline); this.finishClose = () => {}; };
    const deadline = setTimeout(() => {
      if (finished) return;
      finished = true; cleanup();
      rejectClose(new PipelineError('LOCAL_CLOSE_TIMEOUT', '로컬 번역 프로세스 종료를 확인하지 못했습니다. 작업 처리기를 확인해 주세요.'));
    }, CLOSE_TIMEOUT_MS);
    this.finishClose = () => {
      if (finished || !this.childExited || (forceStarted && !forceSettled)) return;
      finished = true; cleanup();
      if (forceError) rejectClose(forceError); else resolveClose();
    };
    const force = () => {
      if (finished || forceStarted) return;
      if (this.childExited) { this.finishClose(); return; }
      forceStarted = true;
      if (process.platform === 'win32' && this.child.pid) {
        // This PID belongs to our spawn only. /T also covers a Windows venv
        // redirector. The registered Hy-MT bridge owns its native server via a
        // kill-on-close Job; normal bridge exit already awaits the native child.
        execFile('taskkill', ['/PID', String(this.child.pid), '/T', '/F'], {
          windowsHide: true, timeout: 10000, killSignal: 'SIGKILL',
        }, error => {
          forceSettled = true;
          if (error && !this.childExited) forceError = new PipelineError('LOCAL_CLOSE_FAILED', '로컬 번역 프로세스를 종료하지 못했습니다.');
          this.finishClose();
        });
      } else {
        forceSettled = true;
        try { this.child.kill('SIGKILL'); } catch { forceError = new PipelineError('LOCAL_CLOSE_FAILED', '로컬 번역 프로세스를 종료하지 못했습니다.'); }
        this.finishClose();
      }
    };
    if (this.childExited) this.finishClose();
    else if (immediate) force();
    else {
      graceTimer = setTimeout(force, CLOSE_GRACE_MS);
      try { this.child.stdin.end(); } catch { force(); }
    }
    return this.closePromise;
  }
  close(): Promise<void> { return this.beginClose(stopped()); }
}
export async function translateLocally(input: ProviderRequest, signal?: AbortSignal): Promise<ProviderResult> {
  const acquired = await withLifecycle(async () => {
    if (signal?.aborted) throw cancelled();
    let runtime = localRuntime();
    if (!runtime) throw new PipelineError('SETUP_REQUIRED', translationRuntime().statusMessage);
    if (runtime.identity !== input.model) throw new PipelineError('PROVIDER_CHANGED', '번역 모델이 변경되었습니다. 현재 설정으로 다시 요청하세요.');
    if (engine && (engine.closing || engine.identity !== runtime.identity)) {
      await engine.close(); engine = undefined;
      if (signal?.aborted) throw cancelled();
      runtime = localRuntime();
      if (!runtime || runtime.identity !== input.model) throw new PipelineError('PROVIDER_CHANGED', '번역 모델이 변경되었습니다. 현재 설정으로 다시 요청하세요.');
    }
    engine ||= new LocalEngine(runtime);
    const translation = engine.translate(input, signal);
    void translation.catch(() => {});
    return {translation};
  });
  return acquired.translation;
}
export function closeLocalTranslator(): Promise<void> {
  return withLifecycle(async () => {
    if (engine) { await engine.close(); engine = undefined; }
  });
}
