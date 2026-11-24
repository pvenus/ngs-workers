/** Ephemeral runtime admission proof. This is not a work lease or model lock. */
import {homedir} from 'node:os';
import {basename,dirname,isAbsolute,join,resolve} from 'node:path';
import {lstat,realpath,rename,unlink,writeFile} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';

const base=join(homedir(),'Workspaces','Workers','runtime','prop-animation');
export const defaultPropBinding=join(base,'i2v-binding.json');
export const defaultPropReady=join(base,'prop-animation-ready.json');
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
export async function confirmBindingPath(path){await check(path,'i2v-binding.json');}

export function propAnimationReadiness({path=defaultPropReady,probe,assertKnown,now=Date.now}){
 let closed=false,pending,valid=false;
 async function clear(){
  valid=false;
  try{await check(path);await unlink(path);}catch{/* expiry stays fail-closed after any filesystem uncertainty */}
 }
 function refresh(){
  if(closed)return Promise.resolve(false);
  if(pending)return pending;
  pending=(async()=>{
   let stage;
   try{
    await check(path);assertKnown();
    const ready=await probe();
    require(ready?.ready===true&&ready.model==='wan2.2-ti2v-5b'&&ready.quantization==='q8_0');
    assertKnown();require(!closed);
    const checkedAt=now();require(Number.isSafeInteger(checkedAt)&&checkedAt>0);
    stage=join(dirname(path),'.prop-ready-'+randomUUID());
    await writeFile(stage,JSON.stringify({schemaVersion:'workflow.prop-animation-renderer-ready.v1',
      provider:'wan-ti2v-q8_0',checkedAt})+'\n',{mode:0o600,flag:'wx'});
    assertKnown();require(!closed);
    await rename(stage,path);stage=undefined;valid=true;return true;
   }catch{await clear();return false;}
   finally{if(stage)await unlink(stage).catch(()=>{});}
  })().finally(()=>{pending=undefined;});
  return pending;
 }
 return {refresh,ready:()=>valid&&!closed,async stop(){closed=true;await pending;await clear();},invalidate:clear};
}
