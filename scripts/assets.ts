import fs from 'node:fs';
import path from 'node:path';
import { APP_ROOT } from '../lib/config';
export function copyAssets(){
  const pdf=path.join(APP_ROOT,'node_modules/pdfjs-dist');const dest=path.join(APP_ROOT,'public');fs.mkdirSync(dest,{recursive:true});
  fs.copyFileSync(path.join(pdf,'build/pdf.worker.min.mjs'),path.join(dest,'pdf.worker.min.mjs'));
  for(const folder of ['cmaps','standard_fonts','wasm']){const src=path.join(pdf,folder);if(fs.existsSync(src))fs.cpSync(src,path.join(dest,'pdfjs',folder),{recursive:true});}
}
copyAssets();
