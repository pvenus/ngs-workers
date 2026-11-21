import {createHash} from 'node:crypto';
import {audioKind,audioDuration} from './vendor/planner/audio-generate-envelope.mjs';

const MiB=1024*1024;
export const maxAudioBytes=24*MiB;
const reject=()=>{throw Error('AUDIO_WAV_INVALID');};
export const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
/** Bounded RIFF/WAVE with one PCM16 format and one nonempty audio data chunk. */
export function validateWav(wav,kind,durationSeconds){
 if(!Buffer.isBuffer(wav)||wav.length<44||wav.length>maxAudioBytes||
  wav.toString('latin1',0,4)!=='RIFF'||wav.toString('latin1',8,12)!=='WAVE'||wav.readUInt32LE(4)!==wav.length-8)reject();
 let rate,align,data;
 for(let i=12,count=0;i<wav.length;){
  if(++count>64||i+8>wav.length)reject();
  const tag=wav.toString('latin1',i,i+4),size=wav.readUInt32LE(i+4),pos=i+8,next=pos+size+(size&1);
  if(next>wav.length)reject();
  if(tag==='fmt '){
   if(rate!==undefined||size!==16||data!==undefined||wav.readUInt16LE(pos)!==1||wav.readUInt16LE(pos+14)!==16)reject();
   const channels=wav.readUInt16LE(pos+2);
   rate=wav.readUInt32LE(pos+4);align=wav.readUInt16LE(pos+12);
   if(![1,2].includes(channels)||rate<8000||rate>96000||align!==channels*2||wav.readUInt32LE(pos+8)!==rate*align)reject();
  }else if(tag==='data'){
   if(data!==undefined||rate===undefined||size===0||size%align!==0)reject();
   data=size;
  }else reject();
  i=next;
 }
 if(rate===undefined||data===undefined)reject();
 const duration=data/(rate*align);
 if(kind!==audioKind||!(durationSeconds===null
   ? duration>=audioDuration.min&&duration<=audioDuration.autoMax
   : Number.isFinite(durationSeconds)&&durationSeconds>=audioDuration.min&&durationSeconds<=audioDuration.max&&
     Math.abs(duration-durationSeconds)<=0.100000001))reject();
 return Object.freeze({mimeType:'audio/wav',sha256:digest(wav),bytes:wav.length,base64:wav.toString('base64')});
}
