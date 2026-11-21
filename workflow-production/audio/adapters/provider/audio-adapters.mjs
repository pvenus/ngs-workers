/** HQ-only BGM port for the common worker; unconfigured until trusted assembly. */
import {spawn} from 'node:child_process';
import {homedir} from 'node:os';
import {isAbsolute,join,normalize} from 'node:path';
import {fileURLToPath} from 'node:url';
import {maxAudioBytes} from '../../audio-wav.mjs';
import {audioDuration,audioVocalTextLimit} from '../../vendor/planner/audio-generate-envelope.mjs';

const defaultHelper=fileURLToPath(new URL('./audio-hq-cli.py',import.meta.url));
const abs=value=>typeof value==='string'&&isAbsolute(value)&&normalize(value)===value;
const fail=code=>Error(code);
const maxResponse=Math.ceil(maxAudioBytes/3)*4+4096;
const maxInput=32768;

function launch(binary,argv,value,signal,spawnProcess){
 return new Promise((resolve,reject)=>{
  if(signal?.aborted){reject(fail('AUDIO_ONE_CANCELLED'));return;}
  const env=Object.fromEntries(['HOME','PATH','LANG','LC_ALL','TMPDIR','USER','LOGNAME'].filter(k=>process.env[k]!==undefined).map(k=>[k,process.env[k]]));
  env.GIT_OPTIONAL_LOCKS='0';
  const child=spawnProcess(binary,argv,{env,stdio:['pipe','pipe','pipe']});
  let pending=null,total=0,terminateTimer;
  const chunks=[];
  let diagnosticBytes=0, diagnostics=[];
  child.stderr.on('data',value=>{
   diagnosticBytes+=value.length;
   if(diagnosticBytes<=512)diagnostics.push(value);else diagnostics=[];
  });
  const stop=code=>{
   if(pending)return;
   pending=code;child.kill('SIGTERM');
   // Give the Python bridge time to cancel/join the owned model group and
   // remove private scratch. Native locks are inherited by the model process.
   terminateTimer=setTimeout(()=>child.kill('SIGKILL'),30_000);
  };
  const abort=()=>stop('AUDIO_ONE_CANCELLED');
  signal?.addEventListener('abort',abort,{once:true});
  if(signal?.aborted)abort();
  const timeout=setTimeout(()=>stop('AUDIO_ONE_PROVIDER_TIMEOUT'),1_200_000);
  child.stdout.on('data',value=>{
   if(pending)return;
   total+=value.length;
   if(total>maxResponse){stop('AUDIO_ONE_OUTPUT_OVERSIZE');return;}
   chunks.push(value);
  });
  child.stdin.on('error',()=>stop('AUDIO_ONE_PROVIDER_FAILED'));
  child.once('error',()=>stop('AUDIO_ONE_PROVIDER_UNAVAILABLE'));
  child.once('close',code=>{
   clearTimeout(timeout);clearTimeout(terminateTimer);
   signal?.removeEventListener('abort',abort);
   if(pending||code!==0){
    const error=fail(pending??'AUDIO_ONE_PROVIDER_FAILED');
    const diagnostic=!pending&&diagnosticBytes<=512&&
     /^AUDIO_HQ_DIAGNOSTIC stage=(setup|plan|render|verify|unknown) code=((?:AUDIO_HQ_|HQ_)[A-Z0-9_]{1,52})\r?\n?$/
     .exec(Buffer.concat(diagnostics).toString('utf8'));
    if(diagnostic){error.providerPhase=diagnostic[1];error.providerCode=diagnostic[2];}
    reject(error);return;
   }
   try{
    const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks,total)));
    if(!value||Object.keys(value).sort().join(',')!=='bpm,bpmBasis,wavBase64'||typeof value.wavBase64!=='string'||
     !(value.bpmBasis==='unavailable'&&value.bpm===null||value.bpmBasis==='model'&&Number.isSafeInteger(value.bpm)&&value.bpm>=30&&value.bpm<=300))
     throw fail('AUDIO_HQ_RECEIPT_INVALID');
    const wav=Buffer.from(value.wavBase64,'base64');
    if(wav.length<44||wav.length>maxAudioBytes||wav.toString('base64')!==value.wavBase64||
     wav.toString('ascii',0,4)!=='RIFF'||wav.toString('ascii',8,12)!=='WAVE')throw fail('AUDIO_HQ_RECEIPT_INVALID');
    resolve({wav,bpm:value.bpm,bpmBasis:value.bpmBasis});
   }catch{reject(fail('AUDIO_HQ_RECEIPT_INVALID'));}
  });
  child.stdin.end(value);
 });
}
export function createAudioAdapters({
 pythonPath='/opt/homebrew/bin/python3.12',helper=defaultHelper,
 hqBinding=join(homedir(),'Workspaces/Workers/config/ngs-music-hq.private.json'),
 spawnProcess=spawn,
}={}){
 if(![pythonPath,helper,hqBinding].every(abs)||typeof spawnProcess!=='function')throw fail('AUDIO_ONE_CONFIGURATION');
 const bgm=async(input,{signal}={})=>{
  const keys=input&&typeof input==='object'&&Object.keys(input).sort().join(',');
  const hasText=keys==='durationSeconds,kind,prompt,text,title';
  if(input===null||typeof input!=='object'||Array.isArray(input)||!(hasText||keys==='durationSeconds,kind,prompt,title')
   ||input.kind!=='bgm'||typeof input.title!=='string'||!input.title.trim()||[...input.title].length>120
   ||typeof input.prompt!=='string'||!input.prompt.trim()||!input.prompt.isWellFormed()||[...input.prompt].length>2000||
   !(input.durationSeconds===null
    ? !hasText||typeof input.text==='string'&&input.text.isWellFormed()&&input.text===input.text.trim()&&
      input.text.length>0&&[...input.text].length<=audioVocalTextLimit&&!/\x00/u.test(input.text)
    : !hasText&&Number.isFinite(input.durationSeconds)&&input.durationSeconds>=audioDuration.min&&input.durationSeconds<=audioDuration.max))
   throw fail('AUDIO_ONE_INPUT_INVALID');
  const bytes=Buffer.from(JSON.stringify(input));
  if(bytes.length>maxInput)throw fail('AUDIO_ONE_INPUT_OVERSIZE');
  return await launch(pythonPath,['-I','-B',helper,hqBinding],bytes,signal,spawnProcess);
 };
 return Object.freeze({bgm});
}
