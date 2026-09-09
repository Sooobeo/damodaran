import fs from 'node:fs';
import path from 'node:path';
import { APP_ROOT,DATA_DIR } from '../lib/config';
import { setupDatabase,db,closeDb,openDb,LATEST_SCHEMA_VERSION } from '../lib/db';
import { backup } from '../lib/backup';
import { seed } from '../lib/seed';
import { copyAssets } from './assets';
const lockPath=path.join(APP_ROOT,'.setup.lock');
let lock:number|undefined;
try{
  lock=fs.openSync(lockPath,'wx');fs.writeFileSync(lock,String(process.pid));
  if(!fs.existsSync(path.join(APP_ROOT,'.env.local')))fs.copyFileSync(path.join(APP_ROOT,'.env.example'),path.join(APP_ROOT,'.env.local'));
  const existed=fs.existsSync(path.join(DATA_DIR,'library.sqlite'));
  if(existed){const previous=openDb();try{
    const version=(previous.prepare('SELECT MAX(version) version FROM schema_migrations').get() as {version:number}).version;
    if(version<LATEST_SCHEMA_VERSION)console.log(JSON.stringify({beforeMigrationBackup:await backup(previous)}));
  }finally{previous.close();}}
  setupDatabase();seed();copyAssets();
  const runtime=db().prepare('SELECT sqlite_version() version').get() as {version:string};
  const [major,minor,patch]=runtime.version.split('.').map(Number);
  if(major<3||(major===3&&(minor<51||(minor===51&&patch<3))))throw new Error('WAL 수정이 포함된 SQLite 런타임이 필요합니다.');
  console.log(JSON.stringify({status:'ready',existingDataPreserved:existed,sqlite:runtime.version,resources:(db().prepare('SELECT COUNT(*) n FROM resources').get() as {n:number}).n,modules:(db().prepare('SELECT COUNT(*) n FROM modules').get() as {n:number}).n,glossary:(db().prepare('SELECT COUNT(*) n FROM glossary_terms').get() as {n:number}).n},null,2));
}catch(e){console.error(e instanceof Error?e.message:'설치 실패');process.exitCode=1;}finally{closeDb();if(lock!==undefined){fs.closeSync(lock);fs.unlinkSync(lockPath);}}
