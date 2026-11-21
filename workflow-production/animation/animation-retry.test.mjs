import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFile} from 'node:fs/promises';
import {chmodSync,mkdtempSync,realpathSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {choose,claim,markFailed} from './animation-cli.mjs';
const wallTime=Date.now();
const now=1770000000000, cutoff=10*60*1000;
const task={taskId:'workflow.task.'+'a'.repeat(64),officialRequestId:'request.official.fixture',key:'animation_walk_s',parent:'portrait_s',motion:'walk',direction:'s'};
const type='npc.animation-native-png';
test.beforeEach(t=>t.mock.method(Date,'now',()=>now));
function fixture(t,extra={}){
 const db=new DatabaseSync(extra.path??':memory:');t.after(()=>db.close());
 if(extra.path)chmodSync(extra.path,0o600);
 db.exec(`CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,project_id TEXT,payload_sha256 TEXT,payload TEXT,state TEXT,registered_at INTEGER);
  CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_key TEXT,task_type TEXT,worker_type TEXT,depends_on_task_id TEXT,
    task_input TEXT,status TEXT,result_ref TEXT,updated_at INTEGER,retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count BETWEEN 0 AND 3));
  INSERT INTO workflow_tasks VALUES ('parent','request.official.fixture','portrait_s','npc.portrait','image',NULL,'{}','completed','published',1,0)`);
 db.prepare('INSERT INTO workflow_runs VALUES (?,?,?,?,?,?)').run(task.officialRequestId,'fixture','hash','{"character":{"npcId":"npc.fixture"}}',extra.runState??'planned',1);
 db.prepare('INSERT INTO workflow_tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)').run(task.taskId,task.officialRequestId,task.key,type,extra.workerType??'animation',
 'parent','{"npcId":"npc.fixture","motion":"walk","direction":"s","output":"native-png"}',
 extra.status??'failed',extra.resultRef??null,extra.updatedAt??now-cutoff,extra.retryCount??0);
 // Keep SQLite's own retry predicate but freeze the SQL clock for exact +/-1ms tests.
 db.function('unixepoch',{varargs:true},()=>now/1000);

 const read=()=>db.prepare('SELECT status,updated_at,retry_count,result_ref FROM workflow_tasks WHERE task_id=?').get(task.taskId);
 const candidate=()=>({...task});
 return {db,read,candidate};
}
test('pending claim never counts as retry',t=>{
 const {db,read,candidate}=fixture(t,{status:'pending',updatedAt:now,retryCount:3});
 const old=candidate();const accepted=claim(db,old);assert(accepted);assert.equal(read().retry_count,3);
 assert.equal(claim(db,old),null);assert.equal(read().status,'running');
});
test('failed boundary -1 ms blocked, exact threshold allowed once',t=>{
 const {db,read,candidate}=fixture(t,{updatedAt:now-cutoff+1});
 assert.equal(claim(db,candidate()),null);assert.equal(read().retry_count,0);
 db.prepare("UPDATE workflow_tasks SET updated_at=? WHERE task_id=?").run(now-cutoff,task.taskId);
 const old=candidate();const claimed=claim(db,old);assert(claimed);assert.equal(read().retry_count,1);
 assert.equal(claim(db,old),null);assert.equal(read().retry_count,1);
});
test('failed older than cutoff increments existing retry count once',t=>{
 const {db,read,candidate}=fixture(t,{updatedAt:now-cutoff-1,retryCount:2});const old=candidate();
 const claimed=claim(db,old);assert(claimed);assert.equal(read().retry_count,3);
 assert.equal(markFailed(db,{...claimed.dto,claimStamp:claimed.dto.claimStamp-1}),false);
 assert.equal(markFailed(db,claimed.dto),true);
 assert.equal(claim(db,candidate()),null);assert.equal(read().retry_count,3);
 db.prepare('UPDATE workflow_tasks SET updated_at=? WHERE task_id=?').run(now-cutoff-1,task.taskId);
 assert.equal(claim(db,candidate()),null);assert.equal(read().retry_count,3);
});
test('invalid claim scopes and published/running/completed rows never retry',t=>{
 for(const extra of [{workerType:'image'},{runState:'failed'},{resultRef:'published'},{status:'running'},{status:'completed'},{retryCount:3}]){
  const {db,read,candidate}=fixture(t,extra);assert.equal(claim(db,candidate()),null);assert.equal(read().retry_count,extra.retryCount??0);
 }
});
test('stale candidate and dependency change cannot increment',t=>{
 const {db,read,candidate}=fixture(t,{updatedAt:now-cutoff-9});const old=candidate();
 db.prepare("UPDATE workflow_tasks SET updated_at=? WHERE task_id=?").run(now-cutoff+1,task.taskId);
 assert.equal(claim(db,old),null);
 db.exec("UPDATE workflow_tasks SET status='failed' WHERE task_id='parent'");
 assert.equal(claim(db,candidate()),null);assert.equal(read().retry_count,0);
});

test('CAS rejects exact stamp/status changes between SELECT and UPDATE even while eligible',t=>{
 for(const change of ["updated_at=updated_at+1","status='pending'"]){
  const {db,read,candidate}=fixture(t,{updatedAt:now-cutoff-9});
  const wrapped={exec:sql=>db.exec(sql),prepare(sql){
   const st=db.prepare(sql);
   if(!sql.startsWith('UPDATE workflow_tasks AS t'))return st;
   return {run(...args){
    db.prepare('UPDATE workflow_tasks SET '+change+' WHERE task_id=?').run(task.taskId);
    return st.run(...args);
   }};
  }};
  assert.equal(claim(wrapped,candidate()),null);assert.equal(read().retry_count,0);
 }
});
test('native probe still precedes any claim and is neither a retry nor a claim',async()=>{
 const source=await readFile(new URL('./animation-cli.mjs',import.meta.url),'utf8');
 assert.match(source,/if\(warm\.cold\(\)\)\{\s*const preflight=await runNative\('--probe',signal\);insist\(preflight.ready===true,'ANIMATION_MODEL_UNAVAILABLE'\);\s*\}\s*signal.throwIfAborted\(\);\s*await execute\(db,t,signal,warm\);/);
});

test('read-only poll neither claims nor increments and respects persisted cooldown/cap',t=>{
 const dir=realpathSync(mkdtempSync(join(tmpdir(),'animation-retry-fixture-'))),file=join(dir,'workflow.sqlite');
 try {
  const {db,read}=fixture(t,{path:file,updatedAt:wallTime-cutoff-10000});
  assert.equal(choose(file)?.taskId,task.taskId);assert.equal(read().retry_count,0);assert.equal(read().status,'failed');
  db.prepare('UPDATE workflow_tasks SET updated_at=? WHERE task_id=?').run(wallTime,task.taskId);
  assert.equal(choose(file),undefined);
  db.prepare('UPDATE workflow_tasks SET updated_at=?,retry_count=3 WHERE task_id=?').run(wallTime-cutoff-10000,task.taskId);
  assert.equal(choose(file),undefined);assert.equal(read().retry_count,3);
 }finally{rmSync(dir,{recursive:true,force:true});}
});
