/** One exact document task. Storage and rendering are supplied by trusted ports. */
import {DatabaseSync} from 'node:sqlite';
import {assertDatabasePath,protectSchema,nextWorkflowTaskFromPath} from './workflow-ready.mjs';
export {nextWorkflowTaskFromPath} from './workflow-ready.mjs';
import {buildPlan,exact} from './vendor/planner-f24d23a/planner.mjs';
import {presetArtifact} from './preset.mjs';
import {preparePlanningSources,readPlanningSources} from './planning-input.mjs';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';

const validId=(v)=>typeof v==='string'&&/^workflow\.task\.[a-f0-9]{64}$/.test(v);
export const PORTRAIT='npc.portrait-representative';
const PRESET='npc.preset', REPRESENTATIVE='npc.plan-representative';
const contracts={
 [PRESET]:{key:'preset',worker:'document',receipt:'workflow.preset-publication-receipt.v1'},
 [REPRESENTATIVE]:{key:'planning_representative',worker:'document'},
 [PORTRAIT]:{key:'portrait_representative',worker:'image'},
};
const ready=`SELECT t.task_id,t.task_key,t.task_input,t.depends_on_task_id,
  t.updated_at AS task_updated_at,
  (SELECT p.result_ref FROM workflow_tasks p
   WHERE p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id) AS parent_result_ref,
  r.*
 FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
 WHERE t.task_id=? AND r.official_request_id=? AND t.status='pending' AND t.task_type=?
 AND t.worker_type=? AND t.result_ref IS NULL AND r.state='planned'
 AND (t.depends_on_task_id IS NULL OR EXISTS (
   SELECT 1 FROM workflow_tasks p
   WHERE p.task_id=t.depends_on_task_id AND p.official_request_id=t.official_request_id AND p.status='completed'
 ))`;

function claim(db,taskId,officialRequestId,operation){
 db.exec('BEGIN IMMEDIATE');
 try{
  const role=contracts[operation].worker;
  const row=db.prepare(ready).get(taskId,officialRequestId,operation,role);
  if(!row){db.exec('COMMIT');return null;}
  const plan=buildPlan({officialRequestId:row.official_request_id,projectId:row.project_id,
   requestType:row.request_type,schemaVersion:row.schema_version,payload:JSON.parse(row.payload),
   payloadSha256:row.payload_sha256,status:'registered',registeredAt:row.registered_at});
  const expected=plan.tasks.find(t=>t.key===contracts[operation].key);
  if(!expected||expected.id!==taskId||row.task_key!==expected.key||row.payload!==plan.payload||
      row.task_input!==canonical(expected.input)||row.depends_on_task_id!==expected.dependsOn||
      !Number.isSafeInteger(row.task_updated_at)||row.task_updated_at<0)throw Error('WORKFLOW_PRESET_BINDING_INVALID');
  const stamp=Math.max(Date.now(),row.task_updated_at+1);
  if(!Number.isSafeInteger(stamp))throw Error('WORKFLOW_PRESET_CLAIM_CLOCK');
  const changed=db.prepare(`UPDATE workflow_tasks SET status='running',updated_at=?
    WHERE task_id=(SELECT task_id FROM (${ready})) AND updated_at=?`).run(stamp,taskId,officialRequestId,operation,role,row.task_updated_at).changes;
  if(changed!==1)throw Error('WORKFLOW_PRESET_CLAIM_CHANGED');
  const payload=JSON.parse(plan.payload);
  const command=Object.freeze({officialRequestId:row.official_request_id,taskId,projectId:row.project_id,
   npcId:expected.input.npcId,payloadSha256:row.payload_sha256,operation,
   predecessorReceiptRef:row.parent_result_ref??null});
  db.exec('COMMIT');
  return {row,stamp,command,payload};
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

function currentBasis(value,payload,contentKey){
 if(!exact(value,['basis','character',contentKey])||!exact(value.character,Object.keys(payload.character))||
  canonical(value.character)!==canonical(payload.character))throw Error('WORKFLOW_PLAN_CHARACTER_CHANGED');
 const b=value.basis;
 const portrait=contentKey==='planningContent';
 const hex64=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
 if(!exact(b,portrait?['categoryId']:['sourceVersion','revision','generationId','categoryId'])||
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

function portraitInput(sources,payload,basis,planningContent){
 if(typeof planningContent!=='string'||!planningContent.isWellFormed()||!planningContent.trim()||[...planningContent].length>16000)
  throw Error('WORKFLOW_PORTRAIT_PLAN_REQUIRED');
 const section=sources.files.find(f=>f.scope===sources.scopes[1]&&f.path===basis.categoryId+'.json');
 if(!section)throw Error('WORKFLOW_PORTRAIT_PLAN_REQUIRED');
 let doc;
 try{doc=JSON.parse(section.text);}catch{throw Error('WORKFLOW_PORTRAIT_PLAN_INVALID');}
 if(!doc||doc.id!==basis.categoryId||doc.planningRole!=='portrait.representative'||doc.content!==planningContent)
  throw Error('WORKFLOW_PORTRAIT_PLAN_CHANGED');
 const {sources:conceptSources}=preparePlanningSources(sources,payload.character.npcId);
 const {character,intent,sourceRefs}=payload;
 return {character:{...character},intent,sourceRefs:[...sourceRefs],planningContent,conceptSources};
}

const pngMagic=Buffer.from([137,80,78,71,13,10,26,10]);
function portraitArtifact(bytes,command){
 if(!Buffer.isBuffer(bytes)||bytes.length<57||bytes.length>8*1024*1024||!bytes.subarray(0,8).equals(pngMagic))
  throw Error('WORKFLOW_PORTRAIT_PNG_REQUIRED');
 return canonical({schemaVersion:'workflow.npc-representative-portrait-artifact.v1',taskId:command.taskId,
  projectId:command.projectId,npcId:command.npcId,sha256:sha(bytes),base64:bytes.toString('base64')});
}

/** No polling, role fallback, PDS ownership reservation, nor re-execution of a running task. */
async function runSelected({db,taskId,officialRequestId,pds,operation=PRESET,render,withPlanningSnapshot,signal,onPhase}){
 if(typeof operation!=='string'||!Object.hasOwn(contracts,operation))throw Error('WORKFLOW_DOCUMENT_OPERATION_DENIED');
 if(typeof officialRequestId!=='string'||!/^request\.official\.[a-f0-9]{64}$/.test(officialRequestId))throw Error('WORKFLOW_PRESET_OFFICIAL_ID_INVALID');
 if(!validId(taskId))throw Error('WORKFLOW_PRESET_TASK_ID_INVALID');
 const ports=operation===PRESET?['publishExactPreset']:operation===PORTRAIT?['readPortrait','publishPortrait']:['readRepresentative','publishRepresentative'];
 if(ports.some(port=>typeof pds?.[port]!=='function')||
  (operation!==PRESET&&(typeof render!=='function'||typeof withPlanningSnapshot!=='function')))
  throw Error('WORKFLOW_DOCUMENT_PORT_REQUIRED');
 signal?.throwIfAborted();
 protectSchema(db);
 const phase=value=>{if(operation===PORTRAIT)try{onPhase?.(value);}catch{/* no telemetry authority */}};
 const selected=claim(db,taskId,officialRequestId,operation);
 if(!selected)return {state:'not_claimable',taskId};
 phase('claimed');
 const unresolved={state:'requires_reconciliation',taskId,code:operation===PRESET?'WORKFLOW_PRESET_RECONCILIATION_REQUIRED':operation===PORTRAIT?'WORKFLOW_PORTRAIT_RECONCILIATION_REQUIRED':'WORKFLOW_PLAN_RECONCILIATION_REQUIRED'};
 try{
  let artifact,basis;
  if(operation===PRESET)artifact=canonical(presetArtifact(selected.payload,taskId));
  else if(operation===PORTRAIT){
   phase('reading-current-basis');
   const input=await pds.readPortrait(selected.command,selected.stamp,signal);
   basis=currentBasis(input,selected.payload,'planningContent');
   const npcId=selected.payload.character.npcId;
   phase('verifying-source-folders');
   const image=await withPlanningSnapshot({npcId,signal},async snapshot=>{
    const prepared=portraitInput(await readPlanningSources(snapshot,npcId),selected.payload,basis,input.planningContent);
    phase('rendering');return render(prepared,{signal});
   });
   artifact=portraitArtifact(image,selected.command);
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
   const response=operation===PORTRAIT?await pds.publishPortrait(command,selected.stamp,basis,signal):
    await pds.publishRepresentative(command,selected.stamp,basis);
   if(!exact(response,['ok'])||response.ok!==true)return unresolved;
  }
  finish(db,selected,resultRef);phase('completed');
  return {state:'completed',taskId,resultRef};
 }catch{return unresolved;}
}
export function runSelectedDocument(options){
 if(options?.operation===PORTRAIT)throw Error('WORKFLOW_DOCUMENT_OPERATION_DENIED');
 return runSelected(options);
}
export const runSelectedPortrait=options=>runSelected({...options,operation:PORTRAIT});
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

export const nextPortraitFromPath=dbPath=>nextWorkflowTaskFromPath(dbPath,{
 workerType:'image',taskType:PORTRAIT,taskKey:'portrait_representative',predecessorKey:'planning_representative',
});
