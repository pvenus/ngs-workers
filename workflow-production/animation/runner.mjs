import {probePresetDatabase,runDocumentFromPath} from './worker.mjs';

const REP='npc.plan-representative';
export function parseArgs(args){
 let auditDirectory;
 if(args.at(-2)==='--audit-dir'&&args.at(-1).startsWith('/')){
  auditDirectory=args.at(-1);args=args.slice(0,-2);
 }
 const explicit=args.at(-2)==='--operation'&&args.at(-1)===REP;
 if(auditDirectory!==undefined&&(!explicit||args[0]!=='--execute'))throw Error('DOCUMENT_WORKER_EXPLICIT_MODE_REQUIRED');
 if(explicit)args=args.slice(0,-2);
 const op=explicit?{operation:REP,...(auditDirectory?{auditDirectory}:{})}:{};
 if(args.length===3&&args[0]==='--watch'&&args[1]==='--db'&&args[2].startsWith('/'))return {mode:'watch',dbPath:args[2],...op};
 if(args.length===7&&args[0]==='--execute'&&args[1]==='--db'&&args[2].startsWith('/')&&args[3]==='--official-request-id'&&
    /^request\.official\.[a-f0-9]{64}$/.test(args[4])&&args[5]==='--task-id'&&/^workflow\.task\.[a-f0-9]{64}$/.test(args[6]))
  return {mode:'execute',dbPath:args[2],officialRequestId:args[4],taskId:args[6],...op};
 throw Error('DOCUMENT_WORKER_EXPLICIT_MODE_REQUIRED');
}

export async function watchOnce({dbPath,pds,operation,signal,probe=probePresetDatabase}){
 try{if(probe(dbPath)!==true)return {ready:false,code:'DOCUMENT_WORKER_DATABASE_UNAVAILABLE'};}
 catch{return {ready:false,code:'DOCUMENT_WORKER_DATABASE_UNAVAILABLE'};}
 if(!await pds.capability(signal,operation))return {ready:false,code:'DOCUMENT_WORKER_PDS_UNAVAILABLE'};
 if(signal?.aborted)return {ready:false,code:'DOCUMENT_WORKER_STOPPING'};
 const heartbeat=await pds.heartbeat(signal);
 return {ready:true,heartbeat};
}

export async function executeOnce({dbPath,officialRequestId,taskId,pds,operation,render,withPlanningSnapshot,signal,run=runDocumentFromPath}){
 if(!await pds.capability(signal,operation))return {state:'not_claimable',code:'DOCUMENT_WORKER_PDS_UNAVAILABLE'};
 return await run({dbPath,officialRequestId,taskId,pds,operation,render,withPlanningSnapshot,signal});
}
