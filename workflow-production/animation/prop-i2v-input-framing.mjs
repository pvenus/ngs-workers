/** Worker-owned green preparation for ordinary and grandfathered prop appends. No CLI/user paths. */
import {spawn} from 'node:child_process';
import {lstat,readFile,writeFile} from 'node:fs/promises';
import {isAbsolute,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {candleChromaProfile} from './vendor/prop-composite/prop-generate-envelope.mjs';
import {png,maxImageBytes} from './i2v-worker.mjs';

const entry=fileURLToPath(new URL('./prop-i2v-input-framing.py',import.meta.url));
const fail=()=>Error('PROP_I2V_FRAMING_FAILED');
export const defaultPropFramingProfile='prop-green-headroom-v1';
export const propFrameCanvas=Object.freeze({width:512,height:352});
export function createPropI2VInputFraming(python){
 if(typeof python!=='string'||!isAbsolute(python))throw fail();
 return async ({dir,source,renderProfile,signal})=>{
  if(![defaultPropFramingProfile,candleChromaProfile].includes(renderProfile))throw Error('PROP_I2V_FRAMING_PROFILE');
  signal.throwIfAborted();
  const first=join(dir,'original-prop-frame.png'),last=join(dir,'prepared-prop-frame.png');
  await writeFile(first,source,{mode:0o600,flag:'wx'});
  const deadline=AbortSignal.any([signal,AbortSignal.timeout(20_000)]);
  deadline.throwIfAborted();
  await new Promise((resolve,reject)=>{
   let errored=false;
   const p=spawn(python,['-B',entry,'--preset',renderProfile===candleChromaProfile?'candle-headroom-v1':defaultPropFramingProfile,'--source',first,'--output',last],
    {signal:deadline,killSignal:'SIGKILL',env:{PATH:'/usr/bin:/bin',HOME:dir,PYTHONNOUSERSITE:'1'},stdio:'ignore'});
   p.once('error',()=>{errored=true;});
   p.once('close',code=>{if(errored||code!==0||deadline.aborted)reject(fail());else resolve();});
  });
  const s=await lstat(last);
  if(!s.isFile()||s.nlink!==1||s.size<57||s.size>maxImageBytes)throw fail();
  const bytes=await readFile(last);
  const shape=png(bytes,maxImageBytes,{rgb:true});
  if(shape.width!==propFrameCanvas.width||shape.height!==propFrameCanvas.height)throw fail();
  return bytes;
 };
}
