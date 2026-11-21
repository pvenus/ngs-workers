import {setTimeout as delay} from 'node:timers/promises';
import {parseArgs,watchOnce,executeOnce,pollOnce} from './runner.mjs';
import {localPorts} from './local-ports.mjs';

try{
 const settings=parseArgs(process.argv.slice(2));
 const pds=localPorts({singleTarget:settings.singleTarget===true});
 if(settings.mode==='execute'){
  const result=await executeOnce({...settings,pds});
  console.log(JSON.stringify({service:'file-worker',state:result.state,...(result.code?{code:result.code}:{})}));
  if(result.state!=='completed')process.exitCode=1;
 }else{
  const stop=new AbortController();
  process.once('SIGINT',()=>stop.abort());process.once('SIGTERM',()=>stop.abort());
  const loop=async (operation,interval,kind)=>{
   let last;
   while(!stop.signal.aborted){
    const started=performance.now();
    const result=await operation({...settings,pds,signal:stop.signal});
    // Keep receipts and internal payloads out of operational logs.
    const {ready,heartbeat,state,code,taskId}=result;
    const line=JSON.stringify({service:'file-worker',kind,ready,heartbeat,state,code,taskId});
    if(last!==line){console.log(line);last=line;}
    if(!stop.signal.aborted)await delay(Math.max(0,interval-(performance.now()-started)),undefined,{signal:stop.signal})
     .catch(e=>{if(e.name!=='AbortError')throw e;});
   }
  };
  try{
   if(settings.mode==='resident')await Promise.all([
    loop(watchOnce,60000,'heartbeat'),
    loop(pollOnce,1000,'queue'),
   ]);
   else await loop(watchOnce,60000,'heartbeat');
  }finally{stop.abort();}
 }
}catch{
 console.error('FILE_WORKER_OPERATION_FAILED');
 process.exitCode=1;
}
