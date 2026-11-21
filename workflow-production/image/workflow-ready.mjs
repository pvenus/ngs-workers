import {DatabaseSync} from 'node:sqlite';
import {lstatSync,realpathSync} from 'node:fs';
import {dirname,isAbsolute,resolve} from 'node:path';

export function protectSchema(db){
 const names=db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").all().map(r=>r.name);
 if(names.length!==2||names[0]!=='workflow_runs'||names[1]!=='workflow_tasks')throw Error('WORKFLOW_PRESET_DB_SCHEMA');
}
export function assertDatabasePath(dbPath){
 if(typeof dbPath!=='string'||!isAbsolute(dbPath)||resolve(dbPath)!==dbPath||realpathSync(dbPath)!==dbPath)
  throw Error('WORKFLOW_PRESET_DB_PATH');
 const parent=lstatSync(dirname(dbPath)),file=lstatSync(dbPath);
 if(!parent.isDirectory()||(parent.mode&0o077)||parent.uid!==process.getuid()||
    !file.isFile()||file.nlink!==1||(file.mode&0o777)!==0o600||file.uid!==process.getuid())
  throw Error('WORKFLOW_PRESET_DB_PATH');
}

export const workflowTaskRetryDelayMs=600_000;
export const workflowTaskMaxRetries=3;

/** A failed attempt is eligible only at or after its 10-minute database-clock boundary. */
export function workflowTaskEligibleSql(alias=''){
 if(alias!==''&&!/^[a-z][a-z0-9_]*$/.test(alias))throw Error('WORKFLOW_RETRY_SQL_ALIAS');
 const p=alias?alias+'.':'';
 return `(${p}status='pending' OR (${p}status='failed' AND ${p}retry_count<${workflowTaskMaxRetries} AND ${p}updated_at<=CAST(unixepoch('subsec')*1000 AS INTEGER)-${workflowTaskRetryDelayMs}))`;
}

// SQLite SET expressions read the old state, and this assignment runs inside the exact claim UPDATE.
export const workflowTaskRetrySetSql="retry_count=retry_count+CASE WHEN status='failed' THEN 1 ELSE 0 END";

/** Candidate discovery is read only; the exact claim transaction is authoritative. */
export function nextWorkflowTaskFromPath(dbPath,{workerType,taskType,taskKey,predecessorKey,alsoCompleted=null}){
 const name=value=>typeof value==='string'&&/^[a-z][a-z0-9._-]*$/.test(value);
 if(!name(workerType)||!name(taskType)||!name(taskKey)||(predecessorKey!==null&&!name(predecessorKey)))
  throw Error('WORKFLOW_POLL_SCOPE_REQUIRED');
 if(alsoCompleted!==null&&(!name(alsoCompleted.taskKey)||!name(alsoCompleted.taskType)||!name(alsoCompleted.workerType)))
  throw Error('WORKFLOW_POLL_SCOPE_REQUIRED');
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  const extra=alsoCompleted?
   [alsoCompleted.taskKey,alsoCompleted.taskType,alsoCompleted.workerType]:[];
  return db.prepare(`SELECT t.task_id AS taskId, t.official_request_id AS officialRequestId
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
   WHERE r.state='planned' AND ${workflowTaskEligibleSql('t')} AND t.result_ref IS NULL
    AND t.worker_type=? AND t.task_type=? AND t.task_key=?
    AND ((? IS NULL AND t.depends_on_task_id IS NULL) OR
     EXISTS (SELECT 1 FROM workflow_tasks p WHERE p.task_id=t.depends_on_task_id
      AND p.official_request_id=t.official_request_id AND p.task_key=? AND p.status='completed'))
    ${alsoCompleted?`AND EXISTS (SELECT 1 FROM workflow_tasks a WHERE a.official_request_id=t.official_request_id
     AND a.task_key=? AND a.task_type=? AND a.worker_type=? AND a.status='completed')`:''}
   ORDER BY r.registered_at,t.official_request_id,t.task_id LIMIT 1`).get(workerType,taskType,taskKey,predecessorKey,predecessorKey,...extra)??null;
 }finally{db.close();}
}

/** Terminal execution failure, never a transition into retry or a request-level status. */
export function markWorkflowTaskFailed(db,{taskId,officialRequestId,claimStamp,workerType}){
 if(typeof taskId!=='string'||!taskId||typeof officialRequestId!=='string'||!officialRequestId||
    typeof workerType!=='string'||!workerType||!Number.isSafeInteger(claimStamp)||claimStamp<1||
    claimStamp>=Number.MAX_SAFE_INTEGER)throw Error('WORKFLOW_FAILURE_CLAIM_REQUIRED');
 return db.prepare(`UPDATE workflow_tasks SET status='failed',updated_at=?
  WHERE task_id=? AND official_request_id=? AND worker_type=? AND status='running'
   AND updated_at=? AND result_ref IS NULL`)
  .run(Math.max(Date.now(),claimStamp+1),taskId,officialRequestId,workerType,claimStamp).changes===1;
}
