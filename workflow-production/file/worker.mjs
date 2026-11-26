import {fileDeleteSchema,fileDeleteTaskKey,fileDeleteType,validateFileDelete} from './file-delete-envelope.mjs';
/** Single exact task only. PDS is an injected trusted boundary, not implemented here. */
import {DatabaseSync} from 'node:sqlite';
import {lstatSync,realpathSync} from 'node:fs';
import {dirname,isAbsolute,resolve} from 'node:path';
import {buildPlan as buildConceptPlan} from './vendor/planner-concept-v1/planner.mjs';
import {buildPlan,exact} from './vendor/planner-f24d23a/planner.mjs';
import {markWorkflowTaskFailed,workflowTaskEligibleSql,workflowTaskRetrySetSql} from './workflow-ready.mjs';
import {DeleteDefinitelyRejected} from './local-ports.mjs';
import {conceptDelete,npcDelete,audioDelete,audioDeleteSchema,audioDeleteKey,audioResultId,audioDeleteRoot,deleteCommandFromPlannedTask,singleTargetDeleteSchema,verifiedSingleTargetDelete} from './file-delete.mjs';
export {conceptDelete} from './file-delete.mjs';
import {buildPlan as buildSelectionPlan,isSelectionSchema} from './vendor/planner-npc-motion-selection-v1/planner.mjs';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';

const validId=(v)=>typeof v==='string'&&/^workflow\.task\.[a-f0-9]{64}$/.test(v);
const operations=Object.freeze([npcDelete,conceptDelete,audioDelete,fileDeleteType]);
const ready=db=>`SELECT t.task_id,t.task_key,t.task_input,t.depends_on_task_id,
  t.updated_at AS task_updated_at,r.*
 FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
 WHERE t.task_id=? AND r.official_request_id=? AND ${workflowTaskEligibleSql(db, 't')} AND t.task_type=?
 AND t.worker_type='file' AND t.result_ref IS NULL AND r.state='planned'
 AND (t.depends_on_task_id IS NULL OR EXISTS (
   SELECT 1 FROM workflow_tasks p
   WHERE p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id AND p.status='completed'
 ))`;

function protectSchema(db){
 const names=db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").all().map(r=>r.name);
 if(names.length!==2||names[0]!=='workflow_runs'||names[1]!=='workflow_tasks')throw Error('WORKFLOW_DELETE_DB_SCHEMA');
}
function buildAudioDeletePlan(row){
 const p=JSON.parse(row.payload);
 if(row.request_type!==audioDelete||row.schema_version!==audioDeleteSchema||row.project_id!=='binari'||
    row.reason!==null||!Number.isSafeInteger(row.registered_at)||row.registered_at<0||
    !exact(p,['schemaVersion','projectId','officialRequestId'])||p.schemaVersion!==audioDeleteSchema||
    p.projectId!=='binari'||typeof p.officialRequestId!=='string'||!audioResultId.test(p.officialRequestId)||
    p.officialRequestId===row.official_request_id)throw Error('WORKFLOW_DELETE_BINDING_INVALID');
 const payload=canonical(p);
 if(row.payload_sha256!==sha(payload))throw Error('WORKFLOW_DELETE_BINDING_INVALID');
 const input={officialRequestId:p.officialRequestId,target:{type:'directory',path:audioDeleteRoot+'/'+p.officialRequestId}};
 return {payload,tasks:[{id:'workflow.task.'+sha(canonical([row.official_request_id,audioDeleteKey])),
  key:audioDeleteKey,type:audioDelete,workerType:'file',dependsOn:null,input}]};
}
function buildFileDeletePlan(row){
 const payload=validateFileDelete(JSON.parse(row.payload),row.project_id),{target,expected}=JSON.parse(payload);
 if(row.request_type!==fileDeleteType||row.schema_version!==fileDeleteSchema||row.reason!==null||
    !Number.isSafeInteger(row.registered_at)||row.registered_at<0||row.payload_sha256!==sha(payload)||
    target.path.split('/').at(-1)===row.official_request_id)throw Error('WORKFLOW_DELETE_BINDING_INVALID');
 return {payload,tasks:[{id:'workflow.task.'+sha(canonical([row.official_request_id,fileDeleteTaskKey])),
  key:fileDeleteTaskKey,type:fileDeleteType,workerType:'file',dependsOn:null,input:{target,expected}}]};
}
function claim(db,taskId,officialRequestId,operation){
 db.exec('BEGIN IMMEDIATE');
 try{
  const row=db.prepare(ready(db)).get(taskId,officialRequestId,operation);
  if(!row){db.exec('COMMIT');return null;}
  const concept=operation===conceptDelete;
  const plan=operation===fileDeleteType?buildFileDeletePlan(row):operation===audioDelete?buildAudioDeletePlan(row):(concept?buildConceptPlan:isSelectionSchema(row.schema_version)?buildSelectionPlan:buildPlan)({officialRequestId:row.official_request_id,projectId:row.project_id,
   requestType:row.request_type,schemaVersion:row.schema_version,payload:JSON.parse(row.payload),
   payloadSha256:row.payload_sha256,status:'registered',registeredAt:row.registered_at});
  const expected=plan.tasks.find(t=>t.key===(concept?'category_delete':operation===fileDeleteType?fileDeleteTaskKey:operation===audioDelete?audioDeleteKey:'delete')&&t.type===operation&&t.workerType==='file');
  if(!expected||expected.id!==taskId||row.task_key!==expected.key||row.payload!==plan.payload||
      row.task_input!==canonical(expected.input)||row.depends_on_task_id!==expected.dependsOn||
      !Number.isSafeInteger(row.task_updated_at)||row.task_updated_at<0)throw Error('WORKFLOW_DELETE_BINDING_INVALID');
  const command=deleteCommandFromPlannedTask(expected);
  const stamp=Math.max(Date.now(),row.task_updated_at+1);
  if(!Number.isSafeInteger(stamp))throw Error('WORKFLOW_DELETE_CLAIM_CLOCK');
  const changed=db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?, ${workflowTaskRetrySetSql}
    WHERE task_id=(SELECT task_id FROM (${ready(db)})) AND updated_at=?`).run(stamp,taskId,officialRequestId,operation,row.task_updated_at).changes;
  if(changed!==1)throw Error('WORKFLOW_DELETE_CLAIM_CHANGED');
  const binding=Object.freeze({
   officialRequestId:row.official_request_id,taskId,projectId:row.project_id,payloadSha256:row.payload_sha256,claimStamp:stamp,
  });
  db.exec('COMMIT');
  return {row,binding,command,operation};
 }catch(e){db.exec('ROLLBACK');throw e;}
}
function verified(receipt,{binding,command,operation}){
 return exact(receipt,['schemaVersion','officialRequestId','taskId','projectId','npcId','payloadSha256',
   'operation','status','verified','targetAbsent','receiptId'])&&
  receipt.schemaVersion==='workflow.pds-delete-receipt.v1'&&
  ['officialRequestId','taskId','projectId','payloadSha256'].every(k=>receipt[k]===binding[k])&&
  receipt.npcId===command.targets[0].id&&receipt.operation===operation&&
  receipt.status==='committed'&&receipt.verified===true&&receipt.targetAbsent===true&&
  typeof receipt.receiptId==='string'&&/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(receipt.receiptId);
}
function finish(db,selected,resultRef){
 const {row,binding:{claimStamp:stamp,taskId,officialRequestId},operation}=selected;
 db.exec('BEGIN IMMEDIATE');
 try{
  const changed=db.prepare(`UPDATE workflow_tasks SET status='completed',updated_at=?,result_ref=?
     WHERE task_id=? AND official_request_id=? AND status='running' AND worker_type='file'
       AND task_type=? AND task_key=? AND task_input=? AND updated_at=?
       AND result_ref IS NULL AND depends_on_task_id IS ?
       AND EXISTS (SELECT 1 FROM workflow_runs r WHERE r.official_request_id=workflow_tasks.official_request_id
         AND r.state='planned' AND r.project_id=? AND r.request_type=? AND r.schema_version=?
         AND r.payload=? AND r.payload_sha256=? AND r.registered_at=?)`)
   .run(Math.max(Date.now(),stamp+1),resultRef,taskId,officialRequestId,
       operation,row.task_key,row.task_input,stamp,row.depends_on_task_id,row.project_id,row.request_type,
       row.schema_version,row.payload,row.payload_sha256,row.registered_at).changes;
  if(changed!==1)throw Error('WORKFLOW_DELETE_COMPLETION_CHANGED');
  db.exec('COMMIT');
 }catch(e){db.exec('ROLLBACK');throw e;}
}

/**
 * The caller injects one trusted adapter (auth, exact scope, storage fencing,
 * verified readback). The normalized receipt is not a claimed PDS wire format.
 * Never automatically act on a previously running or completed row.
 */
export async function runSelectedDelete({db,taskId,officialRequestId,pds,operation=npcDelete}){
 if(typeof officialRequestId!=='string'||!/^request\.official\.[a-f0-9]{64}$/.test(officialRequestId))throw Error('WORKFLOW_DELETE_OFFICIAL_ID_INVALID');
 if(!validId(taskId))throw Error('WORKFLOW_DELETE_TASK_ID_INVALID');
 if(!operations.includes(operation))throw Error('WORKFLOW_DELETE_OPERATION_DENIED');
 const concept=operation===conceptDelete;
 if([audioDelete,fileDeleteType].includes(operation)&&pds?.deleteContract!==singleTargetDeleteSchema)throw Error('WORKFLOW_DELETE_PDS_PORT_REQUIRED');
 if(typeof pds?.deleteTargets!=='function'||(pds.deleteContract!==undefined&&pds.deleteContract!==singleTargetDeleteSchema))
  throw Error('WORKFLOW_DELETE_PDS_PORT_REQUIRED');
 const singleTarget=pds.deleteContract===singleTargetDeleteSchema;
 protectSchema(db);
 const selected=claim(db,taskId,officialRequestId,operation);
 if(!selected)return {state:'not_claimable',taskId};
 const unresolved={state:'requires_reconciliation',taskId,code:'WORKFLOW_DELETE_RECONCILIATION_REQUIRED'};
 try{
  const receipt=await pds.deleteTargets(selected.command,selected.binding,operation);
  if(singleTarget?!verifiedSingleTargetDelete(receipt,taskId):concept?(!exact(receipt,['ok'])||receipt.ok!==true):!verified(receipt,selected))return unresolved;
  const resultRef=concept||[audioDelete,fileDeleteType].includes(operation)?null:singleTarget?receipt.operationId:receipt.receiptId;
  finish(db,selected,resultRef);
  return {state:'completed',taskId,resultRef};
 }catch(e){
  if(e instanceof DeleteDefinitelyRejected){
   try{if(markWorkflowTaskFailed(db,{taskId,officialRequestId,claimStamp:selected.binding.claimStamp,workerType:'file'}))
    return {state:'failed',taskId,code:'WORKFLOW_DELETE_DEFINITIVELY_REJECTED'};}catch{/* stale or unavailable */}
  }
  return unresolved;
 }
}

// In production the trusted caller must supply an explicit, pre-existing
// dedicated workflow DB path and task ID. No default path, schema creation,
// polling, executable hook, credentials or deployment configuration.
function assertDatabasePath(dbPath){
 if(typeof dbPath!=='string'||!isAbsolute(dbPath)||resolve(dbPath)!==dbPath||realpathSync(dbPath)!==dbPath)
  throw Error('WORKFLOW_DELETE_DB_PATH');
 const parent=lstatSync(dirname(dbPath)),file=lstatSync(dbPath);
 if(!parent.isDirectory()||(parent.mode&0o077)||parent.uid!==process.getuid()||
    !file.isFile()||file.nlink!==1||(file.mode&0o777)!==0o600||file.uid!==process.getuid())
  throw Error('WORKFLOW_DELETE_DB_PATH');
}
export function probeDeleteDatabase(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{protectSchema(db);return true;}finally{db.close();}
}
export async function runDeleteFromPath({dbPath,taskId,officialRequestId,pds,operation}){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');
  return await runSelectedDelete({db,taskId,officialRequestId,pds,operation});
 }finally{db.close();}
}
