/** Formal one-prop request: authoring intent, not worker/render knobs. Copy verbatim to Planner. */
export const propGenerateType='prop.generate',propGenerateSchema='ngs.prop-generate.v1';
export const propGenerateInvalid='NGS_PROP_GENERATE_PAYLOAD_INVALID',propGenerateUnavailable='PROP_GENERATE_RUNTIME_UNAVAILABLE';
export const propGenerateMotionUnavailable='PROP_GENERATE_MOTION_UNAVAILABLE';
export const exactProp=(value,keys)=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&
 Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k));
export const propHash=v=>typeof v==='string'&&v.length===64&&/^[0-9a-f]{64}$/u.test(v);
const slug=v=>typeof v==='string'&&v.length>=2&&v.length<=80&&v.trim()===v&&/^[a-z0-9]+(?:[-._][a-z0-9]+)*$/u.test(v);
export const spiritPresentationProfile='spirit-v1',pixelSourcePresentationProfile='spirit-v2';
export const propPresentationProfiles=Object.freeze([spiritPresentationProfile,pixelSourcePresentationProfile]);
const presentation=v=>Object.hasOwn(v??{},'presentationProfile');
export const propScaleType='prop.scale.generate',propScaleSchema='ngs.prop-scale-generate.v1';
export const scalePercents=Object.freeze([5,10,20,30,40,50,60,70,80,90,100]);
export const validPropScalePercents=v=>Array.isArray(v)&&v.length>0&&v.length<=11&&new Set(v).size===v.length&&Array.from(v).every(n=>scalePercents.includes(n));
const scaleSchema={type:'array',items:{enum:scalePercents},uniqueItems:true,minItems:1,maxItems:11};
const fields=['schemaVersion','projectId','propId','intent','designContextSha256'];
const referenceFields=['propId','revision','sha256'];
export const validPropGenerateReference=(reference,target)=>exactProp(reference,referenceFields)&&
 slug(reference.propId)&&reference.propId!==target&&sourcePin({revision:reference.revision,sha256:reference.sha256});
export function validatePropGenerate(v,project){
 const motion=Object.hasOwn(v??{},'motion'),reference=Object.hasOwn(v??{},'reference');
 if(!exactProp(v,[...fields,...(motion?['motion']:[]),...(reference?['reference']:[]),...(presentation(v)?['presentationProfile']:[]),...(Object.hasOwn(v??{},'scalePercents')?['scalePercents']:[])])||v.schemaVersion!==propGenerateSchema||
    Object.hasOwn(v,'scalePercents')&&(!validPropScalePercents(v.scalePercents)||v.presentationProfile!==pixelSourcePresentationProfile)||
    reference&&!validPropGenerateReference(v.reference,v.propId)||
    presentation(v)&&!propPresentationProfiles.includes(v.presentationProfile)||
    project!=='binari'||v.projectId!==project||!slug(v.propId)||!propHash(v.designContextSha256)||
    Object.hasOwn(v,'motion')&&!['still','subtle'].includes(v.motion)||typeof v.intent!=='string'||
    !v.intent.isWellFormed()||!v.intent.length||v.intent.trim()!==v.intent||Buffer.byteLength(v.intent)>4000||
    /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(v.intent))throw Error(propGenerateInvalid);
 return JSON.stringify(Object.fromEntries(Object.keys(v).sort().map(k=>[k,k==='reference'?Object.fromEntries(referenceFields.map(f=>[f,v.reference[f]])):v[k]])));
}
const generateProperties={
 schemaVersion:{const:propGenerateSchema},projectId:{const:'binari'},propId:{type:'string',minLength:2,maxLength:80,pattern:'^[a-z0-9]+(?:[-._][a-z0-9]+)*$'},
 intent:{type:'string',minLength:1,maxLength:4000},designContextSha256:{type:'string',pattern:'^[a-f0-9]{64}$'},
 motion:{enum:['still','subtle']},presentationProfile:{enum:propPresentationProfiles},
};
export const propGeneratePayloadSchema={type:'object',properties:{...generateProperties,scalePercents:scaleSchema,
 reference:{type:'object',properties:{propId:generateProperties.propId,
  revision:{type:'integer',minimum:1,maximum:Number.MAX_SAFE_INTEGER-1},
  sha256:{type:'string',pattern:'^[a-f0-9]{64}$'}},required:referenceFields,additionalProperties:false},
},required:fields,additionalProperties:false,if:{required:['scalePercents']},
 then:{required:['presentationProfile'],properties:{presentationProfile:{const:pixelSourcePresentationProfile}}}};

export const propRegenerateType='prop.regenerate',propRegenerateSchema='ngs.prop-regenerate.v1';
export const propAnimationType='prop.animation.generate',propAnimationSchema='ngs.prop-animation-generate.v1';
export const candleChromaProfile='candle-chroma-headroom-v1',candleChromaPropId='candle-brass-petal-unlit-clean';
export const propMutationUnavailable='PROP_MUTATION_RUNTIME_UNAVAILABLE';
export const propTypes=Object.freeze([propGenerateType,propRegenerateType,propAnimationType,propScaleType]);
const sourcePin=v=>exactProp(v,['revision','sha256'])&&Number.isSafeInteger(v.revision)&&
 v.revision>=1&&v.revision<Number.MAX_SAFE_INTEGER&&propHash(v.sha256);
const clean=(v,max)=>typeof v==='string'&&v.isWellFormed()&&v.length>0&&v.trim()===v&&Buffer.byteLength(v)<=max&&
 !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(v);
const stable=v=>JSON.stringify(Object.fromEntries(Object.keys(v).sort().map(k=>[k,k==='source'?{revision:v[k].revision,sha256:v[k].sha256}:v[k]])));
const regenerateFields=['schemaVersion','projectId','propId','source','intent','designContextSha256'];
const animationFields=['schemaVersion','projectId','propId','source','motionId','prompt'];
function validateMutation(v,project,keys,schema){
 if(!exactProp(v,keys)||v.schemaVersion!==schema||project!=='binari'||v.projectId!==project||
    !slug(v.propId)||!sourcePin(v.source))throw Error(propGenerateInvalid);
}
export function validatePropRegenerate(v,project){
 validateMutation(v,project,[...regenerateFields,...(Object.hasOwn(v??{},'motion')?['motion']:[]),...(presentation(v)?['presentationProfile']:[])],propRegenerateSchema);
 if(presentation(v)&&!propPresentationProfiles.includes(v.presentationProfile)||Object.hasOwn(v,'motion')&&!['still','subtle'].includes(v.motion)||!clean(v.intent,4000)||!propHash(v.designContextSha256))throw Error(propGenerateInvalid);
 return stable(v);
}
export function validatePropAnimation(v,project){
 const hasProfile=Object.hasOwn(v??{},'renderProfile');
 validateMutation(v,project,hasProfile?[...animationFields,'renderProfile']:animationFields,propAnimationSchema);
 if(!slug(v.motionId)||!clean(v.prompt,2000)||/\bembedding:/iu.test(v.prompt)||
   hasProfile&&(v.renderProfile!==candleChromaProfile||v.propId!==candleChromaPropId))throw Error(propGenerateInvalid);
 return stable(v);
}
const sourceSchema={type:'object',properties:{revision:{type:'integer',minimum:1,maximum:Number.MAX_SAFE_INTEGER-1},
 sha256:{type:'string',pattern:'^[a-f0-9]{64}$'}},required:['revision','sha256'],additionalProperties:false};
export const propRegeneratePayloadSchema={type:'object',properties:{...generateProperties,
 schemaVersion:{const:propRegenerateSchema},source:sourceSchema},required:regenerateFields,additionalProperties:false};
export const propAnimationPayloadSchema={type:'object',properties:{schemaVersion:{const:propAnimationSchema},projectId:{const:'binari'},
 propId:propGeneratePayloadSchema.properties.propId,source:sourceSchema,motionId:propGeneratePayloadSchema.properties.propId,
 prompt:{type:'string',minLength:1,maxLength:2000},renderProfile:{const:candleChromaProfile}},required:animationFields,
 additionalProperties:false,if:{required:['renderProfile']},then:{properties:{propId:{const:candleChromaPropId}}}};
const scaleFields=['schemaVersion','projectId','propId','source','scalePercents'];
export function validatePropScale(v,project){
 validateMutation(v,project,scaleFields,propScaleSchema);
 if(!validPropScalePercents(v.scalePercents))throw Error(propGenerateInvalid);
 return stable(v);
}
export const propScalePayloadSchema={type:'object',properties:{schemaVersion:{const:propScaleSchema},projectId:{const:'binari'},
 propId:generateProperties.propId,source:sourceSchema,scalePercents:scaleSchema},required:scaleFields,additionalProperties:false};
export const propValidator=Object.freeze({[propGenerateType]:validatePropGenerate,[propRegenerateType]:validatePropRegenerate,[propAnimationType]:validatePropAnimation,[propScaleType]:validatePropScale});
