import fs from 'node:fs';
import assert from 'node:assert/strict';
import { dataPath } from '../lib/config';
import { db,now,closeDb } from '../lib/db';
import { enqueueTranslation,getJob,runOnce } from '../lib/jobs';
import { getTranslationForBlock } from '../lib/translation';
import { assertTranslationAvailable } from '../lib/translation/runtime';
import { closeLocalTranslator } from '../lib/translation/local';
try{
  const runtime=assertTranslationAvailable(),htmlOnly=process.argv.includes('--html-only');
  const html=db().prepare("SELECT v.id FROM source_versions v WHERE resource_id='R01' AND extraction_status IN ('ready','partial') ORDER BY imported_at DESC LIMIT 1").get() as {id:string}|undefined;
  const pdf=db().prepare("SELECT v.id FROM source_versions v WHERE v.format='pdf' AND v.extraction_status IN ('ready','partial') ORDER BY v.imported_at DESC LIMIT 1").get() as {id:string}|undefined;
  if(!html||(!pdf&&!htmlOnly))throw new Error('먼저 HTML과 텍스트 PDF를 가져오세요. HTML만 검증하려면 npm run verify:translation:html을 실행하세요.');
  const htmlIds=(db().prepare("SELECT id FROM source_blocks WHERE source_version_id=? AND type IN ('paragraph','p') AND length(text)>80 ORDER BY sort_order LIMIT 3").all(html.id) as {id:string}[]).map(x=>x.id);
  if(htmlIds.length!==3)throw new Error('확인할 HTML 본문 3문단이 필요합니다.');
  const scopes:Array<{sourceVersionId:string;blockIds?:string[];pageRange?:[number,number]}>= [{sourceVersionId:html.id,blockIds:htmlIds}];
  if(!htmlOnly&&pdf)scopes.push({sourceVersionId:pdf.id,pageRange:[1,1]});
  const checks=[];
  for(const scope of scopes){const result=enqueueTranslation(scope);const deadline=Date.now()+600000;while(result.jobIds.some(j=>['queued','running'].includes(getJob(j).status))){if(Date.now()>deadline)throw new Error('번역 검증 시간이 초과되었습니다. 작업 상태를 확인하세요.');if(!await runOnce())await new Promise(r=>setTimeout(r,1000));}
    const savedTexts:string[]=[];
    for(const target of result.targets){const t=getTranslationForBlock(target.blockId);assert(t&&t.current&&t.validationStatus==='passed','일부 번역이 검토 필요 또는 실패 상태입니다.');savedTexts.push(t.textKo);}
    assert(savedTexts.some(text=>/[가-힣]/.test(text)),'한국어 출력이 저장되었는지 확인하세요.');
    const before=(db().prepare('SELECT COUNT(*) n FROM usage_records').get() as {n:number}).n;const cached=enqueueTranslation(scope);const after=(db().prepare('SELECT COUNT(*) n FROM usage_records').get() as {n:number}).n;assert.equal(before,after);assert.equal(cached.jobIds.length,0);checks.push({versionId:scope.sourceVersionId,blocks:result.targets.length,cacheVerified:true});
  }
  const report={verifiedAt:now(),provider:runtime.provider,model:runtime.identity,promptVersion:runtime.promptVersion,scope:htmlOnly?'html-only':'html-and-pdf',checks,semanticReview:'자동 무결성 검증이며 전문가 의미 검수가 아닙니다.'};fs.writeFileSync(dataPath('derived/translation-verification.json'),JSON.stringify(report,null,2));db().prepare("INSERT INTO settings VALUES('translationVerification',?,?) ON CONFLICT(key) DO UPDATE SET non_secret_value_json=excluded.non_secret_value_json,updated_at=excluded.updated_at").run(JSON.stringify(report),now());console.log(JSON.stringify(report,null,2));
}catch(e){console.error(e instanceof Error?e.message:'번역 검증 실패');process.exitCode=1;}finally{closeLocalTranslator();closeDb();}
