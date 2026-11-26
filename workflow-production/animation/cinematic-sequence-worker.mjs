/** Cinematic sequence lane: one Wan run; background finals additionally require a real 2x postprocess. */
import {DatabaseSync} from 'node:sqlite';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';
import {png,maxImageBytes,maxFrameBytes,maxBundleBytes} from './i2v-worker.mjs';
import {backgroundUpscaleModel,backgroundUpscaleScale,backgroundUpscaleMaxBytes} from './seedvr2-upscale.mjs';
import {sequenceCheckpoints} from './sequence-checkpoint.mjs';
import {backgroundAnimationPixelMethod} from './reference-palette-correction.mjs';
import {validCutscenePrompt} from './cutscene-i2v.mjs';
import {WAN_I2V_FPS,WAN_I2V_LENGTH} from './i2v-wan-graph.mjs';

export const sequenceType='cinematic.sequence.generate',sequenceSchema='ngs.cinematic-sequence-generate.v1';
export const sequenceKey='cinematic_sequence_render',sequenceTaskType='cinematic.sequence.generate.render';
const sequencePath='/v1/cinematics/ownership/workflow-sequence',sequenceCapability='workflow.cinematic-sequence-write.v1';
const projectId='binari',workerType='animation';
const oid=/^request\.official\.[a-f0-9]{64}$/u,digest=/^[a-f0-9]{64}$/u;
const hash=v=>typeof v==='string'&&v.length===64&&digest.test(v);
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const require=(ok,code='CINEMATIC_SEQUENCE_CLAIM_BINDING')=>{if(!ok)throw Error(code);};
const failureCode=e=>typeof e?.message==='string'&&/^[A-Z0-9_]{1,100}$/u.test(e.message)?e.message:'CINEMATIC_SEQUENCE_UNCONFIRMED';
const n=(v,a,b)=>Number.isFinite(v)&&v>=a&&v<=b;
const grid=v=>Number.isSafeInteger(v)&&v>=128&&v<=512&&v%32===0;
function curve(list,duration,keys,values){
 return Array.isArray(list)&&list.length>=1&&list.length<=128&&Array.from(list).every((k,i)=>
  exact(k,['frame',...keys])&&Number.isSafeInteger(k.frame)&&k.frame<duration&&
  (i===0?k.frame===0:k.frame>list[i-1].frame)&&values(k));
}
export function validSequencePlayback(v){
 return exact(v,['durationFrames','speedCurve','cameraCurve'])&&Number.isSafeInteger(v.durationFrames)&&n(v.durationFrames,1,1440)&&
  curve(v.speedCurve,v.durationFrames,['rate'],k=>n(k.rate,0,4))&&
  curve(v.cameraCurve,v.durationFrames,['zoom','x','y'],k=>n(k.zoom,1,3)&&n(k.x,0,1)&&n(k.y,0,1));
}
const backgroundType='background.animation.workflow',backgroundSchema='ngs.background-animation-workflow.v1';
const backgroundKeys={cinematic:'background_animation_cinematic',pixel:'background_animation_pixel'};
const backgroundStageSchema='ngs.background-animation-workflow.v2';
const sequenceOperations={wan:'media.animation.render',upscale:'media.frames.upscale',pixel_correct:'media.frames.pixel-correct',publish:'media.sequence.publish'};
const phases={cinematic:['wan','upscale','publish'],pixel:['wan','upscale','pixel_correct','publish']};
const stagedKeys=Object.entries(phases).flatMap(([v,steps])=>steps.map(stage=>backgroundKeys[v]+'_'+stage));
const links=`(t.depends_on_task_id IS NULL OR EXISTS (SELECT 1 FROM workflow_tasks p
 WHERE p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id AND p.status='completed' AND p.result_ref IS NOT NULL))`;
const scope=`r.state='planned' AND r.reason IS NULL AND r.project_id=?
 AND t.worker_type=? AND t.result_ref IS NULL AND ${links}`+
 ` AND ((r.request_type=? AND r.schema_version=? AND t.task_type=? AND t.task_key=? AND t.depends_on_task_id IS NULL)
  OR (r.request_type=? AND r.schema_version=? AND t.task_type=? AND t.task_key IN (?,?) AND t.depends_on_task_id IS NULL)
  OR (r.request_type=? AND r.schema_version=? AND t.task_key IN (${stagedKeys.map(()=>'?').join(',')})))`;
const args=[projectId,workerType,sequenceType,sequenceSchema,sequenceTaskType,sequenceKey,
 backgroundType,backgroundSchema,backgroundType,...Object.values(backgroundKeys),backgroundType,backgroundStageSchema,...stagedKeys];
const taskIdFor=(id,key)=>'workflow.task.'+sha(canonical([id,key]));
const refFor=id=>projectId+'/cinematics/sequences/'+id+'/manifest.json';
const inputKeys=['sourceSha256','motionPrompt','playback'];
function tx(db,fn){
 db.exec('BEGIN IMMEDIATE');
 try{const value=fn();db.exec('COMMIT');return value;}
 catch(e){db.exec('ROLLBACK');throw e;}
}

export function chooseCinematicSequence(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  return db.prepare(`SELECT t.task_id AS taskId,r.official_request_id AS officialRequestId
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) WHERE ${scope} AND ${workflowTaskEligibleSql('t')}
   ORDER BY r.registered_at,r.official_request_id,t.task_id LIMIT 1`).get(...args)??null;
 }finally{db.close();}
}
function candidate(db,t,stamp){
 const running=stamp!==undefined;
 const row=db.prepare(`SELECT t.*,r.payload,r.payload_sha256,r.registered_at,r.request_type,r.schema_version
  FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) WHERE ${scope}
  AND t.task_id=? AND t.official_request_id=? AND ${running?"t.status='running' AND t.updated_at=?":workflowTaskEligibleSql('t')}`)
  .get(...args,t.taskId,t.officialRequestId,...(running?[stamp]:[]));
 if(!row)return null;
 const p=JSON.parse(row.payload);
 require(p?.projectId===projectId&&validCutscenePrompt(p.motionPrompt)&&validSequencePlayback(p.playback)&&
  canonical(p)===row.payload&&sha(row.payload)===row.payload_sha256);
 const only=db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 8').all(t.officialRequestId);
 if(row.request_type===backgroundType){
  const refs=p.references,variants=Object.keys(refs??{}),staged=row.schema_version===backgroundStageSchema;
  require(exact(p,['schemaVersion','projectId','backgroundSha256','references','motionPrompt','playback'])&&p.schemaVersion===row.schema_version&&
   hash(p.backgroundSha256)&&variants.length>=1&&variants.length<=2&&exact(refs,variants)&&
   variants.every(v=>Object.hasOwn(backgroundKeys,v)&&hash(refs[v]))&&new Set(Object.values(refs)).size===variants.length&&
   only.length===variants.reduce((count,v)=>count+(staged?phases[v].length:1),0));
  let selected;
  for(const variant of variants){
   const base=backgroundKeys[variant],input={sourceSha256:refs[variant],motionPrompt:p.motionPrompt,playback:p.playback};
   const group={requestId:t.officialRequestId,variant,backgroundSha256:p.backgroundSha256};
   let prior=null,priorPhase;const ancestors=[];
   for(const phase of staged?phases[variant]:[null]){
    const key=phase?base+'_'+phase:base,id=taskIdFor(t.officialRequestId,key);
    const entry=only.find(x=>x.task_id===id);
    require(entry?.task_key===key&&entry.task_type===(phase?sequenceOperations[phase]:backgroundType)&&entry.worker_type===workerType&&
     entry.depends_on_task_id===(prior?.task_id??null)&&
     entry.task_input===canonical({...input,variant,backgroundSha256:p.backgroundSha256,...(phase?{stage:phase}:{})}));
    if(id===t.taskId){
     require(ancestors.every(x=>x.status==='completed'&&/^background\.transient\.[a-f0-9]{64}$/u.test(x.result_ref??'')));
     selected={input,row,group,phase,resultId:'request.official.'+sha(canonical([t.officialRequestId,base])),
      ...(prior?{prior:{taskId:prior.task_id,stage:priorPhase,expectedRef:prior.result_ref}}:{})};
    }
    prior=entry;priorPhase=phase;ancestors.push(entry);
   }
  }
  require(selected);return selected;
 }
 require(exact(p,['schemaVersion','projectId',...inputKeys])&&p.schemaVersion===sequenceSchema&&hash(p.sourceSha256));
 const input=Object.fromEntries(inputKeys.map(k=>[k,p[k]]));
 require(canonical(input)===row.task_input&&only.length===1&&only[0].task_id===taskIdFor(t.officialRequestId,sequenceKey));
 return {input,row,resultId:t.officialRequestId};
}

function claimSequence(db,t){
 require(typeof t?.officialRequestId==='string'&&t.officialRequestId.length===81&&oid.test(t.officialRequestId)&&[sequenceKey,...Object.values(backgroundKeys),...stagedKeys].some(key=>t.taskId===taskIdFor(t.officialRequestId,key)));
 return tx(db,()=>{
  const found=candidate(db,t);
  if(!found)return null;
  const {row}=found;
  const claimStamp=Math.max(Date.now(),row.updated_at+1);
  require(Number.isSafeInteger(claimStamp)&&claimStamp<Number.MAX_SAFE_INTEGER&&Number.isSafeInteger(row.registered_at)&&
   row.registered_at>=0&&row.registered_at<=claimStamp);
  require(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
   WHERE task_id=? AND official_request_id=? AND updated_at=? AND result_ref IS NULL AND ${workflowTaskEligibleSql()}`)
   .run(claimStamp,t.taskId,t.officialRequestId,row.updated_at).changes===1);
  return {...found,claim:{taskId:t.taskId,officialRequestId:t.officialRequestId,projectId,payloadSha256:row.payload_sha256,claimStamp}};
 });
}

export const cinematicSequencePorts=local=>({
 async ready(signal){
  const v=await local(sequencePath,{signal,maxJsonBytes:4096});
  return exact(v,['enabled','schemaVersion'])&&v.enabled===true&&v.schemaVersion===sequenceCapability;
 },
 read:(claim,signal)=>local(sequencePath+'/read',{body:{claim},signal,maxJsonBytes:64*1024}),
 asset:(claim,signal)=>local(sequencePath+'/asset',{body:{claim},signal,binary:true,maxBinaryBytes:maxImageBytes}),
 stage:(claim,frame)=>local(sequencePath+'/frame',{body:{claim,frame},timeout:20_000,maxJsonBytes:4096}),
 publish:(claim,manifest)=>local(sequencePath,{body:{claim,manifest},timeout:40_000,maxJsonBytes:4096}),
});

async function sameClaimPublication(attempt,{current,signal}){
 try{return await attempt();}
 catch(e){
  // The exact same claim and bytes are idempotent. Recheck the fence and
  // retry at most once in-process on uncertain transport/server errors.
  const knownRefusal=typeof e?.message==='string'&&/^ANIMATION_PDS_HTTP_4(?!08|29)\d\d$/u.test(e.message);
  if(knownRefusal)throw e;
  signal.throwIfAborted();current();
  return await attempt();
 }
}
function nativeManifest(output,id,input,upscale){
 const outputGrid=upscale?n=>grid(n/backgroundUpscaleScale):grid;
 const budget=upscale?backgroundUpscaleMaxBytes:maxBundleBytes;
 require(exact(output,['frames','width','height','fps'])&&output.fps===WAN_I2V_FPS&&outputGrid(output.width)&&outputGrid(output.height)&&
  Array.isArray(output.frames)&&output.frames.length===WAN_I2V_LENGTH,'CINEMATIC_SEQUENCE_RENDER_SHAPE');
 let total=0;
 const frames=Array.from(output.frames,(buffer,index)=>{
  const shape=png(buffer,maxFrameBytes,{rgb:true});total+=buffer.length;
  require(shape.width===output.width&&shape.height===output.height&&total<=budget,'CINEMATIC_SEQUENCE_FRAME_SHAPE');
  return {index,sha256:sha(buffer),bytes:buffer.length};
 });
 return {schemaVersion:'ngs.cinematic-sequence-result.v1',projectId,officialRequestId:id,sourceSha256:input.sourceSha256,
  width:output.width,height:output.height,masterFps:WAN_I2V_FPS,frameCount:frames.length,frames,playback:input.playback,...(upscale?{upscale}:{})};
}

/** The resident owns both models. Only a verified final passes to publication. */
export async function executeCinematicSequence(db,t,signal,{ports,runI2V,runUpscale,probeUpscale,runPixelCorrection,checkpointRoot,
 makeDir=()=>mkdtemp(join(tmpdir(),'workflow-cinematic-sequence-')),cleanup=dir=>rm(dir,{recursive:true,force:true})}){
 if(signal.aborted)return {state:'stale',taskId:t.taskId};
 const found=claimSequence(db,t);
 if(!found)return {state:'stale',taskId:t.taskId};
 const {claim,input,row,resultId,group,phase,prior}=found;
 const current=()=>{
  const next=candidate(db,t,claim.claimStamp);
  require(next&&canonical(next.prior??null)===canonical(prior??null)&&['payload','task_input','payload_sha256','registered_at'].every(k=>row[k]===next.row[k]),'CINEMATIC_SEQUENCE_CLAIM_CHANGED');
 };
 const commit=expected=>tx(db,()=>{
  current();
  require(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
   WHERE task_id=? AND official_request_id=? AND status='running' AND result_ref IS NULL AND updated_at=?`)
   .run(expected,Math.max(Date.now(),claim.claimStamp+1),t.taskId,t.officialRequestId,claim.claimStamp).changes===1,'CINEMATIC_SEQUENCE_COMPLETION_CHANGED');
 });
 const store=phase&&checkpointRoot?sequenceCheckpoints(checkpointRoot):null;
 const bind=phase?{requestId:t.officialRequestId,variant:group.variant,payloadSha256:claim.payloadSha256}:null;
 const releasePrevious=async()=>{if(store&&prior)try{await store.release({...bind,stage:prior.stage});}catch{return false;}return true;};
 let dir,result,stage='read',unknownStop=false,written=false;
 try{
  const r=await ports.read(claim,signal);
  require(exact(r,inputKeys)&&canonical(r)===canonical(input),'CINEMATIC_SEQUENCE_SOURCE_BINDING');
  let output,manifest;
  const pinned=async()=>{
   const source=await ports.asset(claim,signal);png(source,maxImageBytes);
   require(sha(source)===input.sourceSha256,'CINEMATIC_SEQUENCE_SOURCE_BYTES');return source;
  };
  if(phase){
   require(store,'SEQUENCE_CHECKPOINT_UNAVAILABLE');
   const own={...bind,taskId:t.taskId,stage:phase};
   const recorded=phase==='publish'?null:await store.read({...own,optional:true});
   if(recorded){
    manifest=nativeManifest(recorded.output,resultId,input,recorded.manifest.upscale);
    require(canonical(manifest)===canonical(recorded.manifest),'SEQUENCE_CHECKPOINT_BINDING');
    commit(recorded.reference);const released=await releasePrevious();
    return {state:'completed',taskId:t.taskId,resultRef:recorded.reference,frameCount:manifest.frameCount,...(released?{}:{cleanup:'pending'})};
   }
   const prev=prior?await store.read({...bind,...prior}):null;
   if(prev)require(canonical(nativeManifest(prev.output,resultId,input,prev.manifest.upscale))===canonical(prev.manifest),'SEQUENCE_CHECKPOINT_BINDING');
   signal.throwIfAborted();tx(db,current);
   if(phase==='wan'||phase==='upscale'||phase==='pixel_correct')dir=await makeDir();
   if(phase==='wan'){
    stage='render';
    const source=await pinned();
    try{output=await runI2V({dir,source,motionPrompt:input.motionPrompt,signal});}
    catch(e){unknownStop=e?.rendererJoined!==true;throw e;}
    manifest=nativeManifest(output,resultId,input);
   }else if(phase==='upscale'){
    stage='upscale';
    require(typeof runUpscale==='function','BACKGROUND_SEEDVR2_UNAVAILABLE');
    if(probeUpscale)await probeUpscale();
    const upscale={model:backgroundUpscaleModel,scale:backgroundUpscaleScale,sourceWidth:prev.output.width,sourceHeight:prev.output.height};
    try{output=await runUpscale({dir,output:prev.output,signal});}
    catch(e){unknownStop=e?.rendererJoined!==true;throw e;}
    stage='validate_upscale';
    require(output?.width===upscale.sourceWidth*2&&output?.height===upscale.sourceHeight*2,'BACKGROUND_SEEDVR2_OUTPUT_SHAPE');
    manifest=nativeManifest(output,resultId,input,upscale);
   }else if(phase==='pixel_correct'){
    stage='pixel_correction';
    require(typeof runPixelCorrection==='function'&&prev.manifest.upscale,'BACKGROUND_ANIMATION_PIXEL_UNAVAILABLE');
    const reference=await pinned(),upscaledFramesSha256=sha(canonical(prev.manifest.frames.map(f=>f.sha256)));
    output=await runPixelCorrection({dir,reference,output:prev.output,signal});
    require(output?.width===prev.output.width&&output?.height===prev.output.height,'BACKGROUND_ANIMATION_PIXEL_FRAME_SHAPE');
    manifest=nativeManifest(output,resultId,input,{...prev.manifest.upscale,pixelCorrection:{method:backgroundAnimationPixelMethod,upscaledFramesSha256}});
   }else{
    stage='verify_publish_input';output=prev.output;manifest={...prev.manifest,group};
    require(manifest.upscale&&(group.variant!=='pixel'||manifest.upscale.pixelCorrection),'CINEMATIC_SEQUENCE_PUBLICATION_INPUT');
   }
   if(phase!=='publish'){
    stage='checkpoint';
    const expected=await store.write({...own,manifest,frames:output.frames,current:()=>tx(db,current),signal});
    commit(expected);const released=await releasePrevious();
    result={state:'completed',taskId:t.taskId,resultRef:expected,frameCount:manifest.frameCount,...(released?{}:{cleanup:'pending'})};
    return result;
   }
  }else{
  stage='asset';
  const source=await ports.asset(claim,signal);
  png(source,maxImageBytes);
  require(sha(source)===input.sourceSha256,'CINEMATIC_SEQUENCE_SOURCE_BYTES');
  if(group){
   stage='upscale_preflight';
   require(typeof runUpscale==='function','BACKGROUND_SEEDVR2_UNAVAILABLE');
   if(probeUpscale)await probeUpscale();
   if(group.variant==='pixel')require(typeof runPixelCorrection==='function','BACKGROUND_ANIMATION_PIXEL_UNAVAILABLE');
  }
  stage='prepare';dir=await makeDir();
  signal.throwIfAborted();tx(db,current);
  stage='render';
  try{output=await runI2V({dir,source,motionPrompt:input.motionPrompt,signal});}
  catch(e){unknownStop=e?.rendererJoined!==true;throw e;}
  stage='validate';
  manifest=nativeManifest(output,resultId,input);
  if(group){
   const upscale={model:backgroundUpscaleModel,scale:backgroundUpscaleScale,sourceWidth:manifest.width,sourceHeight:manifest.height};
   stage='upscale';
   signal.throwIfAborted();tx(db,current);
   try{output=await runUpscale({dir,output,signal});}
   catch(e){unknownStop=e?.rendererJoined!==true;throw e;}
   stage='validate_upscale';
   require(output?.width===upscale.sourceWidth*upscale.scale&&output?.height===upscale.sourceHeight*upscale.scale,
    'BACKGROUND_SEEDVR2_OUTPUT_SHAPE');
   manifest=nativeManifest(output,resultId,input,upscale);
   if(group.variant==='pixel'){
    stage='pixel_correction';
    signal.throwIfAborted();tx(db,current);
    const upscaledFramesSha256=sha(canonical(manifest.frames.map(frame=>frame.sha256)));
    output=await runPixelCorrection({dir,reference:source,output,signal});
    stage='validate_pixel_correction';
    signal.throwIfAborted();tx(db,current);
    require(output?.width===manifest.width&&output?.height===manifest.height,'BACKGROUND_ANIMATION_PIXEL_FRAME_SHAPE');
    manifest=nativeManifest(output,resultId,input,{...upscale,pixelCorrection:{method:backgroundAnimationPixelMethod,upscaledFramesSha256}});
   }
   manifest.group=group;
  }
  }
  const uploadSignal=AbortSignal.any([signal,AbortSignal.timeout(group?300_000:120_000)]);
  const upload={signal:uploadSignal,current:()=>tx(db,current)};
  for(const frame of manifest.frames){
   uploadSignal.throwIfAborted();upload.current();
   stage='stage_frame';written=true;
   const payload={...frame,base64:output.frames[frame.index].toString('base64')};
   const ack=await sameClaimPublication(()=>ports.stage(claim,payload),upload);
   require(exact(ack,['ok','index','sha256'])&&ack.ok===true&&ack.index===frame.index&&ack.sha256===frame.sha256,
    'CINEMATIC_SEQUENCE_FRAME_UNCONFIRMED');
  }
  uploadSignal.throwIfAborted();upload.current();
  stage='publish';
  const expected=refFor(resultId);
  const ack=await sameClaimPublication(()=>ports.publish(claim,manifest),upload);
  require(exact(ack,['ok','resultRef'])&&ack.ok===true&&ack.resultRef===expected,'CINEMATIC_SEQUENCE_PUBLICATION_UNCONFIRMED');
  stage='commit';
  commit(expected);const released=await releasePrevious();
  result={state:'completed',taskId:t.taskId,resultRef:expected,frameCount:manifest.frameCount,...(released?{}:{cleanup:'pending'})};
 }catch(e){
  let failed=false;
  // A stage/commit attempt can have side effects without an ACK. Preserve
  // the running claim, rather than automatically rerender a second sequence.
  if(!unknownStop&&!written){
   try{failed=tx(db,()=>{current();return markWorkflowTaskFailed(db,{...claim,workerType});});}catch{}
  }
  result={state:failed?'failed':'requires_reconciliation',taskId:t.taskId,stage,code:failureCode(e),
   ...(['render','upscale'].includes(stage)?{rendererStop:unknownStop?'unknown':'confirmed'}:{})};
 }finally{
  if(dir&&!unknownStop)try{await cleanup(dir);}catch{result={...result,cleanup:'pending'};}
 }
 return result;
}
