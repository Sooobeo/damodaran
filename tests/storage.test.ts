import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
process.env.DATA_DIR=fs.mkdtempSync(path.join(os.tmpdir(),'study-storage-'));
Object.assign(process.env,{NODE_ENV:'test'});
const {db,setupDatabase,closeDb,hash,now}=await import('../lib/db');
const {seed}=await import('../lib/seed');
const s=await import('../lib/service');
const {dataPath,DATA_DIR}=await import('../lib/config');
const {default:Database}=await import('better-sqlite3');
const {backup,restore}=await import('../lib/backup');
setupDatabase();seed();
test('catalog counts, seed preserves personal notes, priority, progress, bookmarks and restart',()=>{
  assert.equal(s.resourceRows().length,36);assert.equal(s.moduleRows().length,8);assert.ok(s.glossaryRows().length>=40);
  const note=s.saveNote({resourceId:'R01',text:'테스트 개인 메모'});const bookmark=s.saveBookmark({resourceId:'R01'});s.saveProgress('M01',{status:'completed'});
  db().prepare('INSERT INTO resource_preferences VALUES(?,?,?)').run('R01','심화',now());seed();closeDb();
  assert.equal(s.noteRows().find(n=>n.id===note!.id)?.text,'테스트 개인 메모');assert.ok(s.bookmarkRows().some(b=>b.id===bookmark!.id));assert.equal(s.progressRows()[0].status,'completed');assert.equal(s.resourceRows().find(r=>r.id==='R01')?.priority,'심화');
});
test('source version ownership, block and page references reject mismatched records',()=>{
  assert.throws(()=>s.saveNote({resourceId:'R01',blockId:'missing',text:'invalid'}));
  const bytes=Buffer.from('<p>Locally authored test fixture, not Damodaran source.</p>');const relative='originals/test-source.html';fs.writeFileSync(dataPath(relative),bytes);
  db().prepare(`INSERT INTO source_versions(id,resource_id,file_hash,original_path,mime,format,byte_size,imported_at,extractor_version,extraction_config_hash,extraction_status) VALUES('test-v1','R01',?,?,'text/html','html',?,?,'test','test','ready')`).run(hash(bytes),relative,bytes.length,now());
  db().prepare("INSERT INTO source_blocks(id,source_version_id,sort_order,type,text,source_hash) VALUES('test-block','test-v1',0,'paragraph','Test fixture','h')").run();
  assert.throws(()=>s.savePosition({resourceId:'R02',sourceVersionId:'test-v1',blockId:'test-block',offset:0,languageMode:'en'}));
  assert.throws(()=>db().prepare("INSERT INTO notes(id,resource_id,source_version_id,text,updated_at) VALUES('bad','R02','test-v1','bad',?)").run(now()));
  s.savePosition({resourceId:'R01',sourceVersionId:'test-v1',blockId:'test-block',offset:17,languageMode:'parallel'});
  assert.equal(s.positionRows()[0].blockId,'test-block');
});
test('consistent backup restores notes, positions and byte-identical source in another folder',async()=>{
  const result=await backup();const target=path.join(os.tmpdir(),'study-restored-'+Date.now());const restored=restore(result.path,target);assert.equal(restored.workerStarted,false);
  const {default:Database}=await import('better-sqlite3');const copied=new Database(path.join(target,'library.sqlite'));
  assert.equal((copied.prepare('SELECT COUNT(*) n FROM notes').get() as {n:number}).n,1);assert.equal((copied.prepare('SELECT block_id FROM reading_positions').get() as {block_id:string}).block_id,'test-block');copied.close();assert.equal(hash(fs.readFileSync(path.join(target,'originals/test-source.html'))),hash(fs.readFileSync(dataPath('originals/test-source.html'))));
  assert.throws(()=>restore(result.path,target));
});
test('settings schemas and path traversal are rejected',()=>{assert.throws(()=>s.saveSettings({fontSize:200}));assert.throws(()=>s.saveSettings({OPENAI_API_KEY:'forbidden'}));assert.throws(()=>dataPath('../outside'));assert.throws(()=>dataPath('C:\\outside'));});

test('backup rejects same-size corrupted originals instead of blessing their new hash',async()=>{
  const original=dataPath('originals/test-source.html'),bytes=fs.readFileSync(original);
  const completed=()=>fs.readdirSync(path.join(DATA_DIR,'backups')).filter(name=>!name.endsWith('.partial')).sort();
  const before=completed(),corrupt=Buffer.from(bytes);corrupt[corrupt.length-1]^=1;
  try{fs.writeFileSync(original,corrupt);await assert.rejects(backup(),/해시·크기/);assert.deepEqual(completed(),before);}
  finally{fs.writeFileSync(original,bytes);}
});

test('backup validates local asset hashes against the snapshot DB too',async()=>{
  const relative='derived/test-asset.png',bytes=Buffer.from('A locally authored asset fixture'),file=dataPath(relative);
  fs.writeFileSync(file,bytes);
  db().prepare('INSERT INTO source_assets(id,source_version_id,source_url,local_path,file_hash,mime,status) VALUES(?,?,?,?,?,?,?)').run('test-asset','test-v1','https://example.test/asset.png',relative,hash(bytes),'image/png','ready');
  try{fs.writeFileSync(file,Buffer.from('A locally corrupted asset fixture'));await assert.rejects(backup(),/해시·크기/);}
  finally{fs.writeFileSync(file,bytes);}
});

test('restore rejects a manifest omitting a DB-referenced original before creating its destination',async()=>{
  const result=await backup();const altered=fs.mkdtempSync(path.join(os.tmpdir(),'study-backup-missing-'));fs.cpSync(result.path,altered,{recursive:true});
  const manifestPath=path.join(altered,'manifest.json'),manifest=JSON.parse(fs.readFileSync(manifestPath,'utf8'));
  manifest.files=manifest.files.filter((entry:{path:string})=>entry.path!=='originals/test-source.html');fs.writeFileSync(manifestPath,JSON.stringify(manifest));
  const target=path.join(os.tmpdir(),'study-reject-missing-'+crypto.randomUUID());
  assert.throws(()=>restore(altered,target),/목록과 DB|누락/);assert.equal(fs.existsSync(target),false);
});

test('restore rejects tampered source even when its manifest hash was changed to match',async()=>{
  const result=await backup();const altered=fs.mkdtempSync(path.join(os.tmpdir(),'study-backup-corrupt-'));fs.cpSync(result.path,altered,{recursive:true});
  const manifestPath=path.join(altered,'manifest.json'),manifest=JSON.parse(fs.readFileSync(manifestPath,'utf8'));
  const entry=manifest.files.find((entry:{path:string})=>entry.path==='originals/test-source.html');
  const file=path.join(altered,entry.path),corrupt=fs.readFileSync(file);corrupt[corrupt.length-1]^=1;fs.writeFileSync(file,corrupt);entry.hash=hash(corrupt);fs.writeFileSync(manifestPath,JSON.stringify(manifest));
  const target=path.join(os.tmpdir(),'study-reject-corrupt-'+crypto.randomUUID());
  assert.throws(()=>restore(altered,target),/원본 파일이 누락되거나 변경/);assert.equal(fs.existsSync(target),false);
});

test('restore verifies actual destination bytes after copying',async()=>{
  const result=await backup(),target=path.join(os.tmpdir(),'study-reject-copy-'+crypto.randomUUID());
  const originalCopy=fs.copyFileSync;
  fs.copyFileSync=((from:fs.PathLike,to:fs.PathLike,mode?:number)=>{
    originalCopy(from,to,mode);
    if(String(to)===path.join(target,'originals/test-source.html')){const bytes=fs.readFileSync(to);bytes[bytes.length-1]^=1;fs.writeFileSync(to,bytes);}
  }) as typeof fs.copyFileSync;
  try{assert.throws(()=>restore(result.path,target),/해시·크기/);}
  finally{fs.copyFileSync=originalCopy;}
});

test('an unsupported schema is rejected on every access and never cached as usable',()=>{
  closeDb();const file=path.join(DATA_DIR,'library.sqlite');const incompatible=new Database(file);incompatible.prepare('UPDATE schema_migrations SET version=999 WHERE version=2').run();incompatible.close();
  try{assert.throws(()=>db(),/DB 스키마가 맞지/);assert.throws(()=>db(),/DB 스키마가 맞지/);}
  finally{closeDb();const repair=new Database(file);repair.prepare('UPDATE schema_migrations SET version=2 WHERE version=999').run();repair.close();}
  assert.equal(s.noteRows().length,1);assert.equal(s.positionRows()[0].blockId,'test-block');
});
test.after(()=>closeDb());
