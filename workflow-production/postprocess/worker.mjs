import {retrySqliteBusy} from './sqlite-busy-retry.mjs';
/** Single exact task only. PDS is an injected trusted boundary, not implemented here. */
import {DatabaseSync} from 'node:sqlite';
import {lstatSync,realpathSync} from 'node:fs';
import {dirname,isAbsolute,resolve} from 'node:path';
import {buildPlan,exact} from './vendor/planner-f24d23a/planner.mjs';
import {canonical} from './vendor/planner-f24d23a/envelope.mjs';
import {workflowDependenciesCompletedSql} from './workflow-ready.mjs';

const validId=(v)=>typeof v==='string'&&/^workflow\.task\.[a-f0-9]{64}$/.test(v);
const ready=db=>`SELECT t.task_id,t.task_key,t.task_input,t.depends_on_task_id,
  t.updated_at AS task_updated_at,r.*
 FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
 WHERE t.task_id=? AND r.official_request_id=? AND t.status='pending' AND ${workflowDependenciesCompletedSql(db,'t')} AND t.task_type='npc.delete-folder'
 AND t.worker_type='file' AND t.result_ref IS NULL AND r.state='planned'
 AND (t.depends_on_task_id IS NULL OR EXISTS (
   SELECT 1 FROM workflow_tasks p
   WHERE p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id AND p.status='completed'
 ))`;

function protectSchema(db){
 const names=db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").all().map(r=>r.name);
 if(names.length!==2||names[0]!=='workflow_runs'||names[1]!=='workflow_tasks')throw Error('WORKFLOW_DELETE_DB_SCHEMA');
}
function claim(db,taskId,officialRequestId){
 db.exec('BEGIN IMMEDIATE');
 try{
  const row=db.prepare(ready(db)).get(taskId,officialRequestId);
  if(!row){db.exec('COMMIT');return null;}
  const plan=buildPlan({officialRequestId:row.official_request_id,projectId:row.project_id,
   requestType:row.request_type,schemaVersion:row.schema_version,payload:JSON.parse(row.payload),
   payloadSha256:row.payload_sha256,status:'registered',registeredAt:row.registered_at});
  const expected=plan.tasks.find(t=>t.key==='delete');
  if(!expected||expected.id!==taskId||row.task_key!==expected.key||row.payload!==plan.payload||
      row.task_input!==canonical(expected.input)||row.depends_on_task_id!==expected.dependsOn||
      !Number.isSafeInteger(row.task_updated_at)||row.task_updated_at<0)throw Error('WORKFLOW_DELETE_BINDING_INVALID');
  const stamp=Math.max(Date.now(),row.task_updated_at+1);
  if(!Number.isSafeInteger(stamp))throw Error('WORKFLOW_DELETE_CLAIM_CLOCK');
  const changed=db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?
    WHERE task_id=(SELECT task_id FROM (${ready(db)})) AND updated_at=?`).run(stamp,taskId,officialRequestId,row.task_updated_at).changes;
  if(changed!==1)throw Error('WORKFLOW_DELETE_CLAIM_CHANGED');
  const command=Object.freeze({
   officialRequestId:row.official_request_id,taskId,projectId:row.project_id,npcId:expected.input.npcId,
   payloadSha256:row.payload_sha256,operation:'npc.delete-folder',scope:expected.input.scope,
   preserveRequestHistory:true,noOldDataReuse:true
  });
  db.exec('COMMIT');
  return {row,stamp,command};
 }catch(e){db.exec('ROLLBACK');throw e;}
}
function verified(receipt,command){
 return exact(receipt,['schemaVersion','officialRequestId','taskId','projectId','npcId','payloadSha256',
   'operation','status','verified','targetAbsent','receiptId'])&&
  receipt.schemaVersion==='workflow.pds-delete-receipt.v1'&&
  ['officialRequestId','taskId','projectId','npcId','payloadSha256','operation'].every(k=>receipt[k]===command[k])&&
  receipt.status==='committed'&&receipt.verified===true&&receipt.targetAbsent===true&&
  typeof receipt.receiptId==='string'&&/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(receipt.receiptId);
}
function finish(db,selected,receipt){
 const {row,stamp,command}=selected;
 db.exec('BEGIN IMMEDIATE');
 try{
  const changed=db.prepare(`UPDATE workflow_tasks SET status='completed',updated_at=?,result_ref=?
     WHERE task_id=? AND official_request_id=? AND status='running' AND worker_type='file'
       AND task_type='npc.delete-folder' AND task_key=? AND task_input=? AND updated_at=?
       AND result_ref IS NULL AND depends_on_task_id IS ?
       AND EXISTS (SELECT 1 FROM workflow_runs r WHERE r.official_request_id=workflow_tasks.official_request_id
         AND r.state='planned' AND r.project_id=? AND r.request_type=? AND r.schema_version=?
         AND r.payload=? AND r.payload_sha256=? AND r.registered_at=?)`)
   .run(Math.max(Date.now(),stamp+1),receipt.receiptId,command.taskId,command.officialRequestId,
       row.task_key,row.task_input,stamp,row.depends_on_task_id,row.project_id,row.request_type,
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
export async function runSelectedDelete({db,taskId,officialRequestId,pds}){
 if(typeof officialRequestId!=='string'||!/^request\.official\.[a-f0-9]{64}$/.test(officialRequestId))throw Error('WORKFLOW_DELETE_OFFICIAL_ID_INVALID');
 if(!validId(taskId))throw Error('WORKFLOW_DELETE_TASK_ID_INVALID');
 if(typeof pds?.deleteExactNpcFolder!=='function')throw Error('WORKFLOW_DELETE_PDS_PORT_REQUIRED');
 protectSchema(db);
 const selected=claim(db,taskId,officialRequestId);
 if(!selected)return {state:'not_claimable',taskId};
 const unresolved={state:'requires_reconciliation',taskId,code:'WORKFLOW_DELETE_RECONCILIATION_REQUIRED'};
 try{
  const receipt=await pds.deleteExactNpcFolder(selected.command,selected.stamp);
  if(!verified(receipt,selected.command))return unresolved;
  await retrySqliteBusy(()=>finish(db,selected,receipt),taskId);
  return {state:'completed',taskId,resultRef:receipt.receiptId};
 }catch{return unresolved;}
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
export async function runDeleteFromPath({dbPath,taskId,officialRequestId,pds}){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');
  return await runSelectedDelete({db,taskId,officialRequestId,pds});
 }finally{db.close();}
}
