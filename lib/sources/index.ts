import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import https from 'node:https';
import { lookup } from 'node:dns/promises';
import { isIP } from 'node:net';
import { config, dataPath, ensureDataDirs } from '../config';
import { db, hash, id, now } from '../db';

export class PipelineError extends Error {
  constructor(public code:string, message:string, public retryable=false) { super(message); this.name='PipelineError'; }
}
const HOSTS=new Set(['pages.stern.nyu.edu','www.stern.nyu.edu','aswathdamodaran.blogspot.com']);
function allowedHost(hostname:string){
  if(HOSTS.has(hostname))return true;
  // This first-party host is opt-in after its redirect has been verified; arbitrary extra hosts are not accepted.
  return hostname==='people.stern.nyu.edu'&&config.EXTRA_SOURCE_HOSTS.split(',').map(h=>h.trim().toLowerCase()).includes(hostname);
}
export function validateSourceUrl(value:string):URL {
  let url:URL; try { url=new URL(value); } catch { throw new PipelineError('INVALID_URL','올바른 원문 주소가 아닙니다.'); }
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.port||!allowedHost(url.hostname)) throw new PipelineError('SOURCE_NOT_ALLOWED','허용된 원문 주소만 가져올 수 있습니다.');
  let pathname:string; try{pathname=decodeURIComponent(url.pathname);}catch{throw new PipelineError('INVALID_URL','원문 경로를 해석할 수 없습니다.');}
  if(pathname.includes('\\')||pathname.split('/').some(p=>p==='..'||p==='.'))throw new PipelineError('INVALID_URL','잘못된 원문 경로입니다.');
  if(url.hostname.endsWith('stern.nyu.edu')&&!/^\/(?:~adamodar|adamodar)\//.test(pathname))throw new PipelineError('SOURCE_NOT_ALLOWED','Damodaran 자료 경로만 가져올 수 있습니다.');
  url.hash='';return url;
}
export function isPublicAddress(address:string):boolean {
  const ip=address.toLowerCase().replace(/^::ffff:/,'');
  if(isIP(ip)===4){const [a,b]=ip.split('.').map(Number);return !(a===0||a===10||a===127||a>=224||(a===100&&b>=64&&b<=127)||(a===169&&b===254)||(a===172&&b>=16&&b<=31)||(a===192&&(b===168||b===0))||(a===198&&(b===18||b===19)));}
  // Public IPv6 unicast only; this excludes loopback, mapped, ULA, link-local and multicast.
  return isIP(ip)===6&&/^[23][0-9a-f]{0,3}:/.test(ip)&&!ip.startsWith('2001:db8:');
}
export type Download={bytes:Buffer;finalUrl:string;contentType:string;etag:string|null;lastModified:string|null;status:number};
async function oneRequest(url:URL,maxBytes:number):Promise<Download & {location?:string}> {
  let addresses;let dnsTimer:ReturnType<typeof setTimeout>|undefined;
  try{addresses=await Promise.race([lookup(url.hostname,{all:true}),new Promise<never>((_resolve,reject)=>{dnsTimer=setTimeout(()=>reject(new PipelineError('TIMEOUT','원문 서버 주소 확인 시간이 초과됐습니다.',true)),10000);})]);}catch(error){throw error instanceof PipelineError?error:new PipelineError('NETWORK_ERROR','원문 서버의 주소를 확인하지 못했습니다.',true);}finally{if(dnsTimer)clearTimeout(dnsTimer);}
  if(!addresses.length||addresses.some(a=>!isPublicAddress(a.address)))throw new PipelineError('UNSAFE_ADDRESS','외부 원문 서버가 아닌 주소는 접근할 수 없습니다.');
  const pinned=addresses.find(a=>a.family===4)||addresses[0];
  return new Promise((resolve,reject)=>{
    let settled=false;const fail=(e:unknown)=>{if(!settled){settled=true;reject(e instanceof PipelineError?e:new PipelineError('NETWORK_ERROR','원문 서버 연결에 실패했습니다.',true));}};
    const transport=url.protocol==='https:'?https:http;
    const req=transport.request(url,{method:'GET',autoSelectFamily:true,headers:{'User-Agent':'DamodaranStudyRoom/1.0 (personal learning)','Accept-Encoding':'identity'},lookup:((_host:unknown,opts:{all?:boolean},cb:(...args:unknown[])=>void)=>opts.all?cb(null,addresses):cb(null,pinned.address,pinned.family)) as never} as http.RequestOptions & {autoSelectFamily:boolean},res=>{
      const status=res.statusCode||0;const base={finalUrl:url.href,contentType:String(res.headers['content-type']||''),etag:res.headers.etag||null,lastModified:res.headers['last-modified']||null,status};
      if(status>=300&&status<400&&res.headers.location){res.resume();settled=true;clearTimeout(timer);resolve({...base,bytes:Buffer.alloc(0),location:res.headers.location});return;}
      if(status<200||status>=300){res.resume();clearTimeout(timer);fail(new PipelineError(`HTTP_${status}`,`원문 서버에서 ${status} 응답을 받았습니다.`,status===429||status>=500));return;}
      if(Number(res.headers['content-length'])>maxBytes){res.destroy();clearTimeout(timer);fail(new PipelineError('FILE_TOO_LARGE','다운로드 크기 제한을 넘었습니다.'));return;}
      const chunks:Buffer[]=[];let total=0;
      res.on('data',(chunk:Buffer)=>{total+=chunk.length;if(total>maxBytes){res.destroy();fail(new PipelineError('FILE_TOO_LARGE','다운로드 크기 제한을 넘었습니다.'));}else chunks.push(Buffer.from(chunk));});
      res.on('error',fail);res.on('end',()=>{clearTimeout(timer);if(!settled){settled=true;resolve({...base,bytes:Buffer.concat(chunks)});}});
    });
    const timer=setTimeout(()=>req.destroy(new PipelineError('TIMEOUT','원문 다운로드 제한시간을 넘었습니다.',true)),30000);
    req.on('error',(e)=>{clearTimeout(timer);fail(e);});req.end();
  });
}
export async function downloadSource(value:string,maxBytes=config.MAX_DOWNLOAD_BYTES):Promise<Download> {
  let url=validateSourceUrl(value);for(let hop=0;hop<=5;hop++){
    const result=await oneRequest(url,maxBytes);
    if(!result.location)return result;
    if(hop===5)throw new PipelineError('TOO_MANY_REDIRECTS','원문 주소 이동 횟수를 넘었습니다.');
    url=validateSourceUrl(new URL(result.location,url).href);
  }throw new PipelineError('TOO_MANY_REDIRECTS','원문 주소 이동 횟수를 넘었습니다.');
}
export type FileKind='html'|'pdf'|'xls'|'xlsx'|'png'|'jpeg'|'gif'|'webp';
export function sniffFile(bytes:Buffer,contentType='',filename=''):FileKind {
  if(bytes.subarray(0,5).toString()==='%PDF-')return 'pdf';
  if(bytes.subarray(0,8).equals(Buffer.from([0xd0,0xcf,0x11,0xe0,0xa1,0xb1,0x1a,0xe1]))&&(bytes.includes(Buffer.from('Workbook','utf16le'))||bytes.includes(Buffer.from('Book','utf16le'))))return 'xls';
  if(bytes.subarray(0,2).toString()==='PK'&&bytes.includes(Buffer.from('xl/workbook.xml'))&&bytes.includes(Buffer.from('[Content_Types].xml')))return 'xlsx';
  if(bytes.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))return 'png';
  if(bytes[0]===0xff&&bytes[1]===0xd8&&bytes[2]===0xff)return 'jpeg';
  if(/^GIF8[79]a/.test(bytes.subarray(0,6).toString()))return 'gif';
  if(bytes.subarray(0,4).toString()==='RIFF'&&bytes.subarray(8,12).toString()==='WEBP')return 'webp';
  const start=bytes.subarray(0,4096).toString('latin1');
  if(/<(?:!doctype\s+html|html|head|body|title|p|h[1-6]|table)\b/i.test(start)){
    if(/\.(pdf|xlsx?)(?:$|[?#])/i.test(filename)||/application\/(?:pdf|vnd\.)/i.test(contentType))throw new PipelineError('WRONG_FORMAT','파일 대신 HTML 응답을 받았습니다. 원문 링크를 확인하세요.');
    return 'html';
  }
  throw new PipelineError('UNSUPPORTED_FORMAT','지원하지 않거나 손상된 파일 형식입니다.');
}
export const mimeFor=(kind:FileKind)=>({html:'text/html',pdf:'application/pdf',xls:'application/vnd.ms-excel',xlsx:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',png:'image/png',jpeg:'image/jpeg',gif:'image/gif',webp:'image/webp'}[kind]);
export function storeOriginal(bytes:Buffer,kind:FileKind,folder:'originals'|'derived'='originals') {
  ensureDataDirs();const fileHash=hash(bytes),relative=`${folder}/${fileHash}.${kind}`;const target=dataPath(relative);
  if(!fs.existsSync(target)){const temporary=dataPath(`tmp/${id()}.part`);fs.writeFileSync(temporary,bytes,{flag:'wx'});try{fs.renameSync(temporary,target);}catch(e){if(fs.existsSync(target)&&hash(fs.readFileSync(target))===fileHash)fs.unlinkSync(temporary);else throw e;}}
  return {fileHash,relative};
}
export const EXTRACTOR_VERSION='structured-v2';
export const extractionConfigHash=()=>hash(JSON.stringify({maxPdfPages:config.MAX_PDF_PAGES,blockChars:6000,extractor:EXTRACTOR_VERSION}));
export function uploadOriginal(bytes:Buffer,filename:string):{resourceId:string;versionId:string} {
  if(!bytes.length||bytes.length>config.MAX_DOWNLOAD_BYTES)throw new PipelineError('FILE_TOO_LARGE','PDF가 비어 있거나 업로드 크기 제한을 넘었습니다.');
  if(sniffFile(bytes,'',filename)!=='pdf')throw new PipelineError('UNSUPPORTED_FORMAT','업로드는 PDF 파일만 지원합니다.');
  const saved=storeOriginal(bytes,'pdf'),resourceId=id(),versionId=id(),stamp=now();const safeName=path.basename(filename.replace(/\\/g,'/')).slice(0,200)||'업로드.pdf';
  db().transaction(()=>{
    db().prepare(`INSERT INTO resources(id,source_type,original_filename,title_en,title_ko,summary_ko,kind,format,source_status,created_at) VALUES(?,'upload',?,?,?,'사용자가 업로드한 개인 PDF','파일','pdf','queued',?)`).run(resourceId,safeName,safeName,safeName,stamp);
    db().prepare(`INSERT INTO source_versions(id,resource_id,file_hash,original_path,mime,format,byte_size,imported_at,extractor_version,extraction_config_hash,extraction_status) VALUES(?,?,?,?,?,'pdf',?,?,?,?, 'queued')`).run(versionId,resourceId,saved.fileHash,saved.relative,mimeFor('pdf'),bytes.length,stamp,EXTRACTOR_VERSION,extractionConfigHash());
  })();return {resourceId,versionId};
}
