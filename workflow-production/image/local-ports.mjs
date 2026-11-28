import {armConnectionDeadline} from './connection-deadline.mjs';
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
const backgroundSourcePath='/v1/cinematics/ownership/workflow-background';
const chainPath=Object.freeze({
 'background.pixel.workflow.cinematic':backgroundSourcePath+'-cinematic-source',
 'background.pixel.workflow.draft':backgroundSourcePath+'-pixel-draft'
});
const uiPath='/v1/ui/ownership/workflow-asset/';
const uiRoutes=Object.freeze(Object.fromEntries(['generate','import'].map(mode=>['ui.asset.'+mode,{path:uiPath+mode,schema:'workflow.ui-asset-'+mode+'.v1'}])));
const routes={...uiRoutes,...Object.fromEntries(Object.entries(chainPath).map(([op,path])=>[op,{path,schema:'workflow.background-pixel-write.v1'}])),
 'background.source.generate':{path:backgroundSourcePath,schema:'workflow.background-source-write.v1'},
 'cinematic.source.generate':{path:cinematicSourcePath,schema:'workflow.cinematic-source-write.v1'},
 'prop.generate.base':{path:propBasePath,schema:'workflow.prop-stage.v1'},
 'prop.generate.pixel-source':{path:propBasePath,schema:'workflow.prop-stage.v1'},
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

export function requestLocalJson({port,path,method,body,deadline,signal,responseLimit=65536,portraitRead=false,propBaseReference=false,propAsset=false}){
 return new Promise((resolve,reject)=>{
  const payload=body?Buffer.from(JSON.stringify(body)):null;
  const timeout=portraitRead?undefined:AbortSignal.timeout(deadline);
  const requestSignal=timeout&&signal?AbortSignal.any([signal,timeout]):signal??timeout;
  const connectError=Error('WORKFLOW_PORTRAIT_PDS_CONNECT_TIMEOUT');
  const fail=error=>reject(error===connectError?error:Error('DOCUMENT_WORKER_LOCAL_TRANSPORT'));
  const req=request({hostname:'127.0.0.1',port,path,method,agent:false,signal:requestSignal,
   headers:{Accept:propAsset?'image/png':'application/json',...(payload?{'Content-Type':'application/json','Content-Length':payload.length}:{})}},resp=>{
   const propConflict=propBaseReference&&resp.statusCode===409;
   const contentType=propAsset?/^image\/png(?:\s*;|$)/i:/^application\/json(?:\s*;|$)/i;
   if((resp.statusCode!==200&&!propConflict)||!contentType.test(String(resp.headers['content-type']??''))){resp.destroy();fail();return;}
   let len=0;const chunks=[];
   resp.on('data',part=>{len+=part.length;if(len>(propConflict?1024:responseLimit)){req.destroy();fail();}else chunks.push(part);});
   resp.on('aborted',fail);resp.on('error',fail);
   resp.on('end',()=>{try{
    if(propAsset){resolve(Buffer.concat(chunks));return;}
    const body=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks)));
    if(propConflict){
     if(exact(body,['error'])&&body.error==='WORKFLOW_PROP_REFERENCE_CHANGED')
      reject(Error('WORKFLOW_PROP_REFERENCE_CHANGED'));
     else fail();
    }else resolve(body);
   }catch{fail();}});
  });
  if(portraitRead)armConnectionDeadline(req,deadline,connectError);
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
   return await transport({port:8030,path:routes[command.operation].path+'/read',method:'POST',body:{claim:claimOf(command,claimStamp)},deadline:5000,signal,responseLimit:12*1024*1024,portraitRead:true});
  },
  async publishPortrait(command,claimStamp,basis,signal){
   return await transport({port:8030,path:routes[command.operation].path,method:'POST',
    body:{claim:claimOf(command,claimStamp),basis,artifact:JSON.parse(command.artifact)},deadline:120000,signal});
  },
  readUiAsset(operation,claim,signal){
   return transport({port:8030,path:uiRoutes[operation].path+'/read',method:'POST',body:{claim},deadline:15000,signal});
  },
  publishUiAsset(operation,claim,artifact){
   return transport({port:8030,path:uiRoutes[operation].path,method:'POST',body:artifact?{claim,artifact}:{claim},deadline:120000});
  },
  async readCinematicSource(claim,signal){
   return await transport({port:8030,path:cinematicSourcePath+'/read',method:'POST',body:{claim},deadline:5000,signal});
  },
  async readBackgroundSource(claim,signal){
   return await transport({port:8030,path:backgroundSourcePath+'/read',method:'POST',body:{claim},deadline:5000,signal});
  },
  readBackgroundChain(operation,claim,signal){
   return transport({port:8030,path:chainPath[operation]+'/read',method:'POST',body:{claim},deadline:20000,signal,responseLimit:12*1024*1024});
  },
  publishBackgroundChain(operation,claim,artifact){
   return transport({port:8030,path:chainPath[operation],method:'POST',body:{claim,artifact},deadline:120000});
  },
  reconcileBackgroundChain(operation,claim,signal){
   return transport({port:8030,path:chainPath[operation]+'/reconcile',method:'POST',body:{claim},deadline:10000,signal});
  },
  async readCinematicConcept(signal){
   // The loopback PDS provides the canonical Filex snapshot. The worker
   // scopes the snapshot to the sole registered cinematic design section.
   return await transport({port:8030,path:'/api/concept',method:'GET',deadline:8000,signal,responseLimit:2*1024*1024});
  },
  async publishCinematicSource(claim,artifact){
   return await transport({port:8030,path:cinematicSourcePath,method:'POST',body:{claim,artifact},deadline:120000});
  },
  async publishBackgroundSource(claim,artifact){
   return await transport({port:8030,path:backgroundSourcePath,method:'POST',body:{claim,artifact},deadline:120000});
  },
  async reconcileCinematicSource(claim,signal){
   return await transport({port:8030,path:cinematicSourcePath+'/reconcile',method:'POST',body:{claim},deadline:10000,signal});
  },
  async reconcileBackgroundSource(claim,signal){
   return await transport({port:8030,path:backgroundSourcePath+'/reconcile',method:'POST',body:{claim},deadline:10000,signal});
  },
  async readPropBase(claim,signal,withReference=false){
   return transport({port:8030,path:propBasePath+'/read',method:'POST',body:{claim},deadline:withReference?20000:5000,signal,responseLimit:withReference?12*1024*1024:128*1024,propBaseReference:withReference});
  },
  async fetchPropPixelSourceAsset(claim,signal){
   return transport({port:8030,path:propBasePath+'/asset',method:'POST',body:{claim,purpose:'input',index:null},deadline:20000,signal,responseLimit:8*1024*1024,propAsset:true});
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
