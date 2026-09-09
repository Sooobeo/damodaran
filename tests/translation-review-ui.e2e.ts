/** Review UI contract with labelled fixtures. Every API request is intercepted;
 * no personal database writes, model calls, or semantic translation claims. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium, expect } from '@playwright/test';
import type { Bootstrap, BlocksResult, Resource, ResourceDetail, Translation, Version } from '../lib/client-types';

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:3000';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const resultDirectory = path.resolve('test-results'); fs.mkdirSync(resultDirectory, { recursive: true });
const resource: Resource = { id: 'REVIEW-UI-TEST', titleKo: '번역 검수 UI 테스트 · 실제 원문 아님', titleEn: 'UI REVIEW TEST FIXTURE', summaryKo: '검수 동작 확인용', kind: 'article', format: 'html', level: 'beginner', priority: 'essential', url: null, author: 'UI test', tags: [], objectives: [], sourceStatus: 'ready', versionId: 'review-ui-version', versionCount: 1, blockCount: 3, translatedCount: 3, bookmarked: false, moduleIds: [] };
const version: Version = { id: resource.versionId!, resourceId: resource.id, fileHash: 'fixture', format: 'html', pageCount: null, importedAt: '2026-09-09T00:00:00Z', extractionStatus: 'ready' };
const originalTranslation: Translation = { id: 'review-ui-translation', textKo: 'UI 테스트 예제: 매출액 100, 비용 60.', reviewStatus: 'unreviewed', validationStatus: 'passed', current: true, reviewId: null, origin: 'machine', structure: null, warnings: [] };
let savedTranslation = { ...originalTranslation };
const table = { schemaVersion: 1, rows: [{ cells: [{ text: 'Revenue', header: true }, { text: '100' }] }] };
const blocks = (): BlocksResult => ({ version, total: 3, blocks: [
  { id: 'review-ui-block', order: 0, type: 'paragraph', text: 'UI TEST FIXTURE: Revenue 100, costs 60.', pageIndex: null, structure: null, warnings: [], translation: { ...savedTranslation } },
  { id: 'review-ui-table', order: 1, type: 'table', text: 'Revenue\t100', pageIndex: null, structure: table, warnings: [], translation: { ...originalTranslation, id: 'review-ui-table-translation', textKo: '매출액\t100', structure: table } },
  { id: 'review-ui-memory', order: 2, type: 'paragraph', text: 'UI TEST FIXTURE: A reviewed translation is reused.', pageIndex: null, structure: null, warnings: [], translation: { ...originalTranslation, id: 'review-ui-memory-translation', textKo: 'UI 테스트 예제: 검수한 번역을 다시 사용합니다.', reviewStatus: 'user_reviewed', origin: 'memory', reviewId: 'memory-review' } },
] });
const detail: ResourceDetail = { resource, versions: [version], relations: [], modules: [], position: null };
const bootstrap = (): Bootstrap => ({ resources: [resource], modules: [], glossary: [], toolGuides: [], notes: [], bookmarks: [], positions: [], progress: [], jobs: [], settings: { fontSize: 17, languageMode: 'parallel' }, translationStatus: { provider: 'argos', providerLabel: 'Argos Translate', local: true, apiKeyRequired: false, configured: false, keyConfigured: false, modelConfigured: false, model: null, maxCharsPerJob: 20000, maxCharsPerDay: 100000, liveVerified: false, statusMessage: 'UI 테스트: 모델 없이 저장 번역 검수' }, usage: { sourceChars: 0, localSourceChars: 0, localJobs: 0, remoteSourceChars: 0, inputTokens: 0, outputTokens: 0, unknownCount: 0 } });
type ReviewRequest = { translationId: string; expectedReviewId: string | null; textKo: string };
const requests: ReviewRequest[] = []; const unexpected: string[] = []; const errors: string[] = []; const checks: string[] = [];
let delayNextReview = false; let releaseReview: (() => void) | undefined;
const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'] });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
await context.route('**/*', async route => {
  const request = route.request(); const url = new URL(request.url());
  if (url.origin !== new URL(base).origin) { unexpected.push(request.url()); await route.abort(); return; }
  if (!url.pathname.startsWith('/api/')) { await route.continue(); return; }
  if (url.pathname === '/api/translations/review' && request.method() === 'POST') {
    const input = request.postDataJSON() as ReviewRequest; requests.push(input);
    if (delayNextReview) { delayNextReview = false; await new Promise<void>(resolve => { releaseReview = resolve; }); releaseReview = undefined; }
    if (input.expectedReviewId !== savedTranslation.reviewId) { await route.fulfill({ status: 409, json: { message: '다른 창에서 번역을 수정했습니다. 최신 검수 내용을 확인해 주세요.' } }); return; }
    if (!input.textKo.includes('100') || !input.textKo.includes('60')) { await route.fulfill({ status: 422, json: { message: '숫자·부호·통화·단위가 원문과 다릅니다.' } }); return; }
    savedTranslation = { ...savedTranslation, textKo: input.textKo, reviewId: `review-ui-${requests.length}`, reviewStatus: 'user_reviewed', origin: 'user' };
    await route.fulfill({ status: 200, json: savedTranslation }); return;
  }
  let body: unknown;
  if (url.pathname === '/api/bootstrap') body = bootstrap();
  else if (url.pathname === `/api/resources/${resource.id}`) body = detail;
  else if (url.pathname === `/api/resources/${resource.id}/blocks`) body = blocks();
  else if (url.pathname === '/api/reading-position' && request.method() === 'PUT') body = { saved: true };
  else { unexpected.push(`${request.method()} ${url.pathname}`); await route.fulfill({ status: 500, json: { message: '등록되지 않은 UI 테스트 요청' } }); return; }
  await route.fulfill({ status: 200, json: body });
});
const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
const block = page.locator('[id="block-review-ui-block"]');
async function navigate(mode = 'parallel') { await page.goto(`${base}/reader/${resource.id}?versionId=${version.id}&mode=${mode}`, { waitUntil: 'domcontentloaded', timeout: 45000 }); await expect(block).toBeVisible({ timeout: 20000 }); }
async function check(name: string, work: () => Promise<void>) { await work(); checks.push(name); console.log(`통과: ${name}`); }

try {
  await check('모델·키 없이 기존 번역 수정, 키보드 시작·Escape 취소·포커스 복원', async () => {
    await navigate(); const edit = block.getByRole('button', { name: '번역 수정', exact: true }); await edit.focus(); await page.keyboard.press('Enter');
    const field = block.getByLabel('1번째 문단 번역 수정', { exact: true }); await expect(field).toBeFocused();
    await field.fill('취소할 UI 테스트 초안'); await field.press('Escape'); await expect(field).toHaveCount(0); await expect(edit).toBeFocused();
    await expect(block.locator('.translated-block')).toContainText(originalTranslation.textKo); assert.equal(requests.length, 0);
  });
  await check('표 수정 제외 및 검수 번역 재사용 배지', async () => {
    const table = page.locator('[id="block-review-ui-table"]'); await expect(table.getByRole('button', { name: '번역 수정', exact: true })).toHaveCount(0);
    await expect(table).toContainText('표 등 구조가 있는 번역은 아직 수정할 수 없습니다.');
    const memory = page.locator('[id="block-review-ui-memory"]'); await expect(memory.locator('.translation-badges')).toContainText('사용자 검수 완료'); await expect(memory.locator('.translation-badges')).toContainText('검수 번역 재사용');
  });
  await check('검수 저장 중 중복 요청 방지, 검수 배지와 새로고침 후 수정문 표시', async () => {
    const revised = 'UI 테스트 예제: 매출은 100이고 비용은 60이다.';
    await block.getByRole('button', { name: '번역 수정', exact: true }).click(); const field = block.getByLabel('1번째 문단 번역 수정', { exact: true });
    await field.fill(revised); delayNextReview = true; await field.press('Control+Enter');
    await expect.poll(() => requests.length).toBe(1); await expect(block.getByRole('button', { name: '저장 중…', exact: true })).toBeDisabled();
    await field.press('Control+Enter'); assert.equal(requests.length, 1); assert.deepEqual(requests[0], { translationId: originalTranslation.id, expectedReviewId: null, textKo: revised });
    assert.ok(releaseReview); releaseReview(); await expect(block.getByLabel('1번째 문단 번역 수정', { exact: true })).toHaveCount(0);
    await expect(block.locator('.translation-badges')).toContainText('사용자 검수 완료'); await expect(block.locator('.translated-block')).toContainText(revised);
    await navigate(); await expect(block.locator('.translated-block')).toContainText(revised); await page.screenshot({ path: path.join(resultDirectory, 'review-saved-desktop.png') });
  });
  await check('서버 무결성 거부 시 오류와 초안 보존, 수정 후 재시도', async () => {
    await block.getByRole('button', { name: '번역 수정', exact: true }).click(); const field = block.getByLabel('1번째 문단 번역 수정', { exact: true });
    const invalid = 'UI 테스트 예제: 매출 999, 비용 60.'; await field.fill(invalid); await block.getByRole('button', { name: '검수 완료로 저장', exact: true }).click();
    await expect(block.getByRole('alert')).toContainText('원문과 다릅니다.'); await expect(field).toHaveValue(invalid);
    await expect(block.locator('.translated-block')).toContainText('매출은 100이고 비용은 60이다.');
    await field.fill('UI 테스트 예제: 매출 100, 비용 60을 확인했다.'); await block.getByRole('button', { name: '검수 완료로 저장', exact: true }).click();
    await expect(block.getByRole('alert')).toHaveCount(0); await expect(field).toHaveCount(0);
  });
  await check('동시 수정 409: 이전 검수 ID 제출, 최신본 비교 후 초안 유지·다시 저장', async () => {
    await block.getByRole('button', { name: '번역 수정', exact: true }).click(); const field = block.getByLabel('1번째 문단 번역 수정', { exact: true }); const expectedReview = savedTranslation.reviewId;
    const mine = 'UI 테스트 예제: 나의 검수안은 매출 100, 비용 60이다.'; await field.fill(mine);
    savedTranslation = { ...savedTranslation, textKo: 'UI 테스트 예제: 다른 창의 검수안, 매출 100과 비용 60.', reviewId: 'external-review' };
    await block.getByRole('button', { name: '검수 완료로 저장', exact: true }).click(); await expect(block.getByRole('alert')).toContainText('다른 창에서');
    assert.equal(requests.at(-1)?.expectedReviewId, expectedReview); await expect(field).toHaveValue(mine);
    await block.getByRole('button', { name: '최신 번역과 비교', exact: true }).click(); await expect(block.locator('.translated-block')).toContainText('다른 창의 검수안'); await expect(field).toHaveValue(mine);
    await field.press('Control+Enter'); await expect(field).toHaveCount(0); assert.equal(requests.at(-1)?.expectedReviewId, 'external-review'); await expect(block.locator('.translated-block')).toContainText(mine);
  });
  await check('390px 모바일 검수 폼·키보드 Tab 저장 및 가로 넘침 없음', async () => {
    await page.setViewportSize({ width: 390, height: 844 }); await navigate('ko'); await expect(page.locator('.reader-panel')).toHaveCount(0);
    await block.getByRole('button', { name: '번역 수정', exact: true }).click(); const field = block.getByLabel('1번째 문단 번역 수정', { exact: true });
    await field.fill('UI 테스트 예제: 모바일에서 검수한 매출 100, 비용 60.');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.screenshot({ path: path.join(resultDirectory, 'review-editor-mobile.png'), fullPage: true });
    await field.press('Tab'); await expect(block.getByRole('button', { name: '검수 완료로 저장', exact: true })).toBeFocused(); await page.keyboard.press('Enter');
    await expect(field).toHaveCount(0); await expect(block.locator('.translation-badges')).toContainText('사용자 검수 완료'); await expect(block.locator('.translated-block')).toContainText('모바일에서 검수한');
  });
  assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
  fs.writeFileSync(path.join(resultDirectory, 'translation-review-ui.json'), JSON.stringify({ passed: true, checks, errors, unexpected, fixtureOnly: true }, null, 2));
} finally { releaseReview?.(); await browser.close(); }
