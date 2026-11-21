/** Explicit runner for one original portrait, using the pinned legacy native provider. */
import {spawn} from 'node:child_process';
import {constants} from 'node:fs';
import {access,lstat,realpath} from 'node:fs/promises';
import {homedir} from 'node:os';
import {isAbsolute,normalize} from 'node:path';
import {fileURLToPath} from 'node:url';
import {exact} from './vendor/planner-f24d23a/planner.mjs';

const helper=fileURLToPath(new URL('./portrait-native.py',import.meta.url));
const python='/opt/homebrew/bin/python3.12';
const maxPNG=8*1024*1024, maxPayload=192*1024;
const signature=Buffer.from('89504e470d0a1a0a','hex');
const fail=code=>Error(code);

function options({hostConfig=process.env.WORKFLOW_IMAGE_HOST_CONFIG,hostConfigSha256=process.env.WORKFLOW_IMAGE_HOST_CONFIG_SHA256,
 pythonPath=python}={}){
 if(typeof hostConfig!=='string'||!isAbsolute(hostConfig)||normalize(hostConfig)!==hostConfig||
    typeof hostConfigSha256!=='string'||!/^[a-f0-9]{64}$/.test(hostConfigSha256)||
    typeof pythonPath!=='string'||!isAbsolute(pythonPath))throw fail('WORKFLOW_IMAGE_HOST_BINDING_REQUIRED');
 return {hostConfig,hostConfigSha256,pythonPath};
}

async function call({hostConfig,hostConfigSha256,pythonPath},mode,payload,signal){
 if(signal?.aborted)throw fail('WORKFLOW_IMAGE_CANCELLED');
 const st=await lstat(hostConfig);
 if(!st.isFile()||st.uid!==process.getuid()||st.nlink!==1||(st.mode&0o077)!==0||
    await realpath(hostConfig)!==hostConfig)throw fail('WORKFLOW_IMAGE_HOST_CONFIG_UNTRUSTED');
 await access(pythonPath,constants.X_OK);
 if(signal?.aborted)throw fail('WORKFLOW_IMAGE_CANCELLED');
 return new Promise((resolve,reject)=>{
  const child=spawn(pythonPath,['-I','-B',helper,mode,hostConfig,hostConfigSha256],{
   env:{HOME:homedir(),PATH:'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'},stdio:['pipe','pipe','ignore']});
  let length=0,code;const chunks=[];
  const terminate=(reason)=>{
   code??=reason;
   // The helper handles SIGTERM and joins the managed Codex process group.
   // Never kill the helper mid-generation and pretend the image stopped.
   child.kill('SIGTERM');
  };
  const abort=()=>terminate('WORKFLOW_IMAGE_CANCELLED');
  signal?.addEventListener('abort',abort,{once:true});
  const clock=setTimeout(()=>{
   if(mode==='probe')child.kill('SIGKILL');
   else terminate('WORKFLOW_IMAGE_NATIVE_TIMEOUT');
  },mode==='probe'?15000:920000);
  child.stdout.on('data',bytes=>{
   length+=bytes.length;
   if(length>(mode==='probe'?3:maxPNG))terminate('WORKFLOW_IMAGE_OUTPUT_INVALID');
   else chunks.push(bytes);
  });
  child.on('error',()=>{code??='WORKFLOW_IMAGE_NATIVE_START_FAILED';});
  child.stdin.on('error',()=>{code??='WORKFLOW_IMAGE_NATIVE_INPUT_FAILED';});
  child.on('close',status=>{
   clearTimeout(clock);signal?.removeEventListener('abort',abort);
   if(code||status!==0)reject(fail(code??'WORKFLOW_IMAGE_NATIVE_UNCONFIRMED'));
   else resolve(Buffer.concat(chunks));
  });
  child.stdin.end(payload);
  if(signal?.aborted)abort();
 });
}

export async function probePortraitRenderer(config){
 try{return (await call(options(config),'probe',Buffer.alloc(0))).equals(Buffer.from('ok\n'));}
 catch{return false;}
}

export function createPortraitRenderer(config){
 const pinned=options(config);
 return async (input,{signal}={})=>{
  if(!exact(input,['character','intent','sourceRefs','planningContent','conceptSources'])||
     !exact(input.character,['npcId','slug','name','role','actId','chapterId'])||
     typeof input.intent!=='string'||!Array.isArray(input.sourceRefs)||
     typeof input.planningContent!=='string'||!input.planningContent.trim()||
     !input.conceptSources||!Array.isArray(input.conceptSources.files))throw fail('WORKFLOW_IMAGE_INPUT_INVALID');
  const payload=Buffer.from(JSON.stringify(input));
  if(payload.length>maxPayload)throw fail('WORKFLOW_IMAGE_INPUT_TOO_LARGE');
  const raw=await call(pinned,'render',payload,signal);
  if(!raw.subarray(0,8).equals(signature))throw fail('WORKFLOW_IMAGE_OUTPUT_INVALID');
  return raw;
 };
}
