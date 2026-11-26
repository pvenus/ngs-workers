import {createHash,randomBytes,randomUUID} from 'node:crypto';
import {spawn} from 'node:child_process';
import {createReadStream,constants} from 'node:fs';
import {access,lstat,open,realpath,mkdir,symlink,writeFile,readFile,readdir} from 'node:fs/promises';
import {basename,dirname,isAbsolute,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as sleep} from 'node:timers/promises';
import {buildWanI2VGraph,WAN_I2V_MODEL_ID,WAN_I2V_QUANTIZATION,WAN_I2V_HIGH,WAN_I2V_LOW,
 WAN_I2V_HIGH_SHA256,WAN_I2V_LOW_SHA256,WAN_I2V_ENCODER,WAN_I2V_VAE,WAN_I2V_VAE_SHA256,
 WAN_I2V_INPUT_IMAGE,WAN_I2V_SAVE_NODE,WAN_I2V_SAVE_PREFIX,WAN_I2V_FPS,WAN_I2V_LENGTH} from './i2v-wan-graph.mjs';
import {png,maxBundleBytes,maxFrameBytes,maxImageBytes} from './i2v-worker.mjs';

export const wanI2VBindingSchema='ngs.animation-i2v-a14b-q4km-binding.v1';
const bindingKeys='comfyRoot,ggufNodesRoot,modelsRoot,python,schemaVersion';
export const wanI2VAssetPins=Object.freeze([
 Object.freeze(['diffusion_models',WAN_I2V_HIGH,WAN_I2V_HIGH_SHA256,'I2V_A14B_HIGH_EXPERT_MISSING']),
 Object.freeze(['diffusion_models',WAN_I2V_LOW,WAN_I2V_LOW_SHA256,'I2V_A14B_LOW_EXPERT_MISSING']),
 Object.freeze(['text_encoders',WAN_I2V_ENCODER,'7b8850f1961e1cf8a77cca4c964a358d303f490833c6c087d0cff4b2f99db2af','I2V_A14B_ENCODER_MISSING']),
 Object.freeze(['vae',WAN_I2V_VAE,WAN_I2V_VAE_SHA256,'I2V_A14B_VAE_MISSING']),
]);
const boot=fileURLToPath(new URL('./i2v-comfy-bootstrap.py',import.meta.url));
const nodeNames=['UnetLoaderGGUF','WanImageToVideo','CLIPLoader','VAELoader','LoadImage','CLIPTextEncode','ModelSamplingSD3','KSamplerAdvanced','VAEDecode','SaveImage'];
const error=(code,rendererJoined=true)=>Object.assign(Error(code),{rendererJoined});
const need=(cond,code)=>{if(!cond)throw error(code);};

async function regular(p){
 const s=await lstat(p,{bigint:true});
 need(s.isFile()&&(s.mode&0o022n)===0n&&s.uid===BigInt(process.getuid())&&s.size>0n,'I2V_A14B_FILE_INVALID');
 return s;
}
export async function readWanI2VBinding(p){
 need(typeof p==='string'&&isAbsolute(p)&&await realpath(p)===p,'I2V_A14B_BINDING_INVALID');
 const fd=await open(p,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
 try{
  const stat=await fd.stat();
  need(stat.isFile()&&stat.uid===process.getuid()&&(stat.mode&0o077)===0&&stat.size>=1&&stat.size<=4096,'I2V_A14B_BINDING_INVALID');
  const b=Buffer.alloc(4097),read=await fd.read(b,0,4097,0);
  need(read.bytesRead===stat.size,'I2V_A14B_BINDING_INVALID');
  const v=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(b.subarray(0,read.bytesRead)));
  need(v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join(',')===bindingKeys&&v.schemaVersion===wanI2VBindingSchema,
   'I2V_A14B_BINDING_INVALID');
  for(const k of ['comfyRoot','ggufNodesRoot','modelsRoot','python'])
   need(typeof v[k]==='string'&&isAbsolute(v[k])&&await realpath(dirname(v[k]))===dirname(v[k]),'I2V_A14B_BINDING_INVALID');
  return Object.freeze(v);
 } finally{await fd.close();}
}

export async function requireGgufHeader(fd){
 const buf=Buffer.alloc(24);
 need((await fd.read(buf,0,buf.length,0)).bytesRead===24&&buf.toString('ascii',0,4)==='GGUF'&&
  buf.readUInt32LE(4)===3&&buf.readBigUInt64LE(8)>=1n&&buf.readBigUInt64LE(8)<=100000n&&
  buf.readBigUInt64LE(16)>=1n&&buf.readBigUInt64LE(16)<=10000n,'I2V_A14B_GGUF_HEADER_INVALID');
}
export async function verifyI2VFile(path,digest,{gguf=false,cache=new Map()}={}){
 const first=await regular(path);
 const stamp=s=>[s.dev,s.ino,s.size,s.ctimeNs,s.mtimeNs].join(':');
 const token=stamp(first);
 if(cache.get(path)===digest+':'+gguf+':'+token)return;
 const fd=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW|constants.O_NONBLOCK);
 try{
  need(stamp(await fd.stat({bigint:true}))===token,'I2V_A14B_FILE_CHANGED');
  if(gguf)await requireGgufHeader(fd);
  const hash=createHash('sha256');
  for await(const part of createReadStream(path,{fd:fd.fd,autoClose:false,start:0,highWaterMark:2**20}))hash.update(part);
  need(hash.digest('hex')===digest,'I2V_A14B_CHECKPOINT_HASH_INVALID');
  need(stamp(await fd.stat({bigint:true}))===token&&stamp(await regular(path))===token,'I2V_A14B_FILE_CHANGED');
  cache.set(path,digest+':'+gguf+':'+token);
 }finally{await fd.close();}
}
async function canonicalDirectory(p,code){
 try{
  need(typeof p==='string'&&isAbsolute(p)&&await realpath(p)===p,code);
  const s=await lstat(p);
  need(s.isDirectory()&&s.uid===process.getuid()&&(s.mode&0o022)===0,code);
 }catch{throw error(code);}
}
export async function verifyWanI2V(binding,cache){
 need(binding&&Object.keys(binding).sort().join(',')===bindingKeys&&binding.schemaVersion===wanI2VBindingSchema,'I2V_A14B_BINDING_INVALID');
 await canonicalDirectory(binding.comfyRoot,'I2V_COMFY_RUNTIME_MISSING');
 await canonicalDirectory(binding.modelsRoot,'I2V_A14B_MODELS_ROOT_MISSING');
 await canonicalDirectory(binding.ggufNodesRoot,'I2V_GGUF_LOADER_MISSING');
 try{await access(binding.python,constants.X_OK);await regular(join(binding.comfyRoot,'main.py'));}
 catch{throw error('I2V_COMFY_RUNTIME_MISSING');}
 for(const file of ['__init__.py','nodes.py','loader.py','ops.py','dequant.py']){
  try{await regular(join(binding.ggufNodesRoot,file));}
  catch{throw error('I2V_GGUF_LOADER_MISSING');}
 }
 try{
  // The explicit isolated virtualenv is the only Python dependency location.
  const lib=join(dirname(dirname(binding.python)),'lib');
  const modes=(await readdir(lib,{withFileTypes:true})).filter(i=>i.isDirectory()&&/^python3[.]\d+$/.test(i.name));
  need(modes.length===1,'I2V_GGUF_DEPENDENCY_MISSING');
  await regular(join(lib,modes[0].name,'site-packages','gguf','__init__.py'));
 }catch{throw error('I2V_GGUF_DEPENDENCY_MISSING');}
 // Detect missing/invalid assets before spending time hashing any large model.
 for(const folder of new Set(wanI2VAssetPins.map(([name])=>name)))
  await canonicalDirectory(join(binding.modelsRoot,folder),'I2V_A14B_MODEL_DIRECTORY_INVALID');
 for(const [folder,file,,missing] of wanI2VAssetPins){
  try{await regular(join(binding.modelsRoot,folder,file));}
  catch(e){if(e.code==='ENOENT')throw error(missing);throw e;}
 }
 for(const [folder,file,digest,missing] of wanI2VAssetPins){
  try{await verifyI2VFile(join(binding.modelsRoot,folder,file),digest,{gguf:folder==='diffusion_models',cache});}
  catch(e){if(e.code==='ENOENT')throw error(missing);throw e;}
 }
}

export function launch(py,args,opts,start){
 let child;
 try{child=start(py,args,{...opts,detached:true,stdio:['ignore','pipe','ignore']});}
 catch{throw error('I2V_COMFY_SPAWN_FAILED');}
 const state={child,exit:false,error:false};
 child.once('exit',()=>{state.exit=true;});
 child.once('error',()=>{state.error=true;});
 return state;
}
function groupSignal(state,sig,send){
 try{send(-state.child.pid,sig);return 'live';}
 catch(e){return e?.code==='ESRCH'?'gone':'unknown';}
}
export async function stopI2VGroup(state,send=process.kill,pause=sleep,steps=60){
 if(!state)return true;
 if(!Number.isInteger(state.child.pid)||state.child.pid<1)return state.error;
 const gone=()=>state.exit&&groupSignal(state,0,send)==='gone';
 if(gone())return true;
 for(const sig of ['SIGTERM','SIGKILL']){
  groupSignal(state,sig,send);
  for(let n=0;n<=steps;n++){if(gone())return true;if(n<steps)await pause(100);}
 }
 return false;
}
export async function waitReady(state,signal,pause){
 let tail='',port=0;
 const onData=b=>{
  const lines=(tail+b.toString('utf8')).split('\n');tail=lines.pop().slice(-200);
  for(const line of lines){
   const m=/^NGS_OWNED_I2V_READY ([0-9]{1,5})$/.exec(line);
   if(m&&Number(m[1])>0&&Number(m[1])<65536)port=Number(m[1]);
  }
 };
 state.child.stdout.on('data',onData);
 try{
  for(let i=0;i<600;i++){
   signal.throwIfAborted();need(!state.error&&!state.exit,'I2V_COMFY_START_FAILED');
   if(port)return port;
   await pause(100,undefined,{signal});
  }
  throw error('I2V_COMFY_START_TIMEOUT');
 }finally{state.child.stdout.off('data',onData);state.child.stdout.resume();}
}
export async function request(http,base,token,route,signal,body){
 const r=await http(base+route,{method:body?'POST':'GET',redirect:'error',
  headers:{authorization:'Bearer '+token,...(body?{'content-type':'application/json'}:{})},
  ...(body?{body:JSON.stringify(body)}:{}),signal:AbortSignal.any([signal,AbortSignal.timeout(30_000)])});
 if(!r.ok)await r.body?.cancel().catch(()=>{});
 need(r.ok&&r.body,'I2V_COMFY_REQUEST_FAILED');
 const parts=[];let size=0;
 for await(const b of r.body){size+=b.length;need(size<=4194304,'I2V_COMFY_RESPONSE_LIMIT');parts.push(b);}
 return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(parts)));
}
const matchedFrame=(i,f)=>f&&f.filename===`${WAN_I2V_SAVE_PREFIX}_${String(i+1).padStart(5,'0')}_.png`&&f.type==='output'&&f.subfolder==='';
export const i2vWanFrames=WAN_I2V_LENGTH,i2vWanFps=WAN_I2V_FPS;
function resolution(raw){
 const longest=Math.max(raw.width,raw.height);
 const fit=n=>Math.max(128,Math.min(512,Math.round(n/longest*16)*32));
 return {width:fit(raw.width),height:fit(raw.height)};
}
export function createWanI2V({binding,ports={}}){
 const pause=ports.pause??sleep,start=ports.spawn??spawn,send=ports.kill??process.kill,http=ports.fetch??fetch;
 const verify=ports.verify??verifyWanI2V,cache=new Map();
 let busy=false,unsafe=false;
 return {
  async probe(){
   await verify(binding,cache); // Local file/header/digest verification only, no inference.
   return {ready:true,model:WAN_I2V_MODEL_ID,quantization:WAN_I2V_QUANTIZATION,fps:i2vWanFps,frames:i2vWanFrames};
  },
  async render({dir,source,motionPrompt,signal}){
   if(busy||unsafe)throw error('I2V_MODEL_OWNERSHIP_UNKNOWN',false);
   busy=true;
   let state;
   try{
    const timeout=AbortSignal.any([signal,AbortSignal.timeout(30*60_000)]);
    timeout.throwIfAborted();
    const shape=resolution(png(source,maxImageBytes));
    await verify(binding,cache);
    const scratch=await realpath(dir);
    const engine=join(scratch,'i2v-comfy'),models=join(engine,'models'),custom=join(engine,'custom_nodes'),input=join(engine,'input'),out=join(engine,'output');
    for(const p of [engine,models,custom,input,out])await mkdir(p,{mode:0o700});
    for(const folder of new Set(wanI2VAssetPins.map(([name])=>name)))await mkdir(join(models,folder),{mode:0o700});
    for(const [folder,file] of wanI2VAssetPins){
     await symlink(join(binding.modelsRoot,folder,file),join(models,folder,file));
    }
    await symlink(binding.ggufNodesRoot,join(custom,'ComfyUI-GGUF'));
    await writeFile(join(input,WAN_I2V_INPUT_IMAGE),source,{mode:0o600,flag:'wx'});
    const token=randomBytes(32).toString('hex'), prompt=buildWanI2VGraph({...shape,motionPrompt,seed:417});
    timeout.throwIfAborted();
    // -c sets sys.path[0] to cwd, the pinned Comfy root; user source is file bytes only.
    state=launch(binding.python,['-B','-c','import runpy,sys;p=sys.argv.pop(1);runpy.run_path(p,run_name="__main__")',boot,
     '--base-directory',engine,'--listen','127.0.0.1','--port','0','--offline','--disable-all-custom-nodes',
     '--whitelist-custom-nodes','ComfyUI-GGUF','--cache-none','--disable-metadata','--preview-method','none','--use-pytorch-cross-attention'],
     {cwd:binding.comfyRoot,env:{PATH:'/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin',HOME:engine,TMPDIR:engine,
      PYTHONNOUSERSITE:'1',PYTHONDONTWRITEBYTECODE:'1',HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1',DO_NOT_TRACK:'1',
      NGS_I2V_LOCAL_TOKEN:token}},start);
    const base='http://127.0.0.1:'+await waitReady(state,timeout,pause);
    const read=(route,body)=>request(http,base,token,route,timeout,body);
    const info=await read('/object_info');
    need(nodeNames.every(name=>Object.hasOwn(info,name)),'I2V_GGUF_GRAPH_UNSUPPORTED');
    const id=randomUUID(), receipt=await read('/prompt',{prompt,prompt_id:id,client_id:id});
    need(receipt?.prompt_id===id,'I2V_COMFY_PROMPT_BINDING');
    let frames;
    while(!frames){
     timeout.throwIfAborted();need(!state.exit&&!state.error,'I2V_COMFY_PROCESS_LOST');
     const history=await read('/history/'+id);
     const job=history?.[id];
     if(job){
      need(job.status?.status_str==='success'&&job.status.completed===true,'I2V_COMFY_RENDER_FAILED');
      frames=job.outputs?.[WAN_I2V_SAVE_NODE]?.images;
      need(Array.isArray(frames)&&frames.length===i2vWanFrames&&frames.every((f,i)=>matchedFrame(i,f)),'I2V_COMFY_FRAME_SET');
     }else await pause(1000,undefined,{signal:timeout});
    }
    if(!await stopI2VGroup(state,send,pause)){
     unsafe=true;throw error('I2V_COMFY_STOP_UNCONFIRMED',false);
    }
    state=undefined;
    let total=0;
    const output=[];
    for(const frame of frames){
     const p=join(out,basename(frame.filename));
     const stat=await regular(p);
     need(stat.size<=BigInt(maxFrameBytes)&&total+Number(stat.size)<=maxBundleBytes,'I2V_COMFY_FRAME_SIZE');
     const b=await readFile(p);
     const {width,height}=png(b,maxFrameBytes,{rgb:true});
     need(width===shape.width&&height===shape.height,'I2V_COMFY_FRAME_SHAPE');
     output.push(b);total+=b.length;
    }
    return {frames:output,fps:i2vWanFps,...shape};
   }catch(e){
    if(!unsafe&&!await stopI2VGroup(state,send,pause))unsafe=true;
    if(unsafe)throw error('I2V_COMFY_STOP_UNCONFIRMED',false);
    if(e.rendererJoined===true)throw e;
    throw error(signal.aborted?'I2V_COMFY_CANCELLED':'I2V_COMFY_UNCONFIRMED');
   }finally{busy=false;}
  }
 };
}
