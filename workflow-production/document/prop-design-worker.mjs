/** Exact official prop design lane inside the existing resident document worker. */
import {DatabaseSync} from 'node:sqlite';
import {assertDatabasePath,protectSchema,documentTaskEligibleSql,workflowTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';
import {canonical,sha,exact,projectId,propDesignKey,propDesignOperations,propDesignTaskId,isPropDesignOperation,
 requirePropDesign,expectedInput,verifiedDesignInput,designFromModel,propDesignResultRef} from './prop-design-contract.mjs';

const worker='document';
const scope=`r.state='planned' AND r.reason IS NULL AND r.project_id=? AND r.request_type=? AND r.schema_version=?
 AND t.task_type=? AND t.task_key=? AND t.worker_type=? AND t.task_id=? AND t.official_request_id=?
 AND t.depends_on_task_id IS NULL AND t.result_ref IS NULL`;
function claim(db,{officialRequestId,taskId,operation}){
 const spec=propDesignOperations[operation];
 db.exec('BEGIN IMMEDIATE');
 try{
  const row=db.prepare(`SELECT r.payload,r.payload_sha256,r.registered_at,t.task_input,t.updated_at,t.status
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
   WHERE ${scope} AND ${documentTaskEligibleSql}`)
   .get(projectId,spec.requestType,spec.schema,operation,propDesignKey,worker,taskId,officialRequestId);
  if(!row){db.exec('COMMIT');return null;}
  const payload=JSON.parse(row.payload),input=expectedInput(payload,operation);
  requirePropDesign(row.payload===canonical(payload)&&sha(row.payload)===row.payload_sha256&&
   row.task_input===canonical(input)&&Number.isSafeInteger(row.registered_at)&&row.registered_at>=0&&
   Number.isSafeInteger(row.updated_at)&&row.updated_at>=0&&row.updated_at<Number.MAX_SAFE_INTEGER-1);
  const claimStamp=Math.max(Date.now(),row.updated_at+1);
  requirePropDesign(row.registered_at<=claimStamp);
  const done=db.prepare(`UPDATE workflow_tasks AS t SET status='running',updated_at=?,${workflowTaskRetrySetSql}
   WHERE task_id=? AND official_request_id=? AND worker_type=? AND task_type=? AND task_key=? AND updated_at=? AND status=?
   AND result_ref IS NULL AND depends_on_task_id IS NULL AND ${workflowTaskEligibleSql('t')}`)
   .run(claimStamp,taskId,officialRequestId,worker,operation,propDesignKey,row.updated_at,row.status);
  requirePropDesign(done.changes===1);
  db.exec('COMMIT');
  return {operation,row,input,claim:{officialRequestId,taskId,projectId,payloadSha256:row.payload_sha256,claimStamp}};
 }catch(e){db.exec('ROLLBACK');throw e;}
}
const immutableRoot=`r.payload=? AND r.payload_sha256=? AND r.registered_at=?`;
const immutableTask=`t.status='running' AND t.updated_at=? AND t.task_input=?`;
function assertCurrent(db,selected){
 const {operation,row,claim}=selected,spec=propDesignOperations[operation];
 const existing=db.prepare(`SELECT 1 FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
  WHERE ${scope} AND ${immutableTask} AND ${immutableRoot}`)
  .get(projectId,spec.requestType,spec.schema,operation,propDesignKey,worker,claim.taskId,claim.officialRequestId,
   claim.claimStamp,row.task_input,row.payload,row.payload_sha256,row.registered_at);
 requirePropDesign(existing,'WORKFLOW_PROP_DESIGN_CLAIM_CHANGED');
}
function commit(db,selected,resultRef){
 const {operation,row,claim}=selected,spec=propDesignOperations[operation];
 const done=db.prepare(`UPDATE workflow_tasks AS t SET status='completed',result_ref=?,updated_at=?
   WHERE t.task_type=? AND t.task_key=? AND t.worker_type=? AND t.task_id=? AND t.official_request_id=?
   AND t.depends_on_task_id IS NULL AND t.result_ref IS NULL AND ${immutableTask}
   AND EXISTS (SELECT 1 FROM workflow_runs r WHERE r.official_request_id=t.official_request_id
    AND r.state='planned' AND r.reason IS NULL AND r.project_id=? AND r.request_type=? AND r.schema_version=? AND ${immutableRoot})`)
  .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),operation,propDesignKey,worker,claim.taskId,claim.officialRequestId,
   claim.claimStamp,row.task_input,projectId,spec.requestType,spec.schema,row.payload,row.payload_sha256,row.registered_at);
 requirePropDesign(done.changes===1,'WORKFLOW_PROP_DESIGN_COMMIT_CHANGED');
}
const publicCode=e=>typeof e?.message==='string'&&/^(?:PROP_DESIGN_|WORKFLOW_PROP_DESIGN_|REPRESENTATIVE_CODEX_)[A-Z_]{1,80}$/.test(e.message)?
 e.message:'WORKFLOW_PROP_DESIGN_FAILED_BEFORE_PUBLISH';
export async function runPropDesignFromPath({dbPath,taskId,officialRequestId,operation,pds,render,signal}){
 requirePropDesign(isPropDesignOperation(operation)&&taskId===propDesignTaskId(officialRequestId)&&
  typeof pds?.readPropDesign==='function'&&typeof pds?.publishPropDesign==='function'&&typeof render==='function');
 assertDatabasePath(dbPath);
 if(signal?.aborted)return {state:'stopping'};
 if(render.ready?.()===false)return {state:'not_ready',code:'REPRESENTATIVE_CODEX_UNAVAILABLE'};
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  const selected=claim(db,{officialRequestId,taskId,operation});
  if(!selected)return {state:'not_claimable',taskId};
  let sent=false;
  try{
   const read=await pds.readPropDesign(selected.claim,signal),input=verifiedDesignInput(read,selected.input);
   signal?.throwIfAborted();
   assertCurrent(db,selected);
   const model=await render(input,{signal});
   const artifact=designFromModel(model,input);
   signal?.throwIfAborted();
   assertCurrent(db,selected);
   const ref=propDesignResultRef(artifact);
   sent=true;
   const receipt=await pds.publishPropDesign(selected.claim,artifact);
   requirePropDesign(exact(receipt,['ok','resultRef'])&&receipt.ok===true&&receipt.resultRef===ref,
    'WORKFLOW_PROP_DESIGN_PUBLICATION_UNCONFIRMED');
   commit(db,selected,ref);
   return {state:'completed',taskId,resultRef:ref};
  }catch(e){
   if(!sent){
    let failed=false;try{
     assertCurrent(db,selected);
     failed=markWorkflowTaskFailed(db,{...selected.claim,workerType:worker});
    }catch{/* uncertain or revoked claim */}
    if(failed)return {state:'failed',taskId,code:publicCode(e)};
   }
   return {state:'requires_reconciliation',taskId,code:sent?'WORKFLOW_PROP_DESIGN_PUBLICATION_UNCONFIRMED':'WORKFLOW_PROP_DESIGN_CLAIM_CHANGED'};
  }
 }finally{db.close();}
}
