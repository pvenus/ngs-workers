/** Ephemeral runtime admission proof. This is not a work lease or model lock. */
import {homedir} from 'node:os';
import {basename,dirname,isAbsolute,join,resolve} from 'node:path';
import {lstat,realpath,rename,unlink,writeFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {WAN_I2V_MODEL_ID,WAN_I2V_QUANTIZATION,WAN_I2V_PROVIDER} from './i2v-wan-graph.mjs';

const base=join(homedir(),'Workspaces','Workers','runtime','prop-animation');
export const defaultPropBinding=join(base,'i2v-a14b-binding.json');
export const defaultPropReady=join(base,'prop-animation-ready.json');
export const defaultCinematicReady=join(base,'cinematic-animation-ready.json');
export const defaultSequenceReady=join(base,'cinematic-sequence-animation-ready.json');
export const defaultUpscaleReady=join(base,'cinematic-sequence-upscale-animation-ready.json');
const code='PROP_ANIMATION_READINESS_PATH';
const require=(ok)=>{if(!ok)throw Error(code);};
async function check(path,leaf='prop-animation-ready.json'){
 require(typeof path==='string'&&isAbsolute(path)&&resolve(path)===path&&basename(path)===leaf);
 const dir=dirname(path),st=await lstat(dir);
 require(st.isDirectory()&&st.uid===process.getuid()&&(st.mode&0o777)===0o700&&await realpath(dir)===dir);
 try{
  const f=await lstat(path);
  require(f.isFile()&&f.nlink===1&&f.uid===process.getuid()&&(f.mode&0o777)===0o600);
 }catch(e){if(e.code!=='ENOENT')throw e;}
}
export async function confirmBindingPath(path){await check(path,'i2v-a14b-binding.json');}

export function propAnimationReadiness(options){
 return runtimeReadiness(options,defaultPropReady,'prop-animation-ready.json','workflow.prop-animation-renderer-ready.v1');
}
export function cinematicAnimationReadiness(options){
 return runtimeReadiness(options,defaultCinematicReady,'cinematic-animation-ready.json','workflow.cinematic-animation-renderer-ready.v1');
}
export function cinematicSequenceReadiness(options){
 return runtimeReadiness(options,defaultSequenceReady,'cinematic-sequence-animation-ready.json','workflow.cinematic-sequence-animation-renderer-ready.v1');
}
export function cinematicUpscaleReadiness(options){
 return runtimeReadiness(options,defaultUpscaleReady,'cinematic-sequence-upscale-animation-ready.json',
  'workflow.cinematic-sequence-upscale-renderer-ready.v1','seedvr2-7b-fp16',
  proof=>proof?.ready===true&&proof.model==='seedvr2-7b-fp16');
}
function runtimeReadiness({path=undefined,probe,assertKnown,now=Date.now},defaultPath,leaf,schemaVersion,provider=WAN_I2V_PROVIDER,
 matchesProof=proof=>proof?.ready===true&&proof.model===WAN_I2V_MODEL_ID&&proof.quantization===WAN_I2V_QUANTIZATION){
 if(path===undefined)path=defaultPath;
 let closed=false,pending,valid=false;
 async function clear(){
  valid=false;
  try{await check(path,leaf);await unlink(path);}catch{/* expiry stays fail-closed after any filesystem uncertainty */}
 }
 function refresh(){
  if(closed)return Promise.resolve(false);
  if(pending)return pending;
  pending=(async()=>{
   let stage;
   try{
    await check(path,leaf);assertKnown();
    const ready=await probe();
    require(matchesProof(ready));
    assertKnown();require(!closed);
    const checkedAt=now();require(Number.isSafeInteger(checkedAt)&&checkedAt>0);
    stage=join(dirname(path),'.animation-ready-'+randomUUID());
    await writeFile(stage,JSON.stringify({schemaVersion,
      provider,checkedAt})+'\n',{mode:0o600,flag:'wx'});
    assertKnown();require(!closed);
    await rename(stage,path);stage=undefined;valid=true;return true;
   }catch{await clear();return false;}
   finally{if(stage)await unlink(stage).catch(()=>{});}
  })().finally(()=>{pending=undefined;});
  return pending;
 }
 return {refresh,ready:()=>valid&&!closed,async stop(){closed=true;await pending;await clear();},invalidate:clear};
}
