/** Independent intake validation: typed One-Stop registration, never execution or deletion. */
import {createHash} from 'node:crypto';

export const requestType='npc.workflow',schemaVersion='ngs.npc-one-stop.v1',replaceSchemaVersion='ngs.npc-one-stop.replace.v1';
export const selectionSchemaVersion='ngs.npc-one-stop.selection.v1',
 selectionReplaceSchemaVersion='ngs.npc-one-stop.selection.replace.v1';
export const legacyOneStopSchemas=Object.freeze([schemaVersion,replaceSchemaVersion]);
export const selectionSchemas=Object.freeze([selectionSchemaVersion,selectionReplaceSchemaVersion]);
export const oneStopSchemas=Object.freeze([...legacyOneStopSchemas,...selectionSchemas]);
export const isSelectionSchema=schema=>selectionSchemas.includes(schema);
export const isReplaceSchema=schema=>schema===replaceSchemaVersion||schema===selectionReplaceSchemaVersion;
export const motionPresetIdPattern='^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$';
export const motionSelectionPoses=Object.freeze(['idle','combat']);
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

function validateMotionSelection(selection) {
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
 const validShape=selected?keys(value,[...fields,'motionSelection']):
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
 if(isReplaceSchema(value.schemaVersion)&&
  (!/^(?:npc|character)\.[A-Za-z0-9][A-Za-z0-9._-]*$/.test(c.npcId)||
   !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(c.slug)))throw invalid();
 if(selected)validateMotionSelection(value.motionSelection);
 else {
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
