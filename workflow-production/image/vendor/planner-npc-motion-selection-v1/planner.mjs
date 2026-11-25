// Selected NPC foreground projection of the canonical workflow planner.
// Media tasks stay in their own role and are not reconstructed here.
import {canonical,sha,validateOneStop,isReplaceSchema,isSelectionSchema,isBasicSchema,isPoseSelectionSchema,motionPoseRegistry,motionSelectionPoses} from './envelope.mjs';
export {isSelectionSchema,isBasicSchema};
const fields=['officialRequestId','projectId','requestType','schemaVersion','payload','payloadSha256','status','registeredAt'];
export function buildPlan(row){
 if(!row||Object.keys(row).length!==fields.length||!fields.every(k=>Object.hasOwn(row,k))||
  typeof row.officialRequestId!=='string'||row.officialRequestId.length!==81||!/^request\.official\.[a-f0-9]{64}$/.test(row.officialRequestId)||
  row.requestType!=='npc.workflow'||row.projectId!=='binari'||row.status!=='registered'||
  !Number.isSafeInteger(row.registeredAt)||row.registeredAt<0||!(isSelectionSchema(row.schemaVersion)||isBasicSchema(row.schemaVersion)))
  throw Error('WORKFLOW_ROW_INVALID');
 const payload=validateOneStop(row.payload,row.projectId);
 if(row.payload.schemaVersion!==row.schemaVersion||sha(payload)!==row.payloadSha256)throw Error('WORKFLOW_PAYLOAD_BINDING_CHANGED');
 const npcId=row.payload.character.npcId,replace=isReplaceSchema(row.schemaVersion);
 const id=key=>'workflow.task.'+sha(canonical([row.officialRequestId,key]));
 const tasks=[];
 const add=(key,type,workerType,dep,extra={})=>tasks.push({id:id(key),key,type,workerType,dependsOn:dep===null?null:id(dep),input:{npcId,...extra}});
 if(replace)add('delete','npc.delete-folder','file',null,{scope:'exact-npc-folder',preserveRequestHistory:true,noOldDataReuse:true});
 add('preset','npc.preset','document',replace?'delete':null,{mode:replace?'fresh-replacement':'new',noOldDataReuse:true});
 add('planning_overview','npc.plan-overview','document','preset');
 add('planning_portrait_art','npc.plan-portrait-art','document','planning_overview');
 add('planning_representative','npc.plan-representative','document','planning_portrait_art');
 add('portrait_representative','npc.portrait-representative','image','planning_representative');
 const basic=isBasicSchema(row.schemaVersion);
 const required=new Set(['idle',...(basic&&row.payload.includeCombat?['combat']:isPoseSelectionSchema(row.schemaVersion)?row.payload.motionSelection.map(r=>r.posePreset):[])]);
 const poses=motionSelectionPoses.filter(pose=>required.has(pose));
 for(const pose of poses){
  const lane=motionPoseRegistry[pose],center='portrait_pixel_'+pose+'_reference';
  add(lane.planKey,lane.planType,'document','planning_representative');
  add(lane.referenceKey,lane.referenceType,'image',lane.planKey,basic?{}:{imageStyle:'pixel-sprite.v1'});
  if(!basic)add(center,'npc.portrait-pixel','postprocess',lane.referenceKey,{posePreset:pose,direction:null});
 }
 if(basic)return {payload,tasks};
 for(const direction of ['n','ne','e','se','s','sw','w','nw'])for(const pose of poses){
  const lane=motionPoseRegistry[pose];
  add(lane.directionPrefix+direction,lane.directionType,'image','portrait_pixel_'+pose+'_reference',{direction,imageStyle:'pixel-sprite.v1'});
 }
 return {payload,tasks};
}
