import { resources, modules, glossary } from '../content';
import { db, now } from './db';
import { z } from 'zod';
export function seed() {
  const d=db();
  if(resources.length!==36 || modules.length!==8 || glossary.length<40) throw new Error('초기 콘텐츠 개수가 설계와 다릅니다.');
  d.transaction(()=>{
    for(const r of resources){
      z.object({id:z.string().regex(/^[RBT]\d{2}$/),url:z.url(),titleKo:z.string().min(1),titleEn:z.string().min(1),objectives:z.array(z.string()),tags:z.array(z.string())}).parse(r);
      d.prepare(`INSERT INTO resources(id,source_type,canonical_url,author,title_en,title_ko,summary_ko,kind,format,level,priority,tags_json,objectives_json,question,created_at)
        VALUES(@id,'remote',@url,@author,@titleEn,@titleKo,@summaryKo,@kind,@format,@level,@priority,@tags,@objectives,@question,@createdAt)
        ON CONFLICT(id) DO UPDATE SET title_ko=excluded.title_ko,summary_ko=excluded.summary_ko,tags_json=excluded.tags_json,objectives_json=excluded.objectives_json,question=excluded.question`).run({...r,tags:JSON.stringify(r.tags),objectives:JSON.stringify(r.objectives),createdAt:now()});
    }
    for(const g of glossary){
      z.object({id:z.string().min(1),termKo:z.string().min(1),definitionKo:z.string().min(10),aliases:z.array(z.string()),sources:z.array(z.url()),revision:z.number().int().positive()}).parse(g);
      d.prepare(`INSERT INTO glossary_terms(id,term_en,term_ko,acronym,aliases_json,definition_ko,formula,example_ko,notes,sources_json,revision)
        VALUES(@id,@termEn,@termKo,@acronym,@aliases,@definitionKo,@formula,@exampleKo,@notes,@sources,@revision)
        ON CONFLICT(id) DO UPDATE SET term_en=excluded.term_en,term_ko=excluded.term_ko,acronym=excluded.acronym,aliases_json=excluded.aliases_json,definition_ko=excluded.definition_ko,formula=excluded.formula,example_ko=excluded.example_ko,notes=excluded.notes,sources_json=excluded.sources_json,revision=excluded.revision WHERE excluded.revision>glossary_terms.revision`).run({...g,acronym:g.acronym??null,formula:g.formula??null,exampleKo:g.exampleKo??null,notes:g.notes??null,aliases:JSON.stringify(g.aliases),sources:JSON.stringify(g.sources)});
    }
    for(const m of modules){
      z.object({id:z.string(),slug:z.string(),resourceIds:z.array(z.string()),prerequisiteTermIds:z.array(z.string()),questions:z.array(z.string())}).parse(m);
      d.prepare(`INSERT INTO modules VALUES(@id,@slug,@order,@titleKo,@question,@objectives,@questions) ON CONFLICT(id) DO UPDATE SET title_ko=excluded.title_ko,question=excluded.question,objectives_json=excluded.objectives_json,questions_json=excluded.questions_json`).run({...m,objectives:JSON.stringify(m.objectives),questions:JSON.stringify(m.questions)});
      m.resourceIds.forEach((resourceId,i)=>d.prepare('INSERT INTO module_resources(module_id,resource_id,sort_order) VALUES(?,?,?) ON CONFLICT(module_id,resource_id) DO UPDATE SET sort_order=excluded.sort_order').run(m.id,resourceId,i));
      const termIds=new Set([...m.prerequisiteTermIds,...glossary.filter(g=>g.moduleIds.includes(m.id)).map(g=>g.id)]);
      [...termIds].forEach((termId,i)=>d.prepare('INSERT INTO module_terms(module_id,term_id,role,sort_order) VALUES(?,?,?,?) ON CONFLICT(module_id,term_id) DO UPDATE SET role=excluded.role,sort_order=excluded.sort_order').run(m.id,termId,m.prerequisiteTermIds.includes(termId)?'prerequisite':'core',i));
    }
  })();
}
