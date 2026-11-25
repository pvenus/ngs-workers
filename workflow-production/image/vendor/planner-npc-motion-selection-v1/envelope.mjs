/** Independent intake validation: typed One-Stop registration, never execution or deletion. */
import {createHash} from 'node:crypto';

export const requestType='npc.workflow',schemaVersion='ngs.npc-one-stop.v1',replaceSchemaVersion='ngs.npc-one-stop.replace.v1';
export const selectionSchemaVersion='ngs.npc-one-stop.selection.v1',
 selectionReplaceSchemaVersion='ngs.npc-one-stop.selection.replace.v1',
 poseSelectionSchemaVersion='ngs.npc-one-stop.selection.v2',
 poseSelectionReplaceSchemaVersion='ngs.npc-one-stop.selection.replace.v2';
export const legacyOneStopSchemas=Object.freeze([schemaVersion,replaceSchemaVersion]);
export const poseSelectionSchemas=Object.freeze([poseSelectionSchemaVersion,poseSelectionReplaceSchemaVersion]);
export const selectionSchemas=Object.freeze([selectionSchemaVersion,selectionReplaceSchemaVersion,...poseSelectionSchemas]);
export const basicSchemaVersion='ngs.npc-one-stop.basic.v1';
export const isBasicSchema=schema=>schema===basicSchemaVersion;
export const oneStopSchemas=Object.freeze([...legacyOneStopSchemas,...selectionSchemas,basicSchemaVersion]);
export const isSelectionSchema=schema=>selectionSchemas.includes(schema);
export const isPoseSelectionSchema=schema=>poseSelectionSchemas.includes(schema);
export const isReplaceSchema=schema=>schema===replaceSchemaVersion||schema===selectionReplaceSchemaVersion||schema===poseSelectionReplaceSchemaVersion;
export const motionPresetIdPattern='^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$';
// Pose capabilities are registered once for all motion presets. v2 expands
// only the requested poses; v1 retains its deployed idle appearance graph.
export const motionPoseRegistry=Object.freeze({
 idle:Object.freeze({
  planKey:'planning_idle',planType:'npc.plan-idle',
  referenceKey:'portrait_idle_reference',referenceType:'npc.portrait-idle-reference',
  directionPrefix:'portrait_',directionType:'npc.portrait-direction',
 }),
 combat:Object.freeze({
  planKey:'planning_combat',planType:'npc.plan-combat',
  referenceKey:'portrait_combat_reference',referenceType:'npc.portrait-combat-reference',
  directionPrefix:'portrait_combat_',directionType:'npc.portrait-combat-direction',
 }),
});
export const motionSelectionPoses=Object.freeze(Object.keys(motionPoseRegistry));
const motionPresetId=new RegExp(motionPresetIdPattern);
const assetSetHash=/^[a-f0-9]{64}$/;
const selectionFields=['presetId','posePreset','assetSetSha256'];
export const maxPayloadBytes=64*1024;
export const submitTool='request_submit',cancelTool='request_cancel',updateStatusTool='request_update_status';
export const workflowStatuses=Object.freeze(['planned','completed','failed']);
export const ident=value=>typeof value==='string'&&value.length>=1&&value.length<=200&&!/[^A-Za-z0-9._:-]/.test(value);
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const keys=(value,names)=>object(value)&&Object.keys(value).length===names.length&&names.every(k=>Object.hasOwn(value,k));
const length=value=>typeof value==='string'&&value.isWellFormed()?[...value].length:-1;
const fits=(value,min,max)=>{const n=length(value);return n>=min&&n<=max;};
const invalid=()=>Error('NGS_ONE_STOP_PAYLOAD_INVALID');

export function canonical(value){
 if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
 if(object(value))return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';
 return JSON.stringify(value);
}
export const sha=value=>createHash('sha256').update(value).digest('hex');

export const motionSelectionSlot=presetId=>sha(presetId).slice(0,16);

export function validateMotionSelection(selection) {
 if(!Array.isArray(selection)||!selection.length)throw invalid();
 const ids=new Set(),slots=new Set();
 for(const entry of selection){
  if(!keys(entry,selectionFields)||typeof entry.presetId!=='string'||
     entry.presetId.trim()!==entry.presetId||!motionPresetId.test(entry.presetId)||
     !motionSelectionPoses.includes(entry.posePreset)||typeof entry.assetSetSha256!=='string'||
     entry.assetSetSha256.length!==64||!assetSetHash.test(entry.assetSetSha256))throw invalid();
  const folded=entry.presetId.toLowerCase(),slot=motionSelectionSlot(entry.presetId);
  if(ids.has(folded)||slots.has(slot))throw invalid();
  ids.add(folded);slots.add(slot);
 }
}

export function validateOneStop(value,projectId){
 const fields=['schemaVersion','projectId','intent','character','sourceRefs'];
 if(!object(value)||!oneStopSchemas.includes(value.schemaVersion))throw invalid();
 const selected=isSelectionSchema(value.schemaVersion);
 const basic=isBasicSchema(value.schemaVersion);
 const hasCombatRequirements=Object.hasOwn(value,'combatRequirements');
 const validShape=basic?keys(value,[...fields,'includeCombat',...(hasCombatRequirements?['combatRequirements']:[])])&&
  typeof value.includeCombat==='boolean'&&(!hasCombatRequirements||(value.includeCombat===true&&
   fits(value.combatRequirements,1,900)&&value.combatRequirements===value.combatRequirements.trim()&&
   !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(value.combatRequirements))):selected?keys(value,[...fields,'motionSelection']):
  fields.every(k=>Object.hasOwn(value,k))&&typeof value.includeCombat==='boolean'&&
  !Object.keys(value).some(k=>!fields.includes(k)&&!['includeCombat','motionPresets','combatMotionPresets'].includes(k))&&
  Object.hasOwn(value,'includeCombat');
 if(!validShape||projectId!=='binari'||value.projectId!==projectId||
    !fits(value.intent,5,3500)||!value.intent.trim()||!Array.isArray(value.sourceRefs)||!value.sourceRefs.length||
    value.sourceRefs.length>maxPayloadBytes||!value.sourceRefs.every(v=>fits(v,1,300)&&v.trim()))throw invalid();
 const c=value.character;
 if(!keys(c,['npcId','name','slug','role','actId','chapterId'])||
    !['npcId','slug','actId','chapterId'].every(k=>ident(c[k]))||
    !fits(c.name,1,60)||!c.name.trim()||!fits(c.role,1,300)||!c.role.trim())throw invalid();
 if(basic&&(!/^[a-z0-9][a-z0-9._-]{1,79}$/.test(c.slug)||c.npcId!=='npc.'+c.slug||
   !['act1','act01'].includes(c.actId)||!/^(?:ch|chapter0)[1-5]$/.test(c.chapterId)))throw invalid();
 const replace=isReplaceSchema(value.schemaVersion);
 if(c.npcId.startsWith('character.')){
  // Imported, enrolled NPCs keep their canonical legacy owner ID on replacement.
  if(!replace||c.npcId.length<12||c.npcId.length>90||!/^character\.[a-z0-9]+(?:[-._][a-z0-9]+)*$/.test(c.npcId)||
     c.slug!==c.npcId.slice('character.'.length)||
     !['act1','act01'].includes(c.actId)||!/^(?:ch|chapter0)[1-5]$/.test(c.chapterId))throw invalid();
 }else if(!/^npc\.[A-Za-z0-9][A-Za-z0-9._-]*$/i.test(c.npcId)||
   (replace&&!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(c.slug)))throw invalid();
 if(selected){
  if(replace||!Array.isArray(value.motionSelection)||value.motionSelection.length)validateMotionSelection(value.motionSelection);
 }
 else if(!basic){
  if(Object.hasOwn(value,'motionPresets')&&(!keys(value.motionPresets,['walk','run'])||
     value.motionPresets.walk!=='walk'||value.motionPresets.run!=='run'))throw invalid();
  if(value.includeCombat){
   const names=value.combatMotionPresets;
   if(!Array.isArray(names)||!names.length||names.length>16||new Set(names).size!==names.length||
      !names.every(v=>ident(v)&&v.length<=128&&/^[A-Za-z0-9]/.test(v)))throw invalid();
  }else if(Object.hasOwn(value,'combatMotionPresets'))throw invalid();
 }
 const payload=canonical(value);
 if(Buffer.byteLength(payload)>maxPayloadBytes)throw invalid();
 return payload;
}

export function validateSubmit(value) {
 if(!keys(value,['sourceId','requestType','idempotencyKey','payload'])||
    value.sourceId!=='local'||value.requestType!==requestType||!ident(value.idempotencyKey))
  throw Error('REQUEST_SUBMIT_INVALID');
 const projectId='binari';
 const payload=validateOneStop(value.payload,projectId);
 return {sourceId:value.sourceId,projectId,idempotencyKey:value.idempotencyKey,payload,payloadSha256:sha(payload),
  schemaVersion:value.payload.schemaVersion,npcId:value.payload.character.npcId,npcSlug:value.payload.character.slug};
}

const officialId=value=>typeof value==='string'&&value.length===81&&/^request\.official\.[a-f0-9]{64}$/.test(value);
export function validateCancel(value){
 if(!keys(value,['officialRequestId'])||!officialId(value.officialRequestId))throw Error('REQUEST_CANCEL_INVALID');
 return value;
}
export function validateStatusUpdate(value){
 if(!keys(value,['officialRequestId','status'])||!officialId(value.officialRequestId)||!workflowStatuses.includes(value.status))
  throw Error('REQUEST_STATUS_UPDATE_INVALID');
 return value;
}
