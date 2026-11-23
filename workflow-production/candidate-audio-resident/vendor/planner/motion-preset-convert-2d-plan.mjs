/** Registered DAG for the formal non-NPC 2D motion-preset route. */
import {canonical,sha} from './envelope.mjs';
import {motion2DRequestType} from './motion-preset-convert-2d-envelope.mjs';

export const motion2DDirections=Object.freeze(['n','ne','e','se','s','sw','w','nw']);
export const motion2DImageKey='motion_2d_image';
export const motion2DFinalizeKey='motion_2d_finalize';
export const motion2DFramesKey=direction=>'motion_2d_frames_'+direction;
export const motion2DEncodeKey=direction=>'motion_2d_encode_'+direction;
export const motion2DId=(officialRequestId,key)=>'workflow.task.'+sha(canonical([officialRequestId,key]));
// One excess-row sentinel: exact matching must also reject a complete graph with junk appended.
export const motion2DCompletionReadLimit=1+2*motion2DDirections.length+1+1;

const imageType=motion2DRequestType+'.image',frameType=motion2DRequestType+'.frames',
 encodeType=motion2DRequestType+'.encode',oldClipType=motion2DRequestType+'.clip',finalType=motion2DRequestType+'.finalize';

function shapeTask(id,key,type,workerType,dependsOn,input){return {id:id(key),key,type,workerType,dependsOn,input};}
function topology({officialRequestId,payload:{sourcePresetId,variantPresetId}}){
 const id=key=>motion2DId(officialRequestId,key),input={sourcePresetId,variantPresetId};
 const image=shapeTask(id,motion2DImageKey,imageType,'image',null,input);
 return {id,input,image};
}

export function buildMotion2DTasks(run){
 const {id,input,image}=topology(run);
 const frames=motion2DDirections.map(direction=>shapeTask(id,motion2DFramesKey(direction),frameType,
  'animation',image.id,{...input,direction}));
 const encodes=motion2DDirections.map(direction=>shapeTask(id,motion2DEncodeKey(direction),encodeType,
  'postprocess',id(motion2DFramesKey(direction)),{...input,direction}));
 // The scalar column is deliberately null: the final worker and canonical publish
 // fence must verify the eight encoded IDs from this persisted same-run list.
 const final=shapeTask(id,motion2DFinalizeKey,finalType,'postprocess',null,
  {...input,dependsOnTaskIds:encodes.map(encode=>encode.id)});
 return [image,...frames,...encodes,final];
}

// Reconstruction only for historical completion evidence. Never used when staging a new run.
function historicalMotion2DTasks(run){
 const {id,input,image}=topology(run);
 const clips=motion2DDirections.map(direction=>shapeTask(id,'motion_2d_'+direction,oldClipType,
  'animation',image.id,{...input,direction}));
 return [image,...clips,shapeTask(id,motion2DFinalizeKey,finalType,'animation',null,
  {...input,dependsOnTaskIds:clips.map(clip=>clip.id)})];
}

/** Completion projection requires exact persisted topology and typed receipts; no row rewriting. */
export function hasCompleteMotion2DGraph(run,rows){
 if(!Array.isArray(rows))return false;
 let tasks;
 try{tasks=rows.length===10?historicalMotion2DTasks(run):buildMotion2DTasks(run);}catch{return false;}
 if(rows.length!==tasks.length)return false;
 const byId=new Map(rows.map(row=>[row.task_id,row]));
 return tasks.every(task=>{
  const row=byId.get(task.id);
  if(!row||row.status!=='completed'||row.task_key!==task.key||row.task_type!==task.type||
     row.worker_type!==task.workerType||row.depends_on_task_id!==task.dependsOn||row.task_input!==canonical(task.input))return false;
  const prefix=task.type===imageType?'motion.2d.image.':task.type===finalType?'motion.2d.published.':
   'motion.2d.'+(task.type===frameType?'frames.':'clip.')+task.input.direction+'.';
  const ref=row.result_ref;
  return typeof ref==='string'&&ref.length===prefix.length+64&&ref.startsWith(prefix)&&/^[0-9a-f]{64}$/.test(ref.slice(prefix.length));
 });
}
