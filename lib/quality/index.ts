import { db, hash, id, json, now } from '../db';
import { PipelineError } from '../sources';
import { checkMeaningRules, QUALITY_RULES_VERSION, validateFindings } from './rules';
import { evaluateQuality, qualityRuntime } from './runtime';
import type { QualityAssessment, QualityFinding } from './types';

type TranslationRow={id:string;block_id:string;text_ko:string;context_hash:string;structure_json:string|null;source_version_id:string;source_text:string;block_type:string;source_hash:string;resource_id:string};
type AssessmentRow={id:string;translation_id:string;source_hash:string;translation_hash:string;context_hash:string;model_identity:string;calibration_version:string;rules_version:string;status:QualityAssessment['status'];risk:QualityAssessment['risk'];score:number|null;findings_json:string;message:string;created_at:string;job_id:string|null};
export type QualitySnapshot={assessmentId:string;translationId:string;blockId:string;sourceVersionId:string;resourceId:string;source:string;translation:string;sourceHash:string;translationHash:string;context:string;contextHash:string;modelIdentity:string;calibrationVersion:string;rulesVersion:string;threshold:number|null;structured:boolean};
function translationRow(translationId:string):TranslationRow {
  const row=db().prepare(`SELECT t.*,b.text source_text,b.type block_type,b.source_hash,b.source_version_id,v.resource_id FROM translations t JOIN source_blocks b ON b.id=t.block_id JOIN source_versions v ON v.id=b.source_version_id WHERE t.id=? AND t.generation_status='ready'`).get(translationId) as TranslationRow|undefined;
  if(!row)throw new PipelineError('NOT_FOUND','검사할 번역을 찾을 수 없습니다.');return row;
}
function isReviewed(translationId:string){return !!db().prepare('SELECT translation_id FROM translation_review_links WHERE translation_id=?').get(translationId);}
function clearEnqueueFailureWarning(translationId:string){
  // Clear only the dedicated enqueue failure marker, preserving other warning/usage data.
  db().prepare(`UPDATE translations SET usage_json=json_remove(usage_json,'$.qualityWarning') WHERE id=? AND CASE WHEN json_valid(usage_json) THEN json_extract(usage_json,'$.qualityWarning') END=?`).run(translationId,'의미 검사 예약에 실패했습니다. 번역은 저장했으며 다시 검사할 수 있습니다.');
}
function buildContext(row:TranslationRow){
  const location=db().prepare('SELECT sort_order FROM source_blocks WHERE id=?').get(row.block_id) as {sort_order:number};
  const title=db().prepare('SELECT title_en,title_ko FROM resources WHERE id=?').get(row.resource_id) as {title_en:string;title_ko:string};
  const neighbors=db().prepare('SELECT text FROM source_blocks WHERE source_version_id=? AND sort_order BETWEEN ? AND ? ORDER BY sort_order').all(row.source_version_id,location.sort_order-1,location.sort_order+1) as {text:string}[];
  return `${title.title_en}\n${title.title_ko}\n${neighbors.map(n=>n.text).join('\n')}`.slice(0,10000);
}
export function enqueueQualityAssessment(translationId:string,context?:string):{assessmentId:string|null;jobId:string|null;cached:boolean} {
  return db().transaction(()=>{
    const row=translationRow(translationId);if(isReviewed(translationId)){clearEnqueueFailureWarning(translationId);return {assessmentId:null,jobId:null,cached:true};}
    const runtime=qualityRuntime(),actualContext=context??buildContext(row);
    const sourceHash=hash(row.source_text),translationHash=hash(row.text_ko),contextHash=hash(actualContext);
    if(sourceHash!==row.source_hash)throw new PipelineError('SOURCE_CHANGED','원문 해시가 일치하지 않아 의미 검사를 시작하지 않았습니다.');
    const cacheKey=hash(JSON.stringify({sourceHash,translationHash,contextHash,model:runtime.identity,calibration:runtime.calibrationVersion,rules:QUALITY_RULES_VERSION}));
    const existing=db().prepare(`SELECT a.id,a.job_id,a.status,j.cancel_requested_at FROM translation_quality_assessments a LEFT JOIN jobs j ON j.id=a.job_id WHERE a.translation_id=? AND a.cache_key=? AND a.status IN ('queued','running','completed','unavailable') ORDER BY a.created_at DESC LIMIT 1`).get(translationId,cacheKey) as {id:string;job_id:string;status:string;cancel_requested_at:string|null}|undefined;
    if(existing){if(['queued','running'].includes(existing.status)&&existing.cancel_requested_at)throw new PipelineError('QE_CANCELLING','이전 의미 검사가 취소 처리 중입니다. 종료 후 다시 검사하세요.');clearEnqueueFailureWarning(translationId);return {assessmentId:existing.id,jobId:['queued','running'].includes(existing.status)?existing.job_id:null,cached:true};}
    const groupKey=`quality:${row.source_version_id}:${runtime.identity}:${QUALITY_RULES_VERSION}`;
    const group=db().prepare(`SELECT id FROM jobs WHERE type='quality' AND (dedupe_key=? OR json_extract(scope_json,'$.qualityGroupKey')=?) AND status IN ('queued','running') AND cancel_requested_at IS NULL`).get(groupKey,groupKey) as {id:string}|undefined;
    const assessmentId=id(),jobId=group?.id??id(),stamp=now();
    const snapshot:QualitySnapshot={assessmentId,translationId,blockId:row.block_id,sourceVersionId:row.source_version_id,resourceId:row.resource_id,source:row.source_text,translation:row.text_ko,sourceHash,translationHash,context:actualContext,contextHash,modelIdentity:runtime.identity,calibrationVersion:runtime.calibrationVersion,rulesVersion:QUALITY_RULES_VERSION,threshold:runtime.threshold,structured:row.block_type==='table'||row.structure_json!==null};
    if(!group)db().prepare(`INSERT INTO jobs(id,type,dedupe_key,status,scope_json,created_at,updated_at) VALUES(?,'quality',?,'queued',?,?,?)`).run(jobId,`${groupKey}:${jobId}`,JSON.stringify({resourceId:row.resource_id,sourceVersionId:row.source_version_id,qualityGroupKey:groupKey}),stamp,stamp);
    db().prepare(`INSERT INTO translation_quality_assessments(id,translation_id,block_id,source_version_id,source_hash,translation_hash,context_hash,cache_key,model_identity,calibration_version,rules_version,status,risk,job_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued','unknown',?,?)`).run(assessmentId,translationId,row.block_id,row.source_version_id,sourceHash,translationHash,contextHash,cacheKey,runtime.identity,runtime.calibrationVersion,QUALITY_RULES_VERSION,jobId,stamp);
    db().prepare(`INSERT INTO job_items(id,job_id,unit_key,work_key,block_id,status,scope_json) VALUES(?,?,?,?,?,'queued',?)`).run(id(),jobId,assessmentId,`quality:${translationId}:${cacheKey}`,row.block_id,JSON.stringify(snapshot));
    clearEnqueueFailureWarning(translationId);
    return {assessmentId,jobId,cached:false};
  }).immediate();
}
function sameInput(snapshot:QualitySnapshot){
  const row=translationRow(snapshot.translationId);
  return row.block_id===snapshot.blockId&&row.source_version_id===snapshot.sourceVersionId&&row.resource_id===snapshot.resourceId
    && hash(row.source_text)===snapshot.sourceHash&&hash(row.text_ko)===snapshot.translationHash
    && hash(snapshot.source)===snapshot.sourceHash&&hash(snapshot.translation)===snapshot.translationHash&&hash(snapshot.context)===snapshot.contextHash
    && hash(buildContext(row))===snapshot.contextHash&&snapshot.rulesVersion===QUALITY_RULES_VERSION&&snapshot.modelIdentity===qualityRuntime().identity;
}
export async function assessQuality(snapshot:QualitySnapshot):Promise<{status:QualityAssessment['status'];risk:QualityAssessment['risk'];score:number|null;findings:QualityFinding[];message:string}>{
  if(!sameInput(snapshot)||isReviewed(snapshot.translationId))return {status:'stale',risk:'unknown',score:null,findings:[],message:'번역 또는 검수 상태가 바뀌어 이전 평가를 적용하지 않았습니다.'};
  if(snapshot.structured)return {status:'unavailable',risk:'unknown',score:null,findings:[],message:'표 구조의 의미 검사는 아직 지원하지 않습니다. 원문과 비교해 주세요.'};
  const findings=checkMeaningRules(snapshot.source,snapshot.translation);
  let status:QualityAssessment['status']='completed',score:number|null=null,message='자동 의미 검사상 특이점 없음 · 사용자 미검수';
  if(!qualityRuntime().configured){status='unavailable';message='의미 검사 모델 미설치 또는 미등록 · 제한된 규칙만 확인했습니다.';}
  else {
    try {
      const result=await evaluateQuality({source:snapshot.source,translation:snapshot.translation,context:snapshot.context},snapshot.modelIdentity);
      if(!Number.isFinite(result.score)||!validateFindings(snapshot.source,snapshot.translation,result.findings))throw new PipelineError('QE_PROTOCOL','의미 검사 결과가 올바르지 않습니다.');
      score=result.score;findings.push(...result.findings);
      if(snapshot.threshold!==null&&score<=snapshot.threshold&&!findings.length)findings.push({category:'general_low_confidence',severity:'warning',reason:'별도 평가기의 점수가 검토 기준 이하입니다. 원문과 문단 전체를 비교해 주세요.',detector:'qe',source:null,target:null});
    }catch(error){status='failed';message=error instanceof PipelineError?error.message:'의미 검사를 완료하지 못했습니다. 번역은 보존됩니다.';}
  }
  const risk=findings.length?'review':status==='completed'?'no_findings':'unknown';
  if(risk==='review')message=`자동 의미 검사: 검토 권장${status==='completed'?'':` · ${message}`}`;
  return {status,risk,score,findings,message};
}
export function storeQualityAssessment(snapshot:QualitySnapshot,result:Awaited<ReturnType<typeof assessQuality>>){
  if(!sameInput(snapshot)||isReviewed(snapshot.translationId))result={status:'stale',risk:'unknown',score:null,findings:[],message:'번역 또는 검수 상태가 바뀌어 이전 평가를 적용하지 않았습니다.'};
  if(!validateFindings(snapshot.source,snapshot.translation,result.findings))throw new PipelineError('QE_OFFSETS','평가 구간이 현재 원문·번역과 일치하지 않습니다.');
  const changed=db().prepare(`UPDATE translation_quality_assessments SET status=?,risk=?,score=?,findings_json=?,message=?,completed_at=? WHERE id=? AND translation_id=? AND block_id=? AND source_version_id=? AND source_hash=? AND translation_hash=? AND context_hash=?`).run(result.status,result.risk,result.score,JSON.stringify(result.findings),result.message,now(),snapshot.assessmentId,snapshot.translationId,snapshot.blockId,snapshot.sourceVersionId,snapshot.sourceHash,snapshot.translationHash,snapshot.contextHash).changes;
  if(changed!==1)throw new PipelineError('QE_CHANGED','평가 이력의 원문·번역 연결이 일치하지 않습니다.');return result;
}
export function qualityForTranslation(translationId:string,source:string,translation:string,contextHash?:string):QualityAssessment|null {
  const row=db().prepare(`SELECT * FROM translation_quality_assessments WHERE translation_id=? AND source_hash=? AND translation_hash=? ORDER BY created_at DESC,rowid DESC LIMIT 1`).get(translationId,hash(source),hash(translation)) as AssessmentRow|undefined;
  if(!row)return null;
  const findings=json<QualityFinding[]>(row.findings_json,[]);if(!validateFindings(source,translation,findings))return null;
  const stale=row.rules_version!==QUALITY_RULES_VERSION||row.model_identity!==qualityRuntime().identity||row.context_hash!==(contextHash??hash(buildContext(translationRow(translationId))));
  return {id:row.id,status:stale?'stale':row.status,risk:stale?'unknown':row.risk,sourceHash:row.source_hash,translationHash:row.translation_hash,modelIdentity:row.model_identity,calibrationVersion:row.calibration_version,score:row.score,findings:stale?[]:findings,message:stale?'의미 검사 설정이 변경되었습니다. 다시 검사할 수 있습니다.':row.message,createdAt:row.created_at,jobId:row.job_id??undefined,scope:'block'};
}
