import test from 'node:test';
import assert from 'node:assert/strict';
import {chmodSync,mkdtempSync,realpathSync,rmSync,symlinkSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PlannerStore} from './vendor/planner-f24d23a/planner.mjs';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {runDeleteFromPath,runSelectedDelete} from './worker.mjs';

function fixture(t,onDisk=false){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'workflow-delete-worker-')));
 t.after(()=>rmSync(dir,{recursive:true,force:true}));
 const dbPath=join(dir,'workflow.sqlite'),s=new PlannerStore(onDisk?dbPath:':memory:');s.db.exec('ALTER TABLE workflow_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 3)');
 if(onDisk)chmodSync(dbPath,0o600);
 t.after(()=>s.close());
 const payload={schemaVersion:'ngs.npc-one-stop.replace.v1',projectId:'binari',intent:'fixture replacement only',
  character:{npcId:'npc.ch1_dajeong',name:'다정',slug:'dajeong',role:'fixture',actId:'act1',chapterId:'ch1'},sourceRefs:['story.fixture'],includeCombat:false};
 const id='request.official.'+sha('fixture');
 s.stage({officialRequestId:id,projectId:'binari',requestType:'npc.workflow',schemaVersion:payload.schemaVersion,
  payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1800000000000});
 const task=k=>s.db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? AND task_key=?').get(id,k);
 return {s,id,task,db:s.db,dbPath,dir,taskId:task('delete').task_id};
}
const proof=c=>({schemaVersion:'workflow.pds-delete-receipt.v1',
 officialRequestId:c.officialRequestId,taskId:c.taskId,projectId:c.projectId,npcId:c.npcId,payloadSha256:c.payloadSha256,
 operation:c.operation,status:'committed',verified:true,targetAbsent:true,receiptId:'fixture.receipt'});
const untouched={deleteExactNpcFolder(){assert.fail('storage adapter must not be called');}};

test('one exact planned task, verified receipt, no RS state mutation',async t=>{
 const f=fixture(t),{s,id,db,taskId,task}=f;
 assert.equal((await runSelectedDelete({db,taskId,officialRequestId:id,pds:untouched})).state,'not_claimable');
 s.acknowledge(id);
 const before=db.prepare('SELECT * FROM workflow_runs').all();
 const otherRows=db.prepare('SELECT * FROM workflow_tasks WHERE task_id<>? ORDER BY task_id');
 const others=otherRows.all(taskId);
 const map=new Map([['npc.ch1_dajeong','target'],['npc.ch1_dajeong_alt','neighbor']]);
 const seen=[];
 const out=await runSelectedDelete({db,taskId,officialRequestId:id,pds:{deleteExactNpcFolder(command){
  assert(Object.isFrozen(command));assert.equal(task('delete').status,'running');
  assert.equal(command.scope,'exact-npc-folder');assert.equal(command.noOldDataReuse,true);
  assert.equal(command.preserveRequestHistory,true);assert.equal(command.npcId,'npc.ch1_dajeong');
  seen.push(command.npcId);assert.equal(map.delete(command.npcId),true);return proof(command);
 }}});
 assert.equal(out.state,'completed');assert.equal(task('delete').result_ref,'fixture.receipt');
 assert.equal(task('preset').status,'pending');assert.deepEqual(db.prepare('SELECT * FROM workflow_runs').all(),before);
 assert.deepEqual(otherRows.all(taskId),others);
 assert.deepEqual(seen,['npc.ch1_dajeong']);assert.equal(map.get('npc.ch1_dajeong_alt'),'neighbor');
 assert.equal((await runSelectedDelete({db,taskId,officialRequestId:id,pds:untouched})).state,'not_claimable');
});

test('lost response after dispatch cannot retry after caller restart',async t=>{
 const {s,id,dbPath,task,taskId}=fixture(t,true);s.acknowledge(id);
 let calls=0;
 const out=await runDeleteFromPath({dbPath,taskId,officialRequestId:id,pds:{deleteExactNpcFolder(){calls++;throw Error('fixture response lost');}}});
 assert.equal(out.state,'requires_reconciliation');assert.equal(task('delete').status,'running');assert.equal(task('delete').result_ref,null);
 assert.equal((await runDeleteFromPath({dbPath,taskId,officialRequestId:id,pds:untouched})).state,'not_claimable');
 assert.equal(calls,1);assert.equal(task('preset').status,'pending');
});

test('receipt scope, exact digest and absence must match, otherwise no completion',async t=>{
 for(const patch of [{npcId:'npc.other'},{taskId:'workflow.task.'+sha('wrong')},{targetAbsent:false},
  {verified:false},{payloadSha256:'1'.repeat(64)},{receiptId:'https://foreign.test/id'}]){
  const {s,id,db,taskId,task}=fixture(t);s.acknowledge(id);
  const r=await runSelectedDelete({db,taskId,officialRequestId:id,pds:{deleteExactNpcFolder(c){return {...proof(c),...patch};}}});
  assert.equal(r.state,'requires_reconciliation');assert.equal(task('delete').status,'running');assert.equal(task('delete').result_ref,null);
 }
});

test('distinct DB connections cannot claim a selected deletion twice',async t=>{
 const {s,id,dbPath,taskId}=fixture(t,true);s.acknowledge(id);
 let release;
 const p=runDeleteFromPath({dbPath,taskId,officialRequestId:id,pds:{deleteExactNpcFolder(c){return new Promise(resolve=>{release=()=>resolve(proof(c));});}}});
 assert.equal((await runDeleteFromPath({dbPath,taskId,officialRequestId:id,pds:untouched})).state,'not_claimable');
 release();assert.equal((await p).state,'completed');
});

test('forged selected task input, immutable payload, and wrong worker refuse without dispatch',async t=>{
 const {s,id,db,taskId,task}=fixture(t);s.acknowledge(id);
 const before=task('delete');
 const update=db.prepare('UPDATE workflow_tasks SET task_input=? WHERE task_id=?');
 for(const patch of [{npcId:'npc.other'},{npcId:'../npc.other'},{path:'/other'},{scope:'all-npcs'}]){
  update.run(canonical({...JSON.parse(before.task_input),...patch}),taskId);
  await assert.rejects(runSelectedDelete({db,taskId,officialRequestId:id,pds:untouched}),/WORKFLOW_DELETE_BINDING_INVALID/);
  assert.equal(task('delete').status,'pending');
 }
 update.run(before.task_input,taskId);
 db.prepare("UPDATE workflow_tasks SET worker_type='image' WHERE task_id=?").run(taskId);
 assert.equal((await runSelectedDelete({db,taskId,officialRequestId:id,pds:untouched})).state,'not_claimable');
 db.prepare("UPDATE workflow_tasks SET worker_type='file' WHERE task_id=?").run(taskId);
 db.prepare("UPDATE workflow_runs SET payload_sha256=? WHERE official_request_id=?").run('0'.repeat(64),id);
 await assert.rejects(runSelectedDelete({db,taskId,officialRequestId:id,pds:untouched}),/WORKFLOW_PAYLOAD_BINDING_CHANGED/);
 assert.equal(task('delete').status,'pending');
});

test('dependency must be the exact planned dependency, and concurrent row drift cannot acknowledge',async t=>{
 const {s,id,db,taskId,task}=fixture(t);s.acknowledge(id);
 db.prepare('UPDATE workflow_tasks SET depends_on_task_id=? WHERE task_id=?').run(task('preset').task_id,taskId);
 assert.equal((await runSelectedDelete({db,taskId,officialRequestId:id,pds:untouched})).state,'not_claimable');
 db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_id=?").run(task('preset').task_id);
 await assert.rejects(runSelectedDelete({db,taskId,officialRequestId:id,pds:untouched}),/WORKFLOW_DELETE_BINDING_INVALID/);
 db.prepare('UPDATE workflow_tasks SET depends_on_task_id=NULL WHERE task_id=?').run(taskId);
 const r=await runSelectedDelete({db,taskId,officialRequestId:id,pds:{deleteExactNpcFolder(c){
  db.prepare('UPDATE workflow_tasks SET updated_at=updated_at+1 WHERE task_id=?').run(taskId);
  return proof(c);
 }}});
 assert.equal(r.state,'requires_reconciliation');assert.equal(task('delete').result_ref,null);
 assert.equal(task('delete').status,'running');
});

test('disk interface refuses non-private and aliased database paths before opening',async t=>{
 const {dbPath,dir,taskId,id}=fixture(t,true),alias=join(dir,'alias.sqlite');
 symlinkSync(dbPath,alias);
 await assert.rejects(runDeleteFromPath({dbPath:alias,taskId,officialRequestId:id,pds:untouched}),/WORKFLOW_DELETE_DB_PATH/);
 chmodSync(dbPath,0o644);
 await assert.rejects(runDeleteFromPath({dbPath,taskId,officialRequestId:id,pds:untouched}),/WORKFLOW_DELETE_DB_PATH/);
});
