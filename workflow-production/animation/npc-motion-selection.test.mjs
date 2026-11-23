import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,realpathSync,rmSync,chmodSync,readFileSync,mkdirSync,writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {DatabaseSync} from 'node:sqlite';
import {choose,claim,execute} from './animation-cli.mjs';
import {canonical,selectedMotionBranch} from './vendor/npc-motion-selection-v1/media-branch.mjs';
import {buildPlan as documentProjection} from '../document/vendor/planner-npc-motion-selection-v1/planner.mjs';
import {buildPlan as fileProjection} from '../file/vendor/planner-npc-motion-selection-v1/planner.mjs';
import {buildPlan as imageProjection} from '../image/vendor/planner-npc-motion-selection-v1/planner.mjs';
import {presetArtifact} from '../document/preset.mjs';
import {sha,motionSelectionSlot} from './vendor/npc-motion-selection-v1/envelope.mjs';

const cases=JSON.parse(readFileSync(new URL('./vendor/npc-motion-selection-v1/golden-media.fixture.json',import.meta.url)));
const poseCases=JSON.parse(readFileSync(new URL('./vendor/npc-motion-selection-v1/npc-one-stop-motion-selection.v2.fixture.json',import.meta.url))).cases;
const regeneratePoseGolden=JSON.parse(readFileSync(new URL('./vendor/npc-motion-selection-v1/npc-animation-regenerate.v2.fixture.json',import.meta.url)));
const regenerateGolden=JSON.parse(readFileSync(new URL('./vendor/npc-motion-selection-v1/npc-animation-regenerate.v1.fixture.json',import.meta.url)));
const regenerateCase=()=>({row:regenerateGolden.row,tasks:regenerateGolden.plan.tasks.filter(t=>t.workerType==='animation')});
const generationId='a'.repeat(48),resultBasisSha256='6'.repeat(64);
const title='npc-animation workflow fixture';
const dummyPNG=Buffer.from('not production pixels');
const dummyMP4=Buffer.from('not production video');
function fixture(t,which=0){
 const {row,tasks}=which==='regenerate'?regenerateCase():which==='pose'?poseCases[0]:
  which==='regen-pose'?{row:regeneratePoseGolden.row,tasks:regeneratePoseGolden.plan.tasks.filter(t=>t.workerType==='animation')}:cases[which];
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


const dbRow=(row)=>({official_request_id:row.officialRequestId,request_type:row.requestType,project_id:row.projectId,
 schema_version:row.schemaVersion,state:'planned',reason:null,payload:canonical(row.payload),payload_sha256:row.payloadSha256,registered_at:row.registeredAt});

test('pose v2 worker projections agree with exact Planner golden task bytes',()=>{
 for(const {row,tasks} of poseCases){
  const d=dbRow(row);
  for(const projection of [documentProjection,imageProjection,fileProjection]){
   const got=projection(row);
   assert.equal(got.payload,d.payload);
   assert.deepEqual(got.tasks,tasks.filter(t=>t.workerType==='document'||t.workerType==='image'||
    t.workerType==='file'||t.key==='portrait_pixel_idle_reference'||t.key==='portrait_pixel_combat_reference'));
  }
  for(const task of tasks.filter(t=>t.key.includes('_motion_')||t.key.startsWith('portrait_pixel_'))){
   const branch=selectedMotionBranch(d,task.key);
   assert.deepEqual(branch.target,task);
   assert.deepEqual(branch.parent,tasks.find(t=>t.id===task.dependsOn));
  }
 }
 const {row,plan}=regeneratePoseGolden;
 for(const target of plan.tasks){
  const branch=selectedMotionBranch(dbRow(row),target.key);
  assert.deepEqual(branch.target,target);
  assert.deepEqual(branch.parent,plan.tasks.find(t=>t.id===target.dependsOn)??null);
  assert.equal(branch.sourcePortraitStage,(target.input.posePreset==='combat'?'portrait_combat_':'portrait_')+target.input.direction);
 }
});

test('two arbitrarily named combat presets share one same-direction final pixel step',()=>{
 const row=structuredClone(poseCases[0].row);
 row.payload.motionSelection.push({presetId:'neutral-sounding-alias',posePreset:'combat',assetSetSha256:'4'.repeat(64)});
 row.payloadSha256=sha(canonical(row.payload));
 for(const d of ['n','se','w']){
  const a=selectedMotionBranch(dbRow(row),'animation_motion_'+motionSelectionSlot('sword-attack-flat')+'_'+d);
  const b=selectedMotionBranch(dbRow(row),'animation_motion_'+motionSelectionSlot('neutral-sounding-alias')+'_'+d);
  const idle=selectedMotionBranch(dbRow(row),'animation_motion_'+motionSelectionSlot('run-2d')+'_'+d);
  assert.deepEqual(a.parent,b.parent);assert.equal(a.parent.key,'portrait_pixel_combat_'+d);
  assert.notDeepEqual(a.parent,idle.parent);assert.equal(idle.parent.key,'portrait_pixel_idle_'+d);
  assert.equal(a.sourcePortraitStage,'portrait_combat_'+d);
 }
 const head=documentProjection(row).tasks;
 assert.equal(head.filter(t=>t.key==='planning_combat').length,1);
 const role='portrait.combat';
 const next=presetArtifact(row.payload,head.find(t=>t.key==='preset').id,{selectedPlanningRoles:[role]});
 assert.equal(next.profile.planningCategories.filter(v=>v.planningRole===role).length,1);
 const legacy=presetArtifact(row.payload,head.find(t=>t.key==='preset').id);
 assert.equal(legacy.profile.planningCategories.some(v=>v.planningRole===role),false);
 assert.equal(legacy.presetRegistrySha256,next.presetRegistrySha256);
});

test('animation v2 waits for exact final matching pose pixel and refuses tampered pose before any claim',t=>{
 const f=fixture(t,'pose');
 f.finish('portrait_combat_n');
 f.finish('portrait_pixel_idle_ne');
 const idle=choose(f.path);
 assert(idle&&idle.motion==='run-2d'&&idle.direction==='ne');
 f.finish(idle.key);
 assert.equal(choose(f.path),undefined);
 f.finish('portrait_pixel_combat_n');
 const target=choose(f.path);
 assert.equal(target.parent,'portrait_pixel_combat_n');
 f.db.prepare("UPDATE workflow_tasks SET task_input=json_set(task_input,'$.posePreset','idle') WHERE task_key='portrait_pixel_combat_n'").run();
 assert.throws(()=>claim(f.db,target),/ANIMATION_DB_SELECTED_TARGET/);
 assert.equal(f.status(target.key),'pending');
});

test('animation-only v2 consumes existing combat asset without preparing any portrait task',async t=>{
 const f=fixture(t,'regen-pose');
 const target=choose(f.path);
 assert(target&&target.parent===null);
 const calls=await send(f);
 assert(calls.includes('/v1/npcs/ownership/workflow-animation'));
 assert.equal(f.status(target.key),'completed');
});

test('combat chain consumes exact art/pose documents, representative PNG and combat center correction',async t=>{
 const {runDocumentFromPath}=await import('../document/worker.mjs');
 const {planningHeadings}=await import('../document/planning-markdown.mjs');
 const {runPortraitFromPath,nextPortraitFromPath}=await import('../image/worker.mjs');
 const {nextPostprocessFromPath,runPostprocessFromPath}=await import('../postprocess/postprocess-worker.mjs');
 const f=fixture(t,'pose'), {row,tasks}=f, task=key=>tasks.find(x=>x.key===key);
 const {projectId,officialRequestId,payload:{character}}=row;
 const definition=(op)=>'# NPC fixture\n'+planningHeadings[op].map((h,i)=>'## '+h+'\n'+op+' 내용 '+i).join('\n');
 const category=(key)=>'category.preset.'+sha(canonical(['npc.standard','3',task('preset').id,key])).slice(0,32);
 const art=definition('npc.plan-portrait-art'),rep=definition('npc.plan-representative'),combat=definition('npc.plan-combat');
 const repPng=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=','base64');
 const pixelPng=Buffer.concat([repPng,Buffer.from('different-corrected-pixel')]);
 const reference=bytes=>({base64:bytes.toString('base64'),sha256:sha(bytes),assetId:sha(bytes)+'.png'});
 const statAsset=bytes=>({sha256:sha(bytes),assetId:sha(bytes)+'.png',mimeType:'image/png',bytes:bytes.length});
 const dir=join(f.root,'snapshot');mkdirSync(dir,{mode:0o700});
 mkdirSync(join(dir,'planning'),{mode:0o700});mkdirSync(join(dir,'concept'),{mode:0o700});
 const files=[];
 for(const [key,role,content] of [['portrait-art','portrait.art',art],['portrait.representative','portrait.representative',rep],['portrait.combat','portrait.combat',combat]]){
  const id=category(key),path=id+'.json',file=join(dir,'planning',path),text=canonical({id,planningRole:role,content});
  writeFileSync(file,text,{mode:0o600});files.push({scope:'binari/npcs/'+character.npcId+'/planning',path,file,sha256:sha(text),size:Buffer.byteLength(text)});
 }
 const master='{}',file=join(dir,'concept','master.json');writeFileSync(file,master,{mode:0o600});
 files.push({scope:'binari/concept',file,path:'master.json',sha256:sha(master),size:2});
 const snapshot=async(_,fn)=>fn({root:dir,files});
 let stage;
 const withBase=key=>({dbPath:f.path,officialRequestId,taskId:task(key).id,withPlanningSnapshot:snapshot});
 const cat=category('portrait.combat');
 f.finish('planning_portrait_art');f.finish('planning_representative');
 const doc=await runDocumentFromPath({...withBase('planning_combat'),operation:'npc.plan-combat',pds:{
  async readCombat(){return {character,basis:{categoryId:cat},presetContext:canonical({creationBasis:{},planningPreset:{},
   category:{id:cat,planningRole:'portrait.combat',content:''},chapters:[],status:'active'}),portraitArtContent:art,representativeContent:rep};},
  async publishCombat(cmd,_,basis){const dto=JSON.parse(cmd.artifact);assert.equal(dto.schemaVersion,'workflow.npc-combat-plan-artifact.v1');
   assert.equal(dto.categoryId,basis.categoryId);assert.equal(dto.content,combat);stage='planned';return {ok:true};}},
  render:async(input,opts)=>{assert(opts.fourDoc);assert.equal(input.portraitArtContent,art);assert.equal(input.representativeContent,rep);return combat;}});
 assert.equal(doc.state,'completed');assert.equal(stage,'planned');
 const center=()=>runPortraitFromPath({...withBase('portrait_combat_reference'),operation:'npc.portrait-combat-reference',pds:{
  async readPortrait(){return {character,basis:{categoryId:cat},planningContent:combat,reference:reference(repPng)};},
  async publishPortrait(cmd){assert.equal(JSON.parse(cmd.artifact).schemaVersion,'workflow.npc-combat-reference-portrait-artifact.v1');stage='center';return {ok:true};}},
  render:async input=>{assert.equal(input.posePreset,'combat');assert.equal(input.reference.assetId,reference(repPng).assetId);
   assert.match(input.poseDirection,/npc.plan-combat/);return repPng;}});
 assert.equal((await center()).state,'not_claimable');
 f.finish('portrait_representative');
 assert.equal((await center()).state,'completed');assert.equal(stage,'center');
 assert.equal(nextPostprocessFromPath(f.path,{frames:false})?.taskId,task('portrait_pixel_combat_reference').id);
 const correction=await runPostprocessFromPath({...withBase('portrait_pixel_combat_reference'),pds:{
  async read(input){assert.equal(input.posePreset,'combat');assert.equal(input.direction,null);return statAsset(repPng);},
  async publish(input,_,{artifact}){assert.equal(input.posePreset,'combat');assert.equal(artifact.sourceAssetId,reference(repPng).assetId);
   stage='corrected';return {ok:true};}},
  render:async source=>({artifact:{sourceAssetId:source.assetId,base64:pixelPng.toString('base64')}})});
 assert.equal(correction.state,'completed');assert.equal(stage,'corrected');
 assert.equal(nextPortraitFromPath(f.path).operation,'npc.portrait-combat-direction');
 const dirOp=referenceStage=>runPortraitFromPath({...withBase('portrait_combat_n'),operation:'npc.portrait-combat-direction',pds:{
  async readPortrait(){return {character,basis:{categoryId:cat,referenceStage,referenceAssetId:reference(pixelPng).assetId},
   planningContent:combat,reference:reference(pixelPng),direction:'n'};},
  async publishPortrait(cmd){assert.equal(JSON.parse(cmd.artifact).schemaVersion,'workflow.npc-combat-direction-portrait-artifact.v1');
   stage='direction';return {ok:true};}},render:async input=>{assert.equal(input.posePreset,'combat');assert.equal(input.direction,'n');
   assert.equal(input.reference.assetId,reference(pixelPng).assetId);return repPng;}});
 assert.equal((await dirOp('portrait_pixel_idle_reference')).state,'failed');
 assert.equal(stage,'corrected');
 f.db.prepare("UPDATE workflow_tasks SET updated_at=? WHERE task_key='portrait_combat_n'").run(Date.now()-601_000);
 assert.equal((await dirOp('portrait_pixel_combat_reference')).state,'completed');
 assert.equal(stage,'direction');
});
