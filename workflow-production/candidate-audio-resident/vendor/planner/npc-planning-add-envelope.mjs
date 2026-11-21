/** Exact request for creating a separate custom NPC planning document. */
import {conceptCategoryAddIdPattern,validRequiredSections} from './concept-category-envelope.mjs';
import {npcPlanningUpdateIdPattern} from './npc-planning-update-envelope.mjs';

export const npcPlanningAddType='npc.planning.add';
export const npcPlanningAddSchema='ngs.npc-planning-add.v1';
export const npcPlanningAddTaskKey='planning_add';
export const npcPlanningAddIdPattern=npcPlanningUpdateIdPattern;
export const npcPlanningAddCategoryIdPattern=conceptCategoryAddIdPattern;
const npcId=new RegExp(npcPlanningAddIdPattern);
const categoryId=new RegExp(npcPlanningAddCategoryIdPattern);
const fields=['categoryId','npcId','outline','projectId','requiredSections','requirements','schemaVersion','title'];
const fits=(text,min,max)=>typeof text==='string'&&text.isWellFormed()&&text===text.trim()&&
 !/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/u.test(text)&&[...text].length>=min&&[...text].length<=max;

export function validateNPCPlanningAdd(value,projectId){
 const bad=()=>Error('NGS_NPC_PLANNING_ADD_PAYLOAD_INVALID');
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==fields.length||
  !fields.every(k=>Object.hasOwn(value,k))||value.schemaVersion!==npcPlanningAddSchema||projectId!=='binari'||
  value.projectId!==projectId||typeof value.npcId!=='string'||value.npcId!==value.npcId.trim()||
  !npcId.test(value.npcId)||typeof value.categoryId!=='string'||value.categoryId!==value.categoryId.trim()||
  !categoryId.test(value.categoryId)||!fits(value.title,1,120)||/[\t\r\n\u2028\u2029]/u.test(value.title)||
  !fits(value.outline,1,4000)||!fits(value.requirements,0,4000)||!validRequiredSections(value.requiredSections))throw bad();
 return JSON.stringify(Object.fromEntries(fields.map(k=>[k,value[k]])));
}
