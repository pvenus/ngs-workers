import assert from 'node:assert/strict';
import test from 'node:test';
import {DatabaseSync} from 'node:sqlite';
import {execFileSync} from 'node:child_process';
import {mkdtempSync,realpathSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {crc32,deflateSync} from 'node:zlib';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {chooseCinematic,executeCinematic,cinematicPorts} from './cinematic-worker.mjs';
import {createAnimationModelSwitch} from './i2v-model-switch.mjs';

const oid='request.official.'+'2'.repeat(64), taskId='workflow.task.'+sha(canonical([oid,'cinematic_render']));
const resultRef='binari/cinematics/results/'+oid+'.json';
const tinfo={taskId,officialRequestId:oid};
const width=128,height=128,fps=24,frameCount=45;
function pack(n){const b=Buffer.alloc(4);b.writeUInt32BE(n);return b;}
function chunk(k,v){const block=Buffer.concat([Buffer.from(k),v]);return Buffer.concat([pack(v.length),block,pack(crc32(block))]);}
function still(red){
 const head=Buffer.concat([pack(width),pack(height),Buffer.from([8,2,0,0,0])]);
 const raw=Buffer.alloc((width*3+1)*height);
 // An unmistakably colored, full-bleed background lets the production encoder path
 // detect accidental black/transparent replacement, not just a valid MP4 header.
 for(let y=0;y<height;y++)for(let x=0;x<width;x++){
  const i=y*(width*3+1)+1+x*3;
  raw[i]=x>40&&x<88&&y>30&&y<100?red:30;raw[i+1]=180;raw[i+2]=70;
 }
 return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',head),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]);
}
const source=still(90), sourceSha256=sha(source), motionPrompt='The room brightens as the figure opens the jacket.';
const raw={width,height,fps,frames:Array.from({length:frameCount},(_,i)=>still(i*5))};
const payload=canonical({schemaVersion:'ngs.cinematic-generate.v1',projectId:'binari',sourceSha256,motionPrompt});
const taskInput=canonical({sourceSha256,motionPrompt});
function fixture(t){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'cinematic-render-stage-')));
 const dbPath=join(root,'plan.sqlite');writeFileSync(dbPath,'',{mode:0o600});
 const db=new DatabaseSync(dbPath);
 t.after(()=>{db.close();rmSync(root,{recursive:true,force:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id,project_id,request_type,schema_version,payload,payload_sha256,registered_at,state,updated_at,reason);
  CREATE TABLE workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status,retry_count);`);
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)").run(oid,'binari','cinematic.generate','ngs.cinematic-generate.v1',payload,sha(payload));
 db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,2,NULL,'pending',0)")
  .run(taskId,oid,'cinematic_render','cinematic.generate.render','animation',taskInput);
 const state=()=>db.prepare('SELECT status,result_ref,retry_count FROM workflow_tasks WHERE task_id=?').get(taskId);
 const events=[], artifacts=[];
 const ports={async read(claim){
  events.push('read');assert.equal(state().status,'running');assert.equal(claim.payloadSha256,sha(payload));
  assert.equal(claim.projectId,'binari');assert.equal(claim.officialRequestId,oid);assert.equal(claim.taskId,taskId);
  return {sourceSha256,motionPrompt};
 },async asset(){events.push('asset');return source;},async publish(_claim,artifact){
  events.push('publish');artifacts.push(artifact);assert.equal(state().status,'running');
  return {ok:true,resultRef};
 }};
 const model={async render(job){events.push('model');assert.deepEqual(job.source,source);assert.equal(job.motionPrompt,motionPrompt);return raw;}};
 const wan={async shutdown(){events.push('release');return true;}};
 const slot=createAnimationModelSwitch(wan,model);
 const bytes=Buffer.concat([pack(16),Buffer.from('ftyp'),Buffer.alloc(20)]);
 const f={root,dbPath,db,state,events,artifacts,ports,model,wan,slot,encode:async(value)=>{
  events.push('encode');assert.equal(value,raw);return {bytes,sha256:sha(bytes),width,height,frameCount,fps,mimeType:'video/mp4'};
 }};
 f.run=(signal=new AbortController().signal)=>executeCinematic(db,tinfo,signal,{ports,runI2V:job=>slot.runI2V(job),encode:f.encode});
 return f;
}
test('the managed lane preserves full RGB, unloads the old model, publishes exactly once, and completes only on ACK',async t=>{
 const f=fixture(t);assert.deepEqual({...chooseCinematic(f.dbPath)},tinfo);
 const r=await f.run();
 assert.deepEqual(r,{state:'completed',taskId,resultRef,frameCount});
 assert.equal(f.state().result_ref,resultRef);assert.equal(f.state().status,'completed');
 assert.deepEqual(f.events,['read','asset','release','model','encode','publish']);
 const artifact=f.artifacts[0], bytes=Buffer.from(artifact.base64,'base64');
 assert.deepEqual(artifact,{sourceSha256,sha256:sha(bytes),bytes:bytes.length,mimeType:'video/mp4',width,height,fps,frameCount,base64:bytes.toString('base64')});
 assert.equal(chooseCinematic(f.dbPath),null);assert.equal((await f.run()).state,'stale');
});
test('aborted, wrong source, mutated plan, and renderer failures are not published',async t=>{
 for(const mode of ['cancelled','different-png','withdrawn','unsafe-stop','known-stop','encoder']){
  const f=fixture(t);
  if(mode==='different-png')f.ports.asset=async()=>still(0);
  if(mode==='withdrawn')f.model.render=async()=>{f.db.prepare("UPDATE workflow_runs SET reason='cancelled'").run();return raw;};
  if(mode.endsWith('stop'))f.model.render=async()=>{throw Object.assign(Error('MODEL_RENDER_FAILED'),{rendererJoined:mode==='known-stop'});};
  if(mode==='encoder')f.encode=async()=>{throw Error('CUTSCENE_ENCODER_FAILED');};
  const result=await f.run(mode==='cancelled'?AbortSignal.abort():undefined);
  const blocked=['withdrawn','unsafe-stop'].includes(mode);
  assert.equal(result.state,mode==='cancelled'?'stale':blocked?'requires_reconciliation':'failed',mode);
  assert.equal(f.state().status,mode==='cancelled'?'pending':blocked?'running':'failed',mode);
  assert.equal(f.artifacts.length,0,mode);
  if(mode==='different-png')assert.ok(!f.events.includes('model'));
  if(mode==='unsafe-stop'){
   assert.equal(result.rendererStop,'unknown');assert.throws(()=>f.slot.enterWan(),/ANIMATION_MODEL_OWNERSHIP_UNKNOWN/);
  }
 }
});
test('unknown or incorrect publish acknowledgement and a superseded claim never re-queue a published job',async t=>{
 for(const mode of ['lost','wrong-reference','superseded']){
  const f=fixture(t);
  f.ports.publish=async()=>{
   f.events.push('publish');
   if(mode==='superseded')f.db.prepare("UPDATE workflow_tasks SET updated_at=updated_at+1").run();
   if(mode==='lost')throw Error('ACK_LOST');
   return {ok:true,resultRef:mode==='wrong-reference'?resultRef+'x':resultRef};
  };
  const result=await f.run();
  assert.equal(result.state,'requires_reconciliation');
  assert.equal(f.state().status,'running');assert.equal(f.state().result_ref,null);
  assert.equal(chooseCinematic(f.dbPath),null);assert.equal((await f.run()).state,'stale');
  assert.equal(f.events.filter(x=>x==='publish').length,1);
 }
});
test('malformed exact graph cannot launch a model; runtime transport uses only the scoped PDS URLs',async t=>{
 const f=fixture(t);
 f.db.prepare("UPDATE workflow_tasks SET task_input='{}' WHERE task_id=?").run(taskId);
 await assert.rejects(f.run(),/CINEMATIC_CLAIM_BINDING/);assert.equal(f.events.length,0);assert.equal(f.state().status,'pending');
 const calls=[];
 let version='unsupported';
 const port=cinematicPorts(async(route,options)=>{
  calls.push({route,options});
  return {enabled:true,schemaVersion:version};
 });
 assert.equal(await port.ready(),false);version='workflow.cinematic-write.v1';assert.equal(await port.ready(),true);
 await port.read(tinfo);await port.asset(tinfo);await port.publish(tinfo,{});
 assert.deepEqual(calls.map(c=>c.route),['','', '/read','/asset',''].map(s=>'/v1/cinematics/ownership/workflow'+s));
 assert.equal(calls[3].options.binary,true);
});
test('real encoder through the claim lane delivers all 45 frames, preserves background color and encodes without audio',
 {skip:process.env.NGS_TEST_CUTSCENE_ENCODER!=='1'},async t=>{
 const f=fixture(t);f.encode=undefined;
 assert.equal((await f.run()).state,'completed');
 const a=f.artifacts[0], out=join(f.root,'published.mp4');
 writeFileSync(out,Buffer.from(a.base64,'base64'));
 const frame=execFileSync('/opt/homebrew/bin/ffmpeg',['-nostdin','-v','error','-i',out,'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-']);
 assert.equal(frame.length,width*height*3);
 // Preserve the green corner through YUV downsampling within a rounding tolerance.
 assert.ok(frame[0]<45&&frame[1]>160&&frame[2]>55&&frame[2]<85,frame.subarray(0,3).join(','));
 assert.equal(a.frameCount,45);assert.equal(a.fps,24);
});
