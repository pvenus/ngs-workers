import {request} from 'node:http';
import {exact} from './vendor/planner/planner.mjs';
import {maxAudioBytes} from './audio-wav.mjs';
import {AudioDefinitelyRejected} from './audio-worker.mjs';

export const audioPath='/v1/audio/ownership/workflow-generate';
export const capabilitySchema='workflow.audio-generate-write.v2';
export const rejectBeforeWrite='WORKFLOW_AUDIO_REJECTED_BEFORE_WRITE';
export const separatePath='/v1/audio/ownership/workflow-separate';
export const separateCapabilitySchema='workflow.audio-separate-write.v2';
const heartbeatPath='/api/service-heartbeat';

export function requestLocalJson({port,path,method,body,deadline,signal,binary=false}){
 return new Promise((resolve,reject)=>{
  const payload=body?Buffer.from(JSON.stringify(body)):null;
  const timeout=AbortSignal.timeout(deadline);
  const active=signal?AbortSignal.any([signal,timeout]):timeout;
  const fail=()=>reject(Error('AUDIO_LOCAL_TRANSPORT'));
  const req=request({hostname:'127.0.0.1',port,path,method,agent:false,signal:active,
   headers:{Accept:binary?'audio/wav':'application/json',...(payload?{'Content-Type':'application/json','Content-Length':payload.length}:{})}},resp=>{
   if(binary?(resp.statusCode!==200||resp.headers['content-type']!=='audio/wav'):
      !/^application\/json(?:\s*;|$)/i.test(String(resp.headers['content-type']??''))){resp.destroy();fail();return;}
   let bytes=0;const chunks=[];
   resp.on('data',part=>{bytes+=part.length;if(bytes>(binary?maxAudioBytes:2*1024*1024)){req.destroy();fail();}else chunks.push(part);});
   resp.on('aborted',fail);resp.on('error',fail);
   resp.on('end',()=>{try{
    if(binary){resolve({status:resp.statusCode,value:Buffer.concat(chunks)});return;}
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
  async capabilitySeparate(signal){
   try{
    const {status,value}=await call(separatePath,'GET',{signal});
    return status===200&&exact(value,['enabled','schemaVersion'])&&value.enabled===true&&value.schemaVersion===separateCapabilitySchema;
   }catch{return false;}
  },
  async readSeparate(claim,{signal}={}){
   const {status,value}=await call(separatePath+'/read','POST',{body:{claim},signal});
   if(status!==200||!exact(value,['sourceOfficialRequestId','sourceSha256','bytes','mimeType'])||
      value.mimeType!=='audio/wav'||!Number.isSafeInteger(value.bytes)||value.bytes<44||value.bytes>maxAudioBytes)
    throw Error('AUDIO_SEPARATION_SOURCE_UNAVAILABLE');
   const asset=await call(separatePath+'/asset','POST',{body:{claim},signal,binary:true,deadline:30000});
   if(asset.status!==200||!Buffer.isBuffer(asset.value)||asset.value.length!==value.bytes)throw Error('AUDIO_SEPARATION_SOURCE_UNAVAILABLE');
   return {...value,wav:asset.value};
  },
  async publishSeparate(claim,artifact){
   const {status,value}=await call(separatePath,'POST',{deadline:120000,body:{claim,artifact}});
   if(status===422&&exact(value,['error'])&&value.error===rejectBeforeWrite)throw new AudioDefinitelyRejected(rejectBeforeWrite);
   if(status!==200)throw Error('AUDIO_PUBLICATION_UNCERTAIN');
   return value;
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
