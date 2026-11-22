/** One exact document task. Storage and rendering are supplied by trusted ports. */
import {DatabaseSync} from 'node:sqlite';
import {assertDatabasePath,protectSchema,nextWorkflowTaskFromPath,markWorkflowTaskFailed,workflowTaskEligibleSql,workflowTaskRetrySetSql} from './workflow-ready.mjs';
export {nextWorkflowTaskFromPath} from './workflow-ready.mjs';
import {buildPlan,directions,exact} from './vendor/planner-f24d23a/planner.mjs';
import {buildPlan as buildPixelPlan,pixelImageStyle,portraitPixelTaskType} from './vendor/planner-pixel-sprite-v1/planner.mjs';
import {validatePlanningMarkdown} from './planning-markdown.mjs';
import {presetArtifact} from './preset.mjs';
import {resolvePlanningDocument} from './planning-document-storage.mjs';
import {preparePlanningSources,readPlanningSources} from './planning-input.mjs';
import {buildPlan as buildSelectionPlan,isSelectionSchema} from './vendor/planner-npc-motion-selection-v1/planner.mjs';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';

const validId=(v)=>typeof v==='string'&&/^workflow\.task\.[a-f0-9]{64}$/.test(v);
export const PORTRAIT='npc.portrait-representative',IDLE_PORTRAIT='npc.portrait-idle-reference',DIRECTION_PORTRAIT='npc.portrait-direction';
export const imageOperations=Object.freeze([PORTRAIT,IDLE_PORTRAIT,DIRECTION_PORTRAIT]);
const PRESET='npc.preset', REPRESENTATIVE='npc.plan-representative';
const pixelCenterKey='portrait_pixel_idle_reference';
const contracts={
 [PRESET]:{key:'preset',worker:'document',receipt:'workflow.preset-publication-receipt.v1'},
 [REPRESENTATIVE]:{key:'planning_representative',worker:'document'},
 [PORTRAIT]:{key:'portrait_representative',worker:'image',parent:'planning_representative'},
 [IDLE_PORTRAIT]:{key:'portrait_idle_reference',worker:'image',parent:'planning_idle'},
 [DIRECTION_PORTRAIT]:{worker:'image',parent:'portrait_idle_reference'},
};
const idleImagePrerequisite=Object.freeze({taskKey:'portrait_representative',taskType:PORTRAIT,workerType:'image'});
const imageArtifacts={
 [PORTRAIT]:'workflow.npc-representative-portrait-artifact.v1',
 [IDLE_PORTRAIT]:'workflow.npc-idle-reference-portrait-artifact.v1',
 [DIRECTION_PORTRAIT]:'workflow.npc-direction-portrait-artifact.v1',
};
const ready=(retry,idle)=>`SELECT t.task_id,t.task_key,t.task_input,t.depends_on_task_id,
  t.updated_at AS task_updated_at,
  (SELECT p.result_ref FROM workflow_tasks p
   WHERE p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id) AS parent_result_ref,
  r.*
 FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
 WHERE t.task_id=? AND r.official_request_id=? AND ${retry?workflowTaskEligibleSql('t'):"t.status='pending'"} AND t.task_type=?
 AND t.worker_type=? AND t.result_ref IS NULL AND r.state='planned'
 AND (t.depends_on_task_id IS NULL OR EXISTS (
   SELECT 1 FROM workflow_tasks p
   WHERE p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id AND p.status='completed'
 ))
 ${idle?`AND EXISTS (SELECT 1 FROM workflow_tasks a WHERE a.task_id=? AND a.official_request_id=t.official_request_id
  AND a.task_key='portrait_representative' AND a.task_type='npc.portrait-representative'
  AND a.worker_type='image' AND a.status='completed')`:''}`;

function claim(db,taskId,officialRequestId,operation){
 db.exec('BEGIN IMMEDIATE');
 try{
  const role=contracts[operation].worker;
  const canRetry=role==='image';
  const idle=operation===IDLE_PORTRAIT,query=ready(canRetry,idle);
  const primary=[taskId,officialRequestId,operation,role];
  if(idle)primary.push('workflow.task.'+sha(canonical([officialRequestId,idleImagePrerequisite.taskKey])));
  const row=db.prepare(query).get(...primary);
  if(!row){db.exec('COMMIT');return null;}
  const taskInput=JSON.parse(row.task_input);
  const pixel=taskInput.imageStyle===pixelImageStyle;
  if(pixel&&![IDLE_PORTRAIT,DIRECTION_PORTRAIT].includes(operation))throw Error('WORKFLOW_PORTRAIT_STYLE_INVALID');
  const build=isSelectionSchema(row.schema_version)?buildSelectionPlan:pixel?buildPixelPlan:buildPlan;
  const plan=build({officialRequestId:row.official_request_id,projectId:row.project_id,
   requestType:row.request_type,schemaVersion:row.schema_version,payload:JSON.parse(row.payload),
   payloadSha256:row.payload_sha256,status:'registered',registeredAt:row.registered_at},{pixelPortraits:pixel});
  const expected=plan.tasks.find(t=>t.id===taskId&&t.type===operation&&t.workerType===role&&
   (operation===DIRECTION_PORTRAIT?directions.includes(t.input.direction)&&t.key==='portrait_'+t.input.direction:t.key===contracts[operation].key));
  if(!expected||expected.id!==taskId||row.task_key!==expected.key||row.payload!==plan.payload||
      row.task_input!==canonical(expected.input)||row.depends_on_task_id!==expected.dependsOn||
      !Number.isSafeInteger(row.task_updated_at)||row.task_updated_at<0)throw Error('WORKFLOW_PRESET_BINDING_INVALID');
  if(pixel&&operation===DIRECTION_PORTRAIT){
   const prior=plan.tasks.find(t=>t.key===pixelCenterKey);
   const actual=db.prepare('SELECT task_key,task_type,worker_type,task_input,depends_on_task_id,status FROM workflow_tasks WHERE official_request_id=? AND task_id=?').get(officialRequestId,expected.dependsOn);
   if(!prior||!actual||actual.task_key!==prior.key||actual.task_type!==portraitPixelTaskType||
    actual.worker_type!=='postprocess'||actual.status!=='completed'||actual.task_input!==canonical(prior.input)||
    actual.depends_on_task_id!==prior.dependsOn)throw Error('WORKFLOW_PORTRAIT_PIXEL_PREDECESSOR_CHANGED');
  }
  let requireArt=false;
  if(canRetry){
   const art=db.prepare(`SELECT task_id,task_type,worker_type,status,task_input FROM workflow_tasks
    WHERE official_request_id=? AND task_key='planning_portrait_art'`).get(officialRequestId);
   if(pixel&&!art)throw Error('WORKFLOW_PORTRAIT_ART_TASK_CHANGED');
   if(art){
    if(art.task_id!=='workflow.task.'+sha(canonical([officialRequestId,'planning_portrait_art']))||
       art.task_type!=='npc.plan-portrait-art'||art.worker_type!=='document'||art.task_input!==canonical({npcId:expected.input.npcId}))
     throw Error('WORKFLOW_PORTRAIT_ART_TASK_CHANGED');
    if(art.status!=='completed'){db.exec('COMMIT');return null;}
    requireArt=true;
   }
  }
  const stamp=Math.max(Date.now(),row.task_updated_at+1);
  if(!Number.isSafeInteger(stamp))throw Error('WORKFLOW_PRESET_CLAIM_CLOCK');
  const changed=db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?${canRetry?', '+workflowTaskRetrySetSql:''}
    WHERE task_id=(SELECT task_id FROM (${query})) AND updated_at=?`).run(stamp,...primary,row.task_updated_at).changes;
  if(changed!==1)throw Error('WORKFLOW_PRESET_CLAIM_CHANGED');
  const payload=JSON.parse(plan.payload);
  const command=Object.freeze({officialRequestId:row.official_request_id,taskId,projectId:row.project_id,
   npcId:expected.input.npcId,payloadSha256:row.payload_sha256,operation,
   predecessorReceiptRef:row.parent_result_ref??null});
  db.exec('COMMIT');
  return {row,stamp,command,payload,expected,requireArt,pixel};
 }catch(e){db.exec('ROLLBACK');throw e;}
}
function verifiedPreset(receipt,command){
 return exact(receipt,['schemaVersion','officialRequestId','taskId','projectId','npcId','payloadSha256',
   'operation','artifactSha256','status','verified','receiptId'])&&
  receipt.schemaVersion===contracts[PRESET].receipt&&
  ['officialRequestId','taskId','projectId','npcId','payloadSha256','operation','artifactSha256'].every(k=>receipt[k]===command[k])&&
  receipt.status==='committed'&&receipt.verified===true&&
  typeof receipt.receiptId==='string'&&/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(receipt.receiptId);
}
function finish(db,selected,resultRef){
 const {row,stamp,command}=selected;
 db.exec('BEGIN IMMEDIATE');
 try{
  const changed=db.prepare(`UPDATE workflow_tasks SET status='completed',updated_at=?,result_ref=?
     WHERE task_id=? AND official_request_id=? AND status='running' AND worker_type=?
       AND task_type=? AND task_key=? AND task_input=? AND updated_at=?
       AND result_ref IS NULL AND depends_on_task_id IS ?
       AND EXISTS (SELECT 1 FROM workflow_runs r WHERE r.official_request_id=workflow_tasks.official_request_id
         AND r.state='planned' AND r.project_id=? AND r.request_type=? AND r.schema_version=?
         AND r.payload=? AND r.payload_sha256=? AND r.registered_at=?)`)
   .run(Math.max(Date.now(),stamp+1),resultRef,command.taskId,command.officialRequestId,contracts[command.operation].worker,
       command.operation,row.task_key,row.task_input,stamp,row.depends_on_task_id,row.project_id,row.request_type,
       row.schema_version,row.payload,row.payload_sha256,row.registered_at).changes;
  if(changed!==1)throw Error('WORKFLOW_PRESET_COMPLETION_CHANGED');
  db.exec('COMMIT');
 }catch(e){db.exec('ROLLBACK');throw e;}
}

function currentBasis(value,payload,contentKey,extra=[],pixelReference=false){
 if(!exact(value,['basis','character',contentKey,...extra])||!exact(value.character,Object.keys(payload.character))||
  canonical(value.character)!==canonical(payload.character))throw Error('WORKFLOW_PLAN_CHARACTER_CHANGED');
 const b=value.basis;
 const portrait=contentKey==='planningContent';
 const hex64=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
 if(!exact(b,portrait?(pixelReference?['categoryId','referenceStage','referenceAssetId']:['categoryId']):['sourceVersion','revision','generationId','categoryId'])||
  (pixelReference&&(b.referenceStage!==pixelCenterKey||typeof b.referenceAssetId!=='string'||!/^[a-f0-9]{64}\.png$/.test(b.referenceAssetId)))||
  (!portrait&&(!Number.isSafeInteger(b.revision)||b.revision<0||!hex64(b.sourceVersion)||
   typeof b.generationId!=='string'||!/^[a-f0-9]{48}$/.test(b.generationId)))||
  typeof b.categoryId!=='string'||!/^category\.preset\.[a-f0-9]{32}$/.test(b.categoryId))
  throw Error('WORKFLOW_PLAN_BASIS_INVALID');
 return Object.freeze({...b});
}
function readBasis(value,payload){
 const basis=currentBasis(value,payload,'presetContext');
 const presetMetadata=value.presetContext;
 if(typeof presetMetadata!=='string'||!presetMetadata.isWellFormed()||!presetMetadata.trim()||[...presetMetadata].length>8000)
  throw Error('WORKFLOW_PLAN_PRESET_METADATA_INVALID');
 let metadata;
 try{metadata=JSON.parse(presetMetadata);}catch{throw Error('WORKFLOW_PLAN_PRESET_METADATA_INVALID');}
 if(!exact(metadata,['creationBasis','planningPreset','category','chapters','status'])||canonical(metadata)!==presetMetadata||metadata.category?.id!==basis.categoryId)
  throw Error('WORKFLOW_PLAN_PRESET_METADATA_INVALID');
 return {basis,presetMetadata};
}

function portraitInput(sources,payload,basis,planningContent,operation,officialRequestId,requireArt,extra={}){
 if(typeof planningContent!=='string'||!planningContent.isWellFormed()||!planningContent.trim()||[...planningContent].length>16000)
  throw Error('WORKFLOW_PORTRAIT_PLAN_REQUIRED');
 const doc=resolvePlanningDocument(sources,basis.categoryId);
 if(!doc)throw Error('WORKFLOW_PORTRAIT_PLAN_REQUIRED');
 if(!doc||doc.id!==basis.categoryId||doc.planningRole!==(operation===PORTRAIT?'portrait.representative':'portrait.idle')||doc.content!==planningContent)
  throw Error('WORKFLOW_PORTRAIT_PLAN_CHANGED');
 const {character,intent,sourceRefs}=payload;
 const presetTask='workflow.task.'+sha(canonical([officialRequestId,'preset']));
 // Same stable preset category ID as the pinned planner/writer; no title matching.
 const artId='category.preset.'+sha(canonical(['npc.standard','3',presetTask,'portrait-art'])).slice(0,32);
 const art=resolvePlanningDocument(sources,artId);
 const marked=(content,heading)=>typeof content==='string'&&content.split(/\r?\n/u).some(line=>line.trim()===`## ${heading}`);
 const modern=requireArt||art?.planningRole==='portrait.art'||marked(art?.content,'이미지 제작 지시')||marked(planningContent,'포즈 제작 지시');
 if(modern){
  if(art?.id!==artId||art.planningRole!=='portrait.art'||typeof art.content!=='string'||
   !art.content.trim()||[...art.content].length>16000)throw Error('WORKFLOW_PORTRAIT_ART_REQUIRED');
  const artDirection=validatePlanningMarkdown(art.content,'npc.plan-portrait-art')['이미지 제작 지시'];
  const poseOperation=operation===PORTRAIT?'npc.plan-representative':'npc.plan-idle';
  const poseDirection=validatePlanningMarkdown(planningContent,poseOperation)['포즈 제작 지시'];
  return {character:{...character},artDirection,poseDirection,...extra};
 }
 const {sources:conceptSources}=preparePlanningSources(sources,character.npcId);
 return {character:{...character},intent,sourceRefs:[...sourceRefs],planningContent,conceptSources,...extra};
}

const pngMagic=Buffer.from([137,80,78,71,13,10,26,10]);
function portraitReference(reference){
 if(!exact(reference,['assetId','sha256','base64'])||reference.assetId!==reference.sha256+'.png'||
  typeof reference.sha256!=='string'||!/^[a-f0-9]{64}$/.test(reference.sha256)||typeof reference.base64!=='string'||
  reference.base64.length>Math.ceil(8*1024*1024/3)*4)throw Error('WORKFLOW_PORTRAIT_REFERENCE_INVALID');
 const bytes=Buffer.from(reference.base64,'base64');
 if(bytes.length<57||bytes.length>8*1024*1024||!bytes.subarray(0,8).equals(pngMagic)||
  bytes.toString('base64')!==reference.base64||sha(bytes)!==reference.sha256)throw Error('WORKFLOW_PORTRAIT_REFERENCE_INVALID');
 return Object.freeze({...reference});
}
function portraitArtifact(bytes,command){
 if(!Buffer.isBuffer(bytes)||bytes.length<57||bytes.length>8*1024*1024||!bytes.subarray(0,8).equals(pngMagic))
  throw Error('WORKFLOW_PORTRAIT_PNG_REQUIRED');
 return canonical({schemaVersion:imageArtifacts[command.operation],taskId:command.taskId,
  projectId:command.projectId,npcId:command.npcId,sha256:sha(bytes),base64:bytes.toString('base64')});
}

/** No polling, role fallback, PDS ownership reservation, nor re-execution of a running task. */
async function runSelected({db,taskId,officialRequestId,pds,operation=PRESET,render,withPlanningSnapshot,signal,onPhase}){
 if(typeof operation!=='string'||!Object.hasOwn(contracts,operation))throw Error('WORKFLOW_DOCUMENT_OPERATION_DENIED');
 if(typeof officialRequestId!=='string'||!/^request\.official\.[a-f0-9]{64}$/.test(officialRequestId))throw Error('WORKFLOW_PRESET_OFFICIAL_ID_INVALID');
 if(!validId(taskId))throw Error('WORKFLOW_PRESET_TASK_ID_INVALID');
 const image=imageOperations.includes(operation);
 const ports=operation===PRESET?['publishExactPreset']:image?['readPortrait','publishPortrait']:['readRepresentative','publishRepresentative'];
 if(ports.some(port=>typeof pds?.[port]!=='function')||
  (operation!==PRESET&&(typeof render!=='function'||typeof withPlanningSnapshot!=='function')))
  throw Error('WORKFLOW_DOCUMENT_PORT_REQUIRED');
 signal?.throwIfAborted();
 protectSchema(db);
 const phase=value=>{if(image)try{onPhase?.(value);}catch{/* no telemetry authority */}};
 const selected=claim(db,taskId,officialRequestId,operation);
 if(!selected)return {state:'not_claimable',taskId};
 phase('claimed');
 let publishStarted=false;
 const unresolved={state:'requires_reconciliation',taskId,code:operation===PRESET?'WORKFLOW_PRESET_RECONCILIATION_REQUIRED':image?'WORKFLOW_PORTRAIT_RECONCILIATION_REQUIRED':'WORKFLOW_PLAN_RECONCILIATION_REQUIRED'};
 try{
  let artifact,basis;
  if(operation===PRESET)artifact=canonical(presetArtifact(selected.payload,taskId));
  else if(image){
   phase('reading-current-basis');
   const input=await pds.readPortrait(selected.command,selected.stamp,signal);
   const needsReference=operation!==PORTRAIT,needsDirection=operation===DIRECTION_PORTRAIT;
   const pixelReference=selected.pixel&&needsDirection;
   basis=currentBasis(input,selected.payload,'planningContent',needsDirection?['reference','direction']:needsReference?['reference']:[],pixelReference);
   if(needsDirection&&input.direction!==selected.expected.input.direction)throw Error('WORKFLOW_PORTRAIT_DIRECTION_CHANGED');
   const extra=needsReference?{reference:portraitReference(input.reference),...(needsDirection?{direction:input.direction}:{})}:{};
   if(pixelReference&&extra.reference.assetId!==basis.referenceAssetId)throw Error('WORKFLOW_PORTRAIT_PIXEL_REFERENCE_CHANGED');
   if(selected.pixel)Object.assign(extra,{imageStyle:pixelImageStyle,posePreset:'idle'});
   const npcId=selected.payload.character.npcId;
   phase('verifying-source-folders');
   const resultImage=await withPlanningSnapshot({npcId,signal},async snapshot=>{
    const prepared=portraitInput(await readPlanningSources(snapshot,npcId),selected.payload,basis,input.planningContent,operation,officialRequestId,selected.requireArt,extra);
    phase('rendering');return render(prepared,{signal});
   });
   artifact=portraitArtifact(resultImage,selected.command);
  }else{
   const input=readBasis(await pds.readRepresentative(selected.command,selected.stamp),selected.payload);
   basis=input.basis;
   const {character,intent,sourceRefs}=selected.payload;
   const content=await withPlanningSnapshot({npcId:character.npcId,signal},async snapshot=>
    render({character:{...character},intent,sourceRefs:[...sourceRefs],presetMetadata:input.presetMetadata,sources:await readPlanningSources(snapshot,character.npcId)},{signal}));
   if(typeof content!=='string'||!content.isWellFormed()||!content.trim()||[...content].length>16000)throw Error('WORKFLOW_PLAN_CONTENT_INVALID');
   artifact=canonical({schemaVersion:'workflow.npc-representative-plan-artifact.v1',taskId,
    projectId:selected.command.projectId,npcId:selected.command.npcId,categoryId:basis.categoryId,content});
  }
  signal?.throwIfAborted();
  let resultRef=null;
  if(operation===PRESET){
   const command=Object.freeze({...selected.command,artifactSha256:sha(artifact),artifact});
   const receipt=await pds.publishExactPreset(command,selected.stamp);
   if(!verifiedPreset(receipt,command))return unresolved;
   resultRef=receipt.receiptId;
  }else{
   const command=Object.freeze({...selected.command,artifact});
   phase('publishing');
   if(image){signal?.throwIfAborted();publishStarted=true;}
   const response=image?await pds.publishPortrait(command,selected.stamp,basis,signal):
    await pds.publishRepresentative(command,selected.stamp,basis);
   if(!exact(response,['ok'])||response.ok!==true)return unresolved;
  }
  finish(db,selected,resultRef);phase('completed');
  return {state:'completed',taskId,resultRef};
 }catch(error){
  if(image&&!publishStarted){
   try{
    if(markWorkflowTaskFailed(db,{taskId,officialRequestId,claimStamp:selected.stamp,workerType:'image'})){
     phase('failed');return {state:'failed',taskId,code:error?.message==='WORKFLOW_IMAGE_TRANSPARENCY_REJECTED'?error.message:'WORKFLOW_PORTRAIT_FAILED_BEFORE_PUBLISH'};
    }
   }catch{/* exact claim changed or DB transition not confirmed */}
  }
  return unresolved;
 }
}
export function runSelectedDocument(options){
 if(imageOperations.includes(options?.operation))throw Error('WORKFLOW_DOCUMENT_OPERATION_DENIED');
 return runSelected(options);
}
export function runSelectedPortrait(options){
 const operation=options?.operation??PORTRAIT;
 if(!imageOperations.includes(operation))throw Error('WORKFLOW_PORTRAIT_OPERATION_DENIED');
 return runSelected({...options,operation});
}
export const runSelectedPreset=options=>runSelectedDocument({...options,operation:PRESET});

// In production the trusted caller must supply an explicit, pre-existing
// dedicated workflow DB path and task ID. No default path, schema creation,
// polling, executable hook, credentials or deployment configuration.
export function probePresetDatabase(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{protectSchema(db);return true;}finally{db.close();}
}
async function runFromPath(runner,{dbPath,taskId,officialRequestId,pds,operation,render,withPlanningSnapshot,signal,onPhase}){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');
  return await runner({db,taskId,officialRequestId,pds,operation,render,withPlanningSnapshot,signal,onPhase});
 }finally{db.close();}
}
export const runDocumentFromPath=options=>runFromPath(runSelectedDocument,options);
export const runPortraitFromPath=options=>runFromPath(runSelectedPortrait,options);
export const runPresetFromPath=options=>runDocumentFromPath({...options,operation:PRESET});

export function nextPortraitFromPath(dbPath){
 for(const operation of imageOperations){
  const {key,parent}=contracts[operation];
  for(const taskKey of key?[key]:directions.map(value=>'portrait_'+value)){
   const candidate=predecessorKey=>nextWorkflowTaskFromPath(dbPath,{workerType:'image',taskType:operation,taskKey,predecessorKey,
    ...(operation===IDLE_PORTRAIT?{alsoCompleted:idleImagePrerequisite}:{})});
   const target=candidate(parent)??(operation===DIRECTION_PORTRAIT?candidate(pixelCenterKey):null);
   if(target)return {...target,operation};
  }
 }
 return null;
}
