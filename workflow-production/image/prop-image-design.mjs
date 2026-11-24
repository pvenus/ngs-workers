/** Read the validated PDS snapshot, bind it to the request, then render one prop image. */
import {createHash} from 'node:crypto';

export const propImageRequestSchema='ngs.prop-image-generate.local.v1';
export const propDesignContextSchema='ngs.prop-image-design.v1';
const masterCategoryId='expression',propCategoryId='category.add.26710502f5713d99018b77a29384d5da';
const exact=(v,ks)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===ks.length&&ks.every(k=>Object.hasOwn(v,k));
const hex=v=>typeof v==='string'&&v.length===64&&/^[a-f0-9]{64}$/u.test(v);
const sha=v=>createHash('sha256').update(v).digest('hex');
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':
 v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const hasControl=/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u;
const text=(v,max)=>typeof v==='string'&&v.isWellFormed()&&v.trim()===v&&v.length>0&&
 Buffer.byteLength(v,'utf8')<=max&&!hasControl.test(v);
const id=v=>typeof v==='string'&&v.length<=128&&v.trim()===v&&/^[a-z][a-z0-9_-]*$/u.test(v);
const fieldIds=['visualStyle','visualProhibitions'];
const headings=['형태와 식별','재료와 색','상태 사이의 일관성','납품과 검수'];
const fail=code=>Error(code);

export function validatePropImageRequest(request){
 if(!exact(request,['schemaVersion','projectId','propId','state','lane','intent','designContextSha256'])||
    request.schemaVersion!==propImageRequestSchema||request.projectId!=='binari'||!id(request.propId)||!id(request.state)||
    !['detail','pixel'].includes(request.lane)||!text(request.intent,4000)||!hex(request.designContextSha256))
  throw fail('PROP_IMAGE_REQUEST_INVALID');
 return request;
}

export function validatePropDesign(context,request){
 if(!exact(context,['schemaVersion','projectId','sources','contextSha256'])||
    context.schemaVersion!==propDesignContextSchema||context.projectId!==request.projectId||
    !Array.isArray(context.sources)||context.sources.length!==2||!hex(context.contextSha256))
  throw fail('PROP_IMAGE_DESIGN_INVALID');
 const [master,prop]=context.sources;
 if(!exact(master,['role','categoryId','sha256','fields'])||master.role!=='master-design'||master.categoryId!==masterCategoryId||
    !hex(master.sha256)||!Array.isArray(master.fields)||master.fields.length!==2||!master.fields.every((row,i)=>
     exact(row,['id','sha256','text'])&&row.id===fieldIds[i]&&text(row.text,12000)&&hex(row.sha256)&&sha(row.text)===row.sha256)||
    sha(canonical(master.fields))!==master.sha256||!exact(prop,['role','categoryId','sha256','markdown'])||
    prop.role!=='prop-design'||prop.categoryId!==propCategoryId||!hex(prop.sha256)||
    typeof prop.markdown!=='string'||!prop.markdown.isWellFormed()||hasControl.test(prop.markdown)||
    !prop.markdown.startsWith('# 프랍 디자인 설계\n')||Buffer.byteLength(prop.markdown)>24000||
    JSON.stringify([...prop.markdown.matchAll(/^## (.+)$/gmu)].map(match=>match[1]))!==JSON.stringify(headings)||
    sha(prop.markdown)!==prop.sha256)throw fail('PROP_IMAGE_DESIGN_INVALID');
 const expected=sha(canonical(context.sources.map(x=>[x.role,x.categoryId,x.sha256])));
 if(expected!==context.contextSha256||request.designContextSha256!==expected)throw fail('PROP_IMAGE_DESIGN_CHANGED');
 return context;
}

export function preparePropImageInput(request,context){
 validatePropImageRequest(request);validatePropDesign(context,request);
 return {projectId:request.projectId,propId:request.propId,state:request.state,lane:request.lane,
  sources:structuredClone(context.sources),contextSha256:context.contextSha256,intent:request.intent};
}

export async function runPropImage({request,pds,renderProp,signal}){
 validatePropImageRequest(request);
 if(typeof pds?.readPropDesign!=='function'||typeof renderProp!=='function')throw fail('PROP_IMAGE_RUNTIME_UNAVAILABLE');
 signal?.throwIfAborted();
 const context=await pds.readPropDesign(request.projectId,{signal});
 const input=preparePropImageInput(request,context);
 signal?.throwIfAborted();
 const sourcePins=input.sources.map(({role,categoryId,sha256})=>({role,categoryId,sha256}));
 const contextSha256=input.contextSha256;
 const output=await renderProp(input,{signal});
 return {output,contextSha256,sourcePins};
}
