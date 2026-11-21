import {DatabaseSync} from 'node:sqlite';
import {directions,buildPlan,portraitPixelTaskType,pixelImageStyle} from './vendor/planner-pixel-chain/planner.mjs';
import {canonical} from './vendor/planner-f24d23a/envelope.mjs';
import {assertDatabasePath,protectSchema,nextWorkflowTaskFromPath,markWorkflowTaskFailed,workflowTaskEligibleSql,workflowTaskRetrySetSql} from './workflow-ready.mjs';

import {FramesDefinitelyRejected} from './postprocess-ports.mjs';

const TYPE='npc.frames-postprocess',WORKER='postprocess';
const frameScopes=['walk','run'].flatMap(m=>directions.map(d=>Object.freeze({
 workerType:WORKER,taskType:TYPE,taskKey:'frames_'+m+'_'+d,predecessorKey:'animation_'+m+'_'+d,
})));
const pixelScopes=['idle','combat'].flatMap(pose=>[null,...directions].map(d=>Object.freeze({
 workerType:WORKER,taskType:portraitPixelTaskType,
 taskKey:'portrait_pixel_'+pose+'_'+(d??'reference'),
 predecessorKey:pose==='idle'?(d?'portrait_'+d:'portrait_idle_reference'):
  'portrait_combat_'+(d??'reference'),
})));
const id=/^workflow\.task\.[a-f0-9]{64}$/;
const official=/^request\.official\.[a-f0-9]{64}$/;
const expect=(ok,code)=>{if(!ok)throw Error(code);};
function tx(db,fn){
 db.exec('BEGIN IMMEDIATE');
 try{const r=fn();db.exec('COMMIT');return r;}
 catch(e){db.exec('ROLLBACK');throw e;}
}
function checked(db,taskId,requestId,state,stamp){
 const claiming=state==='eligible';
 const task=db.prepare(`SELECT t.* FROM workflow_tasks t WHERE task_id=? AND official_request_id=?${claiming?` AND ${workflowTaskEligibleSql('t')}`:''}`).get(taskId,requestId);
 const row=db.prepare("SELECT * FROM workflow_runs WHERE official_request_id=? AND state='planned'").get(requestId);
 if(!row||!task||(!claiming&&task.status!==state)||![TYPE,portraitPixelTaskType].includes(task.task_type)||task.worker_type!==WORKER||task.result_ref!==null||
    (stamp!==undefined&&task.updated_at!==stamp))return null;
 const plan=buildPlan({officialRequestId:requestId,projectId:row.project_id,requestType:row.request_type,
  schemaVersion:row.schema_version,payload:JSON.parse(row.payload),payloadSha256:row.payload_sha256,
  status:'registered',registeredAt:row.registered_at});
 const target=plan.tasks.find(t=>t.id===taskId),parent=plan.tasks.find(t=>t.id===target?.dependsOn);
 expect(target?.type===task.task_type&&target.workerType===WORKER&&task.task_key===target.key&&task.task_input===canonical(target.input)&&
  task.depends_on_task_id===target.dependsOn&&row.payload===plan.payload,'POSTPROCESS_TASK_BINDING_INVALID');
 const pixel=target.type===portraitPixelTaskType;
 if(pixel){
  const {posePreset,direction}=target.input;
  const slot=pixelScopes.find(v=>v.taskKey===target.key);
  expect(slot&&['idle','combat'].includes(posePreset)&&(direction===null||directions.includes(direction))&&
   parent?.key===slot.predecessorKey&&parent.workerType==='image'&&parent.input.imageStyle===pixelImageStyle&&
   (parent.input.direction??null)===direction&&parent.input.npcId===target.input.npcId,
   'POSTPROCESS_PORTRAIT_SOURCE_BINDING_INVALID');
 }else expect(parent?.type==='npc.animation-native-png'&&parent.workerType==='animation'&&
  parent.input.direction===target.input.direction&&parent.input.motion===target.input.motion,'POSTPROCESS_TASK_BINDING_INVALID');
 const dep=db.prepare('SELECT * FROM workflow_tasks WHERE task_id=? AND official_request_id=?').get(target.dependsOn,requestId);
 if(!dep||dep.status!=='completed')return null;
 expect(dep.task_key===parent.key&&dep.task_type===parent.type&&dep.worker_type===parent.workerType&&
  dep.task_input===canonical(parent.input)&&dep.depends_on_task_id===parent.dependsOn,
  'POSTPROCESS_PREDECESSOR_BINDING_INVALID');
 return {task,row,dep,input:target.input,pixel};
}
export function nextPostprocessFromPath(dbPath,{frames=true,portraits=true}={}){
 // Exact tuple queries share the same readiness implementation as all other workers.
 // Rotation between directions is unnecessary: each direction is consumed once
 // and the next tick cannot reselect a running/completed task.
 for(const scope of [...(portraits?pixelScopes:[]),...(frames?frameScopes:[])]){
  const target=nextWorkflowTaskFromPath(dbPath,scope);
  if(target)return target;
 }
 return null;
}
export async function runPostprocessFromPath({dbPath,taskId,officialRequestId,pds,render,signal}){
 expect(id.test(taskId)&&official.test(officialRequestId),'POSTPROCESS_IDS_REQUIRED');
 expect(typeof pds?.read==='function'&&typeof pds?.publish==='function'&&typeof render==='function','POSTPROCESS_PORTS_REQUIRED');
 assertDatabasePath(dbPath);
 const db=new DatabaseSync(dbPath);
 try{
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');protectSchema(db);
  if(signal?.aborted)return {state:'stopping'};
  const selected=tx(db,()=>{
   const found=checked(db,taskId,officialRequestId,'eligible');
   if(!found)return null;
   const stamp=Math.max(Date.now(),found.task.updated_at+1);
   expect(Number.isSafeInteger(stamp),'POSTPROCESS_STAMP_INVALID');
   expect(db.prepare(`UPDATE workflow_tasks AS t SET status='running',updated_at=?, ${workflowTaskRetrySetSql}
    WHERE task_id=? AND official_request_id=? AND ${workflowTaskEligibleSql('t')} AND result_ref IS NULL AND updated_at=?`)
    .run(stamp,taskId,officialRequestId,found.task.updated_at).changes===1,'POSTPROCESS_CLAIM_CHANGED');
   const claim=Object.freeze({officialRequestId,taskId,projectId:found.row.project_id,npcId:found.input.npcId,
    payloadSha256:found.row.payload_sha256,claimStamp:stamp});
   const binding=Object.freeze({claim,direction:found.input.direction,predecessorTaskId:found.dep.task_id,
    ...(found.pixel?{posePreset:found.input.posePreset}:{motion:found.input.motion})});
   return {found,stamp,binding};
  });
  if(!selected)return {state:'not_claimable',taskId};
  const {found,stamp,binding}=selected;
  let rendered,publishing=false;
  try{
   const source=await pds.read(binding);
   rendered=await render(source,binding);
   expect(rendered&&typeof rendered==='object','POSTPROCESS_RENDER_RESULT_INVALID');
   // PDS independently verifies the exact running claim and unchanged source.
   publishing=true;
   const result=await pds.publish(binding,source,rendered);
   expect(result?.ok===true&&Object.keys(result).length===1,'POSTPROCESS_PUBLICATION_UNVERIFIED');
   const resultRef=found.pixel?null:'canonical-files/binari/npcs/'+binding.claim.npcId+'/animation-processed-directions/idle/'+binding.motion+'/'+binding.direction+'.json';
   tx(db,()=>{
    const current=checked(db,taskId,officialRequestId,'running',stamp);
    expect(current&&canonical(current.row)===canonical(found.row)&&canonical(current.dep)===canonical(found.dep),'POSTPROCESS_FINAL_BINDING_CHANGED');
    expect(db.prepare("UPDATE workflow_tasks SET status='completed',result_ref=?,updated_at=? WHERE task_id=? AND official_request_id=? AND status='running' AND updated_at=? AND result_ref IS NULL")
     .run(resultRef,Math.max(Date.now(),stamp+1),taskId,officialRequestId,stamp).changes===1,'POSTPROCESS_COMPLETE_CHANGED');
   });
   return {state:'completed',taskId,resultRef:resultRef};
  }catch(e){
   // No retry after a possibly committed write. Definitive pre-publication errors
   // are terminal only if we still own this exact row.
   if(!publishing||e instanceof FramesDefinitelyRejected){
    try{tx(db,()=>{
     const current=checked(db,taskId,officialRequestId,'running',stamp);
     expect(current&&canonical(current.row)===canonical(found.row)&&canonical(current.dep)===canonical(found.dep),'POSTPROCESS_FAILURE_BINDING_CHANGED');
     expect(markWorkflowTaskFailed(db,{taskId,officialRequestId,claimStamp:stamp,workerType:WORKER}),'POSTPROCESS_FAILURE_CHANGED');
    });return {state:'failed',taskId,code:publishing?'POSTPROCESS_DEFINITIVELY_REJECTED':'POSTPROCESS_READ_OR_RENDER_FAILED'};}catch{}
   }
   return {state:'requires_reconciliation',taskId,code:'POSTPROCESS_PUBLICATION_UNCERTAIN'};
  }finally{
   try{await rendered?.cleanup?.();}catch{/* renderer logs locally; never undo durable publication */}
  }
 }finally{db.close();}
}
