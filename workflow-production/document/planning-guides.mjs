/** Only fixed, versioned Markdown in the worker package can shape the prompt. */
import {readFile} from 'node:fs/promises';
import {planningHeadings} from './planning-markdown.mjs';

export async function readNpcPlanningGuide(operation){
 if(!Object.hasOwn(planningHeadings,operation))throw Error('WORKFLOW_PLAN_GUIDE_UNKNOWN');
 let guide;
 try{
  const bytes=await readFile(new URL('./guides/'+operation.slice('npc.plan-'.length)+'.md',import.meta.url));
  if(bytes.length>16*1024)throw Error();
  guide=new TextDecoder('utf-8',{fatal:true}).decode(bytes).trim();
 }catch{throw Error('WORKFLOW_PLAN_GUIDE_UNAVAILABLE');}
 if(!guide.startsWith('# ')||!guide.includes('\n- '))throw Error('WORKFLOW_PLAN_GUIDE_INVALID');
 return guide;
}

export function requireCombatWeapon(sections){
 const lines=sections['전투 자세'].split(/\r?\n/u)
  .filter(line=>/^무기:[ \t]*/u.test(line));
 const weapon=lines.length===1?lines[0].replace(/^무기:\s*/u,'').trim():'';
 if(!/[\p{L}\p{N}]/u.test(weapon)||
    /^(?:없음|무기 없음|미정|해당 없음|무장 없음|비무장|맨손|none|unarmed|tbd|n\/a)(?:[\s.,;!?]|$)/iu.test(weapon))
  throw Error('WORKFLOW_PLAN_COMBAT_WEAPON_REQUIRED');
}
