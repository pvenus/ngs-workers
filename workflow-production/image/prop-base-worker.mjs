/** Formal static prop image stage: one exact approved design in, one durable RGB/RGBA PNG receipt out. */
import {DatabaseSync} from 'node:sqlite';
import {canonical,sha} from './vendor/prop-composite-v1/envelope.mjs';
import {exactProp as exact,propGenerateType,propGenerateSchema,propRegenerateType,propRegenerateSchema,
 validatePropGenerate,validatePropRegenerate} from './vendor/prop-composite-v1/prop-generate-envelope.mjs';
import {buildPropGeneratePlan,exactPropTask,propKeys,propRef,propTaskId,isPropRef} from './vendor/prop-composite-v1/prop-generate-plan.mjs';
import {assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';
import {validatePropDesign} from './prop-image-design.mjs';

export const propBaseOperations=Object.freeze({
 'prop.generate.base':Object.freeze({type:propGenerateType,schema:propGenerateSchema,validate:validatePropGenerate}),
 'prop.regenerate.base':Object.freeze({type:propRegenerateType,schema:propRegenerateSchema,validate:validatePropRegenerate}),
});
const worker='image', projectId='binari', official=/^request\.official\.[a-f0-9]{64}$/u;
const signature=Buffer.from('89504e470d0a1a0a','hex');
const need=(cond,code='WORKFLOW_PROP_BASE_BINDING_INVALID')=>{if(!cond)throw Error(code);};
const supported=v=>typeof v==='string'&&Object.hasOwn(propBaseOperations,v);
const scope=`r.state='planned' AND r.reason IS NULL AND r.project_id='binari'
 AND t.task_key='prop_base' AND t.worker_type='image' AND t.result_ref IS NULL`;
const parents=`JOIN workflow_tasks d ON d.task_id=t.depends_on_task_id AND d.official_request_id=t.official_request_id
 AND d.task_key='prop_design' AND d.worker_type='document' AND d.status='completed'`;

export function nextPropBaseFromPath(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{
  protectSchema(db);
  return db.prepare(`SELECT t.task_id AS taskId,t.official_request_id AS officialRequestId,t.task_type AS operation
  FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) ${parents}
  WHERE ${scope} AND ${workflowTaskEligibleSql('t')}
   AND ((t.task_type=? AND r.request_type=? AND r.schema_version=? AND d.task_type=?) OR
        (t.task_type=? AND r.request_type=? AND r.schema_version=? AND d.task_type=?))
   AND d.result_ref GLOB 'prop.design-spec.*'
  ORDER BY r.registered_at,t.official_request_id LIMIT 1`)
   .get('prop.generate.base',propGenerateType,propGenerateSchema,'prop.generate.design',
    'prop.regenerate.base',propRegenerateType,propRegenerateSchema,'prop.regenerate.design')??null;
 }finally{db.close();}
}

const currentSql=`SELECT t.*,r.request_type,r.schema_version,r.payload,r.payload_sha256,r.registered_at,
 d.result_ref AS design_ref,d.task_input AS design_input,d.task_type AS design_type,d.updated_at AS design_stamp,
 d.depends_on_task_id AS design_predecessor
 FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) ${parents}
 WHERE ${scope} AND r.request_type=? AND r.schema_version=? AND t.task_type=? AND t.official_request_id=? AND t.task_id=?`;
function readCurrent(db,{operation,officialRequestId,taskId},eligible=false){
 const spec=propBaseOperations[operation];
 return db.prepare(currentSql+(eligible?' AND '+workflowTaskEligibleSql('t'):''))
  .get(spec.type,spec.schema,operation,officialRequestId,taskId);
}
function claimTask(db,request){
 db.exec('BEGIN IMMEDIATE');
 try{
  const row=readCurrent(db,request,true);
  if(!row){db.exec('COMMIT');return null;}
  const {officialRequestId,taskId,operation}=request;
  const payload=JSON.parse(row.payload),spec=propBaseOperations[operation];
  need(spec.validate(payload,projectId)===row.payload&&canonical(payload)===row.payload&&sha(row.payload)===row.payload_sha256&&
   Number.isSafeInteger(row.updated_at)&&row.updated_at>=0&&row.updated_at<Number.MAX_SAFE_INTEGER-1&&
   Number.isSafeInteger(row.registered_at)&&row.registered_at>=0);
  const [design,base]=buildPropGeneratePlan({officialRequestId,requestType:row.request_type,payload});
  need(base.key===propKeys.base&&design.key===propKeys.design&&exactPropTask(row,base)&&
   row.design_input===canonical(design.input)&&row.design_type===design.type&&row.design_predecessor===null&&
   Number.isSafeInteger(row.design_stamp)&&row.design_stamp>=0&&isPropRef(propKeys.design,row.design_ref));
  const claimStamp=Math.max(Date.now(),row.updated_at+1);
  need(row.registered_at<=claimStamp&&row.design_stamp<=claimStamp);
  need(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
   WHERE task_id=? AND official_request_id=? AND worker_type=? AND task_type=? AND task_key=?
    AND updated_at=? AND status=? AND result_ref IS NULL AND ${workflowTaskEligibleSql()}`)
   .run(claimStamp,taskId,officialRequestId,worker,operation,propKeys.base,row.updated_at,row.status).changes===1);
  db.exec('COMMIT');
  return {row,design,request,claim:{officialRequestId,taskId,projectId,payloadSha256:row.payload_sha256,claimStamp}};
 }catch(error){db.exec('ROLLBACK');throw error;}
}
function assertCurrent(db,selected){
 const {row,request,claim}=selected,live=readCurrent(db,request);
 need(live?.status==='running'&&live.updated_at===claim.claimStamp&&
  ['payload','payload_sha256','registered_at','task_input','depends_on_task_id','design_ref','design_input','design_type',
   'design_stamp','design_predecessor'].every(k=>row[k]===live[k]),'WORKFLOW_PROP_BASE_CLAIM_CHANGED');
}
function commit(db,selected,resultRef){
 db.exec('BEGIN IMMEDIATE');
 try{
  assertCurrent(db,selected);
  const {claim}=selected;
  need(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
   WHERE official_request_id=? AND task_id=? AND worker_type=? AND task_type=? AND task_key=?
   AND status='running' AND updated_at=? AND result_ref IS NULL`).run(resultRef,Math.max(Date.now(),claim.claimStamp+1),
    claim.officialRequestId,claim.taskId,worker,selected.request.operation,propKeys.base,claim.claimStamp).changes===1);
  db.exec('COMMIT');
 }catch(error){db.exec('ROLLBACK');throw error;}
}
const text=(s,n)=>typeof s==='string'&&s.isWellFormed()&&s.trim()===s&&s.length>0&&Buffer.byteLength(s)<=n&&
 !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]|\bembedding:/iu.test(s);
const perms=['xPermille','yPermille','widthPermille','heightPermille'];
const box=b=>b===null||(exact(b,perms)&&perms.every(k=>Number.isSafeInteger(b[k])&&b[k]>=0&&b[k]<=1000)&&
 b.widthPermille>=1&&b.heightPermille>=1&&b.xPermille+b.widthPermille<=1000&&b.yPermille+b.heightPermille<=1000);
export function propBaseRenderInput(packet,selected){
 const {claim,design,row}=selected,i=design.input;
 need(exact(packet,['design','context']),'WORKFLOW_PROP_BASE_DESIGN_INVALID');
 const r=packet.design;
 need(exact(r,['officialRequestId','taskId','key','resultRef','artifact','lineage'])&&
  r.officialRequestId===claim.officialRequestId&&r.taskId===design.id&&r.key===propKeys.design&&
  r.resultRef===row.design_ref&&r.lineage===null&&r.resultRef===propRef(propKeys.design,sha(canonical(r.artifact))),
  'WORKFLOW_PROP_BASE_DESIGN_INVALID');
 const a=r.artifact;
 need(exact(a,['schemaVersion','propId','designContextSha256','title','appearancePrompt','motion','motionPrompt','interaction'])&&
  a.schemaVersion==='ngs.prop-design-spec.v1'&&a.propId===i.propId&&a.designContextSha256===i.designContextSha256&&
  typeof a.title==='string'&&a.title.isWellFormed()&&a.title.trim()===a.title&&[...a.title].length>=1&&[...a.title].length<=160&&
  !/[\p{Cc}\u2028\u2029]/u.test(a.title)&&text(a.appearancePrompt,4000)&&a.motion===i.motion&&
  (a.motion==='still'?a.motionPrompt===null:text(a.motionPrompt,2000))&&
  exact(a.interaction,['pixelOpen','detailTrigger'])&&Object.values(a.interaction).every(box),
  'WORKFLOW_PROP_BASE_DESIGN_INVALID');
 const ctx=validatePropDesign(packet.context,{projectId,designContextSha256:i.designContextSha256});
 return {projectId,propId:i.propId,state:'base',lane:'detail',background:'green-screen.v1',intent:a.appearancePrompt,sources:ctx.sources,contextSha256:ctx.contextSha256};
}
function mediaArtifact(buffer){
 need(Buffer.isBuffer(buffer)&&buffer.length>=57&&buffer.length<=16*1024*1024&&buffer.subarray(0,8).equals(signature)&&
  buffer.readUInt32BE(8)===13&&buffer.toString('ascii',12,16)==='IHDR'&&buffer[24]===8&&
  buffer[25]===2&&buffer[26]===0&&buffer[27]===0&&buffer[28]===0,'WORKFLOW_PROP_BASE_PNG_INVALID');
 const width=buffer.readUInt32BE(16),height=buffer.readUInt32BE(20);
 need(width>=12&&width<=2048&&height>=12&&height<=2048,'WORKFLOW_PROP_BASE_PNG_INVALID');
 // RGB may still carry tRNS, and APNG starts with PNG's same header.
 let end=false, data=false;
 for(let pos=8;pos+12<=buffer.length;){
  const size=buffer.readUInt32BE(pos),kind=buffer.toString('ascii',pos+4,pos+8);
  pos+=12+size;
  need(pos<=buffer.length&&kind!=='tRNS'&&kind!=='acTL'&&kind!=='fcTL'&&kind!=='fdAT','WORKFLOW_PROP_BASE_PNG_INVALID');
  if(kind==='IDAT')data=true;
  if(kind==='IEND'){end=size===0&&pos===buffer.length;break;}
 }
 need(data&&end,'WORKFLOW_PROP_BASE_PNG_INVALID');
 return {media:{sha256:sha(buffer),bytes:buffer.length,width,height,mediaType:'image/png'},base64:buffer.toString('base64')};
}
const safe=e=>typeof e?.message==='string'&&/^(?:WORKFLOW_PROP_BASE_|PROP_IMAGE_DESIGN_)[A-Z_]{1,64}$/u.test(e.message)?
 e.message:'WORKFLOW_PROP_BASE_FAILED_BEFORE_PUBLISH';

export async function runPropBaseFromPath({dbPath,taskId,officialRequestId,operation,pds,renderProp,signal}){
 need(typeof officialRequestId==='string'&&officialRequestId.length===81&&official.test(officialRequestId)&&
  supported(operation)&&taskId===propTaskId(officialRequestId,propKeys.base)&&typeof renderProp==='function'&&
  typeof pds?.readPropBase==='function'&&typeof pds?.publishPropBase==='function');
 assertDatabasePath(dbPath);
 if(signal?.aborted)return {state:'stopping'};
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  const selected=claimTask(db,{officialRequestId,taskId,operation});
  if(!selected)return {state:'not_claimable',taskId};
  let sent=false;
  try{
   const input=propBaseRenderInput(await pds.readPropBase(selected.claim,signal),selected);
   signal?.throwIfAborted();assertCurrent(db,selected);
   const artifact=mediaArtifact(await renderProp(input,{signal}));
   signal?.throwIfAborted();assertCurrent(db,selected);
   const resultRef=propRef(propKeys.base,artifact.media.sha256);
   sent=true;
   const receipt=await pds.publishPropBase(selected.claim,artifact);
   need(exact(receipt,['ok','resultRef'])&&receipt.ok===true&&receipt.resultRef===resultRef,'WORKFLOW_PROP_BASE_PUBLICATION_UNCONFIRMED');
   commit(db,selected,resultRef);
   return {state:'completed',taskId,resultRef};
  }catch(error){
   if(!sent){
    try{
     assertCurrent(db,selected);
     if(markWorkflowTaskFailed(db,{...selected.claim,workerType:worker}))return {state:'failed',taskId,code:safe(error)};
    }catch{/* Revoked/uncertain state never becomes a forced failure. */}
   }
   return {state:'requires_reconciliation',taskId,code:sent?'WORKFLOW_PROP_BASE_PUBLICATION_UNCONFIRMED':'WORKFLOW_PROP_BASE_CLAIM_CHANGED'};
  }
 }finally{db.close();}
}
