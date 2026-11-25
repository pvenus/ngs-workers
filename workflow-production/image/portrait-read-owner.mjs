import {DatabaseSync} from 'node:sqlite';
import {assertDatabasePath,protectSchema} from './workflow-ready.mjs';

const column='portrait_read_owner_pid';
const id=/^workflow\.task\.[a-f0-9]{64}$/;
const official=/^request\.official\.[a-f0-9]{64}$/;
export const hasPortraitReadOwner=db=>db.prepare('PRAGMA table_info(workflow_tasks)').all()
 .some(field=>field.name===column);
function exited(pid){
 if(!Number.isSafeInteger(pid)||pid<1||pid>2147483647||pid===process.pid)return false;
 try{process.kill(pid,0);return false;}catch(e){return e?.code==='ESRCH';}
}
export function finishPortraitRead(db,{taskId,officialRequestId,claimStamp},{failed=false}={}){
 const sql=failed?'status=\'failed\',updated_at=?,portrait_read_owner_pid=NULL':'portrait_read_owner_pid=NULL';
 const args=failed?[Math.max(Date.now(),claimStamp+1)]:[];
 return db.prepare(`UPDATE workflow_tasks SET ${sql} WHERE task_id=? AND official_request_id=? AND worker_type='image'
  AND status='running' AND updated_at=? AND result_ref IS NULL AND portrait_read_owner_pid=?`)
  .run(...args,taskId,officialRequestId,claimStamp,process.pid).changes===1;
}
export function detectLostPortraitReads(db,{knownTypes,ownerExited=exited}){
 if(!hasPortraitReadOwner(db))return [];
 const candidates=db.prepare(`SELECT task_id,official_request_id,task_type,t.updated_at AS stamp,portrait_read_owner_pid AS pid
  FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
  WHERE t.worker_type='image' AND t.status='running' AND t.result_ref IS NULL AND r.state='planned'
  AND portrait_read_owner_pid IS NOT NULL ORDER BY t.updated_at,t.task_id LIMIT 64`).all();
 const update=db.prepare(`UPDATE workflow_tasks SET status='failed',updated_at=?,portrait_read_owner_pid=NULL
  WHERE task_id=? AND official_request_id=? AND task_type=? AND worker_type='image' AND status='running'
   AND updated_at=? AND result_ref IS NULL AND portrait_read_owner_pid=?
   AND EXISTS(SELECT 1 FROM workflow_runs r WHERE r.official_request_id=workflow_tasks.official_request_id AND r.state='planned')`);
 const failures=[];
 for(const {task_id,official_request_id,task_type,stamp,pid} of candidates){
  if(!id.test(task_id)||!official.test(official_request_id)||!knownTypes.includes(task_type)||
    !Number.isSafeInteger(stamp)||stamp<1||stamp>=Number.MAX_SAFE_INTEGER||
    !Number.isSafeInteger(pid)||pid<1||pid>2147483647||pid===process.pid||ownerExited(pid)!==true)continue;
  if(update.run(Math.max(Date.now(),stamp+1),task_id,official_request_id,task_type,stamp,pid).changes===1)
   failures.push({taskId:task_id,state:'failed',code:'WORKFLOW_PORTRAIT_READ_OWNER_LOST'});
 }
 return failures;
}
export function detectLostPortraitReadsFromPath(dbPath,knownTypes){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{protectSchema(db);return detectLostPortraitReads(db,{knownTypes});}finally{db.close();}
}
