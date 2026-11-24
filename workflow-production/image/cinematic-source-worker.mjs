/** Official cinematic source lane. Native ImageGen output, no video generation. */
import {DatabaseSync} from 'node:sqlite';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {exact} from './vendor/planner-f24d23a/planner.mjs';
import {assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskRetrySetSql} from './workflow-ready.mjs';
import {nextWorkflowTaskFromPath,markWorkflowTaskFailed} from './workflow-ready.mjs';

export const sourceType='cinematic.source.generate',sourceSchema='ngs.cinematic-source-generate.v1';
export const sourceKey='cinematic_source_generate';
const projectId='binari',workerType='image',inputKeys=['title','prompt'];
const official=id=>typeof id==='string'&&id.length===81&&/^request\.official\.[a-f0-9]{64}$/u.test(id);
const validText=(v,points,max)=>typeof v==='string'&&v.trim()===v&&v.length>0&&v.isWellFormed()&&Array.from(v).length<=points&&Buffer.byteLength(v)<=max&&
 !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(v)&&!(/\bembedding:/iu.test(v));
export const validCinematicSource=v=>exact(v,inputKeys)&&validText(v.title,120,480)&&validText(v.prompt,2000,4000);
const signature=Buffer.from('89504e470d0a1a0a','hex');
export function sourceGeometry(png){
 require(Buffer.isBuffer(png)&&png.length>=57&&png.length<=8*1024*1024&&
  png.subarray(0,8).equals(signature)&&png.readUInt32BE(8)===13&&png.toString('ascii',12,16)==='IHDR'&&
  png.readUInt32BE(16)===1536&&png.readUInt32BE(20)===1024&&png[24]===8&&[2,6].includes(png[25]),
  'CINEMATIC_SOURCE_PNG_INVALID');
 return {width:1536,height:1024};
}
export const nextCinematicSourceFromPath=dbPath=>nextWorkflowTaskFromPath(dbPath,
 {workerType,taskType:sourceType,taskKey:sourceKey,predecessorKey:null});
const require=(ok,code='CINEMATIC_SOURCE_CLAIM_BINDING')=>{if(!ok)throw Error(code);};
const scope=`r.state='planned' AND r.reason IS NULL AND r.project_id=? AND r.request_type=? AND r.schema_version=?
 AND t.worker_type=? AND t.task_type=? AND t.task_key=? AND t.depends_on_task_id IS NULL AND t.result_ref IS NULL`;
const args=[projectId,sourceType,sourceSchema,workerType,sourceType,sourceKey];
const taskIdFor=id=>'workflow.task.'+sha(canonical([id,sourceKey]));
const refFor=id=>projectId+'/cinematics/sources/'+id+'.json';
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
 require(exact(p,['schemaVersion','projectId',...inputKeys])&&p.schemaVersion===sourceSchema&&p.projectId===projectId&&
  canonical(p)===row.payload&&sha(row.payload)===row.payload_sha256);
 const input=Object.fromEntries(inputKeys.map(k=>[k,p[k]]));
 require(validCinematicSource(input)&&canonical(input)===row.task_input);
 const tasks=db.prepare('SELECT task_id FROM workflow_tasks WHERE official_request_id=? LIMIT 2').all(t.officialRequestId);
 require(tasks.length===1&&tasks[0].task_id===t.taskId);
 return {input,row};
}
function claimSource(db,t){
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

export async function runCinematicSourceFromPath({dbPath,officialRequestId,taskId,pds,renderCinematicSource,signal}){
 require(typeof pds?.readCinematicSource==='function'&&typeof pds?.publishCinematicSource==='function'&&typeof renderCinematicSource==='function','CINEMATIC_SOURCE_PORT_REQUIRED');
 if(signal?.aborted)return {state:'not_claimable',taskId};
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  const target={officialRequestId,taskId},found=claimSource(db,target);
  if(!found)return {state:'not_claimable',taskId};
  const {row,input,claim}=found;

  const current=()=>{
   const next=candidate(db,target,claim.claimStamp);
   require(next&&['payload','task_input','payload_sha256','registered_at'].every(k=>row[k]===next.row[k]),'CINEMATIC_SOURCE_CLAIM_CHANGED');
  };
  const verifyCurrent=()=>tx(db,current);
  let sent=false,nativePending=false;
  try{
   verifyCurrent();
   const bound=await pds.readCinematicSource(claim,signal);
   require(validCinematicSource(bound)&&canonical(bound)===canonical(input),'CINEMATIC_SOURCE_READ_MISMATCH');
   nativePending=true;
   const png=await renderCinematicSource(input,{signal});
   nativePending=false;
   const geometry=sourceGeometry(png),sha256=sha(png),resultRef=refFor(sha256);
   const artifact={sha256,bytes:png.length,...geometry,base64:png.toString('base64')};
   signal?.throwIfAborted();verifyCurrent();
   sent=true;
   let ack;
   try{ack=await pds.publishCinematicSource(claim,artifact);}
   catch{verifyCurrent();ack=await pds.publishCinematicSource(claim,artifact);}
   require(exact(ack,['ok','resultRef'])&&ack.ok===true&&ack.resultRef===resultRef,'CINEMATIC_SOURCE_ACK_UNCONFIRMED');
   tx(db,()=>{
    current();
    require(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
     WHERE task_id=? AND official_request_id=? AND status='running' AND result_ref IS NULL AND updated_at=?`)
     .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),taskId,officialRequestId,claim.claimStamp).changes===1);
   });
   return {state:'completed',taskId,resultRef};
  }catch{
   if(!sent&&!nativePending&&markWorkflowTaskFailed(db,{...claim,workerType}))
    return {state:'failed',taskId,code:'CINEMATIC_SOURCE_FAILED_BEFORE_PUBLISH'};
   return {state:'requires_reconciliation',taskId,code:'CINEMATIC_SOURCE_RECONCILIATION_REQUIRED'};
  }
 }finally{db.close();}
}
