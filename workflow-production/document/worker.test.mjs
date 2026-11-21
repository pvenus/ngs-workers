import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,rmSync,chmodSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PlannerStore} from './vendor/planner-89c7568/planner.mjs';
import {canonical,sha} from './vendor/planner-89c7568/envelope.mjs';
import {runSelectedPreset,runPresetFromPath} from './worker.mjs';

function setup(t,{newNPC=false,disk=false}={}){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'workflow-document-preset-')));
 const dbPath=join(dir,'workflow.sqlite'),s=new PlannerStore(disk?dbPath:':memory:');
 s.db.exec('ALTER TABLE workflow_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 3)');
 if(disk)chmodSync(dbPath,0o600);
 t.after(()=>{s.close();rmSync(dir,{recursive:true,force:true});});
 const payload={schemaVersion:newNPC?'ngs.npc-one-stop.v1':'ngs.npc-one-stop.replace.v1',
  projectId:'binari',intent:'Official fixture only',character:{npcId:'npc.ch1_dajeong',name:'다정',slug:'dajeong',
   role:'마을 주민',actId:'act1',chapterId:'ch1'},sourceRefs:['story.fixture'],includeCombat:false};
 const id='request.official.'+sha('dajeong');
 s.stage({officialRequestId:id,projectId:'binari',requestType:'npc.workflow',schemaVersion:payload.schemaVersion,
  payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1800000000000});
 const task=k=>s.db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? AND task_key=?').get(id,k);
 const taskId=task('preset').task_id;
 const completeDelete=()=>s.db.prepare("UPDATE workflow_tasks SET status='completed',result_ref='exact.delete.receipt' WHERE task_id=?").run(task('delete').task_id);
 return {s,db:s.db,dbPath,task,id,payload,taskId,completeDelete,entry:{db:s.db,taskId,officialRequestId:id},diskEntry:{dbPath,taskId,officialRequestId:id}};
}
const denied={publishExactPreset(){assert.fail('publication must not be called');}};
const verified=c=>({schemaVersion:'workflow.preset-publication-receipt.v1',officialRequestId:c.officialRequestId,taskId:c.taskId,
 projectId:c.projectId,npcId:c.npcId,payloadSha256:c.payloadSha256,operation:c.operation,
 artifactSha256:c.artifactSha256,status:'committed',verified:true,receiptId:'preset.receipt'});

test('exact Dajeong planned replacement with pending delete changes nothing and performs zero publication',async t=>{
 const f=setup(t);
 f.s.acknowledge(f.id);
 const all=f.db.prepare('SELECT * FROM workflow_tasks ORDER BY task_id'),before=all.all();
 const runs=f.db.prepare('SELECT * FROM workflow_runs'),runsBefore=runs.all();
 assert.equal((await runSelectedPreset({...f.entry,pds:denied})).state,'not_claimable');
 assert.deepEqual(all.all(),before);assert.deepEqual(runs.all(),runsBefore);
 assert.equal(f.task('delete').status,'pending');
});

test('exact completed predecessor unlocks deterministic official-only preset and only own row changes',async t=>{
 const f=setup(t);f.s.acknowledge(f.id);f.completeDelete();
 const others=f.db.prepare('SELECT * FROM workflow_tasks WHERE task_id<>? ORDER BY task_id'),before=others.all(f.taskId);
 const runs=f.db.prepare('SELECT * FROM workflow_runs'),runsBefore=runs.all();
 const out=await runSelectedPreset({...f.entry,pds:{publishExactPreset(c){
  assert(Object.isFrozen(c));assert.equal(c.predecessorReceiptRef,'exact.delete.receipt');assert.equal(sha(c.artifact),c.artifactSha256);
  assert.equal(f.task('preset').status,'running');assert(!('leaseToken' in c));assert(!('guard' in c));
  const a=JSON.parse(c.artifact);assert.deepEqual(a.character,f.payload.character);assert.equal(a.profile.role,'마을 주민');
  assert.equal(a.contentGenerated,false);assert.equal(a.oldDataReused,false);
  assert.equal(a.profile.planningPreset.version,'3');assert.equal(a.profile.planningCategories.length,4);
  assert(a.profile.planningCategories.every(p=>p.content===''));
  assert.deepEqual(a.profile.planningCategories.filter(p=>p.planningRole).map(p=>p.planningRole),['portrait.representative','portrait.idle']);
  return verified(c);
 }}});
 assert.equal(out.state,'completed');assert.equal(f.task('preset').result_ref,'preset.receipt');
 assert.equal(f.task('preset').retry_count,0);
 assert.deepEqual(others.all(f.taskId),before);assert.deepEqual(runs.all(),runsBefore);
 assert.equal((await runSelectedPreset({...f.entry,pds:denied})).state,'not_claimable');
});

test('exact official ID and task type required; forging NULL predecessor cannot bypass delete',async t=>{
 const f=setup(t);f.s.acknowledge(f.id);
 assert.equal((await runSelectedPreset({...f.entry,officialRequestId:'request.official.'+sha('different'),pds:denied})).state,'not_claimable');
 assert.equal((await runSelectedPreset({...f.entry,taskId:f.task('delete').task_id,pds:denied})).state,'not_claimable');
 f.db.prepare('UPDATE workflow_tasks SET depends_on_task_id=NULL WHERE task_id=?').run(f.taskId);
 await assert.rejects(runSelectedPreset({...f.entry,pds:denied}),/WORKFLOW_PRESET_BINDING_INVALID/);
 assert.equal(f.task('preset').status,'pending');
});

test('completed parent with no receipt is eligible; completion does not re-read parent eligibility',async t=>{
 const f=setup(t);f.s.acknowledge(f.id);
 const deleteId=f.task('delete').task_id;
 f.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_id=?").run(deleteId);
 const out=await runSelectedPreset({...f.entry,pds:{publishExactPreset(c){
  assert.equal(c.predecessorReceiptRef,null);
  // Independent fixture writer changes the parent after this worker claimed.
  f.db.prepare("UPDATE workflow_tasks SET status='failed',result_ref='other.receipt' WHERE task_id=?").run(deleteId);
  return verified(c);
 }}});
 assert.equal(out.state,'completed');
 assert.equal(f.task('preset').result_ref,'preset.receipt');
 assert.equal(f.task('delete').status,'failed');
 assert.equal(f.task('delete').result_ref,'other.receipt');
});

test('wrong publication digest or unknown outcomes never complete and restart never republishes',async t=>{
 for(const failure of [c=>({...verified(c),artifactSha256:'0'.repeat(64)}),()=>{throw Error('fixture response lost');}]){
  const f=setup(t,{disk:true});f.s.acknowledge(f.id);f.completeDelete();
  const result=await runPresetFromPath({...f.diskEntry,pds:{publishExactPreset:failure}});
  assert.equal(result.state,'requires_reconciliation');assert.equal(f.task('preset').status,'running');
  assert.equal((await runPresetFromPath({...f.diskEntry,pds:denied})).state,'not_claimable');
  assert.equal(f.task('planning_representative').status,'pending');
 }
});

test('new exact NPC has no predecessor and awaiting RS cannot start',async t=>{
 const f=setup(t,{newNPC:true});
 assert.equal((await runSelectedPreset({...f.entry,pds:denied})).state,'not_claimable');
 f.s.acknowledge(f.id);
 const out=await runSelectedPreset({...f.entry,pds:{publishExactPreset(c){assert.equal(c.predecessorReceiptRef,null);return verified(c);}}});
 assert.equal(out.state,'completed');
});
