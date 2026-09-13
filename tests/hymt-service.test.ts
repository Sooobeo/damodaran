import test, {after, mock} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import type {TranslationStatus, TranslationUsage} from '../lib/client-types';

// Both roots are isolated before imports, including config's dotenv lookup and
// runtime discovery. No real installation, saved source or provider is used.
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'damodaran-hymt-service-'));
const migrations = path.join(temporary, 'lib/db/migrations');
fs.mkdirSync(migrations, {recursive:true});
for (const file of fs.readdirSync(path.join(process.cwd(),'lib/db/migrations')).filter(name=>name.endsWith('.sql'))) {
  fs.copyFileSync(new URL(`../lib/db/migrations/${file}`, import.meta.url), path.join(migrations, file));
}
const environment = {
  APP_ROOT:temporary, DATA_DIR:path.join(temporary, 'data'), NODE_ENV:'production',
  TRANSLATION_PROVIDER:'argos', TRANSLATION_MODEL:'synthetic-paid-model', OPENAI_API_KEY:'synthetic-key-never-sent',
};
const previousEnvironment = Object.fromEntries(Object.keys(environment).map(key => [key, process.env[key]]));
Object.assign(process.env, environment);
const forbiddenFetch = mock.method(globalThis, 'fetch', async () => {throw new Error('Network access is forbidden in this fixture');});
const {db, setupDatabase, closeDb, hash, id, now} = await import('../lib/db');
const {config} = await import('../lib/config');
const {usage, translationStatus} = await import('../lib/service');
const {enqueueTranslation, runOnce, getJob, retryJob} = await import('../lib/jobs');
const {translationSnapshot} = await import('../lib/translation');
setupDatabase();
after(() => {
  forbiddenFetch.mock.restore();
  closeDb();
  assert.ok(path.resolve(temporary).startsWith(path.resolve(os.tmpdir()) + path.sep));
  assert.ok(path.basename(temporary).startsWith('damodaran-hymt-service-'));
  fs.rmSync(temporary, {recursive:true, force:true});
  for (const [key, value] of Object.entries(previousEnvironment)) {
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  }
});

function fixture(texts = ['Synthetic source for provider switching.']) {
  const resourceId = id(), versionId = id();
  db().prepare(`INSERT INTO resources(id,source_type,title_en,title_ko,kind,format,created_at)
    VALUES(?,'upload','SYNTHETIC TEST','합성 테스트','본문','html',?)`).run(resourceId, now());
  db().prepare(`INSERT INTO source_versions(id,resource_id,file_hash,original_path,mime,format,byte_size,imported_at,extractor_version,extraction_config_hash,extraction_status)
    VALUES(?,?,?,'originals/synthetic-unused.html','text/html','html',1,?,'fixture-v1','fixture','ready')`).run(versionId, resourceId, hash(resourceId), now());
  const blockIds = texts.map((text, index) => {
    const blockId = id();
    db().prepare(`INSERT INTO source_blocks(id,source_version_id,sort_order,type,text,source_hash)
      VALUES(?,?,?,'paragraph',?,?)`).run(blockId, versionId, index, text, hash(text));
    return blockId;
  });
  return {resourceId, versionId, blockIds};
}

function paidWork(texts?: string[]) {
  assert.equal(process.env.NODE_ENV, 'production');
  config.TRANSLATION_PROVIDER = 'openai';
  try {
    const source = fixture(texts);
    const queued = enqueueTranslation({sourceVersionId:source.versionId, blockIds:source.blockIds});
    assert.equal(queued.jobIds.length, 1);
    const jobId = queued.jobIds[0];
    const items = db().prepare('SELECT id,scope_json FROM job_items WHERE job_id=? ORDER BY rowid').all(jobId) as {id:string; scope_json:string}[];
    assert.ok(items.every(item => JSON.parse(item.scope_json).provider === 'openai'));
    return {...source, jobId, items};
  } finally {config.TRANSLATION_PROVIDER = 'hymt';}
}

function counts() {
  return {
    usage:(db().prepare('SELECT COUNT(*) n FROM usage_records').get() as {n:number}).n,
    translations:(db().prepare('SELECT COUNT(*) n FROM translations').get() as {n:number}).n,
    jobs:(db().prepare('SELECT COUNT(*) n FROM jobs').get() as {n:number}).n,
  };
}

function assertPaidExecutionRejected(work: ReturnType<typeof paidWork>, itemIndex = 0) {
  const item = db().prepare('SELECT status,error_code,scope_json FROM job_items WHERE id=?').get(work.items[itemIndex].id) as {status:string; error_code:string; scope_json:string};
  assert.equal(item.status, 'failed');
  assert.equal(item.error_code, 'PROVIDER_CHANGED');
  assert.equal(item.scope_json, work.items[itemIndex].scope_json);
  const job = db().prepare('SELECT error_code,lease_owner,lease_until FROM jobs WHERE id=?').get(work.jobId) as {error_code:string; lease_owner:string|null; lease_until:string|null};
  assert.equal(job.error_code, 'PROVIDER_CHANGED');
  assert.equal(job.lease_owner, null);
  assert.equal(job.lease_until, null);
  assert.equal(forbiddenFetch.mock.callCount(), 0);
}

function usageJob(provider?: string) {
  const jobId = id();
  db().prepare(`INSERT INTO jobs(id,type,dedupe_key,status,scope_json,created_at,updated_at)
    VALUES(?,'translation',?,'completed','{}',?,?)`).run(jobId, id(), now(), now());
  const itemId = id();
  db().prepare(`INSERT INTO job_items(id,job_id,unit_key,work_key,status,scope_json)
    VALUES(?,?,?,?,'completed',?)`).run(itemId, jobId, id(), id(), JSON.stringify(provider ? {provider} : {}));
  return {jobId, itemId};
}

function recordUsage(work: {jobId:string; itemId:string|null}, sourceChars: number, status: string, inputTokens: number|null = null, outputTokens: number|null = null) {
  const usageId = id();
  db().prepare(`INSERT INTO usage_records(id,job_id,job_item_id,attempt_id,model,source_chars,reservation_status,input_tokens,output_tokens,created_at)
    VALUES(?,?,?,?,?,?,?,?,?,?)`).run(usageId, work.jobId, work.itemId, id(), 'synthetic-model', sourceChars, status, inputTokens, outputTokens, now());
  return usageId;
}

test('Hy-MT usage stays local across attempts while legacy and unknown paid usage remain separate', () => {
  const before = usage();
  const local = usageJob('hymt');
  recordUsage(local, 100, 'reported', 999, 999); // Even malformed local tokens are not API tokens.
  const secondItem = id();
  db().prepare(`INSERT INTO job_items(id,job_id,unit_key,work_key,status,scope_json)
    VALUES(?,?,?,?,'completed','{"provider":"hymt"}')`).run(secondItem, local.jobId, id(), id());
  recordUsage({...local, itemId:secondItem}, 40, 'reported');
  recordUsage(local, 60, 'unknown');
  recordUsage(local, 20, 'sent');
  recordUsage(local, 1000, 'released');
  recordUsage(usageJob('hymt'), 500, 'released');
  recordUsage(usageJob('argos'), 30, 'reported');
  recordUsage(usageJob('finetuned'), 50, 'reported');
  recordUsage(usageJob('openai'), 70, 'reported', 11, 13);
  recordUsage(usageJob('openai'), 80, 'unknown');
  recordUsage(usageJob(), 90, 'reported');
  recordUsage({...usageJob(), itemId:null}, 15, 'reported', 5, 7);
  recordUsage(usageJob('future-provider'), 25, 'reported');
  recordUsage(usageJob('openai'), 5000, 'released');
  const current = usage();
  const expected: TranslationUsage = {sourceChars:580, localSourceChars:300, localJobs:3, remoteSourceChars:280, inputTokens:16, outputTokens:20, unknownCount:3};
  for (const [key, delta] of Object.entries(expected)) assert.equal(current[key] - before[key], delta, key);
  assert.equal(current.sourceChars, current.localSourceChars + current.remoteSourceChars);
});

test('Hy-MT verification metadata is readable and another model is never shown as currently verified', () => {
  const provider: TranslationStatus['provider'] = 'hymt';
  config.TRANSLATION_PROVIDER = provider;
  const stamp = '2026-09-10T00:00:00.000Z';
  const record = {provider, model:'synthetic-other-model', promptVersion:'synthetic-other-rules', verifiedAt:stamp};
  const save = (value: unknown) => db().prepare(`INSERT INTO settings(key,non_secret_value_json,updated_at) VALUES('translationVerification',?,?)
    ON CONFLICT(key) DO UPDATE SET non_secret_value_json=excluded.non_secret_value_json`).run(JSON.stringify(value), now());
  save(record);
  const status = translationStatus();
  assert.equal(status.provider, provider);
  assert.equal(status.local, true);
  assert.equal(status.apiKeyRequired, false);
  assert.equal(status.configured, false);
  assert.equal(status.verifiedAt, stamp);
  assert.equal(status.liveVerified, false);
  save({...record, provider:'unsupported'});
  assert.equal(translationStatus().verifiedAt, null);
  assert.equal(translationStatus().liveVerified, false);
  db().prepare("DELETE FROM settings WHERE key='translationVerification'").run();
});

test('an unregistered Hy-MT request fails the real availability guard without making a job', () => {
  assert.equal(process.env.NODE_ENV, 'production');
  config.TRANSLATION_PROVIDER = 'hymt';
  const source = fixture(), before = counts();
  assert.throws(() => enqueueTranslation({sourceVersionId:source.versionId, blockIds:source.blockIds}),
    error => error instanceof Error && 'code' in error && error.code === 'SETUP_REQUIRED');
  assert.deepEqual(counts(), before);
  assert.equal(forbiddenFetch.mock.callCount(), 0);
});

test('switching to Hy-MT rejects queued OpenAI work before usage reservation or any provider call', async () => {
  const work = paidWork(), before = counts();
  assert.equal(await runOnce(), true);
  assertPaidExecutionRejected(work);
  assert.equal(getJob(work.jobId).status, 'failed');
  assert.deepEqual(counts(), before);
});

test('expired OpenAI recovery preserves completed work and paid uncertainty, then refuses the remaining call', async () => {
  const work = paidWork(['Previously completed synthetic paragraph.', 'Interrupted synthetic paragraph.']);
  const oldSnapshot = JSON.parse(work.items[0].scope_json) as ReturnType<typeof translationSnapshot>;
  const translationId = id();
  db().prepare(`INSERT INTO translations(id,block_id,cache_key,text_ko,provider,model,prompt_version,glossary_version,context_hash,generation_status,validation_status,review_status,created_at)
    VALUES(?,?,?,'합성 보존 결과','openai',?,?,?,?,'ready','passed','unreviewed',?)`).run(translationId, oldSnapshot.blockId, oldSnapshot.cacheKey, oldSnapshot.model, oldSnapshot.promptVersion, oldSnapshot.glossaryVersion, oldSnapshot.contextHash, now());
  db().prepare("UPDATE job_items SET status='completed',result_id=? WHERE id=?").run(translationId, work.items[0].id);
  db().prepare("UPDATE job_items SET status='running' WHERE id=?").run(work.items[1].id);
  db().prepare("UPDATE jobs SET status='running',lease_owner='expired-fixture-owner',lease_until='2000-01-01T00:00:00.000Z' WHERE id=?").run(work.jobId);
  recordUsage({jobId:work.jobId, itemId:work.items[0].id}, 30, 'reported', 7, 11);
  const unfinished = {jobId:work.jobId, itemId:work.items[1].id};
  const reservedId = recordUsage(unfinished, 40, 'reserved'), sentId = recordUsage(unfinished, 40, 'sent');
  const before = counts(), usageBefore = usage();
  const preserved = db().prepare('SELECT * FROM translations WHERE id=?').get(translationId);
  assert.equal(await runOnce(), true);
  assertPaidExecutionRejected(work, 1);
  const completed = db().prepare('SELECT status,result_id FROM job_items WHERE id=?').get(work.items[0].id);
  assert.deepEqual(completed, {status:'completed', result_id:translationId});
  assert.deepEqual(db().prepare('SELECT * FROM translations WHERE id=?').get(translationId), preserved);
  assert.equal(getJob(work.jobId).status, 'partial');
  assert.deepEqual(counts(), before);
  assert.deepEqual(db().prepare('SELECT reservation_status,outcome FROM usage_records WHERE id=?').get(reservedId), {reservation_status:'released', outcome:'interrupted'});
  assert.deepEqual(db().prepare('SELECT reservation_status,outcome FROM usage_records WHERE id=?').get(sentId), {reservation_status:'unknown', outcome:'interrupted'});
  assert.equal(usage().remoteSourceChars, usageBefore.remoteSourceChars - 40);
  assert.equal(usage().unknownCount, usageBefore.unknownCount + 1);
});

for (const previousStatus of ['failed', 'cancelled', 'needs_review'] as const) {
  test(`retrying ${previousStatus} OpenAI work after the Hy-MT switch cannot run the old paid snapshot`, async () => {
    const work = paidWork();
    db().prepare('UPDATE jobs SET status=? WHERE id=?').run(previousStatus === 'needs_review' ? 'failed' : previousStatus, work.jobId);
    db().prepare('UPDATE job_items SET status=? WHERE job_id=?').run(previousStatus, work.jobId);
    const before = counts();
    // The retry API queues the existing snapshot; the worker's production guard
    // must reject it before creating a new paid attempt or choosing a fallback.
    assert.equal(retryJob(work.jobId).status, 'queued');
    assert.equal(await runOnce(), true);
    assertPaidExecutionRejected(work);
    assert.equal(getJob(work.jobId).status, 'failed');
    assert.deepEqual(counts(), before);
  });
}
