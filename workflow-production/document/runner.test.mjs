import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,realpathSync,chmodSync,readdirSync} from 'node:fs';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {canonical,sha} from './vendor/planner-89c7568/envelope.mjs';
import {PlannerStore} from './vendor/planner-89c7568/planner.mjs';
import {localPorts,presetPath,representativePath,heartbeatPath,requestLocalJson} from './local-ports.mjs';
import {executeOnce,parseArgs,watchOnce} from './runner.mjs';
import {createPlanningFolderDownloader} from './planning-folders.mjs';

function setup(t){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'document-worker-runner-'))),dbPath=join(root,'workflow.sqlite');
 const s=new PlannerStore(dbPath);chmodSync(dbPath,0o600);
 s.db.exec('ALTER TABLE workflow_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 3)');
 t.after(()=>{s.close();rmSync(root,{recursive:true,force:true});});
 const officialRequestId='request.official.'+sha('fixture');
 const payload={schemaVersion:'ngs.npc-one-stop.replace.v1',projectId:'binari',intent:'fixture delete only',
  character:{npcId:'npc.ch1_dajeong',name:'다정',slug:'dajeong',role:'fixture',actId:'act1',chapterId:'ch1'},
  sourceRefs:['story.fixture'],includeCombat:false};
 s.stage({officialRequestId,projectId:'binari',requestType:'npc.workflow',schemaVersion:payload.schemaVersion,
  payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1800000000000});
 s.acknowledge(officialRequestId);
 const task=()=>s.db.prepare("SELECT * FROM workflow_tasks WHERE task_key='preset'").get();
 const completedDelete=()=>s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key='delete'").run();
 return {dbPath,s,officialRequestId,taskId:task().task_id,task,completedDelete};
}
const capability={enabled:true,schemaVersion:'workflow.preset-publication-receipt.v1'};

test('watch exact GET capability and heartbeat only if enabled, without touching rows',async t=>{
 const f=setup(t),before=f.s.db.prepare('SELECT * FROM workflow_tasks ORDER BY task_id').all(),calls=[];
 const pds=localPorts({transport:async({port,method,path,body})=>{
  calls.push({port,method,path,body});
  if(method==='GET')return capability;
  assert.equal(path,heartbeatPath);assert.deepEqual(body,{serverName:'document-worker'});return {ok:true};
 }});
 assert.deepEqual(await watchOnce({...f,pds}),{ready:true,heartbeat:true});
 assert.deepEqual(calls,[{port:8030,path:presetPath,method:'GET',body:undefined},
  {port:8090,path:heartbeatPath,method:'POST',body:{serverName:'document-worker'}}]);
 assert.deepEqual(f.s.db.prepare('SELECT * FROM workflow_tasks ORDER BY task_id').all(),before);
});

test('capability disabled: watcher emits no heartbeat, one-shot never claims',async t=>{
 const f=setup(t),calls=[];
 const pds=localPorts({transport:async r=>{calls.push(r.method);return {enabled:false,schemaVersion:capability.schemaVersion};}});
 assert.equal((await watchOnce({...f,pds})).ready,false);
 assert.equal((await executeOnce({...f,pds})).state,'not_claimable');
 assert.equal(f.task().status,'pending');assert.deepEqual(calls,['GET','GET']);
});

test('one explicit command through actual loopback HTTP sends only exact current claim and stamp',async t=>{
 const f=setup(t),calls=[];
 const srv=createServer(async(req,res)=>{
  const path=req.url;calls.push([req.method,path]);let result=capability;
  if(req.method==='POST'){
   let body='';for await(const part of req)body+=part;
   const {claim,artifact,predecessorReceiptRef}=JSON.parse(body);
   assert.deepEqual(Object.keys(JSON.parse(body)).sort(),['artifact','claim','predecessorReceiptRef']);
   assert.equal(predecessorReceiptRef,null);assert.equal(artifact.character.npcId,'npc.ch1_dajeong');
   const task=f.task();assert.equal(task.status,'running');assert.deepEqual(claim,{
    officialRequestId:f.officialRequestId,taskId:f.taskId,projectId:'binari',
    npcId:'npc.ch1_dajeong',payloadSha256:f.s.db.prepare('SELECT payload_sha256 AS h FROM workflow_runs').get().h,claimStamp:task.updated_at});
   const {claimStamp,...bound}=claim;
   result={...bound,schemaVersion:capability.schemaVersion,operation:'npc.preset',artifactSha256:sha(canonical(artifact)),status:'committed',verified:true,receiptId:f.taskId};
  }
  res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(result));
 });
 await new Promise(resolve=>srv.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>srv.close(resolve)));
 const pds=localPorts({transport:opts=>{assert.equal(opts.port,8030);return requestLocalJson({...opts,port:srv.address().port});}});
 assert.equal((await executeOnce({...f,pds})).state,'not_claimable');
 assert.equal(calls.filter(([method])=>method==='POST').length,0);
 f.completedDelete();
 assert.equal((await executeOnce({...f,officialRequestId:'request.official.'+sha('wrong'),pds})).state,'not_claimable');
 assert.equal((await executeOnce({...f,pds})).state,'completed');
 assert.equal((await executeOnce({...f,pds})).state,'not_claimable');
 assert.equal(calls.filter(([method])=>method==='POST').length,1);assert.equal(f.task().result_ref,f.taskId);
});

test('CLI has no implicit execute, override URL, scope glob, or missing identity',()=>{
 const p='/private/planner/workflow.sqlite',o='request.official.'+sha('fixture'),id='workflow.task.'+sha('fixture');
 assert.deepEqual(parseArgs(['--watch','--db',p]),{mode:'watch',dbPath:p});
 assert.deepEqual(parseArgs(['--execute','--db',p,'--official-request-id',o,'--task-id',id]),
  {mode:'execute',dbPath:p,officialRequestId:o,taskId:id});
 for(const args of [[],['--db',p],['--execute','--db',p,'--task-id',id],['--watch','--db',p,'--port','9999'],
  ['--execute','--db',p,'--official-request-id','*','--task-id',id]])assert.throws(()=>parseArgs(args),/EXPLICIT_MODE_REQUIRED/);
});

for(const mode of ['write ACK','negative ACK','legacy receipt','render failed','claim changed','completion CAS changed'])test('representative exact HTTP and row updates: '+mode,async t=>{
 const accepted=mode==='write ACK';
 const ackCommitted=accepted||mode==='completion CAS changed';
 const beforePublicationFailure=['render failed','claim changed'].includes(mode);
 const f=setup(t),op='npc.plan-representative';
 const picked=()=>f.s.db.prepare("SELECT * FROM workflow_tasks WHERE task_key='planning_representative'").get();
 const taskId=picked().task_id;
 const held={...f,taskId,operation:op};
 const basis={sourceVersion:'1'.repeat(64),revision:7,generationId:'2'.repeat(48),categoryId:'category.preset.'+'3'.repeat(32)};
 const run=f.s.db.prepare('SELECT * FROM workflow_runs').get(),character=JSON.parse(run.payload).character;
 const schema='workflow.representative-plan-write.v1',calls=[];
 const presetMetadata=canonical({creationBasis:{},planningPreset:{},category:{id:basis.categoryId},chapters:[],status:'fresh'});
 const srv=createServer(async(req,res)=>{
  calls.push([req.method,req.url]);
  let result;
  if(req.method==='GET')result={enabled:true,schemaVersion:schema};
  else{
   let data='';for await(const part of req)data+=part;
   const packet=JSON.parse(data),{claim}=packet;
   assert.deepEqual(claim,{officialRequestId:f.officialRequestId,taskId,projectId:'binari',
      npcId:'npc.ch1_dajeong',payloadSha256:run.payload_sha256,claimStamp:picked().updated_at});
   assert.equal(picked().status,'running');
   if(req.url===representativePath+'/read'){
    assert.deepEqual(Object.keys(packet),['claim','inputMode']);assert.equal(packet.inputMode,'folder-snapshot');result={basis,character,presetContext:presetMetadata};
   }else{
    assert.deepEqual(Object.keys(packet),['claim','basis','artifact']);
    assert.deepEqual(packet.basis,basis);
    const artifact={schemaVersion:'workflow.npc-representative-plan-artifact.v1',taskId,projectId:'binari',
      npcId:character.npcId,categoryId:basis.categoryId,content:'Fixture representative plan'};
    assert.deepEqual(packet.artifact,artifact);
    const {claimStamp,...bound}=claim;
    if(mode==='completion CAS changed')
     f.s.db.prepare('UPDATE workflow_tasks SET updated_at=updated_at+1 WHERE task_id=?').run(taskId);
    result=mode==='legacy receipt'?{...bound,schemaVersion:'workflow.representative-plan-publication-receipt.v1',
     operation:op,status:'committed',verified:true,artifactSha256:sha(canonical(artifact)),receiptId:taskId}:{ok:ackCommitted};
   }
  }
  res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(result));
 });
 await new Promise(resolve=>srv.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>srv.close(resolve)));
 const pds=localPorts({transport:opts=>{assert.equal(opts.port,8030);return requestLocalJson({...opts,port:srv.address().port});}});
 const contentByScope={
  'binari/concept': ['c.json','{"idea":"complete content"}'],
  'binari/npcs/npc.ch1_dajeong/planning': ['p.txt','fresh exact folder planning file'],
 };
 const mcpCalls=[];
 const withPlanningSnapshot=createPlanningFolderDownloader({tempParent:realpathSync(new URL('.', 'file://'+f.dbPath)),call:async (tool,args)=>{
   mcpCalls.push({tool,args});
   const [path,text]=contentByScope[args.scope],bytes=Buffer.from(text);
   if(tool==='npc_folders_compare')return {scope:args.scope,groups:[{folder:'',files:[{path,sha256:sha(text),size:bytes.length}]}]};
   assert.equal(tool,'npc_folder_file_chunk');assert.equal(args.path,path);assert.equal(args.sha256,sha(text));assert.equal(args.offset,0);
   return {...args,size:bytes.length,base64:bytes.toString('base64'),nextOffset:null};
 }});
 const render=async input=>{
  calls.push(['render']);
  assert.deepEqual(input.character,character);assert.equal(input.intent,JSON.parse(run.payload).intent);
  assert.deepEqual(input.sourceRefs,JSON.parse(run.payload).sourceRefs);
  assert(!('conceptContext' in input)&&!('presetContext' in input));assert.equal(input.presetMetadata,presetMetadata);
  assert.deepEqual(input.sources.files.map(f=>f.text),['{"idea":"complete content"}','fresh exact folder planning file']);
  assert(input.sources.files.every(f=>!('file' in f)));
  if(mode==='claim changed'){
   f.s.db.prepare('UPDATE workflow_tasks SET updated_at=updated_at+1 WHERE task_id=?').run(taskId);
   throw Error('fixture render after claim changed');
  }
  if(mode==='render failed')throw Error('fixture definitive render error');
  return 'Fixture representative plan';
 };
 assert.equal((await executeOnce({...held,pds,render,withPlanningSnapshot})).state,'not_claimable');
 assert.deepEqual(calls,[['GET',representativePath+'?inputMode=folder-snapshot']]);
 f.s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key='preset'").run();
 const others=f.s.db.prepare('SELECT * FROM workflow_tasks WHERE task_id<>? ORDER BY task_id');
 const before=others.all(taskId);
 const result=await executeOnce({...held,pds,render,withPlanningSnapshot});
 assert.equal(result.state,accepted?'completed':mode==='render failed'?'failed':'requires_reconciliation');
 if(mode==='render failed'){assert.equal(result.failureStage,'render');assert.equal(result.reasonCode,'NPC_PLANNING_STAGE_FAILED');
  assert.doesNotMatch(JSON.stringify(result),/fixture definitive render error/);}
 assert.deepEqual(calls.slice(1),[['GET',representativePath+'?inputMode=folder-snapshot'],['POST',representativePath+'/read'],['render'],...(beforePublicationFailure?[]:[['POST',representativePath]])]);
 assert.equal(readdirSync(realpathSync(new URL('.', 'file://'+f.dbPath))).some(n=>n.startsWith('workflow-planning-folders-')),false);
 assert.deepEqual(mcpCalls.filter(x=>x.tool==='npc_folders_compare').map(x=>x.args.scope),
   ['binari/concept','binari/npcs/npc.ch1_dajeong/planning','binari/concept','binari/npcs/npc.ch1_dajeong/planning']);
 assert.equal(picked().result_ref,null);
 assert.equal(picked().status,accepted?'completed':mode==='render failed'?'failed':'running');assert.deepEqual(others.all(taskId),before);
 assert.deepEqual(f.s.db.prepare('SELECT * FROM workflow_runs').get(),run);
 assert.equal((await executeOnce({...held,pds,render,withPlanningSnapshot})).state,'not_claimable');
});

test('representative refuses wrong PDS character and never invokes renderer or publication',async t=>{
 const f=setup(t);
 f.s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key='preset'").run();
 const picked=()=>f.s.db.prepare("SELECT * FROM workflow_tasks WHERE task_key='planning_representative'").get();
 const pds={capability:async()=>true,readRepresentative:async()=>({basis:{},character:{npcId:'npc.other'}}),
  publishRepresentative:async()=>assert.fail('no publication')};
 const out=await executeOnce({...f,taskId:picked().task_id,pds,operation:'npc.plan-representative',render:()=>assert.fail('no renderer'),withPlanningSnapshot:()=>assert.fail('no snapshot')});
 assert.equal(out.state,'failed');assert.equal(out.code,'WORKFLOW_DOCUMENT_FAILED_BEFORE_PUBLICATION');
 assert.equal(out.failureStage,'pds-contract');assert.equal(out.reasonCode,'WORKFLOW_PLAN_CHARACTER_CHANGED');
 assert.equal(picked().status,'failed');
 assert.equal(picked().result_ref,null);
});


test('audit directory is explicit for exact representative execute only',()=>{
 const base=['--execute','--db','/private/db','--official-request-id','request.official.'+sha('fixture'),
  '--task-id','workflow.task.'+sha('fixture'),'--operation','npc.plan-representative'];
 assert.equal(parseArgs([...base,'--audit-dir','/private/trace']).auditDirectory,'/private/trace');
 assert.throws(()=>parseArgs(['--watch','--db','/private/db','--operation','npc.plan-representative','--audit-dir','/private/trace']));
 assert.throws(()=>parseArgs([...base.slice(0,-2),'--audit-dir','/private/trace']));
 assert.throws(()=>parseArgs([...base,'--retry']));
});


test('representative declines legacy readiness before claiming or publishing',async t=>{
 const f=setup(t);
 f.s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key='preset'").run();
 const get=()=>f.s.db.prepare("SELECT * FROM workflow_tasks WHERE task_key='planning_representative'").get();
 const before=get();
 const pds=localPorts({transport:async opts=>{
  assert.equal(opts.method,'GET');assert.equal(opts.path,representativePath+'?inputMode=folder-snapshot');
  return {enabled:true,schemaVersion:'workflow.representative-plan-publication-receipt.v1'};
 }});
 assert.equal((await executeOnce({...f,taskId:before.task_id,pds,operation:'npc.plan-representative'})).state,'not_claimable');
 assert.deepEqual(get(),before);
});

test('resident idle automatically claims only after representative plan and publishes exact idle artifact',async t=>{
 const f=setup(t), character=JSON.parse(f.s.db.prepare('SELECT payload FROM workflow_runs').get().payload).character;
 const find=key=>f.s.db.prepare('SELECT * FROM workflow_tasks WHERE task_key=?').get(key);
 const {documentTick}=await import('./runner.mjs'),{idlePath}=await import('./local-ports.mjs');
 const basis={categoryId:'category.preset.'+'c'.repeat(32)};
 const representativeContent='다정의 대표 포즈와 의상 원문';
 const presetContext=canonical({creationBasis:'approved',planningPreset:'fixture',chapters:[],status:'active',
  category:{id:basis.categoryId,content:'',planningRole:'portrait.idle'}});
 const files={'binari/concept': ['c.json','{"concept":"source"}'],
  'binari/npcs/npc.ch1_dajeong/planning': ['category.preset.'+'d'.repeat(32)+'.json',
   canonical({id:'category.preset.'+'d'.repeat(32),content:representativeContent,planningRole:'portrait.representative'})]};
 const withPlanningSnapshot=createPlanningFolderDownloader({call:async (tool,args)=>{
   const [path,content]=files[args.scope],bytes=Buffer.from(content);
   if(tool==='npc_folders_compare')return {scope:args.scope,groups:[{folder:'',files:[{path,sha256:sha(content),size:bytes.length}]}]};
   return {...args,size:bytes.length,base64:bytes.toString('base64'),nextOffset:null};
 }});
 const calls=[];
 const pds=localPorts({transport:async({method,path,body})=>{
  calls.push(path);
  if(method==='GET')return {enabled:true,schemaVersion:'workflow.idle-plan-write.v1'};
  const idle=find('planning_idle');
  assert.equal(idle.status,'running');
  assert.equal(body.claim.taskId,idle.task_id);
  assert.equal(body.claim.claimStamp,idle.updated_at);
  if(path.endsWith('/read'))return {basis,character,presetContext,representativeContent};
  assert.deepEqual(body.basis,basis);
  assert.deepEqual(body.artifact,{schemaVersion:'workflow.npc-idle-plan-artifact.v1',taskId:idle.task_id,
   projectId:'binari',npcId:'npc.ch1_dajeong',categoryId:basis.categoryId,content:'아이들 포즈'});
  return {ok:true};
 }});
 const opts={dbPath:f.dbPath,pds,operation:'npc.plan-idle',withPlanningSnapshot,rendererFor:operation=>async input=>{
  assert.equal(operation,'npc.plan-idle');
  assert.equal(input.representativeContent,representativeContent);
  assert.deepEqual(input.sources.files.map(f=>f.scope),Object.keys(files));
  return '아이들 포즈';
 }};
 assert.deepEqual(parseArgs(['--run','--db',f.dbPath]),{mode:'run',dbPath:f.dbPath});
 assert.deepEqual(await documentTick(opts),{state:'idle'});assert.deepEqual(calls,[]);
 f.s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key='planning_representative'").run();
 const kept=find('portrait_representative');
 assert.equal((await documentTick(opts)).state,'completed');
 assert.equal(find('planning_idle').status,'completed');assert.equal(find('planning_idle').result_ref,null);
 assert.deepEqual(find('portrait_representative'),kept);
 assert.deepEqual(calls,[idlePath+'?inputMode=folder-snapshot',idlePath+'/read',idlePath]);
 assert.deepEqual(await documentTick(opts),{state:'idle'});
});


test('document polling retries only its exact aged failed task with completed predecessor',async t=>{
 const {documentTick}=await import('./runner.mjs');
 const f=setup(t),task=f.s.db.prepare("SELECT * FROM workflow_tasks WHERE task_key='preset'").get();
 const get=()=>f.s.db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(task.task_id);
 const set=(status,time)=>f.s.db.prepare('UPDATE workflow_tasks SET status=?,updated_at=? WHERE task_id=?').run(status,time,task.task_id);
 const others=()=>f.s.db.prepare('SELECT * FROM workflow_tasks WHERE task_id<>? ORDER BY task_id').all(task.task_id);
 const request=()=>f.s.db.prepare('SELECT * FROM workflow_runs').get();
 const opts={dbPath:f.dbPath,operation:'npc.preset',pds:{
  capability:async()=>true,
  publishExactPreset(c,stamp){
   calls++;
   assert.equal(get().status,'running');assert.equal(get().updated_at,stamp);
   assert.equal(get().retry_count,1);
   return {schemaVersion:'workflow.preset-publication-receipt.v1',officialRequestId:c.officialRequestId,taskId:c.taskId,
    projectId:c.projectId,npcId:c.npcId,payloadSha256:c.payloadSha256,operation:c.operation,artifactSha256:c.artifactSha256,
    status:'committed',verified:true,receiptId:'retry.exact'};
  },
 }};
 let calls=0,now=Date.now();
 set('failed',now-601000);
 const waiting=get();
 assert.deepEqual(await documentTick(opts),{state:'idle'});assert.deepEqual(get(),waiting);
 f.s.db.prepare("UPDATE workflow_tasks SET status='completed' WHERE task_key='delete'").run();
 const siblings=others(),run=request();
 set('failed',now-590000);const recent=get();
 assert.deepEqual(await documentTick(opts),{state:'idle'});assert.deepEqual(get(),recent);
 assert.equal((await executeOnce({...f,taskId:task.task_id,pds:opts.pds})).state,'not_claimable');
 assert.deepEqual(get(),recent);
 set('running',now-601000);const uncertain=get();
 assert.deepEqual(await documentTick(opts),{state:'idle'});assert.deepEqual(get(),uncertain);
 set('failed',now-601000);
 f.s.db.prepare('UPDATE workflow_tasks SET retry_count=3 WHERE task_id=?').run(task.task_id);
 const exhausted=get();
 assert.deepEqual(await documentTick(opts),{state:'idle'});
 assert.equal((await executeOnce({...f,taskId:task.task_id,pds:opts.pds})).state,'not_claimable');
 assert.deepEqual(get(),exhausted);assert.equal(calls,0);
 f.s.db.prepare('UPDATE workflow_tasks SET retry_count=0 WHERE task_id=?').run(task.task_id);
 assert.equal((await documentTick(opts)).state,'completed');
 assert.equal(get().retry_count,1);assert.equal(get().result_ref,'retry.exact');assert.equal(calls,1);
 assert.deepEqual(others(),siblings);assert.deepEqual(request(),run);
 assert.deepEqual(await documentTick(opts),{state:'idle'});assert.equal(calls,1);
});
