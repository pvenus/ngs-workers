import {setTimeout as delay} from 'node:timers/promises';
import {DatabaseSync} from 'node:sqlite';
import {assertDatabasePath,protectSchema} from './workflow-ready.mjs';
import {nextPostprocessFromPath,runPostprocessFromPath} from './postprocess-worker.mjs';
import {postprocessPorts} from './postprocess-ports.mjs';
import {createPostprocessRenderer} from './postprocess-provider.mjs';
import {probePresetEncoder} from './preset-video-artifact.mjs';
import {presetPostprocessPorts} from './preset-video-ports.mjs';
import {choosePresetPostprocess,runPresetPostprocessFromPath} from './preset-video-postprocess.mjs';
import {backgroundPixelPorts,createBackgroundPixelRenderer,nextBackgroundPixel,runBackgroundPixel} from './background-pixel-worker.mjs';
import {propStagePorts} from './prop-stage-ports.mjs';
import {createPropDerivationRenderer} from './prop-derivation-provider.mjs';
import {nextPropDerivationFromPath,runPropDerivationFromPath} from './prop-derivation-worker.mjs';
import {nextPropPackageFromPath,runPropPackageFromPath} from './prop-package-worker.mjs';

import {cinematicCropPorts,createCinematicCropRenderer,nextCinematicCrop,runCinematicCrop} from './cinematic-crop-worker.mjs';

export function parseArgs(args){
 const flags=['--run','--db','--config','--model-pins','--worker-source','--sam-source'];
 if(args.length!==11||args[0]!==flags[0]||!flags.slice(1).every((f,i)=>args[1+2*i]===f&&args[2+2*i]?.startsWith('/')))
  throw Error('POSTPROCESS_EXPLICIT_CONFIG_REQUIRED');
 return Object.fromEntries(['dbPath','configPath','modelPinsPath','workerSource','samSource'].map((k,i)=>[k,args[2+2*i]]));
}
async function main(){
 const options=parseArgs(process.argv.slice(2));
 assertDatabasePath(options.dbPath);
 const db=new DatabaseSync(options.dbPath,{readOnly:true});
 try{protectSchema(db);}finally{db.close();}
 const pds=postprocessPorts(),presetPds=presetPostprocessPorts();
 const propPds=propStagePorts();
 const bgPds=backgroundPixelPorts();
 const cropPds=cinematicCropPorts();
 let render,renderProp,renderBG,renderCrop;
 void createCinematicCropRenderer(options).then(value=>{renderCrop=value;}).catch(()=>{});
 void createBackgroundPixelRenderer(options).then(value=>{renderBG=value;}).catch(()=>{});
 // Existing NPC alpha/pixel setup never blocks or becomes a dependency of the
 // new CPU-only lane. This promise validates paths only; no GPU is loaded.
 void createPostprocessRenderer({...options,pds}).then(value=>{render=value;}).catch(()=>{});
 void createPropDerivationRenderer(options).then(value=>{renderProp=value;}).catch(()=>{});
 const stop=new AbortController();
 process.once('SIGINT',()=>stop.abort());process.once('SIGTERM',()=>stop.abort());
 let ready=false,heartbeatStarted=false,last;
 const pulse=async()=>{if(ready&&!stop.signal.aborted)await pds.heartbeat(stop.signal);};
 const timer=setInterval(()=>void pulse(),60000);timer.unref();
 try{
  while(!stop.signal.aborted){
   let result;
   try{
    const motionCheck=Promise.all([probePresetEncoder(),presetPds.ready(stop.signal)])
     .then(parts=>parts.every(Boolean));
    const [[frames,portraits],motion,props]=await Promise.all([
     render?Promise.all([pds.capability(),pds.capabilityPortrait()]):[false,false],motionCheck,renderProp?propPds.capabilities(stop.signal):null
    ]);
    const bgReady=Boolean(renderBG)&&await bgPds.ready(stop.signal);
    const cropReady=Boolean(renderCrop)&&await cropPds.ready(stop.signal);
    ready=frames||portraits||motion||Boolean(props)||bgReady||cropReady;
    if(ready&&!heartbeatStarted){heartbeatStarted=true;void pulse();}
    if(!ready)heartbeatStarted=false;
    const available=!stop.signal.aborted;
    const target=available&&(frames||portraits)&&nextPostprocessFromPath(options.dbPath,{frames,portraits});
    const preset=available&&!target&&motion&&choosePresetPostprocess(options.dbPath);
    // A committed raw stage no longer needs the renderer's live admission
    // lease. These consumers still require exact same-run completed parents.
    const propMode={stillOnly:true,appendEnabled:Boolean(props)};
    const prop=available&&!target&&!preset&&props&&nextPropDerivationFromPath(options.dbPath,propMode);
    const pack=available&&!target&&!preset&&!prop&&props&&nextPropPackageFromPath(options.dbPath,propMode);
    const bg=available&&bgReady&&!target&&!preset&&nextBackgroundPixel(options.dbPath);
    const crop=available&&cropReady&&!target&&!preset&&!bg&&nextCinematicCrop(options.dbPath);
    result=target?await runPostprocessFromPath({...options,...target,pds,render,signal:stop.signal}):
     preset?await runPresetPostprocessFromPath({...options,...preset,pds:presetPds,signal:stop.signal}):
     bg?await runBackgroundPixel({...options,...bg,pds:bgPds,render:renderBG,signal:stop.signal}):
     crop?await runCinematicCrop({...options,...crop,pds:cropPds,render:renderCrop,signal:stop.signal}):
           prop?await runPropDerivationFromPath({...options,...prop,pds:propPds,render:renderProp,signal:stop.signal,...propMode}):
     pack?await runPropPackageFromPath({...options,...pack,pds:propPds,signal:stop.signal,...propMode}):
     {state:ready?'idle':'not_ready'};
   }catch{result={state:'blocked',code:'POSTPROCESS_EXACT_TASK_REJECTED'};}
   const {state,code,taskId,phase}=result;
   const line=JSON.stringify({service:'postprocess-worker',state,code,taskId,phase});
   if(last!==line){console.log(line);last=line;}
   if(!stop.signal.aborted)await delay(1000,undefined,{signal:stop.signal}).catch(e=>{if(e.name!=='AbortError')throw e;});
  }
 }finally{clearInterval(timer);}
}
if(import.meta.url===new URL(process.argv[1],'file:').href)main().catch(()=>{console.error('POSTPROCESS_START_FAILED');process.exitCode=1;});
