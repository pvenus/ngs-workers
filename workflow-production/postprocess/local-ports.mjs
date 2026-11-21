import {request} from 'node:http';
import {exact} from './vendor/planner-f24d23a/planner.mjs';

export const deletePath='/v1/npcs/ownership/workflow-delete';
export const heartbeatPath='/api/service-heartbeat';
const receiptSchema='workflow.pds-delete-receipt.v1';

export function requestLocalJson({port,path,method,body,deadline,signal}){
 return new Promise((resolve,reject)=>{
  const payload=body?Buffer.from(JSON.stringify(body)):null;
  const signals=[AbortSignal.timeout(deadline)];
  if(signal)signals.unshift(signal);
  const fail=()=>reject(Error('FILE_WORKER_LOCAL_TRANSPORT'));
  const req=request({hostname:'127.0.0.1',port,path,method,agent:false,signal:AbortSignal.any(signals),
   headers:{Accept:'application/json',...(payload?{'Content-Type':'application/json','Content-Length':payload.length}:{})}},resp=>{
   if(resp.statusCode!==200||!/^application\/json(?:\s*;|$)/i.test(String(resp.headers['content-type']??''))){resp.destroy();fail();return;}
   let len=0;const chunks=[];
   resp.on('data',part=>{len+=part.length;if(len>65536){req.destroy();fail();}else chunks.push(part);});
   resp.on('aborted',fail);resp.on('error',fail);
   resp.on('end',()=>{try{resolve(JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks))));}catch{fail();}});
  });
  req.on('error',fail);req.end(payload);
 });
}

export function localPorts({transport=requestLocalJson}={}){
 return {
  async capability(signal){
   try{
    const data=await transport({port:8030,path:deletePath,method:'GET',deadline:5000,signal});
    return exact(data,['enabled','schemaVersion'])&&data.enabled===true&&data.schemaVersion===receiptSchema;
   }catch{return false;}
  },
  async heartbeat(signal){
   try{
    const data=await transport({port:8090,path:heartbeatPath,method:'POST',body:{serverName:'file-worker'},deadline:5000,signal});
    return exact(data,['ok'])&&data.ok===true;
   }catch{return false;}
  },
  async deleteExactNpcFolder(command,claimStamp){
   if(!Number.isSafeInteger(claimStamp)||claimStamp<0)throw Error('WORKFLOW_DELETE_CLAIM_STAMP_INVALID');
   const {officialRequestId,taskId,projectId,npcId,payloadSha256}=command;
   return await transport({port:8030,path:deletePath,method:'POST',
    body:{claim:{officialRequestId,taskId,projectId,npcId,payloadSha256,claimStamp}},deadline:120000});
  },
 };
}
