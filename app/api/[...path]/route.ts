import fs from 'node:fs';
import { Readable } from 'node:stream';
import { z } from 'zod';
import { db, json } from '@/lib/db';
import { config,dataPath } from '@/lib/config';
import * as service from '@/lib/service';
import { enqueueImport,enqueueUpload,enqueueTranslation,getJob,listJobs,cancelJob,retryJob } from '@/lib/jobs';
import { uploadOriginal } from '@/lib/sources';
import { getTranslationForBlock } from '@/lib/translation';
import { saveTranslationReview,translationReviewHistory } from '@/lib/translation/memory';
import { enqueueQualityAssessment } from '@/lib/quality';
export const runtime='nodejs';
export const dynamic='force-dynamic';
type Context={params:Promise<{path:string[]}>};
const ok=(data:unknown,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store'}});
function guard(request:Request){
  const allowed=new Set([`127.0.0.1:${config.APP_PORT}`,`localhost:${config.APP_PORT}`]);
  const host=request.headers.get('host')||'';
  if(!allowed.has(host)) throw new service.AppError('HOST_REJECTED','로컬 앱 주소로 접근하세요.',403);
  if(!['GET','HEAD'].includes(request.method)){
    const origin=request.headers.get('origin');
    if(!origin||!['http://'+host].includes(origin)||request.headers.get('sec-fetch-site')==='cross-site')throw new service.AppError('ORIGIN_REJECTED','앱 화면에서 실행한 요청만 허용됩니다.',403);
  }
}
async function bodyBytes(request:Request,max=100000){
  if(Number(request.headers.get('content-length'))>max)throw new service.AppError('TOO_LARGE','허용 분량을 초과했습니다.',413);
  const reader=request.body?.getReader();if(!reader)return Buffer.alloc(0);const chunks:Uint8Array[]=[];let size=0;
  try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>max){await reader.cancel();throw new service.AppError('TOO_LARGE','허용 분량을 초과했습니다.',413);}chunks.push(value);}}finally{reader.releaseLock();}return Buffer.concat(chunks);
}
async function input(request:Request){if(!request.headers.get('content-type')?.includes('application/json'))throw new service.AppError('CONTENT_TYPE','JSON 요청이 필요합니다.',415);try{return JSON.parse((await bodyBytes(request)).toString('utf8'));}catch(e){if(e instanceof service.AppError)throw e;throw new service.AppError('INVALID_JSON','입력 형식이 잘못되었습니다.');}}
function fileResponse(request:Request,relative:string,mime:string,name:string,inline=false){
  const file=dataPath(relative);if(!fs.existsSync(file))throw new service.AppError('FILE_MISSING','저장된 파일이 없습니다. 백업 또는 원문 가져오기를 확인하세요.',404);
  const size=fs.statSync(file).size;let start=0,end=size-1,status=200;
  const headers:Record<string,string>={'Content-Type':mime,'Accept-Ranges':'bytes','Cache-Control':'private, max-age=0','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'",'Content-Disposition':`${inline?'inline':'attachment'}; filename*=UTF-8''${encodeURIComponent(name)}`};
  const range=request.headers.get('range');
  if(range){const match=/^bytes=(\d*)-(\d*)$/.exec(range);if(!match||(!match[1]&&!match[2]))return new Response(null,{status:416,headers:{'Content-Range':`bytes */${size}`}});
    if(match[1]){start=Number(match[1]);end=match[2]?Math.min(Number(match[2]),end):end;}else{start=Math.max(0,size-Number(match[2]));}
    if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start>end||start>=size)return new Response(null,{status:416,headers:{'Content-Range':`bytes */${size}`}});
    status=206;headers['Content-Range']=`bytes ${start}-${end}/${size}`;
  }
  headers['Content-Length']=String(Math.max(0,end-start+1));
  return new Response(size?Readable.toWeb(fs.createReadStream(file,{start,end})) as ReadableStream:null,{status,headers});
}
async function handle(request:Request,context:Context){
  try{
    guard(request);const {path:p}=await context.params;const url=new URL(request.url);const q=url.searchParams;const method=request.method;
    if(method==='GET'&&p[0]==='bootstrap')return ok({...service.baseBootstrap(),jobs:listJobs()});
    if(p[0]==='resources'){
      if(method==='GET'&&p.length===1){let rows=service.resourceRows();const query=(q.get('q')||'').toLocaleLowerCase();if(query)rows=rows.filter(r=>[r.titleKo,r.titleEn,r.summaryKo,...r.tags].join(' ').toLocaleLowerCase().includes(query));for(const key of ['kind','format','level','priority'] as const){const val=q.get(key);if(val)rows=rows.filter(r=>r[key]===val);}if(q.get('module'))rows=rows.filter(r=>r.moduleIds.includes(q.get('module')!));const page=Math.max(1,Number(q.get('page'))||1);return ok({resources:rows.slice((page-1)*30,page*30),total:rows.length,page});}
      const resourceId=p[1];if(method==='GET'&&p.length===2)return ok(service.resourceDetail(resourceId));
      if(p[2]==='import'&&method==='POST')return ok(enqueueImport(resourceId),202);
      if(p[2]==='preferences'&&method==='PATCH'){service.validateLocation({resourceId});const {priority}=z.object({priority:z.enum(['필수','보충','심화','참조']).nullable()}).parse(await input(request));db().prepare('INSERT INTO resource_preferences VALUES(?,?,?) ON CONFLICT(resource_id) DO UPDATE SET priority_override=excluded.priority_override,updated_at=excluded.updated_at').run(resourceId,priority,new Date().toISOString());return ok({saved:true});}
      if(p[2]==='original'&&method==='GET'){const v=service.getVersion(resourceId,z.string().min(1).parse(q.get('versionId')));return fileResponse(request,v.original_path,v.mime,`${resourceId}.${v.format}`,v.format==='pdf');}
      if(p[2]==='blocks'&&method==='GET'){
        const v=service.getVersion(resourceId,z.string().min(1).parse(q.get('versionId')));
        if(['page','cursor','anchorBlockId'].filter(k=>q.has(k)).length>1)throw new service.AppError('AMBIGUOUS_LOCATION','페이지·문단·커서 중 하나만 지정하세요.');
        const total=(db().prepare('SELECT COUNT(*) n FROM source_blocks WHERE source_version_id=?').get(v.id) as service.Row).n;
        let rows:service.Row[]=[];let start=0;
        if(v.format==='pdf'){
          let page=z.coerce.number().int().min(1).parse(q.get('page')||'1');
          if(q.has('anchorBlockId')){const anchor=db().prepare('SELECT page_index FROM source_blocks WHERE source_version_id=? AND id=?').get(v.id,q.get('anchorBlockId')) as service.Row|undefined;if(!anchor)throw new service.AppError('BLOCK_INVALID','해당 버전에서 문단을 찾을 수 없습니다.',404);page=anchor.page_index+1;}
          if(page>v.page_count)throw new service.AppError('PAGE_INVALID','범위를 벗어난 페이지입니다.');
          rows=db().prepare('SELECT * FROM source_blocks WHERE source_version_id=? AND page_index=? ORDER BY sort_order').all(v.id,page-1) as service.Row[];
        }else{
          start=z.coerce.number().int().min(0).parse(q.get('cursor')||'0');
          if(q.has('anchorBlockId')){const anchor=db().prepare('SELECT sort_order FROM source_blocks WHERE source_version_id=? AND id=?').get(v.id,q.get('anchorBlockId')) as service.Row|undefined;if(!anchor)throw new service.AppError('BLOCK_INVALID','해당 버전에서 문단을 찾을 수 없습니다.',404);start=Math.max(0,anchor.sort_order-3);}
          rows=db().prepare('SELECT * FROM source_blocks WHERE source_version_id=? AND sort_order>=? ORDER BY sort_order LIMIT 30').all(v.id,start) as service.Row[];
        }
        const blocks=rows.map(b=>{const active=db().prepare("SELECT i.job_id FROM job_items i JOIN jobs j ON j.id=i.job_id WHERE i.block_id=? AND i.status IN ('queued','running') AND j.type='translation' LIMIT 1").get(b.id) as service.Row|undefined;return {id:b.id,order:b.sort_order,type:b.type,text:b.text,pageIndex:b.page_index,structure:json(b.structure_json,null),warnings:json(b.warnings_json,[]),translation:getTranslationForBlock(b.id),activeJobId:active?.job_id};});
        const last=rows.at(-1)?.sort_order;return ok({version:service.versionRow(v),blocks,total,nextCursor:v.format!=='pdf'&&last!=null&&last+1<total?last+1:null,prevCursor:v.format!=='pdf'&&start>0?Math.max(0,start-30):null,pageCount:v.page_count,position:service.positionRows().find(x=>x.resourceId===resourceId&&x.sourceVersionId===v.id)||null});
      }
    }
    if(p[0]==='assets'&&method==='GET'){const a=db().prepare('SELECT * FROM source_assets WHERE id=?').get(p[1]) as service.Row|undefined;if(!a?.local_path)throw new service.AppError('ASSET_MISSING','원문 이미지를 가져오지 못했습니다.',404);return fileResponse(request,a.local_path,a.mime||'application/octet-stream',p[1],true);}
    if(p[0]==='uploads'&&method==='POST'){
      const bytes=await bodyBytes(request,config.MAX_DOWNLOAD_BYTES+1048576);const contentType=request.headers.get('content-type')||'';if(!contentType.startsWith('multipart/form-data'))throw new service.AppError('UPLOAD_FORMAT','PDF 파일을 선택하세요.');
      const form=await new Request('http://127.0.0.1/upload',{method:'POST',headers:{'content-type':contentType},body:bytes}).formData();const file=form.get('file');if(!(file instanceof File))throw new service.AppError('UPLOAD_REQUIRED','PDF 파일을 선택하세요.');const result=await uploadOriginal(Buffer.from(await file.arrayBuffer()),file.name);const job=enqueueUpload(result.resourceId,result.versionId);return ok({resourceId:result.resourceId,jobId:job.id},202);
    }
    if(p[0]==='modules'&&method==='GET'){const modules=service.moduleRows();if(!p[1])return ok(modules);const module=modules.find(m=>m.slug===p[1]);if(!module)throw new service.AppError('NOT_FOUND','단원을 찾을 수 없습니다.',404);return ok(module);}
    if(p[0]==='glossary'&&method==='GET'){const query=(q.get('q')||'').toLocaleLowerCase();return ok(service.glossaryRows().filter(g=>[g.termKo,g.termEn,g.acronym,...g.aliases].join(' ').toLocaleLowerCase().includes(query)));}
    if(p[0]==='translations'&&p[1]==='review'&&p.length===2&&method==='POST')return ok(saveTranslationReview(await input(request)),201);
    if(p[0]==='translations'&&p[1]==='quality'&&p.length===2&&method==='POST'){const value=z.object({translationId:z.string().min(1)}).strict().parse(await input(request));return ok(enqueueQualityAssessment(value.translationId),202);}
    if(p[0]==='translations'&&p[2]==='quality'&&p.length===3&&method==='GET'){const row=db().prepare('SELECT block_id FROM translations WHERE id=?').get(p[1]) as {block_id:string}|undefined;if(!row)throw new service.AppError('NOT_FOUND','번역을 찾을 수 없습니다.',404);const current=getTranslationForBlock(row.block_id);return ok(current?.id===p[1]?current.quality:null);}
    if(p[0]==='translations'&&p[2]==='reviews'&&p.length===3&&method==='GET')return ok(translationReviewHistory(p[1]));
    if(p[0]==='translations'&&p.length===1&&method==='POST'){const t=z.object({sourceVersionId:z.string().min(1),blockIds:z.array(z.string().min(1)).min(1).max(200).optional(),pageRange:z.tuple([z.number().int().positive(),z.number().int().positive()]).optional()}).refine(v=>!!v.blockIds!==!!v.pageRange,'문단 또는 페이지 범위 중 하나를 선택하세요.').parse(await input(request));return ok(enqueueTranslation(t),202);}
    if(p[0]==='jobs'){
      if(method==='GET')return ok(p[1]?getJob(p[1]):listJobs());
      if(method==='POST'&&p[2]==='cancel')return ok(cancelJob(p[1]));if(method==='POST'&&p[2]==='retry')return ok(retryJob(p[1]));
    }
    if(p[0]==='notes'){
      if(method==='GET')return ok(service.noteRows());
      if(method==='POST')return ok(service.saveNote(await input(request)),201);
      if(method==='PATCH'){const original=service.noteRows().find(n=>n.id===p[1]);if(!original)throw new service.AppError('NOT_FOUND','메모가 없습니다.',404);const changes=z.object({text:z.string(),quote:z.string().nullish()}).parse(await input(request));return ok(service.saveNote({...original,...changes},p[1]));}
      if(method==='DELETE'){db().prepare('DELETE FROM notes WHERE id=?').run(p[1]);return ok({deleted:true});}
    }
    if(p[0]==='bookmarks'){if(method==='GET')return ok(service.bookmarkRows());if(method==='POST')return ok(service.saveBookmark(await input(request)),201);if(method==='DELETE'){db().prepare('DELETE FROM bookmarks WHERE id=?').run(p[1]);return ok({deleted:true});}}
    if(p[0]==='progress'){if(method==='GET')return ok(service.progressRows());if(method==='PATCH')return ok(service.saveProgress(p[1],await input(request)));}
    if(p[0]==='reading-position'){if(method==='GET')return ok(service.positionRows().filter(x=>!q.get('resourceId')||x.resourceId===q.get('resourceId')));if(method==='PUT')return ok(service.savePosition(await input(request)));}
    if(p[0]==='settings'){if(method==='GET')return ok(p[1]==='translation-status'?service.translationStatus():service.getSettings());if(method==='PATCH')return ok(service.saveSettings(await input(request)));}
    throw new service.AppError('NOT_FOUND','요청한 경로가 없습니다.',404);
  }catch(error){
    if(error instanceof z.ZodError)return ok({code:'INVALID_INPUT',message:'입력값을 확인하세요.',details:error.issues.map(i=>({path:i.path,message:i.message})),retryable:false},400);
    const e=error as Error&{code?:string,status?:number,statusCode?:number,retryable?:boolean};const status=e.status||e.statusCode||(e.code?.includes('NOT_FOUND')?404:e.name==='PipelineError'?(e.retryable?503:400):500);
    // Never expose driver errors, local paths, or provider request bodies.
    const safe=error instanceof service.AppError||e.name==='PipelineError';return ok({code:safe?e.code:'INTERNAL_ERROR',message:safe?e.message:'요청을 처리하지 못했습니다. 설정과 작업 상태를 확인하세요.',retryable:e.retryable??status>=500},status);
  }
}
export const GET=handle;export const POST=handle;export const PATCH=handle;export const PUT=handle;export const DELETE=handle;
