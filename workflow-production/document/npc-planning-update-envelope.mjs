/** Immutable intake for one existing NPC planning document. No storage or generation. */
export const npcPlanningUpdateType='npc.planning.update';
export const npcPlanningUpdateSchema='ngs.npc-planning-update.v1';
export const npcPlanningUpdateTaskKey='planning_update';
export const npcPlanningRoles=Object.freeze(['overview','portrait.art','portrait.representative','portrait.idle']);
export const npcPlanningUpdateIdPattern='^(?:npc|character)\\.[A-Za-z0-9][A-Za-z0-9._-]{0,120}$';
export const npcPlanningRequirementsLimit=4000;
const fields=['npcId','projectId','requirements','role','schemaVersion'];
const id=new RegExp(npcPlanningUpdateIdPattern);
const control=/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u;

export function validateNPCPlanningUpdate(value,projectId){
 const bad=()=>Error('NGS_NPC_PLANNING_UPDATE_PAYLOAD_INVALID');
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==fields.length||
  !fields.every(k=>Object.hasOwn(value,k))||value.schemaVersion!==npcPlanningUpdateSchema||
  projectId!=='binari'||value.projectId!==projectId||!npcPlanningRoles.includes(value.role)||
  typeof value.npcId!=='string'||value.npcId!==value.npcId.trim()||!id.test(value.npcId))throw bad();
 const text=value.requirements;
 if(typeof text!=='string'||!text.isWellFormed()||!text.length||text!==text.trim()||
  [...text].length>npcPlanningRequirementsLimit||control.test(text))throw bad();
 return JSON.stringify(Object.fromEntries(fields.map(k=>[k,value[k]])));
}
