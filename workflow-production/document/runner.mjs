import {assemblyTypes,assemblyStage,runAssemblyFromPath} from './assembly-worker.mjs';
import {mapTypes,mapStage,mapGenerateStages,mapDecorationStage} from './map-contract.mjs';
import {runMapFromPath} from './map-worker.mjs';
const runner=(op,ordinary,prop)=>assemblyTypes.includes(op)?runAssemblyFromPath:mapTypes.includes(op)?runMapFromPath:isPropDesignOperation(op)?prop:ordinary;
import {isPropDesignOperation,propDesignKey,propDesignOperations} from './prop-design-contract.mjs';
import {runPropDesignFromPath} from './prop-design-worker.mjs';
import {probePresetDatabase,runDocumentFromPath} from './worker.mjs';

import {motionPoseRegistry} from './vendor/planner-npc-motion-selection-v1/envelope.mjs';
import {nextWorkflowTaskFromPath,oldestRunnable} from './workflow-ready.mjs';

const CONCEPT='concept.category-generate',UPDATE='npc.planning.update',ADD='npc.planning.add';
const PRESET='npc.preset',OVERVIEW='npc.plan-overview',ART='npc.plan-portrait-art',REP='npc.plan-representative',IDLE='npc.plan-idle',COMBAT='npc.plan-combat';
const supported=Object.freeze([
 {workerType:'document',taskType:CONCEPT,taskKey:'category_generate',predecessorKey:null},
 {workerType:'document',taskType:CONCEPT,taskKey:'category_generate',predecessorKey:'category_delete'},
 {workerType:'document',taskType:PRESET,taskKey:'preset',predecessorKey:null},
 {workerType:'document',taskType:PRESET,taskKey:'preset',predecessorKey:'delete'},
 {workerType:'document',taskType:OVERVIEW,taskKey:'planning_overview',predecessorKey:'preset'},
 {workerType:'document',taskType:ART,taskKey:'planning_portrait_art',predecessorKey:'planning_overview'},
 {workerType:'document',taskType:REP,taskKey:'planning_representative',predecessorKey:'planning_portrait_art'},
 {workerType:'document',taskType:REP,taskKey:'planning_representative',predecessorKey:'preset'},
 ...Object.values(motionPoseRegistry).map(({planKey,planType})=>({workerType:'document',taskType:planType,taskKey:planKey,predecessorKey:'planning_representative'})),
 {workerType:'document',taskType:UPDATE,taskKey:'planning_update',predecessorKey:null},
 {workerType:'document',taskType:ADD,taskKey:'planning_add',predecessorKey:null},
 ...assemblyTypes.map(taskType=>({workerType:'document',taskType,taskKey:assemblyStage,predecessorKey:null})),
 ...mapTypes.map(taskType=>({workerType:'document',taskType,taskKey:mapStage,predecessorKey:null})),
 ...[...mapGenerateStages,mapDecorationStage].map((taskKey,i)=>({workerType:'document',taskType:mapTypes[0],taskKey,predecessorKey:i===0?null:mapGenerateStages[i-1]})),
 ...Object.keys(propDesignOperations).map(taskType=>({workerType:'document',taskType,taskKey:propDesignKey,predecessorKey:null})),
]);
export function parseArgs(args){
 let auditDirectory;
 if(args.at(-2)==='--audit-dir'&&args.at(-1).startsWith('/')){
  auditDirectory=args.at(-1);args=args.slice(0,-2);
 }
 const explicit=args.at(-2)==='--operation'&&[OVERVIEW,ART,REP,IDLE,COMBAT,CONCEPT,UPDATE,ADD,...assemblyTypes,...mapTypes,...Object.keys(propDesignOperations)].includes(args.at(-1))?args.at(-1):null;
 if(auditDirectory!==undefined&&(!explicit||args[0]!=='--execute'||isPropDesignOperation(explicit)))throw Error('DOCUMENT_WORKER_EXPLICIT_MODE_REQUIRED');
 if(explicit)args=args.slice(0,-2);
 const op=explicit?{operation:explicit,...(auditDirectory?{auditDirectory}:{})}:{};
 if(args.length===3&&args[0]==='--run'&&args[1]==='--db'&&args[2].startsWith('/'))return {mode:'run',dbPath:args[2],...op};
 if(args.length===3&&args[0]==='--watch'&&args[1]==='--db'&&args[2].startsWith('/'))return {mode:'watch',dbPath:args[2],...op};
 if(args.length===7&&args[0]==='--execute'&&args[1]==='--db'&&args[2].startsWith('/')&&args[3]==='--official-request-id'&&
    /^request\.official\.[a-f0-9]{64}$/.test(args[4])&&args[5]==='--task-id'&&/^workflow\.task\.[a-f0-9]{64}$/.test(args[6]))
  return {mode:'execute',dbPath:args[2],officialRequestId:args[4],taskId:args[6],...op};
 throw Error('DOCUMENT_WORKER_EXPLICIT_MODE_REQUIRED');
}

export async function watchOnce({dbPath,pds,operation,signal,serverName,probe=probePresetDatabase}){
 try{if(probe(dbPath)!==true)return {ready:false,code:'DOCUMENT_WORKER_DATABASE_UNAVAILABLE'};}
 catch{return {ready:false,code:'DOCUMENT_WORKER_DATABASE_UNAVAILABLE'};}
 if(!await pds.capability(signal,operation))return {ready:false,code:'DOCUMENT_WORKER_PDS_UNAVAILABLE'};
 if(signal?.aborted)return {ready:false,code:'DOCUMENT_WORKER_STOPPING'};
 const heartbeat=await pds.heartbeat(signal,serverName);
 return {ready:true,heartbeat};
}

export async function executeOnce({dbPath,officialRequestId,taskId,pds,operation,render,validateDefinition,withPlanningSnapshot,signal,run=runDocumentFromPath,runProp=runPropDesignFromPath}){
 if(!await pds.capability(signal,operation))return {state:'not_claimable',code:'DOCUMENT_WORKER_PDS_UNAVAILABLE'};
 return await runner(operation,run,runProp)({dbPath,officialRequestId,taskId,pds,operation,render,validateDefinition,withPlanningSnapshot,signal});
}


/** At most one claimed job per tick; only a lost claim immediately reselects. */
export async function documentTick({dbPath,pds,operation,rendererFor,validateDefinition,withPlanningSnapshot,signal,admissionSignal,
 next=nextWorkflowTaskFromPath,run=runDocumentFromPath,runProp=runPropDesignFromPath}){
 const stopping=()=>signal?.aborted||admissionSignal?.aborted;
 const skipped=new Set();
 for(let attempt=0;attempt<16;attempt++){
  if(stopping())return {state:'stopping'};
  let ready;
  try{
   const candidates=[];
   for(const spec of supported){
    if(operation&&operation!==spec.taskType)continue;
    const task=next(dbPath,spec);
    if(task)candidates.push({...task,operation:spec.taskType});
   }
   ready=oldestRunnable(dbPath,candidates);
  }catch{return {state:'not_ready',code:'DOCUMENT_WORKER_DATABASE_UNAVAILABLE'};}
  let lost=false,unavailable=false;
  for(const task of ready){
   if(skipped.has(task.taskId))continue;
   const operation=task.operation;
   if(!await pds.capability(signal,operation)){unavailable=true;continue;}
   if(stopping())return {state:'stopping'};
   const result=await runner(operation,run,runProp)({dbPath,pds,...task,validateDefinition,withPlanningSnapshot,signal,
    render:operation===PRESET||operation===assemblyTypes[1]?undefined:rendererFor(operation)});
   if(result.state!=='not_claimable')return {...result,operation};
   skipped.add(task.taskId);
   lost=true;
   break;
  }
  if(!lost)return unavailable?{state:'not_ready',code:'DOCUMENT_WORKER_PDS_UNAVAILABLE'}:{state:'idle'};
 }
 return {state:'idle'};
}
