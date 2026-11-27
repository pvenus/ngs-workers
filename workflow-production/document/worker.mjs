import {retrySqliteBusy,isSqliteBusyExhausted} from './sqlite-busy-retry.mjs';
import {npcPlanningAddSchema,validateNPCPlanningAdd} from './npc-planning-add-envelope.mjs';
import {resolvePlanningDocument,resolvePlanningRole} from './planning-document-storage.mjs';
import {npcPlanningUpdateSchema,validateNPCPlanningUpdate} from './npc-planning-update-envelope.mjs';
import {formatConceptSections} from './concept-sections.mjs';
/** One exact document task. Storage and rendering are supplied by trusted ports. */
import {DatabaseSync} from 'node:sqlite';
import {settleFailure} from './failure-state.mjs';
import {assertDatabasePath,markWorkflowTaskFailed,protectSchema,documentTaskEligibleSql,workflowTaskRetrySetSql} from './workflow-ready.mjs';
import {buildPlan as buildLegacyPlan,exact} from './vendor/planner-89c7568/planner.mjs';
import {buildPlan as buildConceptPlan} from './vendor/planner-concept-v1/planner.mjs';
import {buildPlan as buildFourDocPlan} from './vendor/planner-four-doc-v1/planner.mjs';
import {validatePlanningMarkdown} from './planning-markdown.mjs';
import {presetArtifact} from './preset.mjs';
import {readPlanningSources,assertCurrentPlanning} from './planning-input.mjs';
import {buildPlan as buildSelectionPlan,isSelectionSchema,isBasicSchema} from './vendor/planner-npc-motion-selection-v1/planner.mjs';
import {motionPoseRegistry} from './vendor/planner-npc-motion-selection-v1/envelope.mjs';
import {canonical,sha} from './vendor/planner-89c7568/envelope.mjs';

const validId=(v)=>typeof v==='string'&&/^workflow\.task\.[a-f0-9]{64}$/.test(v);
const CONCEPT='concept.category-generate',UPDATE='npc.planning.update',ADD='npc.planning.add';
const PRESET='npc.preset', OVERVIEW='npc.plan-overview', ART='npc.plan-portrait-art',
 REPRESENTATIVE='npc.plan-representative', IDLE=motionPoseRegistry.idle.planType, COMBAT=motionPoseRegistry.combat.planType;
const contracts={
 [ADD]:{key:'planning_add',port:'PlanningAdd',artifact:'workflow.npc-planning-add-markdown.v1'},
 [UPDATE]:{key:'planning_update',port:'PlanningUpdate',artifact:'workflow.npc-planning-update-markdown.v1'},
 [CONCEPT]:{key:'category_generate',port:'ConceptCategory',artifact:'workflow.concept-category-markdown.v1'},
 [PRESET]:{key:'preset',receipt:'workflow.preset-publication-receipt.v1'},
 [OVERVIEW]:{key:'planning_overview',role:'overview',category:'overview',port:'Overview',
   artifact:'workflow.npc-overview-plan-artifact.v1',upstream:[]},
 [ART]:{key:'planning_portrait_art',role:'portrait.art',category:'portrait-art',port:'PortraitArt',
   artifact:'workflow.npc-portrait-art-plan-artifact.v1',upstream:[['overviewContent',OVERVIEW]]},
 [REPRESENTATIVE]:{key:'planning_representative',role:'portrait.representative',category:'portrait.representative',
   port:'Representative',artifact:'workflow.npc-representative-plan-artifact.v1',upstream:[['portraitArtContent',ART]]},
 [IDLE]:{key:motionPoseRegistry.idle.planKey,role:'portrait.idle',category:'portrait.idle',port:'Idle',
   artifact:'workflow.npc-idle-plan-artifact.v1',upstream:[['portraitArtContent',ART],['representativeContent',REPRESENTATIVE]]},
 [COMBAT]:{key:motionPoseRegistry.combat.planKey,role:'portrait.combat',category:'portrait.combat',port:'Combat',
   artifact:'workflow.npc-combat-plan-artifact.v1',upstream:[['portraitArtContent',ART],['representativeContent',REPRESENTATIVE]]},
};
const taskKey=(officialRequestId,key)=>'workflow.task.'+sha(canonical([officialRequestId,key]));
const targetCategory=(officialRequestId,operation)=>'category.preset.'+
 sha(canonical(['npc.standard','3',taskKey(officialRequestId,'preset'),contracts[operation].category])).slice(0,32);
const goodText=v=>typeof v==='string'&&v.isWellFormed()&&v.trim()&&[...v].length<=16000;
const planningFailureCodes=new Set([
 'NPC_PLANNING_PDS_READ_TIMEOUT','DOCUMENT_WORKER_LOCAL_TRANSPORT','DOCUMENT_PDS_CONNECT_REFUSED_BEFORE_SEND',
 'WORKFLOW_PLAN_CHARACTER_CHANGED','WORKFLOW_PLAN_BASIS_INVALID','WORKFLOW_PLAN_PRESET_METADATA_INVALID','WORKFLOW_PLAN_UPSTREAM_INVALID',
 'PLANNING_FOLDER_MCP_UNAVAILABLE','PLANNING_FOLDER_RESPONSE_INVALID','PLANNING_FOLDER_MEDIA_NOT_ALLOWED',
 'PLANNING_FOLDER_LOCAL_PATH_INVALID','PLANNING_FOLDER_DIGEST_MISMATCH','PLANNING_FOLDER_SNAPSHOT_CHANGED',
 'WORKFLOW_PLAN_SOURCE_SHAPE','WORKFLOW_PLAN_SOURCE_BINDING','WORKFLOW_PLAN_SOURCE_ORDER','WORKFLOW_PLAN_SOURCE_SCOPE_EMPTY',
 'WORKFLOW_PLAN_MANIFEST_CHANGED','WORKFLOW_PLAN_SNAPSHOT_REQUIRED','WORKFLOW_PLAN_SNAPSHOT_UNTRUSTED',
 'WORKFLOW_PLAN_SNAPSHOT_FILE_INVALID','WORKFLOW_PLAN_SNAPSHOT_FILE_UNTRUSTED','WORKFLOW_PLAN_SOURCE_JSON_INVALID',
 'WORKFLOW_PLAN_CONCEPT_SECTION','WORKFLOW_PLAN_CONCEPT_SUMMARY','WORKFLOW_PLAN_PROMPT_TOO_LARGE','REPRESENTATIVE_INPUT_INVALID',
 'REPRESENTATIVE_CODEX_UNAVAILABLE','REPRESENTATIVE_CODEX_START_FAILED','REPRESENTATIVE_CODEX_TIMEOUT',
 'REPRESENTATIVE_CODEX_FAILED','REPRESENTATIVE_CODEX_OUTPUT_INVALID','REPRESENTATIVE_CODEX_STDIO_FAILED',
 'WORKFLOW_DOCUMENT_PUBLICATION_REJECTED','WORKFLOW_PLAN_MARKDOWN_INVALID','WORKFLOW_PLAN_CONTENT_INVALID','WORKFLOW_PLAN_COMBAT_WEAPON_REQUIRED',
]);
const pdsTransportKinds=new Set(['deadline_exceeded','caller_aborted','http_error','invalid_content_type',
 'response_too_large','response_aborted','response_stream_error','unexpected_conflict_response',
 'invalid_json','connection_refused','connection_reset','request_error','connection_error']);
const completionFailureReason=error=>{
 if(error?.message==='WORKFLOW_PRESET_COMPLETION_CHANGED')return 'WORKFLOW_DOCUMENT_COMPLETION_CLAIM_CHANGED';
 const sqlite=Number.isInteger(error?.errcode)?error.errcode&255:null;
 if(sqlite===5)return 'SQLITE_BUSY';
 if(sqlite===6)return 'SQLITE_LOCKED';
 if(sqlite===19)return 'SQLITE_CONSTRAINT';
 return sqlite!==null?'WORKFLOW_DOCUMENT_COMPLETION_SQLITE_ERROR':'WORKFLOW_DOCUMENT_COMPLETION_RECORD_FAILED';
};
const planningFailureFields=(stage,error)=>stage?{
 failureStage:stage,reasonCode:stage==='record-completion'?completionFailureReason(error):
  planningFailureCodes.has(error?.message)?error.message:'NPC_PLANNING_STAGE_FAILED',
 ...(stage==='publish-request'&&pdsTransportKinds.has(error?.pdsTransportKind)?{
  pdsTransportKind:error.pdsTransportKind,
  ...(Number.isInteger(error.pdsHttpStatus)&&error.pdsHttpStatus>=100&&error.pdsHttpStatus<=599?
   {pdsHttpStatus:error.pdsHttpStatus}:{}),
 }:{}),
}:{};
const roleOperation=Object.freeze({overview:OVERVIEW,'portrait.art':ART,
 'portrait.representative':REPRESENTATIVE,'portrait.idle':IDLE});
function buildAddPlan(row){
 if(row.requestType!==ADD||row.schemaVersion!==npcPlanningAddSchema)throw Error('WORKFLOW_PLAN_ADD_BINDING');
 const payload=validateNPCPlanningAdd(row.payload,row.projectId);
 if(sha(payload)!==row.payloadSha256)throw Error('WORKFLOW_PLAN_ADD_BINDING');
 return {payload,tasks:[{id:taskKey(row.officialRequestId,contracts[ADD].key),key:contracts[ADD].key,
  dependsOn:null,input:{npcId:row.payload.npcId,categoryId:row.payload.categoryId}}]};
}
function buildUpdatePlan(row){
 if(row.requestType!==UPDATE||row.schemaVersion!==npcPlanningUpdateSchema)throw Error('WORKFLOW_PLAN_UPDATE_BINDING');
 const payload=validateNPCPlanningUpdate(row.payload,row.projectId);
 if(sha(payload)!==row.payloadSha256)throw Error('WORKFLOW_PLAN_UPDATE_BINDING');
 return {payload,tasks:[{id:taskKey(row.officialRequestId,contracts[UPDATE].key),key:contracts[UPDATE].key,
  dependsOn:null,input:{npcId:row.payload.npcId,role:row.payload.role}}]};
}
const ready=db=>`SELECT t.task_id,t.task_key,t.task_input,t.depends_on_task_id,
  t.updated_at AS task_updated_at,
  (SELECT p.result_ref FROM workflow_tasks p
   WHERE p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id) AS parent_result_ref,
  r.*
 FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
 WHERE t.task_id=? AND r.official_request_id=? AND ${documentTaskEligibleSql(db)} AND t.task_type=?
 AND t.worker_type='document' AND t.result_ref IS NULL AND r.state='planned'
 AND (t.depends_on_task_id IS NULL OR EXISTS (
   SELECT 1 FROM workflow_tasks p
   WHERE p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id AND p.status='completed'
 ))`;

function claim(db,taskId,officialRequestId,operation){
 db.exec('BEGIN IMMEDIATE');
 try{
  const readySql=ready(db);
  const row=db.prepare(readySql).get(taskId,officialRequestId,operation);
  if(!row){db.exec('COMMIT');return null;}
  const extra=db.prepare("SELECT task_key FROM workflow_tasks WHERE official_request_id=? AND task_key IN ('planning_overview','planning_portrait_art')").all(officialRequestId);
  if(extra.length!==0&&extra.length!==2)throw Error('WORKFLOW_DOCUMENT_GRAPH_INVALID');
  const fourDoc=extra.length===2;
  const selection=isSelectionSchema(row.schema_version)||isBasicSchema(row.schema_version);
  if(selection&&!fourDoc)throw Error('WORKFLOW_DOCUMENT_GRAPH_INVALID');
  const plan=(operation===ADD?buildAddPlan:operation===UPDATE?buildUpdatePlan:operation===CONCEPT?buildConceptPlan:selection?buildSelectionPlan:fourDoc?buildFourDocPlan:buildLegacyPlan)({officialRequestId:row.official_request_id,projectId:row.project_id,
   requestType:row.request_type,schemaVersion:row.schema_version,payload:JSON.parse(row.payload),
   payloadSha256:row.payload_sha256,status:'registered',registeredAt:row.registered_at});
  const expected=plan.tasks.find(t=>t.key===contracts[operation].key);
  if(!expected||expected.id!==taskId||row.task_key!==expected.key||row.payload!==plan.payload||
      row.task_input!==canonical(expected.input)||row.depends_on_task_id!==expected.dependsOn||
      !Number.isSafeInteger(row.task_updated_at)||row.task_updated_at<0)throw Error('WORKFLOW_PRESET_BINDING_INVALID');
  const stamp=Math.max(Date.now(),row.task_updated_at+1);
  if(!Number.isSafeInteger(stamp))throw Error('WORKFLOW_PRESET_CLAIM_CLOCK');
  const changed=db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?,${workflowTaskRetrySetSql}
    WHERE task_id=(SELECT task_id FROM (${readySql})) AND updated_at=?`).run(stamp,taskId,officialRequestId,operation,row.task_updated_at).changes;
  if(changed!==1)throw Error('WORKFLOW_PRESET_CLAIM_CHANGED');
  const payload=JSON.parse(plan.payload);
  const command=Object.freeze({officialRequestId:row.official_request_id,taskId,projectId:row.project_id,
   ...(operation===CONCEPT?{categoryId:expected.input.categoryId}:{npcId:expected.input.npcId}),
   payloadSha256:row.payload_sha256,operation,
   predecessorReceiptRef:row.parent_result_ref??null});
  db.exec('COMMIT');
  const selectedPlanningRoles=plan.tasks.filter(task=>task.workerType==='document'&&contracts[task.type]?.role).map(task=>contracts[task.type].role);
  return {row,stamp,command,payload,fourDoc,selectedPlanningRoles};
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
  const clearSaving=db.prepare('PRAGMA table_info(workflow_tasks)').all().some(c=>c.name==='save_started_at')?',save_started_at=NULL':'';
  const changed=db.prepare(`UPDATE workflow_tasks SET status='completed',updated_at=?,result_ref=?${clearSaving}
     WHERE task_id=? AND official_request_id=? AND status='running' AND worker_type='document'
       AND task_type=? AND task_key=? AND task_input=? AND updated_at=?
       AND result_ref IS NULL AND depends_on_task_id IS ?
       AND EXISTS (SELECT 1 FROM workflow_runs r WHERE r.official_request_id=workflow_tasks.official_request_id
         AND r.state='planned' AND r.project_id=? AND r.request_type=? AND r.schema_version=?
         AND r.payload=? AND r.payload_sha256=? AND r.registered_at=?)`)
   .run(Math.max(Date.now(),stamp+1),resultRef,command.taskId,command.officialRequestId,
       command.operation,row.task_key,row.task_input,stamp,row.depends_on_task_id,row.project_id,row.request_type,
       row.schema_version,row.payload,row.payload_sha256,row.registered_at).changes;
  if(changed!==1)throw Error('WORKFLOW_PRESET_COMPLETION_CHANGED');
  db.exec('COMMIT');
 }catch(e){db.exec('ROLLBACK');throw e;}
}

function readBasis(value,selected,operation){
 const {payload,fourDoc,command}=selected;
 const spec=contracts[operation],idle=operation===IDLE;
 const upstream=fourDoc?spec.upstream:idle?[['representativeContent',REPRESENTATIVE]]:[];
 if(!exact(value,['basis','character','presetContext',...upstream.map(([k])=>k)])||
  !exact(value.character,Object.keys(payload.character))||canonical(value.character)!==canonical(payload.character))
  throw Error('WORKFLOW_PLAN_CHARACTER_CHANGED');
 const b=value.basis;
 const narrow=fourDoc||idle;
 const validScope=narrow?exact(b,['categoryId']):
  exact(b,['sourceVersion','revision','generationId','categoryId'])&&Number.isSafeInteger(b.revision)&&b.revision>=0&&
  typeof b.sourceVersion==='string'&&/^[a-f0-9]{64}$/.test(b.sourceVersion)&&
  typeof b.generationId==='string'&&/^[a-f0-9]{48}$/.test(b.generationId);
 if(!validScope||typeof b.categoryId!=='string'||!/^category\.preset\.[a-f0-9]{32}$/.test(b.categoryId)||
    fourDoc&&b.categoryId!==targetCategory(command.officialRequestId,operation))throw Error('WORKFLOW_PLAN_BASIS_INVALID');
 const presetMetadata=value.presetContext;
 if(typeof presetMetadata!=='string'||!presetMetadata.isWellFormed()||!presetMetadata.trim()||[...presetMetadata].length>8000)
  throw Error('WORKFLOW_PLAN_PRESET_METADATA_INVALID');
 let metadata;
 try{metadata=JSON.parse(presetMetadata);}catch{throw Error('WORKFLOW_PLAN_PRESET_METADATA_INVALID');}
 if(!exact(metadata,['creationBasis','planningPreset','category','chapters','status'])||canonical(metadata)!==presetMetadata||
    metadata.category?.id!==b.categoryId||
    (narrow&&(metadata.category?.planningRole!==spec.role||metadata.category?.content!=='')))
  throw Error('WORKFLOW_PLAN_PRESET_METADATA_INVALID');
 const prior={};
 for(const [name,parent] of upstream){
  if(!goodText(value[name]))throw Error('WORKFLOW_PLAN_UPSTREAM_INVALID');
  if(fourDoc)validatePlanningMarkdown(value[name],parent);
  prior[name]=value[name];
 }
 return {basis:Object.freeze({...b}),presetMetadata,upstream,prior};
}

const goodMarkdown=(value,title)=>typeof value==='string'&&value.isWellFormed()&&value.trim().length>0&&
 Buffer.byteLength(value)<=1024*1024&&!/[\u0000-\u0008\u000B-\u000C\u000E-\u001F\u007F]/u.test(value)&&
 value.replaceAll('\r\n','\n').split('\n',1)[0]==='# '+title;
function checkAuthoredMarkdown(content,title){
 if(!goodMarkdown(content,title)||content.length>12000||!content.replaceAll('\r\n','\n').split('\n')
  .slice(1).some(line=>/^## [^#\s]/u.test(line)))throw Error('WORKFLOW_CONCEPT_MARKDOWN_INVALID');
 return content;
}
function exactMigrationMarkdown(content,categoryId,title){
 if(typeof content!=='string'||!content.isWellFormed()||!content.trim()||
  Buffer.byteLength(content)>1024*1024||content.includes('\0'))return false;
 const [marker,heading]=content.split('\n',2),prefix='<!-- ngs/category:',suffix=' -->';
 if(!marker.startsWith(prefix+'{')||!marker.endsWith('}'+suffix)||heading!=='# '+title)return false;
 try{
  const metadata=JSON.parse(marker.slice(prefix.length,-suffix.length));
  return metadata!==null&&metadata.id===categoryId&&metadata.title===title&&Array.isArray(metadata.fieldIds);
 }catch{return false;}
}
function validUpdateMarkdown(content,categoryId,title){
 if(exactMigrationMarkdown(content,categoryId,title))return true;
 return goodMarkdown(content,title)&&content.replaceAll('\r\n','\n').split('\n')
  .slice(1).some(line=>/^## [^#\s]/u.test(line));
}
async function generateConceptCategory(selected,pds,render,signal){
 const {payload,command,stamp}=selected;
 if(payload.operation==='migrate'){
  const value=await pds.readConceptCategory(command,stamp,signal);
  if(!exact(value,['categoryId','title','source'])||value.categoryId!==command.categoryId||value.title!==payload.title||
   !exactMigrationMarkdown(value.source,command.categoryId,payload.title))throw Error('WORKFLOW_CONCEPT_MIGRATION_SOURCE_INVALID');
  return {content:value.source};
 }
 if(payload.operation==='update'){
  if(typeof render!=='function')throw Error('WORKFLOW_CONCEPT_RENDER_REQUIRED');
  const {categoryId,title,requirements}=payload;
  const value=await pds.readConceptCategory(command,stamp,signal);
  if(!exact(value,['categoryId','title','source','sourceSha256'])||value.categoryId!==categoryId||value.title!==title||
   !validUpdateMarkdown(value.source,categoryId,title)||typeof value.sourceSha256!=='string'||!/^[a-f0-9]{64}$/.test(value.sourceSha256)||
   sha(value.source)!==value.sourceSha256)throw Error('WORKFLOW_CONCEPT_UPDATE_SOURCE_INVALID');
  const content=await render({categoryId,title,requirements,source:value.source},{signal});
  if(!validUpdateMarkdown(content,categoryId,title)||
   (!content.startsWith('<!-- ngs/category:')&&content.length>12000)||
   content.startsWith('<!-- ngs/category:')!==value.source.startsWith('<!-- ngs/category:'))
   throw Error('WORKFLOW_CONCEPT_MARKDOWN_INVALID');
  return {content,expectedSourceSha256:value.sourceSha256};
 }
 if(!['add','replace'].includes(payload.operation)||typeof render!=='function')throw Error('WORKFLOW_CONCEPT_RENDER_REQUIRED');
 const {categoryId,title,outline,requirements}=payload;
 const sections=payload.requiredSections;
 const generated=await render({categoryId,title,outline,requirements,
  ...(sections===undefined?{}:{requiredSections:[...sections]})},{signal});
 return {content:sections===undefined?checkAuthoredMarkdown(generated,title):formatConceptSections(title,sections,generated)};
}
async function generatePlanningAdd(selected,pds,render,withPlanningSnapshot,signal){
 const {payload,command,stamp}=selected,{npcId,categoryId,title,outline,requirements,requiredSections}=payload;
 const basis=await pds.readPlanningAdd(command,stamp,signal);
 if(!exact(basis,['categoryId','title','generationId'])||basis.categoryId!==categoryId||basis.title!==title||
    typeof basis.generationId!=='string'||!/^[a-f0-9]{48}$/u.test(basis.generationId))
  throw Error('WORKFLOW_PLAN_ADD_BASIS_INVALID');
 const bodies=await withPlanningSnapshot({npcId,signal},async snapshot=>{
  const sources=await readPlanningSources(snapshot,npcId,{allowEmptyPlanning:true});
  const current=resolvePlanningRole(sources,'overview',{optional:true});
  if(current?.content&&!goodText(current.content))throw Error('WORKFLOW_PLAN_ADD_OVERVIEW_INVALID');
  if(sources.files.some(f=>f.scope===sources.scopes[1]&&[categoryId+'.json',categoryId+'/content.md'].includes(f.path)))
   throw Error('WORKFLOW_PLAN_ADD_TARGET_PRESENT');
  return render({npcId,categoryId,title,outline,requirements,requiredSections:[...requiredSections],
   overviewContent:current?.content||null,sources},{signal});
 });
 return {schemaVersion:contracts[ADD].artifact,categoryId,title,generationId:basis.generationId,
  content:formatConceptSections(title,requiredSections,bodies)};
}
async function generatePlanningUpdate(selected,pds,render,withPlanningSnapshot,signal){
 const {payload,command,stamp}=selected,{npcId,role,requirements}=payload;
 const spec=contracts[UPDATE],operation=roleOperation[role],value=await pds.readPlanningUpdate(command,stamp,signal);
 if(!exact(value,['categoryId','role','title','source','sourceSha256','generationId'])||value.role!==role||
    typeof value.categoryId!=='string'||!/^category\.preset\.[a-f0-9]{32}$/u.test(value.categoryId)||
    !goodText(value.title)||[...value.title].length>120||/[\t\r\n\u0085\u2028\u2029]/u.test(value.title)||!goodText(value.source)||
    typeof value.generationId!=='string'||!/^[a-f0-9]{48}$/u.test(value.generationId)||
    typeof value.sourceSha256!=='string'||sha(value.source)!==value.sourceSha256)throw Error('WORKFLOW_PLAN_UPDATE_SOURCE_INVALID');
 const heading=value.source.split(/\r?\n/u,1)[0];
 const outputHeading=/^# [^#\s]/u.test(heading)?heading:'# '+value.title;
 const content=await withPlanningSnapshot({npcId,signal},async snapshot=>{
  const sources=await readPlanningSources(snapshot,npcId);
  const doc=resolvePlanningDocument(sources,value.categoryId);
  if(doc?.planningRole!==role||doc.name!==value.title||doc.content!==value.source||doc.sourceSha256!==value.sourceSha256)
   throw Error('WORKFLOW_PLAN_UPDATE_SOURCE_CHANGED');
  const prior=Object.fromEntries(contracts[operation].upstream.map(([name,upstream])=>{
   const current=resolvePlanningRole(sources,contracts[upstream].role);
   if(!goodText(current?.content))throw Error('WORKFLOW_PLAN_UPSTREAM_INVALID');
   return [name,current.content];
  }));
  return render({npcId,role,title:value.title,heading:outputHeading,requirements,source:value.source,...prior,sources},{signal});
 });
 validatePlanningMarkdown(content,operation);
 if(content.split(/\r?\n/u,1)[0]!==outputHeading)throw Error('WORKFLOW_PLAN_UPDATE_TITLE_CHANGED');
 return {schemaVersion:spec.artifact,categoryId:value.categoryId,role,generationId:value.generationId,
  expectedSourceSha256:value.sourceSha256,content};
}
/** Exact CAS for both resident polling and explicit invocation. */
export async function runSelectedDocument({db,taskId,officialRequestId,pds,operation=PRESET,render,withPlanningSnapshot,signal}){
 if(typeof operation!=='string'||!Object.hasOwn(contracts,operation))throw Error('WORKFLOW_DOCUMENT_OPERATION_DENIED');
 if(typeof officialRequestId!=='string'||!/^request\.official\.[a-f0-9]{64}$/.test(officialRequestId))throw Error('WORKFLOW_PRESET_OFFICIAL_ID_INVALID');
 if(!validId(taskId))throw Error('WORKFLOW_PRESET_TASK_ID_INVALID');
 const spec=contracts[operation],read='read'+spec.port,publish='publish'+spec.port;
 if(operation===PRESET?typeof pds?.publishExactPreset!=='function':operation===CONCEPT?
  typeof pds?.[read]!=='function'||typeof pds?.[publish]!=='function':
  typeof pds?.[read]!=='function'||typeof pds?.[publish]!=='function'||typeof render!=='function'||typeof withPlanningSnapshot!=='function')
  throw Error('WORKFLOW_DOCUMENT_PORT_REQUIRED');
 signal?.throwIfAborted();
 protectSchema(db);
 const selected=claim(db,taskId,officialRequestId,operation);
 if(!selected)return {state:'not_claimable',taskId};
 let publicationStarted=false,planningFailureStage;
 const unresolved={state:'requires_reconciliation',taskId,code:operation===PRESET?'WORKFLOW_PRESET_RECONCILIATION_REQUIRED':'WORKFLOW_PLAN_RECONCILIATION_REQUIRED'};
 try{
  let artifact,basis;
  if(operation===PRESET)artifact=canonical(presetArtifact(selected.payload,taskId,{selectedPlanningRoles:selected.selectedPlanningRoles}));
  else if(operation===ADD)artifact=canonical(await generatePlanningAdd(selected,pds,render,withPlanningSnapshot,signal));
  else if(operation===UPDATE)artifact=canonical(await generatePlanningUpdate(selected,pds,render,withPlanningSnapshot,signal));
  else if(operation===CONCEPT)artifact=canonical({schemaVersion:spec.artifact,categoryId:selected.command.categoryId,
   title:selected.payload.title,...await generateConceptCategory(selected,pds,render,signal)});
  else{
   planningFailureStage='pds-read';
   const reply=await pds[read](selected.command,selected.stamp);
   planningFailureStage='pds-contract';
   const input=readBasis(reply,selected,operation);
   planningFailureStage='snapshot-download';
   basis=input.basis;
   const {character,intent,sourceRefs,combatRequirements}=selected.payload;
   const content=await withPlanningSnapshot({npcId:character.npcId,signal},async snapshot=>
    {
     planningFailureStage='snapshot-contract';
     const sources=await readPlanningSources(snapshot,character.npcId);
     const prior=Object.fromEntries(input.upstream.map(([name,parent])=>[name,assertCurrentPlanning(sources,
      contracts[parent].role,input.prior[name],selected.fourDoc?targetCategory(officialRequestId,parent):undefined)]));
     planningFailureStage='render';
     return render({character:{...character},intent,sourceRefs:[...sourceRefs],presetMetadata:input.presetMetadata,
      ...(operation==='npc.plan-combat'&&combatRequirements?{combatRequirements}:{}),
      ...prior,sources},{signal,fourDoc:selected.fourDoc});
    });
   planningFailureStage='result-contract';
   if(!goodText(content))throw Error('WORKFLOW_PLAN_CONTENT_INVALID');
   if(selected.fourDoc)validatePlanningMarkdown(content,operation);
   artifact=canonical({schemaVersion:spec.artifact,taskId,
    projectId:selected.command.projectId,npcId:selected.command.npcId,categoryId:basis.categoryId,content});
  }
  signal?.throwIfAborted();
  let resultRef=null;
  if(operation===PRESET){
   const command=Object.freeze({...selected.command,artifactSha256:sha(artifact),artifact});
   publicationStarted=true;planningFailureStage='publish-request';
   const receipt=await pds.publishExactPreset(command,selected.stamp);
   if(!verifiedPreset(receipt,command))return {...unresolved,failureStage:'verify-publish-ack'};
   resultRef=receipt.receiptId;
  }else{
   const command=Object.freeze({...selected.command,artifact});
   publicationStarted=true;planningFailureStage='publish-request';
   const response=[CONCEPT,UPDATE,ADD].includes(operation)?await pds[publish](command,selected.stamp,signal):
    await pds[publish](command,selected.stamp,basis);
   if(!exact(response,['ok'])||response.ok!==true)return {...unresolved,failureStage:'verify-publish-ack'};
  }
  planningFailureStage='record-completion';
  await retrySqliteBusy(()=>finish(db,selected,resultRef),taskId);
  return {state:'completed',taskId,resultRef,...(operation===CONCEPT&&selected.payload.operation==='migrate'?
   {conversion:'deterministic-markdown'}:{})};
 }catch(error){
  // Fail closed on an ambiguous save result. Definite no-send/rejection and
  // post-ack completion recording errors follow the ordinary retry path.
  const presetRejected=operation===PRESET&&error?.message==='WORKFLOW_PRESET_REJECTED_BEFORE_WRITE';
  const notSent=error?.message==='DOCUMENT_PDS_CONNECT_REFUSED_BEFORE_SEND';
  const planRejected=planningFailureStage==='publish-request'&&error?.message==='WORKFLOW_DOCUMENT_PUBLICATION_REJECTED';
  // If BUSY persists after the bounded DB-only retries, do not attempt a
  // second state write into the same locked DB. Other completion errors can fail normally.
  const completionFailed=planningFailureStage==='record-completion';
  if(isSqliteBusyExhausted(error)||publicationStarted&&!presetRejected&&!notSent&&!planRejected&&!completionFailed)return {...unresolved,...planningFailureFields(planningFailureStage,error)};
  try{
   const changed=await settleFailure(()=>markWorkflowTaskFailed(db,{taskId,officialRequestId,claimStamp:selected.stamp,workerType:'document'}));
   if(changed===true)return {state:'failed',taskId,code:completionFailed?'WORKFLOW_DOCUMENT_COMPLETION_RECORD_FAILED':presetRejected||notSent||planRejected?error.message:'WORKFLOW_DOCUMENT_FAILED_BEFORE_PUBLICATION',
    ...planningFailureFields(planningFailureStage,error)};
  }catch{
   // An unconfirmed terminal DB write requires reconciliation as well.
  }
  return {...unresolved,...planningFailureFields(planningFailureStage,error)};
 }
}
export const runSelectedPreset=options=>runSelectedDocument({...options,operation:PRESET});

export function probePresetDatabase(dbPath){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath,{readOnly:true});
 try{protectSchema(db);return true;}finally{db.close();}
}
export async function runDocumentFromPath({dbPath,taskId,officialRequestId,pds,operation,render,withPlanningSnapshot,signal}){
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');
  return await runSelectedDocument({db,taskId,officialRequestId,pds,operation,render,withPlanningSnapshot,signal});
 }finally{db.close();}
}
export const runPresetFromPath=options=>runDocumentFromPath({...options,operation:PRESET});
