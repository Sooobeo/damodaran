import { chromium } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
const base=process.env.BASE_URL||'http://127.0.0.1:3000';
const output=path.resolve('test-results');fs.mkdirSync(output,{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-background-timer-throttling','--disable-renderer-backgrounding']});
const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
try{
  await page.goto(base,{waitUntil:'domcontentloaded',timeout:120000});await page.locator('main h1').waitFor({timeout:60000});await page.screenshot({path:path.join(output,'home-desktop.png'),fullPage:true});
  const state=await (await page.request.get(base+'/api/bootstrap')).json();if(!state.resources)throw new Error(JSON.stringify(state));
  const r01=state.resources.find((r:{id:string})=>r.id==='R01');const routes=['/learn','/learn/'+state.modules[0].slug,'/library','/resources/R01','/tools','/tools/'+state.toolGuides[0].slug,'/glossary','/notes','/settings',`/reader/R01?versionId=${r01.versionId}&mode=en`];const checks=[];
  for(const route of routes){await page.goto(base+route,{waitUntil:'domcontentloaded'});await page.locator('main h1').waitFor();const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);checks.push({route,desktopOverflow:overflow,title:await page.locator('main h1').first().innerText()});if(overflow)throw new Error('Desktop overflow: '+route);}
  await page.screenshot({path:path.join(output,'reader-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  for(const route of ['/',...routes]){await page.goto(base+route,{waitUntil:'domcontentloaded'});await page.locator('main h1').waitFor();const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);checks.push({route,mobileOverflow:overflow});if(overflow)throw new Error('Mobile overflow: '+route);if(route==='/')await page.screenshot({path:path.join(output,'home-mobile.png'),fullPage:true});}
  await page.screenshot({path:path.join(output,'reader-mobile.png'),fullPage:true});
  if(errors.length)throw new Error(errors.join('\n'));
  fs.writeFileSync(path.join(output,'browser-smoke.json'),JSON.stringify({checkedAt:new Date().toISOString(),checks,errors},null,2));console.log(JSON.stringify({routes:checks.length,errors,output}));
}finally{await browser.close();}
