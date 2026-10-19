#!/usr/bin/env node
// Isolated local-only browser qualification. Never connects to production.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const python=process.env.TESTNOW_TEST_PYTHON||'python3',chrome=process.env.TESTNOW_TEST_CHROME||'google-chrome';
const output=path.join(root,'docs/remediation/evidence/phase7');fs.mkdirSync(output,{recursive:true});
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'testnow-remediation-browser-'));
const children=[];let socket;
async function freePort(){const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const port=server.address().port;await new Promise(resolve=>server.close(resolve));return port;}
function launch(command,args,options={}){const child=spawn(command,args,{cwd:root,detached:true,stdio:['ignore','pipe','pipe'],...options});children.push(child);child.stdout.on('data',value=>fs.appendFileSync(path.join(temporary,`${child.pid}.log`),value));child.stderr.on('data',value=>fs.appendFileSync(path.join(temporary,`${child.pid}.log`),value));return child;}
async function waitFor(url){for(let attempt=0;attempt<120;attempt++){try{if((await fetch(url,{signal:AbortSignal.timeout(1500)})).ok)return;}catch{}await new Promise(resolve=>setTimeout(resolve,250));}throw Error('Local service unavailable: '+url);}
const [apiPort,webPort,debugPort]=await Promise.all([freePort(),freePort(),freePort()]);
const base=`http://127.0.0.1:${webPort}`;
const result={environment:'Disposable SQLite, memory storage/queue seams, Chrome headless and Next development server; not production qualification',checks:{},widths:[],performance:{}};
let sequence=0;const pending=new Map();
function send(method,params={}){return new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});}
async function evaluate(expression){const response=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(response.exceptionDetails)throw Error(response.exceptionDetails.exception?.description||response.exceptionDetails.text);return response.result.value;}
async function until(expression){for(let attempt=0;attempt<240;attempt++){if(await evaluate(`Boolean(${expression})`))return;await new Promise(resolve=>setTimeout(resolve,100));}throw Error('Timed out: '+expression);}
async function click(text){await evaluate(`(()=>{const button=Array.from(document.querySelectorAll('button')).find(button=>button.textContent.trim()===${JSON.stringify(text)});if(!button)throw Error('Missing button');button.focus();button.click();})()`);}
async function navigate(route){await send('Page.navigate',{url:base+route});}
async function type(selector,text){await evaluate(`(()=>{const input=document.querySelector(${JSON.stringify(selector)});input.focus();input.select?.();})()`);await send('Input.insertText',{text});}
async function screenshot(name){const capture=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(output,name),Buffer.from(capture.data,'base64'));}
try{
 const environment={...process.env,TESTNOW_UI_FIXTURE:'1',DATABASE_URL:'sqlite+pysqlite:///:memory:',SEED_DEMO_DATA:'false',SUPER_ADMIN_ACCESS_CODE:'',OPENROUTER_API_KEY:'',PYTHONPATH:[path.join(root,'apps/api'),path.join(root,'apps/api/tests')].join(path.delimiter)};
 launch(python,['-m','uvicorn','remediation_ui_fixture:app','--host','127.0.0.1','--port',String(apiPort)],{env:environment});await waitFor(`http://127.0.0.1:${apiPort}/health`);
 launch('npm',['--workspace','@testnow/web','run','dev','--','--hostname','127.0.0.1','--port',String(webPort)],{env:{...process.env,API_INTERNAL_URL:`http://127.0.0.1:${apiPort}`}});await waitFor(base);
 launch(chrome,['--headless','--no-sandbox','--disable-gpu','--remote-debugging-address=127.0.0.1',`--remote-debugging-port=${debugPort}`,`--user-data-dir=${path.join(temporary,'chrome')}`,'about:blank']);await waitFor(`http://127.0.0.1:${debugPort}/json/version`);
 const target=await(await fetch(`http://127.0.0.1:${debugPort}/json/new?about:blank`,{method:'PUT'})).json();socket=new WebSocket(target.webSocketDebuggerUrl);await new Promise(resolve=>socket.addEventListener('open',resolve,{once:true}));
 socket.addEventListener('message',event=>{const response=JSON.parse(event.data);if(response.id){const request=pending.get(response.id);pending.delete(response.id);response.error?request.reject(response.error):request.resolve(response.result);}if(response.method==='Page.javascriptDialogOpening')void send('Page.handleJavaScriptDialog',{accept:true});});
 await send('Page.enable');await send('Runtime.enable');await send('Performance.enable');
 await send('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
 await navigate('/admin/banks/bank');await until(`Boolean(document.querySelector('.import-primary-action'))`);
 result.checks.monitorDefault=!await evaluate(`Boolean(document.querySelector('input[name="question_files"]'))`);
 const chooseBank=async(name)=>evaluate(`Array.from(document.querySelectorAll('.bank-choice-row')).find(button=>button.textContent.includes(${JSON.stringify(name)})).click()`);
 await chooseBank('Race bank A');await until(`document.querySelector('.bank-detail-workspace h2')?.textContent==='Race bank A'`);await chooseBank('Race bank B');await until(`!document.querySelector('.bank-detail-workspace h2')`);await chooseBank('Race bank C');await until(`document.querySelector('.bank-detail-workspace h2')?.textContent==='Race bank C'`);await new Promise(resolve=>setTimeout(resolve,1000));result.checks.rapidSwitch=await evaluate(`document.querySelector('.bank-detail-workspace h2')?.textContent==='Race bank C'`);
 await chooseBank('Race bank D');await until(`document.body.innerText.includes('Fixture bank unavailable')`);result.checks.failedSelectionClearsControls=!await evaluate(`Boolean(document.querySelector('.bank-detail-workspace h2'))`);
 await navigate('/admin/banks/bank');await until(`Boolean(document.querySelector('.import-primary-action'))`);await click('Review');await until(`document.querySelectorAll('.queue-question').length===25`);result.checks.boundedInitialCards=await evaluate(`document.querySelectorAll('.bank-question-review').length===0`);
 await evaluate(`window.__longTasks=[];window.__longObserver=new PerformanceObserver(list=>window.__longTasks.push(...list.getEntries().map(entry=>entry.duration)));window.__longObserver.observe({entryTypes:['longtask']})`);
 const memoryBefore=await send('Performance.getMetrics');
 for(let cycle=0;cycle<5;cycle++){await evaluate(`document.querySelector('.queue-question').click()`);await until(`document.querySelectorAll('.bank-question-review').length===1`);await click('← Back to documents');await until(`!document.querySelector('.document-detail-page')`);await click('Review');await until(`document.querySelectorAll('.queue-question').length===25`);}
 const memoryAfter=await send('Performance.getMetrics');const metric=(metrics,name)=>metrics.metrics.find(item=>item.name===name)?.value;
 result.performance={cycles:5,heapBefore:metric(memoryBefore,'JSHeapUsedSize'),heapAfter:metric(memoryAfter,'JSHeapUsedSize'),domNodesBefore:metric(memoryBefore,'Nodes'),domNodesAfter:metric(memoryAfter,'Nodes'),longTasks:await evaluate(`({count:window.__longTasks.length,max:Math.max(0,...window.__longTasks)})`),qualification:'Short local development-server trace; no production memory/performance guarantee'};
 await click('Next page');await until(`new URLSearchParams(location.search).get('qpage')==='2'&&document.querySelector('.bounded-review-workspace').getAttribute('aria-busy')==='false'`);result.checks.pagination=true;
 for(const width of [390,599,600,601,767,768,769,849,850,851,899,900,901,1024,1100,1280]){await send('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:width===390});await new Promise(resolve=>setTimeout(resolve,40));const sample=await evaluate(`({viewport:innerWidth,scrollWidth:document.querySelector('.document-detail-page').scrollWidth})`);result.widths.push(sample);if(sample.scrollWidth>sample.viewport)throw Error('Source overflow at '+width);}
 await send('Emulation.setDeviceMetricsOverride',{width:640,height:450,deviceScaleFactor:2,mobile:false});result.checks.zoomEquivalentReflow=await evaluate(`document.querySelector('.document-detail-page').scrollWidth<=innerWidth`);result.zoomMethod='640 CSS px at 2x device scale, equivalent reflow space for 1280 px at 200%; native browser zoom/assistive technology remain manual gates';await screenshot('zoom-equivalent-source.png');
 await send('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});await click('← Back to documents');
 await navigate('/admin/banks/bank/questions/valid?task=review');await until(`Boolean(document.querySelector('.bank-question-review'))`);await click('Edit');await evaluate(`document.querySelectorAll('.choice-editor input[type=radio]')[1].click()`);await type('.choice-editor textarea','Four');await evaluate(`Array.from(document.querySelectorAll('.bank-question-review label')).find(label=>label.textContent.startsWith('Reason for change')).querySelector('input').focus()`);await send('Input.insertText',{text:'Verified both options and answer against source'});await click('Save and approve');await until(`!document.querySelector('.choice-editor')`);const saved=await evaluate(`fetch('/api/admin/question-banks/bank/questions/valid').then(response=>response.json())`);result.checks.structuredMcq=saved.answer.option===1&&saved.options[0].markdown==='Four';
 await navigate('/admin/banks/bank');await until(`Boolean(document.querySelector('.import-primary-action'))`);await click('Publish all ready materials');await until(`document.querySelector('.toast')?.textContent.includes('blocked')`);const preview=await evaluate(`fetch('/api/admin/question-banks/bank/imports/job/publication-preview').then(response=>response.json())`);result.checks.publicationBlockedNullAnswer=preview.blocked===1&&preview.already_published===178;
 await navigate('/admin/teachers/teacher');await until(`Boolean(document.querySelector('.teacher-support h1'))`);await type('input[name="access_code"]','22222');await click('Reset teacher access');await until(`document.body.innerText.includes('Access code changed and existing sessions revoked')`);const teacher=await evaluate(`fetch('/api/admin/teachers/teacher').then(response=>response.json())`);result.checks.recoveryPreservesSuspension=teacher.teacher.status==='suspended';
 await click('Attempts (1)');await until(`Boolean(document.querySelector('.teacher-support .queue-question'))`);await evaluate(`document.querySelector('.teacher-support .queue-question').click()`);await until(`document.querySelector('.support-resource-detail')?.textContent.includes('Student response: Option A')`);result.checks.supportContext=true;await screenshot('teacher-attempt-support.png');
 if(Object.values(result.checks).some(value=>!value))throw Error('A qualification check failed: '+JSON.stringify(result.checks));
 fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
}finally{
 socket?.close();for(const child of children.reverse()){try{process.kill(-child.pid,'SIGTERM');}catch{}}console.log('Stopped isolated fixture services. Diagnostic logs: '+temporary);
}
