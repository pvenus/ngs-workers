import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
const fixture=JSON.parse(readFileSync(new URL('./npc-selection-nonmedia.fixture.json',import.meta.url)));
for (const role of ['document','image','file']){
 const {buildPlan}=await import('../workflow-production/'+role+'/vendor/planner-npc-motion-selection-v1/planner.mjs');
 const {canonical}=await import('../workflow-production/'+role+'/vendor/planner-npc-motion-selection-v1/envelope.mjs');
 test(role+' exactly matches canonical selection create and replace workflow records',()=>{
  for(const {row,tasks} of fixture.cases){
   const projected=buildPlan(row);
   assert.deepEqual(projected.tasks,tasks);
   assert.equal(projected.payload,canonical(row.payload));
  }
 });
 test(role+' rejects bad digest, legacy extras and changed schema/request type',()=>{
  const {row}=fixture.cases[0];
  assert.throws(()=>buildPlan({...row,payloadSha256:'a'.repeat(64)}));
  assert.throws(()=>buildPlan({...row,payload:{...row.payload,includeCombat:false}}));
  assert.throws(()=>buildPlan({...row,requestType:'npc.other'}));
  assert.throws(()=>buildPlan({...row,schemaVersion:fixture.cases[1].row.schemaVersion}));
 });
}

test('selection replacement unlocks Document after exact File claim and reaches Image PDS read',async()=>{
 const {PlannerStore}=await import('../workflow-production/document/vendor/planner-four-doc-v1/planner.mjs');
 const {canonical}=await import('../workflow-production/document/vendor/planner-npc-motion-selection-v1/envelope.mjs');
 const {runSelectedPreset}=await import('../workflow-production/document/worker.mjs');
 const {runSelectedPortrait}=await import('../workflow-production/image/worker.mjs');
 const {runSelectedDelete}=await import('../workflow-production/file/worker.mjs');
 const {row,tasks}=fixture.cases[1], id=row.officialRequestId;
 const store=new PlannerStore(':memory:');
 try{
  const {db}=store;
  if(!db.prepare('PRAGMA table_info(workflow_tasks)').all().some(c=>c.name==='retry_count'))db.exec('ALTER TABLE workflow_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0');
  db.prepare('INSERT INTO workflow_runs(official_request_id,project_id,request_type,schema_version,payload,payload_sha256,registered_at,state,updated_at) VALUES(?,?,?,?,?,?,?,?,?)')
    .run(id,row.projectId,row.requestType,row.schemaVersion,canonical(row.payload),row.payloadSha256,row.registeredAt,'planned',row.registeredAt);
  const put=db.prepare('INSERT INTO workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,status,depends_on_task_id,task_input,updated_at) VALUES(?,?,?,?,?,?,?,?,?)');
  for(const t of tasks)put.run(t.id,id,t.key,t.type,t.workerType,'pending',t.dependsOn,canonical(t.input),row.registeredAt);
  const task=key=>tasks.find(t=>t.key===key).id;
  const args=(key,pds)=>({db,officialRequestId:id,taskId:task(key),pds});
  const preset={publishExactPreset(command){return {
   schemaVersion:'workflow.preset-publication-receipt.v1',officialRequestId:id,taskId:task('preset'),projectId:'binari',npcId:row.payload.character.npcId,
   payloadSha256:row.payloadSha256,operation:'npc.preset',artifactSha256:command.artifactSha256,status:'committed',verified:true,receiptId:'fixture.preset'};}};
  assert.equal((await runSelectedPreset(args('preset',preset))).state,'not_claimable');
  assert.equal((await runSelectedDelete(args('delete',{deleteTargets(command,binding){
   assert.equal(command.targets[0].id,row.payload.character.npcId);
   assert.equal(binding.payloadSha256,row.payloadSha256);
   return {schemaVersion:'workflow.pds-delete-receipt.v1',officialRequestId:id,taskId:task('delete'),projectId:'binari',
    npcId:row.payload.character.npcId,payloadSha256:row.payloadSha256,operation:'npc.delete-folder',
    status:'committed',verified:true,targetAbsent:true,receiptId:'fixture.delete'};
  }}))).state,'completed');
  assert.equal((await runSelectedPreset(args('preset',preset))).state,'completed');
  for(const key of ['planning_overview','planning_portrait_art','planning_representative'])
   db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_id=?").run(task(key));
  let read=false;
  const result=await runSelectedPortrait({...args('portrait_representative',{readPortrait(command){
   assert.equal(command.payloadSha256,row.payloadSha256);
   read=true;
   throw Error('fixture no backing files');
  },publishPortrait(){assert.fail('must not reach publication')}}), render(){assert.fail('must not render')},withPlanningSnapshot(){assert.fail('must not snapshot')}});
  assert.equal(result.state,'failed');
  assert.equal(read,true);
 }finally{store.close();}
});
