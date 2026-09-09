import fs from 'node:fs';
import { config, dataPath } from '../config';
import { db, hash, id, json, now } from '../db';
import { downloadSource, EXTRACTOR_VERSION, extractionConfigHash, mimeFor, PipelineError, sniffFile, storeOriginal, validateSourceUrl } from '../sources';
import { extractHtml, extractPdf, type ExtractedDocument } from '../extraction';
import { translationSnapshot, translateSnapshot, type TranslationSnapshot } from '../translation';
import { assertTranslationAvailable, isLocalTranslationProvider } from '../translation/runtime';
import { reuseReviewedTranslation } from '../translation/memory';

type JobRow={id:string;type:string;status:string;scope_json:string;cancel_requested_at:string|null;lease_owner:string|null;lease_until:string|null;error_message:string|null;attempts:number};
type ItemRow={id:string;job_id:string;unit_key:string;work_key:string;block_id:string|null;status:string;scope_json:string;attempts:number};
type ResourceRow={id:string;canonical_url:string|null;source_type:string;title_en:string;title_ko:string;kind:string;format:string};
type VersionRow={id:string;resource_id:string;original_path:string;format:string;final_url:string|null;mime:string;extraction_status:string};
export type PublicJob={id:string;type:string;status:string;total:number;completed:number;failed:number;needsReview:number;remaining:number;cancelled:number;errorMessage:string|null;resourceId:string|null};
export function getJob(jobId:string):PublicJob {
  const row=db().prepare('SELECT * FROM jobs WHERE id=?').get(jobId) as JobRow|undefined;if(!row)throw new PipelineError('NOT_FOUND','작업을 찾을 수 없습니다.');
  const counts=db().prepare('SELECT status,COUNT(*) n FROM job_items WHERE job_id=? GROUP BY status').all(jobId) as {status:string;n:number}[];const count=(s:string)=>counts.find(c=>c.status===s)?.n||0;
  const scope=json<Record<string,unknown>>(row.scope_json,{});
  return {id:row.id,type:row.type,status:row.status,total:counts.reduce((s,c)=>s+c.n,0),completed:count('completed'),failed:count('failed'),needsReview:count('needs_review'),remaining:count('queued')+count('running'),cancelled:count('cancelled'),errorMessage:row.error_message,resourceId:typeof scope.resourceId==='string'?scope.resourceId:null};
}
export function listJobs(){return (db().prepare('SELECT id FROM jobs ORDER BY created_at DESC LIMIT 100').all() as {id:string}[]).map(r=>getJob(r.id));}
function addJob(type:string,dedupeKey:string,scope:unknown){const jobId=id(),stamp=now();db().prepare(`INSERT INTO jobs(id,type,dedupe_key,status,scope_json,created_at,updated_at) VALUES(?,?,?,'queued',?,?,?)`).run(jobId,type,dedupeKey,JSON.stringify(scope),stamp,stamp);return jobId;}
function addItem(jobId:string,unitKey:string,workKey:string,scope:unknown,blockId:string|null=null){const itemId=id();db().prepare(`INSERT INTO job_items(id,job_id,unit_key,work_key,block_id,status,scope_json) VALUES(?,?,?,?,?,'queued',?)`).run(itemId,jobId,unitKey,workKey,blockId,JSON.stringify(scope));return itemId;}
export function enqueueImport(resourceId:string):PublicJob {
  const resource=db().prepare('SELECT * FROM resources WHERE id=?').get(resourceId) as ResourceRow|undefined;if(!resource)throw new PipelineError('NOT_FOUND','자료를 찾을 수 없습니다.');
  if(resource.source_type!=='remote'||!resource.canonical_url)throw new PipelineError('INVALID_SOURCE','업로드 자료는 원격에서 가져올 수 없습니다.');validateSourceUrl(resource.canonical_url);
  const jobId=db().transaction(()=>{const existing=db().prepare(`SELECT id FROM jobs WHERE dedupe_key=? AND status IN ('queued','running')`).get(`import:${resourceId}`) as {id:string}|undefined;if(existing)return existing.id;
    const created=addJob('import',`import:${resourceId}`,{resourceId});addItem(created,resourceId,`import:${resourceId}`,{resourceId});db().prepare(`UPDATE resources SET source_status='queued' WHERE id=?`).run(resourceId);return created;}).immediate();return getJob(jobId);
}
export function enqueueUpload(resourceId:string,versionId:string):PublicJob {
  const version=db().prepare('SELECT id FROM source_versions WHERE resource_id=? AND id=?').get(resourceId,versionId);if(!version)throw new PipelineError('INVALID_VERSION','자료와 PDF 버전이 일치하지 않습니다.');
  const jobId=db().transaction(()=>{const existing=db().prepare(`SELECT id FROM jobs WHERE dedupe_key=? AND status IN ('queued','running')`).get(`extract:${versionId}`) as {id:string}|undefined;if(existing)return existing.id;const created=addJob('extract',`extract:${versionId}`,{resourceId,versionId});addItem(created,versionId,`extract:${versionId}`,{resourceId,versionId});return created;}).immediate();return getJob(jobId);
}
export type TranslationTarget={blockId:string;cacheKey:string;translationId?:string;jobId?:string;itemId?:string};
export function enqueueTranslation(input:{sourceVersionId:string;blockIds?:string[];pageRange?:[number,number]}):{jobIds:string[];targets:TranslationTarget[];cached:number} {
  const version=db().prepare('SELECT * FROM source_versions WHERE id=?').get(input.sourceVersionId) as VersionRow|undefined;if(!version)throw new PipelineError('NOT_FOUND','원문 버전을 찾을 수 없습니다.');
  if(Boolean(input.blockIds)===Boolean(input.pageRange))throw new PipelineError('INVALID_INPUT','문단 또는 페이지 범위 중 하나를 선택하세요.');
  let blocks:{id:string;text:string;type:string}[];
  if(input.pageRange){const [from,to]=input.pageRange;const pageCount=(db().prepare('SELECT page_count FROM source_versions WHERE id=?').get(version.id) as {page_count:number|null}).page_count;if(version.format!=='pdf'||!Number.isInteger(from)||!Number.isInteger(to)||from<1||to<from||to>(pageCount||0))throw new PipelineError('INVALID_PAGE','올바른 PDF 페이지 범위를 선택하세요.');blocks=db().prepare(`SELECT id,text,type FROM source_blocks WHERE source_version_id=? AND page_index BETWEEN ? AND ? ORDER BY sort_order`).all(version.id,from-1,to-1) as typeof blocks;}
  else {const wanted=input.blockIds!;if(!wanted.length||wanted.length>1000||new Set(wanted).size!==wanted.length)throw new PipelineError('INVALID_BLOCKS','중복되지 않은 문단을 선택하세요.');blocks=wanted.map(blockId=>{const block=db().prepare('SELECT id,text,type FROM source_blocks WHERE id=? AND source_version_id=?').get(blockId,version.id) as {id:string;text:string;type:string}|undefined;if(!block)throw new PipelineError('INVALID_BLOCKS','다른 버전의 문단은 번역할 수 없습니다.');return block;});}
  blocks=blocks.filter(b=>b.text.trim()&&b.type!=='image');if(!blocks.length)throw new PipelineError('OCR_REQUIRED','번역할 텍스트가 없습니다. 원본 또는 OCR 필요 상태를 확인하세요.');
  if(blocks.reduce((sum,b)=>sum+b.text.length,0)>config.MAX_SOURCE_CHARS_PER_JOB)throw new PipelineError('TEXT_LIMIT','한 번에 번역할 원문 분량을 넘었습니다. 더 작은 범위를 선택하세요.');
  const snapshots=blocks.map(b=>translationSnapshot(b.id));
  return db().transaction(()=>{const targets:TranslationTarget[]=[],jobIds=new Set<string>();let newJobId:string|undefined,cached=0;
    for(const snapshot of snapshots){
      const reviewed=reuseReviewedTranslation(snapshot);
      if(reviewed){cached++;targets.push({blockId:snapshot.blockId,cacheKey:snapshot.cacheKey,translationId:reviewed.id});continue;}
      const saved=db().prepare(`SELECT id FROM translations WHERE cache_key=? AND generation_status='ready' AND validation_status='passed'`).get(snapshot.cacheKey) as {id:string}|undefined;
      if(saved){cached++;targets.push({blockId:snapshot.blockId,cacheKey:snapshot.cacheKey,translationId:saved.id});continue;}
      if(process.env.NODE_ENV!=='test')assertTranslationAvailable(snapshot);
      const active=db().prepare(`SELECT id,job_id FROM job_items WHERE work_key=? AND status IN ('queued','running')`).get(`translation:${snapshot.cacheKey}`) as {id:string;job_id:string}|undefined;
      if(active){jobIds.add(active.job_id);targets.push({blockId:snapshot.blockId,cacheKey:snapshot.cacheKey,jobId:active.job_id,itemId:active.id});continue;}
      if(!newJobId)newJobId=addJob('translation',`translation:${hash(snapshots.map(s=>s.cacheKey).sort().join('|'))}`,{resourceId:version.resource_id,sourceVersionId:version.id});
      const itemId=addItem(newJobId,snapshot.blockId,`translation:${snapshot.cacheKey}`,snapshot,snapshot.blockId);jobIds.add(newJobId);targets.push({blockId:snapshot.blockId,cacheKey:snapshot.cacheKey,jobId:newJobId,itemId});
    }return {jobIds:[...jobIds],targets,cached};}).immediate();
}
export function cancelJob(jobId:string):PublicJob {
  getJob(jobId);db().transaction(()=>{const row=db().prepare('SELECT * FROM jobs WHERE id=?').get(jobId) as JobRow;if(!['queued','running'].includes(row.status))return;
    db().prepare(`UPDATE jobs SET cancel_requested_at=?,updated_at=? WHERE id=?`).run(now(),now(),jobId);db().prepare(`UPDATE job_items SET status='cancelled' WHERE job_id=? AND status='queued'`).run(jobId);
    db().prepare(`UPDATE usage_records SET reservation_status='released',outcome='cancelled_before_send' WHERE job_id=? AND reservation_status='reserved'`).run(jobId);
    if(row.status==='queued')db().prepare(`UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until=NULL WHERE id=?`).run(jobId);
  }).immediate();return getJob(jobId);
}
export function retryJob(jobId:string):PublicJob {
  getJob(jobId);db().transaction(()=>{
    const row=db().prepare('SELECT * FROM jobs WHERE id=?').get(jobId) as JobRow;if(['queued','running'].includes(row.status))return;
    const items=db().prepare(`SELECT * FROM job_items WHERE job_id=? AND status IN ('failed','cancelled','needs_review')`).all(jobId) as ItemRow[];
    for(const item of items){const other=db().prepare(`SELECT id FROM job_items WHERE work_key=? AND status IN ('queued','running') AND id<>?`).get(item.work_key,item.id);if(other)throw new PipelineError('CONFLICT','같은 문단의 다른 작업이 진행 중입니다. 해당 작업을 먼저 확인하세요.');}
    if(!items.length)return;
    for(const item of items)db().prepare(`UPDATE job_items SET status='queued',attempts=0,next_attempt_at=NULL,error_code=NULL,error_message=NULL WHERE id=?`).run(item.id);
    db().prepare(`UPDATE jobs SET status='queued',cancel_requested_at=NULL,next_attempt_at=NULL,lease_owner=NULL,lease_until=NULL,error_code=NULL,error_message=NULL,updated_at=? WHERE id=?`).run(now(),jobId);
  }).immediate();return getJob(jobId);
}

function assertLease(jobId:string,owner:string){const row=db().prepare('SELECT lease_owner,lease_until FROM jobs WHERE id=?').get(jobId) as {lease_owner:string;lease_until:string};if(row.lease_owner!==owner||row.lease_until<now())throw new PipelineError('LEASE_LOST','작업 소유권이 만료되어 저장을 중단했습니다.');}
function isCancelled(jobId:string){return Boolean((db().prepare('SELECT cancel_requested_at FROM jobs WHERE id=?').get(jobId) as JobRow).cancel_requested_at);}
function summarize(jobId:string,owner:string){
  assertLease(jobId,owner);const summary=getJob(jobId),row=db().prepare('SELECT * FROM jobs WHERE id=?').get(jobId) as JobRow;
  let status=summary.remaining?'queued':row.cancel_requested_at?'cancelled':summary.failed||summary.needsReview?(summary.completed?'partial':'failed'):'completed';
  const next=(db().prepare(`SELECT MIN(next_attempt_at) due FROM job_items WHERE job_id=? AND status='queued'`).get(jobId) as {due:string|null}).due;
  db().prepare(`UPDATE jobs SET status=?,progress_json=?,next_attempt_at=?,lease_owner=NULL,lease_until=NULL,updated_at=? WHERE id=?`).run(status,JSON.stringify(summary),next,now(),jobId);
}
function todayStart(){const date=new Date();const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(date);const value=(t:string)=>parts.find(p=>p.type===t)!.value;return new Date(`${value('year')}-${value('month')}-${value('day')}T00:00:00+09:00`).toISOString();}
function reserveUsage(jobId:string,item:ItemRow,snapshot:TranslationSnapshot){
  return db().transaction(()=>{const used=(db().prepare(`SELECT COALESCE(SUM(source_chars),0) n FROM usage_records WHERE created_at>=? AND reservation_status<>'released'`).get(todayStart()) as {n:number}).n;
    if(used+snapshot.text.length>config.MAX_SOURCE_CHARS_PER_DAY)throw new PipelineError('DAILY_LIMIT','오늘의 번역 분량 한도에 도달했습니다.');
    const attemptId=id();db().prepare(`INSERT INTO usage_records(id,job_id,job_item_id,attempt_id,model,source_chars,reservation_status,created_at) VALUES(?,?,?,?,?,?,'reserved',?)`).run(id(),jobId,item.id,attemptId,snapshot.model,snapshot.text.length,now());return attemptId;
  }).immediate();
}
function errorDetails(error:unknown):PipelineError {
  if(error instanceof PipelineError)return error;const status=(error as {status?:number})?.status;
  if(status)return new PipelineError(`PROVIDER_${status}`,status===401?'번역 API 키를 확인하세요.':`번역 제공자 오류 (${status})`,status===429||status>=500);
  if((error as {name?:string})?.name==='APIConnectionError'||(error as {name?:string})?.name==='APIConnectionTimeoutError')return new PipelineError('PROVIDER_NETWORK','번역 서버 연결 또는 응답 시간이 초과됐습니다.',true);
  return new PipelineError('PROCESSING_ERROR','자료 처리 중 오류가 발생했습니다. 원문 형식과 설정을 확인하고 다시 시도하세요.');
}

async function processTranslation(job:JobRow,item:ItemRow,owner:string){
  const snapshot=json<TranslationSnapshot>(item.scope_json,{} as TranslationSnapshot);
  if(!snapshot.blockId||!snapshot.cacheKey)throw new PipelineError('INVALID_JOB','번역 작업 데이터가 올바르지 않습니다.');
  const reviewed=db().transaction(()=>{assertLease(job.id,owner);return reuseReviewedTranslation(snapshot);}).immediate();
  if(reviewed){db().prepare(`UPDATE job_items SET status='completed',result_id=? WHERE id=?`).run(reviewed.id,item.id);return;}
  const cached=db().prepare(`SELECT id FROM translations WHERE cache_key=? AND validation_status='passed' AND generation_status='ready'`).get(snapshot.cacheKey) as {id:string}|undefined;
  if(cached){db().prepare(`UPDATE job_items SET status='completed',result_id=? WHERE id=?`).run(cached.id,item.id);return;}
  if(process.env.NODE_ENV!=='test')assertTranslationAvailable(snapshot);
  if(snapshot.text.length>config.MAX_SOURCE_CHARS_PER_JOB)throw new PipelineError('TEXT_LIMIT','작업 분량 제한을 넘었습니다.');
  const attemptId=reserveUsage(job.id,item,snapshot);
  if(isCancelled(job.id)){db().prepare(`UPDATE usage_records SET reservation_status='released',outcome='cancelled_before_send' WHERE attempt_id=?`).run(attemptId);db().prepare(`UPDATE job_items SET status='cancelled' WHERE id=?`).run(item.id);return;}
  assertLease(job.id,owner);db().prepare(`UPDATE usage_records SET reservation_status='sent' WHERE attempt_id=?`).run(attemptId);
  try{
    const result=await translateSnapshot(snapshot);db().transaction(()=>{assertLease(job.id,owner);
      // A user may finish a review while inference is in progress. Keep both that
      // review and the original machine text, and still account for the work done.
      const acceptedReview=reuseReviewedTranslation(snapshot);
      const translationId=acceptedReview?.id||(db().prepare('SELECT id FROM translations WHERE cache_key=?').get(snapshot.cacheKey) as {id:string}|undefined)?.id||id();
      const needsReview=!acceptedReview&&result.warnings.length>0,validity=needsReview?'needs_review':'passed';
      if(!acceptedReview)db().prepare(`INSERT INTO translations(id,block_id,cache_key,text_ko,provider,model,prompt_version,glossary_version,context_hash,generation_status,validation_status,review_status,usage_json,structure_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,'ready',?,'unreviewed',?,?,?) ON CONFLICT(cache_key) DO UPDATE SET text_ko=excluded.text_ko,validation_status=excluded.validation_status,usage_json=excluded.usage_json,structure_json=excluded.structure_json,created_at=excluded.created_at`).run(translationId,snapshot.blockId,snapshot.cacheKey,result.textKo,snapshot.provider,snapshot.model,snapshot.promptVersion,snapshot.glossaryVersion,snapshot.contextHash,validity,JSON.stringify({inputTokens:result.usage.inputTokens,outputTokens:result.usage.outputTokens,warnings:result.warnings}),result.structure?JSON.stringify(result.structure):null,now());
      db().prepare(`UPDATE job_items SET status=?,result_id=?,error_code=?,error_message=? WHERE id=?`).run(needsReview?'needs_review':'completed',translationId,needsReview?'VALIDATION_FAILED':null,needsReview?result.warnings.join(' '):null,item.id);
      db().prepare(`UPDATE usage_records SET reservation_status=?,provider_request_id=?,input_tokens=?,output_tokens=?,outcome=? WHERE attempt_id=?`).run(isLocalTranslationProvider(snapshot.provider)?'reported':result.usage.inputTokens===null?'unknown':'reported',result.usage.requestId,result.usage.inputTokens,result.usage.outputTokens,validity,attemptId);
      if(needsReview)db().prepare('UPDATE jobs SET error_message=? WHERE id=?').run('일부 번역의 숫자·수식·용어 검토가 필요합니다.',job.id);
    }).immediate();
  }catch(error){const usage=(error as {providerUsage?:{inputTokens:number|null;outputTokens:number|null;requestId:string|null}}).providerUsage;
    if(usage)db().prepare(`UPDATE usage_records SET reservation_status=?,input_tokens=?,output_tokens=?,provider_request_id=?,outcome=? WHERE attempt_id=?`).run(isLocalTranslationProvider(snapshot.provider)?'reported':usage.inputTokens===null?'unknown':'reported',usage.inputTokens,usage.outputTokens,usage.requestId,errorDetails(error).code,attemptId);
    else db().prepare(`UPDATE usage_records SET reservation_status=?,outcome=? WHERE attempt_id=? AND reservation_status IN ('reserved','sent')`).run(isLocalTranslationProvider(snapshot.provider)?'reported':'unknown',errorDetails(error).code,attemptId);throw error;}
}

function recordDiscoveredLinks(resource:ResourceRow,doc:ExtractedDocument){
  if(!['목록','catalog','list'].includes(resource.kind))return;
  const insert=db().prepare(`INSERT OR IGNORE INTO resources(id,source_type,canonical_url,title_en,title_ko,summary_ko,kind,format,priority,source_status,created_at) VALUES(?,'remote',?,?,?,'원문 목록에서 확인한 연결 자료','파일',?,'보충','not_imported',?)`);
  let order=0;for(const link of doc.links){
    let url:URL;try{url=validateSourceUrl(link.url);}catch{continue;}
    const ext=url.pathname.match(/\.(pdf|xlsx?|html?)$/i)?.[1]?.toLowerCase();if(!ext||/video|webcast|youtube|\.mp[34]/i.test(url.href))continue;if(url.href===resource.canonical_url)continue;
    const existing=db().prepare('SELECT id FROM resources WHERE canonical_url=? LIMIT 1').get(url.href) as {id:string}|undefined,childId=existing?.id||`linked-${hash(url.href).slice(0,24)}`;
    insert.run(childId,url.href,link.text,link.text,ext==='htm'?'html':ext,now());
    const relation=/solut|answer|해답/i.test(link.text+' '+url.pathname)?'solution':ext.startsWith('xls')?'tool':'attachment';
    db().prepare('INSERT OR IGNORE INTO resource_relations(parent_id,child_id,relation,sort_order) VALUES(?,?,?,?)').run(resource.id,childId,relation,order++);
  }
}
async function extractVersion(version:VersionRow,job:JobRow,owner:string,contentType?:string){
  const bytes=fs.readFileSync(dataPath(version.original_path));let doc:ExtractedDocument;
  if(version.format==='pdf')doc=await extractPdf(bytes);else if(version.format==='html')doc=extractHtml(bytes,version.final_url!,contentType||version.mime);else doc={title:null,blocks:[],links:[],images:[],pageCount:null,warnings:[],status:'ready'};
  const assets:Array<{id:string;url:string;path:string|null;hash:string|null;mime:string|null;status:string}>=[];
  if(new Set(doc.images.map(i=>i.url)).size>60)doc.warnings.push('이미지가 많아 처음 60개만 로컬에 가져왔습니다. 나머지는 원문 링크에서 확인하세요.');
  for(const image of [...new Map(doc.images.map(i=>[i.url,i])).values()].slice(0,60)){
    if(isCancelled(job.id))throw new PipelineError('CANCELLED','가져오기를 취소했습니다.');
    const assetId=id();try{const result=await downloadSource(image.url,Math.min(config.MAX_DOWNLOAD_BYTES,8*1024*1024)),kind=sniffFile(result.bytes,result.contentType,image.url);if(!['png','jpeg','gif','webp'].includes(kind))throw new PipelineError('UNSUPPORTED_ASSET','지원하지 않는 이미지입니다.');const saved=storeOriginal(result.bytes,kind,'derived');assets.push({id:assetId,url:image.url,path:saved.relative,hash:saved.fileHash,mime:mimeFor(kind),status:'ready'});}catch{assets.push({id:assetId,url:image.url,path:null,hash:null,mime:null,status:'failed'});doc.warnings.push('일부 원문 이미지를 가져오지 못했습니다. 원문 링크를 확인하세요.');}
  }
  db().transaction(()=>{assertLease(job.id,owner);
    // Only a newly pending version is populated. A saved version and its block IDs remain immutable.
    if((db().prepare('SELECT COUNT(*) n FROM source_blocks WHERE source_version_id=?').get(version.id) as {n:number}).n===0){
      const statement=db().prepare(`INSERT INTO source_blocks(id,source_version_id,sort_order,type,text,source_hash,page_index,bbox_json,structure_json,warnings_json) VALUES(?,?,?,?,?,?,?,?,?,?)`);
      doc.blocks.forEach((block,index)=>{const asset=assets.find(a=>a.url===block.structure?.sourceUrl);statement.run(id(),version.id,index,block.type,block.text,hash(block.text),block.pageIndex??null,block.bbox?JSON.stringify(block.bbox):null,JSON.stringify({...block.structure,...(asset?{assetId:asset.id,assetStatus:asset.status}:{}),schemaVersion:1}),JSON.stringify(block.warnings));});
      const insertAsset=db().prepare('INSERT OR IGNORE INTO source_assets(id,source_version_id,source_url,local_path,file_hash,mime,status) VALUES(?,?,?,?,?,?,?)');for(const a of assets)insertAsset.run(a.id,version.id,a.url,a.path,a.hash,a.mime,a.status);
    }
    const status=doc.status==='ready'&&assets.some(a=>a.status==='failed')?'partial':doc.status;
    db().prepare('UPDATE source_versions SET page_count=?,extraction_status=?,warnings_json=? WHERE id=?').run(doc.pageCount,status,JSON.stringify([...new Set(doc.warnings)]),version.id);
    db().prepare('UPDATE resources SET source_status=? WHERE id=?').run(status,version.resource_id);
    const resource=db().prepare('SELECT * FROM resources WHERE id=?').get(version.resource_id) as ResourceRow;recordDiscoveredLinks(resource,doc);
    if(doc.title)db().prepare('UPDATE resources SET title_en=? WHERE id=? AND source_type=?').run(doc.title,resource.id,'remote');
  }).immediate();
}
async function processImport(job:JobRow,item:ItemRow,owner:string){
  const scope=json<{resourceId:string;versionId?:string}>(item.scope_json,{} as never);let version:VersionRow;
  if(job.type==='extract')version=db().prepare('SELECT * FROM source_versions WHERE id=? AND resource_id=?').get(scope.versionId,scope.resourceId) as VersionRow;
  else{
    const resource=db().prepare('SELECT * FROM resources WHERE id=?').get(scope.resourceId) as ResourceRow;const result=await downloadSource(resource.canonical_url!);if(isCancelled(job.id))throw new PipelineError('CANCELLED','가져오기를 취소했습니다.');
    const kind=sniffFile(result.bytes,result.contentType,result.finalUrl);if(!['html','pdf','xls','xlsx'].includes(kind))throw new PipelineError('UNSUPPORTED_FORMAT','이 자료는 HTML·PDF·Excel 형식이 아닙니다.');
    const saved=storeOriginal(result.bytes,kind),cfg=extractionConfigHash();
    version=db().prepare('SELECT * FROM source_versions WHERE resource_id=? AND file_hash=? AND extractor_version=? AND extraction_config_hash=?').get(resource.id,saved.fileHash,EXTRACTOR_VERSION,cfg) as VersionRow;
    if(!version){const versionId=id();db().transaction(()=>{assertLease(job.id,owner);db().prepare(`INSERT INTO source_versions(id,resource_id,file_hash,original_path,final_url,mime,format,byte_size,imported_at,fetched_at,http_last_modified,etag,declared_version,extractor_version,extraction_config_hash,extraction_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'queued')`).run(versionId,resource.id,saved.fileHash,saved.relative,result.finalUrl,mimeFor(kind),kind,result.bytes.length,now(),now(),result.lastModified,result.etag,kind==='pdf'?'버전 확인 필요':null,EXTRACTOR_VERSION,cfg);}).immediate();version=db().prepare('SELECT * FROM source_versions WHERE id=?').get(versionId) as VersionRow;}
    item.scope_json=JSON.stringify({...scope,versionId:version.id,httpStatus:result.status,finalUrl:result.finalUrl,checkedAt:now()});db().prepare('UPDATE job_items SET scope_json=? WHERE id=?').run(item.scope_json,item.id);
    if(!['ready','partial','ocr_needed'].includes(version.extraction_status))await extractVersion(version,job,owner,result.contentType);
  }
  if(!version)throw new PipelineError('INVALID_VERSION','추출할 원문 버전을 찾을 수 없습니다.');
  if(job.type==='extract'&&!['ready','partial','ocr_needed'].includes(version.extraction_status))await extractVersion(version,job,owner);
  db().transaction(()=>{assertLease(job.id,owner);db().prepare(`UPDATE job_items SET status='completed',result_id=? WHERE id=?`).run(version.id,item.id);const fresh=db().prepare('SELECT extraction_status FROM source_versions WHERE id=?').get(version.id) as {extraction_status:string};db().prepare('UPDATE resources SET source_status=? WHERE id=?').run(fresh.extraction_status,version.resource_id);}).immediate();
}
export async function runOnce():Promise<boolean>{
  const owner=id(),stamp=now(),until=new Date(Date.now()+60000).toISOString();
  const job=db().transaction(()=>{
    const expired=db().prepare(`SELECT id FROM jobs WHERE status='running' AND (lease_until IS NULL OR lease_until<?)`).all(stamp) as {id:string}[];
    for(const old of expired){db().prepare(`UPDATE job_items SET status='queued',error_code='INTERRUPTED',error_message='이전 실행이 중단되어 남은 부분부터 재개합니다.' WHERE job_id=? AND status='running'`).run(old.id);db().prepare(`UPDATE usage_records SET reservation_status=CASE WHEN reservation_status='reserved' THEN 'released' ELSE 'unknown' END,outcome='interrupted' WHERE job_id=? AND reservation_status IN ('reserved','sent')`).run(old.id);db().prepare(`UPDATE jobs SET status='queued',lease_owner=NULL,lease_until=NULL WHERE id=?`).run(old.id);}
    const next=db().prepare(`SELECT * FROM jobs WHERE status='queued' AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at LIMIT 1`).get(stamp) as JobRow|undefined;if(!next)return null;
    db().prepare(`UPDATE jobs SET status='running',lease_owner=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?`).run(owner,until,stamp,next.id);return {...next,lease_owner:owner,lease_until:until};
  }).immediate();if(!job)return false;
  const heartbeat=setInterval(()=>{try{db().prepare(`UPDATE jobs SET lease_until=?,updated_at=? WHERE id=? AND lease_owner=? AND status='running'`).run(new Date(Date.now()+60000).toISOString(),now(),job.id,owner);}catch{/* Claim/save checks remain authoritative. */}},10000);heartbeat.unref();
  try{
    const pending=db().prepare(`SELECT * FROM job_items WHERE job_id=? AND status='queued' AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY rowid`).all(job.id,now()) as ItemRow[];
    for(const item of pending){
      assertLease(job.id,owner);if(isCancelled(job.id)){db().prepare(`UPDATE job_items SET status='cancelled' WHERE job_id=? AND status='queued'`).run(job.id);break;}
      db().prepare(`UPDATE job_items SET status='running',attempts=attempts+1 WHERE id=?`).run(item.id);item.attempts++;
      try{if(job.type==='translation')await processTranslation(job,item,owner);else await processImport(job,item,owner);}
      catch(error){const detail=errorDetails(error);if(detail.code==='LEASE_LOST')throw detail;
        db().transaction(()=>{assertLease(job.id,owner);const retry=detail.retryable&&item.attempts<3&&!isCancelled(job.id);const state=detail.code==='CANCELLED'||isCancelled(job.id)?'cancelled':retry?'queued':'failed';db().prepare(`UPDATE job_items SET status=?,next_attempt_at=?,error_code=?,error_message=? WHERE id=?`).run(state,retry?new Date(Date.now()+1000*2**item.attempts).toISOString():null,detail.code,detail.message,item.id);db().prepare('UPDATE jobs SET error_code=?,error_message=? WHERE id=?').run(detail.code,detail.message,job.id);
          if(job.type!=='translation'){const scope=json<{resourceId:string;versionId?:string}>(item.scope_json,{} as never);db().prepare(`UPDATE resources SET source_status='failed' WHERE id=?`).run(scope.resourceId);if(scope.versionId)db().prepare(`UPDATE source_versions SET extraction_status='failed' WHERE id=? AND extraction_status='queued'`).run(scope.versionId);}
        }).immediate();
      }
    }
    db().transaction(()=>summarize(job.id,owner)).immediate();return true;
  }finally{clearInterval(heartbeat);}
}
