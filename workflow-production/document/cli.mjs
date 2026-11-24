import {createPropDesignRenderer} from './prop-design-renderer.mjs';
import {isPropDesignOperation} from './prop-design-contract.mjs';
import {setTimeout as delay} from 'node:timers/promises';
import {parseArgs,watchOnce,executeOnce,documentTick} from './runner.mjs';
import {localPorts} from './local-ports.mjs';
import {withPlanningSnapshot} from './planning-folders.mjs';
import {createRepresentativeRenderer} from './representative-renderer.mjs';

try{
 const settings=parseArgs(process.argv.slice(2)),pds=localPorts(),stop=new AbortController();
 const signal=stop.signal;
 process.once('SIGINT',()=>stop.abort());process.once('SIGTERM',()=>stop.abort());
 const emit=x=>console.log(JSON.stringify({service:'document-worker',...x}));
 const rendererFor=operation=>isPropDesignOperation(operation)?createPropDesignRenderer():createRepresentativeRenderer({auditDirectory:settings.auditDirectory,operation});
 if(settings.mode==='execute'){
  const result=await executeOnce({...settings,pds,render:settings.operation?rendererFor(settings.operation):undefined,
   withPlanningSnapshot,signal});
  emit({state:result.state,...(result.code?{code:result.code}:{}),...(result.conversion?{conversion:result.conversion}:{})});
  if(result.state!=='completed')process.exitCode=1;
 }else if(settings.mode==='run'){
  // The process is available during long external model work as well.
  let pendingPulse=false;
  const beat=async()=>{
   if(pendingPulse||signal.aborted)return;
   pendingPulse=true;
   try{await pds.heartbeat(signal);}finally{pendingPulse=false;}
  };
  void beat();
  const timer=setInterval(()=>void beat(),60000);
  let last;
  try{
   while(!signal.aborted){
    let result;
    try{result=await documentTick({...settings,pds,rendererFor,withPlanningSnapshot,signal});}
    catch{result={state:'not_ready',code:'DOCUMENT_WORKER_OPERATION_FAILED'};}
    const current=JSON.stringify(result);
    if(last!==current){emit(result);last=current;}
    if(!signal.aborted)await delay(5000,undefined,{signal}).catch(e=>{if(e.name!=='AbortError')throw e;});
   }
  }finally{clearInterval(timer);}
 }else{
  let last,next=performance.now();
  while(!signal.aborted){
   const result=await watchOnce({...settings,pds,signal}),current=JSON.stringify(result);
   if(last!==current){emit(result);last=current;}
   next+=60000;
   if(!signal.aborted)await delay(Math.max(0,next-performance.now()),undefined,{signal}).catch(e=>{if(e.name!=='AbortError')throw e;});
  }
 }
}catch{
 console.error('DOCUMENT_WORKER_OPERATION_FAILED');
 process.exitCode=1;
}
