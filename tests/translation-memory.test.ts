import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';

const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'damodaran-memory-'));
Object.assign(process.env,{DATA_DIR:temporary,NODE_ENV:'test',TRANSLATION_PROVIDER:'argos',APP_ROOT:process.cwd()});
const {db,setupDatabase,closeDb,hash,id,now}=await import('../lib/db');
const {seed}=await import('../lib/seed');
const {translationSnapshot,getTranslationForBlock,setTestTranslationProvider}=await import('../lib/translation');
const {saveTranslationReview,reviewedTranslationPairs,translationReviewHistory}=await import('../lib/translation/memory');
const {selectTranslationGlossary}=await import('../lib/translation/glossary');
const {enqueueTranslation,runOnce}=await import('../lib/jobs');
const {backup,restore}=await import('../lib/backup');
setupDatabase();seed();
after(()=>{setTestTranslationProvider(undefined);closeDb();fs.rmSync(temporary,{recursive:true,force:true});});
function fixture(text:string,title='Memory test',type='paragraph'){
  const resourceId=id(),versionId=id(),blockId=id();const bytes=Buffer.from(text),relative=`originals/${versionId}.txt`;
  fs.writeFileSync(path.join(temporary,relative),bytes);
  db().prepare("INSERT INTO resources(id,source_type,title_en,title_ko,kind,format,created_at) VALUES(?,'upload',?,'메모리 시험','본문','html',?)").run(resourceId,title,now());
  db().prepare("INSERT INTO source_versions(id,resource_id,file_hash,original_path,mime,format,byte_size,imported_at,extractor_version,extraction_config_hash,extraction_status) VALUES(?,?,?,?,'text/html','html',?,?,'test','test','ready')").run(versionId,resourceId,hash(bytes),relative,bytes.length,now());
  db().prepare('INSERT INTO source_blocks(id,source_version_id,sort_order,type,text,source_hash) VALUES(?,?,0,?,?,?)').run(blockId,versionId,type,text,hash(text));
  return {resourceId,versionId,blockId};
}
async function translated(text:string,title?:string,type?:string){
  const f=fixture(text,title,type);setTestTranslationProvider(async input=>({data:{segments:input.segments.map(s=>({id:s.id,translatedText:'시험 번역 '+s.text,warnings:[]}))},inputTokens:null,outputTokens:null,requestId:null}));
  enqueueTranslation({sourceVersionId:f.versionId,blockIds:[f.blockId]});await runOnce();return {...f,translation:getTranslationForBlock(f.blockId)!};
}

test('glossary uses word boundaries and hashes aliases, target and revision',()=>{
  const term={id:'custom',term_en:'Discount rate',term_ko:'할인율',acronym:null,aliases_json:'["required discount rate"]',notes:null,revision:1};
  assert.equal(selectTranslationGlossary('discount rateable','',[term]).glossary.length,0);
  const before=selectTranslationGlossary('The discount rate changes.','',[term]);assert.ok(before.glossary.some(rule=>rule.source.toLowerCase()==='discount rate'));
  const after=selectTranslationGlossary('The discount rate changes.','',[{...term,aliases_json:'["required discount rate","hurdle rate"]'}]);assert.notEqual(before.glossaryVersion,after.glossaryVersion);
  assert.notEqual(before.glossaryVersion,selectTranslationGlossary('The discount rate changes.','',[{...term,revision:2}]).glossaryVersion);
  assert.notEqual(before.glossaryVersion,selectTranslationGlossary('The discount rate changes.','',[{...term,term_ko:'다른 번역'}]).glossaryVersion);
  const acronymTerm={...term,id:'test-pv',term_en:'Present Value',term_ko:'현재가치',acronym:'PV',aliases_json:'[]'};
  const acronym=selectTranslationGlossary('PV is a formula variable.','',[acronymTerm]).glossary;
  assert.ok(acronym.some(rule=>rule.mode==='exact'&&rule.source==='PV'));
  assert.ok(!acronym.some(rule=>rule.mode==='phrase'&&rule.aliases.includes('PV')));
});

test('review preserves the machine result and keeps immutable corrections with conflict checks',async()=>{
  const f=await translated('The present value is 100 USD.');
  const first=saveTranslationReview({translationId:f.translation.id,textKo:'현재가치는 100 USD이다.',expectedReviewId:null});
  assert.equal(first.reviewStatus,'user_reviewed');assert.equal(first.current,true);assert.equal(first.origin,'user');
  assert.equal((db().prepare('SELECT text_ko FROM translations WHERE id=?').get(f.translation.id) as {text_ko:string}).text_ko,f.translation.textKo);
  assert.throws(()=>saveTranslationReview({translationId:f.translation.id,textKo:'현재가치는 100 USD다.',expectedReviewId:null}),/다른 창/);
  const second=saveTranslationReview({translationId:f.translation.id,textKo:'현재가치는 100 USD다.',expectedReviewId:first.reviewId});
  assert.notEqual(second.reviewId,first.reviewId);assert.equal(translationReviewHistory(f.translation.id).length,2);
  saveTranslationReview({translationId:f.translation.id,textKo:second.textKo,expectedReviewId:second.reviewId});assert.equal(translationReviewHistory(f.translation.id).length,2);
  closeDb();assert.equal(getTranslationForBlock(f.blockId)?.textKo,second.textKo);
  assert.equal(reviewedTranslationPairs().filter(pair=>pair.source==='The present value is 100 USD.').length,1);
});

test('review refuses damaged numbers, formula changes, empty input and tables',async()=>{
  const f=await translated('Cash is -5% of USD 100.');
  assert.throws(()=>saveTranslationReview({translationId:f.translation.id,textKo:'현금은 USD 100의 5%다.'}),/숫자/);
  assert.throws(()=>saveTranslationReview({translationId:f.translation.id,textKo:' '}));
  assert.throws(()=>saveTranslationReview({translationId:'unknown',textKo:'번역'}),/찾을/);
  const equation=await translated('PV = CF / (1+r)^n');assert.throws(()=>saveTranslationReview({translationId:equation.translation.id,textKo:'PV = XX / (1+r)^n'}),/수식/);
  const table=await translated('Column value','Table','table');assert.throws(()=>saveTranslationReview({translationId:table.translation.id,textKo:'표'}),/표는/);
});

test('an exact reviewed pair is reused across source versions without a translation call',async()=>{
  const f=await translated('Operating income is 200.','Reuse case');
  const reviewed=saveTranslationReview({translationId:f.translation.id,textKo:'영업이익은 200이다.'});
  const copy=fixture('Operating income is 200.','Reuse case');let calls=0;setTestTranslationProvider(async()=>{calls++;throw new Error('No inference expected');});
  const before=(db().prepare('SELECT COUNT(*) n FROM usage_records').get() as {n:number}).n;
  const request=enqueueTranslation({sourceVersionId:copy.versionId,blockIds:[copy.blockId]});
  assert.equal(request.cached,1);assert.deepEqual(request.jobIds,[]);assert.equal(calls,0);
  const result=getTranslationForBlock(copy.blockId)!;assert.equal(result.textKo,reviewed.textKo);assert.equal(result.origin,'memory');assert.equal(result.reviewStatus,'user_reviewed');assert.equal(result.current,true);
  assert.equal((db().prepare('SELECT COUNT(*) n FROM usage_records').get() as {n:number}).n,before);
  const changed=fixture('Operating income is 201.','Reuse case');assert.equal(enqueueTranslation({sourceVersionId:changed.versionId,blockIds:[changed.blockId]}).cached,0);
  const context=fixture('Operating income is 200.','Different context');assert.equal(enqueueTranslation({sourceVersionId:context.versionId,blockIds:[context.blockId]}).cached,0);
  db().prepare("UPDATE jobs SET status='cancelled' WHERE status='queued'").run();db().prepare("UPDATE job_items SET status='cancelled' WHERE status='queued'").run();
});

test('a changed glossary keeps prior reviews but prevents automatic reuse',async()=>{
  const f=await translated('The discount rate is 5%.','Glossary case');saveTranslationReview({translationId:f.translation.id,textKo:'할인율은 5%다.'});
  const previous=translationSnapshot(f.blockId).glossaryVersion;
  db().prepare("UPDATE glossary_terms SET revision=revision+1 WHERE id='G03'").run();
  assert.notEqual(translationSnapshot(f.blockId).glossaryVersion,previous);assert.equal(getTranslationForBlock(f.blockId)?.current,false);
  const copy=fixture('The discount rate is 5%.','Glossary case');assert.equal(enqueueTranslation({sourceVersionId:copy.versionId,blockIds:[copy.blockId]}).cached,0);
  db().prepare("UPDATE jobs SET status='cancelled' WHERE status='queued'").run();db().prepare("UPDATE job_items SET status='cancelled' WHERE status='queued'").run();
});

test('reviewed pairs and complete correction history survive backup and restore',async()=>{
  const result=await backup(),destination=path.join(temporary,'restored');restore(result.path,destination);
  const copied=new Database(path.join(destination,'library.sqlite'),{readonly:true});
  try{for(const table of ['translation_reviews','translation_review_links'])assert.equal((copied.prepare(`SELECT COUNT(*) n FROM ${table}`).get() as {n:number}).n,(db().prepare(`SELECT COUNT(*) n FROM ${table}`).get() as {n:number}).n);assert.deepEqual(copied.pragma('foreign_key_check'),[]);}finally{copied.close();}
});

test('a review saved during inference cannot be overwritten by the late machine result',async()=>{
  const f=await translated('Cash is 300.','Concurrent review');
  db().prepare("UPDATE translations SET validation_status='needs_review' WHERE id=?").run(f.translation.id);
  setTestTranslationProvider(async input=>{
    saveTranslationReview({translationId:f.translation.id,textKo:'현금은 300이다.'});
    return {data:{segments:input.segments.map(s=>({id:s.id,translatedText:'늦게 도착한 번역 '+s.text,warnings:[]}))},inputTokens:null,outputTokens:null,requestId:null};
  });
  enqueueTranslation({sourceVersionId:f.versionId,blockIds:[f.blockId]});await runOnce();
  assert.equal(getTranslationForBlock(f.blockId)?.textKo,'현금은 300이다.');
  assert.equal((db().prepare('SELECT text_ko FROM translations WHERE id=?').get(f.translation.id) as {text_ko:string}).text_ko,f.translation.textKo);
});

test('editing an older translation after a new current result arrives returns a conflict',async()=>{
  const f=await translated('Revenue is 400.','Old context');
  db().prepare('UPDATE resources SET title_en=? WHERE id=?').run('Updated context',f.resourceId);
  enqueueTranslation({sourceVersionId:f.versionId,blockIds:[f.blockId]});await runOnce();
  const current=getTranslationForBlock(f.blockId)!;assert.notEqual(current.id,f.translation.id);
  assert.throws(()=>saveTranslationReview({translationId:f.translation.id,textKo:'매출액은 400이다.'}),error=>error instanceof Error&&'status' in error&&error.status===409);
  assert.equal(getTranslationForBlock(f.blockId)?.id,current.id);assert.equal(translationReviewHistory(f.translation.id).length,0);
});
