import {DatabaseSync} from 'node:sqlite';
import {mkdtemp,readFile,writeFile,rm,open} from 'node:fs/promises';
import {constants as fsConstants} from 'node:fs';
import {homedir,tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {setTimeout as delay} from 'node:timers/promises';
import {fileURLToPath} from 'node:url';
import {createWarmAnimation,spawnAnimationChannel} from './animation-warm.mjs';
import {chooseI2V,executeI2V,i2vPorts} from './i2v-worker.mjs';
import {createBackgroundSeedVR2} from './background-seedvr2.mjs';
import {createAnimationModelSwitch} from './i2v-model-switch.mjs';
import {choosePropAnimation,executePropAnimation,propAnimationPorts} from './prop-animation-worker.mjs';
import {confirmBindingPath,defaultPropBinding,propAnimationReadiness,cinematicAnimationReadiness,cinematicSequenceReadiness} from './prop-animation-readiness.mjs';
import {chooseCinematic,executeCinematic,cinematicPorts} from './cinematic-worker.mjs';
import {chooseCinematicSequence,executeCinematicSequence,cinematicSequencePorts} from './cinematic-sequence-worker.mjs';
import {createPropI2VInputFraming} from './prop-i2v-input-framing.mjs';
import {probeCutsceneEncoder} from './cutscene-i2v.mjs';
import {WAN_I2V_MODEL_ID,WAN_I2V_QUANTIZATION} from './i2v-wan-graph.mjs';
import {choosePresetFrames,executePresetFrames,presetFramePorts} from './preset-frame-worker.mjs';
import {isSelectedMediaRun,isRegenerateRun,selectionSchemas,poseSelectionSchemas,regenerateType,regenerateSchemas,selectedMotionBranch,matchesMotionTask} from './vendor/npc-motion-selection-v1/media-branch.mjs';
import {assertDatabasePath,protectSchema,nextWorkflowTaskFromPath,oldestRunnable,markWorkflowTaskFailed,workflowTaskEligibleSql,workflowTaskRetrySetSql} from './workflow-ready.mjs';

const path='/v1/npcs/ownership/workflow-animation',type='npc.animation-native-png';
const directions=['n','ne','e','se','s','sw','w','nw'], motions=['walk','run'];
const sha=b=>createHash('sha256').update(b).digest('hex');
const provider=process.env.WORKFLOW_WAN_RELEASE, py=process.env.WORKFLOW_WAN_PYTHON,
 binding=process.env.WORKFLOW_WAN_BINDING;
const helper=fileURLToPath(new URL('./animation-native.py',import.meta.url));
function insist(test,error){if(!test)throw Error(error);}
async function local(route,{body,rawBody,port=8030,signal,timeout=30000,binary=false,maxBinaryBytes,maxJsonBytes,onRejected}={}){
 insist(rawBody===undefined||Buffer.isBuffer(rawBody)&&body===undefined,'ANIMATION_PDS_REQUEST_TYPE');
 const response=await fetch('http://127.0.0.1:'+port+route,{method:body||rawBody?'POST':'GET',redirect:'error',
  headers:rawBody?{'Content-Type':'application/octet-stream'}:body?{'Content-Type':'application/json'}:{},
  body:rawBody??(body?JSON.stringify(body):undefined),
  signal:AbortSignal.any([AbortSignal.timeout(timeout),...(signal?[signal]:[])])});
 const recognizedReject=typeof onRejected==='function'&&response.status===422&&maxJsonBytes;
 insist(response.ok||recognizedReject,'ANIMATION_PDS_HTTP_'+response.status);
 if(binary){
  if(!maxBinaryBytes)return Buffer.from(await response.arrayBuffer());
  insist(/^image\/png(?:\s*;|$)/i.test(response.headers.get('Content-Type')??'')&&response.body,'ANIMATION_PDS_RESPONSE_TYPE');
  const size=response.headers.get('Content-Length');
  insist(size===null||/^\d+$/.test(size)&&Number(size)<=maxBinaryBytes,'ANIMATION_PDS_RESPONSE_SIZE');
  let total=0;const pieces=[];
  for await(const chunk of response.body){total+=chunk.length;insist(total<=maxBinaryBytes,'ANIMATION_PDS_RESPONSE_SIZE');pieces.push(chunk);}
  return Buffer.concat(pieces);
 }
 if(maxJsonBytes){
  insist(/^application\/json(?:\s*;|$)/i.test(response.headers.get('Content-Type')??'')&&response.body,
   'ANIMATION_PDS_RESPONSE_TYPE');
  const size=response.headers.get('Content-Length');
  insist(size===null||/^\d+$/.test(size)&&Number(size)<=maxJsonBytes,'ANIMATION_PDS_RESPONSE_SIZE');
  const parts=[];let total=0;
  for await(const chunk of response.body){
   total+=chunk.length;
   insist(total<=maxJsonBytes,'ANIMATION_PDS_RESPONSE_SIZE');parts.push(chunk);
  }
  const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(parts)));
  if(recognizedReject){
   onRejected(value); // only a positively authenticated scoped pre-write rejection is definite
   throw Error('ANIMATION_PDS_HTTP_'+response.status);
  }
  return value;
 }
 return response.json();
}
function transaction(db,fn){db.exec('BEGIN IMMEDIATE');try{const r=fn();db.exec('COMMIT');return r;}catch(e){db.exec('ROLLBACK');throw e;}}
function chooseSelected(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  const cols=new Set(db.prepare('PRAGMA table_info(workflow_runs)').all().map(r=>r.name));
  if(!['request_type','schema_version','reason'].every(k=>cols.has(k)))return;
  const row=db.prepare(`SELECT r.*,t.task_id,t.task_key
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
   LEFT JOIN workflow_tasks p ON p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id
   WHERE r.state='planned' AND r.reason IS NULL AND r.project_id='binari'
   AND ${workflowTaskEligibleSql('t')} AND t.result_ref IS NULL AND t.worker_type='animation' AND t.task_type=?
   AND t.task_key GLOB 'animation_motion_*' AND (
    (r.request_type='npc.workflow' AND r.schema_version IN (${selectionSchemas.map(()=>'?')})
      AND p.status='completed' AND p.worker_type=CASE WHEN r.schema_version IN (${poseSelectionSchemas.map(()=>'?')}) THEN 'postprocess' ELSE 'image' END)
    OR (r.request_type=? AND r.schema_version IN (${regenerateSchemas.map(()=>'?')}) AND t.depends_on_task_id IS NULL))
   ORDER BY r.registered_at,r.official_request_id,t.task_key LIMIT 1`).get(type,...selectionSchemas,...poseSelectionSchemas,regenerateType,...regenerateSchemas);
  if(!row)return;
  const {target,parent}=selectedMotionBranch(row,row.task_key);
  insist(target.id===row.task_id,'ANIMATION_DB_TARGET');
  return {taskId:row.task_id,officialRequestId:row.official_request_id,key:target.key,parent:parent?.key??null,
   motion:target.input.motion,direction:target.input.direction};
 }finally{db.close();}
}
function animationCandidates(dbPath){
 const candidates=[];
 for(const direction of directions)for(const motion of motions){
  const key='animation_'+motion+'_'+direction, parent='portrait_'+direction;
  const row=nextWorkflowTaskFromPath(dbPath,{workerType:'animation',taskType:type,taskKey:key,predecessorKey:parent});
  if(row)candidates.push({...row,key,parent,motion,direction});
 }
 return [...candidates,chooseSelected(dbPath)];
}
const choose=dbPath=>oldestRunnable(dbPath,animationCandidates(dbPath))[0];
function claim(db,t){
 const selected=t.key.startsWith('animation_motion_');
 const fields=selected?'t.*,r.project_id,r.payload_sha256,r.payload,r.request_type,r.schema_version,r.state,r.reason,r.registered_at':
  't.updated_at,t.status,t.task_input,r.project_id,r.payload_sha256,r.payload';
 const s=db.prepare(`SELECT ${fields}
  FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
  ${selected?'LEFT ':''}JOIN workflow_tasks p ON p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id
  WHERE r.state='planned' AND ${workflowTaskEligibleSql('t')} AND t.result_ref IS NULL AND t.worker_type='animation'
   AND t.task_type=? AND t.task_id=? AND t.official_request_id=? AND t.task_key=? AND
   ${selected?`((? IS NULL AND r.request_type=? AND r.schema_version IN (${regenerateSchemas.map(()=>'?')}) AND t.depends_on_task_id IS NULL) OR (p.task_key=? AND p.status='completed'))`:
     "(p.task_key=? AND p.status='completed')"}`);
 return transaction(db,()=>{
  const args=selected?[t.parent,regenerateType,...regenerateSchemas,t.parent]:[t.parent];
  const r=s.get(type,t.taskId,t.officialRequestId,t.key,...args);
  if(!r)return null;
  const params=JSON.parse(r.task_input), intent=JSON.parse(r.payload);
  const regenerate=selected&&isRegenerateRun(r.request_type,r.schema_version);
  insist(params.motion===t.motion&&params.direction===t.direction&&params.output==='native-png'&&
    params.npcId===(regenerate?intent.npcId:intent.character?.npcId),'ANIMATION_DB_TARGET');
  if(selected){
   insist(isSelectedMediaRun(r.request_type,r.schema_version),'ANIMATION_DB_SELECTED_TARGET');
   const {target,parent}=selectedMotionBranch(r,t.key);
   insist(matchesMotionTask(r,target)&&(parent?.key??null)===t.parent,'ANIMATION_DB_SELECTED_TARGET');
   if(parent){
    const source=db.prepare('SELECT * FROM workflow_tasks WHERE task_id=? AND official_request_id=?')
     .get(target.dependsOn,t.officialRequestId);
    insist(matchesMotionTask(source,parent)&&source.status==='completed','ANIMATION_DB_SELECTED_TARGET');
   }
  }else insist(motions.includes(t.motion)&&t.key==='animation_'+t.motion+'_'+t.direction&&t.parent==='portrait_'+t.direction,
   'ANIMATION_DB_TARGET');
  const claimStamp=Math.max(Date.now(),r.updated_at+1);
  const claimed=db.prepare(`UPDATE workflow_tasks AS t
   SET status='running',updated_at=?,${workflowTaskRetrySetSql}
   WHERE t.task_id=? AND t.official_request_id=? AND t.worker_type='animation' AND t.task_type=? AND t.task_key=?
    AND t.status=? AND t.updated_at=? AND t.result_ref IS NULL AND ${workflowTaskEligibleSql('t')}`)
   .run(claimStamp,t.taskId,t.officialRequestId,type,t.key,r.status,r.updated_at);
  if(claimed.changes!==1)return null;
  return {dto:{taskId:t.taskId,officialRequestId:t.officialRequestId,projectId:r.project_id,npcId:params.npcId,
   payloadSha256:r.payload_sha256,claimStamp},intent,...(selected?{selection:params,regenerate}:{})};
 });
}
function markFailed(db,{taskId,officialRequestId,claimStamp}){
 return markWorkflowTaskFailed(db,{taskId,officialRequestId,claimStamp,workerType:'animation'});
}
function runNative(dir,signal){
 return new Promise((resolve,reject)=>{
  const p=spawn(py,['-I','-B',helper,provider,binding,dir],{stdio:['ignore','pipe','pipe']});
  let out='',err='';
  p.stdout.on('data',b=>{if(out.length<256000)out+=b;});p.stderr.on('data',b=>{if(err.length<2000)err+=b;});
  const abort=()=>p.kill('SIGTERM');
  signal.addEventListener('abort',abort,{once:true});
  if(signal.aborted)abort();
  p.once('error',reject);
  p.once('close',code=>{signal.removeEventListener('abort',abort);
   try{
    if(code!==0){
     const native=err.trim(), joined=/^JOINED [A-Z0-9_]+$/.test(native);
     const e=Error(joined?native.slice(7):/^[A-Z0-9_]+$/.test(native)?native:'ANIMATION_NATIVE_UNCONFIRMED');
     e.rendererJoined=joined;
     throw e;
    }
    resolve(JSON.parse(out));
   }catch(e){reject(e);}
  });
 });
}
async function readStopDiagnostic(dir){
 let file;
 try{
  file=await open(join(dir,'stop-diagnostic.json'),fsConstants.O_RDONLY|fsConstants.O_NOFOLLOW|fsConstants.O_NONBLOCK);
  const stat=await file.stat();if(!stat.isFile()||stat.size>512)return null;
  const buffer=Buffer.alloc(513);const {bytesRead}=await file.read(buffer,0,513,0);if(bytesRead>512)return null;
  const value=JSON.parse(buffer.subarray(0,bytesRead).toString('utf8'));
  const steps=['leader_poll','leader_sigterm','leader_wait_term','leader_sigkill','leader_wait_kill','leader_wait_exited','group_probe','group_sigkill','group_exit_deadline','unknown'];
  const classes=['PermissionError','ProcessLookupError','TimeoutExpired','ChildProcessError','OSError','ValueError','ExecutionStopUnconfirmed','KeyboardInterrupt','SystemExit','OtherError'];
  if(!value||typeof value!=='object'||Array.isArray(value))return null;
  const keys=Object.keys(value).sort().join(',');
  if(!['causeClass,errno,schemaVersion,step','causeClass,cleanupLayer,errno,renderCode,renderOutcome,schemaVersion,step'].includes(keys)||
   value.schemaVersion!=='animation.stop-diagnostic.v1'||!steps.includes(value.step)||!classes.includes(value.causeClass)||
   !(value.errno===null||(Number.isInteger(value.errno)&&value.errno>=0&&value.errno<=65535)))return null;
  if(keys.includes('renderOutcome')){
   const codes=['ANIMATION_CANCELLED','ANIMATION_COMFY_EXITED','ANIMATION_COMFY_PORT_BUSY','ANIMATION_COMFY_QUEUE_BUSY',
    'ANIMATION_COMFY_START_TIMEOUT','ANIMATION_COMPLETION_WITHOUT_BINDING','ANIMATION_CONDITIONING_OUTPUT_AMBIGUOUS','ANIMATION_EXECUTION_NOT_IDLE',
    'ANIMATION_FRAME_COUNT_MISMATCH','ANIMATION_FRAMING_POLICY_INVALID','ANIMATION_MEMORY_NOT_READY','ANIMATION_NATIVE_JOB_CHANGED',
    'ANIMATION_NATIVE_OUTCOME_UNKNOWN_NO_REEXECUTION','ANIMATION_NATIVE_PHASE_FAILED','ANIMATION_PIXEL_NATIVE_REPROCESS_REQUIRED',
    'ANIMATION_PNG_FORMAT','ANIMATION_PNG_TOO_LARGE','ANIMATION_REFERENCE_LAYOUT_INVALID','ANIMATION_RESOURCE_LIMIT','ANIMATION_NATIVE_FAILED'];
   if(!['inner','outer'].includes(value.cleanupLayer))return null;
   if(value.renderOutcome==='returned'){if(value.renderCode!==null||value.cleanupLayer!=='outer')return null;}
   else if(value.renderOutcome!=='failed'||typeof value.renderCode!=='string'||
    !(codes.includes(value.renderCode)||/^ANIMATION_COMFY_(QUEUE|PROMPT|FREE|HISTORY|OTHER)_HTTP_[1-5][0-9]{2}$/.test(value.renderCode)))return null;
  }
  return value;
 }catch{return null;}finally{await file?.close().catch(()=>{});}
}
function validateSelectedBasis(selection,settings,reference,{regenerate=false}={}){
 const sha64=s=>typeof s==='string'&&/^[a-f0-9]{64}$/.test(s)&&s.length===64;
 const exact=ok=>settings&&typeof settings==='object'&&!Array.isArray(settings)&&Object.keys(settings).sort().join(',')===ok;
 const fields='assetSetSha256,direction,'+(regenerate?'generationId,':'')+'motion,portraitAssetId,posePreset,referencePresetHash,referenceSha256'+(regenerate?',resultBasisSha256':'');
 insist(exact(fields)&&(!regenerate||typeof settings.generationId==='string'&&settings.generationId.length===48&&
  /^[a-f0-9]{48}$/.test(settings.generationId)&&sha64(settings.resultBasisSha256))&&
  settings.posePreset===selection.posePreset&&settings.assetSetSha256===selection.assetSetSha256&&
  sha64(settings.referenceSha256)&&sha64(settings.referencePresetHash),'ANIMATION_PDS_SELECTED_BASIS');
 if(reference!==undefined)insist(reference.posePreset===selection.posePreset&&reference.sha256===settings.referenceSha256&&
  reference.presetHash===settings.referencePresetHash,'ANIMATION_PDS_SELECTED_REFERENCE');
}
async function execute(db,t,signal,warm=null,{transport=local,native=runNative}={}){
 const current=claim(db,t);if(!current)return;
 const {dto,intent,selection,regenerate}=current;
 const logPhase=phase=>console.log(JSON.stringify({phase,taskId:t.taskId}));
 logPhase('claimed');
 let dir,stage='read';
 try{
  logPhase(stage);
  dir=warm?await warm.jobDir():await mkdtemp(join(tmpdir(),'workflow-wan-'));
  const settings=await transport(path+'/read',{body:{claim:dto},signal});
  insist(settings.direction===t.direction&&settings.motion===t.motion&&/^[a-f0-9]{64}\.png$/.test(settings.portraitAssetId),'ANIMATION_PDS_TARGET');
  if(selection)validateSelectedBasis(selection,settings,undefined,{regenerate});
  const [portrait,reference]=await Promise.all([
   transport(path+'/asset',{body:{claim:dto,assetId:settings.portraitAssetId},binary:true,signal}),
   transport('/v1/motion-presets/'+encodeURIComponent(t.motion)+'/'+t.direction,{signal,...(selection?{maxJsonBytes:88*1024*1024}:{})})
  ]);
  if(selection)validateSelectedBasis(selection,settings,reference,{regenerate});
  const source=Buffer.from(reference.base64,'base64');
  insist(sha(portrait)+'.png'===settings.portraitAssetId&&source.length===reference.bytes&&sha(source)===reference.sha256&&
   sha(source)+'.mp4'===reference.assetId&&reference.direction===t.direction&&reference.presetId===t.motion,'ANIMATION_SOURCE_HASH');
  const job={requestId:t.taskId,frameCount:reference.frameCount,fps:reference.fps,referenceType:'portrait',
   appearancePrompt:'Preserve the exact reference character design and costume. '+String(intent.intent??'').slice(0,6000),
   motionPrompt:reference.policy.motionInstruction,
   negativePrompt:'Identity changes, costume changes, duplicate limbs, cropping, camera movement, text, other characters.',
   framingPolicy:reference.policy.framingPolicy};
  await Promise.all([writeFile(join(dir,'job.json'),JSON.stringify(job)),writeFile(join(dir,'motion.mp4'),source),writeFile(join(dir,'portrait.png'),portrait)]);
  stage='render';logPhase(stage);const out=warm?await warm.render(dir,signal):await native(dir,signal);
  stage='validate';
  insist(out.frames.length===job.frameCount&&out.fps===job.fps&&out.width===768&&out.height===432,'ANIMATION_FRAME_MANIFEST');
  const frames=await Promise.all(out.frames.map(async (name,i)=>{
   insist(name===String(i).padStart(5,'0')+'.png','ANIMATION_FRAME_ORDER');
   const b=await readFile(join(dir,'wan','frames',name));
   insist(sha(b)===out.frameHashes[i],'ANIMATION_FRAME_HASH');return b.toString('base64');
  }));
  signal.throwIfAborted(); // An already-cancelled claim has not sent a publish request.
  stage='publish';logPhase(stage);
  const ack=await transport(path,{body:{claim:dto,artifact:{fps:out.fps,width:out.width,height:out.height,frames,
   ...(regenerate?{source:{generationId:settings.generationId,portraitAssetId:settings.portraitAssetId,resultBasisSha256:settings.resultBasisSha256}}:{})}},signal,timeout:180000});
  insist(Object.keys(ack).length===1&&ack.ok===true,'ANIMATION_PUBLICATION_UNCONFIRMED');
  stage='commit';
  const done=db.prepare("UPDATE workflow_tasks SET status='completed',updated_at=? WHERE task_id=? AND official_request_id=? AND status='running' AND updated_at=?")
    .run(Math.max(Date.now(),dto.claimStamp+1),dto.taskId,dto.officialRequestId,dto.claimStamp);
  insist(done.changes===1,'ANIMATION_DB_COMPLETION');
  console.log(JSON.stringify({state:'completed',taskId:t.taskId,frames:frames.length}));
 }catch(e){
  const message=e?.message;
  const code=signal.aborted&&['AbortError','TimeoutError'].includes(e?.name)?'ANIMATION_CANCELLED':
   typeof message==='string'&&/^[A-Z0-9_]{1,130}$/.test(message)?message:'ANIMATION_UNCONFIRMED';
  // Task disposition and proof of renderer shutdown are independent.
  const rendererStop=stage==='render'?(e?.rendererJoined===true?'confirmed':'unknown'):null;
  let failed=false, failureUpdate;
  if(!['publish','commit'].includes(stage)){
   try{
    failed=markFailed(db,dto);
    if(!failed)failureUpdate='claim_mismatch';
   }catch{failureUpdate='update_unconfirmed';}
  }
  const stopDiagnostic=stage==='render'&&dir?await readStopDiagnostic(dir):null;
  console.error(JSON.stringify({state:failed?'failed':'requires_reconciliation',taskId:t.taskId,stage,code,
   ...(rendererStop?{rendererStop}:{}),...(failureUpdate?{failureUpdate}:{}),...(stopDiagnostic?{stopDiagnostic}:{})}));
 }finally{
  if(dir&&(!warm||warm.safeToClean())){
   try{await rm(dir,{force:true,recursive:true});}
   catch{console.error(JSON.stringify({state:'cleanup_failed',taskId:t.taskId,stage,code:'ANIMATION_SCRATCH_CLEANUP_FAILED'}));}
  }
  warm?.workFinished(); // TTL begins after publication attempt and scratch cleanup.
 }
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
 const [mode,flag,dbPath]=process.argv.slice(2);
 insist(mode==='--run'&&flag==='--db'&&process.argv.length===5&&[provider,py,binding].every(p=>p?.startsWith('/')),'ANIMATION_WORKER_CONFIGURATION');
 const stop=new AbortController(),signal=stop.signal;
 for(const sig of ['SIGTERM','SIGINT'])process.once(sig,()=>stop.abort());
 assertDatabasePath(dbPath);const db=new DatabaseSync(dbPath);protectSchema(db);db.exec('PRAGMA busy_timeout=5000');
 const ping=()=>void local('/api/service-heartbeat',{body:{serverName:'animation-worker'},port:8090,signal}).catch(()=>{});
 const heart=setInterval(ping,60000);ping();
 const preset=presetFramePorts(local);
 const i2v=i2vPorts(local);
 const prop=propAnimationPorts(local);
 const cinematic=cinematicPorts(local);
 const sequence=cinematicSequencePorts(local);
 // Existing resident owns both model families. Fixed, private host binding
 // enables I2V only with the exact A14B high/low Q4_K_M pair and Wan 2.1 VAE.
 const override=process.env.WORKFLOW_I2V_A14B_BINDING;
 const a14bBinding=override??defaultPropBinding;
 let i2vModel,preparePropInput,backgroundUpscaler;
 try{
  insist(a14bBinding.startsWith('/'),'I2V_BINDING_INVALID');
  if(!override)await confirmBindingPath(a14bBinding);
  const {readWanI2VBinding,createWanI2V}=await import('./i2v-wan.mjs');
  const binding=await readWanI2VBinding(a14bBinding);
  i2vModel=createWanI2V({binding});
  preparePropInput=createPropI2VInputFraming(binding.python);
  const modelsRoot=process.env.WORKFLOW_SEEDVR2_MODELS_ROOT??join(homedir(),'Workspaces','Workers','runtime','seedvr2-fp16-staging','models');
  backgroundUpscaler=createBackgroundSeedVR2({binding:{modelsRoot,comfyRoot:binding.comfyRoot,python:binding.python}});
 }catch(e){
  if(override||e?.code!=='ENOENT')console.error(JSON.stringify({state:'not_ready',code:'I2V_BINDING_INVALID'}));
 }
 const warm=createWarmAnimation({launch:root=>spawnAnimationChannel({py,helper,provider,binding,root}),
  onIdleUnload:()=>console.log(JSON.stringify({state:'idle_model_unloaded'}))});
 const modelSlot=createAnimationModelSwitch(warm,i2vModel,backgroundUpscaler);
 let inFlightModelProbe;
 const probeModel=()=>{
  if(i2vModel&&!inFlightModelProbe)inFlightModelProbe=i2vModel.probe().finally(()=>{inFlightModelProbe=undefined;});
  return inFlightModelProbe;
 };
 const assertKnown=()=>modelSlot.assertKnown();
 const propReady=propAnimationReadiness({path:process.env.WORKFLOW_PROP_ANIMATION_READY_FILE,
  probe:()=>preparePropInput?probeModel():undefined,assertKnown});
 const cinematicReady=cinematicAnimationReadiness({path:process.env.WORKFLOW_CINEMATIC_ANIMATION_READY_FILE,assertKnown,
  probe:async()=>{const [proof]=await Promise.all([probeModel(),probeCutsceneEncoder()]);return proof;}});
 const sequenceReady=cinematicSequenceReadiness({path:process.env.WORKFLOW_CINEMATIC_SEQUENCE_ANIMATION_READY_FILE,probe:probeModel,assertKnown});
 const allReadiness=[propReady,cinematicReady,sequenceReady];
 const pulseReadiness=()=>{for(const lifecycle of allReadiness)void lifecycle.refresh();};
 const readinessHeart=setInterval(pulseReadiness,30000);readinessHeart.unref();pulseReadiness();
 const invalidateReady=()=>Promise.all(allReadiness.map(lifecycle=>lifecycle.invalidate()));
 try{while(!signal.aborted){
  let idle=false;
  try{
   const byLane=[
    ['npc',animationCandidates(dbPath)],
    ['preset',[choosePresetFrames(dbPath)]],
    ['prop',[propReady.ready()?choosePropAnimation(dbPath):null]],
    ['i2v',[i2vModel&&override?chooseI2V(dbPath):null]],
    ['cinematic',[cinematicReady.ready()?chooseCinematic(dbPath):null]],
    ['sequence',[sequenceReady.ready()?chooseCinematicSequence(dbPath):null]],
   ];
   const choice=oldestRunnable(dbPath,byLane.flatMap(([lane,tasks])=>tasks.filter(Boolean).map(task=>({...task,lane}))))[0];
   const {lane,...job}=choice??{};
   const t=choice?job:undefined;
   if(!t){
    idle=true; // At the timeout boundary a runnable job takes precedence.
    if(!await warm.idle())throw Error('ANIMATION_PROCESS_STOP_UNCONFIRMED');
   }else if(lane==='prop'){
    modelSlot.assertKnown();
    insist(await prop.ready(signal),'PROP_I2V_PDS_UNAVAILABLE');
    signal.throwIfAborted();
    const result=await executePropAnimation(db,t,signal,{ports:prop,prepareInput:preparePropInput,renderer:{render:job=>modelSlot.runI2V(job)}});
    if(result.state==='completed')console.log(JSON.stringify(result));
    else if(result.state!=='stale')console.error(JSON.stringify(result));
    if(result.rendererStop==='unknown')await invalidateReady();
    idle=result.state==='stale'||result.rendererStop==='unknown';
   }else if(lane==='i2v'){
    modelSlot.assertKnown();
    insist(await i2v.ready(signal),'I2V_PDS_UNAVAILABLE');
    const ready=await i2vModel.probe();
    insist(ready?.ready===true&&ready.model===WAN_I2V_MODEL_ID&&ready.quantization===WAN_I2V_QUANTIZATION,'I2V_MODEL_UNAVAILABLE');
    signal.throwIfAborted();
    const result=await executeI2V(db,t,signal,{ports:i2v,renderer:{render:job=>modelSlot.runI2V(job)}});
    if(result.state==='completed')console.log(JSON.stringify(result));
    else if(result.state!=='stale')console.error(JSON.stringify(result));
    idle=result.state==='stale'||result.rendererStop==='unknown';
   }else if(lane==='cinematic'){
    modelSlot.assertKnown();
    insist(await cinematic.ready(signal),'CINEMATIC_PDS_UNAVAILABLE');
    signal.throwIfAborted();
    const result=await executeCinematic(db,t,signal,{ports:cinematic,runI2V:job=>modelSlot.runI2V(job)});
    if(result.state==='completed')console.log(JSON.stringify(result));
    else if(result.state!=='stale')console.error(JSON.stringify(result));
    if(result.rendererStop==='unknown')await invalidateReady();
    idle=result.state==='stale'||result.rendererStop==='unknown';
   }else if(lane==='sequence'){
    modelSlot.assertKnown();
    insist(await sequence.ready(signal),'CINEMATIC_SEQUENCE_PDS_UNAVAILABLE');
    signal.throwIfAborted();
    const result=await executeCinematicSequence(db,t,signal,{ports:sequence,runI2V:job=>modelSlot.runI2V(job),
     ...(backgroundUpscaler?{runUpscale:job=>modelSlot.runUpscale(job),probeUpscale:()=>backgroundUpscaler.probe()}:{})});
    if(result.state==='completed')console.log(JSON.stringify(result));
    else if(result.state!=='stale')console.error(JSON.stringify(result));
    if(result.rendererStop==='unknown')await invalidateReady();
    idle=result.state==='stale'||result.rendererStop==='unknown';
   }else if(lane==='npc'){
    modelSlot.enterWan();
    const r=await local(path,{signal});insist(r.enabled===true&&r.schemaVersion==='workflow.animation-write.v1','ANIMATION_PDS_UNAVAILABLE');
    if(warm.cold()){
     const preflight=await runNative('--probe',signal);insist(preflight.ready===true,'ANIMATION_MODEL_UNAVAILABLE');
    }
    signal.throwIfAborted();
    await execute(db,t,signal,warm);
   }else{
    modelSlot.enterWan();
    insist(await preset.ready(signal),'PRESET_2D_PDS_UNAVAILABLE');
    if(warm.cold()){
     const preflight=await runNative('--probe',signal);insist(preflight.ready===true,'ANIMATION_MODEL_UNAVAILABLE');
    }
    signal.throwIfAborted();
    const result=await executePresetFrames(db,t,signal,{pds:preset,warm,native:runNative});
    if(result.state==='failed'||result.state==='requires_reconciliation')console.error(JSON.stringify(result));
    else if(result.state==='completed')console.log(JSON.stringify(result));
    else idle=true;
   }
  }catch(e){
   if(!signal.aborted)console.error(JSON.stringify({state:'not_ready',code:e.message}));
   // No runnable work could be taken this tick (e.g. PDS unavailable).
   idle=true;
   try{modelSlot.assertKnown();}catch{await invalidateReady();}
   try{await warm.idle();}catch{/* preserve the prior readiness error */}
  }
  if(!signal.aborted)await delay(idle?warm.idleDelay(5000):0,undefined,{signal}).catch(e=>{if(e.name!=='AbortError')throw e;});
 }}finally{
  clearInterval(readinessHeart);await Promise.all(allReadiness.map(lifecycle=>lifecycle.stop()));
  try{
   if(!await warm.shutdown())console.error(JSON.stringify({state:'requires_reconciliation',stage:'stop',code:'ANIMATION_PROCESS_STOP_UNCONFIRMED'}));
  }catch{console.error(JSON.stringify({state:'cleanup_failed',stage:'stop',code:'ANIMATION_SCRATCH_CLEANUP_FAILED'}));}
  clearInterval(heart);
  try{db.close();}catch{console.error(JSON.stringify({state:'cleanup_failed',stage:'close',code:'ANIMATION_DB_CLOSE_FAILED'}));}
 }
}
export {choose,claim,execute,markFailed,readStopDiagnostic,validateSelectedBasis};
