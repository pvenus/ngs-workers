/** Offline SeedVR2 7B FP16 video postprocess. This owns exactly one Comfy process group. */
import {randomBytes,randomUUID} from 'node:crypto';
import {spawn} from 'node:child_process';
import {constants} from 'node:fs';
import {access,lstat,readFile,mkdir,symlink,realpath,writeFile} from 'node:fs/promises';
import {isAbsolute,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as sleep} from 'node:timers/promises';
import {launch,request,stopI2VGroup,verifyI2VFile,waitReady} from './i2v-wan.mjs';
import {png,maxFrameBytes} from './i2v-worker.mjs';
import {WAN_I2V_FPS,WAN_I2V_LENGTH} from './i2v-wan-graph.mjs';

export const backgroundUpscaleModel='seedvr2-7b-fp16',backgroundUpscaleScale=2,backgroundUpscaleMaxBytes=128*1024*1024;
const dit='seedvr2_7b_fp16.safetensors',vae='ema_vae_fp16.safetensors';
const modelPins=[
 ['diffusion_models',dit,'2742ca6fee63bc5cc1773f426dd4b07b78cad27f51c9ea5cd42b035e6b592252'],
 ['vae',vae,'20678548f420d98d26f11442d3528f8b8c94e57ee046ef93dbb7633da8612ca1'],
];
const nativeNodeSha='70c27b735456a9b3140cb9ca315c13716abe7f2158a68ced4fb338c91fa991aa';
const prefix='background-seedvr2';
const inputFile=i=>'frame_'+String(i).padStart(3,'0')+'.png';
const outputFile=i=>prefix+'_'+String(i+1).padStart(5,'0')+'_.png';
const boot=fileURLToPath(new URL('./i2v-comfy-bootstrap.py',import.meta.url));
const failure=(code,rendererJoined=true)=>Object.assign(Error(code),{rendererJoined});
const need=(ok,code)=>{if(!ok)throw failure(code);};
const grid=n=>Number.isSafeInteger(n)&&128<=n&&n<=512&&n%32===0;

export function buildBackgroundSeedVR2Graph(frameCount=WAN_I2V_LENGTH){
 need([8,WAN_I2V_LENGTH].includes(frameCount),'BACKGROUND_SEEDVR2_INPUT_SHAPE');
 const prompt={};
 const node=(class_type,inputs)=>{
  const id=String(Object.keys(prompt).length+1);prompt[id]={class_type,inputs};return [id,0];
 };
 let frames=Array.from({length:frameCount},(_,i)=>node('LoadImage',{image:inputFile(i)}));
 while(frames.length>1){
  const batches=[];
  for(let i=0;i<frames.length;i+=2)batches.push(i+1<frames.length?node('ImageBatch',{image1:frames[i],image2:frames[i+1]}):frames[i]);
  frames=batches;
 }
 const scaled=node('ImageScaleBy',{image:frames[0],upscale_method:'lanczos',scale_by:backgroundUpscaleScale});
 const padded=node('SeedVR2Preprocess',{resized_images:scaled});
 const vaeRef=node('VAELoader',{vae_name:vae});
 const model=node('UNETLoader',{unet_name:dit,weight_dtype:'default'});
 const tiling={tile_size:512,overlap:128,temporal_size:32,temporal_overlap:8};
 const encoded=node('VAEEncodeTiled',{pixels:padded,vae:vaeRef,...tiling});
 const condition=node('SeedVR2Conditioning',{model,vae_conditioning:encoded});
 const refined=node('KSampler',{model,seed:417,steps:1,cfg:1,sampler_name:'euler',scheduler:'simple',
  positive:condition,negative:[condition[0],1],latent_image:encoded,denoise:1});
 const decoded=node('VAEDecodeTiled',{samples:refined,vae:vaeRef,...tiling});
 const corrected=node('SeedVR2PostProcessing',{images:decoded,original_resized_images:scaled,color_correction_method:'lab'});
 const [saveId]=node('SaveImage',{images:corrected,filename_prefix:prefix});
 return {prompt,saveId};
}

async function verify({comfyRoot,python,modelsRoot},cache){
 for(const folder of [comfyRoot,modelsRoot]){
  need(typeof folder==='string'&&isAbsolute(folder)&&await realpath(folder)===folder,'BACKGROUND_SEEDVR2_CONFIG_INVALID');
  const stat=await lstat(folder);
  need(stat.isDirectory()&&stat.uid===process.getuid()&&(stat.mode&0o022)===0,'BACKGROUND_SEEDVR2_CONFIG_INVALID');
 }
 need(typeof python==='string'&&isAbsolute(python),'BACKGROUND_SEEDVR2_CONFIG_INVALID');
 await access(python,constants.X_OK);
 for(const [,name,sha] of modelPins)await verifyI2VFile(join(modelsRoot,name),sha,{cache});
 await verifyI2VFile(join(comfyRoot,'comfy_extras/nodes_seedvr.py'),nativeNodeSha,{cache});
}
export function createSeedVR2Upscaler({binding,ports={}}){
 const http=ports.fetch??fetch,pause=ports.pause??sleep,start=ports.spawn??spawn,send=ports.kill??process.kill;
 const inspect=ports.verify??verify,cache=new Map();
 let busy=false,unsafe=false;
 return {
  async probe(){
   if(unsafe)throw failure('BACKGROUND_SEEDVR2_STOP_UNCONFIRMED',false);
   try{await inspect(binding,cache);}
   catch{throw failure('BACKGROUND_SEEDVR2_UNAVAILABLE');}
  },
  async render({dir,output,signal}){
   if(busy||unsafe)throw failure('BACKGROUND_SEEDVR2_SLOT_UNAVAILABLE',false);
   busy=true;
   let state;
   try{
    const timeout=AbortSignal.any([signal,AbortSignal.timeout(30*60_000)]);
    timeout.throwIfAborted();
    need(output?.fps===WAN_I2V_FPS&&grid(output.width)&&grid(output.height)&&Array.isArray(output.frames)&&
      [8,WAN_I2V_LENGTH].includes(output.frames.length),'BACKGROUND_SEEDVR2_INPUT_SHAPE');
    await inspect(binding,cache);
    const engine=join(await realpath(dir),'background-seedvr2'),models=join(engine,'models');
    const input=join(engine,'input'),out=join(engine,'output');
    for(const p of [engine,models,input,out])await mkdir(p,{mode:0o700});
    for(const [scope,name] of modelPins){
     await mkdir(join(models,scope),{mode:0o700});
     await symlink(join(binding.modelsRoot,name),join(models,scope,name));
    }
    for(const [i,bytes] of output.frames.entries()){
     const shape=png(bytes,maxFrameBytes,{rgb:true});
     need(shape.width===output.width&&shape.height===output.height,'BACKGROUND_SEEDVR2_INPUT_SHAPE');
     await writeFile(join(input,inputFile(i)),bytes,{mode:0o600,flag:'wx'});
    }
    const token=randomBytes(32).toString('hex');
    const frameCount=output.frames.length,{prompt,saveId}=buildBackgroundSeedVR2Graph(frameCount);
    timeout.throwIfAborted();
    state=launch(binding.python,['-B','-c','import runpy,sys;p=sys.argv.pop(1);runpy.run_path(p,run_name="__main__")',boot,
     '--base-directory',engine,'--listen','127.0.0.1','--port','0','--offline','--disable-all-custom-nodes','--disable-auto-launch',
     '--cache-none','--disable-metadata','--preview-method','none','--use-pytorch-cross-attention','--fp16-unet'],
     {cwd:binding.comfyRoot,env:{PATH:'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',HOME:engine,TMPDIR:engine,
      PYTHONNOUSERSITE:'1',PYTHONDONTWRITEBYTECODE:'1',HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1',DO_NOT_TRACK:'1',
      NGS_I2V_LOCAL_TOKEN:token}},start);
    const base='http://127.0.0.1:'+await waitReady(state,timeout,pause);
    const call=(route,body)=>request(http,base,token,route,timeout,body);
    // Fail closed rather than silently running an unexpected backend or node graph.
    const stats=await call('/system_stats');
    need(stats?.devices?.[0]?.type==='mps','BACKGROUND_SEEDVR2_MPS_UNAVAILABLE');
    const info=await call('/object_info');
    need(info&&Object.values(prompt).every(n=>Object.hasOwn(info,n.class_type)),'BACKGROUND_SEEDVR2_GRAPH_UNSUPPORTED');
    const id=randomUUID(),receipt=await call('/prompt',{prompt,prompt_id:id,client_id:id});
    need(receipt?.prompt_id===id,'BACKGROUND_SEEDVR2_PROMPT_BINDING');
    while(true){
     timeout.throwIfAborted();need(!state.exit&&!state.error,'BACKGROUND_SEEDVR2_ENGINE_LOST');
     const history=await call('/history/'+id),job=history?.[id];
     if(!job){await pause(1000,undefined,{signal:timeout});continue;}
     need(job.status?.status_str==='success'&&job.status.completed===true,'BACKGROUND_SEEDVR2_RENDER_FAILED');
     const frames=job.outputs?.[saveId]?.images;
     need(Array.isArray(frames)&&frames.length===frameCount&&frames.every((f,i)=>
      f?.filename===outputFile(i)&&f.type==='output'&&f.subfolder===''),'BACKGROUND_SEEDVR2_OUTPUT_FRAME_SET');
     break;
    }
    if(!await stopI2VGroup(state,send,pause)){unsafe=true;throw failure('BACKGROUND_SEEDVR2_STOP_UNCONFIRMED',false);}
    state=undefined;
    let bytes=0;
    const frames=[],width=output.width*backgroundUpscaleScale,height=output.height*backgroundUpscaleScale;
    for(let i=0;i<frameCount;i++){
     timeout.throwIfAborted();
     const path=join(out,outputFile(i)),stat=await lstat(path);
     need(stat.isFile()&&!stat.isSymbolicLink()&&stat.size<=maxFrameBytes&&bytes+stat.size<=backgroundUpscaleMaxBytes,'BACKGROUND_SEEDVR2_OUTPUT_BYTES');
     const b=await readFile(path),shape=png(b,maxFrameBytes,{rgb:true});
     need(b.length===stat.size&&shape.width===width&&shape.height===height,'BACKGROUND_SEEDVR2_OUTPUT_SHAPE');
     frames.push(b);bytes+=b.length;
    }
    return {frames,fps:WAN_I2V_FPS,width,height};
   }catch(e){
    if(!unsafe&&!await stopI2VGroup(state,send,pause))unsafe=true;
    if(unsafe)throw failure('BACKGROUND_SEEDVR2_STOP_UNCONFIRMED',false);
    if(typeof e?.message==='string'&&e.message.startsWith('BACKGROUND_SEEDVR2_'))throw e;
    throw failure(signal.aborted?'BACKGROUND_SEEDVR2_CANCELLED':'BACKGROUND_SEEDVR2_UNCONFIRMED');
   }finally{busy=false;}
  },
 };
}
