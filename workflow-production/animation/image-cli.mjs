import {setTimeout as delay} from 'node:timers/promises';
import {localPorts} from './local-ports.mjs';
import {imageTick,parseImageArgs,startImageHeartbeat} from './image-runner.mjs';
import {withPlanningSnapshot} from './planning-folders.mjs';
import {createPortraitRenderer,probePortraitRenderer} from './portrait-renderer.mjs';

try{
 const settings=parseImageArgs(process.argv.slice(2));
 const stop=new AbortController();
 const signal=stop.signal;
 process.once('SIGINT',()=>stop.abort());process.once('SIGTERM',()=>stop.abort());
 const pds=localPorts(),providerReady=()=>probePortraitRenderer();
 let renderPort,last;
 const heartbeat=startImageHeartbeat({pds,signal});
 const onHealth=heartbeat.setReady;
 const render=(...args)=>(renderPort??=createPortraitRenderer())(...args);
 const onPhase=phase=>console.error(JSON.stringify({service:'image-worker',phase}));
 do{
  let result;
  try{result=await imageTick({...settings,pds,providerReady,render,withPlanningSnapshot,signal,onPhase,onHealth});}
  catch{onHealth(false);result={state:'not_ready',code:'IMAGE_WORKER_OPERATION_FAILED'};}
  const line=JSON.stringify({service:'image-worker',...result});
  if(last!==line||settings.mode==='execute'){console.log(line);last=line;}
  if(settings.mode==='execute'){if(result.state!=='completed')process.exitCode=1;break;}
  if(!signal.aborted)await delay(settings.mode==='run'?5000:60000,undefined,{signal}).catch(e=>{if(e.name!=='AbortError')throw e;});
 }while(!signal.aborted);
 heartbeat.close();
}catch{
 console.error('IMAGE_WORKER_OPERATION_FAILED');
 process.exitCode=1;
}
