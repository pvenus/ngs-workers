import {PORTRAIT,imageOperations,nextPortraitFromPath,probePresetDatabase,runPortraitFromPath} from './worker.mjs';
import {motionImageOperation,nextMotionImageFromPath,runMotionImageFromPath} from './motion-neutral-reference.mjs';

export function parseImageArgs(args){
 let operation;
 if(args.at(-2)==='--operation'&&[...imageOperations,motionImageOperation].includes(args.at(-1))){
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

function selectImageTask(dbPath){
 const portrait=nextPortraitFromPath(dbPath);
 if(portrait)return portrait;
 const motion=nextMotionImageFromPath(dbPath);
 return motion?{...motion,operation:motionImageOperation}:null;
}
const executeImage=options=>options.operation===motionImageOperation?runMotionImageFromPath(options):runPortraitFromPath(options);

/** At most one runnable image task per tick. Never reclaims running rows. */
export async function imageTick(options,{select=selectImageTask,execute=executeImage,check=imageHealth}={}){
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
 const target=select(dbPath);
 if(!target)return {state:'idle'};
 if(target.operation&&target.operation!==PORTRAIT&&!await options.pds.capability(signal,target.operation))
  return {state:'not_ready',code:'IMAGE_WORKER_STAGE_PDS_UNAVAILABLE'};
 if(stopping())return {state:'stopping'};
 return execute({...options,...target});
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
