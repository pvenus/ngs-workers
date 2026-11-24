import {createHash} from 'node:crypto';
import {propGenerateType,propGenerateSchema,propRegenerateType,propRegenerateSchema,
 validatePropGenerate,validatePropRegenerate} from './vendor/prop-composite-v1/prop-generate-envelope.mjs';

export const propDesignKey='prop_design';
export const propDesignOperations=Object.freeze({
 'prop.generate.design':Object.freeze({requestType:propGenerateType,schema:propGenerateSchema,validate:validatePropGenerate}),
 'prop.regenerate.design':Object.freeze({requestType:propRegenerateType,schema:propRegenerateSchema,validate:validatePropRegenerate})
});
export const propDesignSchema='ngs.prop-design-spec.v1';
export const propDesignCapability='workflow.prop-design-write.v1';
export const propDesignPath='/v1/props/ownership/workflow-design';
export const projectId='binari';
export const masterCategory='expression';
export const propCategory='category.add.26710502f5713d99018b77a29384d5da';
const masterFields=[['visualStyle','ngs.character-style-contract.v1'],['visualProhibitions','ngs.character-style-constraints.v1']];
const requiredHeadings=['형태와 식별','재료와 색','상태 사이의 일관성','납품과 검수'];
export const sha=v=>createHash('sha256').update(v).digest('hex');
export const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':
 v!==null&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
export const exact=(v,ks)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===ks.length&&ks.every(k=>Object.hasOwn(v,k));
const hash=v=>typeof v==='string'&&v.length===64&&/^[a-f0-9]{64}$/u.test(v);
const official=v=>typeof v==='string'&&v.length===81&&/^request\.official\.[a-f0-9]{64}$/u.test(v);
const bounded=(v,max)=>typeof v==='string'&&v.isWellFormed()&&v.trim().length>0&&Buffer.byteLength(v)<=max&&
 !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u.test(v);
export const requirePropDesign=(ok,code='WORKFLOW_PROP_DESIGN_BINDING_INVALID')=>{if(!ok)throw Error(code);};
export const isPropDesignOperation=v=>typeof v==='string'&&Object.hasOwn(propDesignOperations,v);
export function propDesignTaskId(requestId){
 requirePropDesign(official(requestId));
 return 'workflow.task.'+sha(canonical([requestId,propDesignKey]));
}
export function expectedInput(payload,operation){
 requirePropDesign(isPropDesignOperation(operation));
 const spec=propDesignOperations[operation],wire=spec.validate(payload,projectId);
 requirePropDesign(wire===canonical(payload));
 return {propId:payload.propId,designContextSha256:payload.designContextSha256,intent:payload.intent,
  motion:payload.motion??'still',...(spec.requestType===propRegenerateType?{source:payload.source}:{})};
}
export function verifiedDesignInput(doc,input){
 const need=(ok)=>requirePropDesign(ok,'PROP_DESIGN_CONTEXT_INVALID');
 need(exact(doc,['context','input'])&&canonical(doc.input)===canonical(input));
 const ctx=doc.context;
 need(exact(ctx,['schemaVersion','projectId','sources','contextSha256'])&&ctx.schemaVersion==='ngs.prop-image-design.v1'&&
  ctx.projectId===projectId&&ctx.contextSha256===input.designContextSha256&&hash(ctx.contextSha256)&&
  Array.isArray(ctx.sources)&&ctx.sources.length===2);
 const [master,prop]=ctx.sources;
 need(exact(master,['role','categoryId','sha256','fields'])&&master.role==='master-design'&&master.categoryId===masterCategory&&
  hash(master.sha256)&&Array.isArray(master.fields)&&master.fields.length===masterFields.length);
 for(const [i,[id,schema]] of masterFields.entries()){
  const field=master.fields[i];
  need(exact(field,['id','sha256','text'])&&field.id===id&&hash(field.sha256)&&bounded(field.text,12000)&&sha(field.text)===field.sha256);
  let parsed;try{parsed=JSON.parse(field.text);}catch{need(false);}
  need(parsed&&Object.getPrototypeOf(parsed)===Object.prototype&&parsed.schemaVersion===schema);
 }
 need(sha(canonical(master.fields))===master.sha256);
 need(exact(prop,['role','categoryId','sha256','markdown'])&&prop.role==='prop-design'&&prop.categoryId===propCategory&&
  hash(prop.sha256)&&bounded(prop.markdown,24000)&&sha(prop.markdown)===prop.sha256);
 const md=prop.markdown.replaceAll('\r\n','\n');
 need(md.split('\n')[0]==='# 프랍 디자인 설계'&&
  canonical([...md.matchAll(/^## (.+)$/gm)].map(m=>m[1]))===canonical(requiredHeadings));
 need(sha(canonical(ctx.sources.map(s=>[s.role,s.categoryId,s.sha256])))===ctx.contextSha256);
 // Old artifact is a server-side comparator only, never prompt/file authority.
 return {propId:input.propId,intent:input.intent,motion:input.motion,designContextSha256:ctx.contextSha256,sources:ctx.sources};
}
const ints=(n,lo,hi)=>Number.isSafeInteger(n)&&n>=lo&&n<=hi;
const boxKeys=['xPermille','yPermille','widthPermille','heightPermille'];
const useBox=(v)=>{
 if(v===null)return null;
 requirePropDesign(exact(v,boxKeys)&&ints(v.xPermille,0,999)&&ints(v.yPermille,0,999)&&
  ints(v.widthPermille,1,1000-v.xPermille)&&ints(v.heightPermille,1,1000-v.yPermille),'PROP_DESIGN_HOTSPOT_INVALID');
 return Object.fromEntries(boxKeys.map(k=>[k,v[k]]));
};
const modelText=(s,limit)=>bounded(s,limit)&&s===s.trim()&&!/\bembedding:/iu.test(s);
const allOutput=['title','appearancePrompt','motionPrompt','interaction'];
export function designFromModel(output,input){
 const want=(ok)=>requirePropDesign(ok,'PROP_DESIGN_OUTPUT_INVALID');
 want(exact(output,allOutput)&&typeof output.title==='string'&&output.title.isWellFormed()&&output.title===output.title.trim()&&
  [...output.title].length>=1&&[...output.title].length<=160&&!/[\p{Cc}\u2028\u2029]/u.test(output.title)&&
  modelText(output.appearancePrompt,4000)&&['still','subtle'].includes(input.motion)&&
  (input.motion==='still'?output.motionPrompt===null:modelText(output.motionPrompt,2000))&&
  exact(output.interaction,['pixelOpen','detailTrigger']));
 return {schemaVersion:propDesignSchema,propId:input.propId,designContextSha256:input.designContextSha256,
  title:output.title,appearancePrompt:output.appearancePrompt,motion:input.motion,motionPrompt:output.motionPrompt,
  interaction:{pixelOpen:useBox(output.interaction.pixelOpen),detailTrigger:useBox(output.interaction.detailTrigger)}};
}
const boxJson={type:'object',properties:Object.fromEntries(boxKeys.map(k=>[k,{type:'integer',minimum:0,maximum:1000}])),
 required:boxKeys,additionalProperties:false};
const optionalBox={anyOf:[boxJson,{type:'null'}]};
export function propDesignOutputSchema(motion){
 requirePropDesign(['still','subtle'].includes(motion));
 return {type:'object',properties:{title:{type:'string'},appearancePrompt:{type:'string'},
  motionPrompt:{type:motion==='still'?'null':'string'},
  interaction:{type:'object',properties:{pixelOpen:optionalBox,detailTrigger:optionalBox},
   required:['pixelOpen','detailTrigger'],additionalProperties:false}},required:allOutput,additionalProperties:false};
}
export const propDesignResultRef=artifact=>'prop.design-spec.'+sha(canonical(artifact));
