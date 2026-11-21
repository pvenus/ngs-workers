import test from 'node:test';
import assert from 'node:assert/strict';
import {chmodSync,mkdtempSync,realpathSync,rmSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {execFileSync} from 'node:child_process';
import {sha,canonical} from './vendor/planner-pixel-chain/envelope.mjs';
import {PlannerStore} from './vendor/planner-pixel-chain/planner.mjs';
import {postprocessPorts} from './postprocess-ports.mjs';
import {nextPostprocessFromPath,runPostprocessFromPath} from './postprocess-worker.mjs';
import {createPostprocessRenderer} from './postprocess-provider.mjs';

const route='/v1/npcs/ownership/workflow-portrait-pixel';
function fixture(t){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'postprocess-static-fixture-'))),dbPath=join(root,'db');
 const s=new PlannerStore(dbPath);chmodSync(dbPath,0o600);t.after(()=>{s.close();rmSync(root,{recursive:true,force:true});});
 const officialRequestId='request.official.'+sha('fixture');
 const payload={schemaVersion:'ngs.npc-one-stop.replace.v1',projectId:'binari',intent:'fixture',
  character:{npcId:'npc.ch1_fixture',name:'Fixture',slug:'fixture',role:'fixture',actId:'act1',chapterId:'ch1'},
  sourceRefs:['story.fixture'],includeCombat:false};
 s.stage({officialRequestId,projectId:'binari',requestType:'npc.workflow',schemaVersion:payload.schemaVersion,
  payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1});s.acknowledge(officialRequestId);
 const row=key=>s.db.prepare('SELECT * FROM workflow_tasks WHERE task_key=?').get(key);
 const complete=key=>s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key=?").run(key);
 const select=()=>nextPostprocessFromPath(dbPath,{frames:false,portraits:true});
 return {root,dbPath,officialRequestId,row,complete,select,s};
}
function transportFor(f,{raw=Buffer.from('fixture'),direction=null,fail=false,readSource=null}={}){
 const source={assetId:sha(raw)+'.png',sha256:sha(raw),bytes:raw.length,mimeType:'image/png'};
 const calls=[];
 const json=value=>Buffer.from(JSON.stringify(value));
 const transport=async req=>{
  calls.push(req);
  if(req.path===route&&req.body===undefined)return json({enabled:true,schemaVersion:'workflow.portrait-pixel-write.v1'});
  if(req.path===route+'/read'){
   assert.deepEqual(Object.keys(req.body),['claim']);
   return json({posePreset:'idle',direction,source:readSource??source});
  }
  if(req.path===route+'/asset'){
   assert.equal(req.body.assetId,source.assetId);
   assert.equal(req.length,source.bytes);return raw;
  }
  if(req.path===route){
   if(fail)throw Error('fixture ack lost');
   assert.deepEqual(Object.keys(req.body),['claim','artifact']);
   assert.deepEqual(Object.keys(req.body.artifact),['sourceAssetId','base64']);
   assert.equal(req.body.artifact.sourceAssetId,source.assetId);
   return json({ok:true});
  }
  throw Error('unexpected route');
 };
 return {pds:postprocessPorts({transport}),calls,source};
}
test('exact center/direct postprocess use existing raw, while animation dependency stays original',async t=>{
 const f=fixture(t);f.complete('portrait_representative');
 assert.equal(f.select(),null);
 f.complete('portrait_idle_reference');
 assert.equal(nextPostprocessFromPath(f.dbPath,{frames:true,portraits:false}),null);
 for(const direction of [null,'nw']){
  if(direction)f.complete('portrait_nw');
  const pixel=f.row('portrait_pixel_idle_'+(direction??'reference'));
  const original=f.row(direction?'portrait_nw':'portrait_idle_reference');
  const before=structuredClone(original);
  const target=f.select();assert.equal(target.taskId,pixel.task_id);
  const {pds,calls,source}=transportFor(f,{direction});
  assert.equal(await pds.capabilityPortrait(),true);
  const result=await runPostprocessFromPath({...f,...target,pds,render:async(s,b)=>{
   assert.equal(f.row(pixel.task_key).status,'running');
   assert.equal(b.posePreset,'idle');assert.equal(b.direction,direction);
   assert.equal(b.predecessorTaskId,original.task_id);
   assert.deepEqual(s,source);await pds.fetchPortraitAsset(b.claim,s);
   return {artifact:{sourceAssetId:s.assetId,base64:'fixture-base64'}};
  }});
  assert.equal(result.state,'completed');assert.equal(f.row(pixel.task_key).result_ref,null);
  assert.deepEqual({...f.row(original.task_key)},before);
  assert.deepEqual(calls.slice(1).map(c=>c.path),[route+'/read',route+'/asset',route]);
  assert.equal(f.row('animation_walk_nw').depends_on_task_id,f.row('portrait_nw').task_id);
 }
 assert.equal(f.select(),null);
});
test('static source tampering is definitively prepublish; lost write ACK is not retried',async t=>{
 const f=fixture(t);f.complete('portrait_idle_reference');
 const target=f.select();
 const bad=transportFor(f,{readSource:{assetId:'wrong'}});
 const failed=await runPostprocessFromPath({...f,...target,pds:bad.pds,render:async()=>{throw Error('must not render');}});
 assert.equal(failed.state,'failed');
 assert.equal(f.select(),null);
 f.s.db.prepare('UPDATE workflow_tasks SET updated_at=? WHERE task_id=?').run(Date.now()-601000,target.taskId);
 const good=transportFor(f,{fail:true});
 const uncertain=await runPostprocessFromPath({...f,...target,pds:good.pds,render:async(s)=>({artifact:{sourceAssetId:s.assetId,base64:'fixture'}})});
 assert.equal(uncertain.state,'requires_reconciliation');
 const pixel=f.row('portrait_pixel_idle_reference');assert.equal(pixel.retry_count,1);assert.equal(pixel.status,'running');
 assert.equal(f.select(),null);
});
test('malformed artifact fails definitively before its HTTP publish',async t=>{
 const f=fixture(t);f.complete('portrait_idle_reference');
 const {pds,calls}=transportFor(f);
 const result=await runPostprocessFromPath({...f,...f.select(),pds,
  render:async()=>({artifact:{sourceAssetId:'wrong',base64:'fixture'}})});
 assert.equal(result.state,'failed');assert.equal(f.row('portrait_pixel_idle_reference').status,'failed');
 assert.deepEqual(calls.map(c=>c.path),[route+'/read']);
});
test('wrong raw lineage or fabricated representative pixel is not dispatched',async t=>{
 const f=fixture(t);f.complete('portrait_idle_reference');
 const center=f.select();
 f.s.db.prepare("UPDATE workflow_tasks SET task_input=? WHERE task_key='portrait_idle_reference'")
   .run(canonical({npcId:'npc.ch1_fixture'}));
 const pds={read:async()=>assert.fail('read forbidden'),publish:async()=>assert.fail('publish forbidden')};
 await assert.rejects(runPostprocessFromPath({...f,...center,pds,render:async()=>assert.fail('render forbidden')}),/PREDECESSOR_BINDING_INVALID/);
 assert.equal(f.row('portrait_pixel_idle_reference').status,'pending');
 assert.equal(f.row('portrait_pixel_representative'),undefined);
});
const python=process.env.PIXEL_FIXTURE_PYTHON;
test('existing postprocess provider calls real static resize+cleanup subprocess with no model', {skip:!python},async t=>{
 const f=fixture(t);f.complete('portrait_idle_reference');
 const raw=execFileSync(python,['-B','-c','from PIL import Image;import sys; im=Image.new("RGBA",(256,192));im.paste((50,60,70,255),(8,7,240,170));im.save(sys.stdout.buffer,format="PNG")']);
 const {pds,calls}=transportFor(f,{raw});
 const configPath=join(f.root,'config.json'),modelPinsPath=join(f.root,'pins.json'),matte=join(f.root,'matte.fixture'),track=join(f.root,'track.fixture');
 writeFileSync(matte,'unused');writeFileSync(track,'unused');
 writeFileSync(configPath,JSON.stringify({FRAME_ALPHA_PYTHON:python,FRAME_ALPHA_MODULE_ROOT:f.root,
  FRAME_ALPHA_MATTING_MODEL:matte,FRAME_ALPHA_TRACKING_MODEL:track}));
 writeFileSync(modelPinsPath,JSON.stringify({matte:{path:matte},track:{path:track}}));
 const render=await createPostprocessRenderer({configPath,modelPinsPath,workerSource:f.root,samSource:f.root,pds});
 const result=await runPostprocessFromPath({...f,...f.select(),pds,render});
 assert.equal(result.state,'completed');
 const published=Buffer.from(calls.at(-1).body.artifact.base64,'base64');
 assert.equal(published.readUInt32BE(16),128);assert.equal(published.readUInt32BE(20),128);assert.equal(published[25],6);
 assert.notEqual(sha(published),sha(raw));
});
