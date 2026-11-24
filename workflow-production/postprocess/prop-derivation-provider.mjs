/** Provider for the exact prop job: same local pinned alpha environment on a private scratch. */
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {access,lstat,mkdir,mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {constants} from 'node:fs';
import {delimiter,isAbsolute,join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {tmpdir} from 'node:os';

const entry=fileURLToPath(new URL('./prop-derive.py',import.meta.url));
const keys=['FRAME_ALPHA_PYTHON','FRAME_ALPHA_MODULE_ROOT','FRAME_ALPHA_MATTING_MODEL','FRAME_ALPHA_TRACKING_MODEL'];
const budget=16*1024*1024;
const absolute=p=>typeof p==='string'&&isAbsolute(p)&&resolve(p)===p;
const assert=(ok,c)=>{if(!ok)throw Error(c);};
const propExit=Object.freeze({
 31:'PROP_DERIVATION_OUTPUT_BUDGET',32:'PROP_DERIVATION_ALPHA_REQUIRED',
 33:'PROP_DERIVATION_MATTE_BACKEND',34:'PROP_DERIVATION_INITIAL_MASK_EMPTY',
 35:'PROP_DERIVATION_TRACKER_FRAME_SET',36:'PROP_DERIVATION_INPUT_CHANGED',
 37:'PROP_DERIVATION_APNG_ENCODE',38:'PROP_DERIVATION_PIXEL_FOREGROUND',
 39:'PROP_DERIVATION_STATIC_NATIVE_ALPHA_REQUIRED',40:'PROP_DERIVATION_STATIC_SOURCE_PNG_INVALID',
});
const visible=new Set([...Object.values(propExit),'PROP_DERIVATION_RENDER_START','PROP_DERIVATION_RENDER_ABORTED',
 'PROP_DERIVATION_RENDER_TIMEOUT','PROP_DERIVATION_RENDER_OUTPUT']);
export const propRenderDiagnostic=e=>visible.has(e?.message)?e.message:null;

function execute(python,script,root,env,signal){
 return new Promise((resolve,reject)=>{
  signal?.throwIfAborted();
  const child=spawn(python,['-B',script,root],{env,stdio:'ignore'});
  let reason=null;
  function stop(code){
   if(reason===null){reason=code;child.kill('SIGKILL');}
  }
  function abort(){stop('PROP_DERIVATION_RENDER_ABORTED');}
  const timer=setTimeout(()=>stop('PROP_DERIVATION_RENDER_TIMEOUT'),30*60*1000);
  timer.unref();
  signal?.addEventListener('abort',abort,{once:true});
  if(signal?.aborted)abort();
  function close(){
   signal?.removeEventListener('abort',abort);clearTimeout(timer);
  }
  child.once('error',()=>{close();reject(Error('PROP_DERIVATION_RENDER_START'));});
  child.once('close',code=>{
   close();
   if(code===0&&reason===null)resolve();
   else reject(Error(reason??propExit[code]??'PROP_DERIVATION_RENDER_FAILED'));
  });
 });
}

// Callers must pass approved installed configuration at launch, not request metadata.
export async function createPropDerivationRenderer({configPath,modelPinsPath,workerSource,samSource}){
 assert([configPath,modelPinsPath,workerSource,samSource].every(absolute),'PROP_DERIVATION_MODEL_PATH');
 const cfg=JSON.parse(await readFile(configPath,'utf8'));
 const pins=await readFile(modelPinsPath);
 const entries=Object.values(JSON.parse(pins));
 assert(keys.every(key=>absolute(cfg[key]))&&entries.some(m=>m.path===cfg.FRAME_ALPHA_MATTING_MODEL)&&
    entries.some(m=>m.path===cfg.FRAME_ALPHA_TRACKING_MODEL||m.path===resolve(cfg.FRAME_ALPHA_TRACKING_MODEL,'..')),
    'PROP_DERIVATION_MODEL_PINS');
 await access(cfg.FRAME_ALPHA_PYTHON,constants.X_OK);
 for(const path of [workerSource,samSource,...keys.slice(1).map(k=>cfg[k])])await access(path,constants.R_OK);
 const env={HOME:process.env.HOME??'/tmp',PATH:'/usr/bin:/bin',
   PYTHONPATH:[workerSource,samSource,cfg.FRAME_ALPHA_MODULE_ROOT].join(delimiter),
   HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1',PYTHONDONTWRITEBYTECODE:'1',
   FRAME_ALPHA_MATTING_MODEL:cfg.FRAME_ALPHA_MATTING_MODEL,
   FRAME_ALPHA_TRACKING_MODEL:cfg.FRAME_ALPHA_TRACKING_MODEL,
   WORKFLOW_POSTPROCESS_MODEL_DIGEST:createHash('sha256').update(pins).digest('hex')};
 return async (job,binding,{signal}={})=>{
  const root=await mkdtemp(join(tmpdir(),'workflow-prop-derivation-'));
  try{
   return await renderPropInScratch(job,binding,{root,signal,execute:(workspace,s)=>execute(cfg.FRAME_ALPHA_PYTHON,entry,workspace,env,s)});
  }finally{await rm(root,{recursive:true,force:true});}
 };
}

// The fixture can replace only the external renderer boundary. It does not
// bypass claim, source, individual file, manifest, or output checks.
export async function renderPropInScratch(job,binding,{root,signal,execute}){
 const {source,canvas,buffers,frames,media,frameDurationsMs}=job;
 const spec={kind:binding.role,canvas,frameDurationsMs,sourceHash:source.resultRef.slice(-64)};
 if(binding.role==='pixel'){
  spec.media=media;
  await writeFile(join(root,'source.png'),buffers[0],{mode:0o600,flag:'wx'});
 }else{
  spec.frames=frames??[{...media,index:0}];
  await mkdir(join(root,'raw'),{mode:0o700});
  for(const [i,buffer] of buffers.entries()){
   signal?.throwIfAborted();
   await writeFile(join(root,'raw','frame-'+String(i).padStart(8,'0')+'.png'),buffer,{mode:0o600,flag:'wx'});
  }
 }
 await writeFile(join(root,'job.json'),JSON.stringify(spec),{mode:0o600,flag:'wx'});
 await execute(root,signal);
 const result=join(root,'result.png'),stat=await lstat(result);
 assert(stat.isFile()&&!stat.isSymbolicLink()&&stat.nlink===1&&stat.size>=57&&stat.size<=budget,
      'PROP_DERIVATION_RENDER_OUTPUT');
 return readFile(result);
}
