import {fileDeleteSchema,fileDeleteTaskKey,fileDeleteType,validateFileDelete,backgroundDeleteRoot,cinematicDeleteRoot,mapDeleteRoot,assemblyDeleteRoot,cinematicDeleteTarget} from './file-delete-envelope.mjs';
// Machine-only execution boundary. One workflow claim can authorize one target.
import {exact} from './vendor/planner-f24d23a/planner.mjs';

const categoryId=/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const npcId=/^(?:npc|character)\.[A-Za-z0-9][A-Za-z0-9._-]*$/;
export const conceptDelete='concept.category-delete';
export const npcDelete='npc.delete-folder';
export const audioDelete='audio.delete';
export const audioDeleteSchema='ngs.audio-delete.v1';
export const audioDeleteKey='audio_delete';
export const audioResultId=/^request\.official\.[a-f0-9]{64}$/;
export const audioDeleteRoot='canonical-files/binari/audio';
export const propDeleteRoot='canonical-files/binari/props';

export function exactDeleteTarget(command){
 const targets=command?.targets;
 if(!exact(command,['operation','targets'])||command.operation!=='file.delete'||
    !Array.isArray(targets)||targets.length!==1||!exact(targets[0],['kind','id']))
  throw Error('WORKFLOW_DELETE_TARGET_INVALID');
 const target=targets[0];
 const pattern=target.kind==='concept-category'?categoryId:target.kind==='npc-directory'?npcId:['audio-directory','map-directory','assembly-directory'].includes(target.kind)?audioResultId:null;
 if(target.kind==='background-file'){
  if(typeof target.id!=='string'||!/^[a-f0-9]{64}\.(?:png|json)$/u.test(target.id))throw Error('WORKFLOW_DELETE_TARGET_INVALID');
  return target;
 }
 if(['cinematic-directory','cinematic-file'].includes(target.kind)){
  const source=typeof target.id==='string'&&/^sources\/([a-f0-9]{64})\./u.exec(target.id);
  if(!cinematicDeleteTarget(target.id,target.kind.split('-')[1],source?.[1]))throw Error('WORKFLOW_DELETE_TARGET_INVALID');
  return target;
 }
 if(target.kind==='prop-directory'){
  if(typeof target.id!=='string'||target.id.length<2||target.id.length>80||!/^[a-z0-9]+(?:[-._][a-z0-9]+)*$/u.test(target.id))throw Error('WORKFLOW_DELETE_TARGET_INVALID');
  return target;
 }
 if(!pattern||typeof target.id!=='string'||target.id.length>200||!pattern.test(target.id)||/[\s/\\]/u.test(target.id))
  throw Error('WORKFLOW_DELETE_TARGET_INVALID');
 return target;
}

// Only call after the stored file task has matched its reconstructed official plan.
// Both historical planner task types and IDs stay intact in SQLite.
export function deleteCommandFromPlannedTask(task){
 if(task?.workerType!=='file')throw Error('WORKFLOW_DELETE_TARGET_INVALID');
 let target;
 if(task.type===conceptDelete&&task.key==='category_delete'&&exact(task.input,['categoryId']))
  target={kind:'concept-category',id:task.input.categoryId};
 else if(task.type===npcDelete&&task.key==='delete'&&
         exact(task.input,['npcId','scope','preserveRequestHistory','noOldDataReuse'])&&
         task.input.scope==='exact-npc-folder'&&task.input.preserveRequestHistory===true&&task.input.noOldDataReuse===true)
  target={kind:'npc-directory',id:task.input.npcId};
 else if(task.type===fileDeleteType&&task.key===fileDeleteTaskKey&&exact(task.input,['target','expected'])){
  const physical=task.input.target;
  validateFileDelete({schemaVersion:fileDeleteSchema,projectId:'binari',target:physical,expected:task.input.expected},'binari');
  target={kind:physical.root===npcDeleteRoot?'npc-directory':physical.root===backgroundDeleteRoot?'background-file':physical.root===cinematicDeleteRoot?'cinematic-'+physical.kind:physical.root===mapDeleteRoot?'map-directory':physical.root===assemblyDeleteRoot?'assembly-directory':'prop-directory',id:physical.path};
 }
 else if(task.type===audioDelete&&task.key===audioDeleteKey&&exact(task.input,['officialRequestId','target'])&&
         typeof task.input.officialRequestId==='string'&&audioResultId.test(task.input.officialRequestId)&&
         exact(task.input.target,['type','path'])&&task.input.target.type==='directory'&&
         task.input.target.path===audioDeleteRoot+'/'+task.input.officialRequestId)
  target={kind:'audio-directory',id:task.input.officialRequestId};
 else throw Error('WORKFLOW_DELETE_TARGET_INVALID');
 const command=Object.freeze({operation:'file.delete',targets:Object.freeze([Object.freeze(target)])});
 exactDeleteTarget(command);
 return command;
}

export const singleTargetDeleteSchema='workflow.single-target-delete.v1';
export const conceptDeleteRoot='canonical-files/binari/concept';
export const npcDeleteRoot='canonical-files/binari/npcs';

// Scope coordinates are root-relative identifiers, never mount paths.
export function workflowDeleteFolder(command){
 const target=exactDeleteTarget(command);
 const concept=target.kind==='concept-category';
 if(target.kind==='background-file')return Object.freeze({root:backgroundDeleteRoot,path:target.id,kind:'file'});
 if(target.kind.startsWith('cinematic-'))return Object.freeze({root:cinematicDeleteRoot,path:target.id,kind:target.kind.split('-')[1]});
 if(target.kind==='assembly-directory')return Object.freeze({root:assemblyDeleteRoot,path:target.id,kind:'directory'});
 if(target.kind==='map-directory')return Object.freeze({root:mapDeleteRoot,path:target.id,kind:'directory'});
 if(target.kind==='prop-directory')return Object.freeze({root:propDeleteRoot,path:target.id,kind:'directory'});
 return Object.freeze({root:concept?conceptDeleteRoot:target.kind==='audio-directory'?audioDeleteRoot:npcDeleteRoot,
  path:concept?'sections/'+target.id:target.id,kind:'directory'});
}
export function verifiedSingleTargetDelete(ack,taskId){
 return exact(ack,['ok','operationId','targetAbsent','recoveryId'])&&ack.ok===true&&ack.operationId===taskId&&
  ack.targetAbsent===true&&typeof ack.recoveryId==='string'&&ack.recoveryId.length===64&&/^[a-f0-9]+$/.test(ack.recoveryId);
}
