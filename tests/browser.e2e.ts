/** Functional browser verification. All mutations use an isolated restored DATA_DIR. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import Database from 'better-sqlite3';
import { chromium, expect, type Browser, type Page } from '@playwright/test';
import { backup, restore } from '../lib/backup';
import { closeDb } from '../lib/db';
import { APP_ROOT } from '../lib/config';
import type { Bootstrap, ResourceDetail, BlocksResult, Job, Translation } from '../lib/client-types';

const base = 'http://127.0.0.1:3011';
const resultsDirectory = path.join(APP_ROOT, 'test-results'); fs.mkdirSync(resultsDirectory, { recursive: true });
const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'damodaran-browser-e2e-'));
const isolatedData = path.join(tempRoot, 'restored');
const checks: { name: string; status: string; durationMs: number; detail?: string }[] = [];
const consoleErrors: string[] = []; const pageErrors: string[] = [];
let browser: Browser | undefined; let page: Page | undefined; let server: ChildProcess | undefined;
let serverOutput = ''; let reportError: string | null = null;
const localTranslationEvidence: { format: 'html' | 'pdf'; resourceId: string; sourceVersionId: string; blockIds: string[]; provider: 'argos'; blocks: number; sourceChars: number; cached: number; semanticReview: string }[] = [];
const translationFailures: { blockId: string; source: string; textKo: string; validationStatus: string; provider: string; usageJson: string }[] = [];
const reviewEvidence: { correctedTranslationId: string; reviewId: string; reusedTranslationId?: string; originalMachineTextPreserved: boolean; extraTranslationUsage: number; exportedPairVerified?: boolean }[] = [];
const hash = (bytes: Uint8Array) => createHash('sha256').update(bytes).digest('hex');
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

async function request<T>(route: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(base + route, { ...options, headers: { Origin: base, ...options.headers }, signal: AbortSignal.timeout(15000) });
  const result = await response.json();
  assert.ok(response.ok, `${route}: ${response.status} ${JSON.stringify(result)}`);
  return result as T;
}
async function poll<T>(read: () => Promise<T>, valid: (value: T) => boolean, timeout = 20000): Promise<T> {
  const started = Date.now(); let latest: T;
  do { latest = await read(); if (valid(latest)) return latest; await delay(250); } while (Date.now() - started < timeout);
  throw new Error(`상태 대기 시간 ${timeout}ms 초과`);
}
async function step(name: string, work: () => Promise<void>) {
  const started = Date.now(); console.log(`확인: ${name}`);
  try { await work(); checks.push({ name, status: 'passed', durationMs: Date.now() - started }); }
  catch (error) { checks.push({ name, status: 'failed', durationMs: Date.now() - started, detail: error instanceof Error ? error.message : String(error) }); throw error; }
}
async function navigate(route: string) {
  assert.ok(page); await page.goto(base + route, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await expect(page.locator('.loading-state')).toHaveCount(0, { timeout: 20000 });
}
async function startServer() {
  assert.ok(fs.existsSync(path.join(APP_ROOT, '.next', 'BUILD_ID')), '먼저 npm run build를 완료해야 합니다.');
  serverOutput = '';
  server = spawn(process.execPath, ['--import', 'tsx', path.join(APP_ROOT, 'scripts/run.ts'), 'start'], {
    cwd: APP_ROOT, windowsHide: true,
    env: { ...process.env, APP_PORT: '3011', DATA_DIR: isolatedData, TRANSLATION_PROVIDER: 'argos', OPENAI_API_KEY: '', TRANSLATION_MODEL: '', WEB_ONLY: '0', NODE_ENV: 'production' },
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
  });
  server.stdout?.on('data', chunk => { serverOutput += chunk.toString(); }); server.stderr?.on('data', chunk => { serverOutput += chunk.toString(); });
  const started = Date.now();
  while (Date.now() - started < 45000) {
    if (server.exitCode !== null) throw new Error(`격리 서버 시작 실패: ${serverOutput}`);
    try { await request('/api/bootstrap'); return; } catch { await delay(350); }
  }
  throw new Error(`격리 서버 준비 시간 초과: ${serverOutput}`);
}
async function stopServer() {
  if (!server || server.exitCode !== null || server.signalCode !== null) return;
  const target = server;
  const stopped = new Promise<void>(resolve => target.once('exit', () => resolve()));
  if (target.connected) target.send({ type: 'shutdown' });
  const timeout = setTimeout(() => {
    if (target.exitCode !== null || target.signalCode !== null || !target.pid) return;
    // PID belongs exclusively to this test's own child runner.
    if (process.platform === 'win32') spawn('taskkill', ['/PID', String(target.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    else target.kill('SIGTERM');
  }, 6000);
  await stopped; clearTimeout(timeout); server = undefined;
}

function testPdf() {
  const streams = ['BT /F1 22 Tf 55 730 Td (TEST FIXTURE - Browser E2E) Tj 0 -45 Td /F1 14 Tf (Revenue 100, costs 60, margin 40%.) Tj ET', 'BT /F1 22 Tf 55 730 Td (TEST FIXTURE - Second page) Tj 0 -45 Td /F1 14 Tf (Present value uses a discount rate.) Tj ET'];
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>', '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>', `<< /Length ${Buffer.byteLength(streams[0])} >>\nstream\n${streams[0]}\nendstream`, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>', `<< /Length ${Buffer.byteLength(streams[1])} >>\nstream\n${streams[1]}\nendstream`];
  let content = '%PDF-1.4\n'; const offsets = [0];
  objects.forEach((object, i) => { offsets.push(Buffer.byteLength(content)); content += `${i + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = Buffer.byteLength(content); content += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.slice(1).map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(content);
}

type TranslationScope = { sourceVersionId: string; blockIds?: string[]; pageRange?: [number, number] };
type TranslationRequestResult = { jobIds: string[]; cached: number; targets: { blockId: string; translationId?: string }[] };
async function waitForTranslation(result: TranslationRequestResult) {
  for (const jobId of result.jobIds) {
    const job = await poll(() => request<Job>(`/api/jobs/${jobId}`), job => !['queued', 'running'].includes(job.status), 420000);
    if (job.status !== 'completed') {
      const snapshot = new Database(path.join(isolatedData, 'library.sqlite'), { readonly: true, fileMustExist: true });
      try {
        // Only capture our generated PDF fixture, never notes or imported source text.
        const failed = snapshot.prepare("SELECT b.id AS blockId,b.text AS source,t.text_ko AS textKo,t.validation_status AS validationStatus,t.provider,t.usage_json AS usageJson FROM translations t JOIN source_blocks b ON b.id=t.block_id JOIN job_items i ON i.block_id=b.id JOIN source_versions v ON v.id=b.source_version_id JOIN resources r ON r.id=v.resource_id WHERE i.job_id=? AND r.source_type='upload' AND r.title_en LIKE 'TEST FIXTURE%' ORDER BY b.sort_order").all(jobId) as typeof translationFailures;
        translationFailures.push(...failed);
      } finally { snapshot.close(); }
    }
    assert.equal(job.status, 'completed', `번역 자동 무결성 검사 실패: ${job.errorMessage || job.status}`);
    assert.equal(job.failed, 0); assert.equal(job.needsReview, 0);
  }
}
async function verifyCache(scope: TranslationScope, expectedBlocks: number) {
  const before = (await request<Bootstrap>('/api/bootstrap')).usage;
  const result = await request<TranslationRequestResult>('/api/translations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(scope) });
  assert.equal(result.cached, expectedBlocks); assert.deepEqual(result.jobIds, []);
  const after = (await request<Bootstrap>('/api/bootstrap')).usage;
  assert.deepEqual(after, before, '캐시 요청은 추가 로컬 처리량 또는 원격 호출을 만들지 않아야 합니다.');
  return result;
}
function assertStoredLocalTranslations(blockIds: string[]) {
  const snapshot = new Database(path.join(isolatedData, 'library.sqlite'), { readonly: true, fileMustExist: true });
  try {
    for (const blockId of blockIds) {
      const row = snapshot.prepare('SELECT provider,review_status FROM translations WHERE block_id=? ORDER BY created_at DESC LIMIT 1').get(blockId) as { provider: string; review_status: string } | undefined;
      assert.ok(row); assert.equal(row.provider, 'argos'); assert.equal(row.review_status, 'unreviewed');
    }
  } finally { snapshot.close(); }
}

try {
  await step('일관된 DB 백업과 별도 DATA_DIR 복원', async () => {
    const snapshot = await backup(); closeDb(); const copy = restore(snapshot.path, isolatedData);
    assert.equal(copy.workerStarted, false); assert.notEqual(path.resolve(isolatedData), path.resolve(APP_ROOT, 'data'));
    execFileSync(process.execPath, ['--import', 'tsx', '--input-type=module', '-e', "import {setupDatabase,closeDb} from './lib/db/index.ts'; setupDatabase(); closeDb();"], {
      cwd: APP_ROOT, windowsHide: true, encoding: 'utf8', env: { ...process.env, APP_ROOT, DATA_DIR: isolatedData, TRANSLATION_PROVIDER: 'argos', OPENAI_API_KEY: '', TRANSLATION_MODEL: '', WEB_ONLY: '1' },
    });
  });
  await startServer();
  browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'] });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(15000);
  page.on('pageerror', error => pageErrors.push(error.message)); page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  const initial = await request<Bootstrap>('/api/bootstrap');
  const verifyLocalTranslation = initial.translationStatus.provider === 'argos' && initial.translationStatus.configured;
  if (process.env.VERIFY_LOCAL_TRANSLATION === '1') assert.ok(verifyLocalTranslation, '실제 로컬 번역 검증을 요청했지만 Argos 모델이 준비되지 않았습니다.');
  const first = initial.resources.find(r => r.id === 'R01'); assert.ok(first?.versionId, '실제 가져온 R01 원문이 있어야 HTML 읽기를 검증할 수 있습니다.');
  const blocks = await request<BlocksResult>(`/api/resources/R01/blocks?versionId=${first.versionId}`);
  const paragraph = blocks.blocks.find(b => b.type === 'paragraph' && b.text.length > 70); assert.ok(paragraph, '실제 HTML 문단이 있어야 합니다.');
  const stamp = `TEST FIXTURE E2E ${Date.now()}`; const editedText = `${stamp} — 수정된 개인 메모`;
  let noteId = ''; let bookmarkId = ''; let uploadedId = ''; let pdfVersionId = '';
  let translatedHtmlBlockId = ''; let translatedHtmlText = ''; let translatedPdfText = '';
  let reviewedPdfBlockId = ''; let reviewedPdfText = ''; let reviewId = ''; let memoryResourceId = ''; let memoryVersionId = ''; let memoryBlockId = '';
  const previousProgress = initial.progress.find(p => p.moduleId === 'M01')?.status || 'not_started';

  await step('한국어 자료 검색과 URL 필터', async () => {
    await navigate('/library'); await page!.getByRole('textbox', { name: '자료 제목과 내용 검색' }).fill('현재가치 입문');
    await page!.getByRole('button', { name: '검색', exact: true }).click(); await expect(page!.locator('.resource-card')).toHaveCount(1);
    await expect(page!.locator('.card-title')).toHaveText('현재가치 입문'); assert.ok(new URL(page!.url()).searchParams.get('q') === '현재가치 입문');
    await navigate('/library'); await page!.getByLabel('파일 형식', { exact: true }).selectOption('xls');
    await expect(page!.locator('.resource-card')).toHaveCount(9); assert.ok(new URL(page!.url()).searchParams.get('format') === 'xls');
  });
  await step('영문·약어 용어 검색과 정의 표시', async () => {
    await navigate('/glossary'); await page!.getByLabel('금융 용어 검색', { exact: true }).fill('WACC'); await page!.getByRole('button', { name: '검색', exact: true }).click();
    await expect(page!.locator('.glossary-list>a')).toHaveCount(1); await expect(page!.locator('.term-article')).toContainText('가중평균');
  });
  await step('자료 북마크 저장', async () => {
    await navigate('/library?q=현재가치 입문');
    const originalBookmark = initial.bookmarks.find(b => b.resourceId === 'R02' && !b.sourceVersionId);
    if (originalBookmark) await request(`/api/bookmarks/${originalBookmark.id}`, { method: 'DELETE' });
    await page!.reload({ waitUntil: 'domcontentloaded' });
    await page!.getByRole('button', { name: '현재가치 입문 북마크', exact: true }).click();
    const saved = await poll(() => request<Bootstrap>('/api/bootstrap'), d => d.bookmarks.some(b => b.resourceId === 'R02' && !b.sourceVersionId));
    bookmarkId = saved.bookmarks.find(b => b.resourceId === 'R02' && !b.sourceVersionId)!.id;
    await navigate('/notes?tab=bookmarks'); await expect(page!.locator('.record-rows')).toContainText('현재가치 입문');
  });
  await step('단원 학습 완료 저장', async () => {
    await navigate('/learn/financial-statements'); await page!.getByLabel('현재 상태', { exact: true }).selectOption('completed');
    await poll(() => request<Bootstrap>('/api/bootstrap'), d => d.progress.some(p => p.moduleId === 'M01' && p.status === 'completed'));
    await expect(page!.getByLabel('현재 상태', { exact: true })).toHaveValue('completed');
  });
  await step('HTML 원문 문단 메모 저장과 기록에서 수정', async () => {
    await navigate(`/reader/R01?versionId=${first.versionId}&blockId=${paragraph.id}&mode=en`);
    await expect(page!.locator(`[id="block-${paragraph.id}"] .original-block`)).toContainText(paragraph.text.slice(0, 50));
    await page!.locator(`[id="block-${paragraph.id}"] .original-block`).click(); await page!.locator('#reader-note').fill(stamp);
    await page!.getByRole('button', { name: '메모 저장', exact: true }).click();
    const saved = await poll(() => request<Bootstrap>('/api/bootstrap'), d => d.notes.some(n => n.text === stamp));
    const note = saved.notes.find(n => n.text === stamp)!; noteId = note.id; assert.equal(note.sourceVersionId, first.versionId); assert.equal(note.blockId, paragraph.id);
    await navigate('/notes'); const card = page!.locator('.note-card').filter({ hasText: stamp }); await card.getByRole('button', { name: '수정', exact: true }).click();
    await card.locator('textarea').fill(editedText); await card.getByRole('button', { name: '수정 저장', exact: true }).click();
    await poll(() => request<Bootstrap>('/api/bootstrap'), d => d.notes.some(n => n.id === noteId && n.text === editedText));
  });
  await step('읽기 모드·버전·문단이 새로고침 후 유지', async () => {
    await navigate(`/reader/R01?versionId=${first.versionId}&blockId=${paragraph.id}&mode=en`);
    await page!.getByRole('button', { name: '한국어', exact: true }).click();
    await expect(page!.getByRole('button', { name: '한국어', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await poll(() => request<Bootstrap>('/api/bootstrap'), d => d.positions.some(p => p.resourceId === 'R01' && p.sourceVersionId === first.versionId && p.languageMode === 'ko' && p.blockId === paragraph.id));
    await expect.poll(() => new URL(page!.url()).searchParams.get('mode')).toBe('ko');
    assert.equal(new URL(page!.url()).searchParams.get('versionId'), first.versionId); assert.equal(new URL(page!.url()).searchParams.get('blockId'), paragraph.id);
    await page!.reload({ waitUntil: 'domcontentloaded' }); await expect(page!.getByRole('button', { name: '한국어', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page!.locator(`[id="block-${paragraph.id}"]`)).toBeVisible();
    await page!.screenshot({ path: path.join(resultsDirectory, 'e2e-html-reader.png') });
  });
  if (verifyLocalTranslation) await step('실제 Argos: HTML 선택 문단 번역·저장·유효 캐시 재사용', async () => {
    const target = blocks.blocks.find(b => b.type === 'paragraph' && b.text.length > 70 && b.text.length < 2000 && !b.translation?.current);
    assert.ok(target, '새로 번역할 실제 R01 문단이 필요합니다.'); translatedHtmlBlockId = target.id;
    await navigate(`/reader/R01?versionId=${first.versionId}&blockId=${target.id}&mode=parallel`);
    await expect(page!.locator('.translation-setup-note')).toContainText('무료 번역');
    const block = page!.locator(`[id="block-${target.id}"]`);
    await block.getByRole('checkbox').check();
    const responsePromise = page!.waitForResponse(response => response.url() === `${base}/api/translations` && response.request().method() === 'POST');
    await page!.getByRole('button', { name: '한국어로 번역', exact: true }).click();
    const response = await responsePromise; assert.equal(response.status(), 202);
    const result = await response.json() as TranslationRequestResult; assert.ok(result.jobIds.length > 0, '이미 있던 결과가 아닌 실제 실행을 확인합니다.');
    await waitForTranslation(result);
    const translated = await request<BlocksResult>(`/api/resources/R01/blocks?versionId=${first.versionId}&anchorBlockId=${target.id}`);
    const translation = translated.blocks.find(b => b.id === target.id)?.translation;
    assert.ok(translation?.current); assert.equal(translation.validationStatus, 'passed'); assert.match(translation.textKo, /[가-힣]/); translatedHtmlText = translation.textKo;
    await expect(block.locator('.translated-block')).toContainText(translatedHtmlText.slice(0, 50), { timeout: 20000 });
    await expect(block.locator('.translation-badges')).toContainText('기계 번역 · 사용자 미검수');
    assertStoredLocalTranslations([target.id]);
    const cached = await verifyCache({ sourceVersionId: first.versionId!, blockIds: [target.id] }, 1);
    localTranslationEvidence.push({ format: 'html', resourceId: 'R01', sourceVersionId: first.versionId!, blockIds: [target.id], provider: 'argos', blocks: 1, sourceChars: target.text.length, cached: cached.cached, semanticReview: '자동 무결성·동작 검증이며 전문가 의미 검수가 아닙니다.' });
    await block.scrollIntoViewIfNeeded(); await page!.screenshot({ path: path.join(resultsDirectory, 'e2e-argos-html-translation.png') });
  });
  else checks.push({ name: '실제 Argos HTML·PDF 번역 검증', status: 'skipped', durationMs: 0, detail: '무료 모델 미설치: npm run setup:translation 후 다시 실행하세요.' });
  const pdf = testPdf();
  await step('격리된 테스트 PDF 업로드와 실제 worker 추출', async () => {
    await navigate('/library'); await page!.locator('input[type=file]').setInputFiles({ name: 'TEST FIXTURE browser-e2e.pdf', mimeType: 'application/pdf', buffer: pdf });
    await page!.waitForURL(/\/resources\//, { timeout: 20000 }); uploadedId = new URL(page!.url()).pathname.split('/').at(-1)!;
    const ready = await poll(() => request<ResourceDetail>(`/api/resources/${uploadedId}`), d => !!d.resource.versionId, 30000);
    pdfVersionId = ready.resource.versionId!; assert.equal(ready.versions.find(v => v.id === pdfVersionId)?.pageCount, 2);
    await expect(page!.getByRole('link', { name: '공부방에서 읽기', exact: true })).toBeVisible({ timeout: 20000 });
  });
  await step('PDF canvas·TextLayer·페이지 전환·확대', async () => {
    await navigate(`/reader/${uploadedId}?versionId=${pdfVersionId}&page=1&mode=en`);
    await expect(page!.locator('.reader-heading')).toContainText('원저자 미확인');
    await expect(page!.locator('.reader-heading')).not.toContainText('ASWATH DAMODARAN');
    await expect(page!.locator('.pdf-rendered-page canvas')).toBeVisible({ timeout: 30000 });
    await expect(page!.locator('.textLayer')).toContainText('TEST FIXTURE - Browser E2E', { timeout: 20000 });
    await page!.getByRole('button', { name: '다음 PDF 페이지', exact: true }).click();
    await expect(page!.locator('.textLayer')).toContainText('TEST FIXTURE - Second page', { timeout: 20000 });
    assert.equal(new URL(page!.url()).searchParams.get('page'), '2');
    const widthBefore = await page!.locator('.pdf-rendered-page canvas').evaluate(canvas => canvas.getBoundingClientRect().width);
    await page!.getByRole('button', { name: 'PDF 확대', exact: true }).click();
    await expect.poll(async () => page!.locator('.pdf-rendered-page canvas').evaluate(canvas => canvas.getBoundingClientRect().width)).toBeGreaterThan(widthBefore);
    await expect(page!.locator('.textLayer')).toContainText('TEST FIXTURE - Second page');
    await page!.screenshot({ path: path.join(resultsDirectory, 'e2e-pdf-reader.png') });
  });
  if (verifyLocalTranslation) await step('실제 Argos: 업로드 PDF 1페이지 번역·숫자 보존·유효 캐시', async () => {
    await navigate(`/reader/${uploadedId}?versionId=${pdfVersionId}&page=1&mode=parallel`);
    const original = await request<BlocksResult>(`/api/resources/${uploadedId}/blocks?versionId=${pdfVersionId}&page=1`);
    const targets = original.blocks.filter(b => b.type !== 'image' && b.text.trim()); assert.ok(targets.length > 0);
    const responsePromise = page!.waitForResponse(response => response.url() === `${base}/api/translations` && response.request().method() === 'POST');
    await page!.locator('.page-translate').getByRole('button', { name: '번역', exact: true }).click();
    const response = await responsePromise; assert.equal(response.status(), 202);
    const result = await response.json() as TranslationRequestResult; assert.ok(result.jobIds.length > 0); await waitForTranslation(result);
    const translated = await request<BlocksResult>(`/api/resources/${uploadedId}/blocks?versionId=${pdfVersionId}&page=1`);
    for (const block of translated.blocks.filter(b => b.type !== 'image' && b.text.trim())) { assert.ok(block.translation?.current); assert.equal(block.translation.validationStatus, 'passed'); assert.equal(block.translation.reviewStatus, 'unreviewed'); }
    translatedPdfText = translated.blocks.map(b => b.translation?.textKo || '').join('\n'); assert.match(translatedPdfText, /[가-힣]/);
    for (const token of ['100', '60', '40%']) assert.ok(translatedPdfText.includes(token), `숫자·단위 보존: ${token}`);
    await expect(page!.locator('.translation-badges').first()).toContainText('기계 번역 · 사용자 미검수', { timeout: 20000 });
    assertStoredLocalTranslations(targets.map(b => b.id));
    const cached = await verifyCache({ sourceVersionId: pdfVersionId, pageRange: [1, 1] }, targets.length);
    localTranslationEvidence.push({ format: 'pdf', resourceId: uploadedId, sourceVersionId: pdfVersionId, blockIds: targets.map(b => b.id), provider: 'argos', blocks: targets.length, sourceChars: targets.reduce((sum, b) => sum + b.text.length, 0), cached: cached.cached, semanticReview: '직접 작성한 TEST FIXTURE PDF이며 Damodaran PDF 수집·의미 검수를 대신하지 않습니다.' });
    const usage = (await request<Bootstrap>('/api/bootstrap')).usage;
    assert.equal(usage.remoteSourceChars, initial.usage.remoteSourceChars, '원격 유료 호출 추가 없음');
    assert.equal(usage.inputTokens, initial.usage.inputTokens); assert.equal(usage.outputTokens, initial.usage.outputTokens); assert.equal(usage.unknownCount, initial.usage.unknownCount);
    assert.ok(usage.localSourceChars > initial.usage.localSourceChars);
    await page!.locator('.document-column-labels').scrollIntoViewIfNeeded(); await page!.screenshot({ path: path.join(resultsDirectory, 'e2e-argos-pdf-translation.png') });
  });
  if (verifyLocalTranslation) await step('PDF 문단 검수: 숫자 오류 거부·수정 저장·기계 번역 보존·추가 처리 없음', async () => {
    const original = await request<BlocksResult>(`/api/resources/${uploadedId}/blocks?versionId=${pdfVersionId}&page=1`);
    const target = original.blocks.find(b => b.text.includes('Revenue 100')); assert.ok(target?.translation); reviewedPdfBlockId = target.id;
    const machineTranslation = { ...target.translation }; const usageBefore = (await request<Bootstrap>('/api/bootstrap')).usage;
    await navigate(`/reader/${uploadedId}?versionId=${pdfVersionId}&page=1&mode=ko`);
    const block = page!.locator(`[id="block-${target.id}"]`); await block.getByRole('button', { name: '번역 수정', exact: true }).click();
    const field = block.getByLabel(`${target.order + 1}번째 문단 번역 수정`, { exact: true }); await field.fill('매출액은 999, 비용은 60, 마진은 40%이다.');
    const rejectedResponse = page!.waitForResponse(response => response.url() === `${base}/api/translations/review` && response.request().method() === 'POST');
    await block.getByRole('button', { name: '검수 완료로 저장', exact: true }).click(); assert.equal((await rejectedResponse).ok(), false);
    await expect(block.getByRole('alert')).toContainText('원문과 다릅니다.'); await expect(field).toHaveValue('매출액은 999, 비용은 60, 마진은 40%이다.');
    reviewedPdfText = '매출액은 100, 비용은 60, 마진은 40%이다.'; await field.fill(reviewedPdfText);
    const savedResponse = page!.waitForResponse(response => response.url() === `${base}/api/translations/review` && response.request().method() === 'POST');
    await block.getByRole('button', { name: '검수 완료로 저장', exact: true }).click(); const response = await savedResponse; assert.equal(response.ok(), true);
    const saved = await response.json() as Translation; assert.equal(saved.reviewStatus, 'user_reviewed'); assert.equal(saved.origin, 'user'); assert.ok(saved.reviewId); reviewId = saved.reviewId;
    await expect(block.locator('.translation-badges')).toContainText('사용자 검수 완료'); await expect(field).toHaveCount(0); await expect(block.locator('.translated-block')).toContainText(reviewedPdfText);
    const snapshot = new Database(path.join(isolatedData, 'library.sqlite'), { readonly: true, fileMustExist: true });
    try { const row = snapshot.prepare('SELECT text_ko,provider FROM translations WHERE id=?').get(machineTranslation.id) as { text_ko: string; provider: string }; assert.equal(row.text_ko, machineTranslation.textKo); assert.equal(row.provider, 'argos'); }
    finally { snapshot.close(); }
    await verifyCache({ sourceVersionId: pdfVersionId, pageRange: [1, 1] }, original.blocks.filter(b => b.type !== 'image' && b.text.trim()).length);
    const fresh = await request<BlocksResult>(`/api/resources/${uploadedId}/blocks?versionId=${pdfVersionId}&page=1`); translatedPdfText = fresh.blocks.map(b => b.translation?.textKo || '').join('\n');
    const usageAfter = (await request<Bootstrap>('/api/bootstrap')).usage; assert.deepEqual(usageAfter, usageBefore);
    reviewEvidence.push({ correctedTranslationId: machineTranslation.id, reviewId, originalMachineTextPreserved: true, extraTranslationUsage: 0 });
    await block.scrollIntoViewIfNeeded(); await page!.screenshot({ path: path.join(resultsDirectory, 'e2e-reviewed-pdf-translation.png') });
  });
  if (verifyLocalTranslation) await step('같은 원문·문맥의 새 PDF에서 검수 번역 메모리 재사용', async () => {
    const before = (await request<Bootstrap>('/api/bootstrap')).usage;
    await navigate('/library'); await page!.locator('input[type=file]').setInputFiles({ name: 'TEST FIXTURE browser-e2e.pdf', mimeType: 'application/pdf', buffer: pdf });
    await page!.waitForURL(/\/resources\//, { timeout: 20000 }); memoryResourceId = new URL(page!.url()).pathname.split('/').at(-1)!; assert.notEqual(memoryResourceId, uploadedId);
    const ready = await poll(() => request<ResourceDetail>(`/api/resources/${memoryResourceId}`), d => !!d.resource.versionId, 30000); memoryVersionId = ready.resource.versionId!; assert.notEqual(memoryVersionId, pdfVersionId);
    const source = await request<BlocksResult>(`/api/resources/${memoryResourceId}/blocks?versionId=${memoryVersionId}&page=1`); const target = source.blocks.find(b => b.text.includes('Revenue 100')); assert.ok(target); memoryBlockId = target.id; assert.notEqual(memoryBlockId, reviewedPdfBlockId);
    await navigate(`/reader/${memoryResourceId}?versionId=${memoryVersionId}&page=1&mode=ko`); const block = page!.locator(`[id="block-${target.id}"]`); await block.getByRole('checkbox').check();
    const resultPromise = page!.waitForResponse(response => response.url() === `${base}/api/translations` && response.request().method() === 'POST');
    await page!.getByRole('button', { name: '한국어로 번역', exact: true }).click(); const response = await resultPromise; assert.equal(response.ok(), true); const result = await response.json() as TranslationRequestResult;
    assert.equal(result.cached, 1); assert.deepEqual(result.jobIds, []);
    await expect(block.locator('.translation-badges')).toContainText('검수 번역 재사용'); await expect(block.locator('.translated-block')).toContainText(reviewedPdfText);
    const reused = await request<BlocksResult>(`/api/resources/${memoryResourceId}/blocks?versionId=${memoryVersionId}&page=1`); const translation = reused.blocks.find(b => b.id === memoryBlockId)?.translation;
    assert.ok(translation?.current); assert.equal(translation.origin, 'memory'); assert.equal(translation.reviewStatus, 'user_reviewed'); assert.equal(translation.reviewId, reviewId); assert.equal(translation.textKo, reviewedPdfText);
    reviewEvidence[0].reusedTranslationId = translation.id; assert.notEqual(translation.id, reviewEvidence[0].correctedTranslationId);
    assert.deepEqual((await request<Bootstrap>('/api/bootstrap')).usage, before);
    const exported = JSON.parse(execFileSync(process.execPath, ['--import', 'tsx', 'scripts/export-translation-memory.ts'], {
      cwd: APP_ROOT, windowsHide: true, encoding: 'utf8', env: { ...process.env, APP_ROOT, DATA_DIR: isolatedData, OPENAI_API_KEY: '', TRANSLATION_MODEL: '' },
    })) as { path: string; pairs: number; modelTrainingStarted: boolean };
    assert.equal(exported.modelTrainingStarted, false); assert.ok(path.resolve(exported.path).startsWith(path.resolve(isolatedData) + path.sep));
    const pairs = fs.readFileSync(exported.path, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
    assert.equal(exported.pairs, pairs.length); const pair = pairs.filter(pair => pair.reviewId === reviewId); assert.equal(pair.length, 1);
    assert.equal(pair[0].source, 'Revenue 100, costs 60, margin 40%.'); assert.equal(pair[0].target, reviewedPdfText); assert.equal(pair[0].reviewStatus, 'user_reviewed');
    assert.equal(pair[0].sourceLanguage, 'en'); assert.equal(pair[0].targetLanguage, 'ko'); assert.equal(pair[0].resourceId, uploadedId); assert.equal(pair[0].sourceVersionId, pdfVersionId); assert.equal(pair[0].sourceUrl, null);
    reviewEvidence[0].exportedPairVerified = true;
    await page!.setViewportSize({ width: 390, height: 844 }); await navigate(`/reader/${memoryResourceId}?versionId=${memoryVersionId}&page=1&mode=ko`); await expect(page!.locator('.reader-panel')).toHaveCount(0); await expect(block.locator('.translation-badges')).toContainText('검수 번역 재사용');
    await block.scrollIntoViewIfNeeded(); assert.equal(await page!.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false); await page!.screenshot({ path: path.join(resultsDirectory, 'e2e-reviewed-memory-mobile.png') });
    await page!.setViewportSize({ width: 1440, height: 1000 });
  });
  await step('PDF 원본 byte range와 Excel 원본 해시', async () => {
    const original = `/api/resources/${uploadedId}/original?versionId=${pdfVersionId}`;
    const response = await fetch(base + original, { headers: { Origin: base, Range: 'bytes=0-31' } }); assert.equal(response.status, 206);
    assert.deepEqual(Buffer.from(await response.arrayBuffer()), pdf.subarray(0, 32)); assert.equal(response.headers.get('content-range'), `bytes 0-31/${pdf.length}`);
    const full = await fetch(base + original); assert.equal(hash(new Uint8Array(await full.arrayBuffer())), hash(pdf));
    const excel = initial.resources.find(r => r.id === 'T01');
    if (excel?.versionId) { const detail = await request<ResourceDetail>('/api/resources/T01'); const version = detail.versions.find(v => v.id === excel.versionId)!; const file = await fetch(`${base}/api/resources/T01/original?versionId=${excel.versionId}`); assert.equal(file.status, 200); assert.equal(hash(new Uint8Array(await file.arrayBuffer())), version.fileHash); }
    else checks.push({ name: 'T01 실제 원본 해시', status: 'skipped', durationMs: 0, detail: '사전 가져온 T01 원본 없음' });
  });
  await step('외부 네트워크 차단 상태에서 저장 HTML·PDF·번역·메모 읽기', async () => {
    let blockedExternalRequests = 0;
    await page!.context().route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.hostname === '127.0.0.1' || ['data:', 'blob:'].includes(url.protocol)) return route.continue();
      blockedExternalRequests += 1; return route.abort('internetdisconnected');
    });
    try {
      await navigate(`/reader/R01?versionId=${first.versionId}&blockId=${paragraph.id}&mode=en`);
      await expect(page!.locator(`[id="block-${paragraph.id}"] .original-block`)).toContainText(paragraph.text.slice(0, 50));
      await navigate(`/reader/${uploadedId}?versionId=${pdfVersionId}&page=1&mode=en`);
      await expect(page!.locator('.pdf-rendered-page canvas')).toBeVisible({ timeout: 20000 });
      await expect(page!.locator('.textLayer')).toContainText('TEST FIXTURE - Browser E2E');
      if (verifyLocalTranslation) {
        const before = (await request<Bootstrap>('/api/bootstrap')).usage;
        await navigate(`/reader/R01?versionId=${first.versionId}&blockId=${translatedHtmlBlockId}&mode=ko`);
        await expect(page!.locator(`[id="block-${translatedHtmlBlockId}"] .translated-block`)).toContainText(translatedHtmlText.slice(0, 50));
        await navigate(`/reader/${uploadedId}?versionId=${pdfVersionId}&page=1&mode=ko`);
        await expect(page!.locator('.document-blocks')).toContainText('40%');
        const after = (await request<Bootstrap>('/api/bootstrap')).usage; assert.deepEqual(after, before);
      }
      await navigate('/notes'); await expect(page!.locator('.note-card').filter({ hasText: editedText })).toBeVisible();
      assert.equal(blockedExternalRequests, 0, '저장 자료 읽기에 외부 CDN 요청이 없어야 합니다.');
    } finally { await page!.context().unroute('**/*'); }
  });
  await step('390px 모바일 주요 기록 동선과 가로 넘침', async () => {
    await page!.setViewportSize({ width: 390, height: 844 });
    for (const route of ['/learn', '/library', '/notes', '/tools', '/glossary']) { await navigate(route); assert.equal(await page!.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false, `가로 넘침: ${route}`); }
    await page!.getByRole('button', { name: '메뉴 열기', exact: true }).click(); await expect(page!.getByRole('navigation', { name: '주 메뉴', exact: true })).toBeVisible();
    await page!.getByRole('link', { name: /^내 학습 기록/ }).click(); await expect(page!.locator('.note-card').filter({ hasText: editedText })).toBeVisible();
    await page!.screenshot({ path: path.join(resultsDirectory, 'e2e-mobile-records.png') });
  });
  const beforeRestart = await request<Bootstrap>('/api/bootstrap');
  await step('웹·worker 재시작 후 메모·북마크·진도·원문·번역·위치 유지', async () => {
    await stopServer(); await startServer(); const after = await request<Bootstrap>('/api/bootstrap');
    assert.equal(after.notes.find(n => n.id === noteId)?.text, editedText); assert.ok(after.bookmarks.some(b => b.id === bookmarkId));
    assert.equal(after.progress.find(p => p.moduleId === 'M01')?.status, 'completed'); assert.ok(after.resources.some(r => r.id === uploadedId && r.versionId === pdfVersionId));
    assert.deepEqual(after.positions, beforeRestart.positions); assert.equal(after.translationStatus.keyConfigured, false);
    if (verifyLocalTranslation) {
      assert.equal(after.translationStatus.provider, 'argos'); assert.deepEqual(after.usage, beforeRestart.usage);
      const html = await request<BlocksResult>(`/api/resources/R01/blocks?versionId=${first.versionId}&anchorBlockId=${translatedHtmlBlockId}`);
      assert.equal(html.blocks.find(b => b.id === translatedHtmlBlockId)?.translation?.textKo, translatedHtmlText);
      const pdf = await request<BlocksResult>(`/api/resources/${uploadedId}/blocks?versionId=${pdfVersionId}&page=1`);
      assert.equal(pdf.blocks.map(b => b.translation?.textKo || '').join('\n'), translatedPdfText);
      const reviewed = pdf.blocks.find(b => b.id === reviewedPdfBlockId)?.translation; assert.equal(reviewed?.reviewId, reviewId); assert.equal(reviewed?.reviewStatus, 'user_reviewed');
      const copied = await request<BlocksResult>(`/api/resources/${memoryResourceId}/blocks?versionId=${memoryVersionId}&page=1`); assert.equal(copied.blocks.find(b => b.id === memoryBlockId)?.translation?.textKo, reviewedPdfText); assert.equal(copied.blocks.find(b => b.id === memoryBlockId)?.translation?.origin, 'memory');
    }
    await page!.setViewportSize({ width: 1440, height: 1000 }); await navigate('/notes'); await expect(page!.locator('.note-card').filter({ hasText: editedText })).toBeVisible();
  });
  await step('메모 삭제·북마크 해제·학습 상태 되돌리기', async () => {
    const card = page!.locator('.note-card').filter({ hasText: editedText }); await card.getByRole('button', { name: /메모 삭제/ }).click(); await card.getByRole('button', { name: '삭제', exact: true }).click();
    await poll(() => request<Bootstrap>('/api/bootstrap'), d => !d.notes.some(n => n.id === noteId));
    await navigate('/notes?tab=bookmarks'); await page!.getByRole('button', { name: '현재가치 입문 북마크 해제', exact: true }).click();
    await poll(() => request<Bootstrap>('/api/bootstrap'), d => !d.bookmarks.some(b => b.id === bookmarkId));
    await navigate('/learn/financial-statements'); await page!.getByLabel('현재 상태', { exact: true }).selectOption(previousProgress);
    await poll(() => request<Bootstrap>('/api/bootstrap'), d => (d.progress.find(p => p.moduleId === 'M01')?.status || 'not_started') === previousProgress);
  });
  assert.deepEqual(pageErrors, [], '브라우저 JavaScript 런타임 오류');
} catch (error) {
  reportError = error instanceof Error ? error.stack || error.message : String(error); console.error(reportError);
  try { await page?.screenshot({ path: path.join(resultsDirectory, 'e2e-failure.png') }); } catch { /* Closed browser cannot provide a screenshot. */ }
  process.exitCode = 1;
} finally {
  await browser?.close(); await stopServer(); closeDb();
  fs.writeFileSync(path.join(resultsDirectory, 'browser-e2e.json'), JSON.stringify({ generatedAt: new Date().toISOString(), dataIsolation: true, isolatedData, browser: 'Chrome headless', checks, localTranslationEvidence, reviewEvidence, translationFailures, pageErrors, consoleErrors, error: reportError }, null, 2));
  fs.writeFileSync(path.join(resultsDirectory, 'browser-e2e-server.log'), serverOutput);
  const resolvedTemp = path.resolve(tempRoot), allowedParent = path.resolve(os.tmpdir());
  if (path.dirname(resolvedTemp) === allowedParent && path.basename(resolvedTemp).startsWith('damodaran-browser-e2e-')) fs.rmSync(resolvedTemp, { recursive: true, force: true });
  console.log(JSON.stringify({ passed: checks.filter(c => c.status === 'passed').length, failed: checks.filter(c => c.status === 'failed').length, report: path.join(resultsDirectory, 'browser-e2e.json') }));
}
