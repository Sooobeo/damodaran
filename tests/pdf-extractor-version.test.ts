import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'damodaran-pdf-version-'));
Object.assign(process.env,{DATA_DIR:temporary,NODE_ENV:'test',APP_ROOT:process.cwd(),TRANSLATION_PROVIDER:'openai',TRANSLATION_MODEL:'fixture-no-inference',OPENAI_API_KEY:''});
const {db,setupDatabase,closeDb,hash,id,now}=await import('../lib/db');
const {dataPath,config}=await import('../lib/config');
const {uploadOriginal,EXTRACTOR_VERSION,PDF_EXTRACTOR_V1,PDF_EXTRACTOR_VERSION,extractorVersionFor,extractionConfigHash}=await import('../lib/sources');
const {extractPdf}=await import('../lib/extraction');
const {enqueueUpload,runOnce,getJob}=await import('../lib/jobs');
const {translationSnapshot,getTranslationForBlock}=await import('../lib/translation');
setupDatabase();
after(()=>{
  closeDb();const target=path.resolve(temporary),tempRoot=path.resolve(os.tmpdir());
  assert.ok(target.startsWith(tempRoot+path.sep)&&path.dirname(target)===tempRoot);
  assert.ok(path.basename(target).startsWith('damodaran-pdf-version-'));
  fs.rmSync(target,{recursive:true,force:true});
});
const first='The amount invested was 100 USD before the business';
const second='expanded its operations during 2026.';
function pdfWithStream(stream:string){
  const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'];
  objects.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
  let text='%PDF-1.4\n';const offsets=[0];
  objects.forEach((value,index)=>{offsets.push(Buffer.byteLength(text));text+=`${index+1} 0 obj\n${value}\nendobj\n`;});
  const xref=Buffer.byteLength(text);text+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`;
  offsets.slice(1).forEach(offset=>{text+=`${String(offset).padStart(10,'0')} 00000 n \n`;});
  return Buffer.from(text+`trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
}
function simplePdf(){return pdfWithStream(`BT /F1 12 Tf 60 700 Td (${first}) Tj 0 -14 Td (${second}) Tj ET`);}
const listTexts=['1. The delivery status can be','1. Ready after dispatch.','2. Delayed during inspection.','3. Closed on arrival.'];
function listPdf(){return pdfWithStream(`BT /F1 12 Tf 60 700 Td (${listTexts[0]}) Tj /F1 10 Tf 24 -15 Td (${listTexts[1]}) Tj 0 -12 Td (${listTexts[2]}) Tj 0 -12 Td (${listTexts[3]}) Tj ET`);}
function cloneVersion(existing:string,version:string,extractor:string){
  db().prepare(`INSERT INTO source_versions(id,resource_id,file_hash,original_path,mime,format,byte_size,imported_at,extractor_version,extraction_config_hash,extraction_status)
    SELECT ?,resource_id,file_hash,original_path,mime,format,byte_size,imported_at,?,?,'queued' FROM source_versions WHERE id=?`).run(version,extractor,[EXTRACTOR_VERSION,PDF_EXTRACTOR_V1,PDF_EXTRACTOR_VERSION].includes(extractor)?extractionConfigHash('pdf',extractor):hash(extractor),existing);
}

test('PDF identity is format-specific and legacy direct calls are unchanged',async()=>{
  assert.equal(extractorVersionFor('pdf'),PDF_EXTRACTOR_VERSION);assert.equal(extractorVersionFor('html'),EXTRACTOR_VERSION);
  assert.equal(PDF_EXTRACTOR_VERSION,'pdf-paragraphs-v2');assert.equal(PDF_EXTRACTOR_V1,'pdf-paragraphs-v1');
  assert.equal(extractionConfigHash('html'),extractionConfigHash());assert.notEqual(extractionConfigHash('pdf'),extractionConfigHash());
  for(const extractor of [EXTRACTOR_VERSION,PDF_EXTRACTOR_V1]){
    assert.equal(extractionConfigHash('pdf',extractor),hash(JSON.stringify({maxPdfPages:config.MAX_PDF_PAGES,blockChars:6000,extractor})));
  }
  assert.notEqual(extractionConfigHash('pdf'),extractionConfigHash('pdf',PDF_EXTRACTOR_V1));
  const bytes=simplePdf();assert.equal((await extractPdf(bytes)).blocks.length,2);
  assert.deepEqual((await extractPdf(bytes,PDF_EXTRACTOR_VERSION)).blocks.map(b=>b.text),[first+' '+second]);
  assert.deepEqual((await extractPdf(bytes,PDF_EXTRACTOR_V1)).blocks.map(b=>b.text),[first+' '+second]);
  await assert.rejects(extractPdf(bytes,'unsupported-pdf-v99'),/추출기 버전/);
});

test('queued v1 preserves its exact list blocks, config and records while new v2 groups the complete list',async()=>{
  const bytes=listPdf(),uploaded=uploadOriginal(bytes,'list-fixture.pdf'),v1=id();
  cloneVersion(uploaded.versionId,v1,PDF_EXTRACTOR_V1);
  const oldJob=enqueueUpload(uploaded.resourceId,v1);await runOnce();assert.equal(getJob(oldJob.id).status,'completed');
  const oldVersion=db().prepare('SELECT * FROM source_versions WHERE id=?').get(v1) as Record<string,any>;
  const oldBlocks=db().prepare('SELECT * FROM source_blocks WHERE source_version_id=? ORDER BY sort_order').all(v1) as Record<string,any>[];
  assert.deepEqual(oldBlocks.map(b=>b.text),listTexts);
  assert.ok(oldBlocks.every(b=>JSON.parse(b.structure_json).pdfParagraphVersion===1));
  assert.equal(oldVersion.extraction_config_hash,extractionConfigHash('pdf',PDF_EXTRACTOR_V1));
  const block=oldBlocks[0],stamp=now(),snapshot=translationSnapshot(block.id),translation=id(),review=id();
  db().prepare("INSERT INTO translations(id,block_id,cache_key,text_ko,provider,model,prompt_version,glossary_version,context_hash,validation_status,created_at) VALUES(?,?,?,'기존 v1 번역','openai',?,?,?,?,'passed',?)").run(translation,block.id,snapshot.cacheKey,snapshot.model,snapshot.promptVersion,snapshot.glossaryVersion,snapshot.contextHash,stamp);
  db().prepare("INSERT INTO translation_reviews(id,translation_id,source_text,source_hash,context_hash,glossary_version,block_type,text_ko,created_at) VALUES(?,?,?,?,?,?,'paragraph','기존 v1 검수',?)").run(review,translation,block.text,block.source_hash,snapshot.contextHash,snapshot.glossaryVersion,stamp);
  db().prepare('INSERT INTO translation_review_links VALUES(?,?)').run(translation,review);
  db().prepare("INSERT INTO notes(id,resource_id,source_version_id,block_id,page_index,quote,text,updated_at) VALUES(?,?,?,?,0,?,'기존 v1 메모',?)").run(id(),uploaded.resourceId,v1,block.id,block.text,stamp);
  db().prepare('INSERT INTO bookmarks(id,resource_id,source_version_id,block_id,page_index,location_key,created_at) VALUES(?,?,?,?,0,?,?)').run(id(),uploaded.resourceId,v1,block.id,'v1-list-bookmark',stamp);
  db().prepare("INSERT INTO reading_positions VALUES(?,?,?,?,0.4,'parallel',?)").run(uploaded.resourceId,v1,block.id,0,stamp);
  const preserve=()=>Object.fromEntries(['notes','bookmarks','reading_positions','translations','translation_reviews','translation_review_links','settings','learning_progress','resource_preferences'].map(table=>[table,db().prepare(`SELECT * FROM ${table} ORDER BY rowid`).all()]));
  const previous=preserve(),nextJob=enqueueUpload(uploaded.resourceId,uploaded.versionId);await runOnce();assert.equal(getJob(nextJob.id).status,'completed');
  const fresh=db().prepare('SELECT * FROM source_blocks WHERE source_version_id=? ORDER BY sort_order').all(uploaded.versionId) as Record<string,any>[];
  assert.equal(fresh.length,1);assert.equal(fresh[0].text,listTexts.join('\n'));assert.equal(JSON.parse(fresh[0].structure_json).pdfParagraphVersion,2);
  assert.equal(JSON.parse(fresh[0].structure_json).lineCount,4);assert.equal(fresh[0].source_hash,hash(listTexts.join('\n')));
  assert.deepEqual(db().prepare('SELECT * FROM source_versions WHERE id=?').get(v1),oldVersion);
  assert.deepEqual(db().prepare('SELECT * FROM source_blocks WHERE source_version_id=? ORDER BY sort_order').all(v1),oldBlocks);
  assert.deepEqual(preserve(),previous);assert.deepEqual(fs.readFileSync(dataPath(oldVersion.original_path)),bytes);
  assert.notEqual(translationSnapshot(fresh[0].id).cacheKey,snapshot.cacheKey);assert.equal(getTranslationForBlock(fresh[0].id),null);
  for(const version of [v1,uploaded.versionId]){const job=enqueueUpload(uploaded.resourceId,version);await runOnce();assert.equal(getJob(job.id).status,'completed');}
  assert.deepEqual(db().prepare('SELECT * FROM source_blocks WHERE source_version_id=? ORDER BY sort_order').all(v1),oldBlocks);
  assert.deepEqual(db().prepare('SELECT * FROM source_blocks WHERE source_version_id=? ORDER BY sort_order').all(uploaded.versionId),fresh);
  assert.deepEqual(preserve(),previous);assert.equal((db().prepare('SELECT COUNT(*) n FROM usage_records').get() as {n:number}).n,0);
  assert.deepEqual(db().prepare('PRAGMA foreign_key_check').all(),[]);
});

test('queued legacy version uses legacy rules; new extraction preserves original and personal history',async()=>{
  const bytes=simplePdf(),uploaded=uploadOriginal(bytes,'fixture.pdf'),legacy=id();
  const original=db().prepare('SELECT * FROM source_versions WHERE id=?').get(uploaded.versionId) as Record<string,any>;
  assert.equal(original.extractor_version,PDF_EXTRACTOR_VERSION);assert.equal(original.extraction_config_hash,extractionConfigHash('pdf'));
  cloneVersion(uploaded.versionId,legacy,EXTRACTOR_VERSION);
  const oldJob=enqueueUpload(uploaded.resourceId,legacy);await runOnce();assert.equal(getJob(oldJob.id).status,'completed');
  const blocks=db().prepare('SELECT * FROM source_blocks WHERE source_version_id=? ORDER BY sort_order').all(legacy) as Record<string,any>[];
  assert.deepEqual(blocks.map(b=>b.text),[first,second]);
  const block=blocks[0],stamp=now(),snapshot=translationSnapshot(block.id),translation=id(),review=id();
  db().prepare("INSERT INTO translations(id,block_id,cache_key,text_ko,provider,model,prompt_version,glossary_version,context_hash,validation_status,created_at) VALUES(?,?,?,'테스트 전용 기존 번역','openai',?,?,?,?,'passed',?)").run(translation,block.id,snapshot.cacheKey,snapshot.model,snapshot.promptVersion,snapshot.glossaryVersion,snapshot.contextHash,stamp);
  db().prepare("INSERT INTO translation_reviews(id,translation_id,source_text,source_hash,context_hash,glossary_version,block_type,text_ko,created_at) VALUES(?,?,?,?,?,?,'paragraph','테스트 전용 검수 기록',?)").run(review,translation,block.text,block.source_hash,snapshot.contextHash,snapshot.glossaryVersion,stamp);
  db().prepare('INSERT INTO translation_review_links VALUES(?,?)').run(translation,review);
  db().prepare("INSERT INTO notes(id,resource_id,source_version_id,block_id,page_index,quote,text,updated_at) VALUES(?,?,?,?,0,?,'보존할 메모',?)").run(id(),uploaded.resourceId,legacy,block.id,block.text,stamp);
  db().prepare('INSERT INTO bookmarks(id,resource_id,source_version_id,block_id,page_index,location_key,created_at) VALUES(?,?,?,?,0,?,?)').run(id(),uploaded.resourceId,legacy,block.id,'fixture-bookmark',stamp);
  db().prepare("INSERT INTO reading_positions VALUES(?,?,?,?,0.4,'parallel',?)").run(uploaded.resourceId,legacy,block.id,0,stamp);
  db().prepare("INSERT INTO settings VALUES('pdf-fixture','17',?)").run(stamp);
  db().prepare("INSERT INTO modules VALUES('pdf-fixture','pdf-fixture',99,'검증용','질문','[]','[]')").run();
  db().prepare("INSERT INTO learning_progress VALUES('pdf-fixture','completed',?,?)").run(stamp,stamp);
  db().prepare('INSERT INTO resource_preferences VALUES(?,?,?)').run(uploaded.resourceId,'핵심',stamp);
  const tables=['notes','bookmarks','reading_positions','translations','translation_reviews','translation_review_links','settings','learning_progress','resource_preferences'];
  const preserve=()=>Object.fromEntries(tables.map(table=>[table,db().prepare(`SELECT * FROM ${table} ORDER BY rowid`).all()]));
  const previous=preserve(),oldVersion=db().prepare('SELECT * FROM source_versions WHERE id=?').get(legacy);
  const nextJob=enqueueUpload(uploaded.resourceId,uploaded.versionId);await runOnce();assert.equal(getJob(nextJob.id).status,'completed');
  const fresh=db().prepare('SELECT * FROM source_blocks WHERE source_version_id=? ORDER BY sort_order').all(uploaded.versionId) as Record<string,any>[];
  assert.equal(fresh.length,1);assert.equal(fresh[0].text,first+' '+second);
  assert.deepEqual(db().prepare('SELECT * FROM source_versions WHERE id=?').get(legacy),oldVersion);
  assert.deepEqual(db().prepare('SELECT * FROM source_blocks WHERE source_version_id=? ORDER BY sort_order').all(legacy),blocks);
  assert.deepEqual(preserve(),previous);assert.deepEqual(fs.readFileSync(dataPath(original.original_path)),bytes);
  assert.notEqual(translationSnapshot(fresh[0].id).cacheKey,snapshot.cacheKey);assert.equal(getTranslationForBlock(fresh[0].id),null);
  const repeated=enqueueUpload(uploaded.resourceId,uploaded.versionId);await runOnce();assert.equal(getJob(repeated.id).status,'completed');
  assert.deepEqual(db().prepare('SELECT * FROM source_blocks WHERE source_version_id=? ORDER BY sort_order').all(uploaded.versionId),fresh);
  assert.deepEqual(preserve(),previous);assert.equal((db().prepare('SELECT COUNT(*) n FROM usage_records').get() as {n:number}).n,0);
  assert.deepEqual(db().prepare('PRAGMA foreign_key_check').all(),[]);
});

test('queued unknown extractor is explicitly rejected without populating its version',async()=>{
  const uploaded=uploadOriginal(simplePdf(),'unsupported.pdf'),unknown=id();cloneVersion(uploaded.versionId,unknown,'pdf-unknown-v99');
  const job=enqueueUpload(uploaded.resourceId,unknown);await runOnce();assert.equal(getJob(job.id).status,'failed');
  assert.equal((db().prepare('SELECT COUNT(*) n FROM source_blocks WHERE source_version_id=?').get(unknown) as {n:number}).n,0);
  assert.equal((db().prepare('SELECT error_code FROM job_items WHERE job_id=?').get(job.id) as {error_code:string}).error_code,'UNSUPPORTED_EXTRACTOR');
});

test('approved actual PDF is extracted through the new version job in an isolated database', {skip:process.env.VERIFY_REAL_PDF_PARAGRAPHS!=='1'},async()=>{
  const directory=path.join(process.cwd(),'.training/verifications/source-pdf-20260910');
  const source=path.join(directory,'session2-de10cd81e1b2d49ed9d3a17cad40d8a1b4be89fcbb1c4462d0c2ebd7ff86d895.pdf');
  const bytes=fs.readFileSync(source),sourceHash='de10cd81e1b2d49ed9d3a17cad40d8a1b4be89fcbb1c4462d0c2ebd7ff86d895';
  assert.equal(hash(bytes),sourceHash);assert.ok(dataPath('library.sqlite').startsWith(temporary+path.sep));
  const uploaded=uploadOriginal(bytes,'approved-source-fixture.pdf');
  const job=enqueueUpload(uploaded.resourceId,uploaded.versionId);await runOnce();assert.equal(getJob(job.id).status,'completed');
  const version=db().prepare('SELECT * FROM source_versions WHERE id=?').get(uploaded.versionId) as Record<string,any>;
  const blocks=db().prepare('SELECT * FROM source_blocks WHERE source_version_id=? AND page_index=2 ORDER BY sort_order').all(uploaded.versionId) as Record<string,any>[];
  assert.equal(version.extractor_version,PDF_EXTRACTOR_VERSION);assert.equal(version.file_hash,sourceHash);assert.equal(blocks.length,4);
  const metadataBytes=fs.readFileSync(path.join(directory,'page-3-render-metadata.json'));assert.equal(hash(metadataBytes),'0c6fa47f6c606df5cf567ee515144ac59429df49956c1dc57d9e9d166e79f970');
  const lines=(JSON.parse(metadataBytes.toString('utf8')).lines as {text:string}[]).map(l=>l.text);
  assert.deepEqual(blocks.map(b=>b.text),[lines[0],lines.slice(1,5).join(' '),[lines.slice(5,7).join(' '),lines.slice(7,9).join(' '),lines.slice(9,11).join(' '),lines[11]].join('\n'),lines[12]]);
  assert.deepEqual(blocks.map(b=>JSON.parse(b.structure_json).lineCount),[1,4,7,1]);
  assert.ok(blocks.every(b=>hash(b.text)===b.source_hash));
  assert.equal(hash(fs.readFileSync(dataPath(version.original_path))),sourceHash);assert.equal(hash(fs.readFileSync(source)),sourceHash);
  assert.equal((db().prepare('SELECT COUNT(*) n FROM usage_records').get() as {n:number}).n,0);
});
