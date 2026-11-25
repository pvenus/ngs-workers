import assert from 'node:assert/strict';
import test from 'node:test';
import {DatabaseSync} from 'node:sqlite';
import {detectLostPortraitReads,finishPortraitRead} from './portrait-read-owner.mjs';

const kind='npc.portrait-representative';
function fixture(t,column=true){
 const db=new DatabaseSync(':memory:');t.after(()=>db.close());
 db.exec(`CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_type TEXT,worker_type TEXT,
 status TEXT,updated_at INTEGER,result_ref TEXT,retry_count INTEGER${column?',portrait_read_owner_pid INTEGER':''});
 CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,state TEXT)`);
 const add=(i,{type=kind,worker='image',status='running',pid=6789,result=null,state='planned'}={})=>{
  const taskId='workflow.task.'+i.repeat(64),officialRequestId='request.official.'+i.repeat(64);
  db.prepare('INSERT INTO workflow_runs VALUES(?,?)').run(officialRequestId,state);
  db.prepare(`INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,?,1${column?',?':''})`)
   .run(taskId,officialRequestId,type,worker,status,123,result,...(column?[pid]:[]));
  return {taskId,officialRequestId,claimStamp:123};
 };
 return {db,add,read:id=>db.prepare('SELECT * FROM workflow_tasks WHERE task_id=?').get(id)};
}
test('only exact marked live-state image read with dead owner is marked as ordinary failed',t=>{
 const {db,add,read}=fixture(t);
 const gone=add('a'),unmarked=add('b',{pid:null}),active=add('c',{pid:7890}),unknown=add('d',{type:'cinematic.source.generate'}),
 own=add('e',{pid:process.pid}),settled=add('f',{status:'completed'}),foreign=add('1',{worker:'document'}),
 stored=add('2',{result:'verified-reference'}),notPlanned=add('3',{state:'failed'});
 const before=new Map([unmarked,active,unknown,own,settled,foreign,stored,notPlanned].map(r=>[r.taskId,read(r.taskId)]));
 const opts={knownTypes:[kind],ownerExited:pid=>pid===6789};
 assert.deepEqual(detectLostPortraitReads(db,opts),[{taskId:gone.taskId,state:'failed',code:'WORKFLOW_PORTRAIT_READ_OWNER_LOST'}]);
 const failed=read(gone.taskId);
 assert.equal(failed.status,'failed');assert.equal(failed.retry_count,1);assert.equal(failed.portrait_read_owner_pid,null);
 assert.ok(failed.updated_at>123);assert.equal(failed.result_ref,null);
 for(const [id,row] of before)assert.deepEqual(read(id),row);
 assert.deepEqual(detectLostPortraitReads(db,opts),[]);
});
test('marker is cleared before rendering, and exact CAS protects advanced claims',t=>{
 const {db,add,read}=fixture(t);
 const own=add('a',{pid:process.pid});
 assert.equal(finishPortraitRead(db,{...own,claimStamp:999}),false);
 assert.equal(finishPortraitRead(db,own),true);
 assert.equal(read(own.taskId).status,'running');assert.equal(read(own.taskId).portrait_read_owner_pid,null);
 assert.deepEqual(detectLostPortraitReads(db,{knownTypes:[kind],ownerExited:()=>true}),[]);
 const failing=add('b',{pid:process.pid});
 assert.equal(finishPortraitRead(db,failing,{failed:true}),true);
 assert.equal(read(failing.taskId).status,'failed');assert.equal(read(failing.taskId).retry_count,1);
});
test('old databases have no evidence, so their running claims are not changed',t=>{
 const {db,add,read}=fixture(t,false),old=add('a');
 assert.deepEqual(detectLostPortraitReads(db,{knownTypes:[kind],ownerExited:()=>true}),[]);
 assert.equal(read(old.taskId).status,'running');
});
test('owner evidence is invalidated if the phase changed before the failure CAS',t=>{
 const {db,add,read}=fixture(t),row=add('a');
 assert.deepEqual(detectLostPortraitReads(db,{knownTypes:[kind],ownerExited:()=>{
  db.prepare('UPDATE workflow_tasks SET portrait_read_owner_pid=NULL WHERE task_id=?').run(row.taskId);
  return true;
 }}),[]);
 assert.equal(read(row.taskId).status,'running');
});
