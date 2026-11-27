import {setTimeout as delay} from 'node:timers/promises';

export const sqliteBusyRetryCount=10;
export const sqliteBusyRetryWaitMs=5000;

export const isSqliteBusy=error=>error?.code==='SQLITE_BUSY'||
 (Number.isInteger(error?.errcode)&&(error.errcode&255)===5);

const exhausted=new WeakSet();
export const isSqliteBusyExhausted=error=>exhausted.has(error);

/** Retry only a synchronous, claim-bound DB write; no model or storage calls. */
export async function retrySqliteBusy(write,taskId,{sleep=delay,log=console.error}={}){
 for(let attempt=0;;attempt++){
  try{return write();}
  catch(error){
   if(!isSqliteBusy(error))throw error;
   if(attempt===sqliteBusyRetryCount){
    exhausted.add(error);
    log(JSON.stringify({event:'workflow_sqlite_busy_exhausted',
     ...(typeof taskId==='string'&&/^workflow\.task\.[a-f0-9]{64}$/.test(taskId)?{taskId}:{}),
     phase:'record-completion',retryCount:attempt,waitMs:sqliteBusyRetryWaitMs}));
    throw error;
   }
   await sleep(sqliteBusyRetryWaitMs);
  }
 }
}

/** Complete or fail an exact running claim without leaving a PDS saving stage.
 * Older isolated ledgers have no additive reservation column.
 */
export const saveReservationClearSql=db=>db.prepare('PRAGMA table_info(workflow_tasks)').all()
 .some(column=>column.name==='save_started_at')?',save_started_at=NULL':'';

/** A transport failed before PDS claimed saving: settle only the still exact,
 * unreserved attempt. A live or abandoned reservation is owned by PDS. */
export function failUnreservedSave(db,{taskId,officialRequestId,claimStamp,workerType}){
 if(!saveReservationClearSql(db))return false;
 return db.prepare(`UPDATE workflow_tasks SET status='failed',updated_at=?
 WHERE task_id=? AND official_request_id=? AND worker_type=? AND updated_at=?
 AND status='running' AND result_ref IS NULL AND save_started_at IS NULL`)
 .run(Math.max(Date.now(),claimStamp+1),taskId,officialRequestId,workerType,claimStamp).changes===1;
}
