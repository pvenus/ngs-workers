import {request} from 'node:http';
import {exact} from './vendor/planner/planner.mjs';
import {AudioDefinitelyRejected} from './audio-worker.mjs';

export const audioPath='/v1/audio/ownership/workflow-generate';
export const capabilitySchema='workflow.audio-generate-write.v1';
export const rejectBeforeWrite='WORKFLOW_AUDIO_REJECTED_BEFORE_WRITE';
const heartbeatPath='/api/service-heartbeat';

export function requestLocalJson({port,path,method,body,deadline,signal}){
 return new Promise((resolve,reject)=>{
  const payload=body?Buffer.from(JSON.stringify(body)):null;
  const timeout=AbortSignal.timeout(deadline);
  const active=signal?AbortSignal.any([signal,timeout]):timeout;
  const fail=()=>reject(Error('AUDIO_LOCAL_TRANSPORT'));
  const req=request({hostname:'127.0.0.1',port,path,method,agent:false,signal:active,
   headers:{Accept:'application/json',...(payload?{'Content-Type':'application/json','Content-Length':payload.length}:{})}},resp=>{
   if(!/^application\/json(?:\s*;|$)/i.test(String(resp.headers['content-type']??''))){resp.destroy();fail();return;}
   let bytes=0;const chunks=[];
   resp.on('data',part=>{bytes+=part.length;if(bytes>2*1024*1024){req.destroy();fail();}else chunks.push(part);});
   resp.on('aborted',fail);resp.on('error',fail);
   resp.on('end',()=>{try{
    const value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks)));
    resolve({status:resp.statusCode,value});
   }catch{fail();}});
  });
  req.on('error',fail);req.end(payload);
 });
}

export function audioPorts({transport=requestLocalJson}={}){
 const call=(path,method,options={})=>transport({port:8030,path,method,deadline:5000,...options});
 return {
  async capability(signal){
   try{
    const {status,value}=await call(audioPath,'GET',{signal});
    return status===200&&exact(value,['enabled','schemaVersion'])&&value.enabled===true&&value.schemaVersion===capabilitySchema;
   }catch{return false;}
  },
  async heartbeat(signal){
   try{
    const {status,value}=await call(heartbeatPath,'POST',{port:8090,body:{serverName:'audio-worker'},signal});
    return status===200&&exact(value,['ok'])&&value.ok===true;
   }catch{return false;}
  },
  async read(claim,{signal}={}){
   const {status,value}=await call(audioPath+'/read','POST',{deadline:30000,body:{claim},signal});
   if(status!==200)throw Error('AUDIO_MASTER_SOURCE_UNAVAILABLE');
   return value;
  },
  async publish(claim,source,artifact){
   const {status,value}=await call(audioPath,'POST',{deadline:120000,body:{claim,source,artifact}});
   if(status===422&&exact(value,['error'])&&value.error===rejectBeforeWrite)throw new AudioDefinitelyRejected(rejectBeforeWrite);
   if(status!==200)throw Error('AUDIO_PUBLICATION_UNCERTAIN');
   return value;
  },
 };
}
