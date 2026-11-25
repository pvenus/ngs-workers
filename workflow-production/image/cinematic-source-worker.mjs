/** Official cinematic source lane. Native ImageGen output, no video generation. */
import {DatabaseSync} from 'node:sqlite';
import {setTimeout as delay} from 'node:timers/promises';
import {settleFailure} from './failure-state.mjs';
import {finishPortraitRead,hasPortraitReadOwner} from './portrait-read-owner.mjs';
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
export const cinematicDesignId='category.add.ab33d46cb2f60e8467b4a3a37db9ca3d';
const cinematicDesignTitle='시네마틱 영상 디자인';
const cinematicDesignGroup='group.art-design';
// The canonical Binari Filex namespace stores the game's legacy Master Concept project key.
const masterConceptProjectId='ProjectBS';
function validCinematicDesign(d){
 return exact(d,['categoryId','sha256','markdown'])&&d.categoryId===cinematicDesignId&&
  typeof d.markdown==='string'&&d.markdown.length>0&&validText(d.markdown.trim(),12000,24000)&&
  Buffer.byteLength(d.markdown)<=24000&&d.markdown.startsWith('# '+cinematicDesignTitle+'\n')&&
  d.sha256===sha(d.markdown);
}
export function selectCinematicDesign(snapshot){
 const doc=snapshot?.document,groups=doc?.hierarchy?.groups,archived=doc?.hierarchy?.archivedCategoryIds;
 require(doc?.projectId===masterConceptProjectId&&Array.isArray(doc.sections)&&Array.isArray(groups)&&
  Array.isArray(archived)&&!archived.includes(cinematicDesignId)&&
  groups.every(g=>Array.isArray(g?.categoryIds)),'CINEMATIC_DESIGN_UNAVAILABLE');
 const group=groups.filter(g=>g.id===cinematicDesignGroup);
 require(group.length===1&&group[0].categoryIds.includes(cinematicDesignId)&&
  groups.flatMap(g=>g.categoryIds).filter(id=>id===cinematicDesignId).length===1,'CINEMATIC_DESIGN_UNAVAILABLE');
 const design=doc.sections.filter(x=>x?.id===cinematicDesignId),section=design[0];
 require(design.length===1&&section.title===cinematicDesignTitle&&Array.isArray(section.fields)&&
  section.fields.length===1&&section.fields[0]?.id==='content'&&typeof section.fields[0]?.value==='string',
  'CINEMATIC_DESIGN_UNAVAILABLE');
 const markdown=section.fields[0].value;
 const selected={categoryId:cinematicDesignId,markdown,sha256:sha(markdown)};
 require(validCinematicDesign(selected),'CINEMATIC_DESIGN_UNAVAILABLE');
 return selected;
}
export const validCinematicSourceRenderInput=v=>exact(v,['title','prompt','design'])&&
 validCinematicSource({title:v.title,prompt:v.prompt})&&validCinematicDesign(v.design);
const signature=Buffer.from('89504e470d0a1a0a','hex');
export function sourceGeometry(png){
 const format=Buffer.isBuffer(png)&&png.length>=57&&png.length<=8*1024*1024&&
  png.subarray(0,8).equals(signature)&&png.readUInt32BE(8)===13&&png.toString('ascii',12,16)==='IHDR';
 require(format,'CINEMATIC_SOURCE_PNG_INVALID');
 const width=png.readUInt32BE(16),height=png.readUInt32BE(20);
 if(width!==1536||height!==1024)throw Object.assign(Error('CINEMATIC_SOURCE_PNG_GEOMETRY_INVALID'),{width,height});
 require(png[24]===8&&[2,6].includes(png[25]),'CINEMATIC_SOURCE_PNG_INVALID');
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
  const ownerTracked=hasPortraitReadOwner(db);
  require(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}${ownerTracked?',portrait_read_owner_pid=?':''}
   WHERE task_id=? AND official_request_id=? AND updated_at=? AND result_ref IS NULL AND ${workflowTaskEligibleSql()}`)
   .run(claimStamp,...(ownerTracked?[process.pid]:[]),t.taskId,t.officialRequestId,row.updated_at).changes===1);
  return {...b,ownerTracked,claim:Object.freeze({taskId:t.taskId,officialRequestId:t.officialRequestId,projectId,payloadSha256:row.payload_sha256,claimStamp})};
 });
}

const validAck=(ack,resultRef)=>exact(ack,['ok','resultRef'])&&ack.ok===true&&ack.resultRef===resultRef;
class PublicationAbsentFenced extends Error{}

async function finishPublication({pds,claim,artifact,resultRef,verifyCurrent,signal,reconcileDelays}){
 let lastError;
 // Replay the exact same PNG, not the model. It can finish a partial Filex write.
 for(let i=0;i<2;i++){
  verifyCurrent();
  try{
   const ack=await pds.publishCinematicSource(claim,artifact);
   if(validAck(ack,resultRef))return;
   lastError=Error('CINEMATIC_SOURCE_ACK_UNCONFIRMED');
  }catch(error){lastError=error;}
 }
 if(typeof pds.reconcileCinematicSource!=='function')throw lastError;
 // A lost ACK proves neither absence nor completion. Filex serializes receipt
 // inspection and an attempt fence against the same lock used for publication.
 for(const wait of reconcileDelays){
  if(wait)await delay(wait,undefined,{signal});
  verifyCurrent();
  let evidence;
  try{evidence=await pds.reconcileCinematicSource(claim,signal);}
  catch{continue;}
  if(exact(evidence,['state'])&&evidence.state==='absent_fenced')throw new PublicationAbsentFenced('CINEMATIC_SOURCE_PUBLICATION_ABSENT_FENCED');
  if(exact(evidence,['state','resultRef'])&&evidence.state==='published'){
   require(evidence.resultRef===resultRef,'CINEMATIC_SOURCE_ACK_UNCONFIRMED');return;
  }
 }
 throw lastError;
}

export async function runCinematicSourceFromPath({dbPath,officialRequestId,taskId,pds,renderCinematicSource,signal,
 reconcileDelays=[0,1000,2000,4000,8000,10000,15000,20000]}){
 require(typeof pds?.readCinematicSource==='function'&&typeof pds?.readCinematicConcept==='function'&&typeof pds?.publishCinematicSource==='function'&&typeof renderCinematicSource==='function','CINEMATIC_SOURCE_PORT_REQUIRED');
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
  let sent=false,ownerTracked=found.ownerTracked,stage='read-inputs';
  try{
   verifyCurrent();
   const bound=await pds.readCinematicSource(claim,signal);
   require(validCinematicSource(bound)&&canonical(bound)===canonical(input),'CINEMATIC_SOURCE_READ_MISMATCH');
   stage='read-design';
   const design=selectCinematicDesign(await pds.readCinematicConcept(signal));
   signal?.throwIfAborted();verifyCurrent();
   stage='render';
   const png=await renderCinematicSource({...input,design},{signal});
   stage='validate-png';
   const geometry=sourceGeometry(png),sha256=sha(png),resultRef=refFor(sha256);
   const artifact={sha256,bytes:png.length,...geometry,base64:png.toString('base64')};
   signal?.throwIfAborted();verifyCurrent();
   stage='release-prewrite-owner';
   if(ownerTracked){
    require(await settleFailure(()=>finishPortraitRead(db,claim)),'CINEMATIC_SOURCE_OWNER_CHANGED');
    ownerTracked=false;
   }
   signal?.throwIfAborted();
   stage='publish';
   sent=true;
   await finishPublication({pds,claim,artifact,resultRef,verifyCurrent,signal,reconcileDelays});
   stage='record-completion';
   tx(db,()=>{
    current();
    require(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
     WHERE task_id=? AND official_request_id=? AND status='running' AND result_ref IS NULL AND updated_at=?`)
     .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),taskId,officialRequestId,claim.claimStamp).changes===1);
   });
   return {state:'completed',taskId,resultRef,designSha256:design.sha256};
  }catch(error){
   const allowed=new Set(['CINEMATIC_SOURCE_PNG_INVALID','CINEMATIC_SOURCE_PNG_GEOMETRY_INVALID','CINEMATIC_SOURCE_READ_MISMATCH',
    'CINEMATIC_SOURCE_OWNER_CHANGED','CINEMATIC_SOURCE_ACK_UNCONFIRMED','CINEMATIC_DESIGN_UNAVAILABLE',
    'CINEMATIC_SOURCE_PUBLICATION_ABSENT_FENCED',
    'WORKFLOW_IMAGE_CLI_OUTCOME_UNCONFIRMED','WORKFLOW_IMAGE_CLI_PROCESS_FAILED','WORKFLOW_IMAGE_CLI_RESPONSE_INVALID',
    'WORKFLOW_IMAGE_CLI_REPORTED_BLOCKED','WORKFLOW_IMAGE_CLI_RESULT_REJECTED','WORKFLOW_IMAGE_NATIVE_UNCONFIRMED',
    'WORKFLOW_IMAGE_NATIVE_TIMEOUT','WORKFLOW_IMAGE_CANCELLED','WORKFLOW_IMAGE_OUTPUT_INVALID','DOCUMENT_WORKER_LOCAL_TRANSPORT']);
   const reason=allowed.has(error?.message)?error.message:'UNCLASSIFIED';
   const diagnostic={stage,reason,...(reason==='CINEMATIC_SOURCE_PNG_GEOMETRY_INVALID'&&
    [error.width,error.height].every(n=>Number.isSafeInteger(n)&&n>=0&&n<16384)?{width:error.width,height:error.height}:{})};
   // Native generation only returns private scratch bytes; it never publishes.
   // Any failure before the first storage RPC is safe for the normal retry policy.
   if(!sent||error instanceof PublicationAbsentFenced){
    try{
     const changed=await settleFailure(()=>ownerTracked?finishPortraitRead(db,claim,{failed:true}):markWorkflowTaskFailed(db,{...claim,workerType}));
     if(changed)return {state:'failed',taskId,code:sent?reason:stage==='read-design'?'CINEMATIC_DESIGN_UNAVAILABLE':'CINEMATIC_SOURCE_FAILED_BEFORE_PUBLISH',diagnostic};
     diagnostic.failureTransition='CLAIM_NOT_UPDATED';
    }catch{
     diagnostic.failureTransition='STATE_WRITE_UNCONFIRMED';
    }
   }
   return {state:'requires_reconciliation',taskId,code:'CINEMATIC_SOURCE_RECONCILIATION_REQUIRED',diagnostic};
  }
 }finally{db.close();}
}
