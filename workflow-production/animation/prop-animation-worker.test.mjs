import assert from 'node:assert/strict';
import test from 'node:test';
import {DatabaseSync} from 'node:sqlite';
import {mkdtempSync,realpathSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {crc32,deflateSync} from 'node:zlib';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {buildPropGeneratePlan} from './vendor/prop-composite/prop-generate-plan.mjs';
import {propI2VRGBStage} from './prop-i2v-rgb-stage.mjs';
import {createPropI2VInputFraming} from './prop-i2v-input-framing.mjs';
import {candleChromaProfile,candleChromaPropId,validatePropAnimation} from './vendor/prop-composite/prop-generate-envelope.mjs';
import {choosePropAnimation,executePropAnimation,propAnimationPorts} from './prop-animation-worker.mjs';

const oid='request.official.'+'2'.repeat(64);
const payload={schemaVersion:'ngs.prop-animation-generate.v1',projectId:'binari',propId:'candle-v1',
 source:{revision:1,sha256:'f'.repeat(64)},motionId:'warm-flame',prompt:'Only gentle flame tip motion'};
function pack(n){const b=Buffer.alloc(4);b.writeUInt32BE(n);return b;}
function chunk(k,v){const block=Buffer.concat([Buffer.from(k),v]);return Buffer.concat([pack(v.length),block,pack(crc32(block))]);}
function png(c,w=16,h=16){
 const head=Buffer.concat([pack(w),pack(h),Buffer.from([8,2,0,0,0])]);
 const row=Buffer.alloc(1+3*w);row[1]=c;
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',head),
  chunk('IDAT',deflateSync(Buffer.concat(Array(h).fill(row)))),chunk('IEND',Buffer.alloc(0))]);
}
function fixture(t,{profile}={}){
 const intent=profile?{...payload,propId:candleChromaPropId,renderProfile:profile}:payload;
 const [w,h]=profile?[512,352]:[16,16];
 const root=realpathSync(mkdtempSync(join(tmpdir(),'prop-motion-stage-')));
 const dbPath=join(root,'plan.sqlite');writeFileSync(dbPath,'',{mode:0o600});
 const db=new DatabaseSync(dbPath);t.after(()=>{db.close();rmSync(root,{recursive:true,force:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,payload,payload_sha256,registered_at,state,updated_at,reason);
  CREATE TABLE workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status,retry_count);`);
 const raw=canonical(intent);
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)").run(oid,'binari','prop.animation.generate',intent.schemaVersion,raw,sha(raw));
 const plan=buildPropGeneratePlan({officialRequestId:oid,requestType:'prop.animation.generate',payload:intent});
 for(const task of plan)db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,2,?,'pending',0)")
  .run(task.id,oid,task.key,task.type,task.workerType,canonical(task.input),task.dependsOn);
 const tinfo={taskId:plan[0].id,officialRequestId:oid};
 const source=png(3),sourceSha256=sha(source),output={width:w,height:h,fps:10,frames:[png(4,w,h),png(5,w,h),png(6,w,h)]};
 const received=propI2VRGBStage(output,{...tinfo,propId:intent.propId,sourceSha256});
 const state=()=>db.prepare('SELECT status,result_ref,retry_count FROM workflow_tasks WHERE task_id=?').get(tinfo.taskId);
 const events=[];
 const ports={async read(claim){
  events.push('read');assert.equal(state().status,'running');assert.equal(claim.payloadSha256,sha(raw));
  return {package:intent.source,motionPrompt:intent.prompt,...(profile?{renderProfile:profile}:{}),
    media:{assetId:sourceSha256+'.png',sha256:sourceSha256,bytes:source.length,width:16,height:16,mediaType:'image/png',frameDurationsMs:[]}};
 },async asset(){events.push('asset');return source;},async write(_claim,artifact){
  events.push('write');assert.deepEqual(artifact,received.artifact);
  return {ok:true,resultRef:received.resultRef};
 }};
 const renderer={async render(job){events.push('render');if(!profile)assert.deepEqual(job.source,source);assert.equal(job.motionPrompt,intent.prompt);return output;}};
 const run=(sign=new AbortController().signal,prepareInput)=>executePropAnimation(db,tinfo,sign,{ports,renderer,prepareInput,makeDir:async()=>root,cleanup:async()=>{events.push('cleanup');}});
 return {dbPath,db,tinfo,state,events,ports,renderer,run,plan,received,source,intent};
}
test('exact named request stages authorized RGB once with same-claim proof',async t=>{
 const f=fixture(t);assert.deepEqual({...choosePropAnimation(f.dbPath)},f.tinfo);
 const r=await f.run();assert.equal(r.state,'completed');assert.equal(r.alpha,'none');assert.equal(f.state().result_ref,f.received.resultRef);
 assert.deepEqual(f.events,['read','asset','render','write','cleanup']);assert.equal((await f.run()).state,'stale');
});
test('changed package pin, changed claim, unknown renderer and lost ACK never stage or retry incorrectly',async t=>{
 for(const mode of ['pin','claim','stop','ack']){
  const f=fixture(t);
  if(mode==='pin'){
   const original=f.ports.read;
   f.ports.read=async(...args)=>({...await original(...args),package:{revision:2,sha256:payload.source.sha256}});
  }
  if(mode==='claim'){
   const old=f.renderer.render;
   f.renderer.render=async input=>{const r=await old(input);f.db.prepare("UPDATE workflow_runs SET reason='withdrawn'").run();return r;};
  }
  if(mode==='stop')f.renderer.render=async()=>{throw Error('MODEL_STOP_UNKNOWN');};
  if(mode==='ack')f.ports.write=async()=>{throw Error('ACK_LOST');};
  const r=await f.run();assert.equal(r.state,mode==='pin'?'failed':'requires_reconciliation');
  if(mode==='stop')assert.equal(r.rendererStop,'unknown');
  assert.equal(f.state().status,mode==='pin'?'failed':'running');assert.equal(f.state().result_ref,null);
  assert.equal(f.events.includes('write'),false);
 }
});
test('prop raw rejects unexpected graph or missing capability',async t=>{
 const f=fixture(t);
 f.db.prepare("UPDATE workflow_tasks SET task_input='{}' WHERE task_id=?").run(f.plan[1].id);
 await assert.rejects(f.run(),/PROP_I2V_CLAIM_BINDING/);assert.equal(f.events.length,0);
 let modes=['still'];
 const ports=propAnimationPorts(async(route)=>route.endsWith('stage')?
  {enabled:true,schemaVersion:'workflow.prop-stage.v1'}:{enabled:true,schemaVersion:'workflow.prop-package-write.v1',modes});
 assert.equal(await ports.ready(),false);modes=['still','append-animation'];assert.equal(await ports.ready(),true);
});

test('exact approved profile changes source preparation but not task graph; mismatched PDS profile never renders',async t=>{
 const f=fixture(t,{profile:candleChromaProfile});
 const original={...f.intent};delete original.renderProfile;
 assert.deepEqual(f.plan,buildPropGeneratePlan({officialRequestId:oid,requestType:'prop.animation.generate',payload:original}));
 for(const invalid of [{...f.intent,propId:'a-different-prop'},{...f.intent,renderProfile:'unreviewed'},{...f.intent,renderProfile:null}])
  assert.throws(()=>validatePropAnimation(invalid,'binari'),/NGS_PROP_GENERATE_PAYLOAD_INVALID/);
 const prepare=async job=>{f.events.push('prepare');assert.deepEqual(job.source,f.source);
  assert.equal(job.renderProfile,candleChromaProfile);return png(28,512,352);};
 assert.equal((await f.run(undefined,prepare)).state,'completed');
 assert.deepEqual(f.events,['read','asset','prepare','render','write','cleanup']);
 const swapped=fixture(t,{profile:candleChromaProfile}),old=swapped.ports.read;
 swapped.ports.read=async(...args)=>{const result=await old(...args);delete result.renderProfile;return result;};
 assert.equal((await swapped.run(undefined,prepare)).state,'failed');
 assert.deepEqual(swapped.events,['read']);
});
test('actual Pillow helper is usable from the prop worker scratch pre-render boundary',
 {skip:!process.env.NGS_TEST_PROP_I2V_FRAMER_PYTHON},async t=>{
 const f=fixture(t,{profile:candleChromaProfile});
 const prepare=createPropI2VInputFraming(process.env.NGS_TEST_PROP_I2V_FRAMER_PYTHON);
 const old=f.renderer.render;
 f.renderer.render=async job=>{
  assert.equal(job.source.readUInt32BE(16),512);assert.equal(job.source.readUInt32BE(20),352);
  assert.notEqual(sha(job.source),sha(f.source));return await old(job);
 };
 assert.equal((await f.run(undefined,prepare)).state,'completed');
});
