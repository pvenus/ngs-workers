/** CPU-only final pixel correction of the already verified SeedVR2 output. */
import {execFile} from 'node:child_process';
import {lstat,mkdir,readFile,readdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';
import {maxFrameBytes,png} from './i2v-worker.mjs';
import {backgroundUpscaleMaxBytes} from './seedvr2-upscale.mjs';

const run=promisify(execFile),entry=fileURLToPath(new URL('../postprocess/reference-palette-sequence.py',import.meta.url));
export const backgroundAnimationPixelMethod='reference-palette-v1';
export function createReferencePaletteCorrection(python,{execute=run}={}){
 return async ({dir,reference,output,signal,downscale=1})=>{
  if(![1,2].includes(downscale)||![8,45].includes(output.frames.length))throw Error('BACKGROUND_ANIMATION_PIXEL_PROFILE');
  const width=output.width/downscale,height=output.height/downscale;
  const path=join(dir,'background-animation-pixel'),src=join(path,'input'),dst=join(path,'output');
  await mkdir(path,{mode:0o700});await mkdir(src,{mode:0o700});await mkdir(dst,{mode:0o700});
  const put=(p,data)=>writeFile(p,data,{flag:'wx',mode:0o600});
  const names=output.frames.map((_,i)=>String(i).padStart(4,'0')+'.png');
  await put(join(path,'reference.png'),reference);
  for(const [i,name] of names.entries()){
   signal.throwIfAborted();await put(join(src,name),output.frames[i]);
  }
  try{await execute(python,['-B',entry,path,String(output.width),String(output.height),String(output.frames.length),String(downscale)],{
   signal,timeout:120_000,maxBuffer:64*1024,encoding:'utf8',
  });}catch(error){throw Error(signal.aborted?'BACKGROUND_ANIMATION_PIXEL_CANCELLED':'BACKGROUND_ANIMATION_PIXEL_FAILED',{cause:error});}
  const files=(await readdir(dst)).sort();
  if(files.length!==names.length||files.some((file,i)=>file!==names[i]))throw Error('BACKGROUND_ANIMATION_PIXEL_FRAME_SET');
  const frames=[];let bytes=0;
  for(const name of names){
   signal.throwIfAborted();
   const file=join(dst,name),stat=await lstat(file);
   if(!stat.isFile()||stat.nlink!==1||stat.size>maxFrameBytes||stat.size+bytes>backgroundUpscaleMaxBytes)throw Error('BACKGROUND_ANIMATION_PIXEL_FRAME_BYTES');
   const raw=await readFile(file),shape=png(raw,maxFrameBytes,{rgb:true});
   if(raw.length!==stat.size||shape.width!==width||shape.height!==height)throw Error('BACKGROUND_ANIMATION_PIXEL_FRAME_SHAPE');
   bytes+=raw.length;frames.push(raw);
  }
  return {...output,width,height,frames};
 };
}
