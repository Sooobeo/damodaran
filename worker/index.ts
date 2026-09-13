import { runOnce } from '../lib/jobs';
import { closeDb } from '../lib/db';
import { translationRuntime } from '../lib/translation/runtime';
import { closeLocalTranslator } from '../lib/translation/local';
import { closeQualityEvaluator } from '../lib/quality/runtime';

let stopping=false;
process.on('SIGINT',()=>{stopping=true;});process.on('SIGTERM',()=>{stopping=true;});
process.on('message',(message:unknown)=>{if(message&&typeof message==='object'&&'type' in message&&message.type==='shutdown')stopping=true;});
console.log(`작업 처리기를 시작했습니다. ${translationRuntime().providerLabel}: ${translationRuntime().configured?'준비됨':'설치 또는 설정 필요'}`);
try{
  while(!stopping){try{const worked=await runOnce();if(!worked)await new Promise(resolve=>setTimeout(resolve,1000));}catch{console.error('작업 처리 중 오류가 발생했습니다. 저장된 작업 상태에서 다시 시도합니다.');await new Promise(resolve=>setTimeout(resolve,2000));}}
}finally{try{try{await closeQualityEvaluator();}finally{await closeLocalTranslator();}}finally{closeDb();if(process.connected)process.disconnect?.();}console.log('작업 처리기를 종료했습니다.');}
