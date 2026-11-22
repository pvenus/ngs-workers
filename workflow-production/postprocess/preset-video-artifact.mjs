/** Private decoded PNGs -> validated one-direction MP4. No inference or publication here. */
import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';
import {constants} from 'node:fs';
import {open,realpath,stat} from 'node:fs/promises';
import {join} from 'node:path';

export const directions=Object.freeze(['n','ne','e','se','s','sw','w','nw']);
// Published previews must fit the existing Local/Space playback limit.
export const width=768,height=432,maxBytes=8*1024*1024;
export const sha256=bytes=>createHash('sha256').update(bytes).digest('hex');
export const digestPattern=/^[a-f0-9]{64}$/;
const PNG=Buffer.from('89504e470d0a1a0a','hex');
const code=tag=>Error('PRESET_2D_'+tag);
export function need(ok,tag){if(!ok)throw code(tag);}
export function validTiming(value){
 return value!==null&&typeof value==='object'&&Number.isInteger(value.frameCount)&&value.frameCount>=2&&value.frameCount<=1000&&
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
  const bytes=await f.readFile(),after=await f.stat();
  need(bytes.length===before.size&&before.size===after.size&&before.ctimeMs===after.ctimeMs&&
   before.mtimeMs===after.mtimeMs,'FILE_CHANGED');
  return bytes;
 }finally{await f.close();}
}
async function trustedTool(name){
 need(name==='ffmpeg'||name==='ffprobe','ENCODER_SCOPE');
 const fixed='/opt/homebrew/bin/'+name;
 const actual=await realpath(fixed);
 const escaped=name; // Both possibilities are fixed literals checked above.
 need(new RegExp('^/opt/homebrew/(?:bin/'+escaped+'|Cellar/ffmpeg/[A-Za-z0-9._-]+/bin/'+escaped+')$').test(actual),'ENCODER_SCOPE');
 const s=await stat(actual);
 need(s.isFile()&&[0,process.getuid()].includes(s.uid)&&!(s.mode&0o022)&&(s.mode&0o111)!==0,'ENCODER_SCOPE');
 return actual;
}
export async function probePresetEncoder(){
 try{await Promise.all([trustedTool('ffmpeg'),trustedTool('ffprobe')]);return true;}catch{return false;}
}
async function invoke(name,args,signal){
 const tool=await trustedTool(name), deadline=AbortSignal.any([AbortSignal.timeout(180000),signal]);
 deadline.throwIfAborted();
 return new Promise((resolve,reject)=>{
  const child=spawn(tool,args,{env:{PATH:'/usr/bin:/bin',LANG:'C',LC_ALL:'C'},stdio:['ignore','pipe','ignore']});
  const chunks=[];let size=0,overflow=false;
  const stop=()=>child.kill('SIGKILL');
  const detach=()=>deadline.removeEventListener('abort',stop);
  deadline.addEventListener('abort',stop,{once:true});
  if(deadline.aborted)stop();
  child.stdout.on('data',b=>{
   size+=b.length;
   if(size>64*1024){overflow=true;stop();}else chunks.push(b);
  });
  child.once('error',()=>{detach();reject(code('ENCODER_UNAVAILABLE'));});
  child.once('close',status=>{
   detach();
   if(deadline.aborted||overflow||status!==0){reject(code('ENCODER_FAILED'));return;}
   resolve(Buffer.concat(chunks).toString('utf8'));
  });
 });
}
function ratio(value){
 if(typeof value!=='string'||!/^[1-9][0-9]{0,12}\/[1-9][0-9]{0,12}$/.test(value))return NaN;
 const [n,d]=value.split('/').map(Number);
 return n/d;
}
export function validateProbe(proof,timing){
 const streams=proof?.streams,v=streams?.[0],f=proof?.format;
 const duration=Number(v?.duration);
 need(validTiming(timing)&&Array.isArray(streams)&&streams.length===1&&v?.codec_type==='video'&&v.codec_name==='h264'&&
  v.width===width&&v.height===height&&v.pix_fmt==='yuv420p'&&v.nb_read_frames===String(timing.frameCount)&&
  Math.abs(ratio(v.avg_frame_rate)-timing.fps)<=1e-5&&Math.abs(ratio(v.r_frame_rate)-timing.fps)<=1e-5&&
  Number.isFinite(duration)&&Math.abs(duration-timing.frameCount/timing.fps)<=1e-4&&
  typeof f?.format_name==='string'&&f.format_name.split(',').includes('mp4'),'MP4_PROBE');
}

export async function encodePresetVideo(dir,timing,signal,{tool=invoke}={}){
 need(validTiming(timing),'TIMING');
 signal.throwIfAborted();
 const output=join(dir,'generic-2d.mp4');
 // Numeric timing and fixed directory paths only; no user/model arguments.
 const rate=String(timing.fps);
 await tool('ffmpeg',['-nostdin','-n','-v','error','-f','image2','-framerate',rate,'-start_number','0',
  '-i',join(dir,'frames','%05d.png'),'-map','0:v:0','-frames:v',String(timing.frameCount),
  '-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart','-an','-sn','-dn',output],signal);
 const verified=JSON.parse(await tool('ffprobe',['-v','error','-count_frames','-show_entries',
  'stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,r_frame_rate,nb_read_frames,duration:format=format_name',
  '-of','json',output],signal));
 validateProbe(verified,timing);
 const bytes=await boundedFile(output,maxBytes);
 need(mp4Shape(bytes),'MP4_FORMAT');
 return {mimeType:'video/mp4',sha256:sha256(bytes),bytes:bytes.length,base64:bytes.toString('base64'),
  fps:timing.fps,frameCount:timing.frameCount,width,height};
}
