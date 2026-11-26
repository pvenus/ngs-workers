/** Shared official prop request runner, using the existing pinned image provider. */
import {DatabaseSync} from 'node:sqlite';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {exact} from './vendor/planner-f24d23a/planner.mjs';
import {assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskRetrySetSql,
 markWorkflowTaskFailed,nextWorkflowTaskFromPath} from './workflow-ready.mjs';
import {runPropImage,validatePropImageRequest,propImageRequestSchema} from './prop-image-design.mjs';

export const propImageOperation='prop.image-generate',propImageKey='prop_image_generate';
const officialSchema=propImageRequestSchema;
const official=/^request\.official\.[a-f0-9]{64}$/u;
const signature=Buffer.from('89504e470d0a1a0a','hex');
const taskFor=id=>'workflow.task.'+sha(canonical([id,propImageKey]));
const assert=(value,code='WORKFLOW_PROP_IMAGE_BINDING_INVALID')=>{if(!value)throw Error(code);};
const limits={workerType:'image',taskType:propImageOperation,taskKey:propImageKey,predecessorKey:null};

export function nextPropImageFromPath(dbPath){
 return nextWorkflowTaskFromPath(dbPath,limits);
}
function claim(db,officialRequestId,taskId){
 db.exec('BEGIN IMMEDIATE');
 try{
  const row=db.prepare(`SELECT r.*,t.task_input,t.updated_at AS task_stamp FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
   WHERE t.official_request_id=? AND t.task_id=? AND r.state='planned' AND r.reason IS NULL
    AND r.request_type=? AND r.project_id='binari' AND r.schema_version=? AND t.task_key=?
    AND t.task_type=? AND t.worker_type='image' AND t.depends_on_task_id IS NULL
    AND t.result_ref IS NULL AND ${workflowTaskEligibleSql(db,'t')}`)
   .get(officialRequestId,taskId,propImageOperation,officialSchema,propImageKey,propImageOperation);
  if(!row){db.exec('COMMIT');return null;}
  const payload=JSON.parse(row.payload);
  assert(payload?.schemaVersion===officialSchema&&row.payload===canonical(payload)&&sha(row.payload)===row.payload_sha256&&
   row.task_input===row.payload&&Number.isSafeInteger(row.task_stamp)&&row.task_stamp>=0&&row.task_stamp<Number.MAX_SAFE_INTEGER-1);
  const request=validatePropImageRequest(payload);
  const claimStamp=Math.max(Date.now(),row.task_stamp+1);
  assert(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
    WHERE official_request_id=? AND task_id=? AND updated_at=? AND result_ref IS NULL AND ${workflowTaskEligibleSql(db)}`)
   .run(claimStamp,officialRequestId,taskId,row.task_stamp).changes===1);
  db.exec('COMMIT');
  return {row,request,claim:{officialRequestId,taskId,claimStamp,projectId:'binari',payloadSha256:row.payload_sha256}};
 }catch(error){db.exec('ROLLBACK');throw error;}
}
function complete(db,{row,claim},resultRef){
 assert(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
  WHERE task_id=? AND official_request_id=? AND status='running' AND updated_at=? AND worker_type='image'
   AND task_key=? AND task_type=? AND task_input=? AND result_ref IS NULL AND depends_on_task_id IS NULL
   AND EXISTS (SELECT 1 FROM workflow_runs r WHERE r.official_request_id=workflow_tasks.official_request_id
    AND r.state='planned' AND r.reason IS NULL AND r.project_id='binari' AND r.request_type=?
    AND r.schema_version=? AND r.payload=? AND r.payload_sha256=? AND r.registered_at=?)`)
  .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),claim.taskId,claim.officialRequestId,claim.claimStamp,
   propImageKey,propImageOperation,row.task_input,propImageOperation,officialSchema,row.payload,row.payload_sha256,row.registered_at)
   .changes===1,'WORKFLOW_PROP_IMAGE_COMPLETION_CHANGED');
}
export async function runPropImageFromPath({dbPath,taskId,officialRequestId,pds,renderProp,signal}){
 assert(typeof officialRequestId==='string'&&officialRequestId.length===81&&official.test(officialRequestId)&&taskId===taskFor(officialRequestId));
 assert(typeof renderProp==='function'&&typeof pds?.readPropDesign==='function'&&typeof pds?.publishPropImage==='function');
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  if(signal?.aborted)return {state:'not_claimable',taskId};
  const selected=claim(db,officialRequestId,taskId);
  if(!selected)return {state:'not_claimable',taskId};
  let sent=false;
  try{
   const {output,contextSha256}=await runPropImage({request:selected.request,signal,renderProp,
    pds:{readPropDesign:(_project,{signal:readSignal})=>pds.readPropDesign(selected.claim,readSignal)}});
   assert(Buffer.isBuffer(output)&&output.length>=57&&output.length<=8*1024*1024&&output.subarray(0,8).equals(signature),
    'WORKFLOW_PROP_IMAGE_PNG_INVALID');
   signal?.throwIfAborted();
   const imageSha=sha(output),resultRef='media-artifacts/assets/'+imageSha+'.png';
   sent=true;
   const receipt=await pds.publishPropImage(selected.claim,{base64:output.toString('base64'),sha256:imageSha,
    bytes:output.length,mimeType:'image/png',contextSha256});
   assert(exact(receipt,['ok','resultRef'])&&receipt.ok===true&&receipt.resultRef===resultRef,'WORKFLOW_PROP_IMAGE_PUBLICATION_UNCONFIRMED');
   complete(db,selected,resultRef);
   return {state:'completed',taskId,resultRef};
  }catch(error){
   if(!sent&&markWorkflowTaskFailed(db,{...selected.claim,workerType:'image'}))return {state:'failed',taskId,
    code:error?.message==='PROP_IMAGE_DESIGN_CHANGED'?'PROP_IMAGE_DESIGN_CHANGED':'WORKFLOW_PROP_IMAGE_FAILED_BEFORE_PUBLISH'};
   return {state:'requires_reconciliation',taskId,code:'WORKFLOW_PROP_IMAGE_PUBLICATION_UNCONFIRMED'};
  }
 }finally{db.close();}
}
