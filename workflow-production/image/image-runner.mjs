import {oldestRunnable} from './workflow-ready.mjs';
import {sourceType,nextCinematicSourceFromPath,runCinematicSourceFromPath} from './cinematic-source-worker.mjs';
import {propBaseOperations,nextPropBaseFromPath,runPropBaseFromPath} from './prop-base-worker.mjs';
import {propImageOperation,nextPropImageFromPath,runPropImageFromPath} from './prop-image-worker.mjs';
import {PORTRAIT,imageOperations,portraitCandidatesFromPath,probePresetDatabase,runPortraitFromPath} from './worker.mjs';
import {motionImageOperation,nextMotionImageFromPath,runMotionImageFromPath} from './motion-neutral-reference.mjs';

export function parseImageArgs(args){
 let operation;
 if(args.at(-2)==='--operation'&&[...imageOperations,motionImageOperation,propImageOperation,sourceType,...Object.keys(propBaseOperations)].includes(args.at(-1))){
  operation=args.at(-1);args=args.slice(0,-2);
 }
 if(args.length>=3&&args[1]==='--db'&&args[2].startsWith('/')){
  const [mode,,dbPath]=args;
  if(args.length===3&&operation===undefined&&['--run','--watch'].includes(mode))return {mode:mode.slice(2),dbPath};
  if(args.length===7&&mode==='--execute'&&args[3]==='--official-request-id'&&/^request\.official\.[a-f0-9]{64}$/.test(args[4])&&
     args[5]==='--task-id'&&/^workflow\.task\.[a-f0-9]{64}$/.test(args[6]))
   return {mode:'execute',dbPath,officialRequestId:args[4],taskId:args[6],...(operation?{operation}:{})};
 }
 throw Error('IMAGE_WORKER_EXPLICIT_MODE_REQUIRED');
}

export async function imageHealth({dbPath,pds,providerReady,signal,operation=PORTRAIT,probe=probePresetDatabase}){
 if(signal?.aborted)return {ready:false,code:'IMAGE_WORKER_STOPPING'};
 try{if(probe(dbPath)!==true)return {ready:false,code:'IMAGE_WORKER_DATABASE_UNAVAILABLE'};}
 catch{return {ready:false,code:'IMAGE_WORKER_DATABASE_UNAVAILABLE'};}
 try{if(await providerReady()!==true)return {ready:false,code:'IMAGE_WORKER_PROVIDER_UNAVAILABLE'};}
 catch{return {ready:false,code:'IMAGE_WORKER_PROVIDER_UNAVAILABLE'};}
 if(!await pds.capability(signal,operation))return {ready:false,code:'IMAGE_WORKER_PDS_UNAVAILABLE'};
 return {ready:true};
}

export function selectImageTasks(dbPath){
 const motion=nextMotionImageFromPath(dbPath);
 const prop=nextPropImageFromPath(dbPath);
 const source=nextCinematicSourceFromPath(dbPath);
 return oldestRunnable(dbPath,[...portraitCandidatesFromPath(dbPath),
  motion&&{...motion,operation:motionImageOperation},prop&&{...prop,operation:propImageOperation},
  nextPropBaseFromPath(dbPath),source&&{...source,operation:sourceType}]);
}
const executeImage=options=>options.operation===sourceType?runCinematicSourceFromPath(options):Object.hasOwn(propBaseOperations,options.operation)?runPropBaseFromPath(options):options.operation===propImageOperation?runPropImageFromPath(options):options.operation===motionImageOperation?runMotionImageFromPath(options):runPortraitFromPath(options);

/** At most one runnable image task per tick. Never reclaims running rows. */
export async function imageTick(options,{select=selectImageTasks,execute=executeImage,check=imageHealth}={}){
 const {mode,dbPath,signal,admissionSignal,onHealth}=options;
 const stopping=()=>signal?.aborted||admissionSignal?.aborted;
 if(stopping())return {state:'stopping'};
 const health=await check(options);
 if(stopping())return {state:'stopping'};
 try{onHealth?.(health.ready===true);}catch{/* health observation cannot change eligibility */}
 if(!health.ready)return {state:'not_ready',code:health.code};
 if(mode==='watch')return {state:'ready'};
 if(mode==='execute')return execute(options);
 if(mode!=='run')throw Error('IMAGE_WORKER_EXPLICIT_MODE_REQUIRED');
 const choice=select(dbPath);
 const targets=Array.isArray(choice)?choice:[choice].filter(Boolean);
 for(const target of targets){
  if(target.operation&&target.operation!==PORTRAIT&&!await options.pds.capability(signal,target.operation))continue;
  if(stopping())return {state:'stopping'};
  return execute({...options,...target});
 }
 return targets.length?{state:'not_ready',code:'IMAGE_WORKER_STAGE_PDS_UNAVAILABLE'}:{state:'idle'};
}

/** Keep OPS liveness independent of a long asynchronous renderer call. */
export function startImageHeartbeat({pds,signal,intervalMs=60000}){
 let ready=false,inFlight=false,stopped=false;
 const pulse=async()=>{
  if(!ready||inFlight||stopped||signal?.aborted)return;
  inFlight=true;
  try{await pds.heartbeat(signal,'image-worker');}catch{/* next regular tick may report */}
  finally{inFlight=false;}
 };
 const timer=setInterval(pulse,intervalMs);timer.unref();
 return {
  setReady(now){const recovered=now===true&&!ready;ready=now===true;if(recovered)void pulse();},
  close(){stopped=true;clearInterval(timer);},
 };
}
