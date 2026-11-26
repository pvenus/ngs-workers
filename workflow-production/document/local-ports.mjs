import {mapTypes,mapPds,mapCapability} from './map-contract.mjs';
import {propDesignOperations,propDesignPath,propDesignCapability} from './prop-design-contract.mjs';
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
 ...Object.fromEntries(mapTypes.map(v=>[v,{path:mapPds,schema:mapCapability}])),
 ...Object.fromEntries(Object.keys(propDesignOperations).map(v=>[v,{path:propDesignPath,schema:propDesignCapability}])),
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

export const npcPlanningReadDeadlineMs=30_000;
export function requestLocalJson({port,path,method,body,deadline,signal,responseLimit=131072,npcPlanningRead=false,presetPublication=false,mapPublication=false}){
 return new Promise((resolve,reject)=>{
  const payload=body?Buffer.from(JSON.stringify(body)):null;
  const timeout=AbortSignal.timeout(deadline);
  const signals=[timeout];
  if(signal)signals.unshift(signal);
  const fail=()=>reject(Error(npcPlanningRead&&timeout.aborted?'NPC_PLANNING_PDS_READ_TIMEOUT':'DOCUMENT_WORKER_LOCAL_TRANSPORT'));
  const req=request({hostname:'127.0.0.1',port,path,method,agent:false,signal:AbortSignal.any(signals),
   headers:{Accept:'application/json',...(payload?{'Content-Type':'application/json','Content-Length':payload.length}:{})}},resp=>{
   const presetConflict=presetPublication&&path===presetPath&&method==='POST'&&resp.statusCode===409;
   const mapConflict=mapPublication&&resp.statusCode===409;
   if((resp.statusCode!==200&&!presetConflict&&!mapConflict)||!/^application\/json(?:\s*;|$)/i.test(String(resp.headers['content-type']??''))){resp.destroy();fail();return;}
   let len=0;const chunks=[];
   resp.on('data',part=>{len+=part.length;if(len>responseLimit){req.destroy();fail();}else chunks.push(part);});
   resp.on('aborted',fail);resp.on('error',fail);
   resp.on('end',()=>{try{
    const reply=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks)));
    if(mapConflict&&reply?.error==='MAP_REVISION_CONFLICT')reject(Error('MAP_REVISION_CONFLICT'));
    else if(mapConflict)fail();
    else if(presetConflict){
     if(exact(reply,['error'])&&reply.error==='WORKFLOW_PRESET_REJECTED_BEFORE_WRITE')reject(Error(reply.error));
     else fail();
    }else resolve(reply);
   }catch{fail();}});
  });
  let connected=false;
  req.once('socket',socket=>{
   if(!socket.connecting)connected=true;
   else socket.once('connect',()=>{connected=true;});
  });
  req.on('error',error=>{
   if(method==='POST'&&!connected&&error?.code==='ECONNREFUSED'&&error?.syscall==='connect')
    reject(Error('DOCUMENT_PDS_CONNECT_REFUSED_BEFORE_SEND'));
   else fail();
  });
  req.end(payload);
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
   if(!['document-worker','document-worker-2'].includes(serverName))return false;
   try{
    const data=await transport({port:8090,path:heartbeatPath,method:'POST',body:{serverName},deadline:5000,signal});
    return exact(data,['ok'])&&data.ok===true;
   }catch{return false;}
  },
  async publishExactPreset(command,claimStamp){
   const {predecessorReceiptRef,artifact}=command;
   return await transport({port:8030,path:presetPath,method:'POST',
    body:{claim:claimOf(command,claimStamp),artifact:JSON.parse(artifact),predecessorReceiptRef},deadline:120000,presetPublication:true});
  },
  async readMap(claim,signal){
   return await transport({port:8030,path:mapPds+'/read',method:'POST',body:{claim},deadline:10000,signal});
  },
  async readMapImage(claim,imageSha256,signal){
   return await transport({port:8030,path:mapPds+'/image',method:'POST',body:{claim,imageSha256},deadline:20000,signal,responseLimit:12*1024*1024});
  },
  async publishMap(claim,graph){
   return await transport({port:8030,path:mapPds,method:'POST',body:{claim,graph},deadline:30000,mapPublication:true});
  },
  async readPropDesign(claim,signal){
   return await transport({port:8030,path:propDesignPath+'/read',method:'POST',body:{claim},deadline:5000,signal});
  },
  async publishPropDesign(claim,artifact){
   return await transport({port:8030,path:propDesignPath,method:'POST',body:{claim,artifact},deadline:120000});
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
    body:{claim:claimOf(command,claimStamp),inputMode:'folder-snapshot'},deadline:npcPlanningReadDeadlineMs,npcPlanningRead:true});
  },
  async readPortraitArt(command,claimStamp){
   return await transport({port:8030,path:portraitArtPath+'/read',method:'POST',
    body:{claim:claimOf(command,claimStamp),inputMode:'folder-snapshot'},deadline:npcPlanningReadDeadlineMs,npcPlanningRead:true});
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
    body:{claim:claimOf(command,claimStamp),inputMode:'folder-snapshot'},deadline:npcPlanningReadDeadlineMs,npcPlanningRead:true});
  },
  async readIdle(command,claimStamp){
   return await transport({port:8030,path:idlePath+'/read',method:'POST',
    body:{claim:claimOf(command,claimStamp),inputMode:'folder-snapshot'},deadline:npcPlanningReadDeadlineMs,npcPlanningRead:true});
  },
  async readCombat(command,claimStamp){
   return await transport({port:8030,path:combatPath+'/read',method:'POST',
    body:{claim:claimOf(command,claimStamp),inputMode:'folder-snapshot'},deadline:npcPlanningReadDeadlineMs,npcPlanningRead:true,responseLimit:512*1024});
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
