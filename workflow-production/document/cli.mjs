import {createAssemblyValidator} from './assembly-validator.mjs';
import {assemblyTypes,createAssemblyRenderer} from './assembly-worker.mjs';
import {createDocumentSlot,documentSlotIdentity} from './document-slot.mjs';
import {mapTypes} from './map-contract.mjs';
import {createMapRenderer} from './map-worker.mjs';
import {createPropDesignRenderer} from './prop-design-renderer.mjs';
import {isPropDesignOperation} from './prop-design-contract.mjs';
import {setTimeout as delay} from 'node:timers/promises';
import {parseArgs,watchOnce,executeOnce,documentTick} from './runner.mjs';
import {localPorts} from './local-ports.mjs';
import {createPlanningFolderDownloader} from './planning-folders.mjs';
import {closeDocumentCodexSession,createRepresentativeRenderer} from './representative-renderer.mjs';

let workspace;
try{
 const settings=parseArgs(process.argv.slice(2)),pds=localPorts(),stop=new AbortController(),drain=new AbortController();
 const {slot,serverName}=documentSlotIdentity(process.env.WORKFLOW_DOCUMENT_WORKER_SLOT);
 if(settings.mode!=='watch'){
  workspace=createDocumentSlot(slot);
  process.env.TMPDIR=workspace.tmp;
 }
 const withPlanningSnapshot=createPlanningFolderDownloader();
 const signal=stop.signal,admissionSignal=drain.signal;
 const stopOrDrain=()=>settings.mode==='run'?drain.abort():stop.abort();
 process.on('SIGINT',stopOrDrain);process.on('SIGTERM',stopOrDrain);
 const emit=x=>console.log(JSON.stringify({service:serverName,...x}));
 const validateDefinition=createAssemblyValidator();
 const rendererFor=operation=>operation===assemblyTypes[0]?createAssemblyRenderer():mapTypes.includes(operation)?createMapRenderer():isPropDesignOperation(operation)?createPropDesignRenderer():createRepresentativeRenderer({auditDirectory:settings.auditDirectory,operation});
 if(settings.mode==='execute'){
  const release=workspace.tryEnter();
  let result;
  try{result=release?await executeOnce({...settings,pds,render:settings.operation&&settings.operation!==assemblyTypes[1]?rendererFor(settings.operation):undefined,
   withPlanningSnapshot,validateDefinition,signal}):{state:'not_claimable',code:'DOCUMENT_WORKER_SLOT_PAUSED_OR_ACTIVE'};}finally{release?.();}
  emit({state:result.state,...(result.code?{code:result.code}:{}),...(result.stage?{stage:result.stage}:{}),
   ...(result.pdsHttpStatus?{pdsHttpStatus:result.pdsHttpStatus}:{}),...(result.pdsTransportKind?{pdsTransportKind:result.pdsTransportKind}:{}),...(result.conversion?{conversion:result.conversion}:{})});
  if(result.state!=='completed')process.exitCode=1;
 }else if(settings.mode==='run'){
  emit({state:'started',pid:process.pid});
  // The process is available during long external model work as well.
  let pendingPulse=false;
  const beat=async()=>{
   if(pendingPulse||signal.aborted)return;
   pendingPulse=true;
   try{await pds.heartbeat(signal,serverName);}finally{pendingPulse=false;}
  };
  void beat();
  const timer=setInterval(()=>void beat(),60000);
  let last;
  try{
   while(!admissionSignal.aborted){
    let result,release;
    try{
     release=workspace.tryEnter();
     result=release?await documentTick({...settings,pds,rendererFor,withPlanningSnapshot,validateDefinition,signal,admissionSignal}):{state:'draining'};
    }catch{result={state:'not_ready',code:'DOCUMENT_WORKER_OPERATION_FAILED'};}
    finally{release?.();}
    const current=JSON.stringify(result);
    if(last!==current){emit(result);last=current;}
    if(!admissionSignal.aborted)await delay(5000,undefined,{signal:admissionSignal}).catch(e=>{if(e.name!=='AbortError')throw e;});
   }
  }finally{clearInterval(timer);}
 }else{
  let last,next=performance.now();
  while(!signal.aborted){
   const result=await watchOnce({...settings,pds,signal,serverName}),current=JSON.stringify(result);
   if(last!==current){emit(result);last=current;}
   next+=60000;
   if(!signal.aborted)await delay(Math.max(0,next-performance.now()),undefined,{signal}).catch(e=>{if(e.name!=='AbortError')throw e;});
  }
 }
}catch{
 console.error('DOCUMENT_WORKER_OPERATION_FAILED');
 process.exitCode=1;
}

finally{closeDocumentCodexSession();workspace?.cleanup();}
