/** One non-NPC neutral reference bound to a formal motion conversion task. */
import {DatabaseSync} from 'node:sqlite';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {exact} from './vendor/planner-f24d23a/planner.mjs';
import {assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskRetrySetSql,
 markWorkflowTaskFailed,nextWorkflowTaskFromPath} from './workflow-ready.mjs';
import {neutralReferenceKind} from './portrait-renderer.mjs';

export const motionImageOperation='motion-preset.convert-2d.image';
export const motionImageKey='motion_2d_image';
export const motionImageRequest='motion-preset.convert-2d';
export const motionImageSchema='ngs.motion-preset-convert-2d.v1';
const hex=/^[a-f0-9]{64}$/u;
const official=/^request\.official\.[a-f0-9]{64}$/u;
const sourceId=/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u;
const variantId=/^[a-z][a-z0-9-]{1,63}$/u;
const pngSignature=Buffer.from('89504e470d0a1a0a','hex');
const assert=(valid,code='WORKFLOW_MOTION_IMAGE_BINDING_INVALID')=>{if(!valid)throw Error(code);};
const begin=db=>db.exec('BEGIN IMMEDIATE');
const commit=db=>db.exec('COMMIT');

export function nextMotionImageFromPath(dbPath){
 return nextWorkflowTaskFromPath(dbPath,{workerType:'image',taskType:motionImageOperation,
  taskKey:motionImageKey,predecessorKey:null});
}

function claim(db,officialRequestId,taskId){
 begin(db);
 try{
  const row=db.prepare(`SELECT t.task_input,t.updated_at AS task_updated_at,r.*
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
   WHERE t.official_request_id=? AND t.task_id=? AND r.state='planned'
    AND t.task_key=? AND t.task_type=? AND t.worker_type='image' AND t.depends_on_task_id IS NULL
    AND t.result_ref IS NULL AND ${workflowTaskEligibleSql('t')}`).get(officialRequestId,taskId,motionImageKey,motionImageOperation);
  if(!row){commit(db);return null;}
  const payload=JSON.parse(row.payload);
  assert(exact(payload,['schemaVersion','projectId','sourcePresetId','variantPresetId','label'])&&
   row.project_id===payload.projectId&&row.project_id==='binari'&&row.request_type===motionImageRequest&&
   row.schema_version===motionImageSchema&&payload.schemaVersion===motionImageSchema&&
   typeof payload.sourcePresetId==='string'&&sourceId.test(payload.sourcePresetId)&&
   typeof payload.variantPresetId==='string'&&variantId.test(payload.variantPresetId)&&
   payload.sourcePresetId!==payload.variantPresetId&&typeof payload.label==='string'&&payload.label.isWellFormed()&&
   payload.label===payload.label.trim()&&payload.label.length>0&&[...payload.label].length<=120&&
   !/[\p{Cc}\u2028\u2029]/u.test(payload.label)&&
   row.payload===canonical(payload)&&hex.test(row.payload_sha256)&&sha(row.payload)===row.payload_sha256&&
   row.task_input===canonical({sourcePresetId:payload.sourcePresetId,variantPresetId:payload.variantPresetId})&&
   Number.isSafeInteger(row.task_updated_at)&&row.task_updated_at>=0&&row.task_updated_at<Number.MAX_SAFE_INTEGER-1);
  const stamp=Math.max(Date.now(),row.task_updated_at+1);
  assert(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
   WHERE official_request_id=? AND task_id=? AND updated_at=? AND result_ref IS NULL AND ${workflowTaskEligibleSql()}`)
   .run(stamp,officialRequestId,taskId,row.task_updated_at).changes===1);
  commit(db);
  return {row,claim:{officialRequestId,taskId,projectId:row.project_id,payloadSha256:row.payload_sha256,claimStamp:stamp}};
 }catch(error){db.exec('ROLLBACK');throw error;}
}

function complete(db,{row,claim},resultRef){
 const {taskId,officialRequestId,claimStamp}=claim;
 assert(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
  WHERE task_id=? AND official_request_id=? AND status='running' AND updated_at=?
   AND worker_type='image' AND task_key=? AND task_type=? AND task_input=?
   AND result_ref IS NULL AND depends_on_task_id IS NULL
   AND EXISTS (SELECT 1 FROM workflow_runs r WHERE r.official_request_id=workflow_tasks.official_request_id
    AND r.state='planned' AND r.project_id=? AND r.request_type=? AND r.schema_version=?
    AND r.payload=? AND r.payload_sha256=? AND r.registered_at=?)`)
  .run(resultRef,Math.max(Date.now(),claimStamp+1),taskId,officialRequestId,claimStamp,
   motionImageKey,motionImageOperation,row.task_input,row.project_id,row.request_type,row.schema_version,
   row.payload,row.payload_sha256,row.registered_at).changes===1,'WORKFLOW_MOTION_IMAGE_COMPLETION_CHANGED');
}

export async function runMotionImageFromPath({dbPath,taskId,officialRequestId,pds,renderNeutral,signal}){
 assert(typeof officialRequestId==='string'&&official.test(officialRequestId)&&
  taskId==='workflow.task.'+sha(canonical([officialRequestId,motionImageKey])));
 assert(typeof renderNeutral==='function'&&typeof pds?.readMotionImage==='function'&&typeof pds?.publishMotionImage==='function');
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');
  protectSchema(db);
  if(signal?.aborted)return {state:'not_claimable',taskId};
  const selected=claim(db,officialRequestId,taskId);
  if(!selected)return {state:'not_claimable',taskId};
  let sent=false;
  try{
   const basis=await pds.readMotionImage(selected.claim,signal);
   assert(exact(basis,['sourcePresetHash'])&&typeof basis.sourcePresetHash==='string'&&hex.test(basis.sourcePresetHash));
   assert(!signal?.aborted,'WORKFLOW_MOTION_IMAGE_CANCELLED');
   const png=await renderNeutral({referenceKind:neutralReferenceKind},{signal});
   assert(Buffer.isBuffer(png)&&png.length>=33&&png.length<=8*1024*1024&&
    png.subarray(0,8).equals(pngSignature),'WORKFLOW_MOTION_IMAGE_PNG_INVALID');
   assert(!signal?.aborted,'WORKFLOW_MOTION_IMAGE_CANCELLED');
   const hash=sha(png),resultRef='motion.2d.image.'+hash;
   sent=true;
   const receipt=await pds.publishMotionImage(selected.claim,{base64:png.toString('base64'),sha256:hash});
   assert(exact(receipt,['ok','resultRef'])&&receipt.ok===true&&receipt.resultRef===resultRef,
    'WORKFLOW_MOTION_IMAGE_PUBLICATION_UNCONFIRMED');
   complete(db,selected,resultRef);
   return {state:'completed',taskId,resultRef};
  }catch(error){
   if(!sent&&markWorkflowTaskFailed(db,{...selected.claim,workerType:'image'}))
    return {state:'failed',taskId,code:signal?.aborted?'WORKFLOW_MOTION_IMAGE_CANCELLED':'WORKFLOW_MOTION_IMAGE_FAILED_BEFORE_PUBLISH'};
   return {state:'requires_reconciliation',taskId,code:'WORKFLOW_MOTION_IMAGE_PUBLICATION_UNCONFIRMED'};
  }
 }finally{db.close();}
}
