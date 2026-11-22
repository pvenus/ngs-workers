/** One exact private ordered directional lossless PNG bundle. No encoding here. */
import {createHash} from 'node:crypto';
import {constants} from 'node:fs';
import {open} from 'node:fs/promises';
import {join} from 'node:path';

export const directions=Object.freeze(['n','ne','e','se','s','sw','w','nw']);
export const width=768,height=432,maxFrameBytes=8*1024*1024,maxBundleBytes=64*1024*1024,
 maxReferenceBytes=64*1024*1024,maxFrames=1_000,maxHeaderBytes=128*1024;
export const sha256=bytes=>createHash('sha256').update(bytes).digest('hex');
export const digestPattern=/^[a-f0-9]{64}$/;
const PNG=Buffer.from('89504e470d0a1a0a','hex');
const code=tag=>Error('PRESET_2D_'+tag);
export function need(ok,tag){if(!ok)throw code(tag);}
export function validTiming(value){
 return value!==null&&typeof value==='object'&&Number.isInteger(value.frameCount)&&value.frameCount>=2&&value.frameCount<=maxFrames&&
  typeof value.fps==='number'&&Number.isFinite(value.fps)&&value.fps>=1&&value.fps<=120;
}
export function pngShape(bytes){
 return Buffer.isBuffer(bytes)&&bytes.length>=33&&bytes.subarray(0,8).equals(PNG)&&bytes.toString('ascii',12,16)==='IHDR'&&
  bytes.readUInt32BE(16)>0&&bytes.readUInt32BE(20)>0&&bytes.readUInt32BE(16)<=8192&&bytes.readUInt32BE(20)<=8192;
}
export function mp4Shape(bytes){
 if(!Buffer.isBuffer(bytes)||bytes.length<24||bytes.toString('ascii',4,8)!=='ftyp')return false;
 const first=bytes.readUInt32BE(0);
 return first>=16&&first<bytes.length;
}
async function boundedFile(path,limit){
 const f=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
 try{
  const before=await f.stat();
  need(before.isFile()&&before.nlink===1&&before.uid===process.getuid()&&before.size>0&&before.size<=limit,'FILE_SCOPE');
  const target=Buffer.allocUnsafe(before.size+1);
  let read=0;
  while(read<target.length){
   const {bytesRead}=await f.read(target,read,target.length-read,read);
   if(!bytesRead)break;
   read+=bytesRead;
  }
  const bytes=target.subarray(0,read),after=await f.stat();
  need(bytes.length===before.size&&before.size===after.size&&before.ctimeMs===after.ctimeMs&&
   before.mtimeMs===after.mtimeMs,'FILE_CHANGED');
  return bytes;
 }finally{await f.close();}
}
export async function makeFrameBundle(dir,out,timing,{claim,direction,sourcePresetHash},signal){
 need(validTiming(timing)&&out?.width===width&&out.height===height&&out.fps===timing.fps&&
  Array.isArray(out.frames)&&out.frames.length===timing.frameCount&&Array.isArray(out.frameHashes)&&
  out.frameHashes.length===timing.frameCount&&directions.includes(direction)&&
  typeof sourcePresetHash==='string'&&digestPattern.test(sourcePresetHash),'FRAME_MANIFEST');
 const frames=[],chunks=[];
 let total=0;
 for(let i=0;i<timing.frameCount;i++){
  signal.throwIfAborted();
  const name=String(i).padStart(5,'0')+'.png';
  need(out.frames[i]===name&&typeof out.frameHashes[i]==='string'&&digestPattern.test(out.frameHashes[i]),'FRAME_ORDER');
  const bytes=await boundedFile(join(dir,'wan','frames',name),maxFrameBytes);
  total+=bytes.length;
  need(total<=maxBundleBytes&&pngShape(bytes)&&bytes.readUInt32BE(16)===width&&bytes.readUInt32BE(20)===height&&
   sha256(bytes)===out.frameHashes[i],'FRAME_HASH');
  chunks.push(bytes);
  frames.push({bytes:bytes.length,sha256:out.frameHashes[i]});
 }
 const header=Buffer.from(JSON.stringify({claim,frames}));
 need(header.length>0&&header.length<=maxHeaderBytes,'FRAME_HEADER');
 const length=Buffer.alloc(4);length.writeUInt32BE(header.length);
 // Keys are explicitly sorted, including the byte/hash keys of each frame.
 // No physical scratch path or renderer metadata goes into the proof.
 const manifestSha256=sha256(JSON.stringify({direction,frames,height,sourcePresetHash,width}));
 return {wire:Buffer.concat([length,header,...chunks],4+header.length+total),manifestSha256};
}
