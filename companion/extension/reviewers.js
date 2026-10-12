'use strict';
const reviewPurposes=['plan','code','tests','visual','question'];
const reviewNames={plan:'Plan & architecture',code:'Code & result',tests:'Tests & edge cases',visual:'Visual review',question:'Targeted question'};
let reviewerInfo=null,reviewCatalogue=null,reviewDirty=false,reviewEditRevision=0,reviewEpoch=0,reviewSelected=null,reviewRequestId=null;
let reviewJobsSignature=null;const reviewDetailCache=new Map(),reviewJobVersions=new Map();
function selectedReviewerModels(purpose){
 return [...$('review-model-'+purpose).querySelectorAll('input[type="checkbox"]')].filter(input=>input.checked).map(input=>input.value);
}
function markReviewerDirty(){
 reviewDirty=true;reviewEditRevision++;$('review-policy-state').textContent='Unsaved reviewer settings. Save to apply them.';$('review-request').disabled=true;
}
function reviewerModelCount(purpose){
 const n=selectedReviewerModels(purpose).length;$('review-model-count-'+purpose).textContent=`${n} selected`;
}
function filterReviewerModels(){
 const query=$('review-model-search').value.trim().toLowerCase();
 for(const purpose of reviewPurposes){
  let visible=0;
  for(const row of $('review-model-'+purpose).children){row.hidden=!!query&&!row.dataset.search.includes(query);if(!row.hidden)visible++;}
  $('review-model-empty-'+purpose).hidden=visible>0;
  $('review-model-empty-'+purpose).textContent=query?'No models match your search.':'Refresh free models to see available reviewers.';
 }
}
function reviewerSettings(){
 const models={};for(const purpose of reviewPurposes)models[purpose]=selectedReviewerModels(purpose);
 return {enabled:$('review-enabled').checked,mode:$('review-mode').value,
  stages:reviewPurposes.filter(p=>$('review-stage-'+p).checked),models,
  concurrency:Number($('review-concurrency').value),limit:Number($('review-cap').value),timeout:Number($('review-timeout').value),context_budget_bytes:Number($('review-budget').value)*1024,tools:$('review-tools').value};
}
function reviewerModelChoices(settings){
 for(const purpose of reviewPurposes){
  const picker=$('review-model-'+purpose),chosen=settings.models[purpose]||[];picker.replaceChildren();
  const listed=new Map((reviewCatalogue?.models||[]).filter(m=>purpose!=='visual'||m.inputs.includes('image')).map(m=>[m.id,m]));
  for(const id of chosen)if(!listed.has(id))listed.set(id,{id,name:id,unconfirmed:true});
  for(const m of listed.values()){
   const row=document.createElement('label');row.className='review-model-choice';row.title=m.id;row.dataset.search=(m.name+' '+m.id).toLowerCase();
   const input=document.createElement('input');input.type='checkbox';input.value=m.id;input.checked=chosen.includes(m.id);
   const name=document.createElement('span');name.textContent=m.name;
   if(m.unconfirmed||m.vision_tested){const hint=document.createElement('small');hint.textContent=m.unconfirmed?'Refresh to check availability':'Image test confirmed';name.append(hint);}
   input.addEventListener('change',()=>{
    if(selectedReviewerModels(purpose).length>8){input.checked=false;notice('Choose up to 8 models for '+reviewNames[purpose]+'.',true);return;}
    reviewerModelCount(purpose);markReviewerDirty();
   });
   row.append(input,name);picker.append(row);
  }
  reviewerModelCount(purpose);
 }
 filterReviewerModels();
}
function renderReviewers(value){
 const key=cid(selected?.url),r=value?.reviewers;
 if(key!==reviewSelected){reviewSelected=key;reviewDirty=false;reviewRequestId=null;reviewEpoch++;reviewJobsSignature=null;reviewDetailCache.clear();reviewJobVersions.clear();}
 reviewerInfo=r||null;
 const settings=r?.policy?.settings;
 if(settings&&!reviewDirty){
  $('review-enabled').checked=settings.enabled;$('review-mode').value=settings.mode;
  $('review-tools').value=settings.tools||'plan';
  $('review-concurrency').value=settings.concurrency;$('review-cap').value=settings.limit;$('review-timeout').value=settings.timeout;$('review-budget').value=(settings.context_budget_bytes||524288)/1024;
  for(const p of reviewPurposes)$('review-stage-'+p).checked=settings.stages.includes(p);
  reviewerModelChoices(settings);
 }
 $('review-policy-state').textContent=reviewDirty?'Unsaved reviewer settings. Save to apply them.':!r?'Connect a conversation to configure external reviews.':
  `${settings.enabled?'ON · '+settings.mode.toUpperCase():'OFF · existing task behavior'} · ${r.task_id?'current task':'next goal'} · settings revision ${r.policy.version}`;
 $('review-required-state').textContent=r?.issue|| (r?.waiting?'Reviews running. The loop waits without using chat continuations.':'Opinions need evaluation by ChatGPT; they are separate from executed tests.');
 for(const id of ['review-save','review-refresh','review-request'])$(id).disabled=busy||!binding||binding.tab!==selected?.id;
 $('review-request').disabled||=!r?.task_id||!settings?.enabled||reviewDirty;
 const jobsSignature=JSON.stringify([reviewEpoch,r?.jobs||[],r?.has_more]);
 if(jobsSignature===reviewJobsSignature)return;reviewJobsSignature=jobsSignature;
 $('review-jobs').replaceChildren();
 for(const job of r?.jobs||[]){
  const cacheKey=r.task_id+':'+job.id,jobVersion=JSON.stringify(job);
  if(reviewJobVersions.get(cacheKey)!==jobVersion)reviewDetailCache.delete(cacheKey);
  reviewJobVersions.set(cacheKey,jobVersion);
  const card=document.createElement('li');card.className='review-job';
  const heading=document.createElement('strong');heading.textContent=`${reviewNames[job.purpose]} · ${job.model} · ${job.state}`;card.append(heading);
  const meta=document.createElement('p');meta.className='small muted';meta.textContent=[timestamp(job.created),job.stale_reason?'STALE: '+job.stale_reason:'Context current',job.decision?'Evaluated':'Evaluation pending'].join(' · ');card.append(meta);
  if(job.error){const p=document.createElement('p');p.textContent=job.error;card.append(p);}
  const detail=document.createElement('details'),title=document.createElement('summary');title.textContent='Response, findings & evaluation';detail.append(title);
  const pre=document.createElement('pre');pre.textContent=[job.summary,...(job.findings||[]).map(f=>`${f.id} · ${f.severity}\n${f.description}\nEvidence: ${f.evidence}\nRecommendation: ${f.recommendation}`),job.decision?'ChatGPT evaluation:\n'+JSON.stringify(job.decision,null,2):''].filter(Boolean).join('\n\n');detail.append(pre);card.append(detail);
  const button=document.createElement('button');button.className='quiet';button.textContent=job.state==='uncertain'?'Exclude uncertain attempt':['queued','running'].includes(job.state)?'Cancel review':'Load context for a new review';
  button.disabled=busy;button.onclick=async()=>{
   try{
    const epoch=reviewEpoch;
    if(['queued','running','uncertain'].includes(job.state)){await native({action:'reviews_cancel',task_id:r.task_id,job_id:job.id});await status();return;}
    const full=await loadReviewerDetail(r.task_id,job.id,epoch);
    const packet=full.packet;
    $('review-purpose').value=packet.purpose;$('review-strategy').value=packet.strategy;
    $('review-question').value=packet.question;$('review-justification').value=packet.reason;
    $('review-context').value=packet.context;$('review-files').value=packet.files.map(f=>f.path+':'+f.start_line+':'+f.end_line).join('\n');
    $('review-images').value=packet.images.map(f=>f.path).join('\n');$('review-repeat').checked=true;
    $('review-question').scrollIntoView({behavior:'smooth',block:'center'});
    notice('Context loaded. Inspect the question and evidence before explicitly requesting a new review. No request sent.');
   }catch(e){notice(e.message,true);}
  };card.append(button);
  if(job.state==='completed'){
   const fullBox=document.createElement('div'),fullButton=document.createElement('button');fullButton.className='quiet';fullButton.textContent='View full review';
   const renderFull=full=>{
    fullBox.replaceChildren();
    const response=document.createElement('pre');response.className='full-review-response';response.textContent=full.result?.response||full.result?.summary||'No provider response retained.';fullBox.append(response);
    for(const finding of full.result?.findings||[]){
     const line=document.createElement('p');line.textContent=`${finding.id} · ${finding.severity}
${finding.description}
Evidence: ${finding.evidence}
Recommendation: ${finding.recommendation}`;fullBox.append(line);
     const investigate=document.createElement('button');investigate.className='quiet';investigate.textContent='Ask ChatGPT to investigate '+finding.id;
     const updateId=crypto.randomUUID();investigate.onclick=async()=>{
      const epoch=reviewEpoch;investigate.disabled=true;
      try{await native({action:'reviews_investigate',task_id:r.task_id,job_id:job.id,finding_id:finding.id,id:updateId});if(epoch!==reviewEpoch)throw Error('Conversation changed.');await status();notice('Investigation queued for this task. No chat message sent. Bridge contact or a permitted continuation can deliver it; if the loop is stopped, review resume.');}
      catch(e){notice(e.message,true);}finally{investigate.disabled=false;}
     };fullBox.append(investigate);
    }
    const supplied=document.createElement('details'),label=document.createElement('summary'),body=document.createElement('pre');label.textContent='Full supplied context and ChatGPT evaluation';body.textContent=JSON.stringify({packet:full.packet,evaluation:full.decision},null,2);supplied.append(label,body);fullBox.append(supplied);
   };
   fullButton.onclick=async()=>{fullButton.disabled=true;try{renderFull(await loadReviewerDetail(r.task_id,job.id,reviewEpoch));}catch(e){notice(e.message,true);}finally{fullButton.disabled=false;}};
   card.append(fullButton,fullBox);
  }
  $('review-jobs').append(card);
 }
 if(r?.has_more){const p=document.createElement('li');p.textContent='Latest reviews shown. Retrieve earlier jobs with external_review_status.';$('review-jobs').append(p);}
}
async function loadReviewerDetail(taskId,jobId,epoch){
 const key=taskId+':'+jobId;if(reviewDetailCache.has(key))return reviewDetailCache.get(key);
 let offset=0,version='',body='';
 do{
  const result=await native({action:'reviews_detail',task_id:taskId,job_id:jobId,offset,version});
  if(epoch!==reviewEpoch)throw Error('Conversation changed while loading the full review.');
  const page=result.review_detail;
  if(!page||page.id!==jobId||page.total_characters>3000000||typeof page.text!=='string'||(page.next_offset!==null&&page.next_offset<=offset))throw Error('Invalid review detail page.');
  if(version&&page.version!==version)throw Error('Review detail changed. Reload the full response.');
  version=page.version;body+=page.text;offset=page.next_offset;
 }while(offset!==null);
 const full=JSON.parse(body);reviewDetailCache.set(key,full);return full;
}
for(const id of ['review-enabled','review-mode','review-tools','review-concurrency','review-cap','review-timeout','review-budget',...reviewPurposes.map(p=>'review-stage-'+p)]){
 $(id).addEventListener('change',markReviewerDirty);
}
$('review-model-search').addEventListener('input',filterReviewerModels);
action('review-save',async()=>{
 if(!reviewerInfo)throw Error('Connect a conversation first.');
 const epoch=reviewEpoch,editRevision=reviewEditRevision;
 await native({action:'reviews_configure',task_id:reviewerInfo.task_id,version:reviewerInfo.policy.version,
  next_policy_version:reviewerInfo.next_policy_version,future:$('review-future').checked,settings:reviewerSettings()});
 if(epoch!==reviewEpoch)throw Error('Conversation changed while saving. Refresh before editing.');
 if(editRevision===reviewEditRevision)reviewDirty=false;
 await status();notice(reviewDirty?'Reviewer settings saved. Your newer edits are still unsaved.':'Reviewer settings saved. No model request or chat message sent.');
});
action('review-refresh',async()=>{
 const epoch=reviewEpoch;const result=await native({action:'reviews_catalog',refresh:true});
 if(epoch!==reviewEpoch)throw Error('Conversation changed while refreshing.');
 const settings=reviewerSettings();reviewCatalogue=result.catalogue;
 reviewerModelChoices(settings);$('review-catalogue-state').textContent=`${reviewCatalogue.models.length} zero-cost models confirmed · ${timestamp(reviewCatalogue.updated)}`+(reviewCatalogue.stale?' · STALE: calls disabled':'');
 notice(reviewCatalogue.stale?'Catalogue refresh failed. Calls remain disabled until free status is confirmed.':'Free models refreshed. Your model selections were preserved.',reviewCatalogue.stale);
});
action('review-request',async()=>{
 if(reviewDirty)throw Error('Save reviewer settings first.');
 const lines=id=>$(id).value.split('\n').map(x=>x.trim()).filter(Boolean);
 const files=lines('review-files').map(line=>{const m=line.match(/^(.*):(\d+):(\d+)$/);return m?{path:m[1],start_line:Number(m[2]),end_line:Number(m[3])}:{path:line};});
 const payload={action:'reviews_request',task_id:reviewerInfo.task_id,id:reviewRequestId||crypto.randomUUID(),
  purpose:$('review-purpose').value,strategy:$('review-strategy').value,question:$('review-question').value,
  reason:$('review-justification').value,context:$('review-context').value,files,images:lines('review-images'),repeat:$('review-repeat').checked};
 if(typeof TextEncoder!=='undefined'&&new TextEncoder().encode(JSON.stringify(payload)).length>62000)throw Error('Request exceeds browser transport size. Narrow the inline context or select files instead. Nothing sent.');
 reviewRequestId=payload.id;
 await native(payload);reviewRequestId=null;await status();notice('Review request recorded. ChatGPT can retrieve and evaluate it through the Core tools.');
});
for(const id of ['review-purpose','review-strategy','review-question','review-justification','review-context','review-files','review-images','review-repeat']){
 $(id).addEventListener('input',()=>{reviewRequestId=null;});
}
