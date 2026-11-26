import {canonical,sha} from '../planner-f24d23a/envelope.mjs';
import {propGenerateType,propRegenerateType,propAnimationType,propTypes,propHash} from './prop-generate-envelope.mjs';

export const propKeys=Object.freeze({
 design:'prop_design',base:'prop_base',motion:'prop_motion',detail:'prop_detail',pixel:'prop_pixel',animDetail:'prop_animation_detail',animPixel:'prop_animation_pixel',publish:'prop_pack_publish',
});
// This exact declarative registry is consumed by Planner, not injected by requesters.
// Stage capability is registered even where trusted execution ports are not yet wired.
export const propStageRegistry=Object.freeze({
 [propKeys.design]:Object.freeze({type:'prop.generate.design',workerType:'document',output:'design-spec'}),
 [propKeys.base]:Object.freeze({type:'prop.generate.base',workerType:'image',output:'base-png'}),
 [propKeys.motion]:Object.freeze({type:'prop.generate.motion',workerType:'animation',output:'i2v-rgb'}),
 [propKeys.animDetail]:Object.freeze({type:'prop.generate.motion-detail',workerType:'postprocess',output:'animation-detail-rgba'}),
 [propKeys.animPixel]:Object.freeze({type:'prop.generate.motion-pixel',workerType:'postprocess',output:'animation-pixel-rgba'}),
 [propKeys.detail]:Object.freeze({type:'prop.generate.detail',workerType:'postprocess',output:'detail-rgba'}),
 [propKeys.pixel]:Object.freeze({type:'prop.generate.pixel',workerType:'postprocess',output:'pixel-rgba'}),
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
export function buildPropGeneratePlan(run){
 const {officialRequestId:oid,payload}=run;
 if(!propTypes.includes(run.requestType)||typeof oid!=='string'||oid.length!==81||!official.test(oid))
  throw Error('PROP_COMPOSITE_TOPOLOGY_INVALID');
 if(run.requestType===propAnimationType)return buildAppendPlan(run);
 const id=key=>propTaskId(oid,key),motion=payload.motion==='subtle', mode=motion?'subtle':'still';
 const regenerate=run.requestType===propRegenerateType;
 const scope={propId:payload.propId,designContextSha256:payload.designContextSha256,...(regenerate?{source:payload.source}:{})};
 const tasks=[];
 const task=(key,parent,input)=>{
  const {type,workerType}=propStageRegistry[key];
  tasks.push({id:id(key),key,type:regenerate?type.replace(propGenerateType,propRegenerateType):type,workerType,dependsOn:parent===null?null:id(parent),input:{...scope,...input}});
 };
 task(propKeys.design,null,{intent:payload.intent,motion:mode});
 task(propKeys.base,propKeys.design,{designTaskId:id(propKeys.design),
  ...(run.requestType===propGenerateType&&payload.reference?{reference:payload.reference}:{})});
 task(propKeys.detail,propKeys.base,{designTaskId:id(propKeys.design),baseTaskId:id(propKeys.base),sourceTaskId:id(propKeys.base)});
 task(propKeys.pixel,propKeys.detail,{designTaskId:id(propKeys.design),detailTaskId:id(propKeys.detail)});
 if(motion){
  task(propKeys.motion,propKeys.base,{designTaskId:id(propKeys.design),baseTaskId:id(propKeys.base)});
  task(propKeys.animDetail,propKeys.motion,{designTaskId:id(propKeys.design),baseTaskId:id(propKeys.base),sourceTaskId:id(propKeys.motion)});
  task(propKeys.animPixel,propKeys.animDetail,{designTaskId:id(propKeys.design),detailTaskId:id(propKeys.animDetail)});
 }
 const keys=tasks.map(row=>row.key);
 // Canonical publishing must verify ALL predecessors and their bytes; the
 // scalar indexed parent does not authorize publication by itself.
 task(propKeys.publish,motion?propKeys.animPixel:propKeys.pixel,{dependsOnTaskIds:keys.map(id)});
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
  row.worker_type===task.workerType&&row.depends_on_task_id===task.dependsOn&&row.task_input===canonical(task.input);
}
export const propCompletionLimit=9; // eight permitted tasks, plus one sentinel for extra-row rejection
export function checkPropPublishRows(run,rows,{published=false}={}){
 const tasks=buildPropGeneratePlan(run);
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
