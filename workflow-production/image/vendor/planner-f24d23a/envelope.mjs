/** Independent intake validation: typed One-Stop registration, never execution or deletion. */
import {createHash} from 'node:crypto';

export const requestType='npc.workflow',schemaVersion='ngs.npc-one-stop.v1',replaceSchemaVersion='ngs.npc-one-stop.replace.v1';
export const oneStopSchemas=Object.freeze([schemaVersion,replaceSchemaVersion]);
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

export function validateOneStop(value,projectId){
 const fields=['schemaVersion','projectId','intent','character','sourceRefs','includeCombat'];
 if(!object(value)||!fields.every(k=>Object.hasOwn(value,k))||
    Object.keys(value).some(k=>!fields.includes(k)&&!['motionPresets','combatMotionPresets'].includes(k))||
    !oneStopSchemas.includes(value.schemaVersion)||projectId!=='binari'||value.projectId!==projectId||typeof value.includeCombat!=='boolean'||
    !fits(value.intent,5,3500)||!value.intent.trim()||!Array.isArray(value.sourceRefs)||!value.sourceRefs.length||
    value.sourceRefs.length>maxPayloadBytes||!value.sourceRefs.every(v=>fits(v,1,300)&&v.trim()))throw invalid();
 const c=value.character;
 if(!keys(c,['npcId','name','slug','role','actId','chapterId'])||
    !['npcId','slug','actId','chapterId'].every(k=>ident(c[k]))||
    !fits(c.name,1,60)||!c.name.trim()||!fits(c.role,1,300)||!c.role.trim())throw invalid();
 if(value.schemaVersion===replaceSchemaVersion&&
  (!/^(?:npc|character)\.[A-Za-z0-9][A-Za-z0-9._-]*$/.test(c.npcId)||
   !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(c.slug)))throw invalid();
 if(Object.hasOwn(value,'motionPresets')&&(!keys(value.motionPresets,['walk','run'])||
    value.motionPresets.walk!=='walk'||value.motionPresets.run!=='run'))throw invalid();
 if(value.includeCombat){
  const names=value.combatMotionPresets;
  if(!Array.isArray(names)||!names.length||names.length>16||new Set(names).size!==names.length||
     !names.every(v=>ident(v)&&v.length<=128&&/^[A-Za-z0-9]/.test(v)))throw invalid();
 }else if(Object.hasOwn(value,'combatMotionPresets'))throw invalid();
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
