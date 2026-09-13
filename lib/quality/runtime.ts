import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { execFile, spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { z } from 'zod';
import { APP_ROOT } from '../config';
import { PipelineError } from '../sources';
import type { QualityFinding } from './types';
import { validateFindings } from './rules';

const fileSchema=z.object({path:z.string(),sha256:z.string().regex(/^[a-f0-9]{64}$/),size:z.number().int().nonnegative()});
const manifestSchema=z.object({schemaVersion:z.literal(1),modelId:z.string().min(1),modelRevision:z.string().min(1),modelHash:z.string().regex(/^[a-f0-9]{64}$/),modelIdentity:z.string().regex(/^[a-f0-9]{64}$/),pythonPath:z.string(),bridgePath:z.string(),contextUsed:z.literal(false),spanSupport:z.literal(false),files:z.array(fileSchema).min(3),calibration:z.object({version:z.literal('product-qe-95-v2'),threshold:z.number().finite(),accepted:z.literal(true),scoreDirection:z.literal('lower-is-risk'),comparison:z.literal('score<=threshold'),evidencePath:z.string(),evidenceSha256:z.string().regex(/^[a-f0-9]{64}$/)})});
export type QualityRuntime= { identity:string; calibrationVersion:string; configured:boolean; threshold:number|null; manifest?:z.infer<typeof manifestSchema> };
export type QualityInput={source:string;translation:string;context:string};
export type QualityModelResult={score:number;findings:QualityFinding[]};
let testProvider:((input:QualityInput)=>Promise<QualityModelResult>)|undefined;
export function setTestQualityProvider(provider:typeof testProvider) { if(process.env.NODE_ENV!=='test')throw new Error('테스트 전용 평가기입니다.');testProvider=provider; }
const digest=(text:string|Buffer)=>createHash('sha256').update(text).digest('hex');
const allowedPrefixes=['.translation/qe/','.venv-qe/','scripts/local-qe/','.training/quality-evaluation/'];
function fileParts(relative:string) {
  const normalized=relative.replaceAll('\\','/'),parts=normalized.split('/');
  if(path.isAbsolute(relative)||!allowedPrefixes.some(prefix=>normalized.startsWith(prefix))||parts.some(part=>!part||part==='.'||part==='..'||part.includes(':')))throw new Error('평가 파일 경로가 올바르지 않습니다.');
  return parts;
}
function localFile(relative:string) {
  const parts=fileParts(relative);let current=APP_ROOT;
  for(const part of parts){current=path.join(current,part);if(fs.lstatSync(current).isSymbolicLink())throw new Error('평가 파일의 링크 경로는 허용하지 않습니다.');}
  const full=path.resolve(APP_ROOT,relative),real=fs.realpathSync(full),root=fs.realpathSync(APP_ROOT)+path.sep;
  if(!full.startsWith(path.resolve(APP_ROOT)+path.sep)||!real.startsWith(root)||!fs.statSync(real).isFile())throw new Error('평가 파일 경로가 허용 범위를 벗어났습니다.');return real;
}
export function qualityRuntime():QualityRuntime {
  if(testProvider&&process.env.NODE_ENV==='test')return {identity:'test-quality-v1',calibrationVersion:'test-only',configured:true,threshold:0};
  if(process.env.NODE_ENV==='test')return {identity:'qe:not-registered',calibrationVersion:'none',configured:false,threshold:null};
  try {
    const bytes=fs.readFileSync(localFile('.translation/qe/manifest.json'));
    const manifest=manifestSchema.parse(JSON.parse(bytes.toString('utf8')));
    for(const relative of [manifest.pythonPath,manifest.bridgePath,manifest.calibration.evidencePath,...manifest.files.map(file=>file.path)])fileParts(relative);
    return {identity:`qe:${manifest.modelHash}:${digest(bytes)}`,calibrationVersion:manifest.calibration.version,configured:true,threshold:manifest.calibration.threshold,manifest};
  }catch {return {identity:'qe:not-registered',calibrationVersion:'none',configured:false,threshold:null};}
}
async function fileDigest(file:string){
  const before=fs.statSync(file,{bigint:true}),hash=createHash('sha256');
  for await(const chunk of fs.createReadStream(file))hash.update(chunk);
  const after=fs.statSync(file,{bigint:true});
  if(['dev','ino','size','mtimeNs','ctimeNs'].some(key=>before[key as keyof typeof before]!==after[key as keyof typeof after]))throw new PipelineError('QE_CHANGED','의미 검사 파일이 확인 중 변경되었습니다.');
  return hash.digest('hex');
}
async function verifyRuntime(runtime:QualityRuntime){
  const m=runtime.manifest!;
  const seen=new Set<string>();
  for(const file of m.files){const full=localFile(file.path),key=process.platform==='win32'?full.toLowerCase():full;if(seen.has(key))throw new PipelineError('QE_CHANGED','의미 검사 등록에 중복된 파일이 있습니다.');seen.add(key);if(fs.statSync(full).size!==file.size||await fileDigest(full)!==file.sha256)throw new PipelineError('QE_CHANGED','의미 검사 모델 파일이 등록 상태와 다릅니다.');}
  if(!m.files.some(f=>f.path===m.pythonPath)||!m.files.some(f=>f.path===m.bridgePath)||!m.files.some(f=>f.path===m.calibration.evidencePath&&f.sha256===m.calibration.evidenceSha256))throw new PipelineError('QE_CHANGED','의미 검사 실행기·교정 근거가 등록되지 않았습니다.');
  if(await fileDigest(localFile(m.calibration.evidencePath))!==m.calibration.evidenceSha256)throw new PipelineError('QE_CHANGED','의미 검사 교정 근거가 변경되었습니다.');
  return m;
}
let engine:QualityEngine|undefined;
let lifecycle:Promise<void>=Promise.resolve();
function withLifecycle<T>(operation:()=>Promise<T>):Promise<T>{const result=lifecycle.then(operation);lifecycle=result.then(()=>undefined,()=>undefined);return result;}
export class QualityEngine {
  child:ChildProcessWithoutNullStreams; closed:Promise<void>; private rejectPending:((error:Error)=>void)|undefined; private resolvePending:((message:Record<string,unknown>)=>void)|undefined; private buffer=''; private requestId:string|null=null; private closePromise:Promise<void>|undefined; private exited=false; private failure:Error|undefined; private received=false;
  get closing(){return !!this.closePromise||this.exited;}
  constructor(readonly runtime:QualityRuntime){
    const m=runtime.manifest!;
    this.child=spawn(localFile(m.pythonPath),['-u',localFile(m.bridgePath)],{cwd:APP_ROOT,windowsHide:true,env:{...process.env,OPENAI_API_KEY:'',HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1',PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'}});
    this.child.stderr.resume(); // Model diagnostics never expose source text through logs/API.
    this.closed=new Promise(resolve=>{this.child.once('close',()=>{this.exited=true;this.stop(new PipelineError('QE_STOPPED','의미 검사 실행기가 종료되었습니다.'));resolve();});});
    this.child.on('error',()=>this.stop(new PipelineError('QE_START_FAILED','의미 검사 모델을 시작하지 못했습니다.')));
    this.child.stdin.on('error',()=>this.stop(new PipelineError('QE_STOPPED','의미 검사 입력을 전달하지 못했습니다.')));
    this.child.stdout.setEncoding('utf8');this.child.stdout.on('data',(chunk:string)=>{
      if(this.closing)return;
      this.buffer+=chunk;if(this.buffer.length>1024*1024){this.stop(new PipelineError('QE_PROTOCOL','의미 검사 응답이 허용 범위를 넘었습니다.'));return;}
      let end:number;while((end=this.buffer.indexOf('\n'))>=0){const line=this.buffer.slice(0,end).trim();this.buffer=this.buffer.slice(end+1);if(!line)continue;
        try{const message=JSON.parse(line);if(!message||typeof message!=='object'||Array.isArray(message)||!this.requestId||message.id!==this.requestId||this.received)throw new Error('Wrong request');this.received=true;this.resolvePending?.(message);}catch{this.stop(new PipelineError('QE_PROTOCOL','의미 검사 응답을 확인할 수 없습니다.'));return;}
      }
    });
  }
  private stop(error:Error){this.failure||=error;this.rejectPending?.(this.failure);void this.close().catch(()=>{});}
  async evaluate(value:QualityInput):Promise<QualityModelResult>{
    const input={...value};
    if([input.source,input.translation].some(text=>typeof text!=='string'||!text.trim()||text.length>30000)||typeof input.context!=='string'||input.context.length>30000)throw new PipelineError('QE_INPUT','의미 검사할 원문·번역·문맥의 분량을 확인해 주세요.');
    if(this.closePromise||this.exited||this.requestId)throw new PipelineError('QE_BUSY','의미 검사 실행기가 준비되지 않았습니다.');
    const requestId=randomUUID();this.requestId=requestId;this.received=false;
    try {
      const message=await new Promise<Record<string,unknown>>((resolve,reject)=>{
        const timer=setTimeout(()=>this.stop(new PipelineError('QE_TIMEOUT','의미 검사 시간이 초과되었습니다.')),300000);
        this.resolvePending=m=>{clearTimeout(timer);resolve(m);};this.rejectPending=e=>{clearTimeout(timer);reject(e);};
        try{this.child.stdin.write(JSON.stringify({id:requestId,...input})+'\n',e=>{if(e)this.stop(new PipelineError('QE_STOPPED','의미 검사 입력을 전달하지 못했습니다.'));});}catch{this.stop(new PipelineError('QE_STOPPED','의미 검사 입력을 전달하지 못했습니다.'));}
      });
      if(this.failure)throw this.failure;
      if(message.error||message.status==='failed'||message.status==='unavailable')throw new PipelineError('QE_FAILED','의미 검사를 완료하지 못했습니다. 모델 상태·지원 분량을 확인해 주세요.');
      if(message.status!=='completed'||typeof message.score!=='number'||!Number.isFinite(message.score))throw new PipelineError('QE_PROTOCOL','의미 검사 완료 상태·원시 점수가 올바르지 않습니다.');
      const manifest=this.runtime.manifest!;
      if(message.modelHash!==manifest.modelHash||message.modelId!==manifest.modelId||message.modelRevision!==manifest.modelRevision||message.modelIdentity!==manifest.modelIdentity||message.calibrationVersion!==manifest.calibration.version)throw new PipelineError('QE_CHANGED','의미 검사 모델 정체성이 다릅니다.');
      if(message.sourceSha256!==digest(input.source)||message.translationSha256!==digest(input.translation)||message.contextSha256!==digest(input.context)||message.truncated!==false||message.scoreDirection!=='higher-is-better'||message.contextUsed!==false||message.risk!==(message.score<=manifest.calibration.threshold?'review':'no_findings'))throw new PipelineError('QE_PROTOCOL','의미 검사 입력·출력 정체성이 일치하지 않습니다.');
      if(!Array.isArray(message.spans)||message.spans.length||Array.isArray(message.findings)&&message.findings.length)throw new PipelineError('QE_OFFSETS','등록된 평가기는 오류 구간을 제공하지 않습니다.');
      const findings=(message.findings??[]) as QualityFinding[];
      if(!validateFindings(input.source,input.translation,findings))throw new PipelineError('QE_OFFSETS','의미 검사 구간이 현재 번역과 일치하지 않습니다.');
      return {score:message.score,findings};
    }catch(error){this.stop(error instanceof Error?error:new PipelineError('QE_PROTOCOL','의미 검사 응답을 확인할 수 없습니다.'));throw error;}finally{this.requestId=null;this.resolvePending=undefined;this.rejectPending=undefined;}
  }
  close():Promise<void>{
    if(this.closePromise)return this.closePromise;
    // Assign before ending stdin, whose error/close listeners may reenter close().
    this.closePromise=Promise.resolve().then(async()=>{
      if(this.exited)return;this.failure||=new PipelineError('QE_STOPPED','의미 검사를 종료했습니다.');this.rejectPending?.(this.failure);this.buffer='';try{this.child.stdin.end();}catch{/* Escalate only this owned child below. */}
      let timer:ReturnType<typeof setTimeout>|undefined;
      const graceful=await Promise.race([this.closed.then(()=>true),new Promise<boolean>(r=>{timer=setTimeout(()=>r(false),2000);})]);if(timer)clearTimeout(timer);
      if(!graceful&&!this.exited){
        if(process.platform==='win32'&&this.child.pid)await new Promise<void>((resolve,reject)=>execFile('taskkill',['/PID',String(this.child.pid),'/T','/F'],{windowsHide:true,timeout:10000,killSignal:'SIGKILL'},error=>!error||this.exited?resolve():reject(new PipelineError('QE_CLOSE_FAILED','의미 검사 프로세스 종료를 확인하지 못했습니다.'))));else this.child.kill('SIGKILL');
        const done=await Promise.race([this.closed.then(()=>true),new Promise<boolean>(r=>{timer=setTimeout(()=>r(false),5000);})]);if(timer)clearTimeout(timer);if(!done)throw new PipelineError('QE_CLOSE_FAILED','의미 검사 프로세스 종료를 확인하지 못했습니다.');
      }
    });void this.closePromise.catch(()=>{});return this.closePromise;
  }
}
export async function evaluateQuality(input:QualityInput,identity:string):Promise<QualityModelResult>{
  const acquired=await withLifecycle(async()=>{
    const runtime=qualityRuntime();if(runtime.identity!==identity)throw new PipelineError('QE_CHANGED','의미 검사 설정이 변경되었습니다. 다시 검사하세요.');
    if(testProvider&&process.env.NODE_ENV==='test')return {result:testProvider(input)};
    if(!runtime.configured)throw new PipelineError('QE_UNAVAILABLE','검증된 의미 검사 모델이 설치·등록되지 않았습니다.');
    if(engine&&(engine.closing||engine.runtime.identity!==identity)){await engine.close();engine=undefined;}
    if(!engine){
      try{await verifyRuntime(runtime);}catch(error){throw error instanceof PipelineError?error:new PipelineError('QE_CHANGED','의미 검사 실행 파일·등록 상태를 확인해 주세요.');}
      if(qualityRuntime().identity!==identity)throw new PipelineError('QE_CHANGED','의미 검사 설정이 확인 중 변경되었습니다.');
      engine=new QualityEngine(runtime);
    }
    const result=engine.evaluate(input);void result.catch(()=>{});return {result};
  });return acquired.result;
}
export function closeQualityEvaluator():Promise<void>{return withLifecycle(async()=>{if(engine){await engine.close();engine=undefined;}});}
