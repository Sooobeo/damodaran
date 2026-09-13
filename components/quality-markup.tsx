import type { Translation } from '@/lib/client-types';
import type { QualityAssessment, QualityFinding, QualitySpan } from '@/lib/quality/types';
import { validSpan } from '@/lib/quality/rules';

export const qualityCategoryLabel: Record<QualityFinding['category'], string> = {
  quantity_formula:'수량·단위·연산 방향', semantic_role:'주체·대상', negation_condition:'부정·조건',
  word_sense:'용어 의미', omission:'내용 누락', general_low_confidence:'일반 저신뢰',
};
export function isReviewedTranslation(translation: Translation) {
  return ['user_reviewed','reviewed','approved'].includes(translation.reviewStatus);
}
export function visibleQuality(translation: Translation | null | undefined): QualityAssessment | null {
  return translation && !isReviewedTranslation(translation) ? translation.quality || null : null;
}
export function reviewFindings(assessment: QualityAssessment | null): QualityFinding[] {
  if (!hasReviewRisk(assessment)) return [];
  return assessment!.status === 'completed' ? assessment!.findings : assessment!.findings.filter(finding => finding.detector === 'rule');
}
export function hasReviewRisk(assessment: QualityAssessment | null): boolean {
  return !!assessment && ['completed','unavailable','failed'].includes(assessment.status) && assessment.risk === 'review';
}
// A validated source offset identifies evidence, not a bilingual phrase alignment.
// Always expand it to complete source sentences before showing the correspondence.
export function sourceSentence(text: string, finding: QualityFinding): QualitySpan | null {
  const span=finding.source;
  if (!span || !validSpan(text,span)) return null;
  const sentences=[...new Intl.Segmenter('en',{granularity:'sentence'}).segment(text)]
    .filter(part => part.index < span.end && part.index+part.segment.length > span.start);
  if (!sentences.length) return null;
  const start=sentences[0].index,end=sentences.at(-1)!.index+sentences.at(-1)!.segment.length;
  return {start,end,text:text.slice(start,end)};
}
export type QualityRange = {start:number;end:number;reason:string;severity:'warning'|'major'|'critical'|'source'};
export function qualityRanges(text:string,findings:QualityFinding[],side:'source'|'target'):QualityRange[] {
  return findings.flatMap(finding => {
    const span=side==='source'?sourceSentence(text,finding):finding.target;
    if (!span || !validSpan(text,span)) return [];
    return [{start:span.start,end:span.end,reason:qualityCategoryLabel[finding.category]+': '+finding.reason,severity:side==='source'?'source':finding.severity}];
  });
}
export function QualityText({text,offset=0,ranges=[]}:{text:string;offset?:number;ranges?:QualityRange[]}) {
  const matching=ranges.filter(range=>range.start<offset+text.length&&range.end>offset);
  if (!matching.length) return <>{text}</>;
  const boundaries=[...new Set([0,text.length,...matching.flatMap(range=>[Math.max(0,range.start-offset),Math.min(text.length,range.end-offset)])])].sort((a,b)=>a-b);
  const priority={source:0,warning:1,major:2,critical:3};
  return <>{boundaries.slice(0,-1).map((start,index)=>{
    const end=boundaries[index+1],covers=matching.filter(range=>range.start<=offset+start&&range.end>=offset+end);
    if(!covers.length)return text.slice(start,end);
    const severity=covers.reduce((best,range)=>priority[range.severity]>priority[best]?range.severity:best,covers[0].severity);
    return <mark key={start} className={`quality-mark quality-mark-${severity}`} title={[...new Set(covers.map(range=>range.reason))].join(' ')}>{text.slice(start,end)}</mark>;
  })}</>;
}
