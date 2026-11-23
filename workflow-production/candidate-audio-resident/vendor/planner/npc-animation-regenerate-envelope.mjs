/** Immutable intent for selected motion regeneration on a pre-existing NPC. */
import {canonical,maxPayloadBytes,validateMotionSelection} from './envelope.mjs';

export const npcAnimationRegenerateType='npc.animation.regenerate';
export const npcAnimationRegenerateSchema='ngs.npc-animation.regenerate.v1';
export const npcAnimationRegeneratePoseSchema='ngs.npc-animation.regenerate.v2';
export const npcAnimationRegenerateSchemas=Object.freeze([npcAnimationRegenerateSchema,npcAnimationRegeneratePoseSchema]);
export const npcAnimationOwnerPattern='^(?:npc|character)\\.[A-Za-z0-9][A-Za-z0-9._-]{0,120}$';
const owner=new RegExp(npcAnimationOwnerPattern);
const fields=['schemaVersion','projectId','npcId','motionSelection'];
export function validateNpcAnimationRegenerate(value,projectId){
 const bad=()=>Error('NGS_NPC_ANIMATION_REGENERATE_PAYLOAD_INVALID');
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==fields.length||
  !fields.every(k=>Object.hasOwn(value,k))||projectId!=='binari'||value.projectId!==projectId||
  !npcAnimationRegenerateSchemas.includes(value.schemaVersion)||typeof value.npcId!=='string'||
  value.npcId.trim()!==value.npcId||!owner.test(value.npcId))throw bad();
 try{validateMotionSelection(value.motionSelection);}catch{throw bad();}
 const payload=canonical(value);
 if(Buffer.byteLength(payload)>maxPayloadBytes)throw bad();
 return payload;
}
