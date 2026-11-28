import {retrySqliteBusy,saveReservationClearSql} from './sqlite-busy-retry.mjs';
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
export const backgroundSourceType='background.source.generate';
export const backgroundCineOperation='background.pixel.workflow.cinematic';
export const backgroundDraftOperation='background.pixel.workflow.draft';
const backgroundChainType='background.pixel.workflow',backgroundChainSchema='ngs.background-pixel-workflow.v1';
const chainKeys=['background_cinematic_source','background_pixel_generate','background_pixel_postprocess'];
export const uiOperations=Object.freeze(['ui.asset.generate','ui.asset.import']);
const uiDesignId='category.add.4573d0a2e2c659fc0bba561ba5774ac8';
const uiGen='ui.asset.generate';
const uiInputKeys=mode=>['kind','title',mode===uiGen?'prompt':'sha256'];
const uiInput=(v,mode)=>exact(v,uiInputKeys(mode))&&['logo','button'].includes(v.kind)&&validText(v.title,120,480)&&
 (mode===uiGen?validText(v.prompt,2000,4000):typeof v.sha256==='string'&&/^[a-f0-9]{64}$/u.test(v.sha256));
const uiDesign=d=>exact(d,['categoryId','sha256','markdown'])&&d.categoryId===uiDesignId&&
 typeof d.markdown==='string'&&d.markdown.startsWith('# UI 디자인\n')&&Buffer.byteLength(d.markdown)<=24000&&
 validText(d.markdown.trim(),12000,24000)&&d.sha256===sha(d.markdown);
export const validUiRenderInput=v=>{
 if(!v||typeof v!=='object')return false;
 const {design,...input}=v;
 return uiInput(input,uiGen)&&uiDesign(design);
};
export function uiPng(raw){
 require(Buffer.isBuffer(raw)&&raw.length>=57&&raw.length<=8*1024*1024&&
   raw.subarray(0,16).equals(Buffer.from('89504e470d0a1a0a0000000d49484452','hex'))&&
   raw[24]===8&&raw[25]===6&&raw[26]===0&&raw[27]===0&&raw[28]===0&&
   [raw.readUInt32BE(16),raw.readUInt32BE(20)].every(n=>n>0&&n<=4096),'UI_ASSET_PNG_INVALID');
 return raw;
}
const sourceProfiles=Object.freeze({
 ...Object.fromEntries(uiOperations.map(type=>{const mode=type.split('.').at(-1);return [type,{type,schema:'ngs.ui-asset-'+mode+'.v1',key:'ui_asset_'+mode,ui:true}];})),
 [sourceType]:{type:sourceType,schema:sourceSchema,key:sourceKey,collection:'sources'},
 [backgroundCineOperation]:{type:backgroundChainType,schema:backgroundChainSchema,key:chainKeys[0],collection:'backgrounds',chain:1},
 [backgroundDraftOperation]:{type:backgroundChainType,schema:backgroundChainSchema,key:chainKeys[1],collection:'backgrounds',chain:2},
 [backgroundSourceType]:{type:backgroundSourceType,schema:'ngs.background-source-generate.v1',key:'background_source_generate',collection:'backgrounds'}
});
const backgroundLims={title:120,timeOfDay:80,season:80,mood:160,color:120,location:160,requirements:900,additionalNotes:900};
const backgroundLabels={timeOfDay:'시간대',season:'계절',mood:'분위기',color:'주조색',location:'장소',requirements:'필수 요청 사항',additionalNotes:'추가 요청 사항'};
const validBackground=v=>exact(v,Object.keys(backgroundLims))&&Object.entries(backgroundLims)
 .every(([k,n])=>k==='additionalNotes'&&v[k]===''||validText(v[k],n,n*4));
export const backgroundDesignId='category.add.22ea3e79a6d349534c5b92d939b34c30';
const backgroundDesignTitle='배경 디자인 설계';
const commonIds=['visualStyle','visualProhibitions'];
const commonSchemas=['ngs.character-style-contract.v1','ngs.character-style-constraints.v1'];

const official=id=>typeof id==='string'&&id.length===81&&/^request\.official\.[a-f0-9]{64}$/u.test(id);
const validText=(v,points,max)=>typeof v==='string'&&v.trim()===v&&v.length>0&&v.isWellFormed()&&Array.from(v).length<=points&&Buffer.byteLength(v)<=max&&
 !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(v)&&!(/\bembedding:/iu.test(v));
export const validCinematicSource=v=>exact(v,inputKeys)&&validText(v.title,120,480)&&validText(v.prompt,2000,4000);
export const cinematicDesignId='category.add.ab33d46cb2f60e8467b4a3a37db9ca3d';
const cinematicDesignTitle='시네마틱 영상 디자인';
const cinematicDesignGroup='group.art-design';
// The canonical Binari Filex namespace stores the game's legacy Master Concept project key.
const masterConceptProjectId='ProjectBS';
function validCinematicDesign(d,background=false){
 const id=background?backgroundDesignId:cinematicDesignId,title=background?backgroundDesignTitle:cinematicDesignTitle;
 return exact(d,['categoryId','sha256','markdown'])&&d.categoryId===id&&
  typeof d.markdown==='string'&&d.markdown.length>0&&validText(d.markdown.trim(),12000,24000)&&
  Buffer.byteLength(d.markdown)<=24000&&d.markdown.startsWith('# '+title+'\n')&&
  d.sha256===sha(d.markdown);
}
export function selectCinematicDesign(snapshot,background=false){
 const id=background?backgroundDesignId:cinematicDesignId,title=background?backgroundDesignTitle:cinematicDesignTitle;
 const doc=snapshot?.document,groups=doc?.hierarchy?.groups,archived=doc?.hierarchy?.archivedCategoryIds;
 require(doc?.projectId===masterConceptProjectId&&Array.isArray(doc.sections)&&Array.isArray(groups)&&
  Array.isArray(archived)&&!archived.includes(id)&&
  groups.every(g=>Array.isArray(g?.categoryIds)),'CINEMATIC_DESIGN_UNAVAILABLE');
 const group=groups.filter(g=>g.id===cinematicDesignGroup);
 require(group.length===1&&group[0].categoryIds.includes(id)&&
  groups.flatMap(g=>g.categoryIds).filter(v=>v===id).length===1,'CINEMATIC_DESIGN_UNAVAILABLE');
 const design=doc.sections.filter(x=>x?.id===id),section=design[0];
 require(design.length===1&&section.title===title&&Array.isArray(section.fields)&&
  section.fields.length===1&&section.fields[0]?.id==='content'&&typeof section.fields[0]?.value==='string',
  'CINEMATIC_DESIGN_UNAVAILABLE');
 const markdown=section.fields[0].value;
 const selected={categoryId:id,markdown,sha256:sha(markdown)};
 require(validCinematicDesign(selected,background),'CINEMATIC_DESIGN_UNAVAILABLE');
 if(!background)return selected;
 const sectionMatches=doc.sections.filter(x=>x?.id==='expression');
 require(sectionMatches.length===1&&!archived.includes('expression')&&Array.isArray(sectionMatches[0].fields),'BACKGROUND_COMMON_DESIGN_UNAVAILABLE');
 const common=commonIds.map((id,i)=>{
  const matches=sectionMatches[0].fields.filter(x=>x?.id===id);
  require(matches.length===1,'BACKGROUND_COMMON_DESIGN_UNAVAILABLE');
  const text=typeof matches[0].value==='string'?matches[0].value:JSON.stringify(matches[0].value);
  require(validText(text,8000,16000),'BACKGROUND_COMMON_DESIGN_UNAVAILABLE');
  let obj;try{obj=JSON.parse(text);}catch{}
  require(obj?.schemaVersion===commonSchemas[i],'BACKGROUND_COMMON_DESIGN_UNAVAILABLE');
  return {id,sha256:sha(text),text};
 });
 return {design:selected,common};
}
export const validCinematicSourceRenderInput=v=>exact(v,['title','prompt','design'])&&
 validCinematicSource({title:v.title,prompt:v.prompt})&&validCinematicDesign(v.design);
export const validBackgroundRenderInput=v=>exact(v,['title','prompt','design','common'])&&
 validText(v.title,120,480)&&validText(v.prompt,2800,12000)&&validCinematicDesign(v.design,true)&&
 Array.isArray(v.common)&&v.common.length===2&&v.common.every((f,i)=>{
  try{return exact(f,['id','sha256','text'])&&f.id===commonIds[i]&&validText(f.text,8000,16000)&&
   f.sha256===sha(f.text)&&JSON.parse(f.text).schemaVersion===commonSchemas[i];}catch{return false;}
 });
const signature=Buffer.from('89504e470d0a1a0a','hex');
export function sourceGeometry(png,{background=false}={}){
 const format=Buffer.isBuffer(png)&&png.length>=57&&png.length<=8*1024*1024&&
  png.subarray(0,8).equals(signature)&&png.readUInt32BE(8)===13&&png.toString('ascii',12,16)==='IHDR';
 require(format,'CINEMATIC_SOURCE_PNG_INVALID');
 const width=png.readUInt32BE(16),height=png.readUInt32BE(20);
 if(width!==1536||height!==(background?1024:768))throw Object.assign(Error('CINEMATIC_SOURCE_PNG_GEOMETRY_INVALID'),{width,height});
 require(png[24]===8&&[2,6].includes(png[25]),'CINEMATIC_SOURCE_PNG_INVALID');
 return {width,height};
}
export const nextCinematicSourceFromPath=(dbPath,operation=sourceType)=>nextWorkflowTaskFromPath(dbPath,
 {workerType,taskType:sourceProfiles[operation].type,taskKey:sourceProfiles[operation].key,predecessorKey:sourceProfiles[operation].chain===2?chainKeys[0]:null});
const require=(ok,code='CINEMATIC_SOURCE_CLAIM_BINDING')=>{if(!ok)throw Error(code);};
const scope=`r.state='planned' AND r.reason IS NULL AND r.project_id=? AND r.request_type=? AND r.schema_version=?
 AND t.worker_type=? AND t.task_type=? AND t.task_key=? AND t.result_ref IS NULL`;
const args=p=>[projectId,p.type,p.schema,workerType,p.type,p.key];
const taskIdFor=(id,profile)=>'workflow.task.'+sha(canonical([id,profile.key]));
const refFor=(id,profile)=>projectId+'/cinematics/'+profile.collection+'/'+id+'.json';
function tx(db,fn){
 db.exec('BEGIN IMMEDIATE');
 try{const v=fn();db.exec('COMMIT');return v;}
 catch(e){db.exec('ROLLBACK');throw e;}
}
function candidate(db,t,stamp){
 const running=stamp!==undefined;
 const row=db.prepare(`SELECT t.*,r.payload,r.payload_sha256,r.registered_at
  FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id) WHERE ${scope}
  AND t.task_id=? AND t.official_request_id=? AND ${running?"t.status='running' AND t.updated_at=?":workflowTaskEligibleSql(db,'t')}`)
  .get(...args(t.profile),t.taskId,t.officialRequestId,...(running?[stamp]:[]));
 if(!row)return null;
 const p=JSON.parse(row.payload),background=t.profile.type===backgroundSourceType||Boolean(t.profile.chain);
 const fields=t.profile.ui?uiInputKeys(t.profile.type):background?Object.keys(backgroundLims):inputKeys;
 const valid=t.profile.ui?v=>uiInput(v,t.profile.type):background?validBackground:validCinematicSource;
 require(exact(p,['schemaVersion','projectId',...fields])&&p.schemaVersion===t.profile.schema&&p.projectId===projectId&&
  canonical(p)===row.payload&&sha(row.payload)===row.payload_sha256);
 const input=Object.fromEntries(fields.map(k=>[k,p[k]]));
 require(valid(input)&&canonical(input)===row.task_input);
 const tasks=db.prepare('SELECT task_id,task_key,task_type,worker_type,task_input,depends_on_task_id,status FROM workflow_tasks WHERE official_request_id=? LIMIT 4').all(t.officialRequestId);
 if(t.profile.chain){
  require(tasks.length===3&&chainKeys.every((key,i)=>{
   const q=tasks.find(v=>v.task_key===key),id=key=>taskIdFor(t.officialRequestId,{key});
   return q&&q.task_id===id(key)&&q.task_type===t.profile.type&&q.worker_type===(i===2?'postprocess':'image')&&q.task_input===row.task_input&&
    q.depends_on_task_id===(i?id(chainKeys[i-1]):null)&&
    (i>=t.profile.chain-1||q.status==='completed');
  }));
 }else require(tasks.length===1&&tasks[0].task_id===t.taskId&&row.depends_on_task_id===null);
 return {input,row};
}
function claimSource(db,t){
 require(official(t.officialRequestId)&&t.taskId===taskIdFor(t.officialRequestId,t.profile));
 return tx(db,()=>{
  const b=candidate(db,t);
  if(!b)return null;
  const {row}=b,claimStamp=Math.max(Date.now(),row.updated_at+1);
  require(Number.isSafeInteger(claimStamp)&&claimStamp<Number.MAX_SAFE_INTEGER&&Number.isSafeInteger(row.registered_at)&&
   row.registered_at>=0&&row.registered_at<=claimStamp);
  const ownerTracked=hasPortraitReadOwner(db);
  require(db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}${ownerTracked?',portrait_read_owner_pid=?':''}
   WHERE task_id=? AND official_request_id=? AND updated_at=? AND result_ref IS NULL AND ${workflowTaskEligibleSql(db)}`)
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

export async function runCinematicSourceFromPath({dbPath,officialRequestId,taskId,pds,renderCinematicSource,signal,operation=sourceType,
 reconcileDelays=[0,1000,2000,4000,8000,10000,15000,20000]}){
 const profile=Object.hasOwn(sourceProfiles,operation)?sourceProfiles[operation]:null;
 require(profile!==null);
 const background=operation===backgroundSourceType||Boolean(profile.chain);
 if(profile.chain){
  const originalPds=pds;
  pds={readCinematicSource:(...a)=>originalPds.readBackgroundChain(operation,...a),readCinematicConcept:originalPds.readCinematicConcept,
   publishCinematicSource:(...a)=>originalPds.publishBackgroundChain(operation,...a),reconcileCinematicSource:(...a)=>originalPds.reconcileBackgroundChain(operation,...a)};
 }else if(background)pds={readCinematicSource:pds.readBackgroundSource,readCinematicConcept:pds.readCinematicConcept,
  publishCinematicSource:pds.publishBackgroundSource,reconcileCinematicSource:pds.reconcileBackgroundSource};
 require(typeof pds?.readCinematicSource==='function'&&typeof pds?.readCinematicConcept==='function'&&typeof pds?.publishCinematicSource==='function'&&typeof renderCinematicSource==='function','CINEMATIC_SOURCE_PORT_REQUIRED');
 if(signal?.aborted)return {state:'not_claimable',taskId};
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  const target={officialRequestId,taskId,profile},found=claimSource(db,target);
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
   const rawBound=await pds.readCinematicSource(claim,signal);
   const {sourceSha256,base64,...bound}=rawBound;
   require((background?validBackground:validCinematicSource)(bound)&&canonical(bound)===canonical(input),'CINEMATIC_SOURCE_READ_MISMATCH');
   let reference;
   if(profile.chain===2){
    require(typeof base64==='string'&&base64.length<=Math.ceil(8*1024*1024/3)*4);
    const bytes=Buffer.from(base64,'base64');sourceGeometry(bytes,{background:true});
    require(sha(bytes)===sourceSha256);
    reference={assetId:sourceSha256+'.png',sha256:sourceSha256,base64};
   }else require(sourceSha256===undefined&&base64===undefined);
   stage='read-design';
   const snapshot=await pds.readCinematicConcept(signal);
   const selected=background?selectCinematicDesign(snapshot,true):{design:selectCinematicDesign(snapshot)};
   signal?.throwIfAborted();verifyCurrent();
   stage='render';
   const png=await renderCinematicSource({...background?{title:input.title,prompt:Object.entries(backgroundLabels).flatMap(([key,label])=>input[key]?[label+': '+input[key]]:[]).join('\n')}:input,...selected,...(profile.chain===1?{cinematic:selectCinematicDesign(snapshot)}:{}),...(reference?{reference}:{})},{signal});
   stage='validate-png';
   const geometry=sourceGeometry(png,{background}),sha256=sha(png),resultRef=refFor(sha256,profile);
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
   await retrySqliteBusy(()=>{
    tx(db,()=>{
     current();
     require(db.prepare(`UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=?${saveReservationClearSql(db)}
      WHERE task_id=? AND official_request_id=? AND status='running' AND result_ref IS NULL AND updated_at=?`)
      .run(resultRef,Math.max(Date.now(),claim.claimStamp+1),taskId,officialRequestId,claim.claimStamp).changes===1);
    });
   },taskId);
   return {state:'completed',taskId,resultRef,designSha256:selected.design.sha256};
  }catch(error){
   const allowed=new Set(['CINEMATIC_SOURCE_PNG_INVALID','CINEMATIC_SOURCE_PNG_GEOMETRY_INVALID','CINEMATIC_SOURCE_READ_MISMATCH',
    'CINEMATIC_SOURCE_OWNER_CHANGED','CINEMATIC_SOURCE_ACK_UNCONFIRMED','CINEMATIC_DESIGN_UNAVAILABLE','BACKGROUND_COMMON_DESIGN_UNAVAILABLE',
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
     if(changed)return {state:'failed',taskId,code:sent?reason:stage==='read-design'?(reason==='BACKGROUND_COMMON_DESIGN_UNAVAILABLE'?reason:'CINEMATIC_DESIGN_UNAVAILABLE'):'CINEMATIC_SOURCE_FAILED_BEFORE_PUBLISH',diagnostic};
     diagnostic.failureTransition='CLAIM_NOT_UPDATED';
    }catch{
     diagnostic.failureTransition='STATE_WRITE_UNCONFIRMED';
    }
   }
   return {state:'requires_reconciliation',taskId,code:'CINEMATIC_SOURCE_RECONCILIATION_REQUIRED',diagnostic};
  }
 }finally{db.close();}
}

// Share claims/dispatch with the existing ImageGen slots, but keep UI assets and
// their PDS-owned completion out of cinematic publication and recovery code.
export async function runUiAssetFromPath({dbPath,officialRequestId,taskId,pds,renderUiAsset,signal,operation}){
 const profile=sourceProfiles[operation],generated=operation===uiGen;
 require(profile?.ui&&typeof pds?.readUiAsset==='function'&&typeof pds?.publishUiAsset==='function'&&
  (!generated||typeof renderUiAsset==='function'),'UI_ASSET_PORT_REQUIRED');
 if(signal?.aborted)return {state:'not_claimable',taskId};
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  const target={officialRequestId,taskId,profile},found=claimSource(db,target);
  if(!found)return {state:'not_claimable',taskId};
  const {input,claim}=found;
  let stage='read',sent=false;
  try{
   const bound=await pds.readUiAsset(operation,claim,signal);
   const {design,...fields}=bound??{};
   require(uiInput(fields,operation)&&canonical(fields)===canonical(input)&&
    (generated?uiDesign(design):design===undefined),'UI_ASSET_INPUT_MISMATCH');
   let artifact;
   if(generated){
    stage='render';
    const png=uiPng(await renderUiAsset({...input,design},{signal}));
    artifact={sha256:sha(png),base64:png.toString('base64'),designSha256:design.sha256};
   }
   signal?.throwIfAborted();
   stage='publish';
   sent=true;
   const ref=projectId+'/ui/assets/'+officialRequestId+'/manifest.json';
   const response=await pds.publishUiAsset(operation,claim,artifact);
   require(validAck(response,ref),'UI_ASSET_PUBLISH_UNCONFIRMED');
   return {state:'completed',taskId,resultRef:ref};
  }catch(error){
   // If a transport disconnect happens before PDS reserves this claim, fail
   // it atomically so a late request cannot publish. After reservation only
   // PDS/its save reaper can settle it. Do not rerender for an ambiguous ACK.
   try{
    const changed=await settleFailure(()=>{
     if(!sent)return found.ownerTracked?finishPortraitRead(db,claim,{failed:true}):markWorkflowTaskFailed(db,{...claim,workerType});
     const hasSave=db.prepare('PRAGMA table_info(workflow_tasks)').all().some(c=>c.name==='save_started_at');
     if(!hasSave)return false;
     return db.prepare(`UPDATE workflow_tasks SET status='failed',updated_at=?${found.ownerTracked?',portrait_read_owner_pid=NULL':''}
       WHERE task_id=? AND official_request_id=? AND status='running' AND updated_at=? AND result_ref IS NULL AND save_started_at IS NULL`)
      .run(Math.max(Date.now(),claim.claimStamp+1),taskId,officialRequestId,claim.claimStamp).changes===1;
    });
    const status=db.prepare('SELECT status FROM workflow_tasks WHERE task_id=? AND official_request_id=?').get(taskId,officialRequestId)?.status;
    if(changed||status==='failed')return {state:'failed',taskId,code:sent?'UI_ASSET_STORAGE_FAILED':'UI_ASSET_FAILED_BEFORE_PUBLISH',diagnostic:{stage}};
   }catch{/* leave owned save for PDS expiry */}
   return {state:'requires_reconciliation',taskId,code:'UI_ASSET_HANDOFF_UNCONFIRMED',diagnostic:{stage}};
  }
 }finally{db.close();}
}
