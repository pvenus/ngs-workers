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
