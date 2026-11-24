import test from 'node:test';
import assert from 'node:assert/strict';
import {access} from 'node:fs/promises';
import {deflateSync,crc32} from 'node:zlib';
import {createHash} from 'node:crypto';
import {createCutsceneRenderer} from './cutscene-i2v.mjs';

function chunk(type,data){
 const value=Buffer.concat([Buffer.from(type),data]), out=Buffer.alloc(4), sum=Buffer.alloc(4);
 out.writeUInt32BE(data.length); sum.writeUInt32BE(crc32(value));
 return Buffer.concat([out,value,sum]);
}
function frame(red){
 const h=Buffer.alloc(13);h.writeUInt32BE(64);h.writeUInt32BE(32,4);h[8]=8;h[9]=2;
 const pixels=Buffer.alloc(32*(64*3+1));
 for(let y=0;y<32;y++)for(let x=0;x<64;x++){
  const i=y*193+1+x*3;
  pixels[i]=red; pixels[i+1]=y*8; pixels[i+2]=x*4;
 }
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',h),chunk('IDAT',deflateSync(pixels)),chunk('IEND',Buffer.alloc(0))]);
}
const source=frame(8), raw={width:64,height:32,fps:24,frames:Array.from({length:45},(_,i)=>frame(i*5))};

test('passes one original still to the shared renderer, preserving every RGB frame to the encoder',async()=>{
 let d;
 const run=createCutsceneRenderer({async renderI2V({dir,source:s,motionPrompt}){
  d=dir;assert.equal(s,source);assert.equal(motionPrompt,'The paper shifts in the wind.');return raw;
 },async encode(result,dir){assert.equal(result,raw);assert.equal(dir,d);return {mimeType:'video/mp4'};}});
 const result=await run({source,motionPrompt:'The paper shifts in the wind.'});
 assert.equal(result.sourceSha256,createHash('sha256').update(source).digest('hex'));
 await assert.rejects(access(d),{code:'ENOENT'});
});
test('invalid input or cancellation cannot reach the model',async()=>{
 const run=createCutsceneRenderer({renderI2V:async()=>assert.fail('must not render')});
 await assert.rejects(run({source,motionPrompt:'embedding:foreign'}),/CUTSCENE_PROMPT/);
 await assert.rejects(run({source:Buffer.from('invalid'),motionPrompt:'still'}),/I2V_PNG/);
 await assert.rejects(run({source,motionPrompt:'still',signal:AbortSignal.abort()}),{name:'AbortError'});
});
test('real FFmpeg produces one playable H.264 MP4 with all 45 RGB frames and original dimensions',
 {skip:process.env.NGS_TEST_CUTSCENE_ENCODER!=='1'},async()=>{
 const run=createCutsceneRenderer({renderI2V:async()=>raw});
 const video=await run({source,motionPrompt:'The light gradually shifts.'});
 assert.equal(video.bytes.toString('ascii',4,8),'ftyp');
 assert.equal(video.frameCount,45);assert.equal(video.fps,24);assert.equal(video.width,64);assert.equal(video.height,32);
});

test('a failed encoder does not leave a partial result or scratch files',async()=>{
 let d;
 const run=createCutsceneRenderer({async renderI2V({dir}){d=dir;return raw;},
   async encode(){throw Error('ENCODE_FAILED');}});
 await assert.rejects(run({source,motionPrompt:'A slow reveal.'}),/ENCODE_FAILED/);
 await assert.rejects(access(d),{code:'ENOENT'});
});
