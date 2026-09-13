import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import Database from 'better-sqlite3';
import dotenv from 'dotenv';

// Import app modules only after fixing the isolated absolute DATA_DIR below.
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OWNED_ROOT = path.join(ROOT, '.training', 'quality-evaluation');
type Row = Record<string, string | number | null>;
type Version = Row & { id: string; resource_id: string; format: string; extractor_version: string; file_hash: string; original_path: string; byte_size: number; page_count: number | null };
type Block = Row & { id: string; source_version_id: string; sort_order: number; type: string; text: string; source_hash: string; page_index: number | null };
type SourceSnapshot = { dataDir: string; schemaVersion: number; publicSourceSnapshotId: string; resource: Row; version: Version; blocks: Block[]; assets: Row[] };
type FileProof = { relativePath: string; sha256: string; size: number };
type Scope = { name: string; sourceVersionId: string; blockIds?: string[]; pageRange?: [number, number]; expectedBlockIds: string[] };
type Preparation = { version: 'quality-app-qa-v1'; preparedAt: string; dataDir: string; sourceDatabasesReadOnly: true; schemaVersion: number; sourceSnapshot: FileProof; files: FileProof[]; scopes: Scope[]; hymtManifestSha256: string; personalRecordsCopied: false; existingTranslationsCopied: false; modelExecuted: false; operationalSchemaVersions: number[] };
const sha = (value: Buffer | string) => createHash('sha256').update(value).digest('hex');
function stable(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, stable(item)]));
  return value;
}
const rowHash = (value: unknown) => sha(JSON.stringify(stable(value)));
function writeNew(file: string, value: unknown) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n', { flag: 'wx' }); }
function within(base: string, target: string) { const relative = path.relative(base, target); return relative !== '' && !relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative); }
function localFile(directory: string, relative: string) {
  assert(!path.isAbsolute(relative), 'DB 파일 경로는 DATA_DIR 기준 상대 경로여야 합니다.');
  const target = path.resolve(directory, relative);
  assert(within(directory, target), 'DATA_DIR 밖 파일은 읽거나 복사하지 않습니다.');
  if (fs.existsSync(target)) assert(within(fs.realpathSync(directory), fs.realpathSync(target)), '심볼릭 링크가 DATA_DIR 밖을 가리킵니다.');
  return target;
}
function fileProof(directory: string, relativePath: string): FileProof {
  const file = localFile(directory, relativePath), bytes = fs.readFileSync(file);
  return { relativePath, sha256: sha(bytes), size: bytes.length };
}
function verifyFile(directory: string, proof: FileProof) { assert.deepEqual(fileProof(directory, proof.relativePath), proof, '보존한 원본/검증 파일 해시가 달라졌습니다.'); }
function options() {
  const values = new Map<string, string>(); let prepareOnly = false, run = false;
  for (let index = 2; index < process.argv.length; index++) {
    const argument = process.argv[index];
    if (argument === '--help') {
      console.log('실제 앱 품질 QA: 운영 자료를 읽기 전용으로 새 격리 DATA_DIR에 복제합니다.\n' +
        'node --import tsx scripts/verify-quality-app.ts --prepare-only --data-dir <새 폴더> [--source-data-dir <운영 DATA_DIR>] [--pdf-data-dir <v2 PDF DATA_DIR>] [--pdf-version <ID>] [--pdf-pages 3:3]\n' +
        'node --import tsx scripts/verify-quality-app.ts --run --data-dir <준비한 폴더> [--timeout-minutes 30]\n' +
        '--prepare-only는 모델을 로드하지 않습니다. --run은 현재 등록된 Hy7로 HTML 3문단과 선택한 PDF 페이지의 전체 텍스트 블록을 실제 번역합니다.');
      process.exit(0);
    }
    if (argument === '--prepare-only') prepareOnly = true;
    else if (argument === '--run') run = true;
    else {
      assert(['--data-dir', '--source-data-dir', '--pdf-data-dir', '--pdf-version', '--pdf-pages', '--timeout-minutes'].includes(argument), '지원하지 않는 CLI 인자입니다.');
      const value = process.argv[++index]; assert(value && !value.startsWith('--') && !values.has(argument), 'CLI 인자 값이 없거나 중복됐습니다.'); values.set(argument, value);
    }
  }
  assert(prepareOnly !== run, '--prepare-only 또는 --run 중 하나를 명시하세요.');
  assert(values.has('--data-dir'), '새 격리 --data-dir를 명시하세요.');
  const directory = path.resolve(ROOT, values.get('--data-dir')!);
  assert(within(OWNED_ROOT, directory), 'QA DATA_DIR는 .training/quality-evaluation/ 아래 새 폴더여야 합니다.');
  return { values, prepareOnly, run, directory };
}
function readSource(directory: string, format: 'html' | 'pdf', versionId?: string): SourceSnapshot {
  const source = new Database(localFile(directory, 'library.sqlite'), { readonly: true, fileMustExist: true });
  try {
    source.pragma('query_only = ON'); source.pragma('busy_timeout = 5000');
    return source.transaction(() => {
      const schemaVersion = (source.prepare('SELECT MAX(version) version FROM schema_migrations').get() as { version: number }).version;
      const version = (versionId
        ? source.prepare("SELECT * FROM source_versions WHERE id=? AND format=? AND extraction_status IN ('ready','partial')").get(versionId, format)
        : source.prepare(format === 'html'
          ? "SELECT * FROM source_versions WHERE resource_id='R01' AND format='html' AND extraction_status IN ('ready','partial') ORDER BY imported_at DESC,id DESC LIMIT 1"
          : "SELECT * FROM source_versions WHERE format='pdf' AND extractor_version='pdf-paragraphs-v2' AND extraction_status IN ('ready','partial') ORDER BY imported_at DESC,id DESC LIMIT 1").get()) as Version | undefined;
      assert(version, format === 'pdf' ? '읽기 가능한 pdf-paragraphs-v2 PDF가 없습니다. 기존 격리 검증본을 --pdf-data-dir로 명시하세요.' : 'R01의 읽기 가능한 HTML 버전이 없습니다.');
      if (format === 'pdf') assert.equal(version.extractor_version, 'pdf-paragraphs-v2', 'PDF는 v2 불변 버전을 명시해야 합니다.');
      if (format === 'html') assert.equal(version.resource_id, 'R01');
      const resource = source.prepare('SELECT * FROM resources WHERE id=?').get(version.resource_id) as Row;
      const blocks = source.prepare('SELECT * FROM source_blocks WHERE source_version_id=? ORDER BY sort_order').all(version.id) as Block[];
      const assets = source.prepare('SELECT * FROM source_assets WHERE source_version_id=? ORDER BY id').all(version.id) as Row[];
      assert(resource && blocks.length, '원문에 연결된 자료·블록이 없습니다.');
      for (const block of blocks) assert.equal(sha(block.text), block.source_hash, '원문 블록의 실제 텍스트 해시가 다릅니다.');
      const publicSourceSnapshotId = 'sha256:' + rowHash({ resource, version, blocks, assets });
      return { dataDir: fs.realpathSync(directory), schemaVersion, publicSourceSnapshotId, resource, version, blocks, assets };
    })();
  } finally { source.close(); }
}
function insertRows(destination: Database.Database, table: 'resources' | 'source_versions' | 'source_blocks' | 'source_assets', rows: Row[]) {
  for (const row of rows) {
    const columns = Object.keys(row); assert(columns.every(column => /^[a-z_]+$/.test(column)));
    const update = table === 'resources' ? ' ON CONFLICT(id) DO UPDATE SET ' + columns.filter(column => column !== 'id').map(column => `${column}=excluded.${column}`).join(',') : '';
    destination.prepare(`INSERT INTO ${table} (${columns.join(',')}) VALUES (${columns.map(() => '?').join(',')})${update}`).run(...columns.map(column => row[column]));
  }
}
function compareSource(destination: Database.Database, snapshot: SourceSnapshot) {
  assert.equal(rowHash(destination.prepare('SELECT * FROM resources WHERE id=?').get(snapshot.version.resource_id)), rowHash(snapshot.resource));
  assert.equal(rowHash(destination.prepare('SELECT * FROM source_versions WHERE id=?').get(snapshot.version.id)), rowHash(snapshot.version));
  assert.equal(rowHash(destination.prepare('SELECT * FROM source_blocks WHERE source_version_id=? ORDER BY sort_order').all(snapshot.version.id)), rowHash(snapshot.blocks));
  assert.equal(rowHash(destination.prepare('SELECT * FROM source_assets WHERE source_version_id=? ORDER BY id').all(snapshot.version.id)), rowHash(snapshot.assets));
}
function personalCounts(destination: Database.Database) {
  return Object.fromEntries(['notes', 'bookmarks', 'reading_positions', 'learning_progress', 'resource_preferences', 'translation_reviews', 'translation_review_links'].map(table => [table, (destination.prepare(`SELECT COUNT(*) count FROM ${table}`).get() as { count: number }).count]));
}

async function main() {
  const args = options();
  dotenv.config({ path: path.join(ROOT, '.env.local'), quiet: true }); dotenv.config({ path: path.join(ROOT, '.env'), quiet: true });
  const sourceDirectory = path.resolve(ROOT, args.values.get('--source-data-dir') ?? process.env.DATA_DIR ?? './data');
  const pdfDirectory = path.resolve(ROOT, args.values.get('--pdf-data-dir') ?? sourceDirectory);
  assert(args.directory !== sourceDirectory && args.directory !== pdfDirectory, '원본 DATA_DIR에서 QA를 실행하지 않습니다.');
  assert(process.env.NODE_ENV !== 'test', '실제 앱 검증은 테스트 제공자 모드에서 실행하지 않습니다.');
  Object.assign(process.env, { APP_ROOT: ROOT, DATA_DIR: args.directory, NODE_ENV: 'production' });
  const preparationPath = path.join(args.directory, 'derived/quality-app-preparation.json');
  const hymtManifestPath = path.join(ROOT, '.translation/hymt/manifest.json');

  if (args.prepareOnly) {
    assert(!fs.existsSync(args.directory), '기존 QA 폴더는 덮어쓰지 않습니다. 새 경로를 지정하세요.');
    // Complete read-only source selection before creating the new database.
    const html = readSource(sourceDirectory, 'html');
    const pdf = readSource(pdfDirectory, 'pdf', args.values.get('--pdf-version'));
    const pageMatch = (args.values.get('--pdf-pages') ?? '3:3').match(/^(\d+):(\d+)$/);
    assert(pageMatch, '--pdf-pages는 3:3 또는 3:5처럼 1부터 시작하는 범위입니다.');
    const from = Number(pageMatch[1]), to = Number(pageMatch[2]);
    assert(from >= 1 && to >= from && to <= (pdf.version.page_count ?? 0));
    const htmlIds = html.blocks.filter(block => ['paragraph', 'p'].includes(block.type) && block.text.length > 80).slice(0, 3).map(block => block.id);
    assert.equal(htmlIds.length, 3, 'HTML 본문 3문단이 필요합니다.');
    const pdfIds = pdf.blocks.filter(block => block.page_index !== null && block.page_index >= from - 1 && block.page_index <= to - 1 && block.type !== 'image' && block.text.trim()).map(block => block.id);
    assert(pdfIds.length > 0, '선택한 PDF 페이지에 실제 텍스트 블록이 없습니다.');
    const scopes: Scope[] = [{ name: 'html-three-paragraphs', sourceVersionId: html.version.id, blockIds: htmlIds, expectedBlockIds: htmlIds },
      { name: `pdf-pages-${from}-${to}`, sourceVersionId: pdf.version.id, pageRange: [from, to], expectedBlockIds: pdfIds }];
    const snapshots = [html, pdf], proofs = new Map<string, FileProof>();
    const parentDirectory = path.dirname(args.directory), ownedRealPath = fs.realpathSync(OWNED_ROOT);
    assert(fs.existsSync(parentDirectory), 'QA 상위 폴더가 존재해야 합니다.');
    const parentRealPath = fs.realpathSync(parentDirectory);
    assert(parentRealPath === ownedRealPath || within(ownedRealPath, parentRealPath), '실제 QA 상위 경로가 지정 범위를 벗어났습니다.');
    fs.mkdirSync(args.directory, { recursive: false });
    assert(within(fs.realpathSync(OWNED_ROOT), fs.realpathSync(args.directory)), '실제 QA 경로가 지정 범위를 벗어났습니다.');
    for (const snapshot of snapshots) {
      const files = [{ relativePath: snapshot.version.original_path, expectedHash: snapshot.version.file_hash, expectedSize: snapshot.version.byte_size },
        ...snapshot.assets.filter(asset => typeof asset.local_path === 'string').map(asset => ({ relativePath: String(asset.local_path), expectedHash: String(asset.file_hash), expectedSize: undefined }))];
      for (const file of files) {
        const proof = fileProof(snapshot.dataDir, file.relativePath);
        assert.equal(proof.sha256, file.expectedHash, '원본/자산 파일의 실제 해시가 DB와 다릅니다.');
        if (file.expectedSize !== undefined) assert.equal(proof.size, file.expectedSize);
        if (proofs.has(proof.relativePath)) assert.deepEqual(proofs.get(proof.relativePath), proof);
        else {
          const target = localFile(args.directory, proof.relativePath); fs.mkdirSync(path.dirname(target), { recursive: true });
          fs.copyFileSync(localFile(snapshot.dataDir, proof.relativePath), target, fs.constants.COPYFILE_EXCL);
          verifyFile(args.directory, proof); proofs.set(proof.relativePath, proof);
        }
      }
    }
    const { setupDatabase, db, closeDb, LATEST_SCHEMA_VERSION } = await import('../lib/db');
    const { seed } = await import('../lib/seed');
    try {
      assert.equal(LATEST_SCHEMA_VERSION, 3); setupDatabase(); seed(); const destination = db();
      destination.transaction(() => { for (const snapshot of snapshots) { insertRows(destination, 'resources', [snapshot.resource]); insertRows(destination, 'source_versions', [snapshot.version]); insertRows(destination, 'source_blocks', snapshot.blocks); insertRows(destination, 'source_assets', snapshot.assets); } })();
      for (const snapshot of snapshots) compareSource(destination, snapshot);
      assert(Object.values(personalCounts(destination)).every(count => count === 0));
      assert.equal((destination.prepare('SELECT COUNT(*) count FROM translations').get() as { count: number }).count, 0);
      assert.deepEqual(destination.pragma('foreign_key_check'), []); assert.equal(destination.pragma('integrity_check', { simple: true }), 'ok');
      writeNew(path.join(args.directory, 'derived/quality-app-source-snapshot.json'), snapshots);
      const preparation: Preparation = { version: 'quality-app-qa-v1', preparedAt: new Date().toISOString(), dataDir: args.directory,
        sourceDatabasesReadOnly: true, schemaVersion: 3, sourceSnapshot: fileProof(args.directory, 'derived/quality-app-source-snapshot.json'),
        files: [...proofs.values()], scopes, hymtManifestSha256: sha(fs.readFileSync(hymtManifestPath)), personalRecordsCopied: false,
        existingTranslationsCopied: false, modelExecuted: false, operationalSchemaVersions: snapshots.map(snapshot => snapshot.schemaVersion) };
      writeNew(preparationPath, preparation);
      console.log(JSON.stringify({ status: 'prepared', dataDir: args.directory, schemaVersion: 3, htmlBlocks: htmlIds.length, pdfPages: [from, to], pdfBlocks: pdfIds.length, totalImmutableBlocks: snapshots.reduce((count, snapshot) => count + snapshot.blocks.length, 0), sourceSnapshots: snapshots.map(snapshot => ({ dataDir: snapshot.dataDir, schemaVersion: snapshot.schemaVersion, publicSourceSnapshotId: snapshot.publicSourceSnapshotId, versionId: snapshot.version.id, originalSha256: snapshot.version.file_hash })), actualModelExecuted: false, sourceDatabasesReadOnly: true }, null, 2));
    } finally { closeDb(); }
    return;
  }

  assert(fs.existsSync(preparationPath), '--prepare-only로 준비한 QA 폴더만 실행할 수 있습니다.');
  const preparation = JSON.parse(fs.readFileSync(preparationPath, 'utf8')) as Preparation;
  assert.equal(preparation.version, 'quality-app-qa-v1'); assert.equal(preparation.dataDir, args.directory); assert.equal(preparation.schemaVersion, 3);
  verifyFile(args.directory, preparation.sourceSnapshot); for (const proof of preparation.files) verifyFile(args.directory, proof);
  assert.equal(sha(fs.readFileSync(hymtManifestPath)), preparation.hymtManifestSha256, '준비 이후 Hy7 등록이 변경됐습니다.');
  assert.equal(process.env.TRANSLATION_PROVIDER, 'hymt', '현재 명시 선택된 무료 Hy7 제공자가 필요합니다.');
  const hymt = JSON.parse(fs.readFileSync(hymtManifestPath, 'utf8')) as { model: string; modelHash: string };
  assert.equal(hymt.model, 'tencent/Hy-MT2-7B-GGUF');
  assert(!fs.existsSync(path.join(ROOT, '.translation/qe/manifest.json')), '이 QA는 미등록 QE의 규칙/미등록 상태 검증입니다. 등록된 QE가 생기면 별도 범위로 검증하세요.');
  if (process.platform === 'win32') {
    const running = execFileSync('powershell.exe', ['-NoProfile', '-Command', "@(Get-Process -Name 'llama-server' -ErrorAction SilentlyContinue).Count"], { windowsHide: true, encoding: 'utf8', timeout: 15000 }).trim();
    assert.equal(running, '0', '다른 번역 모델이 실행 중입니다. 계산 슬롯이 비기 전에는 실행하지 않습니다.');
  }
  assert(os.freemem() >= 11 * 1024 ** 3, '실제 Hy7 앱 검증 전에 가용 물리 메모리 11GiB를 확보하세요.');
  const { db, closeDb } = await import('../lib/db');
  const { enqueueTranslation, getJob, runOnce, cancelJob } = await import('../lib/jobs');
  const { getTranslationForBlock } = await import('../lib/translation');
  const { assertTranslationAvailable } = await import('../lib/translation/runtime');
  const { closeLocalTranslator } = await import('../lib/translation/local');
  const { qualityRuntime, closeQualityEvaluator } = await import('../lib/quality/runtime');
  const { enqueueQualityAssessment } = await import('../lib/quality');
  const { validateFindings } = await import('../lib/quality/rules');
  const destination = db(), snapshots = JSON.parse(fs.readFileSync(localFile(args.directory, preparation.sourceSnapshot.relativePath), 'utf8')) as SourceSnapshot[];
  for (const snapshot of snapshots) compareSource(destination, snapshot);
  assert(Object.values(personalCounts(destination)).every(count => count === 0));
  for (const table of ['translations', 'usage_records', 'jobs', 'translation_quality_assessments']) assert.equal((destination.prepare(`SELECT COUNT(*) count FROM ${table}`).get() as { count: number }).count, 0, '실행 이력이 없는 새 QA DB를 사용하세요.');
  const runtime = assertTranslationAvailable(); assert.equal(runtime.provider, 'hymt'); assert.equal(qualityRuntime().configured, false);
  const reportPath = path.join(args.directory, 'derived/quality-app-report.json'); assert(!fs.existsSync(reportPath), '기존 실제 검증 보고서는 덮어쓰지 않습니다.');
  const startedAt = new Date().toISOString(); const translationJobIds = new Set<string>(); let stopReason: string | null = null, closeError: string | null = null;
  const timeoutMinutes = Number(args.values.get('--timeout-minutes') ?? '30'); assert(Number.isFinite(timeoutMinutes) && timeoutMinutes > 0 && timeoutMinutes <= 180);
  const stop = (reason: string) => { if (stopReason) return; stopReason = reason; for (const jobId of translationJobIds) cancelJob(jobId); void closeLocalTranslator().catch(() => { closeError = 'local_translator_close_failed'; }); };
  const onInterrupt = () => stop('interrupted'); const onTerminate = () => stop('terminated');
  process.on('SIGINT', onInterrupt); process.on('SIGTERM', onTerminate);
  const timer = setTimeout(() => stop('timeout'), timeoutMinutes * 60000); timer.unref();
  const report: Record<string, unknown> = { version: 'quality-app-qa-v1', startedAt, status: 'running', provider: runtime.provider, modelIdentity: runtime.identity, hymtManifestSha256: preparation.hymtManifestSha256, dataDir: args.directory, sourceDatabasesReadOnly: true, humanReviewed: false, semanticReviewCompleted: false, paidCalls: 0, preparationSha256: sha(fs.readFileSync(preparationPath)) };
  writeNew(path.join(args.directory, 'derived/quality-app-start.json'), { ...report, codeHashes: Object.fromEntries(['scripts/verify-quality-app.ts', 'lib/jobs/index.ts', 'lib/translation/index.ts', 'lib/translation/local.ts', 'lib/quality/index.ts', 'lib/quality/runtime.ts', 'lib/quality/rules.ts'].map(file => [file, sha(fs.readFileSync(path.join(ROOT, file)))])) });
  try {
    const requests = preparation.scopes.map(scope => {
      const queued = enqueueTranslation(scope); assert.deepEqual(queued.targets.map(target => target.blockId), scope.expectedBlockIds); assert.equal(queued.cached, 0);
      queued.jobIds.forEach(jobId => translationJobIds.add(jobId)); return { scope, queued };
    });
    while ((destination.prepare("SELECT COUNT(*) count FROM jobs WHERE status IN ('queued','running')").get() as { count: number }).count) {
      if (stopReason) throw new Error(stopReason);
      if (!await runOnce()) await new Promise(resolve => setTimeout(resolve, 300));
      console.log(JSON.stringify({ event: 'job-progress', translations: (destination.prepare('SELECT COUNT(*) count FROM translations').get() as { count: number }).count, qualityAssessments: (destination.prepare("SELECT COUNT(*) count FROM translation_quality_assessments WHERE status NOT IN ('queued','running')").get() as { count: number }).count }));
    }
    if (stopReason) throw new Error(stopReason);
    const checks = requests.map(({ scope, queued }) => {
      const blocks = scope.expectedBlockIds.map(blockId => {
        const block = destination.prepare('SELECT * FROM source_blocks WHERE id=?').get(blockId) as Block;
        const translation = getTranslationForBlock(blockId); assert(translation && translation.current, '현재 원문/모델에 대응한 번역이 저장되지 않았습니다.');
        assert.equal(translation.origin, 'machine'); assert.equal(translation.reviewStatus, 'unreviewed');
        const quality = translation.quality; assert(quality && quality.status === 'unavailable', '미등록 QE를 검사 완료로 표시하면 안 됩니다.');
        assert.equal(quality.score, null); assert.equal(quality.sourceHash, sha(block.text)); assert.equal(quality.translationHash, sha(translation.textKo));
        assert(validateFindings(block.text, translation.textKo, quality.findings)); assert(quality.findings.every(finding => finding.detector === 'rule'));
        return { blockId, translationId: translation.id, sourceSha256: sha(block.text), translationSha256: sha(translation.textKo), containsKorean: /[가-힣]/.test(translation.textKo), validationStatus: translation.validationStatus, warnings: translation.warnings, quality };
      });
      assert(blocks.some(block => block.containsKorean), '한국어 번역이 저장되지 않았습니다.');
      const allPassed = blocks.every(block => block.validationStatus === 'passed');
      const before = { usage: (destination.prepare('SELECT COUNT(*) count FROM usage_records').get() as { count: number }).count, jobs: (destination.prepare('SELECT COUNT(*) count FROM jobs').get() as { count: number }).count, quality: (destination.prepare('SELECT COUNT(*) count FROM translation_quality_assessments').get() as { count: number }).count };
      if (allPassed) { const cached = enqueueTranslation(scope); assert.equal(cached.cached, scope.expectedBlockIds.length); assert.equal(cached.jobIds.length, 0); }
      for (const block of blocks) { const cached = enqueueQualityAssessment(block.translationId); assert(cached.cached && cached.jobId === null); }
      const after = { usage: (destination.prepare('SELECT COUNT(*) count FROM usage_records').get() as { count: number }).count, jobs: (destination.prepare('SELECT COUNT(*) count FROM jobs').get() as { count: number }).count, quality: (destination.prepare('SELECT COUNT(*) count FROM translation_quality_assessments').get() as { count: number }).count };
      assert.deepEqual(after, before, '유효 캐시 확인 중 새 모델 호출/작업이 생성됐습니다.');
      return { scope, blocks, jobs: queued.jobIds.map(getJob), translationCacheVerified: allPassed, qualityCacheVerified: true, cacheAttemptSkippedReason: allPassed ? null : 'automatic_validation_needs_review_preserved_without_regeneration' };
    });
    for (const snapshot of snapshots) compareSource(destination, snapshot); for (const proof of preparation.files) verifyFile(args.directory, proof);
    assert(Object.values(personalCounts(destination)).every(count => count === 0));
    assert.deepEqual(destination.pragma('foreign_key_check'), []); assert.equal(destination.pragma('integrity_check', { simple: true }), 'ok');
    report.checks = checks; report.usageRecords = destination.prepare('SELECT model,source_chars,reservation_status,input_tokens,output_tokens,outcome FROM usage_records ORDER BY created_at,id').all();
    report.actualMachineTranslations = (destination.prepare('SELECT COUNT(*) count FROM translations').get() as { count: number }).count;
    report.status = checks.every(check => check.translationCacheVerified) ? 'completed' : 'needs_review'; report.originalsAndBlocksUnchanged = true;
  } catch (error) {
    report.status = 'failed'; report.error = error instanceof Error ? error.message : 'app_quality_verification_failed';
    report.jobs = destination.prepare('SELECT id,type,status,error_code,error_message FROM jobs ORDER BY created_at,id').all(); process.exitCode = 1;
  } finally {
    clearTimeout(timer); process.off('SIGINT', onInterrupt); process.off('SIGTERM', onTerminate);
    try { await closeQualityEvaluator(); } catch { closeError = 'quality_evaluator_close_failed'; }
    try { await closeLocalTranslator(); } catch { closeError = 'local_translator_close_failed'; }
    report.processClosureAwaited = closeError === null; report.closeError = closeError; if (closeError) { report.status = 'failed'; process.exitCode = 1; }
    report.completedAt = new Date().toISOString(); report.elapsedSeconds = (Date.now() - Date.parse(startedAt)) / 1000;
    writeNew(reportPath, report); closeDb();
    console.log(JSON.stringify({ status: report.status, report: reportPath, actualMachineTranslations: report.actualMachineTranslations, processClosureAwaited: report.processClosureAwaited, semanticReviewCompleted: false }, null, 2));
  }
}

main().catch(error => { console.error(error instanceof Error ? error.message : '격리 앱 품질 검증 실패'); process.exitCode = 1; });
