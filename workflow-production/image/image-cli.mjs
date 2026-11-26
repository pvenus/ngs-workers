import {createImageSlot,imageSlotIdentity} from './image-slot.mjs';
import {detectLostPortraitReadsFromPath} from './portrait-read-owner.mjs';
import {imageOperations} from './worker.mjs';
import {propBaseOperations} from './prop-base-worker.mjs';
import {sourceType,backgroundSourceType} from './cinematic-source-worker.mjs';
import {setTimeout as delay} from 'node:timers/promises';
import {localPorts} from './local-ports.mjs';
import {imageTick,parseImageArgs,startImageHeartbeat,imageHealth} from './image-runner.mjs';
import {withPlanningSnapshot} from './planning-folders.mjs';
import {closeImageCodexSession,createPortraitRenderer,createNeutralReferenceRenderer,createPropImageRenderer,createCinematicSourceRenderer,probePortraitRenderer} from './portrait-renderer.mjs';

let workspace;
try{
 const settings=parseImageArgs(process.argv.slice(2));
 const {slot,serverName}=imageSlotIdentity(process.env.WORKFLOW_IMAGE_WORKER_SLOT);
 if(settings.mode==='run'){
  workspace=createImageSlot(slot);
  process.env.TMPDIR=workspace.tmp;
  console.log(JSON.stringify({service:serverName,state:'started',pid:process.pid}));
 }
 const stop=new AbortController(),drain=new AbortController();
 const signal=stop.signal,admissionSignal=drain.signal;
 const stopOrDrain=()=>{if(settings.mode==='run')drain.abort();else stop.abort();};
 process.on('SIGINT',stopOrDrain);process.on('SIGTERM',stopOrDrain);
 const pds=localPorts(),providerReady=()=>probePortraitRenderer();
 let renderPort,neutralRenderPort,propRenderPort,cinematicSourcePort,last;
 const heartbeat=startImageHeartbeat({pds,signal,serverName});
 const onHealth=heartbeat.setReady;
 const render=(...args)=>(renderPort??=createPortraitRenderer())(...args);
 const renderNeutral=(...args)=>(neutralRenderPort??=createNeutralReferenceRenderer())(...args);
 const renderProp=(...args)=>(propRenderPort??=createPropImageRenderer())(...args);
 const renderCinematicSource=(...args)=>(cinematicSourcePort??=createCinematicSourceRenderer())(...args);
 const onPhase=phase=>console.error(JSON.stringify({service:serverName,phase}));
 do{
  let result;
  let release;
  try{
   release=workspace?.tryEnter();
   if(workspace&&!release){
    const health=await imageHealth({...settings,pds,providerReady,signal});onHealth(health.ready===true);
    result={state:health.ready?'draining':'not_ready',...(health.code?{code:health.code}:{})};
   }else{
   if(settings.mode==='run'&&!admissionSignal.aborted)for(const failure of detectLostPortraitReadsFromPath(settings.dbPath,[...imageOperations,sourceType,backgroundSourceType,...Object.keys(propBaseOperations)]))
    console.log(JSON.stringify({service:serverName,...failure}));
   result=await imageTick({...settings,pds,providerReady,render,renderNeutral,renderProp,renderCinematicSource,withPlanningSnapshot,signal,admissionSignal,onPhase,onHealth});
   }
  }
  catch{onHealth(false);result={state:'not_ready',code:'IMAGE_WORKER_OPERATION_FAILED'};}finally{release?.();}
  const line=JSON.stringify({service:serverName,...result});
  if(last!==line||settings.mode==='execute'){console.log(line);last=line;}
  if(settings.mode==='execute'){if(result.state!=='completed')process.exitCode=1;break;}
  if(!signal.aborted&&!admissionSignal.aborted)await delay(settings.mode==='run'?5000:60000,undefined,{signal:AbortSignal.any([signal,admissionSignal])}).catch(e=>{if(e.name!=='AbortError')throw e;});
 }while(!signal.aborted&&!admissionSignal.aborted);
 heartbeat.close();
}catch{
 console.error('IMAGE_WORKER_OPERATION_FAILED');
 process.exitCode=1;
}

finally{closeImageCodexSession();workspace?.cleanup();}
