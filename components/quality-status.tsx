'use client';

import { useState } from 'react';
import { AlertTriangle, Check, Info, LoaderCircle, Pencil, Search } from 'lucide-react';
import type { Translation } from '@/lib/client-types';
import { validSpan } from '@/lib/quality/rules';
import { hasReviewRisk, isReviewedTranslation, qualityCategoryLabel, reviewFindings, sourceSentence, visibleQuality } from './quality-markup';

export default function QualityStatus({translation,source,blockOrder,structured=false,onRequest,onEdit}:{translation:Translation;source:string;blockOrder:number;structured?:boolean;onRequest:()=>Promise<void>;onEdit?:()=>void}) {
  const [requesting,setRequesting]=useState(false),[error,setError]=useState('');
  if(isReviewedTranslation(translation))return null;
  const quality=visibleQuality(translation),findings=reviewFindings(quality);
  const pending=requesting||quality?.status==='queued'||quality?.status==='running';
  const risk=hasReviewRisk(quality),partialRisk=risk&&quality?.status!=='completed';
  const clear=quality?.status==='completed'&&quality.risk==='no_findings';
  const status=requesting?'의미 검사 요청 중':quality?.status==='queued'?'의미 검사 대기 중':quality?.status==='running'?'의미 검사 중':quality?.status==='unavailable'?(structured?'표 의미 검사 미지원':'의미 검사 모델 미설치'):quality?.status==='failed'?'의미 검사 실패':risk?'자동 의미 검사: 검토 권장':clear?'자동 검사상 특이점 없음':quality?.status==='cancelled'?'의미 검사 취소됨':quality?.status==='stale'?'의미 검사 결과가 오래되었습니다':quality?.status==='completed'?'의미 검사 결과 확인 필요':'자동 의미 검사 전';
  const explanation=clear?'등록된 검사 범위에서 확인했습니다. 의미 오류가 없음을 보증하지 않습니다.':partialRisk?quality?.message||'의미 검사 모델의 평가를 완료하지 못했습니다. 확인된 규칙 경고는 아래에 표시합니다.':risk?'문단 단위 검사입니다. 원문과 근거를 대조해 주세요.':quality?.message||'저장된 번역을 선택해 검사할 수 있습니다.';
  async function request() {
    if(pending)return;
    setRequesting(true);setError('');
    try{await onRequest();}catch(error){setError(error instanceof Error?error.message:'의미 검사를 요청하지 못했습니다.');}finally{setRequesting(false);}
  }
  return <section className={`quality-status ${risk?'quality-status-review':''}`} aria-label={`${blockOrder+1}번째 문단 자동 의미 검사`} onClick={event=>event.stopPropagation()}>
    <div className="quality-status-heading" role="status" aria-live="polite">{pending?<LoaderCircle size={15} className="spin" aria-hidden="true"/>:risk?<AlertTriangle size={15} aria-hidden="true"/>:clear?<Check size={15} aria-hidden="true"/>:<Info size={15} aria-hidden="true"/>}<strong>{status}</strong><span>문단 단위</span></div>
    {partialRisk&&<p className="quality-partial-warning"><strong>규칙 검사: 검토 권장</strong> · 의미 검사 모델의 평가와 별개입니다.</p>}
    <p className="quality-status-description">{explanation}</p>
    {risk&&<details className="quality-details"><summary>검사 근거 보기{findings.length?` · ${findings.length}건`:''}</summary><div className="quality-findings">
      {!findings.length&&<p>낮은 신뢰도로 문단 전체를 표시했습니다. 오류가 있는 단어나 문장을 특정한 결과는 아닙니다.</p>}
      {findings.map((finding,index)=>{const english=sourceSentence(source,finding),target=finding.target&&validSpan(translation.textKo,finding.target)?finding.target:null;return <div className="quality-finding" key={index}>
        <div className="quality-finding-heading"><AlertTriangle size={14} aria-hidden="true"/><strong>{qualityCategoryLabel[finding.category]}</strong><span>{finding.severity==='critical'?'심각':finding.severity==='major'?'중요':'검토 권장'}</span></div>
        <p>{finding.reason}</p>
        {target?<div className="quality-evidence"><small>검토할 한국어 구간</small><q lang="ko">{target.text}</q></div>:<p className="quality-unlocated">오류 위치를 특정하지 않아 문단 전체를 표시했습니다.</p>}
        {english?<div className="quality-evidence"><small>대응하는 원문 문장 전체</small><blockquote lang="en">{english.text}</blockquote></div>:<p className="quality-unlocated">대응 문장을 특정하지 못했습니다. 원문 문단 전체와 대조해 주세요.</p>}
      </div>;})}
    </div></details>}
    <div className="quality-actions">{!quality||['unavailable','failed','cancelled','stale'].includes(quality.status)?<button className="text-link" type="button" disabled={pending} aria-busy={requesting} onClick={()=>void request()}><Search size={13}/>{requesting?'요청 중…':quality?'의미 검사 다시 요청':'의미 검사'}</button>:null}{risk&&onEdit&&<button className="text-link" type="button" onClick={onEdit}><Pencil size={13}/>원문과 비교하며 번역 수정</button>}</div>
    {error&&<p className="inline-error" role="alert">{error}</p>}
  </section>;
}
