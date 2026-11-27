import {saveReservationClearSql} from './sqlite-busy-retry.mjs';
/** One approved cinematic request, one native RGB inference, one claim-bound MP4 publication. */
import {DatabaseSync} from 'node:sqlite';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';
import {png,maxImageBytes} from './i2v-worker.mjs';
import {createCutsceneRenderer,MAX_CUTSCENE_MP4_BYTES,validCutscenePrompt} from './cutscene-i2v.mjs';

export const cinematicRequestType='cinematic.generate',cinematicRequestSchema='ngs.cinematic-generate.v1';
export const cinematicTaskKey='cinematic_render',cinematicTaskType='cinematic.generate.render';
const cinematicPath='/v1/cinematics/ownership/workflow', cinematicCapability='workflow.cinematic-write.v1';
const projectId='binari',workerType='animation';
const oid=/^request\.official\.[a-f0-9]{64}$/u,digest=/^[a-f0-9]{64}$/u;
const hash=v=>typeof v==='string'&&v.length===64&&digest.test(v);
const exact=(v,names)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===names.length&&names.every(n=>Object.hasOwn(v,n));
const require=(ok,code='CINEMATIC_CLAIM_BINDING')=>{if(!ok)throw Error(code);};
const failureCode=e=>typeof e?.message==='string'&&/^[A-Z0-9_]{1,100}$/u.test(e.message)?e.message:'CINEMATIC_UNCONFIRMED';

const scope=`r.state='planned' AND r.reason IS NULL AND r.project_id=? AND r.request_type=? AND r.schema_version=?
 AND t.worker_type=? AND t.task_type=? AND t.task_key=? AND t.depends_on_task_id IS NULL AND t.result_ref IS NULL`;
const binding=[projectId,cinematicRequestType,cinematicRequestSchema,workerType,cinematicTaskType,cinematicTaskKey];
const taskIdFor=id=>'workflow.task.'+sha(canonical([id,cinematicTaskKey]));
const refFor=id=>projectId+'/cinematics/results/'+id+'.json';
function tx(db,fn){
 db.exec('BEGIN IMMEDIATE');
 try{const value=fn();db.exec('COMMIT');return value;}
 catch(e){db.exec('ROLLBACK');throw e;}
}

export function chooseCinematic(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  return db.prepare(`SELECT t.task_id AS taskId,r.official_request_id AS officialRequestId
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) WHERE ${scope} AND ${workflowTaskEligibleSql(db, 't')}
   ORDER BY r.registered_at,r.official_request_id,t.task_id LIMIT 1`).get(...binding)??null;
 }finally{db.close();}
}
function candidate(db,t,stamp){
 const running=stamp!==undefined;
 const row=db.prepare(`SELECT t.*,r.payload,r.payload_sha256,r.registered_at
  FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) WHERE ${scope}
  AND t.task_id=? AND t.official_request_id=? AND ${running?"t.status='running' AND t.updated_at=?":workflowTaskEligibleSql(db, 't')}`)
  .get(...binding,t.taskId,t.officialRequestId,...(running?[stamp]:[]));
 if(!row)return null;
 const p=JSON.parse(row.payload);
 require(exact(p,['schemaVersion','projectId','sourceSha256','motionPrompt'])&&
  p.schemaVersion===cinematicRequestSchema&&p.projectId===projectId&&hash(p.sourceSha256)&&validCutscenePrompt(p.motionPrompt)&&
  canonical(p)===row.payload&&sha(row.payload)===row.payload_sha256&&
  canonical({sourceSha256:p.sourceSha256,motionPrompt:p.motionPrompt})===row.task_input);
 const only=db.prepare('SELECT task_id FROM workflow_tasks WHERE official_request_id=? LIMIT 2').all(t.officialRequestId);
 require(only.length===1&&only[0].task_id===t.taskId);
 return {p,row};
}

export function claimCinematic(db,t){
 require(typeof t?.officialRequestId==='string'&&t.officialRequestId.length===81&&oid.test(t.officialRequestId)&&t.taskId===taskIdFor(t.officialRequestId));
 return tx(db,()=>{
  const found=candidate(db,t);
  if(!found)return null;
  const {row}=found;
  const claimStamp=Math.max(Date.now(),row.updated_at+1);
  require(Number.isSafeInteger(claimStamp)&&claimStamp<Number.MAX_SAFE_INTEGER&&Number.isSafeInteger(row.registered_at)&&
   row.registered_at>=0&&row.registered_at<=claimStamp);
  require(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
   WHERE task_id=? AND official_request_id=? AND updated_at=? AND result_ref IS NULL AND ${workflowTaskEligibleSql(db)}`)
   .run(claimStamp,t.taskId,t.officialRequestId,row.updated_at).changes===1);
  return {...found,claim:{taskId:t.taskId,officialRequestId:t.officialRequestId,projectId,payloadSha256:row.payload_sha256,claimStamp}};
 });
}

export const cinematicPorts=local=>({
 async ready(signal){
  const v=await local(cinematicPath,{signal,maxJsonBytes:4096});
  return exact(v,['enabled','schemaVersion'])&&v.enabled===true&&v.schemaVersion===cinematicCapability;
 },
 read:(claim,signal)=>local(cinematicPath+'/read',{body:{claim},signal,maxJsonBytes:4096}),
 asset:(claim,signal)=>local(cinematicPath+'/asset',{body:{claim},signal,binary:true,maxBinaryBytes:maxImageBytes}),
 // Once dispatched, an ambiguous response cannot be taken as a generation failure.
 publish:(claim,artifact)=>local(cinematicPath,{body:{claim,artifact},timeout:180000,maxJsonBytes:4096}),
});

function mp4Artifact(v,sourceSha256){
 const size=v?.bytes;
 require(Buffer.isBuffer(size)&&size.length>=24&&size.length<=MAX_CUTSCENE_MP4_BYTES&&size.toString('ascii',4,8)==='ftyp'&&
  hash(v.sha256)&&sha(size)===v.sha256&&v.sourceSha256===sourceSha256&&v.mimeType==='video/mp4'&&v.fps===24&&v.frameCount===45&&
  [v.width,v.height].every(n=>Number.isSafeInteger(n)&&n>=128&&n<=512&&n%32===0),'CINEMATIC_MP4_BINDING');
 return {sourceSha256,sha256:v.sha256,bytes:size.length,mimeType:v.mimeType,width:v.width,height:v.height,
  fps:v.fps,frameCount:v.frameCount,base64:size.toString('base64')};
}

/** Dependencies are the existing exclusive model slot plus read and publish through PDS. */
export async function executeCinematic(db,t,signal,{ports,runI2V,encode}){
 if(signal.aborted)return {state:'stale',taskId:t.taskId};
 const found=claimCinematic(db,t);
 if(!found)return {state:'stale',taskId:t.taskId};
 const {claim,p,row}=found;
 const current=()=>{
  const next=candidate(db,t,claim.claimStamp);
  require(next&&['payload','task_input','payload_sha256','registered_at'].every(k=>row[k]===next.row[k]),'CINEMATIC_CLAIM_CHANGED');
 };
 let stage='read',unknownStop=false;
 try{
  const r=await ports.read(claim,signal);
  require(exact(r,['sourceSha256','motionPrompt'])&&r.sourceSha256===p.sourceSha256&&r.motionPrompt===p.motionPrompt,'CINEMATIC_SOURCE_BINDING');
  stage='asset';
  const source=await ports.asset(claim,signal);
  png(source,maxImageBytes);
  require(sha(source)===p.sourceSha256,'CINEMATIC_SOURCE_BYTES');
  signal.throwIfAborted();tx(db,current);
  stage='prepare';
  const render=createCutsceneRenderer({encode,renderI2V:async job=>{
   // Actual spawning remains exclusively within the resident's shared model slot.
   try{tx(db,current);signal.throwIfAborted();}
   catch(e){throw Object.assign(new Error(e?.message),{rendererJoined:true});}
   stage='render';
   try{
    const frames=await runI2V(job);
    stage='encode';
    return frames;
   }catch(e){unknownStop=e?.rendererJoined!==true;throw e;}
  }});
  const artifact=mp4Artifact(await render({source,motionPrompt:p.motionPrompt,signal}),p.sourceSha256);
  signal.throwIfAborted();tx(db,current);
  stage='publish';
  const expected=refFor(t.officialRequestId);
  const ack=await ports.publish(claim,artifact);
  require(exact(ack,['ok','resultRef'])&&ack.ok===true&&ack.resultRef===expected,'CINEMATIC_PUBLICATION_UNCONFIRMED');
  stage='commit';
  tx(db,()=>{
   current();
   require(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?${saveReservationClearSql(db)}
    WHERE task_id=? AND official_request_id=? AND status='running' AND result_ref IS NULL AND updated_at=?`)
    .run(expected,Math.max(Date.now(),claim.claimStamp+1),t.taskId,t.officialRequestId,claim.claimStamp).changes===1,
    'CINEMATIC_COMPLETION_CHANGED');
  });
  return {state:'completed',taskId:t.taskId,resultRef:expected,frameCount:artifact.frameCount};
 }catch(e){
  let failed=false;
  if(!unknownStop&&!['publish','commit'].includes(stage)){
   try{failed=tx(db,()=>{current();return markWorkflowTaskFailed(db,{...claim,workerType});});}catch{}
  }
  return {state:failed?'failed':'requires_reconciliation',taskId:t.taskId,stage,code:failureCode(e),
   ...(stage==='render'?{rendererStop:unknownStop?'unknown':'confirmed'}:{})};
 }
}
