import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pdfParagraphs,type PdfTextItem} from '../lib/extraction/pdf-paragraphs';

const viewport={width:600,height:800,transform:[1,0,0,-1,0,800],rotation:0};
function item(str:string,x:number,y:number,width=440,height=12,fontName='body',hasEOL=true):PdfTextItem {
  return {str,transform:[height,0,0,height,x,y],width,height,fontName,hasEOL,dir:'ltr'};
}
const first='The estimated amount includes -5% and $100 paid';
const second='after the stated date, with 2026 kept unchanged.';
const compact=(text:string)=>text.replace(/\s/g,'');
const v2=(items:PdfTextItem[],maxChars=6000)=>pdfParagraphs(items,viewport,maxChars,2);
function dependentList(lead='The delivery status shown beside this request can be') {
  const bullet=(text:string,x:number,y:number,height:number,font:string)=>[
    item('•',x,y,5,height,'symbol',false),item(text,x+18,y,400,height,font)];
  return [...bullet(lead,60,700,14,'parent'),
    ...bullet('Ready after the dispatcher confirms collection.',88,683,12,'child'),
    ...bullet('Delayed while the vehicle is being inspected.',88,668,12,'child'),
    ...bullet('Closed when the parcel reaches its destination.',88,653,12,'child')];
}

test('v2 keeps a dependent lead-in and the complete indented list in one translation paragraph',()=>{
  const items=dependentList(),v1=pdfParagraphs(items,viewport),current=v2(items);
  assert.equal(v1.length,4);assert.equal(current.length,1);
  assert.equal(current[0].text,v1.map(p=>p.text).join('\n'));
  assert.deepEqual(current[0].lineBoxes,v1.flatMap(p=>p.lineBoxes));
  assert.equal(compact(current[0].text),compact(v1.map(p=>p.text).join('')));
  const [x,y,w,h]=current[0].bbox;
  for(const [lx,ly,lw,lh] of current[0].lineBoxes)assert.ok(lx>=x-1e-8&&ly>=y-1e-8&&lx+lw<=x+w+1e-8&&ly+lh<=y+h+1e-8);
  assert.equal(current[0].ambiguousLayout,false);
  assert.equal(v2(dependentList('Available delivery states:')).length,1);
});

test('v2 leaves complete sentences, bare headings and a single child unjoined',()=>{
  for(const lead of ['The delivery status was updated yesterday.','Available delivery states']){
    const items=dependentList(lead);assert.deepEqual(v2(items),pdfParagraphs(items,viewport));
  }
  const one=dependentList().slice(0,4);assert.deepEqual(v2(one),pdfParagraphs(one,viewport));
});

test('v2 rejects an entire dependent list when any later child changes level, font, size or spacing',()=>{
  for(const change of ['indent','font','size','gap'] as const){
    const items=dependentList().map(i=>({...i,transform:[...i.transform]}));
    if(change==='indent')for(const i of items.slice(6))i.transform[4]+=16;
    if(change==='font')items[7].fontName='different-child';
    if(change==='size')for(const i of items.slice(6)){i.height=10;i.transform[0]=i.transform[3]=10;}
    if(change==='gap')for(const i of items.slice(6))i.transform[5]-=25;
    assert.deepEqual(v2(items),pdfParagraphs(items,viewport),change);
  }
  // A disconnected continuation is not permission to combine only the first alternatives.
  const continuation=[...dependentList(),item('continued text without a bullet',106,630,400,12,'child')];
  assert.deepEqual(v2(continuation),pdfParagraphs(continuation,viewport));
});

test('v2 length limit never publishes a prefix of the list or removes numeric symbols',()=>{
  const items=dependentList('The adjustment to -5% and $100 can be'),v1=pdfParagraphs(items,viewport);
  const full=v1.map(p=>p.text).join('\n');
  assert.equal(v2(items,full.length)[0].text,full);
  assert.deepEqual(v2(items,full.length-1),pdfParagraphs(items,viewport,full.length-1));
  assert.match(v2(items)[0].text,/-5% and \$100/);
  const oversized=dependentList().map((item,index)=>index>=3&&index%2===1?{...item,str:item.str.repeat(60)}:item);
  assert.ok(pdfParagraphs(oversized,viewport).map(p=>p.text).join('\n').length>6000);
  assert.deepEqual(v2(oversized),pdfParagraphs(oversized,viewport));
});

test('v2 preserves headings, peer bullets, footer and all ambiguous-page fallbacks',()=>{
  const list=dependentList(),peers=[...list,item('• A new independent paragraph starts here.',60,625,440,14,'parent'),item('2',560,30,8,10)];
  assert.equal(v2(peers).length,3);assert.equal(v2(peers)[0].lineBoxes.length,4);
  for(const risky of [item('PV = CF / (1+r)^n',60,550),
    {...item('rotated',500,550,80),transform:[0,12,-12,0,500,550]},
    item('Cash    100    200',60,550)]){
    const items=[...list,risky];assert.deepEqual(v2(items),pdfParagraphs(items,viewport));
    assert.ok(v2(items).every(p=>p.ambiguousLayout));
  }
  const columns=[...list,item('other column',520,683,70,12,'child')];
  assert.deepEqual(v2(columns),pdfParagraphs(columns,viewport));assert.ok(v2(columns).every(p=>p.ambiguousLayout));
});

test('conservative continuation joins full text, signs, numbers, and union boxes',()=>{
  const result=pdfParagraphs([item(first,60,700),item(second,60,686)],viewport);
  assert.equal(result.length,1);assert.equal(result[0].text,first+' '+second);
  assert.equal(compact(result[0].text),compact(first+second));assert.equal(result[0].lineBoxes.length,2);
  [.1,.11,440/600,(800-686)/800-.11].forEach((value,index)=>assert.ok(Math.abs(result[0].bbox[index]-value)<1e-12));
  assert.equal(result[0].ambiguousLayout,false);
});

test('bullet text indent, nested and historical PDF bullet glyphs form separate items',()=>{
  const items=[item('Heading',60,760,200,22),
    item('•',60,700,5,12,'symbol',false),item(first,78,700,420),item(second,78,686,420),
    item('¨',60,672,5,12,'symbol',false),item('Another item begins with an independent condition',78,672,420),
    item('and continues without changing its stated content.',78,658,420),
    item('¤',88,640,5,10,'symbol',false),item('A nested condition remains an independent list item',104,640,390,10),
    item('and uses a separate indentation level.',104,628,350,10)];
  const result=pdfParagraphs(items,viewport);
  assert.equal(result.length,4);assert.deepEqual(result.map(r=>r.lineBoxes.length),[1,2,2,2]);
  assert.ok(result[1].text.startsWith('• '));assert.ok(result[2].text.startsWith('¨ '));assert.ok(result[3].text.startsWith('¤ '));
});

test('new bullets, indentation, font change, large gap and sentence boundary stop joining',()=>{
  for(const next of [item('• '+second,60,686),item(second,90,686),item(second,60,670),item(second,60,686,440,14),item('A new paragraph starts at this position.',60,686)]){
    assert.equal(pdfParagraphs([item(first,60,700),next],viewport).length,2);
  }
  assert.equal(pdfParagraphs([item(first+'.',60,700),item(second,60,686)],viewport).length,2);
  assert.equal(pdfParagraphs([item(first+'-',60,700),item(second,60,686)],viewport).length,2);
  assert.equal(pdfParagraphs([item(first,60,700),item(second,60,686)],viewport,first.length+5).length,2);
});

test('same-baseline cells and staggered columns remain separate before paragraph grouping',()=>{
  const rows=[item('Stage 1',40,700,70,12,'body',false),item('Stage 2',330,700,70,12,'body',false),
    item('young business description',40,686,190),item('mature business description',330,686,190)];
  const result=pdfParagraphs(rows,viewport);assert.equal(result.length,4);assert.ok(result.every(r=>r.ambiguousLayout));
  assert.deepEqual(result.map(r=>r.text),rows.map(r=>r.str));
  const columns=[item(first,40,700,200),item(second,330,693,200),item(first,40,686,200),item(second,330,679,200)];
  assert.ok(pdfParagraphs(columns,viewport).every(r=>r.ambiguousLayout&&r.lineBoxes.length===1));
  // Real slide 9 layout: separate arrow labels are only about one font height apart.
  const labels=[item('Accessing private equity',86,200,105,7.615,'label',false),item('Initial Public offering',199.34,200,95,7.615,'label',false),item('Seasoned equity issue',305.87,200,100,7.615,'label',false),item('Bond issues',415.75,200,60,7.615,'label',false)];
  assert.deepEqual(pdfParagraphs(labels,viewport).map(r=>r.text),labels.map(r=>r.str));
});

test('adjacent decimal, minus and punctuation fragments do not invent inline spaces',()=>{
  const tokens=[item('-',60,700,4,12,'body',false),item('0.',64,700,9,12,'body',false),item('25%',73,700,18,12,'body',false)];
  assert.equal(pdfParagraphs(tokens,viewport)[0].text,'-0.25%');
  const punctuation=[item('Claim',60,700,30,12,'body',false),item('.',90,700,3,12,'body',false),item(' ',93,700,3,0,'body',false),item('If',96,700,10)];
  assert.equal(pdfParagraphs(punctuation,viewport)[0].text,'Claim. If');
  const ambiguous=[...tokens,item('other cell',400,700,90)];
  assert.equal(pdfParagraphs(ambiguous,viewport)[0].text,'-0.25%');
});

test('formulas, wide inline grids, mixed fonts and rotated text disable paragraph joining',()=>{
  for(const risky of [item('PV = CF / (1+r)^n',60,650),item('100 - 50',60,650),item('r-g',60,650),item('Cash    100    200',60,650),
    {...item('rotated',500,650,80),transform:[0,12,-12,0,500,650]}]){
    const result=pdfParagraphs([item(first,60,700),item(second,60,686),risky],viewport);
    assert.equal(result.length,3);assert.ok(result.every(r=>r.ambiguousLayout));
    assert.ok(result.every(r=>r.bbox.every(n=>Number.isFinite(n)&&n>=0&&n<=1)));
  }
  const mixed=[item('A sentence with ',60,700,85,12,'body',false),item('bold text remaining separate',147,700,250,12,'bold'),item(second,60,686)];
  assert.ok(pdfParagraphs(mixed,viewport).every(r=>r.ambiguousLayout));
  assert.ok(pdfParagraphs([{...item(first,60,700),fontName:undefined},item(second,60,686)],viewport).every(r=>r.ambiguousLayout));
  assert.equal(pdfParagraphs([item(first,60,700),item(second,60,686)],{...viewport,rotation:90}).length,2);
});

test('empty EOL items and a footer-first stream retain text with safe visual ordering',()=>{
  const result=pdfParagraphs([item('2',560,30,8,10),item('Heading',60,760,200,22),
    item(first,60,700,440,12,'body',false),item('',60,686,0,0),item(second,60,686)],viewport);
  assert.deepEqual(result.map(r=>r.text),['Heading',first+' '+second,'2']);
});

test('approved actual PDF: legacy 13/v1 seven/v2 four blocks, full list and unchanged complex pages', {skip:process.env.VERIFY_REAL_PDF_PARAGRAPHS!=='1'},async()=>{
  const directory=path.join(process.cwd(),'.training/verifications/source-pdf-20260910');
  const filename=path.join(directory,'session2-de10cd81e1b2d49ed9d3a17cad40d8a1b4be89fcbb1c4462d0c2ebd7ff86d895.pdf');
  const bytes=fs.readFileSync(filename),digest=(value:Buffer)=>createHash('sha256').update(value).digest('hex');
  const expectedHash='de10cd81e1b2d49ed9d3a17cad40d8a1b4be89fcbb1c4462d0c2ebd7ff86d895';
  assert.equal(digest(bytes),expectedHash);
  const metadataBytes=fs.readFileSync(path.join(directory,'page-3-render-metadata.json'));
  assert.equal(digest(metadataBytes),'0c6fa47f6c606df5cf567ee515144ac59429df49956c1dc57d9e9d166e79f970');
  const metadata=JSON.parse(metadataBytes.toString('utf8')) as {lines:{text:string}[]};
  const {extractPdf}=await import('../lib/extraction');const {PDF_EXTRACTOR_V1,PDF_EXTRACTOR_VERSION}=await import('../lib/sources');
  const legacy=await extractPdf(bytes),v1=await extractPdf(bytes,PDF_EXTRACTOR_V1),current=await extractPdf(bytes,PDF_EXTRACTOR_VERSION);
  const before=legacy.blocks.filter(b=>b.pageIndex===2),old=v1.blocks.filter(b=>b.pageIndex===2),after=current.blocks.filter(b=>b.pageIndex===2);
  const lines=metadata.lines.map(l=>l.text);
  const expected=[lines[0],lines.slice(1,5).join(' '),lines.slice(5,7).join(' '),lines.slice(7,9).join(' '),lines.slice(9,11).join(' '),lines[11],lines[12]];
  assert.equal(before.length,13);assert.equal(old.length,7);assert.deepEqual(old.map(b=>b.text),expected);
  assert.deepEqual(old.map(b=>b.structure?.lineCount),[1,4,2,2,2,1,1]);
  assert.equal(after.length,4);assert.deepEqual(after.map(b=>b.text),[expected[0],expected[1],expected.slice(2,6).join('\n'),expected[6]]);
  assert.deepEqual(after.map(b=>b.structure?.lineCount),[1,4,7,1]);
  assert.ok(old.every(b=>b.structure?.pdfParagraphVersion===1));assert.ok(after.every(b=>b.structure?.pdfParagraphVersion===2));
  assert.deepEqual(after[2].structure!.lineBoxes,old.slice(2,6).flatMap(b=>b.structure!.lineBoxes as number[][]));
  assert.equal(compact(after.map(b=>b.text).join('')),compact(lines.join('')));
  assert.deepEqual(after.flatMap(b=>b.text.match(/[+−-]?\d+(?:[.,]\d+)*(?:%|\$)?/g)||[]),['2']);
  for(const block of after){
    const [x,y,w,h]=block.bbox!;assert.ok(x>=0&&y>=0&&x+w<=1.000001&&y+h<=1.000001);
    for(const [lx,ly,lw,lh] of block.structure!.lineBoxes as number[][])assert.ok(lx>=x-1e-8&&ly>=y-1e-8&&lx+lw<=x+w+1e-8&&ly+lh<=y+h+1e-8);
  }
  for(const [pageIndex,count] of [[6,58],[8,63]]){
    const prior=v1.blocks.filter(b=>b.pageIndex===pageIndex),next=current.blocks.filter(b=>b.pageIndex===pageIndex);
    assert.equal(prior.length,count);assert.equal(next.length,count);
    const content=(blocks:typeof prior)=>blocks.map(b=>({...b,structure:{...b.structure,pdfParagraphVersion:undefined}}));
    assert.deepEqual(content(next),content(prior));
    assert.ok(next.every(b=>b.structure?.lineCount===1&&b.warnings[0].includes('표·수식·다단')));
  }
  assert.equal(digest(fs.readFileSync(filename)),expectedHash);
});
