import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {constants as fs} from 'node:fs';
import {access,lstat,mkdir,mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {delimiter,isAbsolute,join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';

const script=fileURLToPath(new URL('./postprocess-render.py',import.meta.url));
const staticScript=fileURLToPath(new URL('./postprocess-pixel.py',import.meta.url));
const fail=code=>{throw Error(code);};
const sha=b=>createHash('sha256').update(b).digest('hex');
const required=['FRAME_ALPHA_PYTHON','FRAME_ALPHA_MODULE_ROOT','FRAME_ALPHA_MATTING_MODEL','FRAME_ALPHA_TRACKING_MODEL'];
export async function createPostprocessRenderer({configPath,modelPinsPath,workerSource,samSource,pds}){
 const paths=[configPath,modelPinsPath,workerSource,samSource];
 if(!paths.every(p=>typeof p==='string'&&isAbsolute(p)&&resolve(p)===p))fail('POSTPROCESS_CONFIG_PATH_REQUIRED');
 const config=JSON.parse(await readFile(configPath,'utf8'));
 const pins=await readFile(modelPinsPath),modelDigest=sha(pins);
 const parsed=JSON.parse(pins);
 const lockedModels=Object.values(parsed);
 if(!required.every(k=>typeof config[k]==='string'&&isAbsolute(config[k])&&resolve(config[k])===config[k])||
    !lockedModels.some(m=>m.path===config.FRAME_ALPHA_MATTING_MODEL)||
    !lockedModels.some(m=>m.path===config.FRAME_ALPHA_TRACKING_MODEL||m.path===resolve(config.FRAME_ALPHA_TRACKING_MODEL,'..')))
    fail('POSTPROCESS_CONFIG_INVALID');
 await access(config.FRAME_ALPHA_PYTHON,fs.X_OK);
 for(const p of [workerSource,samSource,...required.slice(1).map(k=>config[k])])await access(p,fs.R_OK);
 const env={PATH:'/usr/bin:/bin',HOME:process.env.HOME??'/tmp',
  PYTHONPATH:[workerSource,samSource,config.FRAME_ALPHA_MODULE_ROOT].join(delimiter),
  HF_HUB_OFFLINE:'1',TRANSFORMERS_OFFLINE:'1',PYTHONDONTWRITEBYTECODE:'1',
  FRAME_ALPHA_MATTING_MODEL:config.FRAME_ALPHA_MATTING_MODEL,
  FRAME_ALPHA_TRACKING_MODEL:config.FRAME_ALPHA_TRACKING_MODEL,
  WORKFLOW_POSTPROCESS_MODEL_DIGEST:modelDigest};
 async function run(root,entry=script,timeout=30*60*1000){
  return await new Promise((resolveRun,reject)=>{
   const child=spawn(config.FRAME_ALPHA_PYTHON,['-B',entry,root],{env,stdio:'ignore'});
   // Bound actual model attempt, no retries or orphaned resident invocations.
   const timer=setTimeout(()=>child.kill('SIGKILL'),timeout);timer.unref();
   child.once('error',()=>{clearTimeout(timer);reject(Error('POSTPROCESS_PROVIDER_START'));});
   child.once('close',code=>{clearTimeout(timer);if(code===0)resolveRun();else reject(Error('POSTPROCESS_PROVIDER_FAILED'));});
  });
 }
 return async(sequence,{claim,motion,direction,posePreset,motionPosePreset,assetSetSha256,sourcePortraitStage})=>{
  if(posePreset!==undefined){
   const root=await mkdtemp(join(tmpdir(),'workflow-postprocess-pixel-'));
   try{
    const raw=await pds.fetchPortraitAsset(claim,sequence);
    await writeFile(join(root,'source.png'),raw,{mode:0o600,flag:'wx'});
    await run(root,staticScript,120000);
    const path=join(root,'pixel.png'),stat=await lstat(path);
    if(!stat.isFile()||stat.isSymbolicLink()||stat.size>1024*1024)fail('POSTPROCESS_STATIC_PIXEL_OUTPUT');
    const result=await readFile(path);
    if(result.length<33||result.subarray(0,8).toString('hex')!=='89504e470d0a1a0a'||
       result.subarray(12,16).toString()!=='IHDR'||result.readUInt32BE(16)!==128||
       result.readUInt32BE(20)!==128||result[24]!==8||result[25]!==6)
       fail('POSTPROCESS_STATIC_PIXEL_OUTPUT');
    return {artifact:{sourceAssetId:sequence.assetId,base64:result.toString('base64')}};
   }finally{await rm(root,{recursive:true,force:true});}
  }
  const root=await mkdtemp(join(tmpdir(),'workflow-postprocess-'));
  const cleanup=()=>rm(root,{recursive:true,force:true});
  try{
   await mkdir(join(root,'raw'),{mode:0o700});
   await writeFile(join(root,'input.json'),JSON.stringify({claim,motion,direction,sequence,...(motionPosePreset===undefined?{}:{motionPosePreset,assetSetSha256,sourcePortraitStage})}),{mode:0o600,flag:'wx'});
   for(const [i,frame] of sequence.frames.entries()){
    if(frame.index!==i)fail('POSTPROCESS_SOURCE_ORDER');
    const raw=await pds.fetchAsset(claim,frame);
    await writeFile(join(root,'raw','frame-'+String(i).padStart(8,'0')+'.png'),raw,{mode:0o600,flag:'wx'});
   }
   await run(root);
   const {alpha,pixel}=JSON.parse(await readFile(join(root,'output.json'),'utf8'));
   if(alpha.requestId!==claim.taskId||pixel.requestId!==claim.taskId||alpha.frames.length!==sequence.frameCount||
      pixel.frames.length!==sequence.frameCount||
      alpha.posePreset!==(motionPosePreset??'idle')||pixel.posePreset!==(motionPosePreset??'idle'))fail('POSTPROCESS_OUTPUT_BINDING');
   const assets={};
   for(const id of new Set([...alpha.frames.map(f=>f.alphaAssetId),...pixel.frames.map(f=>f.assetId)])){
    if(!/^[a-f0-9]{64}\.png$/.test(id))fail('POSTPROCESS_OUTPUT_ASSET');
    const path=join(root,'assets',id),st=await lstat(path);
    if(!st.isFile()||st.isSymbolicLink())fail('POSTPROCESS_OUTPUT_ASSET');
    const raw=await readFile(path);
    if(sha(raw)+'.png'!==id)fail('POSTPROCESS_OUTPUT_BYTES');
    assets[id]=raw.toString('base64');
   }
   // The complete artifact stays in memory; actual model scratch is now disposable.
   await cleanup();return {artifact:{alpha,pixel,assets}};
  }catch(e){await cleanup();throw e;}
 };
}
