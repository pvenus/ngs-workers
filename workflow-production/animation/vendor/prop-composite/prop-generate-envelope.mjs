/** Formal one-prop request: authoring intent, not worker/render knobs. Copy verbatim to Planner. */
export const propGenerateType='prop.generate',propGenerateSchema='ngs.prop-generate.v1';
export const propGenerateInvalid='NGS_PROP_GENERATE_PAYLOAD_INVALID',propGenerateUnavailable='PROP_GENERATE_RUNTIME_UNAVAILABLE';
export const propGenerateMotionUnavailable='PROP_GENERATE_MOTION_UNAVAILABLE';
export const exactProp=(value,keys)=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&
 Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k));
export const propHash=v=>typeof v==='string'&&v.length===64&&/^[0-9a-f]{64}$/u.test(v);
const slug=v=>typeof v==='string'&&v.length>=2&&v.length<=80&&v.trim()===v&&/^[a-z0-9]+(?:[-._][a-z0-9]+)*$/u.test(v);
const fields=['schemaVersion','projectId','propId','intent','designContextSha256'];
export function validatePropGenerate(v,project){
 if(!(exactProp(v,fields)||exactProp(v,[...fields,'motion']))||v.schemaVersion!==propGenerateSchema||
    project!=='binari'||v.projectId!==project||!slug(v.propId)||!propHash(v.designContextSha256)||
    Object.hasOwn(v,'motion')&&!['still','subtle'].includes(v.motion)||typeof v.intent!=='string'||
    !v.intent.isWellFormed()||!v.intent.length||v.intent.trim()!==v.intent||Buffer.byteLength(v.intent)>4000||
    /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(v.intent))throw Error(propGenerateInvalid);
 return JSON.stringify(Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])));
}
export const propGeneratePayloadSchema={type:'object',properties:{
 schemaVersion:{const:propGenerateSchema},projectId:{const:'binari'},propId:{type:'string',minLength:2,maxLength:80,pattern:'^[a-z0-9]+(?:[-._][a-z0-9]+)*$'},
 intent:{type:'string',minLength:1,maxLength:4000},designContextSha256:{type:'string',pattern:'^[a-f0-9]{64}$'},
 motion:{enum:['still','subtle']},
},required:fields,additionalProperties:false};

export const propRegenerateType='prop.regenerate',propRegenerateSchema='ngs.prop-regenerate.v1';
export const propAnimationType='prop.animation.generate',propAnimationSchema='ngs.prop-animation-generate.v1';
export const propMutationUnavailable='PROP_MUTATION_RUNTIME_UNAVAILABLE';
export const propTypes=Object.freeze([propGenerateType,propRegenerateType,propAnimationType]);
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
 validateMutation(v,project,Object.hasOwn(v??{},'motion')?[...regenerateFields,'motion']:regenerateFields,propRegenerateSchema);
 if(Object.hasOwn(v,'motion')&&!['still','subtle'].includes(v.motion)||!clean(v.intent,4000)||!propHash(v.designContextSha256))throw Error(propGenerateInvalid);
 return stable(v);
}
export function validatePropAnimation(v,project){
 validateMutation(v,project,animationFields,propAnimationSchema);
 if(!slug(v.motionId)||!clean(v.prompt,2000)||/\bembedding:/iu.test(v.prompt))throw Error(propGenerateInvalid);
 return stable(v);
}
const sourceSchema={type:'object',properties:{revision:{type:'integer',minimum:1,maximum:Number.MAX_SAFE_INTEGER-1},
 sha256:{type:'string',pattern:'^[a-f0-9]{64}$'}},required:['revision','sha256'],additionalProperties:false};
export const propRegeneratePayloadSchema={type:'object',properties:{...propGeneratePayloadSchema.properties,
 schemaVersion:{const:propRegenerateSchema},source:sourceSchema},required:regenerateFields,additionalProperties:false};
export const propAnimationPayloadSchema={type:'object',properties:{schemaVersion:{const:propAnimationSchema},projectId:{const:'binari'},
 propId:propGeneratePayloadSchema.properties.propId,source:sourceSchema,motionId:propGeneratePayloadSchema.properties.propId,
 prompt:{type:'string',minLength:1,maxLength:2000}},required:animationFields,additionalProperties:false};
export const propValidator=Object.freeze({[propGenerateType]:validatePropGenerate,[propRegenerateType]:validatePropRegenerate,[propAnimationType]:validatePropAnimation});
