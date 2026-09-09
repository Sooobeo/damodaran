import { restore } from '../lib/backup';
const [source,target]=process.argv.slice(2);
try{if(!source||!target)throw new Error('사용법: npm run restore -- <백업 폴더> <존재하지 않는 새 복원 폴더>');console.log(JSON.stringify(restore(source,target),null,2));}catch(e){console.error(e instanceof Error?e.message:'복원 실패');process.exitCode=1;}
