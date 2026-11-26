import assert from 'node:assert/strict';
import test from 'node:test';
import {DatabaseSync} from 'node:sqlite';
import {mkdtempSync,realpathSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {crc32,deflateSync} from 'node:zlib';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {chooseCinematicSequence,executeCinematicSequence,cinematicSequencePorts,sequenceKey,sequenceSchema,sequenceTaskType,sequenceType,validSequencePlayback} from './cinematic-sequence-worker.mjs';
import {createAnimationModelSwitch} from './i2v-model-switch.mjs';

const officialRequestId='request.official.'+'3'.repeat(64);
const taskId='workflow.task.'+sha(canonical([officialRequestId,sequenceKey]));
const resultRef='binari/cinematics/sequences/'+officialRequestId+'/manifest.json',job={officialRequestId,taskId};
const pack=v=>{const b=Buffer.alloc(4);b.writeUInt32BE(v);return b;};
function chunk(type,data){
 const body=Buffer.concat([Buffer.from(type),data]);return Buffer.concat([pack(data.length),body,pack(crc32(body))]);
}
function still(i,{w=128,h=128,rgba=false}={}){
 const head=Buffer.concat([pack(w),pack(h),Buffer.from([8,rgba?6:2,0,0,0])]);
 const stride=w*(rgba?4:3)+1;
 const raw=Buffer.alloc(stride*h);
 // Background stays green; one different native byte per frame. Ancillary PNG
 // content proves that no decoding/re-encoding occurred before publication.
 for(let y=0;y<h;y++)for(let x=1;x<stride;x++)raw[y*stride+x]=(x%3===0?60:x%3===2?182:i);
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',head),
  chunk('tEXt',Buffer.from('Native\0'+i)),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]);
}
const source=still(143);
const playback={durationFrames:120,speedCurve:[{frame:0,rate:0.5},{frame:32,rate:0},{frame:64,rate:1.5}],
 cameraCurve:[{frame:0,zoom:1,x:0.25,y:0.5},{frame:98,zoom:2.15,x:0.9,y:0.25}]};
const input={sourceSha256:sha(source),motionPrompt:'Low torchlight glimmers against the stone.',playback};
const payload=canonical({schemaVersion:sequenceSchema,projectId:'binari',...input});
const raw={width:128,height:128,fps:24,frames:Array.from({length:45},(_,i)=>still(i))};
function fixture(t,{legacy=false}={}){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'cinematic-sequence-stage-')));
 const path=join(root,'plan.sqlite');writeFileSync(path,'',{mode:0o600});
 const db=new DatabaseSync(path);t.after(()=>{db.close();rmSync(root,{recursive:true,force:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,payload,payload_sha256,registered_at,state,updated_at,reason);
  CREATE TABLE workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status,retry_count);`);
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)").run(officialRequestId,'binari',
  legacy?'cinematic.generate':sequenceType,legacy?'ngs.cinematic-generate.v1':sequenceSchema,payload,sha(payload));
 db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,2,NULL,'pending',0)").run(taskId,officialRequestId,
  legacy?'cinematic_render':sequenceKey,legacy?'cinematic.generate.render':sequenceTaskType,'animation',canonical(input));
 const state=()=>db.prepare('SELECT status,result_ref FROM workflow_tasks WHERE task_id=?').get(taskId);
 const f={path,root,db,state,frames:new Map(),invocations:0,released:false,published:[],cleaned:false};
 let pinnedClaim;
 f.ports={
  async read(claim){
   assert.equal(state().status,'running');assert.equal(claim.payloadSha256,sha(payload));
   assert.deepEqual(claim,{...job,projectId:'binari',payloadSha256:sha(payload),claimStamp:claim.claimStamp});
   pinnedClaim=claim;return input;
  },
  async asset(claim){assert.equal(claim,pinnedClaim);return source;},
  async stage(claim,row){
   assert.equal(claim,pinnedClaim);assert.equal(state().status,'running');
   assert.deepEqual(Object.keys(row).sort(),['base64','bytes','index','sha256']);
   assert.deepEqual(Buffer.from(row.base64,'base64'),raw.frames[row.index]);
   assert.equal(row.sha256,sha(raw.frames[row.index]));assert.equal(row.bytes,raw.frames[row.index].length);
   if(f.frames.has(row.index))assert.deepEqual(row,f.frames.get(row.index));else f.frames.set(row.index,row);
   return {ok:true,index:row.index,sha256:row.sha256};
  },
  async publish(claim,manifest){
   assert.equal(claim,pinnedClaim);assert.equal(f.frames.size,45);assert.equal(state().status,'running');
   f.published.push(manifest);return {ok:true,resultRef};
  },
 };
 f.renderer={async render(job){
  assert.ok(f.released);f.invocations++;assert.deepEqual(job.source,source);
  assert.equal(job.motionPrompt,input.motionPrompt);
  assert.ok(!Object.hasOwn(job,'playback'));
  return raw;
 }};
 f.slot=createAnimationModelSwitch({async shutdown(){f.released=true;return true;}},f.renderer);
 f.run=signal=>executeCinematicSequence(db,job,signal??new AbortController().signal,{ports:f.ports,runI2V:job=>f.slot.runI2V(job),
  makeDir:async()=>root,cleanup:async()=>{f.cleaned=true;}});
 return f;
}

test('official v2 uses exact native PNG bytes, native frame order and an editing-only playback manifest, not MP4',async t=>{
 const f=fixture(t);
 assert.deepEqual({...chooseCinematicSequence(f.path)},job);
 const result=await f.run();
 assert.deepEqual(result,{state:'completed',taskId,resultRef,frameCount:45});
 assert.equal(f.invocations,1);assert.equal(f.cleaned,true);
 const [m]=f.published;
 assert.deepEqual(m,{schemaVersion:'ngs.cinematic-sequence-result.v1',projectId:'binari',officialRequestId,sourceSha256:input.sourceSha256,
  width:128,height:128,masterFps:24,frameCount:45,
  frames:raw.frames.map((b,index)=>({index,sha256:sha(b),bytes:b.length})),playback});
 assert.equal(m.playback.durationFrames,120);
 assert.deepEqual([...f.frames.keys()],Array.from({length:45},(_,i)=>i));
 assert.equal(f.state().result_ref,resultRef);assert.equal(f.state().status,'completed');
 assert.equal((await f.run()).state,'stale');assert.equal(chooseCinematicSequence(f.path),null);
});
test('v1 render lane is untouched and v2 refuses incorrect finite ranges, unordered or extra keyframes',async t=>{
 const f=fixture(t,{legacy:true});
 assert.equal(chooseCinematicSequence(f.path),null);
 assert.equal((await f.run()).state,'stale');
 assert.equal(validSequencePlayback(playback),true);
 for(const bad of [{...playback,durationFrames:0},{...playback,durationFrames:1441},{...playback,durationFrames:2}, {...playback,encode:true},
  {...playback,speedCurve:new Array(2)}, {...playback,speedCurve:[{frame:1,rate:1}]}, {...playback,speedCurve:[{frame:0,rate:NaN}]},
  {...playback,speedCurve:[{frame:0,rate:0},{frame:0,rate:1}]}, {...playback,speedCurve:[{frame:0,rate:4.01}]},
  {...playback,cameraCurve:[{frame:0,zoom:3,x:1.01,y:1}]}, {...playback,cameraCurve:[{frame:0,zoom:0.99,x:1,y:1}]}])
  assert.equal(validSequencePlayback(bad),false,JSON.stringify(bad));
});
test('exact active claim can retry a lost frame or publish ACK with identical bytes, without a second Wan render',async t=>{
 const f=fixture(t),stage=f.ports.stage,publish=f.ports.publish;
 let staged=0,finalized=0;
 f.ports.stage=async(...args)=>{const ack=await stage(...args);if(args[1].index===3&&++staged===1)throw Error('lost ACK');return ack;};
 f.ports.publish=async(...args)=>{const ack=await publish(...args);if(++finalized===1)throw Error('lost ACK');return ack;};
 assert.equal((await f.run()).state,'completed');
 assert.equal(f.invocations,1);assert.equal(staged,2);assert.equal(finalized,2);
 assert.deepEqual(f.published[0],f.published[1]);
});
test('wrong result refs, superseded claims, and exhausted publication retries never trigger a new model attempt',async t=>{
 for(const mode of ['after-prefix','wrong-ack','unresolved-publish','known-refusal']){
  const f=fixture(t),stage=f.ports.stage;let publishN=0,stageN=0;
  if(mode==='after-prefix')f.ports.stage=async(...args)=>{
   const ack=await stage(...args);
   if(ack.index===10)f.db.prepare("UPDATE workflow_runs SET reason='withdrawn'").run();
   return ack;
  };
  if(mode==='known-refusal')f.ports.stage=async()=>{stageN++;throw Error('ANIMATION_PDS_HTTP_403');};
  f.ports.publish=async()=>{
   publishN++;
   if(mode==='unresolved-publish')throw Error('lost ACK');
   return {ok:true,resultRef:'unrelated.json'};
  };
  assert.equal((await f.run()).state,'requires_reconciliation',mode);
  assert.equal(f.state().status,'running',mode);
  assert.equal(f.state().result_ref,null,mode);
  assert.equal(chooseCinematicSequence(f.path),null,mode);
  assert.equal((await f.run()).state,'stale',mode);
  assert.equal(f.invocations,1,mode);
  assert.equal(publishN,mode==='after-prefix'||mode==='known-refusal'?0:mode==='wrong-ack'?1:2);
  if(mode==='known-refusal')assert.equal(stageN,1);
 }
});
test('wrong pin, mismatched curve, invalid PNG, and an unknown renderer stop cannot stage any frame',async t=>{
 for(const mode of ['source','curve','frame-count','transparent','stop']){
  const f=fixture(t);
  if(mode==='source')f.ports.asset=async()=>still(144);
  if(mode==='curve')f.ports.read=async()=>({...input,playback:{...playback,durationFrames:300}});
  if(mode==='frame-count')f.renderer.render=async()=>({...raw,frames:raw.frames.slice(1)});
  if(mode==='transparent')f.renderer.render=async()=>({...raw,frames:[...raw.frames.slice(0,-1),still(2,{rgba:true})]});
  if(mode==='stop')f.renderer.render=async()=>{throw Error('MODEL_STOP_UNKNOWN');};
  const result=await f.run();
  assert.equal(result.state,mode==='stop'?'requires_reconciliation':'failed',mode);
  assert.equal(f.frames.size,0);assert.equal(f.published.length,0);assert.equal(f.cleaned,mode!=='stop'&&!['source','curve'].includes(mode));
  if(mode==='stop'){assert.equal(result.rendererStop,'unknown');assert.throws(()=>f.slot.enterWan(),/ANIMATION_MODEL_OWNERSHIP_UNKNOWN/);}
 }
});
test('v2 capability and wire paths cannot invoke the legacy or prop endpoints',async()=>{
 const calls=[];const p=cinematicSequencePorts(async(route,options)=>{calls.push([route,options]);return {enabled:true,schemaVersion:'workflow.cinematic-sequence-write.v1'};});
 assert.equal(await p.ready(),true);
 await p.read(job);await p.asset(job);await p.stage(job,{});await p.publish(job,{});
 assert.deepEqual(calls.map(v=>v[0]),['','/read','/asset','/frame',''].map(s=>'/v1/cinematics/ownership/workflow-sequence'+s));
 assert.ok(calls[2][1].binary);
 assert.ok(calls[1][1].maxJsonBytes>4096);
});

test('the same background request independently renders both pinned originals and publishes distinct group results',async t=>{
 const {db,path,root}=fixture(t);
 const assets={cinematic:source,pixel:still(140)};
 const references=Object.fromEntries(Object.entries(assets).map(([k,v])=>[k,sha(v)]));
 const type='background.animation.workflow',schema='ngs.background-animation-workflow.v1';
 const backgroundSha256=references.pixel;
 const body=canonical({projectId:'binari',schemaVersion:schema,references,backgroundSha256,
  motionPrompt:input.motionPrompt,playback});
 db.prepare('UPDATE workflow_runs SET request_type=?,schema_version=?,payload=?,payload_sha256=?').run(type,schema,body,sha(body));
 db.prepare('DELETE FROM workflow_tasks').run();
 const byTask=new Map();
 for(const [variant,sourceSha256] of Object.entries(references)){
  const key='background_animation_'+variant,id='workflow.task.'+sha(canonical([officialRequestId,key]));
  const wireInput={sourceSha256,playback,motionPrompt:input.motionPrompt};
  db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,2,NULL,'pending',0)").run(id,officialRequestId,key,type,'animation',
   canonical({...wireInput,backgroundSha256,variant}));
  byTask.set(id,{variant,wireInput,id:'request.official.'+sha(canonical([officialRequestId,key]))});
 }
 const rendered=[],published=[];
 for(let i=0;i<2;i++){
  const pick=chooseCinematicSequence(path),job=byTask.get(pick.taskId);
  assert.ok(job);
  const out=await executeCinematicSequence(db,pick,new AbortController().signal,{
   ports:{
    read:async claim=>{assert.equal(claim.officialRequestId,officialRequestId);return job.wireInput;},
    asset:async()=>assets[job.variant],
    stage:async(_claim,f)=>({ok:true,index:f.index,sha256:f.sha256}),
    publish:async(_claim,m)=>{published.push(m);return {ok:true,resultRef:'binari/cinematics/sequences/'+job.id+'/manifest.json'};},
   },
   runI2V:async r=>{assert.equal(r.motionPrompt,input.motionPrompt);rendered.push(sha(r.source));return raw;},
   makeDir:async()=>root,cleanup:async()=>{},
  });
  assert.equal(out.state,'completed');
 }
 assert.deepEqual(new Set(rendered),new Set(Object.values(references)));
 assert.equal(new Set(published.map(m=>m.officialRequestId)).size,2);
 for(const m of published){
  assert.equal(m.group.requestId,officialRequestId);assert.equal(m.group.backgroundSha256,backgroundSha256);
  assert.equal(m.sourceSha256,references[m.group.variant]);assert.deepEqual(m.playback,playback);
 }
 assert.equal(chooseCinematicSequence(path),null);
});
