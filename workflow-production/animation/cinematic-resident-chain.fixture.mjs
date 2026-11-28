/** Test-only process. Real ports, stores, claim binding and ffmpeg; no model process. */
import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {DatabaseSync} from 'node:sqlite';
import {crc32,deflateSync} from 'node:zlib';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {cinematicPorts,chooseCinematic,executeCinematic} from './cinematic-worker.mjs';
import {cinematicSequencePorts,chooseCinematicSequence,executeCinematicSequence} from './cinematic-sequence-worker.mjs';
import {createAnimationModelSwitch} from './i2v-model-switch.mjs';
const [services,dbPath,inboxDir,origin,sourceSha256,kind='mp4']=process.argv.slice(2);
const derive=kind==='upscale';
const named=kind==='named'||derive;
const sequence=kind==='sequence'||named;
assert.ok(sequence||kind==='mp4');
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
const prefix='/v1/cinematics/ownership/workflow'+(sequence?'-sequence':'');
const ports=(sequence?cinematicSequencePorts:cinematicPorts)(callPds);
const ready=()=>ports.ready(signal),sourceReady=async source=>(await callPds(prefix+'/source-status',{body:{sourceSha256:source}})).ok;
const server=createRequestService({directory:inboxDir,cinematicEnabled:true,
 ...(sequence?{cinematicSequenceReadiness:ready,cinematicSequenceSourceReadiness:sourceReady,cinematicOperationReadiness:async type=>type==='cinematic.sequence.upscale'&&(await callPds(prefix+'/upscale')).enabled===true}:
 {cinematicReadiness:ready,cinematicSourceReadiness:sourceReady})});
await new Promise(resolve=>server.listen(0,host,resolve));
const rs={async call(name,args){
 const response=await fetch('http://'+host+':'+server.address().port+'/mcp',{method:'POST',signal,
  headers:{'Content-Type':'application/json','Accept':'application/json, text/event-stream'},
  body:JSON.stringify({jsonrpc:'2.0',id:1,method:'tools/call',params:{name,arguments:args}})});
 const result=(await response.json()).result;
 assert.equal(result?.isError,false,name+': '+JSON.stringify(result));
 return result.structuredContent;
}};
const readCinematicPublication=({officialRequestId,family})=>callPds(prefix+(family==='cinematic.sequence.upscale'?'/upscale':'')+'/result',{body:{officialRequestId}});
const planner=new PlannerStore(dbPath,{cinematicEnabled:true,readCinematicPublication});
const db=new DatabaseSync(dbPath);db.exec('PRAGMA busy_timeout=2000');
const encodeU32=n=>{const b=Buffer.alloc(4);b.writeUInt32BE(n);return b;};
const chunk=(tag,payload)=>{
 const inner=Buffer.concat([Buffer.from(tag),payload]);return Buffer.concat([encodeU32(payload.length),inner,encodeU32(crc32(inner))]);
};
function rgbFrame(index,w=sequence?512:128,h=sequence?256:128){
 const head=Buffer.concat([encodeU32(w),encodeU32(h),Buffer.from([8,2,0,0,0])]),raw=Buffer.alloc(h*(w*3+1));
 for(let y=0;y<h;y++)for(let x=0;x<w;x++){
  const i=y*(w*3+1)+1+x*3;raw[i]=y<48?80:140;raw[i+1]=190;raw[i+2]=x===index?200:40;
 }
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',head),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]);
}

try{
 const playback=named?{durationFrames:45,speedCurve:[{frame:0,rate:1}],cameraCurve:[{frame:0,zoom:1,x:.5,y:.5}]}:{durationFrames:75,speedCurve:[{frame:0,rate:0},{frame:64,rate:1.35}],
  cameraCurve:[{frame:0,zoom:1,x:0.5,y:0.5},{frame:72,zoom:1.2,x:0.55,y:0.65}]};
 const hashes=[];
 const payload={schemaVersion:named?'ngs.cinematic-sequence-generate.v2':sequence?'ngs.cinematic-sequence-generate.v1':'ngs.cinematic-generate.v1',projectId:'binari',
  sourceSha256,motionPrompt:'A slow reveal from the ritual room.',...(named?{title:'처마 너머 먼 산'}:sequence?{playback}:{})};
 const accepted=await rs.call('request_submit',{sourceId:'local',requestType:sequence?'cinematic.sequence.generate':'cinematic.generate',
  idempotencyKey:'fixture-cinematic',payload});
 const {officialRequestId}=accepted;
 assert.equal(accepted.status,'registered');
 await pollOnce(planner,rs);
 const select=sequence?chooseCinematicSequence:chooseCinematic,execute=sequence?executeCinematicSequence:executeCinematic;
 const task=select(dbPath);
 assert.equal(task.officialRequestId,officialRequestId);
 assert.equal(task.taskId,'workflow.task.'+sha(canonical([officialRequestId,sequence?'cinematic_sequence_render':'cinematic_render'])));
 let unloaded=false,invocations=0;
 const slot=createAnimationModelSwitch({async shutdown(){unloaded=true;return true;}},{async render(job){
  assert.ok(unloaded);invocations++;assert.equal(sha(job.source),sourceSha256);assert.equal(job.motionPrompt,payload.motionPrompt);
  const frames=Array.from({length:45},(_,i)=>rgbFrame(i));
  hashes.push(...frames.map(sha));
  assert.equal(job.framing,sequence?'cinematic-wide-2x1':undefined);
  return {width:sequence?512:128,height:sequence?256:128,fps:24,frames};
 }});
 assert.equal(await ports.ready(signal),true);
 const worker=await execute(db,task,signal,{ports,runI2V:job=>slot.runI2V(job)});
 assert.equal(worker.state,'completed');assert.equal(invocations,1);
 assert.equal(select(dbPath),null);
 const completed=await pollOnce(planner,rs);assert.equal(completed.completed,1);
 const proof=await readCinematicPublication({officialRequestId});
 assert.equal(proof.manifest.sourceSha256,sourceSha256);
 if(named)assert.equal(proof.manifest.title,'처마 너머 먼 산');
 assert.equal(proof.resultRef,worker.resultRef);
 let upscale;
 if(derive){
  const original=proof.manifest;
  const requestType='cinematic.sequence.upscale';
  const payload={schemaVersion:'ngs.cinematic-sequence-upscale.v1',projectId:'binari',sequenceId:officialRequestId,
   expectedRevision:0,sourceSha256,playback};
  const accepted=await rs.call('request_submit',{sourceId:'local',requestType,idempotencyKey:'fixture-upscale',payload});
  assert.equal(accepted.status,'registered');
  await pollOnce(planner,rs);
  assert.equal(chooseCinematicSequence(dbPath),null);
  const task=chooseCinematicSequence(dbPath,{allowUpscale:true});
  assert.equal(task.officialRequestId,accepted.officialRequestId);
  let invoked=0;
  const worker=await executeCinematicSequence(db,task,signal,{ports,
   runI2V:()=>assert.fail('upscale must not rerun Wan'),
   async runUpscale({output}){
    invoked++;assert.deepEqual([output.width,output.height],[512,256]);
    assert.deepEqual(output.frames.map(sha),hashes);
    return {width:1024,height:512,fps:24,frames:Array.from({length:45},(_,i)=>rgbFrame(i,1024,512))};
   }});
  assert.equal(invoked,1);
  assert.equal(worker.state,'handed_off',JSON.stringify(worker));
  const completion=await pollOnce(planner,rs);assert.equal(completion.completed,1);
  const derived=await readCinematicPublication({officialRequestId:task.officialRequestId,family:requestType});
  assert.equal(derived.manifest.upscale.sourceSequenceId,officialRequestId);
  assert.equal(derived.manifest.title,original.title);
  assert.deepEqual([derived.manifest.width,derived.manifest.height],[1024,512]);
  assert.notEqual(worker.resultRef,proof.resultRef);
  assert.deepEqual((await readCinematicPublication({officialRequestId})).manifest,original);
  upscale={id:task.officialRequestId,hashes:derived.manifest.frames.map(f=>f.sha256)};
 }
 console.log(JSON.stringify({officialRequestId,...(sequence?{hashes,playback,upscale}:{artifactSha256:proof.manifest.sha256})}));
}finally{
 db.close();planner.close();await new Promise(resolve=>server.close(resolve));
}
