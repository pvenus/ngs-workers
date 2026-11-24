/** Official append-only prop motion lane, one local RGB I2V call. */
import {DatabaseSync} from 'node:sqlite';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {propAnimationType,propAnimationSchema,validatePropAnimation,exactProp,propHash} from './vendor/prop-composite/prop-generate-envelope.mjs';
import {buildPropGeneratePlan,exactPropTask,propKeys,propTaskId} from './vendor/prop-composite/prop-generate-plan.mjs';
import {assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';
import {png,maxImageBytes} from './i2v-worker.mjs';
import {propI2VRGBStage,propStagePath,propStageCapability} from './prop-i2v-rgb-stage.mjs';

const sql=`r.project_id='binari' AND r.state='planned' AND r.reason IS NULL
 AND r.request_type=? AND r.schema_version=? AND t.worker_type='animation' AND t.task_type=? AND t.task_key=?
 AND t.depends_on_task_id IS NULL AND t.result_ref IS NULL`;
const ids=[propAnimationType,propAnimationSchema,propAnimationType+'.raw',propKeys.motion];
const require=(ok,code='PROP_I2V_CLAIM_BINDING')=>{if(!ok)throw Error(code);};
const labelCode=e=>typeof e?.message==='string'&&/^[A-Z0-9_]{1,100}$/u.test(e.message)?e.message:'PROP_I2V_UNCONFIRMED';
function tx(db,fn){
 db.exec('BEGIN IMMEDIATE');
 try{const result=fn();db.exec('COMMIT');return result;}
 catch(e){db.exec('ROLLBACK');throw e;}
}
export function choosePropAnimation(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  return db.prepare(`SELECT t.task_id AS taskId,r.official_request_id AS officialRequestId
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) WHERE ${sql} AND ${workflowTaskEligibleSql('t')}
   ORDER BY r.registered_at,r.official_request_id LIMIT 1`).get(...ids)??null;
 }finally{db.close();}
}
function candidate(db,t,stamp){
 const running=stamp!==undefined;
 const row=db.prepare(`SELECT t.*,r.payload,r.payload_sha256,r.registered_at
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) WHERE ${sql}
   AND t.task_id=? AND t.official_request_id=? AND ${running?"t.status='running' AND t.updated_at=?":workflowTaskEligibleSql('t')}`)
  .get(...ids,t.taskId,t.officialRequestId,...(running?[stamp]:[]));
 if(!row)return null;
 const p=JSON.parse(row.payload);
 require(validatePropAnimation(p,'binari')===row.payload&&canonical(p)===row.payload&&sha(row.payload)===row.payload_sha256);
 const expected=buildPropGeneratePlan({officialRequestId:t.officialRequestId,requestType:propAnimationType,payload:p});
 const actual=db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 5').all(t.officialRequestId);
 require(exactPropTask(row,expected[0])&&actual.length===expected.length&&
  expected.every(task=>actual.some(v=>exactPropTask(v,task))));
 return {p,row};
}
export function claimPropAnimation(db,t){
 require(typeof t.officialRequestId==='string'&&t.officialRequestId.length===81&&
  /^request\.official\.[a-f0-9]{64}$/u.test(t.officialRequestId)&&t.taskId===propTaskId(t.officialRequestId,propKeys.motion));
 return tx(db,()=>{
  const b=candidate(db,t);
  if(!b)return null;
  const claimStamp=Math.max(Date.now(),b.row.updated_at+1);
  require(Number.isSafeInteger(claimStamp)&&claimStamp<Number.MAX_SAFE_INTEGER&&Number.isSafeInteger(b.row.registered_at)&&
   b.row.registered_at>=0&&b.row.registered_at<=claimStamp);
  require(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
   WHERE task_id=? AND official_request_id=? AND updated_at=? AND result_ref IS NULL AND ${workflowTaskEligibleSql()}`)
   .run(claimStamp,t.taskId,t.officialRequestId,b.row.updated_at).changes===1);
  return {...b,claim:{taskId:t.taskId,officialRequestId:t.officialRequestId,projectId:'binari',payloadSha256:b.row.payload_sha256,claimStamp}};
 });
}
export const propAnimationPorts=local=>({
 async ready(signal){
  const [stage,pack]=await Promise.all([
   local(propStagePath,{signal,maxJsonBytes:4096}),
   local('/v1/props/ownership/workflow-package',{signal,maxJsonBytes:4096}),
  ]);
  return exactProp(stage,['enabled','schemaVersion'])&&stage.enabled===true&&stage.schemaVersion===propStageCapability&&
   exactProp(pack,['enabled','schemaVersion','modes'])&&pack.enabled===true&&pack.schemaVersion==='workflow.prop-package-write.v1'&&
   Array.isArray(pack.modes)&&pack.modes.length===2&&pack.modes[0]==='still'&&pack.modes[1]==='append-animation';
 },
 read:(claim,signal)=>local(propStagePath+'/read',{body:{claim},signal,maxJsonBytes:4096}),
 asset:(claim,signal)=>local(propStagePath+'/asset',{body:{claim,purpose:'input',index:null},signal,binary:true,maxBinaryBytes:maxImageBytes}),
 write:(claim,artifact)=>local(propStagePath,{body:{claim,artifact},timeout:180000,maxJsonBytes:4096}),
});
export async function executePropAnimation(db,t,signal,{ports,renderer,
 makeDir=()=>mkdtemp(join(tmpdir(),'workflow-prop-i2v-')),cleanup=dir=>rm(dir,{recursive:true,force:true})}){
 if(signal.aborted)return {state:'stale',taskId:t.taskId};
 const found=claimPropAnimation(db,t);
 if(!found)return {state:'stale',taskId:t.taskId};
 const {claim,p,row}=found;
 const current=()=>{
  const v=candidate(db,t,claim.claimStamp);
  require(v&&['payload','task_input','payload_sha256','registered_at'].every(k=>row[k]===v.row[k]),'PROP_I2V_CLAIM_CHANGED');
 };
 let dir,stage='read',result,unknownStop=false;
 try{
  const r=await ports.read(claim,signal),media=r?.media;
  require(exactProp(r,['package','media','motionPrompt'])&&canonical(r.package)===canonical(p.source)&&r.motionPrompt===p.prompt&&
   exactProp(media,['assetId','sha256','bytes','width','height','mediaType','frameDurationsMs'])&&propHash(media.sha256)&&
   media.assetId===media.sha256+'.png'&&media.mediaType==='image/png'&&Array.isArray(media.frameDurationsMs)&&media.frameDurationsMs.length===0&&
   Number.isSafeInteger(media.bytes)&&media.bytes>=57&&media.bytes<=maxImageBytes&&
   ['width','height'].every(x=>Number.isSafeInteger(media[x])&&media[x]>=12&&media[x]<=2048),'PROP_I2V_SOURCE_BINDING');
  const source=await ports.asset(claim,signal),shape=png(source,maxImageBytes);
  require(source.length===media.bytes&&sha(source)===media.sha256&&shape.width===media.width&&shape.height===media.height,'PROP_I2V_SOURCE_BYTES');
  dir=await makeDir();
  signal.throwIfAborted();tx(db,current);
  stage='render';
  const output=await renderer.render({dir,source,motionPrompt:p.prompt,signal});
  stage='validate';
  const {artifact,resultRef}=propI2VRGBStage(output,{...t,propId:p.propId,sourceSha256:media.sha256,detailCanvas:shape});
  signal.throwIfAborted();tx(db,current);
  stage='write';
  const ack=await ports.write(claim,artifact);
  require(exactProp(ack,['ok','resultRef'])&&ack.ok===true&&ack.resultRef===resultRef,'PROP_I2V_STAGE_ACK');
  stage='commit';
  tx(db,()=>{
   current();
   require(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
    WHERE task_id=? AND official_request_id=? AND status='running' AND result_ref IS NULL AND updated_at=?`)
    .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),t.taskId,t.officialRequestId,claim.claimStamp).changes===1,
    'PROP_I2V_COMPLETION_CHANGED');
  });
  result={state:'completed',taskId:t.taskId,resultRef,alpha:'none',frameCount:artifact.manifest.frames.length,renderedFrameCount:output.frames.length};
 }catch(e){
  unknownStop=stage==='render'&&e?.rendererJoined!==true;
  let failed=false;
  if(!['write','commit'].includes(stage)&&!unknownStop){
   try{failed=tx(db,()=>{current();return markWorkflowTaskFailed(db,{...claim,workerType:'animation'});});}catch{}
  }
  result={state:failed?'failed':'requires_reconciliation',taskId:t.taskId,stage,code:labelCode(e),
   ...(stage==='render'?{rendererStop:unknownStop?'unknown':'confirmed'}:{})};
 }finally{
  if(dir&&!unknownStop)try{await cleanup(dir);}catch{result={...result,cleanup:'pending'};}
 }
 return result;
}
