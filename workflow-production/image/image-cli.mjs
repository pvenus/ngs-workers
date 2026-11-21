import {setTimeout as delay} from 'node:timers/promises';
import {localPorts} from './local-ports.mjs';
import {imageTick,parseImageArgs,startImageHeartbeat} from './image-runner.mjs';
import {withPlanningSnapshot} from './planning-folders.mjs';
import {createPortraitRenderer,probePortraitRenderer} from './portrait-renderer.mjs';

try{
 const settings=parseImageArgs(process.argv.slice(2));
 const stop=new AbortController(),drain=new AbortController();
 const signal=stop.signal,admissionSignal=drain.signal;
 const stopOrDrain=()=>{if(settings.mode==='run')drain.abort();else stop.abort();};
 process.on('SIGINT',stopOrDrain);process.on('SIGTERM',stopOrDrain);
 const pds=localPorts(),providerReady=()=>probePortraitRenderer();
 let renderPort,last;
 const heartbeat=startImageHeartbeat({pds,signal});
 const onHealth=heartbeat.setReady;
 const render=(...args)=>(renderPort??=createPortraitRenderer())(...args);
 const onPhase=phase=>console.error(JSON.stringify({service:'image-worker',phase}));
 do{
  let result;
  try{result=await imageTick({...settings,pds,providerReady,render,withPlanningSnapshot,signal,admissionSignal,onPhase,onHealth});}
  catch{onHealth(false);result={state:'not_ready',code:'IMAGE_WORKER_OPERATION_FAILED'};}
  const line=JSON.stringify({service:'image-worker',...result});
  if(last!==line||settings.mode==='execute'){console.log(line);last=line;}
  if(settings.mode==='execute'){if(result.state!=='completed')process.exitCode=1;break;}
  if(!signal.aborted&&!admissionSignal.aborted)await delay(settings.mode==='run'?5000:60000,undefined,{signal:AbortSignal.any([signal,admissionSignal])}).catch(e=>{if(e.name!=='AbortError')throw e;});
 }while(!signal.aborted&&!admissionSignal.aborted);
 heartbeat.close();
}catch{
 console.error('IMAGE_WORKER_OPERATION_FAILED');
 process.exitCode=1;
}
