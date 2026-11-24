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
import {choosePropAnimation,executePropAnimation,propAnimationPorts} from './prop-animation-worker.mjs';

const oid='request.official.'+'2'.repeat(64);
const payload={schemaVersion:'ngs.prop-animation-generate.v1',projectId:'binari',propId:'candle-v1',
 source:{revision:1,sha256:'f'.repeat(64)},motionId:'warm-flame',prompt:'Only gentle flame tip motion'};
function pack(n){const b=Buffer.alloc(4);b.writeUInt32BE(n);return b;}
function chunk(k,v){const block=Buffer.concat([Buffer.from(k),v]);return Buffer.concat([pack(v.length),block,pack(crc32(block))]);}
function png(c){
 const head=Buffer.concat([pack(16),pack(16),Buffer.from([8,2,0,0,0])]);
 const row=Buffer.alloc(49);row[1]=c;
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',head),
  chunk('IDAT',deflateSync(Buffer.concat(Array(16).fill(row)))),chunk('IEND',Buffer.alloc(0))]);
}
function fixture(t){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'prop-motion-stage-')));
 const dbPath=join(root,'plan.sqlite');writeFileSync(dbPath,'',{mode:0o600});
 const db=new DatabaseSync(dbPath);t.after(()=>{db.close();rmSync(root,{recursive:true,force:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,payload,payload_sha256,registered_at,state,updated_at,reason);
  CREATE TABLE workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status,retry_count);`);
 const raw=canonical(payload);
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)").run(oid,'binari','prop.animation.generate',payload.schemaVersion,raw,sha(raw));
 const plan=buildPropGeneratePlan({officialRequestId:oid,requestType:'prop.animation.generate',payload});
 for(const task of plan)db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,2,?,'pending',0)")
  .run(task.id,oid,task.key,task.type,task.workerType,canonical(task.input),task.dependsOn);
 const tinfo={taskId:plan[0].id,officialRequestId:oid};
 const source=png(3),sourceSha256=sha(source),output={width:16,height:16,fps:10,frames:[png(4),png(5),png(6)]};
 const received=propI2VRGBStage(output,{...tinfo,propId:payload.propId,sourceSha256});
 const state=()=>db.prepare('SELECT status,result_ref,retry_count FROM workflow_tasks WHERE task_id=?').get(tinfo.taskId);
 const events=[];
 const ports={async read(claim){
  events.push('read');assert.equal(state().status,'running');assert.equal(claim.payloadSha256,sha(raw));
  return {package:payload.source,motionPrompt:payload.prompt,
    media:{assetId:sourceSha256+'.png',sha256:sourceSha256,bytes:source.length,width:16,height:16,mediaType:'image/png',frameDurationsMs:[]}};
 },async asset(){events.push('asset');return source;},async write(_claim,artifact){
  events.push('write');assert.deepEqual(artifact,received.artifact);
  return {ok:true,resultRef:received.resultRef};
 }};
 const renderer={async render(job){events.push('render');assert.deepEqual(job.source,source);assert.equal(job.motionPrompt,payload.prompt);return output;}};
 const run=(sign=new AbortController().signal)=>executePropAnimation(db,tinfo,sign,{ports,renderer,makeDir:async()=>root,cleanup:async()=>{events.push('cleanup');}});
 return {dbPath,db,tinfo,state,events,ports,renderer,run,plan,received};
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
