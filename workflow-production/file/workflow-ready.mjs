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
export function nextWorkflowTaskFromPath(dbPath,{workerType,taskType,taskKey,predecessorKey,targetRoot=null}){
 const name=value=>typeof value==='string'&&/^[a-z][a-z0-9._-]*$/.test(value);
 if(targetRoot!==null&&(!['canonical-files/binari/props','canonical-files/binari/npcs','canonical-files/binari/cinematics/backgrounds','canonical-files/binari/cinematics'].includes(targetRoot)||taskType!=='file.delete')||!name(workerType)||!name(taskType)||!name(taskKey)||(predecessorKey!==null&&!name(predecessorKey)))
  throw Error('WORKFLOW_POLL_SCOPE_REQUIRED');
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  // Cinematic units are separate official requests. Do not claim a sidecar
  // or image while a prerequisite is pending, running, or retryable.
  const cinematicOrder=targetRoot==='canonical-files/binari/cinematics'?`
    AND NOT EXISTS (
      SELECT 1 FROM workflow_tasks p JOIN workflow_runs q USING(official_request_id)
      WHERE p.task_id<>t.task_id AND q.state IN ('awaiting_rs','planned') AND q.reason IS NULL
       AND p.task_type='file.delete' AND json_valid(p.task_input)
       AND json_extract(p.task_input,'$.target.root')='canonical-files/binari/cinematics'
       AND json_extract(p.task_input,'$.expected.sourceSha256')=json_extract(t.task_input,'$.expected.sourceSha256')
       AND (p.status IN ('pending','running') OR p.status='failed' AND p.retry_count<${workflowTaskMaxRetries})
       AND (
        json_extract(t.task_input,'$.target.path') LIKE 'sources/%.json' AND json_extract(p.task_input,'$.target.path') NOT LIKE 'sources/%'
        OR json_extract(t.task_input,'$.target.path') LIKE 'sources/%.png' AND json_extract(p.task_input,'$.target.path') NOT LIKE 'sources/%.png'
        OR json_extract(t.task_input,'$.target.path') LIKE 'results/%.json' AND
          json_extract(p.task_input,'$.target.path')=substr(json_extract(t.task_input,'$.target.path'),1,length(json_extract(t.task_input,'$.target.path'))-5)||'.mp4'
       )
    )`:'';
  return db.prepare(`SELECT t.task_id AS taskId, t.official_request_id AS officialRequestId
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
   WHERE r.state='planned' AND ${workflowTaskEligibleSql('t')} AND t.result_ref IS NULL
    AND t.worker_type=? AND t.task_type=? AND t.task_key=?
    AND (? IS NULL OR json_extract(CASE WHEN json_valid(t.task_input) THEN t.task_input ELSE '{}' END,'$.target.root')=?)
    AND ((? IS NULL AND t.depends_on_task_id IS NULL) OR
     EXISTS (SELECT 1 FROM workflow_tasks p WHERE p.task_id=t.depends_on_task_id
      AND p.official_request_id=t.official_request_id AND p.task_key=? AND p.status='completed'))
   ${cinematicOrder}
   ORDER BY r.registered_at,t.official_request_id,t.task_id LIMIT 1`).get(workerType,taskType,taskKey,targetRoot,targetRoot,predecessorKey,predecessorKey)??null;
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

/** Non-authoritative preflight; execution revalidates the body and claim. */
export function fileDeleteTargetRootFromPath(dbPath,officialRequestId,taskId){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  const row=db.prepare("SELECT task_input FROM workflow_tasks WHERE task_id=? AND official_request_id=? AND task_type='file.delete' AND task_key='file_delete' AND worker_type='file'")
   .get(taskId,officialRequestId);
  if(!row)return null;
  const root=JSON.parse(row.task_input)?.target?.root;
  return ['canonical-files/binari/props','canonical-files/binari/npcs','canonical-files/binari/cinematics/backgrounds','canonical-files/binari/cinematics'].includes(root)?root:null;
 }finally{db.close();}
}
