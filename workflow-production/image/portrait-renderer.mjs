import {validCinematicSourceRenderInput,validBackgroundRenderInput,sourceGeometry} from './cinematic-source-worker.mjs';
/** Explicit runner for one original portrait, using the pinned legacy native provider. */
import {createCodexSessionClient} from './codex-session-client.mjs';
import {constants} from 'node:fs';
import {access,lstat,realpath} from 'node:fs/promises';
import {isAbsolute,normalize} from 'node:path';
import {exact} from './vendor/planner-f24d23a/planner.mjs';
import {preparePropImageInput,propImageRequestSchema,propDesignContextSchema} from './prop-image-design.mjs';

const helper=new URL('./portrait-native.py',import.meta.url);
const python='/opt/homebrew/bin/python3.12';
const maxPNG=8*1024*1024, maxPayload=192*1024;
const signature=Buffer.from('89504e470d0a1a0a','hex');
const fail=code=>Error(code);
export const nativePortraitExitCode=status=>({
 21:'WORKFLOW_IMAGE_TRANSPARENCY_REJECTED',
 22:'WORKFLOW_IMAGE_CLI_OUTCOME_UNCONFIRMED',
 23:'WORKFLOW_IMAGE_CLI_PROCESS_FAILED',
 24:'WORKFLOW_IMAGE_CLI_RESPONSE_INVALID',
 25:'WORKFLOW_IMAGE_CLI_REPORTED_BLOCKED',
 26:'WORKFLOW_IMAGE_CLI_RESULT_REJECTED',
}[status]??'WORKFLOW_IMAGE_NATIVE_UNCONFIRMED');


function options({hostConfig=process.env.WORKFLOW_IMAGE_HOST_CONFIG,hostConfigSha256=process.env.WORKFLOW_IMAGE_HOST_CONFIG_SHA256,
 pythonPath=python}={}){
 if(typeof hostConfig!=='string'||!isAbsolute(hostConfig)||normalize(hostConfig)!==hostConfig||
    typeof hostConfigSha256!=='string'||!/^[a-f0-9]{64}$/.test(hostConfigSha256)||
    typeof pythonPath!=='string'||!isAbsolute(pythonPath))throw fail('WORKFLOW_IMAGE_HOST_BINDING_REQUIRED');
 return {hostConfig,hostConfigSha256,pythonPath};
}

// The production worker has one immutable host binding. Scope is replaced
// only for locally injected alternate bindings (e.g. verification fixtures).
let bridge, key;
export function closeImageCodexSession(){bridge?.close();bridge=key=undefined;}
async function call({hostConfig,hostConfigSha256,pythonPath},mode,payload,signal){
 if(signal?.aborted)throw fail('WORKFLOW_IMAGE_CANCELLED');
 const st=await lstat(hostConfig);
 if(!st.isFile()||st.uid!==process.getuid()||st.nlink!==1||(st.mode&0o077)!==0||
    await realpath(hostConfig)!==hostConfig)throw fail('WORKFLOW_IMAGE_HOST_CONFIG_UNTRUSTED');
 await access(pythonPath,constants.X_OK);
 const identity=JSON.stringify([pythonPath,hostConfig,hostConfigSha256]);
 if(key!==identity){
  closeImageCodexSession();
  bridge=createCodexSessionClient({script:helper,python:pythonPath,args:[hostConfig,hostConfigSha256]});
  key=identity;
 }
 let result;
 try{
  result=await bridge.call({mode,data:mode==='probe'?null:JSON.parse(payload)},
    {signal,timeoutMs:mode==='probe'?15000:920000,maxReply:12*1024*1024});
 }catch(error){
  throw fail(error.message==='cancelled'?'WORKFLOW_IMAGE_CANCELLED':error.message==='timeout'
   ?'WORKFLOW_IMAGE_NATIVE_TIMEOUT':'WORKFLOW_IMAGE_NATIVE_UNCONFIRMED');
 }
 if(result?.code!==0)throw fail(nativePortraitExitCode(result?.code));
 if(typeof result.base64!=='string')throw fail('WORKFLOW_IMAGE_OUTPUT_INVALID');
 const raw=Buffer.from(result.base64,'base64');
 if(raw.length>(mode==='probe'?3:maxPNG))throw fail('WORKFLOW_IMAGE_OUTPUT_INVALID');
 return raw;
}

export async function probePortraitRenderer(config){
 try{return (await call(options(config),'probe',Buffer.alloc(0))).equals(Buffer.from('ok\n'));}
 catch{return false;}
}

export const neutralReferenceKind='motion-neutral-2d.v1';
export function createNeutralReferenceRenderer(config){
 const pinned=options(config);
 return async (input,{signal}={})=>{
  if(!exact(input,['referenceKind'])||input.referenceKind!==neutralReferenceKind)
   throw fail('WORKFLOW_IMAGE_NEUTRAL_REFERENCE_INVALID');
  const png=await call(pinned,'render-neutral',Buffer.from(JSON.stringify(input)),signal);
  if(!png.subarray(0,8).equals(signature))throw fail('WORKFLOW_IMAGE_OUTPUT_INVALID');
  return png;
 };
}

export function createPropImageRenderer(config){
 const pinned=options(config);
 return async (input,{signal}={})=>{
  const green=input!=null&&Object.hasOwn(input,'background');
  const hasReference=input!=null&&Object.hasOwn(input,'reference');
  const ref=input?.reference;
  if(!exact(input,['projectId','propId','state','lane','intent','sources','contextSha256',...(green?['background']:[]),...(hasReference?['reference']:[])])||
     (hasReference&&(!green||!exact(ref,['assetId','sha256','base64'])||
      typeof ref.sha256!=='string'||!/^[a-f0-9]{64}$/u.test(ref.sha256)||ref.assetId!==ref.sha256+'.png'||
      typeof ref.base64!=='string'||!ref.base64.length||ref.base64.length>Math.ceil(8*1024*1024/3)*4))||
     (green&&(input.background!=='green-screen.v1'||input.state!=='base'||input.lane!=='detail')))throw fail('PROP_IMAGE_INPUT_INVALID');
  const {sources,contextSha256,background,reference,...request}=input;
  preparePropImageInput({...request,schemaVersion:propImageRequestSchema,designContextSha256:contextSha256},
   {schemaVersion:propDesignContextSchema,projectId:input.projectId,sources,contextSha256},
   {formalBase:input.state==='base'&&input.lane==='detail'});
  const wire=Buffer.from(JSON.stringify(input));
  if(Buffer.byteLength(JSON.stringify({...input,reference:undefined}))>maxPayload||wire.length>12*1024*1024)
   throw fail('WORKFLOW_IMAGE_INPUT_TOO_LARGE');
  const raw=await call(pinned,'render-prop',wire,signal);
  if(!raw.subarray(0,8).equals(signature)||(green&&raw.length<57))
   throw fail('WORKFLOW_PROP_BASE_PNG_SIGNATURE');
  if(green&&(raw.readUInt32BE(16)!==1536||raw.readUInt32BE(20)!==1024))
   throw fail('WORKFLOW_PROP_BASE_PNG_CANVAS');
  if(green&&(raw[24]!==8||raw[25]!==2))throw fail('WORKFLOW_PROP_BASE_PNG_COLOR');
  return raw;
 };
}

export function createPortraitRenderer(config){
 const pinned=options(config);
 return async (input,{signal}={})=>{
  const modern=input!=null&&(Object.hasOwn(input,'artDirection')||Object.hasOwn(input,'poseDirection'));
  const base=modern?['character','artDirection','poseDirection']:['character','intent','sourceRefs','planningContent','conceptSources'];
  const ref=input?.reference;
  const direction=input?.direction;
  const styled=input!=null&&(Object.hasOwn(input,'imageStyle')||Object.hasOwn(input,'posePreset'));
  if(!exact(input,[...base,...(ref?['reference']:[]),...(direction?['direction']:[]),...(styled?['imageStyle','posePreset']:[])])||
     (styled&&(!ref||!(input.imageStyle==='pixel-sprite.v1'&&['idle','combat'].includes(input.posePreset)||
      input.imageStyle==='original-portrait.v1'&&input.posePreset==='combat'&&!direction)))||
     (direction&&(!ref||!['n','ne','e','se','s','sw','w','nw'].includes(direction)))||
     (ref&&(!exact(ref,['assetId','sha256','base64'])||typeof ref.base64!=='string'||!(/^[a-f0-9]{64}$/).test(ref.sha256)||ref.assetId!==ref.sha256+'.png'))||
     !exact(input.character,['npcId','slug','name','role','actId','chapterId'])||
     (modern?
      [input.artDirection,input.poseDirection].some(text=>typeof text!=='string'||!text.trim()):
      (typeof input.intent!=='string'||!Array.isArray(input.sourceRefs)||
       typeof input.planningContent!=='string'||!input.planningContent.trim()||
       !input.conceptSources||!Array.isArray(input.conceptSources.files))))throw fail('WORKFLOW_IMAGE_INPUT_INVALID');
  const payload=Buffer.from(JSON.stringify(input));
  if(Buffer.byteLength(JSON.stringify({...input,reference:undefined}))>maxPayload||payload.length>12*1024*1024)throw fail('WORKFLOW_IMAGE_INPUT_TOO_LARGE');
  const raw=await call(pinned,'render',payload,signal);
  if(!raw.subarray(0,8).equals(signature))throw fail('WORKFLOW_IMAGE_OUTPUT_INVALID');
  return raw;
 };
}


export function createCinematicSourceRenderer(config){
 const pinned=options(config);
 return async (input,{signal}={})=>{
  const {reference,cinematic,...scene}=input??{};
  const background=validBackgroundRenderInput(scene);
  if(reference&&(!background||cinematic||!exact(reference,['assetId','sha256','base64'])||
    !/^[a-f0-9]{64}$/u.test(reference.sha256)||reference.assetId!==reference.sha256+'.png'||
    typeof reference.base64!=='string'||reference.base64.length>Math.ceil(8*1024*1024/3)*4))
   throw fail('BACKGROUND_REFERENCE_INVALID');
  if(cinematic&&(!background||!validCinematicSourceRenderInput({title:scene.title,prompt:'Cinematic environment',design:cinematic})))
   throw fail('BACKGROUND_CINEMATIC_DESIGN_INVALID');
  if(!background&&!validCinematicSourceRenderInput(input))throw fail('CINEMATIC_SOURCE_INPUT_INVALID');
  const raw=await call(pinned,background?'render-background-source':'render-cinematic-source',Buffer.from(JSON.stringify(input)),signal);
  sourceGeometry(raw);
  return raw;
 };
}
