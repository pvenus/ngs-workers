import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,rmSync,chmodSync,readFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {DatabaseSync} from 'node:sqlite';
import {nextPostprocessFromPath,runPostprocessFromPath} from './postprocess-worker.mjs';
import {postprocessPorts} from './postprocess-ports.mjs';
import {canonical,selectedMotionBranch} from './vendor/npc-motion-selection-v1/media-branch.mjs';
import {sha} from './vendor/npc-motion-selection-v1/envelope.mjs';

const cases=JSON.parse(readFileSync(new URL('./vendor/npc-motion-selection-v1/golden-media.fixture.json',import.meta.url)));
const regenerateGolden=JSON.parse(readFileSync(new URL('./vendor/npc-motion-selection-v1/npc-animation-regenerate.v1.fixture.json',import.meta.url)));
const regenerateCase=()=>({row:regenerateGolden.row,tasks:regenerateGolden.plan.tasks});
function fixture(t,which=0){
 const {row,tasks}=which==='regenerate'?regenerateCase():cases[which];
 const root=realpathSync(mkdtempSync(join(tmpdir(),'npc-motion-selection-'))),dbPath=join(root,'workflow.sqlite');
 const db=new DatabaseSync(dbPath);chmodSync(dbPath,0o600);
 t.after(()=>{db.close();rmSync(root,{recursive:true,force:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,project_id TEXT,request_type TEXT,schema_version TEXT,
 payload TEXT,payload_sha256 TEXT,state TEXT,reason TEXT,registered_at INTEGER);
 CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_key TEXT,task_type TEXT,worker_type TEXT,depends_on_task_id TEXT,
 task_input TEXT,status TEXT,result_ref TEXT,updated_at INTEGER,retry_count INTEGER)`);
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,'planned',NULL,?)")
  .run(row.officialRequestId,row.projectId,row.requestType,row.schemaVersion,canonical(row.payload),row.payloadSha256,row.registeredAt);
 const stmt=db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,?,'pending',NULL,1,0)");
 for(const v of tasks)stmt.run(v.id,row.officialRequestId,v.key,v.type,v.workerType,v.dependsOn,canonical(v.input));
 const finish=id=>db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_id=?").run(id);
 const byId=id=>db.prepare('SELECT status,result_ref AS result FROM workflow_tasks WHERE task_id=?').get(id);
 return {row,tasks,db,dbPath,finish,byId};
}
function selected(f,motion='sword-attack-flat'){return f.tasks.find(t=>t.type==='npc.frames-postprocess'&&t.input.motion===motion);}

test('selected frame task verifies exact same-run parent and completes into the true pose/preset/direction',async t=>{
 const f=fixture(t),target=selected(f);
 assert.equal(nextPostprocessFromPath(f.dbPath),null);
 f.finish(target.dependsOn);
 assert.equal(nextPostprocessFromPath(f.dbPath,{frames:false}),null);
 assert.equal(nextPostprocessFromPath(f.dbPath).taskId,target.id);
 const seen=[];
 const result=await runPostprocessFromPath({...f,taskId:target.id,officialRequestId:f.row.officialRequestId,
  pds:{
   read:async binding=>{seen.push(binding);assert.equal(binding.predecessorTaskId,target.dependsOn);
    assert.equal(binding.motionPosePreset,'combat');assert.equal(binding.motion,target.input.motion);
    assert.equal(binding.assetSetSha256,target.input.assetSetSha256);assert.equal(binding.posePreset,undefined);return {};},
   publish:async binding=>{assert.equal(binding,seen[0]);return {ok:true};}},
  render:async()=>({artifact:{}})});
 const path='canonical-files/binari/npcs/npc.fixture/animation-processed-directions/combat/sword-attack-flat/n.json';
 assert.equal(result.state,'completed');assert.equal(result.resultRef,path);
 assert.equal(f.byId(target.id).result,path);assert.equal(f.byId(selected(f,'run-2d').id).status,'pending');
});

test('selected postprocess fails tight scope for moved or mismatched same-run parent',async t=>{
 const f=fixture(t,1),target=selected(f);f.finish(target.dependsOn);
 const current=f.tasks.find(x=>x.id===target.dependsOn);
 f.db.prepare('UPDATE workflow_tasks SET task_input=? WHERE task_id=?')
  .run(canonical({...current.input,assetSetSha256:'e'.repeat(64)}),current.id);
 await assert.rejects(runPostprocessFromPath({...f,taskId:target.id,officialRequestId:f.row.officialRequestId,
  pds:{read:()=>assert.fail(),publish:()=>assert.fail()},render:()=>assert.fail()}),/POSTPROCESS_PREDECESSOR_BINDING_INVALID/);
 assert.equal(f.byId(target.id).status,'pending');
});

test('selected schema still consumes ordinary idle pixel portraits via their original PDS lane',async t=>{
 const f=fixture(t,1),target=f.tasks.find(t=>t.key==='portrait_pixel_idle_reference');
 f.finish(target.dependsOn);
 assert.equal(nextPostprocessFromPath(f.dbPath,{frames:false}).taskId,target.id);
 let read=false;
 const result=await runPostprocessFromPath({...f,taskId:target.id,officialRequestId:f.row.officialRequestId,
  pds:{read:async b=>{assert.equal(b.posePreset,'idle');assert.equal(b.direction,null);
   assert.equal(b.motion,undefined);read=true;return {};},publish:async()=>({ok:true})},render:async()=>({artifact:{}})});
 assert.equal(result.state,'completed');assert(read);assert.equal(result.resultRef,null);
});

test('PDS read binds selected projection and raw source lineage, never routes to static portrait',async()=>{
 const {row,tasks}=cases[0],t=tasks.find(t=>t.type==='npc.frames-postprocess'&&t.input.motion==='sword-attack-flat');
 const {npcId,motion,posePreset:motionPosePreset,direction,assetSetSha256}=t.input;
 const binding={claim:{officialRequestId:row.officialRequestId,npcId},motion,motionPosePreset,direction,assetSetSha256,predecessorTaskId:t.dependsOn};
 const seq={schemaVersion:'ngs.animation-native-png-sequence.v1',npcId,presetId:motion,posePreset:motionPosePreset,direction,
  requestId:t.dependsOn,frameCount:2,frames:[{},{}],
  sourceBindingHash:sha(canonical([row.officialRequestId,'portrait_'+direction,motion,motionPosePreset,assetSetSha256]))};
 let out={direction,motion,sequence:seq,posePreset:motionPosePreset,assetSetSha256};
 const p=postprocessPorts({transport:async ({path,body})=>{
  assert.equal(path,'/v1/npcs/ownership/workflow-frames/read');assert.deepEqual(body,{claim:binding.claim});
  return Buffer.from(JSON.stringify(out));
 }});
 assert.deepEqual(await p.read(binding),seq);
 out={...out,assetSetSha256:'3'.repeat(64)};
 await assert.rejects(p.read(binding),/POSTPROCESS_PDS_SELECTED_SOURCE_BINDING/);
 out={...out,assetSetSha256,sequence:{...seq,sourceBindingHash:'3'.repeat(64)}};
 await assert.rejects(p.read(binding),/POSTPROCESS_PDS_SELECTED_SOURCE_BINDING/);
 out={...out,sequence:{...seq,posePreset:'idle'}};
 await assert.rejects(p.read(binding),/POSTPROCESS_PDS_SOURCE_BINDING/);
});
test('pose v2 postprocess rejects legacy idle lineage as a combat source',async()=>{
 const {row,tasks}=cases[0],t=tasks.find(t=>t.type==='npc.frames-postprocess'&&t.input.posePreset==='combat');
 const {npcId,motion,posePreset:motionPosePreset,direction,assetSetSha256}=t.input;
 const binding={claim:{officialRequestId:row.officialRequestId,npcId},motion,motionPosePreset,direction,assetSetSha256,
  predecessorTaskId:t.dependsOn,sourcePortraitStage:'portrait_combat_'+direction};
 let stage='portrait_'+direction;
 const p=postprocessPorts({transport:async()=>Buffer.from(JSON.stringify({direction,motion,posePreset:motionPosePreset,assetSetSha256,
  sequence:{schemaVersion:'ngs.animation-native-png-sequence.v1',npcId,presetId:motion,posePreset:motionPosePreset,direction,
   requestId:t.dependsOn,frameCount:2,frames:[{},{}],
   sourceBindingHash:sha(canonical([row.officialRequestId,stage,motion,motionPosePreset,assetSetSha256]))}}))});
 await assert.rejects(p.read(binding),/POSTPROCESS_PDS_SELECTED_SOURCE_BINDING/);
 stage=binding.sourcePortraitStage;
 assert.equal((await p.read(binding)).posePreset,'combat');
});
test('matches Planner golden media for replacement and new generation',()=>{
 for(const {row,tasks} of cases){
  const watched=tasks.filter(t=>t.workerType==='postprocess');
  const db={official_request_id:row.officialRequestId,request_type:row.requestType,project_id:row.projectId,
   schema_version:row.schemaVersion,state:'planned',reason:null,payload:canonical(row.payload),payload_sha256:row.payloadSha256,
   registered_at:row.registeredAt};
  for(const t of watched){
   const b=selectedMotionBranch(db,t.key);assert.deepEqual(b.target,t);
   assert.deepEqual(b.parent,tasks.find(candidate=>candidate.id===t.dependsOn));
  }
 }
});

test('regenerated frames wait for exact same-run staged raw and expose only a confirmed selected output',async t=>{
 const f=fixture(t,'regenerate'),target=selected(f);
 assert.equal(nextPostprocessFromPath(f.dbPath),null);
 const other=selected(f,'run-2d');
 f.finish(other.dependsOn);
 assert.equal(nextPostprocessFromPath(f.dbPath).taskId,other.id);
 f.finish(target.dependsOn);
 let publishCalls=0;
 const result=await runPostprocessFromPath({...f,taskId:target.id,officialRequestId:f.row.officialRequestId,
  pds:{
   read:async binding=>{
    assert.equal(binding.predecessorTaskId,target.dependsOn);assert.equal(binding.motionPosePreset,'combat');
    assert.equal(binding.motion,'sword-attack-flat');return {};
   },
   publish:async()=>{publishCalls++;return {ok:true};}},
  render:async()=>({artifact:{}})});
 assert.equal(publishCalls,1);assert.equal(result.state,'completed');
 assert.equal(result.resultRef,'canonical-files/binari/npcs/'+f.row.payload.npcId+'/animation-processed-directions/combat/sword-attack-flat/n.json');
 assert.equal(f.byId(other.id).status,'pending');
});

test('unconfirmed final regeneration write stays unreconciled, without publishing a result ref',async t=>{
 const f=fixture(t,'regenerate'),target=selected(f);
 f.finish(target.dependsOn);
 const result=await runPostprocessFromPath({...f,taskId:target.id,officialRequestId:f.row.officialRequestId,
  pds:{read:async()=>({}), publish:async()=>{throw Error('response lost');}},render:async()=>({artifact:{}})});
 assert.equal(result.state,'requires_reconciliation');
 assert.deepEqual({...f.byId(target.id)},{status:'running',result:null});
});
