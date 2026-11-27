import {retrySqliteBusy,isSqliteBusyExhausted} from './sqlite-busy-retry.mjs';
/** Formal static prop image stage: one exact approved design in, one durable RGB/RGBA PNG receipt out. */
import {DatabaseSync} from 'node:sqlite';
import {canonical,sha} from './vendor/prop-composite-v1/envelope.mjs';
import {exactProp as exact,propGenerateType,propGenerateSchema,propRegenerateType,propRegenerateSchema,
 validatePropGenerate,validatePropRegenerate} from './vendor/prop-composite-v1/prop-generate-envelope.mjs';
import {buildPropGeneratePlan,exactPropTask,propKeys,propRef,propTaskId,isPropRef} from './vendor/prop-composite-v1/prop-generate-plan.mjs';
import {assertDatabasePath,protectSchema,workflowTaskEligibleSql,workflowTaskRetrySetSql,markWorkflowTaskFailed} from './workflow-ready.mjs';
import {settleFailure} from './failure-state.mjs';
import {finishPortraitRead,hasPortraitReadOwner} from './portrait-read-owner.mjs';
import {validatePropDesign} from './prop-image-design.mjs';

export const propBaseOperations=Object.freeze({
 'prop.generate.base':Object.freeze({type:propGenerateType,schema:propGenerateSchema,validate:validatePropGenerate,key:propKeys.base}),
 'prop.regenerate.base':Object.freeze({type:propRegenerateType,schema:propRegenerateSchema,validate:validatePropRegenerate,key:propKeys.base}),
 'prop.generate.pixel-source':Object.freeze({type:propGenerateType,schema:propGenerateSchema,validate:validatePropGenerate,key:propKeys.pixelSource}),
 'prop.regenerate.pixel-source':Object.freeze({type:propRegenerateType,schema:propRegenerateSchema,validate:validatePropRegenerate,key:propKeys.pixelSource}),
});
const worker='image', projectId='binari', official=/^request\.official\.[a-f0-9]{64}$/u;
const signature=Buffer.from('89504e470d0a1a0a','hex');
const need=(cond,code='WORKFLOW_PROP_BASE_BINDING_INVALID')=>{if(!cond)throw Error(code);};
const supported=v=>typeof v==='string'&&Object.hasOwn(propBaseOperations,v);
const scope=`r.state='planned' AND r.reason IS NULL AND r.project_id='binari'
 AND t.task_key IN ('prop_base','prop_pixel_source') AND t.worker_type='image' AND t.result_ref IS NULL`;
const parents=`JOIN workflow_tasks s ON s.task_id=t.depends_on_task_id AND s.official_request_id=t.official_request_id AND s.status='completed'
 JOIN workflow_tasks d ON d.official_request_id=t.official_request_id AND d.task_key='prop_design' AND d.worker_type='document' AND d.status='completed'
 AND ((t.task_key='prop_base' AND d.task_id=s.task_id) OR
      (t.task_key='prop_pixel_source' AND s.task_key='prop_detail' AND s.worker_type='postprocess' AND s.result_ref GLOB 'prop.detail-rgba.*'))`;

export function nextPropBaseFromPath(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true,timeout:1500});
 try{
  protectSchema(db);
  return db.prepare(`SELECT t.task_id AS taskId,t.official_request_id AS officialRequestId,t.task_type AS operation
  FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) ${parents}
  WHERE ${scope} AND ${workflowTaskEligibleSql(db,'t')}
   AND (${Object.keys(propBaseOperations).map(()=>'(t.task_type=? AND t.task_key=? AND r.request_type=? AND r.schema_version=?)').join(' OR ')})
   AND d.result_ref GLOB 'prop.design-spec.*'
  ORDER BY r.registered_at,t.official_request_id,t.task_id LIMIT 1`)
   .get(...Object.entries(propBaseOperations).flatMap(([op,spec])=>[op,spec.key,spec.type,spec.schema]))??null;
 }finally{db.close();}
}

const currentSql=`SELECT t.*,r.request_type,r.schema_version,r.payload,r.payload_sha256,r.registered_at,
 d.result_ref AS design_ref,d.task_input AS design_input,d.task_type AS design_type,d.updated_at AS design_stamp,
 d.depends_on_task_id AS design_predecessor,s.result_ref AS source_ref,s.updated_at AS source_stamp,s.task_id AS source_id,d.task_id AS design_id
 FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) ${parents}
 WHERE ${scope} AND r.request_type=? AND r.schema_version=? AND t.task_type=? AND t.official_request_id=? AND t.task_id=?`;
function readCurrent(db,{operation,officialRequestId,taskId},eligible=false){
 const spec=propBaseOperations[operation];
 return db.prepare(currentSql+(eligible?' AND '+workflowTaskEligibleSql(db,'t'):''))
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
  const plan=buildPropGeneratePlan({officialRequestId,requestType:row.request_type,payload});
  const design=plan.find(t=>t.key===propKeys.design),base=plan.find(t=>t.key===spec.key),pixelSource=spec.key===propKeys.pixelSource;
  const source=plan.find(t=>t.id===base?.dependsOn);
  const sourceRow=db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(row.source_id);
  need(base&&design?.id===row.design_id&&source?.id===row.source_id&&exactPropTask(sourceRow,source)&&isPropRef(source.key,row.source_ref)&&
   (!pixelSource||payload.presentationProfile==='spirit-v2'&&source.key===propKeys.detail)&&exactPropTask(row,base)&&
   row.design_input===canonical(design.input)&&row.design_type===design.type&&row.design_predecessor===null&&
   Number.isSafeInteger(row.design_stamp)&&row.design_stamp>=0&&isPropRef(propKeys.design,row.design_ref));
  const claimStamp=Math.max(Date.now(),row.updated_at+1);
  need(row.registered_at<=claimStamp&&row.design_stamp<=claimStamp&&Number.isSafeInteger(row.source_stamp)&&row.source_stamp>=0&&row.source_stamp<=claimStamp);
  const ownerTracked=hasPortraitReadOwner(db);
  need(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${ownerTracked?'portrait_read_owner_pid=?,':''}${workflowTaskRetrySetSql}
   WHERE task_id=? AND official_request_id=? AND worker_type=? AND task_type=? AND task_key=?
    AND updated_at=? AND status=? AND result_ref IS NULL AND ${workflowTaskEligibleSql(db)}`)
   .run(claimStamp,...(ownerTracked?[process.pid]:[]),taskId,officialRequestId,worker,operation,spec.key,row.updated_at,row.status).changes===1);
  db.exec('COMMIT');
  return {row,design,ownerTracked,key:spec.key,pixelSource,source,reference:base.input.reference,request,claim:{officialRequestId,taskId,projectId,payloadSha256:row.payload_sha256,claimStamp}};
 }catch(error){db.exec('ROLLBACK');throw error;}
}
function assertCurrent(db,selected){
 const {row,request,claim}=selected,live=readCurrent(db,request);
 need(live?.status==='running'&&live.updated_at===claim.claimStamp&&
  ['payload','payload_sha256','registered_at','task_input','depends_on_task_id','design_ref','design_input','design_type',
   'design_stamp','design_predecessor','design_id','source_id','source_ref','source_stamp'].every(k=>row[k]===live[k]),'WORKFLOW_PROP_BASE_CLAIM_CHANGED');
}
function commit(db,selected,resultRef){
 db.exec('BEGIN IMMEDIATE');
 try{
  assertCurrent(db,selected);
  const {claim}=selected;
  need(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?
   WHERE official_request_id=? AND task_id=? AND worker_type=? AND task_type=? AND task_key=?
   AND status='running' AND updated_at=? AND result_ref IS NULL`).run(resultRef,Math.max(Date.now(),claim.claimStamp+1),
    claim.officialRequestId,claim.taskId,worker,selected.request.operation,selected.key,claim.claimStamp).changes===1);
  db.exec('COMMIT');
 }catch(error){db.exec('ROLLBACK');throw error;}
}
const text=(s,n)=>typeof s==='string'&&s.isWellFormed()&&s.trim()===s&&s.length>0&&Buffer.byteLength(s)<=n&&
 !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]|\bembedding:/iu.test(s);
const perms=['xPermille','yPermille','widthPermille','heightPermille'];
const box=b=>b===null||(exact(b,perms)&&perms.every(k=>Number.isSafeInteger(b[k])&&b[k]>=0&&b[k]<=1000)&&
 b.widthPermille>=1&&b.heightPermille>=1&&b.xPermille+b.widthPermille<=1000&&b.yPermille+b.heightPermille<=1000);
export function propBaseRenderInput(packet,selected,pixelBytes){
 const {claim,design,row,reference,pixelSource,source}=selected,i=design.input;
 need(exact(packet,['design','context',...(reference?['reference']:[]),...(pixelSource?['source','media','canvas']:[])]),
  reference?'WORKFLOW_PROP_BASE_REFERENCE_INVALID':'WORKFLOW_PROP_BASE_DESIGN_INVALID');
 let referenceImage;
 if(pixelSource){
  const m=packet.media,c=packet.canvas,r=packet.source;
  need(exact(r,['taskId','resultRef'])&&r.taskId===source.id&&r.resultRef===row.source_ref&&
   exact(m,['sha256','bytes','width','height','mediaType'])&&m.mediaType==='image/png'&&m.sha256===r.resultRef.slice(-64)&&
   exact(c,['width','height'])&&m.width===c.width&&m.height===c.height&&c.width===1536&&c.height===1024&&
   Buffer.isBuffer(pixelBytes)&&pixelBytes.length===m.bytes&&m.bytes>=57&&m.bytes<=8*1024*1024&&
   pixelBytes.subarray(0,8).equals(signature)&&pixelBytes.readUInt32BE(8)===13&&pixelBytes.toString('ascii',12,16)==='IHDR'&&
   pixelBytes.readUInt32BE(16)===c.width&&pixelBytes.readUInt32BE(20)===c.height&&pixelBytes[24]===8&&pixelBytes[25]===6&&
   sha(pixelBytes)===m.sha256,'WORKFLOW_PROP_BASE_REFERENCE_INVALID');
  referenceImage={assetId:m.sha256+'.png',sha256:m.sha256,base64:pixelBytes.toString('base64')};
 }
 if(reference){
  const r=packet.reference;
  need(exact(r,['pin','assetId','sha256','base64'])&&canonical(r.pin)===canonical(reference)&&
   typeof r.sha256==='string'&&/^[a-f0-9]{64}$/u.test(r.sha256)&&r.assetId===r.sha256+'.png'&&
   typeof r.base64==='string'&&r.base64.length>0&&r.base64.length<=Math.ceil(8*1024*1024/3)*4,
   'WORKFLOW_PROP_BASE_REFERENCE_INVALID');
  const png=Buffer.from(r.base64,'base64');
  need(png.length<=8*1024*1024&&png.subarray(0,8).equals(signature)&&
   png.toString('base64')===r.base64&&sha(png)===r.sha256,'WORKFLOW_PROP_BASE_REFERENCE_INVALID');
  referenceImage={assetId:r.assetId,sha256:r.sha256,base64:r.base64};
 }
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
 return {projectId,propId:i.propId,state:'base',lane:pixelSource?'pixel':'detail',background:'green-screen.v1',intent:a.appearancePrompt,sources:ctx.sources,contextSha256:ctx.contextSha256,
  ...(referenceImage?{reference:referenceImage}:{}),...(i.presentationProfile?{presentationProfile:i.presentationProfile}:{})};
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
// Only fixed diagnostic tokens are logged; never forward provider/transport messages.
const reasons=new Set([
 'DOCUMENT_WORKER_LOCAL_TRANSPORT','WORKFLOW_PROP_REFERENCE_CHANGED',
 'PROP_IMAGE_INPUT_INVALID','PROP_IMAGE_REQUEST_INVALID','PROP_IMAGE_DESIGN_CHANGED','PROP_IMAGE_DESIGN_INVALID',
 'WORKFLOW_PROP_BASE_BINDING_INVALID','WORKFLOW_PROP_BASE_DESIGN_INVALID','WORKFLOW_PROP_BASE_REFERENCE_INVALID','WORKFLOW_PROP_BASE_REFERENCE_CHANGED',
 'WORKFLOW_IMAGE_HOST_BINDING_REQUIRED','WORKFLOW_IMAGE_HOST_CONFIG_UNTRUSTED','WORKFLOW_IMAGE_INPUT_TOO_LARGE',
 'WORKFLOW_IMAGE_NATIVE_START_FAILED','WORKFLOW_IMAGE_NATIVE_INPUT_FAILED','WORKFLOW_IMAGE_NATIVE_TIMEOUT',
 'WORKFLOW_IMAGE_NATIVE_UNCONFIRMED','WORKFLOW_IMAGE_CANCELLED',
 'WORKFLOW_IMAGE_TRANSPARENCY_REJECTED','WORKFLOW_IMAGE_CLI_OUTCOME_UNCONFIRMED','WORKFLOW_IMAGE_CLI_PROCESS_FAILED',
 'WORKFLOW_IMAGE_CLI_RESPONSE_INVALID','WORKFLOW_IMAGE_CLI_REPORTED_BLOCKED','WORKFLOW_IMAGE_CLI_RESULT_REJECTED',
 'WORKFLOW_IMAGE_OUTPUT_INVALID','WORKFLOW_PROP_BASE_PNG_INVALID','WORKFLOW_PROP_BASE_PNG_SIGNATURE',
 'WORKFLOW_PROP_BASE_PNG_CANVAS','WORKFLOW_PROP_BASE_PNG_COLOR','WORKFLOW_PROP_BASE_PUBLICATION_UNCONFIRMED',
]);
const safe=e=>reasons.has(e?.message)&&/^(?:WORKFLOW_PROP_BASE_|PROP_IMAGE_DESIGN_)/u.test(e.message)?
 e.message:'WORKFLOW_PROP_BASE_FAILED_BEFORE_PUBLISH';
const pngReasons=new Set(['WORKFLOW_IMAGE_OUTPUT_INVALID','WORKFLOW_PROP_BASE_PNG_SIGNATURE',
 'WORKFLOW_PROP_BASE_PNG_CANVAS','WORKFLOW_PROP_BASE_PNG_COLOR']);
const diagnosis=(stage,e)=>({failureStage:stage==='model-render'&&pngReasons.has(e?.message)?'png-validation':stage,
 reasonCode:reasons.has(e?.message)?e.message:'WORKFLOW_PROP_BASE_UNCLASSIFIED'});


export async function runPropBaseFromPath({dbPath,taskId,officialRequestId,operation,pds,renderProp,signal}){
 need(typeof officialRequestId==='string'&&officialRequestId.length===81&&official.test(officialRequestId)&&
  supported(operation)&&taskId===propTaskId(officialRequestId,propBaseOperations[operation].key)&&typeof renderProp==='function'&&
  typeof pds?.readPropBase==='function'&&typeof pds?.publishPropBase==='function');
 assertDatabasePath(dbPath);
 if(signal?.aborted)return {state:'stopping'};
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  const selected=claimTask(db,{officialRequestId,taskId,operation});
  if(!selected)return {state:'not_claimable',taskId};
  let ownerTracked=selected.ownerTracked,stage='input-read';
  try{
   let packet;
   try{packet=await pds.readPropBase(selected.claim,signal,!!selected.reference);}
   catch(error){
    if(selected.reference&&error?.message==='WORKFLOW_PROP_REFERENCE_CHANGED')throw Error('WORKFLOW_PROP_BASE_REFERENCE_CHANGED');
    throw error;
   }
   stage='input-validation';
   const pixelBytes=selected.pixelSource?await pds.fetchPropPixelSourceAsset(selected.claim,signal):undefined;
   const input=propBaseRenderInput(packet,selected,pixelBytes);
   stage='claim-validation';
   signal?.throwIfAborted();assertCurrent(db,selected);
   stage='model-render';
   const rendered=await renderProp(input,{signal});
   stage='png-validation';
   const artifact=mediaArtifact(rendered);
   stage='claim-validation';
   signal?.throwIfAborted();assertCurrent(db,selected);
   need(!selected.pixelSource||artifact.media.width===1536&&artifact.media.height===1024,'WORKFLOW_PROP_BASE_PNG_CANVAS');
   const resultRef=propRef(selected.key,artifact.media.sha256);
   if(ownerTracked){
    need(await settleFailure(()=>finishPortraitRead(db,selected.claim)),'WORKFLOW_PROP_BASE_CLAIM_CHANGED');
    ownerTracked=false;
   }
   signal?.throwIfAborted();
   stage='stage-write';
   const receipt=await pds.publishPropBase(selected.claim,artifact);
   need(exact(receipt,['ok','resultRef'])&&receipt.ok===true&&receipt.resultRef===resultRef,'WORKFLOW_PROP_BASE_PUBLICATION_UNCONFIRMED');
   stage='record-completion';
   await retrySqliteBusy(()=>commit(db,selected,resultRef),taskId);
   return {state:'completed',taskId,resultRef};
  }catch(error){
   const diagnostic=diagnosis(stage,error);
   if(isSqliteBusyExhausted(error))return {state:'requires_reconciliation',taskId,code:'WORKFLOW_SQLITE_BUSY_EXHAUSTED',...diagnostic};
   // A missing or invalid staging ACK is a failed attempt. The next normal
   // retry renders again; PDS stages each base image by its exact result hash.
   try{
    const changed=await settleFailure(()=>{
      db.exec('BEGIN IMMEDIATE');
      try{
       const live=db.prepare("SELECT 1 FROM workflow_runs WHERE official_request_id=? AND state='planned' AND reason IS NULL AND payload_sha256=?")
        .get(officialRequestId,selected.claim.payloadSha256);
       const changed=!!live&&(ownerTracked?finishPortraitRead(db,selected.claim,{failed:true}):
        markWorkflowTaskFailed(db,{...selected.claim,workerType:worker}));
       db.exec('COMMIT');return changed;
      }catch(e){db.exec('ROLLBACK');throw e;}
     });
    if(changed)return {state:'failed',taskId,code:stage==='stage-write'?'WORKFLOW_PROP_BASE_STAGE_WRITE_FAILED':safe(error),...diagnostic};
    diagnostic.failureTransition='CLAIM_NOT_UPDATED';
   }catch{diagnostic.failureTransition='STATE_WRITE_UNCONFIRMED';}
   return {state:'requires_reconciliation',taskId,code:diagnostic.failureTransition==='STATE_WRITE_UNCONFIRMED'?'WORKFLOW_PROP_BASE_FAILURE_WRITE_UNCONFIRMED':'WORKFLOW_PROP_BASE_CLAIM_CHANGED',...diagnostic};
  }
 }finally{db.close();}
}
