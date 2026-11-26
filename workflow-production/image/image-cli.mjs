import {fileURLToPath} from 'node:url';
import {startImagePool,imagePoolSize} from './image-pool.mjs';
import {detectLostPortraitReadsFromPath} from './portrait-read-owner.mjs';
import {imageOperations} from './worker.mjs';
import {propBaseOperations} from './prop-base-worker.mjs';
import {sourceType,backgroundSourceType} from './cinematic-source-worker.mjs';
import {setTimeout as delay} from 'node:timers/promises';
import {localPorts} from './local-ports.mjs';
import {imageTick,parseImageArgs,startImageHeartbeat} from './image-runner.mjs';
import {withPlanningSnapshot} from './planning-folders.mjs';
import {closeImageCodexSession,createPortraitRenderer,createNeutralReferenceRenderer,createPropImageRenderer,createCinematicSourceRenderer,probePortraitRenderer} from './portrait-renderer.mjs';

try{
 const argv=process.argv.slice(2);
 const settings=parseImageArgs(argv);
 const lane=process.connected&&/^[12]$/.test(process.env.NGS_IMAGE_POOL_LANE??'')?Number(process.env.NGS_IMAGE_POOL_LANE):null;
 if(settings.mode==='run'&&!lane){
  const stop=new AbortController();
  const heartbeat=startImageHeartbeat({pds:localPorts(),signal:stop.signal});
  const pool=startImagePool({entry:fileURLToPath(import.meta.url),args:argv,onReady:heartbeat.setReady,
   emit:e=>console.log(JSON.stringify({service:'image-worker-pool',lanes:imagePoolSize,...e}))});
  const drain=()=>{stop.abort();heartbeat.close();void pool.stop();};
  process.on('SIGINT',drain);process.on('SIGTERM',drain);
  await pool.done;
 }else{
 const stop=new AbortController(),drain=new AbortController();
 const signal=stop.signal,admissionSignal=drain.signal;
 const stopOrDrain=()=>{if(settings.mode==='run')drain.abort();else stop.abort();};
 process.on('SIGINT',stopOrDrain);process.on('SIGTERM',stopOrDrain);
 if(lane){
  process.once('disconnect',stopOrDrain);
  process.on('message',m=>{if(m?.type==='image-pool-drain')stopOrDrain();});
 }
 const pds=localPorts(),providerReady=()=>probePortraitRenderer();
 let renderPort,neutralRenderPort,propRenderPort,cinematicSourcePort,last;
 const heartbeat=lane?null:startImageHeartbeat({pds,signal});
 let lastHealth;
 const onHealth=lane?(ready)=>{
  if(ready===lastHealth||!process.connected)return;
  lastHealth=ready;process.send({type:'image-lane-health',ready},()=>{});
 }:heartbeat.setReady;
 const render=(...args)=>(renderPort??=createPortraitRenderer())(...args);
 const renderNeutral=(...args)=>(neutralRenderPort??=createNeutralReferenceRenderer())(...args);
 const renderProp=(...args)=>(propRenderPort??=createPropImageRenderer())(...args);
 const renderCinematicSource=(...args)=>(cinematicSourcePort??=createCinematicSourceRenderer())(...args);
 const onPhase=phase=>console.error(JSON.stringify({service:'image-worker',...(lane?{lane}:{}),phase}));
 do{
  let result;
  try{
   if(settings.mode==='run'&&(!lane||lane===1)&&!admissionSignal.aborted)for(const failure of detectLostPortraitReadsFromPath(settings.dbPath,[...imageOperations,sourceType,backgroundSourceType,...Object.keys(propBaseOperations)]))
    console.log(JSON.stringify({service:'image-worker',...(lane?{lane}:{}),...failure}));
   result=await imageTick({...settings,pds,providerReady,render,renderNeutral,renderProp,renderCinematicSource,withPlanningSnapshot,signal,admissionSignal,onPhase,onHealth});}
  catch{onHealth(false);result={state:'not_ready',code:'IMAGE_WORKER_OPERATION_FAILED'};}
  const line=JSON.stringify({service:'image-worker',...(lane?{lane}:{}),...result});
  if(last!==line||settings.mode==='execute'){console.log(line);last=line;}
  if(settings.mode==='execute'){if(result.state!=='completed')process.exitCode=1;break;}
  if(!signal.aborted&&!admissionSignal.aborted)await delay(settings.mode==='run'?5000:60000,undefined,{signal:AbortSignal.any([signal,admissionSignal])}).catch(e=>{if(e.name!=='AbortError')throw e;});
 }while(!signal.aborted&&!admissionSignal.aborted);
 heartbeat?.close();
 if(lane&&process.connected)process.disconnect();
 }
}catch{
 console.error('IMAGE_WORKER_OPERATION_FAILED');
 process.exitCode=1;
}

finally{closeImageCodexSession();if(process.connected)process.disconnect();}
