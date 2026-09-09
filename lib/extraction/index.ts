import * as cheerio from 'cheerio';
import iconv from 'iconv-lite';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { APP_ROOT, config } from '../config';
import { PipelineError } from '../sources';

export type ExtractedBlock={type:string;text:string;pageIndex?:number;bbox?:number[];structure?:Record<string,unknown>;warnings:string[]};
export type ExtractedDocument={title:string|null;blocks:ExtractedBlock[];links:Array<{url:string;text:string}>;images:Array<{url:string;alt:string}>;pageCount:number|null;warnings:string[];status:'ready'|'partial'|'ocr_needed'};
const clean=(s:string)=>s.replace(/\u00a0/g,' ').replace(/[\t\r ]+/g,' ').replace(/ *\n */g,'\n').trim();
function safeLink(href:string,base:string):string|null {try{const u=new URL(href,base);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?u.href:null;}catch{return null;}}
export function decodeHtml(bytes:Buffer,contentType='') {
  const preview=bytes.subarray(0,8192).toString('latin1');
  const declared=contentType.match(/charset\s*=\s*["']?([^;\s"']+)/i)?.[1]||preview.match(/charset\s*=\s*["']?([^;\s"'/>]+)/i)?.[1];
  const encoding=declared&&iconv.encodingExists(declared)?declared:bytes.toString('utf8').includes('\ufffd')?'windows-1252':'utf8';
  return iconv.decode(bytes,encoding);
}
export function extractHtml(bytes:Buffer,finalUrl:string,contentType=''):ExtractedDocument {
  const $=cheerio.load(decodeHtml(bytes,contentType));
  const title=clean($('title').first().text())||null;
  $('script,style,iframe,video,audio,object,embed,noscript,nav,footer,form,.comments,#comments,.post-footer,.blog-pager,.sidebar,.navbar').remove();
  const root=$('.post-body').first().length?$('.post-body').first():$('article').first().length?$('article').first():$('body');
  const blocks:ExtractedBlock[]=[],links:Array<{url:string;text:string}>=[],images:Array<{url:string;alt:string}>=[];
  const seenLinks=new Set<string>();root.find('a[href]').each((_i,e)=>{const href=$(e).attr('href')||'';if(href.startsWith('#'))return;const url=safeLink(href,finalUrl);if(url&&!seenLinks.has(url)){seenLinks.add(url);links.push({url,text:clean($(e).text())||url.split('/').pop()||url});}});
  function add(type:string,text:string,structure?:Record<string,unknown>){
    text=type==='table'?text.trim():clean(text);if(!text&&type!=='image')return;
    if(text.length>6000&&type!=='table'){
      let remaining=text;while(remaining.length>6000){let cut=remaining.lastIndexOf(' ',5900);if(cut<3000)cut=5900;blocks.push({type,text:remaining.slice(0,cut),structure,warnings:['긴 문단을 읽기 단위로 분할했습니다.']});remaining=remaining.slice(cut).trim();}if(remaining)blocks.push({type,text:remaining,structure,warnings:[]});
    }else blocks.push({type,text,structure,warnings:[]});
  }
  // NYU comparison tables often contain long text, no numbers, and no <th>.
  // A leaf table with a repeated cell grid is preserved regardless of cell content.
  // Outer one-row/nested tables are traversed as legacy page layout.
  function isDataTable(el:any){const table=$(el),rows=table.find('tr').filter((_i,row)=>$(row).parents('table').first()[0]===el);return !table.find('table').length&&rows.length>1&&rows.toArray().filter(row=>$(row).children('td,th').length>=2).length>=2;}
  function textContent(el:any){
    const node=$(el).clone();node.find('img').remove();node.find('br').replaceWith('\n');
    node.find('sup,sub').each((_i,script)=>{const replacement=$('<span>').text(`${script.tagName==='sup'?'^':'_'}(${$(script).text().trim()})`);$(script).replaceWith(replacement);});
    return node.text();
  }
  function inlineLinks(el:any){return $(el).find('a[href]').toArray().flatMap(a=>{const url=safeLink($(a).attr('href')||'',finalUrl);return url?[{text:clean($(a).text()),url}]:[];});}
  function addImage(el:any){const src=$(el).attr('src'),alt=clean($(el).attr('alt')||'원문 이미지');if(!src)return;const url=safeLink(src,finalUrl);if(!url)return;images.push({url,alt});add('image',alt,{schemaVersion:1,sourceUrl:url,alt});}
  function visit(el:any){
    if(el.type==='text'){const t=clean(el.data||'');if(t)add('paragraph',t);return;}
    if(el.type!=='tag')return;
    const tag=el.name.toLowerCase();
    if(tag==='img'){addImage(el);return;}
    if(tag==='table'&&isDataTable(el)){
      const rows=$(el).find('tr').toArray().map(row=>({cells:$(row).children('td,th').toArray().map(cell=>({text:clean(textContent(cell)),colSpan:Math.min(100,Math.max(1,Number($(cell).attr('colspan'))||1)),rowSpan:Math.min(100,Math.max(1,Number($(cell).attr('rowspan'))||1)),header:cell.tagName==='th'}))}));
      add('table',rows.map(r=>r.cells.map(c=>c.text).join('\t')).join('\n'),{schemaVersion:1,rows});$(el).find('img').each((_i,img)=>addImage(img));return;
    }
    if((/^h[1-6]$/.test(tag)||['p','li','blockquote','pre'].includes(tag))&&!$(el).find('table,img').length){
      add(/^h/.test(tag)?'heading':tag==='li'?'list_item':tag==='pre'?'formula':'paragraph',textContent(el),{schemaVersion:1,level:/^h/.test(tag)?Number(tag[1]):undefined,links:inlineLinks(el)});return;
    }
    // Inline runs belong to one paragraph, avoiding a block per font/span/link.
    let pending='';const flush=()=>{if(clean(pending))add('paragraph',pending);pending='';};
    for(const child of el.children||[]){if(child.type==='text')pending+=child.data;else if(child.type==='tag'&&['a','span','font','b','strong','i','em','u','small','sup','sub','br'].includes(child.name)&&!$(child).find('table,img,p,div,h1,h2,h3,h4,h5,h6,li,blockquote,pre').length){pending+=child.name==='br'?'\n':child.name==='sup'?`^(${$(child).text().trim()})`:child.name==='sub'?`_(${$(child).text().trim()})`:textContent(child);}else{flush();visit(child);}}flush();
  }
  root.toArray().forEach(visit);
  const warnings:string[]=[];
  if(!blocks.some(b=>b.text.length>15))throw new PipelineError('EMPTY_HTML','HTML에서 읽을 본문을 찾지 못했습니다. 원문을 확인하세요.');
  const allText=blocks.slice(0,5).map(b=>b.text).join(' ');if(blocks.length<8&&/access denied|page not found|404 not found|request blocked|forbidden/i.test(allText))throw new PipelineError('ERROR_HTML','원문 대신 오류 안내 HTML을 받았습니다.');
  return {title,blocks,links:links.slice(0,300),images,pageCount:null,warnings,status:'ready'};
}

export async function extractPdf(bytes:Buffer):Promise<ExtractedDocument> {
  const pdfjs=await import('pdfjs-dist/legacy/build/pdf.mjs');
  const task=pdfjs.getDocument({data:new Uint8Array(bytes),isEvalSupported:false,useSystemFonts:true,disableFontFace:true,standardFontDataUrl:pathToFileURL(path.join(APP_ROOT,'node_modules/pdfjs-dist/standard_fonts/')).href});
  let pdf;try{pdf=await task.promise;}catch{throw new PipelineError('INVALID_PDF','PDF를 열 수 없습니다. 암호 또는 손상 여부를 확인하세요.');}
  if(pdf.numPages>config.MAX_PDF_PAGES){await pdf.destroy();throw new PipelineError('PDF_TOO_LONG','PDF 최대 페이지 수를 넘었습니다.');}
  const blocks:ExtractedBlock[]=[],warnings:string[]=[];let blank=0,failed=0,title:string|null=null;
  try{
    try{const metadata=await pdf.getMetadata();const raw=(metadata.info as {Title?:unknown}).Title;title=typeof raw==='string'?raw:null;}catch{/* Metadata is optional. */}
    for(let pageIndex=0;pageIndex<pdf.numPages;pageIndex++){
      try{
        const page=await pdf.getPage(pageIndex+1),viewport=page.getViewport({scale:1}),content=await page.getTextContent();
        const items=content.items.filter((x):x is typeof x & {str:string;transform:number[];width:number;height:number;hasEOL:boolean}=>'str' in x);
        let lines:Array<{text:string;x:number;y:number;width:number;height:number}>=[],line:{text:string;x:number;y:number;width:number;height:number}|null=null;
        for(const item of items){
          if(!item.str.trim())continue;const x=item.transform[4],y=item.transform[5];
          if(!line||Math.abs(line.y-y)>Math.max(3,item.height*.7)){if(line)lines.push(line);line={text:item.str,x,y,width:item.width,height:item.height};}
          else{line.text+=' '+item.str;line.width=Math.max(line.width,x+item.width-line.x);}
          if(item.hasEOL&&line){lines.push(line);line=null;}
        }if(line)lines.push(line);
        if(lines.map(l=>l.text).join('').trim().length<8){blank++;warnings.push(`${pageIndex+1}페이지: 텍스트가 적어 OCR 또는 원본 확인이 필요합니다.`);}
        // Content-stream order is kept; complex columns/formulas remain visible in the original PDF.
        for(let k=0;k<lines.length;k++){
          const l=lines[k];const text=clean(l.text);if(!text)continue;
          blocks.push({type:'paragraph',text,pageIndex,bbox:[l.x/viewport.width,(viewport.height-l.y-l.height)/viewport.height,Math.min(1,l.width/viewport.width),l.height/viewport.height],structure:{schemaVersion:1},warnings:['수식·다단 순서는 원본 PDF와 대조하세요.']});
        }
        page.cleanup();
      }catch{failed++;warnings.push(`${pageIndex+1}페이지: 텍스트 추출 실패. 원본 페이지를 확인하세요.`);}
    }
    return {title,blocks,links:[],images:[],pageCount:pdf.numPages,warnings,status:blank===pdf.numPages||!blocks.length?'ocr_needed':failed||blank?'partial':'ready'};
  }finally{await pdf.destroy();}
}
