/** Exact, model-free title document materialization into the existing PDS/Filex pipeline. */
import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {mapFields as exact,officialId} from './map-contract.mjs';
import {assertDatabasePath,protectSchema,documentTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';
import {titleType,titleTask,titleRef,validateTitleGenerate} from './title-contract.mjs';
export {titleType,titleTask};
const sha=x=>createHash('sha256').update(x).digest('hex');
const allowed=new Set(['TITLE_REVISION_OR_PIN_CONFLICT','WORKFLOW_CINEMATIC_DENIED','WORKFLOW_CINEMATIC_NOT_FOUND','WORKFLOW_CINEMATIC_NOT_READY','WORKFLOW_CINEMATIC_OUTCOME_UNCERTAIN']);

export async function runTitleFromPath({dbPath,officialRequestId,taskId,pds,signal}){
 if(!officialId(officialRequestId)||taskId!=='workflow.task.'+sha(JSON.stringify([officialRequestId,titleTask])))throw Error('TITLE_TASK_INVALID');
 if(signal?.aborted)return {state:'stopping'};
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA busy_timeout=5000; PRAGMA foreign_keys=ON');protectSchema(db);
  const scope=[taskId,officialRequestId,titleTask,titleType];
  const columns=new Set(db.prepare('PRAGMA table_info(workflow_tasks)').all().map(c=>c.name));
  const additional=[2,3,4,5].filter(n=>columns.has('depends_on_task_id_'+n)).map(n=>' AND t.depends_on_task_id_'+n+' IS NULL').join('');
  const immutable="task_id=? AND official_request_id=? AND task_key=? AND task_type=? AND worker_type='document' AND depends_on_task_id IS NULL AND result_ref IS NULL";
  let claim,content;
  db.exec('BEGIN IMMEDIATE');
  try{
   const row=db.prepare(`SELECT t.task_input,r.payload,r.payload_sha256,t.updated_at,r.registered_at,r.schema_version
    FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
    WHERE ${immutable.replace('official_request_id=?','t.official_request_id=?')}${additional}
    AND r.project_id='binari' AND r.request_type=t.task_type AND r.state='planned' AND r.reason IS NULL AND ${documentTaskEligibleSql(db)}`).get(...scope);
   if(!row){db.exec('COMMIT');return {state:'not_claimable',taskId};}
   const p=JSON.parse(row.payload);
   if(row.schema_version!==p?.schemaVersion||row.task_input!==row.payload||
      validateTitleGenerate(p)!==row.payload||sha(row.payload)!==row.payload_sha256)
       throw Error('TITLE_TASK_BINDING_INVALID');
   content=p.content;
   const claimStamp=Math.max(Date.now(),row.updated_at+1,row.registered_at);
   if(!Number.isSafeInteger(claimStamp))throw Error('TITLE_TASK_BINDING_INVALID');
   if(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
     WHERE ${immutable} AND updated_at=?`).run(claimStamp,...scope,row.updated_at).changes!==1)throw Error('TITLE_CLAIM_CHANGED');
   claim={taskId,officialRequestId,projectId:'binari',claimStamp,payloadSha256:row.payload_sha256};
   db.exec('COMMIT');
  }catch(e){db.exec('ROLLBACK');throw e;}
  const resultRef=titleRef(officialRequestId,1);
  const row=()=>db.prepare('SELECT status,result_ref FROM workflow_tasks WHERE task_id=? AND official_request_id=?').get(taskId,officialRequestId);
  const complete=()=>{const v=row();return v?.status==='completed'&&v.result_ref===resultRef;};
  try{
   signal?.throwIfAborted();
   const ack=await pds.publishTitle(claim,content,signal);
   if(!exact(ack,['ok','resultRef'])||ack.ok!==true||ack.resultRef!==resultRef||!complete())throw Error('TITLE_SAVE_NOT_CONFIRMED');
   return {state:'completed',taskId,resultRef};
  }catch(e){
   // PDS owns the save reservation and completion. Observe only after a lost
   // acknowledgement; never replay a publication or complete it from here.
   if(complete())return {state:'completed',taskId,resultRef};
   const failed=row()?.status==='failed'||markWorkflowTaskFailed(db,{...claim,workerType:'document'});
   return {state:failed?'failed':'not_claimable',taskId,code:allowed.has(e?.message)?e.message:'TITLE_PUBLICATION_FAILED'};
  }
 }finally{db.close();}
}
