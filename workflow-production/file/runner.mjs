import {nextWorkflowTaskFromPath} from './workflow-ready.mjs';
import {conceptDelete,probeDeleteDatabase,runDeleteFromPath} from './worker.mjs';
import {npcDelete,audioDelete,audioDeleteKey,singleTargetDeleteSchema} from './file-delete.mjs';
import {playbackType,playbackKey,runPlaybackFromPath} from './cinematic-playback-worker.mjs';

export function parseArgs(args){
 const singleTarget=args[0]==='--single-target';
 if(singleTarget)args=args.slice(1);
 const config=singleTarget?{singleTarget:true}:{};
 const operation=args.at(-2)==='--operation'&&[conceptDelete,audioDelete,playbackType].includes(args.at(-1))?args.at(-1):undefined;
 if(operation){
  if(args[0]!=='--execute')throw Error('FILE_WORKER_EXPLICIT_MODE_REQUIRED');
  args=args.slice(0,-2);
 }
 if(args.length===3&&args[0]==='--resident'&&args[1]==='--db'&&args[2].startsWith('/'))return {mode:'resident',dbPath:args[2],...config};
 if(args.length===3&&args[0]==='--watch'&&args[1]==='--db'&&args[2].startsWith('/'))return {mode:'watch',dbPath:args[2],...config};
 if(args.length===7&&args[0]==='--execute'&&args[1]==='--db'&&args[2].startsWith('/')&&args[3]==='--official-request-id'&&
    /^request\.official\.[a-f0-9]{64}$/.test(args[4])&&args[5]==='--task-id'&&/^workflow\.task\.[a-f0-9]{64}$/.test(args[6]))
  return {mode:'execute',dbPath:args[2],officialRequestId:args[4],taskId:args[6],...config,...(operation?{operation}:{})};
 throw Error('FILE_WORKER_EXPLICIT_MODE_REQUIRED');
}

export async function watchOnce({dbPath,pds,signal,probe=probeDeleteDatabase}){
 try{if(probe(dbPath)!==true)return {ready:false,code:'FILE_WORKER_DATABASE_UNAVAILABLE'};}
 catch{return {ready:false,code:'FILE_WORKER_DATABASE_UNAVAILABLE'};}
 if(!await pds.capability(signal))return {ready:false,code:'FILE_WORKER_PDS_UNAVAILABLE'};
 if(signal?.aborted)return {ready:false,code:'FILE_WORKER_STOPPING'};
 const heartbeat=await pds.heartbeat(signal);
 return {ready:true,heartbeat};
}

export async function executeOnce({dbPath,officialRequestId,taskId,pds,operation,run=runDeleteFromPath,runPlayback=runPlaybackFromPath}){
 if(operation===playbackType){
  if(!await pds.cinematicPlaybackReady?.())return {state:'not_claimable',code:'FILE_WORKER_PDS_UNAVAILABLE'};
  return await runPlayback({dbPath,officialRequestId,taskId,pds});
 }
 if(!await pds.capability(undefined,pds.deleteContract===singleTargetDeleteSchema?(operation??npcDelete):operation))return {state:'not_claimable',code:'FILE_WORKER_PDS_UNAVAILABLE'};
 return await run({dbPath,officialRequestId,taskId,pds,...(operation?{operation}:{})});
}

const scopes=Object.freeze([
 {workerType:'file',taskType:'npc.delete-folder',taskKey:'delete',predecessorKey:null},
 {workerType:'file',taskType:conceptDelete,taskKey:'category_delete',predecessorKey:null},
 {workerType:'file',taskType:audioDelete,taskKey:audioDeleteKey,predecessorKey:null},
 {workerType:'file',taskType:playbackType,taskKey:playbackKey,predecessorKey:null},
]);

export async function pollOnce({dbPath,pds,signal,select=nextWorkflowTaskFromPath,run=runDeleteFromPath,runPlayback=runPlaybackFromPath}){
 if(signal?.aborted)return {state:'stopping'};
 let pending=false;
 for(const scope of scopes){
  let target;
  try{target=select(dbPath,scope);}catch{return {state:'unavailable',code:'FILE_WORKER_DATABASE_UNAVAILABLE'};}
  if(!target)continue;
  pending=true;
  if(scope.taskType===playbackType){
   if(!await pds.cinematicPlaybackReady?.(signal))continue;
   if(signal?.aborted)return {state:'stopping'};
   try{return await runPlayback({dbPath,...target,pds,signal});}
   catch{return {state:'blocked',code:'FILE_WORKER_EXACT_CLAIM_REJECTED'};}
  }
  if(scope.taskType===audioDelete&&pds.deleteContract!==singleTargetDeleteSchema)continue;
  const operation=scope.taskType===npcDelete?undefined:scope.taskType;
  if(!await pds.capability(signal,pds.deleteContract===singleTargetDeleteSchema?scope.taskType:operation))continue;
  if(signal?.aborted)return {state:'stopping'};
  try{return await run({dbPath,...target,pds,...(operation?{operation}:{})});}
  catch{return {state:'blocked',code:'FILE_WORKER_EXACT_CLAIM_REJECTED'};}
 }
 return pending?{state:'unavailable',code:'FILE_WORKER_PDS_UNAVAILABLE'}:{state:'idle'};
}
