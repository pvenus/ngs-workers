import {setTimeout as delay} from 'node:timers/promises';
import {DatabaseSync} from 'node:sqlite';
import {assertDatabasePath,protectSchema} from './workflow-ready.mjs';
import {nextPostprocessFromPath,runPostprocessFromPath} from './postprocess-worker.mjs';
import {postprocessPorts} from './postprocess-ports.mjs';
import {createPostprocessRenderer} from './postprocess-provider.mjs';
import {probePresetEncoder} from './preset-video-artifact.mjs';
import {presetPostprocessPorts} from './preset-video-ports.mjs';
import {choosePresetPostprocess,runPresetPostprocessFromPath} from './preset-video-postprocess.mjs';

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
 let render;
 // Existing NPC alpha/pixel setup never blocks or becomes a dependency of the
 // new CPU-only lane. This promise validates paths only; no GPU is loaded.
 void createPostprocessRenderer({...options,pds}).then(value=>{render=value;}).catch(()=>{});
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
    const [[frames,portraits],motion]=await Promise.all([
     render?Promise.all([pds.capability(),pds.capabilityPortrait()]):[false,false],motionCheck
    ]);
    ready=frames||portraits||motion;
    if(ready&&!heartbeatStarted){heartbeatStarted=true;void pulse();}
    if(!ready)heartbeatStarted=false;
    const available=!stop.signal.aborted;
    const target=available&&(frames||portraits)&&nextPostprocessFromPath(options.dbPath,{frames,portraits});
    const preset=available&&!target&&motion&&choosePresetPostprocess(options.dbPath);
    result=target?await runPostprocessFromPath({...options,...target,pds,render,signal:stop.signal}):
     preset?await runPresetPostprocessFromPath({...options,...preset,pds:presetPds,signal:stop.signal}):
      {state:ready?'idle':'not_ready'};
   }catch{result={state:'blocked',code:'POSTPROCESS_EXACT_TASK_REJECTED'};}
   const {state,code,taskId}=result;
   const line=JSON.stringify({service:'postprocess-worker',state,code,taskId});
   if(last!==line){console.log(line);last=line;}
   if(!stop.signal.aborted)await delay(1000,undefined,{signal:stop.signal}).catch(e=>{if(e.name!=='AbortError')throw e;});
  }
 }finally{clearInterval(timer);}
}
if(import.meta.url===new URL(process.argv[1],'file:').href)main().catch(()=>{console.error('POSTPROCESS_START_FAILED');process.exitCode=1;});
