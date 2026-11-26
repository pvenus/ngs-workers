import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {PassThrough} from 'node:stream';
import {mkdtemp,realpath,readFile,writeFile,readlink,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {crc32,deflateSync} from 'node:zlib';
import {buildBackgroundSeedVR2Graph,createBackgroundSeedVR2} from './background-seedvr2.mjs';
import {createAnimationModelSwitch} from './i2v-model-switch.mjs';

function validPng(w,h){
 const chunk=(type,bytes)=>{
  const raw=Buffer.concat([Buffer.from(type),bytes]),len=Buffer.alloc(4),crc=Buffer.alloc(4);
  len.writeUInt32BE(bytes.length);crc.writeUInt32BE(crc32(raw));return Buffer.concat([len,raw,crc]);
 };
 const hbytes=Buffer.alloc(13);hbytes.writeUInt32BE(w);hbytes.writeUInt32BE(h,4);hbytes[8]=8;hbytes[9]=2;
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',hbytes),
  chunk('IDAT',deflateSync(Buffer.alloc((1+3*w)*h))),chunk('IEND',Buffer.alloc(0))]);
}
test('native upscale graph batches in exact order, pins SeedVR2 FP16, and preserves the full 45-frame temporal pass',()=>{
 const {prompt,saveId}=buildBackgroundSeedVR2Graph();
 const byKind=name=>Object.entries(prompt).filter(([,n])=>n.class_type===name);
 const [first]=byKind('ImageScaleBy');
 const collect=([id])=>prompt[id].class_type==='LoadImage'?[prompt[id].inputs.image]:
  [...collect(prompt[id].inputs.image1),...collect(prompt[id].inputs.image2)];
 assert.deepEqual(collect(first[1].inputs.image),Array.from({length:45},(_,i)=>'frame_'+String(i).padStart(3,'0')+'.png'));
 assert.equal(first[1].inputs.scale_by,2);
 assert.equal(byKind('UNETLoader')[0][1].inputs.unet_name,'seedvr2_7b_fp16.safetensors');
 assert.equal(byKind('VAELoader')[0][1].inputs.vae_name,'ema_vae_fp16.safetensors');
 assert.equal(byKind('SeedVR2PostProcessing')[0][1].inputs.color_correction_method,'lab');
 assert.equal(prompt[saveId].class_type,'SaveImage');
});
for(const mode of ['ok','cpu','wrong_size','missing_frame','unconfirmed_stop'])
test('owned upscale on isolated fixture: '+mode,async t=>{
 const dir=await realpath(await mkdtemp(join(tmpdir(),'bg-seedvr2-test-')));
 t.after(()=>rm(dir,{force:true,recursive:true}));
 const binding={python:'/test/bin/python',comfyRoot:'/test/comfy',modelsRoot:'/test/models'};
 const path=join(dir,'background-seedvr2'),source=validPng(128,128),hi=validPng(256,256);
 const {prompt,saveId}=buildBackgroundSeedVR2Graph();
 let child,token,joined=false,prompts=0;
 const engine=createBackgroundSeedVR2({binding,ports:{
  verify:async()=>{},pause:()=>new Promise(setImmediate),
  spawn(bin,args,opts){
   assert.equal(bin,binding.python);
   assert(args.includes('--offline'));assert(args.includes('--fp16-unet'));assert(args.includes('--disable-all-custom-nodes'));
   assert.equal(opts.detached,true);assert.equal(opts.cwd,binding.comfyRoot);token=opts.env.NGS_I2V_LOCAL_TOKEN;
   child=new EventEmitter();child.pid=456;child.stdout=new PassThrough();
   setImmediate(()=>child.stdout.write('NGS_OWNED_I2V_READY 33333\n'));
   return child;
  },
  kill(pid,sig){
   assert.equal(pid,-456);
   if(mode==='unconfirmed_stop')return;
   if(joined)throw Object.assign(Error(),{code:'ESRCH'});
   if(sig==='SIGTERM'){joined=true;child.emit('exit',0);}
  },
  async fetch(url,options){
   assert.equal(new URL(url).origin,'http://127.0.0.1:33333');
   assert.equal(options.headers.authorization,'Bearer '+token);
   const r=new URL(url).pathname;
   if(r==='/system_stats')return Response.json({devices:[{type:mode==='cpu'?'cpu':'mps'}]});
   if(r==='/object_info')return Response.json(Object.fromEntries(Object.values(prompt).map(n=>[n.class_type,{}])));
   if(r==='/prompt'){
    const body=JSON.parse(options.body);assert.deepEqual(body.prompt,prompt);
    assert.deepEqual(await readFile(join(path,'input','frame_000.png')),source);
    assert.equal(await readlink(join(path,'models','diffusion_models','seedvr2_7b_fp16.safetensors')),join(binding.modelsRoot,'seedvr2_7b_fp16.safetensors'));
    prompts++;return Response.json({prompt_id:body.prompt_id});
   }
   const images=Array.from({length:mode==='missing_frame'?44:45},(_,i)=>({
    filename:'background-seedvr2_'+String(i+1).padStart(5,'0')+'_.png',type:'output',subfolder:''
   }));
   await Promise.all(images.map(f=>writeFile(join(path,'output',f.filename),mode==='wrong_size'?source:hi)));
   return Response.json({[r.split('/').at(-1)]:{status:{status_str:'success',completed:true},outputs:{[saveId]:{images}}}});
  },
 }});
 const input={dir,output:{width:128,height:128,fps:24,frames:Array(45).fill(source)},signal:new AbortController().signal};
 if(mode==='ok'){
  const out=await engine.render(input);
  assert.equal(joined,true);assert.equal(prompts,1);assert.equal(out.width,256);assert.equal(out.height,256);assert.deepEqual(out.frames,Array(45).fill(hi));
 }else{
  const reason={cpu:'MPS_UNAVAILABLE',wrong_size:'OUTPUT_SHAPE',missing_frame:'OUTPUT_FRAME_SET',unconfirmed_stop:'STOP_UNCONFIRMED'}[mode];
  await assert.rejects(engine.render(input),e=>e.message==='BACKGROUND_SEEDVR2_'+reason&&e.rendererJoined===(mode!=='unconfirmed_stop'));
  assert.equal(joined,mode!=='unconfirmed_stop');
  assert.equal(prompts,mode==='cpu'?0:1);
 }
});

test('Wan, I2V, and the postprocessor share one exclusive ownership slot',async()=>{
 let finish,called=false;
 const slot=createAnimationModelSwitch({shutdown:async()=>true},{render:async()=>({})},{
  render:()=>{called=true;return new Promise(r=>{finish=r;});},
 });
 const waiter=slot.runUpscale({});
 await new Promise(setImmediate);
 assert.equal(called,true);assert.throws(()=>slot.enterWan(),/SLOT_BUSY/);
 await assert.rejects(slot.runI2V({}),/SLOT_BUSY/);
 finish('ok');assert.equal(await waiter,'ok');
 slot.enterWan();
});
