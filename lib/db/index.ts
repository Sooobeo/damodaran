import Database from 'better-sqlite3';
import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { APP_ROOT, DATA_DIR, ensureDataDirs } from '../config';
let connection: Database.Database | undefined;
export const LATEST_SCHEMA_VERSION = 3;
const migrations = ['001_initial.sql', '002_translation_reviews.sql', '003_translation_quality.sql'];
export const now = () => new Date().toISOString();
export const id = () => randomUUID();
export const hash = (value:string|Buffer) => createHash('sha256').update(value).digest('hex');
export function db() {
  if(!connection){
    if(!fs.existsSync(path.join(DATA_DIR,'library.sqlite'))) throw new Error('DB가 없습니다. npm run setup을 먼저 실행하세요.');
    const candidate = openDb();
    try {
      const current = candidate.prepare('SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1').get() as {version:number}|undefined;
      if(current?.version!==LATEST_SCHEMA_VERSION) throw new Error('DB 스키마가 맞지 않습니다. 백업 후 npm run setup을 실행하세요.');
      // Publish only a validated connection; a failed call must not bypass the next check.
      connection = candidate;
    } catch(error) { candidate.close(); throw error; }
  }
  return connection;
}
export function openDb() {
  const d = new Database(path.join(DATA_DIR,'library.sqlite'));
  try { d.pragma('journal_mode = WAL'); d.pragma('foreign_keys = ON');d.pragma('busy_timeout = 5000');d.pragma('synchronous = FULL');return d; }
  catch(error) { d.close(); throw error; }
}
export function setupDatabase() {
  ensureDataDirs();const d = openDb();
  try {
    d.exec('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,checksum TEXT NOT NULL,applied_at TEXT NOT NULL)');
    const applied = d.prepare('SELECT version,checksum FROM schema_migrations ORDER BY version').all() as {version:number;checksum:string}[];
    if(applied.some((entry,index)=>entry.version!==index+1||entry.version>LATEST_SCHEMA_VERSION))throw new Error('지원하지 않는 DB 마이그레이션 이력입니다.');
    const scripts=migrations.map(file=>fs.readFileSync(path.join(APP_ROOT,'lib/db/migrations',file),'utf8'));
    for(const entry of applied)if(entry.checksum!==hash(scripts[entry.version-1]))throw new Error('적용한 마이그레이션 파일이 변경되었습니다.');
    d.transaction(()=>{scripts.forEach((sql,index)=>{if(index<applied.length)return;d.exec(sql);d.prepare('INSERT INTO schema_migrations VALUES(?,?,?)').run(index+1,hash(sql),now());});})();
  }finally{d.close();}
}
export function closeDb(){connection?.close();connection=undefined;}
export function json<T>(value:unknown,fallback:T):T { if(typeof value!=='string')return fallback;try{return JSON.parse(value) as T;}catch{return fallback;} }
