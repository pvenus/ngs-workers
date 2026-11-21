import {setTimeout as delay} from 'node:timers/promises';
import {parseArgs,watchOnce,executeOnce} from './runner.mjs';
import {localPorts} from './local-ports.mjs';

try{
 const settings=parseArgs(process.argv.slice(2));
 const pds=localPorts();
 if(settings.mode==='execute'){
  const result=await executeOnce({...settings,pds});
  console.log(JSON.stringify({service:'file-worker',state:result.state,...(result.code?{code:result.code}:{})}));
  if(result.state!=='completed')process.exitCode=1;
 }else{
  const stop=new AbortController();
  process.once('SIGINT',()=>stop.abort());process.once('SIGTERM',()=>stop.abort());
  let last;
  let next=performance.now();
  while(!stop.signal.aborted){
   const result=await watchOnce({...settings,pds,signal:stop.signal});
   const line=JSON.stringify({service:'file-worker',...result});
   if(last!==line){console.log(line);last=line;}
   next+=60000;
   if(!stop.signal.aborted)await delay(Math.max(0,next-performance.now()),undefined,{signal:stop.signal}).catch(e=>{if(e.name!=='AbortError')throw e;});
  }
 }
}catch{
 console.error('FILE_WORKER_OPERATION_FAILED');
 process.exitCode=1;
}
