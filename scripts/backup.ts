import { backup } from '../lib/backup';
import { closeDb } from '../lib/db';
try{console.log(JSON.stringify(await backup(),null,2));}catch(e){console.error(e instanceof Error?e.message:'백업 실패');process.exitCode=1;}finally{closeDb();}
