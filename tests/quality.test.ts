import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'damodaran-quality-'));
Object.assign(process.env,{APP_ROOT:process.cwd(),DATA_DIR:temporary,NODE_ENV:'test',TRANSLATION_PROVIDER:'openai',TRANSLATION_MODEL:'quality-test-fixture'});
const {db,setupDatabase,closeDb,hash,id,now}=await import('../lib/db');
const {enqueueQualityAssessment,qualityForTranslation}=await import('../lib/quality');
const {setTestQualityProvider}=await import('../lib/quality/runtime');
const {checkMeaningRules,validSpan,validateFindings}=await import('../lib/quality/rules');
const {getTranslationForBlock,setTestTranslationProvider,translationSnapshot}=await import('../lib/translation');
const {saveTranslationReview}=await import('../lib/translation/memory');
const {runOnce,getJob,cancelJob,enqueueTranslation}=await import('../lib/jobs');
setupDatabase();
after(()=>{setTestQualityProvider(undefined);setTestTranslationProvider(undefined);closeDb();const resolved=path.resolve(temporary);assert.ok(resolved.startsWith(path.resolve(os.tmpdir())+path.sep));fs.rmSync(resolved,{recursive:true,force:true});});
function fixture(source='A loan pays interest.',target='대출에는 이자가 발생한다.'){
  const resourceId=id(),versionId=id(),blockId=id(),translationId=id();
  db().prepare(`INSERT INTO resources(id,source_type,title_en,title_ko,kind,format,created_at) VALUES(?,'upload','QUALITY TEST','품질 검사 테스트','본문','html',?)`).run(resourceId,now());
  db().prepare(`INSERT INTO source_versions(id,resource_id,file_hash,original_path,mime,format,byte_size,imported_at,extractor_version,extraction_config_hash,extraction_status) VALUES(?,?,?,'originals/quality-fixture.html','text/html','html',1,?,'test','test','ready')`).run(versionId,resourceId,hash(source),now());
  db().prepare(`INSERT INTO source_blocks(id,source_version_id,sort_order,type,text,source_hash) VALUES(?,?,0,'paragraph',?,?)`).run(blockId,versionId,source,hash(source));
  const snapshot=translationSnapshot(blockId);
  db().prepare(`INSERT INTO translations(id,block_id,cache_key,text_ko,provider,model,prompt_version,glossary_version,context_hash,validation_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,'passed',?)`).run(translationId,blockId,snapshot.cacheKey,target,snapshot.provider,snapshot.model,snapshot.promptVersion,snapshot.glossaryVersion,snapshot.contextHash,now());
  return {resourceId,versionId,blockId,translationId,source,target};
}
const enqueueFailureWarning='의미 검사 예약에 실패했습니다. 번역은 저장했으며 다시 검사할 수 있습니다.';
function usageWithEnqueueFailure(){return {inputTokens:19,outputTokens:11,warnings:['기존 숫자 경고',enqueueFailureWarning],qualityWarning:enqueueFailureWarning,requestId:'keep-request',details:{source:'fixture',attempts:[1,2]}};}
function writeUsage(translationId:string,value:unknown){db().prepare('UPDATE translations SET usage_json=? WHERE id=?').run(JSON.stringify(value),translationId);}
function readUsage(translationId:string){return (db().prepare('SELECT usage_json FROM translations WHERE id=?').get(translationId) as {usage_json:string}).usage_json;}
function workCounts(){return Object.fromEntries(['jobs','job_items','translation_quality_assessments'].map(table=>[table,(db().prepare(`SELECT COUNT(*) n FROM ${table}`).get() as {n:number}).n]));}
async function finish(jobId:string){for(let i=0;i<20&&['queued','running'].includes(getJob(jobId).status);i++)assert.equal(await runOnce(),true);assert.ok(!['queued','running'].includes(getJob(jobId).status));}
test('narrow bilingual rules detect division reversal and percentage-point substitution with exact UTF-16 spans',()=>{
  for(const [source,target,rule] of [
    ['Calculate the time by dividing 84 by the interest rate.','기간은 이자율을 84로 나누어 계산한다.','rate-division-direction'],
    ['Divide 120 by 5.','5를 120으로 나눈다.','numeric-division-direction'],
    ['The analyst increases the ratio by 3%.','분석가는 비율을 3%포인트 올린다.','relative-percent-as-points']]){
    const result=checkMeaningRules(source,target);assert.equal(result[0]?.ruleId,rule);assert.ok(validateFindings(source,target,result));assert.ok(result[0].target);
  }
});
test('correct division, ordinary equity, explicit percentage points and legitimate long bonds do not trigger narrow rules',()=>{
  for(const [source,target] of [
    ['Divide 120 by 5.','120을 5로 나눈다.'],
    ['Divide 5 by 5.','5를 5로 나눈다.'],
    ['Divide 120 by 5, then divide 5 by 120.','120을 5로 나눈 다음 5를 120으로 나눈다.'],
    ['Do not divide 120 by 5.','5를 120으로 나누지 않는다.'],
    ['Divide 120 by 5.','5를 120으로 나누는 것이 아니라 120을 5로 나눈다.'],
    ['The bank supports equity in hiring.','은행은 채용의 공평성을 지지한다.'],
    ['The company balances debt and equity.','회사는 부채와 자기자본의 균형을 맞춘다.'],
    ['The firm uses equity financing and supports fairness.','회사는 지분 조달을 이용하며 공평성을 지지한다.'],
    ['The ratio increases by 3 percentage points.','비율은 3%포인트 증가한다.'],
    ['Treasury bills differ from long-term Treasury bonds.','단기 국채는 장기 국채와 다르다.']])assert.deepEqual(checkMeaningRules(source,target),[]);
});
test('offset verification rejects stale text, out-of-bounds offsets and split surrogate pairs',()=>{
  assert.equal(validSpan('😀금융',{start:2,end:4,text:'금융'}),true);
  assert.equal(validSpan('😀금융',{start:1,end:2,text:'\ude00'}),false);
  assert.equal(validSpan('😀금융',{start:0,end:1,text:'\ud83d'}),false);
  assert.equal(validSpan('금융',{start:0,end:2,text:'투자'}),false);
  assert.equal(validSpan('금융',{start:0,end:3,text:'금융'}),false);
});
test('source/translation compound foreign keys reject unrelated version and block ownership',()=>{
  const a=fixture(),b=fixture();const request=enqueueQualityAssessment(a.translationId);
  assert.throws(()=>db().prepare('UPDATE translation_quality_assessments SET source_version_id=? WHERE id=?').run(b.versionId,request.assessmentId));
  assert.throws(()=>db().prepare('UPDATE translation_quality_assessments SET block_id=? WHERE id=?').run(b.blockId,request.assessmentId));cancelJob(request.jobId!);
});
test('reading never schedules work; explicit requests deduplicate and unavailable model still preserves a detected rule warning',async()=>{
  setTestQualityProvider(undefined);
  const f=fixture('Calculate by dividing 84 by the discount or interest rate.','할인율 또는 이자율을 84로 나누어 계산한다.');
  const before=(db().prepare('SELECT COUNT(*) n FROM jobs').get() as {n:number}).n;
  assert.equal(getTranslationForBlock(f.blockId)?.quality,null);assert.equal((db().prepare('SELECT COUNT(*) n FROM jobs').get() as {n:number}).n,before);
  const first=enqueueQualityAssessment(f.translationId),second=enqueueQualityAssessment(f.translationId);assert.equal(first.jobId,second.jobId);await finish(first.jobId!);
  const quality=qualityForTranslation(f.translationId,f.source,f.target)!;assert.equal(quality.status,'unavailable');assert.equal(quality.risk,'review');assert.equal(quality.score,null);assert.equal(quality.findings[0].ruleId,'rate-division-direction');
  assert.equal(getTranslationForBlock(f.blockId)?.validationStatus,'passed');assert.equal(enqueueQualityAssessment(f.translationId).cached,true);
});
test('newly generated selected translation schedules quality without a second translation call or usage record',async()=>{
  const f=fixture();db().prepare('DELETE FROM translations WHERE id=?').run(f.translationId);
  let calls=0;setTestTranslationProvider(async request=>{calls++;return {data:{segments:request.segments.map(s=>({id:s.id,translatedText:f.target,warnings:[]}))},inputTokens:1,outputTokens:1,requestId:'fixture'};});
  const request=enqueueTranslation({sourceVersionId:f.versionId,blockIds:[f.blockId]});await finish(request.jobIds[0]);
  const translation=getTranslationForBlock(f.blockId)!;assert.equal(translation.quality?.status,'queued');await finish(translation.quality!.jobId!);
  assert.equal(calls,1);assert.equal((db().prepare('SELECT COUNT(*) n FROM usage_records WHERE job_id=?').get(request.jobIds[0]) as {n:number}).n,1);
});
test('raw unbounded QE scores are preserved, low scores warn without fabricated error offsets, high scores remain unreviewed',async()=>{
  let value=-1.75;setTestQualityProvider(async()=>({score:value,findings:[]}));
  const a=fixture();const queued=enqueueQualityAssessment(a.translationId);await finish(queued.jobId!);const low=getTranslationForBlock(a.blockId)!.quality!;
  assert.equal(low.score,-1.75);assert.equal(low.risk,'review');assert.equal(low.findings[0].target,null);
  value=2.4;const b=fixture();await finish(enqueueQualityAssessment(b.translationId).jobId!);const high=getTranslationForBlock(b.blockId)!;assert.equal(high.quality!.risk,'no_findings');assert.equal(high.reviewStatus,'unreviewed');
});
test('assessment failure preserves translation and records failed state',async()=>{
  setTestQualityProvider(async()=>{throw new Error('private diagnostic must not be exposed');});const f=fixture();await finish(enqueueQualityAssessment(f.translationId).jobId!);
  const displayed=getTranslationForBlock(f.blockId)!;assert.equal(displayed.textKo,f.target);assert.equal(displayed.quality!.status,'failed');assert.doesNotMatch(displayed.quality!.message,/private/);
});
test('malformed score and stale returned spans are not accepted',async()=>{
  setTestQualityProvider(async()=>({score:NaN,findings:[]}));const a=fixture();await finish(enqueueQualityAssessment(a.translationId).jobId!);assert.equal(getTranslationForBlock(a.blockId)!.quality!.status,'failed');
  setTestQualityProvider(async()=>({score:1,findings:[{category:'word_sense',severity:'major',reason:'fixture',detector:'qe',target:{start:0,end:2,text:'틀림'},source:null}]}));const b=fixture();await finish(enqueueQualityAssessment(b.translationId).jobId!);assert.equal(getTranslationForBlock(b.blockId)!.quality!.status,'failed');
});
test('a review saved during evaluation takes priority and retains old machine assessment history',async()=>{
  const f=fixture();setTestQualityProvider(async()=>{saveTranslationReview({translationId:f.translationId,expectedReviewId:null,textKo:'대출에는 이자가 붙는다.'});return {score:-5,findings:[]};});
  const request=enqueueQualityAssessment(f.translationId);await finish(request.jobId!);const displayed=getTranslationForBlock(f.blockId)!;
  assert.equal(displayed.reviewStatus,'user_reviewed');assert.equal(displayed.textKo,'대출에는 이자가 붙는다.');assert.equal(displayed.quality,null);
  assert.equal((db().prepare('SELECT status FROM translation_quality_assessments WHERE id=?').get(request.assessmentId) as {status:string}).status,'stale');
  assert.equal(enqueueQualityAssessment(f.translationId).jobId,null);
});
test('late evaluation for changed machine text is stale and never overwrites the new text',async()=>{
  const f=fixture();setTestQualityProvider(async()=>{db().prepare('UPDATE translations SET text_ko=? WHERE id=?').run('변경된 번역이다.',f.translationId);return {score:-1,findings:[]};});
  const request=enqueueQualityAssessment(f.translationId);await finish(request.jobId!);assert.equal(getTranslationForBlock(f.blockId)!.textKo,'변경된 번역이다.');assert.equal(getTranslationForBlock(f.blockId)!.quality,null);
  assert.equal((db().prepare('SELECT status FROM translation_quality_assessments WHERE id=?').get(request.assessmentId) as {status:string}).status,'stale');
});
test('queued cancellation and expired lease recovery preserve assessment ownership and use no paid records',async()=>{
  setTestQualityProvider(async()=>({score:1,findings:[]}));const a=fixture();const canceled=enqueueQualityAssessment(a.translationId);cancelJob(canceled.jobId!);assert.equal(getTranslationForBlock(a.blockId)!.quality!.status,'cancelled');
  const b=fixture();const request=enqueueQualityAssessment(b.translationId);db().prepare(`UPDATE jobs SET status='running',lease_owner='old-owner',lease_until='2000-01-01T00:00:00Z' WHERE id=?`).run(request.jobId);db().prepare(`UPDATE job_items SET status='running' WHERE job_id=?`).run(request.jobId);await finish(request.jobId!);assert.equal(getTranslationForBlock(b.blockId)!.quality!.status,'completed');
  assert.equal((db().prepare('SELECT COUNT(*) n FROM usage_records WHERE job_id=?').get(request.jobId) as {n:number}).n,0);
});
test('changed evaluator identity makes old assessment stale and creates a distinct cache entry',async()=>{
  setTestQualityProvider(undefined);const f=fixture();await finish(enqueueQualityAssessment(f.translationId).jobId!);
  setTestQualityProvider(async()=>({score:1,findings:[]}));assert.equal(getTranslationForBlock(f.blockId)!.quality!.status,'stale');const next=enqueueQualityAssessment(f.translationId);assert.equal(next.cached,false);await finish(next.jobId!);assert.equal(getTranslationForBlock(f.blockId)!.quality!.status,'completed');
});

test('changed neighboring context invalidates saved assessment without evaluating on read',async()=>{
  setTestQualityProvider(async()=>({score:1,findings:[]}));const f=fixture();await finish(enqueueQualityAssessment(f.translationId).jobId!);
  const before=(db().prepare('SELECT COUNT(*) n FROM jobs').get() as {n:number}).n;
  db().prepare('UPDATE resources SET title_en=? WHERE id=?').run('Changed title context',f.resourceId);
  assert.equal(getTranslationForBlock(f.blockId)!.quality!.status,'stale');
  assert.equal((db().prepare('SELECT COUNT(*) n FROM jobs').get() as {n:number}).n,before);
  const next=enqueueQualityAssessment(f.translationId);assert.equal(next.cached,false);await finish(next.jobId!);assert.equal(getTranslationForBlock(f.blockId)!.quality!.status,'completed');
});

test('cancelled running assessment recovered after a lost worker does not remain stuck in reader polling',async()=>{
  let calls=0;setTestQualityProvider(async()=>{calls++;return {score:1,findings:[]};});
  const f=fixture(),request=enqueueQualityAssessment(f.translationId);
  db().prepare("UPDATE jobs SET status='running',lease_owner='dead-worker',lease_until='2000-01-01T00:00:00Z',cancel_requested_at=? WHERE id=?").run(now(),request.jobId);
  db().prepare("UPDATE job_items SET status='running' WHERE job_id=?").run(request.jobId);
  db().prepare("UPDATE translation_quality_assessments SET status='running' WHERE id=?").run(request.assessmentId);
  await finish(request.jobId!);assert.equal(getJob(request.jobId!).status,'cancelled');assert.equal(getTranslationForBlock(f.blockId)!.quality!.status,'cancelled');assert.equal(calls,0);
  const retry=enqueueQualityAssessment(f.translationId);assert.equal(retry.cached,false);await finish(retry.jobId!);assert.equal(getTranslationForBlock(f.blockId)!.quality!.status,'completed');
});

test('cancelling translation during inference preserves the returned text without launching a new quality task',async()=>{
  const f=fixture('The invoice contains a fee.','청구서에는 수수료가 포함되어 있다.');db().prepare('DELETE FROM translations WHERE id=?').run(f.translationId);let translationJob='';
  setTestTranslationProvider(async request=>{cancelJob(translationJob);return {data:{segments:request.segments.map(s=>({id:s.id,translatedText:f.target,warnings:[]}))},inputTokens:1,outputTokens:1,requestId:'cancelled-fixture'};});
  const queued=enqueueTranslation({sourceVersionId:f.versionId,blockIds:[f.blockId]});translationJob=queued.jobIds[0];await finish(translationJob);
  const displayed=getTranslationForBlock(f.blockId)!;assert.equal(displayed.textKo,f.target);assert.equal(displayed.quality,null);
});

test('a cancelling group cannot swallow a new block assessment or collide with the next group generation',async()=>{
  setTestQualityProvider(async()=>({score:1,findings:[]}));const f=fixture(),first=enqueueQualityAssessment(f.translationId);
  db().prepare("UPDATE jobs SET status='running',cancel_requested_at=?,lease_owner='old',lease_until='2000-01-01T00:00:00Z' WHERE id=?").run(now(),first.jobId);
  db().prepare("UPDATE job_items SET status='running' WHERE job_id=?").run(first.jobId);
  db().prepare("UPDATE translation_quality_assessments SET status='running' WHERE id=?").run(first.assessmentId);
  assert.throws(()=>enqueueQualityAssessment(f.translationId),/취소 처리 중/);
  const secondBlock=id(),secondTranslation=id(),source='The bond pays a coupon.';
  db().prepare("INSERT INTO source_blocks(id,source_version_id,sort_order,type,text,source_hash) VALUES(?,?,1,'paragraph',?,?)").run(secondBlock,f.versionId,source,hash(source));
  const snapshot=translationSnapshot(secondBlock);
  db().prepare("INSERT INTO translations(id,block_id,cache_key,text_ko,provider,model,prompt_version,glossary_version,context_hash,validation_status,created_at) VALUES(?,?,?,'채권은 이자를 지급한다.',?,?,?,?,?,'passed',?)").run(secondTranslation,secondBlock,snapshot.cacheKey,snapshot.provider,snapshot.model,snapshot.promptVersion,snapshot.glossaryVersion,snapshot.contextHash,now());
  const next=enqueueQualityAssessment(secondTranslation);assert.notEqual(next.jobId,first.jobId);assert.equal(enqueueQualityAssessment(secondTranslation).jobId,next.jobId);
  await finish(first.jobId!);await finish(next.jobId!);assert.equal(getTranslationForBlock(secondBlock)!.quality!.status,'completed');
});

test('successful quality enqueue removes only its previous failure marker and preserves all other usage and warnings',()=>{
  setTestQualityProvider(undefined);const f=fixture(),usage=usageWithEnqueueFailure();writeUsage(f.translationId,usage);
  const queued=enqueueQualityAssessment(f.translationId);assert.equal(queued.cached,false);
  const {qualityWarning:removed,...expected}=usage;assert.equal(removed,enqueueFailureWarning);
  assert.deepEqual(JSON.parse(readUsage(f.translationId)),expected);
  assert.deepEqual(getTranslationForBlock(f.blockId)!.warnings,usage.warnings);cancelJob(queued.jobId!);
  const other=fixture(),unrelated={...usage,qualityWarning:'다른 의미 검사 경고'};writeUsage(other.translationId,unrelated);
  const another=enqueueQualityAssessment(other.translationId);assert.deepEqual(JSON.parse(readUsage(other.translationId)),unrelated);cancelJob(another.jobId!);
});

test('queued and completed quality cache reuse clear a stale enqueue failure without adding work or changing usage',async()=>{
  setTestQualityProvider(async()=>({score:1,findings:[]}));const f=fixture(),first=enqueueQualityAssessment(f.translationId);
  const usage=usageWithEnqueueFailure(),{qualityWarning:removed,...expected}=usage;assert.equal(removed,enqueueFailureWarning);
  writeUsage(f.translationId,usage);const before=workCounts(),active=enqueueQualityAssessment(f.translationId);
  assert.deepEqual(active,{assessmentId:first.assessmentId,jobId:first.jobId,cached:true});assert.deepEqual(workCounts(),before);assert.deepEqual(JSON.parse(readUsage(f.translationId)),expected);
  await finish(first.jobId!);writeUsage(f.translationId,usage);const completed=enqueueQualityAssessment(f.translationId);
  assert.deepEqual(completed,{assessmentId:first.assessmentId,jobId:null,cached:true});assert.deepEqual(workCounts(),before);assert.deepEqual(JSON.parse(readUsage(f.translationId)),expected);
});

test('reviewed translation cache reuse clears the old enqueue failure without scheduling automatic assessment',()=>{
  const f=fixture();saveTranslationReview({translationId:f.translationId,expectedReviewId:null,textKo:'대출에는 이자가 붙는다.'});
  const usage=usageWithEnqueueFailure(),{qualityWarning:removed,...expected}=usage;assert.equal(removed,enqueueFailureWarning);writeUsage(f.translationId,usage);
  const before=workCounts();assert.deepEqual(enqueueQualityAssessment(f.translationId),{assessmentId:null,jobId:null,cached:true});
  assert.deepEqual(workCounts(),before);assert.deepEqual(JSON.parse(readUsage(f.translationId)),expected);
});

test('enqueue insertion or warning cleanup failure rolls back all new work and preserves the original usage bytes',()=>{
  for(const failure of ['item','cleanup']){
    const f=fixture(),usage=usageWithEnqueueFailure();writeUsage(f.translationId,usage);const before=workCounts(),original=readUsage(f.translationId);
    const event=failure==='item'?"INSERT ON job_items WHEN NEW.work_key LIKE 'quality:%'":"UPDATE OF usage_json ON translations";
    db().exec(`CREATE TEMP TRIGGER fail_quality_enqueue BEFORE ${event} BEGIN SELECT RAISE(ABORT,'quality enqueue fixture failure'); END`);
    try{assert.throws(()=>enqueueQualityAssessment(f.translationId),/quality enqueue fixture failure/);assert.deepEqual(workCounts(),before);assert.equal(readUsage(f.translationId),original);}
    finally{db().exec('DROP TRIGGER fail_quality_enqueue');}
  }
});

test('rejected cache reuse while an assessment is cancelling preserves the previous enqueue failure marker',()=>{
  const f=fixture(),request=enqueueQualityAssessment(f.translationId);writeUsage(f.translationId,usageWithEnqueueFailure());const original=readUsage(f.translationId),before=workCounts();
  db().prepare('UPDATE jobs SET cancel_requested_at=? WHERE id=?').run(now(),request.jobId);
  assert.throws(()=>enqueueQualityAssessment(f.translationId),/취소 처리 중/);assert.equal(readUsage(f.translationId),original);assert.deepEqual(workCounts(),before);cancelJob(request.jobId!);
});
