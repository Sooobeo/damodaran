/** Meaning-check UI fixtures only. Every API request is intercepted; no worker,
 * model, external source or personal database is used. Requires a completed build. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn,execFile,type ChildProcess} from 'node:child_process';
import {chromium,expect} from '@playwright/test';
import type {Block,BlocksResult,Bootstrap,Job,Resource,ResourceDetail,Translation,Version} from '../lib/client-types';
import type {QualityAssessment,QualityFinding} from '../lib/quality/types';

const port=Number(process.env.QUALITY_UI_PORT||3017),base=`http://127.0.0.1:${port}`;
assert.ok(Number.isInteger(port)&&port>3000&&port<65536,'Use a dedicated test port.');
const directory=path.resolve('test-results');fs.mkdirSync(directory,{recursive:true});
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'damodaran-quality-ui-'));
const checks:string[]=[],unexpected:string[]=[],pageErrors:string[]=[];
let server:ChildProcess|undefined,serverOutput='',reportError:string|null=null;
const executablePath=[process.env.PLAYWRIGHT_BROWSER_PATH,'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe','C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'].find(file=>file&&fs.existsSync(file));
const resource:Resource={id:'QUALITY-UI-TEST',titleKo:'자동 의미 검사 · UI 테스트 자료',titleEn:'Meaning check interface · fixtures only',summaryKo:'실제 번역 품질 평가가 아닌 화면 검증용 자료입니다.',kind:'article',format:'html',level:'beginner',priority:'essential',url:null,author:'UI test',tags:[],objectives:[],sourceStatus:'ready',versionId:'quality-ui-version',versionCount:1,blockCount:2,translatedCount:2,bookmarked:false,moduleIds:[]};
const version:Version={id:'quality-ui-version',resourceId:resource.id,fileHash:'fixture',format:'html',pageCount:null,importedAt:'2026-09-11T00:00:00.000Z',extractionStatus:'ready'};
const detail:ResourceDetail={resource,versions:[version],relations:[],modules:[],position:null};
const source='Divide 72 by the interest rate. This estimates the doubling time.';
const target='이자율을 72로 나누면 투자금이 두 배가 되는 기간을 추정할 수 있습니다.';
const sentenceEnd=source.indexOf('. ')+2;
const finding:QualityFinding={category:'quantity_formula',severity:'critical',reason:'원문은 72를 이자율로 나눕니다. 번역에서는 나눗셈의 방향이 반대입니다.',detector:'rule',ruleId:'ui-fixture-only',source:{start:0,end:sentenceEnd,text:source.slice(0,sentenceEnd)},target:{start:0,end:11,text:target.slice(0,11)}};
const assessment=(overrides:Partial<QualityAssessment>={}):QualityAssessment=>({id:'quality-ui-assessment',status:'completed',risk:'review',sourceHash:'ui-source',translationHash:'ui-translation',modelIdentity:'UI-FIXTURE-NOT-A-MODEL',calibrationVersion:'ui-fixture',score:null,findings:[finding],message:'UI fixture only',createdAt:'2026-09-11T00:00:00.000Z',scope:'block',...overrides});
const translation=(overrides:Partial<Translation>={}):Translation=>({id:'quality-ui-translation',textKo:target,validationStatus:'passed',reviewStatus:'unreviewed',current:true,origin:'machine',quality:assessment(),...overrides});
const block=(overrides:Partial<Block>={}):Block=>({id:'quality-ui-block',order:0,type:'paragraph',text:source,pageIndex:null,structure:null,warnings:[],translation:translation(),...overrides});
let blocks:Block[]=[block()],jobs:Job[]=[],qualityRequests:unknown[]=[],reviewRequests:unknown[]=[],failRequest=false;
let holdNextBlocks: {started:()=>void;release:Promise<void>} | undefined;
const bootstrap=():Bootstrap=>({resources:[resource],modules:[],toolGuides:[],notes:[],bookmarks:[],positions:[],progress:[],jobs,settings:{fontSize:17,languageMode:'parallel'},translationStatus:{provider:'argos',providerLabel:'Argos Translate',local:true,apiKeyRequired:false,configured:true,keyConfigured:false,modelConfigured:true,model:'UI-FIXTURE',maxCharsPerJob:20000,maxCharsPerDay:100000,liveVerified:false,statusMessage:'테스트 화면 · 실제 번역 모델을 실행하지 않습니다.'},usage:{sourceChars:0,localSourceChars:0,localJobs:0,remoteSourceChars:0,inputTokens:0,outputTokens:0,unknownCount:0},glossary:[{id:'ui-term',termKo:'이자율',termEn:'interest rate',definitionKo:'기간별 이자의 비율을 뜻합니다. 이 설명은 UI 테스트용입니다.',aliases:[],moduleIds:[],sources:[],revision:1}]});
const payload=():BlocksResult=>({version,total:blocks.length,blocks});

async function startServer(){
  assert.ok(fs.existsSync(path.resolve('.next/BUILD_ID')),'Run the build before this UI test.');
  // Launch Next alone: no app runner, worker, bootstrap read or DB setup.
  server=spawn(process.execPath,[path.resolve('node_modules/next/dist/bin/next'),'start','--hostname','127.0.0.1','--port',String(port)],{cwd:process.cwd(),windowsHide:true,env:{...process.env,DATA_DIR:path.join(temporary,'data'),WEB_ONLY:'1',OPENAI_API_KEY:'',NODE_ENV:'production'},stdio:['ignore','pipe','pipe']});
  server.stdout?.on('data',chunk=>{serverOutput+=chunk.toString();});server.stderr?.on('data',chunk=>{serverOutput+=chunk.toString();});
  const started=Date.now();
  while(Date.now()-started<45000){
    if(server.exitCode!==null)throw new Error(`Isolated server failed: ${serverOutput}`);
    try{const response=await fetch(`${base}/reader/${resource.id}`,{signal:AbortSignal.timeout(1500)});if(response.ok)return;}catch{}
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  throw new Error('Isolated UI server startup timed out.');
}
async function stopServer(){
  if(!server?.pid||server.exitCode!==null||server.signalCode!==null)return;
  const child=server,exited=new Promise<void>(resolve=>child.once('exit',()=>resolve()));
  if(process.platform==='win32')await new Promise<void>((resolve,reject)=>execFile('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,timeout:10000},error=>error&&child.exitCode===null&&child.signalCode===null?reject(error):resolve()));
  else child.kill('SIGTERM');
  await exited;
}

await startServer();
const browser=await chromium.launch({executablePath,headless:true,args:['--disable-background-networking','--disable-background-timer-throttling','--disable-renderer-backgrounding']});
const context=await browser.newContext({viewport:{width:1440,height:1000}});
await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.origin!==base){unexpected.push(request.url());await route.abort();return;}
  if(!url.pathname.startsWith('/api/')){await route.continue();return;}
  let body:unknown;
  if(url.pathname==='/api/bootstrap')body=bootstrap();
  else if(url.pathname===`/api/resources/${resource.id}`)body=detail;
  else if(url.pathname===`/api/resources/${resource.id}/blocks`){
    body=payload();
    if(holdNextBlocks){const hold=holdNextBlocks;holdNextBlocks=undefined;hold.started();await hold.release;}
  }
  else if(url.pathname==='/api/reading-position'&&request.method()==='PUT')body={saved:true};
  else if(url.pathname==='/api/translations/quality'&&request.method()==='POST'){
    qualityRequests.push(request.postDataJSON());
    if(failRequest){await route.fulfill({status:503,json:{message:'테스트: 의미 검사 요청을 저장하지 못했습니다.'}});return;}
    blocks=[block({translation:translation({quality:assessment({status:'queued',risk:'unknown',findings:[],jobId:'quality-ui-job'})})})];
    jobs=[{id:'quality-ui-job',type:'quality',status:'queued',total:1,completed:0,failed:0,needsReview:0,remaining:1,resourceId:resource.id}];
    body={assessmentId:'quality-ui-assessment',jobId:'quality-ui-job',cached:false};
  }else if(url.pathname==='/api/translations/review'&&request.method()==='POST'){
    const value=request.postDataJSON();reviewRequests.push(value);
    body=translation({textKo:value.textKo,reviewStatus:'reviewed',reviewId:'quality-ui-review',origin:'user'});blocks=[block({translation:body as Translation})];
  }else{unexpected.push(`${request.method()} ${url.pathname}`);await route.fulfill({status:500,json:{message:'Unexpected fixture request'}});return;}
  await route.fulfill({status:200,json:body});
});
const page=await context.newPage();page.on('pageerror',error=>pageErrors.push(error.message));
const go=async(mode='parallel')=>{await page.goto(`${base}/reader/${resource.id}?versionId=${version.id}&mode=${mode}`,{waitUntil:'domcontentloaded',timeout:45000});await expect(page.locator('.loading-state')).toHaveCount(0,{timeout:20000});await expect(page.locator('.source-block')).toHaveCount(blocks.length);};
const noOverflow=async()=>{const dimensions=await page.evaluate(()=>({width:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth}));assert.ok(dimensions.scrollWidth<=dimensions.width+1,JSON.stringify(dimensions));};
async function check(name:string,work:()=>Promise<void>){await work();checks.push(name);console.log(`통과: ${name}`);}

try{
  await check('유효한 한국어 구간만 물결 밑줄, 영어는 문장 전체, 용어 클릭 유지',async()=>{
    await go();
    await expect(page.getByText('자동 의미 검사: 검토 권장',{exact:true})).toBeVisible();
    assert.equal((await page.locator('.translated-block .quality-mark-critical').allTextContents()).join(''),finding.target!.text);
    assert.equal((await page.locator('.original-block .quality-mark-source').allTextContents()).join(''),source.slice(0,sentenceEnd));
    await expect(page.locator('.translation-reading-text')).toHaveText(target);
    await page.locator('.translated-block .term-link').filter({hasText:'이자율'}).click();
    await expect(page.locator('.reader-term h3')).toHaveText('이자율');
    await page.getByText('검사 근거 보기 · 1건',{exact:true}).click();
    await expect(page.locator('.quality-finding')).toContainText('수량·단위·연산 방향');
    await expect(page.locator('.quality-evidence blockquote')).toHaveText(source.slice(0,sentenceEnd));
    assert.equal(qualityRequests.length,0);
  });
  await check('위치를 특정하지 못한 낮은 신뢰도는 문단 전체만 주황색',async()=>{
    const low=assessment({id:'ui-low',score:0.36,findings:[{category:'general_low_confidence',severity:'warning',reason:'UI 테스트: 문단 의미를 원문과 대조해 주세요.',detector:'qe',target:null,source:null}]});
    blocks=[block(),block({id:'quality-ui-low-block',order:1,text:'A company can retain earnings to fund its operations.',translation:translation({id:'ui-low-translation',textKo:'기업은 영업 활동에 필요한 자금을 마련하기 위해 이익을 유보할 수 있습니다.',quality:low})})];
    await go();await expect(page.locator('#block-quality-ui-low-block .quality-paragraph-review')).toHaveCount(1);
    await expect(page.locator('#block-quality-ui-low-block .quality-mark')).toHaveCount(0);
    await page.locator('#block-quality-ui-low-block summary').focus();await page.keyboard.press('Enter');
    await expect(page.getByText('오류 위치를 특정하지 않아 문단 전체를 표시했습니다.',{exact:true})).toBeVisible();
    await noOverflow();await page.screenshot({path:path.join(directory,'quality-reader-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});await go('ko');
    await expect(page.locator('.reader-panel')).toHaveCount(0);await noOverflow();
    await page.locator('#block-quality-ui-block summary').click();
    await expect(page.locator('#block-quality-ui-block .quality-evidence blockquote')).toBeVisible();
    await page.screenshot({path:path.join(directory,'quality-reader-mobile.png'),fullPage:true});
  });
  await check('검수 저장은 기계 평가와 양쪽 강조를 즉시 숨기고 수정 내용 유지',async()=>{
    blocks=[block()];await page.setViewportSize({width:1440,height:1000});await go();
    await page.getByRole('button',{name:'원문과 비교하며 번역 수정',exact:true}).click();
    const editor=page.getByRole('textbox',{name:'1번째 문단 번역 수정',exact:true});await expect(editor).toBeFocused();
    await editor.fill('72를 이자율로 나누면 투자금이 두 배가 되는 기간을 추정할 수 있습니다.');
    await page.getByRole('button',{name:'검수 완료로 저장',exact:true}).click();
    await expect(page.locator('.quality-status')).toHaveCount(0);await expect(page.locator('.quality-mark')).toHaveCount(0);
    await expect(page.getByText('사용자 검수 완료',{exact:true})).toBeVisible();assert.equal(reviewRequests.length,1);
    await expect(page.locator('.translation-reading-text')).toContainText('72를 이자율로');
  });
  await check('잘못된 문자열과 UTF-16 서로게이트 경계는 밑줄을 거부',async()=>{
    const brokenTarget='📈 이자율을 72로 나눕니다.';
    const invalid={...finding,target:{start:1,end:2,text:brokenTarget.slice(1,2)},source:{start:0,end:5,text:'wrong'}};
    blocks=[block({translation:translation({textKo:brokenTarget,quality:assessment({findings:[invalid]})})})];await go();
    await expect(page.locator('.quality-mark')).toHaveCount(0);await expect(page.locator('.quality-paragraph-review')).toHaveCount(1);
    await page.getByText('검사 근거 보기 · 1건',{exact:true}).click();
    await expect(page.getByText('오류 위치를 특정하지 않아 문단 전체를 표시했습니다.',{exact:true})).toBeVisible();
  });
  await check('검수 저장 후 늦게 도착한 검사 폴링 응답이 검수본을 되돌리지 않음',async()=>{
    jobs=[];blocks=[block({translation:translation({quality:assessment({status:'queued',risk:'unknown',findings:[]})})})];await go();
    let release!:()=>void,start!:()=>void;
    const started=new Promise<void>(resolve=>{start=resolve;});
    holdNextBlocks={started:start,release:new Promise<void>(resolve=>{release=resolve;})};
    await started;
    await page.getByRole('button',{name:'번역 수정',exact:true}).click();
    await page.getByRole('textbox',{name:'1번째 문단 번역 수정',exact:true}).fill('72를 이자율로 나누어 기간을 추정합니다.');
    await page.getByRole('button',{name:'검수 완료로 저장',exact:true}).click();
    await expect(page.getByText('사용자 검수 완료',{exact:true})).toBeVisible();
    release();await page.waitForTimeout(300);
    await expect(page.locator('.quality-status')).toHaveCount(0);await expect(page.locator('.translation-reading-text')).toHaveText('72를 이자율로 나누어 기간을 추정합니다.');
  });
  await check('warning 구간은 주황색만 표시하고 빨간 밑줄을 쓰지 않음',async()=>{
    blocks=[block({translation:translation({quality:assessment({findings:[{...finding,severity:'warning'}]})})})];await go();
    await expect(page.locator('.quality-mark-warning')).not.toHaveCount(0);await expect(page.locator('.quality-mark-major,.quality-mark-critical')).toHaveCount(0);
  });
  await check('저장 번역 검사 요청·대기·진행·완료 폴링을 번역 실행과 구분',async()=>{
    jobs=[];blocks=[block({translation:translation({quality:null})})];await go();
    await page.getByRole('button',{name:'의미 검사',exact:true}).click();
    await expect(page.getByText('의미 검사 대기 중',{exact:true})).toBeVisible();
    await expect(page.locator('.translation-block-status,.translation-selection')).toHaveCount(0);
    await expect(page.getByText('번역 의미 검사',{exact:true})).toBeVisible();
    assert.deepEqual(qualityRequests,[{translationId:'quality-ui-translation'}]);
    blocks=[block({translation:translation({quality:assessment({status:'running',risk:'unknown',findings:[]})})})];jobs[0].status='running';
    await expect(page.getByText('의미 검사 중',{exact:true})).toBeVisible({timeout:10000});
    blocks=[block({translation:translation({quality:assessment({risk:'no_findings',findings:[]})})})];jobs[0]={...jobs[0],status:'completed',completed:1,remaining:0};
    await expect(page.getByText('자동 검사상 특이점 없음',{exact:true})).toBeVisible({timeout:10000});
    await expect(page.getByText('등록된 검사 범위에서 확인했습니다. 의미 오류가 없음을 보증하지 않습니다.',{exact:true})).toBeVisible();
    await expect(page.locator('.quality-mark')).toHaveCount(0);await expect(page.locator('.translation-block-status,.translation-selection')).toHaveCount(0);
  });
  await check('미설치·실패·취소·오래된 결과를 구분하고 저장 번역 유지',async()=>{
    jobs=[];
    for(const [status,text] of [['unavailable','의미 검사 모델 미설치'],['failed','의미 검사 실패'],['cancelled','의미 검사 취소됨'],['stale','의미 검사 결과가 오래되었습니다']] as const){
      blocks=[block({translation:translation({quality:assessment({status})})})];await go();
      await expect(page.getByText(text,{exact:true})).toBeVisible();await expect(page.locator('.translation-reading-text')).toHaveText(target);
      if(status==='unavailable'||status==='failed'){
        await expect(page.getByText('규칙 검사: 검토 권장',{exact:true})).toBeVisible();
        await expect(page.locator('.quality-mark-critical')).not.toHaveCount(0);
        await page.getByText('검사 근거 보기 · 1건',{exact:true}).click();
        await expect(page.locator('.quality-finding')).toContainText('수량·단위·연산 방향');
      }else await expect(page.locator('.quality-mark')).toHaveCount(0);
      await expect(page.getByRole('button',{name:'의미 검사 다시 요청',exact:true})).toBeEnabled();
    }
  });
  await check('검사 요청 실패는 저장 번역을 보존하고 오류를 표시',async()=>{
    blocks=[block({translation:translation({quality:null})})];failRequest=true;await go();
    await page.getByRole('button',{name:'의미 검사',exact:true}).click();
    await expect(page.locator('.quality-status').getByRole('alert')).toContainText('테스트: 의미 검사 요청을 저장하지 못했습니다.');
    await expect(page.locator('.translation-reading-text')).toHaveText(target);
    await expect(page.getByRole('button',{name:'의미 검사',exact:true})).toBeEnabled();
  });
  assert.deepEqual(pageErrors,[]);assert.deepEqual(unexpected,[]);
}catch(error){reportError=error instanceof Error?error.message:String(error);throw error;}
finally{
  fs.writeFileSync(path.join(directory,'quality-ui.json'),JSON.stringify({passed:!reportError,fixtureOnly:true,modelCalls:0,checks,pageErrors,unexpected,reportError},null,2));
  await browser.close();await stopServer();
  assert.ok(temporary.startsWith(os.tmpdir()+path.sep));fs.rmSync(temporary,{recursive:true,force:true});
}
