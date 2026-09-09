import { spawn,type ChildProcess } from 'node:child_process';
import path from 'node:path';
import { config,APP_ROOT } from '../lib/config';
import { db,closeDb } from '../lib/db';
db();closeDb();
const mode=process.argv[2]==='start'?'start':'dev';const children:ChildProcess[]=[];let worker:ChildProcess|undefined;
const common={cwd:APP_ROOT,env:{...process.env,APP_ROOT},stdio:'inherit' as const,windowsHide:true};
const nextArgs=[path.join(APP_ROOT,'node_modules/next/dist/bin/next'),mode,'--hostname',config.APP_HOST,'--port',String(config.APP_PORT)];if(mode==='dev')nextArgs.push('--webpack');
children.push(spawn(process.execPath,nextArgs,common));
if(process.env.WEB_ONLY!=='1'){worker=spawn(process.execPath,['--import','tsx',path.join(APP_ROOT,'worker/index.ts')],{...common,stdio:['inherit','inherit','inherit','ipc']});children.push(worker);}
let exiting=false;
async function shutdown(code=0){
  if(exiting)return;exiting=true;
  if(worker?.connected)worker.send({type:'shutdown'});
  if(worker&&worker.exitCode===null)await Promise.race([new Promise(resolve=>worker!.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,2000))]);
  await Promise.all(children.filter(child=>child.pid&&child.exitCode===null).map(child=>new Promise<void>(resolve=>{
    if(process.platform==='win32'){
      // These are the PIDs spawned above, never broad process-name matching.
      const stop=spawn('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});stop.once('exit',()=>resolve());stop.once('error',()=>resolve());
    }else{child.once('exit',()=>resolve());child.kill('SIGTERM');setTimeout(()=>{if(child.exitCode===null)child.kill('SIGKILL');resolve();},2000).unref();}
  })));
  process.exit(code);
}
for(const child of children){child.on('error',()=>shutdown(1));child.on('exit',code=>shutdown(code||0));}
process.on('SIGINT',()=>shutdown());process.on('SIGTERM',()=>shutdown());
process.on('message',(message:unknown)=>{if(message&&typeof message==='object'&&'type' in message&&message.type==='shutdown')void shutdown();});
// No detached or visible command windows; web and worker share this runner's lifetime.
