import {request} from 'node:http';
import {exact} from './vendor/planner-f24d23a/planner.mjs';

export const presetPath='/v1/npcs/ownership/workflow-preset';
export const representativePath='/v1/npcs/ownership/workflow-plan-representative';
export const portraitPath='/v1/npcs/ownership/workflow-portrait-representative';
export const combatReferencePortraitPath='/v1/npcs/ownership/workflow-portrait-combat-reference';
export const combatDirectionPortraitPath='/v1/npcs/ownership/workflow-portrait-combat-direction';
export const idlePortraitPath='/v1/npcs/ownership/workflow-portrait-idle-reference';
export const directionPortraitPath='/v1/npcs/ownership/workflow-portrait-direction';
export const motionImagePath='/v1/motion-presets/workflow-convert-2d';
export const propBasePath='/v1/props/ownership/workflow-stage';
export const propImagePath='/v1/props/ownership/workflow-image';
export const heartbeatPath='/api/service-heartbeat';
const cinematicSourcePath='/v1/cinematics/ownership/workflow-source';
const routes={
 'cinematic.source.generate':{path:cinematicSourcePath,schema:'workflow.cinematic-source-write.v1'},
 'prop.generate.base':{path:propBasePath,schema:'workflow.prop-stage.v1'},
 'prop.regenerate.base':{path:propBasePath,schema:'workflow.prop-stage.v1'},
 'prop.image-generate':{path:propImagePath,schema:'workflow.prop-image-write.v1'},
 'motion-preset.convert-2d.image':{path:motionImagePath,schema:'workflow.motion-preset-convert-2d.v1'},
 'npc.portrait-representative':{path:portraitPath,schema:'workflow.representative-portrait-write.v1'},
 'npc.portrait-idle-reference':{path:idlePortraitPath,schema:'workflow.idle-reference-portrait-write.v1'},
 'npc.portrait-direction':{path:directionPortraitPath,schema:'workflow.direction-portrait-write.v1'},
 'npc.portrait-combat-reference':{path:combatReferencePortraitPath,schema:'workflow.combat-reference-portrait-write.v1'},
 'npc.portrait-combat-direction':{path:combatDirectionPortraitPath,schema:'workflow.combat-direction-portrait-write.v1'},
 'npc.preset':{path:presetPath,schema:'workflow.preset-publication-receipt.v1'},
 'npc.plan-representative':{path:representativePath+'?inputMode=folder-snapshot',schema:'workflow.representative-plan-write.v1'}
};
function claimOf(command,claimStamp){
 if(!Number.isSafeInteger(claimStamp)||claimStamp<0)throw Error('WORKFLOW_DOCUMENT_CLAIM_STAMP_INVALID');
 const {officialRequestId,taskId,projectId,npcId,payloadSha256}=command;
 return {officialRequestId,taskId,projectId,npcId,payloadSha256,claimStamp};
}

export function requestLocalJson({port,path,method,body,deadline,signal,responseLimit=65536}){
 return new Promise((resolve,reject)=>{
  const payload=body?Buffer.from(JSON.stringify(body)):null;
  const signals=[AbortSignal.timeout(deadline)];
  if(signal)signals.unshift(signal);
  const fail=()=>reject(Error('DOCUMENT_WORKER_LOCAL_TRANSPORT'));
  const req=request({hostname:'127.0.0.1',port,path,method,agent:false,signal:AbortSignal.any(signals),
   headers:{Accept:'application/json',...(payload?{'Content-Type':'application/json','Content-Length':payload.length}:{})}},resp=>{
   if(resp.statusCode!==200||!/^application\/json(?:\s*;|$)/i.test(String(resp.headers['content-type']??''))){resp.destroy();fail();return;}
   let len=0;const chunks=[];
   resp.on('data',part=>{len+=part.length;if(len>responseLimit){req.destroy();fail();}else chunks.push(part);});
   resp.on('aborted',fail);resp.on('error',fail);
   resp.on('end',()=>{try{resolve(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks))));}catch{fail();}});
  });
  req.on('error',fail);req.end(payload);
 });
}

export function localPorts({transport=requestLocalJson}={}){
 return {
  async capability(signal,operation='npc.preset'){
   try{
    if(!Object.hasOwn(routes,operation))return false;
    const {path,schema}=routes[operation];
    const data=await transport({port:8030,path,method:'GET',deadline:5000,signal});
    return exact(data,['enabled','schemaVersion'])&&data.enabled===true&&data.schemaVersion===schema;
   }catch{return false;}
  },
  async heartbeat(signal,serverName='document-worker'){
   try{
    const data=await transport({port:8090,path:heartbeatPath,method:'POST',body:{serverName},deadline:5000,signal});
    return exact(data,['ok'])&&data.ok===true;
   }catch{return false;}
  },
  async publishExactPreset(command,claimStamp){
   const {predecessorReceiptRef,artifact}=command;
   return await transport({port:8030,path:presetPath,method:'POST',
    body:{claim:claimOf(command,claimStamp),artifact:JSON.parse(artifact),predecessorReceiptRef},deadline:120000});
  },
  async readRepresentative(command,claimStamp){
   return await transport({port:8030,path:representativePath+'/read',method:'POST',
    body:{claim:claimOf(command,claimStamp),inputMode:'folder-snapshot'},deadline:5000});
  },
  async readPortrait(command,claimStamp,signal){
   return await transport({port:8030,path:routes[command.operation].path+'/read',method:'POST',body:{claim:claimOf(command,claimStamp)},deadline:5000,signal,responseLimit:12*1024*1024});
  },
  async publishPortrait(command,claimStamp,basis,signal){
   return await transport({port:8030,path:routes[command.operation].path,method:'POST',
    body:{claim:claimOf(command,claimStamp),basis,artifact:JSON.parse(command.artifact)},deadline:120000,signal});
  },
  async readCinematicSource(claim,signal){
   return await transport({port:8030,path:cinematicSourcePath+'/read',method:'POST',body:{claim},deadline:5000,signal});
  },
  async publishCinematicSource(claim,artifact){
   return await transport({port:8030,path:cinematicSourcePath,method:'POST',body:{claim,artifact},deadline:120000});
  },
  async readPropBase(claim,signal){
   return transport({port:8030,path:propBasePath+'/read',method:'POST',body:{claim},deadline:5000,signal,responseLimit:128*1024});
  },
  async publishPropBase(claim,artifact){
   return transport({port:8030,path:propBasePath,method:'POST',body:{claim,artifact},deadline:120000});
  },
  async readPropDesign(claim,signal){
   return transport({port:8030,path:propImagePath+'/read',method:'POST',body:{claim},deadline:5000,signal,responseLimit:128*1024});
  },
  async publishPropImage(claim,artifact){
   return transport({port:8030,path:propImagePath,method:'POST',body:{claim,artifact},deadline:120000});
  },
  async readMotionImage(claim,signal){
   return transport({port:8030,path:motionImagePath+'/read',method:'POST',body:{claim},deadline:5000,signal});
  },
  async publishMotionImage(claim,artifact){
   return transport({port:8030,path:motionImagePath,method:'POST',body:{claim,artifact},deadline:120000});
  },
  async publishRepresentative(command,claimStamp,basis){
   return await transport({port:8030,path:representativePath,method:'POST',
    body:{claim:claimOf(command,claimStamp),basis,artifact:JSON.parse(command.artifact)},deadline:120000});
  },
 };
}
