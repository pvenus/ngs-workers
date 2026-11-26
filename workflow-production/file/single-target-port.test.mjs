import test from 'node:test';
import assert from 'node:assert/strict';
import {chmodSync,mkdtempSync,realpathSync,rmSync} from 'node:fs';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {PlannerStore} from './vendor/planner-concept-v1/planner.mjs';
import {canonical,sha} from './vendor/planner-concept-v1/envelope.mjs';
import {executeOnce,parseArgs,pollOnce} from './runner.mjs';
import {localPorts,requestLocalJson,storageDeletePath} from './local-ports.mjs';
import {singleTargetDeleteSchema,npcDeleteRoot,conceptDeleteRoot,audioDelete,audioDeleteRoot,audioDeleteSchema,audioDeleteKey} from './file-delete.mjs';

function setup(t,kind){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'single-target-file-'))),dbPath=join(dir,'workflow.sqlite');
 const store=new PlannerStore(dbPath);chmodSync(dbPath,0o600);
 t.after(()=>{store.close();rmSync(dir,{recursive:true,force:true});});
 const projectId='binari',officialRequestId='request.official.'+sha(kind);
 const targetId=kind==='npc'?'npc.ch1_dajeong':'category.add.494e3549a0f262e04c571ad4';
 const payload=kind==='npc'?{schemaVersion:'ngs.npc-one-stop.replace.v1',projectId,intent:'fictional fixture only',
   character:{npcId:targetId,name:'다정',slug:'dajeong',role:'fixture',actId:'act1',chapterId:'ch1'},
   sourceRefs:['story.fixture'],includeCombat:false}:
  {schemaVersion:'ngs.concept-category-workflow.v1',projectId,categoryId:targetId,title:'not sent as target',
   operation:kind,groupId:null,outline:kind==='replace'?'new outline':'',requirements:''};
 const payloadSha256=sha(canonical(payload));
 store.stage({officialRequestId,requestType:kind==='npc'?'npc.workflow':'concept.category.workflow',projectId,
  schemaVersion:payload.schemaVersion,payload,payloadSha256,status:'registered',registeredAt:1});store.acknowledge(officialRequestId);
 const db=store.db,key=kind==='npc'?'delete':'category_delete';
 const task=()=>db.prepare('SELECT * FROM workflow_tasks WHERE official_request_id=? AND task_key=?').get(officialRequestId,key);
 const taskId=task().task_id;
 const target=kind==='npc'?{root:npcDeleteRoot,path:targetId,kind:'directory'}:
  {root:conceptDeleteRoot,path:'sections/'+targetId,kind:'directory'};
 const claim=()=>({officialRequestId,taskId,projectId,payloadSha256,claimStamp:task().updated_at,
  [kind==='npc'?'npcId':'categoryId']:targetId});
 return {dbPath,db,officialRequestId,taskId,task,target,claim};
}
function audioSetup(t){
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'workflow-audio-delete-file-'))),dbPath=join(dir,'workflow.sqlite');
 const store=new PlannerStore(dbPath);chmodSync(dbPath,0o600);
 t.after(()=>{store.close();rmSync(dir,{recursive:true,force:true});});
 const db=store.db,projectId='binari',officialRequestId='request.official.'+sha('audio delete');
 const sourceRequestId='request.official.'+sha('published BGM');
 const target={root:audioDeleteRoot,path:sourceRequestId,kind:'directory'};
 const payload=canonical({schemaVersion:audioDeleteSchema,projectId,officialRequestId:sourceRequestId});
 const payloadSha256=sha(payload),taskId='workflow.task.'+sha(canonical([officialRequestId,audioDeleteKey]));
 const taskInput=canonical({officialRequestId:sourceRequestId,target:{type:'directory',path:audioDeleteRoot+'/'+sourceRequestId}});
 db.prepare("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)")
  .run(officialRequestId,projectId,audioDelete,audioDeleteSchema,payload,payloadSha256);
 db.prepare("INSERT INTO workflow_tasks (task_id,official_request_id,task_key,task_type,worker_type,task_input,updated_at,status) VALUES (?,?,?,?, 'file',?,2,'pending')")
  .run(taskId,officialRequestId,audioDeleteKey,audioDelete,taskInput);
 const task=()=>db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(taskId);
 const claim=()=>({officialRequestId,taskId,projectId,payloadSha256,claimStamp:task().updated_at,sourceRequestId});
 return {dbPath,db,officialRequestId,taskId,task,claim,target,taskInput};
}
async function endpoint(t,{roots,handle}){
 const wire=[];
 const server=createServer(async(req,resp)=>{
  wire.push([req.method,req.url]);
  assert.equal(req.url,storageDeletePath,'never downgrade to a legacy delete route');
  const send=(body,status=200)=>resp.writeHead(status,{'Content-Type':'application/json'}).end(JSON.stringify(body));
  if(req.method==='GET')return send({enabled:true,schemaVersion:singleTargetDeleteSchema,roots});
  let raw='';for await(const chunk of req)raw+=chunk;
  handle(JSON.parse(raw),send);
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 t.after(()=>new Promise(resolve=>server.close(resolve)));
 return {wire,pds:localPorts({singleTarget:true,transport:opts=>{assert.equal(opts.port,8030);return requestLocalJson({...opts,port:server.address().port});}})};
}

for(const kind of ['npc','remove','replace'])
test('ordinary resident uses one exact directory in new route for existing '+kind+' row',async t=>{
 const f=setup(t,kind),other=()=>f.db.prepare('SELECT * FROM workflow_tasks WHERE task_id<>? ORDER BY task_id').all(f.taskId);
 const before=other(),run=f.db.prepare('SELECT * FROM workflow_runs').all();
 const {pds,wire}=await endpoint(t,{roots:[f.target.root],handle(body,send){
  assert.equal(f.task().status,'running');
  assert.deepEqual(body,{claim:f.claim(),target:f.target});
  send({ok:true,operationId:f.taskId,targetAbsent:true,recoveryId:'a'.repeat(64)});
 }});
 assert.equal((await pollOnce({...f,pds})).state,'completed');
 assert.equal(f.task().result_ref,kind==='npc'?f.taskId:null);
 assert.deepEqual(other(),before);assert.deepEqual(f.db.prepare('SELECT * FROM workflow_runs').all(),run);
 assert.deepEqual(wire,[['GET',storageDeletePath],['POST',storageDeletePath]]);
 assert.equal((await pollOnce({...f,pds})).state,'idle');
});

test('root readiness is exact; unsupported root never claims, no legacy fallback',async t=>{
 const f=setup(t,'npc');
 const {pds,wire}=await endpoint(t,{roots:[conceptDeleteRoot],handle(){assert.fail('unexpected write');}});
 assert.equal(await pds.capability(),true); // watch only: at least one root
 assert.equal((await executeOnce({...f,pds})).state,'not_claimable');
 assert.equal((await pollOnce({...f,pds})).state,'unavailable');
 assert.equal(f.task().status,'pending');assert(wire.every(([method])=>method==='GET'));
 assert.deepEqual(parseArgs(['--single-target','--resident','--db',f.dbPath]),{mode:'resident',dbPath:f.dbPath,singleTarget:true});
});
for(const [status,response,state] of [
 [200,{ok:true},'requires_reconciliation'],
 [200,{ok:true,operationId:'wrong target',targetAbsent:true,recoveryId:'a'.repeat(64)},'requires_reconciliation'],
 [200,{ok:true,operationId:'bind',targetAbsent:true,recoveryId:'short'},'requires_reconciliation'],
 [409,{error:'STORAGE_DELETE_OUTCOME_UNCONFIRMED'},'requires_reconciliation'],
 [403,{error:'STORAGE_DELETE_CLAIM_DENIED'},'failed'],
])test('new generic wire preserves definite/uncertain distinction: '+JSON.stringify(response),async t=>{
 const f=setup(t,'remove');
 const {pds,wire}=await endpoint(t,{roots:[conceptDeleteRoot],handle(_body,send){
  send(response.operationId==='bind'?{...response,operationId:f.taskId}:response,status);
 }});
 assert.equal((await pollOnce({...f,pds})).state,state);
 assert.equal(f.task().status,state==='failed'?'failed':'running');
 assert.equal(f.task().result_ref,null);
 assert.equal((await pollOnce({...f,pds})).state,'idle');
 assert.equal(wire.filter(([method])=>method==='POST').length,1);
});

test('ordinary resident sends singular BGM claim plus exact relative directory through generic PDS once',async t=>{
 const f=audioSetup(t);const before=f.db.prepare('SELECT * FROM workflow_runs').all();
 const {pds,wire}=await endpoint(t,{roots:[audioDeleteRoot,conceptDeleteRoot,npcDeleteRoot],handle(body,send){
  assert.equal(f.task().status,'running');
  assert.deepEqual(body,{claim:f.claim(),target:f.target});
  send({ok:true,operationId:f.taskId,targetAbsent:true,recoveryId:'a'.repeat(64)});
 }});
 assert.deepEqual(parseArgs(['--single-target','--execute','--db',f.dbPath,'--official-request-id',f.officialRequestId,
  '--task-id',f.taskId,'--operation',audioDelete]),
  {mode:'execute',singleTarget:true,dbPath:f.dbPath,officialRequestId:f.officialRequestId,taskId:f.taskId,operation:audioDelete});
 assert.deepEqual(await pollOnce({...f,pds}),{state:'completed',taskId:f.taskId,resultRef:null});
 assert.deepEqual(f.db.prepare('SELECT * FROM workflow_runs').all(),before);
 assert.equal(f.task().retry_count,0);
 assert.equal((await pollOnce({...f,pds})).state,'idle');
 assert.deepEqual(wire,[['GET',storageDeletePath],['POST',storageDeletePath]]);
});

test('without advertised audio root or generic contract an audio delete is not claimed or downgraded',async t=>{
 const f=audioSetup(t);const {pds,wire}=await endpoint(t,{roots:[conceptDeleteRoot,npcDeleteRoot],handle(){assert.fail('unexpected POST');}});
 assert.equal((await pollOnce({...f,pds})).state,'unavailable');
 const legacy=localPorts({transport(){assert.fail('unexpected legacy capability');}});
 assert.equal((await pollOnce({...f,pds:legacy})).state,'unavailable');
 assert.equal((await executeOnce({...f,pds,operation:audioDelete})).state,'not_claimable');
 assert.equal(f.task().status,'pending');
 assert(wire.every(([m])=>m==='GET'));
});

test('forged audio target (neighbor, traversal, array, wrong type) is not dispatched',async t=>{
 const f=audioSetup(t);const {pds,wire}=await endpoint(t,{roots:[audioDeleteRoot],handle(){assert.fail('unexpected POST');}});
 const original=JSON.parse(f.taskInput);
 for(const target of [{...original.target,path:audioDeleteRoot+'/../npcs'},{...original.target,path:audioDeleteRoot+'/'+f.officialRequestId},
   [original.target],{...original.target,type:'file'}]){
  f.db.prepare('UPDATE workflow_tasks SET task_input=? WHERE task_id=?').run(canonical({...original,target}),f.taskId);
  assert.deepEqual(await pollOnce({...f,pds}),{state:'blocked',code:'FILE_WORKER_EXACT_CLAIM_REJECTED'});
  assert.equal(f.task().status,'pending');
 }
 assert(wire.every(([m])=>m==='GET'));
});

test('audio ambiguous delivery is never resent; definite failure retains exact retry policy',async t=>{
 const a=audioSetup(t),b=audioSetup(t);let failures=0;
 const ack=taskId=>({ok:true,operationId:taskId,targetAbsent:true,recoveryId:'a'.repeat(64)});
 const {pds:unknown,wire:uncertain}=await endpoint(t,{roots:[audioDeleteRoot],handle(_body,send){
  send({error:'STORAGE_DELETE_OUTCOME_UNCONFIRMED'},409);
 }});
 const {pds}=await endpoint(t,{roots:[audioDeleteRoot],handle(body,send){
  assert.deepEqual(body,{claim:b.claim(),target:b.target});
  if(failures++===0)send({error:'STORAGE_DELETE_CLAIM_DENIED'},403);
  else send(ack(b.taskId));
 }});
 assert.equal((await pollOnce({...a,pds:unknown})).state,'requires_reconciliation');
 assert.equal(a.task().status,'running');
 assert.equal((await pollOnce({...a,pds:unknown})).state,'idle');
 assert.equal(uncertain.filter(([m])=>m==='POST').length,1);
 assert.equal((await pollOnce({...b,pds})).state,'failed');
 assert.equal((await pollOnce({...b,pds})).state,'idle');
 assert.equal(b.task().retry_count,0);
 b.db.prepare('UPDATE workflow_tasks SET updated_at=? WHERE task_id=?').run(Date.now()-600001,b.taskId);
 assert.equal((await pollOnce({...b,pds})).state,'completed');
 assert.equal(b.task().retry_count,1);
});

for(const stale of [false,true])
test('pinned standalone prop deletes one directory; stale conflict is a definite failure: '+stale,async t=>{
 const f=audioSetup(t),root='canonical-files/binari/props',target={root,path:'lantern',kind:'directory'};
 const expected={revision:2,sha256:'d'.repeat(64)},schemaVersion='ngs.file-delete.v1',type='file.delete',key='file_delete';
 const payload=canonical({target,expected,projectId:'binari',schemaVersion}),payloadSha256=sha(payload);
 const taskId='workflow.task.'+sha(canonical([f.officialRequestId,key]));
 f.db.prepare("DELETE FROM workflow_tasks").run();
 f.db.prepare("UPDATE workflow_runs SET request_type=?,schema_version=?,payload=?,payload_sha256=? WHERE official_request_id=?")
  .run(type,schemaVersion,payload,payloadSha256,f.officialRequestId);
 f.db.prepare("INSERT INTO workflow_tasks (task_id,official_request_id,task_key,task_type,worker_type,task_input,updated_at,status) VALUES (?,?,?,?, 'file',?,2,'pending')")
  .run(taskId,f.officialRequestId,key,type,canonical({target,expected}));
 const task=()=>f.db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(taskId);
 const {pds,wire}=await endpoint(t,{roots:[root],handle(body,send){
  assert.deepEqual(body,{target,claim:{officialRequestId:f.officialRequestId,projectId:'binari',
   payloadSha256,claimStamp:task().updated_at,taskId}});
  if(stale)send({error:'STORAGE_DELETE_STALE_TARGET'},409);
  else send({ok:true,operationId:taskId,targetAbsent:true,recoveryId:'a'.repeat(64)});
 }});
 assert.equal((await pollOnce({...f,pds})).state,stale?'failed':'completed');
 assert.equal(task().status,stale?'failed':'completed');
 assert.deepEqual(wire,[['GET',storageDeletePath],['POST',storageDeletePath]]);
});
