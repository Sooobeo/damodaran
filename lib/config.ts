import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';
import { z } from 'zod';

// APP_ROOT is passed by every launcher, with a stable source-file fallback for CLI.
const sourceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const APP_ROOT = process.env.APP_ROOT || (fs.existsSync(path.join(sourceRoot, 'package.json')) ? sourceRoot : process.cwd());
dotenv.config({path:path.join(APP_ROOT,'.env.local'),quiet:true});
dotenv.config({path:path.join(APP_ROOT,'.env'),quiet:true});
const positive = (fallback:number) => z.coerce.number().int().positive().default(fallback);
const schema = z.object({
  APP_HOST:z.literal('127.0.0.1').default('127.0.0.1'), APP_PORT:positive(3000).pipe(z.number().max(65535)),
  DATA_DIR:z.string().default('./data'), OPENAI_API_KEY:z.string().default(''),
  TRANSLATION_PROVIDER:z.enum(['argos','finetuned','openai']).default('argos'),TRANSLATION_MODEL:z.string().default(''),
  EXTRA_SOURCE_HOSTS:z.string().default('').refine(value=>value.split(',').map(v=>v.trim()).filter(Boolean).every(host=>host==='people.stern.nyu.edu'),'지원하는 추가 NYU 호스트만 등록할 수 있습니다.'),
  MAX_SOURCE_CHARS_PER_JOB:positive(20000),MAX_SOURCE_CHARS_PER_DAY:positive(100000),
  MAX_DOWNLOAD_BYTES:positive(52428800),MAX_PDF_PAGES:positive(1000),WORKER_CONCURRENCY:z.coerce.number().refine(n=>n===1).default(1)
});
export const config = schema.parse(process.env);
export const DATA_DIR = path.resolve(APP_ROOT,config.DATA_DIR);
export function dataPath(relative:string) {
  if(path.isAbsolute(relative)) throw new Error('절대 파일 경로는 허용되지 않습니다.');
  const target = path.resolve(DATA_DIR,relative);
  if(!target.startsWith(DATA_DIR+path.sep)) throw new Error('잘못된 파일 경로입니다.');
  // Reject existing symlink/junction escapes as well as lexical traversal.
  if(fs.existsSync(target) && !fs.realpathSync(target).startsWith(fs.realpathSync(DATA_DIR)+path.sep)) throw new Error('허용 범위 밖의 파일입니다.');
  return target;
}
export function ensureDataDirs() { for(const folder of ['', 'originals','derived','backups','tmp']) fs.mkdirSync(path.join(DATA_DIR,folder),{recursive:true}); }
