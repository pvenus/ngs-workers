import {request} from 'node:http';
import {createHash} from 'node:crypto';
import {requestLocalJson} from './local-ports.mjs';

export class FramesDefinitelyRejected extends Error {}
const definitive=new Map([[403,new Set(['WORKFLOW_MEDIA_CLAIM_DENIED','WORKFLOW_MEDIA_INTERNAL_ONLY'])],[404,new Set(['WORKFLOW_MEDIA_DISABLED'])]]);
const route='/v1/npcs/ownership/workflow-frames';
const portraitRoute='/v1/npcs/ownership/workflow-portrait-pixel';
const staticMaxPng=16*1024*1024;
const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
function validPortraitAsset(v){
 return exact(v,['sha256','assetId','mimeType','bytes'])&&typeof v.sha256==='string'&&/^[a-f0-9]{64}$/.test(v.sha256)&&
  v.assetId===v.sha256+'.png'&&v.mimeType==='image/png'&&Number.isSafeInteger(v.bytes)&&v.bytes>0&&v.bytes<=staticMaxPng;
}
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');

export function raw({port=8030,path,body,timeout,type,length}){
 return new Promise((resolve,reject)=>{
  const payload=body===undefined?null:Buffer.from(JSON.stringify(body));
  const fail=()=>reject(Error('POSTPROCESS_PDS_TRANSPORT'));
  const req=request({hostname:'127.0.0.1',port,path,method:payload?'POST':'GET',agent:false,
   signal:AbortSignal.timeout(timeout),
   headers:payload?{'Content-Type':'application/json','Content-Length':payload.length}:{}},resp=>{
   const denial=(path===route||path===portraitRoute)&&payload?definitive.get(resp.statusCode):null;
   if((resp.statusCode!==200&&!denial)||String(resp.headers['content-type']).split(';')[0]!==type){resp.destroy();fail();return;}
   const parts=[];let size=0;const limit=length??4*1024*1024;
   resp.on('data',p=>{size+=p.length;if(size>limit){resp.destroy();fail();}else parts.push(p);});
   resp.on('end',()=>{
    if(length!==undefined&&size!==length){fail();return;}
    const data=Buffer.concat(parts);
    if(!denial){resolve(data);return;}
    try{
     const body=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(data));
     if(body&&Object.keys(body).length===1&&Object.hasOwn(body,'error')&&denial.has(body.error)){reject(new FramesDefinitelyRejected(body.error));return;}
    }catch{}
    fail();
   });
   resp.on('error',fail);resp.on('aborted',fail);
  });
  req.on('error',fail);req.end(payload);
 });
}
export function postprocessPorts({transport=raw,heartbeatTransport=requestLocalJson}={}){
 const json=async(path,body,timeout)=>JSON.parse(await transport({path,body,timeout,type:'application/json'}));
 return {
  async capability(){try{const v=await json(route,undefined,5000);return v.enabled===true&&v.schemaVersion==='workflow.frames-write.v1'&&Object.keys(v).length===2;}catch{return false;}},
  async capabilityPortrait(){try{
   const v=await json(portraitRoute,undefined,5000);return exact(v,['enabled','schemaVersion'])&&v.enabled===true&&v.schemaVersion==='workflow.portrait-pixel-write.v1';
  }catch{return false;}},
  async heartbeat(signal){try{return (await heartbeatTransport({port:8090,path:'/api/service-heartbeat',method:'POST',
        body:{serverName:'postprocess-worker'},deadline:5000,signal})).ok===true;}catch{return false;}},
  async read({claim,motion,direction,predecessorTaskId,posePreset,motionPosePreset,assetSetSha256}){
   if(posePreset!==undefined){
    const v=await json(portraitRoute+'/read',{claim},30000);
    if(!exact(v,['posePreset','direction','source'])||v.posePreset!==posePreset||v.direction!==direction||
       !validPortraitAsset(v.source))throw Error('POSTPROCESS_STATIC_PDS_SOURCE_BINDING');
    return v.source;
   }
   const v=await json(route+'/read',{claim},30000);
   const s=v.sequence;
   if(v.motion!==motion||v.direction!==direction||!s||s.schemaVersion!=='ngs.animation-native-png-sequence.v1'||
      s.npcId!==claim.npcId||s.presetId!==motion||s.posePreset!==(motionPosePreset??'idle')||s.direction!==direction||s.requestId!==predecessorTaskId||
      !Number.isInteger(s.frameCount)||s.frameCount<2||s.frameCount>1000||s.frames?.length!==s.frameCount)
    throw Error('POSTPROCESS_PDS_SOURCE_BINDING');
   if(motionPosePreset!==undefined){
    if(!exact(v,['direction','motion','sequence','posePreset','assetSetSha256'])||
       v.posePreset!==motionPosePreset||v.assetSetSha256!==assetSetSha256||
       s.sourceBindingHash!==sha(JSON.stringify([claim.officialRequestId,'portrait_'+direction,motion,motionPosePreset,assetSetSha256])))
      throw Error('POSTPROCESS_PDS_SELECTED_SOURCE_BINDING');
   }
   return s;
  },
  async fetchAsset(claim,frame){
   if(!/^[0-9a-f]{64}$/.test(frame.sha256)||frame.assetId!==frame.sha256+'.png'||frame.mimeType!=='image/png'||
      !Number.isSafeInteger(frame.bytes)||frame.bytes<=0)throw Error('POSTPROCESS_PDS_ASSET_REFERENCE');
   const bytes=await transport({path:route+'/asset',body:{claim,assetId:frame.assetId},timeout:30000,
     type:'image/png',length:frame.bytes});
   if(bytes.length!==frame.bytes||sha(bytes)!==frame.sha256)throw Error('POSTPROCESS_PDS_ASSET_CONTENT');
   return bytes;
  },
  async fetchPortraitAsset(claim,source){
   if(!validPortraitAsset(source))throw Error('POSTPROCESS_STATIC_PDS_ASSET_REFERENCE');
   const bytes=await transport({path:portraitRoute+'/asset',body:{claim,assetId:source.assetId},timeout:30000,
     type:'image/png',length:source.bytes});
   if(bytes.length!==source.bytes||sha(bytes)!==source.sha256)throw Error('POSTPROCESS_STATIC_PDS_ASSET_CONTENT');
   return bytes;
  },
  async publish({claim,posePreset},source,{artifact}){
   if(posePreset!==undefined&&(!exact(artifact,['sourceAssetId','base64'])||!validPortraitAsset(source)||
      artifact.sourceAssetId!==source.assetId||typeof artifact.base64!=='string'||!artifact.base64))
     throw new FramesDefinitelyRejected('POSTPROCESS_STATIC_PDS_ARTIFACT_BINDING');
   return json(posePreset===undefined?route:portraitRoute,{claim,artifact},120000);
  },
 };
}
