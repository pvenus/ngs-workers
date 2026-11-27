import {retrySqliteBusy,saveReservationClearSql} from './sqlite-busy-retry.mjs';
/** Existing CPU postprocess lane for the last stage of a pinned background graph. */
import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
import {execFile} from 'node:child_process';
import {readFile,writeFile,mkdtemp,rm,access} from 'node:fs/promises';
import {promisify,TextDecoder} from 'node:util';
import {tmpdir} from 'node:os';
import {isAbsolute,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {fixedMotionHttp} from './preset-video-ports.mjs';
import {nextWorkflowTaskFromPath,assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';

const run=promisify(execFile),entry=fileURLToPath(new URL('./postprocess-pixel.py',import.meta.url));
const family='background.pixel.workflow',schema='ngs.background-pixel-workflow.v1',workerType='postprocess',key='background_pixel_postprocess';
const keys=['background_cinematic_source','background_pixel_generate',key];
const fields=['additionalNotes','color','location','mood','requirements','season','timeOfDay','title'];
const route='/v1/cinematics/ownership/workflow-background-pixel';
const hash=v=>createHash('sha256').update(v).digest('hex');
const digest=/^[a-f0-9]{64}$/u;
const maxPNG=8*1024*1024;
const exact=(v,k)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===k.length&&k.every(x=>Object.hasOwn(v,x));
const need=(ok,c='BACKGROUND_PIXEL_CLAIM_INVALID')=>{if(!ok)throw Error(c);};
const taskID=(oid,k)=>'workflow.task.'+hash(JSON.stringify([oid,k]));
function geom(bytes){
 need(Buffer.isBuffer(bytes)&&bytes.length>57&&bytes.length<=maxPNG&&bytes.subarray(0,8).equals(Buffer.from('89504e470d0a1a0a','hex'))&&
   bytes.toString('ascii',12,16)==='IHDR'&&bytes.readUInt32BE(16)===1536&&bytes.readUInt32BE(20)===1024,'BACKGROUND_PIXEL_GEOMETRY_INVALID');
}
export const nextBackgroundPixel=db=>nextWorkflowTaskFromPath(db,{workerType,taskType:family,taskKey:key,predecessorKey:keys[1]});
export function backgroundPixelPorts({transport=fixedMotionHttp}={}){
 const json=(suffix,body,signal,timeout=8000)=>transport(route+suffix,{body,signal,timeout,limit:4096,type:'application/json'})
   .then(b=>JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(b)));
 return {
  ready:async signal=>{try{const v=await json('',undefined,signal);return exact(v,['enabled','schemaVersion'])&&v.enabled===true&&v.schemaVersion==='workflow.background-pixel-write.v1';}catch{return false;}},
  read:(claim,signal)=>json('/read',{claim},signal),
  asset:(claim,signal)=>transport(route+'/asset',{body:{claim},signal,timeout:20000,limit:maxPNG,type:'image/png'}),
  publish:(claim,artifact)=>json('',{claim,artifact},undefined,120000),
  reconcile:claim=>json('/reconcile',{claim},undefined,12000),
 };
}
export async function createBackgroundPixelRenderer({configPath}){
 const cfg=JSON.parse(await readFile(configPath,'utf8')),py=cfg.FRAME_ALPHA_PYTHON;
 need(typeof py==='string'&&isAbsolute(py),'BACKGROUND_PIXEL_RUNTIME_UNAVAILABLE');
 await Promise.all([access(py),access(entry)]);
 return async (bytes,sha,signal)=>{
  const root=await mkdtemp(join(tmpdir(),'binari-background-pixel-'));
  try{
   await writeFile(join(root,'source.png'),bytes,{mode:0o600});
   await run(py,['-I','-B',entry,root,'background',sha],{signal,timeout:90000,maxBuffer:16384});
   return await readFile(join(root,'pixel.png'));
  }finally{await rm(root,{recursive:true,force:true});}
 };
}
function tx(db,f){
 db.exec('BEGIN IMMEDIATE');try{const v=f();db.exec('COMMIT');return v;}catch(e){db.exec('ROLLBACK');throw e;}
}
export async function runBackgroundPixel({dbPath,officialRequestId,taskId,pds,render,signal}){
 need(/^request\.official\.[a-f0-9]{64}$/u.test(officialRequestId)&&taskId===taskID(officialRequestId,key));
 assertDatabasePath(dbPath);const db=new DatabaseSync(dbPath);
 let claim,sent=false,stage='claim';
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000');protectSchema(db);
  const input=tx(db,()=>{
   const r=db.prepare("SELECT * FROM workflow_runs WHERE official_request_id=? AND request_type=? AND schema_version=? AND project_id='binari' AND state='planned' AND reason IS NULL").get(officialRequestId,family,schema);
   if(!r)return null;
   const tasks=db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 4').all(officialRequestId);
   const values=JSON.parse(r.payload);
   need(exact(values,[...fields,'projectId','schemaVersion'])&&values.projectId==='binari'&&values.schemaVersion===schema&&hash(r.payload)===r.payload_sha256);
   const inp=Object.fromEntries(fields.map(k=>[k,values[k]])),wire=JSON.stringify(inp);
   need(tasks.length===3&&keys.every((k,i)=>{
    const t=tasks.find(x=>x.task_key===k);
    return t&&t.task_id===taskID(officialRequestId,k)&&t.task_type===family&&t.worker_type===(i===2?workerType:'image')&&
      t.depends_on_task_id===(i?taskID(officialRequestId,keys[i-1]):null)&&t.task_input===wire&&
      (i===2||t.status==='completed'&&/^binari\/cinematics\/backgrounds\/[a-f0-9]{64}\.json$/u.test(t.result_ref));
   }));
   const row=tasks.find(t=>t.task_id===taskId),claimStamp=Math.max(Date.now(),row.updated_at+1);
   const change=db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
    WHERE task_id=? AND updated_at=? AND result_ref IS NULL AND ${workflowTaskEligibleSql(db)}`).run(claimStamp,taskId,row.updated_at);
   if(change.changes!==1)return null;
   claim={officialRequestId,taskId,claimStamp,projectId:'binari',payloadSha256:r.payload_sha256};
   return inp;
  });
  if(!input)return {state:'not_claimable',taskId};
  const alive=()=>need(db.prepare("SELECT 1 ok FROM workflow_tasks WHERE task_id=? AND updated_at=? AND status='running' AND result_ref IS NULL").get(taskId,claim.claimStamp));
  stage='read';const bound=await pds.read(claim,signal);
  need(exact(bound,[...fields,'sourceSha256'])&&fields.every(k=>bound[k]===input[k])&&digest.test(bound.sourceSha256),'BACKGROUND_PIXEL_BOUND_INPUT');
  const source=await pds.asset(claim,signal);geom(source);need(hash(source)===bound.sourceSha256,'BACKGROUND_PIXEL_SOURCE_CHANGED');
  stage='pixel-correct';const png=await render(source,bound.sourceSha256,signal);geom(png);
  const sha256=hash(png),resultRef='binari/cinematics/backgrounds/'+sha256+'.json';
  need(sha256!==bound.sourceSha256,'BACKGROUND_PIXEL_RESULT_EQUALS_SOURCE');
  const artifact={sha256,bytes:png.length,width:1536,height:1024,base64:png.toString('base64')};
  signal?.throwIfAborted();alive();stage='publish';sent=true;
  let proof;
  for(let i=0;i<2&&!proof;i++){
   try{const result=await pds.publish(claim,artifact);if(exact(result,['ok','resultRef'])&&result.ok===true&&result.resultRef===resultRef)proof=true;}catch{}
  }
  if(!proof){
   const r=await pds.reconcile(claim);
   if(exact(r,['state'])&&r.state==='absent_fenced'){sent=false;throw Error('BACKGROUND_PIXEL_WRITE_ABSENT');}
   need(exact(r,['state','resultRef'])&&r.state==='published'&&r.resultRef===resultRef,'BACKGROUND_PIXEL_WRITE_UNCONFIRMED');
  }
  await retrySqliteBusy(()=>{
   tx(db,()=>{alive();need(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?${saveReservationClearSql(db)} WHERE task_id=? AND updated_at=? AND status='running' AND result_ref IS NULL`)
    .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),taskId,claim.claimStamp).changes===1);});
  },taskId);
  return {state:'completed',taskId,resultRef,phase:'publish'};
 }catch(e){
  if(claim&&!sent&&markWorkflowTaskFailed(db,{...claim,workerType}))return {state:'failed',taskId,phase:stage,code:'BACKGROUND_PIXEL_FAILED'};
  return {state:sent?'requires_reconciliation':'not_claimable',taskId,phase:stage,code:sent?'BACKGROUND_PIXEL_WRITE_UNCONFIRMED':'BACKGROUND_PIXEL_CLAIM_INVALID'};
 }finally{db.close();}
}
