import fs from 'node:fs';
import path from 'node:path';
import { DATA_DIR,ensureDataDirs } from '../lib/config';
import { closeDb,id,now } from '../lib/db';
import { reviewedTranslationPairs } from '../lib/translation/memory';

try{
  ensureDataDirs();const pairs=reviewedTranslationPairs();
  const destination=path.join(DATA_DIR,'derived',`reviewed-translations-${now().replace(/[:.]/g,'-')}-${id().slice(0,8)}.jsonl`);
  fs.writeFileSync(destination,pairs.map(pair=>JSON.stringify(pair)).join('\n')+(pairs.length?'\n':''),{encoding:'utf8',flag:'wx'});
  console.log(JSON.stringify({path:destination,pairs:pairs.length,modelTrainingStarted:false},null,2));
}catch(error){console.error(error instanceof Error?error.message:'검수 번역을 내보내지 못했습니다.');process.exitCode=1;}finally{closeDb();}
