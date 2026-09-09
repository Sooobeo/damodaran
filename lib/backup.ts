import fs from 'node:fs';
import path from 'node:path';
import Database from 'better-sqlite3';
import { z } from 'zod';
import { DATA_DIR,dataPath,ensureDataDirs } from './config';
import { db,hash,id,now,LATEST_SCHEMA_VERSION } from './db';
const entrySchema=z.object({path:z.string().min(1),hash:z.string().regex(/^[a-f0-9]{64}$/),size:z.number().int().nonnegative()});
const manifestSchema=z.object({schemaVersion:z.literal(1),createdAt:z.string(),database:entrySchema,files:z.array(entrySchema)});
type Entry=z.infer<typeof entrySchema>;
type Reference={path:string;hash:string;size:number|null};
const pathKey=(value:string)=>process.platform==='win32'?path.normalize(value).toLowerCase():path.normalize(value);

function within(root:string,relative:string){
  if(path.isAbsolute(relative))throw new Error('절대 백업 경로는 허용되지 않습니다.');
  const file=path.resolve(root,relative);
  if(!file.startsWith(root+path.sep))throw new Error('허용 범위 밖의 백업 경로입니다.');
  if(fs.existsSync(file)&&!fs.realpathSync(file).startsWith(fs.realpathSync(root)+path.sep))throw new Error('허용 범위 밖의 백업 파일입니다.');
  return file;
}
function assertDatabase(database:Database.Database){
  if((database.pragma('integrity_check') as {integrity_check:string}[])[0]?.integrity_check!=='ok'||(database.pragma('foreign_key_check') as unknown[]).length)throw new Error('백업 DB 무결성·관계 검사 실패');
  const schema=database.prepare('SELECT MAX(version) version FROM schema_migrations').get() as {version:number|null};
  if(schema.version===null||!Number.isInteger(schema.version)||schema.version<1||schema.version>LATEST_SCHEMA_VERSION)throw new Error('지원하지 않는 백업 DB 스키마입니다.');
}
function references(database:Database.Database):Reference[]{
  const rows=database.prepare(`SELECT original_path path,file_hash hash,byte_size size FROM source_versions
    UNION ALL SELECT local_path path,file_hash hash,NULL size FROM source_assets WHERE local_path IS NOT NULL`).all() as Reference[];
  const refs=new Map<string,Reference>();
  for(const row of rows){
    if(typeof row.path!=='string'||row.path.split(/[\\/]/).some(part=>part==='..'||part==='.')||!['originals','derived'].includes(row.path.split(/[\\/]/)[0])||!(/^[a-f0-9]{64}$/).test(row.hash)||row.size!==null&&(!Number.isSafeInteger(row.size)||row.size<0))throw new Error('DB의 원본 파일 참조가 올바르지 않습니다.');
    const key=pathKey(row.path),previous=refs.get(key);
    if(previous&&(previous.hash!==row.hash||previous.size!==null&&row.size!==null&&previous.size!==row.size))throw new Error('같은 파일을 가리키는 DB 해시·크기가 서로 다릅니다.');
    refs.set(key,{...row,size:row.size??previous?.size??null});
  }
  return [...refs.values()];
}
function verifyFile(file:string,expected:{hash:string;size:number|null}){
  const bytes=fs.readFileSync(file);
  if(expected.size!==null&&bytes.length!==expected.size||hash(bytes)!==expected.hash)throw new Error('원본 파일의 해시·크기가 DB 또는 백업 기록과 다릅니다.');
  return bytes.length;
}
function matchReferences(refs:Reference[],entries:Entry[]){
  const listed=new Map<string,Entry>();
  for(const entry of entries){const key=pathKey(entry.path);if(listed.has(key))throw new Error('백업 파일 목록에 중복 경로가 있습니다.');listed.set(key,entry);}
  if(listed.size!==refs.length)throw new Error('백업 파일 목록과 DB의 원본 참조가 일치하지 않습니다.');
  for(const ref of refs){const entry=listed.get(pathKey(ref.path));if(!entry||entry.hash!==ref.hash||ref.size!==null&&entry.size!==ref.size)throw new Error('백업에서 DB가 참조하는 원본 파일이 누락되거나 변경되었습니다.');}
}
export async function backup(connection:Database.Database=db()){
  ensureDataDirs();const name=now().replace(/[:.]/g,'-')+'-'+id().slice(0,8);const staging=path.join(DATA_DIR,'backups',name+'.partial');fs.mkdirSync(staging);
  await connection.backup(path.join(staging,'library.sqlite'));
  const snapshot=new Database(path.join(staging,'library.sqlite'),{readonly:true,fileMustExist:true});let refs:Reference[];
  try{assertDatabase(snapshot);refs=references(snapshot);}finally{snapshot.close();}
  const files:Entry[]=[];
  for(const ref of refs){
    const from=dataPath(ref.path),to=within(staging,ref.path);
    const size=verifyFile(from,ref);fs.mkdirSync(path.dirname(to),{recursive:true});fs.copyFileSync(from,to);
    // Compare the copied file to the immutable DB hash, not just today's source bytes.
    verifyFile(to,{hash:ref.hash,size});files.push({path:ref.path,hash:ref.hash,size});
  }
  matchReferences(refs,files);
  const database=fs.readFileSync(path.join(staging,'library.sqlite'));const manifest={schemaVersion:1,createdAt:now(),database:{path:'library.sqlite',hash:hash(database),size:database.length},files};fs.writeFileSync(path.join(staging,'manifest.json'),JSON.stringify(manifest,null,2));
  const complete=path.join(DATA_DIR,'backups',name);fs.renameSync(staging,complete);return {path:complete,fileCount:files.length};
}
export function restore(source:string,target:string){
  const from=path.resolve(source),to=path.resolve(target);if(fs.existsSync(to))throw new Error('복원 대상은 존재하지 않는 새 폴더여야 합니다.');
  if(to===from||to.startsWith(from+path.sep))throw new Error('백업 안에 복원할 수 없습니다.');
  const manifest=manifestSchema.parse(JSON.parse(fs.readFileSync(path.join(from,'manifest.json'),'utf8')));
  if(manifest.database.path!=='library.sqlite')throw new Error('백업 DB 파일 경로가 올바르지 않습니다.');
  const entries=[manifest.database,...manifest.files];for(const entry of entries)verifyFile(within(from,entry.path),entry);
  // A valid manifest must include every file referenced by its snapshot DB.
  const snapshot=new Database(within(from,manifest.database.path),{readonly:true,fileMustExist:true});
  try{assertDatabase(snapshot);matchReferences(references(snapshot),manifest.files);}finally{snapshot.close();}
  fs.mkdirSync(to);for(const entry of entries){const dest=within(to,entry.path);fs.mkdirSync(path.dirname(dest),{recursive:true});fs.copyFileSync(within(from,entry.path),dest);verifyFile(dest,entry);}
  const restored=new Database(path.join(to,'library.sqlite'),{fileMustExist:true});
  try{restored.pragma('foreign_keys=ON');assertDatabase(restored);
    const copiedRefs=references(restored);matchReferences(copiedRefs,manifest.files);
    for(const ref of copiedRefs)verifyFile(within(to,ref.path),ref);
    restored.transaction(()=>{restored.prepare("UPDATE job_items SET status='cancelled',next_attempt_at=NULL,error_code='RESTORED',error_message='백업에서 복원된 작업입니다.' WHERE status IN ('queued','running')").run();restored.prepare("UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=NULL,next_attempt_at=NULL,cancel_requested_at=?,error_code='RESTORED',error_message='필요한 작업만 다시 실행하세요.',updated_at=? WHERE status IN ('queued','running')").run(now(),now());restored.prepare("UPDATE usage_records SET reservation_status='released',outcome='restored_before_send' WHERE reservation_status='reserved'").run();})();
  }finally{restored.close();}
  return {path:to,fileCount:manifest.files.length,workerStarted:false};
}
