import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'damodaran-pipeline-'));
process.env.DATA_DIR=temporary;(process.env as Record<string,string|undefined>).NODE_ENV='test';process.env.TRANSLATION_PROVIDER='openai';process.env.TRANSLATION_MODEL='test-fixture-model';process.env.APP_ROOT=process.cwd();
const {db,setupDatabase,closeDb,hash,now}=await import('../lib/db');
const {config,dataPath}=await import('../lib/config');
const {validateSourceUrl,isPublicAddress,sniffFile,uploadOriginal}=await import('../lib/sources');
const {extractHtml,extractPdf}=await import('../lib/extraction');
const {setTestTranslationProvider,protectText,validatePreserved,currentCacheKey,getTranslationForBlock}=await import('../lib/translation');
const {translationSnapshot}=await import('../lib/translation');
const {assertTranslationAvailable}=await import('../lib/translation/runtime');
const {usage}=await import('../lib/service');
const {enqueueTranslation,enqueueUpload,runOnce,getJob,cancelJob,retryJob}=await import('../lib/jobs');
setupDatabase();
after(()=>{setTestTranslationProvider(undefined);closeDb();fs.rmSync(temporary,{recursive:true,force:true});});
let sequence=0;
function fixture(texts:string[]){const resourceId=`fixture-resource-${++sequence}`,versionId=`fixture-version-${sequence}`;
  db().prepare(`INSERT INTO resources(id,source_type,title_en,title_ko,kind,format,created_at) VALUES(?,'upload','TEST FIXTURE','테스트 전용 예제','본문','html',?)`).run(resourceId,now());
  db().prepare(`INSERT INTO source_versions(id,resource_id,file_hash,original_path,mime,format,byte_size,imported_at,extractor_version,extraction_config_hash,extraction_status) VALUES(?,?,?,'originals/test-fixture.html','text/html','html',1,?,'test-v1','test','ready')`).run(versionId,resourceId,hash(resourceId),now());
  const blockIds=texts.map((text,index)=>{const id=`fixture-block-${sequence}-${index}`;db().prepare('INSERT INTO source_blocks(id,source_version_id,sort_order,type,text,source_hash) VALUES(?,?,?,\'paragraph\',?,?)').run(id,versionId,index,text,hash(text));return id;});return {resourceId,versionId,blockIds};
}
function simplePdf(){
  const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'];
  const stream='BT /F1 16 Tf 72 720 Td (TEST FIXTURE - Present value 100 USD) Tj 0 -24 Td (Rate 5 percent, year 2026.) Tj ET';objects.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
  let text='%PDF-1.4\n',offsets=[0];objects.forEach((value,index)=>{offsets.push(Buffer.byteLength(text));text+=`${index+1} 0 obj\n${value}\nendobj\n`;});const xref=Buffer.byteLength(text);text+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`;offsets.slice(1).forEach(offset=>{text+=`${String(offset).padStart(10,'0')} 00000 n \n`;});text+=`trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;return Buffer.from(text);
}

test('source checks reject credentials, local paths, wrong domains, and disguised PDF responses',()=>{
  assert.equal(validateSourceUrl('https://pages.stern.nyu.edu/~adamodar/pc/returncalculator.xls').hostname,'pages.stern.nyu.edu');
  for(const value of ['file:///etc/passwd','http://127.0.0.1/a.pdf','https://pages.stern.nyu.edu/other/a.pdf','https://user:pass@pages.stern.nyu.edu/~adamodar/a.pdf','https://pages.stern.nyu.edu.evil.test/~adamodar/a.pdf'])assert.throws(()=>validateSourceUrl(value));
  for(const ip of ['127.0.0.1','10.1.1.1','192.168.1.1','169.254.169.254','::1','::ffff:127.0.0.1','fc00::1'])assert.equal(isPublicAddress(ip),false);
  assert.equal(isPublicAddress('8.8.8.8'),true);assert.throws(()=>sniffFile(Buffer.from('<html><body>Not a PDF</body></html>'),'text/html','file.pdf'));
  const extras=config.EXTRA_SOURCE_HOSTS;config.EXTRA_SOURCE_HOSTS='';assert.throws(()=>validateSourceUrl('http://people.stern.nyu.edu/adamodar/pdfiles/FoundationsOnline/slides/session2.pdf'));config.EXTRA_SOURCE_HOSTS='people.stern.nyu.edu';assert.equal(validateSourceUrl('http://people.stern.nyu.edu/adamodar/pdfiles/FoundationsOnline/slides/session2.pdf').hostname,'people.stern.nyu.edu');assert.throws(()=>validateSourceUrl('https://people.stern.nyu.edu/other/file.pdf'));config.EXTRA_SOURCE_HOSTS=extras;
});
test('legacy HTML preserves data table cells, paragraph order, images, and excludes executable markup',()=>{
  const html=Buffer.from('<html><title>Test fixture</title><body><script>secret()</script><table><tr><td><h1>Value</h1><p>First paragraph <a href="other.htm">link</a>.</p><table><tr><th>Item</th><th>Value</th></tr><tr><td>Cash</td><td>$100</td></tr></table><p>Last paragraph.</p><img src="chart.png" alt="Chart"></td></tr></table></body></html>');
  const result=extractHtml(html,'https://pages.stern.nyu.edu/~adamodar/test.htm');const table=result.blocks.find(b=>b.type==='table');assert.ok(table);assert.match(table.text,/Item\tValue/);assert.match(table.text,/Cash\t\$100/);assert.equal(result.images[0].url,'https://pages.stern.nyu.edu/~adamodar/chart.png');assert.ok(!result.blocks.some(b=>b.text.includes('secret')));assert.equal(result.blocks.filter(b=>b.text==='Last paragraph.').length,1);
});
test('legacy paragraph/italic wrappers retain long textual comparison tables and explicit exponents',()=>{
  // Independently written fixture reproduces structures observed in R01 AccPrimer/accstate.htm
  // and R02 PVPrimer/pvprimer.htm; its text is not a translated Damodaran passage.
  const longDescription='Accounting interpretation requires context and careful comparison. '.repeat(12);
  const html=Buffer.from('<html><body><p>Before the comparison.<table><tr><td>Item</td><td>Interpretation</td></tr><tr><td>Revenue</td><td>'+longDescription+'</td></tr></table>After the comparison.</p><i><table><tr><td>Frequency</td><td>Formula</td></tr><tr><td>Twice</td><td>(1+.10/2)<sup>2</sup>-1 and r<sub>f</sub></td></tr></table></i><p>Cash before <img src="equation.gif"> cash after the formula.</p></body></html>');
  const result=extractHtml(html,'https://pages.stern.nyu.edu/~adamodar/test.htm');const tables=result.blocks.filter(b=>b.type==='table');assert.equal(tables.length,2);assert.ok(tables[0].text.includes(longDescription.trim()));assert.match(tables[1].text,/\(1\+\.10\/2\)\^\(2\)-1/);assert.match(tables[1].text,/r_\(f\)/);
  const before=result.blocks.findIndex(b=>b.text==='Before the comparison.'),firstTable=result.blocks.indexOf(tables[0]),afterTable=result.blocks.findIndex(b=>b.text==='After the comparison.');assert.ok(before<firstTable&&firstTable<afterTable);
  const image=result.blocks.findIndex(b=>b.type==='image');assert.match(result.blocks[image-1].text,/Cash before/);assert.match(result.blocks[image+1].text,/cash after the formula/);
});
test('PDF upload persists original before extraction and real PDF.js extracts the page',async()=>{
  const bytes=simplePdf();const uploaded=uploadOriginal(bytes,'test-fixture.pdf');const version=db().prepare('SELECT original_path,extraction_status FROM source_versions WHERE id=?').get(uploaded.versionId) as {original_path:string;extraction_status:string};assert.equal(version.extraction_status,'queued');assert.deepEqual(fs.readFileSync(dataPath(version.original_path)),bytes);
  const extracted=await extractPdf(bytes);assert.equal(extracted.pageCount,1);assert.ok(extracted.blocks.some(b=>b.text.includes('Present value 100 USD')));assert.ok(extracted.blocks.every(b=>b.pageIndex===0));
  const job=enqueueUpload(uploaded.resourceId,uploaded.versionId);await runOnce();assert.equal(getJob(job.id).status,'completed');assert.ok((db().prepare('SELECT COUNT(*) n FROM source_blocks WHERE source_version_id=?').get(uploaded.versionId) as {n:number}).n>0);
});
test('numeric, currency, percent, and formula protection detects lost or duplicated tokens',()=>{
  const text='USD 100 million at -5% in 2026. PV = CF / (1+r)^n';const protectedValue=protectText(text);assert.equal(protectedValue.restore(protectedValue.text),text);assert.throws(()=>protectedValue.restore(protectedValue.text.replace(/__PV_[a-f0-9]+_\d+__/,'')));assert.ok(validatePreserved('Return -5% and $100','수익률 5% 및 $100').length);assert.ok(validatePreserved('PV = 100','PV 100').length);
});
test('mixed alphanumeric identifiers are protected as whole values',()=>{
  const original='Browser E2E, CF1 and B2C; rate 40%.';const masked=protectText(original);
  assert.equal(masked.restore(masked.text),original);assert.equal(masked.text.includes('E2E'),false);assert.equal(masked.text.includes('B2C'),false);
  assert.ok(validatePreserved(original,'Browser E2₢ 킹, CF1 and B2C; rate 40%.').length);
});
test('repeated identical paragraphs retain separate block-owned cache entries',async()=>{
  const f=fixture(['Repeated paragraph.','Repeated paragraph.','Repeated paragraph.','Repeated paragraph.']);
  const middle=f.blockIds.slice(1,3).map(translationSnapshot);
  assert.equal(middle[0].contextHash,middle[1].contextHash);assert.notEqual(middle[0].cacheKey,middle[1].cacheKey);
  setTestTranslationProvider(async input=>({data:{segments:input.segments.map(s=>({id:s.id,translatedText:'반복 문단이다.',warnings:[]}))},inputTokens:1,outputTokens:1,requestId:'repeated-block-fixture'}));
  enqueueTranslation({sourceVersionId:f.versionId,blockIds:f.blockIds});await runOnce();
  const translations=f.blockIds.map(blockId=>getTranslationForBlock(blockId));
  assert.ok(translations.every(result=>result?.current&&result.textKo==='반복 문단이다.'));
  assert.equal(new Set(translations.map(result=>result!.id)).size,4);
  assert.equal(enqueueTranslation({sourceVersionId:f.versionId,blockIds:f.blockIds}).cached,4);
});
test('overlapping translation ranges share work; cancellation and cache follow actual ownership',async()=>{
  let calls=0;setTestTranslationProvider(async input=>{calls++;return {data:{segments:input.segments.map(s=>({id:s.id,translatedText:'테스트 예제: '+s.text,warnings:[]}))},inputTokens:10,outputTokens:20,requestId:`test-${calls}`};});
  const f=fixture(['Cash is USD 100.','Rate is 5%.','Growth is 3%.']);const a=enqueueTranslation({sourceVersionId:f.versionId,blockIds:f.blockIds.slice(0,2)}),b=enqueueTranslation({sourceVersionId:f.versionId,blockIds:f.blockIds.slice(1)});
  assert.equal(a.targets[1].itemId,b.targets[0].itemId);assert.equal(b.jobIds.length,2);cancelJob(a.jobIds[0]);assert.equal(getJob(a.jobIds[0]).cancelled,2);await runOnce();assert.equal(calls,1);retryJob(a.jobIds[0]);await runOnce();assert.equal(calls,3);
  const cached=enqueueTranslation({sourceVersionId:f.versionId,blockIds:f.blockIds});assert.equal(cached.cached,3);assert.deepEqual(cached.jobIds,[]);assert.equal(getTranslationForBlock(f.blockIds[0])?.current,true);
  const previous=currentCacheKey(f.blockIds[0]);db().prepare('UPDATE source_blocks SET text=?,source_hash=? WHERE id=?').run('Changed fixture only',hash('Changed fixture only'),f.blockIds[1]);assert.notEqual(currentCacheKey(f.blockIds[0]),previous);assert.equal(getTranslationForBlock(f.blockIds[0])?.current,false);
});
test('missing response IDs fail without a successful translation, malformed protected values require review',async()=>{
  const missing=fixture(['Value is 100.']);setTestTranslationProvider(async()=>({data:{segments:[]},inputTokens:5,outputTokens:1,requestId:'missing-fixture'}));const j=enqueueTranslation({sourceVersionId:missing.versionId,blockIds:missing.blockIds});await runOnce();assert.equal(getJob(j.jobIds[0]).failed,1);assert.equal(getTranslationForBlock(missing.blockIds[0]),null);assert.equal((db().prepare('SELECT input_tokens FROM usage_records WHERE job_id=?').get(j.jobIds[0]) as {input_tokens:number}).input_tokens,5);
  const changed=fixture(['Value is $100.']);setTestTranslationProvider(async input=>({data:{segments:input.segments.map(s=>({id:s.id,translatedText:s.text.replace(/__PV_[a-f0-9]+_\d+__/,'$200'),warnings:[]}))},inputTokens:5,outputTokens:5,requestId:'changed-fixture'}));const j2=enqueueTranslation({sourceVersionId:changed.versionId,blockIds:changed.blockIds});await runOnce();assert.equal(getJob(j2.jobIds[0]).needsReview,1);assert.equal(getJob(j2.jobIds[0]).completed,0);assert.equal(getTranslationForBlock(changed.blockIds[0])?.validationStatus,'needs_review');
});
test('429 retries count calls and expired lease recovery preserves completed results',async()=>{
  let calls=0;setTestTranslationProvider(async input=>{calls++;if(calls===1)throw {status:429};return {data:{segments:input.segments.map(s=>({id:s.id,translatedText:'테스트: '+s.text,warnings:[]}))},inputTokens:2,outputTokens:3,requestId:'retry-fixture'};});
  const f=fixture(['Risk is 4%.']);const j=enqueueTranslation({sourceVersionId:f.versionId,blockIds:f.blockIds});await runOnce();assert.equal(getJob(j.jobIds[0]).status,'queued');db().prepare('UPDATE jobs SET next_attempt_at=NULL WHERE id=?').run(j.jobIds[0]);db().prepare('UPDATE job_items SET next_attempt_at=NULL WHERE job_id=?').run(j.jobIds[0]);await runOnce();assert.equal(calls,2);assert.equal(getJob(j.jobIds[0]).completed,1);assert.equal((db().prepare('SELECT COUNT(*) n FROM usage_records WHERE job_id=?').get(j.jobIds[0]) as {n:number}).n,2);
  const next=fixture(['Another value 8%.']);const interrupted=enqueueTranslation({sourceVersionId:next.versionId,blockIds:next.blockIds});db().prepare(`UPDATE jobs SET status='running',lease_owner='dead-test-worker',lease_until='2000-01-01T00:00:00.000Z' WHERE id=?`).run(interrupted.jobIds[0]);db().prepare(`UPDATE job_items SET status='running' WHERE job_id=?`).run(interrupted.jobIds[0]);await runOnce();assert.equal(getJob(interrupted.jobIds[0]).completed,1);assert.equal(getJob(j.jobIds[0]).completed,1);
});
test('foreign version selection and daily limit cannot trigger provider calls',async()=>{
  const a=fixture(['Cash 4.']),b=fixture(['Cash 7.']);assert.throws(()=>enqueueTranslation({sourceVersionId:a.versionId,blockIds:b.blockIds}));
  let calls=0;setTestTranslationProvider(async()=>{calls++;throw new Error('must not call');});const previous=config.MAX_SOURCE_CHARS_PER_DAY;config.MAX_SOURCE_CHARS_PER_DAY=1;const j=enqueueTranslation({sourceVersionId:a.versionId,blockIds:a.blockIds});await runOnce();config.MAX_SOURCE_CHARS_PER_DAY=previous;assert.equal(calls,0);assert.equal(getJob(j.jobIds[0]).failed,1);
});
test('changing to the free provider invalidates the cache and rejects a queued paid snapshot',async()=>{
  const previous=config.TRANSLATION_PROVIDER;
  try {
    config.TRANSLATION_PROVIDER='openai';
    const f=fixture(['Assets are 100 USD.']);
    setTestTranslationProvider(async input=>({data:{segments:input.segments.map(s=>({id:s.id,translatedText:'테스트 제공자: '+s.text,warnings:[]}))},inputTokens:2,outputTokens:3,requestId:'provider-switch-fixture'}));
    const original=translationSnapshot(f.blockIds[0]);enqueueTranslation({sourceVersionId:f.versionId,blockIds:f.blockIds});await runOnce();
    assert.equal(getTranslationForBlock(f.blockIds[0])?.current,true);
    config.TRANSLATION_PROVIDER='argos';
    const local=translationSnapshot(f.blockIds[0]);assert.equal(local.provider,'argos');assert.notEqual(local.cacheKey,original.cacheKey);
    assert.equal(getTranslationForBlock(f.blockIds[0])?.current,false);
    assert.throws(()=>assertTranslationAvailable(original),error=>error instanceof Error&&'code' in error&&error.code==='PROVIDER_CHANGED');
  }finally {config.TRANSLATION_PROVIDER=previous;}
});
test('local work with no metered tokens is counted separately from unknown paid calls',async()=>{
  const previous=config.TRANSLATION_PROVIDER;
  try {
    config.TRANSLATION_PROVIDER='argos';const before=usage();const texts=['Revenue is 100.','Costs are 60.'];const f=fixture(texts);
    setTestTranslationProvider(async input=>({data:{segments:input.segments.map(s=>({id:s.id,translatedText:'테스트용 로컬 결과: '+s.text,warnings:[]}))},inputTokens:null,outputTokens:null,requestId:null}));
    const work=enqueueTranslation({sourceVersionId:f.versionId,blockIds:f.blockIds});await runOnce();assert.equal(getJob(work.jobIds[0]).completed,2);
    const after=usage();assert.equal(after.localJobs,before.localJobs+1);assert.equal(after.localSourceChars,before.localSourceChars+texts.join('').length);
    assert.equal(after.unknownCount,before.unknownCount);assert.equal(after.remoteSourceChars,before.remoteSourceChars);
    assert.equal(after.inputTokens,before.inputTokens);assert.equal(after.outputTokens,before.outputTokens);
    assert.equal(enqueueTranslation({sourceVersionId:f.versionId,blockIds:f.blockIds}).cached,2);
  }finally {config.TRANSLATION_PROVIDER=previous;}
});
