import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,rmSync,chmodSync,readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {DatabaseSync} from 'node:sqlite';
import {choose,claim,execute} from './animation-cli.mjs';
import {canonical,selectedMotionBranch} from './vendor/npc-motion-selection-v1/media-branch.mjs';
import {sha} from './vendor/npc-motion-selection-v1/envelope.mjs';

const cases=JSON.parse(readFileSync(new URL('./vendor/npc-motion-selection-v1/golden-media.fixture.json',import.meta.url)));
const regenerateGolden=JSON.parse(readFileSync(new URL('./vendor/npc-motion-selection-v1/npc-animation-regenerate.v1.fixture.json',import.meta.url)));
const regenerateCase=()=>({row:regenerateGolden.row,tasks:regenerateGolden.plan.tasks.filter(t=>t.workerType==='animation')});
const generationId='a'.repeat(48),resultBasisSha256='6'.repeat(64);
const title='npc-animation workflow fixture';
const dummyPNG=Buffer.from('not production pixels');
const dummyMP4=Buffer.from('not production video');
function fixture(t,which=0){
 const {row,tasks}=which==='regenerate'?regenerateCase():cases[which];
 const root=realpathSync(mkdtempSync(join(tmpdir(),'npc-motion-selection-'))),path=join(root,'workflow.sqlite');
 const db=new DatabaseSync(path);chmodSync(path,0o600);
 t.after(()=>{db.close();rmSync(root,{recursive:true,force:true});});
 db.exec(`CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,project_id TEXT,request_type TEXT,schema_version TEXT,
 payload TEXT,payload_sha256 TEXT,state TEXT,reason TEXT,registered_at INTEGER);
 CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_key TEXT,task_type TEXT,worker_type TEXT,depends_on_task_id TEXT,
 task_input TEXT,status TEXT,result_ref TEXT,updated_at INTEGER,retry_count INTEGER)`);
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,'planned',NULL,?)")
  .run(row.officialRequestId,row.projectId,row.requestType,row.schemaVersion,canonical(row.payload),row.payloadSha256,row.registeredAt);
 const stmt=db.prepare("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,?,'pending',NULL,1,0)");
 for(const v of tasks)stmt.run(v.id,row.officialRequestId,v.key,v.type,v.workerType,v.dependsOn,canonical(v.input));
 const finish=key=>db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key=?").run(key);
 const status=key=>db.prepare('SELECT status FROM workflow_tasks WHERE task_key=?').get(key).status;
 return {row,tasks,root,path,db,finish,status};
}
const expected=f=>f.row.requestType==='npc.animation.regenerate'?
 f.tasks.toSorted((a,b)=>a.key<b.key?-1:a.key>b.key?1:0)[0]:
 f.tasks.find(x=>x.workerType==='animation'&&x.input.motion==='sword-attack-flat');
const taskLookup=f=>choose(f.path);
const signal=new AbortController().signal;
const send=async(f,{referencePatch={},settingsPatch={}}={})=>{
 const selected=taskLookup(f),{input}=expected(f);
 const regenerate=f.row.requestType==='npc.animation.regenerate';
 const settings={...(regenerate?{generationId,resultBasisSha256}:{}),direction:input.direction,motion:input.motion,portraitAssetId:sha(dummyPNG)+'.png',posePreset:input.posePreset,
  assetSetSha256:input.assetSetSha256,referencePresetHash:'5'.repeat(64),referenceSha256:sha(dummyMP4),...settingsPatch};
 const reference={direction:input.direction,presetId:input.motion,posePreset:input.posePreset,presetHash:'5'.repeat(64),
  assetId:sha(dummyMP4)+'.mp4',sha256:sha(dummyMP4),bytes:dummyMP4.length,base64:dummyMP4.toString('base64'),
  frameCount:2,fps:24,policy:{motionInstruction:title,framingPolicy:{}},...referencePatch};
 const calls=[];
 const transport=async(path,options)=>{
  calls.push(path);
  if(path.endsWith('/read')){
   assert.equal(options.body.claim.taskId,selected.taskId);return settings;
  }
  if(path.endsWith('/asset')){assert.equal(options.body.assetId,settings.portraitAssetId);return dummyPNG;}
  if(path==='/v1/motion-presets/'+input.motion+'/'+input.direction)return reference;
  assert.equal(path,'/v1/npcs/ownership/workflow-animation');
  assert.equal(options.body.artifact.frames.length,2);assert.equal(options.body.artifact.fps,24);
  if(regenerate)assert.deepEqual(options.body.artifact.source,{generationId,portraitAssetId:settings.portraitAssetId,resultBasisSha256});
  else assert.equal(options.body.artifact.source,undefined);
  return {ok:true};
 };
 const native=async(dir)=>{
  assert.equal(readFileSync(join(dir,'motion.mp4')).toString(),dummyMP4.toString());
  assert.equal(readFileSync(join(dir,'portrait.png')).toString(),dummyPNG.toString());
  const job=JSON.parse(readFileSync(join(dir,'job.json')));
  assert.deepEqual([job.fps,job.frameCount,job.motionPrompt],[24,2,title]);
  if(regenerate)assert.equal(job.appearancePrompt.trim(),'Preserve the exact reference character design and costume.');
  mkdirSync(join(dir,'wan','frames'),{recursive:true});
  const frames=['00000.png','00001.png'];
  for(const frame of frames)writeFileSync(join(dir,'wan','frames',frame),dummyPNG);
  return {frames,frameHashes:frames.map(()=>sha(dummyPNG)),fps:24,width:768,height:432};
 };
 await execute(f.db,selected,signal,null,{transport,native});
 return calls;
};
test('exact planner golden directions and prior portrait gate, using the existing Wan execution',async t=>{
 const f=fixture(t);assert.equal(choose(f.path),undefined);
 f.finish('portrait_n');
 const target=taskLookup(f);
 assert.equal(target.taskId,expected(f).id);
 const calls=await send(f);
 assert(calls.includes('/v1/motion-presets/sword-attack-flat/n'));
 assert.equal(f.status(target.key),'completed');
 assert.equal(f.status(f.tasks.find(x=>x.workerType==='animation'&&x.input.motion==='run-2d').key),'pending');
});
test('selected claim rejects wrong portrait lineage independent of current status',t=>{
 const f=fixture(t,1);f.finish('portrait_n');
 const target=taskLookup(f);
 assert.equal(target.taskId,expected(f).id);
 f.db.prepare("UPDATE workflow_tasks SET task_input='{}' WHERE task_key='portrait_n'").run();
 assert.throws(()=>claim(f.db,target),/ANIMATION_DB_SELECTED_TARGET/);
 assert.equal(f.status(target.key),'pending');
});
test('selected preset hash/pose are exact before the renderer or publish',async t=>{
 const f=fixture(t);f.finish('portrait_n');
 const target=taskLookup(f);
 const calls=await send(f,{referencePatch:{presetHash:'6'.repeat(64)}});
 assert(calls.includes('/v1/motion-presets/sword-attack-flat/n'));
 assert(!calls.includes('/v1/npcs/ownership/workflow-animation'));
 assert.equal(f.status(target.key),'failed');
});
test('both golden schema payloads project exact planner media tasks',()=>{
 for(const {row,tasks} of cases){
  const db={official_request_id:row.officialRequestId,request_type:row.requestType,project_id:row.projectId,
   schema_version:row.schemaVersion,state:'planned',reason:null,payload:canonical(row.payload),payload_sha256:row.payloadSha256,
   registered_at:row.registeredAt};
  for(const task of tasks.filter(t=>t.key.includes('_motion_')||t.key.startsWith('portrait_pixel_'))){
   const branch=selectedMotionBranch(db,task.key);
   assert.deepEqual(branch.target,task);
   assert.deepEqual(branch.parent,tasks.find(t=>t.id===task.dependsOn));
  }
 }
});

test('animation-only regeneration claims a null dependency and forwards the exact render-start scoped publication fence',async t=>{
 const f=fixture(t,'regenerate'),target=taskLookup(f);
 assert.equal(target.parent,null);assert.equal(target.taskId,expected(f).id);
 const calls=await send(f);
 assert.deepEqual(calls,[
  '/v1/npcs/ownership/workflow-animation/read','/v1/npcs/ownership/workflow-animation/asset',
  '/v1/motion-presets/'+target.motion+'/'+target.direction,'/v1/npcs/ownership/workflow-animation']);
 assert.equal(f.status(target.key),'completed');
 assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM workflow_tasks WHERE depends_on_task_id IS NOT NULL').get().n,0);
});

test('regen rejects a missing, stale-shaped or tampered basis and does not invent a source',async t=>{
 for(const patch of [{generationId:null},{resultBasisSha256:undefined},{resultBasisSha256:'X'.repeat(64)}]){
  const f=fixture(t,'regenerate');
  const target=taskLookup(f);
  const calls=await send(f,{settingsPatch:patch});
  assert.deepEqual(calls,['/v1/npcs/ownership/workflow-animation/read']);assert.equal(f.status(target.key),'failed');
 }
 const f=fixture(t,'regenerate'),target=taskLookup(f);
 const other=f.tasks.find(x=>x.id!==target.taskId);
 f.db.prepare("UPDATE workflow_tasks SET depends_on_task_id=? WHERE task_id=?").run(other.id,target.taskId);
 assert.equal(claim(f.db,target),null);
 assert.equal(f.status(target.key),'pending');
});
