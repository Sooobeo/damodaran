/** UI contract checks with clearly labelled fixtures. All API requests are intercepted;
 * this never writes personal data or calls a translation provider. Build the app first. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { chromium, expect, type Page } from '@playwright/test';
import type { Bootstrap, BlocksResult, Resource, ResourceDetail, TranslationStatus, Version } from '../lib/client-types';

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:3000';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), '로컬 테스트 서버만 사용합니다.');
const resultsDirectory = path.resolve('test-results');
fs.mkdirSync(resultsDirectory, { recursive: true });
const resource: Resource = { id: 'UI-TEST', titleKo: '번역 UI 테스트 자료 · 실제 원문 아님', titleEn: 'UI TEST FIXTURE', summaryKo: 'UI 동작을 확인하는 테스트 자료', kind: 'article', format: 'html', level: 'beginner', priority: 'essential', url: null, author: 'UI test', tags: [], objectives: [], sourceStatus: 'ready', versionId: 'ui-version', versionCount: 1, blockCount: 2, translatedCount: 0, bookmarked: false, moduleIds: [] };
const version: Version = { id: 'ui-version', resourceId: resource.id, fileHash: 'ui-fixture-hash', format: 'html', pageCount: null, importedAt: '2026-09-09T00:00:00.000Z', extractionStatus: 'ready' };
const localStatus: TranslationStatus = { provider: 'argos', providerLabel: 'Argos Translate', local: true, apiKeyRequired: false, configured: true, keyConfigured: false, modelConfigured: true, model: 'argos-en_ko-1.1', maxCharsPerJob: 20000, maxCharsPerDay: 100000, liveVerified: false, verifiedAt: null, statusMessage: '영어 → 한국어 모델이 준비되었습니다.' };
let currentStatus = { ...localStatus };
const bootstrap = (): Bootstrap => ({ resources: [resource], modules: [], glossary: [], toolGuides: [], notes: [], bookmarks: [], positions: [], progress: [], jobs: [], settings: { fontSize: 17, languageMode: 'parallel' }, translationStatus: currentStatus, usage: { sourceChars: 5801, localSourceChars: 1234, localJobs: 3, remoteSourceChars: 4567, inputTokens: 77, outputTokens: 88, unknownCount: 2 } });
const blocks: BlocksResult = { version, total: 2, blocks: [{ id: 'ui-block-1', order: 0, type: 'paragraph', text: 'UI TEST FIXTURE: Revenue is 100 and cost is 60.', pageIndex: null, structure: null, warnings: [], translation: null }, { id: 'ui-block-2', order: 1, type: 'paragraph', text: 'UI TEST FIXTURE: A previous translation remains available.', pageIndex: null, structure: null, warnings: [], translation: { id: 'ui-previous-translation', textKo: 'UI 테스트 예제: 이전 번역 표시 확인용 문장입니다.', validationStatus: 'passed', reviewStatus: 'unreviewed', current: false } }] };
const detail: ResourceDetail = { resource, versions: [version], relations: [], modules: [], position: null };
const translationRequests: unknown[] = [];
const unexpectedRequests: string[] = [];
const pageErrors: string[] = [];
const checks: string[] = [];
const executablePath = [process.env.PLAYWRIGHT_BROWSER_PATH, 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'].find(candidate => candidate && fs.existsSync(candidate));
const browser = await chromium.launch({ executablePath, headless: true, args: ['--disable-background-networking', '--disable-background-timer-throttling', '--disable-renderer-backgrounding'] });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
await context.route('**/*', async route => {
  const request = route.request(); const url = new URL(request.url());
  if (url.origin !== new URL(base).origin) { unexpectedRequests.push(request.url()); await route.abort(); return; }
  if (!url.pathname.startsWith('/api/')) { await route.continue(); return; }
  let body: unknown;
  if (url.pathname === '/api/bootstrap') body = bootstrap();
  else if (url.pathname === `/api/resources/${resource.id}`) body = detail;
  else if (url.pathname === `/api/resources/${resource.id}/blocks`) body = blocks;
  else if (url.pathname === '/api/reading-position' && request.method() === 'PUT') body = { saved: true };
  else if (url.pathname === '/api/translations' && request.method() === 'POST') { translationRequests.push(request.postDataJSON()); body = { jobIds: [], cached: 1 }; }
  else { unexpectedRequests.push(`${request.method()} ${url.pathname}`); await route.fulfill({ status: 500, json: { message: '등록되지 않은 UI 테스트 요청' } }); return; }
  await route.fulfill({ status: 200, json: body });
});
const page = await context.newPage();
page.on('pageerror', error => pageErrors.push(error.message));
async function go(route: string) {
  await page.goto(base + route, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await expect(page.locator('.loading-state')).toHaveCount(0, { timeout: 20000 });
}
async function check(name: string, work: () => Promise<void>) { await work(); checks.push(name); console.log(`통과: ${name}`); }
async function noOverflow(target: Page) { const dimensions = await target.evaluate(() => ({ width: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth })); assert.ok(dimensions.scrollWidth <= dimensions.width + 1, JSON.stringify(dimensions)); }

try {
  await check('API 키 없이 준비된 무료 로컬 상태 및 API 사용량과 구분', async () => {
    await go('/settings'); const settings = page.getByRole('region', { name: '한국어 번역 설정' }); const usage = page.getByRole('region', { name: '번역 사용량' });
    await expect(settings.getByText('사용 가능', { exact: true })).toBeVisible();
    await expect(settings.getByText('필요 없음', { exact: true })).toBeVisible();
    await expect(settings.getByText('Argos Translate', { exact: true })).toBeVisible();
    await expect(usage.locator('.usage-count strong')).toHaveText('1,234');
    await expect(usage.getByText('입력 토큰', { exact: true })).toHaveCount(0);
    await expect(usage.getByText('3건', { exact: true })).toBeVisible();
    await noOverflow(page); await page.screenshot({ path: path.join(resultsDirectory, 'free-translation-settings-desktop.png'), fullPage: true });
  });
  await check('미설치 안내는 무료 설치 명령을 제공하고 번역 실행만 비활성화', async () => {
    currentStatus = { ...localStatus, configured: false, modelConfigured: false, model: null, statusMessage: '무료 번역 모델을 먼저 설치해 주세요.' };
    await go('/settings'); await page.getByText('무료 번역 설치 방법', { exact: true }).click();
    await expect(page.getByText('npm run setup:translation', { exact: true })).toBeVisible();
    await expect(page.getByText('OPENAI_API_KEY', { exact: false })).toHaveCount(0);
    await go(`/reader/${resource.id}?versionId=${version.id}&mode=parallel`);
    await expect(page.locator('.translation-setup-note')).toContainText(currentStatus.statusMessage);
    await page.getByRole('checkbox', { name: '1번째 문단 번역 선택', exact: true }).check();
    await expect(page.getByRole('button', { name: '한국어로 번역', exact: true })).toBeDisabled();
    await expect(page.getByText('설정 변경 · 이전 번역', { exact: true })).toBeVisible();
    assert.equal(translationRequests.length, 0);
  });
  await check('무료 번역에서 선택한 문단 ID만 전송하고 이전 번역을 보존', async () => {
    currentStatus = { ...localStatus };
    await go(`/reader/${resource.id}?versionId=${version.id}&mode=parallel`);
    await page.getByRole('checkbox', { name: '1번째 문단 번역 선택', exact: true }).check();
    await expect(page.getByRole('button', { name: '한국어로 번역', exact: true })).toBeEnabled();
    await expect(page.locator('.translation-selection')).toContainText('무료 · 이 PC에서 번역');
    await page.getByRole('button', { name: '한국어로 번역', exact: true }).click();
    await expect.poll(() => translationRequests.length).toBe(1);
    assert.deepEqual(translationRequests[0], { sourceVersionId: version.id, blockIds: ['ui-block-1'] });
    await expect(page.getByText('기계 번역 · 사용자 미검수', { exact: true })).toBeVisible();
    await expect(page.getByText('UI 테스트 예제: 이전 번역 표시 확인용 문장입니다.', { exact: true })).toBeVisible();
    await noOverflow(page); await page.screenshot({ path: path.join(resultsDirectory, 'free-translation-reader-desktop.png'), fullPage: true });
  });
  await check('390px 모바일에서도 무료 번역 설정 및 선택 동작 표시', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await go('/settings'); await expect(page.getByText('누적 로컬 번역량', { exact: true })).toBeVisible(); await noOverflow(page);
    await page.screenshot({ path: path.join(resultsDirectory, 'free-translation-settings-mobile.png'), fullPage: true });
    await go(`/reader/${resource.id}?versionId=${version.id}&mode=ko`);
    await expect(page.locator('.reader-panel')).toHaveCount(0);
    await page.getByRole('checkbox', { name: '1번째 문단 번역 선택', exact: true }).check();
    await expect(page.getByRole('button', { name: '한국어로 번역', exact: true })).toBeEnabled(); await noOverflow(page);
    await page.screenshot({ path: path.join(resultsDirectory, 'free-translation-reader-mobile.png'), fullPage: true });
  });
  await check('명시적으로 선택한 OpenAI만 API 키와 원격 사용량 표시', async () => {
    currentStatus = { ...localStatus, provider: 'openai', providerLabel: 'OpenAI', local: false, apiKeyRequired: true, configured: false, keyConfigured: false, modelConfigured: false, model: null, statusMessage: '선택한 OpenAI 제공자의 서버 설정이 필요합니다.' };
    await page.setViewportSize({ width: 1440, height: 1000 }); await go('/settings');
    const usage = page.getByRole('region', { name: '번역 사용량' });
    await expect(usage.locator('.usage-count strong')).toHaveText('4,567');
    await expect(usage.getByText('입력 토큰', { exact: true })).toBeVisible();
    await expect(usage.getByText('77', { exact: true })).toBeVisible();
    await expect(usage.getByText('로컬 번역 작업', { exact: true })).toHaveCount(0);
    await page.getByText('번역 설정 방법', { exact: true }).click();
    await expect(page.getByText('TRANSLATION_PROVIDER=openai', { exact: false })).toBeVisible();
  });
  assert.deepEqual(pageErrors, []); assert.deepEqual(unexpectedRequests, []);
  fs.writeFileSync(path.join(resultsDirectory, 'free-translation-ui.json'), JSON.stringify({ passed: true, checks, pageErrors, unexpectedRequests, fixtureOnly: true }, null, 2));
} finally { await browser.close(); }
