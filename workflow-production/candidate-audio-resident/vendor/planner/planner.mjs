import {DatabaseSync} from 'node:sqlite';
import {canonical,sha,validateOneStop,isReplaceSchema,isSelectionSchema,isPoseSelectionSchema,motionSelectionSlot,motionPoseRegistry,motionSelectionPoses} from './envelope.mjs';

import {conceptCategoryRequestType,validateConceptCategory} from './concept-category-envelope.mjs';

import {npcPlanningUpdateType,npcPlanningUpdateTaskKey,validateNPCPlanningUpdate} from './npc-planning-update-envelope.mjs';

import {npcPlanningAddType,npcPlanningAddTaskKey,validateNPCPlanningAdd} from './npc-planning-add-envelope.mjs';

import {audioRequestType,audioTaskKey,validateAudioGenerate} from './audio-generate-envelope.mjs';
import {audioDeleteType,audioDeleteTaskKey,validateAudioDelete} from './audio-delete-envelope.mjs';
import {audioSeparateType,audioSeparateTaskKey,validateAudioSeparate} from './audio-separate-envelope.mjs';
import {createWorkflowTasksSql,RETRY_COUNT_COLUMN,WORKFLOW_TASK_MAX_RETRIES,migrateWorkflowAudioTask} from './audio-task-schema.mjs';

import {motion2DRequestType,validateMotion2D} from './motion-preset-convert-2d-envelope.mjs';
import {buildMotion2DTasks,hasCompleteMotion2DGraph,motion2DCompletionReadLimit} from './motion-preset-convert-2d-plan.mjs';

import {npcAnimationRegenerateType,validateNpcAnimationRegenerate} from './npc-animation-regenerate-envelope.mjs';

export const portraitPixelTaskType='npc.portrait-pixel';
export const pixelImageStyle='pixel-sprite.v1';
export const directions=Object.freeze(['n','ne','e','se','s','sw','w','nw']);
const rowKeys=['officialRequestId','projectId','requestType','schemaVersion','payload','payloadSha256','status','registeredAt'];
export const exact=(value,keys)=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k));
const identity=/^request\.official\.[a-f0-9]{64}$/;
const ensure=(v,code)=>{if(!v)throw Error(code);};

export function checkRow(row){
 const site=exact(row,[...rowKeys,'sourceId','sourceKey'])&&row.sourceId==='space'&&
  typeof row.sourceKey==='string'&&/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(row.sourceKey)&&
  row.officialRequestId==='request.official.'+sha(canonical(['request.official.v1','space',row.projectId,row.sourceKey]));
 ensure((exact(row,rowKeys)||site)&&typeof row.officialRequestId==='string'&&identity.test(row.officialRequestId)&&['npc.workflow',conceptCategoryRequestType,npcPlanningUpdateType,npcPlanningAddType,audioRequestType,audioDeleteType,audioSeparateType,motion2DRequestType,npcAnimationRegenerateType].includes(row.requestType)&&row.projectId==='binari'&&
   row.status==='registered'&&Number.isSafeInteger(row.registeredAt)&&row.registeredAt>=0&&
   typeof row.payloadSha256==='string'&&/^[a-f0-9]{64}$/.test(row.payloadSha256),'WORKFLOW_ROW_INVALID');
 const payload=(row.requestType===npcAnimationRegenerateType?validateNpcAnimationRegenerate:
  row.requestType===motion2DRequestType?validateMotion2D:
  row.requestType===audioDeleteType?validateAudioDelete:
  row.requestType===audioSeparateType?validateAudioSeparate:
  row.requestType===audioRequestType?validateAudioGenerate:
  row.requestType===npcPlanningAddType?validateNPCPlanningAdd:
  row.requestType===npcPlanningUpdateType?validateNPCPlanningUpdate:
  row.requestType===conceptCategoryRequestType?validateConceptCategory:validateOneStop)(row.payload,row.projectId);
 ensure(row.payload.schemaVersion===row.schemaVersion&&sha(payload)===row.payloadSha256,'WORKFLOW_PAYLOAD_BINDING_CHANGED');
 return payload;
}

export function buildPlan(row,{pixelPortraits=true}={}){
 return materializePlan(row,checkRow(row),pixelPortraits);
}

function materializePlan(row,payload,pixelPortraits=true){
 if(row.requestType===npcAnimationRegenerateType){
  const {npcId,motionSelection}=row.payload,tasks=[];
  const id=key=>'workflow.task.'+sha(canonical([row.officialRequestId,key]));
  for(const {presetId,posePreset,assetSetSha256} of motionSelection)for(const direction of directions){
   const suffix='motion_'+motionSelectionSlot(presetId)+'_'+direction;
   const animation='animation_'+suffix,frames='frames_'+suffix;
   const input={npcId,direction,motion:presetId,posePreset,assetSetSha256};
   tasks.push({id:id(animation),key:animation,type:'npc.animation-native-png',workerType:'animation',dependsOn:null,
    input:{...input,output:'native-png'}},
    {id:id(frames),key:frames,type:'npc.frames-postprocess',workerType:'postprocess',dependsOn:id(animation),
     input:{...input,input:'native-png'}});
  }
  return {payload,tasks};
 }
 if(row.requestType===motion2DRequestType)return {payload,tasks:buildMotion2DTasks(row)};
 if(row.requestType===audioDeleteType){
  const {officialRequestId}=row.payload;
  ensure(officialRequestId!==row.officialRequestId,'NGS_AUDIO_DELETE_PAYLOAD_INVALID');
  const task={id:'workflow.task.'+sha(canonical([row.officialRequestId,audioDeleteTaskKey])),key:audioDeleteTaskKey,
   type:audioDeleteType,workerType:'file',dependsOn:null,input:{officialRequestId,
    target:{type:'directory',path:'canonical-files/binari/audio/'+officialRequestId}}};
  return {payload,tasks:[task]};
 }
 if(row.requestType===audioSeparateType){
  const {sourceOfficialRequestId,sourceSha256}=row.payload;
  ensure(sourceOfficialRequestId!==row.officialRequestId,'NGS_AUDIO_SEPARATE_PAYLOAD_INVALID');
  const task={id:'workflow.task.'+sha(canonical([row.officialRequestId,audioSeparateTaskKey])),key:audioSeparateTaskKey,
   type:audioSeparateType,workerType:'audio',dependsOn:null,input:{sourceOfficialRequestId,sourceSha256}};
  return {payload,tasks:[task]};
 }
 if(row.requestType===audioRequestType){
  const {kind,conceptId}=row.payload;
  const task={id:'workflow.task.'+sha(canonical([row.officialRequestId,audioTaskKey])),key:audioTaskKey,
    type:audioRequestType,workerType:'audio',dependsOn:null,input:{kind,conceptId}};
  return {payload,tasks:[task]};
 }
 if(row.requestType===npcPlanningAddType){
  const {npcId,categoryId}=row.payload;
  const task={id:'workflow.task.'+sha(canonical([row.officialRequestId,npcPlanningAddTaskKey])),
    key:npcPlanningAddTaskKey,type:npcPlanningAddType,workerType:'document',dependsOn:null,input:{npcId,categoryId}};
  return {payload,tasks:[task]};
 }
 if(row.requestType===npcPlanningUpdateType){
  const {npcId,role}=row.payload;
  const task={id:'workflow.task.'+sha(canonical([row.officialRequestId,npcPlanningUpdateTaskKey])),
    key:npcPlanningUpdateTaskKey,type:npcPlanningUpdateType,workerType:'document',dependsOn:null,input:{npcId,role}};
  return {payload,tasks:[task]};
 }
 if(row.requestType===conceptCategoryRequestType){
  const {categoryId,operation}=row.payload;
  const id=key=>'workflow.task.'+sha(canonical([row.officialRequestId,key]));
  const tasks=[];
  if(operation==='replace'||operation==='remove')tasks.push({id:id('category_delete'),key:'category_delete',
   type:'concept.category-delete',workerType:'file',dependsOn:null,input:{categoryId}});
  if(operation!=='remove')tasks.push({id:id('category_generate'),key:'category_generate',
   type:'concept.category-generate',workerType:'document',dependsOn:operation==='replace'?id('category_delete'):null,input:{categoryId}});
  return {payload,tasks};
 }
 // Legacy combat envelopes requested an unsupported dedicated portrait workflow.
 // Selected v1 continues to use the original historical idle-portrait graph.
 if(row.payload.includeCombat)throw Error('WORKFLOW_COMBAT_NOT_SUPPORTED');
 const npcId=row.payload.character.npcId,replace=isReplaceSchema(row.schemaVersion);
 const selected=isSelectionSchema(row.schemaVersion),poseSelection=isPoseSelectionSchema(row.schemaVersion);
 // v2 always uses the canonical pixel appearance chain; the optional graph
 // setting exists solely for verification of historical persisted plans.
 pixelPortraits=poseSelection||pixelPortraits;
 const motions=selected?row.payload.motionSelection.map(({presetId,posePreset,assetSetSha256})=>(
  {motion:presetId,key:'motion_'+motionSelectionSlot(presetId),posePreset,assetSetSha256}
 )):['walk','run'].map(motion=>({motion,key:motion}));
 const required=new Set(['idle',...(poseSelection?motions.map(motion=>motion.posePreset):[])]);
 const poses=motionSelectionPoses.filter(pose=>required.has(pose));
 const tasks=[],ids=new Map();
 const add=(key,type,workerType,dep,extra={})=>{
  ensure(dep===null||ids.has(dep),'WORKFLOW_DEPENDENCY_NOT_EARLIER');
  const id='workflow.task.'+sha(canonical([row.officialRequestId,key]));
  ids.set(key,id);
  tasks.push({id,key,type,workerType,dependsOn:dep===null?null:ids.get(dep),input:{npcId,...extra}});
 };
 if(replace)add('delete','npc.delete-folder','file',null,{scope:'exact-npc-folder',preserveRequestHistory:true,noOldDataReuse:true});
 add('preset','npc.preset','document',replace?'delete':null,{mode:replace?'fresh-replacement':'new',noOldDataReuse:true});
 add('planning_overview','npc.plan-overview','document','preset');
 add('planning_portrait_art','npc.plan-portrait-art','document','planning_overview');
 add('planning_representative','npc.plan-representative','document','planning_portrait_art');
 add('portrait_representative','npc.portrait-representative','image','planning_representative');
 for(const pose of poses){
  const lane=motionPoseRegistry[pose],pixel='portrait_pixel_'+pose+'_reference';
  add(lane.planKey,lane.planType,'document','planning_representative');
  add(lane.referenceKey,lane.referenceType,'image',lane.planKey,pixelPortraits?{imageStyle:pixelImageStyle}:{});
  if(pixelPortraits)add(pixel,portraitPixelTaskType,'postprocess',lane.referenceKey,{posePreset:pose,direction:null});
 }
 for(const direction of directions){
  const appearance=new Map();
  for(const pose of poses){
   const lane=motionPoseRegistry[pose],portrait=lane.directionPrefix+direction;
   const pixel='portrait_pixel_'+pose+'_'+direction;
   add(portrait,lane.directionType,'image',pixelPortraits?'portrait_pixel_'+pose+'_reference':lane.referenceKey,
       pixelPortraits?{direction,imageStyle:pixelImageStyle}:{direction});
   if(pixelPortraits)add(pixel,portraitPixelTaskType,'postprocess',portrait,{posePreset:pose,direction});
   appearance.set(pose,poseSelection?pixel:portrait);
  }
  for(const {key,...source} of motions){
   const animation='animation_'+key+'_'+direction;
   const predecessor=appearance.get(poseSelection?source.posePreset:'idle');
   ensure(predecessor,'WORKFLOW_POSE_STAGE_REQUIRED');
   add(animation,'npc.animation-native-png','animation',predecessor,{direction,...source,output:'native-png'});
   add('frames_'+key+'_'+direction,'npc.frames-postprocess','postprocess',animation,{direction,...source,input:'native-png'});
  }
 }
 return {payload,tasks};
}

export function migrateWorkflowRetryCount(db){
 db.exec('BEGIN IMMEDIATE');
 try{
  const columns=db.prepare("PRAGMA table_info(workflow_tasks)").all();
  if(!columns.some(c=>c.name==='retry_count'))db.exec(`ALTER TABLE workflow_tasks ADD COLUMN ${RETRY_COUNT_COLUMN}`);
  db.exec('COMMIT');
 }catch(e){db.exec('ROLLBACK');throw e;}
}

// A legal worker transition cannot make this exact graph runnable again. In
// particular, a running claim is never inferred to have failed, and a failed
// task below the cap is still live even while its retry cooldown is in effect.
function restoreStoredPlan(run){
 try{
  const plan=buildPlan({officialRequestId:run.official_request_id,projectId:run.project_id,
   requestType:run.request_type,schemaVersion:run.schema_version,payload:JSON.parse(run.payload),
   payloadSha256:run.payload_sha256,status:'registered',registeredAt:run.registered_at});
  return plan.payload===run.payload?plan:null;
 }catch{return null;}
}
const exhausted=row=>row?.status==='failed'&&row.retry_count===WORKFLOW_TASK_MAX_RETRIES;
const exactStoredTask=(row,task)=>row&&row.task_id===task.id&&row.task_key===task.key&&
 row.task_type===task.type&&row.worker_type===task.workerType&&row.task_input===canonical(task.input)&&
 row.depends_on_task_id===task.dependsOn;

function hasExhaustedRegenerationGraph(run,readTasks){
 const plan=restoreStoredPlan(run);
 if(!plan)return false;
 const rows=readTasks.all(run.official_request_id,plan.tasks.length+1);
 if(rows.length!==plan.tasks.length)return false;
 const byId=new Map(rows.map(row=>[row.task_id,row]));
 if(!rows.some(exhausted))return false;
 return plan.tasks.every(task=>{
  const row=byId.get(task.id);
  return exactStoredTask(row,task)&&(row.status==='completed'||exhausted(row)||
   (row.status==='pending'&&task.dependsOn!==null&&exhausted(byId.get(task.dependsOn))));
 });
}

function hasExhaustedAudioSeparation(run,readTasks){
 const plan=restoreStoredPlan(run);
 if(!plan||plan.tasks.length!==1)return false;
 const rows=readTasks.all(run.official_request_id,2);
 return rows.length===1&&exactStoredTask(rows[0],plan.tasks[0])&&
  exhausted(rows[0])&&rows[0].result_ref===null;
}

// Only these families opt in. In particular, failed audio.generate tasks
// continue to use their existing status behavior.
const failurePolicies=new Map([
 [npcAnimationRegenerateType,hasExhaustedRegenerationGraph],
 [audioSeparateType,hasExhaustedAudioSeparation],
]);

export class PlannerStore{
 constructor(path){
  this.db=new DatabaseSync(path);
  const names=this.db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").all();
  if(names.some(({name})=>!['workflow_runs','workflow_tasks'].includes(name))){this.db.close();throw Error('WORKFLOW_DATABASE_NOT_DEDICATED');}
  this.db.exec(`PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
  CREATE TABLE IF NOT EXISTS workflow_runs(
    official_request_id TEXT PRIMARY KEY NOT NULL, project_id TEXT NOT NULL,
    request_type TEXT NOT NULL, schema_version TEXT NOT NULL, payload TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL, registered_at INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('awaiting_rs','planned','failed','not_claimable')),
    updated_at INTEGER NOT NULL, reason TEXT
  ) STRICT;
  ${createWorkflowTasksSql};`);
  try{migrateWorkflowRetryCount(this.db);migrateWorkflowAudioTask(this.db);}catch(e){this.db.close();throw e;}
 }
 stage(row){
  const payload=checkRow(row);
  this.db.exec('BEGIN IMMEDIATE');
  try{
   const prior=this.db.prepare('SELECT * FROM workflow_runs WHERE official_request_id=?').get(row.officialRequestId);
   if(prior){
    // A registered-feed replay cannot replace or regenerate an already persisted DAG.
    ensure(prior.project_id===row.projectId&&prior.request_type===row.requestType&&prior.schema_version===row.schemaVersion&&
      prior.payload===payload&&prior.payload_sha256===row.payloadSha256&&prior.registered_at===row.registeredAt,'WORKFLOW_REPLAY_CHANGED');
   }else{
    if(row.requestType===conceptCategoryRequestType&&row.payload.operation==='replace')throw Error('NGS_CONCEPT_CATEGORY_PAYLOAD_INVALID');
    const plan=materializePlan(row,payload);
    this.db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,?,'awaiting_rs',?,?)")
     .run(row.officialRequestId,row.projectId,row.requestType,row.schemaVersion,plan.payload,row.payloadSha256,row.registeredAt,Date.now(),null);
    const stmt=this.db.prepare('INSERT INTO workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,depends_on_task_id,updated_at) VALUES(?,?,?,?,?,?,?,?)');
    for(const task of plan.tasks)stmt.run(task.id,row.officialRequestId,task.key,task.type,task.workerType,canonical(task.input),task.dependsOn,Date.now());
   }
   this.db.exec('COMMIT');return prior===undefined;
  }catch(e){this.db.exec('ROLLBACK');throw e;}
 }
 pending(){return this.db.prepare("SELECT official_request_id FROM workflow_runs WHERE state='awaiting_rs' ORDER BY registered_at,official_request_id").all();}
 acknowledge(id){return this.db.prepare("UPDATE workflow_runs SET state='planned',updated_at=? WHERE official_request_id=? AND state='awaiting_rs'").run(Date.now(),id).changes;}
 conflict(id){this.db.prepare("UPDATE workflow_runs SET state='not_claimable',updated_at=?,reason='WORKFLOW_RS_STATUS_CONFLICT' WHERE official_request_id=? AND state='awaiting_rs'").run(Date.now(),id);}
 terminalReady(){
  const result=[];
  this.db.exec('BEGIN');
  try{
   const candidates=this.db.prepare(`SELECT r.official_request_id,r.request_type,r.schema_version,r.project_id,r.payload,r.payload_sha256
    FROM workflow_runs r
    WHERE r.state='planned' AND r.reason IS NULL
      AND EXISTS (SELECT 1 FROM workflow_tasks t WHERE t.official_request_id=r.official_request_id)
      AND NOT EXISTS (SELECT 1 FROM workflow_tasks t WHERE t.official_request_id=r.official_request_id AND t.status<>'completed')
    ORDER BY r.registered_at,r.official_request_id`).all();
   const evidence=this.db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT ?');
   for(const row of candidates){
    if(row.request_type===motion2DRequestType){
     let payload;
     try{
      payload=JSON.parse(row.payload);
      if(payload.schemaVersion!==row.schema_version||validateMotion2D(payload,row.project_id)!==row.payload||
         sha(row.payload)!==row.payload_sha256)continue;
     }catch{continue;}
     if(!hasCompleteMotion2DGraph({officialRequestId:row.official_request_id,payload},evidence.all(row.official_request_id,motion2DCompletionReadLimit)))continue;
    }
    result.push({official_request_id:row.official_request_id});
   }
   this.db.exec('COMMIT');
  }catch(error){this.db.exec('ROLLBACK');throw error;}
  return result;
 }
 terminalAcknowledged(id){
  return this.db.prepare("UPDATE workflow_runs SET reason='RS_COMPLETED',updated_at=? WHERE official_request_id=? AND state='planned' AND reason IS NULL")
   .run(Date.now(),id).changes;
 }
 terminalFailures(){
  this.db.exec('BEGIN');
  try{
   const candidates=this.db.prepare(`SELECT r.* FROM workflow_runs r
    WHERE r.state='planned' AND r.reason IS NULL AND r.request_type IN (?,?)
      AND EXISTS (SELECT 1 FROM workflow_tasks t WHERE t.official_request_id=r.official_request_id
        AND t.status='failed' AND t.retry_count=?)
    ORDER BY r.registered_at,r.official_request_id`).all(npcAnimationRegenerateType,audioSeparateType,WORKFLOW_TASK_MAX_RETRIES);
   const evidence=this.db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT ?');
   const ready=candidates.filter(run=>failurePolicies.get(run.request_type)(run,evidence))
    .map(({official_request_id,request_type})=>({official_request_id,request_type}));
   this.db.exec('COMMIT');return ready;
  }catch(e){this.db.exec('ROLLBACK');throw e;}
 }
 terminalFailureAcknowledged(id,requestType){
  if(!failurePolicies.has(requestType))return 0;
  return this.db.prepare(`UPDATE workflow_runs SET state='failed',reason='WORKFLOW_TASK_RETRIES_EXHAUSTED',updated_at=?
    WHERE official_request_id=? AND request_type=? AND state='planned' AND reason IS NULL`)
   .run(Date.now(),id,requestType).changes;
 }
 close(){this.db.close();}
}

export async function pollOnce(store,rs){
 let cursor=null;const seen=new Set();let staged=0,invalid=0,unsupported=0,acknowledged=0,conflicts=0,completed=0,failed=0,pollError=false;
 try{
  do{
   const args=cursor===null?{limit:8}:{limit:8,cursor};
   const page=await rs.call('request_workflow_list',args);
   ensure(exact(page,['requests','nextCursor'])&&Array.isArray(page.requests)&&page.requests.length<=8&&
    (page.nextCursor===null||typeof page.nextCursor==='string'&&page.nextCursor.length>0&&!seen.has(page.nextCursor)),'WORKFLOW_RS_PAGE_INVALID');
   for(const row of page.requests){
    try{staged+=Number(store.stage(row));}
    catch(e){
     if(e.message==='WORKFLOW_COMBAT_NOT_SUPPORTED'){unsupported++;continue;}
     if(!['WORKFLOW_ROW_INVALID','WORKFLOW_PAYLOAD_BINDING_CHANGED','WORKFLOW_REPLAY_CHANGED','NGS_ONE_STOP_PAYLOAD_INVALID','NGS_CONCEPT_CATEGORY_PAYLOAD_INVALID','NGS_NPC_PLANNING_UPDATE_PAYLOAD_INVALID','NGS_NPC_PLANNING_ADD_PAYLOAD_INVALID','NGS_AUDIO_GENERATE_PAYLOAD_INVALID','NGS_AUDIO_DELETE_PAYLOAD_INVALID','NGS_AUDIO_SEPARATE_PAYLOAD_INVALID','NGS_MOTION_PRESET_CONVERT_2D_PAYLOAD_INVALID','NGS_NPC_ANIMATION_REGENERATE_PAYLOAD_INVALID'].includes(e.message))throw e;
     invalid++;
    }
   }
   cursor=page.nextCursor;if(cursor!==null)seen.add(cursor);
  }while(cursor!==null);
 }catch{pollError=true;}
 // Reconcile persisted ACKs independently of the list. A successful but lost
 // response may already have removed the row from RS's registered-only feed.
 for(const {official_request_id:id} of store.pending()){
  const status='planned';
  try{
   const ack=await rs.call('request_update_status',{officialRequestId:id,status});
   ensure(exact(ack,['officialRequestId','status'])&&ack.officialRequestId===id&&ack.status===status,'WORKFLOW_RS_ACK_INVALID');
   acknowledged+=store.acknowledge(id);
  }catch(e){
   if(e.rsStatus===409){store.conflict(id);conflicts++;}
  }
 }
 // Terminal notification changes only RS. Workers never write the official request.
 for(const {official_request_id:id} of store.terminalReady()){
  try{
   const status='completed',ack=await rs.call('request_update_status',{officialRequestId:id,status});
   ensure(exact(ack,['officialRequestId','status'])&&ack.officialRequestId===id&&ack.status===status,'WORKFLOW_RS_ACK_INVALID');
   completed+=store.terminalAcknowledged(id);
  }catch{pollError=true;}
 }
 for(const {official_request_id:id,request_type:requestType} of store.terminalFailures()){
  try{
   const status='failed',ack=await rs.call('request_update_status',{officialRequestId:id,status});
   ensure(exact(ack,['officialRequestId','status'])&&ack.officialRequestId===id&&ack.status===status,'WORKFLOW_RS_ACK_INVALID');
   failed+=store.terminalFailureAcknowledged(id,requestType);
  }catch{pollError=true;}
 }
 return {staged,invalid,unsupported,acknowledged,conflicts,completed,failed,pending:store.pending().length,pollError};
}
