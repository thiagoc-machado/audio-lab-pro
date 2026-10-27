// Run inside the matching Microsoft Playwright Docker image; see README.
const {chromium}=require('/tmp/browser/node_modules/playwright');
const fs=require('node:fs');
const assert=require('node:assert/strict');
function wav(){
 const rate=48000,seconds=16,channels=2,data=Buffer.alloc(44+rate*seconds*channels*2);
 data.write('RIFF');data.writeUInt32LE(data.length-8,4);data.write('WAVEfmt ',8);data.writeUInt32LE(16,16);data.writeUInt16LE(1,20);data.writeUInt16LE(channels,22);data.writeUInt32LE(rate,24);data.writeUInt32LE(rate*channels*2,28);data.writeUInt16LE(channels*2,32);data.writeUInt16LE(16,34);data.write('data',36);data.writeUInt32LE(data.length-44,40);
 for(let i=0;i<rate*seconds;i++){const t=i/rate,x=Math.sin(2*Math.PI*(220*t+4*t*t))*.18;data.writeInt16LE(Math.round(x*32767),44+i*4);data.writeInt16LE(Math.round(x*.85*32767),46+i*4);}
 return data;
}
(async()=>{
 const browser=await chromium.launch({args:['--no-sandbox','--autoplay-policy=no-user-gesture-required']});
 const context=await browser.newContext({viewport:{width:1440,height:1100},acceptDownloads:true});
 const page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('console',m=>{if(m.type()==='error'){errors.push(m.text());console.error('BROWSER:',m.text());}});
 const qaName='Browser QA '+Date.now()+'.wav';
 let audioId;
 try{
 await page.goto('http://localhost:8000/');
 await page.waitForFunction(()=>document.getElementById('engine-status').textContent.includes('connected'));
 await page.waitForLoadState('networkidle');
 const uploadedResponse=page.waitForResponse(response=>response.url().endsWith('/api/audio/upload')&&response.request().method()==='POST');
 await page.locator('#upload').setInputFiles({name:qaName,mimeType:'audio/wav',buffer:wav()});
 const uploaded=await (await uploadedResponse).json();
 assert(uploaded.audio_id,'QA upload must return its own audio identifier');
 audioId=uploaded.audio_id;
 await page.waitForURL(url=>url.searchParams.get('audio')===audioId,{timeout:120000});
 await page.waitForFunction(name=>document.getElementById('track-name').textContent===name,qaName);
 await page.locator('#workspace').waitFor({state:'visible'});
 await page.waitForFunction(()=>!document.getElementById('generate-preview').disabled,{},{timeout:120000});
 assert.equal(new URL(page.url()).searchParams.get('audio'),audioId);
 assert(await page.locator('#metrics .metric').count()===8);
 assert((await page.locator('#fingerprint-status').textContent()).includes('Chromaprint'));
 await page.locator('#creative-mode').click();
 await page.locator('#fx-pitch').evaluate(el=>{el.value=3;el.dispatchEvent(new Event('input',{bubbles:true}));});
 await page.locator('#export-format').selectOption('mp3');
 await page.locator('#preview-start').fill('2');await page.locator('#preview-start').dispatchEvent('change');
 await page.locator('#preview-duration').selectOption('10');
 await page.evaluate(()=>{window.previewEvents=[];const audio=document.getElementById('processed-audio');for(const type of ['loadstart','loadedmetadata','play','pause','emptied','error'])audio.addEventListener(type,()=>window.previewEvents.push({type,time:audio.currentTime,ready:audio.readyState}));});
 await page.locator('#generate-preview').click();
 await page.waitForFunction(()=>!document.getElementById('source-b').disabled,{},{timeout:120000});
 assert(await page.locator('#preview-play').isEnabled());
 await page.locator('#preview-play').click();
 await page.waitForFunction(()=>document.getElementById('processed-audio').currentTime>1);
 assert.equal(await page.locator('#source-b').getAttribute('aria-pressed'),'true');
 assert.equal(await page.locator('#preview-play').getAttribute('aria-label'),'Pause generated preview');
 await page.waitForFunction(()=>!document.getElementById('preview-time').textContent.startsWith('0:00 /'));
 await page.locator('#preview-play').click();
 await page.waitForFunction(()=>document.getElementById('processed-audio').paused);
 assert.equal(await page.locator('#preview-play').getAttribute('aria-pressed'),'false');
 await page.locator('#preview-play').click();
 await page.waitForFunction(()=>!document.getElementById('processed-audio').paused);
 await page.locator('#source-a').click();
 await page.waitForFunction(()=>!document.getElementById('original-audio').paused);
 await page.locator('#play').click();
 await page.locator('#save-preset').click();await page.locator('#preset-name').fill('Browser test preset');await page.locator('#name-dialog button[value=save]').click();
 await page.waitForFunction(()=>!document.getElementById('rename-preset').disabled);
 await page.locator('#rename-preset').click();await page.locator('#preset-name').fill('Browser test renamed');await page.locator('#name-dialog button[value=save]').click();
 await page.waitForFunction(()=>document.querySelector('#preset-select option:checked')?.textContent.includes('renamed'));
 await page.locator('#delete-preset').click();await page.waitForFunction(()=>document.getElementById('rename-preset').disabled);
 await page.locator('#process-full').click();
 await page.waitForFunction(()=>!document.getElementById('comparison').hidden,{},{timeout:120000});
 const downloadPromise=page.waitForEvent('download');await page.locator('#download').click();const download=await downloadPromise;assert((await download.suggestedFilename()).endsWith('.mp3'));assert(!await download.failure());
 assert(await page.locator('#source-b').isEnabled(),'Download must preserve the audio players');
 assert(await page.locator('#preview-play').isEnabled(),'Download must preserve preview playback');
 assert(await page.locator('#original-wave canvas').count()>0,'Original waveform must remain after download');
 await page.evaluate(()=>window.scrollTo(0,0));
 fs.mkdirSync('/work/docs/screenshots',{recursive:true});await page.screenshot({path:'/work/docs/screenshots/studio-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
 await page.screenshot({path:'/work/docs/screenshots/studio-mobile.png',fullPage:true});
 await page.goto('http://localhost:8000/history');await page.waitForSelector('#history-table table');
 assert((await page.locator('#history-table').textContent()).includes('Browser QA'));
 await page.goto('http://localhost:8000/docs');await page.waitForSelector('.swagger-ui .info');
 assert((await page.locator('.swagger-ui .info').textContent()).includes('Audio Lab Pro'));
 assert.deepEqual(errors,[]);
 console.log('PASS: browser upload, waveform, analysis, presets CRUD, effects, preview play/pause and timer, A/B playback, full processing, download, history, local API docs, desktop and mobile layouts');
 }catch(error){
  console.error('Playback diagnostics:',await page.evaluate(()=>{
   const audio=document.getElementById('processed-audio');
   return {paused:audio?.paused,time:audio?.currentTime,duration:audio?.duration,readyState:audio?.readyState,error:audio?.error?.message,src:audio?.currentSrc,events:window.previewEvents,button:document.getElementById('preview-play')?.textContent,sourceB:document.getElementById('source-b')?.getAttribute('aria-pressed'),toast:document.getElementById('toast')?.textContent};
  }));
  throw error;
 }finally{
  if(!audioId){const records=await context.request.get('http://localhost:8000/api/audio');const own=(await records.json()).find(a=>a.name===qaName);audioId=own?.id;}
  if(audioId){const own=await context.request.get('http://localhost:8000/api/audio/'+audioId);if(own.ok()&&(await own.json()).name===qaName){const response=await context.request.delete('http://localhost:8000/api/audio/'+audioId);if(!response.ok())console.error('QA recording cleanup:',response.status(),await response.text());}}
  await browser.close();
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
