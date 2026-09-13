import type { QualityFinding, QualitySpan } from './types';

export const QUALITY_RULES_VERSION = 'finance-meaning-rules-v1';
const categories = new Set(['quantity_formula','semantic_role','negation_condition','word_sense','omission','general_low_confidence']);
export function validSpan(text: string, span: QualitySpan | null): boolean {
  if (span === null) return true;
  return !!span && typeof span==='object' && Number.isInteger(span.start) && Number.isInteger(span.end) && span.start >= 0 && span.end > span.start && span.end <= text.length
    && text.slice(span.start, span.end) === span.text
    && !/[\uDC00-\uDFFF]/.test(text[span.start] || '') && !/[\uDC00-\uDFFF]/.test(text[span.end] || '');
}
export function validateFindings(source: string, target: string, findings: QualityFinding[]): boolean {
  return Array.isArray(findings) && findings.length <= 100 && findings.every(f => f && categories.has(f.category)
    && ['warning','major','critical'].includes(f.severity) && ['rule','qe'].includes(f.detector)
    && typeof f.reason === 'string' && f.reason.length > 0 && f.reason.length <= 1000
    && validSpan(source, f.source) && validSpan(target, f.target)
    && (f.severity === 'warning' || f.target !== null));
}
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
function span(text: string, start: number, length: number): QualitySpan { return { start, end: start + length, text: text.slice(start,start+length) }; }
function sentence(text: string, position: number): QualitySpan {
  for (const part of new Intl.Segmenter('en', { granularity: 'sentence' }).segment(text)) {
    if (part.index <= position && position < part.index + part.segment.length) return span(text,part.index,part.segment.length);
  }
  return span(text,0,text.length);
}
// These are narrow, observable bilingual relations. Absence of a match is not a semantic pass.
export function checkMeaningRules(source: string, target: string): QualityFinding[] {
  const findings: QualityFinding[] = [];
  const singleAffirmativeDivision=(source.match(/\bdivid(?:ing|e)\b/gi)||[]).length===1
    && !/\b(?:not|never|avoid|incorrect(?:ly)?|wrong(?:ly)?)\b/i.test(source) && !/(?:않|말아|말고|금지|잘못|아니라)/.test(target);
  const add = (ruleId: string, category: QualityFinding['category'], severity: QualityFinding['severity'], reason: string, sm: RegExpMatchArray, tm: RegExpMatchArray) => {
    findings.push({ruleId,category,severity,reason,detector:'rule',source:sentence(source,sm.index!),target:span(target,tm.index!,tm[0].length)});
  };
  // A numeric dividend divided by an explicitly named rate, translated with the rate as dividend.
  for (const sm of source.matchAll(/divid(?:ing|e)\s+(\d+(?:\.\d+)?)\s+by\s+(?:the\s+)?(?:discount\s+or\s+)?(?:interest|discount)\s+rate\b/gi)) {
    if(!singleAffirmativeDivision)continue;
    const tm = target.match(new RegExp(`(?:할인율(?:\\s*(?:또는|이나|혹은)\\s*이자율)?|이자율)(?:을|를)\\s*${escape(sm[1])}(?:으)?로\\s*나(?:누|눈|눠)`));
    if (tm) add('rate-division-direction','quantity_formula','critical','원문은 숫자를 이자율로 나누지만 번역은 이자율을 그 숫자로 나누는 방향입니다.',sm,tm);
  }
  for (const sm of source.matchAll(/divid(?:ing|e)\s+(\d+(?:\.\d+)?)\s+by\s+(\d+(?:\.\d+)?)(?!\d|\.\d)/gi)) {
    if(!singleAffirmativeDivision)continue;
    if (sm[1] === sm[2]) continue;
    const tm=target.match(new RegExp(`${escape(sm[2])}(?:을|를)\\s*${escape(sm[1])}(?:으)?로\\s*나(?:누|눈|눠)`));
    if(tm)add('numeric-division-direction','quantity_formula','critical','나눗셈의 나누는 수와 나누어지는 수가 원문과 반대입니다.',sm,tm);
  }
  for (const sm of source.matchAll(/(?:increas(?:es?|ed|ing)|rais(?:es?|ed|ing))\b[^.!?\n]{0,70}\bby\s+(\d+(?:\.\d+)?)\s*%(?!\s*(?:point|p\b))/gi)) {
    const tm=target.match(new RegExp(`${escape(sm[1])}\\s*(?:%\\s*(?:포인트|p\\b)|퍼센트포인트)`,'i'));
    if(tm)add('relative-percent-as-points','quantity_formula','major','원문의 상대 증가율(%)이 번역에서 퍼센트포인트 증가로 바뀌었습니다.',sm,tm);
  }
  const equity = source.match(/\b(?:debt\s+and\s+equity|equity\s+(?:financing|capital)|financing\b[^.!?\n]{0,60}\bequity)\b/i);
  const wrongEquity=!/(?:자본|지분|주식)/.test(target)?target.match(/공평성|형평성|부채(?:와|\s*(?:및|과))\s*균형/):null;
  if(equity&&wrongEquity)add('financing-equity-sense','word_sense','major','자금 조달의 equity가 공평성·균형을 뜻하는 표현으로 옮겨졌을 가능성이 있습니다. 원문과 확인하세요.',equity,wrongEquity);
  const bills=source.match(/\bTreasury\s+bills\b/i), longBonds=target.match(/장기\s*(?:국채|국고채)/);
  if(bills&&longBonds&&!/\b(?:long[- ]term|Treasury\s+bonds)\b/i.test(source))add('treasury-bill-maturity','word_sense','major','원문의 단기 Treasury bills가 장기 국채로 옮겨졌습니다.',bills,longBonds);
  return findings;
}
