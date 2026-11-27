import {fileUpdateType,fileUpdateSchema,fileUpdateTaskKey,fileUpdateRef,validFileUpdate} from './file-update-envelope.mjs';
/** Metadata-only file worker. No frame reads/writes and no model or encoder imports. */
import {DatabaseSync} from 'node:sqlite';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {exact} from './vendor/planner-f24d23a/planner.mjs';
import {assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskMaxRetries,workflowTaskRetrySetSql} from './workflow-ready.mjs';
import {PlaybackRevisionConflict} from './local-ports.mjs';

export const playbackType='cinematic.playback.update',playbackSchema='ngs.cinematic-playback-update.v1';
export const playbackKey='cinematic_playback_update';
const projectId='binari',workerType='file';
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
export const cinematicRemoveType='cinematic.sequences.remove', propMotionRemoveType='prop.animation.remove';
const slug=v=>typeof v==='string'&&v.length>=2&&v.length<=80&&/^[a-z0-9]+(?:[-._][a-z0-9]+)*$/u.test(v);
const hash=v=>typeof v==='string'&&v.length===64&&/^[a-f0-9]{64}$/u.test(v);
const checked=(v,valid)=>Array.isArray(v)&&v.length>=1&&v.length<=30&&
 Array.from(v).every(valid)&&new Set(v).size===v.length;
const mutations=Object.freeze({
 [fileUpdateType]:{
  schema:fileUpdateSchema,key:fileUpdateTaskKey,keys:['target','expected','content'],
  valid:(p,ownId)=>validFileUpdate(p)&&p.target.path!==ownId,
  ref:(v,p)=>v===fileUpdateRef(p),
 },
 [playbackType]:{
  schema:playbackSchema,key:playbackKey,keys:['sequenceId','expectedRevision','playback'],
  valid:(p,ownId)=>official(p.sequenceId)&&p.sequenceId!==ownId&&Number.isSafeInteger(p.expectedRevision)&&
   p.expectedRevision>=0&&p.expectedRevision<Number.MAX_SAFE_INTEGER&&validPlayback(p.playback),
  ref:(v,p)=>v===projectId+'/cinematics/sequences/'+p.sequenceId+'/timeline.json',
 },
 [cinematicRemoveType]:{
  schema:'ngs.cinematic-sequences-remove.v1',key:'cinematic_sequences_remove',keys:['sequenceIds'],
  valid:(p,ownId)=>checked(p.sequenceIds,v=>official(v)&&v!==ownId),
  ref:v=>v===projectId+'/cinematics/sequence-archive.json',
 },
 [propMotionRemoveType]:{
  schema:'ngs.prop-animation-remove.v1',key:'prop_animation_remove',keys:['propId','source','motionIds'],
  valid:p=>slug(p.propId)&&exact(p.source,['revision','sha256'])&&
   Number.isSafeInteger(p.source.revision)&&p.source.revision>=1&&p.source.revision<=Number.MAX_SAFE_INTEGER-2&&
   hash(p.source.sha256)&&checked(p.motionIds,slug),
  ref:v=>typeof v==='string'&&v.length===77&&/^prop\.package\.[a-f0-9]{64}$/u.test(v),
 },
});
export const manifestMutationScopes=Object.freeze(Object.entries(mutations).map(([taskType,m])=>
 Object.freeze({workerType,taskType,taskKey:m.key,predecessorKey:null})));
const taskIdFor=(id,key)=>'workflow.task.'+sha(canonical([id,key]));
function tx(db,fn){
 db.exec('BEGIN IMMEDIATE');
 try{const v=fn();db.exec('COMMIT');return v;}
 catch(e){db.exec('ROLLBACK');throw e;}
}
function candidate(db,t,m,stamp){
 const running=stamp!==undefined;
 const row=db.prepare(`SELECT t.*,r.payload,r.payload_sha256,r.registered_at
  FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) WHERE ${scope}
  AND t.task_id=? AND t.official_request_id=? AND ${running?"t.status='running' AND t.updated_at=?":workflowTaskEligibleSql(db, 't')}`)
  .get(projectId,t.operation,m.schema,workerType,t.operation,m.key,t.taskId,t.officialRequestId,...(running?[stamp]:[]));
 if(!row)return null;
 const p=JSON.parse(row.payload);
 require(exact(p,['schemaVersion','projectId',...m.keys])&&p.schemaVersion===m.schema&&p.projectId===projectId&&
  m.valid(p,t.officialRequestId)&&canonical(p)===row.payload&&sha(row.payload)===row.payload_sha256);
 const input=Object.fromEntries(m.keys.map(k=>[k,p[k]]));
 require(canonical(input)===row.task_input);
 const tasks=db.prepare('SELECT task_id FROM workflow_tasks WHERE official_request_id=? LIMIT 2').all(t.officialRequestId);
 require(tasks.length===1&&tasks[0].task_id===t.taskId);
 return {input,row};
}
function claimMutation(db,t,m){
 require(official(t.officialRequestId)&&t.taskId===taskIdFor(t.officialRequestId,m.key));
 return tx(db,()=>{
  const b=candidate(db,t,m);
  if(!b)return null;
  const {row}=b,claimStamp=Math.max(Date.now(),row.updated_at+1);
  require(Number.isSafeInteger(claimStamp)&&claimStamp<Number.MAX_SAFE_INTEGER&&Number.isSafeInteger(row.registered_at)&&
   row.registered_at>=0&&row.registered_at<=claimStamp);
  require(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
   WHERE task_id=? AND official_request_id=? AND updated_at=? AND result_ref IS NULL AND ${workflowTaskEligibleSql(db)}`)
   .run(claimStamp,t.taskId,t.officialRequestId,row.updated_at).changes===1);
  return {...b,claim:Object.freeze({taskId:t.taskId,officialRequestId:t.officialRequestId,projectId,payloadSha256:row.payload_sha256,claimStamp})};
 });
}

export async function runManifestMutationFromPath({dbPath,officialRequestId,taskId,pds,signal,operation=playbackType}){
 const m=Object.hasOwn(mutations,operation)?mutations[operation]:null;
 const write=(claim)=>operation===fileUpdateType?pds.updateExactMetadata(claim):
  operation===playbackType?pds.updateCinematicPlayback(claim):pds.archiveExactSelection(operation,claim);
 require(m&&(operation===fileUpdateType?typeof pds?.updateExactMetadata==='function':operation===playbackType?typeof pds?.updateCinematicPlayback==='function':typeof pds?.archiveExactSelection==='function'),'CINEMATIC_PLAYBACK_PORT_REQUIRED');
 if(signal?.aborted)return {state:'not_claimable',taskId};
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  const target={officialRequestId,taskId,operation},found=claimMutation(db,target,m);
  if(!found)return {state:'not_claimable',taskId};
  const {row,input,claim}=found;
  const current=()=>{
   const next=candidate(db,target,m,claim.claimStamp);
   require(next&&['payload','task_input','payload_sha256','registered_at'].every(k=>row[k]===next.row[k]),'CINEMATIC_PLAYBACK_CLAIM_CHANGED');
  };
  const verifyCurrent=()=>tx(db,current);
  try{
   verifyCurrent();
   let ack;
   try{ack=await write(claim);}
   catch(e){
    if(operation===fileUpdateType||e instanceof PlaybackRevisionConflict)throw e;
    // Repeat once for a lost ACK, under the same active claim and intent.
    // The endpoint independently confirms the original immutable receipt.
    signal?.throwIfAborted();verifyCurrent();
    ack=await write(claim);
   }
   require(exact(ack,['ok','resultRef'])&&ack.ok===true&&m.ref(ack.resultRef,input),'FILE_WORKER_MANIFEST_ACK_UNCONFIRMED');
   const {resultRef}=ack;
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
    return {state:'failed',taskId,code:operation===fileUpdateType?'FILE_UPDATE_REVISION_CONFLICT':'CINEMATIC_PLAYBACK_REVISION_CONFLICT'};
   }catch{}
   if(operation===fileUpdateType){
    const failed=db.prepare(`UPDATE workflow_tasks SET status='failed',updated_at=?
     WHERE task_id=? AND official_request_id=? AND status='running' AND result_ref IS NULL AND updated_at=?`)
      .run(Math.max(Date.now(),claim.claimStamp+1),taskId,officialRequestId,claim.claimStamp).changes===1;
    return {state:failed?'failed':'not_claimable',taskId,code:'FILE_METADATA_UPDATE_FAILED'};
   }
   return {state:'requires_reconciliation',taskId,code:operation===playbackType?'CINEMATIC_PLAYBACK_RECONCILIATION_REQUIRED':'FILE_WORKER_ARCHIVE_RECONCILIATION_REQUIRED'};
  }
 }finally{db.close();}
}

export const runPlaybackFromPath=runManifestMutationFromPath;
