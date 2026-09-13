import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import Database from 'better-sqlite3';
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'damodaran-migration-'));
Object.assign(process.env,{DATA_DIR:temporary,NODE_ENV:'test',APP_ROOT:process.cwd()});
const {db,openDb,setupDatabase,closeDb,hash,now,LATEST_SCHEMA_VERSION}=await import('../lib/db');
const {backup,restore}=await import('../lib/backup');
test('v1 migration keeps personal records, supports the pre-migration backup and is idempotent',async()=>{
  const file=path.join(temporary,'library.sqlite'),old=new Database(file);
  const originalSql=fs.readFileSync(path.join(process.cwd(),'lib/db/migrations/001_initial.sql'),'utf8');
  try{
    old.exec('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)');old.exec(originalSql);
    old.prepare('INSERT INTO schema_migrations VALUES(1,?,?)').run(hash(originalSql),now());
    old.prepare("INSERT INTO resources(id,source_type,title_en,title_ko,kind,format,created_at) VALUES('personal','upload','Original','원문','본문','html',?)").run(now());
    old.prepare("INSERT INTO notes(id,resource_id,text,updated_at) VALUES('personal-note','personal','사용자 메모 유지',?)").run(now());
  }finally{old.close();}
  assert.throws(()=>db(),/DB 스키마/);
  const previous=openDb();let saved;try{saved=await backup(previous);}finally{previous.close();}
  setupDatabase();setupDatabase();
  assert.equal((db().prepare('SELECT MAX(version) version FROM schema_migrations').get() as {version:number}).version,LATEST_SCHEMA_VERSION);
  assert.equal((db().prepare('SELECT text FROM notes').get() as {text:string}).text,'사용자 메모 유지');
  assert.equal((db().prepare('SELECT COUNT(*) n FROM translation_reviews').get() as {n:number}).n,0);
  assert.equal((db().prepare('SELECT checksum FROM schema_migrations WHERE version=1').get() as {checksum:string}).checksum,hash(originalSql));
  const restored=path.join(temporary,'old-restored');restore(saved.path,restored);
  const copy=new Database(path.join(restored,'library.sqlite'),{readonly:true});try{
    assert.equal((copy.prepare('SELECT MAX(version) version FROM schema_migrations').get() as {version:number}).version,1);
    assert.equal((copy.prepare('SELECT text FROM notes').get() as {text:string}).text,'사용자 메모 유지');
  }finally{copy.close();closeDb();fs.rmSync(temporary,{recursive:true,force:true});}
});
