import {canonical,sha} from './envelope.mjs';
import {propGenerateType,propRegenerateType,propAnimationType,propScaleType,propTypes,propHash,scalePercents,pixelSourcePresentationProfile} from './prop-generate-envelope.mjs';

export const propKeys=Object.freeze({
 design:'prop_design',base:'prop_base',motion:'prop_motion',detail:'prop_detail',pixel:'prop_pixel',pixelSource:'prop_pixel_source',pixelDetail:'prop_pixel_detail',spirit:'prop_spirit',animDetail:'prop_animation_detail',animPixel:'prop_animation_pixel',publish:'prop_pack_publish',
});
export const propScaleKey=n=>'prop_scale_'+String(n).padStart(3,'0');
const scaleKeys=new Set(scalePercents.map(propScaleKey));
export const isPropScaleKey=key=>scaleKeys.has(key);
// This exact declarative registry is consumed by Planner, not injected by requesters.
// Stage capability is registered even where trusted execution ports are not yet wired.
export const propStageRegistry=Object.freeze({
 [propKeys.design]:Object.freeze({type:'prop.generate.design',workerType:'document',output:'design-spec'}),
 [propKeys.base]:Object.freeze({type:'prop.generate.base',workerType:'image',output:'base-png'}),
 [propKeys.motion]:Object.freeze({type:'prop.generate.motion',workerType:'animation',output:'i2v-rgb'}),
 [propKeys.animDetail]:Object.freeze({type:'prop.generate.motion-detail',workerType:'postprocess',output:'animation-detail-rgba'}),
 [propKeys.animPixel]:Object.freeze({type:'prop.generate.motion-pixel',workerType:'postprocess',output:'animation-pixel-rgba'}),
 [propKeys.detail]:Object.freeze({type:'prop.generate.detail',workerType:'postprocess',output:'detail-rgba'}),
 [propKeys.pixelSource]:Object.freeze({type:'prop.generate.pixel-source',workerType:'image',output:'pixel-source-png'}),
 [propKeys.pixelDetail]:Object.freeze({type:'prop.generate.pixel-detail',workerType:'postprocess',output:'pixel-detail-rgba'}),
 [propKeys.pixel]:Object.freeze({type:'prop.generate.pixel',workerType:'postprocess',output:'pixel-rgba'}),
 [propKeys.spirit]:Object.freeze({type:'prop.generate.spirit',workerType:'postprocess',output:'spirit-rgba'}),
 ...Object.fromEntries([...scaleKeys].map(key=>[key,Object.freeze({type:'prop.scale.derive',workerType:'postprocess',output:'scale-pair'})])),
 [propKeys.publish]:Object.freeze({type:'prop.generate.publish',workerType:'postprocess',output:'package'}),
});
const official=/^request\.official\.[a-f0-9]{64}$/u;
export const propTaskId=(requestId,key)=>'workflow.task.'+sha(canonical([requestId,key]));
export const propRef=(key,digest)=>{
 if(!Object.hasOwn(propStageRegistry,key)||!propHash(digest))throw Error('PROP_STAGE_RECEIPT_INVALID');
 return 'prop.'+propStageRegistry[key].output+'.'+digest;
};
export function isPropRef(key,value){
 return typeof value==='string'&&propHash(value.slice(-64))&&value===propRef(key,value.slice(-64));
}
export const propScaleReplacePublish=propScaleType+'.replace-publish';
export function buildPropGeneratePlan(run,{splitScales=true,replaceScales=splitScales}={}){
 const {officialRequestId:oid,payload}=run;
 if(!propTypes.includes(run.requestType)||typeof oid!=='string'||oid.length!==81||!official.test(oid))
  throw Error('PROP_COMPOSITE_TOPOLOGY_INVALID');
 if(run.requestType===propAnimationType)return buildAppendPlan(run);
 const id=key=>propTaskId(oid,key),tasks=[];
 const scaleTasks=(scope,parent)=>{
  if(!splitScales||!payload.scalePercents)return parent;
  for(const scalePercent of [...payload.scalePercents].sort((a,b)=>a-b)){
   const key=propScaleKey(scalePercent);
   const {type,workerType}=propStageRegistry[key];
   tasks.push({id:id(key),key,type,workerType,
    dependsOn:parent===null?null:id(parent),input:{...scope,scalePercent}});
   parent=key;
  }
  return parent;
 };
 if(run.requestType===propScaleType){
  if(replaceScales&&!splitScales)throw Error('PROP_COMPOSITE_TOPOLOGY_INVALID');
  const scope={propId:payload.propId,source:payload.source},parent=scaleTasks(scope,null);
  tasks.push({id:id(propKeys.publish),key:propKeys.publish,type:replaceScales?propScaleReplacePublish:propScaleType+'.publish',
   workerType:'postprocess',dependsOn:parent===null?null:id(parent),
   input:{...scope,scalePercents:payload.scalePercents,dependsOnTaskIds:tasks.map(x=>x.id)}});
  return tasks;
 }
 const motion=payload.motion==='subtle', mode=motion?'subtle':'still';
 const regenerate=run.requestType===propRegenerateType;
 const profile=payload.presentationProfile,pixelSource=profile===pixelSourcePresentationProfile;
 const scope={...(profile?{presentationProfile:profile}:{}),propId:payload.propId,designContextSha256:payload.designContextSha256,...(regenerate?{source:payload.source}:{})};
 const task=(key,parent,input)=>{
  const {type,workerType}=propStageRegistry[key];
  tasks.push({id:id(key),key,type:regenerate?type.replace(propGenerateType,propRegenerateType):type,workerType,dependsOn:parent===null?null:id(parent),input:{...scope,...input}});
 };
 task(propKeys.design,null,{intent:payload.intent,motion:mode});
 task(propKeys.base,propKeys.design,{designTaskId:id(propKeys.design),
  ...(run.requestType===propGenerateType&&payload.reference?{reference:payload.reference}:{})});
 task(propKeys.detail,propKeys.base,{designTaskId:id(propKeys.design),baseTaskId:id(propKeys.base),sourceTaskId:id(propKeys.base)});
 if(pixelSource){
  task(propKeys.pixelSource,propKeys.detail,{designTaskId:id(propKeys.design),detailTaskId:id(propKeys.detail)});
  task(propKeys.pixelDetail,propKeys.pixelSource,{designTaskId:id(propKeys.design),sourceTaskId:id(propKeys.pixelSource)});
 }
 task(propKeys.pixel,pixelSource?propKeys.pixelDetail:propKeys.detail,{designTaskId:id(propKeys.design),
  detailTaskId:id(pixelSource?propKeys.pixelDetail:propKeys.detail),...(pixelSource?{geometryTaskId:id(propKeys.detail)}:{})});
 if(profile)task(propKeys.spirit,propKeys.detail,{designTaskId:id(propKeys.design),detailTaskId:id(propKeys.detail)});
 if(motion){
  task(propKeys.motion,propKeys.base,{designTaskId:id(propKeys.design),baseTaskId:id(propKeys.base)});
  task(propKeys.animDetail,propKeys.motion,{designTaskId:id(propKeys.design),baseTaskId:id(propKeys.base),sourceTaskId:id(propKeys.motion)});
  task(propKeys.animPixel,propKeys.animDetail,{designTaskId:id(propKeys.design),detailTaskId:id(propKeys.animDetail)});
 }
 const scaleParent=scaleTasks(scope,propKeys.pixelDetail);
 const keys=tasks.map(row=>row.key);
 // Canonical publishing must verify ALL predecessors and their bytes; the
 // scalar indexed parent does not authorize publication by itself.
 task(propKeys.publish,motion?propKeys.animPixel:propKeys.pixel,{dependsOnTaskIds:keys.map(id),...(payload.scalePercents?{scalePercents:payload.scalePercents}:{})});
 if(profile)tasks.at(-1).additionalDependencies=[...(motion?[id(propKeys.pixel)]:[]),id(propKeys.spirit),
  ...(splitScales&&payload.scalePercents?[id(scaleParent)]:[])];
 return tasks;
}

export const propAnimationRegistry=Object.freeze({[propKeys.motion]:'animation',[propKeys.animDetail]:'postprocess',
 [propKeys.animPixel]:'postprocess',[propKeys.publish]:'postprocess'});
function buildAppendPlan({officialRequestId:oid,requestType,payload}){
 const id=key=>propTaskId(oid,key),tasks=[];
 const frozen={propId:payload.propId,source:payload.source};
 function task(key,role,parent,extra={}){
  tasks.push({id:id(key),key,type:requestType+'.'+role,workerType:propAnimationRegistry[key],
   dependsOn:parent?id(parent):null,input:{...frozen,...extra}});
 }
 const {motionId,prompt}=payload;
 task(propKeys.motion,'raw',null,{motionId,prompt});
 task(propKeys.animDetail,'detail',propKeys.motion,{motionId,rawTaskId:id(propKeys.motion)});
 task(propKeys.animPixel,'pixel',propKeys.animDetail,{motionId,detailTaskId:id(propKeys.animDetail)});
 task(propKeys.publish,'publish',propKeys.animPixel,{dependsOnTaskIds:tasks.map(row=>row.id)});
 return tasks;
}

export function exactPropTask(row,task){
 return row?.task_id===task.id&&row.task_key===task.key&&row.task_type===task.type&&
  row.worker_type===task.workerType&&row.depends_on_task_id===task.dependsOn&&row.task_input===canonical(task.input)&&
  [2,3,4,5].every((n,i)=>(row['depends_on_task_id_'+n]??null)===(task.additionalDependencies?.[i]??null));
}
// Ten generation stages, eleven possible scale tasks, one publish, one extra-row sentinel.
export const propCompletionLimit=23;
// Historical combined and fan-out add-only runs retain their exact persisted graph.
// Unknown or partial topology still fails the exact row checks.
export const storedPropPlan=(run,rows)=>buildPropGeneratePlan(run,{splitScales:rows.some(r=>isPropScaleKey(r.task_key)),
 replaceScales:rows.some(r=>r.task_key===propKeys.publish&&r.task_type===propScaleReplacePublish)});
export function verifyPropScaleRefs(manifest,refs){
 const entries=refs.filter(r=>isPropScaleKey(r.key));
 return entries.every(r=>manifest.scaleVariants?.some(v=>r.key===propScaleKey(v.scalePercent)&&r.ref===propRef(r.key,sha(canonical(v)))));
}
export function checkPropPublishRows(run,rows,{published=false}={}){
 const tasks=storedPropPlan(run,rows);
 if(rows.length!==tasks.length)return null;
 const byId=new Map(rows.map(row=>[row.task_id,row])),refs=[];
 for(const task of tasks){
  const row=byId.get(task.id);
  if(!exactPropTask(row,task)||row.official_request_id!==run.officialRequestId)return null;
  if(task.key===propKeys.publish&&!published){
   // Only the worker's exact active claim may advance publication.
   if(row.status!=='running'||row.result_ref!==null||!Number.isSafeInteger(row.updated_at)||row.updated_at<1)return null;
   continue;
  }
  if(row.status!=='completed'||!isPropRef(task.key,row.result_ref))return null;
  refs.push({key:task.key,taskId:task.id,ref:row.result_ref});
 }
 return refs;
}
