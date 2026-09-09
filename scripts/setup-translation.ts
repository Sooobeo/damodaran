import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';

const root=path.resolve(process.env.APP_ROOT || path.join(path.dirname(fileURLToPath(import.meta.url)),'..'));
const runtime=path.join(root,'.translation');
const python=path.join(root,'.venv-translation',process.platform==='win32'?'Scripts/python.exe':'bin/python');
const helper=path.join(root,'scripts/local-translation/install.py');
const env={...process.env,APP_ROOT:root,PYTHONUTF8:'1',PIP_DISABLE_PIP_VERSION_CHECK:'1'};

function run(command:string,args:string[],capture=false):Promise<string>{
  return new Promise((resolve,reject)=>{
    const child=spawn(command,args,{cwd:root,env,windowsHide:true,stdio:['ignore',capture?'pipe':'inherit','inherit']});
    let output='';
    child.stdout?.on('data',chunk=>{output+=String(chunk);});
    child.on('error',()=>reject(new Error('Python 실행 파일을 찾을 수 없습니다. Python 3.11 이상을 설치해 주세요.')));
    child.on('exit',code=>code===0?resolve(output):reject(new Error(`번역 설치 단계가 실패했습니다 (종료 코드 ${code}).`)));
  });
}

let locked=false;
try{
  fs.mkdirSync(runtime,{recursive:true});
  fs.writeFileSync(path.join(runtime,'setup.lock'),`${process.pid}\n`,{flag:'wx'});locked=true;
  if(!fs.existsSync(python)){
    console.log('프로젝트 전용 Python 가상환경을 만듭니다.');
    let command=process.env.TRANSLATION_SETUP_PYTHON || (process.platform==='win32'?'py':'python3');
    let args=process.env.TRANSLATION_SETUP_PYTHON?[]:(process.platform==='win32'?['-3.11']:[]);
    await run(command,[...args,'-m','venv',path.join(root,'.venv-translation')]);
  }
  const manifest=path.join(runtime,'manifest.json');
  if(fs.existsSync(manifest)){
    console.log('설치된 로컬 번역기와 모델 무결성을 확인합니다.');
    try{await run(python,[helper,'--verify']);console.log('로컬 번역기가 이미 준비되어 있습니다.');fs.rmSync(path.join(runtime,'setup.lock'),{force:true});locked=false;process.exit(0);}catch{console.log('설치를 복구합니다.');}
  }
  console.log('무료 Argos Translate 실행 환경을 설치합니다. 최초 설치에는 인터넷이 필요합니다.');
  const lockfile=path.join(root,'scripts/local-translation/requirements.lock.txt');
  await run(python,['-m','pip','install','--no-compile','--only-binary=:all:','-r',path.join(root,'scripts/local-translation/requirements.txt'),...(fs.existsSync(lockfile)?['-c',lockfile]:[])]);
  const freeze=await run(python,['-m','pip','freeze','--all'],true);
  fs.writeFileSync(path.join(runtime,'requirements.lock.txt'),freeze,'utf8');
  await run(python,[helper]);
  // The first successful environment becomes the reproducible install contract.
  // Future dependency upgrades must update both direct pins and this lock file.
  if(!fs.existsSync(lockfile))fs.writeFileSync(lockfile,freeze,'utf8');
  console.log('영어 → 한국어 로컬 번역기 설치 완료. 앱과 worker를 재시작하면 사용할 수 있습니다.');
}catch(error){
  console.error(error instanceof Error?error.message:'로컬 번역기 설치에 실패했습니다.');process.exitCode=1;
}finally{
  if(locked)fs.rmSync(path.join(runtime,'setup.lock'),{force:true});
}
