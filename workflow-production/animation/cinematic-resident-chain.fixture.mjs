/** Test-only process. Real ports, stores, claim binding and ffmpeg; no model process. */
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {crc32,deflateSync} from 'node:zlib';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {cinematicPorts,chooseCinematic,executeCinematic} from './cinematic-worker.mjs';
import {createAnimationModelSwitch} from './i2v-model-switch.mjs';
const [services,dbPath,inboxDir,origin,sourceSha256]=process.argv.slice(2);
assert.match(origin,/^http:\/\/127\.0\.0\.1:\d{1,5}$/);
assert.match(sourceSha256,/^[a-f0-9]{64}$/);
const imported=path=>import(pathToFileURL(join(services,path)));
const {createRequestService,host}=await imported('request-service/request-service.mjs');
const {PlannerStore,pollOnce}=await imported('workflow-planner/planner.mjs');

const signal=AbortSignal.timeout(15_000);
const callPds=async(path,{body,binary}={})=>{
 const r=await fetch(origin+path,{signal,redirect:'error',
  ...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
 assert.equal(r.status,200,path+' '+await (r.status===200?'':r.text()));
 return binary?Buffer.from(await r.arrayBuffer()):await r.json();
};
const prefix='/v1/cinematics/ownership/workflow';
const ports=cinematicPorts(callPds);
const server=createRequestService({directory:inboxDir,cinematicEnabled:true,
 cinematicReadiness:()=>ports.ready(signal),cinematicSourceReadiness:async source=>
  (await callPds(prefix+'/source-status',{body:{sourceSha256:source}})).ok});
await new Promise(resolve=>server.listen(0,host,resolve));
const rs={async call(name,args){
 const response=await fetch('http://'+host+':'+server.address().port+'/mcp',{method:'POST',signal,
  headers:{'Content-Type':'application/json','Accept':'application/json, text/event-stream'},
  body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name,arguments:args}})});
 const result=(await response.json()).result;
 assert.equal(result?.isError,false,name+': '+JSON.stringify(result));
 return result.structuredContent;
}};
const readCinematicPublication=({officialRequestId})=>callPds(prefix+'/result',{body:{officialRequestId}});
const planner=new PlannerStore(dbPath,{cinematicEnabled:true,readCinematicPublication});
const db=new DatabaseSync(dbPath);db.exec('PRAGMA busy_timeout=2000');
const encodeU32=n=>{const b=Buffer.alloc(4);b.writeUInt32BE(n);return b;};
const chunk=(tag,payload)=>{
 const inner=Buffer.concat([Buffer.from(tag),payload]);return Buffer.concat([encodeU32(payload.length),inner,encodeU32(crc32(inner))]);
};
function rgbFrame(index){
 const w=128,h=128,head=Buffer.concat([encodeU32(w),encodeU32(h),Buffer.from([8,2,0,0,0])]),raw=Buffer.alloc(h*(w*3+1));
 for(let y=0;y<h;y++)for(let x=0;x<w;x++){
  const i=y*(w*3+1)+1+x*3;raw[i]=y<48?80:140;raw[i+1]=190;raw[i+2]=x===index?200:40;
 }
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',head),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]);
}

try{
 const payload={schemaVersion:'ngs.cinematic-generate.v1',projectId:'binari',sourceSha256,motionPrompt:'A slow reveal from the ritual room.'};
 const accepted=await rs.call('request_submit',{sourceId:'local',requestType:'cinematic.generate',idempotencyKey:'fixture-cinematic',payload});
 const {officialRequestId}=accepted;
 assert.equal(accepted.status,'registered');
 await pollOnce(planner,rs);
 const task=chooseCinematic(dbPath);
 assert.equal(task.officialRequestId,officialRequestId);
 assert.equal(task.taskId,'workflow.task.'+sha(canonical([officialRequestId,'cinematic_render'])));
 let unloaded=false,invocations=0;
 const slot=createAnimationModelSwitch({async shutdown(){unloaded=true;return true;}},{async render(job){
  assert.ok(unloaded);invocations++;assert.equal(sha(job.source),sourceSha256);assert.equal(job.motionPrompt,payload.motionPrompt);
  return {width:128,height:128,fps:24,frames:Array.from({length:45},(_,i)=>rgbFrame(i))};
 }});
 assert.equal(await ports.ready(signal),true);
 const worker=await executeCinematic(db,task,signal,{ports,runI2V:job=>slot.runI2V(job)});
 assert.equal(worker.state,'completed');assert.equal(invocations,1);
 assert.equal(chooseCinematic(dbPath),null);
 const completed=await pollOnce(planner,rs);assert.equal(completed.completed,1);
 const proof=await readCinematicPublication({officialRequestId});
 assert.equal(proof.manifest.sourceSha256,sourceSha256);
 assert.equal(proof.resultRef,worker.resultRef);
 console.log(JSON.stringify({officialRequestId,artifactSha256:proof.manifest.sha256}));
}finally{
 db.close();planner.close();await new Promise(resolve=>server.close(resolve));
}
