import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { checkMeaningRules, QUALITY_RULES_VERSION, validateFindings } from '../lib/quality/rules';
const root=path.resolve('.training/quality-evaluation/dev48-v1');
const sha=(bytes:Buffer|string)=>createHash('sha256').update(bytes).digest('hex');
const lines=(file:string)=>fs.readFileSync(path.join(root,file),'utf8').trim().split(/\r?\n/).map(line=>JSON.parse(line));
const inputs=lines('inputs.jsonl'),labels=new Map(lines('judgments.jsonl').map(row=>[row.id,row]));
if(inputs.length!==48||labels.size!==48)throw new Error('고정 48개 입력·판정이 필요합니다.');
const rows=inputs.map(row=>{
  const label=labels.get(row.id);if(!label||sha(row.source)!==row.sourceSha256||sha(row.translation)!==row.translationSha256||label.sourceSha256!==row.sourceSha256||label.translationSha256!==row.translationSha256)throw new Error('원문·번역·판정 해시 불일치');
  const findings=checkMeaningRules(row.source,row.translation);if(!validateFindings(row.source,row.translation,findings))throw new Error('규칙 구간 불일치');
  return {id:row.id,materialError:label.materialError,namedError:label.namedError,warning:findings.length>0,findings};
});
const tp=rows.filter(r=>r.materialError&&r.warning).length,fp=rows.filter(r=>!r.materialError&&r.warning).length,fn=rows.filter(r=>r.materialError&&!r.warning).length;
const report={createdAt:new Date().toISOString(),rulesVersion:QUALITY_RULES_VERSION,sourceFiles:['inputs.jsonl','judgments.jsonl'].map(file=>({path:path.join(root,file),sha256:sha(fs.readFileSync(path.join(root,file)))})),rulesSha256:sha(fs.readFileSync('lib/quality/rules.ts')),count:rows.length,truePositive:tp,falsePositive:fp,falseNegative:fn,precision:tp+fp?tp/(tp+fp):null,recall:tp/(tp+fn),warningRate:(tp+fp)/rows.length,humanReviewed:false,independentFinalCertification:false,limitation:'이미 알려진 오류를 포함한 작은 도우미 개발 자료입니다. 규칙의 전체 의미 탐지율이나 독립 품질 인증이 아닙니다.',rows};
const output=process.argv[2]??'.training/verifications/quality-rules-dev48-20260911.json';if(fs.existsSync(output))throw new Error('기존 검사 기록은 덮어쓰지 않습니다. 다른 출력 경로를 지정하세요.');fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(report,null,2));
console.log(JSON.stringify({output,count:rows.length,truePositive:tp,falsePositive:fp,falseNegative:fn,precision:report.precision,recall:report.recall,warningRate:report.warningRate,detected:rows.filter(r=>r.warning).map(r=>({id:r.id,rules:r.findings.map(f=>f.ruleId)}))},null,2));
