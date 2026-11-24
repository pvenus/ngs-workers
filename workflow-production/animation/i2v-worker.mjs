/** Single still-image I2V stage. No motion preset or implicit alpha. */
import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {mkdtemp,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {crc32,inflateSync} from 'node:zlib';
import {assertDatabasePath,protectSchema,markWorkflowTaskFailed,workflowTaskEligibleSql,workflowTaskRetrySetSql} from './workflow-ready.mjs';

export const i2vType='animation.i2v', i2vSchema='ngs.animation-i2v.v1', i2vKey='animation_i2v';
export const i2vPath='/v1/animations/ownership/workflow-i2v', i2vCapability='workflow.animation-i2v-write.v1';
const worker='animation',project='binari',digest=/^[a-f0-9]{64}$/,official=/^request\.official\.[a-f0-9]{64}$/;
export const maxImageBytes=8*1024*1024,maxFrameBytes=4*1024*1024,maxBundleBytes=40*1024*1024,maxFrames=96;
const sha=b=>createHash('sha256').update(b).digest('hex');
const exact=(o,ks)=>o!==null&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).length===ks.length&&ks.every(k=>Object.hasOwn(o,k));
const validDigest=v=>typeof v==='string'&&v.length===64&&digest.test(v);
const need=(test,code)=>{if(!test)throw Error(code);};
const validPrompt=v=>typeof v==='string'&&v.length<=2000&&v.isWellFormed()&&v===v.trim()&&v.length>0&&
 [...v].length<=1000&&!/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(v);
export const validSourceRef=v=>exact(v,['assetId','sha256'])&&validDigest(v.sha256)&&v.assetId===v.sha256+'.png';
export function i2vTaskId(id){return 'workflow.task.'+sha(JSON.stringify([id,i2vKey]));}
export function i2vResultRef(id){
 need(typeof id==='string'&&id.length===81&&official.test(id),'I2V_REQUEST_ID');
 return project+'/animations/i2v/'+id+'/manifest.json';
}
function validPayload(v){
 return exact(v,['schemaVersion','projectId','sourceRef','motionPrompt'])&&v.schemaVersion===i2vSchema&&
  v.projectId===project&&validSourceRef(v.sourceRef)&&validPrompt(v.motionPrompt);
}
function tx(db,fn){
 db.exec('BEGIN IMMEDIATE');
 try{const r=fn();db.exec('COMMIT');return r;}
 catch(e){db.exec('ROLLBACK');throw e;}
}
const scope=`r.state='planned' AND r.reason IS NULL AND r.request_type=? AND r.schema_version=? AND r.project_id=?
 AND t.worker_type=? AND t.task_type=? AND t.task_key=? AND t.depends_on_task_id IS NULL
 AND ${workflowTaskEligibleSql('t')} AND t.result_ref IS NULL`;
const args=[i2vType,i2vSchema,project,worker,i2vType,i2vKey];
export function chooseI2V(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  return db.prepare(`SELECT t.task_id AS taskId,r.official_request_id AS officialRequestId
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) WHERE ${scope}
   ORDER BY r.registered_at,r.official_request_id LIMIT 1`).get(...args)??null;
 }finally{db.close();}
}
export function claimI2V(db,t){
 const {taskId,officialRequestId}=t;
 i2vResultRef(officialRequestId);
 need(taskId===i2vTaskId(officialRequestId),'I2V_TASK_ID');
 return tx(db,()=>{
  const row=db.prepare(`SELECT t.updated_at,t.status,t.task_input,r.payload,r.payload_sha256
   FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) WHERE ${scope}
   AND t.task_id=? AND t.official_request_id=?`).get(...args,taskId,officialRequestId);
  if(!row)return null;
  const payload=JSON.parse(row.payload),input=JSON.parse(row.task_input);
  need(validPayload(payload)&&row.payload_sha256===sha(row.payload),'I2V_PAYLOAD_BINDING');
  need(exact(input,['sourceRef','motionPrompt'])&&validSourceRef(input.sourceRef)&&
   input.sourceRef.sha256===payload.sourceRef.sha256&&input.motionPrompt===payload.motionPrompt,'I2V_TASK_INPUT');
  const claimStamp=Math.max(Date.now(),row.updated_at+1);
  need(Number.isSafeInteger(claimStamp)&&claimStamp<Number.MAX_SAFE_INTEGER,'I2V_CLAIM_STAMP');
  const updated=db.prepare(`UPDATE workflow_tasks AS t SET status='running',updated_at=?,${workflowTaskRetrySetSql}
   WHERE t.task_id=? AND t.official_request_id=? AND t.worker_type=? AND t.task_type=? AND t.task_key=?
   AND t.status=? AND t.updated_at=? AND t.result_ref IS NULL AND ${workflowTaskEligibleSql('t')}`)
   .run(claimStamp,taskId,officialRequestId,worker,i2vType,i2vKey,row.status,row.updated_at);
  return updated.changes===1?{claim:{taskId,officialRequestId,projectId:project,payloadSha256:row.payload_sha256,claimStamp},input,inputWire:row.task_input}:null;
 });
}
const pngMagic=Buffer.from('89504e470d0a1a0a','hex');
/** A still is bounded and structurally checked. Rendered frames are specifically decoded 8-bit RGB. */
export function png(bytes,limit,{rgb=false}={}){
 need(Buffer.isBuffer(bytes)&&bytes.length>=57&&bytes.length<=limit&&bytes.subarray(0,8).equals(pngMagic),'I2V_PNG_BYTES');
 let n=8,width,height,foundEnd=false,foundIdat=false,lastIdat=false,compressed=[];
 while(n<bytes.length){
  need(n+12<=bytes.length,'I2V_PNG_CHUNK');
  const length=bytes.readUInt32BE(n),end=n+12+length;
  need(end<=bytes.length&&end>n&&bytes.readUInt32BE(end-4)===crc32(bytes.subarray(n+4,end-4)),'I2V_PNG_CHUNK');
  const type=bytes.toString('ascii',n+4,n+8);
  if(type==='IHDR'){
   need(n===8&&length===13,'I2V_PNG_HEADER');
   width=bytes.readUInt32BE(n+8);height=bytes.readUInt32BE(n+12);
   need(width>=1&&height>=1&&width<=4096&&height<=4096&&width*height<=8_388_608,'I2V_PNG_GEOMETRY');
   if(rgb)need(bytes.subarray(n+16,n+21).equals(Buffer.from([8,2,0,0,0])),'I2V_PNG_RGB_REQUIRED');
  }else if(type==='IDAT'){
   need(n>8&&(!foundIdat||lastIdat),'I2V_PNG_DATA');foundIdat=true;
   if(rgb)compressed.push(bytes.subarray(n+8,end-4));
  }else if(type==='IEND'){
   need(length===0&&foundIdat&&end===bytes.length,'I2V_PNG_END');foundEnd=true;
  }else need(type!=='acTL'&&type!=='fcTL'&&type!=='fdAT'&&(!rgb||type!=='tRNS'),'I2V_PNG_STILL_REQUIRED');
  lastIdat=type==='IDAT';n=end;
 }
 need(width&&height&&foundEnd,'I2V_PNG_END');
 if(rgb){
  const row=1+width*3,full=row*height;
  let decoded;
  try{decoded=inflateSync(Buffer.concat(compressed),{maxOutputLength:full});}catch{throw Error('I2V_PNG_DECODE');}
  need(decoded.length===full,'I2V_PNG_DECODE');
  for(let i=0;i<full;i+=row)need(decoded[i]<=4,'I2V_PNG_DECODE');
 }
 return {width,height};
}
export function outputArtifact(output,sourceSha256){
 need(exact(output,['frames','width','height','fps'])&&Array.isArray(output.frames)&&output.frames.length>=2&&
  output.frames.length<=maxFrames&&Number.isInteger(output.fps)&&output.fps>=1&&output.fps<=30&&
  Number.isInteger(output.width)&&Number.isInteger(output.height)&&output.width>=16&&output.width<=1280&&
  output.height>=16&&output.height<=1280,'I2V_RENDER_MANIFEST');
 let total=0;
 const frames=output.frames.map(b=>{
  const size=png(b,maxFrameBytes,{rgb:true});total+=b.length;
  need(size.width===output.width&&size.height===output.height&&total<=maxBundleBytes,'I2V_FRAME_MANIFEST');
  return b.toString('base64');
 });
 return {sourceSha256,fps:output.fps,width:output.width,height:output.height,frames};
}
export const i2vPorts=local=>({
 async ready(signal){
  const v=await local(i2vPath,{signal,maxJsonBytes:4096});
  return exact(v,['enabled','schemaVersion'])&&v.enabled===true&&v.schemaVersion===i2vCapability;
 },
 read:(claim,signal)=>local(i2vPath+'/read',{body:{claim},signal,maxJsonBytes:4096}),
 asset:(claim,assetId,signal)=>local(i2vPath+'/asset',{body:{claim,assetId},signal,binary:true,maxBinaryBytes:maxImageBytes}),
 // Once sent, an uncertain write is reconciled, never re-rendered just because shutdown was requested.
 publish:(claim,artifact)=>local(i2vPath,{body:{claim,artifact},timeout:180000,maxJsonBytes:4096})
});
const safeCode=e=>typeof e?.message==='string'&&/^[A-Z0-9_]{1,120}$/.test(e.message)?e.message:'I2V_UNCONFIRMED';
export async function executeI2V(db,t,signal,{ports,renderer,makeDir=()=>mkdtemp(join(tmpdir(),'workflow-i2v-')),cleanup=dir=>rm(dir,{recursive:true,force:true})}){
 const bound=claimI2V(db,t);
 if(!bound)return {state:'stale',taskId:t.taskId};
 const {claim,input,inputWire}=bound;
 const resultRef=i2vResultRef(claim.officialRequestId);
 let dir,stage='read',unknownStop=false,result;
 try{
  const settings=await ports.read(claim,signal);
  need(exact(settings,['assetId','sourceSha256'])&&settings.assetId===input.sourceRef.assetId&&
   settings.sourceSha256===input.sourceRef.sha256,'I2V_PDS_SOURCE');
  const source=await ports.asset(claim,settings.assetId,signal);
  png(source,maxImageBytes);
  need(sha(source)===settings.sourceSha256,'I2V_PDS_SOURCE_HASH');
  dir=await makeDir();
  signal.throwIfAborted();
  stage='render';
  const output=await renderer.render({dir,source,motionPrompt:input.motionPrompt,signal});
  stage='validate';
  const artifact=outputArtifact(output,settings.sourceSha256);
  signal.throwIfAborted();
  stage='publish';
  const ack=await ports.publish(claim,artifact);
  need(exact(ack,['ok','resultRef'])&&ack.ok===true&&ack.resultRef===resultRef,'I2V_PUBLICATION_UNCONFIRMED');
  stage='commit';
  const done=db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=? WHERE task_id=?
   AND official_request_id=? AND worker_type=? AND task_type=? AND task_key=? AND task_input=? AND depends_on_task_id IS NULL
   AND status='running' AND updated_at=? AND result_ref IS NULL
   AND EXISTS(SELECT 1 FROM workflow_runs r WHERE r.official_request_id=workflow_tasks.official_request_id
   AND r.state='planned' AND r.reason IS NULL AND r.request_type=? AND r.schema_version=? AND r.project_id=? AND r.payload_sha256=?)`)
   .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),claim.taskId,claim.officialRequestId,worker,i2vType,i2vKey,inputWire,claim.claimStamp,i2vType,i2vSchema,project,claim.payloadSha256);
  need(done.changes===1,'I2V_DB_COMPLETION');
  result={state:'completed',taskId:t.taskId,resultRef,frameCount:output.frames.length,alpha:'none'};
 }catch(e){
  unknownStop=stage==='render'&&e?.rendererJoined!==true;
  let failed=false;
  if(!['publish','commit'].includes(stage)&&!unknownStop){
   try{failed=markWorkflowTaskFailed(db,{...claim,workerType:worker});}catch{/* exact status uncertain */}
  }
  result={state:failed?'failed':'requires_reconciliation',taskId:t.taskId,stage,code:safeCode(e),
   ...(stage==='render'?{rendererStop:unknownStop?'unknown':'confirmed'}:{})};
 }finally{
  if(dir&&!unknownStop){
   try{await cleanup(dir);}catch{result={...result,cleanup:'pending'};}
  }
 }
 return result;
}
