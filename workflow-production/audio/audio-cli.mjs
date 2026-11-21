import {setTimeout as delay} from 'node:timers/promises';
import {resolve,isAbsolute} from 'node:path';
import {pathToFileURL} from 'node:url';
import {assertDatabasePath} from './workflow-ready.mjs';
import {nextAudioFromPath,runAudioFromPath} from './audio-worker.mjs';
import {createCaptionComposer} from './bgm-caption.mjs';
import {audioPorts} from './audio-ports.mjs';

export function parseArgs(args){
 const flags=['--run','--db','--adapter-module','--adapter-config'];
 if(args.length!==7||args[0]!==flags[0]||!flags.slice(1).every((flag,i)=>args[1+2*i]===flag&&isAbsolute(args[2+2*i])&&resolve(args[2+2*i])===args[2+2*i]))
  throw Error('AUDIO_EXPLICIT_CONFIG_REQUIRED');
 return {dbPath:args[2],modulePath:args[4],configPath:args[6]};
}
export async function residentAudio({dbPath,pds,adapters,composeCaption,signal,log=console.log,pause=delay}){
 assertDatabasePath(dbPath);
 const hasBgm=typeof adapters?.bgm==='function',base={dbPath,pds,adapters,composeCaption,signal};
 let ready=false,pulsing=false,last;
 const pulse=async()=>{
  if(ready&&!signal.aborted&&!pulsing){
   pulsing=true;
   try{await pds.heartbeat(signal);}catch{}finally{pulsing=false;}
  }
 };
 const timer=setInterval(()=>void pulse(),60000);timer.unref();
 try{
  while(!signal.aborted){
   let r;
   try{
    const previouslyReady=ready;
    ready=hasBgm&&await pds.capability(signal);
    if(ready&&!previouslyReady)void pulse();
    const target=ready&&!signal.aborted&&nextAudioFromPath(dbPath);
    r=target?await runAudioFromPath({...base,...target}):{state:ready?'idle':'not_ready'};
   }catch{r={state:'blocked',code:'AUDIO_EXACT_TASK_REJECTED'};}
   const {state,code,taskId,providerPhase,providerCode}=r;
   const line=JSON.stringify({service:'audio-worker',state,code,taskId,providerPhase,providerCode});
   if(last!==line){log(line);last=line;}
   if(!signal.aborted)await pause(1000,undefined,{signal}).catch(e=>{if(e.name!=='AbortError')throw e;});
  }
 }finally{clearInterval(timer);}
}
async function main(){
 const {dbPath,modulePath,configPath}=parseArgs(process.argv.slice(2));
 assertDatabasePath(dbPath);
 const {loadAudioAdapters}=await import(pathToFileURL(modulePath).href);
 const adapters=await loadAudioAdapters(configPath); // trusted deployment-owned module/config, never request-configurable
 const stop=new AbortController();
 process.once('SIGTERM',()=>stop.abort());process.once('SIGINT',()=>stop.abort());
 await residentAudio({dbPath,pds:audioPorts(),adapters,composeCaption:createCaptionComposer(),signal:stop.signal});
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href)
 main().catch(()=>{console.error('AUDIO_START_FAILED');process.exitCode=1;});
