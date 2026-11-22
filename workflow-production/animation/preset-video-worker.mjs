/** Separate claim-bound motion-preset lane. Legacy NPC animation never uses this path. */
import {DatabaseSync} from 'node:sqlite';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {assertDatabasePath,protectSchema,markWorkflowTaskFailed,workflowTaskEligibleSql,workflowTaskRetrySetSql} from './workflow-ready.mjs';
import {directions,sha256,digestPattern,need,validTiming,pngShape,mp4Shape,maxBytes,maxReferenceBytes,validateFrames,encodePresetVideo} from './preset-video-artifact.mjs';

export const presetPath='/v1/motion-presets/workflow-convert-2d';
export const presetCapability='workflow.motion-preset-convert-2d.v1';
export const requestType='motion-preset.convert-2d';
export const requestSchema='ngs.motion-preset-convert-2d.v1';
export const imageType='motion-preset.convert-2d.image',imageKey='motion_2d_image';
export const clipType='motion-preset.convert-2d.clip',finalType='motion-preset.convert-2d.finalize',finalKey='motion_2d_finalize';
export const clipKey=d=>'motion_2d_'+d;
const clipKeys=directions.map(clipKey),worker='animation',project='binari';
const official=/^request\.official\.[a-f0-9]{64}$/;
const ident=/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const imageResult=/^motion\.2d\.image\.[a-f0-9]{64}$/;
const finalResult=/^motion\.2d\.published\.[a-f0-9]{64}$/;
export class DefinitePresetWriteRejection extends Error{}
const isDict=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const exact=(v,ks)=>isDict(v)&&Object.keys(v).length===ks.length&&ks.every(k=>Object.hasOwn(v,k));
const eqArray=(a,b)=>Array.isArray(a)&&a.length===b.length&&a.every((v,i)=>v===b[i]);
const taskId=(id,key)=>'workflow.task.'+sha256(JSON.stringify([id,key]));
const short=(value,max)=>typeof value==='string'&&value.isWellFormed()&&value.trim()!==''&&
 [...value].length<=max&&!/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(value);
const exactProject=(p)=>exact(p,['schemaVersion','projectId','sourcePresetId','variantPresetId','label'])&&
 p.schemaVersion===requestSchema&&p.projectId===project&&typeof p.sourcePresetId==='string'&&ident.test(p.sourcePresetId)&&
 typeof p.variantPresetId==='string'&&ident.test(p.variantPresetId)&&p.sourcePresetId!==p.variantPresetId&&short(p.label,160);

function transaction(db,fn){
 db.exec('BEGIN IMMEDIATE');
 try{const value=fn();db.exec('COMMIT');return value;}catch(e){db.exec('ROLLBACK');throw e;}
}
function resultDigest(value, prefix){
 return typeof value==='string'&&value.startsWith(prefix)&&digestPattern.test(value.slice(prefix.length))?
  value.slice(prefix.length):null;
}
function exactInput(p,t){
 const common=t.sourcePresetId===p.sourcePresetId&&t.variantPresetId===p.variantPresetId;
 return common;
}
const readyClipsSql=clipKeys.map(()=>'?').join(',');
export function choosePresetVideo(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  return db.prepare(`SELECT t.task_id AS taskId,t.official_request_id AS officialRequestId,t.task_key AS key,t.task_type AS type
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
   WHERE r.state='planned' AND r.reason IS NULL AND r.request_type=? AND r.schema_version=? AND r.project_id=?
   AND ${workflowTaskEligibleSql('t')} AND t.result_ref IS NULL AND t.worker_type=?
   AND ((t.task_type=? AND t.task_key=? AND t.depends_on_task_id IS NULL AND
    (SELECT COUNT(*) FROM workflow_tasks c WHERE c.official_request_id=t.official_request_id
      AND c.task_type=? AND c.worker_type=? AND c.status='completed' AND c.result_ref IS NOT NULL
      AND c.task_key IN (${readyClipsSql}))=8)
    OR (t.task_type=? AND t.task_key IN (${readyClipsSql}) AND EXISTS(
      SELECT 1 FROM workflow_tasks i WHERE i.task_id=t.depends_on_task_id AND i.official_request_id=t.official_request_id
      AND i.task_key=? AND i.task_type=? AND i.worker_type='image' AND i.status='completed' AND i.result_ref IS NOT NULL)))
    ORDER BY CASE WHEN t.task_key=? THEN 0 ELSE 1 END,r.registered_at,t.official_request_id,t.task_key LIMIT 1`)
   .get(requestType,requestSchema,project,worker,finalType,finalKey,clipType,worker,...clipKeys,
        clipType,...clipKeys,imageKey,imageType,finalKey)??undefined;
 }finally{db.close();}
}

function imageProof(db, id){
 const imageId=taskId(id,imageKey);
 const row=db.prepare(`SELECT status,task_type,task_key,worker_type,result_ref FROM workflow_tasks
  WHERE task_id=? AND official_request_id=?`).get(imageId,id);
 if(!row||row.status!=='completed'||row.task_type!==imageType||row.task_key!==imageKey||
  row.worker_type!=='image'||typeof row.result_ref!=='string'||!imageResult.test(row.result_ref))return null;
 return {imageId,imageSha256:row.result_ref.slice(-64)};
}

function finalizedClips(db,id,input,p){
 const ids=directions.map(d=>taskId(id,clipKey(d))), image=imageProof(db,id);
 if(!image||!eqArray(input.dependsOnTaskIds,ids))return null;
 const rows=db.prepare(`SELECT task_id,task_key,task_type,worker_type,task_input,depends_on_task_id,result_ref,status
  FROM workflow_tasks WHERE official_request_id=? AND (task_type=? OR task_key IN (${readyClipsSql}))`)
  .all(id,clipType,...clipKeys);
 if(rows.length!==directions.length)return null;
 const byKey=new Map(rows.map(v=>[v.task_key,v]));
 const proofs=[];
 for(const [i,d] of directions.entries()){
  const r=byKey.get(clipKey(d));
  if(!r||r.task_id!==ids[i]||r.task_type!==clipType||r.worker_type!==worker||r.status!=='completed'||
   r.depends_on_task_id!==image.imageId||!resultDigest(r.result_ref,'motion.2d.clip.'+d+'.'))return null;
  const args=JSON.parse(r.task_input);
  if(!exact(args,['sourcePresetId','variantPresetId','direction'])||!exactInput(p,args)||args.direction!==d)return null;
  proofs.push(r.result_ref);
 }
 return proofs;
}

export function claimPresetVideo(db,t){
 need(official.test(t?.officialRequestId)&&typeof t.key==='string'&&t.taskId===taskId(t.officialRequestId,t.key),
      'TASK_BINDING');
 const isFinal=t.type===finalType&&t.key===finalKey;
 const direction=t.type===clipType?directions.find(d=>clipKey(d)===t.key):undefined;
 need(isFinal||direction!==undefined,'TASK_KIND');
 return transaction(db,()=>{
  const id=t.officialRequestId;
  const r=db.prepare(`SELECT t.updated_at,t.status,t.task_input,t.depends_on_task_id,r.payload,r.payload_sha256,r.project_id
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
   WHERE r.state='planned' AND r.reason IS NULL AND r.request_type=? AND r.schema_version=? AND r.project_id=?
   AND t.worker_type=? AND t.task_type=? AND t.task_key=? AND t.task_id=? AND t.official_request_id=?
   AND ${workflowTaskEligibleSql('t')} AND t.result_ref IS NULL`).get(requestType,requestSchema,project,worker,t.type,t.key,t.taskId,id);
  if(!r)return null;
  const input=JSON.parse(r.task_input),p=JSON.parse(r.payload);
  need(exactProject(p)&&r.payload_sha256===sha256(r.payload),'PAYLOAD_BINDING');
  need(exact(input,['sourcePresetId','variantPresetId',isFinal?'dependsOnTaskIds':'direction'])&&exactInput(p,input),
       'TASK_INPUT');
  let proofs=null,imageSha256=null;
  if(isFinal){
   need(r.depends_on_task_id===null,'FINALIZE_TASK_BINDING');
   proofs=finalizedClips(db,id,input,p);
   if(!proofs)return null;
  }else{
   need(input.direction===direction&&r.depends_on_task_id===taskId(id,imageKey),'CLIP_TASK_BINDING');
   imageSha256=imageProof(db,id)?.imageSha256;
   if(!imageSha256)return null;
  }
  const claimStamp=Math.max(Date.now(),r.updated_at+1);
  need(Number.isSafeInteger(claimStamp)&&claimStamp<Number.MAX_SAFE_INTEGER,'CLAIM_STAMP');
  const applied=db.prepare(`UPDATE workflow_tasks AS t SET status='running',updated_at=?,${workflowTaskRetrySetSql}
   WHERE t.task_id=? AND t.official_request_id=? AND t.worker_type=? AND t.task_type=? AND t.task_key=?
   AND t.status=? AND t.updated_at=? AND t.result_ref IS NULL AND ${workflowTaskEligibleSql('t')}`)
   .run(claimStamp,t.taskId,id,worker,t.type,t.key,r.status,r.updated_at);
  if(applied.changes!==1)return null;
  return {claim:{officialRequestId:id,taskId:t.taskId,projectId:project,payloadSha256:r.payload_sha256,claimStamp},
   sourcePresetId:p.sourcePresetId,variantPresetId:p.variantPresetId,direction,isFinal,imageSha256,proofs};
 });
}

function decodeExact(data,limit,shape){
 const size=data.bytes, b64=data.base64, hash=data.sha256;
 need(Number.isSafeInteger(size)&&size>=24&&size<=limit&&typeof hash==='string'&&digestPattern.test(hash)&&
   typeof b64==='string'&&b64.length===4*Math.ceil(size/3),'ASSET_ENVELOPE');
 const bytes=Buffer.from(b64,'base64');
 need(bytes.length===size&&bytes.toString('base64')===b64&&sha256(bytes)===hash&&shape(bytes),'ASSET_BYTES');
 return bytes;
}
const referenceKeys=['schemaVersion','presetId','posePreset','label','policy','direction','assetId','sha256','bytes','frameCount',
 'fps','cycleCount','sourceVersion','presetHash','base64'];
const referenceAdditional=['width','height','durationSeconds'];
const finiteBetween=(n,lo,hi)=>typeof n==='number'&&Number.isFinite(n)&&n>=lo&&n<=hi;
const views={n:'north (rear)',ne:'north-east (rear three-quarter right)',e:'east (right side)',se:'south-east (front three-quarter right)',
 s:'south (front)',sw:'south-west (front three-quarter left)',w:'west (left side)',nw:'north-west (rear three-quarter left)'};

export function materializeRead(state,v){
 if(state.isFinal){
  need(exact(v,['sourcePresetHash'])&&typeof v.sourcePresetHash==='string'&&digestPattern.test(v.sourcePresetHash),'FINALIZE_SOURCE');
  return null;
 }
 need(exact(v,['direction','sourcePresetHash','reference','image'])&&v.direction===state.direction&&
   typeof v.sourcePresetHash==='string'&&digestPattern.test(v.sourcePresetHash),'CLIP_SOURCE');
 const r=v.reference,i=v.image;
 need(isDict(r)&&referenceKeys.every(k=>Object.hasOwn(r,k))&&Object.keys(r).every(k=>referenceKeys.includes(k)||referenceAdditional.includes(k))&&
  r.schemaVersion==='ngs.motion-preset.v1'&&r.presetId===state.sourcePresetId&&r.direction===state.direction&&
  r.presetHash===v.sourcePresetHash&&typeof r.sourceVersion==='string'&&digestPattern.test(r.sourceVersion)&&
  r.assetId===r.sha256+'.mp4'&&r.cycleCount===1&&validTiming(r),'CLIP_REFERENCE_SEMANTICS');
 need(exact(i,['sha256','bytes','base64'])&&i.sha256===state.imageSha256,'CLIP_SHARED_IMAGE_BINDING');
 const policy=r.policy, fr=policy?.framingPolicy;
 need(exact(policy,['framingPolicy','equipmentPolicy','motionInstruction'])&&short(policy.motionInstruction,4000)&&
  exact(fr,['schemaVersion','maxHeightRatio','maxWidthRatio','offsetY','allowUpscale'])&&
  fr.schemaVersion==='ngs.animation-motion-safe-frame.v1'&&finiteBetween(fr.maxHeightRatio,Number.MIN_VALUE,1)&&
  finiteBetween(fr.maxWidthRatio,Number.MIN_VALUE,1)&&finiteBetween(fr.offsetY,-1,1)&&fr.allowUpscale===false,
  'CLIP_REFERENCE_POLICY');
 const source=decodeExact(r,maxReferenceBytes,mp4Shape),portrait=decodeExact(i,8*1024*1024,pngShape);
 const job={requestId:state.claim.taskId,frameCount:r.frameCount,fps:r.fps,referenceType:'portrait',
  appearancePrompt:'Use the exact same simple flat two-dimensional guide character as the reference: preserve body proportions and the solid-color design, plain background, full body visible. No costume or identifying features.',
  motionPrompt:'Match the supplied single-cycle directional motion exactly, especially body facing, limb movement, pose timing, and fixed '+views[state.direction]+' view. '+policy.motionInstruction,
  negativePrompt:'Character changes, realistic three-dimensional shading, scene changes, text, logos, duplicate limbs, cropping, camera motion, extra characters.',
  framingPolicy:fr};
 return {source,portrait,job};
}

export function presetVideoPorts(local){
 return {
  async ready(signal){
   const r=await local(presetPath,{signal,maxJsonBytes:4096});
   return exact(r,['enabled','schemaVersion'])&&r.enabled===true&&r.schemaVersion===presetCapability;
  },
  async read(claim,signal){
   return local(presetPath+'/read',{body:{claim},signal,timeout:60_000,maxJsonBytes:104*1024*1024});
  },
  async write(claim,artifact){
   // Once the body is sent, an external cancellation cannot establish
   // whether publication committed. Use only a bounded internal timeout.
   return local(presetPath,{body:{claim,artifact},timeout:180_000,maxJsonBytes:4096,onRejected:value=>{
    if(exact(value,['error'])&&value.error==='MOTION_2D_REJECTED')
     throw new DefinitePresetWriteRejection(value.error);
   }});
  }
 };
}

function complete(db,t,s,ref){
 const {claim}=s;
 return transaction(db,()=>{
  const r=db.prepare(`SELECT t.status,t.result_ref,t.updated_at,r.payload_sha256
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
   WHERE t.task_id=? AND t.official_request_id=? AND t.task_type=? AND t.task_key=? AND t.worker_type=?
   AND r.state='planned' AND r.reason IS NULL AND r.request_type=? AND r.schema_version=? AND r.project_id=?`)
   .get(claim.taskId,claim.officialRequestId,t.type,t.key,worker,requestType,requestSchema,project);
  need(r&&r.status==='running'&&r.result_ref===null&&r.updated_at===claim.claimStamp&&r.payload_sha256===claim.payloadSha256,
   'COMPLETE_CLAIM');
  const done=db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
   WHERE task_id=? AND official_request_id=? AND status='running' AND updated_at=? AND result_ref IS NULL`)
   .run(ref,Math.max(Date.now(),claim.claimStamp+1),claim.taskId,claim.officialRequestId,claim.claimStamp);
  need(done.changes===1,'COMPLETE_UPDATE');
 });
}

export async function executePresetVideo(db,t,signal,{pds,warm=null,native,encode=encodePresetVideo,validate=validateFrames}){
 const state=claimPresetVideo(db,t);
 if(!state)return {state:'not_claimable',taskId:t.taskId};
 let phase='read',dir,rendererAttempt=false;
 try{
  signal.throwIfAborted();
  const input=materializeRead(state,await pds.read(state.claim,signal));
  let artifact={};
  if(!state.isFinal){
   phase='scratch';
   dir=warm?await warm.jobDir():await mkdtemp(join(tmpdir(),'workflow-preset-2d-'));
   await Promise.all([writeFile(join(dir,'job.json'),JSON.stringify(input.job),{flag:'wx',mode:0o600}),
    writeFile(join(dir,'motion.mp4'),input.source,{flag:'wx',mode:0o600}),
    writeFile(join(dir,'portrait.png'),input.portrait,{flag:'wx',mode:0o600})]);
   phase='render';
   rendererAttempt=true;
   const out=warm?await warm.render(dir,signal):await native(dir,signal);
   phase='validate';
   await validate(dir,out,input.job);
   phase='encode';
   const output=await encode(dir,input.job,signal);
   need(isDict(output)&&output.mimeType==='video/mp4'&&typeof output.sha256==='string'&&digestPattern.test(output.sha256)&&
    Number.isSafeInteger(output.bytes)&&output.bytes>=24&&output.bytes<=maxBytes,'ENCODE_RESULT');
   decodeExact(output,maxBytes,mp4Shape);
   artifact={base64:output.base64,sha256:output.sha256};
  }
  signal.throwIfAborted();
  phase='publish';
  const ack=await pds.write(state.claim,artifact);
  const prefix=!state.isFinal?('motion.2d.clip.'+state.direction+'.'+artifact.sha256):null;
  need(exact(ack,['ok','resultRef'])&&ack.ok===true&&typeof ack.resultRef==='string'&&
   (state.isFinal?finalResult.test(ack.resultRef):ack.resultRef===prefix),'PUBLICATION_UNCONFIRMED');
  phase='commit';
  complete(db,t,state,ack.resultRef);
  return {state:'completed',taskId:t.taskId,resultRef:ack.resultRef};
 }catch(e){
  const safe=typeof e?.message==='string'&&/^[A-Z0-9_]{1,130}$/.test(e.message)?e.message:'PRESET_2D_UNCONFIRMED';
  let failed=false;
  if(!['publish','commit'].includes(phase)||phase==='publish'&&e instanceof DefinitePresetWriteRejection){
   try{failed=markWorkflowTaskFailed(db,{...state.claim,workerType:worker});}catch{/* reconciliation only */}
  }
  return {state:failed?'failed':'requires_reconciliation',taskId:t.taskId,stage:phase,code:safe,
   ...(phase==='render'?{rendererStop:e?.rendererJoined===true?'confirmed':'unknown'}:{})};
 }finally{
  if(dir&&(!warm||warm.safeToClean())){
   try{await rm(dir,{force:true,recursive:true});}catch{/* do not change official task disposition */}
  }
  if(rendererAttempt)warm?.workFinished();
 }
}
