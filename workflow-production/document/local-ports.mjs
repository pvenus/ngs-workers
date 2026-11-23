import {request} from 'node:http';
import {exact} from './vendor/planner-89c7568/planner.mjs';

export const presetPath='/v1/npcs/ownership/workflow-preset';
export const overviewPath='/v1/npcs/ownership/workflow-plan-overview';
export const portraitArtPath='/v1/npcs/ownership/workflow-plan-portrait-art';
export const representativePath='/v1/npcs/ownership/workflow-plan-representative';
export const planningAddPath='/v1/npcs/planning/workflow-add';
export const planningUpdatePath='/v1/npcs/planning/workflow-update';
export const combatPath='/v1/npcs/ownership/workflow-plan-combat';
export const idlePath='/v1/npcs/ownership/workflow-plan-idle';
export const conceptCategoryPath='/v1/concept/category-workflow';
export const heartbeatPath='/api/service-heartbeat';
const routes={
 'concept.category-generate':{path:conceptCategoryPath,schema:'workflow.concept-category-write.v1'},
 'npc.preset':{path:presetPath,schema:'workflow.preset-publication-receipt.v1'},
 'npc.plan-overview':{path:overviewPath+'?inputMode=folder-snapshot',schema:'workflow.overview-plan-write.v1'},
 'npc.plan-portrait-art':{path:portraitArtPath+'?inputMode=folder-snapshot',schema:'workflow.portrait-art-plan-write.v1'},
 'npc.plan-representative':{path:representativePath+'?inputMode=folder-snapshot',schema:'workflow.representative-plan-write.v1'},
 'npc.planning.add':{path:planningAddPath,schema:'workflow.npc-planning-add-write.v1'},
 'npc.planning.update':{path:planningUpdatePath,schema:'workflow.npc-planning-update-write.v1'},
 'npc.plan-idle':{path:idlePath+'?inputMode=folder-snapshot',schema:'workflow.idle-plan-write.v1'},
 'npc.plan-combat':{path:combatPath+'?inputMode=folder-snapshot',schema:'workflow.combat-plan-write.v1'}
};
function claimOf(command,claimStamp){
 if(!Number.isSafeInteger(claimStamp)||claimStamp<0)throw Error('WORKFLOW_DOCUMENT_CLAIM_STAMP_INVALID');
 const {officialRequestId,taskId,projectId,npcId,payloadSha256}=command;
 return {officialRequestId,taskId,projectId,npcId,payloadSha256,claimStamp};
}

function conceptClaim(command,claimStamp){
 if(!Number.isSafeInteger(claimStamp)||claimStamp<0)throw Error('WORKFLOW_DOCUMENT_CLAIM_STAMP_INVALID');
 const {officialRequestId,taskId,projectId,categoryId,payloadSha256}=command;
 return {officialRequestId,taskId,projectId,categoryId,payloadSha256,claimStamp};
}

export function requestLocalJson({port,path,method,body,deadline,signal,responseLimit=131072}){
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
  async heartbeat(signal){
   try{
    const data=await transport({port:8090,path:heartbeatPath,method:'POST',body:{serverName:'document-worker'},deadline:5000,signal});
    return exact(data,['ok'])&&data.ok===true;
   }catch{return false;}
  },
  async publishExactPreset(command,claimStamp){
   const {predecessorReceiptRef,artifact}=command;
   return await transport({port:8030,path:presetPath,method:'POST',
    body:{claim:claimOf(command,claimStamp),artifact:JSON.parse(artifact),predecessorReceiptRef},deadline:120000});
  },
  async readConceptCategory(command,claimStamp,signal){
   return await transport({port:8030,path:conceptCategoryPath+'/read',method:'POST',
    body:{claim:conceptClaim(command,claimStamp)},deadline:5000,signal,responseLimit:6*1024*1024+4096});
  },
  async publishConceptCategory(command,claimStamp,signal){
   return await transport({port:8030,path:conceptCategoryPath,method:'POST',
    body:{claim:conceptClaim(command,claimStamp),artifact:JSON.parse(command.artifact)},deadline:120000,signal});
  },
  async readPlanningAdd(command,claimStamp,signal){
   return await transport({port:8030,path:planningAddPath+'/read',method:'POST',
    body:{claim:claimOf(command,claimStamp)},deadline:5000,signal});
  },
  async publishPlanningAdd(command,claimStamp,signal){
   return await transport({port:8030,path:planningAddPath,method:'POST',
    body:{claim:claimOf(command,claimStamp),artifact:JSON.parse(command.artifact)},deadline:120000,signal});
  },
  async readPlanningUpdate(command,claimStamp,signal){
   return await transport({port:8030,path:planningUpdatePath+'/read',method:'POST',
    body:{claim:claimOf(command,claimStamp)},deadline:5000,signal,responseLimit:512*1024});
  },
  async publishPlanningUpdate(command,claimStamp,signal){
   return await transport({port:8030,path:planningUpdatePath,method:'POST',
    body:{claim:claimOf(command,claimStamp),artifact:JSON.parse(command.artifact)},deadline:120000,signal});
  },
  async readOverview(command,claimStamp){
   return await transport({port:8030,path:overviewPath+'/read',method:'POST',
    body:{claim:claimOf(command,claimStamp),inputMode:'folder-snapshot'},deadline:5000});
  },
  async readPortraitArt(command,claimStamp){
   return await transport({port:8030,path:portraitArtPath+'/read',method:'POST',
    body:{claim:claimOf(command,claimStamp),inputMode:'folder-snapshot'},deadline:5000});
  },
  async publishOverview(command,claimStamp,basis){
   return await transport({port:8030,path:overviewPath,method:'POST',
    body:{claim:claimOf(command,claimStamp),basis,artifact:JSON.parse(command.artifact)},deadline:120000});
  },
  async publishPortraitArt(command,claimStamp,basis){
   return await transport({port:8030,path:portraitArtPath,method:'POST',
    body:{claim:claimOf(command,claimStamp),basis,artifact:JSON.parse(command.artifact)},deadline:120000});
  },
  async readRepresentative(command,claimStamp){
   return await transport({port:8030,path:representativePath+'/read',method:'POST',
    body:{claim:claimOf(command,claimStamp),inputMode:'folder-snapshot'},deadline:5000});
  },
  async readIdle(command,claimStamp){
   return await transport({port:8030,path:idlePath+'/read',method:'POST',
    body:{claim:claimOf(command,claimStamp),inputMode:'folder-snapshot'},deadline:5000});
  },
  async readCombat(command,claimStamp){
   return await transport({port:8030,path:combatPath+'/read',method:'POST',
    body:{claim:claimOf(command,claimStamp),inputMode:'folder-snapshot'},deadline:5000,responseLimit:512*1024});
  },
  async publishCombat(command,claimStamp,basis){
   return await transport({port:8030,path:combatPath,method:'POST',
    body:{claim:claimOf(command,claimStamp),basis,artifact:JSON.parse(command.artifact)},deadline:120000});
  },
  async publishIdle(command,claimStamp,basis){
   return await transport({port:8030,path:idlePath,method:'POST',
    body:{claim:claimOf(command,claimStamp),basis,artifact:JSON.parse(command.artifact)},deadline:120000});
  },
  async publishRepresentative(command,claimStamp,basis){
   return await transport({port:8030,path:representativePath,method:'POST',
    body:{claim:claimOf(command,claimStamp),basis,artifact:JSON.parse(command.artifact)},deadline:120000});
  },
 };
}
