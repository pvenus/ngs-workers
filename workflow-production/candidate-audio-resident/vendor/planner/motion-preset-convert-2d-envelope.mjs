/** Immutable intent for one non-NPC conversion. The source remains read-only. */
export const motion2DRequestType='motion-preset.convert-2d';
export const motion2DSchema='ngs.motion-preset-convert-2d.v1';
export const motionPresetIdPattern='^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$';
export const variantPresetIdPattern='^[a-z][a-z0-9-]{1,63}$';
export const motion2DLabelLimit=120;
const source=new RegExp(motionPresetIdPattern),variant=new RegExp(variantPresetIdPattern);
const fields=['schemaVersion','projectId','sourcePresetId','variantPresetId','label'];

export function validateMotion2D(body,projectId){
 const ident=(value,pattern)=>typeof value==='string'&&value.trim()===value&&pattern.test(value);
 if(!body||typeof body!=='object'||Array.isArray(body)||Object.keys(body).length!==fields.length||
    !fields.every(k=>Object.hasOwn(body,k))||projectId!=='binari'||
    body.schemaVersion!==motion2DSchema||body.projectId!==projectId||
    !ident(body.sourcePresetId,source)||!ident(body.variantPresetId,variant)||
    body.sourcePresetId.toLowerCase()===body.variantPresetId.toLowerCase()||
    typeof body.label!=='string'||body.label.trim()!==body.label||!body.label.length||
    [...body.label].length>motion2DLabelLimit||/[\p{Cc}\p{Cs}\p{Zl}\p{Zp}]/u.test(body.label))
  throw Error('NGS_MOTION_PRESET_CONVERT_2D_PAYLOAD_INVALID');
 return JSON.stringify(Object.fromEntries(fields.toSorted().map(k=>[k,body[k]])));
}
