import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {access,readFile,writeFile,mkdtemp,rm} from 'node:fs/promises';
import {promisify} from 'node:util';
import {tmpdir} from 'node:os';
import {join,isAbsolute} from 'node:path';
import {fileURLToPath} from 'node:url';
import {fixedMotionHttp} from './preset-video-ports.mjs';
import {nextWorkflowTaskFromPath,assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';
import {retrySqliteBusy} from './sqlite-busy-retry.mjs';
const type='cinematic.source.crop',schema='ngs.cinematic-source-crop.v1',key='cinematic_source_crop',workerType='postprocess';
const route='/v1/cinematics/ownership/workflow-source-crop',max=8*1024*1024;
const fields=['bottom','height','left','right','sourceSha256','title','top','width'];
const sha=b=>createHash('sha256').update(b).digest('hex');
const same=(a,b)=>a&&b&&Object.keys(a).length===Object.keys(b).length&&Object.keys(b).every(k=>a[k]===b[k]);
const need=(v,code)=>{if(!v)throw Error(code??'CINEMATIC_CROP_CLAIM_INVALID');};
const tx=(db,f)=>{db.exec('BEGIN IMMEDIATE');try{const r=f();db.exec('COMMIT');return r;}catch(e){db.exec('ROLLBACK');throw e;}};
const png=(b,w,h)=>Buffer.isBuffer(b)&&b.length>=57&&b.length<=max&&
 b.subarray(0,16).equals(Buffer.from('89504e470d0a1a0a0000000d49484452','hex'))&&b.readUInt32BE(16)===w&&b.readUInt32BE(20)===h;
export const nextCinematicCrop=db=>nextWorkflowTaskFromPath(db,{workerType,taskType:type,taskKey:key});
export function cinematicCropPorts({transport=fixedMotionHttp}={}){
 const json=async(suffix,body,signal,timeout=8000)=>JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(
  await transport(route+suffix,{body,signal,timeout,limit:4096,type:'application/json'})));
 return {
  ready:async signal=>{try{return same(await json('',undefined,signal),{enabled:true,schemaVersion:'workflow.cinematic-source-crop.v1'});}catch{return false;}},
  read:(claim,signal)=>json('/read',{claim},signal),
  asset:(claim,signal)=>transport(route+'/asset',{body:{claim},signal,timeout:20000,limit:max,type:'image/png'}),
  publish:(claim,artifact)=>json('',{claim,artifact},undefined,45000),
 };
}
export async function createCinematicCropRenderer({configPath}){
 const {FRAME_ALPHA_PYTHON:py}=JSON.parse(await readFile(configPath,'utf8'));
 need(typeof py==='string'&&isAbsolute(py),'CINEMATIC_CROP_RUNTIME');
 const entry=fileURLToPath(new URL('./cinematic-crop.py',import.meta.url));
 await Promise.all([access(entry),promisify(execFile)(py,['-I','-B','-c','from PIL import Image'],{timeout:6000})]);
 return async (raw,input,signal)=>{
  const root=await mkdtemp(join(tmpdir(),'ngs-cinematic-crop-'));
  try{
   const source=join(root,'source.png'),result=join(root,'crop.png');
   await writeFile(source,raw,{mode:0o600});
   await promisify(execFile)(py,['-I','-B',entry,source,result,...['width','height','left','top','right','bottom'].map(k=>String(input[k]))],
    {signal,timeout:30000,maxBuffer:8192});
   return await readFile(result);
  }finally{await rm(root,{recursive:true,force:true});}
 };
}
export async function runCinematicCrop({dbPath,officialRequestId,taskId,pds,render,signal}){
 need(/^request\.official\.[a-f0-9]{64}$/u.test(officialRequestId)&&taskId==='workflow.task.'+sha(JSON.stringify([officialRequestId,key])));
 assertDatabasePath(dbPath);const db=new DatabaseSync(dbPath);
 let claim,phase='claim',sent=false;
 try{
  protectSchema(db);db.exec('PRAGMA busy_timeout=5000');
  const input=await retrySqliteBusy(()=>tx(db,()=>{
   const r=db.prepare("SELECT * FROM workflow_runs WHERE official_request_id=? AND request_type=? AND schema_version=? AND project_id='binari' AND state='planned' AND reason IS NULL").get(officialRequestId,type,schema);
   if(!r)return;
   const t=db.prepare("SELECT * FROM workflow_tasks WHERE task_id=? AND official_request_id=? AND task_type=? AND task_key=? AND worker_type=? AND depends_on_task_id IS NULL AND result_ref IS NULL").get(taskId,officialRequestId,type,key,workerType);
   if(!t)return;
   const wire=JSON.parse(r.payload),inp=Object.fromEntries(fields.map(k=>[k,wire[k]]));
   need(same(wire,{...inp,projectId:'binari',schemaVersion:schema})&&sha(r.payload)===r.payload_sha256&&t.task_input===JSON.stringify(inp));
   const stamp=Math.max(Date.now(),t.updated_at+1);
   if(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
       WHERE task_id=? AND updated_at=? AND result_ref IS NULL AND ${workflowTaskEligibleSql(db)}`).run(stamp,taskId,t.updated_at).changes!==1)return;
   claim={officialRequestId,taskId,projectId:'binari',claimStamp:stamp,payloadSha256:r.payload_sha256};
   return inp;
  }),taskId);
  if(!input)return {state:'not_claimable',taskId};
  phase='read';need(same(await pds.read(claim,signal),input),'CINEMATIC_CROP_BOUND_INPUT');
  const raw=await pds.asset(claim,signal);
  need(png(raw,input.width,input.height)&&sha(raw)===input.sourceSha256,'CINEMATIC_CROP_SOURCE_CHANGED');
  phase='crop';const output=await render(raw,input,signal);
  const width=input.right-input.left,height=input.bottom-input.top;
  need(png(output,width,height),'CINEMATIC_CROP_OUTPUT');
  const sha256=sha(output),resultRef='binari/cinematics/sources/'+sha256+'.json';
  need(sha256!==input.sourceSha256);signal?.throwIfAborted();
  const artifact={sha256,bytes:output.length,width,height,base64:output.toString('base64')};
  phase='publish';sent=true;
  let ack;
  try{ack=await pds.publish(claim,artifact);}catch{}
  const record=db.prepare('SELECT status,result_ref FROM workflow_tasks WHERE task_id=? AND official_request_id=?').get(taskId,officialRequestId);
  if(record?.status==='failed')return {state:'failed',taskId,phase,code:'CINEMATIC_CROP_PUBLISH_FAILED'};
  // PDS owns final success. A lost ACK may only be accepted with the same exact stored result.
  need(record?.status==='completed'&&record.result_ref===resultRef&&(!ack||same(ack,{ok:true,resultRef})),
   'CINEMATIC_CROP_WRITE_UNCONFIRMED');
  return {state:'completed',taskId,resultRef,phase};
 }catch(e){
  if(claim&&!sent&&markWorkflowTaskFailed(db,{...claim,workerType}))return {state:'failed',taskId,phase,code:e.message};
  return {state:sent?'requires_reconciliation':'not_claimable',taskId,phase,code:'CINEMATIC_CROP_UNCONFIRMED'};
 }finally{db.close();}
}
