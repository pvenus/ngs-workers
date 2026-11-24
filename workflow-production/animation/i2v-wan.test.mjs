import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {createHash} from 'node:crypto';
import {mkdtemp,mkdir,realpath,rm,writeFile,readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {deflateSync,crc32} from 'node:zlib';
import {createWanI2V,verifyI2VFile,wanCheckpoint,wanCheckpointSha256,stopI2VGroup,readWanI2VBinding} from './i2v-wan.mjs';
import {WAN_I2V_LENGTH,WAN_I2V_SAVE_NODE,WAN_I2V_SAVE_PREFIX} from './i2v-wan-graph.mjs';
const sha=v=>createHash('sha256').update(v).digest('hex');
const signal=()=>new AbortController().signal;
const absent=()=>Object.assign(Error(),{code:'ESRCH'});
const denied=()=>Object.assign(Error(),{code:'EPERM'});
const spec=['__init__.py','nodes.py','loader.py','ops.py','dequant.py'];
const poll=()=>new Promise(setImmediate);
async function tmp(t){
 const path=await realpath(await mkdtemp(join(tmpdir(),'i2v-wan-test-')));
 t.after(()=>rm(path,{force:true,recursive:true}));return path;
}
function gguf(version=3){
 const h=Buffer.alloc(25);
 h.write('GGUF');h.writeUInt32LE(version,4);h.writeBigUInt64LE(1n,8);h.writeBigUInt64LE(1n,16);
 return h;
}
async function installFixture(t){
 const root=await tmp(t);
 const cfg={schemaVersion:'ngs.animation-i2v-int8-binding.v1',comfyRoot:join(root,'comfy'),
  python:join(root,'venv','bin','python'),ggufNodesRoot:join(root,'gguf-nodes'),modelsRoot:join(root,'models')};
 await Promise.all([cfg.comfyRoot,cfg.ggufNodesRoot,join(cfg.modelsRoot,'diffusion_models'),join(root,'venv','bin'),
  join(root,'venv','lib','python3.12','site-packages','gguf')].map(p=>mkdir(p,{recursive:true})));
 await Promise.all([join(cfg.comfyRoot,'main.py'),...spec.map(f=>join(cfg.ggufNodesRoot,f)),
  join(root,'venv','lib','python3.12','site-packages','gguf','__init__.py')].map(p=>writeFile(p,'# isolated fixture')));
 await writeFile(cfg.python,'# never executed',{mode:0o700});
 const path=join(cfg.modelsRoot,'diffusion_models',wanCheckpoint);
 return {root,cfg,path};
}
test('absent GGUF, renamed FP8 file, wrong header and non-approved valid header all fail before spawning',async t=>{
 const {cfg,path,root}=await installFixture(t), n=0;
 let spawns=n;const wan=createWanI2V({binding:cfg,ports:{spawn(){spawns++;}}});
 for(const [content,reason] of [
  [null,'I2V_INT8_CHECKPOINT_MISSING'],
  [Buffer.from('fp8 e4m3fn safetensors fixture'), 'I2V_INT8_GGUF_HEADER_INVALID'],
  [gguf(1),'I2V_INT8_GGUF_HEADER_INVALID'],
  [gguf(),'I2V_INT8_CHECKPOINT_HASH_INVALID'],
 ]){
  if(content)await writeFile(path,content);
  await assert.rejects(wan.probe(),{message:reason});
  await assert.rejects(wan.render({dir:root,source:validPng(512,512),motionPrompt:'gentle flame',signal:signal()}),{message:reason});
 }
 assert.equal(spawns,0);assert.equal(wanCheckpointSha256.length,64);
});
test('owner-only binding and checkpoint cache bind exact digest, file identity, and GGUF type',async t=>{
 const {cfg,path,root}=await installFixture(t),bind=join(root,'binding.json'),data=gguf(),cache=new Map();
 await writeFile(bind,JSON.stringify(cfg),{mode:0o600});
 assert.deepEqual(await readWanI2VBinding(bind),cfg);
 await writeFile(path,data);
 await verifyI2VFile(path,sha(data),{gguf:true,cache});
 await assert.rejects(verifyI2VFile(path,'0'.repeat(64),{gguf:true,cache}),/CHECKPOINT_HASH_INVALID/);
 await writeFile(path,gguf(2));
 await assert.rejects(verifyI2VFile(path,sha(data),{gguf:true,cache}),/GGUF_HEADER_INVALID/);
});
test('process leader exit alone or EPERM does not confirm; owned absent or confirmed kill does',async()=>{
 const state={child:{pid:123},exit:true,error:false},opts=[poll,1];
 let live=true;const calls=[];
 const positive=(pid,sig)=>{assert.equal(pid,-123);calls.push(sig);if(!live)throw absent();if(sig==='SIGKILL')live=false;};
 assert.equal(await stopI2VGroup(state,positive,...opts),true);
 assert(calls.includes('SIGKILL'));
 assert.equal(await stopI2VGroup(state,()=>{throw denied();},...opts),false);
 assert.equal(await stopI2VGroup({...state,exit:false},()=>{throw absent();},...opts),false);
 assert.equal(await stopI2VGroup({child:{},error:true},positive,...opts),true);
});
function validPng(w,h){
 const chunk=(type,bytes)=>{
  const raw=Buffer.concat([Buffer.from(type),bytes]),len=Buffer.alloc(4),crc=Buffer.alloc(4);
  len.writeUInt32BE(bytes.length);crc.writeUInt32BE(crc32(raw));return Buffer.concat([len,raw,crc]);
 };
 const hbytes=Buffer.alloc(13);hbytes.writeUInt32BE(w);hbytes.writeUInt32BE(h,4);hbytes[8]=8;hbytes[9]=2;
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',hbytes),
  chunk('IDAT',deflateSync(Buffer.alloc((1+3*w)*h))),chunk('IEND',Buffer.alloc(0))]);
}
test('production adapter marshals one owned loopback prompt and joins group before returning 45 true RGB frames',async t=>{
 const {cfg,root}=await installFixture(t),bytes=validPng(512,512);
 let child,token,joined=false,history=0,prompts=0;
 const fakeNodes=['UnetLoaderGGUF','Wan22ImageToVideoLatent','CLIPLoader','VAELoader','LoadImage','CLIPTextEncode',
  'ModelSamplingSD3','KSampler','VAEDecode','SaveImage'];
 const wan=createWanI2V({binding:cfg,ports:{
  verify:async()=>{},pause:poll,
  spawn(bin,args,opts){
   assert.equal(bin,cfg.python);assert.equal(opts.detached,true);assert(args.includes('--disable-metadata'));
   assert(args.includes('ComfyUI-GGUF'));assert(!args.includes('--bf16-unet'));assert(args.includes('--offline'));
   token=opts.env.NGS_I2V_LOCAL_TOKEN;assert.equal(token.length,64);
   assert.equal(opts.env.HF_TOKEN,undefined);assert.equal(opts.env.PYTHONPATH,undefined);
   child=new EventEmitter();child.pid=456;child.stdout=new PassThrough();
   setImmediate(()=>child.stdout.write('NGS_OWNED_I2V_READY 33333\n'));
   return child;
  },
  async fetch(url,opt){
   assert(url.startsWith('http://127.0.0.1:33333/'));assert.equal(opt.redirect,'error');
   assert.equal(opt.headers.authorization,'Bearer '+token);
   const path=new URL(url).pathname;
   if(path==='/object_info')return Response.json(Object.fromEntries(fakeNodes.map(k=>[k,{}])));
   if(path==='/prompt'){
    prompts++;
    const p=JSON.parse(opt.body);
    assert.equal(p.prompt['1'].inputs.unet_name,wanCheckpoint);
    assert.equal(p.prompt['8'].inputs.start_image[0],'4');
    assert.deepEqual(await readFile(join(root,'i2v-comfy','input','source.png')),bytes);
    return Response.json({prompt_id:p.prompt_id});
   }
   history++;if(history===1)return Response.json({});
   const images=Array.from({length:WAN_I2V_LENGTH},(_,i)=>({
    filename:`${WAN_I2V_SAVE_PREFIX}_${String(i+1).padStart(5,'0')}_.png`,type:'output',subfolder:''
   }));
   await Promise.all(images.map(i=>writeFile(join(root,'i2v-comfy','output',i.filename),bytes)));
   return Response.json({[path.split('/').at(-1)]:{
    status:{status_str:'success',completed:true},outputs:{[WAN_I2V_SAVE_NODE]:{images}}}});
  },
  kill(pid,sig){assert.equal(pid,-456);if(joined)throw absent();if(sig==='SIGTERM'){joined=true;child.emit('exit',0);}},
 }});
 const result=await wan.render({dir:root,source:bytes,motionPrompt:'Gentle flame',signal:signal()});
 assert.equal(prompts,1);assert.equal(result.frames.length,45);assert.equal(result.fps,24);assert.equal(joined,true);
});
