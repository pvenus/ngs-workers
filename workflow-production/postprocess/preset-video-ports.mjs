/** Fixed claim-bound local PDS ports for native PNG → MP4 and final publication. */
import {request} from 'node:http';

export const motionPath='/v1/motion-presets/workflow-convert-2d';
export const motionFramesSchema='workflow.motion-preset-convert-2d.frames.v1';
export class DefinitePresetWriteRejection extends Error{}
const exact=(v,keys)=>v!==null&&typeof v==='object'&&!Array.isArray(v)&&
 Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
const decoder=new TextDecoder('utf-8',{fatal:true});

export function fixedMotionHttp(path,{body,signal,type,limit,timeout,publish=false}){
 return new Promise((resolve,reject)=>{
  const data=body===undefined?null:Buffer.from(JSON.stringify(body));
  const limiter=AbortSignal.timeout(timeout), scoped=signal?AbortSignal.any([signal,limiter]):limiter;
  const fail=()=>reject(Error('PRESET_2D_PDS_TRANSPORT'));
  const req=request({hostname:'127.0.0.1',port:8030,path,method:data?'POST':'GET',agent:false,signal:scoped,
   headers:{Accept:type,...(data?{'Content-Type':'application/json','Content-Length':data.length}:{})}},rsp=>{
    const rejected=publish&&rsp.statusCode===422;
    const header=String(rsp.headers['content-type']??'').split(';',1)[0];
    const len=rsp.headers['content-length'];
    if((rsp.statusCode!==200&&!rejected)||header!==type||
       (len!==undefined&&(!/^\d{1,12}$/.test(len)||Number(len)>limit))){
     rsp.destroy();fail();return;
    }
    let size=0;
    const chunks=[];
    rsp.on('data',b=>{size+=b.length;if(size>limit){rsp.destroy();fail();}else chunks.push(b);});
    rsp.on('error',fail);rsp.on('aborted',fail);
    rsp.on('end',()=>{
     if(len!==undefined&&Number(len)!==size){fail();return;}
     const bytes=Buffer.concat(chunks);
     if(!rejected){resolve(bytes);return;}
     try{
      const e=JSON.parse(decoder.decode(bytes));
      if(exact(e,['error'])&&e.error==='MOTION_2D_REJECTED'){
       reject(new DefinitePresetWriteRejection(e.error));return;
      }
     }catch{}
     fail();
    });
  });
  req.on('error',fail);req.end(data);
 });
}

export function presetPostprocessPorts({transport=fixedMotionHttp}={}){
 const json=(path,opts)=>transport(path,{...opts,type:'application/json',limit:4096})
  .then(bytes=>JSON.parse(decoder.decode(bytes)));
 return {
  async ready(signal){
   try{
    const value=await json(motionPath+'/frames',{signal,timeout:5000});
    return exact(value,['enabled','schemaVersion'])&&value.enabled===true&&value.schemaVersion===motionFramesSchema;
   }catch{return false;}
  },
  readFrames(claim,signal){
   return transport(motionPath+'/frames/read',{body:{claim},signal,type:'application/octet-stream',
    limit:4+128*1024+64*1024*1024,timeout:120000});
  },
  write(claim,artifact){
   // Once publication is dispatched keep only the bounded local HTTP timeout.
   return json(motionPath,{body:{claim,artifact},timeout:180000,publish:true});
  },
 };
}
