/** Conservative English PDF line joining. Geometry is evidence, never a license
 * to reconstruct tables, remove hyphens, or change mathematical text. */
export type PdfTextItem = {str:string;transform:number[];width:number;height:number;hasEOL:boolean;fontName?:string;dir?:string};
type Viewport = {width:number;height:number;transform:number[];rotation?:number};
type Box = [number,number,number,number];
type Line = {text:string;x:number;y:number;right:number;height:number;bodyX:number;font:string;safe:boolean;bullet:boolean;box:Box};
export type PdfParagraph = {text:string;bbox:Box;lineBoxes:Box[];ambiguousLayout:boolean};
const bulletGlyph = /^[•●▪◦‣¨¤]$/u;
const bulletStart = /^(?:[•●▪◦‣¨¤]|[-–]\s(?=[^\d])|\(?\d+[.)](?=\s|$))\s*/u;
const normalize = (text:string)=>text.replace(/\u00a0/g,' ').replace(/[\t\r ]+/g,' ').trim();
const clamp = (value:number)=>Math.max(0,Math.min(1,value));
function union(a:Box,b:Box):Box {
  const x=Math.min(a[0],b[0]),y=Math.min(a[1],b[1]);
  return [x,y,Math.max(a[0]+a[2],b[0]+b[2])-x,Math.max(a[1]+a[3],b[1]+b[3])-y];
}
function itemBox(item:PdfTextItem,viewport:Viewport):Box {
  const [a,b,c,d,x,y]=item.transform,advance=Math.hypot(a,b)||1,up=Math.hypot(c,d)||1;
  const dx=a/advance*item.width,dy=b/advance*item.width,hx=c/up*item.height,hy=d/up*item.height;
  const [v0,v1,v2,v3,v4,v5]=viewport.transform;
  const points=[[x,y],[x+dx,y+dy],[x+hx,y+hy],[x+dx+hx,y+dy+hy]].map(([px,py])=>[(v0*px+v2*py+v4)/viewport.width,(v1*px+v3*py+v5)/viewport.height]);
  const left=clamp(Math.min(...points.map(p=>p[0]))),top=clamp(Math.min(...points.map(p=>p[1])));
  return [left,top,clamp(Math.max(...points.map(p=>p[0])))-left,clamp(Math.max(...points.map(p=>p[1])))-top];
}
function mathOrGrid(text:string) {
  return /[=<>≤≥∑∫√^\t]|\S {3,}\S/u.test(text)||/(?:\d|\b[A-Za-z])\s*[+−*/]\s*(?:\d|[A-Za-z])/u.test(text)
    ||/\d\s*-\s*\d|\b[A-Za-z]\s*-\s*[A-Za-z]\b/u.test(text);
}

function joinDependentLists(paragraphs:PdfParagraph[],groups:Line[][],maxChars:number):PdfParagraph[] {
  const output:PdfParagraph[]=[];
  for(let i=0;i<paragraphs.length;i++){
    const lead=paragraphs[i],head=groups[i][0];
    // This recognizes a small grammatical boundary, never a domain phrase or
    // a suggested translation. Unpunctuated headings alone are not evidence.
    if(!head.bullet||!/(?::|\b(?:(?:can|could|may|might|will|would|should|must)\s+be|is|are|was|were))$/i.test(lead.text)){
      output.push(lead);continue;
    }
    const indentTolerance=Math.max(2,head.height*.15);
    let end=i+1,firstChild:Line|undefined,valid=true;
    for(;end<paragraphs.length;end++){
      const next=groups[end][0],previous=groups[end-1].at(-1)!;
      // Same/shallower hierarchy, a larger heading or a numeric footer ends
      // the list. A malformed deeper item invalidates the entire candidate.
      if(next.bodyX<=head.bodyX+indentTolerance||next.height>head.height*1.1||/^\d+$/.test(next.text))break;
      if(!next.bullet){valid=false;break;}
      firstChild??=next;
      const tolerance=Math.max(2,firstChild.height*.15),gap=previous.y-next.y;
      valid&&=next.x>head.x+indentTolerance&&next.bodyX-head.bodyX>=head.height*.45
        &&next.bodyX-head.bodyX<=head.height*2.5&&next.height>=head.height*.7&&next.height<=head.height*1.05
        &&Math.abs(next.bodyX-firstChild.bodyX)<=tolerance&&Math.abs(next.x-firstChild.x)<=tolerance
        &&next.font===firstChild.font&&Math.abs(next.height-firstChild.height)<=Math.max(1,firstChild.height*.08)
        &&gap>=Math.max(previous.height,next.height)*.65&&gap<=Math.max(previous.height,next.height)*1.6;
    }
    const candidates=paragraphs.slice(i,end),text=candidates.map(p=>p.text).join('\n');
    if(candidates.length>=3&&valid&&text.length<=maxChars){
      output.push({text,bbox:candidates.map(p=>p.bbox).reduce(union),
        lineBoxes:candidates.flatMap(p=>p.lineBoxes),ambiguousLayout:false});
    }else output.push(...candidates);
    // Do not publish a partial group, including when later children exceed
    // the limit or violate the shared indentation/font/spacing contract.
    i=end-1;
  }
  return output;
}

// Existing direct callers retain v1; v2 is selected by a stored extractor ID.
export function pdfParagraphs(items:PdfTextItem[],viewport:Viewport,maxChars=6000,paragraphVersion:1|2=1):PdfParagraph[] {
  const lines:Line[]=[],fragments:PdfParagraph[]=[];
  let line:Line|undefined;
  let lastItem:PdfTextItem|undefined,pendingSpace=false;
  const flush=()=>{if(line){line.text=normalize(line.text);if(line.text)lines.push(line);line=undefined;}lastItem=undefined;pendingSpace=false;};
  for(const item of items){
    // PDF.js also signals line endings through empty items.
    if(!item.str.trim()){if(item.hasEOL)flush();else if(item.str)pendingSpace=true;continue;}
    const [a,b,c,d,x,y]=item.transform,height=Math.abs(item.height),text=normalize(item.str);
    const finite=[a,b,c,d,x,y,item.width,height].every(Number.isFinite);
    const safe=finite&&height>0&&item.width>=0&&typeof item.fontName==='string'&&item.fontName.length>0&&a>0&&d>0&&Math.abs(b)<.01&&Math.abs(c)<.01&&(!item.dir||item.dir==='ltr')&&!viewport.rotation;
    const box=finite?itemBox(item,viewport):[0,0,0,0] as Box;
    const gap=line?x-line.right:0;
    if(line&&(Math.abs(line.y-y)>Math.max(2,Math.min(line.height,height)*.2)||gap>Math.max(line.height,height)*(bulletGlyph.test(line.text)?1.8:.8)||x<line.x-2))flush();
    // TextItem boundaries can occur inside a decimal or a word. Only explicit
    // whitespace or a real geometric word gap introduces an inline space.
    const separator=pendingSpace||/^\s/.test(item.str)||!!lastItem&&/\s$/.test(lastItem.str)||gap>Math.max(.4,height*.08)?' ':'';
    const previousFragment=fragments.at(-1);
    if(previousFragment&&lastItem&&lastItem.fontName===item.fontName&&Math.abs(lastItem.transform[5]-y)<.1&&gap<=Math.max(.4,height*.08)&&safe){
      previousFragment.text+=separator+text;previousFragment.bbox=union(previousFragment.bbox,box);previousFragment.lineBoxes=[previousFragment.bbox];
    }else fragments.push({text,bbox:box,lineBoxes:[box],ambiguousLayout:true});
    const marker=bulletGlyph.test(text);
    if(!line){line={text,x,y,right:x+item.width,height,bodyX:x,font:marker?'':item.fontName||'',safe,bullet:bulletStart.test(text),box};}
    else{
      line.text+=separator+text;line.box=union(line.box,box);line.right=Math.max(line.right,x+item.width);
      if(!marker){
        if(!line.font&&line.bullet){line.bodyX=x;line.font=item.fontName||'';line.height=height;}
        else if(line.font!==(item.fontName||'')||Math.abs(line.height-height)>Math.max(1,line.height*.1))line.safe=false;
      }
      line.safe&&=safe;
    }
    // Keep evidence of wide inline spacing before whitespace normalization.
    if(mathOrGrid(item.str))line.safe=false;
    lastItem=item;pendingSpace=false;
    if(item.hasEOL)flush();
  }
  flush();
  // Independent fragments at the same baseline, or adjacent disjoint lanes,
  // are evidence of a table/columns/diagram. Leave the whole page unjoined and
  // in content-stream order; do not pretend a y/x sort solved reading order.
  let ambiguous=lines.some(l=>!l.safe);
  for(let i=0;i<lines.length&&!ambiguous;i++)for(let j=i+1;j<lines.length;j++){
    const a=lines[i],b=lines[j],height=Math.max(a.height,b.height);
    const separated=a.right+height*.5<b.x||b.right+height*.5<a.x;
    const baseline=Math.abs(a.y-b.y);
    if(separated&&(baseline<height*.5||(baseline<height*2&&Math.abs(a.height-b.height)<height*.1&&!/^\d+$/.test(a.text)&&!/^\d+$/.test(b.text)))){ambiguous=true;break;}
  }
  // A line assembled before column detection may already contain independent
  // chart labels. Fall back to original text runs (only touching glyph pieces
  // combined), rather than publishing that speculative combined line.
  if(ambiguous)return fragments;
  const ordered=[...lines].sort((a,b)=>b.y-a.y||a.x-b.x);
  const output:PdfParagraph[]=[],groups:Line[][]=[];
  let previous:Line|undefined;
  for(const next of ordered){
    const current=output.at(-1);
    let join=false;
    if(current&&previous&&!ambiguous&&previous.safe&&next.safe&&!next.bullet){
      const height=previous.height,gap=previous.y-next.y;
      const peers=lines.filter(l=>l.font===previous!.font&&Math.abs(l.height-height)<=Math.max(1,height*.08));
      const right=Math.max(...peers.map(l=>l.right)),left=Math.min(...peers.map(l=>l.bodyX));
      join=next.font===previous.font&&Math.abs(next.height-height)<=Math.max(1,height*.08)
        &&Math.abs(next.bodyX-previous.bodyX)<=Math.max(2,height*.15)
        &&gap>=height*.85&&gap<=height*1.45
        &&previous.right>=right-Math.max(height*2.8,(right-left)*.12)
        &&previous.text.length>=32&&/^[a-z(]/.test(next.text)
        &&!/[.!?;:\-−]$/.test(previous.text)&&!mathOrGrid(previous.text)&&!mathOrGrid(next.text)
        &&current.text.length+1+next.text.length<=maxChars;
    }
    if(join){current!.text+=' '+next.text;current!.bbox=union(current!.bbox,next.box);current!.lineBoxes.push(next.box);groups.at(-1)!.push(next);}
    else {output.push({text:next.text,bbox:next.box,lineBoxes:[next.box],ambiguousLayout:ambiguous});groups.push([next]);}
    previous=next;
  }
  return paragraphVersion===2?joinDependentLists(output,groups,maxChars):output;
}
