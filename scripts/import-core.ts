import fs from 'node:fs';
import * as cheerio from 'cheerio';
import { db,now,closeDb } from '../lib/db';
import { dataPath } from '../lib/config';
import { downloadSource } from '../lib/sources';
import { decodeHtml } from '../lib/extraction';
import { enqueueImport,getJob,runOnce } from '../lib/jobs';
const report:{resourceId:string;status:string;errorMessage?:string|null}[]=[];
async function complete(resourceId:string){const j=enqueueImport(resourceId);const deadline=Date.now()+300000;while(Date.now()<deadline){const state=getJob(j.id);if(!['queued','running'].includes(state.status)){report.push({resourceId,status:state.status,errorMessage:state.errorMessage});console.log(JSON.stringify(report.at(-1)));return state;}const worked=await runOnce();if(!worked)await new Promise(r=>setTimeout(r,800));}throw new Error('가져오기 확인 시간이 초과되었습니다. 작업은 설정 화면에서 확인하세요.');}
try{
  for(const resourceId of ['R01','R02','R05'])await complete(resourceId);
  const parent=db().prepare('SELECT canonical_url FROM resources WHERE id=?').get('R06') as {canonical_url:string};
  try{
    const listing=await downloadSource(parent.canonical_url);const $=cheerio.load(decodeHtml(listing.bytes,listing.contentType));
    const candidates=$('a[href]').toArray().map(el=>new URL($(el).attr('href')!,listing.finalUrl).href).filter(u=>/FoundationsOnline\/slides\/session2\.pdf$/i.test(u));
    if(!candidates.length)throw new Error('R06의 실제 링크에서 대표 PDF를 찾지 못했습니다.');
    const url=candidates[0];const existing=db().prepare('SELECT id FROM resources WHERE canonical_url=?').get(url) as {id:string}|undefined;const resourceId=existing?.id||'R06-PDF02';
    if(!existing)db().prepare(`INSERT INTO resources(id,source_type,canonical_url,author,title_en,title_ko,summary_ko,kind,format,level,priority,tags_json,objectives_json,question,created_at) VALUES(?,'remote',?,'Aswath Damodaran','The Structure of a Business — Session 2 slides','기업의 구조 — 금융기초 슬라이드','R06 금융기초 2회차의 실제 슬라이드 링크입니다. 버전 확인 필요.','article','pdf','입문','필수','["금융기초","재무제표"]','["기업의 재무 구조를 살펴본다."]','기업의 자산과 자금 조달은 어떻게 연결되는가?',?)`).run(resourceId,url,now());
    db().prepare("INSERT OR IGNORE INTO resource_relations(parent_id,child_id,relation) VALUES('R06',?,'attachment')").run(resourceId);
    db().prepare("INSERT OR IGNORE INTO module_resources(module_id,resource_id,sort_order) VALUES('M01',?,20)").run(resourceId);
    await complete(resourceId);
  }catch(e){report.push({resourceId:'R06-PDF02',status:'failed',errorMessage:e instanceof Error?e.message:'PDF 링크 확인 실패'});console.log(JSON.stringify(report.at(-1)));}
  fs.writeFileSync(dataPath('derived/import-core-report.json'),JSON.stringify({checkedAt:now(),results:report},null,2));
  if(report.some(r=>!['completed','partial'].includes(r.status)))process.exitCode=1;
}catch(e){console.error(e instanceof Error?e.message:'가져오기 실패');process.exitCode=1;}finally{closeDb();}
