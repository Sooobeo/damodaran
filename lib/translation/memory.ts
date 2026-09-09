import { z } from 'zod';
import { db, hash, id, now } from '../db';
import { PipelineError } from '../sources';
import { getTranslationForBlock, translationSnapshot, validatePreserved, type TranslationSnapshot } from './index';

export type TranslationReview={id:string;translation_id:string;parent_review_id:string|null;source_text:string;source_hash:string;context_hash:string;glossary_version:string;block_type:string;text_ko:string;created_at:string};
export function linkedReview(translationId:string){
  return db().prepare('SELECT r.* FROM translation_review_links l JOIN translation_reviews r ON r.id=l.review_id WHERE l.translation_id=?').get(translationId) as TranslationReview|undefined;
}
const reviewSchema=z.object({translationId:z.string().min(1),textKo:z.string().trim().min(1).max(50000),expectedReviewId:z.string().min(1).nullable().default(null)}).strict();
export function saveTranslationReview(input:unknown){
  const value=reviewSchema.parse(input);
  const blockId=db().transaction(()=>{
    const original=db().prepare('SELECT id,block_id,structure_json FROM translations WHERE id=?').get(value.translationId) as {id:string;block_id:string;structure_json:string|null}|undefined;
    if(!original)throw new PipelineError('NOT_FOUND','수정할 번역을 찾을 수 없습니다.');
    if(getTranslationForBlock(original.block_id)?.id!==original.id)throw Object.assign(new PipelineError('REVIEW_CONFLICT','현재 표시할 번역이 변경되었습니다. 작성 내용을 유지한 채 최신 번역과 비교해 주세요.'),{status:409});
    const snapshot=translationSnapshot(original.block_id);
    if(snapshot.type==='table'||snapshot.type==='image'||original.structure_json)throw new PipelineError('STRUCTURED_REVIEW_UNSUPPORTED','표는 셀 구조를 유지해야 하므로 현재 문단 수정 대상에서 제외됩니다.');
    const parent=linkedReview(original.id);
    if((parent?.id??null)!==value.expectedReviewId)throw Object.assign(new PipelineError('REVIEW_CONFLICT','다른 창에서 검수한 내용이 있습니다. 작성 내용을 복사한 뒤 문단을 새로 불러와 확인하세요.'),{status:409});
    const warnings=validatePreserved(snapshot.text,value.textKo);
    if(/__PV_[a-f0-9]+_\d+__/.test(value.textKo))warnings.push('내부 보호 표식이 남아 있습니다.');
    const formulas=snapshot.text.match(/(?:\b[A-Za-z][A-Za-z0-9_]*(?:\([^\n]{0,40}\))?\s*=\s*[^\n;]{1,180})/g)||[];
    for(const formula of formulas)if(!value.textKo.replace(/\s+/g,' ').includes(formula.replace(/\s+/g,' ')))warnings.push('원문의 수식을 그대로 유지해 주세요.');
    if(warnings.length)throw new PipelineError('REVIEW_VALIDATION_FAILED',[...new Set(warnings)].join(' '));
    if(parent?.text_ko===value.textKo&&parent.context_hash===snapshot.contextHash&&parent.glossary_version===snapshot.glossaryVersion)return original.block_id;
    const reviewId=id();
    db().prepare('INSERT INTO translation_reviews(id,translation_id,parent_review_id,source_text,source_hash,context_hash,glossary_version,block_type,text_ko,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)')
      .run(reviewId,original.id,parent?.id??null,snapshot.text,hash(snapshot.text),snapshot.contextHash,snapshot.glossaryVersion,snapshot.type,value.textKo,now());
    db().prepare('INSERT INTO translation_review_links VALUES(?,?) ON CONFLICT(translation_id) DO UPDATE SET review_id=excluded.review_id').run(original.id,reviewId);
    return original.block_id;
  }).immediate();
  return getTranslationForBlock(blockId)!;
}

// Reviewed text is reused only for exactly the same source, neighboring context,
// glossary and block kind. Similar sentences are never silently substituted.
export function reuseReviewedTranslation(snapshot:TranslationSnapshot):{id:string}|undefined{
  if(snapshot.type==='table'||snapshot.type==='image')return;
  const review=db().prepare(`SELECT * FROM translation_reviews WHERE source_hash=? AND source_text=? AND context_hash=? AND glossary_version=? AND block_type=? ORDER BY rowid DESC LIMIT 1`)
    .get(hash(snapshot.text),snapshot.text,snapshot.contextHash,snapshot.glossaryVersion,snapshot.type) as TranslationReview|undefined;
  if(!review||validatePreserved(snapshot.text,review.text_ko).length)return;
  const cached=db().prepare('SELECT id FROM translations WHERE cache_key=?').get(snapshot.cacheKey) as {id:string}|undefined;
  const translationId=cached?.id??id();
  if(!cached)db().prepare(`INSERT INTO translations(id,block_id,cache_key,text_ko,provider,model,prompt_version,glossary_version,context_hash,generation_status,validation_status,review_status,usage_json,created_at)
    VALUES(?,?,?,?,'memory','user-reviewed-v1',?,?,?,'ready','passed','user_reviewed',?,?)`)
    .run(translationId,snapshot.blockId,snapshot.cacheKey,review.text_ko,snapshot.promptVersion,snapshot.glossaryVersion,snapshot.contextHash,JSON.stringify({reviewId:review.id,warnings:[]}),now());
  db().prepare('INSERT INTO translation_review_links VALUES(?,?) ON CONFLICT(translation_id) DO UPDATE SET review_id=excluded.review_id').run(translationId,review.id);
  return {id:translationId};
}

export function translationReviewHistory(translationId:string){
  if(!db().prepare('SELECT id FROM translations WHERE id=?').get(translationId))throw new PipelineError('NOT_FOUND','번역을 찾을 수 없습니다.');
  return db().prepare('SELECT id,parent_review_id as parentReviewId,text_ko as textKo,created_at as createdAt FROM translation_reviews WHERE translation_id=? ORDER BY rowid DESC').all(translationId);
}
export function reviewedTranslationPairs(){
  const reviews=db().prepare(`SELECT r.*,v.resource_id,v.id source_version_id,b.id block_id,s.canonical_url FROM translation_reviews r JOIN translations t ON t.id=r.translation_id JOIN source_blocks b ON b.id=t.block_id JOIN source_versions v ON v.id=b.source_version_id JOIN resources s ON s.id=v.resource_id ORDER BY r.rowid DESC`).all() as (TranslationReview&{resource_id:string;source_version_id:string;block_id:string;canonical_url:string|null})[];
  const seen=new Set<string>();
  return reviews.filter(review=>{const key=JSON.stringify([review.source_text,review.context_hash,review.glossary_version,review.block_type]);if(seen.has(key))return false;seen.add(key);return true;})
    .map(review=>({source:review.source_text,target:review.text_ko,sourceLanguage:'en',targetLanguage:'ko',reviewStatus:'user_reviewed',reviewId:review.id,resourceId:review.resource_id,sourceVersionId:review.source_version_id,blockId:review.block_id,sourceUrl:review.canonical_url,contextHash:review.context_hash,glossaryVersion:review.glossary_version,reviewedAt:review.created_at}));
}
