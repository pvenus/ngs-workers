import {request} from 'node:http';
import {exactDeleteTarget,workflowDeleteFolder,singleTargetDeleteSchema,conceptDeleteRoot,npcDeleteRoot,audioDeleteRoot,conceptDelete,npcDelete,audioDelete} from './file-delete.mjs';
import {exact} from './vendor/planner-f24d23a/planner.mjs';

export const deletePath='/v1/npcs/ownership/workflow-delete';
export const conceptCategoryPath='/v1/concept/category-workflow';
export const storageDeletePath='/v1/storage/delete-target';
export const cinematicPlaybackPath='/v1/cinematics/ownership/workflow-playback';
export const cinematicPlaybackCapability='workflow.cinematic-playback-write.v1';
export class PlaybackRevisionConflict extends Error {}
const playbackConflictCode='WORKFLOW_CINEMATIC_PLAYBACK_CONFLICT';
export const heartbeatPath='/api/service-heartbeat';
export class DeleteDefinitelyRejected extends Error {}
const definitive=new Map([[403,new Set(['WORKFLOW_DELETE_CLAIM_DENIED','WORKFLOW_DELETE_INTERNAL_ONLY'])],[404,new Set(['WORKFLOW_DELETE_DISABLED'])]]);
const conceptDefinitive=new Map([[403,new Set(['CONCEPT_WORKFLOW_CLAIM_DENIED','CONCEPT_WORKFLOW_INTERNAL_ONLY',
 'CONCEPT_REQUIRED_CATEGORY_REMOVAL_DENIED'])],[404,new Set(['CONCEPT_WORKFLOW_DISABLED'])]]);
const storageDefinitive=new Map([[403,new Set(['STORAGE_DELETE_CLAIM_DENIED','STORAGE_DELETE_INTERNAL_ONLY'])],
 [404,new Set(['STORAGE_DELETE_DISABLED'])]]);
const roots=new Map([[conceptDelete,conceptDeleteRoot],[npcDelete,npcDeleteRoot],[audioDelete,audioDeleteRoot]]);
const receiptSchema='workflow.pds-delete-receipt.v1';

export function requestLocalJson({port,path,method,body,deadline,signal}){
 return new Promise((resolve,reject)=>{
  const payload=body?Buffer.from(JSON.stringify(body)):null;
  const signals=[AbortSignal.timeout(deadline)];
  if(signal)signals.unshift(signal);
  const fail=()=>reject(Error('FILE_WORKER_LOCAL_TRANSPORT'));
  const req=request({hostname:'127.0.0.1',port,path,method,agent:false,signal:AbortSignal.any(signals),
   headers:{Accept:'application/json',...(payload?{'Content-Type':'application/json','Content-Length':payload.length}:{})}},resp=>{
   const denial=method==='POST'?(path===deletePath?definitive:path===conceptCategoryPath?conceptDefinitive:path===storageDeletePath?storageDefinitive:null)?.get(resp.statusCode):null;
   const playbackConflict=method==='POST'&&path===cinematicPlaybackPath&&resp.statusCode===409;
   if((resp.statusCode!==200&&!denial&&!playbackConflict)||!/^application\/json(?:\s*;|$)/i.test(String(resp.headers['content-type']??''))){resp.destroy();fail();return;}
   let len=0;const chunks=[];
   resp.on('data',part=>{len+=part.length;if(len>65536){req.destroy();fail();}else chunks.push(part);});
   resp.on('aborted',fail);resp.on('error',fail);
   resp.on('end',()=>{try{
    const body=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks)));
    if(playbackConflict){
     if(exact(body,['error'])&&body.error===playbackConflictCode)reject(new PlaybackRevisionConflict(playbackConflictCode));
     else fail();
    }else if(!denial)resolve(body);
    else if(exact(body,['error'])&&denial.has(body.error))reject(new DeleteDefinitelyRejected(body.error));
    else fail();
   }catch{fail();}});
  });
  req.on('error',fail);req.end(payload);
 });
}

export function localPorts({transport=requestLocalJson,singleTarget=false}={}){
 return {
  ...(singleTarget?{deleteContract:singleTargetDeleteSchema}:{}),
  async capability(signal,operation){
   if(singleTarget){
    if(operation!==undefined&&!roots.has(operation))return false;
    try{
     const data=await transport({port:8030,path:storageDeletePath,method:'GET',deadline:5000,signal});
     if(!exact(data,['enabled','schemaVersion','roots'])||data.enabled!==true||data.schemaVersion!==singleTargetDeleteSchema||
        !Array.isArray(data.roots)||!data.roots.length||
        !data.roots.every((root,i)=>[conceptDeleteRoot,npcDeleteRoot,audioDeleteRoot].includes(root)&&(i===0||data.roots[i-1]<root)))return false;
     return operation===undefined||data.roots.includes(roots.get(operation));
    }catch{return false;}
   }
   operation??='npc.delete-folder';
   try{
    const concept=operation==='concept.category-delete';
    if(!concept&&operation!=='npc.delete-folder')return false;
    const data=await transport({port:8030,path:concept?conceptCategoryPath:deletePath,method:'GET',deadline:5000,signal});
    return exact(data,['enabled','schemaVersion'])&&data.enabled===true&&data.schemaVersion===(concept?'workflow.concept-category-write.v1':receiptSchema);
   }catch{return false;}
  },
  async cinematicPlaybackReady(signal){
   try{
    const data=await transport({port:8030,path:cinematicPlaybackPath,method:'GET',deadline:5000,signal});
    return exact(data,['enabled','schemaVersion'])&&data.enabled===true&&data.schemaVersion===cinematicPlaybackCapability;
   }catch{return false;}
  },
  async updateCinematicPlayback(claim){
   if(!exact(claim,['officialRequestId','taskId','projectId','payloadSha256','claimStamp'])||claim.projectId!=='binari'||
      !Number.isSafeInteger(claim.claimStamp)||claim.claimStamp<1)throw Error('CINEMATIC_PLAYBACK_PORT_CLAIM');
   return await transport({port:8030,path:cinematicPlaybackPath,method:'POST',body:{claim},deadline:40_000});
  },
  async heartbeat(signal){
   try{
    const data=await transport({port:8090,path:heartbeatPath,method:'POST',body:{serverName:'file-worker'},deadline:5000,signal});
    return exact(data,['ok'])&&data.ok===true;
   }catch{return false;}
  },
  async deleteTargets(command,binding){
   const target=exactDeleteTarget(command);
   if(!exact(binding,['officialRequestId','taskId','projectId','payloadSha256','claimStamp'])||
      !Number.isSafeInteger(binding.claimStamp)||binding.claimStamp<0)throw Error('WORKFLOW_DELETE_CLAIM_STAMP_INVALID');
   const concept=target.kind==='concept-category';
   const audio=target.kind==='audio-directory';
   if(audio&&!singleTarget)throw Error('WORKFLOW_DELETE_TARGET_INVALID');
   const claim={...binding,[concept?'categoryId':audio?'sourceRequestId':'npcId']:target.id};
   return await transport({port:8030,path:singleTarget?storageDeletePath:concept?conceptCategoryPath:deletePath,method:'POST',
    body:singleTarget?{claim,target:workflowDeleteFolder(command)}:{claim},deadline:120000});
  },
 };
}
