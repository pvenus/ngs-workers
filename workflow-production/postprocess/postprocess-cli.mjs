import {setTimeout as delay} from 'node:timers/promises';
import {DatabaseSync} from 'node:sqlite';
import {assertDatabasePath,protectSchema} from './workflow-ready.mjs';
import {nextPostprocessFromPath,runPostprocessFromPath} from './postprocess-worker.mjs';
import {postprocessPorts} from './postprocess-ports.mjs';
import {createPostprocessRenderer} from './postprocess-provider.mjs';

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
 const pds=postprocessPorts(),render=await createPostprocessRenderer({...options,pds});
 const stop=new AbortController();
 process.once('SIGINT',()=>stop.abort());process.once('SIGTERM',()=>stop.abort());
 let ready=false,heartbeatStarted=false,last;
 const pulse=async()=>{if(ready&&!stop.signal.aborted)await pds.heartbeat(stop.signal);};
 const timer=setInterval(()=>void pulse(),60000);timer.unref();
 try{
  while(!stop.signal.aborted){
   let result;
   try{
    const [frames,portraits]=await Promise.all([pds.capability(),pds.capabilityPortrait()]);
    ready=frames||portraits;
    if(ready&&!heartbeatStarted){heartbeatStarted=true;void pulse();}
    if(!ready)heartbeatStarted=false;
    const target=ready&&!stop.signal.aborted&&nextPostprocessFromPath(options.dbPath,{frames,portraits});
    result=target?await runPostprocessFromPath({...options,...target,pds,render,signal:stop.signal}):{state:ready?'idle':'not_ready'};
   }catch{result={state:'blocked',code:'POSTPROCESS_EXACT_TASK_REJECTED'};}
   const {state,code,taskId}=result;
   const line=JSON.stringify({service:'postprocess-worker',state,code,taskId});
   if(last!==line){console.log(line);last=line;}
   if(!stop.signal.aborted)await delay(1000,undefined,{signal:stop.signal}).catch(e=>{if(e.name!=='AbortError')throw e;});
  }
 }finally{clearInterval(timer);}
}
if(import.meta.url===new URL(process.argv[1],'file:').href)main().catch(()=>{console.error('POSTPROCESS_START_FAILED');process.exitCode=1;});
