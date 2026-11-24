import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {DatabaseSync} from 'node:sqlite';
import {realpathSync,mkdtempSync,chmodSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {deflateSync,crc32} from 'node:zlib';
import {chooseI2V,claimI2V,executeI2V,i2vTaskId,i2vResultRef,i2vType,i2vSchema,i2vKey,i2vPorts,i2vPath} from './i2v-worker.mjs';
import {createAnimationModelSwitch} from './i2v-model-switch.mjs';

const sha=b=>createHash('sha256').update(b).digest('hex');
function makePng({size=16,color=2,badFilter=false,trns=false}={}){
 const m=Buffer.from('89504e470d0a1a0a','hex');
 const chunk=(name,buffer)=>{
  const body=Buffer.concat([Buffer.from(name),buffer]),length=Buffer.alloc(4),crc=Buffer.alloc(4);
  length.writeUInt32BE(buffer.length);crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length,body,crc]);
 };
 const h=Buffer.alloc(13);h.writeUInt32BE(size);h.writeUInt32BE(size,4);h[8]=8;h[9]=color;
 const b=Buffer.alloc((1+size*(color===2?3:4))*size);if(badFilter)b[0]=5;
 return Buffer.concat([m,chunk('IHDR',h),...(trns?[chunk('tRNS',Buffer.alloc(6))]:[]),
  chunk('IDAT',deflateSync(b)),chunk('IEND',Buffer.alloc(0))]);
}
const source=makePng({color:6}),sourceSha256=sha(source),assetId=sourceSha256+'.png';
const hookId='request.official.'+'a'.repeat(64),taskId=i2vTaskId(hookId);
const motionPrompt='불꽃이 제자리에서 잔잔하게 흔들립니다.';
const payload={schemaVersion:i2vSchema,projectId:'binari',sourceRef:{assetId,sha256:sourceSha256},motionPrompt};
const ref=i2vResultRef(hookId),out={frames:[makePng(),makePng()],fps:24,width:16,height:16};
const signal=()=>new AbortController().signal;
function fixture(t,{input={sourceRef:payload.sourceRef,motionPrompt},status='pending',retry=0,
 age=0,parent=null,digest=sha(JSON.stringify(payload))}={}){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'test-i2v-'))),path=join(dir,'test.sqlite');
 const db=new DatabaseSync(path);
 chmodSync(path,0o600);
 db.exec(`CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,request_type TEXT,schema_version TEXT,project_id TEXT,
 state TEXT,reason TEXT,payload TEXT,payload_sha256 TEXT,registered_at INTEGER);
 CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_key TEXT,task_type TEXT,
 worker_type TEXT,status TEXT,updated_at INTEGER,retry_count INTEGER,result_ref TEXT,task_input TEXT,depends_on_task_id TEXT)`);
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?, 'planned',NULL,?,?,0)").run(hookId,i2vType,i2vSchema,'binari',JSON.stringify(payload),digest);
 db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?, 'animation',?,?,?,NULL,?,?)").run(taskId,hookId,i2vKey,i2vType,status,Date.now()-age,retry,JSON.stringify(input),parent);
 t.after(()=>{db.close();rmSync(dir,{recursive:true,force:true});});
 return {db,path,task:{officialRequestId:hookId,taskId},row:()=>db.prepare('SELECT * FROM workflow_tasks').get()};
}
function facade({read={assetId,sourceSha256},still=source,result={ok:true,resultRef:ref},publishError}={}){
 const calls=[];
 return {calls,ports:{
  async read(claim){calls.push(['read',claim]);return read;},
  async asset(claim,id){calls.push(['asset',id]);return still;},
  async publish(claim,artifact){calls.push(['publish',artifact]);if(publishError)throw publishError;return result;}
 }};
}
test('official exact task consumes only authorized hashed PNG and publishes RGB with transparent flag explicitly absent',async t=>{
 const f=fixture(t),p=facade();
 assert.deepEqual({...chooseI2V(f.path)},f.task);
 const r=await executeI2V(f.db,f.task,signal(),{ports:p.ports,renderer:{async render(req){
  assert.deepEqual(req.source,source);assert.equal(req.motionPrompt,motionPrompt);assert.ok(req.dir);
  assert.equal(f.row().status,'running');return out;
 }}});
 assert.equal(r.alpha,'none');assert.equal(r.resultRef,ref);assert.equal(f.row().result_ref,ref);assert.equal(f.row().retry_count,0);
 assert.equal(chooseI2V(f.path),null);
 assert.deepEqual(p.calls.map(v=>v[0]),['read','asset','publish']);
 assert.deepEqual(Object.keys(p.calls[0][1]).sort(),['claimStamp','officialRequestId','payloadSha256','projectId','taskId']);
 assert.deepEqual(p.calls[2][1],{sourceSha256,fps:24,width:16,height:16,frames:out.frames.map(v=>v.toString('base64'))});
});
test('task scope blocks parent, tampered retained payload, arbitrary paths/model override, and stale claims',t=>{
 const a=fixture(t,{parent:'another-task'});assert.equal(chooseI2V(a.path),null);
 const b=fixture(t,{digest:'0'.repeat(64)});assert.throws(()=>claimI2V(b.db,b.task),/I2V_PAYLOAD_BINDING/);
 const c=fixture(t,{input:{sourceRef:payload.sourceRef,motionPrompt,model:'wan'}});assert.throws(()=>claimI2V(c.db,c.task),/I2V_TASK_INPUT/);
 const d=fixture(t);assert( claimI2V(d.db,d.task));assert.equal(claimI2V(d.db,d.task),null);
});
test('retry aged is incremented once, capped or recent failures not selected',t=>{
 const a=fixture(t,{status:'failed',age:601_000,retry:2});assert(chooseI2V(a.path));assert(claimI2V(a.db,a.task));
 assert.equal(a.row().retry_count,3);assert.equal(claimI2V(a.db,a.task),null);
 const b=fixture(t,{status:'failed',age:601_000,retry:3});assert.equal(chooseI2V(b.path),null);
 const c=fixture(t,{status:'failed',age:590_000,retry:0});assert.equal(chooseI2V(c.path),null);
});
test('different pinned source, bad source bytes and invalid RGB (RGBA, fake transparency, corrupt pixels) never publish',async t=>{
 for(const [p,value,expected,modelCalls] of [
  [facade({read:{assetId,sourceSha256:'a'.repeat(64)}}),out,'I2V_PDS_SOURCE',0],
  [facade({still:makePng()}),out,'I2V_PDS_SOURCE_HASH',0],
  [facade(),{...out,frames:[makePng({color:6}),out.frames[1]]},'I2V_PNG_RGB_REQUIRED',1],
  [facade(),{...out,frames:[makePng({trns:true}),out.frames[1]]},'I2V_PNG_STILL_REQUIRED',1],
  [facade(),{...out,frames:[makePng({badFilter:true}),out.frames[1]]},'I2V_PNG_DECODE',1]
 ]){
  const f=fixture(t);let renders=0;
  const r=await executeI2V(f.db,f.task,signal(),{ports:p.ports,renderer:{render:async()=>{renders++;return value;}}});
  assert.equal(r.state,'failed');assert.equal(r.code,expected);assert.equal(renders,modelCalls);
  assert.equal(p.calls.filter(v=>v[0]==='publish').length,0);assert.equal(f.row().status,'failed');
 }
});
test('commit/publication uncertainty is not requeued or rerendered, and ambiguous stop keeps its private scratch',async t=>{
 for(const [p,[joined,expectedStatus,cleanup,rendererStop]] of [
  [facade({result:{ok:true,resultRef:ref.replace(hookId,'wrong')}}),[null,'running',1,undefined]],
  [facade({publishError:Error('PDS_TIMEOUT')}),[null,'running',1,undefined]],
  [facade(),[false,'running',0,'unknown']],
  [facade(),[true,'failed',1,'confirmed']],
 ]){
  const f=fixture(t);let cleanups=0;
  const result=await executeI2V(f.db,f.task,signal(),{ports:p.ports,makeDir:async()=>'/fake-fixture',cleanup:async()=>{cleanups++;},
   renderer:{render:async()=>{if(joined!==null)throw Object.assign(Error('I2V_RENDER_FAILED'),{rendererJoined:joined});return out;}}});
  assert.equal(f.row().status,expectedStatus);assert.equal(result.rendererStop,rendererStop);assert.equal(cleanups,cleanup);
  assert.equal(chooseI2V(f.path),null);
 }
});
test('HTTP worker ports constrain authorized binary read, emit exact readiness and carry no shutdown signal into handed-off write',async()=>{
 const calls=[],p=i2vPorts(async (route,opt)=>{calls.push({route,opt});return {enabled:true,schemaVersion:'workflow.animation-i2v-write.v1'};});
 const claim={taskId},s=signal();
 assert.equal(await p.ready(s),true);
 await p.read(claim,s);await p.asset(claim,assetId,s);await p.publish(claim,out);
 assert.deepEqual(calls.map(v=>v.route),[i2vPath,i2vPath+'/read',i2vPath+'/asset',i2vPath]);
 assert.equal(calls[2].opt.maxBinaryBytes,8*1024*1024);
 assert.equal(calls[3].opt.signal,undefined);
});
test('model switch waits for Wan exit before I2V spawn, and prevents Wan while I2V is running',async()=>{
 let resolveClose,resolveLtx,entered=false;
 const slot=createAnimationModelSwitch({shutdown:()=>new Promise(r=>{resolveClose=r;})},
  {render:()=>{entered=true;return new Promise(r=>{resolveLtx=r;});}});
 const rendering=slot.runI2V({});
 assert(!entered);assert.throws(()=>slot.enterWan(),/ANIMATION_MODEL_SLOT_BUSY/);
 resolveClose(true);
 await new Promise(setImmediate);
 assert(entered);assert.throws(()=>slot.enterWan(),/ANIMATION_MODEL_SLOT_BUSY/);
 resolveLtx(out);assert.deepEqual(await rendering,out);assert.doesNotThrow(()=>slot.enterWan());
});
test('unknown Wan or I2V group blocks either model; confirmed I2V inferences failures can release slot',async()=>{
 let started=0;
 const before=createAnimationModelSwitch({shutdown:async()=>false},{render:async()=>{started++;}});
 await assert.rejects(before.runI2V({}),/ANIMATION_WAN_RELEASE_UNCONFIRMED/);
 await assert.rejects(before.runI2V({}),/ANIMATION_MODEL_OWNERSHIP_UNKNOWN/);assert.equal(started,0);
 assert.throws(()=>before.enterWan(),/ANIMATION_MODEL_OWNERSHIP_UNKNOWN/);
 for(const joined of [false,true]){
  const after=createAnimationModelSwitch({shutdown:async()=>true},{async render(){throw Object.assign(Error('LTX_FAILURE'),{rendererJoined:joined});}});
  await assert.rejects(after.runI2V({}),/LTX_FAILURE/);
  if(joined)after.enterWan();else assert.throws(()=>after.enterWan(),/ANIMATION_MODEL_OWNERSHIP_UNKNOWN/);
 }
});

test('valid publication ACK cannot complete after retained task or root changes',async t=>{
 for(const override of [
  `UPDATE workflow_runs SET state='cancelled'`,
  `UPDATE workflow_tasks SET task_input='{}'`,
 ]){
  const f=fixture(t),p=facade();
  const r=await executeI2V(f.db,f.task,signal(),{renderer:{render:async()=>out},ports:{...p.ports,
   async publish(){f.db.exec(override);return {ok:true,resultRef:ref};}
  }});
  assert.equal(r.state,'requires_reconciliation');assert.equal(r.stage,'commit');
  assert.equal(f.row().status,'running');assert.equal(f.row().result_ref,null);
 }
});
