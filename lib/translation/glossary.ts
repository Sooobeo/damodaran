import { z } from 'zod';
import pack from '../../content/translation-glossary.json';
import { hash, json } from '../db';

const ruleSchema=z.object({id:z.string(),source:z.string().min(1),target:z.string().min(1),aliases:z.array(z.string()),replacements:z.array(z.string()),sources:z.array(z.url()),note:z.string(),mode:z.enum(['phrase','exact'])});
const glossaryPack=z.object({version:z.number().int().positive(),rules:z.array(ruleSchema).max(100)}).parse(pack);
export type TranslationGlossaryEntry=z.infer<typeof ruleSchema>;
export type StudyTerm={id:string;term_en:string;term_ko:string;acronym:string|null;aliases_json:string;notes:string|null;revision:number};
const normalized=(text:string)=>text.trim().replace(/\s+/g,' ').toLowerCase();
function matches(text:string,phrase:string){
  if(!/[A-Za-z]/.test(phrase))return false;
  const escaped=phrase.trim().split(/\s+/).map(part=>part.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('\\s+');
  return new RegExp(`(?<![A-Za-z0-9_])${escaped}(?![A-Za-z0-9_])`,'i').test(text);
}
export function selectTranslationGlossary(text:string,context:string,terms:StudyTerm[]){
  const rules=glossaryPack.rules.map(rule=>({...rule,aliases:[...rule.aliases],replacements:[...rule.replacements]}));
  for(const term of terms){
    const existing=rules.find(rule=>[rule.source,...rule.aliases].some(alias=>normalized(alias)===normalized(term.term_en)));
    const aliases=[term.acronym,...json<string[]>(term.aliases_json,[])].filter((alias):alias is string=>Boolean(alias)&&/[A-Za-z]/.test(alias!));
    if(existing){
      // The study dictionary's selected wording is authoritative for an existing term.
      existing.target=term.term_ko;existing.aliases=[...new Set([...existing.aliases,...aliases.filter(alias=>existing.mode==='exact'||!/^\s*[A-Z][A-Z0-9/.-]{1,10}\s*$/.test(alias))])];
      existing.note=[existing.note,term.notes].filter(Boolean).join(' ');
      if(existing.mode==='phrase'&&term.acronym)rules.push({id:term.id+'-acronym',source:term.acronym,target:term.term_ko,aliases:[],replacements:[],sources:existing.sources,note:term.notes||'',mode:'exact'});
    }else rules.push({id:term.id,source:term.term_en,target:term.term_ko,aliases,replacements:[],sources:[],note:term.notes||'',mode:'exact'});
  }
  const selected=rules.filter(rule=>[rule.source,...rule.aliases].some(phrase=>matches(text+'\n'+context,phrase)))
    .sort((a,b)=>Number([b.source,...b.aliases].some(s=>matches(text,s)))-Number([a.source,...a.aliases].some(s=>matches(text,s)))||b.source.length-a.source.length||a.id.localeCompare(b.id)).slice(0,100);
  // Every applied alias, correction, exception note and target contributes to the cache.
  const version=hash(JSON.stringify({version:glossaryPack.version,rules:selected,studyRevisions:terms.filter(term=>selected.some(rule=>[rule.source,...rule.aliases].some(alias=>normalized(alias)===normalized(term.term_en))||rule.id===term.id+'-acronym')).map(term=>({id:term.id,revision:term.revision}))}));
  return {glossary:selected,glossaryVersion:version};
}
export function translationGlossaryInfo(){return {version:glossaryPack.version,ruleCount:glossaryPack.rules.length,sources:[...new Set(glossaryPack.rules.flatMap(rule=>rule.sources))]};}
