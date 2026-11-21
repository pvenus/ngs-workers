import {probeDeleteDatabase,runDeleteFromPath} from './worker.mjs';

export function parseArgs(args){
 if(args.length===3&&args[0]==='--watch'&&args[1]==='--db'&&args[2].startsWith('/'))return {mode:'watch',dbPath:args[2]};
 if(args.length===7&&args[0]==='--execute'&&args[1]==='--db'&&args[2].startsWith('/')&&args[3]==='--official-request-id'&&
    /^request\.official\.[a-f0-9]{64}$/.test(args[4])&&args[5]==='--task-id'&&/^workflow\.task\.[a-f0-9]{64}$/.test(args[6]))
  return {mode:'execute',dbPath:args[2],officialRequestId:args[4],taskId:args[6]};
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

export async function executeOnce({dbPath,officialRequestId,taskId,pds,run=runDeleteFromPath}){
 if(!await pds.capability())return {state:'not_claimable',code:'FILE_WORKER_PDS_UNAVAILABLE'};
 return await run({dbPath,officialRequestId,taskId,pds});
}
