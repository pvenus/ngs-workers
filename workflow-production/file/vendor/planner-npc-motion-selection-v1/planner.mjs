// Selection v1 foreground projection of the canonical workflow planner.
// Media tasks stay in their own role and are not reconstructed here.
import {canonical,sha,validateOneStop,isReplaceSchema,isSelectionSchema} from './envelope.mjs';
export {isSelectionSchema};
const fields=['officialRequestId','projectId','requestType','schemaVersion','payload','payloadSha256','status','registeredAt'];
export function buildPlan(row){
 if(!row||Object.keys(row).length!==fields.length||!fields.every(k=>Object.hasOwn(row,k))||
  typeof row.officialRequestId!=='string'||row.officialRequestId.length!==81||!/^request\.official\.[a-f0-9]{64}$/.test(row.officialRequestId)||
  row.requestType!=='npc.workflow'||row.projectId!=='binari'||row.status!=='registered'||
  !Number.isSafeInteger(row.registeredAt)||row.registeredAt<0||!isSelectionSchema(row.schemaVersion))
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
 add('planning_idle','npc.plan-idle','document','planning_representative');
 add('portrait_idle_reference','npc.portrait-idle-reference','image','planning_idle',{imageStyle:'pixel-sprite.v1'});
 add('portrait_pixel_idle_reference','npc.portrait-pixel','postprocess','portrait_idle_reference',{posePreset:'idle',direction:null});
 for(const direction of ['n','ne','e','se','s','sw','w','nw'])add('portrait_'+direction,'npc.portrait-direction','image','portrait_pixel_idle_reference',{direction,imageStyle:'pixel-sprite.v1'});
 return {payload,tasks};
}
