import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync,realpathSync,chmodSync} from 'node:fs';
import {createServer} from 'node:http';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {canonical,sha} from './vendor/planner-f24d23a/envelope.mjs';
import {PlannerStore} from './vendor/planner-f24d23a/planner.mjs';
import {localPorts,deletePath,heartbeatPath,requestLocalJson} from './local-ports.mjs';
import {executeOnce,parseArgs,watchOnce,pollOnce} from './runner.mjs';

function setup(t){
 const root=realpathSync(mkdtempSync(join(tmpdir(),'file-worker-runner-'))),dbPath=join(root,'workflow.sqlite');
 const s=new PlannerStore(dbPath);s.db.exec('ALTER TABLE workflow_tasks ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 3)');chmodSync(dbPath,0o600);
 t.after(()=>{s.close();rmSync(root,{recursive:true,force:true});});
 const officialRequestId='request.official.'+sha('fixture');
 const payload={schemaVersion:'ngs.npc-one-stop.replace.v1',projectId:'binari',intent:'fixture delete only',
  character:{npcId:'npc.ch1_dajeong',name:'다정',slug:'dajeong',role:'fixture',actId:'act1',chapterId:'ch1'},
  sourceRefs:['story.fixture'],includeCombat:false};
 s.stage({officialRequestId,projectId:'binari',requestType:'npc.workflow',schemaVersion:payload.schemaVersion,
  payload,payloadSha256:sha(canonical(payload)),status:'registered',registeredAt:1800000000000});
 s.acknowledge(officialRequestId);
 const task=()=>s.db.prepare("SELECT * FROM workflow_tasks WHERE task_key='delete'").get();
 return {dbPath,s,officialRequestId,taskId:task().task_id,task};
}
const capability={enabled:true,schemaVersion:'workflow.pds-delete-receipt.v1'};

test('watch exact GET capability and heartbeat only if enabled, without touching rows',async t=>{
 const f=setup(t),before=f.s.db.prepare('SELECT * FROM workflow_tasks ORDER BY task_id').all(),calls=[];
 const pds=localPorts({transport:async({port,method,path,body})=>{
  calls.push({port,method,path,body});
  if(method==='GET')return capability;
  assert.equal(path,heartbeatPath);assert.deepEqual(body,{serverName:'file-worker'});return {ok:true};
 }});
 assert.deepEqual(await watchOnce({...f,pds}),{ready:true,heartbeat:true});
 assert.deepEqual(calls,[{port:8030,path:deletePath,method:'GET',body:undefined},
  {port:8090,path:heartbeatPath,method:'POST',body:{serverName:'file-worker'}}]);
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
   const {claim}=JSON.parse(body);assert.deepEqual(Object.keys(JSON.parse(body)),['claim']);
   const task=f.task();assert.equal(task.status,'running');assert.deepEqual(claim,{
    officialRequestId:f.officialRequestId,taskId:f.taskId,projectId:'binari',
    npcId:'npc.ch1_dajeong',payloadSha256:f.s.db.prepare('SELECT payload_sha256 AS h FROM workflow_runs').get().h,claimStamp:task.updated_at});
   const {claimStamp,...bound}=claim;
   result={...bound,schemaVersion:capability.schemaVersion,operation:'npc.delete-folder',status:'committed',verified:true,targetAbsent:true,receiptId:f.taskId};
  }
  res.writeHead(200,{'Content-Type':'application/json'}).end(JSON.stringify(result));
 });
 await new Promise(resolve=>srv.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>srv.close(resolve)));
 const pds=localPorts({transport:opts=>{assert.equal(opts.port,8030);return requestLocalJson({...opts,port:srv.address().port});}});
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

test('resident permits only explicit absolute dedicated DB path',()=>{
 assert.deepEqual(parseArgs(['--resident','--db','/private/workflow.sqlite']),{mode:'resident',dbPath:'/private/workflow.sqlite'});
 for(const args of [['--resident'],['--resident','--db','relative'],['--resident','--db','/tmp/db','--worker','image']])
  assert.throws(()=>parseArgs(args),/EXPLICIT_MODE_REQUIRED/);
});

test('resident shared lookup runs the exact first delete; never reruns running or completed',async t=>{
 const f=setup(t),calls=[];
 const pds=localPorts({transport:async ({method,path,body})=>{
  calls.push([method,path]);
  if(method==='GET')return capability;
  const {claimStamp,...bound}=body.claim;
  assert.equal(f.task().status,'running');
  assert.equal(claimStamp,f.task().updated_at);
  assert.equal(bound.taskId,f.taskId);
  return {...bound,schemaVersion:capability.schemaVersion,operation:'npc.delete-folder',status:'committed',verified:true,targetAbsent:true,receiptId:f.taskId};
 }});
 assert.equal((await pollOnce({...f,pds})).state,'completed');
 assert.deepEqual(await pollOnce({...f,pds}),{state:'idle'});
 assert.deepEqual(calls,[['GET',deletePath],['POST',deletePath]]);
 assert.equal(f.task().result_ref,f.taskId);
});

test('resident never selects wrong dependency, worker or already-running task; cancellation avoids claim',async t=>{
 const f=setup(t);
 const change=(key,value)=>f.s.db.prepare('UPDATE workflow_tasks SET '+key+'=? WHERE task_id=?').run(value,f.taskId);
 const pds={capability:async()=>{throw Error('unneeded capability call');}};
 const other=f.s.db.prepare("SELECT task_id FROM workflow_tasks WHERE task_key<>'delete' LIMIT 1").get().task_id;
 change('depends_on_task_id',other);
 assert.deepEqual(await pollOnce({...f,pds}),{state:'idle'});
 change('depends_on_task_id',null);change('worker_type','image');
 assert.deepEqual(await pollOnce({...f,pds}),{state:'idle'});
 change('worker_type','file');change('status','running');
 assert.deepEqual(await pollOnce({...f,pds}),{state:'idle'});
 change('status','pending');const stop=new AbortController();
 const ending={capability:async()=>{stop.abort();return true;}};
 assert.deepEqual(await pollOnce({...f,pds:ending,signal:stop.signal}),{state:'stopping'});
 assert.equal(f.task().status,'pending');
});

test('resident PDS uncertainty leaves running row for reconciliation, never resends',async t=>{
 const f=setup(t);let attempts=0;
 const pds={capability:async()=>true,deleteTargets:async()=>{attempts++;throw Error('fixture response lost');}};
 assert.equal((await pollOnce({...f,pds})).state,'requires_reconciliation');
 assert.equal(f.task().status,'running');
 assert.equal((await pollOnce({...f,pds})).state,'idle');
 assert.equal(attempts,1);
});

for(const [status,error,expected] of [
 [403,'WORKFLOW_DELETE_CLAIM_DENIED','failed'],
 [403,'unrecognized-policy-proxy','requires_reconciliation'],
 [409,'WORKFLOW_DELETE_NOT_CONFIRMED','requires_reconciliation'],
 [503,'WORKFLOW_DELETE_OUTCOME_UNCONFIRMED','requires_reconciliation'],
])test('resident delete status '+status+' '+error+' -> '+expected,async t=>{
 const f=setup(t);
 const srv=createServer(async(req,res)=>{
  for await(const _ of req){}
  res.writeHead(req.method==='GET'?200:status,{'Content-Type':'application/json'}).end(JSON.stringify(req.method==='GET'?capability:{error}));
 });
 await new Promise(resolve=>srv.listen(0,'127.0.0.1',resolve));t.after(()=>new Promise(resolve=>srv.close(resolve)));
 const pds=localPorts({transport:o=>requestLocalJson({...o,port:srv.address().port})});
 assert.equal((await pollOnce({...f,pds})).state,expected);
 assert.equal(f.task().status,expected==='failed'?'failed':'running');
 assert.equal(f.task().result_ref,null);
 assert.equal((await pollOnce({...f,pds})).state,'idle');
});

test('closed delete retries only after ten minutes and increments only exact retry claim',async t=>{
 const f=setup(t);let writes=0;
 const age=(ms)=>f.s.db.prepare("UPDATE workflow_tasks SET updated_at=? WHERE task_id=?").run(Date.now()-ms,f.taskId);
 const pds={capability:async()=>true,deleteTargets:async(command,binding)=>{
  writes++;assert.equal(f.task().retry_count,1);assert.equal(binding.claimStamp,f.task().updated_at);
  const {officialRequestId,taskId,projectId,payloadSha256}=binding;
  const npcId=command.targets[0].id,operation='npc.delete-folder';
  return {officialRequestId,taskId,projectId,npcId,payloadSha256,operation,schemaVersion:capability.schemaVersion,
   status:'committed',verified:true,targetAbsent:true,receiptId:taskId};
 }};
 f.s.db.prepare("UPDATE workflow_tasks SET status='failed' WHERE task_id=?").run(f.taskId);
 age(599000);
 assert.equal((await pollOnce({...f,pds})).state,'idle');
 assert.equal((await executeOnce({...f,pds})).state,'not_claimable');
 assert.equal(f.task().retry_count,0);
 age(601000);
 assert.equal((await pollOnce({...f,pds})).state,'completed');
 assert.equal(f.task().retry_count,1);
 age(601000);
 assert.equal((await pollOnce({...f,pds})).state,'idle');
 assert.equal(writes,1);
 f.s.db.prepare("UPDATE workflow_tasks SET status='failed',retry_count=3,result_ref=NULL WHERE task_id=?").run(f.taskId);
 age(601000);
 assert.equal((await pollOnce({...f,pds})).state,'idle');
 assert.equal((await executeOnce({...f,pds})).state,'not_claimable');
 assert.equal(f.task().retry_count,3);
 assert.equal(writes,1);
});
