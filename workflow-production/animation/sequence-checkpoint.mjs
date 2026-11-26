/** Private, claim-scoped intermediate for reusable sequence operations in this resident.
 * Result references are hashes, never file paths; none of these bytes are published.
 */
import {lstat,mkdir,mkdtemp,open,readFile,rename,realpath,rm,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {maxFrameBytes} from './i2v-worker.mjs';

const identity=/^request\.official\.[a-f0-9]{64}$/u;
const task=/^workflow\.task\.[a-f0-9]{64}$/u;
const digest=/^[a-f0-9]{64}$/u;
const need=ok=>{if(!ok)throw Error('SEQUENCE_CHECKPOINT_INVALID');};
const frameName=i=>String(i).padStart(4,'0')+'.png';
async function privateDir(path){
 const v=await lstat(path);need(v.isDirectory()&&v.uid===process.getuid()&&(v.mode&0o077)===0&&await realpath(path)===path);
}
async function get(path,max){
 const s=await lstat(path);need(s.isFile()&&s.nlink===1&&s.uid===process.getuid()&&(s.mode&0o077)===0&&s.size>0&&s.size<=max);
 const bytes=await readFile(path);need(bytes.length===s.size);return bytes;
}
async function syncDir(path){const f=await open(path,'r');try{await f.sync();}finally{await f.close();}}
const encodedRef=c=>'background.transient.'+sha(canonical(c));

export function sequenceCheckpoints(root){
 const checked=async (requestId,variant,create=false)=>{
  need(identity.test(requestId)&&['cinematic','pixel'].includes(variant));
  const parent=join(root,requestId), branch=join(parent,variant);
  for(const path of [root,parent,branch]){
   if(create)try{await mkdir(path,{mode:0o700});}catch(e){if(e.code!=='EEXIST')throw e;}
   await privateDir(path);
  }
  return branch;
 };
 const leaf=phase=>{need(['wan','upscale','pixel_correct'].includes(phase));return phase;};
 return {
  async read({requestId,variant,stage,taskId,payloadSha256,expectedRef,optional=false}){
   need(task.test(taskId)&&digest.test(payloadSha256));
   const folder=await checked(requestId,variant,optional);
   const where=join(folder,leaf(stage));
   try{await privateDir(where);}catch(e){if(optional&&e.code==='ENOENT')return null;throw e;}
   const raw=await get(join(where,'manifest.json'),64*1024);
   const receipt=JSON.parse(raw.toString());
   need(receipt.requestId===requestId&&receipt.variant===variant&&receipt.stage===stage&&receipt.taskId===taskId&&
    receipt.payloadSha256===payloadSha256&&canonical(receipt)===raw.toString());
   const reference=encodedRef(receipt);
   need(expectedRef===undefined||reference===expectedRef);
   const m=receipt.manifest;need([8,45].includes(m.frameCount)&&m.frames.length===m.frameCount);
   const frames=[];let total=0;
   for(const [index,f] of m.frames.entries()){
    const bytes=await get(join(where,frameName(index)),maxFrameBytes);
    total+=bytes.length;need(total<=128*1024*1024&&f.index===index&&f.bytes===bytes.length&&f.sha256===sha(bytes));
    frames.push(bytes);
   }
   return {reference,manifest:m,output:{frames,fps:m.masterFps,width:m.width,height:m.height}};
  },
  async write({requestId,variant,stage,taskId,payloadSha256,manifest,frames,current,signal}){
   need(task.test(taskId)&&digest.test(payloadSha256)&&[8,45].includes(frames.length));
   const folder=await checked(requestId,variant,true);
   const temp=await mkdtemp(join(folder,'.pending-'));
   const receipt={requestId,variant,stage,taskId,payloadSha256,manifest};
   const put=(name,value)=>writeFile(join(temp,name),value,{mode:0o600,flag:'wx',flush:true});
   try{
    for(const [i,frame] of frames.entries()){signal.throwIfAborted();await put(frameName(i),frame);}
    await put('manifest.json',canonical(receipt));await syncDir(temp);
    signal.throwIfAborted();current();
    await rename(temp,join(folder,leaf(stage)));await syncDir(folder);
    return encodedRef(receipt);
   }finally{await rm(temp,{recursive:true,force:true});}
  },
  async release({requestId,variant,stage}){
   let folder;
   try{folder=await checked(requestId,variant);}catch(e){if(e.code==='ENOENT')return;throw e;}
   const entry=join(folder,leaf(stage));
   try{await privateDir(entry);}catch(e){if(e.code==='ENOENT')return;throw e;}
   await rm(entry,{recursive:true});await syncDir(folder);
  },
 };
}
