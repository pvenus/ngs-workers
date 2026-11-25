import test from 'node:test';
import assert from 'node:assert/strict';
import {chmodSync,mkdtempSync,readdirSync,realpathSync,rmSync} from 'node:fs';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {planningHeadings} from './planning-markdown.mjs';
import {imageTick,parseImageArgs,startImageHeartbeat} from './image-runner.mjs';
import {localPorts,portraitPath,idlePortraitPath,directionPortraitPath,requestLocalJson} from './local-ports.mjs';
import {createPlanningFolderDownloader} from './planning-folders.mjs';
import {PlannerStore,directions} from './vendor/planner-f24d23a/planner.mjs';
import {PlannerStore as PixelPlannerStore} from './vendor/planner-pixel-sprite-v1/planner.mjs';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';

const addRetryCount=db=>db.exec('ALTER TABLE workflow_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 3)');
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=','base64');
const categoryId='category.preset.'+'c'.repeat(32);
const plan='current representative plan only';
const markdown=(operation,imageBlock)=>'# Title\n'+planningHeadings[operation].map((title,i)=>
 '## '+title+'\n'+(i===0?imageBlock:'UNSENT explanatory detail')).join('\n');
const artContent=markdown('npc.plan-portrait-art','exact ART block');
const repContent=markdown('npc.plan-representative','exact REPRESENTATIVE POSE block');
const idleContent=markdown('npc.plan-idle','exact IDLE POSE block');
const artIdFor=officialRequestId=>'category.preset.'+sha(canonical(['npc.standard','3',
 'workflow.task.'+sha(canonical([officialRequestId,'preset'])),'portrait-art'])).slice(0,32);
const cap={enabled:true,schemaVersion:'workflow.representative-portrait-write.v1'};

for(const mode of ['normal','new-art','missing-art','missing-pose','fresh-no-fallback','aged retry','capped retry','version-bearing basis','stale plan','invalid PNG','renderer error','uncertain ACK','publish disconnect'])test('representative image resident: '+mode,async t=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'image-worker-test-'))),dbPath=join(root,'workflow.sqlite');
 const s=new PlannerStore(dbPath);addRetryCount(s.db);
 s.db.exec('ALTER TABLE workflow_tasks ADD COLUMN portrait_read_owner_pid INTEGER');chmodSync(dbPath,0o600);
 t.after(()=>{s.close();rmSync(root,{recursive:true,force:true});});
 const payload={schemaVersion:'ngs.npc-one-stop.replace.v1',projectId:'binari',intent:'official figure portrait',
  character:{npcId:'npc.ch1_dajeong',name:'다정',slug:'dajeong',role:'fixture',actId:'act1',chapterId:'ch1'},
  sourceRefs:['story.fixture'],includeCombat:false};
 const payloadSha256=sha(canonical(payload));
 const enroll=(label,registeredAt)=>{
  const officialRequestId='request.official.'+sha(label);
  s.stage({officialRequestId,projectId:'binari',requestType:'npc.workflow',schemaVersion:payload.schemaVersion,
   payload,payloadSha256,status:'registered',registeredAt});
  s.acknowledge(officialRequestId);return officialRequestId;
 };
 // Older blocked job never leads the queue. Another newer ready portrait
 // remains pending when the selected tick completes.
 const old=enroll('blocked-old',1),officialRequestId=enroll('target',2),later=enroll('later',3);
 const get=(rid,key='portrait_representative')=>s.db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? AND task_key=?').get(rid,key);
 const taskId=get(officialRequestId).task_id,calls=[],phases=[];
 const ageOut=key=>s.db.prepare("UPDATE workflow_tasks SET updated_at=? WHERE task_id=? AND status='failed'").run(Date.now()-601_000,key);
 const isRetry=mode==='aged retry',capped=mode==='capped retry',success=isRetry||['normal','new-art'].includes(mode);
 const newPose=['new-art','missing-art'].includes(mode),planningContent=newPose?repContent:plan;
 const artId=artIdFor(officialRequestId);
 if(mode==='fresh-no-fallback')s.db.prepare(`INSERT INTO workflow_tasks
  (task_id,official_request_id,task_key,task_type,worker_type,task_input,depends_on_task_id,status,updated_at)
  VALUES(?,?,?,?,?,?,?,'completed',?)`).run('workflow.task.'+sha(canonical([officialRequestId,'planning_portrait_art'])),
  officialRequestId,'planning_portrait_art','npc.plan-portrait-art','document',canonical({npcId:payload.character.npcId}),
  get(officialRequestId,'preset').task_id,Date.now());
 if(isRetry||capped){
  s.db.prepare("UPDATE workflow_tasks SET status='failed',updated_at=? WHERE task_id IN (?,?)")
   .run(Date.now(),taskId,get(old).task_id);
  ageOut(get(old).task_id);
  if(capped){
   ageOut(taskId);
   s.db.prepare('UPDATE workflow_tasks SET retry_count=3 WHERE task_id=?').run(taskId);
  }
 }
 const basis={categoryId};
 const srv=createServer(async(req,res)=>{
  calls.push([req.method,req.url]);let data=cap;
  if(req.method==='POST'){
   let bytes='';for await(const chunk of req)bytes+=chunk;
   const packet=JSON.parse(bytes),running=get(officialRequestId);
   assert.equal(running.status,'running');assert.equal(running.retry_count,isRetry?1:0);
   assert.deepEqual(packet.claim,{officialRequestId,taskId,projectId:'binari',npcId:payload.character.npcId,payloadSha256,claimStamp:running.updated_at});
   if(req.url===portraitPath+'/read'){
    assert.equal(running.portrait_read_owner_pid,process.pid);
    assert.deepEqual(Object.keys(packet),['claim']);data={basis:mode==='version-bearing basis'?{...basis,inputSha256:'a'.repeat(64),generationId:'b'.repeat(48)}:basis,character:payload.character,planningContent};
   }else{
    assert.equal(running.portrait_read_owner_pid,null);
    assert.deepEqual(packet,{claim:packet.claim,basis,artifact:{schemaVersion:'workflow.npc-representative-portrait-artifact.v1',
     taskId,projectId:'binari',npcId:payload.character.npcId,base64:png.toString('base64'),sha256:sha(png)}});
    if(mode==='publish disconnect'){res.destroy();return;}
    data={ok:mode!=='uncertain ACK'};
   }
  }
  res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(data));
 });
 await new Promise(resolve=>srv.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>srv.close(resolve)));
 const pds=localPorts({transport:opts=>{assert.equal(opts.port,8030);return requestLocalJson({...opts,port:srv.address().port});}});
 const concept='binari/concept',planning='binari/npcs/'+payload.character.npcId+'/planning';
 const byScope={
  [concept]:{
   'sections/character/metadata.json':{id:'character',title:'character',summary:'tone',fieldIds:['identity','new.active']},
   'sections/character/fields/identity.json':{id:'identity',label:'identity',value:'approved'},
   'sections/character/fields/new.active.json':{id:'new.active',label:'new.active',value:'all active'},
   'sections/expression/metadata.json':{id:'expression',title:'expression',summary:'style',fieldIds:[]},
   'sections/music/metadata.json':{id:'music',title:'do-not-forward'},
  },
  [planning]:{
   [categoryId+'.json']:{id:categoryId,planningRole:'portrait.representative',content:mode==='stale plan'?'old plan':planningContent},
   ...(['new-art','missing-pose'].includes(mode)?{[artId+'.json']:{id:artId,planningRole:'portrait.art',content:artContent}}:{}),
   'other.json':{content:'do-not-forward'},
  },
 };
 const received=new Set();
 const withPlanningSnapshot=createPlanningFolderDownloader({tempParent:root,call:async(tool,args)=>{
  const byPath=byScope[args.scope];
  const encode=path=>Buffer.from(JSON.stringify(byPath[path]));
  if(tool==='npc_folders_compare'){
   const groups=new Map();
   for(const path of Object.keys(byPath)){
    const folder=path.includes('/')?path.split('/')[0]:'';
    if(!groups.has(folder))groups.set(folder,[]);
    groups.get(folder).push({path,size:encode(path).length,sha256:sha(encode(path))});
   }
   return {scope:args.scope,groups:[...groups].map(([folder,files])=>({folder,files}))};
  }
  received.add(args.scope+'/'+args.path);
  const buffer=encode(args.path);
  assert.equal(args.sha256,sha(buffer));assert.equal(args.offset,0);
  return {...args,size:buffer.length,base64:buffer.toString('base64'),nextOffset:null};
 }});
 let rendered=0;
 const render=async input=>{
  assert.equal(get(officialRequestId).portrait_read_owner_pid,null);
  rendered++;
  if(isRetry){
   // Competing handle cannot claim the now-running retry or increment it again.
   assert.equal((await imageTick({...opts,mode:'execute',officialRequestId,taskId})).state,'not_claimable');
   assert.equal(get(officialRequestId).retry_count,1);
  }
  assert.deepEqual(input.character,payload.character);
  if(mode==='new-art'){
   assert.deepEqual(Object.keys(input).sort(),['artDirection','character','poseDirection']);
   assert.equal(input.artDirection,'exact ART block');assert.equal(input.poseDirection,'exact REPRESENTATIVE POSE block');
  }else{
   assert.equal(input.planningContent,plan);
   assert.equal(input.conceptSources.files.length,4);assert.match(JSON.stringify(input),/all active/);
  }
  assert.doesNotMatch(JSON.stringify(input),/do-not-forward|workflow-planning-folders-|UNSENT/);
  if(mode==='renderer error')throw Error('private fixture error');
  return mode==='invalid PNG'?Buffer.from('bad'):png;
 };
 const opts={mode:'run',dbPath,pds,providerReady:()=>true,render,withPlanningSnapshot,onPhase:p=>phases.push(p)};
 assert.equal((await imageTick({...opts,providerReady:()=>false})).state,'not_ready');
 assert.equal((await imageTick(opts)).state,'idle');
 assert.equal(get(officialRequestId).status,isRetry||capped?'failed':'pending');
 for(const rid of capped?[officialRequestId]:[officialRequestId,later])s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE official_request_id=? AND task_key='planning_representative'").run(rid);
 assert.equal((await imageTick({...opts,mode:'watch'})).state,'ready');
 assert.equal(get(officialRequestId).status,isRetry||capped?'failed':'pending');
 if(capped){
  const before=get(officialRequestId);
  assert.equal((await imageTick(opts)).state,'idle');
  assert.equal((await imageTick({...opts,mode:'execute',officialRequestId,taskId})).state,'not_claimable');
  assert.deepEqual(get(officialRequestId),before);assert.equal(before.retry_count,3);
  assert.equal(calls.filter(([method])=>method==='POST').length,0);return;
 }
 if(isRetry){
  assert.equal((await imageTick({...opts,mode:'execute',officialRequestId,taskId})).state,'not_claimable');
  assert.equal(get(officialRequestId).retry_count,0);
  ageOut(taskId);
 }
 const unaffected=()=>s.db.prepare('SELECT * FROM workflow_tasks WHERE task_id<>? ORDER BY task_id').all(taskId);
 const before=unaffected();
 const result=await imageTick(opts);
 const ambiguous=['uncertain ACK','publish disconnect'].includes(mode),failed=!success&&!ambiguous;
 assert.equal(result.taskId,taskId);assert.equal(result.state,success?'completed':ambiguous?'requires_reconciliation':'failed');
 assert.equal(get(officialRequestId).result_ref,null);
 assert.equal(get(officialRequestId).status,success?'completed':ambiguous?'running':'failed');
 assert.deepEqual(unaffected(),before);assert.equal(get(old).status,isRetry?'failed':'pending');assert.equal(get(later).status,'pending');
 assert.equal(rendered,['stale plan','version-bearing basis','missing-art','missing-pose','fresh-no-fallback'].includes(mode)?0:1);
 assert.equal(received.size,mode==='version-bearing basis'?0:['new-art','missing-pose'].includes(mode)?8:7);assert.equal(readdirSync(root).some(n=>n.startsWith('workflow-planning-folders-')),false);
 assert.deepEqual(calls.filter(([method])=>method==='POST').map(([,path])=>path),
  (success||ambiguous)?[portraitPath+'/read',portraitPath]:[portraitPath+'/read']);
 assert.deepEqual(phases,mode==='version-bearing basis'?['claimed','reading-current-basis','failed']:['stale plan','missing-art','missing-pose','fresh-no-fallback'].includes(mode)?['claimed','reading-current-basis','verifying-source-folders','failed']:
  ['claimed','reading-current-basis','verifying-source-folders','rendering',...(success||ambiguous?['publishing']:[]),...(success?['completed']:failed?['failed']:[])]);
 assert.equal((await imageTick({...opts,mode:'execute',officialRequestId,taskId})).state,'not_claimable');
});

test('resident/one-shot image CLI is explicit and cannot select other operations',()=>{
 const db='/private/workflow.sqlite';
 assert.deepEqual(parseImageArgs(['--run','--db',db]),{mode:'run',dbPath:db});
 assert.deepEqual(parseImageArgs(['--watch','--db',db]),{mode:'watch',dbPath:db});
 assert.throws(()=>parseImageArgs(['--run','--db',db,'--operation','npc.portrait-idle-reference']));
 assert.throws(()=>parseImageArgs(['--execute','--db',db]));
});


test('OPS pulse is independent of a held asynchronous render and stops when closed',async t=>{
 const calls=[],pds={async heartbeat(signal,name){calls.push(name);return true;}};
 const heartbeat=startImageHeartbeat({pds,intervalMs:10});
 t.after(heartbeat.close);
 let releaseStarted,releaseRender;
 const started=new Promise(r=>{releaseStarted=r;});
 const done=imageTick({mode:'run',dbPath:'/unused',onHealth:heartbeat.setReady},{
  check:async()=>({ready:true}),select:()=>({taskId:'task',officialRequestId:'official'}),
  execute:()=>{releaseStarted();return new Promise(r=>{releaseRender=r;});},
 });
 await started;
 await new Promise(resolve=>setTimeout(resolve,65));
 assert(calls.length>=2&&calls.every(name=>name==='image-worker'));
 releaseRender({state:'completed'});assert.equal((await done).state,'completed');
 heartbeat.close();const count=calls.length;
 await new Promise(resolve=>setTimeout(resolve,25));assert.equal(calls.length,count);
});


for(const [pixelStyle,markdown] of [[false,false],[true,false],[true,true]])test('resident handles center and eight directions; corrected center only for new style '+pixelStyle+' markdown='+markdown,async t=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'image-worker-dag-'))),dbPath=join(root,'workflow.sqlite');
 const s=new (pixelStyle?PixelPlannerStore:PlannerStore)(dbPath);if(!pixelStyle)addRetryCount(s.db);chmodSync(dbPath,0o600);
 t.after(()=>{s.close();rmSync(root,{recursive:true,force:true});});
 const officialRequestId='request.official.'+sha('portrait chain');
 const character={npcId:'npc.ch1_dajeong',name:'다정',slug:'dajeong',role:'fixture',actId:'act1',chapterId:'ch1'};
 const payload={schemaVersion:'ngs.npc-one-stop.replace.v1',projectId:'binari',intent:'use approved costume',character,
  sourceRefs:['story.fixture'],includeCombat:false};
 const payloadSha256=sha(canonical(payload));
 s.stage({officialRequestId,projectId:'binari',requestType:'npc.workflow',schemaVersion:payload.schemaVersion,payload,
  payloadSha256,status:'registered',registeredAt:15});
 s.acknowledge(officialRequestId);
 const setCompleted=key=>s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key=?").run(key);
 const row=key=>s.db.prepare('SELECT * FROM workflow_tasks WHERE task_key=?').get(key);
 let allow=false,wrongDirection=false,wrongReference=false;
 const publications=[],images=[];
 const reference={assetId:sha(png)+'.png',sha256:sha(png),base64:png.toString('base64')};
 const correctedBytes=Buffer.concat([png,Buffer.from([1])]);
 const correctedReference={assetId:sha(correctedBytes)+'.png',sha256:sha(correctedBytes),base64:correctedBytes.toString('base64')};
 const pds=localPorts({transport:async ({path,method,body})=>{
  let idle=path.startsWith(idlePortraitPath),directional=path.startsWith(directionPortraitPath);
  if(method==='GET')return {enabled:!idle||allow,schemaVersion:idle?'workflow.idle-reference-portrait-write.v1':
   directional?'workflow.direction-portrait-write.v1':cap.schemaVersion};
  assert(idle||directional);
  const active=s.db.prepare("SELECT * FROM workflow_tasks WHERE status='running'").all();
  assert.equal(active.length,1);
  const [running]=active;
  assert.deepEqual(body.claim,{officialRequestId,taskId:running.task_id,projectId:'binari',npcId:character.npcId,payloadSha256,claimStamp:running.updated_at});
  const direction=JSON.parse(running.task_input).direction;
  const pixelDirection=pixelStyle&&directional;
  const basis={categoryId,...(pixelDirection?{referenceStage:'portrait_pixel_idle_reference',referenceAssetId:correctedReference.assetId}:{})};
  const inputRef=pixelDirection&&!wrongReference?correctedReference:reference;
  if(path.endsWith('/read'))return {basis,character,planningContent:idleContent,reference:inputRef,...(directional?{direction:wrongDirection?'w':direction}:{})};
  assert.deepEqual(body.basis,basis);
  assert.deepEqual(body.artifact,{schemaVersion:idle?'workflow.npc-idle-reference-portrait-artifact.v1':'workflow.npc-direction-portrait-artifact.v1',
   taskId:running.task_id,projectId:'binari',npcId:character.npcId,sha256:reference.sha256,base64:reference.base64});
  publications.push(running.task_key);return {ok:true};
 }});
 const concept='binari/concept',planning='binari/npcs/'+character.npcId+'/planning';
 const docs={
  [concept]:Object.fromEntries(['character','expression'].map(id=>['sections/'+id+'/metadata.json',{id,title:id,fieldIds:[],summary:'scope'}])),
  [planning]:{[categoryId+'.json']:{id:categoryId,planningRole:'portrait.idle',content:idleContent},
   [artIdFor(officialRequestId)+'.json']:{id:artIdFor(officialRequestId),planningRole:'portrait.art',content:artContent},
   'excluded.json':{secret:'never send this'}},
 };
 if(markdown){
  for(const id of [categoryId,artIdFor(officialRequestId)]){
   const {content,...meta}=docs[planning][id+'.json'];
   docs[planning][id+'.json']={...meta,contentFormat:'markdown.v1',contentSha256:sha(content)};
   docs[planning][id+'/content.md']=content;
  }
 }
 const withPlanningSnapshot=createPlanningFolderDownloader({tempParent:root,call:async(tool,args)=>{
  const entries=docs[args.scope],bytes=path=>Buffer.from(typeof entries[path]==='string'?entries[path]:JSON.stringify(entries[path]));
  if(tool==='npc_folders_compare')return {scope:args.scope,groups:[...Map.groupBy(Object.keys(entries),
   path=>path.includes('/')?path.split('/')[0]:'')].map(([folder,paths])=>({folder,
   files:paths.map(path=>({path,size:bytes(path).length,sha256:sha(bytes(path))}))}))};
  const raw=bytes(args.path);
  return {...args,size:raw.length,base64:raw.toString('base64'),nextOffset:null};
 }});
 const opts={mode:'run',dbPath,pds,providerReady:()=>true,withPlanningSnapshot,render:async input=>{
  assert.deepEqual(input.reference,pixelStyle&&input.direction?correctedReference:reference);
  assert.equal(input.imageStyle,pixelStyle?'pixel-sprite.v1':undefined);
  assert.equal(input.posePreset,pixelStyle?'idle':undefined);assert.equal(input.artDirection,'exact ART block');assert.equal(input.poseDirection,'exact IDLE POSE block');
  assert.doesNotMatch(JSON.stringify(input),/never send this/);
  images.push(input.direction??'idle');return png;
 }};
 assert.equal((await imageTick(opts)).state,'idle');
 setCompleted('planning_idle');
 if(pixelStyle)setCompleted('planning_portrait_art');
 allow=true;
 assert.equal((await imageTick(opts)).state,'idle');
 assert.equal((await imageTick({...opts,mode:'execute',operation:'npc.portrait-idle-reference',officialRequestId,
  taskId:row('portrait_idle_reference').task_id})).state,'not_claimable');
 assert.equal(row('portrait_idle_reference').status,'pending');
 setCompleted('portrait_representative');allow=false;
 assert.equal((await imageTick(opts)).code,'IMAGE_WORKER_STAGE_PDS_UNAVAILABLE');
 assert.equal(row('portrait_idle_reference').status,'pending');
 allow=true;
 assert.equal((await imageTick(opts)).state,'completed');
 assert.equal(row('portrait_idle_reference').status,'completed');
 if(pixelStyle){
  assert.equal((await imageTick(opts)).state,'idle');
  assert.equal(row('portrait_pixel_idle_reference').status,'pending');
  assert.equal((await imageTick({...opts,mode:'execute',operation:'npc.portrait-direction',officialRequestId,
   taskId:row('portrait_n').task_id})).state,'not_claimable');
  setCompleted('portrait_pixel_idle_reference');
  wrongReference=true;
  const wrongRef=await imageTick({...opts,mode:'execute',operation:'npc.portrait-direction',officialRequestId,taskId:row('portrait_ne').task_id});
  assert.equal(wrongRef.state,'failed');assert.deepEqual(images,['idle']);
  s.db.prepare("UPDATE workflow_tasks SET updated_at=? WHERE task_key='portrait_ne' AND status='failed'").run(Date.now()-601_000);
  wrongReference=false;
 }
 // Wrong PDS view on a separate exact invocation never reaches native render/storage.
 wrongDirection=true;
 const wrong=await imageTick({...opts,mode:'execute',operation:'npc.portrait-direction',officialRequestId,taskId:row('portrait_n').task_id});
 assert.equal(wrong.state,'failed');
 assert.deepEqual(images,['idle']);
 // Once its cooldown elapses the exact failed direction can be rebound.
 s.db.prepare("UPDATE workflow_tasks SET updated_at=? WHERE task_key='portrait_n' AND status='failed'").run(Date.now()-601_000);
 wrongDirection=false;
 for(const dir of directions){
  assert.equal((await imageTick(opts)).state,'completed');
  assert.equal(row('portrait_'+dir).status,'completed');assert.equal(row('portrait_'+dir).retry_count,dir==='n'||pixelStyle&&dir==='ne'?1:0);
  assert.equal(row('animation_walk_'+dir).status,'pending');
  assert.equal(row('animation_run_'+dir).status,'pending');
 }
 assert.equal((await imageTick(opts)).state,'idle');
 assert.deepEqual(publications,['portrait_idle_reference',...directions.map(d=>'portrait_'+d)]);
 assert.deepEqual(images,['idle',...directions]);
 if(pixelStyle)for(const direction of directions)assert.equal(row('portrait_pixel_idle_'+direction).status,'pending');
 assert.equal(readdirSync(root).some(n=>n.startsWith('workflow-planning-folders-')),false);
});


test('resident stop drains current tick without any next claim or model cancellation',async ()=>{
 const drain=new AbortController(),work=new AbortController();
 let entered,release,claims=0;
 const started=new Promise(resolve=>{entered=resolve;});
 const opts={mode:'run',dbPath:'/unused',signal:work.signal,admissionSignal:drain.signal};
 const ports={check:async()=>({ready:true}),select:()=>({taskId:'task',officialRequestId:'official'}),
  execute:async ({signal})=>{
   claims++;entered();await new Promise(resolve=>{release=resolve;});
   assert.equal(signal.aborted,false);return {state:'completed'};
  }};
 const tick=imageTick(opts,ports);await started;
 drain.abort();release();
 assert.deepEqual(await tick,{state:'completed'});
 assert.deepEqual(await imageTick(opts,ports),{state:'stopping'});
 assert.equal(claims,1);
});

test('resident stop during per-stage preflight refuses to claim',async ()=>{
 const drain=new AbortController();
 const opts={mode:'run',dbPath:'/unused',admissionSignal:drain.signal,pds:{async capability(){drain.abort();return true;}}};
 const ports={check:async()=>({ready:true}),select:()=>({taskId:'task',officialRequestId:'official',operation:'npc.portrait-direction'}),
  execute:()=>assert.fail('must not claim')};
 assert.deepEqual(await imageTick(opts,ports),{state:'stopping'});
});

test('older runnable idle portrait takes precedence over a newer representative',async t=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'image-request-order-'))),dbPath=join(root,'workflow.sqlite');
 const s=new PlannerStore(dbPath);addRetryCount(s.db);chmodSync(dbPath,0o600);
 t.after(()=>{s.close();rmSync(root,{recursive:true,force:true});});
 const payload={schemaVersion:'ngs.npc-one-stop.replace.v1',projectId:'binari',intent:'fixture portrait',
  character:{npcId:'npc.fixture',name:'fixture',slug:'fixture',role:'fixture',actId:'act1',chapterId:'ch1'},sourceRefs:['story.fixture'],includeCombat:false};
 const older='request.official.'+sha('older'),newer='request.official.'+sha('newer');
 for(const [id,at] of [[older,1],[newer,2]]){
  s.stage({officialRequestId:id,projectId:'binari',requestType:'npc.workflow',schemaVersion:payload.schemaVersion,payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:at});
  s.acknowledge(id);
 }
 const set=(id,key)=>s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE official_request_id=? AND task_key=?").run(id,key);
 set(newer,'planning_representative');set(older,'planning_idle');
 const run=()=>imageTick({mode:'run',dbPath,pds:{capability:async()=>true}},{
  check:async()=>({ready:true}),execute:async({officialRequestId,operation})=>[officialRequestId,operation]});
 assert.deepEqual(await run(),[newer,'npc.portrait-representative']);
 set(older,'portrait_representative');
 assert.deepEqual(await run(),[older,'npc.portrait-idle-reference']);
 s.db.prepare("UPDATE workflow_tasks SET status='failed',updated_at=? WHERE official_request_id=? AND task_key='portrait_idle_reference'").run(Date.now(),older);
 assert.deepEqual(await run(),[newer,'npc.portrait-representative']);
});
