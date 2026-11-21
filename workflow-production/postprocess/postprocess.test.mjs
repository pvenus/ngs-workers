import test from 'node:test';
import assert from 'node:assert/strict';
import {chmodSync,mkdtempSync,rmSync,realpathSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {PlannerStore} from './vendor/planner-f24d23a/planner.mjs';
import {sha,canonical} from './vendor/planner-f24d23a/envelope.mjs';
import {nextPostprocessFromPath,runPostprocessFromPath} from './postprocess-worker.mjs';
import {createServer} from 'node:http';import {postprocessPorts,raw} from './postprocess-ports.mjs';
function fixture(t){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'postprocess-'))),dbPath=join(root,'db');
 const s=new PlannerStore(dbPath);s.db.exec('ALTER TABLE workflow_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 3)');chmodSync(dbPath,0o600);t.after(()=>{s.close();rmSync(root,{recursive:true,force:true});});
 const officialRequestId='request.official.'+sha('fixture');
 const payload={schemaVersion:'ngs.npc-one-stop.replace.v1',projectId:'binari',intent:'fixture',
  character:{npcId:'npc.ch1_dajeong',name:'다정',slug:'dajeong',role:'fixture',actId:'act1',chapterId:'ch1'},
  sourceRefs:['story.fixture'],includeCombat:false};
 s.stage({officialRequestId,projectId:'binari',requestType:'npc.workflow',schemaVersion:payload.schemaVersion,
  payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1});s.acknowledge(officialRequestId);
 const row=key=>s.db.prepare('SELECT * FROM workflow_tasks WHERE task_key=?').get(key);
 const dep=key=>s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key=?").run(key);
 return {dbPath,officialRequestId,row,dep,s};
}
test('only exact approved motion+direction dependency, then sequential CAS',async t=>{
 const f=fixture(t);
 assert.equal(nextPostprocessFromPath(f.dbPath),null);
 f.dep('portrait_n');assert.equal(nextPostprocessFromPath(f.dbPath),null);
 f.dep('animation_run_sw');
 assert.equal(f.row('animation_run_sw').result_ref,null);
 const target=nextPostprocessFromPath(f.dbPath);
 assert.equal(target.taskId,f.row('frames_run_sw').task_id);
 const calls=[];
 const pds={read:async b=>{calls.push(b);assert.equal(b.predecessorTaskId,f.row('animation_run_sw').task_id);assert.equal(f.row('frames_run_sw').status,'running');assert.equal(f.row('frames_run_sw').updated_at,b.claim.claimStamp);return {source:true};},
  publish:async(b,s,r)=>{assert.equal(calls[0],b);assert.deepEqual(s,{source:true});assert.deepEqual(r,{artifact:true});return {ok:true};}};
 const options={...f,...target,pds,render:async()=>({artifact:true})};
 assert.equal((await runPostprocessFromPath(options)).state,'completed');
 assert.match(f.row('frames_run_sw').result_ref,/animation-processed-directions\/idle\/run\/sw\.json$/);
 assert.equal((await runPostprocessFromPath(options)).state,'not_claimable');
 assert.equal(f.row('frames_walk_sw').status,'pending');
 assert.equal(nextPostprocessFromPath(f.dbPath),null);
});
test('wrong canonical predecessor rejected before dispatch',async t=>{
 const f=fixture(t);f.dep('animation_run_sw');
 f.s.db.prepare("UPDATE workflow_tasks SET task_input='{}' WHERE task_key='animation_run_sw'").run();
 await assert.rejects(runPostprocessFromPath({...f,...nextPostprocessFromPath(f.dbPath),pds:{read(){throw Error('unexpected');},publish(){}},render(){}}),/PREDECESSOR_BINDING_INVALID/);
 assert.equal(f.row('frames_run_sw').status,'pending');
});
test('known source error fails; unknown ACK remains running, never retries',async t=>{
 const f=fixture(t);f.dep('animation_walk_n');
 const pds={read:async()=>{throw Error('read fixture');},publish(){}},render=async()=>({ok:true});
 assert.equal((await runPostprocessFromPath({...f,...nextPostprocessFromPath(f.dbPath),pds,render})).state,'failed');
 f.dep('animation_walk_ne');
 pds.read=async()=>({});pds.publish=async()=>{throw Error('lost fixture');};
 assert.equal((await runPostprocessFromPath({...f,...nextPostprocessFromPath(f.dbPath),pds,render})).state,'requires_reconciliation');
 assert.equal(f.row('frames_walk_ne').status,'running');
 assert.equal(nextPostprocessFromPath(f.dbPath),null);
});
test('asset binary binds exact claim and file digest',async()=>{
 const bytes=Buffer.from('fixture'),hash=sha(bytes),frame={sha256:hash,assetId:hash+'.png',mimeType:'image/png',bytes:bytes.length};
 const claim={taskId:'fixture'};
 const p=postprocessPorts({transport:async o=>{assert.deepEqual(o.body,{claim,assetId:frame.assetId});assert.match(o.path,/\/asset$/);return bytes;}});
 assert.deepEqual(await p.fetchAsset(claim,frame),bytes);
 await assert.rejects(p.fetchAsset(claim,{...frame,bytes:32}),/ASSET_CONTENT/);
});

for(const [status,error,expected] of [
 [403,'WORKFLOW_MEDIA_CLAIM_DENIED','failed'],
 [404,'WORKFLOW_MEDIA_DISABLED','failed'],
 [403,'unexpected-edge-policy','requires_reconciliation'],
 [409,'WORKFLOW_MEDIA_NOT_CONFIRMED','requires_reconciliation'],
])test('frames exact publish status '+status+' '+error+' -> '+expected,async t=>{
 const f=fixture(t);f.dep('animation_run_sw');
 const srv=createServer(async(req,res)=>{
  for await(const _ of req){}
  res.writeHead(status,{'Content-Type':'application/json'}).end(JSON.stringify({error}));
 });
 await new Promise(resolve=>srv.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>srv.close(resolve)));
 const pds=postprocessPorts({transport:o=>raw({...o,port:srv.address().port})});
 pds.read=async()=>({});
 const actual=await runPostprocessFromPath({...f,...nextPostprocessFromPath(f.dbPath),pds,render:async()=>({artifact:{}})});
 assert.equal(actual.state,expected);
 assert.equal(f.row('frames_run_sw').status,expected==='failed'?'failed':'running');
 assert.equal(f.row('frames_run_sw').result_ref,null);
 assert.equal(nextPostprocessFromPath(f.dbPath),null);
});

test('frames failed retry observes cooldown and counts exact worker-owned reclaim only',async t=>{
 const f=fixture(t);f.dep('animation_run_sw');
 const taskId=f.row('frames_run_sw').task_id;
 const pds={read:async()=>{throw Error('definite read failure');},publish(){}},render=async()=>({artifact:{}});
 const options={...f,taskId,pds,render};
 assert.equal((await runPostprocessFromPath(options)).state,'failed');
 assert.equal(f.row('frames_run_sw').retry_count,0);
 assert.equal(nextPostprocessFromPath(f.dbPath),null);
 assert.equal((await runPostprocessFromPath(options)).state,'not_claimable');
 const age=ms=>f.s.db.prepare("UPDATE workflow_tasks SET updated_at=? WHERE task_id=?").run(Date.now()-ms,taskId);
 age(601000);assert.equal(nextPostprocessFromPath(f.dbPath).taskId,taskId);
 pds.read=async()=>({});pds.publish=async()=>{assert.equal(f.row('frames_run_sw').retry_count,1);throw Error('ACK lost');};
 assert.equal((await runPostprocessFromPath(options)).state,'requires_reconciliation');
 age(601000);assert.equal(nextPostprocessFromPath(f.dbPath),null);
 assert.equal((await runPostprocessFromPath(options)).state,'not_claimable');
 assert.equal(f.row('frames_run_sw').retry_count,1);
 f.s.db.prepare("UPDATE workflow_tasks SET status='failed',retry_count=3 WHERE task_id=?").run(taskId);
 age(601000);assert.equal(nextPostprocessFromPath(f.dbPath),null);
 assert.equal((await runPostprocessFromPath(options)).state,'not_claimable');
 assert.equal(f.row('frames_run_sw').retry_count,3);
});
