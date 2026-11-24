/** Metadata-only file worker. No frame reads/writes and no model or encoder imports. */
import {DatabaseSync} from 'node:sqlite';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {exact} from './vendor/planner-f24d23a/planner.mjs';
import {assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskMaxRetries,workflowTaskRetrySetSql} from './workflow-ready.mjs';
import {PlaybackRevisionConflict} from './local-ports.mjs';

export const playbackType='cinematic.playback.update',playbackSchema='ngs.cinematic-playback-update.v1';
export const playbackKey='cinematic_playback_update';
const projectId='binari',workerType='file',inputKeys=['sequenceId','expectedRevision','playback'];
const official=id=>typeof id==='string'&&id.length===81&&/^request\.official\.[a-f0-9]{64}$/u.test(id);
const n=(v,a,b)=>Number.isFinite(v)&&v>=a&&v<=b;
const curve=(list,duration,keys,values)=>Array.isArray(list)&&list.length>=1&&list.length<=128&&Array.from(list).every((k,i)=>
 exact(k,['frame',...keys])&&Number.isSafeInteger(k.frame)&&k.frame<duration&&(i===0?k.frame===0:k.frame>list[i-1].frame)&&values(k));
function validPlayback(v){
 return exact(v,['durationFrames','speedCurve','cameraCurve'])&&Number.isSafeInteger(v.durationFrames)&&n(v.durationFrames,1,1440)&&
  curve(v.speedCurve,v.durationFrames,['rate'],k=>n(k.rate,0,4))&&
  curve(v.cameraCurve,v.durationFrames,['zoom','x','y'],k=>n(k.zoom,1,3)&&n(k.x,0,1)&&n(k.y,0,1));
}
const require=(ok,code='CINEMATIC_PLAYBACK_CLAIM_BINDING')=>{if(!ok)throw Error(code);};
const scope=`r.state='planned' AND r.reason IS NULL AND r.project_id=? AND r.request_type=? AND r.schema_version=?
 AND t.worker_type=? AND t.task_type=? AND t.task_key=? AND t.depends_on_task_id IS NULL AND t.result_ref IS NULL`;
const args=[projectId,playbackType,playbackSchema,workerType,playbackType,playbackKey];
const taskIdFor=id=>'workflow.task.'+sha(canonical([id,playbackKey]));
const refFor=id=>projectId+'/cinematics/sequences/'+id+'/timeline.json';
function tx(db,fn){
 db.exec('BEGIN IMMEDIATE');
 try{const v=fn();db.exec('COMMIT');return v;}
 catch(e){db.exec('ROLLBACK');throw e;}
}
function candidate(db,t,stamp){
 const running=stamp!==undefined;
 const row=db.prepare(`SELECT t.*,r.payload,r.payload_sha256,r.registered_at
  FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) WHERE ${scope}
  AND t.task_id=? AND t.official_request_id=? AND ${running?"t.status='running' AND t.updated_at=?":workflowTaskEligibleSql('t')}`)
  .get(...args,t.taskId,t.officialRequestId,...(running?[stamp]:[]));
 if(!row)return null;
 const p=JSON.parse(row.payload);
 require(exact(p,['schemaVersion','projectId',...inputKeys])&&p.schemaVersion===playbackSchema&&p.projectId===projectId&&
  official(p.sequenceId)&&p.sequenceId!==t.officialRequestId&&Number.isSafeInteger(p.expectedRevision)&&
  p.expectedRevision>=0&&p.expectedRevision<Number.MAX_SAFE_INTEGER&&validPlayback(p.playback)&&
  canonical(p)===row.payload&&sha(row.payload)===row.payload_sha256);
 const input=Object.fromEntries(inputKeys.map(k=>[k,p[k]]));
 require(canonical(input)===row.task_input);
 const tasks=db.prepare('SELECT task_id FROM workflow_tasks WHERE official_request_id=? LIMIT 2').all(t.officialRequestId);
 require(tasks.length===1&&tasks[0].task_id===t.taskId);
 return {input,row};
}
function claimPlayback(db,t){
 require(official(t.officialRequestId)&&t.taskId===taskIdFor(t.officialRequestId));
 return tx(db,()=>{
  const b=candidate(db,t);
  if(!b)return null;
  const {row}=b,claimStamp=Math.max(Date.now(),row.updated_at+1);
  require(Number.isSafeInteger(claimStamp)&&claimStamp<Number.MAX_SAFE_INTEGER&&Number.isSafeInteger(row.registered_at)&&
   row.registered_at>=0&&row.registered_at<=claimStamp);
  require(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
   WHERE task_id=? AND official_request_id=? AND updated_at=? AND result_ref IS NULL AND ${workflowTaskEligibleSql()}`)
   .run(claimStamp,t.taskId,t.officialRequestId,row.updated_at).changes===1);
  return {...b,claim:Object.freeze({taskId:t.taskId,officialRequestId:t.officialRequestId,projectId,payloadSha256:row.payload_sha256,claimStamp})};
 });
}

export async function runPlaybackFromPath({dbPath,officialRequestId,taskId,pds,signal}){
 require(typeof pds?.updateCinematicPlayback==='function','CINEMATIC_PLAYBACK_PORT_REQUIRED');
 if(signal?.aborted)return {state:'not_claimable',taskId};
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  const target={officialRequestId,taskId},found=claimPlayback(db,target);
  if(!found)return {state:'not_claimable',taskId};
  const {row,input,claim}=found;
  const resultRef=refFor(input.sequenceId);
  const current=()=>{
   const next=candidate(db,target,claim.claimStamp);
   require(next&&['payload','task_input','payload_sha256','registered_at'].every(k=>row[k]===next.row[k]),'CINEMATIC_PLAYBACK_CLAIM_CHANGED');
  };
  const verifyCurrent=()=>tx(db,current);
  const write=()=>pds.updateCinematicPlayback(claim);
  try{
   verifyCurrent();
   let ack;
   try{ack=await write();}
   catch(e){
    if(e instanceof PlaybackRevisionConflict)throw e;
    // Repeat once for a lost ACK, under the same active claim and intent.
    // The endpoint independently confirms the original immutable receipt.
    signal?.throwIfAborted();verifyCurrent();
    ack=await write();
   }
   require(exact(ack,['ok','resultRef'])&&ack.ok===true&&ack.resultRef===resultRef,'CINEMATIC_PLAYBACK_ACK_UNCONFIRMED');
   tx(db,()=>{
    current();
    require(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
     WHERE task_id=? AND official_request_id=? AND status='running' AND result_ref IS NULL AND updated_at=?`)
     .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),taskId,officialRequestId,claim.claimStamp).changes===1);
   });
   return {state:'completed',taskId,resultRef};
  }catch(e){
   if(e instanceof PlaybackRevisionConflict)try{
    tx(db,()=>{
     current();
     require(db.prepare(`UPDATE workflow_tasks SET status='failed',retry_count=?,updated_at=?
      WHERE task_id=? AND official_request_id=? AND status='running' AND result_ref IS NULL AND updated_at=?`)
      .run(workflowTaskMaxRetries,Math.max(Date.now(),claim.claimStamp+1),taskId,officialRequestId,claim.claimStamp).changes===1);
    });
    return {state:'failed',taskId,code:'CINEMATIC_PLAYBACK_REVISION_CONFLICT'};
   }catch{}
   return {state:'requires_reconciliation',taskId,code:'CINEMATIC_PLAYBACK_RECONCILIATION_REQUIRED'};
  }
 }finally{db.close();}
}
