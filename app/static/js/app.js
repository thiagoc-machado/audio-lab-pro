import WaveSurfer from '/static/vendor/wavesurfer.js';
import RegionsPlugin from '/static/vendor/regions.js';
const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmtTime = s => `${Math.floor((s || 0)/60)}:${String(Math.floor((s || 0)%60)).padStart(2,'0')}`;
const number = (v, places=1) => v === null || v === undefined ? 'n/a' : Number(v).toFixed(places);
const defaults = {mode:'preserve',pitch:0,tempo:1,bass:0,treble:0,eq_frequency:1000,eq_gain:0,eq_q:1,compression:0,normalize:false,target_lufs:-16,width:1,reverb:0,echo:0,delay_ms:0,chorus:0,flanger:0,phaser:0,saturation:0,lowpass:20000,highpass:20,limiter:true,denoise:0};
const controls = [
 ['pitch','Pitch',-12,12,.1,' st'],['tempo','Tempo',.5,2,.005,'x'],['bass','Bass',-12,12,.5,' dB'],['treble','Treble',-12,12,.5,' dB'],
 ['compression','Compression',0,1,.01,''],['width','Stereo width',0,2,.01,'x'],['reverb','Reverb',0,.8,.01,''],
 ['normalize','Loudness normalization'],['target_lufs','Target loudness',-24,-9,.5,' LUFS'],['limiter','Output limiter'],
 ['eq_frequency','EQ frequency',20,20000,10,' Hz'],['eq_gain','EQ gain',-12,12,.5,' dB'],['eq_q','EQ Q',.1,10,.1,''],
 ['highpass','High-pass',20,5000,10,' Hz'],['lowpass','Low-pass',100,20000,100,' Hz'],['denoise','Noise reduction',0,1,.01,''],
 ['echo','Echo',0,.8,.01,''],['delay_ms','Delay',0,2000,10,' ms'],['chorus','Chorus',0,1,.01,''],['flanger','Flanger',0,1,.01,''],['phaser','Phaser',0,1,.01,''],['saturation','Saturation',0,1,.01,'']
];
let effects = {...defaults}, presets = [], recordings = [], currentId = null, originalData = null;
let waveA = null, waveB = null, regions = null, activeVersion = null, source = 'a', versions = [], lastVersion = null;
let analysisLoaded = false, refreshing = false, loading = false, viewToken = 0, previousJobState = new Map(), capabilities = {};
let selectedPresetName = null, regionUpdating = false, toastTimer, analysisFetchToken = null, playerToken = 0;
function toast(message, error=false) {
 $('toast').textContent=message; $('toast').classList.toggle('error',error); $('toast').hidden=false;
 clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,6500);
}
async function api(url, options={}) {
 const response = await fetch(url, {...options,headers:{...(options.body instanceof FormData ? {} : {'Content-Type':'application/json'}),...options.headers}});
 const data = await response.json();
 if (!response.ok) throw new Error(data.error?.message || `Request failed (${response.status})`);
 return data;
}
function payload(preview=false) {
 const result={effects:{...effects},export:{format:$('export-format').value,bitrate:+$('bitrate').value,sample_rate:+$('sample-rate').value},preset_name:selectedPresetName};
 if(preview) Object.assign(result,{start:+$('preview-start').value,duration:+$('preview-duration').value});
 return result;
}
function dirty() {
 $('preview-description').textContent=activeVersion ? 'Settings changed - regenerate to hear this version' : `${$('preview-duration').value}-second preview - current export encoding`;
 $('download').hidden=true;
}
function effectOutput(def, value) {
 const places = def[0]==='tempo' ? 3 : ['pitch','eq_gain','eq_q','bass','treble','target_lufs'].includes(def[0]) ? 1 : def[4] < 1 ? 2 : 0;
 return Number(value).toFixed(places)+def[5];
}
function renderControls() {
 const main=[], advanced=[];
 controls.forEach((def,i)=>{
  const [key,label,min0,max0,step]=def;
  let min=min0,max=max0;
  if(effects.mode==='preserve') {
   if(key==='pitch') [min,max]=[-2,2]; if(key==='tempo') [min,max]=[.95,1.05];
   if(key==='compression') max=.4; if(key==='width') [min,max]=[.8,1.2]; if(key==='reverb') max=.2;
  }
  const unsupported = ((key==='pitch'||key==='tempo')&&!capabilities.rubberband)||(key==='denoise'&&!capabilities.denoise);
  const creativeOnly=['echo','delay_ms','chorus','flanger','phaser','saturation'].includes(key);
  if(creativeOnly && effects.mode==='preserve') return;
  const html = def.length===2 ? `<div class="effect-toggle"><label for="fx-${key}">${label}</label><input type="checkbox" id="fx-${key}" data-effect="${key}" ${effects[key]?'checked':''}></div>`
   : `<div class="effect-control"><label for="fx-${key}">${label}${unsupported?' (unavailable)':''}</label><input type="range" id="fx-${key}" data-effect="${key}" min="${min}" max="${max}" step="${step}" value="${effects[key]}" ${unsupported?'disabled':''}><output id="out-${key}" for="fx-${key}">${effectOutput(def,effects[key])}</output></div>`;
  (i<10?main:advanced).push(html);
 });
 $('effect-controls').innerHTML='<div class="effect-group-heading">TONE & CHARACTER</div>'+main.join('')+'<details><summary>Advanced effects & equalizer</summary>'+advanced.join('')+'</details>';
 $('preserve-mode').classList.toggle('selected',effects.mode==='preserve');
 $('creative-mode').classList.toggle('selected',effects.mode==='creative');
 $('preserve-mode').setAttribute('aria-pressed',effects.mode==='preserve'); $('creative-mode').setAttribute('aria-pressed',effects.mode==='creative');
}
$('effect-controls').addEventListener('input',event=>{
 const key=event.target.dataset.effect; if(!key)return;
 effects[key]=event.target.type==='checkbox'?event.target.checked:+event.target.value;
 const def=controls.find(d=>d[0]===key); if($('out-'+key))$('out-'+key).textContent=effectOutput(def,effects[key]);
 selectedPresetName=null; $('preset-select').value=''; presetActions(); dirty();
});
for(const mode of ['preserve','creative']) $(mode+'-mode').onclick=()=>{
 effects={...defaults,mode}; selectedPresetName=null; $('preset-select').value=''; renderControls(); presetActions(); dirty();
};
$('reset').onclick=()=>{effects={...defaults,mode:effects.mode}; selectedPresetName=null;$('preset-select').value='';renderControls();presetActions();dirty();};
function presetActions(){const p=presets.find(p=>p.id===$('preset-select').value);$('rename-preset').disabled=!p||p.builtin;$('delete-preset').disabled=!p||p.builtin;}
async function loadPresets(selected='') {
 presets=await api('/api/presets');
 $('preset-select').innerHTML='<option value="">Neutral / custom settings</option>'+['preserve','creative'].map(mode=>`<optgroup label="${mode==='preserve'?'Preserve':'Creative'}">`+presets.filter(p=>p.effects.mode===mode).map(p=>`<option value="${escape(p.id)}">${escape(p.name)}${p.builtin?'':' (custom)'}</option>`).join('')+'</optgroup>').join('');
 $('preset-select').value=selected; presetActions();
}
$('preset-select').onchange=()=>{const p=presets.find(p=>p.id===$('preset-select').value);effects=p?{...p.effects}:{...defaults};selectedPresetName=p?.name||null;renderControls();presetActions();dirty();};
function askName(title, initial='') {
 return new Promise(resolve=>{const dialog=$('name-dialog');$('dialog-title').textContent=title;$('preset-name').value=initial;dialog.returnValue='';dialog.onclose=()=>resolve(dialog.returnValue==='save'?$('preset-name').value.trim():null);dialog.showModal();$('preset-name').focus();});
}
$('save-preset').onclick=async()=>{const name=await askName('Save a custom preset');if(!name)return;try{const p=await api('/api/presets',{method:'POST',body:JSON.stringify({name,effects})});await loadPresets(p.id);selectedPresetName=p.name;toast('Preset saved');}catch(e){toast(e.message,true);}};
$('rename-preset').onclick=async()=>{const p=presets.find(p=>p.id===$('preset-select').value);if(!p||p.builtin)return;const name=await askName('Rename preset',p.name);if(!name)return;try{await api('/api/presets/'+p.id,{method:'PATCH',body:JSON.stringify({name})});await loadPresets(p.id);selectedPresetName=name;}catch(e){toast(e.message,true);}};
$('delete-preset').onclick=async()=>{const p=presets.find(p=>p.id===$('preset-select').value);if(!p||p.builtin)return;try{await api('/api/presets/'+p.id,{method:'DELETE'});await loadPresets();selectedPresetName=null;toast('Custom preset deleted');}catch(e){toast(e.message,true);}};
async function loadLibrary(){
 recordings=await api('/api/audio');$('file-count').textContent=recordings.length;
 $('library').innerHTML=recordings.length?recordings.map(a=>`<button data-audio="${a.id}" class="${a.id===currentId?'selected':''}" title="${escape(a.name)}">\u266b &nbsp;${escape(a.name)}</button>`).join(''):'<p class="muted small">Your uploaded recordings will appear here.</p>';
}
$('library').onclick=event=>{const btn=event.target.closest('[data-audio]');if(btn){if(document.body.dataset.history==='true')location.href='/?audio='+btn.dataset.audio;else selectAudio(btn.dataset.audio).catch(e=>toast(e.message,true));}};
async function upload(file){
 if(!file||loading)return; if(capabilities.max_upload_mb&&file.size>capabilities.max_upload_mb*1024**2){toast('File exceeds the upload limit',true);return;}
 loading=true;$('upload').disabled=true;$('upload-status').textContent='Uploading and validating audio...';
 try{const form=new FormData();form.append('file',file);const result=await api('/api/audio/upload',{method:'POST',body:form});await loadLibrary();if(document.body.dataset.history==='true'){location.href='/?audio='+result.audio_id;return;}await selectAudio(result.audio_id);toast('Recording uploaded. Analysis queued.');}
 catch(e){toast(e.message,true);}finally{loading=false;$('upload').disabled=false;$('upload').value='';$('upload-status').textContent='';}
}
$('upload').onchange=event=>upload(event.target.files[0]);
for(const name of ['dragenter','dragover'])$('upload-zone').addEventListener(name,e=>{e.preventDefault();$('upload-zone').classList.add('dragging');});
for(const name of ['dragleave','drop'])$('upload-zone').addEventListener(name,e=>{e.preventDefault();$('upload-zone').classList.remove('dragging');if(name==='drop')upload(e.dataTransfer.files[0]);});
function teardown(){
 ++playerToken;
 $('original-audio').pause();$('processed-audio').pause();
 waveA?.destroy();waveB?.destroy();waveA=null;waveB=null;regions=null;activeVersion=null;lastVersion=null;
 for(const el of [$('original-audio'),$('processed-audio')]){el.removeAttribute('src');el.load();}
 source='a';$('source-a').classList.add('selected');$('source-b').classList.remove('selected');$('source-b').disabled=true;$('play').textContent='\u25b6';
 $('processed-wave').innerHTML='<p class="muted small">Generate a preview to see its waveform.</p>';
 $('comparison').hidden=true;$('download').hidden=true;
 syncPreviewPlayer();
}
async function selectAudio(id){
 const token=++viewToken;teardown();currentId=id;analysisLoaded=false;versions=[];$('workspace').hidden=false;$('empty-state').hidden=true;
 $('metrics').innerHTML='';$('analysis-details').textContent='';$('analysis-loading').hidden=false;$('analysis-loading').textContent='Analyzing recording...';
 $('fingerprint-status').textContent='Waiting for local fingerprint';$('fingerprint-hash').textContent='';$('fingerprint-details').textContent='';
 $('preview-start').value=0;$('play-time').textContent='0:00 / 0:00';$('generate-preview').disabled=true;$('process-full').disabled=true;
 originalData=await api('/api/audio/'+id);if(token!==viewToken)return;
 $('track-name').textContent=originalData.name;
 const m=originalData.metadata;$('track-subtitle').textContent=`${m.codec.toUpperCase()} / ${number(m.sample_rate/1000)} kHz / ${m.channels===2?'Stereo':'Mono'} / ${fmtTime(m.duration)} / ${number(originalData.size/1024**2)} MB`;
 $('preview-start').max=Math.max(0,m.duration-.1);$('original-audio').src='/api/audio/'+id+'/stream';
 $('original-audio').volume=$('processed-audio').volume=+$('volume').value;
 document.querySelectorAll('[data-audio]').forEach(btn=>btn.classList.toggle('selected',btn.dataset.audio===id));
 history.replaceState(null,'','/?audio='+id); await refreshCurrent(token);
}
function createWave(container,media,color,plugins=[],data){return WaveSurfer.create({container,media,url:media.src,peaks:data.peaks,duration:data.duration,waveColor:color,progressColor:'#d1c4ff',cursorColor:'#e5d9ff',height:100,barWidth:2,barGap:2,barRadius:2,normalize:true,plugins});}
function readyWave(wave){return new Promise((resolve,reject)=>{wave.once('ready',resolve);wave.once('error',reject);wave.once('destroy',resolve);});}
function syncRegion(){
 if(!regions||!originalData||regionUpdating)return;regionUpdating=true;regions.clearRegions();
 const start=Math.max(0,Math.min(+$('preview-start').value,originalData.metadata.duration-.01));$('preview-start').value=start.toFixed(1);
 regions.addRegion({start,end:Math.min(originalData.metadata.duration,start+ +$('preview-duration').value),color:'rgba(169,149,255,0.12)',drag:true,resize:true});regionUpdating=false;
}
function renderAnalysis(a,fp){
 const metrics=[['Integrated loudness',a.integrated_lufs,'LUFS'],['True peak',a.true_peak_dbtp,'dBTP'],['RMS level',a.rms_dbfs,'dBFS'],['Dynamics (approx.)',a.dynamic_range_approx_db,'dB'],['Sample peak',a.peak_dbfs,'dBFS'],['Clipped samples',a.clipped_samples,'',0],['Spectral centroid',a.spectral_centroid_hz,'Hz',0],['Stereo correlation',a.stereo_correlation,'',2]];
 $('metrics').innerHTML=metrics.map(([label,value,unit,precision])=>`<div class="metric"><span>${label}</span><strong>${number(value,precision??1)}</strong><em>${unit}</em></div>`).join('');
 $('analysis-details').textContent=JSON.stringify(a,null,2);$('analysis-loading').hidden=true;
 $('fingerprint-status').textContent=fp.status==='completed'?`${fp.algorithm} / ${number(fp.duration)} s`:fp.reason;
 $('fingerprint-hash').textContent=fp.hash?'SHA-256 / '+fp.hash:'';$('fingerprint-details').textContent=JSON.stringify(fp,null,2);
}
async function refreshCurrent(token=viewToken){
 if(!currentId)return;const id=currentId;const data=await api('/api/audio/'+id);if(token!==viewToken)return;
 originalData=data;renderJobs(data.jobs);
 const done=data.jobs.some(j=>j.kind==='analysis'&&j.state==='completed');
 if(done&&!analysisLoaded&&analysisFetchToken!==token){
  analysisFetchToken=token;
  try {
  const [a,fp,w]=await Promise.all([api(`/api/audio/${id}/analysis`),api(`/api/audio/${id}/fingerprint`),api(`/api/audio/${id}/waveform`)]);if(token!==viewToken)return;
  renderAnalysis(a,fp);regions=RegionsPlugin.create();waveA=createWave('#original-wave',$('original-audio'),'#8e79cc',[regions],w);
  await readyWave(waveA);if(token!==viewToken)return;
  regions.enableDragSelection({color:'rgba(169,149,255,0.12)'});
  regions.on('region-created',region=>{if(regionUpdating)return;for(const other of regions.getRegions())if(other.id!==region.id)other.remove();$('preview-start').value=region.start.toFixed(1);dirty();});
  regions.on('region-updated',region=>{if(!regionUpdating){$('preview-start').value=region.start.toFixed(1);const lengths=[10,20,30,60];$('preview-duration').value=lengths.reduce((best,v)=>Math.abs(v-(region.end-region.start))<Math.abs(best-(region.end-region.start))?v:best,10);syncRegion();dirty();}});
  syncRegion();analysisLoaded=true;analysisFetchToken=null;$('generate-preview').disabled=false;$('process-full').disabled=false;
  } catch(error) {analysisFetchToken=null;throw error;}
 }
 const failed=data.jobs.find(j=>j.kind==='analysis'&&['failed','cancelled'].includes(j.state));
 if(!done&&failed)$('analysis-loading').textContent=failed.error||'Analysis cancelled. Delete and re-upload to analyze.';
 versions=await api(`/api/audio/${id}/versions`);if(token!==viewToken)return;
 if(versions.length&&versions[0].id!==lastVersion){lastVersion=versions[0].id;await loadVersion(versions[0],token);}
}
function renderJobs(jobs){
 $('jobs').innerHTML=jobs.length?jobs.slice(0,20).map(job=>{
  const active=['pending','queued','processing'].includes(job.state);
  const previous=previousJobState.get(job.id);if(previous&&previous!==job.state&&job.state==='failed')toast(job.error||'Processing failed',true);previousJobState.set(job.id,job.state);
  return `<div class="job-row"><div class="job-info"><strong>${escape(job.kind==='analysis'?'Original analysis':job.kind==='preview'?'Region preview':'Full recording')} ${job.params.preset_name?' / '+escape(job.params.preset_name):''}</strong><p>${escape(job.phase)}${job.state==='processing'&&job.progress?` / ${number(job.progress,0)}% of current pass`:''}${job.error?' / '+escape(job.error):''}</p>${job.state==='processing'?`<div class="progress"><span style="width:${Math.max(0,Math.min(100,job.progress))}%"></span></div>`:''}</div><span class="job-state ${escape(job.state)}">${escape(job.state)}</span>${active?`<button data-cancel="${job.id}" ${job.cancel_requested?'disabled':''}>${job.cancel_requested?'Cancelling...':'Cancel'}</button>`:''}${job.kind!=='analysis'?`<button data-reuse="${job.id}">Reuse</button>`:''}${job.version_id?`<button data-listen="${job.version_id}">Listen</button><a class="button" href="/api/audio/${currentId}/download/${job.version_id}">Download</a><button data-delete-version="${job.version_id}" aria-label="Delete version">\u00d7</button>`:''}</div>`;
 }).join(''):'<p class="muted small">No processing jobs yet.</p>';
}
async function loadVersion(version,token=viewToken){
 const thisPlayer=++playerToken;
 $('processed-audio').pause();waveB?.destroy();waveB=null;activeVersion=version;$('source-b').disabled=true;syncPreviewPlayer();
 $('processed-wave').innerHTML='';$('processed-wave').style.display='block';
 const w=await api(`/api/audio/${currentId}/waveform?version_id=${version.id}`);if(token!==viewToken)return;
 if(thisPlayer!==playerToken)return;
 const url=`/api/audio/${currentId}/download/${version.id}?inline=true`;
 $('processed-audio').src=url;waveB=createWave('#processed-wave',$('processed-audio'),'#73bca9',[],w);await readyWave(waveB);if(token!==viewToken||thisPlayer!==playerToken)return;
 $('source-b').disabled=false;syncPreviewPlayer();$('generate-preview').textContent='\u25b7 Regenerate preview';
 $('preview-description').textContent=version.kind==='preview'?`Preview / from ${fmtTime(version.params.start)} / ${version.params.duration}s source region`:`Full export / ${version.params.export.format.toUpperCase()} / ${version.params.export.sample_rate} Hz`;
 $('download').href=`/api/audio/${currentId}/download/${version.id}`;$('download').hidden=false;
 const c=version.comparison;$('comparison').hidden=!c||!Object.keys(c).length;
 if(c&&Object.keys(c).length)$('comparison').innerHTML=`<p>Fingerprint representation: <strong>${c.fingerprint_exact_match===null?'Unavailable':c.fingerprint_exact_match?'Identical':'Different'}</strong> / hash: <strong>${c.fingerprint_hash_match===null?'Unavailable':c.fingerprint_hash_match?'Match':'Different'}</strong></p><p>Loudness change: ${number(c.loudness_difference_lu)} LU / Duration change: ${number(c.duration_difference_seconds,2)} s</p><p>Spectral centroid change: ${number(c.spectral_centroid_difference_hz,0)} Hz</p><p>Aligned waveform correlation: ${number(c.waveform_correlation?.value,3)}${c.waveform_correlation?.reason?' / '+escape(c.waveform_correlation.reason):''}</p><p>${escape(c.notice)}</p>`;
}
function currentAudio(){return $(source==='a'?'original-audio':'processed-audio');}
function toSourceTime(time,version=activeVersion){return time*(version?.params.effects.tempo||1)+(version?.kind==='preview'?version.params.start:0);}
async function switchSource(next){
 if(next===source||next==='b'&&!activeVersion)return;
 const previous=currentAudio(),playing=!previous.paused;
 let sourceTime=source==='a'?previous.currentTime:toSourceTime(previous.currentTime);
 previous.pause();source=next;const target=currentAudio();
 const targetTime=next==='a'?sourceTime:(sourceTime-(activeVersion.kind==='preview'?activeVersion.params.start:0))/activeVersion.params.effects.tempo;
 target.currentTime=Math.max(0,Math.min(targetTime,Math.max(0,(target.duration||0)-.01)));
 for(const key of ['a','b']){$('source-'+key).classList.toggle('selected',source===key);$('source-'+key).setAttribute('aria-pressed',source===key);}
 if(playing)try{await target.play();}catch(e){toast('Playback unavailable in this browser. Download the audio to listen.',true);}
}
$('source-a').onclick=()=>switchSource('a');$('source-b').onclick=()=>switchSource('b');
function syncPreviewPlayer(){
 const audio=$('processed-audio'),button=$('preview-play');
 const playing=!audio.paused&&!audio.ended;
 button.disabled=!activeVersion||!waveB||$('source-b').disabled;
 button.textContent=playing?'\u2161 Pause preview':'\u25b6 Play preview';
 button.setAttribute('aria-label',playing?'Pause generated preview':'Play generated preview');
 button.setAttribute('aria-pressed',String(playing));
 $('preview-time').textContent=`${fmtTime(audio.currentTime)} / ${fmtTime(audio.duration)}`;
}
$('preview-play').onclick=async()=>{
 if($('preview-play').disabled)return;
 const audio=$('processed-audio');
 try{
  const wasPlaying=!audio.paused;
  $('original-audio').pause();
  source='b';
  for(const key of ['a','b']){
   $('source-'+key).classList.toggle('selected',key==='b');
   $('source-'+key).setAttribute('aria-pressed',String(key==='b'));
  }
  if(wasPlaying)audio.pause();
  else await audio.play();
 }catch(e){toast('Playback unavailable in this browser. Download the audio to listen.',true);}
 syncPreviewPlayer();
};
for(const event of ['play','pause','ended','loadedmetadata','durationchange','emptied','timeupdate','error']){
 $('processed-audio').addEventListener(event,syncPreviewPlayer);
}

$('play').onclick=async()=>{const audio=currentAudio();try{if(audio.paused)await audio.play();else audio.pause();}catch(e){toast('Playback unavailable. Wait for audio to load or download to listen.',true);}};
for(const id of ['original-audio','processed-audio']){
 const audio=$(id);audio.addEventListener('play',()=>{$('play').textContent='\u2161';});audio.addEventListener('pause',()=>{if(currentAudio().paused)$('play').textContent='\u25b6';});
 audio.addEventListener('timeupdate',()=>{if(audio!==currentAudio())return;$('play-time').textContent=`${fmtTime(audio.currentTime)} / ${fmtTime(audio.duration)}`;
  if($('loop').checked&&originalData){const start=+$('preview-start').value,end=Math.min(originalData.metadata.duration,start+ +$('preview-duration').value);const position=source==='a'?audio.currentTime:toSourceTime(audio.currentTime);if(position>=end||position<start-.15){const target=source==='a'?start:(start-(activeVersion.kind==='preview'?activeVersion.params.start:0))/activeVersion.params.effects.tempo;audio.currentTime=Math.max(0,Math.min(target,(audio.duration||0)-.01));}}
 });
 audio.addEventListener('error',()=>{if(audio.src&&audio.error)toast('This browser cannot play this encoding. WAV 16-bit or MP3 is recommended for previews.',true);});
}
$('volume').oninput=()=>{$('original-audio').volume=$('processed-audio').volume=+$('volume').value;};
$('zoom').oninput=()=>waveA?.zoom(+$('zoom').value);
for(const id of ['preview-start','preview-duration'])$(id).onchange=()=>{syncRegion();dirty();};
for(const id of ['sample-rate','bitrate'])$(id).onchange=dirty;
$('export-format').onchange=()=>{const mp3=$('export-format').value==='mp3';$('bitrate-label').hidden=!mp3;for(const opt of $('sample-rate').options)opt.disabled=mp3&&+opt.value>48000;if(mp3&&+$('sample-rate').value>48000)$('sample-rate').value='48000';dirty();};
async function submit(kind){
 if(!currentId)return;const btn=$(kind==='preview'?'generate-preview':'process-full');btn.disabled=true;
 try{const data=await api(`/api/audio/${currentId}/${kind}`,{method:'POST',body:JSON.stringify(payload(kind==='preview'))});toast(data.cached?'Using cached preview':'Audio job queued');await refreshCurrent();if(data.cached){const job=await api('/api/jobs/'+data.job_id);const v=versions.find(v=>v.id===job.version_id);if(v)await loadVersion(v);}}
 catch(e){toast(e.message,true);}finally{btn.disabled=!analysisLoaded;}
}
$('generate-preview').onclick=()=>submit('preview');$('process-full').onclick=()=>submit('process');
async function reuse(job){effects={...job.params.effects};selectedPresetName=job.params.preset_name;renderControls();$('preset-select').value='';presetActions();for(const [key,value]of Object.entries(job.params.export))$({format:'export-format',bitrate:'bitrate',sample_rate:'sample-rate'}[key]).value=value;$('export-format').dispatchEvent(new Event('change'));if(job.kind==='preview'){$('preview-start').value=job.params.start;$('preview-duration').value=job.params.duration;syncRegion();}dirty();toast('Previous settings restored');}
$('jobs').onclick=async event=>{const btn=event.target.closest('button');if(!btn)return;try{
 if(btn.dataset.cancel){await api(`/api/jobs/${btn.dataset.cancel}/cancel`,{method:'POST'});await refreshCurrent();}
 if(btn.dataset.reuse)await reuse(await api('/api/jobs/'+btn.dataset.reuse));
 if(btn.dataset.listen){const v=versions.find(v=>v.id===btn.dataset.listen);if(v){await loadVersion(v);await switchSource('b');}}
 if(btn.dataset.deleteVersion){await api(`/api/audio/${currentId}/versions/${btn.dataset.deleteVersion}`,{method:'DELETE'});if(activeVersion?.id===btn.dataset.deleteVersion){await switchSource('a');$('processed-audio').pause();waveB?.destroy();waveB=null;activeVersion=null;$('source-b').disabled=true;syncPreviewPlayer();$('download').hidden=true;$('processed-wave').innerHTML='';$('comparison').hidden=true;}await refreshCurrent();}
 }catch(e){toast(e.message,true);}};
$('delete-audio').onclick=async()=>{try{await api('/api/audio/'+currentId,{method:'DELETE'});++viewToken;teardown();currentId=null;analysisLoaded=false;await loadLibrary();if(recordings.length)await selectAudio(recordings[0].id);else{$('workspace').hidden=true;$('empty-state').hidden=false;history.replaceState(null,'','/');}toast('Recording and its versions deleted');}catch(e){toast(e.message,true);}};
async function refreshHistory(){
 await loadLibrary();const rows=await Promise.all(recordings.map(async a=>({a,data:await api('/api/audio/'+a.id)})));
 $('history-table').innerHTML=rows.length?'<table><thead><tr><th>Recording / uploaded</th><th>Processed</th><th>Mode / preset</th><th>Format</th><th>Status</th><th>Actions</th></tr></thead><tbody>'+rows.flatMap(({a,data})=>data.jobs.map(j=>`<tr><td>${escape(a.name)}<br><span class="muted">${escape(new Date(a.created_at).toLocaleString())}</span></td><td>${escape(new Date(j.created_at).toLocaleString())}</td><td>${escape(j.params.effects?.mode||'Analysis')}<br>${escape(j.params.preset_name||'')}</td><td>${escape(j.params.export?.format||'Original')}</td><td>${escape(j.state)}</td><td><a class="button" href="/?audio=${a.id}&reuse=${j.id}">Open / reuse</a>${j.version_id?`<a class="button" href="/api/audio/${a.id}/download/${j.version_id}">Download</a><button data-history-version="${j.version_id}" data-owner="${a.id}">Delete version</button>`:''}${['pending','queued','processing'].includes(j.state)?`<button data-history-cancel="${j.id}">Cancel</button>`:''}<button data-history-delete="${a.id}">Delete recording</button></td></tr>`)).join('')+'</tbody></table>':'<p class="muted">No recordings yet. Upload audio to begin.</p>';
}
$('history-table').onclick=async event=>{const b=event.target.closest('button');if(!b)return;try{if(b.dataset.historyCancel)await api(`/api/jobs/${b.dataset.historyCancel}/cancel`,{method:'POST'});if(b.dataset.historyDelete)await api('/api/audio/'+b.dataset.historyDelete,{method:'DELETE'});if(b.dataset.historyVersion)await api(`/api/audio/${b.dataset.owner}/versions/${b.dataset.historyVersion}`,{method:'DELETE'});await refreshHistory();}catch(e){toast(e.message,true);}};
$('refresh-history').onclick=()=>refreshHistory().catch(e=>toast(e.message,true));
async function init(){
 try{capabilities=await api('/api/capabilities');$('engine-status').textContent=capabilities.worker_online?'Audio engine connected':'Engine ready / scheduler starting';$('upload-limit').textContent=capabilities.max_upload_mb;renderControls();await loadPresets();await loadLibrary();
 if(document.body.dataset.history==='true')await refreshHistory();else{const query=new URLSearchParams(location.search);const id=query.get('audio')||recordings[0]?.id;if(id)await selectAudio(id);if(query.get('reuse')){const job=await api('/api/jobs/'+query.get('reuse'));if(job.audio_id===currentId&&job.kind!=='analysis')await reuse(job);}}
 }catch(e){toast(e.message,true);$('engine-status').textContent='Connection unavailable';}
 setInterval(async()=>{if(refreshing||document.hidden||document.body.dataset.history==='true')return;refreshing=true;try{if(currentId)await refreshCurrent();}catch(e){toast(e.message,true);}finally{refreshing=false;}},2000);
}
window.addEventListener('pagehide',teardown);init();
