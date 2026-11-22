/** Exact projection of Planner's selected NPC media branch, without side effects. */
import {canonical,sha,validateOneStop,isSelectionSchema,motionSelectionSlot} from './envelope.mjs';
import {npcAnimationRegenerateType as regenerateType,npcAnimationRegenerateSchema as regenerateSchema,
 validateNpcAnimationRegenerate} from './npc-animation-regenerate-envelope.mjs';
export {canonical,isSelectionSchema,regenerateType,regenerateSchema};
export const isRegenerateRun=(type,schema)=>type===regenerateType&&schema===regenerateSchema;
export const isSelectedMediaRun=(type,schema)=>isRegenerateRun(type,schema)||type==='npc.workflow'&&isSelectionSchema(schema);

const id=/^request\.official\.[a-f0-9]{64}$/;
const keyPattern=/^(animation|frames)_motion_([a-f0-9]{16})_(n|ne|e|se|s|sw|w|nw)$/;
const pixelPattern=/^portrait_pixel_idle_(reference|n|ne|e|se|s|sw|w|nw)$/;
const need=ok=>{if(!ok)throw Error('SELECTED_MOTION_TASK_BINDING');};

export function selectedMotionBranch(row,key){
 const found=keyPattern.exec(key),pixel=pixelPattern.exec(key);
 const regenerate=isRegenerateRun(row.request_type,row.schema_version);
 need((found||pixel&&!regenerate)&&row.project_id==='binari'&&isSelectedMediaRun(row.request_type,row.schema_version)&&
  row.state==='planned'&&row.reason===null&&typeof row.official_request_id==='string'&&id.test(row.official_request_id)&&
  Number.isSafeInteger(row.registered_at)&&row.registered_at>=0);
 const payload=JSON.parse(row.payload);
 need(payload.schemaVersion===row.schema_version&&
  (regenerate?validateNpcAnimationRegenerate(payload,row.project_id):validateOneStop(payload,row.project_id))===row.payload&&
  sha(row.payload)===row.payload_sha256);
 const npcId=regenerate?payload.npcId:payload.character.npcId;
 const stableId=k=>'workflow.task.'+sha(canonical([row.official_request_id,k]));
 const task=(k,type,workerType,parent,input)=>({id:stableId(k),key:k,type,workerType,dependsOn:parent===null?null:stableId(parent),input});
 const portraitDirection=direction=>task('portrait_'+direction,'npc.portrait-direction','image','portrait_pixel_idle_reference',
  {npcId,direction,imageStyle:'pixel-sprite.v1'});
 if(pixel){
  const direction=pixel[1]==='reference'?null:pixel[1];
  const parent=direction===null?task('portrait_idle_reference','npc.portrait-idle-reference','image','planning_idle',
   {npcId,imageStyle:'pixel-sprite.v1'}):portraitDirection(direction);
  return {target:task(key,'npc.portrait-pixel','postprocess',parent.key,{npcId,posePreset:'idle',direction}),parent,payload};
 }
 const [,lane,slot,direction]=found;
 const s=payload.motionSelection.filter(x=>motionSelectionSlot(x.presetId)===slot);
 need(s.length===1);
 const {presetId:motion,posePreset,assetSetSha256}=s[0];
 const portrait=regenerate?null:portraitDirection(direction),portraitKey=portrait?.key??null,animationKey='animation_motion_'+slot+'_'+direction;
 const common={npcId,direction,motion,posePreset,assetSetSha256};
 const animation=task(animationKey,'npc.animation-native-png','animation',portraitKey,{...common,output:'native-png'});
 const frames=task('frames_motion_'+slot+'_'+direction,'npc.frames-postprocess','postprocess',animationKey,
  {...common,input:'native-png'});
 return {target:lane==='animation'?animation:frames,parent:lane==='animation'?portrait:animation,payload};
}

export function matchesMotionTask(row,task){
 return row?.task_id===task.id&&row.task_key===task.key&&row.task_type===task.type&&row.worker_type===task.workerType&&
  row.task_input===canonical(task.input)&&row.depends_on_task_id===task.dependsOn;
}
